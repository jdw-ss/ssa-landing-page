"""inplayLABS partner members never enter Stripe (John, 2026-09-29).

Partner members (ADR-0002) are synthetic ``ipl_*`` / ``ipltest_*`` uids whose
entitlements/{uid} doc is written by partner.grant(). If one bought through
/pricing, the webhook's full-overwrite _recompute would drop the partner
grants, and the next launch's grant() set() would drop the Stripe slugs. So:

  * every billing route that could create or change Stripe objects 403s a
    partner session BEFORE any Stripe or Firestore call (checkout,
    change-preview, change, portal, and _get_or_create_customer underneath);
  * normal uids behave exactly as before;
  * a Stripe event that resolves to a partner uid still recomputes, but logs
    a WARNING naming the uid;
  * api/partner_uid.py is the ONE prefix source, and both the partner writer
    guard and the billing guards call its predicate;
  * /pricing swaps its purchase controls for the inplayLABS note on a partner
    session (the page's own script, rendered in node), and changes nothing
    for anonymous or normal signed-in visitors.

Stripe and Firestore are faked throughout; no network.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PARTNER = "ipl_" + "a" * 24
PARTNER_TEST = "ipltest_" + "b" * 24
CUSTOMER = "aBcRealGoogleUid123"


def _purge_api_modules() -> None:
    for name in [m for m in list(sys.modules) if m == "api" or m.startswith("api.")]:
        del sys.modules[name]


class _SubList:
    def __init__(self, subs):
        self._subs = subs

    def auto_paging_iter(self):
        return iter(self._subs)


@pytest.fixture()
def hub(monkeypatch):
    """Fresh api.* modules, a recording fake Stripe, and in-memory Firestore
    fakes. `calls` lists every Stripe entry point and Firestore touch."""
    monkeypatch.setenv("DISABLE_AUTH", "")
    for sku in ("SPORT_CFL", "SPORT_NCAAF", "BUNDLE_FOOTBALL"):
        monkeypatch.setenv(f"STRIPE_PRICE_{sku}", f"price_{sku.lower()}")
    _purge_api_modules()
    import api.app as app_mod
    import api.billing as billing
    import api.entitlements as ent

    calls: list[str] = []
    customers: dict[str, dict] = {}

    def rec(name, result=None):
        def _f(*a, **k):
            calls.append(name)
            return result
        return _f

    fake = types.SimpleNamespace(
        Customer=types.SimpleNamespace(
            create=rec("Customer.create", types.SimpleNamespace(id="cus_new"))),
        checkout=types.SimpleNamespace(Session=types.SimpleNamespace(
            create=rec("checkout.Session.create",
                       types.SimpleNamespace(url="https://checkout.stripe.com/s")))),
        billing_portal=types.SimpleNamespace(Session=types.SimpleNamespace(
            create=rec("billing_portal.Session.create",
                       types.SimpleNamespace(url="https://billing.stripe.com/p")))),
        Subscription=types.SimpleNamespace(
            list=rec("Subscription.list", _SubList([])),
            cancel=rec("Subscription.cancel"),
            modify=rec("Subscription.modify")),
    )

    def _stripe():
        calls.append("_stripe")
        return fake

    def get_customer(uid):
        calls.append("firestore.get_customer")
        return customers.get(uid)

    def set_customer(uid, email, cid):
        calls.append("firestore.set_customer")
        customers[uid] = {"stripe_customer_id": cid}

    def get_entitlements(uid):
        calls.append("firestore.get_entitlements")
        return {"slugs": [], "packages": []}

    monkeypatch.setattr(billing, "_stripe", _stripe)
    monkeypatch.setattr(ent, "get_customer", get_customer)
    monkeypatch.setattr(ent, "set_customer", set_customer)
    monkeypatch.setattr(ent, "get_entitlements", get_entitlements)

    from fastapi.testclient import TestClient

    def client_as(uid):
        from api.auth import optional_session_user, require_session_user
        app_mod.app.dependency_overrides.clear()
        if uid is not None:
            user = {"uid": uid}
            app_mod.app.dependency_overrides[require_session_user] = lambda: user
            app_mod.app.dependency_overrides[optional_session_user] = lambda: user
        else:
            app_mod.app.dependency_overrides[optional_session_user] = lambda: None
        return TestClient(app_mod.app, raise_server_exceptions=False)

    ns = types.SimpleNamespace(app=app_mod, billing=billing, ent=ent, calls=calls,
                               customers=customers, client_as=client_as)
    yield ns
    app_mod.app.dependency_overrides.clear()


# Every session-bound billing route that can reach Stripe.
ROUTES = [
    ("POST", "/api/billing/checkout", {"json": {"sku": "sport_cfl", "term": "monthly"}}),
    ("GET", "/api/billing/change-preview", {"params": {"sku": "bundle_football"}}),
    ("POST", "/api/billing/change", {"json": {"sku": "bundle_football", "term": "monthly"}}),
    ("POST", "/api/billing/portal", {}),
]


@pytest.mark.parametrize("uid", [PARTNER, PARTNER_TEST])
@pytest.mark.parametrize("method,path,kw", ROUTES, ids=[r[1] for r in ROUTES])
def test_partner_session_is_refused_before_any_stripe_call(hub, uid, method, path, kw):
    # A customer mapping exists, as it would for a pre-guard purchase: without
    # the guard, preview/change/portal would all go on to call Stripe.
    hub.customers[uid] = {"stripe_customer_id": "cus_legacy"}
    r = hub.client_as(uid).request(method, path, **kw)
    assert r.status_code == 403, (path, r.status_code, r.text)
    assert "inplayLABS" in r.json()["detail"]
    assert hub.calls == [], f"{path} touched Stripe/Firestore for {uid}: {hub.calls}"


def test_get_or_create_customer_refuses_a_partner_uid_itself(hub):
    """Last line of defence: a future caller that skips the route guard must
    still never mint a Stripe customer for a partner uid."""
    from fastapi import HTTPException
    for uid in (PARTNER, PARTNER_TEST):
        with pytest.raises(HTTPException) as exc:
            hub.billing._get_or_create_customer({"uid": uid, "email": ""})
        assert exc.value.status_code == 403
    assert hub.calls == []


def test_normal_uid_billing_is_unchanged(hub):
    """The guard must not touch real customers: checkout and preview proceed to
    Stripe, portal opens for a mapped customer, change still 409s when there is
    nothing to upgrade."""
    c = hub.client_as(CUSTOMER)

    r = c.post("/api/billing/checkout", json={"sku": "sport_cfl", "term": "monthly"})
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "https://checkout.stripe.com/s"
    assert "Customer.create" in hub.calls and "checkout.Session.create" in hub.calls

    r = c.get("/api/billing/change-preview", params={"sku": "bundle_football"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "new"

    r = c.post("/api/billing/change", json={"sku": "bundle_football", "term": "monthly"})
    assert r.status_code == 409

    r = c.post("/api/billing/portal")
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "https://billing.stripe.com/p"


def test_catalog_flags_partner_sessions_only(hub):
    """/pricing reads `partner_member` from the catalog (additive field) to
    swap its controls; it comes from the same predicate as the 403s."""
    assert hub.client_as(PARTNER).get("/api/billing/catalog").json()["partner_member"] is True
    assert hub.client_as(PARTNER_TEST).get("/api/billing/catalog").json()["partner_member"] is True
    assert hub.client_as(CUSTOMER).get("/api/billing/catalog").json()["partner_member"] is False
    anon = hub.client_as(None).get("/api/billing/catalog").json()
    assert anon["partner_member"] is False and anon["signed_in"] is False


def test_dev_uid_previews_the_partner_view(monkeypatch):
    """The documented local preview: DISABLE_AUTH=1 DEV_UID=ipl_preview makes
    the stub user a partner, so /pricing renders the inplayLABS note and
    checkout 403s. Without DEV_UID the stub stays an ordinary customer."""
    monkeypatch.setenv("DISABLE_AUTH", "1")
    monkeypatch.setenv("DEV_UID", "ipl_preview")
    _purge_api_modules()
    from fastapi.testclient import TestClient
    import api.app as app_mod
    c = TestClient(app_mod.app, raise_server_exceptions=False)
    assert c.get("/api/billing/catalog").json()["partner_member"] is True
    r = c.post("/api/billing/checkout", json={"sku": "sport_cfl"})
    assert r.status_code == 403 and "inplayLABS" in r.json()["detail"]
    monkeypatch.delenv("DEV_UID")
    assert c.get("/api/billing/catalog").json()["partner_member"] is False


# ── Webhook: never skip, always warn ─────────────────────────────────────────

def _webhook_event(hub, monkeypatch, event):
    recomputes = []
    fake = types.SimpleNamespace(
        Webhook=types.SimpleNamespace(construct_event=lambda p, s, sec: event),
        Subscription=types.SimpleNamespace(cancel=lambda *a, **k: None),
    )
    monkeypatch.setattr(hub.billing, "_stripe", lambda: fake)
    monkeypatch.setattr(hub.billing, "_recompute",
                        lambda uid, cid: recomputes.append((uid, cid)))
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    hub.billing.handle_webhook(b"{}", "sig")
    return recomputes


def _partner_warnings(caplog, uid):
    return [r for r in caplog.records
            if r.name == "api.billing" and r.levelno == logging.WARNING
            and uid in r.getMessage()]


@pytest.mark.parametrize("uid", [PARTNER, PARTNER_TEST])
def test_webhook_warns_on_a_partner_subscription_event_and_still_writes(
        hub, monkeypatch, caplog, uid):
    caplog.set_level(logging.WARNING, logger="api.billing")
    recomputes = _webhook_event(hub, monkeypatch, {
        "type": "customer.subscription.updated",
        "data": {"object": {"customer": "cus_legacy", "metadata": {"uid": uid}}},
    })
    assert recomputes == [(uid, "cus_legacy")], "the write must not be skipped"
    assert _partner_warnings(caplog, uid), "no WARNING naming the partner uid"


def test_webhook_warns_on_a_partner_checkout_completed(hub, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="api.billing")
    recomputes = _webhook_event(hub, monkeypatch, {
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_1", "client_reference_id": PARTNER,
                            "customer": "cus_legacy",
                            "customer_details": {"email": ""}, "metadata": {}}},
    })
    assert recomputes == [(PARTNER, "cus_legacy")]
    assert _partner_warnings(caplog, PARTNER)


def test_webhook_stays_quiet_for_a_normal_customer(hub, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="api.billing")
    recomputes = _webhook_event(hub, monkeypatch, {
        "type": "customer.subscription.updated",
        "data": {"object": {"customer": "cus_1", "metadata": {"uid": CUSTOMER}}},
    })
    assert recomputes == [(CUSTOMER, "cus_1")]
    assert not [r for r in caplog.records
                if r.name == "api.billing" and r.levelno >= logging.WARNING]


# ── One predicate, one prefix source ─────────────────────────────────────────

_PREFIX_RE = re.compile(r"ipl(?:test)?_|ipl\(test\)")


def _prefix_literals(path: Path) -> list[str]:
    """Non-docstring string constants in a module that spell a partner
    prefix (or a regex for one). Docstrings describe the namespace; code must
    get it from api/partner_uid.py."""
    tree = ast.parse(path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings and _PREFIX_RE.search(n.value)]


def test_partner_uid_module_is_the_only_prefix_source_in_api():
    home = REPO / "api" / "partner_uid.py"
    # Non-vacuous: the scanner does find the literals where they belong.
    assert sorted(_prefix_literals(home)) == ["ipl_", "ipltest_"]
    offenders = {p.name: _prefix_literals(p)
                 for p in sorted((REPO / "api").glob("*.py")) if p != home}
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"partner prefix literals outside partner_uid.py: {offenders}"


def test_writer_guard_and_billing_guards_share_the_predicate(monkeypatch):
    """Swap the predicate and both sides follow it: proof that partner.grant()
    and the billing refusals call partner_uid.is_partner_uid rather than
    carrying their own prefix checks."""
    _purge_api_modules()
    import api.billing as billing
    import api.partner as partner
    import api.partner_uid as partner_uid
    from fastapi import HTTPException

    monkeypatch.setattr(partner_uid, "is_partner_uid", lambda uid: uid == "zzz_spy")

    written = {}

    class _Ref:
        def get(self):
            return types.SimpleNamespace(exists=False, to_dict=lambda: None)

        def set(self, data):
            written.update(data)

    db = types.SimpleNamespace(collection=lambda name: types.SimpleNamespace(
        document=lambda uid: _Ref()))
    partner.grant("zzz_spy", "nfl", "ssa-nfl-model", window_days=7, db=db)
    assert written["slugs"] == ["nfl"]
    with pytest.raises(partner.LaunchError):
        partner.grant(PARTNER, "nfl", "ssa-nfl-model", window_days=7, db=db)

    with pytest.raises(HTTPException) as exc:
        billing.create_checkout_session({"uid": "zzz_spy"}, "sport_cfl")
    assert exc.value.status_code == 403


# ── /pricing renders the partner note (the page's own script, in node) ──────

NODE = shutil.which("node")


def _render_pricing(payload: dict) -> dict:
    out = subprocess.run(
        [NODE, str(REPO / "tests" / "js" / "render_pricing.js"),
         str(REPO / "static" / "pricing.html")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=30,
        check=True)
    return json.loads(out.stdout)


def _payload(**over) -> dict:
    sys.path.insert(0, str(REPO))
    from api import entitlements as ent
    base = {"catalog": ent.catalog(), "held_slugs": [], "held_packages": [],
            "signed_in": True, "partner_member": False,
            "billing_configured": True, "show_prices": True}
    base.update(over)
    return base


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_pricing_shows_partner_note_instead_of_purchase_controls():
    got = _render_pricing(_payload(partner_member=True, held_slugs=["nfl"]))
    assert got["partner_note"] == {"hidden": False}, "partner note not shown"
    assert len(got["cards"]) == 6
    for card in got["cards"]:
        assert "partner-slot" in card and "inplayLABS" in card
        assert "data-sku" not in card, "a purchase button survived"
        assert 'href="/account"' not in card, "/account bounces partners to /signin"
        assert "Subscribe" not in card and "Upgrade" not in card
    held = [c for c in got["cards"] if "Included through inplayLABS" in c]
    assert len(held) == 1, "the NFL card should read as included"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_pricing_is_unchanged_for_normal_and_anonymous_visitors():
    signed_in = _render_pricing(_payload())
    assert signed_in["partner_note"] == {"hidden": True}
    assert all("data-sku" in c and "Subscribe" in c for c in signed_in["cards"])
    assert not any("partner-slot" in c for c in signed_in["cards"])

    anon = _render_pricing(_payload(signed_in=False))
    assert anon["partner_note"] == {"hidden": True}
    assert all("Sign in to subscribe" in c for c in anon["cards"])
