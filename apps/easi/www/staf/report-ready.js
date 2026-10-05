/* Report preparation is an overlay operation: preserve the workspace and focus.
 *
 * The server sends {requestId, busy, opened, ns} on "staf-report-state". A page can hold several
 * tool bodies (the STAF app, staf-ns.js); each tool keeps its own request sequence, busy state,
 * status line and opener, so one tool's report never blocks or reorders another's. */
(function () {
  "use strict";
  var NS = window.STAFNs;
  var selector = '[data-report], [data-step="report"], .easi-batch-report-link';
  var REPORT_DIALOGS = {"easi-report": "easi", "sfari-report": "sfari", "deep-report": "deep"};
  var tools = {};          // tool prefix -> its report state
  var registered = false, updateQueued = false;

  function fresh() {
    return {busy: false, latest: -1, settled: -1, opener: null, focusKey: null, modal: null, status: null,
            disabled: new Map()};
  }
  function stateOf(prefix) { return tools[prefix] || (tools[prefix] = fresh()); }
  function each(fn) { Object.keys(tools).forEach(function (prefix) { fn(tools[prefix], prefix); }); }
  // the element a tool's controls live in: its body, or the page when it has no body
  function scopeOf(prefix) { var root = NS.forNs(prefix); return root || (prefix ? null : document); }
  function prefixOf(el) { return NS.ns(NS.owner(el)); }

  function visible(el) { return !!(el && el.isConnected && el.getClientRects().length); }
  function doneButton(el) {
    if (!el || !el.matches('[data-nav="1"]')) return false;
    var scope = NS.scope(NS.rootOf(el));
    var active = scope.querySelector('.sfari-nav-fn.active');
    var all = scope.querySelectorAll('.sfari-nav-fn');
    return !!(active && Number(active.dataset.idx) === all.length - 1 && all.length);
  }
  function control(target) {
    if (!target || !target.closest) return null;
    var found = target.closest(selector + ', [data-nav="1"]');
    return found && (found.matches(selector) || doneButton(found)) ? found : null;
  }
  function controls(scope) {
    return Array.from(scope.querySelectorAll(selector + ', [data-nav="1"]'))
      .filter(function (el) { return el.matches(selector) || doneButton(el); });
  }
  function remember(st, scope, el) {
    st.opener = el;
    if (!el) { st.focusKey = null; return; }
    var group = el.matches('.easi-batch-report-link') ? '.easi-batch-report-link'
      : el.matches('[data-step="report"]') ? '[data-step="report"]'
      : el.matches('[data-nav]') ? '[data-nav="1"]' : '[data-report]';
    st.focusKey = {selector: group, index: Array.from(scope.querySelectorAll(group)).indexOf(el)};
  }
  function restoreFocus(st, prefix) {
    var target = st.opener, scope = scopeOf(prefix);
    if (!visible(target) && st.focusKey && scope) {
      var matches = Array.from(scope.querySelectorAll(st.focusKey.selector));
      target = matches[st.focusKey.index];
      if (!visible(target)) target = matches.find(visible);
    }
    if (visible(target) && !target.disabled) target.focus({preventScroll: true});
    st.opener = null; st.focusKey = null; st.modal = null;
  }
  function restoreAttribute(el, name, value) {
    if (value === null) el.removeAttribute(name); else el.setAttribute(name, value);
  }
  function keyboard() {
    // EASI's Report step and batch links have no href; make them keyboard controls.
    controls(document).forEach(function (el) {
      if (el.tagName === 'A' && !el.hasAttribute('href')) {
        if (!el.hasAttribute('role')) el.setAttribute('role', 'button');
        if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
      }
    });
  }
  function updateTool(st, prefix) {
    var scope = scopeOf(prefix);
    if (st.busy && scope) {
      controls(scope).forEach(function (el) {
        if (st.disabled.has(el)) return;
        st.disabled.set(el, {disabled: el.getAttribute('disabled'), aria: el.getAttribute('aria-disabled')});
        if (el.tagName === 'BUTTON') el.setAttribute('disabled', '');
        el.setAttribute('aria-disabled', 'true');
      });
    }
    if (!st.busy || !scope) {
      st.disabled.forEach(function (before, el) {
        restoreAttribute(el, 'disabled', before.disabled);
        restoreAttribute(el, 'aria-disabled', before.aria);
      });
      st.disabled.clear();
      if (st.status) st.status.remove();
      return;
    }
    if (!st.status) {
      st.status = document.createElement('div');
      st.status.id = 'staf-report-preparing' + (prefix ? '-' + prefix : '');
      st.status.setAttribute('role', 'status');
      st.status.setAttribute('aria-live', 'polite');
      st.status.style.cssText = 'font-size:12px;color:#667085;margin:8px 0 0;';
      st.status.textContent = 'Preparing report…';
    }
    var leftpane = '#' + NS.id(NS.forNs(prefix), 'leftpane');
    var host = visible(st.opener) && st.opener.closest('.sfari-rollup, .easi-batch-results-wrap, ' + leftpane);
    if (!host && visible(st.opener)) {
      var workspace = st.opener.closest('.sfari-worksheet');
      host = workspace && workspace.querySelector('.sfari-rollup');
    }
    // Automatic opening can remember the Report step in the worksheet's left
    // rail. The empty map output still has a client rect beneath that workspace.
    if (!visible(host)) host = Array.from(scope.querySelectorAll('.sfari-rollup, .easi-batch-results-wrap')).find(visible);
    if (!host) {
      var pane = scope.querySelector(leftpane);
      if (visible(pane) && Array.from(pane.children).some(function (child) { return child !== st.status; })) host = pane;
    }
    if (host && st.status.parentNode !== host) host.appendChild(st.status);
  }
  function update() { keyboard(); each(updateTool); }
  function ownStatusChange(mutation) {
    var mine = false;
    each(function (st) {
      var status = st.status;
      if (!status || mine) return;
      if (mutation.target === status || status.contains(mutation.target)) { mine = true; return; }
      var nodes = Array.from(mutation.addedNodes).concat(Array.from(mutation.removedNodes));
      mine = nodes.length > 0 && nodes.every(function (node) { return node === status || status.contains(node); });
    });
    return mine;
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
    var prefix = (message && message.ns) || '', st = stateOf(prefix);
    var id = Number(message.requestId);
    if (!Number.isFinite(id) || id < st.latest || (message.busy && id <= st.settled)) return;
    st.latest = id;
    st.busy = !!message.busy;
    if (st.busy) {
      var scope = scopeOf(prefix);
      if (!visible(st.opener) && scope) remember(st, scope, controls(scope).find(visible));
    } else {
      st.settled = id;
      if (!message.opened) { st.opener = null; st.focusKey = null; }
    }
    update();
  }
  function register() {
    if (registered || !window.Shiny || !window.Shiny.addCustomMessageHandler) return;
    window.Shiny.addCustomMessageHandler('staf-report-state', receive);
    registered = true;
  }
  function blockOrRemember(event, el) {
    var prefix = prefixOf(el), st = stateOf(prefix);
    if (st.busy) { event.preventDefault(); event.stopImmediatePropagation(); return true; }
    remember(st, NS.scope(NS.forNs(prefix)), el);
    return false;
  }
  document.addEventListener('click', function (event) {
    var el = control(event.target);
    if (!el) return;
    blockOrRemember(event, el);
  }, true);
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return;
    var el = control(event.target);
    if (!el) return;
    var st = stateOf(prefixOf(el));
    if (st.busy) { event.preventDefault(); event.stopImmediatePropagation(); return; }
    if (el.tagName !== 'BUTTON') {
      event.preventDefault(); event.stopImmediatePropagation(); el.click();
    }
  }, true);
  if (window.jQuery) {
    window.jQuery(document).on('shiny:connected', function () {
      // Request IDs belong to a Shiny session, not to the lifetime of the page.
      each(function (st) {
        st.busy = false; st.latest = -1; st.settled = -1; st.opener = null; st.focusKey = null; st.modal = null;
      });
      update(); register();
    });
    window.jQuery(document).on('shown.bs.modal', function (event) {
      var report = event.target.querySelector('#easi-report, #sfari-report, #deep-report');
      if (report) stateOf(NS.ns(NS.tool(REPORT_DIALOGS[report.id]))).modal = event.target;
    });
    window.jQuery(document).on('hidden.bs.modal', function (event) {
      each(function (st, prefix) { if (event.target === st.modal) restoreFocus(st, prefix); });
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
