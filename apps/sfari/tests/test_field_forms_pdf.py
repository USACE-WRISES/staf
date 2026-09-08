"""The field forms and the desktop metrics PDF (2026-09-07).

The Field forms download is the blank vector worksheet, byte for byte the
shipped template: five US Letter pages with zero rotation, nothing overlaid.
The desktop metrics are their own PDF with the site, the 26 desktop metrics
with a pulled value or an explicit status, the source of each, and the status
legend. Evidence is mocked (offline); pypdf reads the structure.
"""
import io
import re

import pytest

pypdf = pytest.importorskip("pypdf")
from pypdf import PdfReader  # noqa: E402

from sfari import config, report  # noqa: E402

LETTER = (612.0, 792.0)

DELIN_COMID = {"delineation": {
    "comid": "9311402", "gnis_name": "Wildcat Creek", "huc8": "05120202",
    "snapped_lat": 39.12345, "snapped_lon": -84.51234,
    "drainage_area_sqkm": 42.7, "watershed_area_sqkm": 55.1, "reach_length_ft": 200,
}}
DELIN_HR = {"delineation": dict(DELIN_COMID["delineation"], comid=5214461,
                                nhdplus_id=750012345, network="nhdplus-hr"),
            "watershedBasis": "site-engine",
            "siteEngine": {"status": "ok", "engineVersion": "0.4.0"},
            "siteAnchor": {"anchorKind": "hrSurrogate", "scoredReach": {"comid": 5214461,
                                                                         "gnisName": "Big Run"},
                           "routing": {"routedDistanceFt": 1240.0, "daRatio": 1.8}}}


def _ev(mid, fvt="", vt="", status="ok", source="USGS NWIS 03259000",
        url="https://waterdata.usgs.gov/"):
    return {"metric_id": mid, "value_text": vt, "field_value_text": fvt,
            "suggested_likert": None, "confidence": "M", "source": source,
            "source_url": url, "status": status, "note": ""}


def _mock_evidence():
    return {
        "catchment-hydrology-impervious-surface-area": _ev(
            "catchment-hydrology-impervious-surface-area", "Impervious 12.3% (HR reach watershed)",
            "12.3% impervious (HR reach watershed)"),
        "catchment-hydrology-road-density": _ev(
            "catchment-hydrology-road-density", "Roads 1.23 km²/km²",
            "1.23 km² road density"),                       # non-ASCII -> print-safe
        "high-flow-dynamics-bed-mobilization-frequency": _ev(
            "high-flow-dynamics-bed-mobilization-frequency", "Bed shear τ 0.215 lb/ft²",
            "bed shear τ 0.215 lb/ft²"),
        "reach-inflow-concentrated-flow-inputs": _ev(
            "reach-inflow-concentrated-flow-inputs", status="unavailable"),
    }


def _pages(pdf_bytes):
    r = PdfReader(io.BytesIO(pdf_bytes))
    return [(pg.extract_text() or "") for pg in r.pages], r


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


# --- the field forms: the blank worksheet ------------------------------------
def test_field_forms_are_the_shipped_template_byte_for_byte():
    template = (config.DATA_DIR / "FieldForm" / report.FIELD_FORM_TEMPLATE).read_bytes()
    assert report.build_field_forms_pdf() == template
    assert report.build_field_forms_pdf(DELIN_HR, _mock_evidence()) == template  # inputs ignored
    assert template[:5] == b"%PDF-"
    assert not list((config.DATA_DIR / "FieldForm").glob("*.jpg"))
    assert not (config.DATA_DIR / "FieldForm" / "manifest.json").exists()


def test_template_is_five_letter_vector_pages_with_nothing_written_on_them():
    texts, r = _pages(report.build_field_forms_pdf())
    assert len(r.pages) == 5
    for pg in r.pages:
        w, h = float(pg.mediabox.width), float(pg.mediabox.height)
        assert (round(w), round(h)) == (round(LETTER[0]), round(LETTER[1]))
        assert (pg.get("/Rotate") or 0) == 0
        xobjects = (pg.get("/Resources") or {}).get("/XObject") or {}
        assert not any(x.get_object().get("/Subtype") == "/Image" for x in xobjects.values())
    assert all("Notes/Other Metrics" in t for t in texts)
    assert "Reach ID" in texts[0] and "Coordinates" in texts[0]
    whole = _norm(" ".join(texts))
    for absent in ("DESKTOP", "COMID", "NHDPlusID", "Page 1 of 5", "39.12345"):
        assert absent not in whole


def test_field_forms_filename_is_site_independent():
    assert report.field_forms_filename() == "sfari-field-forms.pdf"
    assert report.field_forms_filename(DELIN_HR) == "sfari-field-forms.pdf"


# --- the desktop metrics PDF --------------------------------------------------
def test_desktop_metrics_pdf_lists_all_26_once_with_statuses():
    pdf = report.build_desktop_metrics_pdf(DELIN_COMID, _mock_evidence())
    assert pdf[:5] == b"%PDF-"
    texts, _ = _pages(pdf)
    whole = _norm(" ".join(texts))
    assert "SFARI Desktop Metrics" in whole and "Watershed basis" in whole
    assert "StreamCat reach" in whole and "COMID 9311402 (this reach)" in whole
    metrics = config.desktop_metrics()
    assert len(metrics) == 26
    missing = [m["name"] for m in metrics if _norm(m["name"]) not in whole]
    assert not missing, missing
    assert "Local review required" in whole and "Run cross-section tool" in whole
    assert "Unavailable" in whole and "Available: the value was pulled" in whole
    # print-safe glyphs
    assert "km2" in whole and "tau" in whole
    for glyph in ("²", "τ", "→", "—"):
        assert glyph not in whole


def test_desktop_metrics_pdf_carries_the_engine_labels():
    ev = _mock_evidence()
    imp = "catchment-hydrology-impervious-surface-area"
    ev[imp] = dict(_ev(imp, "Impervious 12.3% (HR reach watershed)",
                       "12.3% impervious (HR reach watershed)",
                       source="STAF site engine v0.4.0 (HR reach watershed)"),
                   origin="engine", engine_version="0.4.0")
    rd = "catchment-hydrology-road-density"
    ev[rd] = dict(_ev(rd, status="pending", source="STAF site engine v0.4.0"), origin="engine")
    wet = "surface-water-storage-wetland-coverage"
    ev[wet] = dict(_ev(wet, "Wetland 4.1%", "4.1% wetland (NHDPlus V2 basin, COMID 5214461)",
                       source="StreamCat lookup engine, nearest StreamCat reach Big Run (COMID 5214461)"),
                   origin="streamcat",
                   anchor_label="nearest StreamCat reach Big Run (COMID 5214461), 1,240 ft "
                                "downstream, which drains 1.8 times this stream",
                   fallback_reason="STAF site engine refused: over budget.")
    texts, _ = _pages(report.build_desktop_metrics_pdf(DELIN_HR, ev))
    whole = _norm(" ".join(texts))
    assert "12.3% impervious (HR reach watershed)" in whole
    assert "Pending: STAF site engine running" in whole
    assert "STAF site engine v0.4.0" in whole
    assert "Describes: nearest StreamCat reach Big Run (COMID 5214461)" in whole
    assert "Fallback: STAF site engine refused" in whole
    assert "HR reach watershed (STAF site engine v0.4.0)" in whole
    assert "Big Run (COMID 5214461), 1,240 ft downstream" in whole


def test_desktop_metrics_filename():
    assert report.desktop_metrics_filename(DELIN_COMID) == "sfari-desktop-metrics-comid-9311402.pdf"
    assert report.desktop_metrics_filename(DELIN_HR) == "sfari-desktop-metrics-nhdplusid-750012345.pdf"
    fn = report.desktop_metrics_filename({"delineation": dict(DELIN_COMID["delineation"], comid=None)})
    assert fn == "sfari-desktop-metrics-n39.12345-w84.51234.pdf"
    assert report.desktop_metrics_filename({}) == "sfari-desktop-metrics.pdf"
