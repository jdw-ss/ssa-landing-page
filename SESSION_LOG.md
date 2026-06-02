# Session Log — ssa-landing-page

Append-only, newest entries on top. Format defined in `~/Claude Projects/docs/DOCUMENTATION_PYRAMID.md` → `<project>/SESSION_LOG.md`.

Write an entry at the end of any non-trivial session (anything that produced commits, decisions, abandoned approaches, or mid-flight work). Skip for pure read-only / Q&A / typo-fix sessions.

---

<!-- New entries go directly below this line -->

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
