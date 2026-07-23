/* EASI Assessment worksheet — client-side navigation + in-card source swap.
 *
 * The middle panel (fn_panel) is server-isolated so it never re-renders on stepping;
 * this file delegates the discrete actions to single Shiny.setInputValue channels:
 *   - [data-step]        click  -> step_nav <stepKey>          (both steppers)
 *   - [data-nav]         click  -> nav_move {d}                (Previous / Next)
 *   - .sfari-nav-fn      click  -> nav_jump {i}                (rail function jump)
 *   - [data-report]      click  -> open_report_evt {}          (Open report)
 *   - [data-suggest]     click  -> override_set {mid, rating: "auto"}  (restore desktop rating)
 *   - [data-xs-view]     click  -> Plotly.relayout on the cross-section (home / extents)
 *   - .easi-src-sel      change -> source_set {mid, source}    (data-source swap)
 * Also injects "Zoom Home" / "Zoom to Extents" buttons into the cross-section plot's own
 * Plotly modebar (config can't carry JS click handlers through shinywidgets).
 * Rating (.easi-rate-sel) and notes (.easi-note-ta) are handled by report-edit.js.
 * Event delegation on document keeps it working across fn_panel re-renders.
 */
(function () {
  "use strict";

  function send(name, payload) {
    if (window.Shiny && Shiny.setInputValue) {
      Shiny.setInputValue(name, Object.assign({ _t: Date.now() }, payload || {}),
                          { priority: "event" });
    }
  }
  function scrollPanelTop() {
    var p = document.querySelector(".sfari-fnpanel");
    if (p) p.scrollTop = 0;
  }

  document.addEventListener("click", function (e) {
    var t = e.target;
    if (!t || !t.closest) return;

    var step = t.closest("[data-step]");
    if (step && window.Shiny && Shiny.setInputValue) {
      e.preventDefault();
      // event priority re-fires even when the target step is unchanged (repeat clicks)
      Shiny.setInputValue("step_nav", step.getAttribute("data-step"), { priority: "event" });
      return;
    }

    var nav = t.closest("[data-nav]");
    if (nav) {
      if (nav.hasAttribute("disabled")) return;
      scrollPanelTop();
      send("nav_move", { d: parseInt(nav.getAttribute("data-nav"), 10) || 0 });
      return;
    }

    var report = t.closest("[data-report]");
    if (report) { send("open_report_evt", {}); return; }

    var sug = t.closest("[data-suggest]");
    if (sug) {
      // "auto" is not a pickable rating, so the server clears the override (see _apply_override)
      send("override_set", { mid: sug.getAttribute("data-suggest"), rating: "auto" });
      return;
    }

    var view = t.closest("[data-xs-view]");
    if (view) {
      // Client-side cross-section view controls (no reliable FigureWidget _rangeInitial; Python
      // range traits go stale after a front-end zoom). "Zoom to Extents" autoranges the whole
      // transect; "Zoom Home" reframes to the windowed default published in #xs_window_range.
      var wrap = view.closest(".easi-xs-in-card") || document;
      var gd = wrap.querySelector(".js-plotly-plot");
      if (gd && window.Plotly && Plotly.relayout) {
        if (view.getAttribute("data-xs-view") === "full") {
          Plotly.relayout(gd, { "xaxis.autorange": true, "yaxis.autorange": true });
        } else {
          var el = document.getElementById("xs_window_range");
          var win = null;
          try { win = JSON.parse((el && el.textContent) || "null"); } catch (err) { win = null; }
          if (win && win.x && win.y) {
            Plotly.relayout(gd, { "xaxis.autorange": false, "yaxis.autorange": false,
                                  "xaxis.range": win.x.slice(), "yaxis.range": win.y.slice() });
          }
        }
      }
      return;
    }

    var fnItem = t.closest(".sfari-nav-fn");
    if (fnItem && fnItem.hasAttribute("data-idx")) {
      scrollPanelTop();
      send("nav_jump", { i: parseInt(fnItem.getAttribute("data-idx"), 10) || 0 });
      return;
    }
  });

  document.addEventListener("change", function (e) {
    var s = e.target;
    if (s && s.classList && s.classList.contains("easi-src-sel")) {
      send("source_set", { mid: s.getAttribute("data-mid"), source: s.value });
    }
  });

  // ---- cross-section modebar: inject "Zoom Home" / "Zoom to Extents" into the plot's own
  // Plotly modebar. Custom modebar buttons need real JS click handlers, which shinywidgets'
  // JSON config can't carry, so the buttons are DOM-injected (with Plotly's stock icons) and
  // serviced by the delegated [data-xs-view] click handler above. The MutationObserver
  // re-injects after every widget remount (fn_panel re-renders per XS-card visit) and any
  // modebar rebuild; injection is idempotent via the [data-xs-view] guard.
  function xsIcon(icon) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 " + (icon.width || 1000) + " " + (icon.height || 1000));
    svg.setAttribute("class", "icon");
    svg.setAttribute("height", "1em");
    svg.setAttribute("width", "1em");
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", icon.path);
    if (icon.transform) path.setAttribute("transform", icon.transform);
    svg.appendChild(path);
    return svg;
  }

  function xsInjectModebar() {
    var icons = window.Plotly && Plotly.Icons;
    if (!icons) return;
    document.querySelectorAll(".easi-xs-in-card .js-plotly-plot .modebar").forEach(function (mb) {
      if (mb.querySelector("[data-xs-view]")) return;       // already injected
      var groups = mb.querySelectorAll(".modebar-group");
      var host = groups.length ? groups[groups.length - 1] : mb;   // join the zoom +/- group
      [["reset", "Zoom Home (framed default view)", icons.home],
       ["full", "Zoom to Extents (full transect)", icons.autoscale]].forEach(function (b) {
        var a = document.createElement("a");
        a.setAttribute("rel", "tooltip");
        a.className = "modebar-btn";
        a.setAttribute("data-title", b[1]);
        a.setAttribute("data-xs-view", b[0]);
        if (b[2] && b[2].path) a.appendChild(xsIcon(b[2]));
        else a.textContent = b[0] === "reset" ? "⌂" : "⛶";
        host.appendChild(a);
      });
    });
  }

  new MutationObserver(function () { xsInjectModebar(); })
    .observe(document.body, { childList: true, subtree: true });
  xsInjectModebar();
})();
