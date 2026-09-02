"""
Package catalog + Firestore entitlements for the SSA paywall.

The catalog here is the source of truth for WHAT each SKU unlocks (sport
slugs). Stripe knows prices; this module knows meaning. League services check
a customer's slug set server-side before returning paid data.

Entitlement slugs are per-sport: "cfl", "ncaaf", "nfl", "golf" (sellable
today), "soccer", "nba", "nhl" (reserved for future launches). The special
slug "all" means every sport, current and future — it is what the All-Access
SKU grants, so new sports automatically join it.

Firestore (project `ssa-auth-71d16`, shared with Firebase Auth):
  customers/{uid}     → {email, stripe_customer_id, created_at}
                        written when a customer first reaches checkout.
  entitlements/{uid}  → {slugs: [...], packages: [...], updated_at}
                        written ONLY by the Stripe webhook (api/billing.py),
                        recomputed from the customer's full subscription list
                        on every subscription event. Never edited by hand.

Access = union of slugs across subscriptions in an ACTIVE-ish status
("active", "trialing", "past_due" — past_due keeps access during dunning).

Dev mode (DISABLE_AUTH=1): Firestore is never touched; entitlements come from
the DEV_ENTITLEMENTS env var (comma-separated slugs, e.g. "cfl,golf").
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── Slugs ────────────────────────────────────────────────────────────────────
ALL_SLUG = "all"
SPORT_SLUGS = ["cfl", "ncaaf", "nfl", "golf", "soccer", "nba", "nhl"]

# Sports with a purchasable package today. Soccer/NBA/NHL join this list (and
# get a SKU below) when their public products launch — All-Access customers
# pick them up automatically via the "all" slug.
SELLABLE_SPORTS = ["cfl", "ncaaf", "nfl", "golf"]

# ── Terms of Service ─────────────────────────────────────────────────────────
# Bump when /terms changes materially. The session mint stamps the version a
# customer accepted onto customers/{uid} (record_tos_acceptance below); the
# /signin card carries the by-continuing-you-agree clickwrap line.
TOS_VERSION = "2026-08-13"

# ── SKU catalog ──────────────────────────────────────────────────────────────
# kind: "sport" | "bundle" | "all". Pricing ladder (John, 2026-08-08):
# sports $99.99/mo; NCAAF+NFL is the only bundle, 25% off the sum ($149.99);
# All-Access is 25% off the sum of every sellable sport ($299.99). Every SKU
# also sells a 6-month prepaid term at 50% off six monthly cycles — six months
# for the price of three. Real amounts come from the PRICE_DISPLAY_* envs; the
# derivations below only shape the placeholder ladder.
SKUS: dict[str, dict] = {
    "sport_cfl": {
        "label": "CFL Package",
        "kind": "sport",
        "slugs": ["cfl"],
        "blurb": "Full CFL model access — power rankings, weekly schedule with "
                 "predicted lines and picks, forecasted records, and team detail sheets.",
    },
    "sport_ncaaf": {
        "label": "NCAAF Package",
        "kind": "sport",
        "slugs": ["ncaaf"],
        "blurb": "College football, modeled — power rankings, schedule with lines "
                 "and picks, the Monte Carlo forecast grid with CFP odds, and team "
                 "cheat sheets.",
    },
    "sport_nfl": {
        "label": "NFL Package",
        "kind": "sport",
        "slugs": ["nfl"],
        "blurb": "Both NFL modules — the ELO model (rankings, schedule picks, "
                 "playoff forecast) and the mock-draft aggregator.",
    },
    "sport_golf": {
        "label": "Golf Package",
        "kind": "sport",
        "slugs": ["golf"],
        "blurb": "Head-to-head and 3-ball matchup grades, win odds and the "
                 "value index, and the DFS lineup builder — refreshed every "
                 "3 hours during tournament weeks.",
    },
    "bundle_football": {
        "label": "Football Bundle",
        "kind": "bundle",
        "slugs": ["ncaaf", "nfl"],
        "blurb": "NCAAF and NFL together at 25% off the combined price. "
                 "Every Saturday and every Sunday, one subscription.",
    },
    "all_access": {
        "label": "All-Access",
        "kind": "all",
        "slugs": [ALL_SLUG],
        "blurb": "Everything, everywhere — every sport and every module at 25% "
                 "off the combined price, including new sports as they launch "
                 "at no extra cost.",
    },
}

# ── Prices ───────────────────────────────────────────────────────────────────
# The decided launch ladder (John, 2026-08-08), in cents. Sports $99.99/mo; the
# Football Bundle is 25% off the sum of its parts ($149.99); All-Access is 25%
# off the sum of all four sellable sports ($299.99) — and unlike four separate
# subscriptions it absorbs future sports for free. The 6-month term is "six
# months for the price of three" (50% off six monthly cycles), rounded UP to
# John's .99 convention: $299.97 → $299.99 and so on.
#
# This table is the single source of truth for amounts: the Stripe bootstrap
# scripts mint prices at these numbers, and the pricing page displays them.
# PRICE_DISPLAY_<SKU> / PRICE_DISPLAY_<SKU>_6MO envs can override the DISPLAY
# (e.g. mid-repricing, while a new Stripe price is being phased in) without a
# code change.
LAUNCH_PRICE_CENTS: dict[tuple[str, str], int] = {
    ("sport_cfl", "monthly"): 9999,        ("sport_cfl", "6mo"): 29999,
    ("sport_ncaaf", "monthly"): 9999,      ("sport_ncaaf", "6mo"): 29999,
    ("sport_nfl", "monthly"): 9999,        ("sport_nfl", "6mo"): 29999,
    ("sport_golf", "monthly"): 9999,       ("sport_golf", "6mo"): 29999,
    ("bundle_football", "monthly"): 14999, ("bundle_football", "6mo"): 44999,
    ("all_access", "monthly"): 29999,      ("all_access", "6mo"): 89999,
}


def display_cents(sku: str, term: str = "monthly") -> int:
    """Display price in cents for a (SKU, term), env override first."""
    suffix = "" if term == "monthly" else "_6MO"
    env = os.environ.get(f"PRICE_DISPLAY_{sku.upper()}{suffix}")
    if env and env.isdigit():
        return int(env)
    return LAUNCH_PRICE_CENTS[(sku, term)]


def catalog() -> list[dict]:
    """The pricing page's payload: every SKU with display prices + meaning."""
    return [
        {
            "sku": sku,
            "label": spec["label"],
            "kind": spec["kind"],
            "slugs": spec["slugs"],
            "blurb": spec["blurb"],
            "monthly_cents": display_cents(sku),
            "six_month_cents": display_cents(sku, "6mo"),
        }
        for sku, spec in SKUS.items()
    ]


def covered(held: set[str], wanted: list[str]) -> bool:
    """Does a slug set already include everything a SKU would grant?"""
    if ALL_SLUG in held:
        return True
    return all(s in held for s in wanted)


# ── Firestore ────────────────────────────────────────────────────────────────

_db = None


def _is_dev() -> bool:
    return os.environ.get("DISABLE_AUTH", "").strip().lower() in {"1", "true", "yes"}


def _firestore():
    """Lazy Firestore client against the shared auth project."""
    global _db
    if _db is None:
        from google.cloud import firestore

        project = os.environ.get("FIRESTORE_PROJECT_ID", "ssa-auth-71d16")
        _db = firestore.Client(project=project)
    return _db


def _dev_entitlements() -> dict:
    slugs = [s.strip() for s in os.environ.get("DEV_ENTITLEMENTS", "").split(",") if s.strip()]
    packages = []
    for sku, spec in SKUS.items():
        if spec["kind"] == "sport" and spec["slugs"][0] in slugs:
            packages.append({
                "sku": sku, "label": spec["label"], "status": "active",
                "current_period_end": None, "subscription_id": "dev",
                "term": "monthly",
            })
    if ALL_SLUG in slugs:
        packages.append({
            "sku": "all_access", "label": SKUS["all_access"]["label"],
            "status": "active", "current_period_end": None, "subscription_id": "dev",
            "term": "monthly",
        })
    return {"slugs": slugs, "packages": packages}


def get_entitlements(uid: str) -> dict:
    """The customer's current access: {"slugs": [...], "packages": [...]}.
    Missing doc = a signed-in user with no purchases (empty, not an error).
    Firestore failures raise — callers surface a 503 rather than silently
    treating a paying customer as unentitled."""
    if _is_dev():
        return _dev_entitlements()

    snap = _firestore().collection("entitlements").document(uid).get()
    if not snap.exists:
        return {"slugs": [], "packages": []}
    data = snap.to_dict() or {}
    return {"slugs": data.get("slugs", []), "packages": data.get("packages", [])}


def write_entitlements(uid: str, slugs: list[str], packages: list[dict]) -> None:
    """Webhook-only write path. Full overwrite — the doc is always a pure
    function of the customer's current Stripe subscription list."""
    if _is_dev():
        logger.info("Dev mode: skipping entitlements write for %s → %s", uid, slugs)
        return
    _firestore().collection("entitlements").document(uid).set({
        "slugs": sorted(set(slugs)),
        "packages": packages,
        "updated_at": datetime.datetime.now(datetime.timezone.utc),
    })
    logger.info("Entitlements for %s → %s", uid, sorted(set(slugs)))


def get_customer(uid: str) -> Optional[dict]:
    if _is_dev():
        return None
    snap = _firestore().collection("customers").document(uid).get()
    return (snap.to_dict() or None) if snap.exists else None


def set_customer(uid: str, email: str, stripe_customer_id: str) -> None:
    if _is_dev():
        logger.info("Dev mode: skipping customer write for %s", uid)
        return
    _firestore().collection("customers").document(uid).set({
        "email": email,
        "stripe_customer_id": stripe_customer_id,
        "created_at": datetime.datetime.now(datetime.timezone.utc),
    }, merge=True)


def record_tos_acceptance(uid: str, email: str = "") -> None:
    """Best-effort clickwrap record: stamp customers/{uid} with the Terms
    version in force when the customer signed in. Never raises — a Firestore
    hiccup must not block sign-in — and skips the write when the stored
    version already matches, so the original acceptance timestamp survives
    routine re-mints."""
    if _is_dev():
        return
    try:
        ref = _firestore().collection("customers").document(uid)
        snap = ref.get()
        data = snap.to_dict() if snap.exists else None
        if data and data.get("tos_version") == TOS_VERSION:
            return
        stamp: dict = {
            "tos_version": TOS_VERSION,
            "tos_accepted_at": datetime.datetime.now(datetime.timezone.utc),
        }
        if email:
            stamp["email"] = email
        ref.set(stamp, merge=True)
        logger.info("ToS %s acceptance recorded for %s", TOS_VERSION, uid)
    except Exception as exc:
        logger.warning("ToS acceptance stamp failed for %s: %s", uid, exc)


def uid_for_stripe_customer(stripe_customer_id: str) -> Optional[str]:
    """Reverse lookup for webhook events whose subscription lacks uid metadata
    (shouldn't happen for checkouts we create, but be defensive)."""
    if _is_dev():
        return None
    q = (
        _firestore().collection("customers")
        .where("stripe_customer_id", "==", stripe_customer_id)
        .limit(1)
        .get()
    )
    for snap in q:
        return snap.id
    return None
