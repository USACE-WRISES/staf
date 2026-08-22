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
/* Leaflet: never invalidateSize() a map that currently measures 0x0.
 *
 * Leaflet registers ONE global window-resize listener (trackResize) and
 * getSize() reads the DOM directly, so a resize reaches every map on the page
 * including those inside a display:none block, where the container measures
 * 0x0. invalidateSize() then computes offset = (oldSize - newSize) / 2 and
 * _rawPanBy()s the map pane by half the last real viewport. The pan is NOT
 * recoverable: the next invalidateSize() re-measures oldSize fresh, so the
 * arithmetic never cancels, and jupyter-leaflet does not write a resize-driven
 * move back to the widget model -- so the Python side still holds the correct
 * center while the browser shows somewhere else entirely, which is why this
 * cannot be repaired from the server. That is what left the wizard's region map
 * centered on the Caribbean after navigating between stages.
 *
 * We removed our own resize dispatch (an "invalidateLeafletSize" handler used
 * to live here -- do not reintroduce it), but that is not sufficient: the
 * dispatches come from vendored code we do not control. Two fire on every stage
 * switch, confirmed by stack trace:
 *   - shinywidgets' output.ts, from Bootstrap's shown.bs.tab
 *   - bslib's components.min.js, from a ResizeObserver
 *
 * So add the guard Leaflet itself lacks. A zero-sized map has nothing
 * meaningful to measure, and skipping it is what Leaflet would do if the
 * listener were per-map. Once the container is shown again it has its real
 * size, so the next invalidateSize() is a correct no-op (oldSize == newSize)
 * and the view is exactly where the user left it.
 *
 * L is published globally by the jupyter-leaflet bundle, which loads
 * asynchronously, so this waits for it. It sits FIRST in this file on purpose:
 * anything above it that threw would take the patch down with it, and a
 * silently unpatched Leaflet looks exactly like the original bug. */
(function () {
  if (window.__streamcurvesLeafletSizeGuard) return;

  function apply(L) {
    if (window.__streamcurvesLeafletSizeGuard) return true;
    if (!L || !L.Map || !L.Map.prototype || !L.Map.prototype.invalidateSize) return false;
    var original = L.Map.prototype.invalidateSize;
    L.Map.prototype.invalidateSize = function (options) {
      var el = this._container;
      if (el && (el.clientWidth === 0 || el.clientHeight === 0)) {
        return this; // hidden: measuring it would pan the pane off-target
      }
      return original.call(this, options);
    };
    window.__streamcurvesLeafletSizeGuard = true;
    return true;
  }

  if (apply(window.L)) return;

  /* Patch the moment the bundle publishes L, so no map can be created -- let
     alone resized -- against an unpatched prototype. */
  var pending;
  try {
    Object.defineProperty(window, "L", {
      configurable: true,
      get: function () {
        return pending;
      },
      set: function (value) {
        pending = value;
        try {
          if (apply(value)) {
            delete window.L; // restore a plain property, keeping the value
            window.L = value;
          }
        } catch (e) {
          /* fall through to the poll below */
        }
      },
    });
  } catch (e) {
    /* defineProperty refused; the poll below is the fallback */
  }

  var attempts = 0;
  (function poll() {
    if (apply(window.L)) return;
    if (attempts++ < 1200) window.setTimeout(poll, 100); // ~2 min, then give up
  })();
})();

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
  // shiny.js refuses a handler whose arity is not exactly one, and the throw
  // ends this script file, so every handler below it would stay unregistered.
  Shiny.addCustomMessageHandler("scrollToSetupWizard", function (_message) {
    var el = document.querySelector(".setup-wizard-card");
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "start" });
  });
})();

/* Scroll an element into view by id once it is visible. The server sends this
   right after a queued navset switch (the curve gallery's "show in table"
   button), and the custom message reaches the browser before the pane is
   shown, so wait for the element to have a layout box before scrolling. */
(function () {
  if (!window.Shiny || typeof Shiny.addCustomMessageHandler !== "function") return;
  Shiny.addCustomMessageHandler("scrollToElement", function (message) {
    var id = message && message.id;
    if (!id) return;
    var attempts = 0;
    (function go() {
      var el = document.getElementById(id);
      if (el && el.offsetParent !== null) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        return;
      }
      if (attempts++ < 40) window.setTimeout(go, 50);
    })();
  });
})();
