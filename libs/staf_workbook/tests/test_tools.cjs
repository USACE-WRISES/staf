"use strict";
// node --test libs/staf_workbook/tests/test_tools.cjs
// One page, several tools: staf-ns.js and the shared scripts that talk to Shiny, on a page without
// a tool body (older pages), with one body and no prefix (a standalone app) and with the STAF app's
// prefixed bodies, where every post must reach the owning tool's own input.
const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const ASSETS = path.join(__dirname, "..", "assets");

const { El, page: fakePage } = require("./fakedom.cjs");

// a page whose scripts come from the lib's assets, staf-ns.js always first
function page(options = {}) {
  const p = fakePage({ ...options, dirs: [ASSETS] });
  const load = p.load;
  p.load = (...names) => load("staf-ns.js", ...names);
  return p;
}

const settle = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------------------- staf-ns.js
test("a page without tool bodies is one tool without a prefix", () => {
  const p = page();
  const NS = p.load();
  const input = p.body.appendChild(new El("input", { id: "lat" }));
  assert.equal(NS.roots().length, 0);
  assert.equal(NS.owner(input), null);
  assert.equal(NS.id(NS.owner(input), "coords_entered"), "coords_entered");
  assert.equal(NS.bare(input), "lat");
  assert.equal(NS.mine(input, "sfari"), true);
  assert.equal(NS.scope(null), p.document);
});

test("a standalone app's body keeps plain ids", () => {
  const p = page({ bodies: [["sfari", ""]] });
  const NS = p.load();
  const input = p.tools.sfari.appendChild(new El("input", { id: "lat" }));
  const dialog = p.body.appendChild(new El("button"));
  assert.equal(NS.owner(input), p.tools.sfari);
  assert.equal(NS.owner(dialog), p.tools.sfari);          // the only body owns the page
  assert.equal(NS.id(p.tools.sfari, "map"), "map");
  assert.equal(NS.bare(input), "lat");
  assert.equal(NS.forNs(""), p.tools.sfari);
  assert.equal(NS.tool("sfari"), p.tools.sfari);
});

test("the STAF app's bodies carry their prefixes and the shown tool owns dialogs", () => {
  const p = page({ bodies: [["easi", "easi"], ["sfari", "sfari"], ["deep", "deep"]], showing: "sfari" });
  const NS = p.load();
  const lat = p.tools.sfari.appendChild(new El("input", { id: "sfari-lat" }));
  const easiButton = p.tools.easi.appendChild(new El("button"));
  const dialog = p.body.appendChild(new El("button"));
  assert.equal(NS.roots().length, 3);
  assert.equal(NS.active(), "sfari");
  assert.equal(NS.shown(), p.tools.sfari);
  assert.equal(NS.owner(easiButton), p.tools.easi);
  assert.equal(NS.owner(dialog), p.tools.sfari);
  assert.equal(NS.id(p.tools.easi, "map"), "easi-map");
  assert.equal(NS.bare(lat), "lat");
  assert.equal(NS.forNs("deep"), p.tools.deep);
  assert.equal(NS.mine(easiButton, "sfari"), false);
  assert.equal(NS.mine(easiButton, "easi"), true);
  assert.equal(NS.mine(dialog, "sfari"), true);            // outside every body: the shown tool's
  assert.equal(NS.mine(dialog, "easi"), false);
  assert.equal(NS.scope(p.tools.deep), p.tools.deep);
});

// ---------------------------------------------------------------------------- coord-entry.js
function coordPage(bodies) {
  const p = page({ bodies, showing: bodies.length > 1 ? bodies[1][0] : "" });
  p.load("coord-entry.js");
  return p;
}
function typeCoords(p, root, prefix) {
  const lat = root.appendChild(new El("input", { id: prefix + "lat" }));
  const lon = root.appendChild(new El("input", { id: prefix + "lon" }));
  lat.value = "39.1"; lon.value = "-84.5";
  p.fire("keydown", lon, { key: "Enter" });
}

test("typed coordinates post to the owning tool's input", () => {
  const p = coordPage([["easi", "easi"], ["sfari", "sfari"]]);
  typeCoords(p, p.tools.easi, "easi-");
  assert.deepEqual(p.names(), ["easi-coords_entered"]);
  assert.deepEqual(p.posts[0].value.lat, 39.1);
  typeCoords(p, p.tools.sfari, "sfari-");
  assert.deepEqual(p.names(), ["easi-coords_entered", "sfari-coords_entered"]);
});

test("typed coordinates keep their plain input name in a standalone app and an older page", () => {
  const standalone = coordPage([["deep", ""]]);
  typeCoords(standalone, standalone.tools.deep, "");
  assert.deepEqual(standalone.names(), ["coords_entered"]);
  const older = coordPage([]);
  typeCoords(older, older.body, "");
  assert.deepEqual(older.names(), ["coords_entered"]);
});

test("a half-typed coordinate posts nothing", () => {
  const p = coordPage([["easi", "easi"]]);
  const lat = p.tools.easi.appendChild(new El("input", { id: "easi-lat" }));
  p.tools.easi.appendChild(new El("input", { id: "easi-lon" }));
  lat.value = "39.1";
  p.fire("keydown", lat, { key: "Enter" });
  assert.deepEqual(p.posts, []);
});

// ---------------------------------------------------------------------------- scenarios.js
test("a scenario choice posts to its tool; a dialog's button to the tool shown", () => {
  const p = page({ bodies: [["easi", "easi"], ["deep", "deep"]], showing: "deep" });
  p.load("scenarios.js");
  const chip = p.tools.easi.appendChild(new El("button", { "data-sc-action": "select", "data-sc-id": "s2" }));
  p.fire("click", chip);
  const remove = p.body.appendChild(new El("button", { "data-sc-action": "delete" }));
  p.fire("click", remove);
  assert.deepEqual(p.names(), ["easi-staf_scenario_evt", "deep-staf_scenario_evt"]);
  assert.equal(p.posts[0].value.action, "select");
  assert.equal(p.posts[0].value.id, "s2");
});

// ---------------------------------------------------------------------------- unsaved-guard.js
test("each tool keeps its own unsaved flag and the page warns while any is set", () => {
  const p = page({ bodies: [["easi", "easi"], ["sfari", "sfari"]], showing: "easi" });
  p.load("unsaved-guard.js");
  const send = p.handlers.get("staf-unsaved");
  send({ dirty: true, ns: "sfari" });
  send({ dirty: false, ns: "easi" });                      // EASI starting never clears SFARI's work
  assert.equal(p.leave(), true);
  send({ dirty: false, ns: "sfari" });
  assert.equal(p.leave(), false);
  send({ dirty: true });                                   // a standalone app's message (no ns)
  assert.equal(p.leave(), true);
});

// ---------------------------------------------------------------------------- legend-dock.js
function mapIn(root) {
  const input = new El("input", { type: "checkbox" });
  input.checked = true;
  const label = new El("label").add(input);
  label.textContent = " Streams";
  const list = new El("div", { class: "leaflet-control-layers-list" })
    .add(new El("div", { class: "leaflet-control-layers-overlays" }).add(label));
  const corner = new El("div", { class: "leaflet-top leaflet-right" })
    .add(new El("div", { class: "leaflet-control-layers" }).add(list));
  root.appendChild(new El("div", { class: "easi-map-wrap" }).add(corner));
  const panel = root.appendChild(new El("div", { class: "easi-legend-panel" }));
  return { input, list, corner, panel };
}

test("each tool's map docks its own legend and posts its own coverage and streams inputs", () => {
  const p = page({ bodies: [["easi", "easi"], ["sfari", "sfari"]], showing: "sfari" });
  const easi = mapIn(p.tools.easi), sfari = mapIn(p.tools.sfari);
  p.load("legend-dock.js");
  assert.equal(easi.panel.parentNode, easi.corner);
  assert.equal(sfari.panel.parentNode, sfari.corner);
  assert.deepEqual(p.posts, [], "nothing posts before the session is initialized");
  p.fire("shiny:sessioninitialized", p.document);
  assert.deepEqual(p.posts.map((x) => [x.name, x.value]), [
    ["easi-streamcat_coverage", false], ["easi-streams_visible", true],
    ["sfari-streamcat_coverage", false], ["sfari-streams_visible", true],
  ]);
  const count = p.posts.length;
  const toggle = sfari.list.querySelector(".staf-coverage-toggle");
  toggle.checked = true;
  toggle.fire("change");
  sfari.input.checked = false;
  sfari.input.fire("change");
  assert.deepEqual(p.posts.slice(count).map((x) => [x.name, x.value]), [
    ["sfari-streamcat_coverage", true], ["sfari-streams_visible", false],
  ]);
  assert.equal(easi.list.querySelector(".staf-coverage-toggle").checked, false);
});

test("a standalone map posts the plain input names", () => {
  const p = page({ bodies: [["deep", ""]] });
  mapIn(p.tools.deep);
  p.load("legend-dock.js");
  p.fire("shiny:sessioninitialized", p.document);
  assert.deepEqual(p.names(), ["streamcat_coverage", "streams_visible"]);
});

test("the Layers button opens on a click, never on hover", () => {
  const p = page({ bodies: [["deep", ""]] });
  const m = mapIn(p.tools.deep);
  p.load("legend-dock.js");
  const control = m.corner.querySelector(".leaflet-control-layers");
  let stopped = 0;
  const hover = () => control.fire("mouseover", { stopImmediatePropagation() { stopped += 1; } });
  hover();
  assert.equal(stopped, 1, "a pointer over the closed button never reaches Leaflet");
  control.classList.add("leaflet-control-layers-expanded");      // a click opened it
  hover();
  assert.equal(stopped, 1, "inside the open menu Leaflet hears the pointer as before");
  assert.equal(control.listeners.mouseover.length, 1);
});

// ---------------------------------------------------------------------------- report-ready.js
test("one tool preparing a report never blocks or reorders another tool's", () => {
  const p = page({ bodies: [["easi", "easi"], ["sfari", "sfari"]], showing: "sfari" });
  const button = {};
  for (const tool of ["easi", "sfari"]) {
    button[tool] = p.tools[tool].appendChild(new El("div", { class: "sfari-rollup" }))
      .appendChild(new El("button", { "data-report": "" }));
  }
  p.load("report-ready.js");
  const send = p.handlers.get("staf-report-state");
  send({ requestId: 5, busy: true, ns: "easi" });
  assert.equal(button.easi.hasAttribute("disabled"), true);
  assert.equal(button.sfari.hasAttribute("disabled"), false);
  assert.ok(p.document.getElementById("staf-report-preparing-easi"));
  send({ requestId: 1, busy: true, ns: "sfari" });         // its own sequence, not EASI's
  assert.equal(button.sfari.hasAttribute("disabled"), true);
  send({ requestId: 5, busy: false, ns: "easi", opened: true });
  assert.equal(button.easi.hasAttribute("disabled"), false);
  assert.equal(button.sfari.hasAttribute("disabled"), true);
  assert.equal(p.document.getElementById("staf-report-preparing-easi"), null);
  const blocked = p.fire("click", button.sfari);
  const allowed = p.fire("click", button.easi);
  assert.equal(blocked.prevented, true);
  assert.equal(allowed.prevented, undefined);
  send({ requestId: 1, busy: false, ns: "sfari", opened: false });
  assert.equal(button.sfari.hasAttribute("disabled"), false);
});

// ---------------------------------------------------------------------------- geocode-autocomplete.js
test("a picked place posts to the tool whose search box it came from", async () => {
  const p = page({ bodies: [["easi", "easi"], ["sfari", "sfari"]], showing: "easi" });
  p.context.fetch = () => Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ features: [{ properties: { countrycode: "US", name: "Mill Creek", state: "Ohio" },
                                               geometry: { coordinates: [-84.5, 39.1] } }] }),
  });
  p.load("geocode-autocomplete.js");
  const box = p.tools.sfari.appendChild(new El("input", { id: "sfari-address" }));
  box.value = "Mill";
  p.fire("input", box);
  await settle(300);                                       // the type-ahead's debounce
  await settle();
  const row = p.body.querySelector(".easi-ac-item");
  assert.ok(row, "the suggestion menu shows the place");
  row.fire("mousedown");
  assert.deepEqual(p.names(), ["sfari-address_pick"]);
  assert.equal(p.posts[0].value.label, "Mill Creek, Ohio");
});
