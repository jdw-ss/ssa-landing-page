# Session Log — ssa-landing-page

Append-only, newest entries on top. Format defined in `~/Claude Projects/docs/DOCUMENTATION_PYRAMID.md` → `<project>/SESSION_LOG.md`.

Write an entry at the end of any non-trivial session (anything that produced commits, decisions, abandoned approaches, or mid-flight work). Skip for pure read-only / Q&A / typo-fix sessions.

---

## 2026-09-29 — Deployed `ssa-landing-00043-sr2` (partner checkout block, webhook retry, credit-free upgrade copy)

**Agent**: claude-opus-5-5 | **Branch**: main | **Commits**: 21c791d..bb44b22 deployed; this commit (docs)

- John approved after the local preview (normal /pricing + /account?upgrade=success on :8085, partner
  view on :8087). `./deploy.sh` at HEAD bb44b22 → `ssa-landing-00043-sr2`, 100% traffic; rollback target
  `ssa-landing-00042-xzv`. No config-sanity warnings; apex, www and /api/health 200; sweep scheduler kept.
- Live checks: /pricing carries "When you upgrade, you pay the new package price today, and your current
  plan is canceled once checkout completes." and no credit/proration wording; /api/billing/catalog
  anonymous → partner_member false, partner_skus []; anonymous POST /api/billing/checkout → 401;
  /account ships the new "Upgrade received" banner; the partner strings ship in the page script.
- Docs flipped from "not yet deployed" to live (CLAUDE.md, ADR-0001 item 2, ADR-0002 notes).
- 2026-09-30: John reports a manual test of the live deploy by a tester passed ("it all works"); the
  specific paths exercised were not recorded.
- Still open: the upgrade-credit sandbox test (John creating a Dashboard sandbox copied from live; key to
  Keychain `STRIPE_SANDBOX_KEY`), then `/terms` §7 + `invoice_now` decisions.

---

## 2026-09-29 — Upgrade-credit experiment: blocked; what the docs say

**Agent**: claude-opus-5-5 | **Branch**: main | **Commits**: this commit (docs only)

- **Question:** does `stripe.Subscription.cancel(sub_id, prorate=True)` in `_retire_superseded`
  (the upgrade webhook) ever credit the customer? The upgrade copy (44bc2eb, NOT deployed) says
  "unused time on your current plan is credited to your account".
- **Experiment did not run:** the only key on this machine is the `.env` sandbox key
  (`rkcs_test_…`, a Stripe-CLI sandbox restricted key; the sandbox was due to expire 2026-08-15 per
  this file's Stripe section). The harness only allowed `sk_test_`/`rk_test_` and stopped before any
  call. Also: no `stripe` package outside a venv. Nothing was created in Stripe.
- **What Stripe's docs say (verified by fetch, not by experiment):** `prorate=True` creates a PENDING
  proration invoice item tied to the cancelled subscription, not customer-balance credit;
  "scheduled invoices for subscriptions other than the specified subscription will ignore the
  invoice item"; after an immediate cancel, items "won't be processed unless you specifically
  generate an invoice that includes them". The new plan's first invoice is paid inside Checkout
  BEFORE the cancel, so it can never carry the credit. Most likely the credit is never applied.
- **Candidate fix and its catch:** `cancel(..., prorate=True, invoice_now=True)` bills the pending
  items on a final negative invoice, moving the credit to the customer balance (applies to the next
  finalized invoice). BUT the Managed Payments docs list as unsupported "creating a subscription
  outside of Checkout or Payment Links", "attaching invoice items … on a Customer object to a
  Managed Payments subscription" and "generating a one-off invoice … outside the billing period" —
  so `invoice_now` may be refused or behave differently on this account. Friend-code subs (100% off
  forever) produce $0 credit either way.
- **A faithful test needs:** a fresh sandbox with Managed Payments configured like production
  (John: `stripe sandbox create`, then `python3 -m scripts.stripe_bootstrap_test`), the harness
  allowing `rkcs_test_` with a `livemode == false` balance check, and the REAL Checkout flow in test
  mode (test card 4242…) rather than `Subscription.create`; record `session.managed_payments` and
  `subscription.billing_mode`; scenarios: monthly→bundle, 6-month→monthly bundle, two subs→bundle,
  and each again with `invoice_now=True`; advance a test clock past two renewals.
- **Open:** `/terms` §7 ("the upgrade is prorated: you receive credit … against the new one") and
  the undeployed copy both depend on the answer. Customers who upgraded since 2026-08-26 may hold
  unapplied pending credits — **checked 2026-09-29, read-only, live account: 0 pending invoice items, 0 proration credit
  lines applied on the 12 invoices since 2026-08-26, 0 subscriptions canceled since 2026-08-26.**
  No customer has upgraded through the 08-26 flow, so no one is owed credit today.
- **Follow-up, same day (John's decision; separate commit, not deployed):** ship the partner block
  and the webhook double-billing fix now, with upgrade copy that makes NO credit claim. /pricing
  foot-notes and the checkout partial-overlap 409 now say "you pay the new package price today, and
  your current plan is canceled once checkout completes" (the webhook cancels it immediately on
  `checkout.session.completed`); the confirm box drops its credit sentence; the `?upgrade=success`
  banner reads "Upgrade received — your previous plan is being replaced. Your new package can take a
  minute or two to appear under Your packages." (the old "is listed below" was false until the
  webhook ran, and that URL has no `&sku=` poll). `tests/test_upgrade_copy.py` now bans
  credit / unused time / prorat / difference on both pages and in every 409 detail under `api/`
  (failed 5/5 on the old copy first). The sandbox check is no longer a deploy gate for the copy;
  it still decides `/terms` §7 (untouched, John's) and `invoice_now`.

---

## 2026-09-29 — Upgrade copy review fixes: credit claim narrowed, webhook cancel retries (not deployed)

A review of `c575003` (the entry below) raised 7 findings. Six were real and
fixed in two commits: `44bc2eb` (copy, 409s, docs) and the webhook commit
that carries this entry. One (ToS §7) is John's legal text and was left
alone. Nothing is pushed or deployed, and no Stripe call was made.

- **"Credited toward your next renewal" was not established, so the copy
  now says less.** The webhook cancels replaced subscriptions with
  `prorate=True` and no `invoice_now`. Stripe's API reference says that
  "will generate a proration invoice item that credits remaining unused
  time": a pending invoice item tied to the cancelled subscription, not
  customer-balance credit. The docs conflict on whether any later invoice
  applies it (the invoice-item reference: other subscriptions' scheduled
  invoices ignore an item set to one subscription; the cancel guide: such
  items "won't be processed unless you specifically generate an invoice
  that includes them", but also that another active subscription might bill
  them). Changed: foot-notes, confirm box and checkout 409 "credited toward
  your next renewal" → "credited to your account"; the /account banner →
  "will be credited to your account". That is John's direction (unused time
  is credited) minus the unverified destination. A test now fails if either
  page says "next renewal" or "future invoice(s)". CLAUDE.md has a new
  gotcha with the sandbox check to run before deploying this copy, and John's
  choice after it: restore "toward your next renewal" if the credit lands
  there; otherwise cancel with `invoice_now=True` (a finalized negative
  invoice moves the credit onto the customer balance, which Stripe applies
  to the next finalized invoice; a billing change) or drop the credit
  sentence. **Corrects the entry below**: its "credits the unused time to
  the customer balance for the next invoice" and "the credit sits on the
  customer balance for whichever invoice comes next" are wrong. The same
  "customer-balance credit" wording is fixed in CLAUDE.md, the
  `api/billing.py` and `api/app.py` docstrings, and the test docstrings.
- **Webhook swallowed every cancel failure** (existing code). Any exception
  from `Subscription.cancel` was logged at INFO as "assuming already
  retired" and answered 200, so a transient Stripe error left the old plan
  billing beside the new one and Stripe never retried. New
  `_retire_superseded` re-reads the subscription after a failed cancel:
  `canceled`/`incomplete_expired` or `resource_missing` is success (a
  redelivery), anything else logs an ERROR ending "may still be billing".
  The webhook then answers 500, after the entitlement write, so the new
  plan's access is live and Stripe redelivers the event.
- **/account banner raced the webhook**: "will be credited" (future tense).
  "Your new package is listed below" has the same race and predates this;
  left as is (a poll would need `&sku=` on the upgrade success URL).
- **Confirm box promised a firm amount**: now "the full price, $X/month
  plus any applicable tax". Stripe is merchant of record under Managed
  Payments and adds sales tax where it applies; no `tax_behavior` is set
  anywhere. An existing customer credit balance could also lower the
  charge; that only favours the member, so it isn't worded.
- **Same-SKU term-downgrade 409s were wrong** (holding 6-month X, buying
  monthly X). Checkout said "choose the 6-month X instead", the plan they
  already hold; the preview said it "renews monthly automatically only if
  you cancel the 6-month term first", but cancelling ends the plan. Both
  paths now go through `_raise_blocked`: "To move to monthly, cancel it from
  your account and subscribe monthly once the paid term ends." The /pricing
  UI can't reach either message; a direct API call can.
- **Partner live check overstated**: CLAUDE.md now says no partner uid has
  a Stripe customer *mapping* (only Firestore was read; an unmapped Stripe
  customer is possible if `set_customer` failed, but no session could
  follow). ADR-0002's note says "as of 2026-09-29", not "before the guard"
  (the guard isn't deployed).
- **ToS §7 "Upgrades" (not edited, John)**: besides the gaps listed below,
  it covers only "a broader package", not the monthly → 6-month switch the
  same flow handles, and "against the new one" depends on the sandbox
  result. Decide after the sandbox check; bump `TOS_VERSION` if it changes.
- **Tests**: suite 149 passed (was 144). New: no landing claim on either
  page; the same-SKU downgrade 409 on checkout, preview and change; a
  missing replaced sub is retired; a replaced sub that is still active, or
  whose status can't be read, fails the event with an ERROR after the
  recompute. Re-pinned: foot-notes, confirm box (tax), banner (future
  tense), the checkout 409, and the redelivery test (which now fakes
  `retrieve`). Against `c575003`, the 6 copy/409 tests fail; against
  `44bc2eb`, both webhook failure cases fail ("DID NOT RAISE"). The
  missing-sub test passes on the old code and is a regression lock.
- **No `?v=` bump**: every page change is inline in `pricing.html`
  (`no-cache`) or `account.html` (`no-store`), per `_page` in `api/app.py`.
- **Preview** (local, `ssa-landing`): the /account `?upgrade=success`
  banner and the /pricing foot-notes read the new text; the confirm box,
  rendered by calling `renderUpgradeConfirm`, fits at 1024px and 375px with
  no horizontal scroll.

**Not done here:** the sandbox check (no Stripe calls this session), John's
calls on the credit mechanism and ToS §7, push, deploy.

## 2026-09-29 — Upgrade copy matches the Checkout flow; partner live check recorded (not deployed)

John decided to reword the upgrade copy to match what the code has done
since 2026-08-26. An upgrade opens a new Checkout at the full new package
price (`apply_plan_change`; `plan_change_preview` quotes that plain price as
`due_now_cents`). After `checkout.session.completed` the webhook cancels the
replaced subscription(s) with `prorate=True`, which credits the unused time
to the customer balance for the next invoice. Approved direction: "you pay
the new package price today; unused time on your current plan is credited
toward your next renewal", and a plain "Upgrade" button. This closes the
"Still stale, NOT changed" item in the stale-doc entry below.

- **Strings changed** (old → new):
  - `/pricing` foot-notes: "Upgrades are prorated, so you only ever pay the
    difference." → "When you upgrade, you pay the new package price today,
    and unused time on your current plan is credited toward your next
    renewal."
  - `/pricing` overlap button: "Upgrade — pay the difference" → "Upgrade".
  - `/pricing` confirm box: "You'll confirm $X/month at checkout … Unused
    time on Y is credited to your account and applies to future invoices."
    → "You'll pay the full price, $X/month, at checkout … Unused time on Y
    is credited toward your next renewal." It says "the full price", not
    "the new package price", because the same box serves the same-package
    monthly → 6-month switch.
  - `/account?upgrade=success` banner: "you were only charged the prorated
    difference" → "unused time on your previous plan is credited toward
    your next renewal".
  - `create_checkout_session` 409 (shown verbatim by /pricing if checkout
    409s): "so you're only charged the difference" → "instead: you pay the
    new package price today, and unused time on your current plan is
    credited toward your next renewal".
  - Two `pricing.html` JS comments (the overlap branch and `buy()`) no
    longer say "prorated"/"in-place".
  No copy promises a refund: the credit is balance for the next renewal,
  and the foot-notes still say payments are non-refundable except where
  law requires.
- **ToS not edited (open for John):** `/terms` §7 "Upgrades" says "the
  upgrade is prorated: you receive credit for the unused portion of your
  current subscription against the new one." It doesn't contradict the new
  copy outright, since credit for unused time is what happens. But it
  doesn't say the full new price is due at checkout. "Prorated" is the word
  the old "pay the difference" copy leaned on. And the credit sits on the
  customer balance for whichever invoice comes next, which is "against the
  new one" only when no other subscription renews first. The §7 automatic
  renewal price notice doesn't mention upgrades. If John changes §7
  materially, bump `TOS_VERSION`.
- **Tests** (4 new in `tests/test_upgrade_copy.py`, 2 existing tests
  re-pinned; suite 144 passed, was 140): the foot-notes sentence; no
  "difference"/"prorat" anywhere in pricing.html or account.html, comments
  included; the confirm box, reached by clicking the Upgrade button through
  the page's own `buy()` (`tests/js/render_pricing.js` gains an optional
  click-sku argument and a `_preview` reply); the /account banner
  (`tests/js/account_portal.js` gains an optional search-string mode); the
  button label in `test_pricing_held_package_states_for_a_normal_customer`;
  and the 409 detail in
  `test_checkout_refuses_a_partial_overlap_and_points_at_the_upgrade`. With
  HEAD's `pricing.html`, `account.html` and `billing.py` in a scratch copy,
  all 6 of those fail. The confirm-box test stops at the button label there,
  and a direct harness run shows HEAD's box reads "You'll confirm …
  applies to future invoices".
- **No `?v=` bump**: every change is inline in `pricing.html` (served
  `no-cache`) or `account.html` (`no-store`) via `_page` in `api/app.py`;
  no `/static` JS or CSS file changed.
- **Partner live check** (orchestrator, 2026-09-29, read-only, Firestore
  `ssa-auth-71d16`): no inplayLABS partner uid has a Stripe customer
  (`customers/{uid}.stripe_customer_id` is written before any Checkout
  session is created), so no partner account has reached checkout. All 11
  partner `entitlements` docs are in partner shape (`source: inplaylabs`
  plus `grants`), none overwritten by Stripe, and the one partner
  `customers/{uid}` doc holds only the ToS stamp. This closes "No live
  check for an existing partner purchase" in the partner-guard entry below.
  Recorded in CLAUDE.md (the partner-writer gotcha) and a third ADR-0002
  decision 2 note. Stripe was not queried, so the at-deploy sweep stays.

**Not done here:** no push, no deploy. The partner billing guard and this
copy both go live with the next deploy.

## 2026-09-29 — Partner billing guard: review fixes (still not deployed)

A review of `21c791d` (the entry below) raised 11 findings. Each was checked
against the code and the preview, and all were real. Two were duplicates.
Nothing is pushed or deployed: the live revision still accepts partner
checkout.

- **Partner cards promised a purchase path that doesn't exist.** Every card
  a partner didn't hold read "Managed through inplayLABS", and the note and
  the 403 detail said to add tools on inplayLABS. But inplayLABS sells one
  tool per sport (nfl, ncaaf, cfl), never Golf, the bundle or All-Access.
  The catalog now carries an additive `partner_skus` (partner sessions
  only): the sport SKUs whose slug the live `IPL_TOOL_MAP` grants, `[]` when
  the map is unset or malformed. Cards read "✓ Included through inplayLABS",
  "Available through inplayLABS" or "Not available with an inplayLABS
  sign-in". The note and `PARTNER_BILLING_DETAIL` now say packages can't be
  bought on an inplayLABS sign-in, and send the member to inplayLABS only
  for the models it offers. **Open for John:** whether to point partners at
  a separate Google-account purchase for Golf/bundle/All-Access. Nothing
  here invents that path.
- **/pricing foot-notes** (promo codes, prorated upgrades, an `/account`
  link that bounces an email-less session to /signin) are hidden for a
  partner session.
- **Partner note gutter**: `width: calc(100% - 48px)`, so at 375px it sits
  at 24–351 like the cards, instead of 0–375. `.free-strip` and `.banner`
  have the same edge-to-edge behaviour; left alone as an optional follow-up.
- **/account Manage billing** shows the 403 detail instead of "Try again in
  a moment". Only the preview (whose dev stub has an email) reaches it
  today.
- **Docs claimed the guard was live.** CLAUDE.md (data layer and the
  partner-writer gotcha), ADR-0002's amendment note, ADR-0001's resolved
  note and the workspace `PROJECT_INDEX.md` row now say "in code, NOT yet
  deployed". The "can only be a pre-guard purchase" overstatements in those
  places and in the `partner_uid.py`/`partner.py`/`_warn_if_partner`
  docstrings now also list a Checkout session opened before the deploy
  (Checkout sessions set no `expires_at`, so Stripe's 24h default applies)
  and a Dashboard-created subscription. CLAUDE.md adds the at-deploy step:
  list open Checkout sessions and customers whose `client_reference_id` or
  `metadata.uid` starts with `ipl_`/`ipltest_`, expire them, and reconcile.
- **Finding the partner WARNING**: the app logs plain text, so the level is
  only a prefix inside `textPayload`. CLAUDE.md now gives the Logs Explorer
  filters (`textPayload:"resolved to inplayLABS partner uid"`, and
  `"refused for inplayLABS partner uid"` for the refusals). A test fails if
  either stops matching the emitted line.
- **Tests** (33 new, suite 140 passed, was 107): lookalike uids (`iplAbc…`
  28 alphanumerics, `IPL_`, `Ipl_`, `ipltestx`, …) are not partner uids,
  and a real `iplAbc…` uid bills normally. Mutating the predicate to
  `startswith("ipl")` or adding `.lower()` fails 8 and 3 tests. Other new
  tests cover the `partner_skus` catalog field, per-card availability, the
  copy, no reachable `/account` link on the partner view, the gutter, the
  /account 403 banner (new `tests/js/account_portal.js` harness, with a
  shared `tests/js/fakedom.js`), and normal-visitor held states (Active,
  Included in Bundle/All-Access, Upgrade, the 6-month switch via the page's
  own term-toggle handler, and the launch gate). 11 of the new tests failed
  before the fixes. The rest are regression locks on behaviour that was
  already right. A scratch render of `d94c593`'s pricing.html against this
  one gave identical cards for 16 non-partner payload/term combinations.
- **Preview**: the workspace `ssa-landing-partner` launch config (not in
  git) gains an `IPL_TOOL_MAP` with the three tools. Checked at desktop and
  375px on 8087, and 8085 (normal) is unchanged. No static JS or CSS file
  was edited (inline page code only), so no `?v=` bump.

## 2026-09-29 — Partner members blocked from Stripe (ADR-0002 decision 2 enforced)

John decided to block inplayLABS partner members from Stripe checkout. This
closes the one-way-guard gap logged in the entry below. Before, a partner
member who bought through /pricing would have their partner doc overwritten
by the webhook recompute, and their next launch would then drop the Stripe
slugs.

- **One predicate**: new `api/partner_uid.py` holds `UID_PREFIX`,
  `TEST_UID_PREFIX` and `is_partner_uid()`, and is the only place in `api/`
  that spells the prefixes. `partner.py` re-exports the constants and
  `grant()` calls the predicate.
- **Server refusal** (`billing._refuse_partner`, a 403 whose detail names
  inplayLABS, raised before any Stripe or Firestore call):
  `create_checkout_session` (`POST /api/billing/checkout`),
  `plan_change_preview` (`GET /api/billing/change-preview`: it lists Stripe
  subscriptions when a customer mapping exists, and it is step one of both
  purchase flows), `apply_plan_change` (`POST /api/billing/change`),
  `create_portal_session` (`POST /api/billing/portal`), and
  `_get_or_create_customer` as the backstop. Not gated: `/api/me` and
  `/api/billing/catalog` (read-only), the session and partner routes (not
  billing), and the webhook (not session-bound).
- **Webhook**: never skips a partner uid. Skipping would leave a legacy
  purchase's doc out of step with Stripe, so a cancellation would never
  revoke, and a non-2xx only makes Stripe retry. It still recomputes and
  logs a WARNING naming the uid (`_warn_if_partner`) on both
  `checkout.session.completed` and `customer.subscription.*`.
- **/pricing**: the catalog gains an additive `partner_member` flag, from
  the same predicate. For a partner session the page shows an inplayLABS
  note, and each card's button becomes inert text ("✓ Included through
  inplayLABS" / "Managed through inplayLABS", with no `/account` link,
  because /account bounces an email-less session to /signin). Anonymous and
  normal signed-in visitors are unchanged. The flag comes from the server,
  not from `account.js`'s client detection: `account.js` is vendored
  byte-identically to every surface, and a copy of the regex in pricing.html
  would be a third prefix source. /account needed no change, because it
  already redirects an email-less partner session before its Manage billing
  button renders. No static JS or CSS file was edited, so no `?v=` bump.
- **Local preview**: `DEV_UID` (dev mode only) overrides the stub uid, and
  a new workspace launch config `ssa-landing-partner` (port 8087,
  `DEV_UID=ipl_preview`, `DEV_ENTITLEMENTS=nfl`) renders the partner view.
  It was checked in the preview pane at desktop and 375px: the note shows,
  the NFL card reads Included, and checkout and change-preview return 403.
  The normal config (8085) still shows Subscribe and Active.
- **Tests**: new `tests/test_partner_billing_guard.py` (20) plus
  `tests/js/render_pricing.js`, which runs pricing.html's own script in a
  node vm with a fake DOM. On a scratch copy of HEAD `d94c593`, 18 of the 20
  fail and the 2 normal-uid controls pass. The AST scan finds `ipl_`/
  `ipltest_` literals in HEAD's `partner.py`. HEAD's /pricing renders
  Subscribe/Upgrade buttons for a partner payload, and labels a
  partner-held NFL card "Included in Football Bundle". Suite: 107 passed
  (was 87).
- **Docs**: CLAUDE.md (data layer, run-locally partner view, the catalog
  contract, and the partner-writer gotcha now "enforced since 2026-09-29"),
  the ADR-0002 decision 2 second amendment note, an ADR-0001 Amendment item
  2 resolved note, the docstrings in `partner.py`, `billing.py` and
  `auth.py`, and the workspace `PROJECT_INDEX.md` row.

**Not done here:** no deploy (the live service still accepts partner
checkouts until the next deploy). No live check for an existing partner
purchase either, which John asked for; after deploy, the new WARNING also
surfaces one on its next Stripe event. The /account redirect of a partner
session to /signin (the Google-auth trap `account.js` avoids in the header)
is unchanged. The stale "prorated" copy listed below is unchanged.

## 2026-09-29 — Stale-doc corrections (doc + docstring only, no behaviour change)

A 2026-09-29 verifier pass flagged docs that disagreed with the code. Each
item was checked against the code before it was changed. The AST diff of the
.py files shows only module-docstring changes.

- **ADR-0001**: the status line is now "amended 2026-09-29". Inline
  `[Amended 2026-09-29]` notes and an Amendment section record four
  overtaken statements; the original decision text is untouched.
  (1) The bundle and All-Access discounts are 25% each, not 20% / 50%
  (`api/entitlements.py` SKU comment + `LAUNCH_PRICE_CENTS`: $149.99 vs
  $199.98, $299.99 vs $399.96). (2) The webhook is no longer the only
  entitlements writer: ADR-0002's `api/partner.py` also writes, for
  `ipl_*`/`ipltest_*` only. (3) It is not monthly-only: `TERMS` in
  `api/billing.py`, plus `interval_count=6` in `scripts/_bootstrap_common.py`.
  (4) There is no public ATS proof, since ATS went internal-only on
  2026-07-31 (ncaaf/cfl CLAUDE.md ATS gotchas); this covers the decision-5
  clause and the rollout "ATS proof" tab.
- **ADR-0002**: the status line is annotated ARMED on both lanes 2026-08-26
  (rev 00028), no longer "dormant".
- **CLAUDE.md**: the data-layer line now names both entitlements writers
  (it said "webhook-only writes") and the actual writers of `customers/{uid}`.
  The snapshot and the launch-gate gotcha no longer claim the deployed site
  renders "Pricing announced at launch". That string is `planCard`'s
  `show_prices: false` fallback, and the live catalog reported
  `show_prices: true` today. The partner-writer gotcha gained a caveat, below.
- **Docstrings**: the `api/billing.py` module docstring and the
  `api/entitlements.py` Firestore block name the partner writer, while
  keeping the webhook's full-overwrite recompute and the disjoint uid
  namespaces.
- **Workspace `docs/ADR_INDEX.md`**: the ADR-0001 row is marked amended, and
  the rows missing for ADR-0002 and ADR-0003 were added.

**Found while verifying, NOT fixed (code change, needs John):** the
namespace isolation is enforced on one side only. `partner.grant()` refuses
non-partner uids, but `/api/billing/checkout` accepts an `ipl_*` session, and
/pricing renders Subscribe for a signed-in partner member. If one buys, the
webhook's full-overwrite `_recompute` replaces their partner doc (dropping
`grants`/`source`/`expires_at`), and the next launch's `grant()` `set()`
drops the Stripe slugs. The docs now state the claim as an expectation, not
an invariant.

Tests: 87 passed (python3.13), unchanged from the pre-edit baseline.

**Follow-up (same day, second pass).** The verifier's residual list covered
the docs that still stated the old facts. Docs, docstrings and comments
only; the AST of each edited .py file matches HEAD once docstrings are
stripped.

- **ADR-0002 decision 2**: a `[Amended 2026-09-29]` note says "partner uids
  never enter Stripe checkout" is an expectation that no code enforces
  (checkout accepts an `ipl_*` session), and names both clobber paths. The
  decision text is untouched.
- **`api/partner.py` module docstring**: UID NAMESPACE ISOLATION now calls
  the no-checkout property an unenforced assumption and points at the open
  issue. OPAQUE-ONLY now describes the "inplayLABS Member" chip that
  `account.js` has rendered since 2026-08-26 (it said "Sign in").
- **CLAUDE.md data layer**: the webhook recomputes only for a uid with a
  checkout or subscription event (not "every Google-sign-in customer"), and
  today that uid can be an `ipl_*` one.
- **Upgrades**: the `api/billing.py` module docstring, the
  `create_checkout_session` comment, the plan-change section comment, the
  `plan_change_preview` docstring, the `/api/billing/change-preview`
  docstring in `api/app.py` and CLAUDE.md's billing line described the
  retired in-place prorated price swap. They now describe the 2026-08-26
  flow: a new Checkout, then the webhook cancels the replaced subscriptions
  with `prorate=True` after `checkout.session.completed`.
- **Workspace docs**: `PROJECT_INDEX.md` says the uid disjointness is
  expected but not enforced at checkout (open, John deciding).
  `ADR_INDEX.md` puts this repo's 0003 (2026-08-26) and 0002 (2026-08-25)
  rows in date order, and moves the ffxiv 0002 (2026-08-23) row below the
  2026-08-24 rows, where it was already out of order.

**Still stale, NOT changed (customer-facing copy or code strings, needs
John):** `static/pricing.html` terms paragraph ("Upgrades are prorated, so
you only ever pay the difference") and its "Upgrade — pay the difference"
button; the `static/account.html` upgrade-success banner ("only charged the
prorated difference"); the 409 text in `create_checkout_session` ("so
you're only charged the difference"). Under the Checkout flow the full new
price is due at checkout, and the old plan's unused time becomes credit
against later invoices. The `pricing.html` JS comments at the overlap
branch and in `buy()` also still say "prorated"/"in-place".

Tests: 87 passed (python3.13) after the follow-up.

## 2026-09-02 — SSA UX program bookmark: waves 1+3 + deploys (wave 2 logged below)

The portfolio-wide UX audit (225 verified findings; report + E1-E45 menu on the
session artifact) drove three same-day implementation waves, all deployed.
This repo's slices beyond the wave-2 entry below:

- Wave 1 (2220675): /help FAQ rewritten to the apex-path reality (visible +
  FAQPage JSON-LD, kept verbatim-matched); pricing free-tier claim corrected +
  golf SKU blurb sells the actual paid features; pricing gained loading/error/
  noscript states and /account a distinct fetch-error card (no more paying
  customer rendered as unsubscribed); aria-live on the checkout path;
  /pricing?sport= scroll+highlight; HTML Cache-Control now no-cache per the
  contract (signin/account keep no-store).
- Token canonicalization (3ca8b4d): tokens.css §1 block rewritten to the fleet
  variant — the doc + this repo were the two outliers.
- Wave 3 (8464d38): /account package rows link what they unlock (origin sport
  only wins when the SKU covers it); LEAGUE_STATUS map + drift-guard tests
  parse index.html badges and /help copy (unknown badge modifiers fail
  loudly); legacy no-sku checkout polling only trusts a package-count
  increase; SSA monogram favicon everywhere + an explicit /favicon.ico route;
  skip links, accent focus rings, sticky nav, unified availability badges.

Deployed and live-verified (favicon 200 image/svg+xml, no-cache headers,
badges/copy guards green). Open tail lives in ~/Claude Projects/ideas.md.

## 2026-09-02 — UX wave 2: post-checkout loop, free-vs-paid matrix, JSON-LD, ARIA pass

E4: checkout success URLs now carry `&sku=<sku>` and (when the buyer arrived
via a `?sport=`/league `?next=` deep link) an allow-listed `&sport=<slug>`;
`/account?checkout=success` polls `/api/me` every ~3s for up to ~30s until the
purchased SKU appears, re-renders the packages panel, and flips the banner to
"<Package> active — open <dashboard link>" (NFL links both modules; origin
sport wins over SKU derivation). The "refresh this page" instruction is gone.
E7: per-SKU `free_features`/`paid_features` added to the catalog
(api/entitlements.py, ADDITIVE — league lock cards read this payload),
populated from the live public shells' tab ladders; /pricing renders them as a
two-group checklist (free group quieter; bundle/All-Access summarise).
E12: /pricing emits Product/Offer JSON-LD at render from the live catalog —
prices are the same cents the cards display — gated on `show_prices`, so the
launch gate suppresses structured data too (replaces the head TODO).
ARIA pass on all 8 apex shells: `aria-current="page"` on the active
nav-right link, `aria-hidden` on the ▾ glyphs (incl. account.js → ?v=7
everywhere), `role="status"` on the remaining JS-swapped loading/empty
containers (account #loading + package states, signin wait states).
Tests: 82 passed (python3.13), incl. 2 new (success-URL allow-listing,
catalog feature lists). NOT deployed; committed by John's wave-2 close-out.
(A concurrent wave-2 stream owned tokens.css?v=5 + token reorder — untouched.)

Full migration DONE in one day: dual-depth plumbing already dark across 8 repos;
today the origin sweep (10 repos + audit workflow), port-80 redirect + legacy
301 host rules + legacy-hosts-cert on the front door, apex DNS flip (John),
hub+league deploys with swept origins, IPL_TOOL_MAP → apex dests, full
confirmation matrix (auth/billing/paywall/sitemaps/internal/rollback all
green), then the 7 league hosts repointed → verified permanent 301s. Incidents:
apex @ record transiently vanished after John's edit (re-added); nfl acme edit
initially hit internal.nfl's record (restored); legacy cert forced through
validation twice to skip failure backoff. ADR-0003 has the architecture;
workspace APEX_MIGRATION_TRACKER.md has the blow-by-blow + the ~Oct teardown
deadline (soccer/nfl LB cert renewals). Search Console re-registration = John.

### Post-cutover adversarial QA (413 checks, 395 passed — healthy with notes)

No auth bypass, no paid-data leak, no wrong-backend leak, no 5xx. Two findings
were this repo's:

- **`og-default.png` never existed.** Twelve shells across ten repos have
  hardcoded the absolute apex URL for `og:image` / `twitter:image` / the
  schema.org Organization logo since the SEO pass, so every social share
  preview and the Rich-Results logo had been 404ing the whole time. Now served
  at the ROOT (`GET /og-default.png`, 1-day cache) — root path, not `/static/`,
  because the absolute URL is what the other ten repos hardcode.
- **soccer.SSA's `/epl` carve-out needed a segment boundary.** The legacy 301
  host rule used a bare `prefixMatch: /epl`, which also swallows `/epla` and
  `/epl-standings` and drops them into the FLAT apex namespace. Split into
  `fullPathMatch: /epl` + `prefixMatch: /epl/` in `infra/apex-frontdoor.sh`.
  Latent today; wrong the moment a `/epl-*` soccer route exists.

### Security response headers (John approved, all 10 services)

`Strict-Transport-Security: max-age=31536000; includeSubDomains`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy:
strict-origin-when-cross-origin`, `X-Frame-Options` — the QA sweep found none
of them on any host. New `security_headers` middleware in `api/app.py`:
fill-in only (a route that set its own value keeps it), registered at module
END so it is the OUTERMOST wrapper (Starlette builds in reverse) and the www
301 and the 404 fallback carry them too.

HSTS is set **unconditionally**, never gated on `request.url.scheme` — behind
Cloud Run and the ALB the inbound scheme reads as `http`, so a scheme test
would silently ship nothing forever. It matters most on this service: it mints
`__session` on the PARENT domain `.sportsbookscienceanalytics.com`, so a
downgrade on ANY host in the family exposes the cookie — hence
`includeSubDomains`.

The first cut stamped a blanket `X-Frame-Options: DENY`, **caught in review
before deploy**. DENY refuses SAME-ORIGIN framing too, and the SPA frames the
self-proxied `/__/auth/iframe` first-party to carry popup sign-in events
(ADR-0004); firebaseapp.com sends no policy of its own, so ours would have
landed on the proxied response and broken sign-in with the exact 2026-07-31
signature — popup completes, page stays signed out. Paths under `/__/` now get
SAMEORIGIN, everything else DENY; both branches pinned by test. 71 green.
Verified live: `/` → DENY, `/__/auth/iframe` → SAMEORIGIN, `/og-default.png`
→ 200.

## 2026-08-26 (cutover sweep) — apex-path origin forms for the cutover

Origin/URL-form-only sweep for the apex-path migration (John approved cutover
2026-08-26; legacy `<league>.SSA` subdomains 301 from then on).

- All 8 static shells: league nav links + homepage directory cards now
  root-relative apex paths (`/nfl` `/ncaaf` `/cfl` `/golf` `/nba` `/nhl`
  `/soccer`). Scheme-anchored replace only — signin's auth-JS hostname
  allow-list, cookie-domain strings, and the Terms' legal prose about
  subdomains untouched.
- `static/robots.txt`: one `Sitemap:` line per league sitemap verified to
  exist in its repo (nfl, ncaaf, cfl, nba, nhl, soccer, epl, golf — all
  serve one), apex-path form; apex line kept.
- `api/app.py` `_LEAGUE_SITEMAPS`: apex-path form + a new `/epl` entry (the
  front-door carves /epl out of /soccer, so the soccer sitemap no longer
  covers it). `_PORTFOLIO_URLS` deliberately unchanged per the sweep spec.
- `docs/INPLAYLABS_ONBOARDING.md`: public-surface URLs → apex-path form;
  noted the deployed `IPL_TOOL_MAP` dests should follow at next env update
  (partner.py and its test fixture untouched — entitlement code).
- deploy.sh needed nothing: its probes already hit the apex (this repo IS
  the apex). No domain-mapping changes; subdomain mappings stay for rollback.
- 68 tests green (python3.13 -m pytest).

## 2026-08-26 (later) — partner chip shipped to every partner-visible surface

"inplayLABS Member" chip (account.js) deployed: apex, nfl.SSA root
(nfl-dashboard — the shared host's owner, initially missed because the ELO
app's static lives under /elomodel while the root is the mock-draft
service's), nfl/elomodel, ncaaf, cfl — served copies diff-verified IDENTICAL
to the repo. golf/nhl/soccer-hub copies are committed and ride their next
regular deploy (partner members never reach those surfaces). Chip replaces
the "Sign in" trap that would have Google-authed a partner member into a
fresh entitlement-less account, replacing their partner session. Prefix
consistency with api/partner.py pinned in tests (67 green).

## 2026-08-26 — inplayLABS bridge ARMED (live, both lanes)

**Agent**: claude-fable-5

Their side shipped; ours is now active. Config of record (exact strings) in
`docs/INPLAYLABS_ONBOARDING.md`.

- Preflight BEFORE arming: PyJWKClient fetched both JWKS lanes, resolved both
  kids, parsed the EC P-256 keys, and rejected a forged ES256 token carrying
  their kid — locally and then IN PRODUCTION (Cloud Run log shows
  `InvalidSignatureError` for a forged test-lane assertion; browser saw only
  the generic 403). The prod issuer carries a TRAILING SLASH
  (`https://tracker.inplaylabs.io/`) — it is part of the exact-match string.
- Service env: IPL_JWKS_URL / IPL_ISSUER / IPL_TEST_JWKS_URL /
  IPL_TEST_ISSUER / IPL_TOOL_MAP set via `--update-env-vars` (custom `^|^`
  delimiter — the tool map JSON contains commas); IPL_SWEEP_TOKEN bound from
  Secret Manager `ipl-sweep-token` (golf-data-projects). Revision 00028.
- Live flips verified: launch + launch-test 404→403-on-garbage; sweep 403
  without / 200 with token (`{"entitlements_pruned":0,"jti_pruned":0}`);
  apex health unaffected.
- Cloud Scheduler `ipl-entitlement-sweep` created (us-east1, daily 4:20 AM
  ET) and mirrored in deploy.sh as describe-else-create gated on the secret
  (the 2026-08-25 CFL lesson: schedulers live in deploy scripts).
- Registration block sent to inplayLABS: tool_ids ssa-nfl-model /
  ssa-ncaaf-model / ssa-cfl-model, both launch URLs, no test account needed
  (test-lane assertions auto-provision `ipltest_*`).

Next: their QA runs the go-live checklist over the test lane; first real
member launch lands a session + 7-day `nfl`/`ncaaf`/`cfl` entitlement with
zero further changes on our side. Commercial terms still John↔inplayLABS.

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
