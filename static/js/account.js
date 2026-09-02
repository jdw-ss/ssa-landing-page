// The shared SSA account control. ONE implementation, vendored verbatim into
// every surface (apex + each league) the way auth.js and entitlements.py already
// are — this replaced four divergent copies plus soccer-hub, which had none at
// all and so could never leave its signed-out state.
//
// Progressive enhancement over markup every page already ships, so a no-JS
// visitor still gets a working sign-in link:
//
//   signed out → the control IS a plain "Sign in" link. No dropdown, no inert
//                header. (Baymard: the control's LABEL should carry the state;
//                a menu that only explains itself isn't worth a click.)
//   signed in  → avatar + name ▾ opening: identity · what you own ·
//                My Account · Browse Packages/Upgrade · Help · ─── · Sign out
//
// Host page configures it via data-* on the .account-stub element:
//   data-apex        absolute origin of the apex hub (required off-apex)
//   data-sport       this surface's entitlement slug, e.g. "cfl" (leagues only)
//   data-sport-label human label for the upsell, e.g. "CFL"
//
// Entitlement detail is APEX-ONLY by design: /api/me is same-origin there, and
// the portfolio deliberately runs no CORS, so leagues must not try to read it
// cross-origin. Off-apex the menu links to My Account for the full picture.
(() => {
    const stub = document.querySelector(".account-stub");
    if (!stub) return;
    const btn = stub.querySelector("button");
    const menu = stub.querySelector(".account-stub-menu");
    if (!btn || !menu) return;

    const APEX = (stub.dataset.apex || "").replace(/\/$/, "");
    const SPORT = stub.dataset.sport || "";
    const SPORT_LABEL = stub.dataset.sportLabel || "";
    const ON_APEX = APEX === "" || APEX === location.origin;
    const A = (p) => (ON_APEX ? p : APEX + p);
    const TOP = ON_APEX ? "" : ' target="_top"';
    const here = encodeURIComponent(location.href);

    // Widget-owned styles, injected once so per-page CSS never needs editing.
    // Tap targets are >=44px per mobile guidance; the old 13px/8px rows were ~30.
    const css = document.createElement("style");
    css.textContent = [
        ".account-stub-menu .item { min-height: 44px; display: flex; align-items: center; }",
        ".account-stub-menu a.item { text-decoration: none; }",
        ".account-stub-menu a.item:hover, .account-stub-menu button.item:hover {",
        "  background: var(--border-subtle); color: var(--text); }",
        ".account-stub-menu a.item:focus-visible, .account-stub-menu button.item:focus-visible {",
        "  outline: 2px solid var(--accent); outline-offset: -2px; }",
        // Sign out is a real <button>: the old <div> was unreachable by keyboard
        // and announced as nothing.
        ".account-stub-menu button.item { width: 100%; background: none; border: 0;",
        "  font: inherit; color: var(--text-dim); cursor: pointer; text-align: left;",
        "  padding: 8px 16px; }",
        // Identity block must NOT look like a menu item — a non-interactive row
        // among links is what invites mis-clicks.
        ".account-stub-menu .acct-id { padding: 10px 16px 8px; cursor: default; }",
        ".account-stub-menu .acct-id .acct-email { font-size: 13px; font-weight: 600;",
        "  color: var(--text); overflow: hidden; text-overflow: ellipsis; }",
        ".account-stub-menu .acct-id .acct-owns { font-size: 12px; color: var(--text-muted);",
        "  margin-top: 2px; }",
        ".account-stub-menu .acct-sep { height: 1px; background: var(--border-subtle);",
        "  margin: 6px 0; }",
        ".acct-btn-avatar { width: 20px; height: 20px; border-radius: 50%;",
        "  background: linear-gradient(135deg, var(--accent), var(--accent-2));",
        "  color: var(--bg); font-size: 11px; font-weight: 700; display: inline-flex;",
        "  align-items: center; justify-content: center; overflow: hidden; flex: 0 0 auto; }",
        ".acct-btn-avatar img { width: 100%; height: 100%; object-fit: cover; }",
        ".acct-upsell { color: var(--accent) !important; font-weight: 600; }",
        // Partner chip (inplayLABS members): identity comes from the launch
        // bridge, not a Google account. Chrome resolves through tokens per
        // DESIGN_SYSTEM — no hex literals here.
        ".acct-partner-chip { display: inline-flex; align-items: center; gap: 6px;",
        "  padding: 6px 12px; border: 1px solid var(--accent); border-radius: 999px;",
        "  color: var(--accent); font-size: 12px; font-weight: 600;",
        "  cursor: default; user-select: none; white-space: nowrap; }",
    ].join("\n");
    document.head.appendChild(css);

    const esc = (s) => String(s).replace(/[&<>"']/g, (c) => (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));

    function closeMenu() {
        menu.classList.remove("open");
        btn.setAttribute("aria-expanded", "false");
    }

    function wireDropdown() {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const open = menu.classList.toggle("open");
            btn.setAttribute("aria-expanded", String(open));
        });
        document.addEventListener("click", (e) => {
            if (!menu.contains(e.target) && e.target !== btn) closeMenu();
        });
        // Escape closes and returns focus to the trigger — the menu was
        // previously mouse-only.
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && menu.classList.contains("open")) {
                closeMenu();
                btn.focus();
            }
        });
    }

    // inplayLABS partner members (ssa-landing-page ADR-0002): synthetic
    // ipl_*/ipltest_* uids with NO email — the generic branch below would
    // render "Sign in", which is worse than cosmetic: a partner member who
    // clicks it Google-auths into a fresh, entitlement-less SSA account and
    // REPLACES their partner session, losing access until they relaunch.
    // The chip removes that affordance entirely. Inert on purpose: their
    // account, billing and support all live on inplayLABS, not here. The
    // prefixes must match api/partner.py's UID_PREFIX/TEST_UID_PREFIX
    // (pinned by the apex repo's tests).
    function renderPartnerMember(uid) {
        const chip = document.createElement("span");
        chip.className = "acct-partner-chip";
        chip.textContent = uid.indexOf("ipltest_") === 0
            ? "inplayLABS Member · test"
            : "inplayLABS Member";
        chip.title = "Access provided through the inplayLABS partnership";
        btn.replaceWith(chip);
        menu.remove();
    }

    // Signed out: no dropdown at all. Replace the control with a direct link so
    // there is nothing redundant to click through.
    function renderSignedOut() {
        const link = document.createElement("a");
        link.className = btn.className;
        link.href = A("/signin") + "?next=" + here;
        if (!ON_APEX) link.target = "_top";
        link.textContent = "Sign in";
        link.style.textDecoration = "none";
        btn.replaceWith(link);
        menu.remove();
    }

    function renderSignedIn(email, owns, buyLabel, buyHref, upsell) {
        const initial = (email[0] || "?").toUpperCase();
        const avatar = window.__ssaPhoto
            ? '<img src="' + esc(window.__ssaPhoto) + '" alt="" referrerpolicy="no-referrer">'
            : esc(initial);
        btn.innerHTML =
            '<span class="acct-btn-avatar">' + avatar + "</span>" +
            "<span>" + esc(email.split("@")[0]) + "</span>" +
            '<span style="font-size:10px;" aria-hidden="true">▾</span>';

        let html =
            '<div class="acct-id">' +
            '<div class="acct-email">' + esc(email) + "</div>" +
            (owns ? '<div class="acct-owns">' + esc(owns) + "</div>" : "") +
            "</div>" +
            '<div class="acct-sep"></div>' +
            '<a class="item" href="' + A("/account") + '"' + TOP + ">My Account</a>";

        if (upsell) {
            html += '<a class="item acct-upsell" href="' + esc(upsell.href) + '"' + TOP +
                ">Unlock " + esc(upsell.label) + "</a>";
        }
        if (buyLabel) {
            html += '<a class="item" href="' + buyHref + '"' + TOP + ">" + esc(buyLabel) + "</a>";
        }
        html +=
            '<a class="item" href="' + A("/help") + '"' + TOP + ">Help</a>" +
            '<div class="acct-sep"></div>' +
            '<button type="button" class="item" id="acct-signout">Sign out</button>';

        menu.innerHTML = html;
        document.getElementById("acct-signout").addEventListener("click", async () => {
            try { await window.Auth.signOut(); } catch (e) { /* fall through to reload */ }
            window.location.reload();
        });
    }

    function loadAuth() {
        if (window.Auth) return Promise.resolve();
        return new Promise((resolve, reject) => {
            const s = document.createElement("script");
            // ?v=lazy is a STABLE cache-buster, distinct from the per-page
            // static tags: this file is vendored byte-identically to all 9
            // surfaces, so it cannot carry any repo's own ?v number. The
            // 300s static TTL bounds how stale the lazy-loaded copy can be.
            // Prefix-aware: this loads THIS surface's OWN auth.js, not the
            // hub's — so it must follow the page's root, exactly like every
            // other same-origin asset. The old form was a dead ternary
            // (both branches "") leaving a root-absolute path, which under an
            // apex prefix resolves to the front door's DEFAULT backend and
            // silently loads ssa-landing's copy instead (2026-08-27). Note
            // A()/APEX above are for HUB links and are correct as they are.
            s.src = (window.__ROOT__ || "") + "/static/js/auth.js?v=lazy";
            s.onload = resolve;
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    async function hydrate() {
        try {
            await loadAuth();
            await window.Auth.ready();
        } catch (e) {
            console.warn("Account widget: auth unavailable", e);
            renderSignedOut();
            return;
        }

        const user = window.Auth.user();
        if (user && !user.email && /^ipl(test)?_/.test(user.uid || "")) {
            renderPartnerMember(user.uid);
            return;
        }
        if (!user || !user.email) { renderSignedOut(); return; }
        window.__ssaPhoto = user.photoURL || "";

        // Entitlement detail is apex-only (same-origin /api/me; no CORS by
        // design). Off-apex we still render the menu — just without the
        // owned-packages line, which My Account carries.
        let owns = "";
        let slugs = [];
        if (ON_APEX) {
            try {
                const r = await fetch("/api/me", { credentials: "same-origin" });
                if (r.ok) {
                    const me = await r.json();
                    slugs = me.slugs || [];
                    owns = (me.packages || []).length
                        ? me.packages.map((p) => p.label).join(" · ")
                        : "No packages yet";
                }
            } catch (e) { /* menu still works without it */ }
        }

        // Contextual buy label: nothing left to sell to an All-Access customer.
        const hasAll = slugs.indexOf("all") !== -1;
        let buyLabel = "Browse Packages";
        if (hasAll) buyLabel = "";
        else if (slugs.length) buyLabel = "Upgrade";

        // Upsell only where it is actionable AND we are SURE it applies.
        //
        // On the apex we know the slug set, so we can decide properly. Off-apex
        // we have no entitlement signal unless the host page sets
        // window.__ssaEntitled, and absence of information must FAIL CLOSED:
        // showing "Unlock CFL" to somebody who already subscribes to CFL is
        // worse than showing nothing at all. Only an explicit `false` — a page
        // that actually determined the customer is locked out — opens it.
        let upsell = null;
        if (SPORT && SPORT_LABEL) {
            const showUpsell = ON_APEX
                ? !(hasAll || slugs.indexOf(SPORT) !== -1)
                : window.__ssaEntitled === false;
            if (showUpsell) {
                upsell = { label: SPORT_LABEL, href: A("/pricing") + "?sport=" + encodeURIComponent(SPORT) };
            }
        }

        renderSignedIn(user.email, owns, buyLabel, A("/pricing"), upsell);
    }

    wireDropdown();
    hydrate();
})();
