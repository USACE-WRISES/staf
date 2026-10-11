// Place a snapped point from typed Latitude/Longitude on the Identify step.
//
// When the user types into the Latitude/Longitude boxes and presses Enter or leaves
// the box, post {lat, lon} to the server (input.coords_entered of the tool the boxes
// belong to, staf-ns.js). The server snaps to a nearby stream and places the point (or
// clears it and warns if none is found nearby). This mirrors the geocode client-event
// pattern in geocode-autocomplete.js. Nothing is posted unless BOTH boxes have a value,
// so an incomplete entry never places a point.
(function () {
  var NS = window.STAFNs;
  var DEBOUNCE_MS = 600;   // coalesce a quick Latitude-then-Longitude edit into one snap
  var pending = {};        // per tool prefix: {timer, lastKey, lastTime}

  function isCoordField(t) {
    var name = t && t.id ? NS.bare(t) : "";
    return name === "lat" || name === "lon";
  }

  function slot(root) {
    var prefix = NS.ns(root);
    return pending[prefix] || (pending[prefix] = { timer: null, lastKey: "", lastTime: 0 });
  }

  function postCoords(root) {
    var s = slot(root);
    var latEl = document.getElementById(NS.id(root, "lat"));
    var lonEl = document.getElementById(NS.id(root, "lon"));
    if (!latEl || !lonEl) return;
    var lat = (latEl.value || "").trim();
    var lon = (lonEl.value || "").trim();
    if (lat === "" || lon === "") return;                     // incomplete -> place nothing
    var key = lat + "," + lon;
    var now = Date.now();
    if (key === s.lastKey && now - s.lastTime < 1500) return; // dedupe Enter + change
    s.lastKey = key;
    s.lastTime = now;
    if (window.Shiny && Shiny.setInputValue) {
      Shiny.setInputValue(NS.id(root, "coords_entered"),
        { lat: parseFloat(lat), lon: parseFloat(lon), nonce: now },
        { priority: "event" });
    }
  }

  // Commit when a box loses focus or its value changes.
  document.addEventListener("change", function (e) {
    if (!isCoordField(e.target)) return;
    var root = NS.owner(e.target), s = slot(root);
    clearTimeout(s.timer);
    s.timer = setTimeout(function () { postCoords(root); }, DEBOUNCE_MS);
  }, true);

  // Commit immediately on Enter.
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || !isCoordField(e.target)) return;
    var root = NS.owner(e.target);
    clearTimeout(slot(root).timer);
    postCoords(root);
  }, true);
})();
