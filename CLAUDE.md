# SSA Landing Page / Apex Hub

## Snapshot

The apex for **sportsbookscienceanalytics.com** (apex + www) — since 2026-07-30
the portfolio's **customer auth + billing hub**, not just a landing page (see
`docs/adr/0001-apex-auth-billing-hub.md`). Four jobs: (1) league directory at
`/` + `/help` FAQ + `/terms` + `/privacy` legal pages (2026-08-13); (2) customer
Google sign-in at `/signin` — the ONE origin
where the popup runs for the whole SSA family, with the Firebase auth handler
self-proxied at `/__/auth/*`; (3) the parent-domain `__session` SSO mint for
ANY verified Google user (no allow-list — this is the customer path; league
internal hosts keep their own `ADMIN_EMAILS` mints); (4) Stripe billing:
`/pricing` → Checkout, `/account` + Customer Portal, and the webhook that
writes Firestore entitlements. Status: **converted, not yet deployed** —
paywall bootstrap pending (`./deploy.sh bootstrap`).

**Since 2026-08-27 this service is also the DEFAULT BACKEND of the apex front
door** — one global ALB (`34.160.208.32`) fronting the whole family. The hub's
own public URLs did not move (`/`, `/help`, `/pricing`, `/terms`, `/privacy`,
`/signin`, `/account`), but it no longer owns the whole apex path namespace:
the public league surfaces are now apex paths — `/nfl` (+`/nfl/mockdrafts`,
`/nfl/elomodel`), `/ncaaf`, `/cfl`, `/golf`, `/nhl`, `/nba`, `/soccer`, and
flat `/epl` (deliberately NOT `/soccer/epl`). Legacy `<league>.SSA` hosts are
PERMANENT 301 host rules on the same urlmap — their DNS repoints at the LB and
is NEVER deleted. `internal.<league>.SSA` is unchanged (operator tier, own
hosts, noindex, same auth). The hub needs no apex middleware — it IS the
default backend and serves at the root; the dual-depth `root_path` recipe
applies to the league apps. Architecture of record:
`docs/adr/0003-apex-path-consolidation.md`; provisioning is
`infra/apex-frontdoor.sh` (staged + idempotent).

## Stack

- **Language**: Python 3.11 (use `python3`, no venv on the dev box)
- **Framework**: FastAPI + uvicorn; vanilla-JS static pages (no build step)
  served from `static/`
- **Data layer**: Firestore in the shared `ssa-auth-71d16` project —
  `customers/{uid}` + `entitlements/{uid}` (webhook-only writes)
- **Billing**: Stripe Checkout + Customer Portal + webhook (`api/billing.py`);
  SKU catalog + slugs + the decided launch price ladder
  (`LAUNCH_PRICE_CENTS`, John 2026-08-08) in `api/entitlements.py`. Two
  billing TERMS per SKU — monthly and 6-month prepaid (50% off six cycles);
  upgrades (sport → bundle/all, monthly → 6-month) swap the existing
  subscription's price with proration via `/api/billing/change`
- **Auth**: firebase-admin session cookies (`api/auth.py`); shared
  `ssa-auth-71d16` Firebase project; `static/js/auth.js` façade (vendored,
  same as every league) + `static/js/account.js` (the Account widget all apex
  pages share)

## Run locally

`.claude/launch.json` config: `ssa-landing` (port **8085**). It sets
`DISABLE_AUTH=1` (dev@local stub user, Firestore never touched) and
`DEV_ENTITLEMENTS=cfl,golf` so `/account` + `/pricing` render a customer who
owns two packages. Stripe unconfigured locally → checkout/portal 503 with
friendly banners (by design).

```bash
cd '/Users/johnwilson/Claude Projects/ssa-landing-page' && \
  DISABLE_AUTH=1 DEV_ENTITLEMENTS=cfl,golf PUBLIC_BASE_URL=http://127.0.0.1:8085 \
  python3 -m uvicorn api.app:app --reload --host 127.0.0.1 --port 8085
```

## Cloud infrastructure

- **GCP project**: `golf-data-projects` (shared with `golf-dashboard`)
- **Region**: `us-east1`
- **Cloud Run service**: `ssa-landing`
- **Custom domains**: `sportsbookscienceanalytics.com` (apex) + `www.…`
- **Apex front door** (2026-08-27): global ALB `34.160.208.32` in this project,
  `ssa-landing-be` as the urlmap default; league prefixes point at backend
  services in six projects. Built by `infra/apex-frontdoor.sh`. The old Cloud
  Run domain mappings stay during the soak as instant rollback
- **Cross-project deps**: Firestore + Firebase Auth in `ssa-auth-71d16`
  (runtime SA needs `roles/datastore.user` there); Stripe secrets in
  `golf-data-projects` Secret Manager (`stripe-secret-key`,
  `stripe-webhook-secret`)

## Schedules

None.

## External connections

- **Stripe** — Checkout sessions, Customer Portal, subscriptions webhook at
  `/api/billing/webhook` (events: `checkout.session.completed`,
  `customer.subscription.created/updated/deleted`)
- **inplayLABS** (partner platform, ADR-0002) — INBOUND launch assertions
  (signed JWTs, ES256) at `POST /partner/inplaylabs/launch` (+ `/launch-test`,
  `/sweep`). We fetch their JWKS over HTTPS; nothing flows out. ARMED
  2026-08-26: prod iss `https://tracker.inplaylabs.io/` (trailing slash
  significant), test iss `.../test`, JWKS on their Supabase functions host —
  exact strings in `docs/INPLAYLABS_ONBOARDING.md`.
  Partner members are synthetic `ipl_*` Firebase uids with time-boxed
  entitlements (`source: inplaylabs`, 7-day window) — see the gotcha.
- **Firebase Auth** (`ssa-auth-71d16`) — id-token verify, session-cookie
  mint/verify, custom-token exchange; `/__/auth/*` + `/__/firebase/*`
  reverse-proxy to `ssa-auth-71d16.firebaseapp.com`

## Deploy

- **Command**: `./deploy.sh` (validates us-east1 image, warns on missing
  FIREBASE/STRIPE envs, health-checks apex + www + `/api/health`)
- **One-time paywall bootstrap**: `./deploy.sh bootstrap` prints the exact
  steps (Firestore create, IAM grants, Firebase authorizedDomains + OAuth
  redirect URI, envs, Stripe products/webhook). Steps 3's redirect-URI add is
  **Google Cloud Console only** and the client is shared by every league —
  edit carefully.
- **Preview required?** Yes (UI changes)

## Companion docs

- `docs/adr/0001-apex-auth-billing-hub.md` — the paywall architecture +
  league-side rollout checklist
- `SESSION_LOG.md` — read top entries at session start

## Related projects

- **`golf-tournament-predictor`** — same GCP project + shared custom domain;
  region drift in either deploy.sh affects both
- **`cfl-elo-dashboard`** — the auth/session/proxy pattern source (ADR-0002/0004)
- **All league services** — consume the entitlements this service's webhook
  writes; league rollout adds `require_entitlement` per league (ADR-0001 here)

## Gotchas

- **NEVER "tighten" the `/__/` frame policy to DENY.** `security_headers`
  (api/app.py, module end) stamps `X-Frame-Options: DENY` on everything EXCEPT
  paths under `/__/`, which get `SAMEORIGIN`. That carve-out is load-bearing
  and looks like an oversight: the Firebase JS SDK **frames** the self-proxied
  `/__/auth/iframe` **first-party** to carry popup sign-in events (ADR-0004),
  and DENY refuses same-origin framing too — firebaseapp.com sends no policy of
  its own, so ours lands on the proxied response and kills sign-in for the
  whole SSA family (this is the ONE origin the customer popup runs on). The
  failure is silent and looks exactly like the 2026-07-31 mint incident: **the
  popup completes, then the page stays signed out** — no error, no 4xx, nothing
  obvious in the apex logs. A blanket DENY reads as a hardening win in review;
  it is an outage. Both branches are pinned in `tests/test_public_hardening.py`.
- **HSTS is set UNCONDITIONALLY — never gate it on `request.url.scheme`.**
  Behind Cloud Run and the ALB the inbound scheme reads as `http`, so a scheme
  test would look correct and silently ship nothing forever. It matters most
  here: this service mints `__session` on the PARENT domain, so a downgrade on
  ANY host in the family exposes it — hence `includeSubDomains`. The whole
  middleware is fill-in only (`setdefault`), and it must stay registered at
  module END: Starlette wraps later registrations OUTERMOST, which is what
  makes the www 301 and the 404 fallback carry the headers.
- **`/og-default.png` is served from the ROOT, not `/static/`** — twelve shells
  across ten repos hardcode the absolute apex URL for `og:image` /
  `twitter:image` / the schema.org Organization logo. Moving or renaming it
  breaks every social share preview in the portfolio silently (it 404'd
  unnoticed from the SEO pass until the 2026-08-27 QA sweep).
- **Bump `TOS_VERSION` (api/entitlements.py) whenever `/terms` changes
  materially** — the session mint stamps the accepted version on
  `customers/{uid}` (best-effort, never blocks sign-in). The clickwrap line
  lives in `/signin`'s fineprint; keep both in step with the Terms.
- **`static/help/index.html` FAQ answers exist TWICE** — visible `<details>`
  AND the FAQPage JSON-LD in `<head>`. Google requires them to match; edit
  both in the same commit (the 2026-08-13 legal pass tripped on this).

- **Launch gate: /pricing shows NO dollar amounts and no purchase path until
  Stripe is configured on the service.** Prices are DECIDED (2026-08-08,
  `LAUNCH_PRICE_CENTS`): sports $99.99/mo, bundle $149.99, All-Access $299.99;
  6-month terms $299.99/$449.99/$899.99 — but the deployed site still renders
  "Pricing announced at launch" until the live bootstrap + secret binding run
  (`./deploy.sh bootstrap` step 5). `SHOW_PREVIEW_PRICES=1` overrides for
  LOCAL exploration only (it's in the launch config) — never set it on Cloud
  Run.
- **Local `.env` points at a disposable Stripe CLI sandbox** (created
  2026-08-08, expires 2026-08-15 unless claimed) — the previous test key had
  expired. Re-run `stripe sandbox create` + `python3 -m
  scripts.stripe_bootstrap_test` when it lapses.
- **Stripe API 2026-07-29.dahlia renamed PromotionCode.create's `coupon` param**
  to the nested `promotion={"type": "coupon", "coupon": id}`; the LIST filter
  is still flat `coupon=`. Both bootstrap scripts encode this.
- **Stripe test keys never go on the deployed service** — a live site wired to
  test mode grants real entitlements for 4242-card "purchases". Test checkout
  locally: `.env` + `python3 -m scripts.stripe_bootstrap_test` + Stripe CLI
  webhook forwarding (steps in `.env.example`). Dev mode (`DISABLE_AUTH=1`)
  skips all Firestore writes, so local webhook tests exercise checkout +
  webhook receipt, not the entitlement write (that's verified at launch).
- **The Stripe account runs Managed Payments (merchant-of-record, "sold
  through Link").** Products REQUIRE an eligible `tax_code` or Checkout 400s —
  the bootstrap script sets `txcd_10000000` (General – Electronically Supplied
  Services). Stripe handles sales tax and is the merchant of record, with its
  own fee structure and Link-branded checkout. Keep-vs-disable is an open
  business decision (John) before launch.
- **Stripe SDK objects do NOT support dict-style `.get()`** (attribute lookup
  raises `AttributeError: get`) — use `api/billing.py::field()` for any
  optional field on webhook payloads or API responses.
- **The session mint accepts ANY Google user — on purpose.** Don't "fix" it by
  adding an allow-list; customer authorization = Firestore entitlements,
  enforced league-side. Operator gating lives on `internal.<league>` hosts.
- **Only the Stripe webhook writes `entitlements/{uid}` — with ONE carve-out
  (ADR-0002).** `api/partner.py` also writes, but exclusively to synthetic
  `ipl_*`/`ipltest_*` uids that can never enter Stripe checkout, so the
  webhook's full-overwrite recompute and the partner writer can never touch
  the same doc. `partner.grant()` hard-refuses any other uid. Never widen
  that carve-out; a partner slug on a real customer's doc would be silently
  clobbered by the next Stripe event.
- Partner entitlements are TIME-BOXED (`expires_at`, 7d): league gates don't
  check expiry, so the daily sweep (`POST /partner/inplaylabs/sweep`, Cloud
  Scheduler + `IPL_SWEEP_TOKEN` header) is what actually revokes them. If
  partner members report access that never expires, check the scheduler.
- **Homepage Live / Coming-Soon badges are manual and drift.** When a league
  launches its public surface, flip its card here (CFL sat stale once already).
- **The apex is INDEXABLE as of 2026-08-16 — the launch gate is open.** John's
  call; `static/robots.txt` is `Allow: /` with the sitemap reference and the
  `noindex` metas came off `/`, `/pricing`, `/terms`, `/privacy` (`/help` was
  already indexable). Context for why it mattered: every league subdomain had
  been `Allow: /` with a sitemap since the SEO pass, so the apex was the ONLY
  host search engines could not see — the homepage, pricing and legal pages,
  i.e. the entire commercial front door, with Stripe live since 2026-08-08.
  **`/signin` and `/account` stay out, and in two ways at once**: a `noindex`
  meta AND a `robots.txt Disallow`. That is not belt-and-braces paranoia — a
  Disallow'd URL can still be indexed title-only from an inbound link, because
  the crawler never fetches it and therefore never sees the meta. `404.html`
  keeps its `noindex` too. **The indexable set is exactly `_PORTFOLIO_URLS` in
  `api/app.py`** — if you add an apex page, add it there and give it an
  `index, follow` meta, or it will be crawlable but absent from the sitemap.
- Two domain mappings (apex + www) — recreating either triggers 15min–hours of
  cert re-provisioning; check both when debugging cache/cert issues.
- `nginx.conf` at repo root is **dead** post-conversion (Dockerfile no longer
  references it) — kept pending John's OK to delete.
- Firestore reads fail-closed: `/api/me` 503s rather than showing a paying
  customer as unsubscribed; `/api/billing/catalog` degrades to signed-out
  rendering instead.
- **Session-cookie minting needs TWO cloud prerequisites** (found in the field
  2026-07-31 after sign-in "worked" but every league site stayed signed out):
  `identitytoolkit.googleapis.com` enabled on `golf-data-projects` (the calling
  project) AND `roles/firebaseauth.admin` for the runtime SA on `ssa-auth-71d16`.
  Without them `POST /api/session` 500s — and the CLIENT deliberately doesn't
  block sign-in on that failure, so the popup looks successful while the
  parent-domain cookie never exists. Both are in `./deploy.sh bootstrap` now.
  Diagnose via: apex logs "Failed to mint session cookie", league logs
  `/api/session/exchange` → 401.
