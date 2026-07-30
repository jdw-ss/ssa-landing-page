"""
FastAPI application for the SSA apex hub (sportsbookscienceanalytics.com).

Converted 2026-07-30 from a static nginx container into the portfolio's
CUSTOMER auth + billing hub (see docs/adr/0001-apex-auth-billing-hub.md).
The apex now has four jobs:

  1. Marketing pages    — the league directory (/), /help, /pricing.
  2. Customer sign-in   — /signin is the ONE origin where the Google popup
                          runs; every league site links here instead of
                          hosting its own popup. `/__/auth/*` is self-proxied
                          so the OAuth handshake is first-party (ADR-0004
                          pattern from cfl-elo-dashboard).
  3. Cross-subdomain SSO— mints the parent-domain `__session` cookie for ANY
                          authenticated Google user (the league services'
                          admin-only mint is unchanged for internal hosts).
  4. Billing            — /pricing → Stripe Checkout; /account + Customer
                          Portal; the Stripe webhook writes Firestore
                          entitlements that league services enforce.

Unlike the league services there is no public/internal host split here — the
apex is a single public audience. www + apex both map to this service.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import Optional

try:  # Local dev: load .env (gitignored) before anything reads os.environ.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv absent — envs come from the shell/Cloud Run
    pass

import requests
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api import billing
from api import entitlements as ent
from api.auth import (
    SESSION_COOKIE_NAME,
    _init_firebase_admin,
    _is_auth_disabled,
    optional_session_user,
    require_session_user,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="SSA Hub", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

SESSION_COOKIE_DOMAIN = os.environ.get(
    "SESSION_COOKIE_DOMAIN", ".sportsbookscienceanalytics.com"
)
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14  # 14d, the Firebase max


class _SessionLogin(BaseModel):
    idToken: str


class _CheckoutBody(BaseModel):
    sku: str


# ── Open routes ──────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/firebase-config")
async def firebase_config():
    """Public Firebase web-app config for /signin. Not secrets — client-side."""
    return JSONResponse(
        {
            "apiKey":      os.environ.get("FIREBASE_API_KEY", ""),
            "authDomain":  os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
            "projectId":   os.environ.get("FIREBASE_PROJECT_ID", ""),
            "appId":       os.environ.get("FIREBASE_APP_ID", ""),
            "disableAuth": _is_auth_disabled(),
        },
        headers={"Cache-Control": "public, max-age=60"},
    )


# ── Cross-subdomain SSO session cookie (parent-domain .SSA) ──────────────────
# Same three routes as every league service (ADR-0002), with the one product
# change of the paywall build: the mint accepts ANY verified Google user, not
# just ADMIN_EMAILS. Customer authorization is entitlements, not identity —
# league services check Firestore slugs server-side before returning paid data.

@app.post("/api/session")
async def create_session(body: _SessionLogin, response: Response):
    if _is_auth_disabled():
        return {"status": "ok", "disabled": True}

    _init_firebase_admin()
    from firebase_admin import auth as fb_auth

    try:
        decoded = fb_auth.verify_id_token(body.idToken, check_revoked=False)
    except Exception as exc:
        logger.warning("Session create: id-token verification failed: %s", exc)
        raise HTTPException(401, "Invalid ID token") from None

    try:
        cookie_value = fb_auth.create_session_cookie(
            body.idToken,
            expires_in=timedelta(seconds=SESSION_MAX_AGE_SECONDS),
        )
    except Exception as exc:
        logger.error("Failed to mint session cookie: %s", exc)
        raise HTTPException(500, "Could not mint session cookie") from None

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie_value,
        max_age=SESSION_MAX_AGE_SECONDS,
        domain=SESSION_COOKIE_DOMAIN,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    email = (decoded.get("email") or "").lower()
    logger.info("Session minted for %s", email)
    return {"status": "ok", "email": email}


@app.delete("/api/session")
async def delete_session(response: Response):
    """Clear the parent-domain session cookie. Idempotent (always 200)."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        domain=SESSION_COOKIE_DOMAIN,
        path="/",
    )
    return {"status": "ok"}


@app.get("/api/session/exchange")
async def exchange_session(
    session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    """Mint a one-shot custom token from a valid `__session` cookie so a page's
    Firebase SDK can recover the signed-in user without a popup."""
    if _is_auth_disabled():
        raise HTTPException(404, "Auth disabled")
    if not session:
        raise HTTPException(401, "No session cookie")

    _init_firebase_admin()
    from firebase_admin import auth as fb_auth

    try:
        decoded = fb_auth.verify_session_cookie(session, check_revoked=False)
    except Exception:
        raise HTTPException(401, "Invalid session cookie") from None

    try:
        custom_token = fb_auth.create_custom_token(decoded["uid"])
    except Exception as exc:
        logger.error("Custom-token mint failed: %s", exc)
        raise HTTPException(500, "Could not mint custom token") from None

    if isinstance(custom_token, bytes):
        custom_token = custom_token.decode("ascii")
    return {"customToken": custom_token}


# ── Customer profile + entitlements ──────────────────────────────────────────

@app.get("/api/me")
async def me(user: dict = Depends(require_session_user)):
    """The signed-in customer's profile + current access, for /account and the
    account widget. Firestore trouble surfaces as 503 rather than quietly
    showing a paying customer as unsubscribed."""
    try:
        access = ent.get_entitlements(user["uid"])
    except Exception as exc:
        logger.error("Entitlements read failed for %s: %s", user["uid"], exc)
        raise HTTPException(503, "Entitlements temporarily unavailable") from None
    return {
        "uid": user["uid"],
        "email": (user.get("email") or "").lower(),
        "slugs": access["slugs"],
        "packages": access["packages"],
        "billing_configured": billing.configured(),
    }


# ── Billing ──────────────────────────────────────────────────────────────────

@app.get("/api/billing/catalog")
async def billing_catalog(user: Optional[dict] = Depends(optional_session_user)):
    """The pricing page payload. Includes the caller's held slugs (empty when
    signed out) so cards can render 'Active' / 'Included in All-Access'."""
    held: list[str] = []
    if user is not None:
        try:
            held = ent.get_entitlements(user["uid"])["slugs"]
        except Exception:
            held = []  # pricing must render even if Firestore hiccups
    return {
        "catalog": ent.catalog(),
        "held_slugs": held,
        "signed_in": user is not None,
        "billing_configured": billing.configured(),
        "show_prices": billing.show_prices(),
    }


@app.post("/api/billing/checkout")
async def billing_checkout(body: _CheckoutBody, user: dict = Depends(require_session_user)):
    url = await asyncio.to_thread(billing.create_checkout_session, user, body.sku)
    return {"url": url}


@app.post("/api/billing/portal")
async def billing_portal(user: dict = Depends(require_session_user)):
    url = await asyncio.to_thread(billing.create_portal_session, user)
    return {"url": url}


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    return await asyncio.to_thread(billing.handle_webhook, payload, sig)


# ── Self-hosted Firebase auth handler (same-origin sign-in) ──────────────────
# The popup/redirect handshake must be first-party with the page running it
# (cfl-elo-dashboard ADR-0004). /signin is served from this origin, so the
# apex proxies the reserved Firebase Hosting paths exactly like every league
# service does. Requires (one-time, deploy checklist): apex in the Firebase
# authorizedDomains list + https://<apex>/__/auth/handler registered as an
# OAuth redirect URI on the shared ssa-auth-71d16 web client.

_FIREBASE_PROXY_HOST = f"{os.environ.get('FIREBASE_PROJECT_ID', 'ssa-auth-71d16')}.firebaseapp.com"
_PROXY_HOP_HEADERS = {"content-length", "transfer-encoding", "connection", "content-encoding"}


@app.get("/__/auth/{path:path}", include_in_schema=False)
@app.get("/__/firebase/{path:path}", include_in_schema=False)
async def firebase_auth_proxy(path: str, request: Request):
    url = f"https://{_FIREBASE_PROXY_HOST}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    upstream = await asyncio.to_thread(
        requests.get, url, timeout=20, allow_redirects=False,
        headers={"User-Agent": request.headers.get("user-agent", "")},
    )
    headers = {k: v for k, v in upstream.headers.items()
               if k.lower() not in _PROXY_HOP_HEADERS}
    return Response(content=upstream.content, status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type"), headers=headers)


# ── Pages ────────────────────────────────────────────────────────────────────

def _page(name: str, cache: str = "public, max-age=300") -> FileResponse:
    return FileResponse(STATIC_DIR / name, headers={"Cache-Control": cache})


@app.get("/", include_in_schema=False)
async def index():
    return _page("index.html")


@app.get("/help", include_in_schema=False)
@app.get("/help/", include_in_schema=False)
async def help_page():
    return _page("help/index.html")


@app.get("/pricing", include_in_schema=False)
async def pricing_page():
    return _page("pricing.html")


# Auth-state pages must never be cached — the same URL renders differently
# signed in vs out.
@app.get("/signin", include_in_schema=False)
async def signin_page():
    return _page("signin.html", cache="no-store")


@app.get("/account", include_in_schema=False)
async def account_page():
    return _page("account.html", cache="no-store")


@app.get("/robots.txt", include_in_schema=False)
async def robots():
    return FileResponse(STATIC_DIR / "robots.txt", media_type="text/plain")


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_rest(full_path: str):
    """Real static files pass through; anything else falls back to the league
    directory (marketing-site behavior) rather than a bare 404."""
    file_path = STATIC_DIR / full_path
    if file_path.is_file():
        return FileResponse(file_path)
    return _page("index.html")
