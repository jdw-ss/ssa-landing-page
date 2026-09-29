"""Upgrade copy says what an upgrade actually does (John, 2026-09-29).

Since 2026-08-26 an upgrade is a NEW Stripe Checkout at the full price of the
new package (billing.apply_plan_change; plan_change_preview quotes that plain
price as due_now). Once it is paid, the webhook cancels the replaced
subscription(s) with prorate=True. Stripe documents that as generating "a
proration invoice item that credits remaining unused time"; it is a PENDING
invoice item tied to the cancelled subscription, not customer-balance credit,
and which invoice (if any) ever applies it is unverified until the sandbox
check in CLAUDE.md runs. So the copy says the unused time is credited to the
member's account and makes no promise about the next renewal or future
invoices. It is not cash back either (ToS §7: payments are non-refundable
except where law requires). The old copy promised "you only ever pay the
difference", which described the in-place price swap retired on 2026-08-26.

Pinned through the pages' own scripts (tests/js harnesses): the /pricing
confirm box and the /account upgrade-success banner. The foot-notes are
static markup, read from the file. The Upgrade button label is pinned in
tests/test_partner_billing_guard.py, the checkout 409 details in
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
ACCOUNT_CREDIT = "credited to your account"
# Claims about WHERE the credit lands. Unverified (see the docstring):
# restore one only once the sandbox check shows it is true.
UNVERIFIED_LANDING = re.compile(r"next renewal|future invoices?", re.I)


def _text(html: str) -> str:
    """Visible text: tags dropped in place (the price is an inline <span>)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def test_pricing_foot_notes_describe_the_checkout_upgrade():
    m = re.search(r'<div class="foot-notes" id="foot-notes">(.*?)</div>',
                  PRICING.read_text(), re.S)
    assert m, "foot-notes markup changed?"
    notes = _text(m.group(1))
    assert ("When you upgrade, " + NEW_PRICE_TODAY + ", and unused time on "
            "your current plan is " + ACCOUNT_CREDIT + ".") in notes, notes
    assert "non-refundable" in notes, "the credit must not read as a refund"


def test_no_pay_the_difference_left_on_pricing_or_account():
    """Covers the page script's own strings and comments too, which the
    renders below don't all reach."""
    for page in (PRICING, ACCOUNT):
        hits = re.findall(r".*(?:difference|prorat).*", page.read_text(), re.I)
        assert hits == [], f"{page.name}: {hits}"


def test_no_page_promises_where_the_upgrade_credit_lands():
    """"Toward your next renewal" (and the earlier "applies to future
    invoices") promise that the replaced plan's credit reaches a later
    invoice. The cancel leaves a pending proration item tied to the cancelled
    subscription, and whether the new plan's renewals ever pick it up is
    unverified, so neither page may say so."""
    for page in (PRICING, ACCOUNT):
        hits = [ln.strip() for ln in page.read_text().splitlines()
                if UNVERIFIED_LANDING.search(ln)]
        assert hits == [], f"{page.name}: {hits}"


@needs_node
def test_upgrade_confirm_box_quotes_the_full_price_and_the_credit():
    """Holding NCAAF monthly, the Football Bundle's Upgrade button runs the
    page's own buy(): change-preview answers kind=upgrade (the shape
    plan_change_preview returns), and the confirm box renders. "The full
    price" rather than "the new package price": the same box serves the
    same-package monthly -> 6-month switch. "Plus any applicable tax": Stripe
    is merchant of record under Managed Payments and adds sales tax at
    checkout where it applies, so the quoted amount alone is not always what
    the member pays."""
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
        f"${cents / 100:.2f}/month plus any applicable tax, at checkout — "
        "nothing changes until you do. Unused time on NCAAF Package is " +
        ACCOUNT_CREDIT + "."), box
    assert not UNVERIFIED_LANDING.search(box), box
    assert "Continue to checkout" in box and "Keep current" in box


@needs_node
def test_account_upgrade_success_banner_names_the_credit_in_future_tense():
    """The redirect can beat the webhook that cancels the replaced plan, so
    at render time the credit may not exist yet: "will be credited"."""
    out = subprocess.run(
        [NODE, str(REPO / "tests" / "js" / "account_portal.js"), str(ACCOUNT),
         "?upgrade=success"],
        input=json.dumps({"status": 200, "body": None}), capture_output=True,
        text=True, timeout=30, check=True)
    got = json.loads(out.stdout)
    assert got["banner"] == (
        "Upgrade applied — unused time on your previous plan will be " +
        ACCOUNT_CREDIT + ". Your new package is listed below.")
    assert got["banner_class"] == "banner show success"
    assert got["navigated"] == ""
