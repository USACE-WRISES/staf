"""Offline tests for export builders (CSV / GeoJSON / PDF)."""
from __future__ import annotations

import json

from easi import report


def _result():
    rep = {
        "metricRows": [
            {"metricId": "catchment-hydrology-impervious-surface-cover",
             "name": "Impervious Surface Cover", "discipline": "Hydrology",
             "functionName": "Catchment hydrology", "functionId": "catchment-hydrology",
             "scale": "W", "confidence": "H", "rating": "Poor", "generatedRating": "Poor",
             "index": 0.195, "functionScore": 3, "valueText": "38% impervious",
             "criteria": ">25%", "source": "EPA StreamCat", "status": "ok",
             "overrideable": False},
            {"metricId": "community-dynamics-invasive-non-native-species-presence",
             "name": "Invasive Species", "discipline": "Biology",
             "functionName": "Community dynamics", "functionId": "community-dynamics",
             "scale": "W", "confidence": "M", "rating": "Fair", "generatedRating": "Fair",
             "index": 0.545, "functionScore": 8, "valueText": "2 taxa",
             "criteria": "5-50%", "source": "USGS NAS", "status": "ok",
             "overrideable": True},
        ],
        "functionScores": {"catchment-hydrology": 3, "community-dynamics": 8},
        "subIndices": {"physical": 0.2, "chemical": 0.3, "biological": 0.45},
        "outcomes": {k: {"direct": 1, "indirect": 0, "weighted": 3.0, "max": 15.0,
                         "subIndex": 0.2} for k in ("physical", "chemical", "biological")},
        "ecosystemConditionIndex": 0.32, "computedCount": 2, "totalCount": 20,
        "overridesApplied": [],
    }
    return {
        "delineation": {"gnis_name": "Test Creek", "comid": 123, "huc12": "050600010025",
                        "drainage_area_sqkm": 10.0, "watershed_area_sqkm": 10.1,
                        "reach_length_ft": 1000.0, "snapped_lat": 40.0,
                        "snapped_lon": -83.0, "warnings": []},
        "report": rep,
        "watershed_geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates":
             [[[-83, 40], [-83.01, 40], [-83.01, 40.01], [-83, 40]]]}, "properties": {}}]},
        "reach_geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "LineString",
             "coordinates": [[-83, 40], [-83.001, 40.001]]}, "properties": {}}]},
    }


def test_build_csv():
    b = report.build_csv(_result())
    assert isinstance(b, bytes)
    assert b"EASI Report" in b and b"Impervious Surface Cover" in b
    assert b"Ecosystem Condition Index" in b


def test_build_geojson():
    fc = json.loads(report.build_geojson(_result()))
    assert fc["type"] == "FeatureCollection"
    types = {f["properties"]["type"] for f in fc["features"]}
    assert {"watershed", "reach", "point"} <= types
    pt = next(f for f in fc["features"] if f["properties"]["type"] == "point")
    assert pt["properties"]["ecosystem_condition_index"] == 0.32


def test_build_pdf():
    b = report.build_pdf(_result())
    assert b[:4] == b"%PDF" and len(b) > 1000


def test_basin_section_in_exports():
    res = _result()
    res["report"]["basin"] = {"rows": [["Drainage area", "10.0 km²"],
                                       ["Channel slope", "0.0042 m/m (0.42%)"]]}
    assert report.build_pdf(res)[:4] == b"%PDF"
    assert b"Channel slope" in report.build_csv(res)
    gj = json.loads(report.build_geojson(res))
    pt = next(f for f in gj["features"] if f["properties"]["type"] == "point")
    assert pt["properties"]["basin_characteristics"]["Drainage area"] == "10.0 km²"


def test_build_pdf_with_cross_section():
    from easi import xsplot
    res = _result()
    res["report"]["crossSection"] = {
        "png_b64": xsplot.cross_section_png_b64([0, 5, 10], [2, 0, 2], bankfull_stage=1.0),
        "caption": "test cross-section",
    }
    b = report.build_pdf(res)
    assert b[:4] == b"%PDF" and len(b) > 1000


def test_notes_in_exports_when_present():
    res = _result()
    res["report"]["metricRows"][1]["userNote"] = "Verified in field — looks worse"
    csv_b = report.build_csv(res)
    assert b"Notes" in csv_b and "Verified in field".encode() in csv_b
    gj = json.loads(report.build_geojson(res))
    pt = next(f for f in gj["features"] if f["properties"]["type"] == "point")
    mid = "community-dynamics-invasive-non-native-species-presence"
    assert pt["properties"]["metrics"][mid]["note"] == "Verified in field — looks worse"
    assert report.build_pdf(res)[:4] == b"%PDF"


def test_notes_absent_when_no_notes():
    res = _result()  # no userNote on any row
    assert b"Notes" not in report.build_csv(res)        # column omitted entirely
    gj = json.loads(report.build_geojson(res))
    pt = next(f for f in gj["features"] if f["properties"]["type"] == "point")
    assert "note" not in pt["properties"]["metrics"]["catchment-hydrology-impervious-surface-cover"]


def test_xs_reach_sentence():
    cands = [{"label": f"{100 * i} ft", "entrenchment_ratio": er, "bank_height_ratio": bhr}
             for i, (er, bhr) in enumerate([(1.26, 2.0), (1.47, 1.81), (2.24, 2.0)], start=1)]
    reach = {"n": 3, "entrenchment_ratio": {"median": 1.47, "min": 1.26, "max": 2.24, "n": 3},
             "bank_height_ratio": {"median": 2.0, "min": 1.81, "max": 2.0, "n": 3}}
    s = report._xs_reach_sentence({"candidates": cands, "selected": 1, "default": 1,
                                   "reach": reach})
    assert s == ("Metrics score on the reach medians of 3 sections: entrenchment ratio 1.47 "
                 "(1.26 to 2.24), bank-height ratio 2.00 (1.81 to 2.00). The section at "
                 "200 ft, nearest both medians, is shown.")
    # without stored statistics the sentence is computed from the sections
    assert report._xs_reach_sentence({"candidates": cands, "selected": 2, "default": 1}) == (
        "Metrics score on the reach medians of 3 sections: entrenchment ratio 1.47 "
        "(1.26 to 2.24), bank-height ratio 2.00 (1.81 to 2.00). The section at 300 ft, "
        "chosen in the worksheet, is shown.")
    assert report._xs_reach_sentence({"candidates": cands[:1]}) == ""
    assert report._xs_reach_sentence({}) == "" and report._xs_reach_sentence(None) == ""


def test_xs_reach_sentence_says_at_least_for_a_capped_median():
    cands = [{"label": "100 ft", "entrenchment_ratio": 1.5, "bank_height_ratio": 2.0,
              "low_bank_capped": True},
             {"label": "200 ft", "entrenchment_ratio": 1.6, "bank_height_ratio": 1.2,
              "low_bank_capped": False},
             {"label": "300 ft", "entrenchment_ratio": 1.7, "bank_height_ratio": 2.0,
              "low_bank_capped": True}]
    s = report._xs_reach_sentence({"candidates": cands, "selected": 0, "default": 0})
    assert "bank-height ratio at least 2.00 (1.20 to at least 2.00)" in s
    assert "≥" not in s                                  # the PDF's standard fonts
