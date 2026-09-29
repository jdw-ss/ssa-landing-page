"""Upgrade copy makes no claim about credit for unused time (John, 2026-09-29).

Since 2026-08-26 an upgrade is a NEW Stripe Checkout at the full price of the
new package (billing.apply_plan_change; plan_change_preview quotes that plain
price as due_now). On checkout.session.completed the webhook cancels the
replaced subscription(s) at once with prorate=True. Stripe documents that as
a PENDING proration invoice item tied to the cancelled subscription, and most
likely nothing ever applies it (SESSION_LOG 2026-09-29, the sandbox check in
CLAUDE.md). John's decision: ship with copy that is true either way. You pay
the new package price today and the replaced plan is canceled; nothing about
credit, unused time, proration or paying the difference ("you only ever pay
the difference" described the in-place price swap retired on 2026-08-26).
Restore a credit sentence only once the sandbox check shows where it lands,
and re-read /terms §7 then (legal text, left for John).

Pinned through the pages' own scripts (tests/js harnesses): the /pricing
confirm box and the /account upgrade banner. The foot-notes are static markup,
read from the file. Every 409 detail is read from the api/ source, since
/pricing shows a checkout or upgrade 409 detail verbatim. The Upgrade button
label is pinned in tests/test_partner_billing_guard.py, the checkout
partial-overlap 409 in tests/test_billing_entitlements.py.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys

from tests.test_partner_billing_guard import (
    ACCOUNT, NODE, PRICING, REPO, _by_label, _payload, needs_node)

NEW_PRICE_TODAY = "you pay the new package price today"
PLAN_CANCELED = "your current plan is canceled once checkout completes"
# Any credit claim, and the "where the credit lands" phrasings earlier drafts
# used ("toward your next renewal", "applies to future invoices").
NO_CREDIT_CLAIM = re.compile(
    r"credit|unused time|prorat|difference|next renewal|future invoices?",
    re.I)
API_MODULES = ("app.py", "billing.py", "partner.py", "partner_uid.py")


def _text(html: str) -> str:
    """Visible text: tags dropped in place (the price is an inline <span>)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def _409_details() -> dict[str, list[str]]:
    """Every string literal inside an HTTPException(409, ...) call in api/,
    keyed by file:line. Literal parts only: the f-string holes and the
    joined labels are package names."""
    found: dict[str, list[str]] = {}
    for name in API_MODULES:
        path = REPO / "api" / name
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "HTTPException"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == 409):
                continue
            found[f"{name}:{node.lineno}"] = [
                n.value for arg in node.args[1:] for n in ast.walk(arg)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return found


def test_pricing_foot_notes_describe_the_checkout_upgrade():
    m = re.search(r'<div class="foot-notes" id="foot-notes">(.*?)</div>',
                  PRICING.read_text(), re.S)
    assert m, "foot-notes markup changed?"
    notes = _text(m.group(1))
    assert ("When you upgrade, " + NEW_PRICE_TODAY + ", and " +
            PLAN_CANCELED + ".") in notes, notes
    assert "non-refundable" in notes, "the ToS §7 refund line must stay"


def test_no_credit_claim_left_on_pricing_or_account():
    """The whole file, so the page scripts' own strings and comments count
    too: the renders below don't reach every branch."""
    for page in (PRICING, ACCOUNT):
        hits = [ln.strip() for ln in page.read_text().splitlines()
                if NO_CREDIT_CLAIM.search(ln)]
        assert hits == [], f"{page.name}: {hits}"


def test_no_409_detail_claims_credit():
    details = _409_details()
    # Sanity: the scan still sees the checkout partial-overlap refusal.
    overlap = [k for k, parts in details.items()
               if any("replaces one you already have" in p for p in parts)]
    assert overlap, f"partial-overlap 409 not found in {sorted(details)}"
    hits = {k: parts for k, parts in details.items()
            if any(NO_CREDIT_CLAIM.search(p) for p in parts)}
    assert hits == {}, hits
    assert any(PLAN_CANCELED in p for p in details[overlap[0]]), \
        details[overlap[0]]


@needs_node
def test_upgrade_confirm_box_quotes_the_full_price_and_nothing_else():
    """Holding NCAAF monthly, the Football Bundle's Upgrade button runs the
    page's own buy(): change-preview answers kind=upgrade (the shape
    plan_change_preview returns), and the confirm box renders. "The full
    price" rather than "the new package price": the same box serves the
    same-package monthly -> 6-month switch. "Plus any applicable tax": Stripe
    is merchant of record under Managed Payments and adds sales tax at
    checkout where it applies, so the quoted amount alone is not always what
    the member pays. Exact text, so no credit sentence can come back."""
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
    copy, sep, actions = got["upgrade_box"].partition('<div class="upg-actions">')
    assert sep, got["upgrade_box"]
    assert not NO_CREDIT_CLAIM.search(_text(copy)), _text(copy)
    assert _text(copy) == (
        "Replaces NCAAF Package. You'll pay the full price, "
        f"${cents / 100:.2f}/month plus any applicable tax, at checkout — "
        "nothing changes until you do."), _text(copy)
    assert "Continue to checkout" in actions and "Keep current" in actions


@needs_node
def test_account_upgrade_success_banner_holds_before_the_webhook():
    """The redirect can beat the webhook that cancels the replaced plan and
    writes the new package, and ?upgrade=success does not poll /api/me, so
    at render time neither may have happened: "is being replaced", and the
    new package "can take a minute or two to appear"."""
    out = subprocess.run(
        [NODE, str(REPO / "tests" / "js" / "account_portal.js"), str(ACCOUNT),
         "?upgrade=success"],
        input=json.dumps({"status": 200, "body": None}), capture_output=True,
        text=True, timeout=30, check=True)
    got = json.loads(out.stdout)
    assert not NO_CREDIT_CLAIM.search(got["banner"]), got["banner"]
    assert got["banner"] == (
        "Upgrade received — your previous plan is being replaced. Your new "
        "package can take a minute or two to appear under Your packages.")
    assert got["banner_class"] == "banner show success"
    assert got["navigated"] == ""
