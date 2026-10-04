/* Warn before leaving a page with unsaved assessment work (STAF shared asset).
   The server sends {dirty: true|false} on the "staf-unsaved" message. A file download never
   triggers the warning, and a closed session has nothing left to lose. */
(function () {
  "use strict";
  var dirty = false, quietUntil = 0, registered = false;

  function register() {
    if (registered) return true;
    if (!window.Shiny || !window.Shiny.addCustomMessageHandler) return false;
    window.Shiny.addCustomMessageHandler("staf-unsaved", function (m) { dirty = !!(m && m.dirty); });
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
    $(document).on("shiny:filedownload", function () { quietUntil = Date.now() + 2000; });
    $(document).on("shiny:disconnected", function () { dirty = false; });
  }
  window.addEventListener("beforeunload", function (e) {
    if (!dirty || Date.now() < quietUntil) return undefined;
    e.preventDefault();
    e.returnValue = "";
    return "";
  });
})();
