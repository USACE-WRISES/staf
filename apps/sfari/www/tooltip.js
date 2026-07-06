/* EASI light tooltip — a clean floating box for the ⓘ info icons.
 *
 * Replaces the browser's native (black, full-width) title tooltip. On hover/focus
 * of any [data-tip] element, a styled .easi-tip box is shown on document.body
 * (so it escapes the left pane's overflow clipping), positioned beside the icon
 * and flipped/clamped to stay on screen. No dependencies.
 */
(function () {
  "use strict";

  var tip = null, hideTimer = null;

  function ensure() {
    if (tip) return tip;
    tip = document.createElement("div");
    tip.className = "easi-tip";
    tip.style.display = "none";
    document.body.appendChild(tip);
    return tip;
  }

  function hide() { if (tip) tip.style.display = "none"; }

  function show(el) {
    var htmlTip = el.getAttribute("data-tip-html");
    var text = el.getAttribute("data-tip");
    if (!htmlTip && !text) return;
    ensure();
    if (htmlTip) {                     // app-generated, HTML-escaped rich card
      tip.innerHTML = htmlTip;
      tip.classList.add("easi-tip--html");
    } else {
      tip.textContent = text;          // .easi-tip uses white-space:pre-line for \n
      tip.classList.remove("easi-tip--html");
    }
    tip.style.display = "block";
    tip.style.left = "-9999px";        // measure off-screen first
    tip.style.top = "0px";
    var r = el.getBoundingClientRect();
    var tw = tip.offsetWidth, th = tip.offsetHeight, pad = 8;
    var left = r.right + pad;                       // prefer right of the icon
    if (left + tw > window.innerWidth - pad) {      // flip to the left if it overflows
      left = r.left - tw - pad;
    }
    left = Math.max(pad, left);
    var top = r.top - 4;
    top = Math.min(top, window.innerHeight - th - pad);
    top = Math.max(pad, top);
    tip.style.left = (left + window.scrollX) + "px";
    tip.style.top = (top + window.scrollY) + "px";
  }

  function target(e) {
    return e.target && e.target.closest
      ? e.target.closest("[data-tip],[data-tip-html]") : null;
  }

  document.addEventListener("mouseover", function (e) {
    var el = target(e);
    if (el) { clearTimeout(hideTimer); show(el); }
  });
  document.addEventListener("mouseout", function (e) {
    if (target(e)) { clearTimeout(hideTimer); hideTimer = setTimeout(hide, 90); }
  });
  document.addEventListener("focusin", function (e) {
    var el = target(e);
    if (el) show(el);
  });
  document.addEventListener("focusout", function (e) { if (target(e)) hide(); });
  // a moving/scrolling icon shouldn't leave a stray box behind
  window.addEventListener("scroll", hide, true);
})();
