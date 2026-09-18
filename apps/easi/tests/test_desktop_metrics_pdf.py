"""The desktop metrics list: the rows behind the Get Forms table and its PDF."""
from __future__ import annotations

import io

import pytest

import calculator_cases as cc
from easi import config, report
from test_report import _result as report_result

DELINEATION = {"comid": 9327042, "gnis_name": "Mink Brook", "snapped_lat": 43.6858, "snapped_lon": -72.2367,
               "drainage_area_sqkm": 47.2}
NOTE = 'culvert outfall, see <b>photo</b> & the </para> sketch'


def _result(ratings=None, note=""):
    rep = cc.score_case({"record": {}, "observed": None, "ratings": ratings})
    rep["metricRows"] = [{**row, "userNote": note if row["metricId"] == cc.FUNCTIONS["m03"] else ""}
                         for row in rep["metricRows"]]
    return {"delineation": dict(DELINEATION), "report": rep}


def _pdf_text(data: bytes) -> str:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(io.BytesIO(data))
    return " ".join(" ".join((page.extract_text() or "").split()) for page in reader.pages)


def test_rows_follow_the_catalog_and_carry_the_traced_values():
    rows = report.desktop_metric_rows(_result({cc.FUNCTIONS["m03"]: "Poor"}, note="seen on imagery"))
    assert [r["metricId"] for r in rows] == list(config.metrics_by_id())
    assert [r["n"] for r in rows] == list(range(1, 21))
    m01, m03, m05 = rows[0], rows[2], rows[4]
    assert m01["inputs"] == [{"label": "Watershed impervious cover", "value": "8%"},
                             {"label": "Watershed agricultural cover", "value": "25%"}]
    # a rating the assessor changed keeps the computed one beside it, and the values it replaced
    assert (m03["rating"], m03["assessed"], m03["computed"], m03["note"]) == ("Poor", True, "Fair", "seen on imagery")
    assert m03["inputs"] == [{"label": "Watershed road density", "value": "1.2 km/km²"}]
    assert not m01["assessed"] and m01["computed"] == ""
    # context inputs (mean annual flow, the feature code) are not entries of the calculator
    assert [i["label"] for i in m05["inputs"]] == ["Monthly flow variability"]


def test_rows_survive_a_missing_or_empty_report():
    for broken in (None, {}, {"report": {}}, {"report": {"metricRows": [{}]}}):
        rows = report.desktop_metric_rows(broken)
        assert len(rows) == 20 and all(r["function"] and r["metric"] for r in rows)
        assert all(r["rating"] == "" and r["inputs"] == [] for r in rows)


def test_value_text():
    text = report._input_value_text
    assert text(8.0, "%") == "8%" and text(1.25, "km/km2") == "1.25 km/km2"
    assert text(1.1, "ratio") == "1.1" and text(0.71, "probability") == "0.71" and text(0, "mapped dams") == "0 mapped dams"
    assert text("4A") == "4A" and text(None, "%") == "-" and text("", "%") == "-"


def test_the_pdf_lists_the_twenty_metrics_with_the_site_and_the_override_scores():
    data = report.build_desktop_metrics_pdf(_result({cc.FUNCTIONS["m03"]: "Poor"}, note=NOTE))
    assert data[:4] == b"%PDF"
    text = _pdf_text(data)
    assert "EASI Desktop Metrics" in text and "Mink Brook" in text
    for label in ("COMID 9327042", "43.68580, -72.23670", "NARS-9 region TPL", "NHD feature code 46006",
                  "Functions rated 20 of 20"):
        assert label in text, label
    squeezed = text.replace(" ", "")
    for meta in config.metrics_by_id().values():        # table cells wrap, so compare without spaces
        assert meta["functionName"].replace(" ", "") in squeezed, meta["functionName"]
    assert "Watershed impervious cover: 8%" in text
    assert "override, computed Fair" in text
    assert "Override scores: Reach inflow Poor (computed Fair)." in text
    # the note is the assessor's free text: markup in it is printed, not parsed
    assert "culvert outfall, see <b>photo</b> & the </para> sketch" in text
    assert "—" not in text


def test_the_report_pdf_prints_a_note_with_markup_in_it():
    # regression: the note went to Paragraph unescaped, so "<b>" or a stray "</para>" failed the download
    res = report_result()
    res["report"]["metricRows"][1]["userNote"] = NOTE
    assert report.build_pdf(res)[:4] == b"%PDF"


def test_download_names():
    assert report.desktop_metrics_filename({"delineation": DELINEATION}) == "easi-desktop-metrics-comid-9327042.pdf"
    routed = {"delineation": DELINEATION, "siteAnchor": {"anchorKind": "hrSurrogate",
                                                         "clickedStream": {"nhdplusId": "10000900012345"}}}
    assert report.site_slug(routed) == "nhdplusid-10000900012345"
    assert report.site_slug({"delineation": {"snapped_lat": 43.6858, "snapped_lon": -72.2367}}) == \
        "n43.68580-w72.23670"
    assert report.site_slug({"delineation": {"comid": "12/../x"}}) == "comid-12..x"      # a file name, not a path
    assert report.desktop_metrics_filename({}) == "easi-desktop-metrics.pdf" and report.site_slug(None) == ""
