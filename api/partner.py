"""inplayLABS partner-launch bridge (ADR-0002).

inplayLABS members buy an SSA tool inside the inplayLABS platform; clicking
Launch there POSTs a short-lived signed assertion (JWT) to
POST /partner/inplaylabs/launch on this hub. We verify it, burn its jti,
grant a time-boxed entitlement, mint the standard parent-domain __session
cookie for a SYNTHETIC Firebase uid, and redirect to the league site. From
that point every existing SSA mechanism — cookie SSO, require_entitlement,
the public paywall — works unchanged.

Load-bearing design facts (full rationale in docs/adr/0002):

- UID NAMESPACE ISOLATION. Partner uids are ``ipl_<sha256(sub)[:24]>``
  (test lane: ``ipltest_``). These uids never enter Stripe checkout, so the
  webhook's full-overwrite ``_recompute`` can never clobber a partner
  entitlement, and the standing "only the Stripe webhook writes
  entitlements/{uid}" ruling survives with one carve-out: this module is
  the ONLY other writer, and ONLY for its own uid prefixes. The guard in
  :func:`grant` enforces that at runtime.

- OPAQUE-ONLY. We request no email from the partner (John, 2026-08-25).
  The signed ``sub`` is the stable account key. The account widget on
  league sites keys on email so partner members see "Sign in" in the
  header; paid tabs still unlock because data gating is pure server-side
  cookie. Cosmetic, revisit later.

- 7-DAY WINDOW (John, 2026-08-25). Session cookie AND entitlement expire
  ``WINDOW_DAYS`` after the most recent launch; every launch from the
  partner portal refreshes both. A cancelled subscriber is denied at next
  launch by the PARTNER (they check entitlement before signing) and coasts
  here at most the window. The sweep endpoint prunes lapsed docs so access
  dies server-side even without a next launch.

- REPLAY. ``jti`` is consumed with a Firestore transactional create in
  ``partner_jti``; a second launch with the same assertion loses the
  transaction and is rejected. Docs carry ``expire_at`` and are pruned by
  the sweep.

Configuration (env; deploy.sh sanity-checks the required ones):

- ``IPL_JWKS_URL`` / ``IPL_ISSUER``     — partner's JWKS + exact issuer.
- ``IPL_TOOL_MAP``  — JSON: tool_id -> {"slug": ..., "dest": ...}, e.g.
  {"ssa-nfl-model": {"slug": "nfl", "dest": "https://nfl.sportsbookscienceanalytics.com/elomodel/"}}
  Destinations are OUR config — a launch destination is never taken from
  the request (their contract, and ours: no open redirect).
- ``IPL_TEST_JWKS_URL`` / ``IPL_TEST_ISSUER`` — optional test lane; absent
  means the test endpoint 404s.
- ``IPL_SWEEP_TOKEN`` — shared secret the Cloud Scheduler sweep presents.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import jwt as pyjwt
import requests as _requests
from google.cloud import firestore

from api import entitlements as ent

logger = logging.getLogger(__name__)

WINDOW_DAYS = int(os.environ.get("IPL_WINDOW_DAYS", "7"))
TEST_WINDOW_DAYS = 1
UID_PREFIX = "ipl_"
TEST_UID_PREFIX = "ipltest_"
# Asymmetric only. "none" and HMAC are rejected by omission — an HS256
# assertion "signed" with the public JWKS bytes must not verify.
ALLOWED_ALGS = ["RS256", "RS384", "RS512", "ES256", "ES384"]
CLOCK_SKEW_SECONDS = 30
# Their contract promises 60-120s assertions; anything longer is
# misconfiguration on the issuing side and gets rejected rather than trusted.
MAX_ASSERTION_LIFETIME_SECONDS = 300

_JTI_COLLECTION = "partner_jti"
_SOURCE = "inplaylabs"


class LaunchError(Exception):
    """Raise with a log-side reason; the browser only ever sees a generic
    message (their contract: detailed diagnostics to secure logs only)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class LaneConfig:
    jwks_url: str
    issuer: str
    uid_prefix: str
    window_days: int


def _lane(test: bool) -> Optional[LaneConfig]:
    if test:
        url = os.environ.get("IPL_TEST_JWKS_URL", "").strip()
        iss = os.environ.get("IPL_TEST_ISSUER", "").strip()
        if not url or not iss:
            return None
        return LaneConfig(url, iss, TEST_UID_PREFIX, TEST_WINDOW_DAYS)
    url = os.environ.get("IPL_JWKS_URL", "").strip()
    iss = os.environ.get("IPL_ISSUER", "").strip()
    if not url or not iss:
        return None
    return LaneConfig(url, iss, UID_PREFIX, WINDOW_DAYS)


def tool_map() -> dict:
    """tool_id -> {"slug": ..., "dest": ...}. Malformed config is a hard
    error at call time — better a 500 in logs than a silent empty map that
    403s every member."""
    raw = os.environ.get("IPL_TOOL_MAP", "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    for tool_id, spec in parsed.items():
        if not isinstance(spec, dict) or "slug" not in spec or "dest" not in spec:
            raise ValueError(f"IPL_TOOL_MAP entry {tool_id!r} needs slug+dest")
    return parsed


# ── JWKS (cached per URL; PyJWKClient handles kid selection + rotation) ──────

_jwks_clients: dict[str, pyjwt.PyJWKClient] = {}


def _jwks_client(url: str) -> pyjwt.PyJWKClient:
    client = _jwks_clients.get(url)
    if client is None:
        client = pyjwt.PyJWKClient(url, cache_keys=True, lifespan=300)
        _jwks_clients[url] = client
    return client


# ── Verification ─────────────────────────────────────────────────────────────

def verify_assertion(token: str, *, test: bool = False) -> dict:
    """Validate the partner assertion and return its claims.

    Enforces: known lane, allowlisted asymmetric alg, signature via the
    lane's JWKS, exact issuer, audience = a configured tool_id, exp/iat
    with bounded skew, bounded total lifetime, and presence of sub/jti/
    tool_id with tool_id == aud and entitlement == data_tool:<tool_id>.
    Raises LaunchError with a log-only reason.
    """
    lane = _lane(test)
    if lane is None:
        raise LaunchError("lane not configured")
    tools = tool_map()
    if not tools:
        raise LaunchError("IPL_TOOL_MAP empty")

    try:
        signing_key = _jwks_client(lane.jwks_url).get_signing_key_from_jwt(token)
    except Exception as exc:  # noqa: BLE001 — reason goes to logs only
        raise LaunchError(f"JWKS/kid resolution failed: {exc}") from exc

    try:
        claims = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=ALLOWED_ALGS,
            issuer=lane.issuer,
            audience=list(tools.keys()),
            leeway=CLOCK_SKEW_SECONDS,
            options={
                "require": ["exp", "iat", "aud", "iss", "sub", "jti"],
            },
        )
    except pyjwt.PyJWTError as exc:
        raise LaunchError(f"JWT rejected: {type(exc).__name__}: {exc}") from exc

    lifetime = int(claims["exp"]) - int(claims["iat"])
    if lifetime <= 0 or lifetime > MAX_ASSERTION_LIFETIME_SECONDS:
        raise LaunchError(f"assertion lifetime {lifetime}s outside policy")

    tool_id = claims.get("tool_id")
    aud = claims["aud"]
    aud_value = aud if isinstance(aud, str) else (aud[0] if len(aud) == 1 else None)
    if tool_id is None or aud_value is None or tool_id != aud_value:
        raise LaunchError(f"tool_id/audience mismatch: {tool_id!r} vs {aud!r}")
    if tool_id not in tools:
        raise LaunchError(f"unknown tool_id {tool_id!r}")

    expected_ent = f"data_tool:{tool_id}"
    if claims.get("entitlement") != expected_ent:
        raise LaunchError(
            f"entitlement claim {claims.get('entitlement')!r} != {expected_ent!r}")

    sub = str(claims["sub"]).strip()
    if not sub:
        raise LaunchError("empty sub")
    if not str(claims["jti"]).strip():
        raise LaunchError("empty jti")

    return claims


# ── jti replay store ─────────────────────────────────────────────────────────

def consume_jti(jti: str, exp_epoch: int, db=None) -> None:
    """Burn the assertion id. First caller wins; everyone else is a replay.

    The doc id is a hash so an attacker-chosen jti can't be a path-hostile
    string; expire_at (exp + skew) lets the sweep prune the collection.
    """
    db = db or ent._firestore()
    doc_id = hashlib.sha256(jti.encode()).hexdigest()
    ref = db.collection(_JTI_COLLECTION).document(doc_id)
    expire_at = dt.datetime.fromtimestamp(
        exp_epoch + CLOCK_SKEW_SECONDS, tz=dt.timezone.utc)

    @firestore.transactional
    def _burn(txn):
        snap = ref.get(transaction=txn)
        if snap.exists:
            raise LaunchError(f"jti replayed: {doc_id[:16]}")
        txn.create(ref, {"expire_at": expire_at,
                         "consumed_at": dt.datetime.now(dt.timezone.utc)})

    _burn(db.transaction())


# ── Identity + entitlement ───────────────────────────────────────────────────

def uid_for_sub(sub: str, *, prefix: str = UID_PREFIX) -> str:
    """Deterministic synthetic Firebase uid for a partner member. Hashed:
    uids are capped at 128 chars and appear in logs, so the partner's raw
    member id never rides in either."""
    return prefix + hashlib.sha256(sub.encode()).hexdigest()[:24]


def grant(uid: str, slug: str, tool_id: str, *, window_days: int, db=None) -> None:
    """Write the time-boxed partner entitlement.

    HARD GUARD: refuses any uid outside the partner namespaces. This module
    is the single non-Stripe writer to entitlements/{uid}, and only for its
    own prefixes — a bug that reached a real customer's doc would be
    clobber-fodder for the webhook recompute AND a rule violation.
    """
    if not (uid.startswith(UID_PREFIX) or uid.startswith(TEST_UID_PREFIX)):
        raise LaunchError(f"grant refused for non-partner uid {uid!r}")
    db = db or ent._firestore()
    now = dt.datetime.now(dt.timezone.utc)
    db.collection("entitlements").document(uid).set({
        "slugs": [slug],
        "packages": [{
            "sku": tool_id,
            "label": f"inplayLABS: {tool_id}",
            "status": "partner",
            "term": f"{window_days}d-launch-window",
        }],
        "source": _SOURCE,
        "tool_id": tool_id,
        "expires_at": now + dt.timedelta(days=window_days),
        "updated_at": now,
    })
    logger.info("ipl grant: uid=%s slug=%s tool=%s window=%dd",
                uid, slug, tool_id, window_days)


def sweep(db=None) -> dict:
    """Prune lapsed partner entitlements and expired jti docs.

    Filters source == inplaylabs client-side after a single-field query so
    no composite index is needed; partner docs number in the hundreds at
    most. Returns counts for the scheduler's logs.
    """
    db = db or ent._firestore()
    now = dt.datetime.now(dt.timezone.utc)
    removed = 0
    for snap in db.collection("entitlements").where("source", "==", _SOURCE).stream():
        data = snap.to_dict() or {}
        expires = data.get("expires_at")
        if expires is not None and expires < now:
            snap.reference.delete()
            removed += 1
    jti_removed = 0
    for snap in db.collection(_JTI_COLLECTION).where("expire_at", "<", now).stream():
        snap.reference.delete()
        jti_removed += 1
    logger.info("ipl sweep: %d entitlements pruned, %d jti pruned",
                removed, jti_removed)
    return {"entitlements_pruned": removed, "jti_pruned": jti_removed}


# ── Session mint (custom token → ID token → session cookie) ─────────────────

_IDENTITY_TOOLKIT = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken"
)


def mint_id_token(uid: str) -> str:
    """create_session_cookie needs an ID token, not a uid (api/app.py mint
    path) — so exchange a custom token server-side via identitytoolkit.
    signInWithCustomToken auto-creates the Firebase user on first launch,
    which IS the partner-member provisioning step: no email, no password,
    no interactive flow."""
    from firebase_admin import auth as fb_auth  # after api.auth init

    custom = fb_auth.create_custom_token(uid)
    api_key = os.environ.get("FIREBASE_API_KEY", "")
    if not api_key:
        raise LaunchError("FIREBASE_API_KEY unset")
    resp = _requests.post(
        _IDENTITY_TOOLKIT,
        params={"key": api_key},
        json={"token": custom.decode() if isinstance(custom, bytes) else custom,
              "returnSecureToken": True},
        timeout=10,
    )
    if resp.status_code != 200:
        # The identitytoolkit error body names the key/SA problem precisely;
        # keep it out of the browser, put it in the logs.
        raise LaunchError(f"signInWithCustomToken {resp.status_code}: {resp.text[:200]}")
    return resp.json()["idToken"]
