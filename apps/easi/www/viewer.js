/* Nationwide message bridge. Both renderers read the same stored tiles. */
(function () {
  "use strict";
  var state = { map: null, config: null, engine: null, renderer: null, epoch: 0,
    lineLayers: [], hover: null, busy: null,
    loading: { pending: false, showTimer: null, longTimer: null, safetyTimer: null, noteTimer: null, failed: 0 } };
  var BAND_COLORS = { Functioning: "#5b8fd6", "Functioning-at-Risk": "#d9b93a", "Non-Functioning": "#d6453d", pending: "#a9b1bd" };
  var COVER_COLORS = { complete: "#c8d9f2", partial: "#f5e7a6", not_started: "#eef1f5" };
  var WIDTH_STOPS = [[4, 0.4, 0.15], [8, 0.8, 0.35], [12, 1.6, 0.6], [16, 3.0, 0.9]];
  var HIT_PX = 8, SHOW_DELAY_MS = 300, LONG_MS = 6000, SAFETY_MS = 60000, NOTE_MS = 6000;
  var BASEMAP = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}";
  function escapeHtml(text) { return String(text).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function fmt(v) { return v === undefined || v === null || v === "" ? "–" : Number(v).toFixed(2); }
  function describe(props) {
    var name = props.name || "(unnamed reach)", band = props.band || "pending";
    var eci = props.eci === undefined || props.eci === null ? null : Number(props.eci);
    var lines = ["<b>" + escapeHtml(name) + "</b> · COMID " + escapeHtml(String(props.comid || ""))];
    if (band === "pending" || eci === null) lines.push("Not yet screened");
    else {
      lines.push("ECI " + eci.toFixed(2) + " · " + escapeHtml(band));
      lines.push("Physical " + fmt(props.phys) + " · Chemical " + fmt(props.chem) + " · Biological " + fmt(props.bio));
      if (props.prov === true || props.prov === "true" || props.prov === 1)
        lines.push("<span class='easi-viewer-note'>Provisional: cross-section metrics not computed</span>");
    }
    return lines.join("<br>");
  }
  function query(config, suffix) {
    var base; try { base = new URL(config.routeBase, window.location.href).href; } catch (_) { base = config.routeBase; }
    var pairs = [];
    if (config.datasetKey != null) pairs.push("datasetKey=" + encodeURIComponent(config.datasetKey));
    if (config.generation != null) pairs.push("generation=" + encodeURIComponent(config.generation));
    pairs.push(suffix); return base + (base.indexOf("?") >= 0 ? "&" : "?") + pairs.join("&");
  }
  function tileUrl(config, vpu) { return query(config, "vpu=" + encodeURIComponent(vpu) + "&z={z}&x={x}&y={y}"); }
  function metaUrl(config, name) { return query(config, "meta=" + encodeURIComponent(name)); }
  function widthAt(zoom, order) {
    order = order != null && Number.isFinite(Number(order)) ? Number(order) : 1;
    if (zoom <= WIDTH_STOPS[0][0]) return WIDTH_STOPS[0][1] + order * WIDTH_STOPS[0][2];
    for (var i = 1; i < WIDTH_STOPS.length; i++) {
      var low = WIDTH_STOPS[i - 1], high = WIDTH_STOPS[i];
      if (zoom <= high[0]) { var t = (zoom - low[0]) / (high[0] - low[0]);
        return low[1] + order * low[2] + t * (high[1] - low[1] + order * (high[2] - low[2])); }
    }
    var last = WIDTH_STOPS[WIDTH_STOPS.length - 1]; return last[1] + order * last[2];
  }
  function segmentDistance(p, a, b) {
    var dx = b.x - a.x, dy = b.y - a.y, len2 = dx * dx + dy * dy;
    var t = len2 ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2)) : 0;
    return Math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy);
  }
  function showStatus(text, ttl) {
    var el = document.getElementById("easi-viewer-status"); if (!el) return;
    el.textContent = text; el.hidden = !text;
    if (state.busy) { clearTimeout(state.busy); state.busy = null; }
    if (text && ttl) state.busy = setTimeout(function () { el.hidden = true; state.busy = null; }, ttl);
  }
  function zoomNote(zoom) {
    var el = document.getElementById("easi-viewer-zoom-note"), config = state.config;
    if (el) el.hidden = !(config && config.available !== false && Number.isFinite(zoom) && zoom < config.minzoom);
  }
  function setLoadingCue(text) {
    var el = document.getElementById("easi-viewer-loading"); if (!el) return;
    var span = el.querySelector ? el.querySelector(".easi-viewer-loading-text") : null;
    if (span) span.textContent = text; else el.textContent = text; el.hidden = false;
  }
  function hideLoadingCue() {
    ["showTimer", "longTimer", "safetyTimer"].forEach(function (key) {
      if (state.loading[key]) clearTimeout(state.loading[key]); state.loading[key] = null;
    });
    state.loading.pending = false;
    var el = document.getElementById("easi-viewer-loading"); if (el) el.hidden = true;
  }
  function loading() {
    var l = state.loading; if (l.pending) return; l.pending = true;
    if (l.noteTimer) { clearTimeout(l.noteTimer); l.noteTimer = null; }
    l.safetyTimer = setTimeout(hideLoadingCue, SAFETY_MS);
    l.showTimer = setTimeout(function () {
      l.showTimer = null; if (!l.pending) return; setLoadingCue("Loading screening tiles…");
      l.longTimer = setTimeout(function () { l.longTimer = null;
        if (l.pending) setLoadingCue("Still loading screening tiles…"); }, LONG_MS - SHOW_DELAY_MS);
    }, SHOW_DELAY_MS);
  }
  function idle() {
    var failed = state.loading.failed; state.loading.failed = 0; hideLoadingCue();
    if (!failed) return; setLoadingCue("Some screening tiles did not load, use Refresh to retry.");
    state.loading.noteTimer = setTimeout(function () { state.loading.noteTimer = null;
      if (!state.loading.pending) hideLoadingCue(); }, NOTE_MS);
  }
  function pick(props) {
    if (!props || !props.comid || !window.Shiny) return;
    if (!props.band || props.band === "pending") { showStatus("This reach has no precomputed assessment yet.", 2500); return; }
    var config = state.config || {};
    window.Shiny.setInputValue("viewer_pick", { comid: Number(props.comid), huc4: props.huc4 || null,
      name: props.name || null, datasetKey: config.datasetKey, generation: config.generation, nonce: Date.now() }, { priority: "event" });
    showStatus("Preparing report…", 0);
  }
  var shared = { BAND_COLORS: BAND_COLORS, COVER_COLORS: COVER_COLORS, WIDTH_STOPS: WIDTH_STOPS,
    HIT_PX: HIT_PX, BASEMAP: BASEMAP, tileUrl: tileUrl, metaUrl: metaUrl, describe: describe,
    widthAt: widthAt, segmentDistance: segmentDistance, escapeHtml: escapeHtml };
  function normalizeRenderer(value) { return value === "standard" ? "standard" : "compatibility"; }
  function destroyEngine() {
    state.epoch += 1; if (state.engine) state.engine.destroy();
    else if (state.map) state.map.remove();
    state.engine = null; state.map = null; state.lineLayers = []; state.hover = null; hideLoadingCue();
    zoomNote(null);
    if (state.loading.noteTimer) clearTimeout(state.loading.noteTimer);
    state.loading.noteTimer = null; state.loading.failed = 0;
    if (state.busy) clearTimeout(state.busy); state.busy = null;
  }
  function init(config) {
    if (!config || !document.getElementById("easi-viewer-map")) return;
    var previous = state.config;
    if (previous && Number.isFinite(config.generation) && Number.isFinite(previous.generation) && config.generation < previous.generation) return;
    if (config.available !== false && (!Number.isInteger(config.minzoom) || !Number.isInteger(config.maxzoom) ||
        config.minzoom < 4 || config.minzoom > 16 || config.maxzoom < config.minzoom || config.maxzoom > 31))
      config = Object.assign({}, config, { available: false, error: "The national dataset has an invalid tile zoom range. Refresh Nationwide screening." });
    var renderer = normalizeRenderer(config.renderer || state.renderer);
    var camera = state.engine && state.engine.camera();
    var same = state.engine && state.renderer === renderer && state.map &&
      state.map.getContainer() === document.getElementById("easi-viewer-map");
    state.config = Object.assign({}, config, { renderer: renderer }); state.renderer = renderer;
    if (config.available === false) showStatus(config.error || "This Nationwide dataset is unavailable.", 0);
    else showStatus("", 0);
    if (same) { state.engine.refresh(state.config); return; }
    destroyEngine();
    var factory = (window.EASIViewerRenderers || {})[renderer];
    if (!factory) { showStatus("The selected map renderer did not load. Reload this page to retry.", 0); return; }
    var epoch = state.epoch;
    var context = { shared: shared, state: state, current: function () { return state.epoch === epoch; },
      zoom: function (value) { if (state.epoch === epoch) zoomNote(value); },
      loading: function () { if (state.epoch === epoch) loading(); },
      idle: function () { if (state.epoch === epoch) idle(); },
      failed: function () { if (state.epoch === epoch) state.loading.failed += 1; },
      pick: function (props) { if (state.epoch === epoch) pick(props); } };
    try { state.engine = factory(context, state.config, camera); }
    catch (error) { destroyEngine(); showStatus("The map could not start. Try the other map renderer or reload this page.", 0);
      if (window.console) window.console.error(error); }
  }
  function setRenderer(value) {
    var renderer = normalizeRenderer(value);
    if (state.config) init(Object.assign({}, state.config, { renderer: renderer }));
    else state.renderer = renderer;
  }
  function teardown(message) { void message; destroyEngine(); state.config = null; showStatus("", 0); }
  function register() {
    if (!window.Shiny || !window.Shiny.addCustomMessageHandler) return false;
    window.Shiny.addCustomMessageHandler("easi-viewer-init", init);
    window.Shiny.addCustomMessageHandler("easi-viewer-teardown", teardown);
    window.Shiny.addCustomMessageHandler("easi-viewer-renderer", function (message) { setRenderer(message && message.renderer); });
    window.Shiny.addCustomMessageHandler("easi-viewer-status", function (message) {
      if (!state.config || !document.getElementById("easi-viewer-map")) return;
      if (state.config.available === false) return;
      if (message && message.datasetKey != null && message.datasetKey !== state.config.datasetKey) return;
      if (message && message.generation != null && message.generation !== state.config.generation) return;
      showStatus(message && message.busy ? "Preparing report…" : "", 0);
    }); return true;
  }
  if (!register()) document.addEventListener("shiny:connected", register, { once: true });
  window.EASIViewer = { init: init, teardown: teardown, setRenderer: setRenderer, state: state,
    describe: describe, tileUrl: tileUrl, shared: shared, HIT_PX: HIT_PX, SHOW_DELAY_MS: SHOW_DELAY_MS, NOTE_MS: NOTE_MS,
    nearestReach: function (map, point) { return state.engine ? state.engine.nearest(point) : null; } };
})();
