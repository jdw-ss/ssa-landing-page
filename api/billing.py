"""
Stripe billing for the SSA paywall: Checkout, Customer Portal, and the webhook
that keeps Firestore entitlements in sync.

Model (John's decisions, 2026-07-30; terms added 2026-08-08):
  - Recurring subscriptions via Stripe Checkout, in one of two TERMS: monthly,
    or a 6-month prepaid cycle (interval_count=6) at 50% off six monthly
    cycles. A term changes what a subscription costs and how often it renews —
    never what it unlocks, so entitlement slugs are term-blind.
  - Stackable à la carte: EACH purchase is its own Stripe subscription; a
    customer's access is the union of slugs across active subscriptions.
  - Bundles/All-Access are just SKUs whose price covers multiple slugs.
  - Upgrades (sport → bundle/all-access, and monthly → 6-month) swap the price
    on the existing subscription, prorated, instead of stacking a second one.
  - No free trials. Promotion codes allowed at checkout; the friend codes are
    100%-off-forever, single-use each (scripts/stripe_bootstrap_live.py).

Configuration (all env):
  STRIPE_SECRET_KEY        — sk_test_… / sk_live_…  (Secret Manager in prod)
  STRIPE_WEBHOOK_SECRET    — whsec_…                (Secret Manager in prod)
  STRIPE_PRICE_<SKU>       — the MONTHLY Stripe Price id for each catalog SKU,
                             e.g. STRIPE_PRICE_SPORT_CFL,
                             STRIPE_PRICE_BUNDLE_FOOTBALL,
                             STRIPE_PRICE_ALL_ACCESS.
  STRIPE_PRICE_<SKU>_6MO   — the 6-MONTH-term Price id for the same SKU. A
                             (SKU, term) with no price id configured 503s at
                             checkout but still displays.
  STRIPE_PORTAL_CONFIG     — optional billing-portal Configuration id (bpc_…)
                             created by the live bootstrap: cancel at period
                             end, no portal-side plan switches. Unset = the
                             account's default portal configuration.
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


# Billing terms. Order matters: later entries outrank earlier ones, and a plan
# change may only hold or raise the term — a 6-month subscriber "switching" to
# monthly would strand months of prepaid credit against a cheaper price, so
# that path is blocked (cancel at period end and rebuy monthly instead).
TERMS = ("monthly", "6mo")
_TERM_RANK = {t: i for i, t in enumerate(TERMS)}
_TERM_NOUN = {"monthly": "monthly", "6mo": "6-month"}


def _validate_term(term: str) -> str:
    if term not in TERMS:
        raise HTTPException(404, f"Unknown billing term: {term}")
    return term


def _price_env(sku: str, term: str = "monthly") -> str:
    base = f"STRIPE_PRICE_{sku.upper()}"
    return base if term == "monthly" else f"{base}_6MO"


def price_ids_for_sku(sku: str, term: str = "monthly") -> list[str]:
    """Every price id that maps to this (SKU, term) — current first, then
    retired ones.

    Stripe Prices are IMMUTABLE: changing an amount mints a NEW price id while
    existing subscriptions keep billing on the old one. With a single id per SKU
    there is no configuration that serves new checkouts at the new price AND
    still recognises existing subscribers — the next webhook for a legacy
    customer would resolve to no SKU and zero their entitlements.

    So `STRIPE_PRICE_<SKU>` (and `…_6MO`) may be a comma-separated list. The
    FIRST entry is what new checkouts use; the rest stay resolvable forever.
    When you change a price, prepend the new id rather than replacing the old
    one.
    """
    raw = os.environ.get(_price_env(sku, term), "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def price_id_for_sku(sku: str, term: str = "monthly") -> str:
    """The price id NEW checkouts should use (first entry)."""
    ids = price_ids_for_sku(sku, term)
    return ids[0] if ids else ""


def sku_term_for_price_id(price_id: str) -> Optional[tuple[str, str]]:
    if not price_id:
        return None
    for sku in ent.SKUS:
        for term in TERMS:
            if price_id in price_ids_for_sku(sku, term):
                return sku, term
    return None


def sku_for_price_id(price_id: str) -> Optional[str]:
    hit = sku_term_for_price_id(price_id)
    return hit[0] if hit else None


def _item_term(item) -> str:
    """A subscription item's term, read off the price's own billing interval —
    authoritative even when the price id is unrecognised (e.g. an item resolved
    via subscription metadata after a repricing)."""
    rec = field(field(item, "price", {}), "recurring", {}) or {}
    if field(rec, "interval") == "month" and field(rec, "interval_count", 1) == 6:
        return "6mo"
    return "monthly"


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


def create_checkout_session(user: dict, sku: str, term: str = "monthly") -> str:
    """Create a subscription Checkout session for one (SKU, term); returns the
    URL."""
    _validate_term(term)
    if sku not in ent.SKUS:
        raise HTTPException(404, f"Unknown package: {sku}")

    price_id = price_id_for_sku(sku, term)
    if not price_id:
        raise HTTPException(
            503, f"Package {sku} has no {_TERM_NOUN[term]} Stripe price configured yet")

    stripe = _stripe()
    customer_id = _get_or_create_customer(user)

    # PARTIAL overlap is an upgrade, not a second purchase. Holding NCAAF and
    # checking out the Football Bundle here would leave BOTH billing — the
    # customer paying for what the bundle alone grants, with the redundant one
    # hidden behind "✓ Included". Same for buying the 6-month term of a package
    # already held monthly. Send them through the prorated swap instead.
    replaceable, blocked = _scan_changes(customer_id, sku, term)
    if blocked:
        raise HTTPException(
            409,
            "You're on the 6-month term for " +
            ", ".join(b["label"] for b in blocked) +
            f" — choose the 6-month {ent.SKUS[sku]['label']} instead, so your "
            "prepaid time counts toward it",
        )
    if replaceable:
        raise HTTPException(
            409,
            "That package replaces one you already have — use the upgrade flow "
            "so you're only charged the difference (POST /api/billing/change)",
        )

    # Refuse pointless double-buys: everything this SKU grants is already held
    # (and, per the scan above, not by anything this purchase would replace).
    held = set(ent.get_entitlements(user["uid"])["slugs"])
    if ent.covered(held, ent.SKUS[sku]["slugs"]):
        raise HTTPException(409, "Your current packages already include this")

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        allow_promotion_codes=True,
        # The friend codes are 100%-off-forever: a $0-forever subscription needs
        # no card, and Stripe only skips collection when nothing will ever be
        # due — paying customers are still asked for one.
        payment_method_collection="if_required",
        client_reference_id=user["uid"],
        subscription_data={"metadata": {"uid": user["uid"], "sku": sku, "term": term}},
        integration_identifier="ssa-paywall-checkout-kvqhmwrz",
        success_url=f"{_base_url()}/account?checkout=success",
        cancel_url=f"{_base_url()}/pricing?checkout=canceled",
    )
    return session.url


# ── Plan changes (upgrade) ───────────────────────────────────────────────────
# John's call, 2026-08-07. Buying a SKU that supersedes something you already
# hold used to create a SECOND subscription and leave the first one billing —
# e.g. NCAAF + Football Bundle both charging for what the bundle alone grants,
# and the redundant card then renders "✓ Included in All-Access" so the
# still-charging subscription becomes invisible on the pricing page.
#
# The fix is to CHANGE the existing subscription's price rather than start a new
# one. Stripe then prorates: it credits the unused remainder of the old plan and
# charges the remainder of the new one, so the customer pays only the difference
# for the time left in the period, and the full new price from the next renewal.
#
# Terms (2026-08-08) ride the same mechanism: monthly → 6-month on the SAME SKU
# is a price swap too, just with the billing anchor reset so the fresh 6-month
# period starts at the moment of purchase (Stripe still credits the unused
# monthly time against it). The one asymmetry: a change may never LOWER the
# term. Swapping a prepaid 6-month package onto a monthly price would strand
# months of credit against a cheaper plan; those attempts are BLOCKED with a
# pointer at the 6-month version of the target.


def _scan_changes(customer_id: str, target_sku: str,
                  term: str = "monthly") -> tuple[list[dict], list[dict]]:
    """Classify the customer's active subscription items against a proposed
    (SKU, term) purchase: (replaceable, blocked).

    replaceable — items the purchase fully supersedes, either because every
    slug they grant is also granted by the target (NCAAF → Football Bundle,
    anything → All-Access) or because it's the same SKU moving up a term
    (monthly → 6-month). These get swapped/cancelled with proration.

    blocked — items the target overlaps but whose term OUTRANKS the target's.
    Proceeding would either strand prepaid credit (same-SKU 6mo → monthly) or
    double-bill a slug (6mo NCAAF + monthly bundle covering ncaaf), so the
    purchase is refused with a pointer at the 6-month target instead.
    """
    stripe = _stripe()
    target_slugs = set(ent.SKUS[target_sku]["slugs"])
    covers_everything = ent.ALL_SLUG in target_slugs
    replaceable: list[dict] = []
    blocked: list[dict] = []
    subs = stripe.Subscription.list(customer=customer_id, status="all", limit=100)
    for sub in subs.auto_paging_iter():
        if sub.status not in ACTIVE_STATUSES:
            continue
        for item in sub["items"]["data"]:
            held_sku = sku_for_price_id(item["price"]["id"]) or field(
                field(sub, "metadata", {}), "sku")
            if held_sku not in ent.SKUS:
                continue
            held_term = _item_term(item)
            held_slugs = set(ent.SKUS[held_sku]["slugs"])
            same_sku = held_sku == target_sku
            slug_subset = covers_everything or held_slugs <= target_slugs
            if same_sku and held_term == term:
                continue  # identical purchase — covered() answers this one
            if not same_sku and not slug_subset:
                continue  # independent packages; they stack
            entry = {
                "subscription_id": sub.id,
                "item_id": item["id"],
                "sku": held_sku,
                "term": held_term,
                "label": ent.SKUS[held_sku]["label"],
            }
            if _TERM_RANK[term] < _TERM_RANK[held_term]:
                blocked.append(entry)
            else:
                replaceable.append(entry)
    # Same-SKU term switches first: when one exists it is the natural primary
    # (the subscription being re-termed), ahead of slug-superseded extras.
    replaceable.sort(key=lambda e: (e["sku"] != target_sku, e["label"]))
    return replaceable, blocked


def _superseded(customer_id: str, target_sku: str, term: str = "monthly") -> list[dict]:
    """Back-compat shim: just the replaceable half of _scan_changes."""
    return _scan_changes(customer_id, target_sku, term)[0]


def _raise_blocked(blocked: list[dict], sku: str) -> None:
    raise HTTPException(
        409,
        "You're on the 6-month term for " +
        ", ".join(b["label"] for b in blocked) +
        f" — choose the 6-month {ent.SKUS[sku]['label']} instead, so your "
        "prepaid time counts toward it",
    )


def plan_change_preview(user: dict, sku: str, term: str = "monthly") -> dict:
    """What buying (`sku`, `term`) would actually do, WITHOUT doing it.

    Returns `kind: "new"` when nothing is superseded (ordinary Checkout), or
    `kind: "upgrade"` plus the exact prorated amount Stripe would charge now.
    The amount is computed by Stripe, not by us — it depends on how much of the
    current period is left, so it is NOT simply the difference in plan price.
    When several subscriptions give way at once, the quote covers the primary
    swap; the extras' unused time arrives as additional credit on the same
    invoice (see apply_plan_change), so the real charge is never MORE than
    quoted.
    """
    _validate_term(term)
    if sku not in ent.SKUS:
        raise HTTPException(404, f"Unknown package: {sku}")
    price_id = price_id_for_sku(sku, term)
    if not price_id:
        raise HTTPException(
            503, f"Package {sku} has no {_TERM_NOUN[term]} Stripe price configured yet")

    existing = ent.get_customer(user["uid"])
    customer_id = existing.get("stripe_customer_id") if existing else None

    replaceable: list[dict] = []
    if customer_id:
        replaceable, blocked = _scan_changes(customer_id, sku, term)
        if blocked:
            _raise_blocked(blocked, sku)

    if not replaceable:
        held = set(ent.get_entitlements(user["uid"])["slugs"])
        if ent.covered(held, ent.SKUS[sku]["slugs"]):
            raise HTTPException(409, "Your current packages already include this")
        return {"kind": "new", "sku": sku, "term": term, "replaces": []}

    stripe = _stripe()
    primary = replaceable[0]
    details = {
        "items": [{"id": primary["item_id"], "price": price_id}],
        "proration_behavior": "always_invoice",
    }
    if primary["term"] != term:
        # Interval changes start a fresh cycle at the moment of purchase; the
        # unused remainder of the old cycle becomes credit on this invoice.
        details["billing_cycle_anchor"] = "now"
    try:
        preview = stripe.Invoice.create_preview(
            customer=customer_id,
            subscription=primary["subscription_id"],
            subscription_details=details,
        )
        due_now = field(preview, "amount_due", 0) or 0
    except Exception as exc:
        logger.error("Proration preview failed for %s -> %s/%s: %s",
                     user["uid"], sku, term, exc)
        raise HTTPException(503, "Could not price that change right now") from None

    then_cents = ent.display_cents(sku, term)
    return {
        "kind": "upgrade",
        "sku": sku,
        "term": term,
        "label": ent.SKUS[sku]["label"],
        "replaces": [s["label"] for s in replaceable],
        # Extras beyond the primary are cancelled with proration on the same
        # invoice — their labels let the UI say "plus credit for X".
        "extra_credit_for": [s["label"] for s in replaceable[1:]],
        "due_now_cents": due_now,
        "then_cents": then_cents,
        "renews_every": _TERM_NOUN[term],
    }


def apply_plan_change(user: dict, sku: str, term: str = "monthly") -> dict:
    """Swap the customer onto (`sku`, `term`) in place, prorated.

    Replaces the price on the superseded subscription's EXISTING item — passing
    a bare price without the item id would ADD a second item and bill both,
    which is the exact bug this replaces.

    Order matters: any FURTHER superseded subscriptions (e.g. CFL + Golf both
    giving way to All-Access) are cancelled FIRST with prorate=True, which
    parks their unused time as pending invoice items; the primary swap's
    always_invoice invoice then sweeps those credits up, so the customer sees
    one invoice with every credit on it. Cancelling after the swap would defer
    those credits to the NEXT renewal — up to six months out on the new terms.
    """
    plan = plan_change_preview(user, sku, term)
    if plan["kind"] != "upgrade":
        raise HTTPException(409, "Nothing to upgrade — use checkout instead")

    existing = ent.get_customer(user["uid"])
    customer_id = existing["stripe_customer_id"]
    replaceable, blocked = _scan_changes(customer_id, sku, term)
    if blocked:
        _raise_blocked(blocked, sku)
    if not replaceable:
        raise HTTPException(409, "Nothing to upgrade — use checkout instead")
    price_id = price_id_for_sku(sku, term)
    stripe = _stripe()

    primary = replaceable[0]
    for extra in replaceable[1:]:
        stripe.Subscription.cancel(extra["subscription_id"], prorate=True)

    modify_kwargs = {}
    if primary["term"] != term:
        modify_kwargs["billing_cycle_anchor"] = "now"
    stripe.Subscription.modify(
        primary["subscription_id"],
        items=[{"id": primary["item_id"], "price": price_id}],
        proration_behavior="always_invoice",
        metadata={"uid": user["uid"], "sku": sku, "term": term},
        **modify_kwargs,
    )

    # Don't wait for the webhook — the customer is looking at the page now.
    # The webhook will recompute again and converge to the same answer.
    _recompute(user["uid"], customer_id)
    return {"status": "ok", "sku": sku, "term": term,
            "replaced": [s["label"] for s in replaceable]}


def create_portal_session(user: dict) -> str:
    """Stripe Customer Portal (cancel / payment method / invoices).

    STRIPE_PORTAL_CONFIG pins the bootstrap-created configuration: cancel takes
    effect at period end (access runs out the paid-for time, John 2026-08-08),
    and portal-side plan switches are off — upgrades go through
    apply_plan_change so SKU metadata and proration behave.
    """
    stripe = _stripe()
    existing = ent.get_customer(user["uid"])
    if not existing or not existing.get("stripe_customer_id"):
        raise HTTPException(404, "No billing profile yet — subscribe to a package first")
    kwargs = {}
    portal_config = os.environ.get("STRIPE_PORTAL_CONFIG", "").strip()
    if portal_config:
        kwargs["configuration"] = portal_config
    session = stripe.billing_portal.Session.create(
        customer=existing["stripe_customer_id"],
        return_url=f"{_base_url()}/account",
        **kwargs,
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
                "term": _item_term(item),
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
