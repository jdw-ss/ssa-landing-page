# Session Log — ssa-landing-page

Append-only, newest entries on top. Format defined in `~/Claude Projects/docs/DOCUMENTATION_PYRAMID.md` → `<project>/SESSION_LOG.md`.

Write an entry at the end of any non-trivial session (anything that produced commits, decisions, abandoned approaches, or mid-flight work). Skip for pure read-only / Q&A / typo-fix sessions.

---

<!-- New entries go directly below this line -->

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
