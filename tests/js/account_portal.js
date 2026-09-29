// Click /account's "Manage billing" button in node and report the banner, for
// tests/test_partner_billing_guard.py.
//
// Usage: node account_portal.js <path/to/account.html>
//        (stdin: {"status": <int>, "body": <json or null>} — the portal reply)
//
// Runs the page's own inline <script> unmodified in a vm context (fake DOM in
// fakedom.js). Auth never loads (the injected auth.js <script> never fires
// onload), so init() parks and the page's listeners are all that run. The
// harness then fires #portal-btn's own click handler against a fetch that
// answers POST /api/billing/portal with the stdin reply, and prints the
// banner's text and class plus any navigation.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { El, pageScript, declaredElements } = require(path.join(__dirname, "fakedom.js"));

const html = fs.readFileSync(process.argv[2], "utf8");
const reply = JSON.parse(fs.readFileSync(0, "utf8"));
const declared = declaredElements(html);

const location = {
    search: "", href: "", hostname: "127.0.0.1",
    replace(u) { this.replaced = u; },
};
const document = {
    getElementById: (id) => declared.get(id) || null,
    createElement: (tag) => new El(tag),
    querySelector: () => null,
    querySelectorAll: () => [],
    head: { appendChild() {} },
};

const ctx = {
    document,
    location,
    URLSearchParams,
    fetch: async (url) => {
        if (url === "/api/billing/portal") {
            return {
                ok: reply.status >= 200 && reply.status < 300,
                status: reply.status,
                json: async () => {
                    if (reply.body === null) throw new SyntaxError("not JSON");
                    return reply.body;
                },
            };
        }
        return new Promise(() => {});  // anything else: never answers
    },
    setTimeout,
    Date,
    Promise,
    console,
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(pageScript(html), ctx);

setTimeout(async () => {
    const btn = declared.get("portal-btn");
    for (const fn of btn.listeners.click || []) await fn();
    const banner = declared.get("banner");
    process.stdout.write(JSON.stringify({
        banner: banner.textContent,
        banner_class: banner.className,
        navigated: location.href,
        button_disabled: btn.disabled,
    }));
}, 20);
