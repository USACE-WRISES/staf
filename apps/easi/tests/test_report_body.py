"""The report modal is read-only: the STAF metric table renders static chips and note text
(never editable controls); scoring detail is revealed client-side by the toggle toolbar."""
from __future__ import annotations

import app
from easi import config


def _rows():
    """One scored row per discipline (rating chips exercise all three bands)."""
    metas = list(config.metrics_by_id().values())
    seen, rows = set(), []
    ratings = ["Good", "Fair", "Poor"]
    for m in metas:
        if m["discipline"] in seen:
            continue
        seen.add(m["discipline"])
        rows.append({"metricId": m["metricId"], "name": m["name"],
                     "discipline": m["discipline"], "functionName": m["functionName"],
                     "valueText": "example value", "rating": ratings[len(rows) % 3],
                     "generatedRating": "Good", "functionScore": 13, "status": "computed"})
    return rows


def _outcomes():
    return {k: {"direct": 1, "indirect": 0, "weighted": 3.0, "max": 15.0, "subIndex": 0.62}
            for k in ("physical", "chemical", "biological")}


def test_metric_table_is_read_only():
    html = str(app._metric_table(_rows(), {}))
    assert "easi-rate-chip" in html                 # static rating chips
    assert "easi-rate-sel" not in html              # no override dropdown in the report
    assert "easi-note-ta" not in html               # no editable note textarea
    assert "easi-note-btn" not in html              # no note button either
    # one shaded separator row per distinct discipline present in the rows
    assert html.count("easi-disc") == len({r["discipline"] for r in _rows()})


def test_metric_table_emits_toggleable_detail():
    html = str(app._metric_table(_rows(), {}))
    for col in ("Function", "Metric", "Value", "Rating", "Function Score", "Index", "Note"):
        assert f">{col}<" in html
    assert "easi-col-map" in html and "easi-col-adv" in html    # hidden until toggled on
    assert "easi-fslider" in html and "easi-fscore-plain" in html  # slider + plain score


def test_metric_table_rollup():
    html = str(app._metric_table(_rows(), {}, outcomes=_outcomes(), eci=0.61))
    assert "easi-rollup-foot" in html and "easi-rollup-standalone" in html
    assert "Ecosystem Condition Index" in html
    assert "0.61" in html
    assert "easi-band" in html                      # band-tinted sub-index / ECI cells


def test_report_body_sections_and_notes():
    rows = _rows()
    rep = {"metricRows": rows, "outcomes": _outcomes(),
           "functionScores": {}, "subIndices": {"physical": 0.6, "chemical": 0.7,
                                                "biological": 0.5},
           "ecosystemConditionIndex": 0.6}
    notes = {rows[0]["metricId"]: "surveyed on site"}
    d = {"gnis_name": "Test Creek", "snapped_lat": 44.0, "snapped_lon": -123.0,
         "reach_length_ft": 1000, "comid": 1, "huc12": "010203040506"}
    body = str(app._report_body(d, rep, notes, ""))
    assert "Test Creek" in body                      # summary header
    assert "surveyed on site" in body                # worksheet note shows as static text
    assert ">Metrics<" in body and ">Summary plots<" in body
    assert body.count("easi-toggle-item") == len(app._METRIC_TOGGLES)  # display toolbar
    assert "show-slider" in body                     # slider visual on by default
    assert "easi-rate-sel" not in body               # fully read-only
    assert "data-rep-expand" not in body             # old expander controls are gone
    # boilerplate removed from the popup (the PDF keeps its own disclaimer copy)
    assert "Use the checkboxes" not in body
    assert "Generated from national datasets" not in body
    assert "easi-instr" not in body


def test_report_modal_has_close_hint():
    # the single-site modal header carries the ✕ plus a muted "close to review" cue
    res = {"delineation": {"gnis_name": "Test Creek", "snapped_lat": 44.0,
                           "snapped_lon": -123.0, "reach_length_ft": 1000, "comid": 1},
           "report": {"metricRows": [], "outcomes": _outcomes(), "functionScores": {},
                      "subIndices": {"physical": 0.6, "chemical": 0.7, "biological": 0.5},
                      "ecosystemConditionIndex": 0.6}}
    m = str(app._report_modal(res, {}))
    assert "easi-modal-hint" in m and "Close to review the Assessment" in m
    assert "close_modal_x" in m


def test_xs_readonly_block():
    assert app._xs_readonly_block({"crossSection": {}}) is None
    assert app._xs_readonly_block({}) is None
    html = str(app._xs_readonly_block({"crossSection": {
        "png_b64": "abc123",
        "geom": {"division": "Appalachian Highlands", "bankfull_width_m": 26.8,
                 "flood_prone_width_m": 56.7, "entrenchment_ratio": 2.11,
                 "bank_height_ratio": 1.03}}}))
    assert "easi-xs-panel" in html and "easi-xsection-wrap" in html
    assert "Cross-section geometry" in html
    assert "data:image/png;base64,abc123" in html
