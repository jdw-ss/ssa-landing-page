"""Billing + entitlement tests for the apex hub.

The apex had none until 2026-08-07, even though the SKU catalog, `covered()` and
`_recompute` are what every league's paywall ultimately rests on. These pin:

  * the purchase -> slug matrix (one league / football bundle / all-access),
  * that an unrecognised Stripe price can never silently revoke a customer,
  * that an upgrade replaces the old subscription instead of stacking onto it.

Stripe is stubbed throughout — these are logic tests, not integration tests. The
prorated-upgrade path still needs one real test-mode transaction before launch;
see the note on FakeStripe.Invoice.create_preview.
"""
from __future__ import annotations

import os
import sys
import types

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import billing  # noqa: E402
from api import entitlements as ent  # noqa: E402


# ── SKU → slug matrix (claims A / B / C) ─────────────────────────────────────

def _unlocks(slugs):
    """Mirror of every league's gate: `sport in slugs or "all" in slugs`."""
    held = set(slugs)
    return [s for s in ent.SPORT_SLUGS if s in held or ent.ALL_SLUG in held]


def test_one_league_unlocks_only_that_league():
    assert _unlocks(ent.SKUS["sport_cfl"]["slugs"]) == ["cfl"]
    assert _unlocks(ent.SKUS["sport_ncaaf"]["slugs"]) == ["ncaaf"]
    assert _unlocks(ent.SKUS["sport_golf"]["slugs"]) == ["golf"]


def test_nfl_package_unlocks_both_nfl_modules():
    # One slug, two products: /elomodel and /mockdrafts both check "nfl".
    assert ent.SKUS["sport_nfl"]["slugs"] == ["nfl"]
    assert _unlocks(["nfl"]) == ["nfl"]


def test_football_bundle_is_exactly_ncaaf_plus_nfl():
    got = _unlocks(ent.SKUS["bundle_football"]["slugs"])
    assert sorted(got) == ["ncaaf", "nfl"]


def test_all_access_unlocks_every_sport_including_unlaunched():
    got = _unlocks(ent.SKUS["all_access"]["slugs"])
    assert sorted(got) == sorted(ent.SPORT_SLUGS)
    # Sports with no product yet must be covered with no migration.
    for future in ("soccer", "nba", "nhl"):
        assert future in got


def test_covered_blocks_only_fully_redundant_buys():
    assert ent.covered({"all"}, ent.SKUS["sport_cfl"]["slugs"]) is True
    assert ent.covered({"ncaaf", "nfl"}, ent.SKUS["bundle_football"]["slugs"]) is True
    assert ent.covered({"cfl"}, ent.SKUS["sport_nfl"]["slugs"]) is False
    # Partial overlap is NOT "covered" — it's an upgrade, handled separately.
    assert ent.covered({"ncaaf"}, ent.SKUS["bundle_football"]["slugs"]) is False


# ── Price id resolution (B1) ─────────────────────────────────────────────────

def test_price_env_accepts_a_list_so_retired_ids_stay_resolvable(monkeypatch):
    # Stripe Prices are immutable: a new amount means a new id, and existing
    # subscriptions keep billing on the old one.
    monkeypatch.setenv("STRIPE_PRICE_SPORT_NCAAF", "price_NEW, price_OLD")
    assert billing.price_ids_for_sku("sport_ncaaf") == ["price_NEW", "price_OLD"]
    # New checkouts use the first entry...
    assert billing.price_id_for_sku("sport_ncaaf") == "price_NEW"
    # ...but a legacy subscriber still resolves.
    assert billing.sku_for_price_id("price_OLD") == "sport_ncaaf"
    assert billing.sku_for_price_id("price_NEW") == "sport_ncaaf"


def test_unknown_price_resolves_nothing(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_SPORT_NCAAF", "price_NEW")
    assert billing.sku_for_price_id("price_GONE") is None


# ── _recompute must never silently revoke (B1) ───────────────────────────────

class _Sub(dict):
    """Minimal Stripe-like subscription; attribute access for id/status."""

    def __init__(self, sid, status, price_ids, meta=None):
        super().__init__({
            "items": {"data": [
                {"id": f"si_{i}", "price": {"id": p}} for i, p in enumerate(price_ids)
            ]},
            "metadata": meta or {},
            "status": status,
            "id": sid,
        })
        self.id = sid
        self.status = status


class _SubList:
    def __init__(self, subs):
        self._subs = subs

    def auto_paging_iter(self):
        return iter(self._subs)


def _fake_stripe(subs):
    mod = types.SimpleNamespace()
    mod.Subscription = types.SimpleNamespace(
        list=lambda **kw: _SubList(subs),
        modify=lambda *a, **k: None,
        delete=lambda *a, **k: None,
    )
    return mod


def test_recompute_writes_union_of_active_subscriptions(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_SPORT_CFL", "price_cfl")
    monkeypatch.setenv("STRIPE_PRICE_SPORT_GOLF", "price_golf")
    monkeypatch.setattr(billing, "_stripe", lambda: _fake_stripe([
        _Sub("sub_1", "active", ["price_cfl"]),
        _Sub("sub_2", "active", ["price_golf"]),
        _Sub("sub_3", "canceled", ["price_cfl"]),
    ]))
    written = {}
    monkeypatch.setattr(ent, "write_entitlements",
                        lambda uid, slugs, pkgs: written.update(slugs=sorted(set(slugs))))
    billing._recompute("uid1", "cus_1")
    assert written["slugs"] == ["cfl", "golf"]


def test_recompute_falls_back_to_subscription_metadata_after_a_price_change(monkeypatch):
    """The exact go-live failure: the price id was retired, so the lookup misses.
    The SKU stamped at checkout must keep the customer alive."""
    monkeypatch.setenv("STRIPE_PRICE_SPORT_NCAAF", "price_NEW")
    monkeypatch.setattr(billing, "_stripe", lambda: _fake_stripe([
        _Sub("sub_1", "active", ["price_RETIRED"], meta={"uid": "uid1", "sku": "sport_ncaaf"}),
    ]))
    written = {}
    monkeypatch.setattr(ent, "write_entitlements",
                        lambda uid, slugs, pkgs: written.update(slugs=sorted(set(slugs))))
    billing._recompute("uid1", "cus_1")
    assert written["slugs"] == ["ncaaf"], "a price change must not drop the customer"


def test_recompute_refuses_to_write_when_a_price_is_unresolvable(monkeypatch):
    """No metadata AND no matching price: writing would zero the doc and lock the
    customer out of every league. It must raise so Stripe retries instead."""
    monkeypatch.setenv("STRIPE_PRICE_SPORT_NCAAF", "price_NEW")
    monkeypatch.setattr(billing, "_stripe", lambda: _fake_stripe([
        _Sub("sub_1", "active", ["price_MYSTERY"]),
    ]))
    calls = []
    monkeypatch.setattr(ent, "write_entitlements",
                        lambda uid, slugs, pkgs: calls.append(slugs))
    with pytest.raises(HTTPException) as exc:
        billing._recompute("uid1", "cus_1")
    assert exc.value.status_code == 500
    assert not calls, "must not overwrite entitlements when an item is unresolved"


# ── Upgrades replace rather than stack (B3) ──────────────────────────────────

def test_superseded_detects_an_upgrade_but_not_a_downgrade(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_SPORT_NCAAF", "price_ncaaf")
    monkeypatch.setattr(billing, "_stripe", lambda: _fake_stripe([
        _Sub("sub_1", "active", ["price_ncaaf"]),
    ]))
    # NCAAF -> Football Bundle replaces the NCAAF sub.
    up = billing._superseded("cus_1", "bundle_football")
    assert [s["sku"] for s in up] == ["sport_ncaaf"]
    # NCAAF -> Golf replaces nothing (they're independent).
    assert billing._superseded("cus_1", "sport_golf") == []


def test_all_access_supersedes_everything_held(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_SPORT_CFL", "price_cfl")
    monkeypatch.setenv("STRIPE_PRICE_SPORT_GOLF", "price_golf")
    monkeypatch.setattr(billing, "_stripe", lambda: _fake_stripe([
        _Sub("sub_1", "active", ["price_cfl"]),
        _Sub("sub_2", "active", ["price_golf"]),
    ]))
    up = billing._superseded("cus_1", "all_access")
    assert sorted(s["sku"] for s in up) == ["sport_cfl", "sport_golf"]


def test_checkout_refuses_a_partial_overlap_and_points_at_the_upgrade(monkeypatch):
    """The double-billing bug: this used to create a SECOND subscription while
    the superseded one kept charging."""
    monkeypatch.setenv("STRIPE_PRICE_SPORT_NCAAF", "price_ncaaf")
    monkeypatch.setenv("STRIPE_PRICE_BUNDLE_FOOTBALL", "price_bundle")
    monkeypatch.setattr(billing, "_stripe", lambda: _fake_stripe([
        _Sub("sub_1", "active", ["price_ncaaf"]),
    ]))
    monkeypatch.setattr(ent, "get_entitlements", lambda uid: {"slugs": ["ncaaf"], "packages": []})
    monkeypatch.setattr(billing, "_get_or_create_customer", lambda user: "cus_1")
    with pytest.raises(HTTPException) as exc:
        billing.create_checkout_session({"uid": "uid1", "email": "a@b.c"}, "bundle_football")
    assert exc.value.status_code == 409
    assert "upgrade" in str(exc.value.detail).lower()


# ── Terms: 6-month prices, term switches, and the downgrade block (2026-08-08) ─

def _six_sub(sid, status, price_id, meta=None):
    """A subscription whose one item bills every 6 months (interval_count=6)."""
    sub = _Sub(sid, status, [price_id], meta)
    sub["items"]["data"][0]["price"]["recurring"] = {
        "interval": "month", "interval_count": 6}
    return sub


def test_six_month_prices_resolve_to_the_same_sku(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_SPORT_NFL", "price_nfl_m")
    monkeypatch.setenv("STRIPE_PRICE_SPORT_NFL_6MO", "price_nfl_6")
    assert billing.price_id_for_sku("sport_nfl") == "price_nfl_m"
    assert billing.price_id_for_sku("sport_nfl", "6mo") == "price_nfl_6"
    # Both terms unlock the same slugs — the webhook must resolve either.
    assert billing.sku_for_price_id("price_nfl_6") == "sport_nfl"
    assert billing.sku_term_for_price_id("price_nfl_6") == ("sport_nfl", "6mo")
    assert billing.sku_term_for_price_id("price_nfl_m") == ("sport_nfl", "monthly")


def test_item_term_reads_the_billing_interval_not_the_env():
    assert billing._item_term({"price": {"id": "x"}}) == "monthly"
    assert billing._item_term(
        {"price": {"id": "x", "recurring": {"interval": "month", "interval_count": 6}}}
    ) == "6mo"


def test_monthly_to_six_month_same_sku_is_an_upgrade(monkeypatch):
    """John's ask: buying 6 months while already subscribed monthly prorates
    instead of stacking or being refused as a double-buy."""
    monkeypatch.setenv("STRIPE_PRICE_SPORT_NFL", "price_nfl_m")
    monkeypatch.setenv("STRIPE_PRICE_SPORT_NFL_6MO", "price_nfl_6")
    monkeypatch.setattr(billing, "_stripe", lambda: _fake_stripe([
        _Sub("sub_1", "active", ["price_nfl_m"]),
    ]))
    replaceable, blocked = billing._scan_changes("cus_1", "sport_nfl", "6mo")
    assert [(s["sku"], s["term"]) for s in replaceable] == [("sport_nfl", "monthly")]
    assert blocked == []


def test_six_month_holder_cannot_drop_to_a_monthly_target(monkeypatch):
    """Same SKU downward, and the sneakier shape: 6-month NCAAF vs monthly
    bundle. Both would strand prepaid credit — both are blocked."""
    monkeypatch.setenv("STRIPE_PRICE_SPORT_NCAAF_6MO", "price_ncaaf_6")
    monkeypatch.setattr(billing, "_stripe", lambda: _fake_stripe([
        _six_sub("sub_1", "active", "price_ncaaf_6"),
    ]))
    replaceable, blocked = billing._scan_changes("cus_1", "sport_ncaaf", "monthly")
    assert replaceable == [] and [b["sku"] for b in blocked] == ["sport_ncaaf"]
    replaceable, blocked = billing._scan_changes("cus_1", "bundle_football", "monthly")
    assert replaceable == [] and [b["sku"] for b in blocked] == ["sport_ncaaf"]
    # The 6-month bundle is the sanctioned path.
    replaceable, blocked = billing._scan_changes("cus_1", "bundle_football", "6mo")
    assert [s["sku"] for s in replaceable] == ["sport_ncaaf"] and blocked == []


def test_blocked_checkout_points_at_the_six_month_target(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_SPORT_NCAAF_6MO", "price_ncaaf_6")
    monkeypatch.setenv("STRIPE_PRICE_BUNDLE_FOOTBALL", "price_bundle_m")
    monkeypatch.setattr(billing, "_stripe", lambda: _fake_stripe([
        _six_sub("sub_1", "active", "price_ncaaf_6"),
    ]))
    monkeypatch.setattr(ent, "get_entitlements", lambda uid: {"slugs": ["ncaaf"], "packages": []})
    monkeypatch.setattr(billing, "_get_or_create_customer", lambda user: "cus_1")
    with pytest.raises(HTTPException) as exc:
        billing.create_checkout_session({"uid": "u", "email": "a@b.c"}, "bundle_football")
    assert exc.value.status_code == 409
    assert "6-month" in str(exc.value.detail)


def test_apply_plan_change_cancels_extras_first_and_resets_anchor_on_term_change(monkeypatch):
    """Two invariants of the swap: extras' proration credits must land on the
    SAME invoice as the charge (cancel before modify — pending invoice items get
    swept by the always_invoice modify), and an interval change starts a fresh
    cycle (billing_cycle_anchor=now) while a same-interval change must NOT."""
    monkeypatch.setenv("STRIPE_PRICE_SPORT_CFL", "price_cfl_m")
    monkeypatch.setenv("STRIPE_PRICE_SPORT_GOLF", "price_golf_m")
    monkeypatch.setenv("STRIPE_PRICE_ALL_ACCESS_6MO", "price_all_6")
    calls = []
    subs = [
        _Sub("sub_cfl", "active", ["price_cfl_m"]),
        _Sub("sub_golf", "active", ["price_golf_m"]),
    ]
    fake = _fake_stripe(subs)
    fake.Subscription.cancel = lambda sid, **kw: calls.append(("cancel", sid, kw))
    fake.Subscription.modify = lambda sid, **kw: calls.append(("modify", sid, kw))
    fake.Invoice = types.SimpleNamespace(
        create_preview=lambda **kw: {"amount_due": 12345})
    monkeypatch.setattr(billing, "_stripe", lambda: fake)
    monkeypatch.setattr(ent, "get_customer",
                        lambda uid: {"stripe_customer_id": "cus_1"})
    monkeypatch.setattr(billing, "_recompute", lambda uid, cid: None)

    result = billing.apply_plan_change({"uid": "u", "email": "a@b.c"},
                                       "all_access", "6mo")

    assert result["status"] == "ok" and result["term"] == "6mo"
    assert [c[0] for c in calls] == ["cancel", "modify"], \
        "extras must be cancelled BEFORE the primary swap"
    cancel_call, modify_call = calls
    assert cancel_call[2].get("prorate") is True
    assert modify_call[2]["items"][0]["price"] == "price_all_6"
    assert modify_call[2]["metadata"]["term"] == "6mo"
    # monthly → 6mo is an interval change: fresh cycle from the purchase moment.
    assert modify_call[2].get("billing_cycle_anchor") == "now"


def test_recompute_stamps_the_term_on_each_package(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_SPORT_NFL_6MO", "price_nfl_6")
    monkeypatch.setenv("STRIPE_PRICE_SPORT_GOLF", "price_golf_m")
    monkeypatch.setattr(billing, "_stripe", lambda: _fake_stripe([
        _six_sub("sub_1", "active", "price_nfl_6"),
        _Sub("sub_2", "active", ["price_golf_m"]),
    ]))
    written = {}
    monkeypatch.setattr(
        ent, "write_entitlements",
        lambda uid, slugs, pkgs: written.update(slugs=sorted(set(slugs)), pkgs=pkgs))
    billing._recompute("uid1", "cus_1")
    assert written["slugs"] == ["golf", "nfl"]
    terms = {p["sku"]: p["term"] for p in written["pkgs"]}
    assert terms == {"sport_nfl": "6mo", "sport_golf": "monthly"}


def test_display_ladder_is_the_decided_2026_08_08_numbers(monkeypatch):
    """Sports $99.99/mo; bundle $149.99 (25% off the pair); All-Access $299.99
    (25% off the 4-sport sum); every 6-month price is John's rounded-up 50% of
    six monthly cycles. Envs override display without touching the table."""
    assert ent.display_cents("sport_nfl") == 9999
    assert ent.display_cents("sport_nfl", "6mo") == 29999
    assert ent.display_cents("bundle_football") == 14999
    assert ent.display_cents("bundle_football", "6mo") == 44999
    assert ent.display_cents("all_access") == 29999
    assert ent.display_cents("all_access", "6mo") == 89999
    # Every 6-month price is an honest "save 50%" claim (49.99%+ of 6×monthly).
    for sku in ent.SKUS:
        six, monthly = ent.display_cents(sku, "6mo"), ent.display_cents(sku)
        assert 0.499 <= 1 - six / (monthly * 6) <= 0.501
    monkeypatch.setenv("PRICE_DISPLAY_ALL_ACCESS", "19999")
    assert ent.display_cents("all_access") == 19999
    item = next(i for i in ent.catalog() if i["sku"] == "all_access")
    assert (item["monthly_cents"], item["six_month_cents"]) == (19999, 89999)
