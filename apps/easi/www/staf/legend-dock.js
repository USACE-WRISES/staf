/* Dock the map legend and add the optional StreamCat coverage view to Layers.
 *
 * ipyleaflet 0.20's native checkboxes do not sync visibility to Python. Coverage
 * is therefore an app-owned checkbox in the same menu, bridged to Shiny. Its
 * state lives for this page session; rebuilding the native control never resets
 * it. The native Streams checkbox continues to own the persistent stream group.
 *
 * The Layers button opens on a click only, never on hover (owner, 2026-10-05:
 * closing DEEP's regions sidebar slid the button under the pointer and the menu
 * sprang open). See clickOnly.
 *
 * A page can hold several tool bodies (the STAF app): each body keeps its own map,
 * legend and coverage choice, and posts to its own tool's inputs (staf-ns.js).
 */
(function () {
  "use strict";

  var NS = window.STAFNs;
  var maps = [];   // one state per tool body; one for a page without bodies
  var sessionReady = false;

  function state(root) {
    for (var i = 0; i < maps.length; i++) if (maps[i].root === root) return maps[i];
    var st = { root: root, coverage: false, streamsVisible: true, sentCoverage: undefined,
               sentStreams: undefined, observer: null };
    maps.push(st);
    return st;
  }

  function publish(st, force) {
    if (!sessionReady || !window.Shiny || !window.Shiny.setInputValue) return;
    if (force || st.sentCoverage !== st.coverage) {
      window.Shiny.setInputValue(NS.id(st.root, "streamcat_coverage"), st.coverage, { priority: "event" });
      st.sentCoverage = st.coverage;
    }
    if (force || st.sentStreams !== st.streamsVisible) {
      window.Shiny.setInputValue(NS.id(st.root, "streams_visible"), st.streamsVisible, { priority: "event" });
      st.sentStreams = st.streamsVisible;
    }
  }

  // Leaflet expands a collapsed Layers control on its mouseenter, which it hears as a mouseover
  // on the control; this capture listener stops that while the menu is closed, before Leaflet's
  // own listener runs. A click (or Enter) on the button still opens it, and leaving the open
  // menu or clicking the map closes it, as before. A rebuilt control gets the listener again.
  function clickOnly(control) {
    if (!control || !control.addEventListener || control._stafClickOnly) return;
    control._stafClickOnly = true;
    control.addEventListener("mouseover", function (e) {
      if (!control.classList.contains("leaflet-control-layers-expanded")) e.stopImmediatePropagation();
    }, true);
  }

  function syncControls(st) {
    var scope = NS.scope(st.root);
    var wrap = scope.querySelector(".easi-map-wrap");
    var corner = wrap && wrap.querySelector(".leaflet-top.leaflet-right");
    var control = corner && corner.querySelector(".leaflet-control-layers");
    clickOnly(control);
    var list = control && control.querySelector(".leaflet-control-layers-list");
    if (!list) return false;

    var panel = scope.querySelector(".easi-legend-panel");
    if (panel && panel.parentNode !== corner) {
      panel.classList.add("leaflet-control");
      corner.appendChild(panel);
      if (window.L && window.L.DomEvent) {
        window.L.DomEvent.disableClickPropagation(panel);
        window.L.DomEvent.disableScrollPropagation(panel);
      }
    }

    // Only observe the native Streams checkbox; Leaflet still handles its click.
    var streamsLabel = null;
    list.querySelectorAll(".leaflet-control-layers-overlays label").forEach(function (label) {
      if (label.textContent.trim() !== "Streams") return;
      streamsLabel = label;
      var input = label.querySelector('input[type="checkbox"]');
      if (!input) return;
      st.streamsVisible = input.checked;
      if (!input.dataset.stafObserved) {
        input.dataset.stafObserved = "true";
        input.addEventListener("change", function () {
          st.streamsVisible = input.checked;
          publish(st, false);
        });
      }
    });

    if (streamsLabel && !list.querySelector(".staf-coverage-control")) {
      var row = document.createElement("div");
      row.className = "staf-coverage-control";
      var label = document.createElement("label");
      var checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "staf-coverage-toggle";
      checkbox.checked = st.coverage;
      // Do not use leaflet-control-layers-selector: this is not a native layer.
      checkbox.addEventListener("change", function () {
        st.coverage = checkbox.checked;
        publish(st, false);
      });
      var text = document.createElement("span");
      text.textContent = " StreamCat coverage";
      label.appendChild(checkbox);
      label.appendChild(text);
      row.appendChild(label);
      streamsLabel.insertAdjacentElement("afterend", row);
    }
    publish(st, false);
    return true;
  }

  function connect(st) {
    var wrap = NS.scope(st.root).querySelector(".easi-map-wrap");
    if (wrap && !st.observer) {
      st.observer = new MutationObserver(function () { syncControls(st); });
      // Native layer changes replace the control. Child-list observation also
      // catches delayed widget mounting, without observing our checkbox values.
      st.observer.observe(wrap, { childList: true, subtree: true });
    }
    return syncControls(st);
  }

  function start() {
    var roots = NS.roots();
    var waiting = (roots.length ? roots : [null]).map(state)
      .filter(function (st) { return !connect(st); });
    if (!waiting.length) return;
    var tries = 0;
    var timer = setInterval(function () {
      tries += 1;
      waiting = waiting.filter(function (st) { return !connect(st); });
      if (!waiting.length || tries > 150) clearInterval(timer);
    }, 200);
  }

  function sessionPending() {
    sessionReady = false;
    maps.forEach(function (st) { st.sentCoverage = st.sentStreams = undefined; });
  }
  function sessionInitialized() {
    sessionReady = true;
    maps.forEach(function (st) { publish(st, true); });
  }
  // Shiny fires connected BEFORE sending its init payload. Sending a priority
  // event there breaks the server protocol; wait for the config acknowledgment.
  if (window.jQuery) {
    window.jQuery(document).on("shiny:connected shiny:disconnected", sessionPending);
    window.jQuery(document).on("shiny:sessioninitialized", sessionInitialized);
  } else {
    document.addEventListener("shiny:connected", sessionPending);
    document.addEventListener("shiny:disconnected", sessionPending);
    document.addEventListener("shiny:sessioninitialized", sessionInitialized);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
