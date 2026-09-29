"""The ONE definition of an inplayLABS partner uid (ADR-0002).

Partner members are synthetic Firebase uids minted by api/partner.py:
``ipl_<sha256(sub)[:24]>`` on the prod lane and ``ipltest_...`` on the test
lane. Two rules key off that namespace and must never disagree:

- ``partner.grant()`` writes ``entitlements/{uid}`` ONLY for partner uids
  (the one carve-out from "only the Stripe webhook writes entitlements").
- Billing refuses partner uids (John, 2026-09-29): checkout, upgrade,
  change-preview, the Customer Portal and Stripe customer creation all 403
  them, so the webhook's full-overwrite recompute never meets a partner doc.

Both sides call :func:`is_partner_uid`. The prefix string literals live here
and nowhere else in ``api/`` (pinned by tests/test_partner_billing_guard.py).
Kept dependency-free so billing.py can import it without pulling in the
partner bridge's JWT/Firestore imports.

Collision safety: Firebase auto-generated uids (Google sign-in) are
alphanumeric with no underscore, so no customer uid carries either prefix.
static/js/account.js keeps its own ``/^ipl(test)?_/`` copy for the header
chip. It is vendored byte-identically to every SSA surface and cannot import
this, so tests/test_partner_launch.py pins it to these constants.
"""

from __future__ import annotations

UID_PREFIX = "ipl_"
TEST_UID_PREFIX = "ipltest_"
PARTNER_UID_PREFIXES = (UID_PREFIX, TEST_UID_PREFIX)


def is_partner_uid(uid: object) -> bool:
    """True for an inplayLABS partner uid on either lane. Non-strings
    (None, a missing claim) are never partner uids."""
    return isinstance(uid, str) and uid.startswith(PARTNER_UID_PREFIXES)
