/* EASI report — display toggles for the metric table (STAF "screening" controls).
 *
 * The 5 checkboxes above the table (.easi-toggle, each with a data-cls) reveal extra
 * detail by flipping a class on the stable #easi-report wrapper. This is PURELY visual:
 * no Shiny.setInputValue, so a toggle never triggers a server round-trip or output
 * re-render — the detail appears/disappears instantly via CSS with no flicker/spinner.
 *
 *   data-cls="show-adv"       -> Index column (.easi-col-adv)
 *   data-cls="show-map"       -> Physical/Chemical/Biological columns (.easi-col-map)
 *   data-cls="show-rollup"    -> rollup computation rows (.easi-rollup-row)
 *   data-cls="show-suggested" -> (auto: N) cue on overridden rows (.easi-auto-cue)
 *   data-cls="show-fnf"       -> F/AR/NF badges (.easi-fnf-badge)
 *
 * State persists in localStorage so reopening the report remembers the user's choices.
 * The modal is created dynamically, so a MutationObserver initializes the toolbar when
 * #easi-report is inserted. The wrapper survives the table's re-render on a rating
 * override (only the inner table re-renders), so classes and checkbox state stay put.
 */
(function () {
  "use strict";
  var KEY = "easi-report-toggles";

  function saved() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; }
  }
  function save(state) {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* ignore */ }
  }
  function wrapper() { return document.getElementById("easi-report"); }

  function applyOne(cls, on) {
    var w = wrapper();
    if (w && cls) w.classList.toggle(cls, !!on);
  }

  // Reflect saved state onto the checkboxes + wrapper when the report is inserted.
  function init(root) {
    var boxes = root.querySelectorAll(".easi-toggle");
    if (!boxes.length) return;
    var state = saved();
    Array.prototype.forEach.call(boxes, function (box) {
      var cls = box.getAttribute("data-cls");
      // a saved choice wins; otherwise fall back to the checkbox's HTML default (so the
      // slider, which ships checked, defaults on for a fresh user)
      var on = (cls in state) ? !!state[cls] : box.checked;
      box.checked = on;
      applyOne(cls, on);
    });
  }

  document.addEventListener("change", function (e) {
    var box = e.target;
    if (!box || !box.classList || !box.classList.contains("easi-toggle")) return;
    var cls = box.getAttribute("data-cls");
    applyOne(cls, box.checked);
    var state = saved();
    state[cls] = box.checked;
    save(state);
  });

  var mo = new MutationObserver(function (muts) {
    for (var i = 0; i < muts.length; i++) {
      var added = muts[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        var n = added[j];
        if (n.nodeType !== 1) continue;
        if (n.id === "easi-report") { init(n); }
        else if (n.querySelector) {
          var el = n.querySelector("#easi-report");
          if (el) init(el);
        }
      }
    }
  });
  if (document.body) mo.observe(document.body, { childList: true, subtree: true });

  if (document.readyState !== "loading") {          // already present on load (rare)
    var w = wrapper();
    if (w) init(w);
  }
})();
