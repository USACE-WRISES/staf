/* The STAF app's header (apps/staf): the EASI | SFARI | DEEP switch.
 *
 * A switch is instant and local: the page shows the chosen tool's section, header slots and lit
 * tier (all from html[data-staf-tool]), turns on that tool's own stylesheets (their media), names
 * the tool in the title and the address bar (?tool=, so it can be bookmarked) and tells Shiny
 * (input.staf_tool), which starts the tool's server the first time it is shown. Every tool keeps
 * its work while another one is shown. A report that opens for a tool that is not shown brings
 * that tool back with it. */
(function () {
  "use strict";
  var TOOLS = ["easi", "sfari", "deep"];
  var REPORTS = {"easi-report": "easi", "sfari-report": "sfari", "deep-report": "deep"};

  function button(tool) { return document.querySelector('.staf-tool-btn[data-tool="' + tool + '"]'); }
  function header() { return document.querySelector(".staf-header"); }

  function closeMenu() {
    var bar = header(), menu = document.querySelector(".staf-menu-btn");
    if (bar) bar.classList.remove("menu-open");
    if (menu) menu.setAttribute("aria-expanded", "false");
  }

  function activate(tool) {
    var btn = button(tool), html = document.documentElement;
    if (!btn || btn.disabled || html.getAttribute("data-staf-tool") === tool) return false;
    html.setAttribute("data-staf-tool", tool);
    document.querySelectorAll(".staf-tool-btn").forEach(function (b) {
      var on = b === btn;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll("link[data-staf-css]").forEach(function (link) {
      link.media = link.getAttribute("data-staf-css") === tool ? "all" : "not all";
    });
    document.title = btn.textContent + " · STAF";   // the tier strip lights itself from html[data-staf-tool]
    try { history.replaceState(history.state, "", "?tool=" + tool + location.hash); } catch (e) { /* no history */ }
    closeMenu();
    // Shiny learns that an output became visible from a resize observer, which does not fire for
    // a page that is not painting (a background window): binding the page again (a no-op for
    // what is bound) makes it look at every output now, so the shown tool's outputs resume.
    if (window.Shiny && Shiny.bindAll) Shiny.bindAll(document.body);
    // Leaflet measures a map only while it shows: let every map re-measure on the next frame.
    requestAnimationFrame(function () { window.dispatchEvent(new Event("resize")); });
    document.dispatchEvent(new CustomEvent("staf:tool-shown", {detail: {tool: tool}}));
    return true;
  }

  var binding = new Shiny.InputBinding();
  $.extend(binding, {
    find: function (scope) { return $(scope).find(".staf-tool-switch"); },
    getValue: function () { return document.documentElement.getAttribute("data-staf-tool"); },
    subscribe: function (el, callback) {
      $(el).on("click.staf", ".staf-tool-btn", function () {
        if (activate(this.getAttribute("data-tool"))) callback(false);
      });
      $(el).on("keydown.staf", ".staf-tool-btn", function (e) {
        var step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!step) return;
        e.preventDefault();
        var at = TOOLS.indexOf(this.getAttribute("data-tool"));
        for (var n = 1; n < TOOLS.length; n++) {
          var next = TOOLS[(at + step * n + TOOLS.length) % TOOLS.length];
          if (activate(next)) { callback(false); button(next).focus(); return; }
        }
      });
      $(el).on("staf:switched.staf", function () { callback(false); });
    },
    unsubscribe: function (el) { $(el).off(".staf"); }
  });
  Shiny.inputBindings.register(binding, "staf.toolSwitch");

  $(document).on("show.bs.modal", function (e) {
    var report = e.target.querySelector("#easi-report, #sfari-report, #deep-report");
    var tool = report && REPORTS[report.id];
    if (tool && activate(tool)) $(".staf-tool-switch").trigger("staf:switched");
  });

  document.addEventListener("click", function (e) {
    var target = e.target && e.target.closest ? e.target : null;
    var bar = header();
    if (!target || !bar) return;
    if (target.closest(".staf-menu-btn")) {
      var open = bar.classList.toggle("menu-open");
      target.closest(".staf-menu-btn").setAttribute("aria-expanded", open ? "true" : "false");
      return;
    }
    if (bar.classList.contains("menu-open") && !target.closest(".staf-slot.staf-nav")) closeMenu();
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeMenu(); });
})();
