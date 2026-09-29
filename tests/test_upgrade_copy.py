"""Upgrade copy says what an upgrade actually does (John, 2026-09-29).

Since 2026-08-26 an upgrade is a NEW Stripe Checkout at the full price of the
new package (billing.apply_plan_change; plan_change_preview quotes that plain
price as due_now). Once it is paid, the webhook cancels the replaced
subscription(s) with prorate=True, so their unused time becomes credit on the
customer's Stripe balance, which the next invoice uses. It is not cash back
(ToS §7: payments are non-refundable except where law requires). The old copy
promised "you only ever pay the difference", which described the in-place
price swap retired on 2026-08-26.

Pinned through the pages' own scripts (tests/js harnesses): the /pricing
confirm box and the /account upgrade-success banner. The foot-notes are
static markup, read from the file. The Upgrade button label is pinned in
tests/test_partner_billing_guard.py, the checkout 409 detail in
tests/test_billing_entitlements.py.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

from tests.test_partner_billing_guard import (
    ACCOUNT, NODE, PRICING, REPO, _by_label, _payload, needs_node)

NEW_PRICE_TODAY = "you pay the new package price today"
RENEWAL_CREDIT = "credited toward your next renewal"


def _text(html: str) -> str:
    """Visible text: tags dropped in place (the price is an inline <span>)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def test_pricing_foot_notes_describe_the_checkout_upgrade():
    m = re.search(r'<div class="foot-notes" id="foot-notes">(.*?)</div>',
                  PRICING.read_text(), re.S)
    assert m, "foot-notes markup changed?"
    notes = _text(m.group(1))
    assert ("When you upgrade, " + NEW_PRICE_TODAY + ", and unused time on "
            "your current plan is " + RENEWAL_CREDIT + ".") in notes, notes
    assert "non-refundable" in notes, "the credit must not read as a refund"


def test_no_pay_the_difference_left_on_pricing_or_account():
    """Covers the page script's own strings and comments too, which the
    renders below don't all reach."""
    for page in (PRICING, ACCOUNT):
        hits = re.findall(r".*(?:difference|prorat).*", page.read_text(), re.I)
        assert hits == [], f"{page.name}: {hits}"


@needs_node
def test_upgrade_confirm_box_quotes_the_full_price_and_the_renewal_credit():
    """Holding NCAAF monthly, the Football Bundle's Upgrade button runs the
    page's own buy(): change-preview answers kind=upgrade (the shape
    plan_change_preview returns), and the confirm box renders. "The full
    price" rather than "the new package price": the same box serves the
    same-package monthly -> 6-month switch."""
    sys.path.insert(0, str(REPO))
    from api import entitlements as ent
    cents = ent.display_cents("bundle_football", "monthly")
    payload = _payload(held_slugs=["ncaaf"],
                       held_packages=[{"sku": "sport_ncaaf", "term": "monthly"}])
    payload["_preview"] = {
        "kind": "upgrade", "sku": "bundle_football", "term": "monthly",
        "label": "Football Bundle", "replaces": ["NCAAF Package"],
        "credited": ["NCAAF Package"], "due_now_cents": cents,
        "then_cents": cents, "renews_every": "monthly"}
    out = subprocess.run(
        [NODE, str(REPO / "tests" / "js" / "render_pricing.js"), str(PRICING),
         "monthly", "bundle_football"],
        input=json.dumps(payload), capture_output=True, text=True, timeout=30,
        check=True)
    got = json.loads(out.stdout)
    assert '">Upgrade</button>' in _by_label(got["cards"])["Football Bundle"]
    assert got["upgrade_box"], "no confirm box rendered"
    box = _text(got["upgrade_box"])
    assert box.startswith(
        "Replaces NCAAF Package. You'll pay the full price, "
        f"${cents / 100:.2f}/month, at checkout — nothing changes until you "
        "do. Unused time on NCAAF Package is " + RENEWAL_CREDIT + "."), box
    assert "Continue to checkout" in box and "Keep current" in box


@needs_node
def test_account_upgrade_success_banner_names_the_renewal_credit():
    out = subprocess.run(
        [NODE, str(REPO / "tests" / "js" / "account_portal.js"), str(ACCOUNT),
         "?upgrade=success"],
        input=json.dumps({"status": 200, "body": None}), capture_output=True,
        text=True, timeout=30, check=True)
    got = json.loads(out.stdout)
    assert got["banner"] == (
        "Upgrade applied — unused time on your previous plan is " +
        RENEWAL_CREDIT + ". Your new package is listed below.")
    assert got["banner_class"] == "banner show success"
    assert got["navigated"] == ""
