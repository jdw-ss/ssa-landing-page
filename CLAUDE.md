# SSA Landing Page / Apex Hub

## Snapshot

The apex for **sportsbookscienceanalytics.com** (apex + www) — since 2026-07-30
the portfolio's **customer auth + billing hub**, not just a landing page (see
`docs/adr/0001-apex-auth-billing-hub.md`). Four jobs: (1) league directory at
`/` + `/help` FAQ; (2) customer Google sign-in at `/signin` — the ONE origin
where the popup runs for the whole SSA family, with the Firebase auth handler
self-proxied at `/__/auth/*`; (3) the parent-domain `__session` SSO mint for
ANY verified Google user (no allow-list — this is the customer path; league
internal hosts keep their own `ADMIN_EMAILS` mints); (4) Stripe billing:
`/pricing` → Checkout, `/account` + Customer Portal, and the webhook that
writes Firestore entitlements. Status: **converted, not yet deployed** —
paywall bootstrap pending (`./deploy.sh bootstrap`).

## Stack

- **Language**: Python 3.11 (use `python3`, no venv on the dev box)
- **Framework**: FastAPI + uvicorn; vanilla-JS static pages (no build step)
  served from `static/`
- **Data layer**: Firestore in the shared `ssa-auth-71d16` project —
  `customers/{uid}` + `entitlements/{uid}` (webhook-only writes)
- **Billing**: Stripe Checkout + Customer Portal + webhook (`api/billing.py`);
  SKU catalog + slugs in `api/entitlements.py`
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

- **Launch gate: /pricing shows NO dollar amounts and no purchase path until
  Stripe is configured on the service.** John is finalizing prices (2026-07-30)
  — the deployed site renders "Pricing announced at launch" + disabled
  "Launching soon" buttons. `SHOW_PREVIEW_PRICES=1` overrides for LOCAL
  exploration only (it's in the launch config) — never set it on Cloud Run.
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
- **Only the Stripe webhook writes `entitlements/{uid}`.** Never hand-edit or
  write from request handlers; it's recomputed from the full subscription list
  on every event (idempotent, self-healing).
- **Homepage Live / Coming-Soon badges are manual and drift.** When a league
  launches its public surface, flip its card here (CFL sat stale once already).
- **Apex is `noindex, nofollow` + `robots.txt Disallow`** until launch — flip
  BOTH (meta in `static/index.html`, `static/robots.txt`) when the paywall
  goes live (John's call, 2026-07-30).
- Two domain mappings (apex + www) — recreating either triggers 15min–hours of
  cert re-provisioning; check both when debugging cache/cert issues.
- `nginx.conf` at repo root is **dead** post-conversion (Dockerfile no longer
  references it) — kept pending John's OK to delete.
- Firestore reads fail-closed: `/api/me` 503s rather than showing a paying
  customer as unsubscribed; `/api/billing/catalog` degrades to signed-out
  rendering instead.
