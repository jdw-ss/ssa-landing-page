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
