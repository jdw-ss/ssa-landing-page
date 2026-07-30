"""
Firebase Auth helpers for the SSA apex hub.

The apex (sportsbookscienceanalytics.com) is the CUSTOMER sign-in origin for the
whole SSA family. Unlike every league service's api/auth.py, there is NO
ADMIN_EMAILS allow-list here: any Google account may sign in, hold a session,
and buy packages. Admin gating still exists — but only on the league services'
internal-host routes, which are untouched by this app.

Two dependencies are exposed:

  require_session_user  — verifies the parent-domain `__session` cookie and
                          returns the decoded claims. Used by /api/me and the
                          billing routes (checkout, portal), where the browser
                          sends the HttpOnly cookie automatically.
  optional_session_user — same, but returns None instead of raising, for routes
                          that personalize when signed in and degrade when not.

Configuration:
  - FIREBASE_PROJECT_ID: Firebase project ID (shared `ssa-auth-71d16`).
  - DISABLE_AUTH:        "1"/"true"/"yes" stubs a dev@local user. Local dev
                         only. NEVER set on Cloud Run.

On Cloud Run the runtime SA verifies tokens via ADC; no JSON key involved.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import Cookie, HTTPException, status

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "__session"

_firebase_initialized = False


def _init_firebase_admin() -> None:
    """Lazy-init firebase_admin. Skipped entirely when DISABLE_AUTH=1."""
    global _firebase_initialized
    if _firebase_initialized:
        return
    import firebase_admin
    from firebase_admin import credentials

    if firebase_admin._apps:
        _firebase_initialized = True
        return

    project_id = os.environ.get("FIREBASE_PROJECT_ID")
    if not project_id:
        raise RuntimeError("FIREBASE_PROJECT_ID env var is required for auth.")

    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred, options={"projectId": project_id})
    _firebase_initialized = True
    logger.info("firebase_admin initialized for project %s.", project_id)


def _is_auth_disabled() -> bool:
    return os.environ.get("DISABLE_AUTH", "").strip().lower() in {"1", "true", "yes"}


def _dev_user() -> dict:
    # example.com, not "dev@local" — Stripe's Customer.create rejects emails
    # without a TLD, which broke local test-mode checkout (2026-07-30).
    return {"uid": "dev", "email": "dev@example.com", "name": "Dev User"}


def _decode_session_cookie(session: Optional[str]) -> Optional[dict]:
    """Verify the `__session` cookie; return decoded claims or None."""
    if _is_auth_disabled():
        return _dev_user()
    if not session:
        return None

    _init_firebase_admin()
    from firebase_admin import auth as fb_auth

    try:
        return fb_auth.verify_session_cookie(session, check_revoked=False)
    except Exception as exc:
        logger.info("Session cookie rejected: %s", exc)
        return None


async def require_session_user(
    session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict:
    """FastAPI dependency: the signed-in customer, or 401."""
    decoded = _decode_session_cookie(session)
    if decoded is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not signed in",
        )
    return decoded


async def optional_session_user(
    session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Optional[dict]:
    """FastAPI dependency: the signed-in customer, or None (never raises)."""
    return _decode_session_cookie(session)
