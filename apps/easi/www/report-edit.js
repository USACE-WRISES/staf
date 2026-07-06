/* EASI report — in-table metric overrides + per-metric notes.
 *
 * The metric table re-renders on every override (it depends on scored()), so the
 * controls are plain HTML driven through single Shiny.setInputValue channels rather
 * than per-row Shiny inputs (which would be recreated each render):
 *   - .easi-rate-sel  change  -> override_set {mid, rating}   ("auto" clears)
 *   - .easi-note-btn  click   -> toggle the row's .easi-note-row.open
 *   - .easi-note-ta   input   -> note_set {mid, text} (debounced) + live ✎ state
 * Event delegation on document keeps it working across table re-renders.
 */
(function () {
  "use strict";

  function setInput(name, payload) {
    if (window.Shiny && Shiny.setInputValue) {
      Shiny.setInputValue(name, Object.assign({ nonce: Date.now() }, payload),
                          { priority: "event" });
    }
  }
  function esc(id) { return (window.CSS && CSS.escape) ? CSS.escape(id) : id; }

  // rating override dropdown
  document.addEventListener("change", function (e) {
    var s = e.target;
    if (!s || !s.classList || !s.classList.contains("easi-rate-sel")) return;
    setInput("override_set", { mid: s.getAttribute("data-mid"), rating: s.value });
  });

  // note icon -> expand/collapse the textarea sub-row
  document.addEventListener("click", function (e) {
    var btn = e.target.closest ? e.target.closest(".easi-note-btn") : null;
    if (!btn) return;
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
  document.addEventListener("input", function (e) {
    if (e.target && e.target.classList && e.target.classList.contains("easi-note-ta")) {
      postNote(e.target, false);
    }
  });
  document.addEventListener("blur", function (e) {   // capture: blur doesn't bubble
    if (e.target && e.target.classList && e.target.classList.contains("easi-note-ta")) {
      postNote(e.target, true);
    }
  }, true);
})();
