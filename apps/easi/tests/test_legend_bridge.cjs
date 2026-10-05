"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function harness(app, jquery = true) {
  const handlers = new Map();
  const updates = [];
  let serverReady = false;
  let observer;
  let nativeCheckbox;
  let streamLabel;
  let coverageRow;

  function bind(eventNames, fn) {
    eventNames.split(" ").forEach((name) => {
      if (!handlers.has(name)) handlers.set(name, []);
      handlers.get(name).push(fn);
    });
  }
  function element() {
    const listeners = {};
    return {
      children: [], dataset: {}, checked: false,
      appendChild(child) { this.children.push(child); },
      addEventListener(name, fn) { listeners[name] = fn; },
      change(checked) { this.checked = checked; listeners.change(); },
    };
  }
  function rebuildControl() {
    const checked = nativeCheckbox ? nativeCheckbox.checked : true;
    nativeCheckbox = element();
    nativeCheckbox.checked = checked;
    streamLabel = {
      textContent: " Streams",
      querySelector() { return nativeCheckbox; },
      insertAdjacentElement(position, row) {
        assert.equal(position, "afterend");
        coverageRow = row;
      },
    };
    coverageRow = null;
    if (observer) observer();
  }
  rebuildControl();

  const list = {
    querySelector() { return coverageRow; },
    querySelectorAll() { return [streamLabel]; },
  };
  const control = { querySelector() { return list; } };
  const corner = { querySelector() { return control; } };
  const wrap = { querySelector() { return corner; } };
  const document = {
    readyState: "complete",
    querySelector(selector) { return selector === ".easi-map-wrap" ? wrap : null; },
    createElement: element,
    addEventListener: bind,
  };
  const window = {
    Shiny: {
      setInputValue(name, value, options) {
        // Models the server protocol error that originally exposed this bug.
        assert.equal(serverReady, true, "an input update preceded session initialization");
        updates.push({ name, value, options });
      },
    },
  };
  if (jquery) window.jQuery = () => ({ on: bind });

  // A page without tool bodies: the shared scripts (www/staf/, staf-ns.js first) post plain names.
  const context = {
    window, document,
    MutationObserver: function (callback) {
      observer = callback;
      this.observe = () => {};
    },
    setInterval() { throw new Error("the fixture control should already be mounted"); },
    clearInterval() {},
  };
  for (const name of ["staf-ns.js", "legend-dock.js"]) {
    const filename = path.resolve(__dirname, "..", "..", app, "www", "staf", name);
    vm.runInNewContext(fs.readFileSync(filename, "utf8"), context, { filename });
  }

  return {
    updates,
    emit(name) {
      if (name === "shiny:connected" || name === "shiny:disconnected") serverReady = false;
      if (name === "shiny:sessioninitialized") serverReady = true;
      (handlers.get(name) || []).forEach((fn) => fn());
    },
    setCoverage(checked) { coverageRow.children[0].children[0].change(checked); },
    setStreams(checked) { nativeCheckbox.change(checked); },
    coverageChecked() { return coverageRow.children[0].children[0].checked; },
    rebuildControl,
  };
}

for (const app of ["easi", "sfari", "deep"]) {
  for (const jquery of [true, false]) {
    test(app + ": no update before initialization, including mounted controls (" +
         (jquery ? "jQuery" : "DOM events") + ")", () => {
      const h = harness(app, jquery);
      assert.deepEqual(h.updates, []);
      h.setCoverage(true); // A preinit UI change is retained but cannot be published.
      h.rebuildControl();
      assert.equal(h.coverageChecked(), true);
      h.emit("shiny:connected");
      assert.deepEqual(h.updates, []);
      h.emit("shiny:sessioninitialized");
      assert.deepEqual(h.updates.map(({ name, value }) => [name, value]), [
        ["streamcat_coverage", true],
        ["streams_visible", true],
      ]);
      const count = h.updates.length;
      h.rebuildControl();
      assert.equal(h.updates.length, count, "rebuilding the menu must not resend inputs");
    });

    test(app + ": reconnect waits for initialization and replays current preferences (" +
         (jquery ? "jQuery" : "DOM events") + ")", () => {
      const h = harness(app, jquery);
      h.emit("shiny:connected");
      h.emit("shiny:sessioninitialized");
      h.setCoverage(true);
      h.setStreams(false);
      const count = h.updates.length;
      h.emit("shiny:disconnected");
      h.rebuildControl();
      h.setCoverage(false);
      h.emit("shiny:connected");
      h.setCoverage(true);
      assert.equal(h.updates.length, count, "disconnected and reconnecting UI stays local");
      h.emit("shiny:sessioninitialized");
      assert.deepEqual(h.updates.slice(count).map(({ name, value }) => [name, value]), [
        ["streamcat_coverage", true],
        ["streams_visible", false],
      ]);
      h.setCoverage(false);
      assert.equal(h.updates.at(-1).value, false);
      assert.equal(h.updates.at(-1).name, "streamcat_coverage");
    });
  }
}
