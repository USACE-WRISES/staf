/* Dock the map legend and add the optional StreamCat coverage view to Layers.
 *
 * ipyleaflet 0.20's native checkboxes do not sync visibility to Python. Coverage
 * is therefore an app-owned checkbox in the same menu, bridged to Shiny. Its
 * state lives for this page session; rebuilding the native control never resets
 * it. The native Streams checkbox continues to own the persistent stream group.
 */
(function () {
  "use strict";

  var coverage = false;
  var streamsVisible = true;
  var sentCoverage, sentStreams;
  var observer = null;
  var sessionReady = false;

  function publish(force) {
    if (!sessionReady || !window.Shiny || !window.Shiny.setInputValue) return;
    if (force || sentCoverage !== coverage) {
      window.Shiny.setInputValue("streamcat_coverage", coverage, { priority: "event" });
      sentCoverage = coverage;
    }
    if (force || sentStreams !== streamsVisible) {
      window.Shiny.setInputValue("streams_visible", streamsVisible, { priority: "event" });
      sentStreams = streamsVisible;
    }
  }

  function syncControls() {
    var wrap = document.querySelector(".easi-map-wrap");
    var corner = wrap && wrap.querySelector(".leaflet-top.leaflet-right");
    var control = corner && corner.querySelector(".leaflet-control-layers");
    var list = control && control.querySelector(".leaflet-control-layers-list");
    if (!list) return false;

    var panel = document.getElementById("easi-legend-panel");
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
      streamsVisible = input.checked;
      if (!input.dataset.stafObserved) {
        input.dataset.stafObserved = "true";
        input.addEventListener("change", function () {
          streamsVisible = input.checked;
          publish(false);
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
      checkbox.checked = coverage;
      // Do not use leaflet-control-layers-selector: this is not a native layer.
      checkbox.addEventListener("change", function () {
        coverage = checkbox.checked;
        publish(false);
      });
      var text = document.createElement("span");
      text.textContent = " StreamCat coverage";
      label.appendChild(checkbox);
      label.appendChild(text);
      row.appendChild(label);
      streamsLabel.insertAdjacentElement("afterend", row);
    }
    publish(false);
    return true;
  }

  function start() {
    var tries = 0;
    function connect() {
      var wrap = document.querySelector(".easi-map-wrap");
      if (wrap && !observer) {
        observer = new MutationObserver(syncControls);
        // Native layer changes replace the control. Child-list observation also
        // catches delayed widget mounting, without observing our checkbox values.
        observer.observe(wrap, { childList: true, subtree: true });
      }
      return syncControls();
    }
    if (connect()) return;
    var timer = setInterval(function () {
      tries += 1;
      if (connect() || tries > 150) clearInterval(timer);
    }, 200);
  }

  function sessionPending() {
    sessionReady = false;
    sentCoverage = sentStreams = undefined;
  }
  function sessionInitialized() {
    sessionReady = true;
    publish(true);
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
