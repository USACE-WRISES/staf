/* Standard Nationwide renderer. Kept as an explicit manual choice. */
(function () {
  "use strict";
  window.EASIViewerRenderers = window.EASIViewerRenderers || {};
  window.EASIViewerRenderers.standard = function (ctx, initial, camera) {
    var C = ctx.shared, state = ctx.state, config = initial, alive = true, ready = false;
    var moving = false, pending = null, frame = null, hoverId = null;
    var map = new window.maplibregl.Map({ container: document.getElementById("easi-viewer-map"),
      style: { version: 8, sources: { basemap: { type: "raster", tiles: [C.BASEMAP], tileSize: 256, maxzoom: 16,
        attribution: "USGS The National Map" } }, layers: [{ id: "basemap", type: "raster", source: "basemap" }] },
      center: camera ? camera.center : (config.center || [-96, 38.5]),
      zoom: camera ? camera.zoom : (config.zoom == null ? 4 : config.zoom), minZoom: 3, maxZoom: 16,
      attributionControl: true });
    state.map = map; state.lineLayers = [];
    map.addControl(new window.maplibregl.NavigationControl({ showCompass: false }), "top-right");
    function current() { return alive && ctx.current(); }
    function hovered() { return ["boolean", ["feature-state", "hover"], false]; }
    function widths(glow) {
      var result = ["interpolate", ["linear"], ["zoom"]];
      C.WIDTH_STOPS.forEach(function (s) { var base = ["+", s[1], ["*", s[2], ["coalesce", ["get", "order"], 1]]];
        result.push(s[0], glow ? ["+", 8, ["*", 1.8, base]] : ["*", ["case", hovered(), 1.8, 1], base]); });
      return result;
    }
    function addRegion(vpu) {
      var id = "easi-" + vpu; if (map.getSource(id)) return;
      var source = { type: "vector", tiles: [C.tileUrl(config, vpu)], minzoom: config.minzoom || 4,
        maxzoom: config.maxzoom || 12, promoteId: { flowlines: "comid" } };
      var bounds = (config.vpuBounds || {})[vpu]; if (Array.isArray(bounds) && bounds.length === 4) source.bounds = bounds;
      map.addSource(id, source);
      map.addLayer({ id: id + "-glow", type: "line", source: id, "source-layer": "flowlines", minzoom: 7,
        paint: { "line-color": "#ffffff", "line-width": widths(true), "line-blur": 2,
          "line-opacity": ["case", hovered(), .9, 0] }, layout: { "line-cap": "round", "line-join": "round" } });
      map.addLayer({ id: id + "-lines", type: "line", source: id, "source-layer": "flowlines",
        paint: { "line-color": ["match", ["get", "band"], "Functioning", C.BAND_COLORS.Functioning,
          "Functioning-at-Risk", C.BAND_COLORS["Functioning-at-Risk"], "Non-Functioning", C.BAND_COLORS["Non-Functioning"], C.BAND_COLORS.pending],
          "line-width": widths(false), "line-opacity": .95 }, layout: { "line-cap": "round", "line-join": "round" } });
      state.lineLayers.push(id + "-lines");
    }
    function coverage() {
      if (map.getSource("easi-coverage")) { map.getSource("easi-coverage").setData(C.metaUrl(config, "coverage") + "&t=" + Date.now()); return; }
      map.addSource("easi-coverage", { type: "geojson", data: C.metaUrl(config, "coverage") });
      map.addLayer({ id: "easi-coverage-fill", type: "fill", source: "easi-coverage", paint: {
        "fill-color": ["match", ["get", "status"], "complete", C.COVER_COLORS.complete, "partial", C.COVER_COLORS.partial, C.COVER_COLORS.not_started],
        "fill-opacity": ["interpolate", ["linear"], ["zoom"], 4, .45, 9, .15, 11, 0] } });
      map.addLayer({ id: "easi-coverage-line", type: "line", source: "easi-coverage",
        paint: { "line-color": "#8a93a3", "line-width": .6, "line-opacity": .8 } });
    }
    function nearest(point) {
      if (!current() || !point || !state.lineLayers.length) return null;
      var box = [[point.x - C.HIT_PX, point.y - C.HIT_PX], [point.x + C.HIT_PX, point.y + C.HIT_PX]];
      var features = map.queryRenderedFeatures(box, { layers: state.lineLayers }) || [];
      var best = null, distance = C.HIT_PX + 1e-8, seen = new Map();
      features.forEach(function (f) {
        if (!f.geometry || !f.geometry.coordinates) return;
        var lines = f.geometry.type === "MultiLineString" ? f.geometry.coordinates : [f.geometry.coordinates];
        var d = Infinity;
        lines.forEach(function (line) { for (var i = 1; i < line.length; i++)
          d = Math.min(d, C.segmentDistance(point, map.project(line[i - 1]), map.project(line[i]))); });
        var id = String((f.properties || {}).comid || f.id);
        if (seen.has(id) && seen.get(id) <= d) return; seen.set(id, d);
        if (d < distance) { distance = d; best = f; }
      }); return best;
    }
    function leave() {
      pending = null;
      if (state.hover) { try { map.setFeatureState(state.hover, { hover: false }); } catch (_) {} }
      state.hover = null; hoverId = null; map.getCanvas().style.cursor = "";
      if (state.popup) state.popup.remove();
    }
    function move(event) {
      if (!current() || moving) return;
      var f = nearest(event.point); if (!f) { leave(); return; }
      var id = f.source + "/" + f.id;
      if (id !== hoverId) {
        leave(); state.hover = { source: f.source, sourceLayer: f.sourceLayer || "flowlines", id: f.id };
        map.setFeatureState(state.hover, { hover: true }); hoverId = id;
        if (!state.popup) state.popup = new window.maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
        state.popup.setHTML("<div class='easi-viewer-popup'>" + C.describe(f.properties || {}) + "</div>");
      }
      map.getCanvas().style.cursor = "pointer"; state.popup.setLngLat(event.lngLat).addTo(map);
    }
    map.on("mousemove", function (event) {
      pending = event; if (frame != null || moving) return;
      frame = window.requestAnimationFrame(function () { frame = null; if (pending) move(pending); });
    });
    map.on("mouseout", leave);
    map.on("movestart", function () { moving = true; pending = null; leave(); });
    map.on("moveend", function () { moving = false; });
    map.on("click", function (event) { if (!current() || moving) return; var f = nearest(event.point); if (f) ctx.pick(f.properties); });
    function screening(event) { var id = event && (event.sourceId || (event.source && event.source.id)); return typeof id === "string" && id.indexOf("easi-") === 0; }
    map.on("sourcedataloading", function (event) { if (current() && screening(event)) ctx.loading(); });
    map.on("idle", function () { if (current()) ctx.idle(); });
    map.on("error", function (event) {
      if (!current()) return;
      if (event && event.tile && screening(event)) ctx.failed();
      else if (event && event.error && window.console) window.console.error(event.error);
    });
    function apply() { if (!current() || !ready || config.available === false) return; coverage(); (config.vpus || []).forEach(addRegion); }
    map.on("style.load", function () { if (!current()) return; ready = true; apply(); });
    return {
      nearest: nearest,
      camera: function () { var center = map.getCenter ? map.getCenter() : null;
        return { center: center ? [center.lng, center.lat] : config.center || [-96, 38.5], zoom: map.getZoom ? map.getZoom() : config.zoom || 4 }; },
      refresh: function (next) {
        var changed = next.datasetKey !== config.datasetKey || next.generation !== config.generation || next.routeBase !== config.routeBase;
        if (ready) {
          leave(); state.lineLayers = state.lineLayers.filter(function (id) { var source = id.slice(0, -6);
            if (!changed && next.available !== false && (next.vpus || []).indexOf(source.slice(5)) >= 0) return true;
            map.removeLayer(id); map.removeLayer(source + "-glow"); map.removeSource(source); return false; });
          if ((changed || next.available === false) && map.getSource("easi-coverage")) {
            map.removeLayer("easi-coverage-line"); map.removeLayer("easi-coverage-fill"); map.removeSource("easi-coverage");
          }
        }
        config = next; apply();
      },
      destroy: function () { alive = false; if (frame != null) window.cancelAnimationFrame(frame);
        if (state.popup) { state.popup.remove(); state.popup = null; } map.remove(); }
    };
  };
})();
