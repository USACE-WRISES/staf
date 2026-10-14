// Apply STAF page: the DEEP calculator picker. Choosing an ecoregion points Download at its
// calculator; its version and status ride in the button's tooltip, and the list groups the
// ecoregions by status (owner, 2026-10-08). The Map button opens a map of the Level III ecoregions
// (DEEP's own outlines, docs/assets/data/ecoregions-l3-map.json, built by
// apps/deep/scripts/build_site_ecoregion_map.py): a click selects that ecoregion. Drafts are
// listed only when asked (owner, 2026-10-14): the Draft group joins the list when "Show Draft
// assessments" is ticked (or the map key's Draft item is clicked), and until then a Draft's
// region on the map is neutral and takes no click. Nothing is remembered. Without JavaScript the
// picker stays hidden and the noscript list of links, with EPA's PDF map, stands in.
(function () {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';
  // Each region takes its calculator's status color, as on DEEP's own map (custom.css holds DEEP's
  // palette): a fixed table, never a class built from the label. The key lists them in DEEP's
  // legend order.
  var STATUS_CLASS = { Draft: 'is-draft', Preliminary: 'is-preliminary', Final: 'is-final' };
  var STATUS_ORDER = ['Draft', 'Preliminary', 'Final'];

  function statusClass(status) {
    return Object.prototype.hasOwnProperty.call(STATUS_CLASS, status) ? STATUS_CLASS[status] : null;
  }

  function initPicker(route) {
    var controls = route.querySelectorAll('[data-calc-controls]');
    var select = route.querySelector('[data-calc-select]');
    var link = route.querySelector('[data-calc-link]');
    if (!controls.length || !select || !link) {
      return;
    }
    var draftGroup = select.querySelector('optgroup[data-calc-draft]');
    var toggle = route.querySelector('[data-calc-drafts]');
    var allOptions = Array.prototype.slice.call(select.options);   // the map looks up every one
    var restored = select.options[select.selectedIndex];
    // a Draft the browser restored (back navigation) keeps the Drafts listed
    var drafts = { shown: !!(draftGroup && restored && restored.parentNode === draftGroup),
                   all: allOptions, listeners: [] };

    function sync() {
      var option = select.options[select.selectedIndex];
      var url = option ? option.value : '';
      if (url) {
        link.setAttribute('href', url);
        link.setAttribute('title', option.getAttribute('data-label') || '');
      } else {
        link.removeAttribute('href');
        link.removeAttribute('title');
      }
    }

    drafts.set = function (on) {
      drafts.shown = !!on;
      if (draftGroup) {
        if (drafts.shown && !draftGroup.parentNode) {
          select.appendChild(draftGroup);
        } else if (!drafts.shown && draftGroup.parentNode) {
          var current = select.options[select.selectedIndex];
          if (current && current.parentNode === draftGroup) {
            select.value = '';
          }
          select.removeChild(draftGroup);
        }
      }
      if (toggle) {
        toggle.checked = drafts.shown;
      }
      sync();
      drafts.listeners.forEach(function (fn) { fn(drafts.shown); });
    };

    select.addEventListener('change', sync);
    if (toggle) {
      toggle.addEventListener('change', function () { drafts.set(toggle.checked); });
    }
    Array.prototype.forEach.call(controls, function (el) { el.hidden = false; });
    drafts.set(drafts.shown); // hidden at first; a choice the browser restored is honored
    initMap(route, select, drafts);
  }

  function initMap(route, select, drafts) {
    var opener = route.querySelector('[data-eco-map-open]');
    var dialog = route.querySelector('[data-eco-map]');
    if (!opener || !dialog || typeof dialog.showModal !== 'function' || !window.fetch) {
      return; // the Map button keeps opening EPA's PDF map in a new tab
    }
    var canvas = dialog.querySelector('[data-eco-map-canvas]');
    var caption = dialog.querySelector('[data-eco-map-caption]');
    var key = dialog.querySelector('[data-eco-map-key]');
    var hint = caption.textContent;
    var paths = {};
    var loading = null;

    function optionFor(code) {
      for (var i = 0; i < drafts.all.length; i += 1) {
        if (drafts.all[i].getAttribute('data-code') === code) {
          return drafts.all[i];
        }
      }
      return null;
    }

    // a Draft while the Drafts are not listed: shown neutral, never chosen from the map
    function hiddenDraft(option) {
      return !!option && option.getAttribute('data-status') === 'Draft' && !drafts.shown;
    }

    function selectedCode() {
      var option = select.options[select.selectedIndex];
      return option ? option.getAttribute('data-code') : null;
    }

    function describe(code) {
      var option = optionFor(code);
      var path = paths[code];
      var name = (path ? path.getAttribute('data-name') : '') + ' (' + code + ')';
      if (!option) {
        return name + ', no calculator yet';
      }
      var status = option.getAttribute('data-status');
      if (hiddenDraft(option)) {
        return name + ' \u00b7 Draft (hidden)';
      }
      return status ? name + ' \u00b7 ' + status : name;
    }

    function markSelected() {
      var current = selectedCode();
      Object.keys(paths).forEach(function (code) {
        paths[code].classList.toggle('is-selected', code === current);
      });
    }

    function keyItem(cls, label) {
      var item = document.createElement('span');
      var swatch = document.createElement('i');
      swatch.className = 'eco-map-swatch ' + cls;
      swatch.setAttribute('aria-hidden', 'true');
      item.appendChild(swatch);
      item.appendChild(document.createTextNode(label));
      return item;
    }

    // the key's Draft item is the Drafts' switch too: the dialog is modal, so the page's box
    // cannot be reached from inside it
    function draftKeyItem() {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'eco-map-key-toggle';
      button.setAttribute('aria-pressed', drafts.shown ? 'true' : 'false');
      button.setAttribute('title', (drafts.shown ? 'Hide' : 'Show') + ' Draft assessments');
      var swatch = document.createElement('i');
      swatch.className = 'eco-map-swatch is-draft' + (drafts.shown ? '' : ' is-off');
      swatch.setAttribute('aria-hidden', 'true');
      button.appendChild(swatch);
      button.appendChild(document.createTextNode('Draft'));
      button.addEventListener('click', function () { drafts.set(!drafts.shown); });
      return button;
    }

    var keyState = null;
    function renderKey(present, missing) {
      if (!key) {
        return;
      }
      keyState = { present: present, missing: missing };
      key.textContent = '';
      STATUS_ORDER.forEach(function (status) {
        if (present[status]) {
          key.appendChild(status === 'Draft' ? draftKeyItem() : keyItem(STATUS_CLASS[status], status));
        }
      });
      if (missing) {
        key.appendChild(keyItem('is-unavailable', 'No calculator yet'));
      }
      key.hidden = !key.firstChild;
    }

    function render(map) {
      var svg = document.createElementNS(SVG_NS, 'svg');
      svg.setAttribute('viewBox', '0 0 ' + map.width + ' ' + map.height);
      svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', 'Map of the EPA Level III ecoregions of the conterminous United States');
      var regions = document.createElementNS(SVG_NS, 'g');
      regions.setAttribute('class', 'eco-map-regions');
      var present = {};
      var missing = false;
      map.regions.forEach(function (region) {
        var path = document.createElementNS(SVG_NS, 'path');
        path.setAttribute('d', region.d);
        path.setAttribute('data-code', region.code);
        path.setAttribute('data-name', region.name);
        var option = optionFor(region.code);
        var status = option ? option.getAttribute('data-status') : null;
        if (!option) {
          missing = true;
        } else if (statusClass(status)) {
          present[status] = true;
        }
        paths[region.code] = path;
        regions.appendChild(path);
      });
      var states = document.createElementNS(SVG_NS, 'path');
      states.setAttribute('d', map.states);
      states.setAttribute('class', 'eco-map-states');
      // the hovered region's outline, drawn over everything; the regions never move (moving the
      // node under the pointer between press and release would cancel the click)
      var hover = document.createElementNS(SVG_NS, 'path');
      hover.setAttribute('class', 'eco-map-hover');
      svg.appendChild(regions);
      svg.appendChild(states);
      svg.appendChild(hover);

      regions.addEventListener('mouseover', function (event) {
        var code = event.target.getAttribute('data-code');
        if (code) {
          hover.setAttribute('d', event.target.getAttribute('d'));
          caption.textContent = describe(code);
        }
      });
      regions.addEventListener('mouseleave', function () {
        hover.removeAttribute('d');
        caption.textContent = hint;
      });
      regions.addEventListener('click', function (event) {
        var code = event.target.getAttribute('data-code');
        var option = code ? optionFor(code) : null;
        if (!option || hiddenDraft(option)) {
          return;
        }
        select.value = option.value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        dialog.close();
        select.focus();
      });

      canvas.textContent = '';
      canvas.appendChild(svg);
      paint();
      renderKey(present, missing);
      markSelected();
    }

    // each region's class from its option: its status, neutral when it has no calculator or is a
    // Draft while the Drafts are not listed; on every change of the Drafts too
    function paint() {
      Object.keys(paths).forEach(function (code) {
        var path = paths[code];
        var option = optionFor(code);
        var status = option ? option.getAttribute('data-status') : null;
        ['is-draft', 'is-preliminary', 'is-final', 'is-unavailable', 'is-hidden'].forEach(function (c) {
          path.classList.remove(c);
        });
        if (!option) {
          path.classList.add('is-unavailable');
        } else if (hiddenDraft(option)) {
          path.classList.add('is-hidden');
        } else if (statusClass(status)) {
          path.classList.add(statusClass(status));
        }
      });
    }
    drafts.listeners.push(function () {
      paint();
      if (keyState) {
        renderKey(keyState.present, keyState.missing);
      }
      markSelected();
    });

    function load() {
      if (!loading) {
        canvas.textContent = 'Loading the map…';
        loading = fetch(dialog.getAttribute('data-src'))
          .then(function (response) {
            if (!response.ok) {
              throw new Error('HTTP ' + response.status);
            }
            return response.json();
          })
          .then(render)
          .catch(function () {
            loading = null; // the next opening tries again
            canvas.textContent = 'The map could not load. EPA’s PDF map below shows every ecoregion.';
          });
      }
      return loading;
    }

    opener.addEventListener('click', function (event) {
      event.preventDefault();
      caption.textContent = hint;
      dialog.showModal();
      load();
      markSelected();
    });
    dialog.querySelector('[data-eco-map-close]').addEventListener('click', function () {
      dialog.close();
    });
    dialog.addEventListener('click', function (event) {
      var box = dialog.getBoundingClientRect();
      var outside = event.clientX < box.left || event.clientX > box.right ||
        event.clientY < box.top || event.clientY > box.bottom;
      if (event.target === dialog && outside) {
        dialog.close(); // a click on the backdrop
      }
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-calc-picker]'), initPicker);
})();
