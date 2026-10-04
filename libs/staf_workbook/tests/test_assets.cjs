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
