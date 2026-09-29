# 0001 — The apex becomes the SSA customer auth + billing hub

**Status**: Accepted; **amended 2026-09-29** (four statements overtaken by
later rulings: the discount figures, the sole-writer rule, monthly-only, and
public ATS proof; see the Amendment at the end). The architecture stands.
**Date**: 2026-07-30
**Project**: ssa-landing-page (portfolio-wide impact — every league service)

> **Superseded in part by `ssa-landing-page/docs/adr/0003-apex-path-consolidation.md`
> (2026-08-26)** — only the public-host topology assumed below. Public league
> surfaces are now apex paths (`SSA.com/<league>`) behind one front door and the
> `<league>.SSA` subdomains 301 permanently; `internal.<league>.SSA` is
> unchanged. Every auth/billing decision here — the single sign-in origin, the
> open session mint, Firestore entitlements, the Stripe model — stands as
> written (ADR-0003 §6).

## Context

The SSA league sites are going behind a customer paywall. John's requirements
(2026-07-30): sign-in persists across all subdomains; every subdomain keeps
free content; per-sport monthly packages unlock a league's full public
product; an NCAAF+NFL bundle at 20% off; an All-Access package at 50% off
everything; `internal.<league>.SSA` keeps operator-only tools; public league
pages become module views with free + locked tabs.

> **[Amended 2026-09-29]** 20% and 50% were the 2026-07-30 asks. The price
> ladder John decided on 2026-08-08 is **25% off for both** the bundle and
> All-Access (`api/entitlements.py` SKU comment + `LAUNCH_PRICE_CENTS`). See
> the Amendment.

What already existed:

- **Cross-subdomain SSO is built** (cfl ADR-0002): a parent-domain `__session`
  cookie minted by `POST /api/session`, recovered by `GET /api/session/exchange`,
  with the Firebase auth handler self-hosted per app (cfl ADR-0004). But every
  mint is gated by `ADMIN_EMAILS` — it's operator SSO, not customer SSO.
- **Paywall enforcement has a convention**: server-side stripping/gating only
  (cfl ADR-0003, guidelines "actionable vs proof"). The public lock-cards are
  inert HTML; internal-only API routes 404 on public hosts.
- **No billing anything**: zero Stripe code, no signup, an inert "Account ▾ /
  soon" dropdown duplicated across ~11 files.
- The prior plan of record (`ideas.md` Phase 5) sketched Stripe Checkout + a
  single `subscriber:true` custom claim — too coarse for per-sport packages,
  bundles, and All-Access.

## Decision

1. **The apex (`sportsbookscienceanalytics.com`) is the single customer
   sign-in + billing origin.** `ssa-landing-page` converts from static nginx
   to a soccer-hub-style FastAPI service serving the same pages plus:
   `/signin`, `/pricing`, `/account`, the session routes, a self-hosted
   `/__/auth/*` proxy, and the Stripe integration. League sites never run a
   sign-in popup for customers — they link to
   `https://sportsbookscienceanalytics.com/signin?next=<return-url>` and
   recover the session silently via their existing `/api/session/exchange`.
   Only ONE origin therefore needs Firebase authorizedDomains + OAuth
   redirect-URI registration for customers, instead of eight bare hosts.

2. **The apex session mint accepts any verified Google user.** Its
   `POST /api/session` has no allow-list. Authorization is entitlements, not
   identity. The league services' own mints (internal hosts, `ADMIN_EMAILS`)
   are untouched; internal surfaces remain operator-only.

3. **Entitlements live in Firestore on `ssa-auth-71d16`** (the shared auth
   project), written ONLY by the Stripe webhook:
   - `customers/{uid}` → `{email, stripe_customer_id}` (written at first checkout)
   - `entitlements/{uid}` → `{slugs, packages, updated_at}` — recomputed on
     every subscription event as a pure function of the customer's full
     Stripe subscription list (idempotent, self-healing).
   - Slugs are per-sport (`cfl`, `ncaaf`, `nfl`, `golf`, reserved `soccer`,
     `nba`, `nhl`) plus the wildcard `all` granted by All-Access — new sports
     join All-Access automatically.
   - **Why not custom claims alone** (the Phase 5 sketch): the 14-day session
     cookie freezes claims at mint time, so purchases wouldn't activate and
     cancellations wouldn't deactivate until re-mint. Firestore reads cut both
     ways immediately; league services will cache lookups in-process (~60s).

   > **[Amended 2026-09-29]** The webhook is no longer the ONLY writer. Since
   > 2026-08-26 the inplayLABS partner bridge (`api/partner.py`, ADR-0002)
   > also writes `entitlements/{uid}`, but only for synthetic
   > `ipl_*`/`ipltest_*` uids. See the Amendment.

4. **Stripe model — stacked à-la-carte subscriptions** (John's decisions):
   monthly recurring only; each purchase is its own subscription; access is
   the union of slugs across subscriptions in status `active` / `trialing` /
   `past_due`. SKU catalog (in `api/entitlements.py`, price ids via
   `STRIPE_PRICE_<SKU>` envs): `sport_cfl`, `sport_ncaaf`, `sport_nfl`,
   `sport_golf`, `bundle_football` (ncaaf+nfl, 20% off the sum),
   `all_access` (`all`, 50% off the sum of every sellable sport). No free
   trials; `allow_promotion_codes=True` at checkout (dashboard-managed promo
   codes now; true refer-a-friend is a post-launch fast-follow). Checkout
   refuses SKUs whose slugs the customer already fully holds (409).

   > **[Amended 2026-09-29]** Not monthly-only: since 2026-08-08 every SKU
   > also sells a 6-month prepaid term (`TERMS` in `api/billing.py`; Stripe
   > `interval_count=6`). The discounts are 25% for both `bundle_football`
   > and `all_access`, not 20% / 50%. See the Amendment.

5. **Free tier is anonymous.** Power rankings are free on every league site
   with no account; the existing public ATS aggregates stay as proof.
   Accounts exist for purchasing and managing packages only.

   > **[Amended 2026-09-29]** The ATS clause is reversed. John made ATS
   > internal-only on 2026-07-31, the day after this ADR, so there is no
   > public ATS tab, route or aggregate on any league. Power rankings stay
   > free. See the Amendment.

6. **The paid line** (per John): subscribers get power rankings, schedules
   with predicted lines/picks, forecast grids, and team detail sheets on the
   PUBLIC league hosts. Admin tools and ATS historical detail stay
   internal-only. Enforcement stays server-side: league public routes will
   gain a `require_entitlement("<sport>")` dependency (teaser payload or 402
   without it) — client-side hiding remains banned.

## Consequences / league-side rollout (separate sessions)

Per league (CFL, NCAAF, NFL×2 modules, Golf to start):

- Vendor a `require_entitlement` dependency (verify `__session` → uid →
  Firestore slugs, in-process cache; `all` counts for every sport) next to
  `api/hosts.py`.
- Un-host-gate `GET/POST/DELETE /api/session*` + the `/__/auth/*` proxy is NOT
  needed publicly — only `GET /api/session/exchange` must become reachable on
  the BARE host so public pages can recover customer sessions (today those
  routes are `require_internal_host`-gated).
- Ensure the bare host is in Firebase `authorizedDomains` (needed for
  `signInWithCustomToken` API-key checks; soccer already is).
- Build the public module page: free tabs (rankings, ATS proof) + entitled
  tabs (schedule/picks, forecast, team sheets), teaser + `/pricing` CTA when
  locked. Wire `serve_spa` to it.

  > **[Amended 2026-09-29]** There is no ATS proof tab. See decision 5's note.
- Grant each league runtime SA `roles/datastore.user` on `ssa-auth-71d16`.
- NFL/Soccer path-split hosts inherit by module: the `nfl` slug covers
  `/mockdrafts` + `/elomodel`; `soccer` covers `/epl` + future leagues.

Deferred by design: the game-preview feature (click a schedule game → compare
two team sheets) and a "1 free schedule line" teaser — John explicitly parked
these; the public schedule payload should leave room for a teaser row.

## One-time bootstrap (see `./deploy.sh bootstrap` for exact commands)

Firestore database in `ssa-auth-71d16` (us-east1); apex runtime SA →
`roles/datastore.user` on `ssa-auth-71d16` + `serviceAccountTokenCreator` on
itself; apex + www added to Firebase authorizedDomains (merge-PATCH, it's a
full replace); `https://sportsbookscienceanalytics.com/__/auth/handler` added
to the shared OAuth web client's redirect URIs (Console-only); `FIREBASE_*`
envs on the service; Stripe products/prices created (test mode first),
secrets in Secret Manager, webhook endpoint registered for
`checkout.session.completed` + `customer.subscription.*`. At launch: flip the
apex `noindex` meta + `robots.txt`.

## Alternatives considered

- **Custom claims as the entitlement store** — rejected: stale-cookie problem
  above, plus 1000-byte claim limits as sports multiply.
- **Per-league sign-in popups for customers** — rejected: 8× OAuth
  redirect-URI + authorizedDomains registrations and 8 more places for the
  mobile-handshake class of bugs; the parent-domain cookie already makes one
  origin sufficient.
- **One multi-item subscription per customer** (add/remove sports as line
  items with automatic bundle discounts) — rejected for v1: proration and
  discount-rule engineering for marginal UX gain; stacked subscriptions are
  simpler and the Customer Portal handles per-package cancel. Revisit if
  customers hold many single sports.
- **A separate `billing.SSA` service** — rejected: the apex already owns the
  brand moment, the account stub, and `/help`; one fewer host to bootstrap.

## Amendment 2026-09-29: four statements overtaken by later rulings

**The architecture stands unchanged.** One sign-in and billing origin on the
apex, the open any-user session mint, Firestore entitlements instead of custom
claims, stacked à-la-carte subscriptions and server-side enforcement all hold.
Four facts inside it were overtaken by later rulings. The original text above
stays as written because this is a record. Where it disagrees with the code,
the code and this Amendment are right.

1. **Both discounts are 25%, not 20% / 50%** (John's price ladder,
   2026-08-08). `bundle_football` costs $149.99/mo against $199.98 for NCAAF
   plus NFL, and `all_access` costs $299.99/mo against $399.96 for the four
   sellable sports. Both are 25% off, rounded up to .99. Evidence: the SKU
   catalog comment and the `LAUNCH_PRICE_CENTS` table with its comment in
   `api/entitlements.py`; the /pricing save chips read "Save 25%".
2. **The Stripe webhook is no longer the only entitlements writer.** ADR-0002's
   inplayLABS bridge (built 2026-08-25, armed on both lanes 2026-08-26) writes
   `entitlements/{uid}` through `partner.grant()`, only for synthetic
   `ipl_*`/`ipltest_*` uids. `grant()` refuses any other uid at runtime.
   Partner docs carry extra fields (`grants`, `source: inplaylabs`,
   `expires_at`) and merge per sport. The webhook's `_recompute` still
   overwrites a customer doc in full from Stripe state. The `grant()` guard
   works in one direction only. It keeps the partner writer off customer
   docs, but nothing on the checkout path refuses an `ipl_*` session. So the
   webhook leaves partner docs alone only while partner members don't buy
   through /pricing.
   **Resolved later on 2026-09-29:** billing now refuses partner sessions
   with a 403, and the webhook warns on a partner uid instead of skipping
   it. See ADR-0002 decision 2's second amendment note.
3. **Not monthly-only.** Since 2026-08-08 every SKU sells two terms: monthly,
   and a 6-month prepaid cycle at 50% off six monthly cycles. The code has
   `TERMS = ("monthly", "6mo")` in `api/billing.py`, and the bootstrap mints
   the 6-month prices with `interval_count=6` (`scripts/_bootstrap_common.py`).
   A term changes the price and how often the subscription renews. It never
   changes the slugs.
4. **No public ATS proof.** John pulled public ATS on 2026-07-31. A betting
   model's ATS rate sits near 50-55%, and customers read that as "this model
   loses money". The free ATS aggregate routes and tabs were removed from the
   league public surfaces, and ATS is now an internal-only diagnostic.
   Evidence: the ATS gotchas in `ncaaf-dashboard/CLAUDE.md` and
   `cfl-elo-dashboard/CLAUDE.md`, and `cfl-elo-dashboard` ADR-0003 (Superseded
   2026-07-31).
