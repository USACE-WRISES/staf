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
    removeLayer(id) { this.layers = this.layers.filter(layer => layer.id !== id); }
    removeSource(id) { delete this.sources[id]; }
    getCenter() { const c = this.options.center; return {lng: c[0], lat: c[1]}; }
    getZoom() { return this.options.zoom; }
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
  const frames = new Map();
  let frameId = 0;
  const inputs = [];
  const status = { textContent: "", hidden: true };
  const zoomNote = { textContent: "Zoom in to see screened reaches.", hidden: true };
  const loadingText = { textContent: "Loading screening tiles…" };
  const loading = {
    hidden: true, textContent: "",
    querySelector(selector) { return selector === ".easi-viewer-loading-text" ? loadingText : null; },
  };
  const mapDiv = { id: "easi-viewer-map" };
  const document = {
    getElementById(id) {
      if (id === "easi-viewer-map") return mapDiv;
      if (id === "easi-viewer-status") return status;
      if (id === "easi-viewer-zoom-note") return zoomNote;
      if (id === "easi-viewer-loading") return loading;
      return null;
    },
    addEventListener() {},
  };
  const maplibregl = fakeMaplibre();
  const logged = [];
  const window = {
    console: { error(message) { logged.push(message); }, warn() {}, log() {} },
    location: { href: "https://example.test/app/" },
    Shiny: {
      // These legacy assertions intentionally exercise the manual Standard renderer.
      addCustomMessageHandler(name, fn) { handlers.set(name, name === "easi-viewer-init" ? config => fn({renderer: "standard", minzoom: 7, maxzoom: 12, zoom: 7, ...config}) : fn); },
      setInputValue(name, value, opts) { inputs.push({ name, value, opts }); },
    },
    maplibregl,
    URL,
    setTimeout, clearTimeout,
    requestAnimationFrame(fn) { frames.set(++frameId, fn); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); },
  };
  window.window = window;
  const context = vm.createContext({ window, document, URL, setTimeout, clearTimeout, Date, Number, String, Math, Infinity, encodeURIComponent, console });
  for (const filename of ["viewer-standard.js", "viewer.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "www", filename), "utf8"), context);
  }
  return { handlers, inputs, status, zoomNote, loading, loadingText, logged, maplibregl, window, mapDiv,
    flushFrames() { const queued = [...frames.values()]; frames.clear(); queued.forEach(fn => fn()); } };
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function loadedViewer(vpus) {
  const h = harness();
  h.handlers.get("easi-viewer-init")({ routeBase: "session/abc/dynamic_route/national-tiles?nonce=1",
                                       vpus: vpus || ["02"], minzoom: 7, maxzoom: 12 });
  const map = h.maplibregl.maps[0];
  assert.equal(map.options.style.sources.basemap.maxzoom, 16, "USGS imagery overzooms above its native coverage");
  map.loaded = true;
  map.fire("style.load");
  return { h, map };
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
  init({ routeBase: "session/abc/dynamic_route/national-tiles?nonce=1", vpus: ["02", "05"], minzoom: 7, maxzoom: 12 });
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
  h.flushFrames();
  same(map.featureState, [{ target: { source: "easi-02", sourceLayer: "flowlines", id: 11 }, state: { hover: true } }]);
  assert.equal(map.canvas.style.cursor, "pointer");
  assert.match(h.window.EASIViewer.state.popup.html, /Mink Brook/);
  // the same reach again: no extra state writes
  map.fire("mousemove", { point: { x: 101, y: 100 }, lngLat: { lng: 1.01, lat: 1 } });
  h.flushFrames();
  assert.equal(map.featureState.length, 1);
  // a different reach: the old one is cleared, the new one set
  map.features = [reach(12, "Functioning", [[0.9, 1.0], [1.1, 1.0]])];
  map.fire("mousemove", { point: { x: 100, y: 100 }, lngLat: { lng: 1, lat: 1 } });
  h.flushFrames();
  same(map.featureState.slice(1), [
    { target: { source: "easi-02", sourceLayer: "flowlines", id: 11 }, state: { hover: false } },
    { target: { source: "easi-02", sourceLayer: "flowlines", id: 12 }, state: { hover: true } }]);
  // nothing near the cursor: cleared, cursor back, popup gone
  map.features = [];
  map.fire("mousemove", { point: { x: 500, y: 500 }, lngLat: { lng: 5, lat: 5 } });
  h.flushFrames();
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

test("a screening source that keeps loading shows the cue after a short delay and idle hides it", async () => {
  const { h, map } = loadedViewer();
  const delay = h.window.EASIViewer.SHOW_DELAY_MS + 80;
  assert.ok(map.handlers["sourcedataloading"] && map.handlers["idle"] && map.handlers["error"], "cue handlers registered");
  map.fire("sourcedataloading", { dataType: "source", sourceId: "basemap", tile: {} });
  await wait(delay);
  assert.equal(h.loading.hidden, true, "the basemap never shows the cue");
  map.fire("sourcedataloading", { dataType: "source", sourceId: "easi-02", tile: {} });
  assert.equal(h.loading.hidden, true, "nothing before the delay");
  await wait(delay);
  assert.equal(h.loading.hidden, false);
  assert.equal(h.loadingText.textContent, "Loading screening tiles…");
  map.fire("idle");
  assert.equal(h.loading.hidden, true, "idle hides the cue");
  // a tile that arrives before the delay (browser cache) never flashes the cue
  map.fire("sourcedataloading", { dataType: "source", sourceId: "easi-coverage" });
  map.fire("idle");
  await wait(delay);
  assert.equal(h.loading.hidden, true);
});

test("a failed screening tile leaves a note once the map is idle; teardown clears it", async () => {
  const { h, map } = loadedViewer();
  map.fire("sourcedataloading", { dataType: "source", sourceId: "easi-02", tile: {} });
  map.fire("error", { error: new Error("502"), sourceId: "easi-02", tile: {} });
  map.fire("error", { error: new Error("style"), sourceId: "basemap" });        // not a tile of ours
  assert.equal(h.logged.length, 1, "other errors still reach the console");
  map.fire("idle");
  assert.equal(h.loading.hidden, false);
  assert.equal(h.loadingText.textContent, "Some screening tiles did not load, use Refresh to retry.");
  h.handlers.get("easi-viewer-teardown")({});
  assert.equal(h.loading.hidden, true);
  assert.equal(h.window.EASIViewer.state.loading.failed, 0);
});

test("generation identities guard refresh, report picks, status, and unavailable data", () => {
  const h = harness(), init = h.handlers.get("easi-viewer-init");
  const config = {routeBase: "r", vpus: ["02"], datasetKey: "alternative-2:hash", generation: 4};
  init(config);
  const map = h.maplibregl.maps[0]; map.fire("style.load");
  assert.match(map.sources["easi-02"].tiles[0], /datasetKey=alternative-2%3Ahash&generation=4/);
  init({...config, datasetKey: "older", generation: 3});
  assert.equal(h.window.EASIViewer.state.config.datasetKey, config.datasetKey);
  map.features = [reach(123, "Functioning", [[0.9, 1], [1.1, 1]])];
  map.fire("click", {point: {x:100,y:100}});
  assert.equal(h.inputs[0].value.datasetKey, config.datasetKey);
  assert.equal(h.inputs[0].value.generation, 4);
  h.handlers.get("easi-viewer-status")({busy:false, datasetKey: "older", generation: 3});
  assert.equal(h.status.hidden, false);
  init({...config, generation:5, available:false, error:"Bundle is incomplete."});
  assert.equal(map.sources["easi-02"], undefined);
  assert.equal(map.sources["easi-coverage"], undefined);
  assert.equal(h.status.textContent, "Bundle is incomplete.");
  h.handlers.get("easi-viewer-status")({busy:false, datasetKey:config.datasetKey, generation:5});
  assert.equal(h.status.textContent, "Bundle is incomplete.");
  assert.equal(h.status.hidden, false);
  assert.equal(h.window.EASIViewer.nearestReach(map, {x:100,y:100}), null);
  h.handlers.get("easi-viewer-teardown")({});
});

test("hover is throttled and suspended through gestures; hit radius is circular", () => {
  const {h,map} = loadedViewer();
  map.features = [reach(1, "Functioning", [[0.9, 1], [1.1, 1]])];
  for (let n=0; n<10; n++) map.fire("mousemove", {point:{x:100,y:100},lngLat:{lng:1,lat:1}});
  assert.equal(map.queries.length, 0);
  h.flushFrames(); assert.equal(map.queries.length, 1);
  map.fire("movestart");
  map.fire("mousemove", {point:{x:100,y:100},lngLat:{lng:1,lat:1}});
  map.fire("click", {point:{x:100,y:100}});
  h.flushFrames(); assert.equal(map.queries.length, 1); assert.equal(h.inputs.length, 0);
  map.fire("moveend");
  map.features = [reach(2, "Functioning", [[1.07, 1.07], [1.08, 1.08]])];
  assert.equal(h.window.EASIViewer.nearestReach(map, {x:100,y:100}), null);
  h.handlers.get("easi-viewer-teardown")({});
});

test("Compatibility is the default and manual renderer switching preserves logical camera", () => {
  const h = harness(); let passedCamera = null, destroyed = false;
  h.window.EASIViewerRenderers.compatibility = (ctx, config, camera) => {
    passedCamera = camera; ctx.state.map = {getContainer:()=>h.mapDiv};
    return {camera:()=>({center:[-80,35],zoom:9}), refresh(){}, nearest(){}, destroy(){destroyed=true;}};
  };
  h.window.EASIViewer.init({routeBase:"r",vpus:[],datasetKey:"d",generation:1,minzoom:7,maxzoom:12});
  assert.equal(h.window.EASIViewer.state.renderer, "compatibility");
  assert.equal(passedCamera, null);
  h.handlers.get("easi-viewer-renderer")({renderer:"standard"});
  assert.equal(destroyed, true);
  const map = h.maplibregl.maps[0]; same(map.options.center, [-80,35]); assert.equal(map.options.zoom,9);
  h.handlers.get("easi-viewer-renderer")({renderer:"compatibility"});
  same(passedCamera, {center:[-80,35],zoom:9}); assert.equal(map.removed,true);
  h.handlers.get("easi-viewer-teardown")({});
});

test("overview has coverage but no reach sources until the shared minimum; zoom note is independent", () => {
  const h = harness(), init = h.handlers.get("easi-viewer-init");
  const config = {routeBase:"r", vpus:["02"], minzoom:8, maxzoom:11, zoom:4};
  init(config);
  const map = h.maplibregl.maps[0]; map.fire("style.load");
  assert.equal(map.options.minZoom,3); assert.equal(map.options.maxZoom,16);
  assert.ok(map.sources["easi-coverage"]);
  assert.equal(map.sources["easi-02"],undefined);
  assert.equal(h.zoomNote.hidden,false);
  h.handlers.get("easi-viewer-status")({busy:true});
  map.options.zoom = 7.99; map.fire("zoom");
  assert.equal(map.sources["easi-02"],undefined);
  assert.equal(h.status.textContent,"Preparing report…");
  map.options.zoom = 8; map.fire("zoom");
  assert.equal(map.sources["easi-02"].minzoom,8);
  assert.equal(map.sources["easi-02"].maxzoom,11);
  assert.ok(map.layers.filter(layer => layer["source-layer"] === "flowlines").every(layer => layer.minzoom === 8));
  assert.equal(h.zoomNote.hidden,true);
  map.options.zoom = 16; map.fire("zoom"); assert.ok(map.sources["easi-02"]);
  map.options.zoom = 7; map.fire("zoom");
  assert.equal(map.sources["easi-02"],undefined); assert.equal(h.zoomNote.hidden,false);
  init({...config, minzoom:7, maxzoom:10});
  assert.equal(map.sources["easi-02"].maxzoom,10); assert.equal(h.zoomNote.hidden,true);
  init({...config, available:false});
  assert.equal(h.zoomNote.hidden,true); assert.equal(map.sources["easi-coverage"],undefined);
  init(config); assert.equal(h.zoomNote.hidden,false);
  h.handlers.get("easi-viewer-teardown")({}); assert.equal(h.zoomNote.hidden,true);
  map.fire("zoom"); assert.equal(h.zoomNote.hidden,true);
});

test("malformed zoom config shows unavailable and creates no reach sources", () => {
  for (const range of [{minzoom:null}, {minzoom:6}, {minzoom:7.5}, {maxzoom:6}, {maxzoom:32}]) {
    const h=harness(); h.handlers.get("easi-viewer-init")({routeBase:"r",vpus:["02"],...range});
    const map=h.maplibregl.maps[0]; map.fire("style.load");
    assert.equal(map.sources["easi-02"],undefined); assert.equal(h.zoomNote.hidden,true);
    assert.match(h.status.textContent,/invalid tile zoom range/);
    h.handlers.get("easi-viewer-teardown")({});
  }
});
