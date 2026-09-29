// Render /pricing's plan cards in node, with a minimal fake DOM, for
// tests/test_partner_billing_guard.py and tests/test_upgrade_copy.py.
//
// Usage: node render_pricing.js <path/to/pricing.html> [monthly|6mo] [click-sku]
//        (catalog JSON on stdin; with click-sku, its top-level "_preview" is
//        the /api/billing/change-preview reply and is not served as catalog)
//
// Runs the page's own inline <script> unmodified in a vm context (fake DOM in
// fakedom.js): fetch() answers /api/billing/catalog with the stdin payload.
// With "6mo", the term toggle's own click handler is fired after the first
// render, as a visitor would. With a click-sku, that card's purchase button
// is then clicked through the page's own handler (buy()), and an upgrade
// preview renders the page's own confirm box. Prints JSON: the partner
// note's state, every declared element's `hidden`, every rendered card's
// HTML, and the confirm box's HTML (null when none rendered).
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { El, pageScript, declaredElements, hiddenMap } = require(path.join(__dirname, "fakedom.js"));

const html = fs.readFileSync(process.argv[2], "utf8");
const term = process.argv[3] || "monthly";
const clickSku = process.argv[4] || "";
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const preview = payload._preview || null;
delete payload._preview;

const declared = declaredElements(html);

// The term toggle's buttons, so the page's own listener can be fired.
const termButtons = ["monthly", "6mo"].map((t) => {
    const b = new El("button");
    b.dataset.term = t;
    return b;
});

// The cards' purchase buttons exist only as innerHTML strings, and
// renderGrids() binds them with querySelectorAll("button[data-sku]"). Hand
// it one stub per rendered data-sku button, so a click can be fired. The
// confirm box replaces its button (replaceWith) and then looks up its own
// two buttons, so the stub records the box and gives it stub buttons.
let skuButtons = [];
let upgradeBox = null;
function renderedSkuButtons() {
    const cards = ["sport-grid", "big-grid"].flatMap((id) =>
        (declared.get(id) ? declared.get(id).children : []));
    skuButtons = cards.flatMap((card) =>
        [...card.innerHTML.matchAll(/<button[^>]*\sdata-sku="([^"]+)"[^>]*>([^<]*)<\/button>/g)])
        .map((m) => {
            const b = new El("button");
            b.dataset.sku = m[1];
            b.textContent = m[2];
            b.replaceWith = (box) => {
                upgradeBox = box;
                box.querySelector = () => new El("button");
            };
            return b;
        });
    return skuButtons;
}

const document = {
    getElementById: (id) => declared.get(id) || null,
    createElement: (tag) => new El(tag),
    querySelector: () => null,
    querySelectorAll: (sel) => (sel === "#term-toggle button" ? termButtons
        : sel === "button[data-sku]" ? renderedSkuButtons() : []),
    head: { appendChild() {} },
};

const ctx = {
    document,
    location: { search: "", href: "" },
    URLSearchParams,
    fetch: async (url) => (String(url).startsWith("/api/billing/change-preview")
        ? { ok: true, status: 200, json: async () => preview }
        : { ok: true, status: 200, json: async () => payload }),
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
        upgrade_box: upgradeBox ? upgradeBox.innerHTML : null,
    }));
}

setTimeout(() => {
    if (term !== "monthly") {
        const btn = termButtons.find((b) => b.dataset.term === term);
        (btn.listeners.click || []).forEach((fn) => fn());
    }
    setTimeout(() => {
        if (!clickSku) return report();
        const btn = skuButtons.find((b) => b.dataset.sku === clickSku);
        if (!btn) {
            process.stderr.write("no rendered button for " + clickSku + "\n");
            process.exit(2);
        }
        (btn.listeners.click || []).forEach((fn) => fn());
        setTimeout(report, 20);
    }, 20);
}, 50);
