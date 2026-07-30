"""
Stripe billing for the SSA paywall: Checkout, Customer Portal, and the webhook
that keeps Firestore entitlements in sync.

Model (John's decisions, 2026-07-30):
  - Monthly recurring subscriptions via Stripe Checkout.
  - Stackable à la carte: EACH purchase is its own Stripe subscription; a
    customer's access is the union of slugs across active subscriptions.
  - Bundles/All-Access are just SKUs whose price covers multiple slugs.
  - No free trials. Promotion codes allowed at checkout (created by hand in
    the Stripe dashboard); true refer-a-friend is a post-launch fast-follow.

Configuration (all env):
  STRIPE_SECRET_KEY        — sk_test_… / sk_live_…  (Secret Manager in prod)
  STRIPE_WEBHOOK_SECRET    — whsec_…                (Secret Manager in prod)
  STRIPE_PRICE_<SKU>       — the Stripe Price id for each catalog SKU, e.g.
                             STRIPE_PRICE_SPORT_CFL, STRIPE_PRICE_BUNDLE_FOOTBALL,
                             STRIPE_PRICE_ALL_ACCESS. A SKU with no price id
                             configured 503s at checkout but still displays.
  PUBLIC_BASE_URL          — absolute origin for success/cancel URLs
                             (default https://sportsbookscienceanalytics.com)

The webhook is the ONLY writer of entitlements docs. On every subscription
event it re-lists the customer's subscriptions from Stripe and rewrites the
doc from scratch — idempotent, order-insensitive, and self-healing if an
event is missed (any later event repairs the doc).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import HTTPException

from api import entitlements as ent

logger = logging.getLogger(__name__)

# Subscription statuses that grant access. past_due keeps access during
# Stripe's dunning window; canceled/unpaid/incomplete do not.
ACTIVE_STATUSES = {"active", "trialing", "past_due"}


def _price_env(sku: str) -> str:
    return f"STRIPE_PRICE_{sku.upper()}"


def price_id_for_sku(sku: str) -> str:
    return os.environ.get(_price_env(sku), "")


def sku_for_price_id(price_id: str) -> Optional[str]:
    for sku in ent.SKUS:
        if price_id and price_id_for_sku(sku) == price_id:
            return sku
    return None


def configured() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def _stripe():
    """Lazy import + key setup. Raises a friendly 503 when unconfigured so the
    UI can say 'billing not live yet' instead of stack-tracing."""
    if not configured():
        raise HTTPException(503, "Billing is not configured yet")
    import stripe

    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    return stripe


# ── Checkout ─────────────────────────────────────────────────────────────────

def _base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "https://sportsbookscienceanalytics.com").rstrip("/")


def _get_or_create_customer(user: dict) -> str:
    """Stripe customer id for this Firebase uid, creating (and persisting the
    mapping) on first checkout."""
    stripe = _stripe()
    uid = user["uid"]
    email = (user.get("email") or "").lower()

    existing = ent.get_customer(uid)
    if existing and existing.get("stripe_customer_id"):
        return existing["stripe_customer_id"]

    customer = stripe.Customer.create(email=email or None, metadata={"uid": uid})
    ent.set_customer(uid, email, customer.id)
    logger.info("Created Stripe customer %s for uid %s", customer.id, uid)
    return customer.id


def create_checkout_session(user: dict, sku: str) -> str:
    """Create a subscription Checkout session for one SKU; returns the URL."""
    if sku not in ent.SKUS:
        raise HTTPException(404, f"Unknown package: {sku}")

    price_id = price_id_for_sku(sku)
    if not price_id:
        raise HTTPException(503, f"Package {sku} has no Stripe price configured yet")

    # Refuse pointless double-buys: everything this SKU grants is already held.
    held = set(ent.get_entitlements(user["uid"])["slugs"])
    if ent.covered(held, ent.SKUS[sku]["slugs"]):
        raise HTTPException(409, "Your current packages already include this")

    stripe = _stripe()
    customer_id = _get_or_create_customer(user)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        allow_promotion_codes=True,
        client_reference_id=user["uid"],
        subscription_data={"metadata": {"uid": user["uid"], "sku": sku}},
        success_url=f"{_base_url()}/account?checkout=success",
        cancel_url=f"{_base_url()}/pricing?checkout=canceled",
    )
    return session.url


def create_portal_session(user: dict) -> str:
    """Stripe Customer Portal (cancel / payment method / invoices)."""
    stripe = _stripe()
    existing = ent.get_customer(user["uid"])
    if not existing or not existing.get("stripe_customer_id"):
        raise HTTPException(404, "No billing profile yet — subscribe to a package first")
    session = stripe.billing_portal.Session.create(
        customer=existing["stripe_customer_id"],
        return_url=f"{_base_url()}/account",
    )
    return session.url


# ── Webhook → entitlements sync ──────────────────────────────────────────────

def _recompute(uid: str, stripe_customer_id: str) -> None:
    """Rewrite entitlements/{uid} as a pure function of the customer's current
    subscription list. Called on every relevant webhook event."""
    stripe = _stripe()
    subs = stripe.Subscription.list(
        customer=stripe_customer_id, status="all", limit=100
    )
    slugs: list[str] = []
    packages: list[dict] = []
    for sub in subs.auto_paging_iter():
        if sub.status not in ACTIVE_STATUSES:
            continue
        for item in sub["items"]["data"]:
            sku = sku_for_price_id(item["price"]["id"])
            if sku is None:
                logger.warning("Subscription %s has unknown price %s — ignored",
                               sub.id, item["price"]["id"])
                continue
            spec = ent.SKUS[sku]
            slugs.extend(spec["slugs"])
            packages.append({
                "sku": sku,
                "label": spec["label"],
                "status": sub.status,
                "current_period_end": sub.get("current_period_end"),
                "subscription_id": sub.id,
            })
    ent.write_entitlements(uid, slugs, packages)


def handle_webhook(payload: bytes, sig_header: Optional[str]) -> dict:
    """Verify + dispatch a Stripe webhook event. Always returns 200-shaped
    data for events we deliberately ignore, so Stripe doesn't retry them."""
    stripe = _stripe()
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(503, "Webhook secret not configured")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as exc:
        logger.warning("Webhook signature rejected: %s", exc)
        raise HTTPException(400, "Bad signature") from None

    etype = event["type"]
    obj = event["data"]["object"]

    if etype == "checkout.session.completed":
        uid = obj.get("client_reference_id")
        customer_id = obj.get("customer")
        if uid and customer_id:
            email = (obj.get("customer_details") or {}).get("email", "") or ""
            ent.set_customer(uid, email.lower(), customer_id)
            _recompute(uid, customer_id)
        else:
            logger.error("checkout.session.completed missing uid/customer: %s", obj.get("id"))

    elif etype in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:
        customer_id = obj.get("customer")
        uid = (obj.get("metadata") or {}).get("uid") or (
            ent.uid_for_stripe_customer(customer_id) if customer_id else None
        )
        if uid and customer_id:
            _recompute(uid, customer_id)
        else:
            logger.error("Subscription event %s: cannot resolve uid (customer=%s)",
                         etype, customer_id)

    return {"received": True, "type": etype}
