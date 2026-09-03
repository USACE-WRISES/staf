/* EASI map legend: dock #easi-legend-panel into the leaflet top-right control stack.
 *
 * Appended AFTER the LayersControl so it stacks BELOW the layers button; leaflet's
 * control flow auto-spaces them (the legend flows down when the layers list expands),
 * so nothing overlaps. ipyleaflet adds that control asynchronously after the map
 * mounts, so docking is retried until the control exists or we give up (~8 s); the
 * CSS keeps an absolute top-right fallback when docking never happens. The panel's
 * content is a Shiny output (#stream_legend); moving the node keeps its binding.
 * Same pattern as DEEP's coverage panel (apps/deep/www/coverage.js).
 */
(function () {
  "use strict";

  var docked = false;
  function dockPanel() {
    if (docked) return true;
    var panel = document.getElementById("easi-legend-panel");
    var corner = document.querySelector(".leaflet-control-container .leaflet-top.leaflet-right")
              || document.querySelector(".leaflet-top.leaflet-right");
    if (!panel || !corner) return false;
    if (!corner.querySelector(".leaflet-control-layers")) return false;
    panel.classList.add("leaflet-control");
    corner.appendChild(panel);
    if (window.L && window.L.DomEvent) {
      window.L.DomEvent.disableClickPropagation(panel);
      window.L.DomEvent.disableScrollPropagation(panel);
    }
    docked = true;
    return true;
  }

  function start() {
    var tries = 0;
    var timer = setInterval(function () {
      if (dockPanel() || tries > 40) { clearInterval(timer); return; }
      tries += 1;
    }, 200);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
