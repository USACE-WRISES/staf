/* EASI Nationwide viewer: a MapLibre map over the precomputed national
   dataset. The server sends `easi-viewer-init` with the tile route (a
   session route that proxies the per-region PMTiles archives), the published
   regions, the coverage polygons, and the vintage; `easi-viewer-teardown`
   removes the map. A click on a reach posts `viewer_pick`; the server answers
   with the read-only report modal and the `staf-report-state` busy message. */
(function () {
  "use strict";

  var BAND_COLORS = {
    "Functioning": "#5b8fd6",
    "Functioning-at-Risk": "#d9b93a",
    "Non-Functioning": "#d6453d",
    "pending": "#a9b1bd"
  };
  var COVER_COLORS = { complete: "#c8d9f2", partial: "#f5e7a6", not_started: "#eef1f5" };
  var BASEMAP = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}";

  var state = { map: null, config: null, popup: null, busy: null, styleReady: false,
                lineLayers: [], hover: null };
  var HIT_PX = 8;          // half-width of the box a click or hover searches for a reach
  var GLOW_MIN_ZOOM = 7;   // below this the lines are hairlines and a glow means nothing

  function absolute(url) {
    try { return new URL(url, window.location.href).href; } catch (e) { return url; }
  }

  function tileUrl(config, vpu) {
    var base = absolute(config.routeBase);
    var sep = base.indexOf("?") >= 0 ? "&" : "?";
    return base + sep + "vpu=" + encodeURIComponent(vpu) + "&z={z}&x={x}&y={y}";
  }

  function metaUrl(config, name) {
    var base = absolute(config.routeBase);
    var sep = base.indexOf("?") >= 0 ? "&" : "?";
    return base + sep + "meta=" + encodeURIComponent(name);
  }

  function lineColor() {
    return ["match", ["get", "band"],
      "Functioning", BAND_COLORS["Functioning"],
      "Functioning-at-Risk", BAND_COLORS["Functioning-at-Risk"],
      "Non-Functioning", BAND_COLORS["Non-Functioning"],
      BAND_COLORS.pending];
  }

  // width stops: wider with zoom and with stream order; order 1 stays thin
  var WIDTH_STOPS = [[4, 0.4, 0.15], [8, 0.8, 0.35], [12, 1.6, 0.6], [16, 3.0, 0.9]];

  function hovered() {
    return ["boolean", ["feature-state", "hover"], false];
  }

  function baseWidth(stop) {
    return ["+", stop[1], ["*", stop[2], ["coalesce", ["get", "order"], 1]]];
  }

  function widthCurve(perStop) {
    // the zoom interpolation must stay the outermost expression (MapLibre
    // rejects a zoom expression nested inside case or arithmetic), so the
    // hover factor is applied inside every stop instead
    var curve = ["interpolate", ["linear"], ["zoom"]];
    WIDTH_STOPS.forEach(function (stop) { curve.push(stop[0], perStop(baseWidth(stop))); });
    return curve;
  }

  function lineWidth() {
    return widthCurve(function (base) { return base; });
  }

  function hoverLineWidth() {
    // the hovered reach draws almost twice as wide
    return widthCurve(function (base) { return ["*", ["case", hovered(), 1.8, 1], base]; });
  }

  function glowWidth() {
    return widthCurve(function (base) { return ["+", 8, ["*", 1.8, base]]; });
  }

  function addRegion(map, config, vpu) {
    var sourceId = "easi-" + vpu;
    if (map.getSource(sourceId)) return;
    map.addSource(sourceId, {
      type: "vector",
      tiles: [tileUrl(config, vpu)],
      minzoom: config.minzoom || 4,
      maxzoom: config.maxzoom || 12,
      promoteId: { flowlines: "comid" }      // feature ids for the hover state
    });
    // the glow sits under the line and only shows for the hovered feature
    // (feature state, so no tile is re-parsed and no data is re-sent)
    map.addLayer({
      id: sourceId + "-glow",
      type: "line",
      source: sourceId,
      "source-layer": "flowlines",
      minzoom: GLOW_MIN_ZOOM,
      paint: { "line-color": "#ffffff", "line-width": glowWidth(), "line-blur": 2,
               "line-opacity": ["case", hovered(), 0.9, 0] },
      layout: { "line-cap": "round", "line-join": "round" }
    });
    map.addLayer({
      id: sourceId + "-lines",
      type: "line",
      source: sourceId,
      "source-layer": "flowlines",
      paint: { "line-color": lineColor(), "line-width": hoverLineWidth(), "line-opacity": 0.95 },
      layout: { "line-cap": "round", "line-join": "round" }
    });
    state.lineLayers.push(sourceId + "-lines");
  }

  function segmentDistance(p, a, b) {
    var dx = b.x - a.x, dy = b.y - a.y;
    var len2 = dx * dx + dy * dy;
    var t = len2 ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2)) : 0;
    var x = a.x + t * dx, y = a.y + t * dy;
    return Math.sqrt((p.x - x) * (p.x - x) + (p.y - y) * (p.y - y));
  }

  function screenDistance(map, point, geometry) {
    if (!geometry || !geometry.coordinates) return Infinity;
    var lines = geometry.type === "MultiLineString" ? geometry.coordinates : [geometry.coordinates];
    var best = Infinity;
    lines.forEach(function (line) {
      var prev = null;
      line.forEach(function (c) {
        var p = map.project(c);
        if (prev) best = Math.min(best, segmentDistance(point, prev, p));
        prev = p;
      });
    });
    return best;
  }

  function nearestReach(map, point) {
    // the reach nearest the cursor within HIT_PX, so a thin line is easy to hit
    if (!point || !state.lineLayers.length) return null;
    var box = [[point.x - HIT_PX, point.y - HIT_PX], [point.x + HIT_PX, point.y + HIT_PX]];
    var features = map.queryRenderedFeatures(box, { layers: state.lineLayers }) || [];
    var best = null, bestDistance = Infinity;
    features.forEach(function (f) {
      var d = screenDistance(map, point, f.geometry);
      if (d < bestDistance) { best = f; bestDistance = d; }
    });
    return best;
  }

  function setHover(map, feature) {
    var next = feature && feature.id !== undefined && feature.id !== null
      ? { source: feature.source, sourceLayer: feature.sourceLayer || "flowlines", id: feature.id } : null;
    var prev = state.hover;
    if (prev && next && prev.source === next.source && prev.id === next.id) return;
    if (prev) { try { map.setFeatureState(prev, { hover: false }); } catch (e) { /* source gone */ } }
    if (next) { try { map.setFeatureState(next, { hover: true }); } catch (e) { next = null; } }
    state.hover = next;
  }

  function addCoverage(map, config) {
    if (map.getSource("easi-coverage")) return;
    map.addSource("easi-coverage", { type: "geojson", data: metaUrl(config, "coverage") });
    map.addLayer({
      id: "easi-coverage-fill", type: "fill", source: "easi-coverage",
      paint: {
        "fill-color": ["match", ["get", "status"],
          "complete", COVER_COLORS.complete, "partial", COVER_COLORS.partial, COVER_COLORS.not_started],
        "fill-opacity": ["interpolate", ["linear"], ["zoom"], 4, 0.45, 9, 0.15, 11, 0.0]
      }
    });
    map.addLayer({
      id: "easi-coverage-line", type: "line", source: "easi-coverage",
      paint: { "line-color": "#8a93a3", "line-width": 0.6, "line-opacity": 0.8 }
    });
  }

  function describe(props) {
    var name = props.name || "(unnamed reach)";
    var eci = props.eci === undefined || props.eci === null ? null : Number(props.eci);
    var band = props.band || "pending";
    var lines = ["<b>" + escapeHtml(name) + "</b> · COMID " + escapeHtml(String(props.comid || ""))];
    if (band === "pending" || eci === null) {
      lines.push("Not yet screened");
    } else {
      lines.push("ECI " + eci.toFixed(2) + " · " + escapeHtml(band));
      lines.push("Physical " + fmt(props.phys) + " · Chemical " + fmt(props.chem) + " · Biological " + fmt(props.bio));
      if (props.prov === true || props.prov === "true" || props.prov === 1) {
        lines.push("<span class='easi-viewer-note'>Provisional: cross-section metrics not computed</span>");
      }
    }
    return lines.join("<br>");
  }

  function fmt(v) {
    return v === undefined || v === null || v === "" ? "–" : Number(v).toFixed(2);
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function onMove(e) {
    var map = state.map;
    if (!map || !e) return;
    var feature = nearestReach(map, e.point);
    if (!feature) { onLeave(); return; }
    setHover(map, feature);
    map.getCanvas().style.cursor = "pointer";
    var props = feature.properties || {};
    if (!state.popup) {
      state.popup = new window.maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
    }
    state.popup.setLngLat(e.lngLat).setHTML("<div class='easi-viewer-popup'>" + describe(props) + "</div>").addTo(map);
  }

  function onLeave() {
    var map = state.map;
    if (!map) return;
    setHover(map, null);
    map.getCanvas().style.cursor = "";
    if (state.popup) state.popup.remove();
  }

  function onMapClick(e) {
    var map = state.map;
    if (!map || !e) return;
    var feature = nearestReach(map, e.point);
    if (feature) onClick({ features: [feature], lngLat: e.lngLat });
  }

  function onClick(e) {
    if (!e.features || !e.features.length || !window.Shiny) return;
    var props = e.features[0].properties || {};
    if (!props.comid) return;
    if (props.band === "pending" || props.band === undefined) {
      showStatus("This reach has no precomputed assessment yet.", 2500);
      return;
    }
    window.Shiny.setInputValue("viewer_pick", {
      comid: Number(props.comid), huc4: props.huc4 || null, name: props.name || null,
      nonce: Date.now()
    }, { priority: "event" });
    showStatus("Preparing report…", 0);
  }

  function showStatus(text, ttl) {
    var el = document.getElementById("easi-viewer-status");
    if (!el) return;
    el.textContent = text;
    el.hidden = !text;
    if (state.busy) { clearTimeout(state.busy); state.busy = null; }
    if (text && ttl) state.busy = setTimeout(function () { el.hidden = true; }, ttl);
  }

  function init(config) {
    var container = document.getElementById("easi-viewer-map");
    if (!container || !window.maplibregl) return;
    if (state.map && state.map.getContainer() !== container) teardown();
    if (!state.map) {
      state.map = new window.maplibregl.Map({
        container: container,
        style: {
          version: 8,
          sources: { basemap: { type: "raster", tiles: [BASEMAP], tileSize: 256,
                                attribution: "USGS The National Map" } },
          layers: [{ id: "basemap", type: "raster", source: "basemap" }]
        },
        center: config.center || [-96, 38.5],
        zoom: config.zoom || 4,
        minZoom: 3,
        maxZoom: 16,
        attributionControl: true
      });
      state.map.addControl(new window.maplibregl.NavigationControl({ showCompass: false }), "top-right");
      // one set of map-level handlers: the hit test picks the nearest reach
      // within HIT_PX of the cursor across every region layer
      state.map.on("mousemove", onMove);
      state.map.on("mouseout", onLeave);
      state.map.on("click", onMapClick);
      // style.load fires once the style is parsed, before every basemap tile has
      // arrived; the sources can be added from then on (load would wait for tiles)
      state.styleReady = false;
      state.map.on("style.load", function () { state.styleReady = true; apply(config); });
    } else {
      apply(config);
    }
    state.config = config;
  }

  function apply(config) {
    var map = state.map;
    if (!map || !state.styleReady) {
      if (map) map.once("style.load", function () { state.styleReady = true; apply(config); });
      return;
    }
    addCoverage(map, config);
    (config.vpus || []).forEach(function (vpu) { addRegion(map, config, vpu); });
    // a refresh re-reads the coverage file (the data url carries a nonce)
    var source = map.getSource("easi-coverage");
    if (source && source.setData) source.setData(metaUrl(config, "coverage") + "&t=" + Date.now());
  }

  function teardown(message) {   // Shiny requires exactly one parameter on a message handler
    void message;
    if (state.popup) { state.popup.remove(); state.popup = null; }
    if (state.map) { try { state.map.remove(); } catch (e) { /* already gone */ } }
    state.map = null;
    state.config = null;
    state.styleReady = false;
    state.lineLayers = [];
    state.hover = null;
  }

  function register() {
    if (!window.Shiny || !window.Shiny.addCustomMessageHandler) return false;
    window.Shiny.addCustomMessageHandler("easi-viewer-init", init);
    window.Shiny.addCustomMessageHandler("easi-viewer-teardown", teardown);
    // the server mirrors the report's busy state on a viewer-only message
    // (Shiny keeps one handler per message name, and staf-report-state
    // belongs to report-ready.js)
    window.Shiny.addCustomMessageHandler("easi-viewer-status", function (message) {
      if (!document.getElementById("easi-viewer-map")) return;
      if (message && message.busy) showStatus("Preparing report…", 0);
      else showStatus("", 0);
    });
    return true;
  }

  if (!register()) {
    document.addEventListener("shiny:connected", register, { once: true });
  }

  window.EASIViewer = { init: init, teardown: teardown, describe: describe, tileUrl: tileUrl, state: state,
                        nearestReach: nearestReach, HIT_PX: HIT_PX };
})();
