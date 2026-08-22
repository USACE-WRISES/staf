"""The inline SVG curve thumbnails (streamcurves/curve_svg.py): pure builders,
asserted on the markup they return."""
from __future__ import annotations

import re

import pandas as pd

from streamcurves import curve_svg as cs
from streamcurves import run_state as rs


def _tile(**over):
    base = {
        "metric": "phab_XEMBED", "display_name": "Embeddedness", "function": "Hyporheic connectivity",
        "units": "%", "in_scope": True, "needs_review": False,
        "review_status": rs.CURVE_STATUS_AUTO_OK, "decision": rs.DECISION_AUTO,
        "reference_range": (20.0, 80.0), "domain": (0.0, 100.0),
        "strata": [{"label": None, "points": [(10.0, 1.0), (40.0, 0.7), (70.0, 0.3), (100.0, 0.0)],
                    "curve_status": "complete", "n_reference": 23, "curve_source": "auto"}],
        "flags": [], "badge": None,
    }
    base.update(over)
    return base


def _polylines(svg: str) -> list[str]:
    return re.findall(r"<polyline[^>]*>", svg)


def test_tile_svg_draws_one_polyline_per_stratum_with_distinct_dashes():
    strata = [dict(label=lab, points=[(0.0, 0.0), (5.0 + i, 0.7), (10.0, 1.0)], curve_status="complete",
                   n_reference=20, curve_source="auto") for i, lab in enumerate(("A", "B", "C"))]
    svg = cs.tile_svg(_tile(strata=strata))
    lines = _polylines(svg)
    assert len(lines) == 3
    assert "stroke-dasharray" not in lines[0]
    assert 'stroke-dasharray="6 3"' in lines[1] and 'stroke-dasharray="2 3"' in lines[2]
    assert 'data-stratum="B"' in lines[1]


def test_tile_svg_dotted_red_only_when_out_of_scope():
    in_scope = cs.tile_svg(_tile())
    assert "out-of-scope" not in in_scope and cs.LINE_COLOR in in_scope
    out = cs.tile_svg(_tile(in_scope=False, decision=rs.DECISION_REMOVED))
    lines = _polylines(out)
    assert lines and all("out-of-scope" in ln and f'stroke-dasharray="{cs.OUT_OF_SCOPE_DASH}"' in ln for ln in lines)
    assert cs.OUT_OF_SCOPE_COLOR in out
    unreviewed = cs.tile_svg(_tile(in_scope=None, decision=None, review_status=None))
    assert "out-of-scope" not in unreviewed


def test_tile_svg_flag_marker_only_when_needs_review():
    assert "curve-tile-flag" not in cs.tile_svg(_tile())
    svg = cs.tile_svg(_tile(needs_review=True, decision=rs.DECISION_PENDING,
                            flags=["status data_review <b>", "influence flag"]))
    assert svg.count("curve-tile-flag") == 1
    assert "<title>status data_review &lt;b&gt;</title>" in svg
    bare = cs.tile_svg(_tile(needs_review=True, decision=rs.DECISION_PENDING, flags=[]))
    assert "<title>Needs review</title>" in bare


def test_tile_svg_placeholder_keeps_the_footprint_when_there_are_no_points():
    svg = cs.tile_svg(_tile(strata=[], reference_range=(None, None)), w=240, h=150)
    assert svg.startswith('<svg viewBox="0 0 240 150"')
    assert 'data-empty="1"' in svg and "No curve" in svg and "curve-tile-empty" in svg
    assert not _polylines(svg)
    assert svg.count("curve-tile-band") == 3


def test_tile_svg_band_breaks_default_to_the_deep_contract_and_flip_with_the_argument():
    assert cs.DEEP_INDEX_BANDS == (0.39, 0.69) and cs.DRAWING_BANDS == (0.3, 0.7)
    default = cs.tile_svg(_tile())
    assert 'data-breaks="0.39,0.69"' in default and default.count("curve-tile-break") == 2
    drawing = cs.tile_svg(_tile(), band_breaks=cs.DRAWING_BANDS)
    assert 'data-breaks="0.3,0.7"' in drawing


def test_tile_svg_reference_range_rect_present_or_omitted():
    assert "curve-tile-range" in cs.tile_svg(_tile())
    assert "curve-tile-range" not in cs.tile_svg(_tile(reference_range=(None, None)))


def test_tile_svg_escapes_labels():
    svg = cs.tile_svg(_tile(display_name="Embeddedness <b>x</b>", metric='a"b'))
    assert "<b>x</b>" not in svg and "&lt;b&gt;x&lt;/b&gt;" in svg
    assert 'data-metric="a&quot;b"' in svg


def test_points_from_curve_row_accepts_nested_frame_records_and_flat_columns():
    nested = {"curve_points": pd.DataFrame({"point_order": [2, 1, 3], "metric_value": [5.0, 0.0, 10.0],
                                            "index_score": [0.7, 0.0, 1.2]})}
    assert cs.points_from_curve_row(nested) == [(0.0, 0.0), (5.0, 0.7), (10.0, 1.0)]
    records = {"curve_points": [{"x": 3.0, "y": 0.5}, {"x": 1.0, "y": 0.0}]}
    assert cs.points_from_curve_row(records) == [(1.0, 0.0), (3.0, 0.5)]
    flat = {"curve_points": None, "curve_point1_x": 0.0, "curve_point1_y": 1.0,
            "curve_point2_x": 9.0, "curve_point2_y": 0.0}
    assert cs.points_from_curve_row(flat) == [(0.0, 1.0), (9.0, 0.0)]
    assert cs.points_from_curve_row({"curve_points": None}) == []
    assert cs.points_from_curve_row(None) == []


def test_tile_from_curve_rows_scope_rules_and_reference_range_union():
    rows = [{"stratum": "A", "min_val": 5.0, "max_val": 40.0, "n_reference": 12, "curve_status": "complete",
             "curve_source": "auto", "curve_points": [{"x": 5.0, "y": 0.0}, {"x": 40.0, "y": 1.0}]},
            {"stratum": float("nan"), "min_val": 1.0, "max_val": 30.0, "n_reference": 9, "curve_status": "complete",
             "curve_source": "manual", "curve_points": [{"x": 1.0, "y": 0.0}, {"x": 30.0, "y": 1.0}]}]
    mc = {"display_name": "Sinuosity", "units": "ratio", "domain_min": 1.0, "domain_max": None}
    pending = {"status": rs.CURVE_STATUS_DATA_REVIEW, "decision": rs.DECISION_PENDING,
               "reasons": ["Missing-data fraction exceeds the review threshold (DATA-03)."]}
    t = cs.tile_from_curve_rows("phab_SINU", rows, metric_entry=mc, review_entry=pending,
                                function_label="Channel and floodplain dynamics")
    assert t["display_name"] == "Sinuosity" and t["units"] == "ratio" and t["domain"] == (1.0, None)
    assert t["reference_range"] == (1.0, 40.0)
    assert [s["label"] for s in t["strata"]] == ["A", None]
    assert t["strata"][1]["curve_source"] == "manual" and t["strata"][0]["n_reference"] == 12
    assert t["needs_review"] is True and t["in_scope"] is False and t["decision"] == rs.DECISION_PENDING
    assert t["flags"] == pending["reasons"]

    removed = cs.tile_from_curve_rows("m", rows, review_entry={"status": "auto_ok", "decision": rs.DECISION_REMOVED})
    assert removed["in_scope"] is False and removed["needs_review"] is False

    none = cs.tile_from_curve_rows("m", rows, review_entry=None)
    assert none["in_scope"] is None and none["decision"] is None and none["flags"] == []

    override = cs.tile_from_curve_rows("m", rows, review_entry={"status": "auto_ok", "decision": rs.DECISION_REMOVED},
                                       in_scope=True)
    assert override["in_scope"] is True

    status_only = cs.tile_from_curve_rows("m", rows, review_entry={"status": rs.CURVE_STATUS_DEGENERATE})
    assert status_only["decision"] == rs.DECISION_PENDING
    assert cs.tile_from_curve_rows("m", rows, review_entry={"status": "auto_ok"})["decision"] == rs.DECISION_AUTO


def test_tile_state_classes_and_labels():
    assert cs.tile_state_classes(_tile()) == []
    assert "is-flagged" in cs.tile_state_classes(_tile(needs_review=True, decision=rs.DECISION_PENDING))
    assert "is-removed" in cs.tile_state_classes(_tile(in_scope=False, decision=rs.DECISION_REMOVED))
    assert "is-finalized" in cs.tile_state_classes(_tile(decision=rs.DECISION_FINALIZED))
    assert "is-unreviewed" in cs.tile_state_classes(_tile(decision=None, review_status=None, in_scope=None))
    assert "is-not-run" in cs.tile_state_classes(_tile(strata=[]))
    two = _tile(strata=[_tile()["strata"][0], dict(_tile()["strata"][0], label="B")])
    assert "is-stratified" in cs.tile_state_classes(two)
    assert set(cs.STATUS_LABELS) == set(rs.CURVE_STATUSES)
    assert cs.status_label(_tile()) == "Clean build"
    assert cs.status_label(_tile(review_status=None, decision=None)) == "Unreviewed"
    assert cs.tile_title(_tile(flags=["influence flag"])) == "Embeddedness. Auto. influence flag"


def test_gallery_html_is_self_contained_with_one_tile_per_row():
    tiles = [_tile(), _tile(metric="phab_PCT_FAST", in_scope=False, decision=rs.DECISION_REMOVED, badge="Low"),
             _tile(metric="phab_SINU", needs_review=True, decision=rs.DECISION_PENDING, flags=["status data_review"])]
    page = cs.gallery_html(tiles, title="Interior Plateau: 3 curves")
    assert page.startswith("<!doctype html>") and "<style>" in page and "<script" not in page
    assert page.count('class="curve-tile ') + page.count('class="curve-tile"') == 3
    assert page.count("<svg ") == 3
    assert "is-removed" in page and "is-flagged" in page and ">Low<" in page
    assert "Interior Plateau: 3 curves" in page and "0.39 and 0.69" in page
