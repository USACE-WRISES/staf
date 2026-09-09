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


def test_metric_table_has_a_scored_at_column_in_the_advanced_group():
    rows = _rows()
    rows[0]["anchorLabel"] = "HR reach watershed (STAF site engine)"
    html = str(app._metric_table(rows, {}))
    assert ">Scored at<" in html
    assert "HR reach watershed (STAF site engine)" in html
    # the aligned rollup keeps its placeholders in step with the new column
    html2 = str(app._metric_table(rows, {}, outcomes=_outcomes(), eci=0.61))
    foot = html2.split("easi-rollup-foot", 1)[1].split("</tfoot>", 1)[0]
    first_row = foot.split("</tr>", 1)[0]
    assert first_row.count("easi-col-adv") == 2


def test_xs_readonly_block_has_the_medians_but_no_section_table():
    cands = [{"label": "100 ft", "entrenchment_ratio": 1.4, "bank_height_ratio": 1.0},
             {"label": "200 ft", "entrenchment_ratio": 1.8, "bank_height_ratio": 1.3},
             {"label": "300 ft", "entrenchment_ratio": 3.1, "bank_height_ratio": 2.0}]
    reach = {"n": 3, "entrenchment_ratio": {"median": 1.8, "min": 1.4, "max": 3.1, "n": 3},
             "bank_height_ratio": {"median": 1.3, "min": 1.0, "max": 2.0, "n": 3}}
    html = str(app._xs_readonly_block({"crossSection": {
        "png_b64": "abc123",
        "geom": {"division": "Interior Plains", "entrenchment_ratio": 1.8,
                 "bank_height_ratio": 1.3},
        "candidates": cands, "selected": 1, "default": 1, "reach": reach}}))
    # the per-section table is worksheet-only (2026-09-07)
    assert "easi-xs-reach" not in html and "Reach cross-sections" not in html
    assert "Section ER" in html and "Reach median ER" in html
    assert "1.80 (1.40 to 3.10, 3 sections)" in html
    # no reach statistics: no median rows
    plain = str(app._xs_readonly_block({"crossSection": {
        "png_b64": "abc123", "geom": {}, "candidates": cands, "selected": 1}}))
    assert "Reach median" not in plain


def test_xs_reach_table_lists_every_section_and_labels_the_cap():
    cands = [{"label": "100 ft", "entrenchment_ratio": 1.4, "bank_height_ratio": 1.0},
             {"label": "200 ft", "entrenchment_ratio": 1.8, "bank_height_ratio": 1.3},
             {"label": "300 ft", "entrenchment_ratio": 3.1, "bank_height_ratio": 2.0}]
    reach = {"n": 3, "entrenchment_ratio": {"median": 1.8, "min": 1.4, "max": 3.1, "n": 3},
             "bank_height_ratio": {"median": 1.3, "min": 1.0, "max": 2.0, "n": 3}}
    tbl = str(app._xs_reach_table_ui(cands, 1, 1, reach))
    assert "100 ft" in tbl and "300 ft" in tbl and "> default<" in tbl
    assert "Reach median" in tbl and "1.40 to 3.10" in tbl
    assert tbl.count("font-weight:600") == 3          # only the shown section's column
    assert "≥" not in tbl and "no bank found" not in tbl
    # capped sections read as at least 2.0, with the footnote
    capped = [dict(c, low_bank_capped=(c["bank_height_ratio"] == 2.0)) for c in cands]
    reach_c = {"n": 3, "entrenchment_ratio": reach["entrenchment_ratio"],
               "bank_height_ratio": {"median": 1.3, "min": 1.0, "max": 2.0, "n": 3,
                                     "capped": 1, "median_capped": False, "max_capped": True}}
    tbl2 = str(app._xs_reach_table_ui(capped, 1, 1, reach_c))
    assert "≥2.00" in tbl2 and "no bank found below the floodprone stage" in tbl2
    assert "1.30" in tbl2 and "(1.00 to ≥2.00)" in tbl2
    assert app._xs_reach_table_ui(cands[:1], 0, 0, reach) is None


def _routed_anchor():
    return {"anchorKind": "hrSurrogate", "clickedStream": {"gnisName": None},
            "scoredReach": {"gnisName": "Mink Brook", "comid": 9327042},
            "routing": {"routedDistanceFt": 1687.6, "daRatio": 32.02, "daRatioLimit": 10.0,
                        "declined": False},
            "metricAnchors": {"m1": {"anchor": "watershed", "label": "HR reach watershed",
                                     "name": "Stream Temperature"}}}


def test_the_routine_routed_site_shows_no_banner():
    """2026-09-08: where the watershed metrics come from is already a row of the
    Basin characteristics block, and which rows are borrowed is now a marker on
    the rows, so the box above the report repeated both."""
    d = {"watershed_source": "site-engine",
         "watershed_engine": {"engineVersion": "0.2.2", "areaSqkm": 1.0005}}
    assert app._anchor_banner(_routed_anchor(), d) is None
    assert app._anchor_banner({"anchorKind": "v2Direct"}, d) is None


def test_the_banner_survives_for_a_watershed_the_engine_could_not_calculate():
    """The one state the basin block cannot convey: the metrics are missing."""
    d = {"watershed_source": "not-calculated",
         "watershed_engine": {"reason": "walk budget exceeded"}}
    html = str(app._anchor_banner(_routed_anchor(), d))
    assert "<b>Note</b>" in html and "\u26a0" not in html and "Warning" not in html
    assert "could not calculate its watershed" in html
    assert "32 times" not in html                            # the ratio stays off the banner
    assert app.NOTE_BOX_STYLE.split(";")[0] in html          # neutral box, not amber
    assert "#fff7e0" not in html
    assert "Stream Temperature" not in html


def test_the_footnote_and_the_marker_appear_together():
    """The marker on a row and the line explaining it come from the same test,
    so a marked row can never be left unexplained."""
    anchor = _routed_anchor()
    base = dict(_rows()[0])
    borrowed = dict(base, metricId="m-borrowed", name="Low-flow Wetted Connectivity",
                    anchorNote="Scored from the nearest StreamCat reach, 1,688 ft downstream.")
    plain = dict(base, metricId="m-plain", name="Stream Temperature")
    foot = str(app._borrowed_footnote([borrowed, plain], anchor))
    assert app.BORROWED_MARK in foot
    assert "Comes from the nearest StreamCat reach, 1,688 ft downstream." in foot
    assert "Low flow" not in foot                            # the marker names the rows
    tbl = str(app._metric_table([borrowed, plain], {}))
    assert f"<sup title=" in tbl and app.BORROWED_MARK in tbl
    assert tbl.count(app.BORROWED_MARK) == 1                 # only the borrowed row
    # nothing marked means nothing to explain
    assert app._borrowed_footnote([plain], anchor) is None
    assert app.BORROWED_MARK not in str(app._metric_table([plain], {}))


def test_the_header_carries_the_watershed_map_when_geometry_is_passed(monkeypatch):
    """EASI's report had no map; the geometry was in the result but never handed
    to the body. This pins the plumbing and the no-geometry fallback."""
    from easi import reportmap

    monkeypatch.setattr(reportmap, "topo_png", lambda *a, **k: None)   # no network
    ws = {"type": "FeatureCollection", "features": [{"geometry": {
        "type": "Polygon", "coordinates": [[[-83.0, 40.0], [-82.99, 40.0],
                                            [-82.99, 40.01], [-83.0, 40.0]]]}}]}
    d = {"gnis_name": "Test Creek", "snapped_lat": 40.0, "snapped_lon": -83.0,
         "reach_length_ft": 1000}

    rep = {"basin": {"rows": [["Drainage area", "1.02 km2"]]}}
    with_map = str(app._header_with_map(d, rep, {"watershed": ws, "reach": None}))
    assert "sfari-minimap" in with_map and "<path" in with_map
    assert "Test Creek" in with_map
    # the basin table shares the row with the map instead of dropping below it
    assert "Basin characteristics" in with_map
    assert with_map.index("Basin characteristics") < with_map.index("sfari-minimap")

    for empty in (None, {}, {"watershed": None, "reach": None}):
        plain = str(app._header_with_map(d, rep, empty))
        assert "sfari-minimap" not in plain and "Test Creek" in plain
