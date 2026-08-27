# ADR-0003: Apex-path consolidation (subdomains → SSA.com/‹league›)

- **Status**: Accepted, executed 2026-08-26 (build + DNS cutover same day)
- **Driver**: jdw; SEO analysis + decision 2026-08-26 ("Path A"), cutover GO same day
- **Supersedes the public half of** `cfl-elo-dashboard/docs/adr/0001-internal-public-hostname-split.md`
  (the internal half — `internal.‹league›.SSA`, Firebase + ADMIN_EMAILS — is unchanged)

## Context

Public league surfaces lived on per-league subdomains (`nfl.SSA`, `ncaaf.SSA`,
…), each a separate origin for search. Ten days after the SEO launch gate
opened (~17 indexed URLs, near-zero link equity), consolidating onto apex
paths was as cheap as it would ever be. Scope of record:
`~/Claude Projects/docs/APEX_CONSOLIDATION_SCOPE.md`; execution log:
`docs/APEX_MIGRATION_TRACKER.md` (workspace).

## The architecture

One **global external ALB** ("apex front door", `golf-data-projects`,
IP `34.160.208.32`) fronts the whole family:

- `apex-frontdoor-urlmap` — default → `ssa-landing-be`; path rules
  `/cfl /ncaaf /golf /nhl /nba /soccer /nfl` (+`/nfl/elomodel` on the longer
  prefix) + legacy-native `/elomodel` `/epl`, referencing **backend services
  in six projects** (cross-project service referencing, same org).
- **Legacy public hosts are permanent 301 host rules on the same map** —
  prefix-prepend redirects (`nfl.SSA/x` → `SSA.com/nfl/x`), soccer carves out
  `/epl` (host-swap only), `www` host-swaps. Their DNS repoints at the LB and
  is NEVER deleted; the redirects carry the SEO equity and old bookmarks.
- Port 80 → HTTPS 301 (`apex-frontdoor-http-redirect`); the old domain
  mappings did this implicitly.
- TLS via Certificate Manager cert map: `apex-frontdoor-cert` (apex+www) +
  `legacy-hosts-cert` (7 league hosts), both dns-authorization validated —
  the `_acme-challenge.*` CNAMEs are permanent renewal fixtures.
- Provisioning is staged + idempotent: `infra/apex-frontdoor.sh`
  (backends / urlmap / dns-auth / cert / frontend / http / redirects).

## Decisions

1. **Canonical URLs are pretty prefixed paths**: `/nfl`, `/nfl/mockdrafts`,
   `/nfl/elomodel`, `/ncaaf`, `/cfl`, `/golf`, `/nhl`, `/nba`, `/soccer`,
   and **flat `/epl`** (deliberately not `/soccer/epl` — EPL's native
   `root_path=/epl` serves the apex unchanged). Flat `/elomodel` stays routed
   as a belt for app-generated redirects but is non-canonical; flat
   `/mockdrafts` was DROPPED (that SPA's assets are host-rooted and would
   fetch ssa-landing statics on the apex).
2. **Dual-depth serving, `root_path`-only middleware.** Each league app
   serves its internal host unprefixed AND its apex path prefixed. The apex
   middleware (keyed strictly on the apex Host from `X-Forwarded-Host`)
   sets `scope["root_path"]` and NEVER strips `scope["path"]` — ASGI
   requires root_path ⊆ path; the strip-both form shipped first and 404'd
   every prefixed static while health routes passed (Mount child scopes
   resolved `static/static/…`). Bare `/‹pfx›` maps to `/‹pfx›/`. The
   middleware registers at module END (Starlette wraps later registrations
   outermost) so `cache_static_assets` sees the resolved scope; the cache
   check normalizes against root_path. Shells carry a `__ROOT__`/`<base>`
   bootstrap; asset tags are relative; public.js fetches prefix with
   `(window.__ROOT__ || "")`. Prefixed statics (with Cache-Control) are
   pinned in every repo's tests.
3. **nfl-elo keeps its native `/elomodel` root_path** and peels only the
   leading `/nfl` on the apex Host; bare `/nfl/elomodel` serves directly
   rather than slash-redirecting (the redirect rebuilt from the stripped
   scope and bounced to non-canonical flat `/elomodel/`).
4. **Internal hosts untouched.** No `internal.SSA.com/‹league›` exists; the
   operator tier stays on its subdomains, noindexed, with the same auth.
5. **The hub's robots.txt is THE robots for the apex host** — it carries a
   `Sitemap:` line per league sitemap (`/‹pfx›/sitemap.xml`,
   cross-submission); `_PORTFOLIO_URLS` mirrors each surface's declared
   canonical exactly and `_LEAGUE_PATH_PREFIXES` scopes the hub's
   sitemap-vs-meta test guard to hub pages.
6. **Auth/billing unchanged by design**: parent-domain `__session` cookie,
   entitlements, Stripe (all URLs already built from the apex), and the
   inplayLABS launch endpoint did not move. `IPL_TOOL_MAP` destinations
   now point at apex paths (no partner-side change; their launch URL was
   always the apex).

## Consequences

- End-state is ONE front door; the nfl/soccer GCLBs remain only for their
  `internal.*` hosts and retire after absorption (−$18/mo). Deadline
  pressure: the soccer LB cert cannot renew (its dns-auth CNAMEs are gone
  from DNS) and the nfl LB cert's public-host CNAME was repurposed for
  `legacy-hosts-cert` — retire both before their renewal windows (~Oct).
- Old Cloud Run domain mappings stay during the soak as instant rollback
  (repoint DNS back), then get deleted. Also queued: `auth.SSA` remnant
  teardown, the orphaned `www.nfl` domain mapping.
- Rollback after mapping deletion means re-creating mappings (cert
  re-provisioning window applies) — hence the soak.

## Rejected

- Per-league LBs with 301s (cost ×7, no consolidation benefit).
- `internal.SSA.com/‹league›` consolidation — zero SEO value (noindex),
  breaks operator bookmarks; possible later via the same LB if ever wanted.
- Uniform `root_path` via env — can't express dual depth on one service.
