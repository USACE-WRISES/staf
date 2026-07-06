/* StreamCurves client helpers — port of the R app's www/custom.js.
 *
 * Dropped relative to the R version (unnecessary under py-shiny, where every
 * modal body is a `ui.output_ui` that shiny.js binds automatically):
 *   - bindWorkspaceModalContent / Shiny.bindAll dance + shown.bs.modal signal
 *   - watchPhase2ResultsReady DOM polling (server on_flushed closes the toast)
 *   - summaryClosePickers + bootstrap-select scroll restore (selectize now)
 *   - streamcurves_reset_hidden (all channels send {priority:'event'})
 *   - DataTables columns.adjust branch (DT died with the port)
 */
(function () {
  if (typeof Shiny === "undefined" || typeof $ === "undefined") {
    return;
  }

  function refreshMetadataAccordionLayout(containerId, onComplete) {
    var $containers = containerId ? $("#" + containerId) : $(".metadata-accordion-content");
    if (!$containers.length) {
      if (typeof onComplete === "function") {
        onComplete();
      }
      return;
    }

    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () {
        $containers.each(function () {
          var $container = $(this);
          var $collapse = $container.closest(".accordion-collapse");

          $container.css({ height: "auto", minHeight: "0" });

          if ($collapse.length) {
            $collapse.css("height", "auto");
          }
        });

        $(window).trigger("resize");

        if (typeof onComplete === "function") {
          onComplete();
        }
      });
    });
  }

  function clearWorkspaceModalBackdrop(force) {
    window.setTimeout(function () {
      if (!force && $(".modal.show").length > 0) {
        return;
      }

      $(".modal-backdrop").remove();
      $("body").removeClass("modal-open").css("padding-right", "");
    }, 0);
  }

  Shiny.addCustomMessageHandler("setSelectOptionsDisabled", function (message) {
    var inputId = message && message.inputId ? message.inputId : null;
    if (!inputId) {
      return;
    }

    var disabledValues = Array.isArray(message && message.disabledValues) ? message.disabledValues : [];
    var disabledLookup = {};
    disabledValues.forEach(function (value) {
      disabledLookup[String(value)] = true;
    });

    var apply = function () {
      var el = document.getElementById(inputId);
      if (!el) {
        return;
      }

      Array.from(el.options || []).forEach(function (option) {
        option.disabled = !!disabledLookup[String(option.value)];
      });

      if (!el.selectize) {
        return;
      }

      var control = el.selectize;
      Object.keys(control.options || {}).forEach(function (value) {
        var option = control.options[value];
        if (!option) {
          return;
        }

        option.disabled = !!disabledLookup[String(value)];
        control.updateOption(value, option);
      });

      control.refreshOptions(false);
    };

    /* The disable message can arrive before the flush frame whose
       update_select rebuilds the option list (wiping disabled flags), so
       re-apply after the flush has had time to land. */
    apply();
    [200, 600].forEach(function (delay) {
      window.setTimeout(apply, delay);
    });
  });

  Shiny.addCustomMessageHandler("clearFileInput", function (message) {
    var inputId = message && message.id ? message.id : null;
    if (!inputId) {
      return;
    }

    var $fileInput = $("#" + inputId);
    if (!$fileInput.length) {
      return;
    }

    $fileInput.val("");
    $fileInput.trigger("change");

    var $container = $fileInput.closest(".shiny-input-container");
    $container.find("input[type='text']").val("");
    $container.find(".btn-file input[type='file']").val("");

    if (typeof Shiny.setInputValue === "function") {
      Shiny.setInputValue(inputId, null, { priority: "event" });
    }
  });

  Shiny.addCustomMessageHandler("clearModalBackdrop", function (message) {
    clearWorkspaceModalBackdrop(true);
  });

  /* py-shiny's ui.modal() has no dialog-level class hook; tag the dialog after
     insertion so the .modal-dialog.workspace-modal-dialog CSS applies (the R
     app passed class= to modalDialog). The modal DOM lands a flush after the
     message, so retry briefly until it exists. */
  Shiny.addCustomMessageHandler("workspaceModalDialogClass", function (message) {
    var cls = message && message.className ? message.className : null;
    if (!cls) return;
    var attempts = 0;
    (function tag() {
      var dialog = document.querySelector("#shiny-modal .modal-dialog");
      if (dialog) {
        dialog.classList.add(cls);
        /* Outputs that bind while the modal is mid-fade (or that render later
           from async prepare work) report themselves hidden and stay suspended
           server-side; a window resize makes shiny.js re-send output
           visibility. Fire a few, staggered past the fade + prepare. */
        [0, 300, 1200, 3000].forEach(function (delay) {
          window.setTimeout(function () {
            window.dispatchEvent(new Event("resize"));
          }, delay);
        });
        return;
      }
      if (attempts++ < 40) {
        window.setTimeout(tag, 25);
      }
    })();
  });

  Shiny.addCustomMessageHandler("refreshMetadataAccordion", function (message) {
    refreshMetadataAccordionLayout(
      message && message.id ? message.id : null,
      function () {
        if (typeof Shiny.setInputValue === "function" && message && message.readyInputId) {
          Shiny.setInputValue(message.readyInputId, message.requestId || null, { priority: "event" });
        }
      }
    );
  });

  $(document).on(
    "shown.bs.tab",
    ".metadata-editor-shell [data-bs-toggle='tab'], .metadata-editor-shell [data-bs-toggle='pill']",
    function () {
      var $container = $(this).closest(".metadata-accordion-content");
      refreshMetadataAccordionLayout($container.attr("id") || null);
    }
  );

  $(document).on("shown.bs.collapse", ".accordion .accordion-collapse", function () {
    var $container = $(this).find(".metadata-accordion-content").first();
    if ($container.length) {
      refreshMetadataAccordionLayout($container.attr("id") || null);
    }
  });

  $(document).on("hidden.bs.modal", ".workspace-modal-dialog", function () {
    clearWorkspaceModalBackdrop(false);
  });
})();

/* Excel-like Ctrl+V paste for the workbook Data grid. The grid wrapper carries
   a data-paste-input attribute naming the Shiny input to notify; we forward the
   raw clipboard text and let the server parse + apply it. */
(function () {
  if (window.__streamcurvesWorkbookPasteBound) return;
  window.__streamcurvesWorkbookPasteBound = true;

  document.addEventListener(
    "paste",
    function (e) {
      var target = e.target;
      var host = target && target.closest ? target.closest(".workbook-grid[data-paste-input]") : null;
      if (!host) return;
      var cd = e.clipboardData || window.clipboardData;
      if (!cd) return;
      var text = cd.getData("text");
      if (!text) return;
      var inputId = host.getAttribute("data-paste-input");
      if (inputId && window.Shiny && typeof Shiny.setInputValue === "function") {
        Shiny.setInputValue(inputId, { text: text, nonce: Date.now() }, { priority: "event" });
        e.preventDefault();
      }
    },
    true
  );
})();

/* After the map import hands a compiled table to the setup wizard, scroll to it. */
(function () {
  if (!window.Shiny || typeof Shiny.addCustomMessageHandler !== "function") return;
  Shiny.addCustomMessageHandler("scrollToSetupWizard", function () {
    var el = document.querySelector(".setup-wizard-card");
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "start" });
  });
})();

/* Nudge leaflet maps to repaint after their container is shown again (the import
   wizard's Region step is hidden via display:none on other steps). */
(function () {
  if (!window.Shiny || typeof Shiny.addCustomMessageHandler !== "function") return;
  Shiny.addCustomMessageHandler("invalidateLeafletSize", function () {
    setTimeout(function () {
      window.dispatchEvent(new Event("resize"));
    }, 120);
  });
})();
