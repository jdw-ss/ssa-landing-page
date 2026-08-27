"""Public hardening matrix for the apex hub (2026-08-07).

Unlike the league services there is no internal/public host split here — the
whole apex is a single public audience, which is exactly why the surface has to
hold on its own. The contract under test (api/app.py::serve_rest + the session
routes):

  - The catch-all joins caller-supplied path segments onto STATIC_DIR. uvicorn
    percent-decodes the ASGI path, so a crafted `%2e%2e` survives every upstream
    proxy. Without containment an anonymous visitor reads this app's source —
    billing, entitlements, the Stripe-key-bearing app module.
  - Unmatched `/api/...` is a hard 404, never the 200-HTML league directory.
  - The OpenAPI schema enumerates the billing + session routes; it must be off.
  - `GET /api/session/exchange` returns a Firebase custom token — a credential.
    This is the CUSTOMER exchange path, so a shared cache storing it would sign
    one customer in as another.

Conventions follow the league suites (tests/test_public_paywall.py in
cfl-elo-dashboard): env via monkeypatch, purge api.* so module-level state
rebinds.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

# Substrings that appear in this repo's Python source and never in the HTML
# pages the catch-all is allowed to serve.
SOURCE_MARKERS = ("import ", "app = FastAPI", "STRIPE_SECRET", "def ")


def _purge_api_modules() -> None:
    for name in list(sys.modules):
        if name == "api" or name.startswith("api."):
            del sys.modules[name]


@pytest.fixture()
def client(monkeypatch):
    """Real-auth-shaped client: DISABLE_AUTH off (set to an empty value rather
    than deleted — api/app.py calls load_dotenv() at import, and a stray .env
    would otherwise reinstate it) so the session routes take their production
    code path."""
    monkeypatch.setenv("DISABLE_AUTH", "")
    _purge_api_modules()
    from api.app import app

    return TestClient(app)


def _stub_firebase(monkeypatch):
    """Let the exchange reach its success path without a real Firebase app.
    api/app.py binds these names at import, so patch them on the app module."""
    import api.app as app_mod
    from firebase_admin import auth as fb_auth

    monkeypatch.setattr(app_mod, "_is_auth_disabled", lambda: False)
    monkeypatch.setattr(app_mod, "_init_firebase_admin", lambda: None)
    monkeypatch.setattr(fb_auth, "verify_session_cookie",
                        lambda session, check_revoked=False: {"uid": "u-1"})
    monkeypatch.setattr(fb_auth, "create_custom_token", lambda uid: b"tok-1")


# ── Catch-all containment ────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/%2e%2e/api/app.py",
    "/..%2fapi%2fapp.py",
    "/%2e%2e/api/billing.py",
    "/%2e%2e/api/entitlements.py",
    "/../api/auth.py",
    "/%2e%2e/.env",
    "/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
])
def test_catch_all_refuses_path_traversal(client, path):
    """Traversal attempts must never return file content — they fall through to
    the league directory (200 HTML) or 404, never source or secrets."""
    r = client.get(path)
    body = r.text
    for marker in SOURCE_MARKERS:
        assert marker not in body, f"{path} leaked source (found {marker!r})"
    assert "root:x:0:0" not in body
    assert "sk_live" not in body and "sk_test" not in body


def test_real_static_files_still_pass_through(client):
    """The containment check must not break normal asset serving."""
    assert client.get("/js/auth.js").status_code == 200


# ── API namespace never soft-404s ────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/no-such-route", "/api/billing/nope", "/api"])
def test_unmatched_api_path_404s(client, path):
    """A 200-with-HTML soft-404 makes a deleted route look alive, breaks client
    `res.json()`, and lets crawlers index junk API URLs."""
    r = client.get(path)
    assert r.status_code == 404
    assert "application/json" in r.headers.get("content-type", "")
    assert "text/html" not in r.headers.get("content-type", "")


# ── Schema disclosure ────────────────────────────────────────────────────────

def test_openapi_schema_not_exposed(client):
    """The schema enumerates the billing + session routes. Must be off."""
    r = client.get("/openapi.json")
    assert '"openapi"' not in r.text
    assert '"/api/billing/webhook"' not in r.text


# ── Session exchange is a credential ─────────────────────────────────────────

def test_session_exchange_is_not_cacheable(client, monkeypatch):
    """The minted custom token must never be stored by a shared cache."""
    _stub_firebase(monkeypatch)
    client.cookies.set("__session", "cookie-value")
    r = client.get("/api/session/exchange")
    assert r.status_code == 200
    assert r.json()["customToken"] == "tok-1"
    assert "no-store" in r.headers["cache-control"]
    assert "Cookie" in r.headers.get("vary", "")


def test_session_exchange_anonymous_401(client, monkeypatch):
    _stub_firebase(monkeypatch)
    client.cookies.clear()
    assert client.get("/api/session/exchange").status_code == 401


# ── Indexability: the launch gate opened 2026-08-16 ──────────────────────────
#
# Until 2026-08-16 the apex served `robots.txt: Disallow: /` plus a `noindex`
# meta on every page — the deliberate pre-launch gate. Every LEAGUE subdomain
# had been `Allow: /` with a sitemap since the SEO pass, so the apex was the one
# host search engines could not see: the homepage, /pricing and the legal pages,
# i.e. the whole commercial front door, with Stripe live since 2026-08-08.
#
# Both directions now matter and both are one careless edit away:
#   - de-indexing the storefront again silently costs every organic signup;
#   - indexing /signin or /account puts a login page and a private customer
#     area into search results.
# So this pins the exact indexable set rather than "robots.txt is not Disallow".

# The apex URLs the sitemap declares -> the local file that must be indexable.
INDEXABLE_PAGES = {
    "/": "index.html",
    "/pricing": "pricing.html",
    "/help": "help/index.html",
    "/terms": "terms/index.html",
    "/privacy": "privacy/index.html",
}

# Private surfaces. `404.html` is here for the ordinary SEO reason (an indexed
# error page is a junk result), the other two because they are a credential
# entry point and a customer's own account view.
NEVER_INDEXABLE = ("signin.html", "account.html", "404.html")


def _static(name: str) -> str:
    from api.app import STATIC_DIR
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_robots_allows_crawling_and_points_at_the_sitemap(client):
    body = client.get("/robots.txt").text
    assert "Allow: /" in body, "the apex is back to Disallow — the storefront is de-indexed"
    assert "Disallow: /\n" not in body, "a blanket Disallow: / is back"
    assert "Sitemap: https://sportsbookscienceanalytics.com/sitemap.xml" in body


def test_robots_disallows_the_private_surfaces(client):
    """A `noindex` meta alone is NOT enough for these two, and the pair is not
    redundant: a Disallow'd URL can still be indexed title-only from an inbound
    link, precisely because the crawler never fetches it and so never sees the
    meta. Belt AND braces is the correct configuration here."""
    body = client.get("/robots.txt").text
    for path in ("/signin", "/account"):
        assert f"Disallow: {path}" in body, f"{path} is crawlable"


@pytest.mark.parametrize("path,filename", sorted(INDEXABLE_PAGES.items()))
def test_every_sitemap_url_is_actually_indexable(client, path, filename):
    """A page in the sitemap that carries `noindex` is worse than one that is
    absent from both: it actively tells the crawler to drop a URL we submitted."""
    html = _static(filename)
    assert "noindex" not in html.lower(), (
        f"{filename} is in the sitemap but carries a noindex meta")
    assert 'content="index, follow"' in html, (
        f"{filename} does not declare index, follow")


@pytest.mark.parametrize("filename", NEVER_INDEXABLE)
def test_the_private_pages_keep_their_noindex(client, filename):
    assert "noindex" in _static(filename).lower(), (
        f"{filename} lost its noindex meta")


def test_the_sitemap_and_the_indexable_set_agree(client):
    """The two drift independently — a new apex page can land in _PORTFOLIO_URLS
    without a meta, or get a meta without ever being submitted. Either half
    alone is a silent SEO bug, so they are asserted against each other."""
    from api.app import _PORTFOLIO_URLS, _LEAGUE_PATH_PREFIXES
    apex = {u.replace("https://sportsbookscienceanalytics.com", "") or "/"
            for u in _PORTFOLIO_URLS
            if u.startswith("https://sportsbookscienceanalytics.com")}
    # Since the 2026-08-27 apex-path cutover the league surfaces share this
    # host but serve their own shells (and index metas) from their own repos
    # — the hub-side meta guard applies only to hub pages.
    apex = {path for path in apex
            if path.lstrip("/").split("/")[0] not in _LEAGUE_PATH_PREFIXES}
    assert apex == set(INDEXABLE_PAGES), (
        f"the apex sitemap URLs and the indexable set disagree: "
        f"sitemap-only={apex - set(INDEXABLE_PAGES)}, "
        f"meta-only={set(INDEXABLE_PAGES) - apex}")


# ── Security response headers ────────────────────────────────────────────────

def test_security_headers_on_a_real_page(client):
    """The QA sweep 2026-08-27 found none of these anywhere on the platform.
    HSTS is the load-bearing one: the `__session` cookie is minted on the
    PARENT domain, so a downgrade on any host in the family exposes it — which
    is why includeSubDomains is pinned here, not just max-age."""
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_og_default_card_exists_and_is_a_png(client):
    """Every shell in all ten repos hardcodes this absolute URL for og:image,
    twitter:image and the schema.org Organization logo. It 404'd from the SEO
    pass until 2026-08-27, so every social share preview was broken — a
    failure invisible from inside the app. Pin the asset, not just the route."""
    r = client.get("/og-default.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(r.content) > 10_000


def test_firebase_handshake_paths_keep_sameorigin():
    """The SPA frames the self-proxied `/__/auth/iframe` first-party to carry
    popup sign-in events. DENY blocks SAME-ORIGIN framing too, so a blanket
    DENY here breaks sign-in with the exact signature of the 2026-07-31 mint
    incident: popup completes, page stays signed out. Pin both branches."""
    import api.app as app_mod
    assert app_mod._SPA_FRAMED_PREFIX == "/__/"

    def policy(path, root=""):
        p = path[len(root):] if root and path.startswith(root) else path
        return "SAMEORIGIN" if p.startswith(app_mod._SPA_FRAMED_PREFIX) else "DENY"

    assert policy("/__/auth/iframe") == "SAMEORIGIN"
    assert policy("/__/firebase/init.json") == "SAMEORIGIN"
    assert policy("/") == "DENY"
    assert policy("/api/health") == "DENY"
