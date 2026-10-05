"use strict";
// node --test apps/staf/tests/test_shell_js.cjs
// The STAF header's switch (www/shell/staf-shell.js): what a switch changes on the page, what it
// tells Shiny, the keyboard, a report dialog that brings its tool back, and the narrow-screen Menu.
const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const { El, page: fakePage } = require(path.join(__dirname, "..", "..", "..", "libs", "staf_workbook",
                                                  "tests", "fakedom.cjs"));
const SHELL = path.join(__dirname, "..", "www", "shell");

// jQuery as far as the shell uses it: on/off (".staf" namespace, delegation), trigger, extend
function jquery(document) {
  const bound = [];
  function split(events) {
    return events.split(" ").map((ev) => ev.endsWith(".staf") ? [ev.slice(0, -5), "staf"] : [ev, ""]);
  }
  function $(target) {
    const els = typeof target === "string" ? document.querySelectorAll(target) : [target];
    const api = {
      on(events, selector, fn) {
        if (typeof selector === "function") { fn = selector; selector = null; }
        for (const [name, ns] of split(events)) els.forEach((el) => bound.push({ el, name, ns, selector, fn }));
        return api;
      },
      off(events) {
        const ns = events.replace(/^\./, "");
        for (let i = bound.length - 1; i >= 0; i--) if (els.includes(bound[i].el) && bound[i].ns === ns) bound.splice(i, 1);
        return api;
      },
      trigger(name) { els.forEach((el) => $.fire(el, name, { target: el })); return api; },
      find(selector) { return els.flatMap((el) => el.querySelectorAll(selector)); },
    };
    return api;
  }
  $.extend = Object.assign;
  $.fire = (el, name, event) => {
    event.preventDefault = event.preventDefault || (() => {});
    for (const b of bound.slice()) {
      if (b.el !== el || b.name !== name) continue;
      if (!b.selector) { b.fn.call(el, event); continue; }
      const hit = event.target && event.target.closest && event.target.closest(b.selector);
      if (hit) b.fn.call(hit, event);
    }
  };
  return $;
}

function shell({ active = "easi", disabled = [] } = {}) {
  const p = fakePage({ showing: active, dirs: [SHELL] });
  const bar = p.body.appendChild(new El("div", { class: "staf-header easi-header" }));
  const sw = new El("div", { id: "staf_tool", class: "staf-tool-switch" });
  const buttons = {};
  for (const [key, name] of [["easi", "EASI"], ["sfari", "SFARI"], ["deep", "DEEP"]]) {
    const b = new El("button", { class: "staf-tool-btn" + (key === active ? " active" : ""), "data-tool": key });
    if (disabled.includes(key)) b.setAttribute("disabled", "");
    b.textContent = name;
    b.focus = () => { p.focused = key; };
    buttons[key] = sw.appendChild(b);
  }
  const menu = new El("button", { class: "staf-menu-btn", "aria-expanded": "false" });
  const nav = new El("div", { class: "staf-slot staf-nav", "data-staf-tool": active });
  bar.add(new El("div", { class: "staf-header-center" }).add(sw), new El("div", { class: "staf-header-right" }).add(nav, menu));
  const links = ["easi", "sfari", "deep"].map((key) => p.body.appendChild(
    new El("link", { "data-staf-css": key, media: key === active ? "all" : "not all" })));
  links.forEach((link) => { link.media = link.getAttribute("media"); });
  const winEvents = [], docEvents = [], urls = [], registered = [];
  p.window.dispatchEvent = (event) => winEvents.push(event.type);
  p.document.dispatchEvent = (event) => { docEvents.push(event); return true; };
  p.document.title = "";
  const $ = jquery(p.document);
  function InputBinding() {}
  Object.assign(p.context, {
    $, jQuery: $, location: { hash: "" }, requestAnimationFrame: (fn) => fn(),
    history: { state: null, replaceState: (state, title, url) => urls.push(url) },
    CustomEvent: function (type, init) { this.type = type; this.detail = (init || {}).detail; },
  });
  p.context.Shiny.InputBinding = InputBinding;
  p.context.Shiny.inputBindings = { register: (binding, name) => registered.push([binding, name]) };
  const rebinds = [];
  p.context.Shiny.bindAll = (scope) => rebinds.push(scope);
  p.load("staf-shell.js");
  const [binding, name] = registered[0];
  const calls = [];
  binding.subscribe(sw, (allow) => calls.push(allow));
  return { p, $, sw, bar, menu, buttons, links, binding, name, calls, winEvents, docEvents, urls, rebinds };
}

test("a click shows the tool: its stylesheets, title, address, and Shiny hears of it once", () => {
  const s = shell();
  assert.equal(s.name, "staf.toolSwitch");
  assert.equal(s.binding.getValue(), "easi");
  s.$.fire(s.sw, "click", { target: s.buttons.sfari });
  assert.equal(s.p.html.getAttribute("data-staf-tool"), "sfari");
  assert.equal(s.binding.getValue(), "sfari");
  assert.ok(s.buttons.sfari.classList.contains("active") && !s.buttons.easi.classList.contains("active"));
  assert.equal(s.buttons.sfari.getAttribute("aria-selected"), "true");
  assert.deepEqual(s.links.map((l) => l.media), ["not all", "all", "not all"]);
  assert.equal(s.p.document.title, "SFARI · STAF");
  assert.deepEqual(s.urls, ["?tool=sfari"]);
  assert.deepEqual(s.winEvents, ["resize"]);           // maps shown again re-measure
  assert.deepEqual(s.rebinds, [s.p.body]);             // Shiny looks at every output again
  assert.equal(s.docEvents[0].type, "staf:tool-shown");
  assert.equal(s.docEvents[0].detail.tool, "sfari");
  assert.deepEqual(s.calls, [false]);
  s.$.fire(s.sw, "click", { target: s.buttons.sfari });   // the tool already shown: nothing happens
  assert.deepEqual(s.calls, [false]);
});

test("a tool that cannot run here is never shown; the arrow keys skip it", () => {
  const s = shell({ disabled: ["sfari"] });
  s.$.fire(s.sw, "click", { target: s.buttons.sfari });
  assert.equal(s.p.html.getAttribute("data-staf-tool"), "easi");
  assert.deepEqual(s.calls, []);
  s.$.fire(s.sw, "keydown", { target: s.buttons.easi, key: "ArrowRight" });
  assert.equal(s.p.html.getAttribute("data-staf-tool"), "deep");
  assert.equal(s.p.focused, "deep");
  s.$.fire(s.sw, "keydown", { target: s.buttons.deep, key: "ArrowRight" });   // wraps around to EASI
  assert.equal(s.p.html.getAttribute("data-staf-tool"), "easi");
  assert.deepEqual(s.calls, [false, false]);
});

test("a report that opens for another tool brings that tool back", () => {
  const s = shell({ active: "sfari" });
  const modal = s.p.body.appendChild(new El("div", { class: "modal" }));
  modal.appendChild(new El("div", { id: "easi-report" }));
  s.$.fire(s.p.document, "show.bs.modal", { target: modal });
  assert.equal(s.p.html.getAttribute("data-staf-tool"), "easi");
  assert.deepEqual(s.calls, [false]);                  // Shiny hears of the switch too
  const other = s.p.body.appendChild(new El("div", { class: "modal" }));
  other.appendChild(new El("div", { id: "help" }));
  s.$.fire(s.p.document, "show.bs.modal", { target: other });
  assert.equal(s.p.html.getAttribute("data-staf-tool"), "easi");
});

test("the Menu opens and closes the shown tool's actions", () => {
  const s = shell();
  s.p.fire("click", s.menu);
  assert.ok(s.bar.classList.contains("menu-open"));
  assert.equal(s.menu.getAttribute("aria-expanded"), "true");
  s.p.fire("click", s.p.body);                          // a click elsewhere closes it
  assert.ok(!s.bar.classList.contains("menu-open"));
  s.p.fire("click", s.menu);
  s.p.fire("keydown", s.p.body, { key: "Escape" });
  assert.ok(!s.bar.classList.contains("menu-open"));
  s.p.fire("click", s.menu);
  s.$.fire(s.sw, "click", { target: s.buttons.deep });   // a switch closes it too
  assert.ok(!s.bar.classList.contains("menu-open"));
});
