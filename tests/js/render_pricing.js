// Render /pricing's plan cards in node, with a minimal fake DOM, for
// tests/test_partner_billing_guard.py.
//
// Usage: node render_pricing.js <path/to/pricing.html> [monthly|6mo]
//        (catalog JSON on stdin)
//
// Runs the page's own inline <script> unmodified in a vm context (fake DOM in
// fakedom.js): fetch() answers /api/billing/catalog with the stdin payload.
// With "6mo", the term toggle's own click handler is fired after the first
// render, as a visitor would. Prints JSON: the partner note's state, every
// declared element's `hidden`, and every rendered card's HTML.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { El, pageScript, declaredElements, hiddenMap } = require(path.join(__dirname, "fakedom.js"));

const html = fs.readFileSync(process.argv[2], "utf8");
const term = process.argv[3] || "monthly";
const payload = JSON.parse(fs.readFileSync(0, "utf8"));

const declared = declaredElements(html);

// The term toggle's buttons, so the page's own listener can be fired.
const termButtons = ["monthly", "6mo"].map((t) => {
    const b = new El("button");
    b.dataset.term = t;
    return b;
});

const document = {
    getElementById: (id) => declared.get(id) || null,
    createElement: (tag) => new El(tag),
    querySelector: () => null,
    querySelectorAll: (sel) => (sel === "#term-toggle button" ? termButtons : []),
    head: { appendChild() {} },
};

const ctx = {
    document,
    location: { search: "", href: "" },
    URLSearchParams,
    fetch: async () => ({ ok: true, status: 200, json: async () => payload }),
    setTimeout,
    console,
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(pageScript(html), ctx);

function report() {
    const note = declared.get("partner-note");
    const cards = ["sport-grid", "big-grid"].flatMap((id) =>
        (declared.get(id) ? declared.get(id).children : []).map((c) => c.innerHTML));
    process.stdout.write(JSON.stringify({
        partner_note: note ? { hidden: note.hidden } : null,
        hidden: hiddenMap(declared),
        cards,
    }));
}

setTimeout(() => {
    if (term !== "monthly") {
        const btn = termButtons.find((b) => b.dataset.term === term);
        (btn.listeners.click || []).forEach((fn) => fn());
    }
    setTimeout(report, 20);
}, 50);
