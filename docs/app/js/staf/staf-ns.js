/* Which STAF tool a page element belongs to (STAF shared asset; load it before the other scripts).
 *
 * Every tool body carries data-staf-tool="<tool>" and data-staf-ns="<prefix>". A standalone app's
 * body has an empty prefix and plain Shiny ids ("map"); inside the combined STAF app the prefix is
 * the tool's module id and every id carries it ("sfari-map"). A page without any tool body (an
 * older page, a test harness) behaves like one tool with no prefix. Scripts never spell an input
 * id themselves: they post to STAFNs.id(root, name), so one file serves both kinds of page, and a
 * tool's own script acts only on events inside its body, or in a dialog while it is the tool
 * shown (STAFNs.mine).
 */
(function () {
  "use strict";
  var NS = "data-staf-ns", TOOL = "data-staf-tool";

  function roots() {
    if (typeof document === "undefined" || !document.querySelectorAll) return [];
    return Array.prototype.slice.call(document.querySelectorAll("[" + NS + "]"));
  }
  function rootOf(el) { return el && el.closest ? el.closest("[" + NS + "]") : null; }
  function ns(root) { return (root && root.getAttribute(NS)) || ""; }
  function id(root, name) { var prefix = ns(root); return prefix ? prefix + "-" + name : name; }
  // an element's id without its tool's prefix ("sfari-lat" -> "lat")
  function bare(el) {
    var raw = (el && el.id) || "", prefix = ns(rootOf(el));
    return prefix && raw.indexOf(prefix + "-") === 0 ? raw.slice(prefix.length + 1) : raw;
  }
  function tool(name) {
    var list = roots();
    for (var i = 0; i < list.length; i++) if (list[i].getAttribute(TOOL) === name) return list[i];
    return null;
  }
  // the tool the STAF app shows (html[data-staf-tool]); empty on a standalone page
  function active() {
    var html = typeof document !== "undefined" && document.documentElement;
    return (html && html.getAttribute && html.getAttribute(TOOL)) || "";
  }
  function shown() {
    var name = active(), list = roots();
    if (name) { var root = tool(name); if (root) return root; }
    return list.length === 1 ? list[0] : null;
  }
  // the body an element belongs to; an element outside every body (a dialog) belongs to the tool shown
  function owner(el) { return rootOf(el) || shown(); }
  function forNs(prefix) {
    var want = prefix || "", list = roots();
    for (var i = 0; i < list.length; i++) if (ns(list[i]) === want) return list[i];
    return null;
  }
  // whether a tool's own script acts on an event at ``el``: inside the tool's body, or outside
  // every body (a dialog) while the tool is the one shown (always, on a standalone page)
  function mine(el, name) {
    var root = rootOf(el), showing = active();
    return root ? root.getAttribute(TOOL) === name : !showing || showing === name;
  }
  function scope(root) { return root || document; }

  window.STAFNs = {roots: roots, rootOf: rootOf, ns: ns, id: id, bare: bare, tool: tool, active: active,
                   shown: shown, owner: owner, forNs: forNs, mine: mine, scope: scope};
})();
