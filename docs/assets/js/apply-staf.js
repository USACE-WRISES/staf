// Apply STAF page: the DEEP calculator picker. Choosing an ecoregion points Download at its
// calculator and shows the version it is. Without JavaScript the picker stays hidden and the
// noscript list of links stands in.
(function () {
  'use strict';

  function init(route) {
    var controls = route.querySelector('[data-calc-controls]');
    var select = route.querySelector('[data-calc-select]');
    var link = route.querySelector('[data-calc-link]');
    var label = route.querySelector('[data-calc-label]');
    if (!controls || !select || !link) {
      return;
    }
    var idle = label ? label.textContent : '';

    function sync() {
      var option = select.options[select.selectedIndex];
      var url = option ? option.value : '';
      if (url) {
        link.setAttribute('href', url);
      } else {
        link.removeAttribute('href');
      }
      if (label) {
        label.textContent = url ? (option.getAttribute('data-label') || idle) : idle;
      }
    }

    select.addEventListener('change', sync);
    controls.hidden = false;
    sync(); // a choice the browser restored (back navigation) is honored
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-calc-picker]'), init);
})();
