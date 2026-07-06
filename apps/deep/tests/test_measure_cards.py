"""Tests for the Measure-tab metric card enrichments (app.py):
- the STAF ``metricStatement`` carried through the build into every metric, and
- the two render helpers ``_curve_svg`` (enlarged, labeled) and
  ``_criteria_table`` (breakpoints + your-location row), plus ``_source_line``.

Importing ``app`` pulls in the whole Shiny module; ``conftest.py`` puts the repo
root on ``sys.path`` so this works under any pytest invocation.
"""
from __future__ import annotations

import app
from deep import assessments, curves

_PTS = [{"x": 0, "y": 1.0}, {"x": 35, "y": 0.7}, {"x": 50, "y": 0.3}]


# --------------------------------------------------------------------------- #
# metricStatement carried through the regenerated predefined library
# --------------------------------------------------------------------------- #
def test_every_predefined_metric_has_a_statement():
    total = 0
    for entry in assessments.list_predefined():
        la = assessments.load_predefined(entry["assessmentId"])
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
# _criteria_table — breakpoints + highlighted your-location row
# --------------------------------------------------------------------------- #
def test_criteria_table_rows_and_here_row():
    midx = curves.interp_curve(_PTS, 30)
    tbl = app._criteria_table(_PTS, value=30, midx=midx)
    # header + one row per breakpoint + the here-row
    assert tbl.count("<tr") == 1 + len(_PTS) + 1
    assert "<th>Value</th>" in tbl and "<th>Index</th>" in tbl and "<th>Condition</th>" in tbl
    # your-location row cells carry the classes measure.js updates
    assert 'class="is-here"' in tbl
    for cls in ("deep-here-x", "deep-here-idx", "deep-here-band"):
        assert cls in tbl
    assert "deep-band-dot" in tbl
    # a real index/band is shown for the supplied value (not an em dash)
    assert f"{midx:.2f}" in tbl


def test_criteria_table_here_row_blank_without_value():
    tbl = app._criteria_table(_PTS, value=None, midx=None)
    here = tbl[tbl.index('class="is-here"'):]
    assert "—" in here                         # em-dash placeholders
    assert 'class="is-here"' in tbl


def test_criteria_table_empty_points_is_empty():
    assert app._criteria_table([], value=1, midx=0.5) == ""


# --------------------------------------------------------------------------- #
# _source_line — humanized inputType + citation + layer, deduped
# --------------------------------------------------------------------------- #
def test_source_line_humanizes_and_dedupes():
    m = {"inputType": "desktop (gis)", "sourceCitation": "Alaska SQT",
         "curve": {"layerName": "Alaska SQT"}}          # citation == layer -> deduped
    line = app._source_line(m, {})
    assert "Desktop (GIS)" in line and "Alaska SQT" in line
    assert line.count("Alaska SQT") == 1

    m2 = {"inputType": "field", "sourceCitation": "NC SQT",
          "curve": {"layerName": "NC SQT reference"}}
    line2 = app._source_line(m2, {})
    assert "Field measurement" in line2 and "NC SQT" in line2 and "NC SQT reference" in line2


def test_source_line_appends_runtime_desktop_source():
    m = {"inputType": "desktop (gis)", "sourceCitation": "SPRING synthetic",
         "curve": {"layerName": "SPRING synthetic"}}
    line = app._source_line(m, {"origin": "desktop", "source": "EPA StreamCat"})
    assert "EPA StreamCat" in line


def test_source_line_empty_when_nothing():
    assert app._source_line({"curve": {}}, {}) == ""
