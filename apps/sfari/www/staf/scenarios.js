/* The scenario chip on the assessment page (STAF shared asset): each menu choice is sent to the
   server as {action, id}; the menu closes on a choice, an outside click or Escape. */
(function () {
  "use strict";
  function closeAll(except) {
    document.querySelectorAll("details.staf-scen-chip[open]").forEach(function (d) {
      if (d !== except) d.removeAttribute("open");
    });
  }
  document.addEventListener("click", function (e) {
    var el = e.target && e.target.closest ? e.target.closest("[data-sc-action]") : null;
    if (el) {
      e.preventDefault();
      if (el.disabled) return;
      closeAll(null);
      if (window.Shiny && window.Shiny.setInputValue) {
        window.Shiny.setInputValue("staf_scenario_evt", {
          action: el.getAttribute("data-sc-action"), id: el.getAttribute("data-sc-id") || "", t: Date.now()
        }, {priority: "event"});
      }
      return;
    }
    var chip = e.target && e.target.closest ? e.target.closest("details.staf-scen-chip") : null;
    closeAll(chip);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeAll(null);
  });
})();
