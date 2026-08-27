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
            '"dest": "https://sportsbookscienceanalytics.com/nfl/elomodel/"}}')


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


# ── The account widget's partner branch tracks partner.py's namespaces ──────

def test_account_widget_prefixes_match_partner_module():
    """account.js renders the partner chip for uids matching /^ipl(test)?_/.
    If UID_PREFIX/TEST_UID_PREFIX ever change in api/partner.py, the widget
    (vendored to every surface) silently regresses to the "Sign in" trap for
    partner members — this pin makes that a test failure instead."""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent
          / "static" / "js" / "account.js").read_text()
    assert "/^ipl(test)?_/" in js
    assert "renderPartnerMember" in js
    # the regex must actually cover both python-side prefixes
    import re
    for prefix in (partner.UID_PREFIX, partner.TEST_UID_PREFIX):
        assert re.match(r"^ipl(test)?_", prefix + "abc"), prefix


def test_rejection_log_names_the_offending_issuer(keypair):
    """A bare "Invalid issuer" cost a partner round-trip on IPL's first live
    launch: the signature had already verified, so exactly one string was
    wrong and neither side could see which. The rejection reason must name
    the received AND the expected issuer — and must never leak `sub`."""
    priv, _ = keypair
    # The exact shape of IPL's first live failure: signature verifies, one
    # string differs. (Prod's configured issuer carries a trailing slash;
    # this fixture's does not, so spell the mismatch out rather than deriving
    # it from ISSUER.)
    wrong = ISSUER + "/"
    token = make_assertion(priv, iss=wrong)
    with pytest.raises(partner.LaunchError) as excinfo:
        partner.verify_assertion(token)
    msg = str(excinfo.value)
    assert "InvalidIssuerError" in msg
    assert f"received iss={wrong!r}" in msg
    assert f"expected iss={ISSUER!r}" in msg
    assert "member-12345" not in msg, "sub must never reach the logs"


# ── Endpoint-level: the launch ROUTE, not just the verify helpers ───────────
#
# Every test above calls partner.* directly, so the whole route — jti burn,
# grant, Firebase init, custom-token mint, session-cookie mint, redirect —
# was NEVER executed by the suite. That is exactly how `_init_firebase_admin()`
# came to sit BELOW the mint that needs it: all 22 rejection tests passed
# because they fail before the mint, and the defect surfaced only on
# inplayLABS' first real launch (2026-08-26, HTTP 500). These tests drive the
# route end to end with the Firebase/Firestore edges faked.

@pytest.fixture()
def launch_client(monkeypatch, keypair):
    import sys
    for name in [m for m in list(sys.modules) if m == "api" or m.startswith("api.")]:
        del sys.modules[name]
    monkeypatch.setenv("IPL_JWKS_URL", "https://tracker.inplaylabs.io/.well-known/jwks.json")
    monkeypatch.setenv("IPL_ISSUER", ISSUER)
    monkeypatch.setenv("IPL_TOOL_MAP", TOOL_MAP)
    monkeypatch.setenv("FIREBASE_API_KEY", "fake-key")

    import api.app as app_mod
    import api.partner as partner_mod

    _, pub = keypair

    class _FakeSigningKey:
        key = pub

    class _FakeJWKSClient:
        def get_signing_key_from_jwt(self, token):
            return _FakeSigningKey()

    monkeypatch.setitem(partner_mod._jwks_clients,
                        "https://tracker.inplaylabs.io/.well-known/jwks.json",
                        _FakeJWKSClient())
    calls = {"init": 0, "custom_token": 0, "granted": None, "burned": None}

    def _init():
        calls["init"] += 1
    monkeypatch.setattr(app_mod, "_init_firebase_admin", _init)

    from firebase_admin import auth as fb_auth

    def _create_custom_token(uid):
        # The real SDK raises ValueError here when the app was never
        # initialized — reproduce that ordering dependency exactly.
        if calls["init"] == 0:
            raise ValueError("The default Firebase app does not exist.")
        calls["custom_token"] += 1
        return b"custom-token"

    monkeypatch.setattr(fb_auth, "create_custom_token", _create_custom_token)
    monkeypatch.setattr(fb_auth, "create_session_cookie",
                        lambda id_token, expires_in: "session-cookie-value")
    monkeypatch.setattr(partner_mod, "consume_jti",
                        lambda jti, exp: calls.__setitem__("burned", jti))
    monkeypatch.setattr(partner_mod, "grant",
                        lambda uid, slug, tool_id, window_days: calls.__setitem__(
                            "granted", (uid, slug, window_days)))

    class _Resp:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"idToken": "id-token"}

    monkeypatch.setattr(partner_mod._requests, "post", lambda *a, **k: _Resp())

    from fastapi.testclient import TestClient
    return TestClient(app_mod.app, follow_redirects=False), calls


def test_launch_endpoint_happy_path(launch_client, keypair):
    """The whole route: verify → burn → grant → mint → cookie → 303."""
    client, calls = launch_client
    priv, _ = keypair
    r = client.post("/partner/inplaylabs/launch",
                    data={"assertion": make_assertion(priv)})
    assert r.status_code == 303, r.text
    assert r.headers["location"].startswith("https://")
    assert calls["init"] >= 1, "Firebase must be initialized before the mint"
    assert calls["custom_token"] == 1
    assert calls["burned"] is not None, "jti must be burned"
    uid, slug, _ = calls["granted"]
    assert uid.startswith("ipl_") and slug == "nfl"
    assert "__session" in r.headers.get("set-cookie", "")


def test_launch_endpoint_rejects_bad_assertion_with_403(launch_client):
    client, _ = launch_client
    r = client.post("/partner/inplaylabs/launch", data={"assertion": "garbage"})
    assert r.status_code == 403
    assert "could not be verified" in r.text


def test_internal_failure_is_500_not_403(launch_client, keypair, monkeypatch):
    """A verified assertion that then hits OUR defect must not masquerade as
    a rejection — the partner would chase a valid token forever."""
    client, _ = launch_client
    priv, _ = keypair
    import api.partner as partner_mod
    monkeypatch.setattr(partner_mod, "mint_id_token",
                        lambda uid: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.post("/partner/inplaylabs/launch",
                    data={"assertion": make_assertion(priv)})
    assert r.status_code == 500
    assert "boom" not in r.text, "internal detail must not reach the partner"


# ── Multi-tool members: launches must accumulate, not clobber ───────────────

class _FakeSnap:
    def __init__(self, data):
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _FakeDocRef:
    def __init__(self):
        self.data = None
        self.deleted = False

    def get(self):
        return _FakeSnap(self.data)

    def set(self, payload, **kwargs):
        self.data = dict(payload)

    def delete(self):
        self.deleted = True
        self.data = None


class _FakeCollection:
    def __init__(self, name, store):
        self.name = name
        self.store = store

    def document(self, uid):
        return self.store.setdefault(uid, _FakeDocRef())

    def where(self, *a, **k):
        return self

    def stream(self):
        for uid, ref in list(self.store.items()):
            snap = _FakeSnap(ref.data)
            snap.reference = ref
            if snap.exists:
                yield snap


class _FakeDB:
    def __init__(self):
        self.stores = {}

    def collection(self, name):
        return _FakeCollection(name, self.stores.setdefault(name, {}))


PARTNER_UID = "ipl_" + "a" * 24


def test_second_tool_launch_does_not_revoke_the_first():
    """A member owning two IPL tools launches them separately. The first cut
    wrote {"slugs": [slug]} with a plain set(), so the NCAAF launch silently
    revoked NFL access granted minutes earlier."""
    db = _FakeDB()
    partner.grant(PARTNER_UID, "nfl", "ssa-nfl-model", window_days=7, db=db)
    assert db.stores["entitlements"][PARTNER_UID].data["slugs"] == ["nfl"]

    partner.grant(PARTNER_UID, "ncaaf", "ssa-ncaaf-model", window_days=7, db=db)
    doc = db.stores["entitlements"][PARTNER_UID].data
    assert doc["slugs"] == ["ncaaf", "nfl"], "the earlier sport was revoked"
    assert set(doc["grants"]) == {"nfl", "ncaaf"}
    assert {p["sku"] for p in doc["packages"]} == {"ssa-nfl-model", "ssa-ncaaf-model"}


def test_relaunch_extends_only_its_own_window():
    """Each launch re-gates ONE sport. Refreshing NFL must not silently
    extend NCAAF's window (that would defeat the revocation bound)."""
    db = _FakeDB()
    partner.grant(PARTNER_UID, "ncaaf", "ssa-ncaaf-model", window_days=7, db=db)
    first = db.stores["entitlements"][PARTNER_UID].data["grants"]["ncaaf"]["expires_at"]

    partner.grant(PARTNER_UID, "nfl", "ssa-nfl-model", window_days=7, db=db)
    grants = db.stores["entitlements"][PARTNER_UID].data["grants"]
    assert grants["ncaaf"]["expires_at"] == first, "NCAAF's window moved"
    assert grants["nfl"]["expires_at"] >= first


def test_sweep_drops_only_the_lapsed_sport():
    """Per-slug expiry: a member whose NFL window lapsed keeps NCAAF, and the
    doc survives. Previously one expires_at deleted the whole document."""
    db = _FakeDB()
    partner.grant(PARTNER_UID, "nfl", "ssa-nfl-model", window_days=7, db=db)
    partner.grant(PARTNER_UID, "ncaaf", "ssa-ncaaf-model", window_days=7, db=db)

    ref = db.stores["entitlements"][PARTNER_UID]
    stale = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    ref.data["grants"]["nfl"]["expires_at"] = stale

    result = partner.sweep(db=db)
    assert result["slugs_pruned"] == 1
    assert result["entitlements_pruned"] == 0
    assert ref.data["slugs"] == ["ncaaf"]
    assert not ref.deleted


def test_sweep_deletes_the_doc_when_every_sport_lapsed():
    db = _FakeDB()
    partner.grant(PARTNER_UID, "nfl", "ssa-nfl-model", window_days=7, db=db)
    ref = db.stores["entitlements"][PARTNER_UID]
    stale = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    ref.data["grants"]["nfl"]["expires_at"] = stale

    result = partner.sweep(db=db)
    assert result["entitlements_pruned"] == 1
    assert ref.deleted
