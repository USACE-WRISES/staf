"use strict";
// node --test apps/deep/tests/test_measure.cjs
// DEEP's measure worksheet script on its own page and beside EASI and SFARI in the STAF app: it
// acts only inside DEEP's body (or in a dialog while DEEP is shown) and posts to DEEP's inputs.
const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const WWW = path.join(__dirname, "..", "www");
const { El, page: fakePage } = require(path.join(__dirname, "..", "..", "..", "libs", "staf_workbook",
                                                  "tests", "fakedom.cjs"));

function page(options) {
  const p = fakePage({ ...options, dirs: [WWW, path.join(WWW, "staf")] });
  p.load("staf-ns.js", "measure.js");
  return p;
}

// one metric on a 0 -> 10 curve, its index chip, and a stepper link
function worksheet(root) {
  const input = new El("input", { class: "deep-metric-input" });
  const chip = new El("span", { class: "deep-metric-index" });
  const metric = new El("div", { class: "deep-metric", "data-metric": "m1",
                                 "data-points": JSON.stringify([{ x: 0, y: 0 }, { x: 10, y: 1 }]) }).add(input, chip);
  root.appendChild(new El("div", { class: "sfari-fnpanel-inner" })).add(metric);
  const step = root.appendChild(new El("a", { "data-step": "measure" }));
  return { input, chip, step };
}

test("in the STAF app a value typed in DEEP posts to DEEP's input and scores on the curve", () => {
  const p = page({ bodies: [["easi", "easi"], ["sfari", "sfari"], ["deep", "deep"]], showing: "deep" });
  const deep = worksheet(p.tools.deep), sfari = worksheet(p.tools.sfari);
  deep.input.value = "5";
  p.fire("input", deep.input);
  assert.deepEqual(p.names(), ["deep-measure_set"]);
  assert.equal(p.posts[0].value.mid, "m1");
  assert.equal(deep.chip.textContent, "0.50 · Functioning-at-Risk");
  sfari.input.value = "9";
  p.fire("input", sfari.input);          // not DEEP's body: not DEEP's to handle
  p.fire("click", sfari.step);
  assert.deepEqual(p.names(), ["deep-measure_set"]);
  p.fire("click", deep.step);
  p.fire("keydown", deep.step, { key: "Enter" });
  assert.deepEqual(p.names().slice(1), ["deep-step_nav", "deep-step_nav"]);
});

test("a dialog's report button posts to DEEP only while DEEP is shown", () => {
  for (const [showing, expected] of [["deep", ["deep-open_report_evt"]], ["sfari", []]]) {
    const p = page({ bodies: [["sfari", "sfari"], ["deep", "deep"]], showing });
    const button = p.body.appendChild(new El("div", { class: "modal" }))
      .appendChild(new El("button", { "data-report": "" }));
    p.fire("click", button);
    assert.deepEqual(p.names(), expected, showing);
  }
});

test("on its own page DEEP posts its plain input names", () => {
  for (const bodies of [[["deep", ""]], []]) {         // a standalone body, and a page without one
    const p = page({ bodies });
    const deep = worksheet(bodies.length ? p.tools.deep : p.body);
    deep.input.value = "10";
    p.fire("input", deep.input);
    p.fire("click", deep.step);
    assert.deepEqual(p.names(), ["measure_set", "step_nav"]);
    assert.equal(deep.chip.textContent, "1.00 · Functioning");
  }
});
