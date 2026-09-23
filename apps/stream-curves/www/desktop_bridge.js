// Desktop shell bridge: present only in StreamCurves Desktop (WebView2). Relays the app's
// streamcurves_desktop custom messages to the C# shell (native file and folder pickers, the
// window title, the project folder for download dialogs) and forwards the shell's replies back
// into Shiny. In a plain browser window.chrome.webview does not exist and this file does
// nothing; the app falls back to a child-process dialog, then a typed-path dialog.
// Ported from HYPE Desktop's www/desktop_bridge.js.
//
// Delivery is belt and suspenders: the page includes this file with a <script> tag AND the
// shell injects the same source into every document (AddScriptToExecuteOnDocumentCreatedAsync),
// so the __scBridgeLoaded flag makes the second arrival a no-op.
//
// Shiny.setInputValue does not exist until Shiny initializes, so a short poll guarantees
// attachment, and the shiny:connected listener re-announces the shell after every reconnect
// (each reconnect is a fresh server session that needs the desktop_shell flag again).
//
// Contract: postMessage MUST send a STRING (the shell reads messages with
// TryGetWebMessageAsString and throws on raw objects), and addCustomMessageHandler throws on a
// duplicate type, so it is always wrapped.
(function () {
  "use strict";
  if (window.__scBridgeLoaded) return;
  window.__scBridgeLoaded = true;

  var host = window.chrome && window.chrome.webview;
  if (!host) return;

  var handlerRegistered = false;

  function attach() {
    if (!(window.Shiny && Shiny.setInputValue && Shiny.addCustomMessageHandler)) return false;
    if (!handlerRegistered) {
      try {
        Shiny.addCustomMessageHandler("streamcurves_desktop", function (msg) {
          try {
            host.postMessage(JSON.stringify(msg || {}));
          } catch (e) { /* shell gone mid-flight: the app's fallback still works */ }
        });
      } catch (e) { /* duplicate registration (reconnect): the handler is already live */ }
      handlerRegistered = true;
    }
    // Re-sent on every attach: each (re)connected session starts with empty inputs.
    Shiny.setInputValue("desktop_shell", { present: true, nonce: Date.now() },
                        { priority: "event" });
    return true;
  }

  host.addEventListener("message", function (e) {
    var d = e.data || {};
    if (!(window.Shiny && Shiny.setInputValue)) return;
    if (d.type === "projectPathPicked" || d.type === "folderPicked") {
      Shiny.setInputValue("desktop_pick",
        { purpose: d.purpose || "", path: d.path || null, cancelled: !!d.cancelled,
          kind: d.type === "folderPicked" ? "folder" : "file", nonce: Date.now() },
        { priority: "event" });
    }
  });

  // Poll until Shiny is ready (about 20 s cap); the listener handles reconnects thereafter.
  if (!attach()) {
    var tries = 0;
    var timer = setInterval(function () {
      if (attach() || ++tries > 130) clearInterval(timer);
    }, 150);
    document.addEventListener("DOMContentLoaded", attach);
  }
  document.addEventListener("shiny:connected", attach);
})();
