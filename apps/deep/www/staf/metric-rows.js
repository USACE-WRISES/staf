/* STAF metric rows (shared asset, see metric-rows.css): a row's Scoring, Note and Photo buttons
   open and close their panels in the browser alone; the server never re-renders for them. The
   host of a button is its .staf-metric row, or any [data-staf-host] (SFARI's score card). A note
   button shows a dot while its note holds text, a photo button a count; an app that adds or
   removes a photo calls window.STAFMetricRows.sync(row) to refresh them. */
(function () {
  "use strict";
  function hostOf(el) {
    return el && el.closest ? el.closest(".staf-metric, [data-staf-host]") : null;
  }
  function panel(host, kind) {
    return host ? host.querySelector('.staf-metric-panel[data-panel="' + kind + '"]') : null;
  }
  function button(host, kind) {
    return host ? host.querySelector('button.staf-act[data-staf-panel="' + kind + '"]') : null;
  }
  function sync(host) {
    if (!host) return;
    var noteBtn = button(host, "note") || button(host, "fnnote");
    if (noteBtn) {
      var ta = host.querySelector("textarea.staf-metric-note");
      noteBtn.classList.toggle("has", !!(ta && ta.value && ta.value.trim()));
    }
    var photoBtn = button(host, "photo");
    if (photoBtn) {
      var box = panel(host, "photo");
      var n = box ? box.querySelectorAll("img").length : 0;
      var count = photoBtn.querySelector(".staf-act-count");
      var label = photoBtn.querySelector(".staf-act-label");
      if (count) count.textContent = n ? String(n) : "";
      if (label) label.textContent = n > 1 ? "Photos" : "Photo";
      photoBtn.classList.toggle("has", n > 0);
    }
  }
  function toggle(btn) {
    var host = hostOf(btn);
    var kind = btn.getAttribute("data-staf-panel");
    if (!host || !kind) return;
    var open = host.classList.toggle("show-" + kind);
    btn.classList.toggle("on", open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open && kind !== "photo" && kind !== "scoring") {
      var box = panel(host, kind) || host;
      var field = box.querySelector("textarea");
      if (field && field.focus) field.focus();
    }
  }
  document.addEventListener("click", function (e) {
    var btn = e.target && e.target.closest ? e.target.closest("button.staf-act[data-staf-panel]") : null;
    if (!btn) return;
    e.preventDefault();
    toggle(btn);
  });
  document.addEventListener("input", function (e) {
    var ta = e.target && e.target.closest ? e.target.closest("textarea.staf-metric-note") : null;
    if (ta) sync(hostOf(ta));
  });
  window.STAFMetricRows = { sync: sync, toggle: toggle };
})();
