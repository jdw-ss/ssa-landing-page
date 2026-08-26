# inplayLABS onboarding — SSA's answers

Fill-in for their "What the partner supplies" checklist, plus what we need
back from them. Send the two sections below; the commercial terms are John's
to negotiate and marked TBD. Architecture on our side: ADR-0002.

## What SSA supplies (their checklist, answered)

- **Display name**: Sportsbook Science Analytics — <Sport> Model
- **Description / icon**: per sport; copy + icon TBD (John).
- **Support contact**: jdwilson@sportsbookscienceanalytics.com (or support@ alias, TBD).
- **Production HTTPS URL**: https://sportsbookscienceanalytics.com
- **Tool IDs** (lowercase, one per sport, launch order TBD by John):
  - `ssa-nfl-model` — NFL ELO model (public surface `sportsbookscienceanalytics.com/nfl/elomodel/`)
  - `ssa-ncaaf-model` — NCAAF ELO model (`sportsbookscienceanalytics.com/ncaaf`)
  - `ssa-cfl-model` — CFL ELO model (`sportsbookscienceanalytics.com/cfl`)

  (Apex-path form since the cutover; the legacy `<league>.SSA` subdomains
  301 to these paths since 2026-08-27. The deployed `IPL_TOOL_MAP` dests
  should move to the apex-path form at the next env update.)
- **Launch URL** (all tools, one endpoint):
  `POST https://sportsbookscienceanalytics.com/partner/inplaylabs/launch`
  (form field `assertion` carrying the signed JWT)
- **Test launch endpoint**:
  `POST https://sportsbookscienceanalytics.com/partner/inplaylabs/launch-test`
  — separate issuer + JWKS, 1-day access window, isolated test identities.
  Returns 404 until we've configured your test keys.
- **Origins to allowlist**: `https://sportsbookscienceanalytics.com` only
  (production and test lanes share it; the test lane is path-separated).
- **Member data needed**: **stable opaque member ID only** (`sub`). We do
  not want email or display name — omit them per your own contract note.
- **Price / interval / trial / cancellation / revenue split**: TBD — John.
  For reference, SSA direct pricing is $99.99/mo per sport ($299.99 for six
  months prepaid).
- **Test account**: we will provision nothing on our side — your test-lane
  assertions create isolated `ipltest_*` identities automatically.
- **Signing-key contact / incident & revocation contact**: John Wilson,
  jdwilsongame@gmail.com.

## What SSA needs from inplayLABS

1. **JWKS URL** (production and test) and the **exact `iss` value** for
   each. We verify RS256/RS384/RS512/ES256/ES384 only; publish `kid`s and
   rotate freely — we re-fetch on unknown kid.
2. Confirmation of the assertion shape in your contract (aud = tool_id,
   `entitlement` = `data_tool:<tool_id>`, `jti` single-use, exp ≤ 120s).
   We additionally reject any assertion whose iat→exp span exceeds 300s.
3. The **browser POST format**: we accept
   `application/x-www-form-urlencoded` with field `assertion`.
4. Your **revocation story**: we time-box access to 7 days per launch
   (1 day on the test lane) and re-gate at every launch. If you can send a
   signed cancellation webhook we will honor it immediately — otherwise the
   window is the bound, per your "should never outlive paid access
   indefinitely" clause.
5. Stripe metadata on your side (`labs_tool_id`) is invisible to us and
   needs no coordination; billing state stays entirely yours.

## Go-live checklist mapping (their list → our verification)

| Their item | Our status |
|---|---|
| Paid member launches, sees only purchased tool | per-tool slug grant; each tool_id maps to exactly one sport |
| Unpaid/expired/wrong-tool denied | your side pre-launch + our aud/tool_id/entitlement triple-check |
| Token ≤2min, no replay | 300s hard cap + Firestore jti burn (tested) |
| Tampered iss/aud/sub/tool/exp denied | 12-case rejection suite in `tests/test_partner_launch.py` |
| Cancellation behavior | 7-day window + daily sweep; webhook slot open |
| Logout / account switch no reuse | fresh assertion per launch; jti single-use; our cookie scoped per member uid |
| No tokens/PII in URLs or logs | form POST body; logs carry hashed jti + hashed uid only |
| Test vs prod separation | separate issuer/JWKS/uid-prefix/window; path-separated endpoints |
| Support + outage ownership | John; incident contact above |


---

## Received from inplayLABS (2026-08-26) — configuration of record

- **Prod**: iss `https://tracker.inplaylabs.io/` (trailing slash is part of
  the exact string) · JWKS
  `https://elejteklxhknthfzovhv.supabase.co/functions/v1/partner-jwks?lane=prod`
  · kid `ipl-partner-prod-2026-08` · ES256
- **Test**: iss `https://tracker.inplaylabs.io/test` · JWKS `...?lane=test`
  · kid `ipl-partner-test-2026-08` · ES256
- Assertion: iat = now−10s, exp = now+90s; sub is opaque and per-lane
  (test subjects prefixed, no prod collision). POST form field `assertion`.
- Revocation: entitlement re-checked at every launch on their side; our
  7-day (1-day test) window is the outer bound. Signed cancellation webhook
  offered later, not required for v1.

**Preflight 2026-08-26**: both JWKS lanes fetched via PyJWKClient, kids
resolved, EC P-256 keys parsed, and a forged ES256 token carrying their kid
was rejected with InvalidSignatureError on both lanes — fetch, kid
selection, key parse, and signature enforcement all proven against their
live endpoints before activation.

## Registration block sent back (their "what we need from you")

- Tool IDs (register all three; per-tool go-live at John's pace):
  `ssa-nfl-model`, `ssa-ncaaf-model`, `ssa-cfl-model`
- Production launch URL (all tools):
  `POST https://sportsbookscienceanalytics.com/partner/inplaylabs/launch`
- Non-production launch URL:
  `POST https://sportsbookscienceanalytics.com/partner/inplaylabs/launch-test`
- Test account: none needed on our side — test-lane assertions
  auto-provision isolated `ipltest_*` identities with a 1-day window. Any
  member of your QA team launching from your test lane exercises the full
  path, including a real session on the league site.
