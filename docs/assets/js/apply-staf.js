// Apply STAF page: the DEEP calculator picker. Choosing an ecoregion points Download at its
// calculator; its version and status ride in the button's tooltip, and the list groups the
// ecoregions by status (owner, 2026-10-08). The Map button opens a map of the Level III ecoregions
// (DEEP's own outlines, docs/assets/data/ecoregions-l3-map.json, built by
// apps/deep/scripts/build_site_ecoregion_map.py): a click selects that ecoregion. Without
// JavaScript the picker stays hidden and the noscript list of links, with EPA's PDF map, stands in.
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

    select.addEventListener('change', sync);
    Array.prototype.forEach.call(controls, function (el) { el.hidden = false; });
    sync(); // a choice the browser restored (back navigation) is honored
    initMap(route, select);
  }

  function initMap(route, select) {
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
      for (var i = 0; i < select.options.length; i += 1) {
        if (select.options[i].getAttribute('data-code') === code) {
          return select.options[i];
        }
      }
      return null;
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

    function renderKey(present, missing) {
      if (!key) {
        return;
      }
      key.textContent = '';
      STATUS_ORDER.forEach(function (status) {
        if (present[status]) {
          key.appendChild(keyItem(STATUS_CLASS[status], status));
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
          path.classList.add('is-unavailable');
          missing = true;
        } else if (statusClass(status)) {
          path.classList.add(statusClass(status));
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
        if (!option) {
          return;
        }
        select.value = option.value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        dialog.close();
        select.focus();
      });

      canvas.textContent = '';
      canvas.appendChild(svg);
      renderKey(present, missing);
      markSelected();
    }

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
