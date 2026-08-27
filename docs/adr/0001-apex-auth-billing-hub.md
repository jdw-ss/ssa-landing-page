# 0001 — The apex becomes the SSA customer auth + billing hub

**Status**: Accepted
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

5. **Free tier is anonymous.** Power rankings are free on every league site
   with no account; the existing public ATS aggregates stay as proof.
   Accounts exist for purchasing and managing packages only.

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
