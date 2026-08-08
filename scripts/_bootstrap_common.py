"""Shared Stripe catalog provisioning for the SSA bootstrap scripts.

Both bootstrap scripts (test + live) mint the same catalog: one Product per
SKU in api/entitlements.py, with TWO recurring USD prices each — monthly, and
the 6-month term (interval_count=6). Amounts come from
api/entitlements.py::LAUNCH_PRICE_CENTS, the single source of truth, so the
pricing page and Stripe can never disagree.

Idempotent by construction: Products are tagged metadata.sku and reused;
prices are matched on (interval, interval_count, unit_amount, currency) and
only created when no active price matches. Older active prices on the same
interval are NOT deactivated — they are appended to the printed env line so
existing subscribers keep resolving (see api/billing.py::price_ids_for_sku).
"""

from __future__ import annotations

from api import entitlements as ent
from api.billing import TERMS, field

# John's Stripe account runs Managed Payments (merchant-of-record), which
# requires an eligible tax_code on every product. This is the generic
# "General - Electronically Supplied Services" code — refine per product in
# the dashboard if ever needed.
TAX_CODE = "txcd_10000000"

_RECURRING = {
    "monthly": {"interval": "month", "interval_count": 1},
    "6mo": {"interval": "month", "interval_count": 6},
}


def _price_env(sku: str, term: str) -> str:
    return f"STRIPE_PRICE_{sku.upper()}" + ("" if term == "monthly" else "_6MO")


def ensure_catalog(stripe) -> list[str]:
    """Ensure every (SKU, term) has a Product + Price at the launch amounts.

    Returns ready-to-paste env lines, current price id first and any legacy
    active same-interval prices after it (comma-separated retirement list).
    """
    existing: dict[str, object] = {}
    for prod in stripe.Product.list(active=True, limit=100).auto_paging_iter():
        sku = field(field(prod, "metadata", {}), "sku")
        if sku:
            existing[sku] = prod

    lines: list[str] = []
    for sku, spec in ent.SKUS.items():
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
            # Keep Stripe's copy of the name/blurb/tax_code current with the
            # catalog — invoices and Checkout render these.
            stripe.Product.modify(
                prod.id, name=spec["label"], description=spec["blurb"],
                tax_code=TAX_CODE,
            )
            print(f"reusing product {prod.id}  {spec['label']}")

        prices = list(stripe.Price.list(product=prod.id, active=True, limit=100))

        for term in TERMS:
            want = ent.LAUNCH_PRICE_CENTS[(sku, term)]
            rec = _RECURRING[term]

            def _same_interval(p, rec=rec):
                r = field(p, "recurring", {}) or {}
                return (field(r, "interval") == rec["interval"]
                        and field(r, "interval_count", 1) == rec["interval_count"])

            current = next(
                (p for p in prices
                 if _same_interval(p) and p.unit_amount == want
                 and field(p, "currency") == "usd"),
                None,
            )
            if current is None:
                current = stripe.Price.create(
                    product=prod.id,
                    unit_amount=want,
                    currency="usd",
                    recurring=rec,
                    lookup_key=f"{sku}_{term}",
                    transfer_lookup_key=True,
                )
                print(f"  created {term} price {current.id}  ${want / 100:.2f}")
            else:
                print(f"  reusing {term} price {current.id}  "
                      f"${(current.unit_amount or 0) / 100:.2f}")

            legacy = [p.id for p in prices
                      if _same_interval(p) and p.id != current.id]
            if legacy:
                print(f"    keeping {len(legacy)} legacy id(s) resolvable: "
                      + ", ".join(legacy))
            lines.append(f"{_price_env(sku, term)}=" +
                         ",".join([current.id] + legacy))
    return lines
