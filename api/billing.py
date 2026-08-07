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


def field(obj, key: str, default=None):
    """Safe field access for Stripe SDK objects: they support obj[key] but NOT
    dict-style .get() (attribute lookup raises instead — found 2026-07-30).
    Works on plain dicts too. None values fall back to the default."""
    try:
        val = obj[key]
    except (KeyError, TypeError, IndexError):
        return default
    return default if val is None else val


def _price_env(sku: str) -> str:
    return f"STRIPE_PRICE_{sku.upper()}"


def price_ids_for_sku(sku: str) -> list[str]:
    """Every price id that maps to this SKU — current first, then retired ones.

    Stripe Prices are IMMUTABLE: changing an amount mints a NEW price id while
    existing subscriptions keep billing on the old one. With a single id per SKU
    there is no configuration that serves new checkouts at the new price AND
    still recognises existing subscribers — the next webhook for a legacy
    customer would resolve to no SKU and zero their entitlements.

    So `STRIPE_PRICE_<SKU>` may be a comma-separated list. The FIRST entry is
    what new checkouts use; the rest stay resolvable forever. When you change a
    price, prepend the new id rather than replacing the old one.
    """
    raw = os.environ.get(_price_env(sku), "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def price_id_for_sku(sku: str) -> str:
    """The price id NEW checkouts should use (first entry)."""
    ids = price_ids_for_sku(sku)
    return ids[0] if ids else ""


def sku_for_price_id(price_id: str) -> Optional[str]:
    for sku in ent.SKUS:
        if price_id and price_id in price_ids_for_sku(sku):
            return sku
    return None


def configured() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def show_prices() -> bool:
    """Launch gate (John, 2026-07-30): public pages show NO dollar amounts
    until Stripe is deliberately configured with final prices. The
    SHOW_PREVIEW_PRICES env is the local-dev override so pricing exploration
    still renders the ladder — never set it on Cloud Run."""
    if configured():
        return True
    return os.environ.get("SHOW_PREVIEW_PRICES", "").strip().lower() in {"1", "true", "yes"}


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

    # PARTIAL overlap is an upgrade, not a second purchase. Holding NCAAF and
    # checking out the Football Bundle here would leave BOTH billing — the
    # customer paying $25.97 for what $15.98 grants, with the redundant one
    # hidden behind "✓ Included". Send them through the prorated swap instead.
    if _superseded(customer_id, sku):
        raise HTTPException(
            409,
            "That package replaces one you already have — use the upgrade flow "
            "so you're only charged the difference (POST /api/billing/change)",
        )
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


# ── Plan changes (upgrade) ───────────────────────────────────────────────────
# John's call, 2026-08-07. Buying a SKU that supersedes something you already
# hold used to create a SECOND subscription and leave the first one billing —
# e.g. NCAAF ($9.99) + Football Bundle ($15.98) = $25.97/mo for what the bundle
# alone grants, and the redundant card then renders "✓ Included in All-Access"
# so the still-charging subscription becomes invisible on the pricing page.
#
# The fix is to CHANGE the existing subscription's price rather than start a new
# one. Stripe then prorates: it credits the unused remainder of the old plan and
# charges the remainder of the new one, so the customer pays only the difference
# for the time left in the period, and the full new price from the next renewal.


def _superseded(customer_id: str, target_sku: str) -> list[dict]:
    """Active subscription items whose SKU the target SKU fully replaces.

    "Fully replaces" = every slug the held SKU grants is also granted by the
    target. Holding NCAAF and buying the Football Bundle qualifies; holding the
    Bundle and buying NCAAF alone does not (that's a downgrade, not an upgrade).
    """
    stripe = _stripe()
    target_slugs = set(ent.SKUS[target_sku]["slugs"])
    covers_everything = ent.ALL_SLUG in target_slugs
    out = []
    subs = stripe.Subscription.list(customer=customer_id, status="all", limit=100)
    for sub in subs.auto_paging_iter():
        if sub.status not in ACTIVE_STATUSES:
            continue
        for item in sub["items"]["data"]:
            held_sku = sku_for_price_id(item["price"]["id"]) or field(
                field(sub, "metadata", {}), "sku")
            if held_sku not in ent.SKUS or held_sku == target_sku:
                continue
            held_slugs = set(ent.SKUS[held_sku]["slugs"])
            if covers_everything or held_slugs <= target_slugs:
                out.append({
                    "subscription_id": sub.id,
                    "item_id": item["id"],
                    "sku": held_sku,
                    "label": ent.SKUS[held_sku]["label"],
                })
    return out


def plan_change_preview(user: dict, sku: str) -> dict:
    """What buying `sku` would actually do, WITHOUT doing it.

    Returns `kind: "new"` when nothing is superseded (ordinary Checkout), or
    `kind: "upgrade"` plus the exact prorated amount Stripe would charge now.
    The amount is computed by Stripe, not by us — it depends on how much of the
    current period is left, so it is NOT simply the difference in monthly price.
    """
    if sku not in ent.SKUS:
        raise HTTPException(404, f"Unknown package: {sku}")
    price_id = price_id_for_sku(sku)
    if not price_id:
        raise HTTPException(503, f"Package {sku} has no Stripe price configured yet")

    held = set(ent.get_entitlements(user["uid"])["slugs"])
    if ent.covered(held, ent.SKUS[sku]["slugs"]):
        raise HTTPException(409, "Your current packages already include this")

    existing = ent.get_customer(user["uid"])
    customer_id = existing.get("stripe_customer_id") if existing else None
    if not customer_id:
        return {"kind": "new", "sku": sku, "replaces": []}

    superseded = _superseded(customer_id, sku)
    if not superseded:
        return {"kind": "new", "sku": sku, "replaces": []}

    stripe = _stripe()
    primary = superseded[0]
    try:
        preview = stripe.Invoice.create_preview(
            customer=customer_id,
            subscription=primary["subscription_id"],
            subscription_details={
                "items": [{"id": primary["item_id"], "price": price_id}],
                "proration_behavior": "always_invoice",
            },
        )
        due_now = field(preview, "amount_due", 0) or 0
    except Exception as exc:
        logger.error("Proration preview failed for %s -> %s: %s",
                     user["uid"], sku, exc)
        raise HTTPException(503, "Could not price that change right now") from None

    return {
        "kind": "upgrade",
        "sku": sku,
        "label": ent.SKUS[sku]["label"],
        "replaces": [s["label"] for s in superseded],
        "due_now_cents": due_now,
        "then_monthly_cents": ent.display_cents(sku),
    }


def apply_plan_change(user: dict, sku: str) -> dict:
    """Swap the customer onto `sku` in place, prorated.

    Replaces the price on the superseded subscription's EXISTING item — passing
    a bare price without the item id would ADD a second item and bill both,
    which is the exact bug this replaces. Any further superseded subscriptions
    (e.g. CFL + Golf both giving way to All-Access) are cancelled with a
    proration credit.
    """
    plan = plan_change_preview(user, sku)
    if plan["kind"] != "upgrade":
        raise HTTPException(409, "Nothing to upgrade — use checkout instead")

    existing = ent.get_customer(user["uid"])
    customer_id = existing["stripe_customer_id"]
    superseded = _superseded(customer_id, sku)
    price_id = price_id_for_sku(sku)
    stripe = _stripe()

    primary = superseded[0]
    stripe.Subscription.modify(
        primary["subscription_id"],
        items=[{"id": primary["item_id"], "price": price_id}],
        proration_behavior="always_invoice",
        metadata={"uid": user["uid"], "sku": sku},
    )
    for extra in superseded[1:]:
        stripe.Subscription.delete(extra["subscription_id"], prorate=True)

    # Don't wait for the webhook — the customer is looking at the page now.
    # The webhook will recompute again and converge to the same answer.
    _recompute(user["uid"], customer_id)
    return {"status": "ok", "sku": sku, "replaced": [s["label"] for s in superseded]}


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
    unresolved: list[str] = []
    for sub in subs.auto_paging_iter():
        if sub.status not in ACTIVE_STATUSES:
            continue
        for item in sub["items"]["data"]:
            sku = sku_for_price_id(item["price"]["id"])
            if sku is None:
                # Fall back to the SKU we stamped on the subscription at
                # checkout (see create_checkout_session's subscription_data).
                # This is what keeps a customer alive across a price change:
                # the price id they bought at may since have been retired, but
                # the subscription still carries what they actually bought.
                meta_sku = field(field(sub, "metadata", {}), "sku")
                if meta_sku in ent.SKUS:
                    logger.info("Subscription %s price %s unrecognised; resolved "
                                "via metadata to %s", sub.id, item["price"]["id"], meta_sku)
                    sku = meta_sku
            if sku is None:
                # Never silently drop an ACTIVE item — that would rewrite the
                # entitlement doc without it and revoke paid access. Collect and
                # abort below instead.
                logger.error("Subscription %s has unresolvable price %s",
                             sub.id, item["price"]["id"])
                unresolved.append(f"{sub.id}:{item['price']['id']}")
                continue
            spec = ent.SKUS[sku]
            slugs.extend(spec["slugs"])
            packages.append({
                "sku": sku,
                "label": spec["label"],
                "status": sub.status,
                "current_period_end": field(sub, "current_period_end")
                    or field(item, "current_period_end"),
                "subscription_id": sub.id,
            })
    if unresolved:
        # write_entitlements is a FULL overwrite with no merge, so writing now
        # would drop whatever these items grant and lock a paying customer out
        # of every league at once — silently, because the webhook would still
        # return 200. Raise instead: the doc keeps its previous value, Stripe
        # retries and surfaces the failure in the dashboard, and the operator
        # fixes it by adding the retired price id to STRIPE_PRICE_<SKU>.
        raise HTTPException(
            500,
            "Unresolvable subscription price(s); refusing to rewrite entitlements: "
            + ", ".join(unresolved),
        )
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
        uid = field(obj, "client_reference_id")
        customer_id = field(obj, "customer")
        if uid and customer_id:
            email = field(field(obj, "customer_details", {}), "email", "") or ""
            ent.set_customer(uid, email.lower(), customer_id)
            _recompute(uid, customer_id)
        else:
            logger.error("checkout.session.completed missing uid/customer: %s",
                         field(obj, "id"))

    elif etype in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:
        customer_id = field(obj, "customer")
        uid = field(field(obj, "metadata", {}), "uid") or (
            ent.uid_for_stripe_customer(customer_id) if customer_id else None
        )
        if uid and customer_id:
            _recompute(uid, customer_id)
        else:
            logger.error("Subscription event %s: cannot resolve uid (customer=%s)",
                         etype, customer_id)

    return {"received": True, "type": etype}
