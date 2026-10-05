"use strict";
// node --test apps/easi/tests/test_worksheet.cjs
// EASI's worksheet and report scripts on their own page and beside SFARI and DEEP in the STAF app,
// whose worksheets use the same markers: they act only inside EASI's body (or in a dialog while
// EASI is shown) and post to EASI's own inputs.
const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const WWW = path.join(__dirname, "..", "www");
const { El, page: fakePage } = require(path.join(__dirname, "..", "..", "..", "libs", "staf_workbook",
                                                  "tests", "fakedom.cjs"));

function page(options) {
  const p = fakePage({ ...options, dirs: [WWW, path.join(WWW, "staf")] });
  p.load("staf-ns.js", "worksheet.js", "report-edit.js");
  return p;
}

// a stepper link, a next button, an observed entry and a rating override
function worksheet(root) {
  const step = root.appendChild(new El("a", { "data-step": "review" }));
  const next = root.appendChild(new El("button", { "data-nav": "1" }));
  const observed = root.appendChild(new El("select", { class: "easi-obs-in", "data-mid": "m4", "data-key": "class" }));
  const rating = root.appendChild(new El("select", { class: "easi-rate-sel", "data-mid": "m4" }));
  return { step, next, observed, rating };
}

test("in the STAF app EASI posts to its own inputs and leaves the other worksheets alone", () => {
  const p = page({ bodies: [["easi", "easi"], ["sfari", "sfari"], ["deep", "deep"]], showing: "easi" });
  const easi = worksheet(p.tools.easi), sfari = worksheet(p.tools.sfari), deep = worksheet(p.tools.deep);
  p.fire("click", easi.step);
  p.fire("click", sfari.step);       // SFARI's own script answers that one
  p.fire("click", deep.next);        // and DEEP's this one
  assert.deepEqual(p.names(), ["easi-step_nav"]);
  assert.equal(p.posts[0].value, "review");            // EASI's step payload is the key itself
  p.fire("click", easi.next);
  easi.observed.value = "C4";
  p.fire("change", easi.observed);
  easi.rating.value = "fair";
  p.fire("change", easi.rating);
  sfari.rating.value = "poor";
  p.fire("change", sfari.rating);
  assert.deepEqual(p.names().slice(1), ["easi-nav_move", "easi-observed_set", "easi-override_set"]);
});

test("a dialog's report button posts to EASI only while EASI is shown", () => {
  for (const [showing, expected] of [["easi", ["easi-open_report_evt"]], ["deep", []]]) {
    const p = page({ bodies: [["easi", "easi"], ["deep", "deep"]], showing });
    const button = p.body.appendChild(new El("div", { class: "modal" }))
      .appendChild(new El("button", { "data-report": "" }));
    p.fire("click", button);
    assert.deepEqual(p.names(), expected, showing);
  }
});

test("Zoom Home reads the window EASI publishes under its own id", () => {
  const p = page({ bodies: [["sfari", "sfari"], ["easi", "easi"]], showing: "easi" });
  const relayouts = [];
  p.window.Plotly = { relayout: (gd, update) => relayouts.push(update) };
  p.context.Plotly = p.window.Plotly;
  const card = p.tools.easi.appendChild(new El("div", { class: "easi-xs-in-card" }));
  card.appendChild(new El("div", { class: "js-plotly-plot" }));
  const button = card.appendChild(new El("a", { "data-xs-view": "reset" }));
  const windowOut = p.tools.easi.appendChild(new El("div", { id: "easi-xs_window_range" }));
  windowOut.textContent = JSON.stringify({ x: [0, 40], y: [90, 96] });
  p.fire("click", button);
  assert.equal(relayouts.length, 1);
  assert.deepEqual([...relayouts[0]["xaxis.range"]], [0, 40]);   // a copy out of the page's realm
});

test("on its own page EASI posts its plain input names", () => {
  for (const bodies of [[["easi", ""]], []]) {         // a standalone body, and a page without one
    const p = page({ bodies });
    const easi = worksheet(bodies.length ? p.tools.easi : p.body);
    p.fire("click", easi.step);
    easi.rating.value = "good";
    p.fire("change", easi.rating);
    assert.deepEqual(p.names(), ["step_nav", "override_set"]);
  }
});
