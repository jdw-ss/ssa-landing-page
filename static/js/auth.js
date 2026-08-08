// Firebase Auth bootstrap for the SSA apex hub — the customer sign-in origin.
// Copied from the shared league implementation (soccer-hub/static/js/auth.js)
// so the façade and SSO behavior are identical everywhere; only this header
// differs. /signin drives Auth.signIn(); every other apex page just calls
// Auth.ready() to recover state.
//
// Cross-subdomain SSO: on bootstrap we try `GET /api/session/exchange`.
// If the backend finds a valid `__session` cookie scoped to
// `.sportsbookscienceanalytics.com` (set the first time the user signed
// in anywhere on SSA), it mints a custom token we immediately consume via
// signInWithCustomToken — no popup. After every successful popup we POST the
// fresh id token to `/api/session` so the cookie follows the user to every
// league site. signOut() clears it via DELETE /api/session.
//
// Exposes a small façade so pages don't re-load the Firebase SDK:
//   await Auth.ready()    — resolves once the auth state is known
//   Auth.user()           — current Firebase user object, or null
//   Auth.idToken()        — current bearer token, or null
//   Auth.signIn()         — Google popup (redirect on mobile) + parent cookie
//   Auth.signOut()        — sign out + cookie clear
//   Auth.disabled         — true when DISABLE_AUTH=1 (local dev stub user)
//
// Cross-site auth STATE cookie (`ssa_auth`, parent-domain, JS-readable,
// never carries identity): "1" = the user's last explicit action anywhere on
// SSA was sign-IN (or a session recovery succeeded); "0" = explicit sign-OUT.
// Every surface's bootstrap consults it FIRST: a "0" with a locally persisted
// Firebase user means sign-out happened on ANOTHER origin and this origin's
// per-origin persistence survived it — purge it and render signed out instead
// of resurrecting the session. The apex self-heal (re-minting the parent
// `__session` cookie from surviving persistence) is gated on the value not
// being "0" — un-gated, it was signing the whole family back in after every
// sign-out (John repro'd 2026-08-08).
//
// Idempotent under double-load: pages with their own auth bootstrap (account,
// signin) AND the account widget can both inject this script before either
// copy executes; re-executing must keep the first façade, or callers awaiting
// ready() on one instance read user() off a fresh unbootstrapped one.
window.Auth = window.Auth || (() => {
    const R = () => window.__ROOT__ || "";
    let _fbCfg = null;
    let _fbAuth = null;
    let _fbProvider = null;
    let _signInWithPopup = null;
    let _signInWithRedirect = null;
    let _getRedirectResult = null;
    let _signInWithCustomToken = null;
    let _signOut = null;

    // Mobile browsers handle the OAuth popup poorly (popup opens as a new tab,
    // the postMessage back to the opener is flaky, and Google intermittently
    // returns a 400 mid-handshake), so we use a full-page redirect there. These
    // popup-failure codes also trigger a redirect fallback on desktop.
    const _isMobile = () => /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent || "");
    const _REDIRECT_FALLBACK = new Set([
        "auth/popup-blocked", "auth/popup-closed-by-user",
        "auth/cancelled-popup-request", "auth/web-storage-unsupported",
        "auth/operation-not-supported-in-this-environment",
    ]);
    let _user = null;
    let _idToken = null;
    let _ready = false;
    let _readyPromise = null;
    let _disabled = false;

    // ssa_auth state cookie — see header. Domain-scoped writes silently no-op
    // on localhost (Domain mismatch) and plain http (Secure), so local dev
    // always sees "absent" and takes the legacy paths.
    const _readState = () => {
        const m = document.cookie.match(/(?:^|;\s*)ssa_auth=([^;]*)/);
        return m ? m[1] : null;
    };
    const _setState = (v) => {
        document.cookie = "ssa_auth=" + v +
            "; Domain=.sportsbookscienceanalytics.com; Path=/" +
            "; Max-Age=2592000; Secure; SameSite=Lax";
    };

    async function _bootstrap() {
        _fbCfg = await fetch(R() + "/api/firebase-config").then(r => r.json());
        if (_fbCfg.disableAuth) {
            _disabled = true;
            _user = { email: "dev@local (auth disabled)" };
            _idToken = "stub-disabled";
            _ready = true;
            return;
        }
        const appMod  = await import("https://www.gstatic.com/firebasejs/10.13.2/firebase-app.js");
        const authMod = await import("https://www.gstatic.com/firebasejs/10.13.2/firebase-auth.js");
        const app = appMod.initializeApp({
            apiKey: _fbCfg.apiKey, authDomain: _fbCfg.authDomain,
            projectId: _fbCfg.projectId, appId: _fbCfg.appId,
        });
        _fbAuth = authMod.getAuth(app);
        _fbProvider = new authMod.GoogleAuthProvider();
        _signInWithPopup = authMod.signInWithPopup;
        _signInWithRedirect = authMod.signInWithRedirect;
        _getRedirectResult = authMod.getRedirectResult;
        _signInWithCustomToken = authMod.signInWithCustomToken;
        _signOut = authMod.signOut;

        // Consult the state cookie BEFORE any recovery attempt: "0" (explicit
        // sign-out somewhere on SSA) vetoes the exchange AND the self-heal —
        // both are session-resurrection paths.
        let _signedOutState = _readState() === "0";

        // Try to recover a parent-domain session before deciding "signed
        // out". If the cookie is valid, signInWithCustomToken below
        // triggers onAuthStateChanged with the recovered user; if not,
        // we fall through and the listener fires with null.
        // _cookieOk tracks whether this page load proved the parent-domain
        // cookie is alive (exchange succeeded, or we just re-minted it).
        let _cookieOk = false;
        if (!_signedOutState) {
            try {
                const resp = await fetch(R() + "/api/session/exchange", { credentials: "same-origin" });
                if (resp.ok) {
                    const { customToken } = await resp.json();
                    if (customToken) {
                        await _signInWithCustomToken(_fbAuth, customToken);
                        _cookieOk = true;
                        _setState("1");
                    }
                }
            } catch (e) {
                console.warn("Session recovery failed:", e);
            }
        }

        // Complete a pending mobile redirect sign-in (signInWithRedirect). When the
        // user returns from Google, getRedirectResult resolves with the user and we
        // persist the parent-domain cookie — the step the popup path does in
        // signIn(). The onIdTokenChanged listener below then sees the signed-in user.
        // This runs even under a "0" state: returning from the Google redirect IS
        // an explicit sign-in, which overrides a stale sign-out marker.
        try {
            const redirectResult = await _getRedirectResult(_fbAuth);
            if (redirectResult && redirectResult.user) {
                await _persistSession(await redirectResult.user.getIdToken());
                _cookieOk = true;
                _signedOutState = false;
                _setState("1");
            }
        } catch (e) {
            console.warn("Redirect sign-in completion failed:", e);
        }

        // `onIdTokenChanged` fires on sign-in, sign-out, AND every time the
        // Firebase SDK auto-refreshes the id token (~55min cadence — under
        // the 1h server-side `verify_id_token` expiry). Subscribing here
        // keeps `_idToken` continuously fresh so long-lived tabs don't 401
        // on the next API call.
        let _resolvedReady = false;
        await new Promise((resolve) => {
            authMod.onIdTokenChanged(_fbAuth, async (user) => {
                if (user) {
                    _user = user;
                    _idToken = await user.getIdToken();
                } else {
                    _user = null;
                    _idToken = null;
                }
                if (!_resolvedReady) {
                    _resolvedReady = true;
                    // Explicit sign-out marker + a locally persisted user:
                    // sign-out happened on another SSA origin and this
                    // origin's per-origin Firebase persistence survived it.
                    // Purge it and render signed out — and specifically do
                    // NOT fall through to the self-heal below, which would
                    // re-mint the parent cookie from the surviving persistence
                    // and sign the whole family back in.
                    if (user && _signedOutState) {
                        try { await _signOut(_fbAuth); } catch (e) {
                            console.warn("Stale-session purge failed:", e);
                        }
                        _user = null;
                        _idToken = null;
                        _ready = true;
                        resolve();
                        return;
                    }
                    // Legacy/migration: signed in from before the state cookie
                    // existed — record the intent so other surfaces see it.
                    if (user && _readState() === null) _setState("1");
                    // Self-heal the parent-domain cookie: Firebase local
                    // persistence can hold a signed-in user here (apex renders
                    // signed in) while the `__session` cookie is missing,
                    // expired, or scoped to a legacy host — and nothing else
                    // re-mints it, so every league site stays signed out until
                    // a manual re-sign-in. If we have a user but the exchange
                    // above did NOT prove the cookie (401/absent), re-mint it
                    // now. Runs at most once per page load; _persistSession is
                    // best-effort and never re-triggers this path. Gated on
                    // the state cookie not being "0" (the purge branch above
                    // returns first).
                    if (user && !_cookieOk) {
                        _cookieOk = true;
                        await _persistSession(_idToken);
                    }
                    _ready = true;
                    resolve();
                }
            });
        });
    }

    function ready() {
        if (!_readyPromise) _readyPromise = _bootstrap();
        return _readyPromise;
    }

    async function _persistSession(idToken) {
        try {
            await fetch(R() + "/api/session", {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ idToken }),
            });
        } catch (e) {
            console.warn("Cross-subdomain session cookie not set:", e);
        }
    }

    async function signIn() {
        if (_disabled) return;
        await ready();
        // Mobile: skip the flaky popup and go straight to a full-page redirect.
        // The flow completes in _bootstrap() via getRedirectResult after Google
        // navigates back, so this call never resolves (the page unloads).
        if (_isMobile()) {
            await _signInWithRedirect(_fbAuth, _fbProvider);
            return;
        }
        // Desktop: popup is snappier, but fall back to redirect if it's blocked
        // or unsupported (e.g. a desktop popup blocker).
        try {
            const result = await _signInWithPopup(_fbAuth, _fbProvider);
            _user = result.user;
            _idToken = await _user.getIdToken();
            _setState("1");
            await _persistSession(_idToken);
        } catch (e) {
            if (e && _REDIRECT_FALLBACK.has(e.code)) {
                await _signInWithRedirect(_fbAuth, _fbProvider);
                return;
            }
            throw e;
        }
    }

    async function signOutFn() {
        if (_disabled || !_fbAuth) return;
        // Record the family-wide intent FIRST — if anything below fails, the
        // other surfaces must still see "0" and purge their surviving
        // per-origin persistence instead of resurrecting the session.
        _setState("0");
        await _signOut(_fbAuth);
        _user = null;
        _idToken = null;
        try {
            await fetch(R() + "/api/session", {
                method: "DELETE",
                credentials: "same-origin",
            });
        } catch (_) { /* best-effort */ }
    }

    return {
        ready,
        user:    () => _user,
        idToken: () => _idToken,
        signIn,
        signOut: signOutFn,
        get disabled() { return _disabled; },
    };
})();
