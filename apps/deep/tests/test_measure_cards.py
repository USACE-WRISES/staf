"""Tests for the function page's metric rows (app.py):
- the STAF ``metricStatement`` carried through the build into every metric,
- the reference curve ``_curve_svg`` (opened from the row's chart toggle),
- the (i) ``_metric_tip_html`` and the row ``_metric_row`` (SFARI's layout,
  2026-10-04): what finishing the assessment needs, and nothing about where
  the curve comes from (that is in the report and the DEEP guide).

Importing ``app`` pulls in the whole Shiny module; ``conftest.py`` puts the repo
root on ``sys.path`` so this works under any pytest invocation.
"""
from __future__ import annotations

import app
from deep import assessments, config

_PTS = [{"x": 0, "y": 1.0}, {"x": 35, "y": 0.7}, {"x": 50, "y": 0.3}]


# --------------------------------------------------------------------------- #
# metricStatement carried through the regenerated predefined library
# --------------------------------------------------------------------------- #
def test_every_built_metric_has_a_statement():
    # The state-SQT assessments guarantee a metricStatement on every metric (from the STAF
    # metric library). They are hidden from the registry now but retained in the baked data,
    # so read them straight from the baked doc. Other StreamCurves-published bundles may omit
    # the statement — the measure card drops the div (app.py reads it with .get).
    total = 0
    for a in config.assessments_doc()["assessments"]:
        if not a["assessmentId"].endswith("-sqt-adapted"):
            continue
        la = assessments.from_bundle(a)
        for fn in la.metrics_by_function:
            for m in fn["metrics"]:
                total += 1
                assert "metricStatement" in m, f"{m['metricId']} missing metricStatement"
                assert m["metricStatement"].strip(), f"{m['metricId']} has empty metricStatement"
    assert total > 0


# --------------------------------------------------------------------------- #
# _fmt_num
# --------------------------------------------------------------------------- #
def test_fmt_num_is_compact():
    assert app._fmt_num(35.0) == "35"
    assert app._fmt_num(2) == "2"
    assert app._fmt_num(1.25) == "1.25"
    assert app._fmt_num(1.250) == "1.25"


# --------------------------------------------------------------------------- #
# _curve_svg — enlarged, axis-labeled, live-updatable marker
# --------------------------------------------------------------------------- #
def test_curve_svg_has_axes_labels_and_geometry():
    svg = app._curve_svg(_PTS, value=30, xlabel="Impervious cover (%)")
    # axis titles
    assert "Impervious cover (%)" in svg          # x-axis title = xLabel
    assert "Index (0" in svg                        # y-axis title
    # plot geometry for measure.js
    for attr in ("data-x0", "data-x1", "data-y0", "data-y1", "data-xmin", "data-xmax"):
        assert attr in svg, f"missing {attr}"
    # x ticks sit on the breakpoints
    for xv in ("0", "35", "50"):
        assert f">{xv}</text>" in svg
    # marker present and VISIBLE when a value is supplied
    assert "deep-mk-dot" in svg and "deep-mk-v" in svg and "deep-mk-h" in svg
    dot = svg[svg.index('class="deep-mk-dot"'):]
    dot = dot[:dot.index("/>")]
    assert "visibility=\"hidden\"" not in dot


def test_curve_svg_marker_hidden_without_value():
    svg = app._curve_svg(_PTS, value=None, xlabel="x")
    dot = svg[svg.index('class="deep-mk-dot"'):]
    dot = dot[:dot.index("/>")]
    assert 'visibility="hidden"' in dot


def test_curve_svg_empty_points_is_empty():
    assert app._curve_svg([], value=1) == ""


# --------------------------------------------------------------------------- #
# _metric_tip_html — the name and how to measure it, nothing else
# --------------------------------------------------------------------------- #
def test_metric_tip_html_is_the_name_and_how_to_measure():
    populated = {"metricName": "Sand & fines",
                 "metricStatement": "Percent of the streambed that is sand or finer.",
                 "howToMeasure": "Percent sand and fines; higher is worse.",
                 "methodContext": "Wolman walk of 100 particles."}
    tip = app._metric_tip_html(populated)
    assert "easi-tip-title" in tip and "Sand &amp; fines" in tip      # name html-escaped
    assert "How to measure" in tip and "Wolman walk of 100 particles." in tip
    assert "higher is worse" not in tip and "Percent of the streambed" not in tip
    assert "has not been provided" not in tip
    # an older version without a method text falls back to its measurement note
    assert "Pebble count." in app._metric_tip_html({"metricName": "x",
                                                    "howToMeasure": "Pebble count."})
    # a metric with no prose -> muted fallback line, no section
    tip2 = app._metric_tip_html({"metricName": "Bare metric", "metricStatement": "",
                                 "howToMeasure": ""})
    assert "Field collection guidance has not been provided" in tip2
    assert "How to measure" not in tip2


# --------------------------------------------------------------------------- #
# _metric_row — SFARI's row: name, (i) and toggles; the value box and the index
# --------------------------------------------------------------------------- #
_ENGINE_VALUE = {"value": 1.0, "na": False, "note": "", "origin": "desktop", "engine": True,
                 "basis": "site-engine",
                 "source": "STAF site engine v0.5.0 impervious (HR reach watershed, NLCD 2021)"}


def _neh():
    return assessments.load_predefined("northeastern-highlands")


def _metric(la, mid):
    return next(m for fn in la.metrics_by_function for m in fn["metrics"] if m["metricId"] == mid)


def test_a_row_shows_only_what_finishing_the_assessment_needs():
    m = _metric(_neh(), "spring-pctimp2019ws")
    row = str(app._metric_row(m, _ENGINE_VALUE, midx=0.97))
    for gone in ("Scored against", "Read with care", "Uncertainty", "HR reach watershed",
                 "STAF site engine", "Published benchmark", "Reference curve breakpoints",
                 "deep-source-row", "deep-basis-tag", "staf-metric-scale", "sfari-metric-toggle",
                 "data-toggle="):
        assert gone not in row, gone
    # the hooks www/measure.js reads, and the row's parts
    for hook in ('data-metric="spring-pctimp2019ws"', "data-points=", 'data-fixed="1"',
                 'class="deep-metric-input"', "deep-metric-index", 'data-mid-idx=',
                 'class="deep-na"', 'data-mid-na=', "deep-domain-warn", 'data-mid-warn=',
                 'data-mid-note=', "sfari-photo", "deep-plot-wrap", "deep-curve"):
        assert hook in row, hook
    assert ">%</span>" in row and 'placeholder="Value"' in row      # the unit beside the value
    assert "0.97 · Functioning" in row
    assert "deep-desktop-tag" in row and ">Desktop<" in row          # an auto-filled value
    typed = str(app._metric_row(m, {**_ENGINE_VALUE, "origin": "field"}, midx=0.97))
    assert "deep-desktop-tag" not in typed and "deep-metric-sub" not in typed


def test_a_row_has_the_shared_layout_and_its_buttons_always_show():
    """Owner, 2026-10-04: one row design in EASI, SFARI and DEEP, with labeled buttons under
    every metric (Scoring, Note, Photo, N/A) that never wait for a hover."""
    m = _metric(_neh(), "spring-pctimp2019ws")
    row = str(app._metric_row(m, _ENGINE_VALUE, midx=0.97))
    assert row.startswith('<div data-metric="spring-pctimp2019ws"')
    for part in ('class="staf-metric deep-metric"', 'class="staf-metric-main"', 'class="staf-metric-head"',
                 'class="staf-metric-desc"', 'class="staf-metric-acts"', 'class="staf-metric-input"',
                 'data-panel="scoring"', 'data-panel="note"', 'data-panel="photo"'):
        assert part in row, part
    acts = row.split('class="staf-metric-acts"', 1)[1].split('class="staf-metric-input"', 1)[0]
    assert [a for a in ("Scoring", "Note", "Photo", "N/A") if f">{a}</span>" in acts] ==         ["Scoring", "Note", "Photo", "N/A"]
    assert acts.count('data-staf-panel="') == 3 and "deep-na-toggle" in acts
    # what the value is and which way is better, under the name
    assert ">Percent of the watershed in impervious surface; higher is worse.</div>" in row
    # the index sits under the value box, in the right column
    right = row.split('class="staf-metric-input"', 1)[1]
    assert right.index("deep-value-box") < right.index("deep-metric-index")
    # the description and the buttons come before the panels, the panels in a fixed order
    assert row.index("staf-metric-acts") < row.index('data-panel="scoring"')         < row.index('data-panel="note"') < row.index('data-panel="photo"')


def test_na_note_and_photo_states_carry_into_the_row():
    m = _metric(_neh(), "spring-pctimp2019ws")
    na = str(app._metric_row(m, {"value": 3.0, "na": True}, midx=None))
    assert 'checked="checked"' in na and 'disabled="disabled"' in na
    assert ">N/A</span>" in na and 'class="staf-act on deep-na-toggle"' in na
    noted = str(app._metric_row(m, {"value": 3.0, "note": "dry bed",
                                    "photos": [{"id": "p1", "uri": "data:x"},
                                               {"id": "p2", "uri": "data:y"}]}, midx=0.9))
    assert "show-note" in noted and "show-photo" in noted and "dry bed" in noted
    # the note button carries its dot, the photo button its count, both open
    note_btn = noted.split('data-staf-panel="note"', 1)[1].split("</button>", 1)[0]
    photo_btn = noted.split('data-staf-panel="photo"', 1)[1].split("</button>", 1)[0]
    assert 'class="staf-act on has"' in note_btn and "staf-act-dot" in note_btn
    assert 'class="staf-act on has"' in photo_btn and ">Photos</span>" in photo_btn
    assert '<span class="staf-act-count">2</span>' in photo_btn
    plain = str(app._metric_row(m, {}, midx=None))
    assert "show-note" not in plain and ">—</span>" in plain
    plain_note = plain.split('data-staf-panel="note"', 1)[1].split("</button>", 1)[0]
    assert 'class="staf-act"' in plain_note and 'aria-expanded="false"' in plain_note


def test_a_stratified_row_offers_its_curve_set_with_the_reason_on_hover():
    m = next(mm for fn in _neh().metrics_by_function for mm in fn["metrics"]
             if len(mm.get("curveLayers") or []) > 1)
    strata = [layer.get("stratum") for layer in m["curveLayers"]]
    row = str(app._metric_row(m, {"stratum": strata[1], "stratumAuto": True},
                              stratum_note="Chosen from the reach's drainage area"))
    assert "Curve set" in row and "deep-stratum-select" in row and "(auto)" in row
    assert 'title="Chosen from the reach' in row and "drainage area" in row
    assert "deep-stratum-note" not in row                 # the reason is the hover, not a line


def test_the_row_warns_only_when_the_assessor_must_act():
    pts = [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 1.0}]
    curve = {"metricId": "m", "metricName": "M", "criteriaBasis": "reference",
             "predictorSource": "site-engine", "curve": {"points": pts}}
    short, full, ref_only = app._card_warning(curve, {"value": 12.5})
    assert short == "Outside the curve's range (0 to 10); scored at the nearest end."
    assert "above the curve domain" in full and ref_only is False
    assert app._card_warning(curve, {"value": 5.0}) == ("", "", False)
    assert app._card_warning(curve, {"value": 12.5, "na": True}) == ("", "", False)
    fixed = {**curve, "criteriaBasis": "fixed"}
    assert app._card_warning(fixed, {"value": 99.0}) == ("", "", False)
    # an engine value on a StreamCat-fitted curve is kept out of the score
    streamcat = {k: v for k, v in curve.items() if k != "predictorSource"}
    engine = {"value": 5.0, "origin": "desktop", "engine": True}
    short, full, ref_only = app._card_warning(streamcat, engine)
    assert short == "Not scored: shown for reference only." and ref_only is True
    assert "StreamCat predictors" in full
    assert 'data-ref-only="1"' in str(app._metric_row(streamcat, engine))
    # once typed over, the value is the assessor's and scores
    assert app._card_warning(streamcat, {**engine, "origin": "field"})[2] is False
    assert app._fmt_bound(2.086666666666667) == "2.0867" and app._fmt_bound(0.0) == "0"


def test_the_function_band_reads_like_measure_js():
    # www/measure.js fnBand: 5 or less, then 10 or less, then above
    assert app._function_band_label(5.0) == "Non-Functioning"
    assert app._function_band_label(5.5) == "Functioning-at-Risk"
    assert app._function_band_label(10.0) == "Functioning-at-Risk"
    assert app._function_band_label(10.5) == "Functioning"
    card = str(app._scorecard("f", 12.0))
    assert "Function score" in card and ">Functioning<" in card and ">12.0<" in card
    assert "Not scored yet" in str(app._scorecard("f", None))


def test_withheld_metrics_are_quiet_rows():
    w = {"metricId": "spring-fish-nat-totlntax", "metricName": "Native fish taxa richness",
         "reason": "insufficient-reference-support",
         "statement": "Insufficient reference support. No station pool passed."}
    row = str(app._withheld_card(w))
    assert "Native fish taxa richness" in row and ">Not scored<" in row
    assert "deep-metric-withheld" in row and "deep-metric " not in row   # measure.js skips it
    assert "No station pool passed." in row                              # on its (i)
