"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// A tiny MapLibre stand-in: records sources/layers, event handlers, feature
// state, and answers queryRenderedFeatures from a list the test sets.
function fakeMaplibre() {
  const maps = [];
  class Popup {
    constructor() { this.removed = false; }
    setLngLat() { return this; }
    setHTML(html) { this.html = html; return this; }
    addTo() { this.added = true; return this; }
    remove() { this.removed = true; }
  }
  class NavigationControl {}
  class Map {
    constructor(options) {
      this.options = options; this.sources = {}; this.layers = []; this.handlers = {};
      this.loaded = false; this.removed = false; this.canvas = { style: {} };
      this.features = []; this.queries = []; this.featureState = [];
      maps.push(this);
    }
    addControl() {}
    on(name, layerOrFn, fn) {
      const key = fn ? name + ":" + layerOrFn : name;
      (this.handlers[key] = this.handlers[key] || []).push(fn || layerOrFn);
    }
    once(name, fn) { this.on(name, fn); }
    fire(name, event) { (this.handlers[name] || []).forEach((h) => h(event)); }
    isStyleLoaded() { return this.loaded; }
    getSource(id) { return this.sources[id]; }
    addSource(id, def) { this.sources[id] = Object.assign({ setData(d) { this.data = d; } }, def); }
    addLayer(def) { this.layers.push(def); }
    getCanvas() { return this.canvas; }
    getContainer() { return this.options.container; }
    // screen space equals lng/lat times 100 for the tests
    project(c) { return { x: c[0] * 100, y: c[1] * 100 }; }
    queryRenderedFeatures(box, opts) { this.queries.push({ box, opts }); return this.features; }
    setFeatureState(target, state) { this.featureState.push({ target, state }); }
    remove() { this.removed = true; }
  }
  return { Map, Popup, NavigationControl, maps };
}

function harness() {
  const handlers = new Map();
  const inputs = [];
  const status = { textContent: "", hidden: true };
  const mapDiv = { id: "easi-viewer-map" };
  const document = {
    getElementById(id) {
      if (id === "easi-viewer-map") return mapDiv;
      if (id === "easi-viewer-status") return status;
      return null;
    },
    addEventListener() {},
  };
  const maplibregl = fakeMaplibre();
  const window = {
    location: { href: "https://example.test/app/" },
    Shiny: {
      addCustomMessageHandler(name, fn) { handlers.set(name, fn); },
      setInputValue(name, value, opts) { inputs.push({ name, value, opts }); },
    },
    maplibregl,
    URL,
    setTimeout, clearTimeout,
  };
  window.window = window;
  const context = vm.createContext({ window, document, URL, setTimeout, clearTimeout, Date, Number, String, Math, Infinity, encodeURIComponent, console });
  const src = fs.readFileSync(path.join(__dirname, "..", "www", "viewer.js"), "utf8");
  vm.runInContext(src, context);
  return { handlers, inputs, status, maplibregl, window, mapDiv };
}

// objects built inside the script's VM have another realm's prototypes, so
// strict deepEqual rejects them; compare their JSON instead
function same(actual, expected) {
  assert.equal(JSON.stringify(actual), JSON.stringify(expected));
}

// the output expression of a zoom stop in an interpolate expression
function lineWidthAt(curve, zoom) {
  for (let i = 3; i < curve.length; i += 2) if (curve[i] === zoom) return curve[i + 1];
  return null;
}

function reach(comid, band, coords, extra) {
  return Object.assign({
    id: comid, source: "easi-02", sourceLayer: "flowlines",
    properties: Object.assign({ comid: String(comid), band: band, huc4: "0208" }, extra || {}),
    geometry: { type: "LineString", coordinates: coords },
  });
}

test("init builds one map, adds coverage and a glow plus a line layer per region", () => {
  const h = harness();
  const init = h.handlers.get("easi-viewer-init");
  assert.ok(init, "init handler registered");
  init({ routeBase: "session/abc/dynamic_route/national-tiles?nonce=1", vpus: ["02", "05"], minzoom: 4, maxzoom: 12 });
  assert.equal(h.maplibregl.maps.length, 1);
  const map = h.maplibregl.maps[0];
  map.loaded = true;
  map.fire("style.load");
  assert.ok(map.sources["easi-coverage"], "coverage source");
  assert.ok(map.sources["easi-02"] && map.sources["easi-05"], "region sources");
  same(map.sources["easi-02"].promoteId, { flowlines: "comid" });
  const tiles = map.sources["easi-02"].tiles[0];
  assert.equal(tiles, "https://example.test/app/session/abc/dynamic_route/national-tiles?nonce=1&vpu=02&z={z}&x={x}&y={y}");
  // a second init (Refresh) with a new region adds it without a second map
  init({ routeBase: "session/abc/dynamic_route/national-tiles?nonce=1", vpus: ["02", "05", "06"] });
  assert.equal(h.maplibregl.maps.length, 1);
  assert.ok(map.sources["easi-06"]);
  const lineLayers = map.layers.filter((l) => l.type === "line" && l["source-layer"] === "flowlines");
  assert.equal(lineLayers.length, 6);                      // a glow under every line layer
  const glow = map.layers.find((l) => l.id === "easi-02-glow");
  const lines = map.layers.find((l) => l.id === "easi-02-lines");
  assert.ok(map.layers.indexOf(glow) < map.layers.indexOf(lines), "the glow draws under the line");
  assert.equal(glow.paint["line-color"], "#ffffff");
  same(glow.paint["line-opacity"], ["case", ["boolean", ["feature-state", "hover"], false], 0.9, 0]);
  assert.equal(glow.minzoom, 7);
  // zoom interpolation outermost (MapLibre rejects zoom inside case); the
  // hover factor sits inside every stop
  assert.equal(lines.paint["line-width"][0], "interpolate");
  assert.equal(JSON.stringify(lines.paint["line-width"][1]), JSON.stringify(["linear"]));
  same(lines.paint["line-width"][4][1], ["case", ["boolean", ["feature-state", "hover"], false], 1.8, 1]);
  assert.equal(glow.paint["line-width"][0], "interpolate");
  assert.equal(lineWidthAt(lines.paint["line-width"], 12).length, 3);
  same(h.window.EASIViewer.state.lineLayers, ["easi-02-lines", "easi-05-lines", "easi-06-lines"]);
  // the handlers live on the map, not on the layers
  assert.equal(map.handlers["click"].length, 1);
  assert.equal(map.handlers["mousemove"].length, 1);
  assert.equal(map.handlers["click:easi-02-lines"], undefined);
});

test("a click picks the nearest reach within the hit box and posts viewer_pick", () => {
  const h = harness();
  h.handlers.get("easi-viewer-init")({ routeBase: "r?nonce=1", vpus: ["02"] });
  const map = h.maplibregl.maps[0];
  map.loaded = true;
  map.fire("style.load");
  // two reaches near the cursor at (1.00, 1.00) screen (100, 100): the second is nearer
  map.features = [reach(1, "Functioning", [[0.9, 1.06], [1.1, 1.06]]),
                  reach(8566387, "Functioning", [[0.9, 1.02], [1.1, 1.02]], { name: "Rivanna River" })];
  map.fire("click", { point: { x: 100, y: 100 }, lngLat: { lng: 1, lat: 1 } });
  assert.equal(map.queries.length, 1);
  same(map.queries[0].box, [[92, 92], [108, 108]]);
  same(map.queries[0].opts, { layers: ["easi-02-lines"] });
  assert.equal(h.inputs.length, 1);
  assert.equal(h.inputs[0].name, "viewer_pick");
  same({ comid: h.inputs[0].value.comid, huc4: h.inputs[0].value.huc4 }, { comid: 8566387, huc4: "0208" });
  assert.equal(h.inputs[0].opts.priority, "event");
  assert.equal(h.status.textContent, "Preparing report…");
  // a pending reach only shows a note; nothing under the cursor does nothing
  map.features = [reach(7, "pending", [[0.9, 1.0], [1.1, 1.0]])];
  map.fire("click", { point: { x: 100, y: 100 }, lngLat: { lng: 1, lat: 1 } });
  assert.equal(h.inputs.length, 1);
  assert.equal(h.status.textContent, "This reach has no precomputed assessment yet.");
  map.features = [];
  map.fire("click", { point: { x: 100, y: 100 }, lngLat: { lng: 1, lat: 1 } });
  assert.equal(h.inputs.length, 1);
  assert.equal(h.handlers.has("staf-report-state"), false);   // that name belongs to report-ready.js
  h.handlers.get("easi-viewer-status")({ busy: false, requestId: 1 });
  assert.equal(h.status.hidden, true);
});

test("hovering highlights the nearest reach through feature state and clears on leave", () => {
  const h = harness();
  h.handlers.get("easi-viewer-init")({ routeBase: "r?nonce=1", vpus: ["02"] });
  const map = h.maplibregl.maps[0];
  map.loaded = true;
  map.fire("style.load");
  map.features = [reach(11, "Functioning", [[0.9, 1.0], [1.1, 1.0]], { name: "Mink Brook", eci: 0.72 })];
  map.fire("mousemove", { point: { x: 100, y: 100 }, lngLat: { lng: 1, lat: 1 } });
  same(map.featureState, [{ target: { source: "easi-02", sourceLayer: "flowlines", id: 11 }, state: { hover: true } }]);
  assert.equal(map.canvas.style.cursor, "pointer");
  assert.match(h.window.EASIViewer.state.popup.html, /Mink Brook/);
  // the same reach again: no extra state writes
  map.fire("mousemove", { point: { x: 101, y: 100 }, lngLat: { lng: 1.01, lat: 1 } });
  assert.equal(map.featureState.length, 1);
  // a different reach: the old one is cleared, the new one set
  map.features = [reach(12, "Functioning", [[0.9, 1.0], [1.1, 1.0]])];
  map.fire("mousemove", { point: { x: 100, y: 100 }, lngLat: { lng: 1, lat: 1 } });
  same(map.featureState.slice(1), [
    { target: { source: "easi-02", sourceLayer: "flowlines", id: 11 }, state: { hover: false } },
    { target: { source: "easi-02", sourceLayer: "flowlines", id: 12 }, state: { hover: true } }]);
  // nothing near the cursor: cleared, cursor back, popup gone
  map.features = [];
  map.fire("mousemove", { point: { x: 500, y: 500 }, lngLat: { lng: 5, lat: 5 } });
  same(map.featureState[3], { target: { source: "easi-02", sourceLayer: "flowlines", id: 12 }, state: { hover: false } });
  assert.equal(map.canvas.style.cursor, "");
  assert.equal(h.window.EASIViewer.state.popup.removed, true);
  assert.equal(h.window.EASIViewer.state.hover, null);
});

test("hover text carries the ECI and the three sub-indices; teardown removes the map", () => {
  const h = harness();
  const describe = h.window.EASIViewer.describe;
  const text = describe({ name: "Mink Brook", comid: 9327042, eci: 0.72, band: "Functioning", phys: 0.8, chem: 0.65, bio: 0.7, prov: true });
  assert.match(text, /Mink Brook/);
  assert.match(text, /ECI 0\.72 · Functioning/);
  assert.match(text, /Physical 0\.80 · Chemical 0\.65 · Biological 0\.70/);
  assert.match(text, /Provisional/);
  assert.match(describe({ comid: 1, band: "pending" }), /Not yet screened/);
  assert.match(describe({ name: "<b>x</b>", comid: 1, eci: 0.5, band: "Functioning-at-Risk" }), /&lt;b&gt;/);
  h.handlers.get("easi-viewer-init")({ routeBase: "r", vpus: [] });
  const map = h.maplibregl.maps[0];
  h.handlers.get("easi-viewer-teardown")({});
  assert.equal(map.removed, true);
  assert.equal(h.window.EASIViewer.state.map, null);
  same(h.window.EASIViewer.state.lineLayers, []);
});
