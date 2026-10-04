/* NHDPlus HR data viewer: draws the slim flowlines and catchments through the
 * app's own REST API, overlays the original geometry in the QA boxes, and
 * compares three ways to fetch a clicked point's catchment or upstream
 * watershed: GDAL streaming the USGS package, the USGS package downloaded once,
 * and the slim copy. Vanilla JS on the vendored Leaflet 1.9.4. */
(function () {
  "use strict";

  var LINE_ZOOM = 12;
  var CAT_ZOOM = 13;
  var POLL_MS = 1000;
  var METHODS = [
    {key: "remote", n: 1, label: "GDAL streaming from USGS", color: "#db2777",
     hint: "GDAL reads the zipped USGS geodatabase on S3 in place, without a copy. Expect one to several minutes.",
     poly: {weight: 7, opacity: 0.4}, line: {weight: 9, opacity: 0.4}},
    {key: "download", n: 2, label: "Download the USGS region (HU4)", color: "#0891b2",
     hint: "Downloads the region's USGS package once, then reads it from disk. Later clicks in the region reuse it.",
     poly: {weight: 4, opacity: 0.9}, line: {weight: 5, opacity: 0.9}},
    {key: "slim", n: 3, label: "Slim copy in this app", color: "#16a34a",
     hint: "Reads the simplified copy stored with this app. Covers the dashed regions only.",
     poly: {weight: 2, opacity: 1, dashArray: "6 4"}, line: {weight: 2.5, opacity: 1}}
  ];
  var TIMINGS = [
    ["download_s", "download"], ["unzip_s", "unzip"], ["open_s", "open"], ["find_s", "find stream"],
    ["catchment_s", "catchment"], ["topology_s", "VAA table"], ["catchment_index_s", "catchment index"],
    ["walk_s", "walk"], ["catchments_s", "read catchments"], ["union_s", "union"], ["total_s", "total"]
  ];
  var state = {tol: null, summary: null, seq: {}, counts: {}, notes: {}, timer: null,
               point: null, where: null, whereSeq: 0, scope: "catchment", runs: {}, runsHtml: null};

  var base = (function () {
    var p = window.location.pathname;
    return p.charAt(p.length - 1) === "/" ? p : p.replace(/[^\/]*$/, "");
  })();

  function api(path) { return base + "api/" + path; }

  function getJSON(url, opts) {
    return fetch(url, opts).then(function (r) {
      return r.json().then(function (body) {
        if (!r.ok) {
          var err = new Error(body.message || r.statusText);
          err.status = r.status;
          throw err;
        }
        return body;
      });
    });
  }

  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[c];
    });
  }
  function num(n, d) { return (n === null || n === undefined) ? "-" : Number(n).toFixed(d); }
  function int(n) { return (n === null || n === undefined) ? "-" : Number(n).toLocaleString("en-US"); }
  function mb(bytes) { return (bytes / 1e6).toFixed(1) + " MB"; }
  function secs(v) { return v < 1 ? Math.round(v * 1000) + " ms" : Number(v).toFixed(1) + " s"; }
  function method(key) { return METHODS.filter(function (m) { return m.key === key; })[0]; }

  // Dash adds asset scripts in its own order (this file can run before
  // leaflet.js) and renders the layout after load, so wait for both.
  function waitFor(id, cb) {
    var el = document.getElementById(id);
    if (el && window.L) { cb(el); return; }
    setTimeout(function () { waitFor(id, cb); }, 60);
  }

  waitFor("hr-map", function (el) { waitFor("hr-panel", function (panel) { init(el, panel); }); });

  function init(el, panel) {
    var map = L.map(el, {preferCanvas: true, minZoom: 3, worldCopyJump: true}).setView([39.5, -96.5], 5);
    window.HRViewer = {map: map, state: state};
    // Dash may still be laying the page out when this runs, so the container can
    // start at zero size; tell Leaflet whenever its size settles or changes.
    if (window.ResizeObserver) {
      new ResizeObserver(function () { map.invalidateSize(); }).observe(el);
    }
    setTimeout(function () { map.invalidateSize(); }, 200);
    var renderer = L.canvas({padding: 0.3, tolerance: 6});
    ["hr-results", "hr-point"].forEach(function (name, i) {
      var pane = map.createPane(name);
      pane.style.zIndex = String(450 + 150 * i);
      pane.style.pointerEvents = "none";
    });
    var resultRenderer = L.svg({pane: "hr-results"});

    var usgs = "https://basemap.nationalmap.gov/arcgis/rest/services/";
    var basemaps = {
      "OpenStreetMap": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        {maxZoom: 19, attribution: "&copy; OpenStreetMap contributors"}),
      "USGS imagery": L.tileLayer(usgs + "USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
        {maxZoom: 19, maxNativeZoom: 16, attribution: "USGS The National Map"}),
      "USGS topo": L.tileLayer(usgs + "USGSTopo/MapServer/tile/{z}/{y}/{x}",
        {maxZoom: 19, maxNativeZoom: 16, attribution: "USGS The National Map"})
    };
    basemaps["OpenStreetMap"].addTo(map);

    var lines = L.geoJSON(null, {renderer: renderer, style: lineStyle, onEachFeature: bindLine});
    var catchments = L.geoJSON(null, {renderer: renderer, style: catStyle, onEachFeature: bindCatchment});
    var original = L.geoJSON(null, {renderer: renderer, style: qaStyle, interactive: false});
    var qaBoxes = L.layerGroup();
    var regions = L.layerGroup();
    var hydroCached = L.tileLayer(usgs + "USGSHydroCached/MapServer/tile/{z}/{y}/{x}",
      {maxZoom: 19, maxNativeZoom: 16, opacity: 0.8, attribution: "USGS The National Map"});
    var live = L.layerGroup();
    var results = {};
    METHODS.forEach(function (m) {
      results[m.key] = L.geoJSON(null, {pane: "hr-results", renderer: resultRenderer, interactive: false,
                                        style: resultStyle(m)});
    });
    var pointMarker = L.circleMarker([0, 0], {pane: "hr-point", renderer: L.svg({pane: "hr-point"}),
      radius: 7, color: "#111827", weight: 2.5, fillColor: "#facc15", fillOpacity: 1, interactive: false});
    // Slim catchments start off (the layer menu turns them on): their outlines
    // crowd the three results drawn on top.
    [hydroCached, regions, qaBoxes, original, lines].forEach(function (l) { l.addTo(map); });
    METHODS.forEach(function (m) { results[m.key].addTo(map); });
    window.HRViewer.layers = {lines: lines, catchments: catchments, original: original, results: results};

    var overlays = {
      "Slim flowlines": lines,
      "Slim catchments": catchments,
      "Original geometry (QA boxes)": original,
      "QA boxes": qaBoxes,
      "Data regions": regions
    };
    METHODS.forEach(function (m) { overlays["Result " + m.n + ": " + m.label] = results[m.key]; });
    overlays["USGS NHD image (cached tiles)"] = hydroCached;
    overlays["USGS NHDPlus HR image (live, slow)"] = live;
    L.control.layers(basemaps, overlays, {collapsed: true}).addTo(map);
    L.control.scale({imperial: true, metric: true}).addTo(map);

    var statusCtl = L.control({position: "bottomleft"});
    statusCtl.onAdd = function () {
      var d = L.DomUtil.create("div", "hr-status");
      L.DomEvent.disableClickPropagation(d);
      return d;
    };
    statusCtl.addTo(map);

    function lineStyle() { return {color: "#1f6feb", weight: 2, opacity: 0.95}; }
    // Outlines only: a filled polygon would catch every click meant for the map.
    function catStyle() { return {color: "#b45309", weight: 1.2, fill: false}; }
    function qaStyle(f) {
      return f.properties.kind === "line"
        ? {color: "#dc2626", weight: 2, opacity: 0.9, dashArray: "5 4"}
        : {color: "#dc2626", weight: 1.2, opacity: 0.9, dashArray: "3 3", fill: false};
    }
    function resultStyle(m) {
      return function (f) {
        var isLine = /LineString/.test((f.geometry || {}).type || "");
        var s = isLine ? m.line : m.poly;
        return {color: m.color, weight: s.weight, opacity: s.opacity, dashArray: s.dashArray || null,
                fill: !isLine, fillColor: m.color, fillOpacity: 0.06, lineCap: "round"};
      };
    }

    function bindLine(f, layer) {
      var p = f.properties || {};
      layer.bindTooltip(esc(p.gnis_name || "Unnamed stream") + " · " + esc(p.nhdplusid) + "<br>" +
        num(p.totdasqkm, 2) + " km² drainage area", {sticky: true, direction: "top", opacity: 0.95});
    }
    function bindCatchment(f, layer) {
      layer.bindTooltip("Catchment " + f.properties.nhdplusid, {sticky: true, direction: "top"});
    }

    // ------------------------------------------------------------ loading
    function bboxParam() {
      var b = map.getBounds();
      return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map(function (v) { return v.toFixed(5); }).join(",");
    }

    function load(layer, key, url) {
      var id = (state.seq[key] = (state.seq[key] || 0) + 1);
      state.notes[key] = "loading";
      renderStatus();
      getJSON(url).then(function (fc) {
        if (id !== state.seq[key]) { return; }
        layer.clearLayers();
        if (fc.features && fc.features.length) { layer.addData(fc); }
        state.counts[key] = (fc.features || []).length;
        state.notes[key] = fc.status === "too-large" ? "zoom in" : null;
        renderStatus();
      }).catch(function () {
        if (id !== state.seq[key]) { return; }
        state.notes[key] = "failed";
        renderStatus();
      });
    }

    function clear(layer, key) {
      state.seq[key] = (state.seq[key] || 0) + 1;
      layer.clearLayers();
      state.counts[key] = 0;
      state.notes[key] = null;
    }

    function qaInView() {
      var b = map.getBounds();
      return (state.summary ? state.summary.vpus : []).some(function (v) {
        return (v.qaBoxes || []).some(function (q) { return b.intersects(L.latLngBounds([q[1], q[0]], [q[3], q[2]])); });
      });
    }

    function refresh() {
      var z = map.getZoom();
      var bbox = bboxParam();
      if (map.hasLayer(lines) && z >= LINE_ZOOM && state.summary) {
        load(lines, "lines", api("lines?bbox=" + bbox));
      } else { clear(lines, "lines"); }
      if (map.hasLayer(catchments) && z >= CAT_ZOOM && state.tol !== null) {
        load(catchments, "catchments", api("catchments?bbox=" + bbox + "&tol=" + state.tol));
      } else { clear(catchments, "catchments"); }
      if (map.hasLayer(original) && z >= CAT_ZOOM && qaInView()) {
        load(original, "original", api("qa?bbox=" + bbox));
      } else { clear(original, "original"); }
      if (map.hasLayer(live)) { updateLive(); }
      renderStatus();
    }

    function schedule() {
      clearTimeout(state.timer);
      state.timer = setTimeout(refresh, 250);
    }
    map.on("moveend", schedule);
    map.on("overlayadd overlayremove", schedule);
    map.on("movestart", function () { if (state.summary === null) { state.userMoved = true; } });

    function updateLive() {
      live.clearLayers();
      var b = map.getBounds();
      var sw = L.CRS.EPSG3857.project(b.getSouthWest());
      var ne = L.CRS.EPSG3857.project(b.getNorthEast());
      var size = map.getSize();
      var url = "https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer/export?bbox=" +
        [sw.x, sw.y, ne.x, ne.y].join(",") + "&bboxSR=3857&imageSR=3857&size=" + size.x + "," + size.y +
        "&layers=show:3,10&format=png32&transparent=true&f=image";
      L.imageOverlay(url, b, {opacity: 0.85}).addTo(live);
    }

    function renderStatus() {
      var z = map.getZoom();
      var parts = [];
      if (z < LINE_ZOOM) {
        parts.push("Slim streams show from zoom " + LINE_ZOOM + " (now " + z + ")");
      } else {
        parts.push(describe("lines", "slim flowlines"));
        parts.push(z >= CAT_ZOOM ? describe("catchments", "slim catchments") : "catchments from zoom " + CAT_ZOOM);
        if (state.counts.original || state.notes.original) { parts.push(describe("original", "original features")); }
      }
      parts.push("click the map to drop a point");
      statusCtl.getContainer().innerHTML = parts.map(esc).join(" · ");
    }
    function describe(key, label) {
      var note = state.notes[key];
      if (note === "loading") { return "loading " + label; }
      if (note === "failed") { return label + " did not load"; }
      if (note === "zoom in") { return "zoom in for " + label; }
      return int(state.counts[key] || 0) + " " + label;
    }

    // ------------------------------------------------------------ point + runs
    map.on("click", function (e) { setPoint(e.latlng); });

    function setPoint(ll) {
      var lon = Math.round(L.Util.wrapNum(ll.lng, [-180, 180], true) * 1e6) / 1e6;
      var lat = Math.round(ll.lat * 1e6) / 1e6;
      state.point = {lat: lat, lon: lon};
      pointMarker.setLatLng(ll);
      if (!map.hasLayer(pointMarker)) { pointMarker.addTo(map); }
      // Finished results belong to the old point; running ones keep going.
      Object.keys(state.runs).forEach(function (k) { if (state.runs[k].status !== "running") { dropRun(k); } });
      state.where = null;
      var id = ++state.whereSeq;
      getJSON(api("where?lon=" + lon + "&lat=" + lat), {cache: "no-store"}).then(function (w) {
        if (id === state.whereSeq) { state.where = w; renderPick(); }
      }).catch(function (err) {
        if (id === state.whereSeq) { state.where = {error: String(err.message || err)}; renderPick(); }
      });
      renderPick();
      renderRuns();
    }

    function startRun(key) {
      if (!state.point) { return; }
      var r = {status: "running", step: "starting", point: state.point, scope: state.scope,
               tol: state.tol, elapsed: 0, misses: 0, t0: Date.now()};
      state.runs[key] = r;
      results[key].clearLayers();
      renderPick();
      renderRuns();
      var body = {lon: r.point.lon, lat: r.point.lat, method: key, scope: r.scope};
      if (key === "slim") { body.tol = r.tol; }
      getJSON(api("pick"), {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
        .then(function (b) {
          if (state.runs[key] !== r) { return; }
          r.job = b.job;
          setTimeout(function () { poll(key, r); }, 300);
        })
        .catch(function (err) { finish(key, r, {status: "error", error: String(err.message || err)}); });
    }

    function poll(key, r) {
      if (state.runs[key] !== r) { return; }
      getJSON(api("job/" + r.job), {cache: "no-store"}).then(function (j) {
        if (state.runs[key] !== r) { return; }
        r.misses = 0;
        r.elapsed = j.elapsed_s;
        r.step = j.step;
        if (j.status === "running") {
          renderRuns();
          setTimeout(function () { poll(key, r); }, POLL_MS);
          return;
        }
        finish(key, r, j);
      }).catch(function (err) {
        if (state.runs[key] !== r) { return; }
        r.misses += 1;
        if (err.status === 404 || r.misses >= 10) {
          finish(key, r, {status: "error", error: String(err.message || err)});
          return;
        }
        setTimeout(function () { poll(key, r); }, POLL_MS * 2);
      });
    }

    function finish(key, r, job) {
      if (state.runs[key] !== r) { return; }
      r.status = job.status;
      r.result = job.result || null;
      r.timings = job.timings || {};
      r.error = job.error || null;
      if (job.elapsed_s !== undefined) { r.elapsed = job.elapsed_s; }
      if (r.status === "done") { draw(key, r); }
      renderPick();
      renderRuns();
    }

    function draw(key, r) {
      var layer = results[key];
      var res = r.result || {};
      layer.clearLayers();
      if (res.polygon) { layer.addData(res.polygon); }
      if (res.reach) { layer.addData(res.reach); }
      if (!map.hasLayer(layer)) { return; }
      if (key === "remote") { layer.bringToBack(); } else if (key === "slim") { layer.bringToFront(); }
      var b = layer.getBounds();
      if (b.isValid() && !map.getBounds().contains(b)) {
        map.fitBounds(b.extend([r.point.lat, r.point.lon]), {padding: [30, 30], maxZoom: 16});
      }
    }

    function dropRun(key) {
      delete state.runs[key];
      results[key].clearLayers();
    }

    // ------------------------------------------------------------ panel
    // Version 2 data (format 2) stores catchments exactly: no tolerance to pick.
    function exact() { return !!(state.summary && state.summary.format === 2); }

    function vpuBytes(v) {
      if (exact()) {
        return Object.keys(v.bytes).reduce(function (t, k) { return t + v.bytes[k]; }, 0);
      }
      return v.bytes.lines + (v.bytes["catchments" + Math.round(state.tol)] || 0);
    }

    function renderPanel() {
      var s = state.summary;
      var html = "<h2>Three ways to fetch NHDPlus HR</h2>" +
        "<p class='hr-lead'>Click the map to drop a point, choose a catchment or the upstream watershed, " +
        "then run each method. Each finds the nearest network stream within 200 m of the point.</p>" +
        "<div id='hr-pick'></div><section><h3>Results</h3><div id='hr-runs'></div></section>";
      if (!s) {
        html += "<p class='hr-muted'>" + esc(state.loadError || "Loading the slim data summary") + "</p>";
      } else {
        var recipe = s.recipe || {};
        if (exact()) {
          html += "<section><h3>Slim copy</h3><p class='hr-muted'>USGS NHDPlus HR network flowlines, rounded to about 1 m " +
            "and simplified at " + num(recipe.line_tolerance_m, 0) + " m, and their catchments stored exactly, " +
            "on the elevation grid they were cut from.</p></section>";
        } else {
          html += "<section><h3>Slim copy</h3><p class='hr-muted'>USGS NHDPlus HR network flowlines and catchments, " +
            "rounded to about 1 m. Flowlines simplified at " + num(recipe.line_tolerance_m, 0) +
            " m; catchments at the tolerance below.</p><div class='hr-seg'>";
          s.tolerances.forEach(function (t) {
            html += "<label><input type='radio' name='hr-tol' value='" + t + "'" + (t === state.tol ? " checked" : "") + "> " + num(t, 0) + " m</label>";
          });
          html += "</div></section>";
        }
        html += "<section><h3>Slim regions</h3><ul class='hr-list hr-cols'>";
        s.vpus.forEach(function (v, i) {
          html += "<li><a href='#' data-zoom-vpu='" + i + "'>" + esc(v.vpu) + "</a> " + mb(vpuBytes(v)) + "</li>";
        });
        html += "</ul></section>";
        var boxes = [];
        s.vpus.forEach(function (v) { (v.qaBoxes || []).forEach(function (b, j) { boxes.push({vpu: v.vpu, n: j + 1, box: b}); }); });
        state.qaList = boxes;
        if (boxes.length) {
          html += "<section><h3>QA boxes</h3><p class='hr-muted'>Red dashed lines are the original USGS geometry, kept in these boxes for comparison.</p><ul class='hr-list hr-cols'>";
          boxes.forEach(function (b, k) { html += "<li><a href='#' data-zoom-qa='" + k + "'>" + esc(b.vpu) + " box " + b.n + "</a></li>"; });
          html += "</ul></section>";
        }
      }
      html += "<section><h3>Legend</h3><ul class='hr-legend'><li><span class='sw sw-point'></span>Selected point</li>";
      METHODS.forEach(function (m) {
        html += "<li><span class='sw sw-result' style='--c:" + m.color + "'></span>" + m.n + " · " + esc(m.label) + "</li>";
      });
      html += "<li><span class='sw sw-line'></span>Slim flowline</li><li><span class='sw sw-cat'></span>Slim catchment</li>" +
        "<li><span class='sw sw-orig'></span>Original USGS geometry</li></ul></section>";
      if (s) {
        var total = 0;
        s.vpus.forEach(function (v) { total += vpuBytes(v); });
        html += "<p class='hr-foot'>Slim copy: " + s.vpus.length + " regions, " + mb(total) +
          (exact() ? "" : " at this tolerance") + ", built " + esc((s.built || "").slice(0, 10)) + "</p>";
      }
      panel.innerHTML = html;
      state.runsHtml = null;
      renderPick();
      renderRuns();
    }

    function renderPick() {
      var box = document.getElementById("hr-pick");
      if (!box) { return; }
      var p = state.point;
      var html = "<section><h3>Point</h3>";
      if (!p) {
        html += "<p class='hr-muted'>No point yet. Click the map. Slim streams show from zoom " + LINE_ZOOM +
          " in the dashed regions; the USGS NHD image shows streams everywhere.</p>";
      } else {
        html += "<p class='hr-point'>" + num(p.lat, 5) + ", " + num(p.lon, 5) + "</p>" + whereHtml();
      }
      html += "<div class='hr-seg hr-scope'>" +
        "<label><input type='radio' name='hr-scope' value='catchment'" + (state.scope === "catchment" ? " checked" : "") + "> Catchment</label>" +
        "<label><input type='radio' name='hr-scope' value='watershed'" + (state.scope === "watershed" ? " checked" : "") + "> Upstream watershed</label></div>";
      METHODS.forEach(function (m) {
        var r = state.runs[m.key];
        var busy = r && r.status === "running";
        html += "<button type='button' class='hr-method' style='--c:" + m.color + "' data-run='" + m.key + "'" +
          (!p || busy ? " disabled" : "") + "><span class='hr-num'>" + m.n + "</span>" + esc(m.label) +
          (busy ? " <span class='hr-busy'>running</span>" : "") + "</button><p class='hr-hint'>" + esc(m.hint) + "</p>";
      });
      box.innerHTML = html + "</section>";
    }

    function whereHtml() {
      var w = state.where;
      if (!w) { return "<p class='hr-muted'>Looking up the USGS region</p>"; }
      if (w.error) { return "<p class='hr-warn'>" + esc(w.error) + "</p>"; }
      if (!w.vpus || !w.vpus.length) { return "<p class='hr-warn'>No USGS NHDPlus HR region here.</p>"; }
      var bits = ["USGS region <b>" + esc(w.vpus[0]) + "</b>"];
      if (w.package) { bits.push("package " + num(w.package.mb, 0) + " MB"); }
      if (w.downloaded) { bits.push("already on the server's disk"); }
      var html = "<p class='hr-muted'>" + bits.join(" · ") + "</p>";
      if (w.listingError) { html += "<p class='hr-warn'>USGS package list unavailable: " + esc(w.listingError) + "</p>"; }
      html += "<p class='hr-muted'>Slim copy: " + (w.slim && w.slim.length ? "covers this region" : "does not cover this region") + "</p>";
      return html;
    }

    function renderRuns() {
      var box = document.getElementById("hr-runs");
      if (!box) { return; }
      var html = METHODS.filter(function (m) { return state.runs[m.key]; }).map(function (m) {
        return runHtml(m, state.runs[m.key]);
      }).join("");
      if (!html) { html = "<p class='hr-muted'>Each method's result appears here and on the map in its color.</p>"; }
      else { html += "<button type='button' class='hr-btn hr-btn-quiet' data-clear-runs='1'>Clear results</button>"; }
      // Rebuild only when the content changes, so a click is never lost to a
      // poll; the running timers update in place.
      if (html !== state.runsHtml) {
        box.innerHTML = html;
        state.runsHtml = html;
      }
      METHODS.forEach(function (m) {
        var r = state.runs[m.key];
        var liveEl = r && box.querySelector("[data-live='" + m.key + "']");
        if (liveEl) {
          var t = r.elapsed || (Date.now() - r.t0) / 1000;
          liveEl.textContent = "Running " + Math.round(t) + " s" + (r.step ? " · " + r.step : "");
        }
      });
    }

    function runHtml(m, r) {
      var p = r.point;
      var same = state.point && p.lat === state.point.lat && p.lon === state.point.lon;
      var html = "<div class='hr-run' style='--c:" + m.color + "'><div class='hr-run-head'><span class='hr-num'>" + m.n +
        "</span><b>" + esc(m.label) + "</b></div><div class='hr-muted'>" +
        (r.scope === "watershed" ? "Upstream watershed" : "Catchment") +
        (same ? "" : " for an earlier point (" + num(p.lat, 4) + ", " + num(p.lon, 4) + ")") +
        (m.key === "slim" && r.tol !== null ? (exact() ? " · exact catchments" : " · catchments at " + num(r.tol, 0) + " m") : "") + "</div>";
      if (r.status === "running") {
        return html + "<p class='hr-live' data-live='" + m.key + "'></p></div>";
      }
      if (r.status === "error") {
        return html + "<p class='hr-warn'>" + esc(r.error || "The run failed.") + "</p>" + timingsHtml(r.timings) + actions(m, false) + "</div>";
      }
      var res = r.result || {};
      if (res.status !== "ok") {
        html += "<p class='hr-warn'>" + esc(res.reason || "Nothing found.") + "</p>";
        if (res.nReaches) { html += "<p class='hr-muted'>" + int(res.nReaches) + " reaches in " + int(res.nHops) + " levels before it stopped.</p>"; }
        return html + timingsHtml(r.timings) + actions(m, !!res.reach) + "</div>";
      }
      var f = (res.reach || {}).properties || {};
      var rows = [["Stream", (f.gnis_name || "Unnamed") + " · " + f.nhdplusid],
                  ["Snapped", num(f.snap_m, 0) + " m from the point"]];
      if (r.scope === "watershed") {
        rows.push(["Area", num(res.areaSqkm, 2) + " km²"]);
        rows.push(["USGS drainage area", res.publishedSqkm === null || res.publishedSqkm === undefined ? "-" :
                   num(res.publishedSqkm, 2) + " km² (ratio " + num(res.agreement, 3) + ")"]);
        rows.push(["Reaches", int(res.nReaches) + " in " + int(res.nHops) + " levels"]);
      } else {
        rows.push(["Area", num(res.areaSqkm, 3) + " km²" + (res.publishedSqkm ? " (USGS " + num(res.publishedSqkm, 3) +
                   ", ratio " + num(res.agreement, 3) + ")" : "")]);
      }
      rows.push(["Outline", int(res.vertices) + " vertices · " + num(res.polygonKb, 1) + " KB"]);
      rows.push(["Source", res.package ? "USGS " + res.vpu + " package, " + num(res.packageMb, 0) + " MB" :
                 "slim region " + res.vpu]);
      html += "<table class='hr-kv'>";
      rows.forEach(function (row) { html += "<tr><th>" + esc(row[0]) + "</th><td>" + esc(row[1]) + "</td></tr>"; });
      return html + "</table>" + timingsHtml(r.timings) + actions(m, true) + "</div>";
    }

    function timingsHtml(t) {
      if (!t) { return ""; }
      var parts = [];
      if (t.download_note) { parts.push("download: " + t.download_note); }
      TIMINGS.forEach(function (pair) {
        if (t[pair[0]] === undefined || t[pair[0]] === null) { return; }
        var text = pair[1] + " " + secs(t[pair[0]]);
        if (pair[0] === "download_s" && t.mb_per_s) { text += " (" + num(t.download_mb, 0) + " MB at " + num(t.mb_per_s, 0) + " MB/s)"; }
        parts.push(text);
      });
      if (t.evicted && t.evicted.length) { parts.push("freed disk: " + t.evicted.join(", ")); }
      return parts.length ? "<p class='hr-times'>" + parts.map(esc).join(" · ") + "</p>" : "";
    }

    function actions(m, zoom) {
      return "<div class='hr-run-actions'>" + (zoom ? "<a href='#' data-zoom-run='" + m.key + "'>Zoom to</a>" : "") +
        "<a href='#' data-drop-run='" + m.key + "'>Remove</a></div>";
    }

    panel.addEventListener("change", function (e) {
      var t = e.target;
      if (!t) { return; }
      if (t.name === "hr-tol") {
        state.tol = Number(t.value);
        renderPanel();
        schedule();
      } else if (t.name === "hr-scope") {
        state.scope = t.value;
      }
    });
    panel.addEventListener("click", function (e) {
      var a = e.target.closest ? e.target.closest("[data-run],[data-zoom-run],[data-drop-run],[data-clear-runs],[data-zoom-vpu],[data-zoom-qa]") : null;
      if (!a) { return; }
      e.preventDefault();
      if (a.hasAttribute("data-run")) {
        startRun(a.getAttribute("data-run"));
      } else if (a.hasAttribute("data-zoom-run")) {
        var b = results[a.getAttribute("data-zoom-run")].getBounds();
        if (b.isValid()) { map.fitBounds(b, {padding: [30, 30], maxZoom: 16}); }
      } else if (a.hasAttribute("data-drop-run")) {
        dropRun(a.getAttribute("data-drop-run"));
        renderPick();
        renderRuns();
      } else if (a.hasAttribute("data-clear-runs")) {
        Object.keys(state.runs).forEach(dropRun);
        renderPick();
        renderRuns();
      } else if (a.hasAttribute("data-zoom-vpu")) {
        var v = state.summary.vpus[Number(a.getAttribute("data-zoom-vpu"))];
        map.fitBounds([[v.bounds[1], v.bounds[0]], [v.bounds[3], v.bounds[2]]]);
      } else {
        var q = state.qaList[Number(a.getAttribute("data-zoom-qa"))].box;
        map.fitBounds([[q[1], q[0]], [q[3], q[2]]]);
      }
    });

    // ------------------------------------------------------------ start
    renderPanel();
    renderStatus();
    getJSON(api("health"), {cache: "no-store"}).then(function (h) {
      if (!h.ok) { state.loadError = h.message || "No slim data."; renderPanel(); return; }
      state.summary = h.dataset;
      state.tol = h.dataset.defaultTolerance;
      var all = null;
      h.dataset.vpus.forEach(function (v) {
        var b = L.latLngBounds([v.bounds[1], v.bounds[0]], [v.bounds[3], v.bounds[2]]);
        L.rectangle(b, {color: "#64748b", weight: 1, dashArray: "6 4", fill: false, interactive: false}).addTo(regions);
        L.marker(b.getCenter(), {icon: L.divIcon({className: "hr-region-label", html: esc(v.vpu)}), interactive: false}).addTo(regions);
        (v.qaBoxes || []).forEach(function (q) {
          L.rectangle([[q[1], q[0]], [q[3], q[2]]], {color: "#dc2626", weight: 1, dashArray: "2 4", fill: false, interactive: false}).addTo(qaBoxes);
        });
        all = all ? all.extend(b) : b;
      });
      map.invalidateSize();
      // Fit to the data only if nobody has moved the map while the summary loaded.
      if (all && !state.userMoved) { map.fitBounds(all, {padding: [20, 20]}); }
      renderPanel();
      schedule();
    }).catch(function (err) {
      state.loadError = "The data service did not answer: " + (err.message || err);
      renderPanel();
    });
  }
})();
