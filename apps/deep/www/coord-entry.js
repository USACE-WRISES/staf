// Place a snapped point from typed Latitude/Longitude on the Identify step.
//
// When the user types into the Latitude/Longitude boxes and presses Enter or leaves
// the box, post {lat, lon} to the server (input.coords_entered). The server snaps to a
// nearby stream and places the point (or clears it and warns if none is found nearby).
// This mirrors the geocode client-event pattern in geocode-autocomplete.js. Nothing is
// posted unless BOTH boxes have a value, so an incomplete entry never places a point.
(function () {
  var DEBOUNCE_MS = 600;   // coalesce a quick Latitude-then-Longitude edit into one snap
  var timer = null;
  var lastKey = "";
  var lastTime = 0;

  function isCoordField(t) {
    return t && (t.id === "lat" || t.id === "lon");
  }

  function postCoords() {
    var latEl = document.getElementById("lat");
    var lonEl = document.getElementById("lon");
    if (!latEl || !lonEl) return;
    var lat = (latEl.value || "").trim();
    var lon = (lonEl.value || "").trim();
    if (lat === "" || lon === "") return;                 // incomplete -> place nothing
    var key = lat + "," + lon;
    var now = Date.now();
    if (key === lastKey && now - lastTime < 1500) return; // dedupe Enter + change
    lastKey = key;
    lastTime = now;
    if (window.Shiny && Shiny.setInputValue) {
      Shiny.setInputValue("coords_entered",
        { lat: parseFloat(lat), lon: parseFloat(lon), nonce: now },
        { priority: "event" });
    }
  }

  // Commit when a box loses focus or its value changes.
  document.addEventListener("change", function (e) {
    if (!isCoordField(e.target)) return;
    clearTimeout(timer);
    timer = setTimeout(postCoords, DEBOUNCE_MS);
  }, true);

  // Commit immediately on Enter.
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || !isCoordField(e.target)) return;
    clearTimeout(timer);
    postCoords();
  }, true);
})();
