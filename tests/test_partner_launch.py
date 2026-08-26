"""Pins for the inplayLABS partner-launch bridge (api/partner.py, ADR-0002).

Runs the partner's own go-live checklist as tests: tampered issuer, audience,
user, tool_id, expiration, algorithm confusion, replay, and the uid-namespace
guard that keeps this module out of real customers' entitlement docs.

Signature verification is exercised for real: a locally generated RSA keypair
stands in for inplayLABS, with PyJWKClient monkeypatched to serve its public
half. No network, no Firestore (fakes below).
"""

from __future__ import annotations

import datetime as dt
import time
import uuid

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from api import partner

ISSUER = "https://tracker.inplaylabs.io"
TOOL = "ssa-nfl-model"
TOOL_MAP = ('{"ssa-nfl-model": {"slug": "nfl", '
            '"dest": "https://nfl.sportsbookscienceanalytics.com/elomodel/"}}')


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, pub


@pytest.fixture(autouse=True)
def lane_env(monkeypatch, keypair):
    monkeypatch.setenv("IPL_JWKS_URL", "https://tracker.inplaylabs.io/.well-known/jwks.json")
    monkeypatch.setenv("IPL_ISSUER", ISSUER)
    monkeypatch.setenv("IPL_TOOL_MAP", TOOL_MAP)

    class _FakeSigningKey:
        def __init__(self, pem):
            self.key = pem

    class _FakeJWKSClient:
        def __init__(self, pem):
            self._pem = pem

        def get_signing_key_from_jwt(self, token):
            return _FakeSigningKey(self._pem)

    _, pub = keypair
    monkeypatch.setitem(partner._jwks_clients,
                        "https://tracker.inplaylabs.io/.well-known/jwks.json",
                        _FakeJWKSClient(pub))
    yield


def make_assertion(priv, **overrides):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": TOOL,
        "sub": "member-12345",
        "tool_id": TOOL,
        "entitlement": f"data_tool:{TOOL}",
        "iat": now,
        "exp": now + 90,
        "jti": str(uuid.uuid4()),
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return pyjwt.encode(claims, priv, algorithm="RS256")


# ── Happy path ───────────────────────────────────────────────────────────────

def test_valid_assertion_verifies(keypair):
    priv, _ = keypair
    claims = partner.verify_assertion(make_assertion(priv))
    assert claims["tool_id"] == TOOL
    assert claims["sub"] == "member-12345"


# ── Go-live checklist: tampered/invalid assertions are denied ────────────────

@pytest.mark.parametrize("overrides,why", [
    ({"iss": "https://evil.example.com"}, "wrong issuer"),
    ({"aud": "some-other-tool", "tool_id": "some-other-tool",
      "entitlement": "data_tool:some-other-tool"}, "unknown tool/audience"),
    ({"aud": "ssa-nfl-model", "tool_id": "ssa-cfl-model"}, "tool_id != aud"),
    ({"entitlement": "data_tool:ssa-cfl-model"}, "entitlement mismatch"),
    ({"entitlement": None}, "entitlement missing"),
    ({"exp": int(time.time()) - 120, "iat": int(time.time()) - 210}, "expired"),
    ({"iat": int(time.time()) + 3600, "exp": int(time.time()) + 3690},
     "not yet valid (iat in future beyond skew)"),
    ({"exp": int(time.time()) + 3600}, "lifetime beyond policy"),
    ({"sub": None}, "sub missing"),
    ({"jti": None}, "jti missing"),
    ({"sub": "   "}, "sub blank"),
])
def test_bad_assertions_rejected(keypair, overrides, why):
    priv, _ = keypair
    with pytest.raises(partner.LaunchError):
        partner.verify_assertion(make_assertion(priv, **overrides))


def test_wrong_key_rejected():
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = other.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    with pytest.raises(partner.LaunchError):
        partner.verify_assertion(make_assertion(priv))


def test_symmetric_and_none_algorithms_rejected():
    """Algorithm-confusion defence in depth. PyJWT >= 2.12 already refuses to
    key an HMAC token with PEM public-key bytes (InvalidKeyError at encode —
    verified while writing this test), but our layer must not depend on that:
    the allowlist itself excludes every symmetric alg and "none"."""
    assert not any(a.startswith("HS") for a in partner.ALLOWED_ALGS)
    assert "none" not in [a.lower() for a in partner.ALLOWED_ALGS]

    # An HS256 token with an ordinary shared secret — structurally valid JWT,
    # wrong algorithm family — must be rejected by the decode allowlist.
    now = int(time.time())
    token = pyjwt.encode(
        {"iss": ISSUER, "aud": TOOL, "sub": "x", "tool_id": TOOL,
         "entitlement": f"data_tool:{TOOL}", "iat": now, "exp": now + 90,
         "jti": "j"},
        "shared-secret", algorithm="HS256")
    with pytest.raises(partner.LaunchError):
        partner.verify_assertion(token)


def test_unconfigured_lane_is_a_hard_no(monkeypatch, keypair):
    priv, _ = keypair
    monkeypatch.delenv("IPL_JWKS_URL")
    with pytest.raises(partner.LaunchError):
        partner.verify_assertion(make_assertion(priv))
    assert partner._lane(True) is None  # test lane absent unless configured


# ── Replay: jti burns exactly once ───────────────────────────────────────────

class FakeSnap:
    def __init__(self, exists): self.exists = exists


class FakeTxn:
    def __init__(self, store): self._store = store
    def create(self, ref, data): self._store[ref.doc_id] = data


class FakeRef:
    def __init__(self, store, doc_id): self._store, self.doc_id = store, doc_id
    def get(self, transaction=None): return FakeSnap(self.doc_id in self._store)
    def delete(self): self._store.pop(self.doc_id, None)


class FakeCollection:
    def __init__(self, store): self._store = store
    def document(self, doc_id): return FakeRef(self._store, doc_id)


class FakeDB:
    def __init__(self): self.stores = {}
    def collection(self, name):
        return FakeCollection(self.stores.setdefault(name, {}))
    def transaction(self): return FakeTxn(self.stores.setdefault("partner_jti", {}))


def test_jti_consumed_once(monkeypatch):
    # firestore.transactional wraps fn(txn); replicate minimally
    monkeypatch.setattr(partner.firestore, "transactional", lambda f: f)
    db = FakeDB()
    exp = int(time.time()) + 90
    partner.consume_jti("jti-abc", exp, db=db)
    with pytest.raises(partner.LaunchError):
        partner.consume_jti("jti-abc", exp, db=db)
    partner.consume_jti("jti-other", exp, db=db)  # different id still fine


# ── Identity and the namespace guard ─────────────────────────────────────────

def test_uid_is_deterministic_prefixed_and_opaque():
    a = partner.uid_for_sub("member-12345")
    assert a == partner.uid_for_sub("member-12345")
    assert a.startswith("ipl_") and len(a) <= 128
    assert "member-12345" not in a  # raw partner id never appears
    assert partner.uid_for_sub("member-12345", prefix="ipltest_").startswith("ipltest_")
    assert a != partner.uid_for_sub("member-12346")


def test_grant_refuses_non_partner_uid():
    """The load-bearing rule: this module must never write a real customer's
    entitlement doc — those belong to the Stripe webhook recompute, which
    would clobber (and be clobbered by) any cross-writing."""
    with pytest.raises(partner.LaunchError):
        partner.grant("aBcRealGoogleUid123", "nfl", TOOL, window_days=7, db=FakeDB())


def test_grant_writes_timeboxed_doc():
    db = FakeDB()

    class _Doc(FakeRef):
        def set(self, data): self._store[self.doc_id] = data

    class _Coll(FakeCollection):
        def document(self, doc_id): return _Doc(self._store, doc_id)

    db.collection = lambda name: _Coll(db.stores.setdefault(name, {}))
    uid = partner.uid_for_sub("member-12345")
    partner.grant(uid, "nfl", TOOL, window_days=7, db=db)
    doc = db.stores["entitlements"][uid]
    assert doc["slugs"] == ["nfl"]
    assert doc["source"] == "inplaylabs"
    delta = doc["expires_at"] - dt.datetime.now(dt.timezone.utc)
    assert dt.timedelta(days=6, hours=23) < delta <= dt.timedelta(days=7)


# ── Route-level smoke (TestClient, no network) ───────────────────────────────

def test_routes_registered_and_fail_closed(monkeypatch, keypair):
    """Unconfigured lanes 404 (deploy-dark safety); a garbage assertion on a
    configured lane returns the GENERIC 403, and the sweep is invisible until
    its token is configured."""
    from fastapi.testclient import TestClient
    from api.app import app

    client = TestClient(app, raise_server_exceptions=False)

    # test lane unconfigured -> 404
    r = client.post("/partner/inplaylabs/launch-test", data={"assertion": "x"})
    assert r.status_code == 404

    # prod lane configured (fixture) but garbage token -> generic 403
    r = client.post("/partner/inplaylabs/launch", data={"assertion": "not-a-jwt"})
    assert r.status_code == 403
    assert r.json()["detail"] == "Launch could not be verified"

    # sweep: 404 unset, 403 wrong token
    r = client.post("/partner/inplaylabs/sweep")
    assert r.status_code == 404
    monkeypatch.setenv("IPL_SWEEP_TOKEN", "sweep-secret")
    r = client.post("/partner/inplaylabs/sweep",
                    headers={"x-ipl-sweep-token": "wrong"})
    assert r.status_code == 403
