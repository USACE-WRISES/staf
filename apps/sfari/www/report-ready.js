/* Report preparation is an overlay operation: preserve the workspace and focus. */
(function () {
  "use strict";
  var selector = '[data-report], [data-step="report"], .easi-batch-report-link';
  var busy = false, latest = -1, settled = -1, opener = null, focusKey = null;
  var modal = null, status = null, registered = false;
  var disabled = new Map();
  var updateQueued = false;

  function visible(el) { return !!(el && el.isConnected && el.getClientRects().length); }
  function doneButton(el) {
    if (!el || !el.matches('[data-nav="1"]')) return false;
    var active = document.querySelector('.sfari-nav-fn.active');
    var all = document.querySelectorAll('.sfari-nav-fn');
    return !!(active && Number(active.dataset.idx) === all.length - 1 && all.length);
  }
  function control(target) {
    if (!target || !target.closest) return null;
    var found = target.closest(selector + ', [data-nav="1"]');
    return found && (found.matches(selector) || doneButton(found)) ? found : null;
  }
  function controls() {
    return Array.from(document.querySelectorAll(selector + ', [data-nav="1"]'))
      .filter(function (el) { return el.matches(selector) || doneButton(el); });
  }
  function remember(el) {
    opener = el;
    if (!el) { focusKey = null; return; }
    var group = el.matches('.easi-batch-report-link') ? '.easi-batch-report-link'
      : el.matches('[data-step="report"]') ? '[data-step="report"]'
      : el.matches('[data-nav]') ? '[data-nav="1"]' : '[data-report]';
    focusKey = {selector: group, index: Array.from(document.querySelectorAll(group)).indexOf(el)};
  }
  function restoreFocus() {
    var target = opener;
    if (!visible(target) && focusKey) {
      var matches = Array.from(document.querySelectorAll(focusKey.selector));
      target = matches[focusKey.index];
      if (!visible(target)) target = matches.find(visible);
    }
    if (visible(target) && !target.disabled) target.focus({preventScroll: true});
    opener = null; focusKey = null; modal = null;
  }
  function restoreAttribute(el, name, value) {
    if (value === null) el.removeAttribute(name); else el.setAttribute(name, value);
  }
  function update() {
    controls().forEach(function (el) {
      // EASI's Report step and batch links have no href; make them keyboard controls.
      if (el.tagName === 'A' && !el.hasAttribute('href')) {
        if (!el.hasAttribute('role')) el.setAttribute('role', 'button');
        if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
      }
      if (!busy || disabled.has(el)) return;
      disabled.set(el, {disabled: el.getAttribute('disabled'), aria: el.getAttribute('aria-disabled')});
      if (el.tagName === 'BUTTON') el.setAttribute('disabled', '');
      el.setAttribute('aria-disabled', 'true');
    });
    if (!busy) {
      disabled.forEach(function (before, el) {
        restoreAttribute(el, 'disabled', before.disabled);
        restoreAttribute(el, 'aria-disabled', before.aria);
      });
      disabled.clear();
      if (status) status.remove();
      return;
    }
    if (!status) {
      status = document.createElement('div');
      status.id = 'staf-report-preparing';
      status.setAttribute('role', 'status');
      status.setAttribute('aria-live', 'polite');
      status.style.cssText = 'font-size:12px;color:#667085;margin:8px 0 0;';
      status.textContent = 'Preparing report…';
    }
    var host = visible(opener) && opener.closest('.sfari-rollup, .easi-batch-results-wrap, #leftpane');
    if (!host && visible(opener)) {
      var workspace = opener.closest('.sfari-worksheet');
      host = workspace && workspace.querySelector('.sfari-rollup');
    }
    // Automatic opening can remember the Report step in the worksheet's left
    // rail. The empty map output still has a client rect beneath that workspace.
    if (!visible(host)) host = Array.from(document.querySelectorAll('.sfari-rollup, .easi-batch-results-wrap')).find(visible);
    if (!host) {
      var pane = document.querySelector('#leftpane');
      if (visible(pane) && Array.from(pane.children).some(function (child) { return child !== status; })) host = pane;
    }
    if (host && status.parentNode !== host) host.appendChild(status);
  }
  function ownStatusChange(mutation) {
    if (!status) return false;
    if (mutation.target === status || status.contains(mutation.target)) return true;
    var nodes = Array.from(mutation.addedNodes).concat(Array.from(mutation.removedNodes));
    return nodes.length > 0 && nodes.every(function (node) { return node === status || status.contains(node); });
  }
  function onMutations(mutations) {
    if (updateQueued || mutations.every(ownStatusChange)) return;
    // Shiny and Plotly may replace several output subtrees in one turn. Avoid
    // synchronous layout reads in their mutation microtasks, and ignore our own
    // status insertion/removal so it cannot schedule another reconciliation.
    updateQueued = true;
    window.requestAnimationFrame(function () { updateQueued = false; update(); });
  }
  function receive(message) {
    var id = Number(message.requestId);
    if (!Number.isFinite(id) || id < latest || (message.busy && id <= settled)) return;
    latest = id;
    busy = !!message.busy;
    if (busy) {
      if (!visible(opener)) remember(controls().find(visible));
    } else {
      settled = id;
      if (!message.opened) { opener = null; focusKey = null; }
    }
    update();
  }
  function register() {
    if (registered || !window.Shiny || !window.Shiny.addCustomMessageHandler) return;
    window.Shiny.addCustomMessageHandler('staf-report-state', receive);
    registered = true;
  }
  document.addEventListener('click', function (event) {
    var el = control(event.target);
    if (!el) return;
    if (busy) { event.preventDefault(); event.stopImmediatePropagation(); return; }
    remember(el);
  }, true);
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return;
    var el = control(event.target);
    if (!el) return;
    if (busy) { event.preventDefault(); event.stopImmediatePropagation(); return; }
    if (el.tagName !== 'BUTTON') {
      event.preventDefault(); event.stopImmediatePropagation(); el.click();
    }
  }, true);
  if (window.jQuery) {
    window.jQuery(document).on('shiny:connected', function () {
      // Request IDs belong to a Shiny session, not to the lifetime of the page.
      busy = false; latest = -1; settled = -1; opener = null; focusKey = null; modal = null;
      update(); register();
    });
    window.jQuery(document).on('shown.bs.modal', function (event) {
      if (event.target.querySelector('#easi-report, #sfari-report, #deep-report')) modal = event.target;
    });
    window.jQuery(document).on('hidden.bs.modal', function (event) {
      if (event.target === modal) restoreFocus();
    });
  }
  document.addEventListener('shiny:connected', register);
  var observer = new MutationObserver(onMutations);
  function init() {
    register(); update();
    if (document.body) observer.observe(document.body, {childList: true, subtree: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
