"use strict";
// A small DOM stand-in for the browser-asset tests (the lib's and each app's): tags, classes,
// attributes and ids; selectors are comma lists of descendant chains of tag.class[attr="value"]
// parts, or #id. ``page`` builds a page with tool bodies (the STAF app's or a standalone app's)
// and runs scripts in it the way the browser does, one shared global scope.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function matchOne(el, sel) {
  const id = sel.match(/^#([\w-]+)$/);
  if (id) return el.id === id[1];
  const m = sel.match(/^([a-z]*)((?:\.[\w-]+)*)((?:\[[^\]]+\])*)$/i);
  if (!m) throw new Error("unsupported selector " + sel);
  if (m[1] && el.tag !== m[1].toLowerCase()) return false;
  for (const c of (m[2] || "").split(".").filter(Boolean)) if (!el.classList.contains(c)) return false;
  for (const a of (m[3] || "").match(/\[[^\]]+\]/g) || []) {
    const am = a.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
    if (!el.hasAttribute(am[1])) return false;
    if (am[2] !== undefined && el.getAttribute(am[1]) !== am[2]) return false;
  }
  return true;
}
function matchChain(el, chain) {
  const parts = chain.trim().split(/\s+/);
  if (!matchOne(el, parts.pop())) return false;
  for (let at = el.parentNode; parts.length && at; at = at.parentNode) {
    if (matchOne(at, parts[parts.length - 1])) parts.pop();
  }
  return parts.length === 0;
}

class El {
  constructor(tag, attrs = {}) {
    this.tag = tag.toLowerCase(); this.attrs = new Map(); this.children = []; this.parentNode = null;
    this.listeners = {}; this.style = {}; this.dataset = {}; this.value = ""; this.checked = false;
    this.textContent = ""; this.hidden = false; this.scrollTop = 0;
    const cls = this._cls = new Set();
    this.classList = {
      contains: (c) => cls.has(c), add: (c) => cls.add(c), remove: (c) => cls.delete(c),
      toggle: (c, force) => { const on = force === undefined ? !cls.has(c) : !!force; on ? cls.add(c) : cls.delete(c); return on; },
    };
    for (const [k, v] of Object.entries(attrs)) this.setAttribute(k, v);
  }
  get id() { return this.getAttribute("id") || ""; }
  set id(v) { this.setAttribute("id", v); }
  get tagName() { return this.tag.toUpperCase(); }
  get disabled() { return this.hasAttribute("disabled"); }
  get className() { return [...this._cls].join(" "); }
  set className(v) { this._cls.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => this._cls.add(c)); }
  set innerHTML(v) { for (const k of [...this.children]) k.remove(); }
  get isConnected() { let e = this; while (e.parentNode) e = e.parentNode; return e.tag === "html"; }
  setAttribute(n, v) {
    v = String(v);
    if (n === "class") { this.className = v; return; }
    this.attrs.set(n, v);
    if (n.startsWith("data-")) this.dataset[n.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase())] = v;
  }
  getAttribute(n) { if (n === "class") return this.className || null; return this.attrs.has(n) ? this.attrs.get(n) : null; }
  hasAttribute(n) { return n === "class" ? this._cls.size > 0 : this.attrs.has(n); }
  removeAttribute(n) { this.attrs.delete(n); }
  appendChild(k) { if (k.parentNode) k.remove(); k.parentNode = this; this.children.push(k); return k; }
  add(...kids) { kids.forEach((k) => this.appendChild(k)); return this; }
  remove() { const p = this.parentNode; if (!p) return; p.children.splice(p.children.indexOf(this), 1); this.parentNode = null; }
  insertAdjacentElement(where, k) {
    assert.equal(where, "afterend");
    const p = this.parentNode;
    if (k.parentNode) k.remove();
    k.parentNode = p; p.children.splice(p.children.indexOf(this) + 1, 0, k);
    return k;
  }
  all() { return this.children.flatMap((k) => [k, ...k.all()]); }
  matches(sel) { return sel.split(",").some((chain) => matchChain(this, chain)); }
  closest(sel) { for (let e = this; e; e = e.parentNode) if (e.matches(sel)) return e; return null; }
  querySelectorAll(sel) { return this.all().filter((e) => e.matches(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  contains(n) { for (let e = n; e; e = e.parentNode) if (e === this) return true; return false; }
  getClientRects() { if (!this.isConnected) return []; for (let e = this; e; e = e.parentNode) if (e.hidden) return []; return [{}]; }
  getBoundingClientRect() { return { left: 0, right: 120, top: 0, bottom: 20, width: 120, height: 20 }; }
  addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); }
  dispatchEvent(event) { event.target = event.target || this; (this.listeners[event.type] || []).forEach((fn) => fn(event)); return true; }
  fire(name, extra = {}) {
    const event = { type: name, target: this, preventDefault() {}, stopImmediatePropagation() {}, ...extra };
    (this.listeners[name] || []).forEach((fn) => fn(event));
    return event;
  }
  focus() {}
}

// A page. ``bodies``: [tool, prefix] pairs (none: an older page without tool bodies); ``showing``:
// the tool the STAF app shows (html[data-staf-tool]); ``dirs``: where ``load`` finds scripts.
function page({ bodies = [], showing = "", dirs = [] } = {}) {
  const html = new El("html");
  if (showing) html.setAttribute("data-staf-tool", showing);
  const body = html.appendChild(new El("body"));
  const listeners = {}, winListeners = {}, posts = [], handlers = new Map();
  const document = {
    documentElement: html, body, readyState: "complete",
    createElement: (tag) => new El(tag),
    querySelectorAll: (sel) => body.querySelectorAll(sel),
    querySelector: (sel) => body.querySelector(sel),
    getElementById: (id) => body.all().find((e) => e.id === id) || null,
    addEventListener: (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); },
  };
  const Shiny = {
    setInputValue: (name, value) => posts.push({ name, value }),
    addCustomMessageHandler: (name, fn) => handlers.set(name, fn),
  };
  const window = {
    Shiny, scrollX: 0, scrollY: 0, innerWidth: 1200, innerHeight: 800,
    addEventListener: (name, fn) => { (winListeners[name] = winListeners[name] || []).push(fn); },
    requestAnimationFrame: (fn) => fn(),
  };
  const tools = {};
  for (const [tool, prefix] of bodies) {
    tools[tool] = body.appendChild(new El("div", { "data-staf-tool": tool, "data-staf-ns": prefix }));
  }
  const context = {
    window, document, Shiny, setTimeout, clearTimeout, setInterval, clearInterval,
    Event: function (type, init) { this.type = type; Object.assign(this, init || {}); },
    MutationObserver: function () { this.observe = () => {}; },
    fetch: () => Promise.reject(new Error("no network in tests")),
  };
  function find(name) {
    for (const dir of dirs) {
      const file = path.join(dir, name);
      if (fs.existsSync(file)) return file;
    }
    throw new Error("script not found: " + name);
  }
  return {
    html, body, tools, document, window, posts, handlers, context,
    load(...names) {
      for (const name of names) {
        const file = find(name);
        vm.runInNewContext(fs.readFileSync(file, "utf8"), context, { filename: file });
      }
      return window.STAFNs;
    },
    fire(name, target, extra = {}) {
      const event = { type: name, target, preventDefault() { this.prevented = true; }, stopImmediatePropagation() {}, ...extra };
      (listeners[name] || []).forEach((fn) => fn(event));
      return event;
    },
    leave() {
      const event = { prevented: false, preventDefault() { this.prevented = true; } };
      (winListeners.beforeunload || []).forEach((fn) => fn(event));
      return event.prevented;
    },
    names() { return posts.map((p) => p.name); },
  };
}

module.exports = { El, page };
