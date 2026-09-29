"""inplayLABS partner members never enter Stripe (John, 2026-09-29).

Partner members (ADR-0002) are synthetic ``ipl_*`` / ``ipltest_*`` uids whose
entitlements/{uid} doc is written by partner.grant(). If one bought through
/pricing, the webhook's full-overwrite _recompute would drop the partner
grants, and the next launch's grant() set() would drop the Stripe slugs. So:

  * every billing route that could create or change Stripe objects 403s a
    partner session BEFORE any Stripe or Firestore call (checkout,
    change-preview, change, portal, and _get_or_create_customer underneath);
  * normal uids behave exactly as before;
  * a Stripe event that resolves to a partner uid still recomputes, but logs
    a WARNING naming the uid;
  * api/partner_uid.py is the ONE prefix source, and both the partner writer
    guard and the billing guards call its predicate;
  * /pricing swaps its purchase controls for the inplayLABS note on a partner
    session (the page's own script, rendered in node), and changes nothing
    for anonymous or normal signed-in visitors.

Stripe and Firestore are faked throughout; no network.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import shutil
import subprocess
import sys
import types
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PARTNER = "ipl_" + "a" * 24
PARTNER_TEST = "ipltest_" + "b" * 24
CUSTOMER = "aBcRealGoogleUid123"
# A real Google-style Firebase uid (28 alphanumerics) that happens to start
# with "ipl": about 1 in 238,000 base62 uids do. It must stay a customer.
IPL_LOOKALIKE = "iplAbcDef" + "1234567890" + "abcdefghi"
TOOL_MAP = json.dumps({
    "ssa-nfl-model": {"slug": "nfl", "dest": "https://example.test/nfl/"},
    "ssa-ncaaf-model": {"slug": "ncaaf", "dest": "https://example.test/ncaaf/"},
    "ssa-cfl-model": {"slug": "cfl", "dest": "https://example.test/cfl/"},
})
IPL_SKUS = ["sport_cfl", "sport_ncaaf", "sport_nfl"]  # catalog order


def _purge_api_modules() -> None:
    for name in [m for m in list(sys.modules) if m == "api" or m.startswith("api.")]:
        del sys.modules[name]


class _SubList:
    def __init__(self, subs):
        self._subs = subs

    def auto_paging_iter(self):
        return iter(self._subs)


@pytest.fixture()
def hub(monkeypatch):
    """Fresh api.* modules, a recording fake Stripe, and in-memory Firestore
    fakes. `calls` lists every Stripe entry point and Firestore touch."""
    monkeypatch.setenv("DISABLE_AUTH", "")
    for sku in ("SPORT_CFL", "SPORT_NCAAF", "BUNDLE_FOOTBALL"):
        monkeypatch.setenv(f"STRIPE_PRICE_{sku}", f"price_{sku.lower()}")
    _purge_api_modules()
    import api.app as app_mod
    import api.billing as billing
    import api.entitlements as ent

    calls: list[str] = []
    customers: dict[str, dict] = {}

    def rec(name, result=None):
        def _f(*a, **k):
            calls.append(name)
            return result
        return _f

    fake = types.SimpleNamespace(
        Customer=types.SimpleNamespace(
            create=rec("Customer.create", types.SimpleNamespace(id="cus_new"))),
        checkout=types.SimpleNamespace(Session=types.SimpleNamespace(
            create=rec("checkout.Session.create",
                       types.SimpleNamespace(url="https://checkout.stripe.com/s")))),
        billing_portal=types.SimpleNamespace(Session=types.SimpleNamespace(
            create=rec("billing_portal.Session.create",
                       types.SimpleNamespace(url="https://billing.stripe.com/p")))),
        Subscription=types.SimpleNamespace(
            list=rec("Subscription.list", _SubList([])),
            cancel=rec("Subscription.cancel"),
            modify=rec("Subscription.modify")),
    )

    def _stripe():
        calls.append("_stripe")
        return fake

    def get_customer(uid):
        calls.append("firestore.get_customer")
        return customers.get(uid)

    def set_customer(uid, email, cid):
        calls.append("firestore.set_customer")
        customers[uid] = {"stripe_customer_id": cid}

    def get_entitlements(uid):
        calls.append("firestore.get_entitlements")
        return {"slugs": [], "packages": []}

    monkeypatch.setattr(billing, "_stripe", _stripe)
    monkeypatch.setattr(ent, "get_customer", get_customer)
    monkeypatch.setattr(ent, "set_customer", set_customer)
    monkeypatch.setattr(ent, "get_entitlements", get_entitlements)

    from fastapi.testclient import TestClient

    def client_as(uid):
        from api.auth import optional_session_user, require_session_user
        app_mod.app.dependency_overrides.clear()
        if uid is not None:
            user = {"uid": uid}
            app_mod.app.dependency_overrides[require_session_user] = lambda: user
            app_mod.app.dependency_overrides[optional_session_user] = lambda: user
        else:
            app_mod.app.dependency_overrides[optional_session_user] = lambda: None
        return TestClient(app_mod.app, raise_server_exceptions=False)

    ns = types.SimpleNamespace(app=app_mod, billing=billing, ent=ent, calls=calls,
                               customers=customers, client_as=client_as)
    yield ns
    app_mod.app.dependency_overrides.clear()


# Every session-bound billing route that can reach Stripe.
ROUTES = [
    ("POST", "/api/billing/checkout", {"json": {"sku": "sport_cfl", "term": "monthly"}}),
    ("GET", "/api/billing/change-preview", {"params": {"sku": "bundle_football"}}),
    ("POST", "/api/billing/change", {"json": {"sku": "bundle_football", "term": "monthly"}}),
    ("POST", "/api/billing/portal", {}),
]


@pytest.mark.parametrize("uid", [PARTNER, PARTNER_TEST])
@pytest.mark.parametrize("method,path,kw", ROUTES, ids=[r[1] for r in ROUTES])
def test_partner_session_is_refused_before_any_stripe_call(hub, uid, method, path, kw):
    # A customer mapping exists, as it would for a pre-guard purchase: without
    # the guard, preview/change/portal would all go on to call Stripe.
    hub.customers[uid] = {"stripe_customer_id": "cus_legacy"}
    r = hub.client_as(uid).request(method, path, **kw)
    assert r.status_code == 403, (path, r.status_code, r.text)
    assert "inplayLABS" in r.json()["detail"]
    assert hub.calls == [], f"{path} touched Stripe/Firestore for {uid}: {hub.calls}"


def test_get_or_create_customer_refuses_a_partner_uid_itself(hub):
    """Last line of defence: a future caller that skips the route guard must
    still never mint a Stripe customer for a partner uid."""
    from fastapi import HTTPException
    for uid in (PARTNER, PARTNER_TEST):
        with pytest.raises(HTTPException) as exc:
            hub.billing._get_or_create_customer({"uid": uid, "email": ""})
        assert exc.value.status_code == 403
    assert hub.calls == []


@pytest.mark.parametrize("uid", [CUSTOMER, IPL_LOOKALIKE])
def test_normal_uid_billing_is_unchanged(hub, uid):
    """The guard must not touch real customers: checkout and preview proceed to
    Stripe, portal opens for a mapped customer, change still 409s when there is
    nothing to upgrade. Includes a real uid that merely starts with "ipl"."""
    c = hub.client_as(uid)

    r = c.post("/api/billing/checkout", json={"sku": "sport_cfl", "term": "monthly"})
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "https://checkout.stripe.com/s"
    assert "Customer.create" in hub.calls and "checkout.Session.create" in hub.calls

    r = c.get("/api/billing/change-preview", params={"sku": "bundle_football"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "new"

    r = c.post("/api/billing/change", json={"sku": "bundle_football", "term": "monthly"})
    assert r.status_code == 409

    r = c.post("/api/billing/portal")
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "https://billing.stripe.com/p"


@pytest.mark.parametrize("uid", [
    IPL_LOOKALIKE,                  # Google-style uid that starts with "ipl"
    "ipl" + "X" * 25,
    "IPL_" + "a" * 24,              # prefixes are case-sensitive
    "Ipl_" + "a" * 24,
    "IPLTEST_" + "a" * 24,
    "ipltestx" + "a" * 20,          # no underscore after "ipltest"
    "iplx_" + "a" * 23,
    "ipl", "ipltest", "", None, 42,
])
def test_is_partner_uid_rejects_lookalikes(uid):
    """Only the exact, lower-case ``ipl_`` / ``ipltest_`` prefixes are partner
    uids. A loosened predicate (startswith("ipl"), a .lower()) would 403 real
    paying customers, so the near misses are pinned here."""
    if isinstance(uid, str) and uid == IPL_LOOKALIKE:
        assert len(uid) == 28 and uid.isalnum()
    import api.partner_uid as partner_uid
    assert partner_uid.is_partner_uid(uid) is False


@pytest.mark.parametrize("uid", ["ipl_", "ipltest_", PARTNER, PARTNER_TEST,
                                 "ipl_x", "ipltest_x"])
def test_is_partner_uid_accepts_both_lanes(uid):
    import api.partner_uid as partner_uid
    assert partner_uid.is_partner_uid(uid) is True


def test_catalog_flags_partner_sessions_only(hub):
    """/pricing reads `partner_member` from the catalog (additive field) to
    swap its controls; it comes from the same predicate as the 403s."""
    assert hub.client_as(PARTNER).get("/api/billing/catalog").json()["partner_member"] is True
    assert hub.client_as(PARTNER_TEST).get("/api/billing/catalog").json()["partner_member"] is True
    assert hub.client_as(CUSTOMER).get("/api/billing/catalog").json()["partner_member"] is False
    anon = hub.client_as(None).get("/api/billing/catalog").json()
    assert anon["partner_member"] is False and anon["signed_in"] is False


def test_catalog_lists_the_skus_inplaylabs_sells(hub, monkeypatch):
    """`partner_skus` (additive) names the catalog SKUs a partner member can
    get on inplayLABS: the single-sport SKUs whose slug a configured
    IPL_TOOL_MAP tool grants. It comes from the live tool map, not the page,
    so a tool that isn't registered yet isn't advertised. Golf, the Football
    Bundle and All-Access are never sold there."""
    monkeypatch.setenv("IPL_TOOL_MAP", TOOL_MAP)
    got = hub.client_as(PARTNER).get("/api/billing/catalog").json()
    assert got["partner_skus"] == IPL_SKUS
    assert hub.client_as(PARTNER_TEST).get(
        "/api/billing/catalog").json()["partner_skus"] == IPL_SKUS

    # Only what is registered: one tool live means one SKU offered.
    monkeypatch.setenv("IPL_TOOL_MAP", json.dumps(
        {"ssa-nfl-model": {"slug": "nfl", "dest": "https://example.test/nfl/"}}))
    assert hub.client_as(PARTNER).get(
        "/api/billing/catalog").json()["partner_skus"] == ["sport_nfl"]

    # Everyone else gets an empty list (the field is additive).
    monkeypatch.setenv("IPL_TOOL_MAP", TOOL_MAP)
    assert hub.client_as(CUSTOMER).get("/api/billing/catalog").json()["partner_skus"] == []
    assert hub.client_as(None).get("/api/billing/catalog").json()["partner_skus"] == []


@pytest.mark.parametrize("raw", ["", "{not json", '{"ssa-nfl-model": {"slug": "nfl"}}'])
def test_catalog_still_renders_for_a_partner_when_the_tool_map_is_unusable(hub, monkeypatch, raw):
    """/pricing must render even if IPL_TOOL_MAP is unset or malformed: the
    partner simply sees nothing offered, rather than a broken page."""
    monkeypatch.setenv("IPL_TOOL_MAP", raw)
    r = hub.client_as(PARTNER).get("/api/billing/catalog")
    assert r.status_code == 200, r.text
    assert r.json()["partner_member"] is True
    assert r.json()["partner_skus"] == []


def test_dev_uid_previews_the_partner_view(monkeypatch):
    """The documented local preview: DISABLE_AUTH=1 DEV_UID=ipl_preview makes
    the stub user a partner, so /pricing renders the inplayLABS note and
    checkout 403s. Without DEV_UID the stub stays an ordinary customer."""
    monkeypatch.setenv("DISABLE_AUTH", "1")
    monkeypatch.setenv("DEV_UID", "ipl_preview")
    _purge_api_modules()
    from fastapi.testclient import TestClient
    import api.app as app_mod
    c = TestClient(app_mod.app, raise_server_exceptions=False)
    assert c.get("/api/billing/catalog").json()["partner_member"] is True
    r = c.post("/api/billing/checkout", json={"sku": "sport_cfl"})
    assert r.status_code == 403 and "inplayLABS" in r.json()["detail"]
    monkeypatch.delenv("DEV_UID")
    assert c.get("/api/billing/catalog").json()["partner_member"] is False


# ── Webhook: never skip, always warn ─────────────────────────────────────────

def _webhook_event(hub, monkeypatch, event):
    recomputes = []
    fake = types.SimpleNamespace(
        Webhook=types.SimpleNamespace(construct_event=lambda p, s, sec: event),
        Subscription=types.SimpleNamespace(cancel=lambda *a, **k: None),
    )
    monkeypatch.setattr(hub.billing, "_stripe", lambda: fake)
    monkeypatch.setattr(hub.billing, "_recompute",
                        lambda uid, cid: recomputes.append((uid, cid)))
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    hub.billing.handle_webhook(b"{}", "sig")
    return recomputes


def _partner_warnings(caplog, uid):
    return [r for r in caplog.records
            if r.name == "api.billing" and r.levelno == logging.WARNING
            and uid in r.getMessage()]


@pytest.mark.parametrize("uid", [PARTNER, PARTNER_TEST])
def test_webhook_warns_on_a_partner_subscription_event_and_still_writes(
        hub, monkeypatch, caplog, uid):
    caplog.set_level(logging.WARNING, logger="api.billing")
    recomputes = _webhook_event(hub, monkeypatch, {
        "type": "customer.subscription.updated",
        "data": {"object": {"customer": "cus_legacy", "metadata": {"uid": uid}}},
    })
    assert recomputes == [(uid, "cus_legacy")], "the write must not be skipped"
    assert _partner_warnings(caplog, uid), "no WARNING naming the partner uid"


def test_webhook_warns_on_a_partner_checkout_completed(hub, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="api.billing")
    recomputes = _webhook_event(hub, monkeypatch, {
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_1", "client_reference_id": PARTNER,
                            "customer": "cus_legacy",
                            "customer_details": {"email": ""}, "metadata": {}}},
    })
    assert recomputes == [(PARTNER, "cus_legacy")]
    assert _partner_warnings(caplog, PARTNER)


@pytest.mark.parametrize("uid", [CUSTOMER, IPL_LOOKALIKE])
def test_webhook_stays_quiet_for_a_normal_customer(hub, monkeypatch, caplog, uid):
    caplog.set_level(logging.WARNING, logger="api.billing")
    recomputes = _webhook_event(hub, monkeypatch, {
        "type": "customer.subscription.updated",
        "data": {"object": {"customer": "cus_1", "metadata": {"uid": uid}}},
    })
    assert recomputes == [(uid, "cus_1")]
    assert not [r for r in caplog.records
                if r.name == "api.billing" and r.levelno >= logging.WARNING]


def test_documented_log_filters_find_the_partner_warnings(hub, monkeypatch, caplog):
    """CLAUDE.md gives the Logs Explorer filters an operator uses to find a
    partner uid in billing. The app logs plain text (api/app.py basicConfig),
    so the level is only a prefix inside textPayload and a severity filter is
    not reliable: the filters match on text. Each documented substring must
    match a WARNING the code really emits, and the webhook's reconcile signal
    must be covered, or a reworded log line silently empties the filter."""
    from fastapi import HTTPException
    filters = re.findall(r'textPayload:"([^"]*inplayLABS partner uid[^"]*)"',
                         (REPO / "CLAUDE.md").read_text())
    assert filters, "CLAUDE.md documents no textPayload filter for the partner WARNINGs"

    caplog.set_level(logging.WARNING, logger="api.billing")
    _webhook_event(hub, monkeypatch, {
        "type": "customer.subscription.updated",
        "data": {"object": {"customer": "cus_legacy", "metadata": {"uid": PARTNER}}},
    })
    webhook_msgs = [r.getMessage() for r in _partner_warnings(caplog, PARTNER)]
    assert webhook_msgs
    with pytest.raises(HTTPException):
        hub.billing._refuse_partner({"uid": PARTNER}, "checkout")
    all_msgs = [r.getMessage() for r in _partner_warnings(caplog, PARTNER)]

    for f in filters:
        assert any(f in m for m in all_msgs), f"documented filter {f!r} matches no WARNING"
    assert any(f in m for f in filters for m in webhook_msgs), \
        "no documented filter finds the webhook's partner WARNING"


# ── One predicate, one prefix source ─────────────────────────────────────────

_PREFIX_RE = re.compile(r"ipl(?:test)?_|ipl\(test\)")


def _prefix_literals(path: Path) -> list[str]:
    """Non-docstring string constants in a module that spell a partner
    prefix (or a regex for one). Docstrings describe the namespace; code must
    get it from api/partner_uid.py."""
    tree = ast.parse(path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings and _PREFIX_RE.search(n.value)]


def test_partner_uid_module_is_the_only_prefix_source_in_api():
    home = REPO / "api" / "partner_uid.py"
    # Non-vacuous: the scanner does find the literals where they belong.
    assert sorted(_prefix_literals(home)) == ["ipl_", "ipltest_"]
    offenders = {p.name: _prefix_literals(p)
                 for p in sorted((REPO / "api").glob("*.py")) if p != home}
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"partner prefix literals outside partner_uid.py: {offenders}"


def test_writer_guard_and_billing_guards_share_the_predicate(monkeypatch):
    """Swap the predicate and both sides follow it: proof that partner.grant()
    and the billing refusals call partner_uid.is_partner_uid rather than
    carrying their own prefix checks."""
    _purge_api_modules()
    import api.billing as billing
    import api.partner as partner
    import api.partner_uid as partner_uid
    from fastapi import HTTPException

    monkeypatch.setattr(partner_uid, "is_partner_uid", lambda uid: uid == "zzz_spy")

    written = {}

    class _Ref:
        def get(self):
            return types.SimpleNamespace(exists=False, to_dict=lambda: None)

        def set(self, data):
            written.update(data)

    db = types.SimpleNamespace(collection=lambda name: types.SimpleNamespace(
        document=lambda uid: _Ref()))
    partner.grant("zzz_spy", "nfl", "ssa-nfl-model", window_days=7, db=db)
    assert written["slugs"] == ["nfl"]
    with pytest.raises(partner.LaunchError):
        partner.grant(PARTNER, "nfl", "ssa-nfl-model", window_days=7, db=db)

    with pytest.raises(HTTPException) as exc:
        billing.create_checkout_session({"uid": "zzz_spy"}, "sport_cfl")
    assert exc.value.status_code == 403


# ── /pricing renders the partner note (the page's own script, in node) ──────

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")
PRICING = REPO / "static" / "pricing.html"
ACCOUNT = REPO / "static" / "account.html"


def _render_pricing(payload: dict, term: str = "monthly") -> dict:
    out = subprocess.run(
        [NODE, str(REPO / "tests" / "js" / "render_pricing.js"), str(PRICING), term],
        input=json.dumps(payload), capture_output=True, text=True, timeout=30,
        check=True)
    return json.loads(out.stdout)


def _payload(**over) -> dict:
    sys.path.insert(0, str(REPO))
    from api import entitlements as ent
    base = {"catalog": ent.catalog(), "held_slugs": [], "held_packages": [],
            "signed_in": True, "partner_member": False, "partner_skus": [],
            "billing_configured": True, "show_prices": True}
    base.update(over)
    return base


def _by_label(cards: list[str]) -> dict[str, str]:
    """Rendered card HTML keyed by its <h2> label (catalog order kept)."""
    out = {}
    for card in cards:
        m = re.search(r"<h2>(.*?)</h2>", card)
        assert m, card
        out[m.group(1)] = card
    return out


INCLUDED = "✓ Included through inplayLABS"
AVAILABLE = "Available through inplayLABS"
NOT_AVAILABLE = "Not available with an inplayLABS sign-in"


@needs_node
def test_pricing_shows_partner_note_instead_of_purchase_controls():
    got = _render_pricing(_payload(partner_member=True, held_slugs=["nfl"],
                                   partner_skus=IPL_SKUS))
    assert got["partner_note"] == {"hidden": False}, "partner note not shown"
    assert len(got["cards"]) == 6
    for card in got["cards"]:
        assert "partner-slot" in card and "inplayLABS" in card
        assert "data-sku" not in card, "a purchase button survived"
        assert 'href="/account"' not in card, "/account bounces partners to /signin"
        assert "Subscribe" not in card and "Upgrade" not in card


@needs_node
def test_partner_cards_only_offer_what_inplaylabs_sells():
    """Each card says what is actually true for a partner member: held, sold
    on inplayLABS (from the catalog's `partner_skus`, i.e. the live tool map),
    or not available on an inplayLABS sign-in at all. inplayLABS sells one
    tool per sport, so Golf, the Football Bundle and All-Access must never
    read as something the member can go and get there."""
    cards = _by_label(_render_pricing(_payload(
        partner_member=True, held_slugs=["nfl"], partner_skus=IPL_SKUS))["cards"])
    assert INCLUDED in cards["NFL Package"]
    assert AVAILABLE in cards["NCAAF Package"]
    assert AVAILABLE in cards["CFL Package"]
    for label in ("Golf Package", "Football Bundle", "All-Access"):
        assert NOT_AVAILABLE in cards[label], label
        assert AVAILABLE not in cards[label], label
    assert not any("Managed through inplayLABS" in c for c in cards.values())

    # Two tools held cover the bundle's slugs: that reads as included.
    cards = _by_label(_render_pricing(_payload(
        partner_member=True, held_slugs=["ncaaf", "nfl"],
        partner_skus=IPL_SKUS))["cards"])
    assert INCLUDED in cards["Football Bundle"]
    assert NOT_AVAILABLE in cards["All-Access"]

    # A tool that isn't registered yet isn't advertised.
    cards = _by_label(_render_pricing(_payload(
        partner_member=True, held_slugs=["nfl"], partner_skus=["sport_nfl"]))["cards"])
    assert NOT_AVAILABLE in cards["NCAAF Package"]


def _note_text() -> str:
    m = re.search(r'<div class="partner-note" id="partner-note"[^>]*>(.*?)</div>',
                  PRICING.read_text(), re.S)
    assert m
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1)))


def test_partner_copy_promises_only_what_inplaylabs_sells():
    """The note and the 403 detail must not send a member to inplayLABS for
    packages inplayLABS doesn't sell: both say packages can't be bought on
    an inplayLABS sign-in, and limit the inplayLABS route to what it
    offers."""
    _purge_api_modules()
    import api.billing as billing
    detail = billing.PARTNER_BILLING_DETAIL
    note = _note_text()
    for text in (detail, note):
        assert "can't be bought" in text and "on an inplayLABS sign-in" in text, text
    assert "inplayLABS offers" in detail
    assert AVAILABLE in note, "the note should point at the per-card availability"


class _Anchors(HTMLParser):
    """Every <a href="/account"> in the static markup, with the ids of its
    open ancestors (script bodies are CDATA, so the page's JS strings are
    not parsed as markup; the rendered cards are checked separately)."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack: list[tuple[str, str]] = []
        self.account_links: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href") == "/account":
            self.account_links.append([i for _, i in self.stack if i])
        if tag not in self.VOID:
            self.stack.append((tag, a.get("id") or ""))

    def handle_endtag(self, tag):
        for k in range(len(self.stack) - 1, -1, -1):
            if self.stack[k][0] == tag:
                del self.stack[k:]
                break


@needs_node
def test_partner_view_has_no_reachable_account_link():
    """/account sends an email-less session to /signin, the Google sign-in
    trap that replaces the partner session. So no /account link may be
    reachable on the partner view: not on the cards, and not in the static
    markup (the foot-notes) unless an ancestor is hidden for a partner.
    Normal visitors keep the foot-notes."""
    parser = _Anchors()
    parser.feed(PRICING.read_text())
    assert parser.account_links, "scanner found nothing: markup changed?"

    partner = _render_pricing(_payload(partner_member=True, held_slugs=["nfl"],
                                       partner_skus=IPL_SKUS))
    for ancestors in parser.account_links:
        assert any(partner["hidden"].get(i) for i in ancestors), \
            f"/account link under {ancestors} is visible on the partner view"
    assert not any('href="/account"' in c for c in partner["cards"])

    normal = _render_pricing(_payload())
    assert normal["hidden"]["foot-notes"] is False
    assert normal["hidden"]["partner-note"] is True


def test_partner_note_keeps_the_page_gutter():
    """At phone width the note must sit inside the same 24px gutters as the
    cards (.grid pads 0 24px); max-width + auto margins alone let its accent
    border run edge to edge."""
    css = PRICING.read_text()
    m = re.search(r"\n\s*\.partner-note \{([^}]*)\}", css)
    assert m, ".partner-note rule not found"
    assert re.search(r"width:\s*calc\(100% - 48px\)", m.group(1)), m.group(1)


# ── Normal visitors: the partner branch changes nothing for them ────────────

@needs_node
def test_pricing_is_unchanged_for_normal_and_anonymous_visitors():
    signed_in = _render_pricing(_payload())
    assert signed_in["partner_note"] == {"hidden": True}
    assert all("data-sku" in c and "Subscribe" in c for c in signed_in["cards"])
    assert not any("partner-slot" in c for c in signed_in["cards"])

    anon = _render_pricing(_payload(signed_in=False))
    assert anon["partner_note"] == {"hidden": True}
    assert all("Sign in to subscribe" in c for c in anon["cards"])


@needs_node
def test_pricing_held_package_states_for_a_normal_customer():
    """The held-package branches a partner flag must never disturb: Active,
    Included in Bundle/All-Access, pay-the-difference upgrades and the
    same-SKU 6-month switch."""
    nfl_monthly = dict(held_slugs=["nfl"],
                       held_packages=[{"sku": "sport_nfl", "term": "monthly"}])
    cards = _by_label(_render_pricing(_payload(**nfl_monthly))["cards"])
    assert '<a class="btn btn-active" href="/account">✓ Active</a>' in cards["NFL Package"]
    assert "Upgrade — pay the difference" in cards["Football Bundle"]
    # All-Access's slug is "all", so no card-level overlap: its button reads
    # Subscribe, and the change-preview on click turns it into the upgrade.
    for label in ("CFL Package", "NCAAF Package", "Golf Package", "All-Access"):
        assert ">Subscribe</button>" in cards[label], label

    cards = _by_label(_render_pricing(_payload(**nfl_monthly), term="6mo")["cards"])
    assert "Switch to 6-month — save 50%" in cards["NFL Package"]
    assert "/6 months" in cards["NFL Package"]

    bundle_6mo = dict(held_slugs=["ncaaf", "nfl"],
                      held_packages=[{"sku": "bundle_football", "term": "6mo"}])
    cards = _by_label(_render_pricing(_payload(**bundle_6mo), term="6mo")["cards"])
    assert "✓ Active · 6-month" in cards["Football Bundle"]
    assert "✓ Included in Football Bundle" in cards["NFL Package"]
    assert "✓ Included in Football Bundle" in cards["NCAAF Package"]

    all_access = dict(held_slugs=["all"],
                      held_packages=[{"sku": "all_access", "term": "monthly"}])
    cards = _by_label(_render_pricing(_payload(**all_access))["cards"])
    assert "✓ Active" in cards["All-Access"]
    for label, card in cards.items():
        if label != "All-Access":
            assert "✓ Included in All-Access" in card, label
    assert not any("partner-slot" in c or "data-sku" in c for c in cards.values())


@needs_node
def test_pricing_launch_gate_is_unchanged():
    """show_prices=false: no amounts and no purchase path, signed in or not."""
    for signed_in in (True, False):
        got = _render_pricing(_payload(show_prices=False, signed_in=signed_in))
        assert all("Launching soon" in c and "Pricing announced at launch" in c
                   for c in got["cards"]), signed_in
        assert got["hidden"]["foot-notes"] is False


# ── /account: Manage billing shows the server's reason on a 403 ─────────────

def _click_manage_billing(status: int, body) -> dict:
    out = subprocess.run(
        [NODE, str(REPO / "tests" / "js" / "account_portal.js"), str(ACCOUNT)],
        input=json.dumps({"status": status, "body": body}), capture_output=True,
        text=True, timeout=30, check=True)
    return json.loads(out.stdout)


@needs_node
def test_manage_billing_shows_the_partner_refusal_not_try_again():
    """A 403 from /api/billing/portal is permanent: "try again in a moment"
    would be wrong. The page shows the server's detail (the inplayLABS
    explanation), the way /pricing's buy() already does."""
    _purge_api_modules()
    import api.billing as billing
    got = _click_manage_billing(403, {"detail": billing.PARTNER_BILLING_DETAIL})
    assert got["banner"] == billing.PARTNER_BILLING_DETAIL
    assert got["navigated"] == "" and got["button_disabled"] is False

    got = _click_manage_billing(403, None)  # no JSON body
    assert got["banner"] and "Try again" not in got["banner"]


@needs_node
def test_manage_billing_other_replies_are_unchanged():
    assert _click_manage_billing(404, {"detail": "x"})["banner"] == \
        "No billing profile yet — subscribe to a package first."
    assert _click_manage_billing(503, {"detail": "x"})["banner"] == \
        "Billing isn't live quite yet."
    assert _click_manage_billing(500, None)["banner"] == \
        "Couldn't open the billing portal. Try again in a moment."
    got = _click_manage_billing(200, {"url": "https://billing.stripe.com/p"})
    assert got["navigated"] == "https://billing.stripe.com/p"
