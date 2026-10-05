/* EASI report — in-table metric overrides + per-metric notes.
 *
 * The metric table re-renders on every override (it depends on scored()), so the
 * controls are plain HTML driven through single Shiny.setInputValue channels rather
 * than per-row Shiny inputs (which would be recreated each render):
 *   - .easi-rate-sel  change  -> override_set {mid, rating}   ("auto" clears)
 *   - .easi-note-btn  click   -> toggle the row's .easi-note-row.open
 *   - .easi-note-ta   input   -> note_set {mid, text} (debounced) + live ✎ state
 * Event delegation on document keeps it working across table re-renders.
 *
 * In the STAF app this file acts only on EASI's own controls (inside EASI's body, or in a
 * dialog while EASI is shown) and posts to EASI's own inputs (staf/staf-ns.js).
 */
(function () {
  "use strict";
  var TOOL = "easi", NS = window.STAFNs;

  function setInput(name, payload) {
    if (window.Shiny && Shiny.setInputValue) {
      Shiny.setInputValue(NS.id(NS.tool(TOOL), name), Object.assign({ nonce: Date.now() }, payload),
                          { priority: "event" });
    }
  }
  function esc(id) { return (window.CSS && CSS.escape) ? CSS.escape(id) : id; }

  // rating override dropdown
  document.addEventListener("change", function (e) {
    var s = e.target;
    if (!s || !s.classList || !s.classList.contains("easi-rate-sel") || !NS.mine(s, TOOL)) return;
    setInput("override_set", { mid: s.getAttribute("data-mid"), rating: s.value });
  });

  // note icon -> expand/collapse the textarea sub-row
  document.addEventListener("click", function (e) {
    var btn = e.target.closest ? e.target.closest(".easi-note-btn") : null;
    if (!btn || !NS.mine(btn, TOOL)) return;
    var row = document.querySelector('.easi-note-row[data-mid="' + esc(btn.getAttribute("data-mid")) + '"]');
    if (!row) return;
    if (row.classList.toggle("open")) {
      var ta = row.querySelector(".easi-note-ta");
      if (ta) ta.focus();
    }
  });

  // note textarea -> persist (debounced) + immediate ✎ "has note" feedback
  var timers = {};
  function postNote(ta, immediate) {
    var mid = ta.getAttribute("data-mid"), text = ta.value;
    var btn = document.querySelector('.easi-note-btn[data-mid="' + esc(mid) + '"]');
    if (btn) btn.classList.toggle("has-note", !!text.trim());
    clearTimeout(timers[mid]);
    var fire = function () { setInput("note_set", { mid: mid, text: text }); };
    if (immediate) fire(); else timers[mid] = setTimeout(fire, 350);
  }
  function isNote(el) {
    return !!(el && el.classList && el.classList.contains("easi-note-ta")) && NS.mine(el, TOOL);
  }
  document.addEventListener("input", function (e) {
    if (isNote(e.target)) postNote(e.target, false);
  });
  document.addEventListener("blur", function (e) {   // capture: blur doesn't bubble
    if (isNote(e.target)) postNote(e.target, true);
  }, true);
})();
