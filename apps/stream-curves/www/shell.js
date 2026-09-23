// StreamCurves shell behaviour (after HYPE Desktop's tree.js chrome and map_bounds.js veil).
//
// 1. Navigation: the stage strip and the Project panel are plain <button data-jump="...">
//    elements; one delegated handler posts the target to the input its container names
//    (data-jump-to), so the server has ONE guarded dispatcher and no duplicate input ids.
// 2. The Project panel collapses from its head; the choice is remembered per viewer.
// 3. The boot veil covers the page from the first byte until the session answers (the first
//    stage-strip render), then fades and posts sc_ready, which opens the start page. A fallback
//    timer makes sure nobody is ever trapped behind it.
(function () {
  "use strict";

  function nonce() { return Date.now() + Math.random(); }

  // ---- 1. navigation -------------------------------------------------------------------
  document.addEventListener("click", function (e) {
    var el = e.target && e.target.closest ? e.target.closest("[data-jump]") : null;
    if (!el) return;
    var box = el.closest("[data-jump-to]");
    var input = box ? box.getAttribute("data-jump-to") : null;
    if (!input || !(window.Shiny && Shiny.setInputValue)) return;
    e.preventDefault();
    Shiny.setInputValue(input, { target: el.getAttribute("data-jump"), n: nonce() },
                        { priority: "event" });
  });

  // ---- 2. the Project panel ------------------------------------------------------------
  var KEY = "streamcurves.panelCollapsed";
  function panel() { return document.getElementById("sc-panel"); }
  function applyStored() {
    var p = panel();
    if (!p) return;
    var collapsed = false;
    try { collapsed = window.localStorage.getItem(KEY) === "1"; } catch (e) { /* no storage */ }
    p.classList.toggle("collapsed", collapsed);
  }
  document.addEventListener("click", function (e) {
    var head = e.target && e.target.closest ? e.target.closest(".sc-panel-head") : null;
    if (!head) return;
    var p = panel();
    if (!p) return;
    var collapsed = !p.classList.contains("collapsed");
    p.classList.toggle("collapsed", collapsed);
    try { window.localStorage.setItem(KEY, collapsed ? "1" : "0"); } catch (err) { /* no storage */ }
  });
  document.addEventListener("DOMContentLoaded", applyStored);

  // ---- the tab title follows the open project ("<project> - StreamCurves") --------------
  function registerTitle() {
    if (!(window.Shiny && Shiny.addCustomMessageHandler)) return false;
    try {
      Shiny.addCustomMessageHandler("sc_title", function (msg) {
        var name = (msg && msg.title) || "";
        document.title = name ? name + " - StreamCurves" : "StreamCurves";
      });
    } catch (e) { /* already registered (reconnect) */ }
    return true;
  }
  if (!registerTitle()) {
    var tries0 = 0;
    var t0 = setInterval(function () { if (registerTitle() || ++tries0 > 130) clearInterval(t0); }, 150);
  }

  // ---- 3. the boot veil ------------------------------------------------------------------
  var done = false;
  function bootReady() {
    if (done) return;
    done = true;
    var veil = document.getElementById("sc-boot");
    if (veil) {
      veil.classList.add("is-done");
      setTimeout(function () { veil.hidden = true; }, 320);
    }
    var post = function () {
      if (window.Shiny && Shiny.setInputValue) {
        Shiny.setInputValue("sc_ready", nonce(), { priority: "event" });
        return true;
      }
      return false;
    };
    if (!post()) {
      var tries = 0;
      var t = setInterval(function () { if (post() || ++tries > 60) clearInterval(t); }, 100);
    }
  }
  document.addEventListener("shiny:value", function (e) {
    // the strip is the first thing the server paints for every session
    if (e && e.name && String(e.name).indexOf("stage_bar") !== -1) {
      requestAnimationFrame(function () { setTimeout(bootReady, 60); });
    }
  });
  document.addEventListener("shiny:connected", function () { setTimeout(bootReady, 6000); });
  setTimeout(bootReady, 20000);
})();
