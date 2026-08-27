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
import hashlib
import logging
import os
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Optional

try:  # Local dev: load .env (gitignored) before anything reads os.environ.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv absent — envs come from the shell/Cloud Run
    pass

import requests
from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
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

app = FastAPI(title="SSA Hub", docs_url=None, redoc_url=None, openapi_url=None)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def cache_static_assets(request: Request, call_next):
    """Portfolio cache policy for the /static mount (DESIGN_SYSTEM.md §3):
    StaticFiles sends ETag/Last-Modified but no Cache-Control, leaving
    browsers to heuristic caching. Fill-in only — routes that set their own
    policy keep it. Path is read BEFORE call_next: the Mount rewrites the
    scope's path during routing (nfl-elo-dashboard field lesson, 2026-08-08)."""
    is_static = request.url.path.startswith("/static/")
    response = await call_next(request)
    if is_static and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "public, max-age=300"
    return response

SESSION_COOKIE_DOMAIN = os.environ.get(
    "SESSION_COOKIE_DOMAIN", ".sportsbookscienceanalytics.com"
)
_APEX_HOST = "sportsbookscienceanalytics.com"


@app.middleware("http")
async def canonicalize_www(request: Request, call_next):
    """www → apex 301 (SEO audit 2026-08-13: www served 200 duplicates of
    every page; crawlers don't run the client-side canonicalize scripts).
    GET/HEAD only — anything else (the Stripe webhook) passes through."""
    host = request.headers.get("host", "").split(":")[0]
    if host == f"www.{_APEX_HOST}" and request.method in ("GET", "HEAD"):
        url = f"https://{_APEX_HOST}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"
        return RedirectResponse(url, status_code=301)
    return await call_next(request)
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14  # 14d, the Firebase max


class _SessionLogin(BaseModel):
    idToken: str


class _CheckoutBody(BaseModel):
    sku: str
    term: str = "monthly"  # "monthly" | "6mo" — validated in api/billing.py


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
    # Clickwrap record: /signin states that continuing accepts the Terms, so
    # a successful mint is the acceptance event. Best-effort — never blocks
    # sign-in (record_tos_acceptance swallows Firestore trouble internally).
    await asyncio.to_thread(ent.record_tos_acceptance, decoded["uid"], email)
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
    # This response IS a credential: a replayed response would sign one customer
    # in as another. Every league service already sets these; the apex is the
    # customer path and was missed. (Portfolio hardening 2026-08-07.)
    return JSONResponse(
        {"customToken": custom_token},
        headers={"Cache-Control": "private, no-store", "Vary": "Cookie"},
    )


# ── inplayLABS partner launch (ADR-0002) ────────────────────────────────────
# An inplayLABS member arrives here via a form POST from the partner's Data
# Tools page carrying a short-lived signed assertion. We verify, burn the jti,
# grant a 7-day entitlement to a SYNTHETIC ipl_* uid, mint the standard
# parent-domain session cookie, and redirect to the league site. Errors are
# deliberately generic to the browser; reasons go to logs (their contract).

def _partner_launch(assertion: str, response_cls, *, test: bool):
    from api import partner

    lane = partner._lane(test)
    if lane is None:
        raise HTTPException(404, "Not configured")
    try:
        claims = partner.verify_assertion(assertion, test=test)
        partner.consume_jti(str(claims["jti"]), int(claims["exp"]))
        tool = partner.tool_map()[claims["tool_id"]]
        uid = partner.uid_for_sub(str(claims["sub"]), prefix=lane.uid_prefix)
        partner.grant(uid, tool["slug"], claims["tool_id"],
                      window_days=lane.window_days)
        id_token = partner.mint_id_token(uid)

        _init_firebase_admin()
        from firebase_admin import auth as fb_auth
        window_seconds = lane.window_days * 24 * 60 * 60
        cookie_value = fb_auth.create_session_cookie(
            id_token, expires_in=timedelta(seconds=window_seconds))
    except partner.LaunchError as exc:
        # jti + hashed uid only — never the token, never the raw sub.
        logger.warning("ipl launch rejected: %s", exc.reason)
        raise HTTPException(403, "Launch could not be verified") from None

    resp = response_cls(url=tool["dest"], status_code=303)
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie_value,
        max_age=window_seconds,
        domain=SESSION_COOKIE_DOMAIN,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    # The JS-readable family state cookie: auth.js treats "0" (an explicit
    # prior sign-out anywhere on .SSA) as a veto on session recovery, which
    # would leave a freshly-launched member header-signed-out. "1" matches
    # what auth.js itself writes after a successful sign-in.
    resp.set_cookie(
        key="ssa_auth", value="1", max_age=window_seconds,
        domain=SESSION_COOKIE_DOMAIN, secure=True, httponly=False,
        samesite="lax", path="/",
    )
    logger.info("ipl launch ok: tool=%s uid=%s jti=%s", claims["tool_id"],
                partner.uid_for_sub(str(claims["sub"]), prefix=lane.uid_prefix),
                hashlib.sha256(str(claims["jti"]).encode()).hexdigest()[:16])
    return resp


@app.post("/partner/inplaylabs/launch")
async def inplaylabs_launch(assertion: str = Form(...)):
    return await asyncio.to_thread(
        _partner_launch, assertion, RedirectResponse, test=False)


@app.post("/partner/inplaylabs/launch-test")
async def inplaylabs_launch_test(assertion: str = Form(...)):
    """Test lane: separate issuer/JWKS, ipltest_ uids, 1-day window. 404s
    unless IPL_TEST_* env is configured — absent in prod by default."""
    return await asyncio.to_thread(
        _partner_launch, assertion, RedirectResponse, test=True)


@app.post("/partner/inplaylabs/sweep")
async def inplaylabs_sweep(request: Request):
    """Cloud Scheduler prunes lapsed partner entitlements + jti docs. Guarded
    by a shared secret header; constant-time compare, 404 when unset so the
    route is invisible until configured."""
    from api import partner

    expected = os.environ.get("IPL_SWEEP_TOKEN", "")
    if not expected:
        raise HTTPException(404, "Not configured")
    supplied = request.headers.get("x-ipl-sweep-token", "")
    if not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(403, "Forbidden")
    return await asyncio.to_thread(partner.sweep)


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
    signed out) so cards can render 'Active' / 'Included in All-Access', plus
    per-SKU terms so a monthly holder's card can offer the 6-month upgrade."""
    held: list[str] = []
    held_packages: list[dict] = []
    if user is not None:
        try:
            access = ent.get_entitlements(user["uid"])
            held = access["slugs"]
            held_packages = [
                {"sku": p["sku"], "term": p.get("term", "monthly")}
                for p in access["packages"]
            ]
        except Exception:
            held = []  # pricing must render even if Firestore hiccups
    return {
        "catalog": ent.catalog(),
        "held_slugs": held,
        "held_packages": held_packages,
        "signed_in": user is not None,
        "billing_configured": billing.configured(),
        "show_prices": billing.show_prices(),
    }


@app.post("/api/billing/checkout")
async def billing_checkout(body: _CheckoutBody, user: dict = Depends(require_session_user)):
    url = await asyncio.to_thread(
        billing.create_checkout_session, user, body.sku, body.term)
    return {"url": url}


@app.get("/api/billing/change-preview")
async def billing_change_preview(
    sku: str, term: str = "monthly", user: dict = Depends(require_session_user)
):
    """What buying (`sku`, `term`) would do: an ordinary new subscription, or a
    prorated upgrade that replaces something the customer already has (a bigger
    package, or the same package moving monthly → 6-month). Read-only — the
    pricing page calls this to label the button and show the real amount before
    anyone is charged."""
    plan = await asyncio.to_thread(billing.plan_change_preview, user, sku, term)
    return JSONResponse(plan, headers={"Cache-Control": "private, no-store"})


@app.post("/api/billing/change")
async def billing_change(body: _CheckoutBody, user: dict = Depends(require_session_user)):
    """Start an upgrade checkout: returns the Stripe Checkout URL for the new
    plan. Nothing is charged or cancelled here — the webhook retires the
    superseded subscriptions only after checkout.session.completed, so an
    abandoned checkout leaves the customer untouched (2026-08-26; the previous
    in-place prorated swap charged the stored card instantly and let promo
    discounts ride onto the bigger plan)."""
    result = await asyncio.to_thread(
        billing.apply_plan_change, user, body.sku, body.term)
    return JSONResponse(result, headers={"Cache-Control": "private, no-store"})


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
async def help_page():
    return _page("help/index.html")


@app.get("/pricing", include_in_schema=False)
async def pricing_page():
    return _page("pricing.html")


@app.get("/terms", include_in_schema=False)
async def terms_page():
    return _page("terms/index.html")


@app.get("/privacy", include_in_schema=False)
async def privacy_page():
    return _page("privacy/index.html")


# Trailing-slash variants 301 to the canonical no-slash form (SEO audit
# 2026-08-13: both forms previously served 200 — duplicate content).
@app.get("/help/", include_in_schema=False)
@app.get("/pricing/", include_in_schema=False)
@app.get("/terms/", include_in_schema=False)
@app.get("/privacy/", include_in_schema=False)
@app.get("/signin/", include_in_schema=False)
@app.get("/account/", include_in_schema=False)
async def slash_redirect(request: Request):
    return RedirectResponse(request.url.path.rstrip("/"), status_code=301)


# Auth-state pages must never be cached — the same URL renders differently
# signed in vs out.
@app.get("/signin", include_in_schema=False)
async def signin_page():
    return _page("signin.html", cache="no-store")


@app.get("/account", include_in_schema=False)
async def account_page():
    return _page("account.html", cache="no-store")


@app.get("/og-default.png", include_in_schema=False)
async def og_default():
    """The portfolio's social-preview card, served at the ROOT path because
    every shell across all ten repos hardcodes the absolute apex URL for
    og:image / twitter:image / the schema.org Organization logo. It had been
    referenced since the SEO pass but never actually existed (404 → every
    share preview and the Rich-Results logo broken); found by the
    post-cutover QA sweep 2026-08-27. Long cache: the file is immutable in
    practice and social scrapers cache aggressively anyway."""
    return FileResponse(STATIC_DIR / "og-default.png", media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/robots.txt", include_in_schema=False)
async def robots():
    # LAUNCH GATE OPENED 2026-08-16 (John). `static/robots.txt` is now
    # `Allow: /` + the sitemap reference, and the noindex metas came off
    # `/`, `/pricing`, `/terms` and `/privacy` (`/help` was already indexable).
    # The apex was the last host still closed — every league subdomain had been
    # `Allow: /` with a sitemap since the SEO pass, so the commercial front door
    # was the only thing search engines could not see, with Stripe live since
    # 2026-08-08.
    #
    # `/signin` and `/account` stay OUT, and deliberately in two ways at once:
    # a `noindex` meta AND a `Disallow`. Those are not redundant — a Disallow'd
    # URL can still be indexed title-only from an inbound link precisely because
    # the crawler never fetches it and so never sees the meta. The indexable set
    # is exactly `_PORTFOLIO_URLS` below.
    return FileResponse(STATIC_DIR / "robots.txt", media_type="text/plain")


# ── sitemap.xml ──────────────────────────────────────────────────────────────
#
# The apex is the portfolio's front door, so it serves the SITEMAP INDEX for
# every public host. /sitemap.xml is the index (one <sitemap> entry per league
# host, each service serving its own host's URLs); /sitemap-portfolio.xml is a
# flat urlset of every public URL, so the whole portfolio stays discoverable
# from one file even if an individual league route hasn't been redeployed yet.
# Both are live now and inert while robots.txt disallows — nothing to flip here
# at launch.
# Apex-path form since the 2026-08-27 cutover (legacy <league>.SSA subdomains
# 301 to these prefixes). /epl gets its own entry: the front-door carves it out
# of /soccer, so the soccer sitemap no longer covers it.
_LEAGUE_SITEMAPS = (
    "https://sportsbookscienceanalytics.com/sitemap-portfolio.xml",
    "https://sportsbookscienceanalytics.com/nfl/sitemap.xml",
    "https://sportsbookscienceanalytics.com/ncaaf/sitemap.xml",
    "https://sportsbookscienceanalytics.com/cfl/sitemap.xml",
    "https://sportsbookscienceanalytics.com/nba/sitemap.xml",
    "https://sportsbookscienceanalytics.com/nhl/sitemap.xml",
    "https://sportsbookscienceanalytics.com/soccer/sitemap.xml",
    "https://sportsbookscienceanalytics.com/epl/sitemap.xml",
    "https://sportsbookscienceanalytics.com/golf/sitemap.xml",
)

_PORTFOLIO_URLS = (
    "https://sportsbookscienceanalytics.com/",
    "https://sportsbookscienceanalytics.com/pricing",
    "https://sportsbookscienceanalytics.com/help",
    "https://sportsbookscienceanalytics.com/terms",
    "https://sportsbookscienceanalytics.com/privacy",
    # League entries mirror each surface's declared canonical EXACTLY
    # (trailing slash included) — apex-path form since the 2026-08-27
    # cutover; EPL is deliberately flat, not /soccer/epl.
    "https://sportsbookscienceanalytics.com/nfl",
    "https://sportsbookscienceanalytics.com/nfl/elomodel/",
    "https://sportsbookscienceanalytics.com/nfl/mockdrafts",
    "https://sportsbookscienceanalytics.com/ncaaf/",
    "https://sportsbookscienceanalytics.com/cfl/",
    "https://sportsbookscienceanalytics.com/nba/",
    "https://sportsbookscienceanalytics.com/nhl/",
    "https://sportsbookscienceanalytics.com/soccer/",
    "https://sportsbookscienceanalytics.com/epl/",
    "https://sportsbookscienceanalytics.com/golf",
)

# First path segments owned by league backends on the apex host. Their pages
# (and index metas) live in the league repos; the hub only advertises them.
# Keep in step with the league entries above AND the LB path matcher.
_LEAGUE_PATH_PREFIXES = ("nfl", "ncaaf", "cfl", "golf", "nhl", "nba", "soccer", "epl")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_index():
    entries = "".join(
        f"  <sitemap><loc>{u}</loc></sitemap>\n" for u in _LEAGUE_SITEMAPS
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}"
        "</sitemapindex>\n"
    )
    return Response(content=body, media_type="application/xml")


@app.get("/sitemap-portfolio.xml", include_in_schema=False)
async def sitemap_portfolio():
    """Every public URL across the portfolio in one urlset. Cross-host entries
    require all hosts to be verified in the same Search Console account — they
    are, the league surfaces living at apex paths the owner controls."""
    locs = "".join(f"  <url><loc>{u}</loc></url>\n" for u in _PORTFOLIO_URLS)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{locs}"
        "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_rest(full_path: str):
    """Real static files pass through; anything else is a REAL 404 (SEO audit
    2026-08-13: the old serve-the-homepage fallback made every junk URL a
    200 soft-404 duplicate)."""
    # Unknown `/api/...` paths must 404, not fall through to the HTML shell — a
    # 200-with-HTML soft-404 misleads clients and lets crawlers index junk API
    # URLs. (Portfolio hardening 2026-08-07.)
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    # Path-traversal containment: uvicorn percent-decodes the ASGI path, so a
    # crafted `%2e%2e` escapes STATIC_DIR at the OS layer and would serve the
    # apex's api/ source — billing, entitlements, the Stripe-key-bearing app —
    # to anonymous visitors. Resolve and require the result to stay inside
    # STATIC_DIR. Ports cfl-elo-dashboard/api/app.py.
    static_root = STATIC_DIR.resolve()
    try:
        file_path = (static_root / full_path).resolve()
    except (OSError, ValueError):
        file_path = None
    if file_path is not None and file_path.is_relative_to(static_root) and file_path.is_file():
        return FileResponse(file_path)
    return FileResponse(
        STATIC_DIR / "404.html",
        status_code=404,
        headers={"Cache-Control": "no-cache"},
    )


# ── Security response headers ────────────────────────────────────────────────

# The reserved Firebase handshake paths are the one exception to DENY. The SPA
# FRAMES `/__/auth/iframe` first-party — that is the entire point of the
# self-proxy (ADR-0004) — and firebaseapp.com sends no X-Frame-Options of its
# own, so a blanket DENY would land on the proxied response and break sign-in
# (DENY blocks same-origin framing too: the popup completes while the page
# stays signed out). SAMEORIGIN still refuses every cross-origin framer.
_SPA_FRAMED_PREFIX = "/__/"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Portfolio security headers (John 2026-08-27, after the post-cutover QA
    sweep found none of them on any host). Fill-in only — a route that set its
    own value keeps it. HSTS is UNCONDITIONAL, never gated on
    `request.url.scheme`: behind Cloud Run's proxy the inbound scheme reads as
    http, so the gate would silently ship nothing. It matters here specifically
    because this service mints the `__session` cookie on the PARENT domain
    .sportsbookscienceanalytics.com — a downgrade on ANY host in the family
    exposes it, hence includeSubDomains. Registered LAST = outermost wrapper
    (Starlette builds in reverse), so the www 301 carries them too."""
    # DENY every page; SAMEORIGIN only on the framed Firebase handshake paths.
    # Path read BEFORE call_next and normalized against root_path — the /static
    # Mount rewrites scope's path while routing.
    p = request.url.path
    root = request.scope.get("root_path", "")
    if root and p.startswith(root):
        p = p[len(root):]
    frame_policy = "SAMEORIGIN" if p.startswith(_SPA_FRAMED_PREFIX) else "DENY"
    response = await call_next(request)
    for name, value in (
        ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", frame_policy),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ):
        response.headers.setdefault(name, value)
    return response
