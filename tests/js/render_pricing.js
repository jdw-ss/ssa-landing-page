// Render /pricing's plan cards in node, with a minimal fake DOM, for
// tests/test_partner_billing_guard.py.
//
// Usage: node render_pricing.js <path/to/pricing.html>  (catalog JSON on stdin)
//
// Runs the page's own inline <script> unmodified in a vm context: fetch()
// answers /api/billing/catalog with the stdin payload, getElementById()
// returns an element only when the markup declares that id (so a missing
// #partner-note reads as null, like a browser), and each element's `hidden`
// starts from the markup's attribute. Prints JSON: the partner note's state
// and every rendered card's HTML.
"use strict";

const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync(process.argv[2], "utf8");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));

// The page script is the last inline <script> (no src attribute).
const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
const pageScript = inline[inline.length - 1];

class ClassList {
    constructor(el) { this.el = el; }
    _set() { return new Set(this.el.className.split(/\s+/).filter(Boolean)); }
    add(c) { const s = this._set(); s.add(c); this.el.className = [...s].join(" "); }
    remove(c) { const s = this._set(); s.delete(c); this.el.className = [...s].join(" "); }
    contains(c) { return this._set().has(c); }
    toggle(c, force) {
        const on = force === undefined ? !this.contains(c) : force;
        if (on) this.add(c); else this.remove(c);
        return on;
    }
}

class El {
    constructor(tag, id, hidden) {
        this.tagName = tag;
        this.id = id || "";
        this.className = "";
        this.dataset = {};
        this.children = [];
        this.hidden = !!hidden;
        this.textContent = "";
        this.style = {};
        this._html = "";
        this.classList = new ClassList(this);
    }
    set innerHTML(v) { this._html = v; this.children = []; }
    get innerHTML() { return this._html + this.children.map((c) => c.outerHTML).join(""); }
    get outerHTML() {
        return "<" + this.tagName + ' class="' + this.className + '">' + this.innerHTML +
            "</" + this.tagName + ">";
    }
    appendChild(c) { this.children.push(c); return c; }
    addEventListener() {}
    setAttribute() {}
    remove() {}
    replaceWith() {}
    scrollIntoView() {}
    focus() {}
    querySelector() { return null; }
    querySelectorAll() { return []; }
}

// Elements the markup declares, with their initial `hidden` attribute.
const declared = new Map();
for (const m of html.matchAll(/<(\w+)([^>]*?)\sid="([^"]+)"([^>]*)>/g)) {
    const attrs = m[2] + " " + m[4];
    declared.set(m[3], new El(m[1], m[3], /\shidden(\s|=|$)/.test(attrs)));
}

const document = {
    getElementById: (id) => declared.get(id) || null,
    createElement: (tag) => new El(tag),
    querySelector: () => null,
    querySelectorAll: () => [],
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
vm.runInContext(pageScript, ctx);

setTimeout(() => {
    const note = declared.get("partner-note");
    const cards = ["sport-grid", "big-grid"].flatMap((id) =>
        (declared.get(id) ? declared.get(id).children : []).map((c) => c.innerHTML));
    process.stdout.write(JSON.stringify({
        partner_note: note ? { hidden: note.hidden } : null,
        cards,
    }));
}, 50);
