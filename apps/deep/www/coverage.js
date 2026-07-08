/* DEEP available-assessment coverage: a floating collapsible panel of per-assessment
 * visibility toggles + client-side, non-interactive outline layers.
 *
 * Modeled on hype-app (www/map_bounds.js map capture + www/tree.js panel chrome). The
 * coverage outlines are drawn as RAW Leaflet layers on the captured map (window.__deepMap),
 * NOT ipyleaflet layers — so they stay out of the LayersControl (the basemaps button) and are
 * interactive:false: they never intercept the click used to place a survey point.
 *
 * Server contract: on shiny:connected we post `coverage_ready`; the server replies with a
 * `deep_coverage` custom message {features:[{assessmentId, name, region, version, geometry}]}.
 * We render one checkbox row per feature into #deep-cov-body and add its outline layer; a
 * checkbox toggles that layer's visibility. No features -> the panel stays hidden.
 */
(function () {
  "use strict";

  // Bright azure reads on both USGS topo (light) and USGS imagery (dark); fill:false so the
  // interior passes clicks through to place a survey point.
  var OUTLINE = { color: "#1f9dff", weight: 3, opacity: 1, fill: false };
  var ZOOM_SVG = "<svg viewBox='0 0 16 16' width='12' height='12' fill='none' " +
    "stroke='currentColor' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'>" +
    "<path d='M6 2H2v4M10 2h4v4M10 14h4v-4M6 14H2v-4'/></svg>";
  var layers = {};      // assessmentId -> L.GeoJSON (client layer)
  var pending = null;   // features awaiting the map handle
  var gotCoverage = false;   // set once the server answers the ready handshake

  // ---- capture the Leaflet map (jupyter-leaflet bundle, no global registry) ----
  function attach(map) {
    if (window.__deepMap === map) return;
    window.__deepMap = map;
    if (pending) { var f = pending; pending = null; drawAll(f); }
  }
  function lateCapture() {
    if (window.__deepMap || !window.L || !window.L.Evented) return;
    var orig = window.L.Evented.prototype.fire;
    window.L.Evented.prototype.fire = function () {
      if (this instanceof window.L.Map) attach(this);
      return orig.apply(this, arguments);
    };
    var cont = document.querySelector(".leaflet-container");
    if (cont) cont.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: 5, clientY: 5 }));
    window.L.Evented.prototype.fire = orig;
  }
  var hooked = false, tries = 0;
  var capT = setInterval(function () {
    tries += 1;
    if (!hooked && window.L && window.L.Map && window.L.Map.addInitHook) {
      hooked = true;
      window.L.Map.addInitHook(function () { attach(this); });
      lateCapture();
    }
    if (hooked && !window.__deepMap) lateCapture();
    if (window.__deepMap || tries > 150) clearInterval(capT);   // give up after ~30s
  }, 200);

  // ---- draw / toggle client layers ----
  function drawAll(features) {
    var map = window.__deepMap;
    if (!map) { pending = features; return; }   // draw once the map arrives
    features.forEach(function (f) {
      var aid = f.assessmentId;
      if (!aid || layers[aid] || !f.geometry) return;
      try {
        // interactive (default) so the OUTLINE shows a hover tooltip; interior is fill:false,
        // so clicks pass through to place a survey point.
        var lyr = window.L.geoJSON({ type: "Feature", geometry: f.geometry },
          { style: function () { return OUTLINE; } });
        lyr.bindTooltip(String(f.name || f.region || aid),
          { sticky: true, direction: "top", opacity: 0.95 });
        layers[aid] = lyr;
      } catch (e) { /* bad geometry — skip */ }
    });
    syncFromChecks();
  }
  function syncFromChecks() {
    var map = window.__deepMap;
    if (!map) return;
    document.querySelectorAll("#deep-cov-body [data-aid]").forEach(function (cb) {
      var lyr = layers[cb.getAttribute("data-aid")];
      if (!lyr) return;
      if (cb.checked) { if (!map.hasLayer(lyr)) map.addLayer(lyr); }
      else if (map.hasLayer(lyr)) { map.removeLayer(lyr); }
    });
  }

  // ---- dock the panel into the leaflet top-right control stack ----
  // Appended AFTER the LayersControl so it stacks BELOW the basemaps button; leaflet's control
  // flow auto-spaces them (the panel flows down when the layers list expands) — no overlap.
  var docked = false;
  function dockPanel() {
    if (docked) return true;
    var panel = document.getElementById("deep-cov-panel");
    var corner = document.querySelector(".leaflet-control-container .leaflet-top.leaflet-right")
              || document.querySelector(".leaflet-top.leaflet-right");
    if (!panel || !corner) return false;
    // Wait for the LayersControl so appendChild lands the panel AFTER it (below the button);
    // ipyleaflet adds that control asynchronously, so we may be called before it exists.
    if (!corner.querySelector(".leaflet-control-layers")) return false;
    panel.classList.add("leaflet-control");
    corner.appendChild(panel);
    if (window.L && window.L.DomEvent) {
      window.L.DomEvent.disableClickPropagation(panel);
      window.L.DomEvent.disableScrollPropagation(panel);
    }
    panel.classList.add("is-ready");   // reveal the collapsed chip as soon as we dock
    docked = true;
    return true;
  }

  // ---- render the panel body (client-owned DOM) ----
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function renderPanel(features) {
    var panel = document.getElementById("deep-cov-panel");
    var body = document.getElementById("deep-cov-body");
    if (!panel || !body) return;
    dockPanel();   // relocate under the top-right layers button (falls back to CSS absolute)
    panel.classList.add("is-ready");   // always visible; an empty library shows an empty state
    body.textContent = "";
    if (!features || !features.length) {
      var empty = document.createElement("div");
      empty.className = "deep-cov-empty";
      empty.textContent = "No published assessments yet";
      body.appendChild(empty);
      return;
    }

    // master "toggle all on/off" row
    var master = document.createElement("label");
    master.className = "deep-cov-lbl deep-cov-all";
    var mcb = document.createElement("input");
    mcb.type = "checkbox"; mcb.checked = true; mcb.id = "deep-cov-all";
    mcb.addEventListener("change", function () {
      document.querySelectorAll("#deep-cov-body input[data-aid]").forEach(function (cb) {
        cb.checked = mcb.checked;
      });
      mcb.indeterminate = false;
      syncFromChecks();
    });
    var mtxt = document.createElement("span");
    mtxt.className = "deep-cov-text";
    mtxt.innerHTML = "<span class='deep-cov-name'>All assessments</span>";
    master.appendChild(mcb); master.appendChild(mtxt);
    body.appendChild(master);

    features.forEach(function (f) {
      var row = document.createElement("div");
      row.className = "deep-cov-row";
      var lbl = document.createElement("label");
      lbl.className = "deep-cov-lbl";
      var cb = document.createElement("input");
      cb.type = "checkbox"; cb.checked = true;
      cb.setAttribute("data-aid", f.assessmentId || "");
      cb.addEventListener("change", function () { syncFromChecks(); syncMaster(); });
      var sub = [];
      if (f.region) sub.push(f.region);
      if (f.version) sub.push("v" + f.version);
      var txt = document.createElement("span");
      txt.className = "deep-cov-text";
      txt.innerHTML = "<span class='deep-cov-name'>" + esc(f.name || f.assessmentId || "assessment") +
        "</span>" + (sub.length ? "<span class='deep-cov-sub'>" + esc(sub.join(" · ")) + "</span>" : "");
      lbl.appendChild(cb); lbl.appendChild(txt);
      var zoom = document.createElement("button");
      zoom.type = "button"; zoom.className = "deep-cov-zoom"; zoom.title = "Zoom to extent";
      zoom.innerHTML = ZOOM_SVG;
      (function (aid) {
        zoom.addEventListener("click", function (e) { e.preventDefault(); zoomTo(aid); });
      })(f.assessmentId);
      row.appendChild(lbl); row.appendChild(zoom);
      body.appendChild(row);
    });
    syncMaster();
  }

  function zoomTo(aid) {
    var map = window.__deepMap, lyr = layers[aid];
    if (!map || !lyr || !lyr.getBounds) return;
    try {
      var b = lyr.getBounds();
      if (b && b.isValid()) map.fitBounds(b, { padding: [26, 26] });
    } catch (e) { /* not ready */ }
  }

  function syncMaster() {
    var mcb = document.getElementById("deep-cov-all");
    if (!mcb) return;
    var boxes = [].slice.call(document.querySelectorAll("#deep-cov-body input[data-aid]"));
    var on = boxes.filter(function (b) { return b.checked; }).length;
    mcb.checked = boxes.length > 0 && on === boxes.length;
    mcb.indeterminate = on > 0 && on < boxes.length;
  }

  function onCoverage(msg) {
    gotCoverage = true;
    var features = (msg && msg.features) || [];
    renderPanel(features);
    drawAll(features);
  }

  // ---- collapse chrome (delegated; mirrors hype-app tree.js initChrome) ----
  function initChrome() {
    document.addEventListener("click", function (e) {
      var head = e.target.closest && e.target.closest(".deep-cov-head");
      if (!head) return;
      var panel = head.closest(".deep-cov-panel");
      if (panel) panel.classList.toggle("collapsed");
    });
  }

  function register() {
    if (window.Shiny && Shiny.addCustomMessageHandler) {
      Shiny.addCustomMessageHandler("deep_coverage", onCoverage);
      return true;
    }
    return false;
  }
  function ready() {
    if (window.Shiny && Shiny.setInputValue) {
      Shiny.setInputValue("coverage_ready", Date.now(), { priority: "event" });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initChrome);
  } else {
    initChrome();
  }
  if (!register()) document.addEventListener("shiny:connected", register);
  document.addEventListener("shiny:connected", ready);
  // Fallback: this deferred script can attach its shiny:connected listener AFTER the event
  // already fired (so `ready` never runs). Poll ready() until the server answers (onCoverage
  // is idempotent) or we give up (~6s).
  var readyTries = 0;
  var readyTimer = setInterval(function () {
    if (gotCoverage || readyTries > 20) { clearInterval(readyTimer); return; }
    readyTries += 1;
    ready();
  }, 300);
  // Dock the panel under the layers button once the LayersControl exists (added async after
  // the map mounts). Retry until docked or we give up (~8s).
  var dockTries = 0;
  var dockTimer = setInterval(function () {
    if (dockPanel() || dockTries > 40) { clearInterval(dockTimer); return; }
    dockTries += 1;
  }, 200);
})();
