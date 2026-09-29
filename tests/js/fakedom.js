// A minimal fake DOM for running a page's own inline <script> in a node vm
// (tests/js/render_pricing.js, tests/js/account_portal.js).
//
// getElementById() returns an element only when the markup declares that id
// (so a missing element reads as null, like a browser), each element's
// `hidden` starts from the markup's attribute, and addEventListener() keeps
// the handlers so a harness can fire them.
"use strict";

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
        this.disabled = false;
        this.textContent = "";
        this.style = {};
        this._html = "";
        this.listeners = {};
        this.classList = new ClassList(this);
    }
    set innerHTML(v) { this._html = v; this.children = []; }
    get innerHTML() { return this._html + this.children.map((c) => c.outerHTML).join(""); }
    get outerHTML() {
        return "<" + this.tagName + ' class="' + this.className + '">' + this.innerHTML +
            "</" + this.tagName + ">";
    }
    appendChild(c) { this.children.push(c); return c; }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    setAttribute() {}
    remove() {}
    replaceWith() {}
    scrollIntoView() {}
    focus() {}
    querySelector() { return null; }
    querySelectorAll() { return []; }
}

// The last inline <script> (no src attribute): the page's own script.
function pageScript(html) {
    const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
    return inline[inline.length - 1];
}

// Elements the markup declares, with their initial `hidden` attribute.
function declaredElements(html) {
    const declared = new Map();
    for (const m of html.matchAll(/<(\w+)([^>]*?)\sid="([^"]+)"([^>]*)>/g)) {
        const attrs = m[2] + " " + m[4];
        declared.set(m[3], new El(m[1], m[3], /\shidden(\s|=|$)/.test(attrs)));
    }
    return declared;
}

// id -> hidden, for every declared element.
function hiddenMap(declared) {
    const out = {};
    for (const [id, el] of declared) out[id] = el.hidden;
    return out;
}

module.exports = { ClassList, El, pageScript, declaredElements, hiddenMap };
