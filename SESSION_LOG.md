# Session Log — ssa-landing-page

Append-only, newest entries on top. Format defined in `~/Claude Projects/docs/DOCUMENTATION_PYRAMID.md` → `<project>/SESSION_LOG.md`.

Write an entry at the end of any non-trivial session (anything that produced commits, decisions, abandoned approaches, or mid-flight work). Skip for pure read-only / Q&A / typo-fix sessions.

---

<!-- New entries go directly below this line -->
## 2026-08-08 — Stripe go-live build: prices decided, 6-month terms, friend codes

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
