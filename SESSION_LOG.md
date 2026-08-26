# Session Log — ssa-landing-page

Append-only, newest entries on top. Format defined in `~/Claude Projects/docs/DOCUMENTATION_PYRAMID.md` → `<project>/SESSION_LOG.md`.

Write an entry at the end of any non-trivial session (anything that produced commits, decisions, abandoned approaches, or mid-flight work). Skip for pure read-only / Q&A / typo-fix sessions.

---

## 2026-08-25 — inplayLABS partner-launch bridge (built, deployed dark)

**Agent**: claude-fable-5

New acquisition channel: inplayLABS members buy an SSA tool on their platform
and land signed-in + entitled on our league sites via a signed-assertion POST
to this hub. Full design in `docs/adr/0002-inplaylabs-partner-launch-bridge.md`;
the filled-in onboarding answers for their team in
`docs/INPLAYLABS_ONBOARDING.md`.

### Shipped

- `api/partner.py` — assertion verification (PyJWT + their JWKS; asymmetric
  algs only, exact iss/aud, 300s lifetime cap, tool_id==aud==entitlement
  triple-check), Firestore jti burn (transactional create), synthetic
  `ipl_<sha256(sub)[:24]>` uids, time-boxed entitlement writer with the
  namespace hard-guard, lapsed-doc sweep, custom-token→identitytoolkit→
  session-cookie mint.
- Routes: `POST /partner/inplaylabs/launch`, `/launch-test` (separate
  issuer/JWKS, `ipltest_` uids, 1-day window), `/sweep` (scheduler,
  `IPL_SWEEP_TOKEN` header). All 404 until env config lands — deployed dark.
- `tests/test_partner_launch.py` — 21 new tests incl. the partner's go-live
  checklist as parametrized rejections, real-signature verification against
  a generated keypair, algorithm-confusion pin, replay, namespace guard.
- `requirements.txt` pins PyJWT[crypto] (was transitive) + python-multipart
  (Form parsing). deploy.sh env sanity list gains the four IPL keys.

### Decisions (John, 2026-08-25)

Per-sport tools (`ssa-nfl-model`→`nfl` etc.) · opaque-only (no email) ·
7-day launch window · build now against placeholder config.

### To activate (when inplayLABS sends their side)

1. Set `IPL_JWKS_URL`, `IPL_ISSUER`, `IPL_TOOL_MAP`, `IPL_SWEEP_TOKEN`
   (Secret Manager for the token) on the service; redeploy.
2. Create the daily sweep Cloud Scheduler job (us-east1) posting to
   `/partner/inplaylabs/sweep` with the header.
3. Run their test lane end-to-end; then the go-live checklist in the
   onboarding doc.

### Open

ToS flow-through for partner members (legal, John) · optional revocation
webhook if they offer one · email-free header identity chip (cosmetic).

<!-- New entries go directly below this line -->

## 2026-08-16 — Launch gate opened: the apex is indexable

**Agent**: claude-opus-5 | Deployed `ssa-landing-00026-lnc`

John: "Go ahead and open the public domains to be crawled, including apex."

The apex was the LAST host still closed. Every league subdomain had been
`Allow: /` with a sitemap since the SEO pass, so the homepage, `/pricing` and
the legal pages — the entire commercial front door — were invisible to every
crawler while Stripe had been live since 2026-08-08.

**Scoped to five URLs, not "remove every noindex".** `_PORTFOLIO_URLS` in
`api/app.py` already declared exactly which apex URLs belong in the sitemap, so
that list decided it: `/`, `/pricing`, `/help`, `/terms`, `/privacy` flipped to
`index, follow` (help already was), and `/signin`, `/account`, `404.html` kept
their `noindex`.

**`/signin` and `/account` are excluded TWO ways on purpose** — a `noindex` meta
AND a `robots.txt Disallow`. Not belt-and-braces paranoia: a Disallow'd URL can
still be indexed title-only from an inbound link, precisely because the crawler
never fetches it and therefore never sees the meta. A credential entry point and
a private customer view warrant both.

**Pinned in both directions**, since both failures are silent and expensive —
de-indexing the storefront costs every organic signup, and indexing `/account`
puts a customer's own view in search results. A further test asserts the sitemap
URL set and the indexable-meta set AGREE, because they drift independently: a new
apex page can land in `_PORTFOLIO_URLS` without a meta, or get a meta without
ever being submitted. All three regressions (de-indexed storefront, exposed
`/account`, blanket `Disallow`) were verified to FAIL the suite before this
shipped.

Also removed the now-false LAUNCH GATE comments from the four flipped pages and
the robots route, and re-anchored `pricing.html`'s Product/Offer JSON-LD TODO so
it no longer reads as blocked on a flip that has happened.

**Verified live** on the deployed service: `robots.txt` allows with the sitemap
reference, all five public pages return `index, follow`, `/signin` + `/account`
return `noindex, nofollow`, the 404 page keeps `noindex`, and the sitemap index
lists 8 child sitemaps.

**Still open**: the Product/Offer JSON-LD on `/pricing` (one Product per SKU in
`api/entitlements.py`, prices read from the LIVE Stripe prices).
## 2026-08-13 — Legal protection stack + nav-mirrors-cards reorder

**Agent**: claude-fable-5 | **Branch**: `main` | **Commits**: 1 (this commit; the card
reorder itself was `62b5f3a` yesterday)

### Changed
- **`static/terms/index.html` + `static/privacy/index.html` (NEW)** — full Terms of
  Service + Privacy Policy, drafted via a 3-doc adversarial-review workflow and
  audited for fact-consistency. Served at `/terms` + `/privacy` (`api/app.py`),
  in the sitemap, noindex'd until launch (the launch flip now covers 4 metas +
  robots.txt).
- **`api/entitlements.py`** — `TOS_VERSION = "2026-08-13"` +
  `record_tos_acceptance()` (merge-write to `customers/{uid}`, skips when the
  stored version matches so the original acceptance timestamp survives, never
  raises, dev-mode skip).
- **`api/app.py`** — `create_session` stamps ToS acceptance after a successful
  mint (clickwrap record; `/signin` carries the by-continuing-you-agree line).
- **`static/css/tokens.css`** — new `.site-footer` legal block (tokens only);
  `?v=4` bumped on all 7 pages.
- **All apex pages** — L1 nav reordered to NFL·NCAAF·CFL·Golf·NBA·NHL·Soccer
  (mirrors homepage cards); legal footer everywhere; signin fineprint is now the
  clickwrap (21+ + legal-wagering-age confirmation + Terms/Privacy links);
  pricing foot-notes disclose auto-renewal at then-current price, Stripe
  merchant-of-record, and no-refunds; help refunds FAQ rewritten to the real
  policy (visible answer AND its JSON-LD twin — the twin was caught by the
  verify workflow after the visible fix) + new Responsible Gaming section.

### Decisions
- Contracting party: **John D Wilson LLC d/b/a Sportsbook Science LLC** (CO LLC).
- **Colorado** governing law + venue; informal-resolution-first; NO arbitration.
- **No refunds** except where required by law; cancel anytime, access to period
  end. **21+** and legal wagering age. No dollar amounts in the legal docs
  (prices live at checkout); the $100 liability floor is the only figure.
- Liability cap: greater of 12 months' payments or $100; consequential damages
  excluded EXPRESSLY including wagering losses.

### Same-day follow-up (second commit)
- Footer redesigned to the Sharp Football pattern (John's call): links row →
  "Owned and Operated by Sportsbook Science LLC • Copyright 2026 | …, All
  rights reserved" → no-illegal-gambling/entertainment-purposes paragraph.
  BYTE-IDENTICAL on every SSA page, absolute apex URLs so league copies match.
  The 1-800-GAMBLER helpline moved OUT of the footer — it lives in Terms §11
  and /help. Rolled out to all 9 league repos in the same pass.

### Same-day follow-up (third commit) — SEO hardening
- Post-deploy SEO audit (3-agent crawl) found: soft-404s on every host (any
  junk URL → 200 homepage), the `/elomodel` + `/epl` no-slash 307→http→301
  chains poisoning sitemaps AND canonicals, www serving 200 duplicates, and
  trailing-slash twins on the apex pages. All fixed portfolio-wide same day:
  real 404s (new `static/404.html`, catch-all status change only — traversal
  containment, api hard-404, auth all untouched), www → apex 301 middleware
  (GET/HEAD only; webhook unaffected; deploy.sh health check now follows with
  -L), slash → no-slash 301s, sitemap `_PORTFOLIO_URLS` now lists
  `/elomodel/` + `/epl/` (the direct-200 forms), league repos got
  `--proxy-headers` + canonical alignment + HEAD support (FastAPI's @app.get
  doesn't auto-add HEAD). Lessons recorded in ANALYTICS_PROJECT_GUIDELINES.

### Open threads
- ToS §7 promises advance notice before charging a renewal at an INCREASED
  price — operational commitment to remember at any repricing.
- Deploys held for John's preview approval (this service + 9 league services
  carry the nav reorder).

## 2026-08-08 (night) — Cross-site auth state: `ssa_auth` cookie + silent sign-in mode

**Agent**: claude-fable-5 subagent | **Branch**: `main` | **Commits**: 1

Seamless-recovery wave 2 (spec: SEAMLESS_RECOVERY_SPEC, John greenlit). One
mechanism fixes two live bugs: (a) sign-out didn't propagate — per-origin
Firebase persistence survives the parent-cookie DELETE, and this repo's own
self-heal (evening entry below) then RE-MINTED the cookie from surviving
persistence, signing the whole family back in; (b) a league page with a dead
cookie stayed signed out until an apex visit.

**`static/js/auth.js`** — new parent-domain state cookie `ssa_auth`
(JS-readable, `Domain=.sportsbookscienceanalytics.com`, 30d, Secure,
SameSite=Lax; values `"1"` intended-signed-in / `"0"` explicit-signed-out;
never carries identity). Bootstrap consults it FIRST: `"0"` vetoes the
exchange AND the self-heal, and a `"0"` + persisted user → local firebase
signOut (purge) + render signed out. Absent + user → legacy migration, set
`"1"`. Set `"1"` on exchange success, redirect completion, popup success;
signOut() sets `"0"` BEFORE the DELETE + firebase signOut. Redirect
completion still runs under `"0"` (returning from Google IS a sign-in).

**`static/signin.html`** — silent mode (`?silent=1&next=<url>`): card hidden,
quiet "One moment…" line, `next` validated (absolute https on
SSA/*.SSA only — open-redirect guard, foreign values fall back), NEVER a
popup. Signed in → bootstrap's self-heal already re-minted →
`location.replace(next)`. Not signed in → the `"1"` state was stale → delete
the cookie (absent ≠ `"0"`: no purge of other origins, but league pages stop
bouncing) → `replace(next)`. League auth.js bounces here at most once per
10 min (sessionStorage guard, league side).

**`static/js/account.js`** — loadAuth constant → `auth.js?v=lazy` (stable
cache-buster; a vendored shared file can't carry per-repo `?v` numbers; the
300s static TTL bounds staleness). The file is now byte-identical across ALL
9 surfaces again (md5 207342751fe40965e4e1bcaf7fd52f8e) — the `?v=2`/`?v=1`
apex divergence from the evening entry is dead.

Versions: auth.js v2→v3 (signin + account inline loaders), account.js v4→v5
(all five pages). tokens.css `.account-stub > a` verified present (no
change). Tests 35/35. NOT deployed — John reviews diffs first.

## 2026-08-08 (evening) — Apex header unified + parent-cookie self-heal

**Agent**: claude-fable-5 (subagent) | **Branch**: `main` | **Commits**: 2

**Commit 1 — header unification** (John's directive: one standard header on
every subdomain and page; league version canonical per DESIGN_SYSTEM.md §3).
All five apex pages (index, pricing, account, signin, help) now render the
league-standard `<nav class="site-nav">`: brand · divider · 7-sport row ·
nav-right (Pricing/Help/account-stub). The five divergent inline `.top-bar`
blocks are deleted; header + account-stub CSS lives ONCE in
`static/css/tokens.css` (bumped `?v=3`), values verbatim from
cfl-elo-dashboard's canonical block. No sports link is `.active` on the apex
(it's the directory); Pricing/Help take the league active state (15px/600
accent) on their own pages. account.html + signin.html — previously bare
Pricing/Help headers — gain the stub + account.js so the header is identical
everywhere. New tokens.css rule `.account-stub > a` pins the signed-out
"Sign in" replacement link to the nav-link spec (apex has no global `a`
colour rule; it rendered UA-blue).

**Commit 2 — session-cookie self-heal** (root cause of "apex shows signed
in, league sites show Sign In"): Firebase local persistence at the apex can
hold a signed-in user while the parent-domain `__session` cookie is
missing/expired/legacy-scoped, and nothing re-minted it. `auth.js
_bootstrap()` now tracks `_cookieOk` (exchange succeeded / redirect-completion
minted) and, on the first auth-state fire with a user but no proven cookie,
re-mints via the existing `_persistSession` — once per page load, no loop.

**Found in verification, fixed in commit 2**: adding account.js to
account/signin created TWO auth.js loaders (each page's inline `loadAuth` +
account.js's) → double script injection → the second IIFE replaced
`window.Auth` mid-bootstrap and the widget read `user()` off the fresh
unbootstrapped instance (reproduced first try: signin's widget rendered
signed-out while the page card showed dev@local; account.html could
spuriously bounce to /signin the same way). Fix: `window.Auth = window.Auth
|| (...)` — double-execution keeps the first façade.

**Vendoring drift (follow-up for the next league-wide vendoring pass)**:
apex account.js's loadAuth constant is now `auth.js?v=2` (league copies say
`?v=1` — only matters on hosts where account.js loads auth.js: apex + NFL
landing), and the apex auth.js now carries the self-heal + idempotence guard
the league copies lack. Both belong portfolio-wide.

Versions: tokens.css v2→v3, account.js v3→v4 (all five pages), auth.js
v1→v2 (account/signin inline loaders + account.js constant). Tests 35/35
green; all five pages visually verified on the local preview (port 8085).
NOT deployed; robots/noindex untouched (launch-gated).

## 2026-08-08 (later) — STRIPE IS LIVE

**Agent**: claude-fable-5 | **Branch**: `main` | **Commits**: this commit

Executed the go-live from the entry below. John pasted the live sk into
`stripe-secret-key`; sequence was: deploy new code FIRST (gate holds without
envs — no intermediate state where old code sells), then
`scripts/stripe_bootstrap_live` (12 prices, coupon + 10 codes, webhook
`we_1U2FEPJNU4Sozfdvwdxo403G`, portal `bpc_1U2FERJNU4SozfdvimZdJMip`), then
bind secrets + 13 envs → revision `ssa-landing-00018-58r`.

**One wrinkle**: the runtime SA (default compute) lacked
`secretmanager.secretAccessor` — first bind attempt failed cleanly (failed
revision never took traffic). Granted per-secret on both, retried, done. This
grant now belongs in any future secret-adding runbook step.

**Verified live**: catalog API serves the exact ladder with
`billing_configured: true`; Stripe read-back audit confirms 12 active prices
(right amounts + lookup_keys), webhook enabled on the 4 events, portal
cancel-at-period-end with plan-switches OFF, coupon 100%/forever with 10/10
codes available. **robots.txt + all noindex metas verified UNTOUCHED** —
John is deliberately holding the SEO flip until a UI cleanup pass.

**Remaining for full launch**: the robots/noindex flip (+ Product JSON-LD on
/pricing per its TODO) — deliberately deferred.


**Agent**: claude-fable-5 | **Branch**: `main` | **Commits**: this commit

**Prices decided (John).** Sports $99.99/mo; NCAAF+NFL bundle $149.99 (25% off
the pair); All-Access $299.99 — REPRICED from John's initial $399.99 after
flagging that $399.99 exceeded the 4-sport sum ($399.96); at $299.99 both
bundle and All-Access are exactly 25% off their parts. Every SKU gains a
6-month prepaid term at 50% off six cycles, rounded to .99: $299.99 / $449.99 /
$899.99. All amounts live in `api/entitlements.py::LAUNCH_PRICE_CENTS` — the
single source of truth the bootstrap scripts mint from and the UI displays.
Also decided: Managed Payments STAYS (Stripe is merchant of record, handles
sales tax); friend codes are 100%-off-FOREVER, single-use each.

**Terms are a billing dimension, not an entitlement one.** `STRIPE_PRICE_<SKU>`
(+ new `…_6MO`) resolve to the same slugs; an item's term is read off the
price's own `recurring.interval_count`, never the env. Upgrades now include
same-SKU monthly → 6-month (proration + `billing_cycle_anchor="now"` so a
fresh 6-month cycle starts at purchase; verified in a sandbox: $99.99 monthly
→ 6-month invoiced exactly $200.00 = $299.99 − $99.99 unused credit, period
end landing 6 months out to the day). Term DOWNGRADES are blocked with a
pointer at the 6-month target — swapping prepaid 6-month credit onto a
cheaper monthly price would strand it. `apply_plan_change` now cancels
superseded extras BEFORE the primary swap so their prorate credits land as
pending invoice items on the SAME always_invoice invoice, not next renewal
(which a 6-month term would push half a year out).

**Checkout**: `payment_method_collection="if_required"` — a 100%-forever code
checks out with no card at all (verified: $0 invoice, `paid`, subscription
`active`, code burned to `times_redeemed 1/1 active:false`). Webhook loop
verified via `stripe listen`: subscription event → uid from metadata →
`_recompute` → `['ncaaf']`.

**New**: `scripts/stripe_bootstrap_live.py` (sk_live-guarded, idempotent:
catalog + Friends & Family coupon + 10 single-use codes + webhook endpoint
with the signing secret piped STRAIGHT to Secret Manager + portal config with
cancel-at-period-end and portal plan-switches OFF). `scripts/
_bootstrap_common.py` shared with the test script. Pricing page: term toggle,
strike-through 6×monthly compare, in-card prorated-upgrade confirm flow (the
409 dead-end banner is gone). Account page shows each package's term.

**Traps hit**: Python banker's rounding derived $149.98 from `round(19998 ×
0.75)` (excised with the derivation itself when LAUNCH_PRICE_CENTS replaced
it); API 2026-07-29.dahlia moved PromotionCode.create's `coupon` into nested
`promotion{}`; the old test key had EXPIRED (replaced with a disposable CLI
sandbox, expires 2026-08-15); deploy.sh's price-retirement example would have
mis-parsed comma lists in `--update-env-vars` (needs the `^:^` delimiter —
fixed in the runbook text).

**Tests**: 27 → 35 (term resolution, term-switch classification, downgrade
block, cancel-before-modify order + anchor reset, term stamping, the ladder).

**Next session starts by**: John pastes the live key into Secret Manager
(`./deploy.sh bootstrap` step 5a), then run the live bootstrap, bind secrets +
envs, deploy, verify prod, hand over the 10 friend codes. The robots/noindex
flip stays a SEPARATE deliberate step.

## 2026-08-07 — Launch prep: critical traversal fix, design-system unification, SEO pass

**Agent**: claude-fable-5 | **Branch**: `main` | **Commits**: this commit

**Here specifically.** SECURITY (critical): the apex catch-all was
live-exploitable for unauthenticated arbitrary file read — `..%2fapi%2fbilling.py`
returned 9,401 bytes of source to anonymous visitors. Google's edge normalizes
`%2e%2e/` but NOT `..%2f`, so any probe testing only the former reports a false
all-clear. Containment + `api/` hard-404 + `openapi_url=None` + `no-store` on
`/api/session/exchange` (that response IS a credential). Deployed and verified
against production. First tests in this repo (14). Added `.gcloudignore` +
`.dockerignore` — `.env` holds Stripe secrets and was protected only by gcloud's
implicit `.gitignore` fallback.

Worth recording precisely: NO Stripe secret exists in any of the ten GCP
projects, and the live service carries only Firebase env vars. So the disclosure
was source code, NOT credentials — the paywall bootstrap has simply never been
run, which is also why the Stripe webhook isn't findable in the dashboard.

Design: this was the only surface with ZERO CSS variables (~199 literals across
5 inline `<style>` blocks). Now has `static/css/tokens.css`. The brand gradient
and primary button were recast off green onto blue→purple. SEO: keyword title,
description, OG/Twitter, canonical, Organization+WebSite JSON-LD, FAQPage on
/help, a sports-row nav, sitemap index, NCAAF card flipped to Live and the
missing Soccer card added.

**The launch gate is UNTOUCHED and verified**: `static/robots.txt` is still
byte-identical `Disallow: /`, and index/pricing/signin/account all still carry
`noindex, nofollow`. Flipping it is a deliberate separate step.

Portfolio-wide launch-prep pass covering three workstreams at once (security,
SEO, design system). Cross-repo context lives in the workspace docs: the new
`docs/DESIGN_SYSTEM.md` contract and two new lessons in
`ANALYTICS_PROJECT_GUIDELINES.md`.

**Design system.** Adopted the one shared token block, replacing three competing
palettes. Green/red are now DATA-ONLY; all chrome resolves through
`var(--accent)` (#58a6ff), with per-league identity surviving only as
`--league-tint`. Inter everywhere. Filled controls take `color: var(--bg)` —
white on the brighter accent measures 2.53:1, a WCAG failure the old darker
league accents had masked; hover states use `filter: brightness(1.12)` (9.01:1)
rather than the `--accent-dim` fill, which would drop a dark label to 3.99:1.

**How this was verified.** An adversarial QA pass (49 agents, every finding
independently refuted before it counted) produced 40 verified defects, then a
remediation pass closed them. Two traps worth remembering: a grep for
`color:#fff` misses `color: white`, and CSS fails SILENTLY — an undefined
`var()` is simply dropped, so only a browser (or a used-vs-defined sweep across
css+html+js) proves a rename landed.

---

## 2026-07-31 (evening) — ATS scrubbed from customer-facing copy

**Agent**: claude-fable-5 | **Branch**: `main` | **Commits**: this commit

John's call: ATS track records read as "the model loses money" to customers who
don't know the real bar is beating the close, so ATS is now internal-only
portfolio-wide. Here that meant one line: the CFL card on the homepage no
longer advertises "ATS performance". The league repos (cfl/ncaaf/nfl-elo) are
dropping their public ATS tabs + `/api/public/ats` routes in the same sweep.

---


## 2026-07-30 (later) — Hub DEPLOYED + Stripe test-mode E2E + launch gate

**Agent**: claude-fable-5 | **Commits**: `5ab2fb0`, `a1b49e8`

**What.** (1) **Pricing launch gate** (`5ab2fb0`): /pricing shows NO amounts
and disabled "Launching soon" buttons until Stripe is configured on the
service; `SHOW_PREVIEW_PRICES=1` is the local-dev override (John is still
exploring prices). Adds .env auto-load, dev-mode Firestore write guards, and
`scripts/stripe_bootstrap_test.py` (idempotent test-mode catalog creator,
sk_test-only). (2) **Live Stripe sandbox E2E** with John's account: checkout
session → hosted page → webhook lifecycle via `stripe listen`; subscribe
computed `['ncaaf']`, cancel computed `[]`, all events 200. Three bugs found
+ fixed (`a1b49e8`): dev stub email needs a TLD; Stripe SDK objects lack
dict `.get()` (new `field()` accessor — the webhook would have crashed in
prod); **the Stripe account runs Managed Payments** → products REQUIRE a
`tax_code` (script sets `txcd_10000000`; keep-vs-disable MoR is John's open
pre-launch decision). (3) **DEPLOYED to Cloud Run**: Firestore created in
`ssa-auth-71d16` (us-east1) + IAM grants (datastore.user cross-project,
tokenCreator on self); apex+www added to authorizedDomains via API; FIREBASE_*
envs set; apex/www/api all 200; launch gate confirmed live (no prices shown,
no purchase possible). John registered the OAuth redirect URI in the Console.
Public business name: dashboard-only change (API needs live keys) — John to
set "Sportsbook Science" under Settings → Business/Public details.

---


## 2026-07-30 — Apex becomes the SSA customer auth + billing hub

**Agent**: claude-fable-5 | **Commits**: `f15fc9f`

**What.** Kicked off the portfolio paywall (John's requirements + decisions
captured in `docs/adr/0001-apex-auth-billing-hub.md`): converted this repo
from static nginx to a soccer-hub-style FastAPI service. New: `/signin`
(Google popup, the ONE customer sign-in origin for all of SSA, self-hosted
`/__/auth/*`), `/pricing` (server-driven catalog: 4 sport SKUs + NCAAF+NFL
bundle at 20% off + All-Access at 50% off, placeholder $9.99 base),
`/account` (packages, Stripe Customer Portal, sign out), an any-user
`__session` mint (`api/app.py` — deliberately NO allow-list; league internal
mints unchanged), `api/billing.py` (Checkout w/ promo codes, portal, webhook
→ recompute-from-scratch Firestore entitlements in `ssa-auth-71d16`), and
`static/js/account.js` — the live Account widget replacing the "soon" stub on
every apex page. Help FAQ updated (subscriptions/cancel answers now real;
stale sports list fixed). Verified in local preview (port 8085, dev mode w/
`DEV_ENTITLEMENTS=cfl,golf`): all pages render, Active/Subscribe states
correct, 503 paths degrade with friendly banners, console clean.

**Decisions (John).** Monthly Stripe subs; stackable à-la-carte (one sub per
purchase, union of slugs); Google-only sign-in; anonymous free tier; CFL +
NCAAF + NFL + Golf sellable day 1; free hook = power rankings; subscribers
get rankings/schedule+picks/forecasts/team sheets, NOT admin or ATS detail;
promo codes now, refer-a-friend later; flip apex noindex at launch.

**Not done / next.** NOT deployed — `./deploy.sh bootstrap` prints the
one-time steps (Firestore create, IAM, Firebase authorizedDomains + OAuth
redirect URI for the apex, Stripe products/secrets/webhook). John to supply
real prices + create Stripe test products. League-side rollout (per-league
`require_entitlement`, un-host-gate `/api/session/exchange` on bare hosts,
public module pages — CFL pilot first) is scoped in the ADR. `nginx.conf` is
dead but kept pending John's OK to delete.

---

## 2026-06-09 — Flip CFL homepage card to Live

**Agent**: claude-opus-4-6 | **Commits**: `9f3c719`

**What.** CFL's public surface (`cfl.SSA`) has been live since the public-ATS
launch, but its homepage card was still badged "Coming Soon" (`class="card"` +
`badge-soon`) while the Golf and NFL placeholder cards were already "Live".
Flipped CFL to `class="card live"` + `badge-live` "Live" so the homepage
matches reality. Verified rendered Live in preview + on the production apex;
deployed revision `ssa-landing-00007-464` (apex + www both 200).

**Gotcha recorded.** These badges are hand-set and decoupled from each league's
actual launch state — added a Gotcha to `CLAUDE.md` so the next public launch
(NBA / NHL / NCAAF) remembers to flip its card here.

---

## 2026-06-01 — FAQ rewrite for parent-domain session-cookie SSO

**Agent**: claude-opus-4-7 | **Commits**: `9354274`

**What.** Rewrote the Safari ITP FAQ entry and the cross-league SSO
entry under `/help` to reflect the new session-cookie mechanism. The
original Safari paragraph described an iframe-handshake workaround
("Allow Cross-Site Tracking exception list") that never actually
fixed anything because the underlying handshake itself never worked
cross-origin in 2026 browsers. New copy is honest: the `__session`
cookie is HttpOnly + Secure + SameSite=Lax, so Safari ITP doesn't
interfere; only expired sessions / private windows / cleared cookies
will re-prompt. Cross-checked against
`cfl-elo-dashboard/docs/adr/0002-parent-domain-session-cookie-sso.md`.

---

## 2026-05-22 — Help page + nav-right (Help + Account stub)

**Agent**: claude-opus-4-7 | **Commits**: `f601ab2`, `2784c14`

**What.** Added `help/index.html` (10-entry FAQ — Getting Started,
Sign-in/Browser, Data/Models, Account/Billing, Contact) and a
nav-right block on the homepage with a Help link + Account dropdown
stub. The `Dockerfile` only copied `index.html` + `robots.txt`
originally, so the first deploy of `/help/` returned 404; fixed by
adding `COPY help/ /usr/share/nginx/html/help/` (`2784c14`).

**Decisions.** Account dropdown is a stub for now — no auth wired to
the public hosts yet. Placeholder for the future paid-product login
flow. Same block was added to all 6 league `coming-soon.html` files.

---

## 2026-05-22 — 6-card homepage grid + cross-league nav rebuild

**Agent**: claude-opus-4-7 | **Commits**: `6a1a84a`, `60434fe`

**What.** Rebuilt the homepage as a 6-card grid (Golf, CFL, NFL, NBA,
NHL, NCAAF), each card linking to the bare `<league>.SSA` public host.
Part of the broader Phase 3 nav-standardization rollout — every
league's public + internal subdomain now shares the same nav markup,
and the apex homepage is the canonical entry point.

**Notes.** Tier-3 agent docs (`CLAUDE.md` + this `SESSION_LOG.md`)
landed in the same series of commits to give future agents a project
briefing without needing to read git history.
