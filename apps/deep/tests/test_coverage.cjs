"use strict";
// node --test apps/deep/tests/test_coverage.cjs
// DEEP's assessment regions (www/coverage.js; owner, 2026-10-05). The script finds DEEP's own map
// even when other tools' maps come first (the STAF app) or another Leaflet arrives later (EASI's
// Nationwide viewer), asks DEEP's own input for the regions, and then: lists them in the right
// sidebar with a search, draws them in a pane under the streams, keeps them explorable only while
// zoomed out on Identify, never lets a click on one reach the stream picker, adds an "Assessment
// regions" checkbox to the Layers menu, and docks the closed sidebar's button under Layers.
const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const WWW = path.join(__dirname, "..", "www");
const { El, page: fakePage } = require(path.join(__dirname, "..", "..", "..", "libs", "staf_workbook",
                                                  "tests", "fakedom.cjs"));

// a Leaflet stand-in: maps run the init hooks, and fire() is what the late capture patches
function leaflet() {
  const hooks = [];
  class Evented {
    on(names, fn) {
      this._ev = this._ev || {};
      for (const name of names.split(" ")) (this._ev[name] = this._ev[name] || []).push(fn);
      return this;
    }
    fire(name, data) {
      const list = (this._ev && this._ev[name]) || [];
      const event = Object.assign(data || {}, { type: name, target: this });   // one event, as Leaflet's
      list.forEach((fn) => fn.call(this, event));
      return this;
    }
  }
  class Layer extends Evented {
    constructor(data, options) {
      super();
      this.data = data; this.options = options || {}; this.fronted = 0;
      this.style = this.options.style ? this.options.style() : {};
    }
    setStyle(style) { this.style = style; return this; }
    getBounds() { return { id: this.data.geometry.id, isValid: () => true }; }
    bringToFront() { this.fronted += 1; return this; }
  }
  class Tooltip {
    constructor(options) { this.options = options; this.content = ""; this.latlng = null; }
    setContent(c) { this.content = c; return this; }
    setLatLng(l) { this.latlng = l; return this; }
  }
  class Map extends Evented {
    constructor(container) {
      super();
      this._container = container; this.layers = new Set(); this.panes = {}; this.zoom = 4;
      this.moves = []; this.tooltip = null;
      // a mousemove on the container reaches the map, as Leaflet's own listeners would
      container.dispatchEvent = (event) => { this.fire(event.type); return true; };
      hooks.forEach((hook) => hook.call(this));
    }
    getContainer() { return this._container; }
    getPane(name) { return this.panes[name]; }
    createPane(name) { return (this.panes[name] = new El("div", { class: "leaflet-pane leaflet-" + name + "-pane" })); }
    getZoom() { return this.zoom; }
    zoomTo(z) { this.zoom = z; this.fire("zoomend"); }
    // a "big" region fits at zoom 5, any other at 8
    getBoundsZoom(bounds) { return bounds.id === "big" ? 5 : 8; }
    fitBounds(bounds, options) { this.moves.push(["fit", bounds.id, options]); return this; }
    setView(latlng, zoom) { this.moves.push(["view", latlng, zoom]); return this; }
    hasLayer(l) { return this.layers.has(l); }
    addLayer(l) { this.layers.add(l); return this; }
    removeLayer(l) { this.layers.delete(l); return this; }
    openTooltip(t) { this.tooltip = t; return this; }
    closeTooltip(t) { if (this.tooltip === t) this.tooltip = null; return this; }
    static addInitHook(fn) { hooks.push(fn); }
  }
  const made = [];
  return {
    Evented, Map, made,
    geoJSON: (data, options) => { const l = new Layer(data, options); made.push(l); return l; },
    tooltip: (options) => new Tooltip(options),
    point: (x, y) => ({ x, y }),
    DomEvent: { disableClickPropagation() {}, disableScrollPropagation() {}, stopPropagation(e) { e.stopped = true; } },
  };
}

// a map's container with its top-right control corner and the Layers menu's overlay list
function mapIn(root) {
  const overlays = new El("div", { class: "leaflet-control-layers-overlays" });
  const corner = new El("div", { class: "leaflet-top leaflet-right" })
    .add(new El("div", { class: "leaflet-control-layers" })
      .add(new El("div", { class: "leaflet-control-layers-list" }).add(overlays)));
  const container = new El("div", { class: "leaflet-container" })
    .add(new El("div", { class: "leaflet-control-container" }).add(corner));
  root.appendChild(new El("div", { class: "easi-map-wrap" }).add(container));
  return { container, corner, overlays };
}

// the sidebar's server markup (app.py _tool_body)
function sidebarIn(root) {
  const tabCount = new El("span", { class: "deep-cov-tab-count" });
  const tab = new El("button", { class: "deep-cov-tab", "aria-expanded": "false" })
    .add(new El("span", { class: "deep-cov-tab-label" }), tabCount);
  const close = new El("button", { class: "deep-cov-close" });
  const count = new El("span", { class: "deep-cov-count" });
  const panel = new El("div", { id: "deep-cov-panel", class: "deep-cov-panel", "data-flow-zoom": "14" })
    .add(new El("div", { class: "easi-pane-head deep-cov-head" }).add(count, close),
         new El("div", { id: "deep-cov-body", class: "deep-cov-body" }));
  const wrap = root.appendChild(new El("div", { class: "deep-cov" }).add(tab, panel));
  return { wrap, tab, tabCount, close, count, panel };
}

function page(options, L = leaflet()) {
  const p = fakePage({ ...options, dirs: [WWW, path.join(WWW, "staf")] });
  const timers = [];
  p.context.setInterval = (fn) => timers.push(fn);
  p.context.clearInterval = (id) => { timers[id - 1] = null; };
  p.context.setTimeout = (fn) => { fn(); return 0; };       // the search's short wait runs at once
  p.context.clearTimeout = () => {};
  p.context.MouseEvent = function (type) { this.type = type; };
  p.window.L = L;
  p.tick = (n = 1) => { for (let i = 0; i < n; i++) timers.slice().forEach((fn) => fn && fn()); };
  return p;
}

const FEATURES = [
  { assessmentId: "southern-coastal-plain", name: "Southern Coastal Plain", code: "75", version: 1,
    status: "Preliminary", certified: false, geometry: { type: "Polygon", id: "big" } },
  { assessmentId: "flint-hills", name: "Flint Hills", code: "28", version: 2, status: "Final",
    certified: true, geometry: { type: "Polygon", id: "flint" } },
  { assessmentId: "acadian-plains-and-hills", name: "Acadian Plains and Hills", code: "82", version: 1,
    status: "Preliminary", certified: false, geometry: { type: "Polygon", id: "acadian" } },
  { assessmentId: "southeastern-plains", name: "Southeastern Plains", code: "65", version: 1,
    status: "Preliminary", certified: false, geometry: { type: "Polygon", id: "se" } },
];

// a standalone DEEP page with its map, sidebar and the regions delivered
function deepPage({ features = FEATURES, width = 1400, storage = null } = {}) {
  const L = leaflet();
  const p = page({ bodies: [["deep", ""]] }, L);
  p.window.innerWidth = width;
  if (storage) p.window.localStorage = storage;
  p.tools.deep.classList.add("easi-shell");
  const m = mapIn(p.tools.deep), side = sidebarIn(p.tools.deep);
  p.load("staf-ns.js", "coverage.js");
  p.tick();
  const map = new L.Map(m.container);
  p.handlers.get("deep_coverage")({ features });
  const layer = (aid) => L.made.find((l) => l.data.geometry.id === features.find((f) => f.assessmentId === aid).geometry.id);
  const rows = () => p.tools.deep.querySelectorAll(".deep-cov-row");
  const row = (aid) => rows().find((r) => r.getAttribute("data-aid") === aid);
  const search = () => p.tools.deep.querySelector(".deep-cov-q");
  return { p, L, map, m, side, layer, rows, row, search };
}

// ---- finding DEEP's map and asking for the regions ----
test("beside other tools' maps, DEEP captures its own map and asks its own input", () => {
  const p = page({ bodies: [["easi", "easi"], ["deep", "deep"]], showing: "deep" });
  const easi = mapIn(p.tools.easi), deep = mapIn(p.tools.deep);
  sidebarIn(p.tools.deep);
  p.load("staf-ns.js", "coverage.js");
  p.tick();                                        // the init hook goes in
  new p.window.L.Map(easi.container);              // EASI's map is made first
  new p.window.L.Map(deep.container);
  assert.equal(p.window.__deepMap.getContainer(), deep.container);
  p.tick();
  assert.ok(p.names().includes("deep-coverage_ready"));
  assert.ok(!p.names().includes("coverage_ready"));
  assert.ok(p.window.__deepMap.getPane("deep-regions"), "the regions get their own pane");
});

test("a Leaflet swapped in later (EASI's Nationwide viewer) is hooked too", () => {
  const p = page({ bodies: [["easi", "easi"], ["deep", "deep"]], showing: "deep" });
  const deep = mapIn(p.tools.deep);
  p.load("staf-ns.js", "coverage.js");
  p.tick();                                        // hooks the first Leaflet
  const other = leaflet();
  p.window.L = other;
  new other.Map(deep.container);                   // made before anything hooked this Leaflet
  assert.equal(p.window.__deepMap, undefined);
  p.tick();                                        // the new Leaflet is hooked and the late capture runs
  assert.equal(p.window.__deepMap.getContainer(), deep.container);
  p.handlers.get("deep_coverage")({ features: FEATURES });
  assert.equal(other.made.length, FEATURES.length, "drawn with the Leaflet that made the map");
});

test("DEEP shown long after the page loaded still finds its map and asks for the regions", () => {
  const p = page({ bodies: [["sfari", "sfari"], ["deep", "deep"]], showing: "sfari" });
  sidebarIn(p.tools.deep);
  p.load("staf-ns.js", "coverage.js");
  p.tick(200);                                     // every retry runs out while DEEP is hidden
  assert.deepEqual(p.names(), [], "no tool is asked for its regions before it is shown");
  const before = p.names().length;
  const deep = mapIn(p.tools.deep);                // DEEP's tool starts: its map arrives
  new p.window.L.Map(deep.container);
  p.html.setAttribute("data-staf-tool", "deep");   // what the shell's switch does, then it says so
  p.fire("staf:tool-shown", p.document, { detail: { tool: "deep" } });
  p.tick();
  assert.equal(p.window.__deepMap.getContainer(), deep.container);
  assert.ok(p.names().slice(before).includes("deep-coverage_ready"));
});

test("on its own page DEEP asks with its plain input name and takes the first map", () => {
  const p = page({ bodies: [["deep", ""]] });
  const deep = mapIn(p.tools.deep);
  p.load("staf-ns.js", "coverage.js");
  p.tick();
  new p.window.L.Map(deep.container);
  assert.equal(p.window.__deepMap.getContainer(), deep.container);
  assert.ok(p.names().includes("coverage_ready"));
});

// ---- the sidebar ----
test("one line per region, sorted by name, with its version and Final only when certified", () => {
  const d = deepPage();
  const names = d.rows().map((r) => r.querySelector(".deep-cov-name").textContent);
  assert.deepEqual(names, ["Acadian Plains and Hills", "Flint Hills", "Southeastern Plains", "Southern Coastal Plain"]);
  const flint = d.row("flint-hills");
  assert.equal(flint.querySelector(".deep-cov-ver").textContent, "v2");
  assert.ok(flint.querySelector(".deep-cov-final"));
  assert.equal(d.row("southern-coastal-plain").querySelector(".deep-cov-final"), null);
  assert.equal(d.side.count.textContent, "4");
  assert.equal(d.search().getAttribute("placeholder"), "Search ecoregions");
  assert.ok(d.search().hasAttribute("data-shiny-no-bind-input"), "the search is never a Shiny input");
  assert.equal(d.search().id, "", "no id: nothing for Shiny to bind");
  assert.ok(d.side.wrap.classList.contains("is-ready"));
});

test("the search narrows the list and the map; digits match the Level III code exactly", () => {
  const d = deepPage();
  d.search().value = "plain";
  d.search().fire("input");
  const shown = () => d.rows().filter((r) => !r.hidden).map((r) => r.getAttribute("data-aid"));
  assert.deepEqual(shown(), ["acadian-plains-and-hills", "southeastern-plains", "southern-coastal-plain"]);
  assert.equal(d.side.count.textContent, "3 of 4");
  assert.equal(d.side.tabCount.textContent, "3 of 4");
  assert.ok(!d.map.hasLayer(d.layer("flint-hills")) && d.map.hasLayer(d.layer("southeastern-plains")));
  d.search().value = "65";
  d.search().fire("input");
  assert.deepEqual(shown(), ["southeastern-plains"]);
  d.search().value = "6";                            // not a prefix match: 6 is no region's code
  d.search().fire("input");
  assert.deepEqual(shown(), []);
  const empty = d.p.tools.deep.querySelector(".deep-cov-empty");
  assert.ok(!empty.hidden && empty.textContent.startsWith("No ecoregion matches"));
  d.search().fire("keydown", { key: "Escape" });     // Esc clears the search first
  assert.equal(shown().length, 4);
  assert.equal(d.side.count.textContent, "4");
});

test("Enter goes to the first match; a row click selects its region and fits the map to it", () => {
  const d = deepPage();
  d.search().value = "flint";
  d.search().fire("input");
  d.search().fire("keydown", { key: "Enter" });
  assert.deepEqual(d.map.moves.at(-1).slice(0, 2), ["fit", "flint"]);
  assert.ok(d.row("flint-hills").classList.contains("is-selected"));
  d.row("acadian-plains-and-hills").fire("click");
  const [kind, id, options] = d.map.moves.at(-1);
  assert.equal(kind + id, "fitacadian");
  assert.equal(options.maxZoom, 13, "a fit never lands in the stream zoom");
  assert.ok(options.paddingTopLeft[1] >= 56, "clear of the zoom cue");
  assert.ok(d.row("acadian-plains-and-hills").classList.contains("is-selected"));
  assert.ok(!d.row("flint-hills").classList.contains("is-selected"));
  assert.equal(d.layer("acadian-plains-and-hills").style.color, "#2f4b7c");
});

test("hovering a row or a region highlights both, and the map names the region", () => {
  const d = deepPage();
  d.row("flint-hills").fire("mouseenter");
  assert.equal(d.layer("flint-hills").style.color, "#2f4b7c");
  assert.equal(d.map.tooltip, null, "a row hovers the region without a tooltip");
  d.row("flint-hills").fire("mouseleave");
  d.layer("southern-coastal-plain").fire("mouseover", { latlng: [30, -84] });
  assert.ok(d.row("southern-coastal-plain").classList.contains("is-hover"));
  assert.ok(d.map.tooltip.content.includes("Southern Coastal Plain") && d.map.tooltip.content.includes("v1 · Preliminary"));
  d.layer("southern-coastal-plain").fire("mouseout");
  assert.equal(d.map.tooltip, null);
  assert.ok(!d.row("southern-coastal-plain").classList.contains("is-hover"));
});

// ---- the regions on the map ----
test("regions draw in their own pane under the streams, explorable, never bubbling a click", () => {
  const d = deepPage();
  const pane = d.map.getPane("deep-regions");
  assert.equal(pane.style.zIndex, 380);
  const lyr = d.layer("flint-hills");
  assert.equal(lyr.options.pane, "deep-regions");
  assert.equal(lyr.options.interactive, true);
  assert.equal(lyr.options.bubblingMouseEvents, false);
  assert.equal(lyr.style.fillColor, "#6b6459");
  assert.ok(!pane.classList.contains("is-passive"));
  const click = { latlng: [37, -96] };
  lyr.fire("click", click);
  assert.equal(click.stopped, true, "the stream picker never hears a region click");
});

test("a click fits a region bigger than the view, then drills in two levels at the click", () => {
  const d = deepPage();
  d.layer("southern-coastal-plain").fire("click", { latlng: [31, -83] });
  assert.deepEqual(d.map.moves.at(-1).slice(0, 2), ["fit", "big"]);   // zoom 4 -> the region (5)
  d.map.zoom = 9;                                   // inside it now
  d.layer("southern-coastal-plain").fire("click", { latlng: [31, -83] });
  assert.deepEqual(d.map.moves.at(-1), ["view", [31, -83], 11]);
  d.map.zoom = 13;
  d.layer("southern-coastal-plain").fire("click", { latlng: [31, -83] });
  assert.deepEqual(d.map.moves.at(-1), ["view", [31, -83], 14]);     // never past the stream zoom
  assert.ok(d.row("southern-coastal-plain").classList.contains("is-selected"));
});

test("at the stream zoom, or off Identify, regions go passive: dashed, inert, no tooltip", () => {
  const d = deepPage();
  const pane = d.map.getPane("deep-regions"), lyr = d.layer("flint-hills");
  lyr.fire("mouseover", { latlng: [38, -96] });
  assert.ok(d.map.tooltip);
  d.map.zoomTo(14);
  assert.ok(pane.classList.contains("is-passive"));
  assert.equal(d.map.tooltip, null, "a tooltip never sticks across the threshold");
  assert.equal(lyr.style.fillOpacity, 0);
  assert.equal(lyr.style.dashArray, "6 5");
  lyr.fire("mouseover", { latlng: [38, -96] });
  assert.equal(d.map.tooltip, null);
  const moves = d.map.moves.length;
  lyr.fire("click", { latlng: [38, -96] });
  assert.equal(d.map.moves.length, moves, "a passive region does nothing with a click");
  d.map.zoomTo(10);
  assert.ok(!pane.classList.contains("is-passive"));
  d.p.handlers.get("deep_coverage_current")({ assessmentId: null, identify: false });
  assert.ok(pane.classList.contains("is-passive"), "Basin and later: passive at any zoom");
});

test("the assessment in use is marked and shown; a link fits its region once", () => {
  const d = deepPage();
  const current = d.p.handlers.get("deep_coverage_current");
  current({ assessmentId: "flint-hills", identify: true, focus: true });
  assert.ok(d.row("flint-hills").classList.contains("is-in-use"));
  assert.ok(d.row("flint-hills").classList.contains("is-selected"));
  assert.deepEqual(d.map.moves.at(-1).slice(0, 2), ["fit", "flint"]);
  const moves = d.map.moves.length;
  current({ assessmentId: "flint-hills", identify: false, focus: false });
  assert.equal(d.map.moves.length, moves, "only a link moves the map");
  d.search().value = "acadian";
  d.search().fire("input");
  assert.ok(d.map.hasLayer(d.layer("flint-hills")), "the region in use stays drawn whatever the search");
  current({ assessmentId: null, identify: true });
  assert.ok(!d.row("flint-hills").classList.contains("is-in-use"));
  assert.ok(!d.row("flint-hills").classList.contains("is-selected"));
});

test("a current state that comes before the regions waits for them", () => {
  const L = leaflet();
  const p = page({ bodies: [["deep", ""]] }, L);
  p.tools.deep.classList.add("easi-shell");
  const m = mapIn(p.tools.deep);
  sidebarIn(p.tools.deep);
  p.load("staf-ns.js", "coverage.js");
  p.tick();
  const map = new L.Map(m.container);
  p.handlers.get("deep_coverage_current")({ assessmentId: "flint-hills", identify: true, focus: true });
  assert.deepEqual(map.moves, []);
  p.handlers.get("deep_coverage")({ features: FEATURES });
  assert.deepEqual(map.moves.at(-1).slice(0, 2), ["fit", "flint"]);
});

test("the Layers menu shows and hides the regions, and its row survives a rebuild", () => {
  const d = deepPage();
  const box = () => d.m.overlays.querySelector(".deep-regions-toggle");
  assert.ok(box() && box().checked);
  assert.ok(box().hasAttribute("data-shiny-no-bind-input"));
  box().checked = false;
  box().fire("change");
  assert.ok(FEATURES.every((f) => !d.map.hasLayer(d.layer(f.assessmentId))));
  d.row("flint-hills").fire("click");               // a chosen region always shows
  assert.ok(box().checked && d.map.hasLayer(d.layer("flint-hills")));
  d.m.overlays.querySelector(".deep-regions-control").remove();      // ipyleaflet rebuilt its list
  d.p.fire("staf:tool-shown", d.p.document, { detail: { tool: "deep" } });
  assert.ok(box() && box().checked);
});

// ---- open, closed, remembered ----
test("closed, the sidebar is a button in the map's controls, under Layers, with the count", () => {
  const d = deepPage({ width: 1100 });              // narrower than 1280: starts closed
  const shell = d.p.tools.deep;
  assert.equal(d.side.tab.parentNode, d.m.corner, "docked in the map's top-right controls");
  assert.ok(d.side.tab.classList.contains("leaflet-control"));
  assert.ok(shell.classList.contains("deep-cov-ready"), "shown once the regions are in");
  assert.equal(d.side.tabCount.textContent, "4");
  d.p.fire("click", d.side.tab);                    // it opens the sidebar from there
  assert.ok(shell.classList.contains("deep-cov-open"));
  d.search().value = "plain";
  d.search().fire("input");
  d.p.fire("click", d.side.close);
  assert.ok(!shell.classList.contains("deep-cov-open"));
  assert.equal(d.side.tabCount.textContent, "3 of 4", "closed, it still says a search filters the map");
});

test("the button goes back into the controls after they are rebuilt", () => {
  const d = deepPage({ width: 1100 });
  d.side.tab.remove();                              // the map's controls were rebuilt without it
  d.p.fire("staf:tool-shown", d.p.document, { detail: { tool: "deep" } });
  assert.equal(d.side.tab.parentNode, d.m.corner);
  d.p.fire("click", d.side.tab);
  assert.ok(d.p.tools.deep.classList.contains("deep-cov-open"));
  d.p.fire("click", d.side.close);
  assert.equal(d.side.tab.getAttribute("aria-expanded"), "false");
});

test("the tab opens the sidebar and the close button shuts it; the choice is remembered", () => {
  const store = {};
  const d = deepPage({ width: 1100 });              // narrower than 1280: starts closed
  const shell = d.p.tools.deep;
  assert.ok(!shell.classList.contains("deep-cov-open"));
  d.p.window.localStorage = { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; } };
  d.p.fire("click", d.side.tab);
  assert.ok(shell.classList.contains("deep-cov-open"));
  assert.equal(d.side.tab.getAttribute("aria-expanded"), "true");
  assert.equal(store["staf.deep.regionsOpen"], "1");
  d.p.fire("click", d.side.close);
  assert.ok(!shell.classList.contains("deep-cov-open"));
  assert.equal(store["staf.deep.regionsOpen"], "0");
  d.p.fire("click", d.side.tab);
  d.search().fire("keydown", { key: "Escape" });    // an empty search: Esc closes
  assert.ok(!shell.classList.contains("deep-cov-open"));
});

test("wide screens open it at first; on a phone choosing a region hands the map back", () => {
  const wide = deepPage({ width: 1400 });
  assert.ok(wide.p.tools.deep.classList.contains("deep-cov-open"));
  const stored = { getItem: () => "1", setItem() {} };                 // left open on a desktop
  assert.ok(deepPage({ width: 1100, storage: stored }).p.tools.deep.classList.contains("deep-cov-open"));
  const phone = deepPage({ width: 400, storage: stored });
  assert.ok(!phone.p.tools.deep.classList.contains("deep-cov-open"), "never open at first on a phone");
  const shell = phone.p.tools.deep;
  assert.ok(!shell.classList.contains("deep-cov-open"));
  phone.p.fire("click", phone.side.tab);
  phone.row("flint-hills").fire("click");
  assert.ok(!shell.classList.contains("deep-cov-open"));
  assert.deepEqual(phone.map.moves.at(-1).slice(0, 2), ["fit", "flint"]);
});

test("an empty library says so", () => {
  const d = deepPage({ features: [] });
  assert.equal(d.p.tools.deep.querySelector(".deep-cov-empty").textContent, "No published assessments yet");
  assert.equal(d.side.count.textContent, "0");
});
