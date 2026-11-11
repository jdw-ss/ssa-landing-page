"""
Pins on the SSO-signer step of `./deploy.sh bootstrap` (2026-11-11).

Static reads, no gcloud, no network. Ported from nba-dashboard's
tests/test_deploy_script.py (commit 338e082): GET /api/session/exchange mints
its Firebase custom token signed AS the shared signer named in api/auth.py
(`serviceAccountId`), so the runtime SA needs roles/iam.serviceAccountTokenCreator
ON THAT SIGNER, granted fail-loud by deploy.sh. The 2026-08-08 grant was a
one-off over that day's runtime SAs; when nba and nhl moved to dedicated SAs it
silently did not follow, and every exchange with a valid cookie 500'd
("signBlob denied") for weeks, unseen (guidelines → the 2026-11-11 Key Lesson).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "deploy.sh").read_text(encoding="utf-8")


def _code(text: str = DEPLOY) -> str:
    """The script minus comment lines — negative assertions must not trip on
    a comment that NAMES the thing they forbid."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def _var(name: str) -> str:
    m = re.search(rf"^{name}=(\S+)$", DEPLOY, re.M)
    assert m, f"deploy.sh does not define {name}"
    return m.group(1).strip('"')


def _signer_from_auth_py() -> str:
    """The signer api/auth.py passes as serviceAccountId — read from CODE, so a
    commented-out option cannot satisfy the pin."""
    auth = (ROOT / "api" / "auth.py").read_text(encoding="utf-8")
    m = re.search(r'"serviceAccountId":\s*"([^"]+)"', _code(auth))
    assert m, "api/auth.py no longer names a serviceAccountId"
    signer = m.group(1)
    assert signer.endswith("@ssa-auth-71d16.iam.gserviceaccount.com"), signer
    return signer


def _token_creator_grants(text: str) -> list[tuple[str, str]]:
    """(target, block) for every add-iam-policy-binding invocation in `text`
    that grants serviceAccountTokenCreator; a block runs to the first newline
    not continued by a backslash."""
    out = []
    for s in re.finditer(r"add-iam-policy-binding\s+(?:\\\n\s*)?(\S+)", text):
        end = re.search(r"[^\\]\n", text[s.start():])
        block = text[s.start():s.start() + (end.end() if end else len(text))]
        if "serviceAccountTokenCreator" in block:
            out.append((s.group(1), block))
    return out


def _bootstrap() -> str:
    m = re.search(r"cat <<'EOF'\n(.*?)\nEOF\n", DEPLOY, re.S)
    assert m, "deploy.sh no longer prints the bootstrap heredoc"
    return m.group(1)


def test_the_bootstrap_grants_token_creator_on_the_signer_auth_py_signs_as():
    """This repo's deploy.sh executes no IAM by design: `./deploy.sh bootstrap`
    PRINTS the one-time grants. Until 2026-11-11 its step 2 printed
    TokenCreator on the runtime SA ITSELF, which signs nothing auth.py mints
    (it signs AS the shared signer, its serviceAccountId). The printed grant
    must target the signer auth.py names (read from auth.py so the two cannot
    drift), for the SA the LIVE service runs as, and no step may print the
    self-grant."""
    signer = _signer_from_auth_py()
    boot = _code(_bootstrap())
    grants = _token_creator_grants(boot)
    assert len(grants) == 1, grants
    target, block = grants[0]
    assert target == signer, target
    assert "--project=ssa-auth-71d16" in block
    assert '--member="serviceAccount:${SA}"' in block
    assert '--role="roles/iam.serviceAccountTokenCreator"' in block
    assert "--condition=None" in block
    sa = boot.index("SA=$(gcloud run services describe ssa-landing")
    assert "spec.template.spec.serviceAccountName" in boot[sa:boot.index("')\n", sa)]
    assert sa < boot.index(block)
