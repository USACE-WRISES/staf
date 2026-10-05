"use strict";
// node --test libs/staf_workbook/tests/test_assets.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const GUARD = fs.readFileSync(path.join(__dirname, "..", "assets", "unsaved-guard.js"), "utf8");

function load({ shiny = true, jquery = true } = {}) {
  const handlers = new Map(), jq = new Map(), win = new Map();
  const clock = { t: 1000000 };
  const timers = [];
  const window = {};
  window.addEventListener = (name, fn) => win.set(name, fn);
  if (jquery) window.jQuery = () => ({ on: (name, fn) => jq.set(name, fn) });
  const shinyObj = { addCustomMessageHandler: (name, fn) => handlers.set(name, fn) };
  if (shiny) window.Shiny = shinyObj;
  const context = {
    window, document: {}, Date: { now: () => clock.t },
    setInterval: (fn) => { timers.push(fn); return timers.length; }, clearInterval: () => {},
  };
  vm.runInNewContext(GUARD, context);
  return { handlers, jq, win, clock, timers, window, shinyObj };
}

function leave(h) {
  const e = { prevented: false, returnValue: undefined, preventDefault() { this.prevented = true; } };
  h.win.get("beforeunload")(e);
  return e;
}

test("no prompt while nothing is unsaved", () => {
  const h = load();
  assert.equal(leave(h).prevented, false);
  h.handlers.get("staf-unsaved")({ dirty: false });
  assert.equal(leave(h).prevented, false);
});

test("prompts while work is unsaved, and stops once it is saved", () => {
  const h = load();
  h.handlers.get("staf-unsaved")({ dirty: true });
  const e = leave(h);
  assert.equal(e.prevented, true);
  assert.equal(e.returnValue, "");
  h.handlers.get("staf-unsaved")({ dirty: false });
  assert.equal(leave(h).prevented, false);
});

test("a file download never triggers the warning", () => {
  const h = load();
  h.handlers.get("staf-unsaved")({ dirty: true });
  h.jq.get("shiny:filedownload")();
  assert.equal(leave(h).prevented, false);          // within the quiet window
  h.clock.t += 2500;
  assert.equal(leave(h).prevented, true);           // a real navigation later still warns
});

test("a closed session has nothing left to lose", () => {
  const h = load();
  h.handlers.get("staf-unsaved")({ dirty: true });
  h.jq.get("shiny:disconnected")();
  assert.equal(leave(h).prevented, false);
});

test("registers once Shiny arrives", () => {
  const h = load({ shiny: false });
  assert.equal(h.handlers.size, 0);
  h.window.Shiny = h.shinyObj;
  h.timers[0]();
  assert.ok(h.handlers.has("staf-unsaved"));
});

// ---------------------------------------------------------------------------- metric rows
// A small DOM stand-in: tags, classes and attributes, closest and querySelector(All) on simple
// selectors (tag.class[attr="value"], comma lists). Enough for assets/metric-rows.js.
const ROWS = fs.readFileSync(path.join(__dirname, "..", "assets", "metric-rows.js"), "utf8");

function matches(el, selector) {
  return selector.split(",").some((one) => {
    const m = one.trim().match(/^([a-z]*)((?:\.[\w-]+)*)((?:\[[^\]]+\])*)$/i);
    if (!m) throw new Error("unsupported selector " + one);
    if (m[1] && el.tag !== m[1]) return false;
    for (const c of (m[2] || "").split(".").filter(Boolean)) if (!el.classList.contains(c)) return false;
    for (const a of (m[3] || "").match(/\[[^\]]+\]/g) || []) {
      const am = a.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
      if (!el.attrs.has(am[1])) return false;
      if (am[2] !== undefined && el.attrs.get(am[1]) !== am[2]) return false;
    }
    return true;
  });
}

class El {
  constructor(tag, cls = "", attrs = {}) {
    this.tag = tag; this.parent = null; this.children = []; this.attrs = new Map(Object.entries(attrs));
    this.value = ""; this.textContent = ""; this.focused = false;
    const set = new Set(cls.split(" ").filter(Boolean));
    this.classList = {
      contains: (c) => set.has(c), add: (c) => set.add(c), remove: (c) => set.delete(c),
      toggle: (c, force) => { const on = force === undefined ? !set.has(c) : !!force; on ? set.add(c) : set.delete(c); return on; },
    };
  }
  add(...kids) { for (const k of kids) { k.parent = this; this.children.push(k); } return this; }
  getAttribute(n) { return this.attrs.has(n) ? this.attrs.get(n) : null; }
  setAttribute(n, v) { this.attrs.set(n, String(v)); }
  focus() { this.focused = true; }
  closest(sel) { for (let e = this; e; e = e.parent) if (matches(e, sel)) return e; return null; }
  all() { return this.children.flatMap((k) => [k, ...k.all()]); }
  querySelectorAll(sel) { return this.all().filter((e) => matches(e, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function rowsDom() {
  const listeners = {};
  const document = { addEventListener: (name, fn) => { listeners[name] = fn; } };
  const window = {};
  vm.runInNewContext(ROWS, { window, document });
  const fire = (name, target) => listeners[name]({ target, preventDefault() {} });
  return { window, fire };
}

function metricRow() {
  const row = new El("div", "staf-metric");
  const note = new El("button", "staf-act", { "data-staf-panel": "note", "aria-expanded": "false" });
  note.add(new El("span", "staf-act-dot"));
  const photo = new El("button", "staf-act", { "data-staf-panel": "photo", "aria-expanded": "false" });
  const label = new El("span", "staf-act-label"); label.textContent = "Photo";
  photo.add(label, new El("span", "staf-act-count"));
  const scoring = new El("button", "staf-act", { "data-staf-panel": "scoring", "aria-expanded": "false" });
  const text = new El("textarea", "staf-metric-note");
  const notePanel = new El("div", "staf-metric-panel", { "data-panel": "note" }).add(text);
  const photoPanel = new El("div", "staf-metric-panel", { "data-panel": "photo" });
  row.add(new El("div", "staf-metric-acts").add(scoring, note, photo), notePanel, photoPanel);
  return { row, note, photo, label, scoring, text, photoPanel };
}

test("a row button opens and closes its panel and says so", () => {
  const { fire } = rowsDom();
  const r = metricRow();
  fire("click", r.scoring);
  assert.ok(r.row.classList.contains("show-scoring"));
  assert.ok(r.scoring.classList.contains("on"));
  assert.equal(r.scoring.getAttribute("aria-expanded"), "true");
  assert.equal(r.text.focused, false);                 // the scoring panel takes no focus
  fire("click", r.scoring);
  assert.ok(!r.row.classList.contains("show-scoring"));
  assert.equal(r.scoring.getAttribute("aria-expanded"), "false");
});

test("opening a note focuses it and typing shows the dot", () => {
  const { fire } = rowsDom();
  const r = metricRow();
  fire("click", r.note.children[0]);                   // a click on the button's inner span
  assert.ok(r.row.classList.contains("show-note") && r.text.focused);
  r.text.value = "dry bed";
  fire("input", r.text);
  assert.ok(r.note.classList.contains("has"));
  r.text.value = "  ";
  fire("input", r.text);
  assert.ok(!r.note.classList.contains("has"));
});

test("sync counts the photos a row holds", () => {
  const { window } = rowsDom();
  const r = metricRow();
  r.photoPanel.add(new El("img"), new El("img"));
  window.STAFMetricRows.sync(r.row);
  assert.equal(r.photo.children[1].textContent, "2");
  assert.equal(r.label.textContent, "Photos");
  assert.ok(r.photo.classList.contains("has"));
  r.photoPanel.children.length = 0;
  window.STAFMetricRows.sync(r.row);
  assert.equal(r.photo.children[1].textContent, "");
  assert.equal(r.label.textContent, "Photo");
  assert.ok(!r.photo.classList.contains("has"));
});

test("a [data-staf-host] card hosts its own note button", () => {
  const { fire } = rowsDom();
  const card = new El("div", "sfari-scorecard", { "data-staf-host": "" });
  const btn = new El("button", "staf-act", { "data-staf-panel": "fnnote", "aria-expanded": "false" });
  const ta = new El("textarea", "sfari-fn-note staf-metric-note");
  card.add(btn, ta);
  fire("click", btn);
  assert.ok(card.classList.contains("show-fnnote") && ta.focused);
  ta.value = "why the score differs";
  fire("input", ta);
  assert.ok(btn.classList.contains("has"));
});

test("clicks elsewhere are left alone", () => {
  const { fire } = rowsDom();
  const r = metricRow();
  fire("click", r.row);
  assert.ok(!r.row.classList.contains("show-scoring") && !r.row.classList.contains("show-note"));
});
