"use strict";
// node --test apps/sfari/tests/test_field_review.cjs
// SFARI's worksheet script on its own page and beside EASI and DEEP in the STAF app: it acts only
// inside SFARI's body (or in a dialog while SFARI is shown) and posts to SFARI's own inputs.
const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const WWW = path.join(__dirname, "..", "www");
const { El, page: fakePage } = require(path.join(__dirname, "..", "..", "..", "libs", "staf_workbook",
                                                  "tests", "fakedom.cjs"));

function page(options) {
  const p = fakePage({ ...options, dirs: [WWW, path.join(WWW, "staf")] });
  p.load("staf-ns.js", "field-review.js");
  return p;
}

// a stepper link, a function panel with one rating, the rated counter and a report button
function worksheet(root) {
  const step = root.appendChild(new El("a", { "data-step": "review" }));
  const select = new El("select", { class: "sfari-likert-select", "data-mid": "m1" });
  root.appendChild(new El("div", { class: "sfari-fnpanel" })).add(select);
  const count = root.appendChild(new El("span", { class: "sfari-sec-count" }));
  const report = root.appendChild(new El("button", { "data-report": "" }));
  return { step, select, count, report };
}

test("in the STAF app SFARI posts to its own inputs and leaves the other tools alone", () => {
  const p = page({ bodies: [["easi", "easi"], ["sfari", "sfari"], ["deep", "deep"]], showing: "sfari" });
  const easi = worksheet(p.tools.easi), sfari = worksheet(p.tools.sfari), deep = worksheet(p.tools.deep);
  p.fire("click", sfari.step);
  p.fire("click", easi.step);        // EASI's own script answers that one
  p.fire("click", deep.report);      // and DEEP's this one
  assert.deepEqual(p.names(), ["sfari-step_nav"]);
  assert.deepEqual(p.posts[0].value.key, "review");
  sfari.select.value = "4";
  p.fire("change", sfari.select);
  assert.deepEqual(p.names().slice(1), ["sfari-likert_set"]);
  assert.equal(sfari.count.textContent, "1 of 1 rated");
  deep.select.value = "2";
  p.fire("change", deep.select);
  assert.equal(p.posts.length, 2, "a rating in DEEP's body is DEEP's");
  assert.equal(deep.count.textContent, "", "SFARI never writes DEEP's counter");
});

test("a dialog's report button posts to SFARI only while SFARI is shown", () => {
  for (const [showing, expected] of [["sfari", ["sfari-open_report_evt"]], ["easi", []]]) {
    const p = page({ bodies: [["easi", "easi"], ["sfari", "sfari"]], showing });
    const button = p.body.appendChild(new El("div", { class: "modal" }))
      .appendChild(new El("button", { "data-report": "" }));
    p.fire("click", button);
    assert.deepEqual(p.names(), expected, showing);
  }
});

test("on its own page SFARI posts its plain input names", () => {
  for (const bodies of [[["sfari", ""]], []]) {      // a standalone body, and a page without one
    const p = page({ bodies });
    const sfari = worksheet(bodies.length ? p.tools.sfari : p.body);
    p.fire("click", sfari.step);
    sfari.select.value = "3";
    p.fire("change", sfari.select);
    assert.deepEqual(p.names(), ["step_nav", "likert_set"]);
    assert.equal(sfari.count.textContent, "1 of 1 rated");
  }
});
