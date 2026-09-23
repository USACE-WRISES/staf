/* Sweep away ZOMBIE modals - a Shiny/Bootstrap race that leaves a dead dialog on top.
 *
 * THE RACE. Shiny keeps exactly one dialog, `#shiny-modal`, inside `#shiny-modal-wrapper`,
 * and `modal_show()` REPLACES the wrapper's content. But its client handler is async (it
 * awaits the HTML dependencies before rendering), so two shows close together interleave:
 * the second replace DETACHES the first modal while Bootstrap is still inside `show()` for
 * it. Bootstrap's `_showElement` re-attaches an element it finds detached:
 *
 *     if (!this._element.parentNode) document.body.append(this._element)
 *
 * so the modal Shiny just discarded comes back as a DIRECT CHILD OF BODY, outside the
 * wrapper, where nothing will ever clean it up. It carries the same z-index and sits later
 * in the DOM, so it paints ON TOP of the live one, forever.
 *
 * WHY IT LOOKS LIKE A FROZEN APP rather than a stray dialog. The zombie is a dead snapshot:
 * its Shiny outputs were unbound when it was detached, so nothing in it ever updates again.
 * Its buttons still work, though, because ours post their events through inline
 * `Shiny.setInputValue` handlers, which need no binding. So the app stays fully responsive
 * and answers every click - it just renders the answer into the live modal BURIED
 * UNDERNEATH. Pressing Back does switch the start page home; you keep seeing the gallery.
 *
 * The start page is where this bites, because every one of its navigations (Assessment
 * library, Back) and every cancel funnel through
 * the start page gate re-shows the whole modal, so overlapping shows are routine.
 *
 * Duplicate ids are ALWAYS a bug, so the rule is unambiguous: the live modal is the one in
 * the wrapper, everything else goes. Dispose first, or Bootstrap re-appends it again on the
 * next transition tick.
 */
(function () {
  "use strict";

  function drop(el) {
    try {
      var bs = window.bootstrap && window.bootstrap.Modal;
      var inst = bs && (bs.getInstance ? bs.getInstance(el) : null);
      if (inst) inst.dispose();          // or it re-appends itself on the next tick
    } catch (e) { /* no Bootstrap, or already disposed */ }
    try {
      if (window.Shiny && window.Shiny.unbindAll) window.Shiny.unbindAll(el);
    } catch (e) { /* nothing bound (the usual case for a detached snapshot) */ }
    if (el.parentNode) el.parentNode.removeChild(el);
  }

  function sweep() {
    var all = document.querySelectorAll("#shiny-modal");
    if (all.length < 2) return;          // the normal case: nothing to do
    // The live dialog is the one Shiny owns. With no wrapper at all (nothing shows a modal
    // that way today) fall back to the last, which is the one on top and the newest.
    var live = document.querySelector("#shiny-modal-wrapper #shiny-modal") || all[all.length - 1];
    for (var i = 0; i < all.length; i++) {
      if (all[i] !== live) drop(all[i]);
    }
    // Each show leaves a backdrop behind it; keep one for the dialog that survived.
    var backs = document.querySelectorAll(".modal-backdrop");
    for (var j = 0; j < backs.length - 1; j++) backs[j].remove();
  }

  function watch() {
    try {
      new MutationObserver(sweep).observe(document.body, { childList: true });
    } catch (e) { /* no MutationObserver: the app still works, zombies just persist */ }
    sweep();
  }

  if (document.body) watch();
  else document.addEventListener("DOMContentLoaded", watch);
})();
