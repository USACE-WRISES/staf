"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function harness(app, options = {}) {
  const listeners = new Map(), jqueryListeners = new Map(), customHandlers = new Map();
  const registrations = [], applicationClicks = [], applicationKeys = [], focusCalls = [];
  let observer = null, observed = false, document;
  const mutations = [], frames = [];
  let frameRequests = 0;

  function changed(el, addedNodes = [], removedNodes = []) {
    if (observed && el.isConnected) mutations.push({ target: el, addedNodes, removedNodes });
  }
  function matchesOne(el, selector) {
    if (selector.startsWith("#")) return el.id === selector.slice(1);
    if (selector.startsWith(".")) {
      const classes = (el.getAttribute("class") || "").split(/\s+/);
      return selector.slice(1).split(".").every((name) => classes.includes(name));
    }
    const attr = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
    if (attr) return attr[2] === undefined ? el.hasAttribute(attr[1]) : el.getAttribute(attr[1]) === attr[2];
    return el.tagName === selector.toUpperCase();
  }
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase();
      this.attributes = new Map();
      this.children = [];
      this.parentNode = null;
      this.dataset = {};
      this.style = {};
      this.hidden = false;
      this.textContent = "";
    }
    get id() { return this.getAttribute("id") || ""; }
    set id(value) { this.setAttribute("id", value); }
    get disabled() { return this.hasAttribute("disabled"); }
    get isConnected() { return this === document.body || !!(this.parentNode && this.parentNode.isConnected); }
    setAttribute(name, value) {
      this.attributes.set(name, String(value));
      if (name.startsWith("data-")) {
        const key = name.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
        this.dataset[key] = String(value);
      }
    }
    getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
    hasAttribute(name) { return this.attributes.has(name); }
    removeAttribute(name) { this.attributes.delete(name); }
    matches(selector) { return selector.split(",").some((part) => matchesOne(this, part.trim())); }
    closest(selector) {
      for (let el = this; el; el = el.parentNode) if (el.matches(selector)) return el;
      return null;
    }
    querySelectorAll(selector) {
      const result = [];
      function visit(parent) {
        for (const child of parent.children) {
          if (child.matches(selector)) result.push(child);
          visit(child);
        }
      }
      visit(this);
      return result;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    contains(node) {
      for (let el = node; el; el = el.parentNode) if (el === this) return true;
      return false;
    }
    appendChild(child) {
      if (child.parentNode) child.remove();
      child.parentNode = this;
      this.children.push(child);
      changed(this, [child]);
      return child;
    }
    remove() {
      if (!this.parentNode) return;
      const parent = this.parentNode;
      changed(parent, [], [this]);
      parent.children.splice(parent.children.indexOf(this), 1);
      this.parentNode = null;
    }
    getClientRects() {
      if (!this.isConnected) return [];
      for (let el = this; el; el = el.parentNode) if (el.hidden) return [];
      return [{}];
    }
    click() {
      if (this.tagName !== "BUTTON" || !this.disabled) dispatch("click", this);
    }
    focus(settings) {
      document.activeElement = this;
      focusCalls.push({ element: this, preventScroll: settings.preventScroll });
    }
  }
  function bind(registry, names, callback, capture = false) {
    for (const name of names.split(" ")) {
      if (!registry.has(name)) registry.set(name, []);
      registry.get(name).push({ callback, capture });
    }
  }
  document = {
    body: null, activeElement: null, readyState: options.loading ? "loading" : "complete",
    createElement: (tag) => new Element(tag),
    querySelectorAll(selector) { return this.body.querySelectorAll(selector); },
    querySelector(selector) { return this.body.querySelector(selector); },
    addEventListener(name, fn, capture) { bind(listeners, name, fn, capture); },
  };
  document.body = new Element("body");
  function make(tag, attrs = {}, parent = document.body) {
    const el = new Element(tag);
    for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, value);
    parent.appendChild(el);
    return el;
  }
  const host = make("div", { class: "sfari-rollup" });
  const button = make("button", { "data-report": "" }, host);
  const anchor = make("a", { "data-step": "report" }, host);
  const window = {
    jQuery: () => ({ on: (name, fn) => bind(jqueryListeners, name, fn) }),
    requestAnimationFrame(fn) { frameRequests++; frames.push(fn); },
  };
  function installShiny() {
    window.Shiny = {
      addCustomMessageHandler(name, fn) {
        registrations.push(name);
        customHandlers.set(name, fn);
      },
    };
  }
  if (!options.lateShiny) installShiny();
  function dispatch(type, target, key) {
    const event = {
      target, key, defaultPrevented: false, stopped: false,
      preventDefault() { this.defaultPrevented = true; },
      stopImmediatePropagation() { this.stopped = true; },
    };
    const ordered = [...(listeners.get(type) || [])].sort((a, b) => Number(b.capture) - Number(a.capture));
    for (const listener of ordered) {
      listener.callback(event);
      if (event.stopped) break;
    }
    return event;
  }
  const filename = path.resolve(__dirname, "..", "..", app, "www", "report-ready.js");
  vm.runInNewContext(fs.readFileSync(filename, "utf8"), {
    window, document,
    MutationObserver: function (callback) {
      observer = callback;
      this.observe = (_, config) => {
        assert.equal(config.childList, true);
        assert.equal(config.subtree, true);
        observed = true;
      };
    },
  }, { filename });
  document.addEventListener("click", (event) => applicationClicks.push(event.target));
  document.addEventListener("keydown", (event) => applicationKeys.push(event.key));
  function flushObservers() {
    for (let count = 0; mutations.length; count++) {
      assert.ok(count < 20, "MutationObserver microtasks should settle without waiting for a frame");
      observer(mutations.splice(0));
    }
  }
  return {
    document, button, anchor, host, make, registrations, applicationClicks, applicationKeys, focusCalls,
    installShiny, dispatch, flushObservers,
    get frameRequests() { return frameRequests; },
    get pendingFrames() { return frames.length; },
    emitJquery(name, target = document) {
      for (const { callback } of jqueryListeners.get(name) || []) callback({ target });
    },
    emitNative(name) { dispatch(name, document); },
    send(message) {
      assert.ok(customHandlers.has("staf-report-state"), "custom handler must be registered");
      customHandlers.get("staf-report-state")(message);
    },
    flushMutations() {
      for (let count = 0; mutations.length || frames.length; count++) {
        assert.ok(count < 20, "MutationObserver and animation frames should settle");
        flushObservers();
        for (const frame of frames.splice(0)) frame();
      }
    },
    status() { return document.querySelector("#staf-report-preparing"); },
    modal(report = true) {
      const modal = make("div", { class: "modal" });
      make("div", { id: report ? app + "-report" : "other-dialog" }, modal);
      return modal;
    },
  };
}

for (const app of ["easi", "sfari", "deep"]) {
  test(app + ": registers once when Shiny arrives after DOM load", () => {
    const h = harness(app, { loading: true, lateShiny: true });
    assert.deepEqual(h.registrations, []);
    h.emitNative("DOMContentLoaded");
    h.installShiny();
    h.emitJquery("shiny:connected");
    h.emitNative("shiny:connected");
    h.emitJquery("shiny:connected");
    assert.deepEqual(h.registrations, ["staf-report-state"]);
    h.send({ requestId: 1, busy: true });
    assert.equal(h.button.disabled, true);
  });

  test(app + ": busy blocks duplicate clicks and activation keys, preserving unrelated actions", () => {
    const h = harness(app);
    const child = h.make("span", {}, h.button);
    h.dispatch("click", child);
    assert.equal(h.applicationClicks.length, 1);
    h.send({ requestId: 1, busy: true });
    const click = h.dispatch("click", child);
    assert.equal(click.defaultPrevented, true);
    assert.equal(click.stopped, true);
    for (const key of ["Enter", " ", "Spacebar"]) {
      const keydown = h.dispatch("keydown", h.anchor, key);
      assert.equal(keydown.defaultPrevented, true);
      assert.equal(keydown.stopped, true);
    }
    assert.equal(h.applicationClicks.length, 1);
    assert.equal(h.applicationKeys.length, 0);
    const other = h.make("button", {}, h.host);
    assert.equal(h.dispatch("click", other).stopped, false);
    assert.equal(h.dispatch("keydown", h.anchor, "Escape").stopped, false);
    assert.equal(h.status().getAttribute("role"), "status");
    assert.equal(h.status().getAttribute("aria-live"), "polite");
    assert.equal(h.status().textContent, "Preparing report…");
  });

  test(app + ": restores original disabled attributes and removes preparation status", () => {
    const h = harness(app);
    const locked = h.make("button", { "data-report": "", disabled: "disabled", "aria-disabled": "true" }, h.host);
    const customAria = h.make("a", { "data-report": "", "aria-disabled": "false" }, h.host);
    h.flushMutations();
    h.send({ requestId: 1, busy: true });
    assert.equal(h.button.disabled, true);
    assert.equal(customAria.getAttribute("aria-disabled"), "true");
    h.send({ requestId: 1, busy: false, opened: true });
    h.flushMutations();
    assert.equal(h.button.disabled, false);
    assert.equal(h.button.getAttribute("aria-disabled"), null);
    assert.equal(locked.getAttribute("disabled"), "disabled");
    assert.equal(locked.getAttribute("aria-disabled"), "true");
    assert.equal(customAria.getAttribute("aria-disabled"), "false");
    assert.equal(h.status(), null);
  });

  test(app + ": newly rendered controls join busy state and are restored afterward", () => {
    const h = harness(app);
    h.send({ requestId: 1, busy: true });
    h.button.remove();
    const replacement = h.make("button", { "data-report": "" }, h.host);
    const batch = h.make("a", { class: "easi-batch-report-link" }, h.host);
    h.flushMutations();
    assert.equal(replacement.disabled, true);
    assert.equal(batch.getAttribute("aria-disabled"), "true");
    assert.equal(batch.getAttribute("tabindex"), "0");
    h.send({ requestId: 1, busy: false, opened: false });
    h.flushMutations();
    assert.equal(replacement.disabled, false);
    assert.equal(batch.getAttribute("aria-disabled"), null);
    assert.equal(h.status(), null);
  });

  test(app + ": output mutation bursts coalesce without looping on the preparation status", () => {
    const h = harness(app);
    h.send({ requestId: 1, busy: true });
    h.flushObservers();
    assert.equal(h.pendingFrames, 0, "inserting our status must not schedule a reconciliation");
    const replacement = h.make("button", { "data-report": "" }, h.host);
    h.flushObservers();
    for (let i = 0; i < 50; i++) {
      h.make("span", {}, h.host);
      h.flushObservers();
    }
    assert.equal(h.pendingFrames, 1, "separate Shiny/Plotly mutation batches share one frame");
    assert.equal(h.frameRequests, 1);
    assert.equal(h.dispatch("click", replacement).stopped, true,
      "duplicate activation remains blocked before the frame updates native disabled state");
    h.flushMutations();
    assert.equal(replacement.disabled, true);
    assert.equal(h.frameRequests, 1);
    h.send({ requestId: 1, busy: false, opened: true });
    h.flushObservers();
    assert.equal(h.status(), null);
    assert.equal(h.pendingFrames, 0, "removing our status must not schedule a reconciliation");
  });

  test(app + ": automatic opening uses the worksheet rail through a workspace replacement", () => {
    const h = harness(app);
    h.host.remove();
    const emptyPane = h.make("div", { id: "leftpane" });
    const workspace = h.make("div", { class: "sfari-worksheet" });
    const nav = h.make("div", { class: "sfari-nav" }, workspace);
    h.make("a", { "data-step": "report" }, nav);
    const rail = h.make("div", { class: "sfari-rollup" }, workspace);
    h.make("button", { "data-report": "" }, rail);
    h.flushMutations();
    h.send({ requestId: 1, busy: true });
    h.flushMutations();
    assert.equal(h.status().parentNode, rail);
    assert.equal(emptyPane.children.length, 0, "the empty map pane beneath Assessment stays empty");
    workspace.remove();
    const nextWorkspace = h.make("div", { class: "sfari-worksheet" });
    const nextRail = h.make("div", { class: "sfari-rollup" }, nextWorkspace);
    const replacement = h.make("button", { "data-report": "" }, nextRail);
    h.flushMutations();
    assert.equal(h.status().parentNode, nextRail);
    assert.equal(replacement.disabled, true);
    const beforeFinish = h.frameRequests;
    h.send({ requestId: 1, busy: false, opened: true });
    h.flushMutations();
    assert.equal(h.status(), null);
    assert.equal(replacement.disabled, false);
    assert.equal(h.frameRequests, beforeFinish);
  });

  test(app + ": stale and invalid messages cannot reopen or end the current busy state", () => {
    const h = harness(app);
    h.send({ requestId: 5, busy: true });
    h.send({ requestId: 4, busy: false });
    h.send({ requestId: "invalid", busy: false });
    assert.equal(h.button.disabled, true);
    h.send({ requestId: 5, busy: false, opened: true });
    h.send({ requestId: 5, busy: true });
    assert.equal(h.button.disabled, false);
    assert.equal(h.status(), null);
    h.send({ requestId: 6, busy: true });
    h.send({ requestId: 5, busy: false });
    assert.equal(h.button.disabled, true);
    h.send({ requestId: 6, busy: false, opened: false });
    assert.equal(h.button.disabled, false);
  });

  for (const replaced of [false, true]) {
    test(app + ": report close restores focus to " + (replaced ? "the replaced opener" : "the original opener"), () => {
      const h = harness(app);
      h.dispatch("click", h.button);
      h.send({ requestId: 1, busy: true });
      let expected = h.button;
      if (replaced) {
        h.button.remove();
        expected = h.make("button", { "data-report": "" }, h.host);
        h.flushMutations();
      }
      const report = h.modal();
      h.emitJquery("shown.bs.modal", report);
      h.send({ requestId: 1, busy: false, opened: true });
      assert.equal(h.focusCalls.length, 0);
      const other = h.modal(false);
      h.emitJquery("shown.bs.modal", other);
      h.emitJquery("hidden.bs.modal", other);
      assert.equal(h.focusCalls.length, 0, "an unrelated modal must not steal focus");
      h.emitJquery("hidden.bs.modal", report);
      assert.equal(h.focusCalls.length, 1);
      assert.equal(h.focusCalls[0].element, expected);
      assert.equal(h.focusCalls[0].preventScroll, true);
      h.emitJquery("hidden.bs.modal", report);
      assert.equal(h.focusCalls.length, 1);
    });
  }

  test(app + ": non-button report entry is focusable and keyboard activation emits one click", () => {
    const h = harness(app);
    assert.equal(h.anchor.getAttribute("role"), "button");
    assert.equal(h.anchor.getAttribute("tabindex"), "0");
    for (const key of ["Enter", " ", "Spacebar"]) {
      const before = h.applicationClicks.length;
      const event = h.dispatch("keydown", h.anchor, key);
      assert.equal(event.defaultPrevented, true);
      assert.equal(event.stopped, true);
      assert.equal(h.applicationClicks.length, before + 1);
      assert.equal(h.applicationClicks.at(-1), h.anchor);
    }
    const native = h.dispatch("keydown", h.button, "Enter");
    assert.equal(native.defaultPrevented, false, "native button activation stays with the browser");
  });

  test(app + ": reconnect restores controls and accepts a fresh session request sequence", () => {
    const h = harness(app);
    h.send({ requestId: 20, busy: true });
    h.emitJquery("shiny:connected");
    assert.equal(h.button.disabled, false);
    assert.equal(h.status(), null);
    h.send({ requestId: 1, busy: true });
    assert.equal(h.button.disabled, true);
    assert.deepEqual(h.registrations, ["staf-report-state"]);
  });

  test(app + ": only the final function Done control participates in report preparation", () => {
    const h = harness(app);
    const first = h.make("div", { class: "sfari-nav-fn active", "data-idx": "0" }, h.host);
    const last = h.make("div", { class: "sfari-nav-fn", "data-idx": "1" }, h.host);
    const next = h.make("button", { "data-nav": "1" }, h.host);
    h.send({ requestId: 1, busy: true });
    assert.equal(next.disabled, false);
    assert.equal(h.dispatch("click", next).stopped, false);
    h.send({ requestId: 1, busy: false });
    first.setAttribute("class", "sfari-nav-fn");
    last.setAttribute("class", "sfari-nav-fn active");
    h.send({ requestId: 2, busy: true });
    assert.equal(next.disabled, true);
    assert.equal(h.dispatch("click", next).stopped, true);
    h.send({ requestId: 2, busy: false });
    assert.equal(next.disabled, false);
  });
}
