"""Report labels for the two watershed engines: the basis line, the GeoJSON
provenance, and the field-form packet's desktop values."""
from __future__ import annotations

import json

import pytest

from deep import curves, measure, report

ASC = [{"x": 0, "y": 0}, {"x": 1, "y": 1}]
BUNDLE = {"assessmentId": "t", "assessmentName": "T", "sourceCitation": "cite",
          "predictorSource": "site-engine v0.2.0",
          "metricsByFunction": [
              {"functionId": "f1", "functionName": "Catchment hydrology", "discipline": "Hydrology",
               "metrics": [{"metricId": "imp", "metricName": "Impervious cover", "xLabel": "%",
                            "predictorSource": "site-engine v0.2.0",
                            "curve": {"points": ASC, "layerName": "seed"}},
                           {"metricId": "field", "metricName": "Bank angle", "xLabel": "deg",
                            "curve": {"points": ASC, "layerName": "seed"}}]}]}
HR_DELIN = {"delineation": {"comid": 5214461, "nhdplus_id": 750012345, "network": "nhdplus-hr",
                            "gnis_name": "Sugar Run", "huc8": "05060001",
                            "snapped_lat": 40.31125, "snapped_lon": -83.05615,
                            "drainage_area_sqkm": 4.19, "reach_length_ft": 500},
            "watershedBasis": "site-engine",
            "siteEngine": {"status": "ok", "engineVersion": "0.2.0"}}
STATE = {"imp": {"value": 0.4, "origin": "desktop", "engine": True, "basis": "site-engine",
                 "source": "STAF site engine v0.2.0 impervious (exact watershed, NLCD 2021)"},
         "field": {"value": 0.2, "origin": "field"}}


def test_watershed_basis_label():
    assert report.watershed_basis_label(HR_DELIN) == "exact watershed (STAF site engine v0.2.0)"
    assert report.watershed_basis_label({}) == "NHDPlus V2 basin (StreamCat lookup engine)"
    upgraded = {"watershedBasis": "nhdplus-v2-basin",
                "siteEngine": {"status": "ok", "engineVersion": "0.2.0"}}
    assert report.watershed_basis_label(upgraded) == (
        "NHDPlus V2 basin drawn, exact watershed computed (STAF site engine v0.2.0)")
    assert "nearest covered reach" in report.watershed_basis_label(
        {"watershedBasis": "nhdplus-v2-basin-of-surrogate"})


def test_rows_score_engine_values_against_engine_curves():
    rows = {m["metricId"]: (val, idx, meta) for _fn, m, val, idx, meta in report._rows(BUNDLE, STATE)}
    assert rows["imp"][1] == 0.4 and rows["imp"][2]["reference_only"] is False
    assert rows["imp"][2]["predictor_source"] == "site-engine v0.2.0"
    assert rows["field"][2]["origin"] == "field" and rows["field"][2]["basis"] == ""


def test_geojson_carries_predictor_source_and_basis():
    sc, _ = curves.score_site(BUNDLE, measure.measured_from_state(STATE))
    gj = json.loads(report.build_geojson(HR_DELIN, BUNDLE, sc, measured=STATE))
    props = gj["features"][0]["properties"]
    assert props["predictor_source"] == "site-engine v0.2.0"
    assert props["watershed_basis"] == "site-engine"
    assert props["engine_values_withheld"] == 0
    sc_bundle = {**BUNDLE, "predictorSource": None,
                 "metricsByFunction": [{**BUNDLE["metricsByFunction"][0], "metrics": [
                     {k: v for k, v in m.items() if k != "predictorSource"}
                     for m in BUNDLE["metricsByFunction"][0]["metrics"]]}]}
    gj = json.loads(report.build_geojson(HR_DELIN, sc_bundle, sc, measured=STATE))
    assert gj["features"][0]["properties"]["engine_values_withheld"] == 1


def test_csv_header_has_the_basis_and_predictor_source():
    sc, _ = curves.score_site(BUNDLE, measure.measured_from_state(STATE))
    csv = report.build_csv(HR_DELIN, BUNDLE, STATE, sc)
    assert "Watershed basis,exact watershed (STAF site engine v0.2.0)" in csv
    assert "Predictor source,site-engine v0.2.0" in csv
    # the source label carries a comma, so the csv module quotes that cell
    assert ',desktop,site-engine,"STAF site engine v0.2.0 impervious' in csv


def test_field_forms_print_desktop_values_and_the_site():
    pypdf = pytest.importorskip("pypdf")
    pdf = report.build_field_forms_pdf(BUNDLE, ref="t@v1", measured=STATE, delineation=HR_DELIN)
    assert pdf[:4] == b"%PDF"
    text = " ".join((pg.extract_text() or "") for pg in pypdf.PdfReader(__import__("io").BytesIO(pdf)).pages)
    text = " ".join(text.split())
    assert "NHDPlusID 750012345" in text and "Sugar Run" in text
    assert "exact watershed (STAF site engine v0.2.0)" in text
    assert "DESKTOP: STAF site engine v0.2.0 impervious" in text
    assert "0.4" in text
    # the legacy call shape still builds
    assert report.build_field_forms_pdf(BUNDLE)[:4] == b"%PDF"


def test_field_forms_flag_reference_only_values():
    pypdf = pytest.importorskip("pypdf")
    sc_bundle = {**BUNDLE, "predictorSource": None,
                 "metricsByFunction": [{**BUNDLE["metricsByFunction"][0], "metrics": [
                     {k: v for k, v in m.items() if k != "predictorSource"}
                     for m in BUNDLE["metricsByFunction"][0]["metrics"]]}]}
    pdf = report.build_field_forms_pdf(sc_bundle, measured=STATE, delineation=HR_DELIN)
    text = " ".join((pg.extract_text() or "") for pg in pypdf.PdfReader(__import__("io").BytesIO(pdf)).pages)
    assert "(reference only)" in " ".join(text.split())
