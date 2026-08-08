"""
Provision the SSA paywall in Stripe LIVE mode — the one-time go-live script.

REFUSES anything that isn't an sk_live_ key. Idempotent: safe to re-run; it
reuses everything it already created. What it ensures, in order:

  1. Catalog — one Product per SKU, monthly + 6-month prices at the launch
     amounts (scripts/_bootstrap_common.py, shared with the test bootstrap).
  2. Friends & Family coupon — 100% off, duration=forever — plus enough
     single-use promotion codes that TEN are unredeemed. Codes are printed at
     the end: each one grants one friend a free subscription until they cancel.
  3. Webhook endpoint at PUBLIC_BASE_URL/api/billing/webhook (the four
     subscription events api/billing.py handles). The signing secret is piped
     STRAIGHT into Secret Manager (gcloud, project golf-data-projects) and
     never printed. If the endpoint already exists its secret is not
     retrievable — to rotate, delete it in the dashboard and re-run.
  4. Customer Portal configuration — cancel takes effect at PERIOD END (the
     customer keeps what they paid for), payment method + email/address
     updates and invoice history on, portal-side plan switches OFF (upgrades
     go through /api/billing/change so proration and SKU metadata behave).

Prints the env lines deploy.sh needs (12× STRIPE_PRICE_*, STRIPE_PORTAL_CONFIG)
plus the promo codes. It does NOT touch the Cloud Run service — deploy.sh does.

Usage (from the project root — the key comes from Secret Manager, so it never
lands in shell history or this script's output):

    STRIPE_SECRET_KEY="$(gcloud secrets versions access latest \
        --secret=stripe-secret-key --project=golf-data-projects)" \
      python3 -m scripts.stripe_bootstrap_live
"""

from __future__ import annotations

import os
import secrets as pysecrets
import shutil
import subprocess
import sys

from scripts._bootstrap_common import ensure_catalog
from api.billing import field

GCP_PROJECT = "golf-data-projects"
WEBHOOK_SECRET_NAME = "stripe-webhook-secret"
WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
]
COUPON_ID = "friends-family-100"
PROMO_TARGET = 10  # unredeemed single-use codes to keep on hand
# No 0/O/1/I — these get read over the phone.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PORTAL_MARKER = {"ssa_portal": "v1"}


def _base_url() -> str:
    return os.environ.get(
        "PUBLIC_BASE_URL", "https://sportsbookscienceanalytics.com").rstrip("/")


def _gcloud() -> str:
    return shutil.which("gcloud") or "/Users/johnwilson/google-cloud-sdk/bin/gcloud"


def _store_secret(name: str, value: str) -> None:
    """Create-or-version a Secret Manager secret from stdin — the value never
    hits argv, a file, or this script's output."""
    gcloud = _gcloud()
    exists = subprocess.run(
        [gcloud, "secrets", "describe", name, "--project", GCP_PROJECT],
        capture_output=True,
    ).returncode == 0
    if exists:
        cmd = [gcloud, "secrets", "versions", "add", name,
               "--project", GCP_PROJECT, "--data-file=-"]
    else:
        cmd = [gcloud, "secrets", "create", name, "--replication-policy=automatic",
               "--project", GCP_PROJECT, "--data-file=-"]
    subprocess.run(cmd, input=value.encode(), check=True)
    print(f"stored {name} in Secret Manager ({'new version' if exists else 'created'})")


def ensure_coupon_and_codes(stripe) -> list[dict]:
    """The forever-100% coupon + enough single-use codes that PROMO_TARGET are
    unredeemed. Returns every code with its redemption state."""
    try:
        coupon = stripe.Coupon.retrieve(COUPON_ID)
        print(f"reusing coupon {coupon.id} ({coupon.percent_off}% off, "
              f"{coupon.duration})")
    except Exception:
        coupon = stripe.Coupon.create(
            id=COUPON_ID,
            percent_off=100,
            duration="forever",
            name="Friends & Family",
        )
        print(f"created coupon {coupon.id} (100% off, forever)")

    codes = list(stripe.PromotionCode.list(
        coupon=coupon.id, limit=100).auto_paging_iter())
    unredeemed = [c for c in codes
                  if field(c, "active") and
                  field(c, "times_redeemed", 0) < (field(c, "max_redemptions") or 1)]
    need = PROMO_TARGET - len(unredeemed)
    for _ in range(max(0, need)):
        code = "SSA-" + "".join(pysecrets.choice(_CODE_ALPHABET) for _ in range(8))
        # API 2026-07-29.dahlia: the coupon rides in a nested `promotion`
        # object; the flat `coupon` param is create-side gone (list still
        # filters by `coupon`).
        created = stripe.PromotionCode.create(
            promotion={"type": "coupon", "coupon": coupon.id},
            code=code, max_redemptions=1)
        codes.append(created)
        print(f"created promo code {created.code}")
    return codes


def ensure_webhook(stripe) -> None:
    url = f"{_base_url()}/api/billing/webhook"
    for ep in stripe.WebhookEndpoint.list(limit=100).auto_paging_iter():
        if field(ep, "url") == url:
            print(f"reusing webhook endpoint {ep.id} ({url})")
            print("  its signing secret was stored at creation; to rotate, "
                  "delete the endpoint in the dashboard and re-run")
            return
    ep = stripe.WebhookEndpoint.create(
        url=url,
        enabled_events=WEBHOOK_EVENTS,
        description="SSA paywall — entitlements sync (api/billing.py)",
    )
    print(f"created webhook endpoint {ep.id} ({url})")
    _store_secret(WEBHOOK_SECRET_NAME, ep.secret)


def ensure_portal_config(stripe) -> str:
    for cfg in stripe.billing_portal.Configuration.list(
            limit=100).auto_paging_iter():
        meta = field(cfg, "metadata", {}) or {}
        if field(meta, "ssa_portal") == PORTAL_MARKER["ssa_portal"]:
            print(f"reusing portal configuration {cfg.id}")
            return cfg.id
    cfg = stripe.billing_portal.Configuration.create(
        features={
            "invoice_history": {"enabled": True},
            "payment_method_update": {"enabled": True},
            "customer_update": {
                "enabled": True,
                "allowed_updates": ["email", "address"],
            },
            # John's call (2026-08-08): cancellation = access until the paid
            # period ends. The webhook keeps entitlements through the final
            # customer.subscription.deleted at period end.
            "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
            # Plan switches stay OUT of the portal — /api/billing/change owns
            # upgrades (proration, metadata, multi-subscription collapse).
            "subscription_update": {"enabled": False},
        },
        default_return_url=f"{_base_url()}/account",
        metadata=PORTAL_MARKER,
    )
    print(f"created portal configuration {cfg.id}")
    return cfg.id


def main() -> int:
    try:
        import stripe
    except ImportError:
        print("The stripe package isn't installed. Run:\n"
              "    python3 -m pip install stripe")
        return 1

    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key.startswith(("sk_live_", "rk_live_")):
        print("STRIPE_SECRET_KEY must be a LIVE key (sk_live_… / rk_live_…).\n"
              "Fetch it from Secret Manager at invocation (see the module "
              "docstring) — do not paste it into a file.")
        return 1
    stripe.api_key = key

    print("── Catalog ─────────────────────────────────────────────")
    lines = ensure_catalog(stripe)
    print("\n── Friends & Family codes ──────────────────────────────")
    codes = ensure_coupon_and_codes(stripe)
    print("\n── Webhook ─────────────────────────────────────────────")
    ensure_webhook(stripe)
    print("\n── Customer Portal ─────────────────────────────────────")
    portal_id = ensure_portal_config(stripe)

    print("\n════ Env for deploy.sh (Cloud Run) ═════════════════════")
    print("\n".join(lines))
    print(f"STRIPE_PORTAL_CONFIG={portal_id}")

    print("\n════ Friend codes (single-use, 100% off forever) ═══════")
    for c in codes:
        used = field(c, "times_redeemed", 0) >= (field(c, "max_redemptions") or 1)
        state = "REDEEMED" if used else ("inactive" if not field(c, "active") else "available")
        print(f"  {field(c, 'code')}  [{state}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
