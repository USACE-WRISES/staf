/* Warn before leaving a page with unsaved assessment work (STAF shared asset).
   The server sends {dirty: true|false, ns} on the "staf-unsaved" message; ns names the tool (its
   Shiny id prefix, empty in a standalone app), so in the STAF app every tool keeps its own flag and
   the page warns while any of them is dirty. A file download opens its own tab (web.download_button),
   so it never navigates this page and every real navigation still warns; a closed session has
   nothing left to lose. Save (the header's save_session download) marks the work saved on the
   server while it writes the file, and the server reports it on its next message: one small input
   after the download asks for that message at once. */
(function () {
  "use strict";
  var dirty = {}, registered = false;
  var SAVED_PING = "staf_saved";        // an input no server reads: it only wakes the server

  function anyDirty() {
    return Object.keys(dirty).some(function (prefix) { return dirty[prefix]; });
  }
  function register() {
    if (registered) return true;
    if (!window.Shiny || !window.Shiny.addCustomMessageHandler) return false;
    window.Shiny.addCustomMessageHandler("staf-unsaved", function (m) {
      dirty[(m && m.ns) || ""] = !!(m && m.dirty);
    });
    registered = true;
    return true;
  }
  if (!register()) {
    var tries = 0, timer = setInterval(function () {
      if (register() || ++tries > 300) clearInterval(timer);
    }, 100);
  }
  var $ = window.jQuery;
  if ($) {
    $(document).on("shiny:filedownload", function (e) {
      var NS = window.STAFNs, el = e && e.name ? document.getElementById(e.name) : null;
      if (!el || !NS || !window.Shiny || !window.Shiny.setInputValue) return;
      var root = NS.owner(el);
      if (el.id !== NS.id(root, "save_session")) return;
      [1000, 4000].forEach(function (wait) {           // the second, should writing the file take longer
        setTimeout(function () {
          window.Shiny.setInputValue(NS.id(root, SAVED_PING), Date.now(), {priority: "event"});
        }, wait);
      });
    });
    $(document).on("shiny:disconnected", function () { dirty = {}; });
  }
  window.addEventListener("beforeunload", function (e) {
    if (!anyDirty()) return undefined;
    e.preventDefault();
    e.returnValue = "";
    return "";
  });
})();
