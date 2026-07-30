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

# ── SKU catalog ──────────────────────────────────────────────────────────────
# kind: "sport" | "bundle" | "all". Bundle pricing rule (John, 2026-07-30):
# NCAAF+NFL is the only bundle, 20% off the sum. All-Access is 50% off the sum
# of every sellable sport. Display prices below follow those rules from the
# per-sport placeholder until real prices are set via PRICE_DISPLAY_* envs.
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
        "blurb": "Tournament predictions refreshed every 3 hours — composite "
                 "player rankings, course fit, and strokes-gained analysis.",
    },
    "bundle_football": {
        "label": "Football Bundle",
        "kind": "bundle",
        "slugs": ["ncaaf", "nfl"],
        "blurb": "NCAAF and NFL together at 20% off the combined price. "
                 "Every Saturday and every Sunday, one subscription.",
    },
    "all_access": {
        "label": "All-Access",
        "kind": "all",
        "slugs": [ALL_SLUG],
        "blurb": "Everything, everywhere — every sport and every module, "
                 "including new sports as they launch, at half the combined price.",
    },
}

_PLACEHOLDER_SPORT_CENTS = 999  # $9.99/mo stand-in until real prices are set


def display_cents(sku: str) -> int:
    """Monthly display price in cents. Real prices come from PRICE_DISPLAY_<SKU>
    envs once John sets them; until then bundle/all-access are DERIVED from the
    sport placeholder so the pricing ladder always reflects the agreed rules
    (bundle = 20% off its parts, all-access = 50% off everything sellable)."""
    env = os.environ.get(f"PRICE_DISPLAY_{sku.upper()}")
    if env and env.isdigit():
        return int(env)

    def _sport_cents(s: str) -> int:
        e = os.environ.get(f"PRICE_DISPLAY_SPORT_{s.upper()}")
        return int(e) if e and e.isdigit() else _PLACEHOLDER_SPORT_CENTS

    spec = SKUS[sku]
    if spec["kind"] == "sport":
        return _sport_cents(spec["slugs"][0])
    if spec["kind"] == "bundle":
        return round(sum(_sport_cents(s) for s in spec["slugs"]) * 0.80)
    return round(sum(_sport_cents(s) for s in SELLABLE_SPORTS) * 0.50)


def catalog() -> list[dict]:
    """The pricing page's payload: every SKU with display price + meaning."""
    return [
        {
            "sku": sku,
            "label": spec["label"],
            "kind": spec["kind"],
            "slugs": spec["slugs"],
            "blurb": spec["blurb"],
            "monthly_cents": display_cents(sku),
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
            })
    if ALL_SLUG in slugs:
        packages.append({
            "sku": "all_access", "label": SKUS["all_access"]["label"],
            "status": "active", "current_period_end": None, "subscription_id": "dev",
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
    _firestore().collection("customers").document(uid).set({
        "email": email,
        "stripe_customer_id": stripe_customer_id,
        "created_at": datetime.datetime.now(datetime.timezone.utc),
    }, merge=True)


def uid_for_stripe_customer(stripe_customer_id: str) -> Optional[str]:
    """Reverse lookup for webhook events whose subscription lacks uid metadata
    (shouldn't happen for checkouts we create, but be defensive)."""
    q = (
        _firestore().collection("customers")
        .where("stripe_customer_id", "==", stripe_customer_id)
        .limit(1)
        .get()
    )
    for snap in q:
        return snap.id
    return None
