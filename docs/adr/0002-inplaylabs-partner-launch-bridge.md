# ADR-0002: inplayLABS partner-launch bridge

- **Status**: Accepted (built 2026-08-25; dormant until partner config lands)
- **Project**: `ssa-landing-page` — the bridge lives ONLY on the apex hub
- **Driver**: jdw; decisions recorded 2026-08-25

## Context

inplayLABS sells access to externally hosted "Data Tools". A member buys an
SSA tool inside their platform, clicks Launch, and must arrive on our league
site already signed in and entitled. Their onboarding contract mandates a
server-side token bridge: a short-lived signed assertion POSTed to our launch
URL; we verify, create our own session, and redirect. They never receive SSA
credentials; we never receive theirs.

SSA already has the primitives: a parent-domain Firebase `__session` cookie
minted only by this hub, Firestore `entitlements/{uid}` read server-side by
every league gate (60s cache), and custom tokens signable for arbitrary uids.
The design question was how to graft a second acquisition channel on without
touching six league repos or the Stripe machinery.

## Decisions

1. **Per-sport tools** (John): `ssa-nfl-model` → slug `nfl`, etc. One
   tool_id per sport, mapped in `IPL_TOOL_MAP` env. Matches SSA's own
   packaging and their "precise entitlement code" rule.

2. **Synthetic uid namespace `ipl_<sha256(sub)[:24]>`** — the single
   load-bearing safety property. The Stripe webhook's `_recompute` does a
   FULL OVERWRITE of `entitlements/{uid}` from Stripe state; partner uids
   never enter Stripe checkout, so the two writers can never touch the same
   doc. The standing "only the Stripe webhook writes entitlements" ruling
   gains exactly one carve-out, enforced at runtime: `partner.grant()`
   refuses any uid outside `ipl_`/`ipltest_`.

3. **Opaque-only** (John): no email requested from the partner. The signed
   `sub` is the account key; the raw value never appears in uids or logs
   (hashed). Known cosmetic cost: the shared account widget keys on email,
   so partner members see "Sign in" in the header while every paid tab
   unlocks correctly (league data gating is pure server-side cookie).

4. **7-day launch window** (John): each launch writes `expires_at = now+7d`
   on the entitlement AND mints the session cookie with a 7-day TTL (not the
   14-day customer default). The partner checks their own entitlement before
   signing an assertion, so a cancelled member is denied at next launch;
   between launches the coast is bounded by the window. A Cloud Scheduler
   sweep (`POST /partner/inplaylabs/sweep`, shared-secret header) prunes
   lapsed docs so server-side access dies even without a next launch —
   league gates notice within their 60s cache.

5. **Replay**: `jti` burned via Firestore transactional create in
   `partner_jti` (doc id = sha256(jti)); losers of the race get the generic
   403. Assertion lifetime is additionally capped at 300s regardless of
   claimed exp.

6. **Session mint path**: `create_session_cookie` requires an ID token, so
   the bridge goes custom token → identitytoolkit `signInWithCustomToken`
   (server-side, `FIREBASE_API_KEY`) → ID token → session cookie. First
   launch auto-creates the Firebase user — that IS provisioning. The launch
   response also sets `ssa_auth=1`: auth.js treats a stored `0` (a previous
   family-wide sign-out on this browser) as a veto on session recovery,
   which would otherwise leave a freshly launched member header-signed-out.

7. **No league-repo changes.** Rejected alternative: expiry enforcement in
   the six vendored `entitlements.py` gates. The sweep achieves the same
   revocation bound with zero synced-file burden.

8. **Destinations are our config.** `IPL_TOOL_MAP` carries the redirect
   target per tool; nothing in the request chooses a URL (no open redirect).

## Open items

- **ToS**: partner members never click SSA's clickwrap; their contract is
  with inplayLABS. Whether the partnership agreement needs SSA terms flowed
  through their checkout is a legal question for the deal, not code. We do
  NOT stamp `tos_accepted` for `ipl_` uids.
- **Revocation webhook**: if inplayLABS offers signed cancellation webhooks,
  add an endpoint to delete the entitlement immediately instead of coasting
  to `expires_at`. The sweep remains as backstop either way.
- ~~Header identity polish~~ **DONE 2026-08-26**: `account.js` renders an
  inert "inplayLABS Member" chip for `ipl_`/`ipltest_` uids instead of
  "Sign in" — which was more than cosmetic: the sign-in link would have
  Google-authed a partner member into a fresh entitlement-less account,
  replacing their partner session. Synced to all 8 vendored copies;
  prefix consistency pinned by `tests/test_partner_launch.py`.

## Rejected

- Sharing the Supabase-side handoff, iframes, email/uid-in-query-string:
  prohibited by their contract and ours.
- Custom-claims entitlements: already rejected in ADR-0001 (frozen for the
  cookie's life); doubly wrong for a 7-day-window design.
- Merging partner members with direct SSA Google accounts (email linking):
  reintroduces the recompute clobber and PII for no v1 benefit.
