"""The /pricing <noscript> price ladder is static text — pin it to the table.

No-JS visitors never see the cards (they render from /api/billing/catalog),
so static/pricing.html carries a hand-written ladder naming every package and
price. It cannot read the catalog, and the CLAUDE.md gotcha asks for it to be
updated in the same commit as LAUNCH_PRICE_CENTS; until 2026-10-14 nothing
checked that it was. This parses the shipped block and fails the suite when
a sport is missing, an amount in the table is absent, or an amount quoted in
the ladder is no longer in the table (a stale price).
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.app import STATIC_DIR  # noqa: E402
from api import entitlements as ent  # noqa: E402


def _noscript_text() -> str:
    html = (STATIC_DIR / "pricing.html").read_text(encoding="utf-8")
    m = re.search(r"<noscript>(.*?)</noscript>", html, re.S)
    assert m, "/pricing lost its <noscript> ladder"
    body = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()


def test_noscript_ladder_matches_launch_prices():
    text = _noscript_text()
    # Every sellable sport is named (label minus " Package": CFL, Golf, NBA…),
    for sku, spec in ent.SKUS.items():
        if spec["kind"] == "sport":
            name = spec["label"].removesuffix(" Package")
            assert re.search(rf"\b{re.escape(name)}\b", text), (
                f"{name} is missing from the <noscript> ladder")
    for label in ("Football Bundle", "All-Access"):
        assert label in text, f"{label} is missing from the <noscript> ladder"
    # ...every amount in the table appears...
    want = {f"${cents / 100:.2f}" for cents in ent.LAUNCH_PRICE_CENTS.values()}
    for amount in sorted(want):
        assert amount in text, f"{amount} is missing from the <noscript> ladder"
    # ...and nothing in the ladder quotes an amount the table no longer has.
    quoted = set(re.findall(r"\$\d[\d,]*\.\d{2}", text))
    assert quoted <= want, (
        f"stale amounts in the <noscript> ladder: {sorted(quoted - want)}")
