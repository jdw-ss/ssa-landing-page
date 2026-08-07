// The real Account control for SSA apex pages — replaces the old inert
// "Account ▾ / soon" stub. Progressive enhancement over the same markup:
// every page keeps a `.account-stub` block whose default menu items are plain
// working links (so no-JS visitors still get to /signin and /pricing); this
// script upgrades it in place with live auth state.
//
//   signed out → Sign in (carries ?next=<current page>) · View pricing
//   signed in  → email header, package summary, My account · Pricing · Sign out
//
// Loads static/js/auth.js lazily (which itself no-ops into a dev stub when the
// backend has DISABLE_AUTH=1), then asks /api/me for package labels. All
// fetches are same-origin; the HttpOnly `__session` cookie does the auth.
(() => {
    const stub = document.querySelector(".account-stub");
    if (!stub) return;
    const btn = stub.querySelector("button");
    const menu = stub.querySelector(".account-stub-menu");
    if (!btn || !menu) return;

    // Widget-owned styles, injected once so the per-page inline CSS blocks
    // don't all need editing when the widget evolves.
    const css = document.createElement("style");
    css.textContent = [
        ".account-stub-menu .item.action { cursor: pointer; color: var(--text-dim); }",
        ".account-stub-menu .item.action:hover { background: var(--border-subtle); }",
        ".account-stub-menu a.item { text-decoration: none; display: flex; }",
        ".account-stub-menu a.item:hover { background: var(--border-subtle); color: var(--text-dim); }",
        ".acct-btn-avatar { width: 20px; height: 20px; border-radius: 50%; " +
            "background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: var(--bg); " +
            "font-size: 11px; font-weight: 700; display: inline-flex; align-items: center; " +
            "justify-content: center; overflow: hidden; }",
        ".acct-btn-avatar img { width: 100%; height: 100%; object-fit: cover; }",
        ".account-stub-menu .item.sub { font-size: 12px; color: var(--text-muted); }",
    ].join("\n");
    document.head.appendChild(css);

    function wireDropdown() {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const open = menu.classList.toggle("open");
            btn.setAttribute("aria-expanded", String(open));
        });
        document.addEventListener("click", (e) => {
            if (!menu.contains(e.target) && e.target !== btn) {
                menu.classList.remove("open");
                btn.setAttribute("aria-expanded", "false");
            }
        });
    }

    function loadAuth() {
        if (window.Auth) return Promise.resolve();
        return new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = "/static/js/auth.js?v=1";
            s.onload = resolve;
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    const here = encodeURIComponent(location.pathname + location.search);

    function renderSignedOut() {
        menu.innerHTML =
            '<div class="item header">Your SSA account</div>' +
            '<a class="item" href="/signin?next=' + here + '">Sign in</a>' +
            '<a class="item" href="/pricing">View pricing</a>';
    }

    function renderSignedIn(email, packagesLine, avatarHtml) {
        btn.innerHTML =
            '<span class="acct-btn-avatar">' + avatarHtml + "</span>" +
            "<span>" + email.split("@")[0] + "</span>" +
            '<span style="font-size: 10px;">▾</span>';
        menu.innerHTML =
            '<div class="item header">' + email + "</div>" +
            '<div class="item sub">' + packagesLine + "</div>" +
            '<a class="item" href="/account">My account</a>' +
            '<a class="item" href="/pricing">Pricing</a>' +
            '<div class="item action" id="acct-signout">Sign out</div>';
        document.getElementById("acct-signout").addEventListener("click", async () => {
            await window.Auth.signOut();
            window.location.reload();
        });
    }

    async function hydrate() {
        try {
            await loadAuth();
            await window.Auth.ready();
        } catch (e) {
            console.warn("Account widget: auth failed to load", e);
            renderSignedOut();
            return;
        }

        const user = window.Auth.user();
        if (!user || !user.email) {
            renderSignedOut();
            return;
        }

        let packagesLine = "No packages yet";
        try {
            const resp = await fetch("/api/me", { credentials: "same-origin" });
            if (resp.ok) {
                const me = await resp.json();
                if (me.packages && me.packages.length) {
                    packagesLine = me.packages.map((p) => p.label).join(" · ");
                }
            }
        } catch (e) { /* menu still works without package detail */ }

        const initial = (user.email[0] || "?").toUpperCase();
        const avatarHtml = user.photoURL
            ? '<img src="' + user.photoURL + '" alt="" referrerpolicy="no-referrer">'
            : initial;
        renderSignedIn(user.email, packagesLine, avatarHtml);
    }

    wireDropdown();
    hydrate();
})();
