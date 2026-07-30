"""
Create the SSA package catalog in Stripe TEST mode.

Reads STRIPE_SECRET_KEY from the environment (or the project's gitignored
.env) and REFUSES anything that isn't an sk_test_ key — this script never
touches live mode. For each SKU in api/entitlements.py it reuses the existing
test Product tagged with metadata.sku, or creates the Product plus a monthly
recurring USD Price at the catalog's display amount (placeholders are fine in
test mode; real prices are a launch-time, live-mode decision).

Idempotent: run it again and it reuses what exists.

Prints ready-to-paste .env lines (STRIPE_PRICE_<SKU>=price_…) when done.

Usage (from the project root):
    python3 -m scripts.stripe_bootstrap_test
"""

from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from api import entitlements as ent
from api.billing import field

# John's Stripe account has Managed Payments (merchant-of-record) enabled by
# default, which requires an eligible tax_code on every product. This is the
# generic "General - Electronically Supplied Services" code — refine per
# product in the dashboard if ever needed.
TAX_CODE = "txcd_10000000"


def main() -> int:
    try:
        import stripe
    except ImportError:
        print("The stripe package isn't installed. Run:\n"
              "    python3 -m pip install stripe python-dotenv")
        return 1

    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key.startswith("sk_test_"):
        print("STRIPE_SECRET_KEY must be a TEST key (sk_test_…) — this script "
              "never touches live mode.\nPut the test key in .env "
              "(see .env.example) and re-run.")
        return 1
    stripe.api_key = key

    existing: dict[str, object] = {}
    for prod in stripe.Product.list(active=True, limit=100).auto_paging_iter():
        sku = field(field(prod, "metadata", {}), "sku")
        if sku:
            existing[sku] = prod

    lines = []
    for sku, spec in ent.SKUS.items():
        cents = ent.display_cents(sku)
        prod = existing.get(sku)
        if prod is None:
            prod = stripe.Product.create(
                name=spec["label"],
                description=spec["blurb"],
                metadata={"sku": sku},
                tax_code=TAX_CODE,
            )
            print(f"created product {prod.id}  {spec['label']}")
        else:
            if not field(prod, "tax_code"):
                stripe.Product.modify(prod.id, tax_code=TAX_CODE)
                print(f"reusing product {prod.id}  {spec['label']} (added tax_code)")
            else:
                print(f"reusing product {prod.id}  {spec['label']}")

        price = None
        for p in stripe.Price.list(product=prod.id, active=True, limit=10):
            if field(field(p, "recurring", {}), "interval") == "month":
                price = p
                break
        if price is None:
            price = stripe.Price.create(
                product=prod.id,
                unit_amount=cents,
                currency="usd",
                recurring={"interval": "month"},
            )
            print(f"  created monthly price {price.id}  ${cents / 100:.2f}")
        else:
            print(f"  reusing monthly price {price.id}  ${(price.unit_amount or 0) / 100:.2f}")
        lines.append(f"STRIPE_PRICE_{sku.upper()}={price.id}")

    print("\nPaste into .env:")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
