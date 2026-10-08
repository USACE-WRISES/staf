/* DEEP's assessment regions (owner, 2026-10-05): a right sidebar that lists the available
 * assessments by their region, with a search, and the regions themselves on DEEP's map.
 *
 * The map. One polygon per region, drawn with the Leaflet copy that made DEEP's map, in a pane
 * under the streams (deep-regions, z 380). Zoomed out on the Identify step the regions are ACTIVE:
 * hovering anywhere inside one highlights it and names it (one shared tooltip; a per-path
 * bindTooltip makes every path focusable, and the browser drew a focus box around a clicked one),
 * and a click selects it and zooms in: to the whole region when it is bigger than the view, else
 * two levels at the click, up to where streams show. A click on a region never reaches the map,
 * so it never starts a stream pick. From the stream zoom (the panel's data-flow-zoom) on, or on
 * any other step, they are PASSIVE: a faint dashed outline that takes no pointer events (the
 * pane's is-passive class), so every click goes to the streams. The Layers menu gets an
 * "Assessment regions" checkbox (app-owned, like legend-dock.js's StreamCat coverage).
 *
 * The sidebar (markup in app.py _tool_body, layout in deep.css): a search over the region names
 * (an all-digit query matches the Level III code exactly), then one row per region: its name, its
 * status (Draft, Preliminary or Final, in its map color), its version and "In use" for the
 * assessment the site uses. Hovering a row
 * highlights its region; clicking it selects the region and fits the map to it. The search
 * filters the map too. Open or closed is the deep-cov-open class on DEEP's body (.easi-shell),
 * remembered per browser; the map's right-hand controls make room (deep.css). Closed, the
 * sidebar is a button in the map's top-right controls, under Layers, with the region count: it is
 * docked into Leaflet's corner (as legend-dock.js docks the legend), because the map is its own
 * stacking context and anything floated over it would cover the Layers menu.
 *
 * Server contract: the page posts `coverage_ready` (DEEP's own input) until the server answers.
 * `deep_coverage` brings {features:[{assessmentId, name, code, version, status, lifecycle,
 * certified, geometry}]} once (lifecycle: draft | preliminary | certified; status is its label);
 * `deep_coverage_current` brings {assessmentId, identify, focus} whenever the
 * assessment in use or the step changes (focus: show that region once, for a link). The Layers
 * checkbox also hides the legend's region key (the deep-regions-off class on DEEP's body).
 *
 * In the STAF app DEEP shares the page with EASI and SFARI, whose maps may come first: DEEP's map
 * is the Leaflet map whose container sits in DEEP's body (staf/staf-ns.js), and the capture and the
 * handshake start again each time DEEP is shown (the shell's staf:tool-shown event).
 */
(function () {
  "use strict";

  var TOOL = "deep", NS = window.STAFNs;
  var PANE = "deep-regions", STORE = "staf.deep.regionsOpen", PHONE = 820;
  function root() { return NS.tool(TOOL); }
  function body() { return NS.scope(root()); }
  function shell() { return root() || document.querySelector(".easi-shell"); }

  // A region's colors are its status's (owner, 2026-10-08): Draft gray (the warm grey measured
  // for the topo basemap, with a slate outline), Preliminary amber, Final blue. app.py
  // REGION_STATUS_STYLE holds the same colors and tests/test_region_status.py keeps the two equal.
  var STATUS = {
    draft: { label: "Draft", line: "#475569", fill: "#6b6459", chip: "deep-cov-draft" },
    preliminary: { label: "Preliminary", line: "#b45309", fill: "#d97706", chip: "deep-cov-prelim" },
    certified: { label: "Final", line: "#303f9f", fill: "#3f51b5", chip: "deep-cov-final" },
  };
  // Styles, by state: an active or passive region takes its status's colors (a 0.12 fill moves the
  // basemap's luminance by ~14, a light shading far below the delineated watershed's 0.40
  // yellow); hover and selection are navy whatever the status.
  var STYLE = {
    active: { weight: 1, opacity: 0.8, fillOpacity: 0.12, dashArray: null },
    hover: { color: "#2f4b7c", weight: 2.25, opacity: 1, fillOpacity: 0.22, dashArray: null },
    selected: { color: "#2f4b7c", weight: 2.5, opacity: 1, fillColor: "#2f4b7c", fillOpacity: 0.14, dashArray: null },
    selectedHover: { color: "#2f4b7c", weight: 2.5, opacity: 1, fillColor: "#2f4b7c", fillOpacity: 0.22, dashArray: null },
    passive: { weight: 1.25, opacity: 0.55, fillOpacity: 0, dashArray: "6 5" },
    passiveSelected: { color: "#2f4b7c", weight: 2, opacity: 0.9, fillOpacity: 0, dashArray: null },
  };
  // a feature's status: its lifecycle, else (a server from before Drafts showed) Final when certified
  function statusOf(f) {
    if (f && STATUS[f.lifecycle]) return f.lifecycle;
    return f && f.certified ? "certified" : "preliminary";
  }

  var features = [], byId = {}, layers = {}, rows = {};
  var map = null, Lm = null, pane = null, tip = null, setupDone = false;
  var selected = null, inUse = null, hovered = null;
  var query = "", visible = true, identify = true, passive = null, focusWanted = false;
  var gotCoverage = false, queryTimer = null;

  function flowZoom() {
    var panel = document.getElementById("deep-cov-panel");
    var z = panel && parseInt(panel.getAttribute("data-flow-zoom"), 10);
    return z > 0 ? z : 14;
  }
  function phone() { return (window.innerWidth || 0) <= PHONE; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  // ---- capture DEEP's Leaflet map (jupyter-leaflet keeps no registry of maps) ----
  // Every map made on the page is seen with the Leaflet copy that made it; DEEP's is the one whose
  // container sits in DEEP's body (on a page without tool bodies, the first one on the page).
  var seen = [];
  function inDeep(m) {
    try {
      var r = root(), c = m.getContainer();
      return r ? r.contains(c) : !!(c && c.isConnected);
    } catch (e) { return false; }
  }
  function resolveMap() {
    if (!map) {
      for (var i = 0; i < seen.length; i++) {
        if (inDeep(seen[i].map)) { map = seen[i].map; Lm = seen[i].L; window.__deepMap = map; break; }
      }
    }
    if (map) setup();
    return !!map;
  }
  function attach(m, L) {
    for (var i = 0; i < seen.length; i++) if (seen[i].map === m) { resolveMap(); return; }
    seen.push({ map: m, L: L });
    resolveMap();
  }
  function lateCapture() {
    var L = window.L;
    if (map || !L || !L.Evented) return;
    var cont = body().querySelector(".leaflet-container");
    if (!cont) return;
    var orig = L.Evented.prototype.fire;
    L.Evented.prototype.fire = function () {
      if (this instanceof L.Map) attach(this, L);
      return orig.apply(this, arguments);
    };
    cont.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: 5, clientY: 5 }));
    L.Evented.prototype.fire = orig;
  }
  // EASI's Nationwide viewer brings its own Leaflet: hook every window.L that turns up.
  var hooked = [], captureTimer = null;
  function capture() {
    if (captureTimer || map) return;
    var tries = 0;
    captureTimer = setInterval(function () {
      tries += 1;
      var L = window.L;
      if (L && L.Map && L.Map.addInitHook && hooked.indexOf(L) < 0) {
        hooked.push(L);
        L.Map.addInitHook(function () { attach(this, L); });
      }
      if (hooked.length && !resolveMap()) lateCapture();
      if (map || tries > 150) { clearInterval(captureTimer); captureTimer = null; }   // ~30 s
    }, 200);
  }

  // ---- the regions on the map ----
  function setup() {
    if (setupDone || !map || !Lm) return;
    setupDone = true;
    pane = map.getPane(PANE) || map.createPane(PANE);
    pane.style.zIndex = 380;            // above the tiles, under the streams (385-395) and overlays (400)
    tip = Lm.tooltip({ direction: "top", offset: [0, -12], opacity: 1, className: "deep-reg-tip" });
    map.on("zoomend", applyMode);
    map.on("zoomstart movestart", leave);
    draw();
    applyMode();
    applyCurrent();
  }

  function styleFor(aid) {
    var on = aid === selected, s = STATUS[statusOf(byId[aid])];
    // passive, only a row of the list can hover a region: it shows the way a selected one does
    if (passive) {
      return on || aid === hovered ? STYLE.passiveSelected : Object.assign({}, STYLE.passive, { color: s.line });
    }
    if (aid === hovered) return on ? STYLE.selectedHover : Object.assign({}, STYLE.hover, { fillColor: s.fill });
    return on ? STYLE.selected : Object.assign({}, STYLE.active, { color: s.line, fillColor: s.fill });
  }
  function restyle(aid) {
    var lyr = layers[aid];
    if (lyr) lyr.setStyle(styleFor(aid));
  }
  function restyleAll() { Object.keys(layers).forEach(restyle); }

  function draw() {
    if (!map || !Lm) return;
    features.forEach(function (f) {
      var aid = f.assessmentId;
      if (layers[aid] || !f.geometry) return;
      try {
        var lyr = Lm.geoJSON({ type: "Feature", geometry: f.geometry }, {
          pane: PANE, interactive: true, bubblingMouseEvents: false,
          style: function () { return styleFor(aid); },
        });
        lyr.on("mouseover", function (e) { hover(aid, e && e.latlng); });
        lyr.on("mousemove", function (e) { if (hovered === aid && tip && e && e.latlng) tip.setLatLng(e.latlng); });
        lyr.on("mouseout", function () { unhover(aid); });
        lyr.on("click", function (e) {
          if (Lm.DomEvent && e) Lm.DomEvent.stopPropagation(e);
          clickRegion(aid, e && e.latlng);
        });
        layers[aid] = lyr;
      } catch (err) { /* a malformed outline is skipped */ }
    });
    applyFilter();
    if (selected && layers[selected] && layers[selected].bringToFront) layers[selected].bringToFront();
  }

  function applyMode() {
    var z = map && map.getZoom ? map.getZoom() : null;
    var next = !identify || (typeof z === "number" && z >= flowZoom());
    if (next === passive) return;
    passive = next;
    if (pane) pane.classList.toggle("is-passive", passive);
    leave();
    restyleAll();
  }

  // ``latlng``: the pointer over the map (opens the region's name there); a row hovers without one
  function hover(aid, latlng) {
    if (!aid || (passive && latlng)) return;
    if (hovered && hovered !== aid) unhover(hovered);
    hovered = aid;
    restyle(aid);
    markRow(aid, "is-hover", true);
    var f = byId[aid];
    if (latlng && tip && map && f) {
      tip.setContent("<b>" + esc(f.name) + "</b><span>" + esc(meta(f)) + "</span>");
      tip.setLatLng(latlng);
      map.openTooltip(tip);
    }
  }
  function unhover(aid) {
    if (hovered !== aid) return;
    hovered = null;
    restyle(aid);
    markRow(aid, "is-hover", false);
    if (tip && map) map.closeTooltip(tip);
  }
  function leave() {
    if (hovered) unhover(hovered);
    else if (tip && map) map.closeTooltip(tip);
  }

  // where a fitted region can sit: clear of the left pane, the open sidebar and the zoom cue
  function padding() {
    var tl = [24, 56], br = [24, 24];
    if (!map || phone()) return { tl: tl, br: br };
    var box = map.getContainer().getBoundingClientRect();
    var left = body().querySelector(".easi-leftpane");
    var lr = left && left.getBoundingClientRect();
    if (lr && lr.width) tl[0] = Math.max(tl[0], lr.right - box.left + 16);
    var panel = document.getElementById("deep-cov-panel");
    var pr = isOpen() && panel && panel.getBoundingClientRect();
    if (pr && pr.width) br[0] = Math.max(br[0], box.right - pr.left + 16);
    return { tl: tl, br: br };
  }
  function fitTo(aid) {
    var lyr = layers[aid];
    if (!map || !lyr || !lyr.getBounds) return;
    try {
      var b = lyr.getBounds(), pad = padding();
      if (b && b.isValid()) {
        map.fitBounds(b, { paddingTopLeft: pad.tl, paddingBottomRight: pad.br, maxZoom: flowZoom() - 1 });
      }
    } catch (e) { /* not ready */ }
  }
  // a click on an active region: fit a region bigger than the view, else zoom in at the click
  function clickRegion(aid, latlng) {
    if (passive || !map) return;
    select(aid, { reveal: true });
    var lyr = layers[aid], z = map.getZoom(), flow = flowZoom();
    try {
      var b = lyr.getBounds(), pad = padding();
      var fit = map.getBoundsZoom(b, false, Lm.point(pad.tl[0] + pad.br[0], pad.tl[1] + pad.br[1]));
      if (Math.min(fit, flow - 1) > z + 0.25) {
        map.fitBounds(b, { paddingTopLeft: pad.tl, paddingBottomRight: pad.br, maxZoom: flow - 1 });
        return;
      }
    } catch (e) { /* zoom in at the click instead */ }
    if (latlng) map.setView(latlng, Math.min(z + 2, flow));
  }

  function select(aid, opts) {
    var prev = selected;
    selected = aid || null;
    if (prev && prev !== selected) restyle(prev);
    if (selected) {
      restyle(selected);
      if (layers[selected] && layers[selected].bringToFront) layers[selected].bringToFront();
      if (!visible) setVisible(true);       // a chosen region always shows
    }
    Object.keys(rows).forEach(function (id) {
      var on = id === selected;
      rows[id].classList.toggle("is-selected", on);
      if (on) rows[id].setAttribute("aria-current", "true"); else rows[id].removeAttribute("aria-current");
    });
    applyFilter();
    if (opts && opts.reveal && selected && rows[selected] && isOpen() && rows[selected].scrollIntoView) {
      rows[selected].scrollIntoView({ block: "nearest" });
    }
  }

  // ---- the filter: the search narrows the list and the map alike ----
  function matches(f) {
    if (!query) return true;
    if (/^\d+$/.test(query)) return String(f.code) === query;
    return f.name.toLowerCase().indexOf(query) >= 0;
  }
  function applyFilter() {
    var shown = 0;
    features.forEach(function (f) {
      var aid = f.assessmentId, on = matches(f);
      if (on) shown += 1;
      if (rows[aid]) rows[aid].hidden = !on;
      var lyr = layers[aid];
      if (!lyr || !map) return;
      var want = visible && (on || aid === selected || aid === inUse);
      if (want && !map.hasLayer(lyr)) map.addLayer(lyr);
      else if (!want && map.hasLayer(lyr)) { if (hovered === aid) unhover(aid); map.removeLayer(lyr); }
    });
    var label = query ? shown + " of " + features.length : String(features.length);
    var count = document.querySelector("#deep-cov-panel .deep-cov-count");
    if (count) count.textContent = label;
    var tabCount = tab() && tab().querySelector(".deep-cov-tab-count");   // closed, it says a filter is on
    if (tabCount) tabCount.textContent = label;
    var empty = document.querySelector("#deep-cov-body .deep-cov-empty");
    if (empty) {
      empty.hidden = !(features.length && shown === 0);
      if (!empty.hidden) empty.textContent = "No ecoregion matches “" + query + "”";
    }
    return shown;
  }
  function setQuery(text) {
    query = String(text || "").trim().toLowerCase();
    var box = document.querySelector("#deep-cov-body .deep-cov-search");
    if (box) box.classList.toggle("has-query", !!query);
    applyFilter();
  }
  function setVisible(on) {
    visible = !!on;
    if (!visible) leave();
    document.querySelectorAll(".deep-regions-toggle").forEach(function (box) { box.checked = visible; });
    var s = shell();
    if (s) s.classList.toggle("deep-regions-off", !visible);   // the legend's region key goes too
    applyFilter();
  }

  // ---- the sidebar ----
  function meta(f) {
    var parts = [];
    if (f.version != null && f.version !== "") parts.push("v" + f.version);
    if (f.status) parts.push(f.status);
    return parts.join(" · ");
  }
  function markRow(aid, cls, on) { if (rows[aid]) rows[aid].classList.toggle(cls, on); }
  function markInUse() {
    Object.keys(rows).forEach(function (id) { rows[id].classList.toggle("is-in-use", id === inUse); });
  }
  function firstShown() {
    for (var i = 0; i < features.length; i++) if (matches(features[i])) return features[i].assessmentId;
    return null;
  }
  function choose(aid) {
    select(aid);
    fitTo(aid);
    if (phone()) setOpen(false);          // on a phone the list covers the map: hand it back
  }

  function renderPanel() {
    var panelBody = document.getElementById("deep-cov-body");
    if (!panelBody) return;
    panelBody.innerHTML = "";
    rows = {};
    if (!features.length) {
      panelBody.appendChild(el("div", "deep-cov-empty", "No published assessments yet"));
      applyFilter();
      return;
    }
    var search = el("div", "deep-cov-search");
    var icon = el("span", "deep-cov-search-icon");
    icon.innerHTML = "<svg viewBox='0 0 16 16' width='14' height='14' aria-hidden='true' fill='none' " +
      "stroke='currentColor' stroke-width='1.6' stroke-linecap='round'><circle cx='7' cy='7' r='4.6'/>" +
      "<path d='M10.4 10.4L14 14'/></svg>";
    var input = el("input", "deep-cov-q");
    input.setAttribute("type", "search");
    input.setAttribute("placeholder", "Search ecoregions");
    input.setAttribute("aria-label", "Search ecoregions");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("spellcheck", "false");
    input.setAttribute("data-shiny-no-bind-input", "");   // the page's own control, never a Shiny input
    input.addEventListener("input", function () {
      if (queryTimer) clearTimeout(queryTimer);
      queryTimer = setTimeout(function () { queryTimer = null; setQuery(input.value); }, 90);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        if (queryTimer) { clearTimeout(queryTimer); queryTimer = null; }
        setQuery(input.value);
        var aid = firstShown();
        if (aid) choose(aid);
        if (e.preventDefault) e.preventDefault();
      } else if (e.key === "Escape") {
        if (input.value) { input.value = ""; setQuery(""); }
        else { setOpen(false, { remember: true, returnFocus: true }); }
        if (e.preventDefault) e.preventDefault();
        if (e.stopPropagation) e.stopPropagation();
      }
    });
    var clear = el("button", "deep-cov-clear");
    clear.setAttribute("type", "button");
    clear.setAttribute("aria-label", "Clear the search");
    clear.innerHTML = "<svg viewBox='0 0 16 16' width='12' height='12' aria-hidden='true' fill='none' " +
      "stroke='currentColor' stroke-width='1.8' stroke-linecap='round'><path d='M4 4l8 8M12 4l-8 8'/></svg>";
    clear.addEventListener("click", function () {
      input.value = ""; setQuery("");
      if (input.focus) input.focus();
    });
    search.appendChild(icon); search.appendChild(input); search.appendChild(clear);
    panelBody.appendChild(search);

    var list = el("div", "deep-cov-list");
    features.forEach(function (f) {
      var aid = f.assessmentId;
      var row = el("button", "deep-cov-row");
      row.setAttribute("type", "button");
      row.setAttribute("data-aid", aid);
      row.setAttribute("title", f.name + (meta(f) ? " (" + meta(f) + ")" : ""));
      var st = STATUS[f.lifecycle];
      row.appendChild(el("span", "deep-cov-name", f.name));
      row.appendChild(el("span", "deep-cov-chip deep-cov-use", "In use"));
      row.appendChild(el("span", "deep-cov-chip " + st.chip, st.label));
      if (f.version != null && f.version !== "") row.appendChild(el("span", "deep-cov-chip deep-cov-ver", "v" + f.version));
      row.addEventListener("mouseenter", function () { hover(aid); });
      row.addEventListener("mouseleave", function () { unhover(aid); });
      row.addEventListener("focus", function () { hover(aid); });
      row.addEventListener("blur", function () { unhover(aid); });
      row.addEventListener("click", function () { choose(aid); });
      rows[aid] = row;
      list.appendChild(row);
    });
    panelBody.appendChild(list);
    var empty = el("div", "deep-cov-empty");
    empty.hidden = true;
    panelBody.appendChild(empty);
    markInUse();
    select(selected);
  }

  // the closed sidebar's button, wherever it is: the server's markup, then the map's controls
  var tabEl = null;
  function tab() {
    if (!tabEl) { var s = shell(); tabEl = (s && s.querySelector(".deep-cov-tab")) || null; }
    return tabEl;
  }
  function isOpen() { var s = shell(); return !!(s && s.classList.contains("deep-cov-open")); }
  function setOpen(open, opts) {
    var s = shell();
    if (!s) return;
    s.classList.toggle("deep-cov-open", !!open);
    var t = tab();
    if (t) t.setAttribute("aria-expanded", open ? "true" : "false");
    if (opts && opts.remember) {
      try { window.localStorage.setItem(STORE, open ? "1" : "0"); } catch (e) { /* storage blocked */ }
    }
    if (open && opts && opts.focus) {
      var q = s.querySelector(".deep-cov-q");
      if (q && q.focus) q.focus();
    }
    if (!open && opts && opts.returnFocus && t && t.focus) t.focus();
  }
  function initiallyOpen() {
    if (phone()) return false;            // on a phone the list covers the map: never at first
    try {
      var v = window.localStorage.getItem(STORE);
      if (v === "1" || v === "0") return v === "1";
    } catch (e) { /* storage blocked: the default */ }
    return (window.innerWidth || 0) >= 1280;
  }

  // ---- the map's top-right controls: the closed sidebar's button and a Layers checkbox ----
  // The button joins the controls between Layers and the legend (deep.css orders the corner's
  // column), so an opened Layers menu pushes it down instead of opening under it; a rebuilt map
  // gets it back. Leaflet then ignores clicks on it: it opens the sidebar, never picks a stream.
  var tabGuarded = false;
  function dockTab() {
    var t = tab(), wrap = body().querySelector(".easi-map-wrap");
    var corner = wrap && wrap.querySelector(".leaflet-top.leaflet-right");
    if (!t || !corner) return false;
    if (t.parentNode !== corner) {
      t.classList.add("leaflet-control");
      corner.appendChild(t);
    }
    var L = Lm || window.L;
    if (!tabGuarded && L && L.DomEvent) {
      tabGuarded = true;
      L.DomEvent.disableClickPropagation(t);
      L.DomEvent.disableScrollPropagation(t);
    }
    return true;
  }

  // The Layers menu gets an app-owned "Assessment regions" checkbox. ipyleaflet rebuilds the
  // control's list on a layer change, so a watcher puts the row back.
  function layersRow() {
    var wrap = body().querySelector(".easi-map-wrap");
    var list = wrap && wrap.querySelector(".leaflet-control-layers-overlays");
    if (!list) return false;
    if (list.querySelector(".deep-regions-control")) return true;
    var row = el("div", "deep-regions-control");
    var label = el("label");
    var box = el("input", "deep-regions-toggle");
    box.setAttribute("type", "checkbox");
    box.type = "checkbox";
    box.checked = visible;
    box.setAttribute("data-shiny-no-bind-input", "");   // not a native layer, not a Shiny input
    box.addEventListener("change", function () { setVisible(box.checked); });
    label.appendChild(box);
    label.appendChild(el("span", null, " Assessment regions"));
    row.appendChild(label);
    list.appendChild(row);
    return true;
  }
  var layersTimer = null, layersObserver = null;
  function controls() {
    var row = layersRow(), docked = dockTab();      // both, every time
    return row && docked;
  }
  function watchControls() {
    var wrap = body().querySelector(".easi-map-wrap");
    if (wrap && !layersObserver && window.MutationObserver) {
      layersObserver = new MutationObserver(function () { controls(); });
      layersObserver.observe(wrap, { childList: true, subtree: true });
    }
    if (controls() || layersTimer) return;
    var tries = 0;
    layersTimer = setInterval(function () {
      tries += 1;
      if (controls() || tries > 75) { clearInterval(layersTimer); layersTimer = null; }
    }, 200);
  }

  // ---- the server's messages ----
  function onCoverage(msg) {
    gotCoverage = true;
    features = ((msg && msg.features) || []).filter(function (f) { return f && f.assessmentId; })
      .map(function (f) {
        var life = statusOf(f);
        return { assessmentId: f.assessmentId, name: String(f.name || f.assessmentId),
                 code: f.code == null ? "" : String(f.code), version: f.version,
                 status: f.status || STATUS[life].label, lifecycle: life, geometry: f.geometry };
      })
      .sort(function (a, b) { return a.name.localeCompare(b.name); });
    byId = {};
    features.forEach(function (f) { byId[f.assessmentId] = f; });
    var wrap = body().querySelector(".deep-cov");
    if (wrap && !wrap.classList.contains("is-ready")) {
      wrap.classList.add("is-ready");
      if (shell()) shell().classList.add("deep-cov-ready");   // the docked button shows from now
      setOpen(initiallyOpen());
    }
    dockTab();
    renderPanel();
    draw();
    applyCurrent();
  }
  var current = { aid: null, identify: true };
  function onCurrent(msg) {
    msg = msg || {};
    current = { aid: msg.assessmentId || null, identify: msg.identify !== false };
    if (msg.focus) focusWanted = true;
    applyCurrent();
  }
  function applyCurrent() {
    identify = current.identify;
    if (inUse !== current.aid) {
      var was = inUse;
      inUse = current.aid;
      markInUse();
      if (inUse) select(inUse);
      else if (selected === was) select(null);
      else applyFilter();
    }
    if (map) applyMode();
    if (focusWanted && inUse && map && layers[inUse]) { focusWanted = false; fitTo(inUse); }
  }

  // ---- open, close, keyboard (delegated: the markup is the server's) ----
  function initChrome() {
    document.addEventListener("click", function (e) {
      var t = e.target && e.target.closest ? e.target : null;
      if (!t) return;
      if (t.closest(".deep-cov-tab")) setOpen(true, { remember: true, focus: true });
      else if (t.closest(".deep-cov-close")) setOpen(false, { remember: true, returnFocus: true });
    });
    document.addEventListener("keydown", function (e) {
      var t = e.target && e.target.closest ? e.target : null;
      if (e.key === "Escape" && t && t.closest("#deep-cov-panel") && !t.closest(".deep-cov-q")) {
        setOpen(false, { remember: true, returnFocus: true });
      }
    });
  }

  function register() {
    if (window.Shiny && Shiny.addCustomMessageHandler) {
      Shiny.addCustomMessageHandler("deep_coverage", onCoverage);
      Shiny.addCustomMessageHandler("deep_coverage_current", onCurrent);
      return true;
    }
    return false;
  }
  function ready() {
    if (window.Shiny && Shiny.setInputValue) {
      Shiny.setInputValue(NS.id(NS.tool(TOOL), "coverage_ready"), Date.now(), { priority: "event" });
    }
  }
  // This deferred script can attach its shiny:connected listener AFTER the event already fired
  // (so `ready` never runs). Ask again until the server answers (it answers once per session)
  // or we give up (~6 s). In the STAF app only while DEEP is shown: its server starts then.
  var readyTimer = null;
  function askForCoverage() {
    var shown = NS.active();
    if (readyTimer || gotCoverage || (shown && shown !== TOOL)) return;
    var readyTries = 0;
    readyTimer = setInterval(function () {
      if (gotCoverage || readyTries > 20) { clearInterval(readyTimer); readyTimer = null; return; }
      readyTries += 1;
      ready();
    }, 300);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initChrome);
  else initChrome();
  if (!register()) document.addEventListener("shiny:connected", register);
  document.addEventListener("shiny:connected", ready);
  // DEEP shown again in the STAF app: its map may only exist now
  document.addEventListener("staf:tool-shown", function (e) {
    if (!e.detail || e.detail.tool !== TOOL) return;
    capture();
    askForCoverage();
    watchControls();
  });
  capture();
  askForCoverage();
  watchControls();
})();
