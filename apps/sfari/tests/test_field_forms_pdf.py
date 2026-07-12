"""Structural tests for the combined SFARI Field Forms PDF (Part B / Part E).

Uses pypdf (dev-only) to assert the packet's structure without a network pull:
%PDF signature, US Letter media boxes with zero rotation, five worksheet pages
before the appendix, continuous "Page X of Y", Page-1 site identity (COMID +
snapped coords, including the coordinate-only fallback), and that the layout
manifest describes exactly five 1700x2200 pages mapping all 20 functions and 26
desktop metrics once. Evidence is mocked (offline).
"""
import re

import pytest

pypdf = pytest.importorskip("pypdf")
from pypdf import PdfReader  # noqa: E402

from sfari import config, report  # noqa: E402

LETTER = (612.0, 792.0)


# --- mocked assessment state (JSON-native; no network) ------------------------
DELIN_COMID = {"delineation": {
    "comid": "9311402", "gnis_name": "Wildcat Creek", "huc8": "05120202",
    "snapped_lat": 39.12345, "snapped_lon": -84.51234,
    "drainage_area_sqkm": 42.7, "watershed_area_sqkm": 55.1, "reach_length_ft": 200,
}}
# Coordinate-only fallback: COMID absent, so the Reach ID becomes snapped lat/lon.
DELIN_COORD = {"delineation": dict(DELIN_COMID["delineation"], comid=None,
                                   gnis_name="(unnamed reach)")}


def _ev(mid, fvt="", vt="", status="ok", source="USGS NWIS 03259000",
        url="https://waterdata.usgs.gov/"):
    return {"metric_id": mid, "value_text": vt, "field_value_text": fvt,
            "suggested_likert": None, "confidence": "M", "source": source,
            "source_url": url, "status": status, "note": ""}


def _mock_evidence():
    """Successful evidence spread across all five pages, one unavailable entry, and a
    non-ASCII field value to exercise print-safe normalization."""
    ev = {
        "catchment-hydrology-impervious-surface-area": _ev(
            "catchment-hydrology-impervious-surface-area", "Impervious 12.3%",
            "12.3% impervious (watershed)"),
        "catchment-hydrology-road-density": _ev(
            "catchment-hydrology-road-density", "Roads 1.23 km²/km²",
            "1.23 km² road density"),                       # non-ASCII -> print-safe
        "surface-water-storage-wetland-coverage": _ev(
            "surface-water-storage-wetland-coverage", "Wetland 4.1%", "4.1% wetland"),
        "streamflow-regime-flow-permanence": _ev(
            "streamflow-regime-flow-permanence", "Zero-flow 1.2%", "1.2% zero-flow days"),
        "low-flow-baseflow-dynamics-low-flow-velocity": _ev(
            "low-flow-baseflow-dynamics-low-flow-velocity", "Low-flow velocity 1.10 ft/s",
            "modeled velocity 1.10 ft/s", source="Native cross-section hydraulics (Manning)"),
        "high-flow-dynamics-bed-mobilization-frequency": _ev(
            "high-flow-dynamics-bed-mobilization-frequency", "Bed shear τ 0.215 lb/ft²",
            "bed shear τ 0.215 lb/ft²"),               # tau + km2 glyphs
        "sediment-continuity-transport-capacity": _ev(
            "sediment-continuity-transport-capacity", "Slope 0.0031 m/m", "channel slope 0.0031 m/m"),
        "light-thermal-regime-riparian-canopy-cover": _ev(
            "light-thermal-regime-riparian-canopy-cover", "Riparian forest 62%", "62% riparian forest"),
        "community-dynamics-riparian-communities": _ev(
            "community-dynamics-riparian-communities", "Riparian forest 62%", "62% riparian forest"),
        "watershed-connectivity-upstream-and-downstream-barriers": _ev(
            "watershed-connectivity-upstream-and-downstream-barriers", "Barriers 2 dam(s)",
            "2 dam/barrier(s) within ~1 mi"),
        # adapter ran, produced nothing usable -> stays off the form, status in appendix
        "reach-inflow-concentrated-flow-inputs": _ev(
            "reach-inflow-concentrated-flow-inputs", status="unavailable"),
    }
    return ev


def _page_texts(pdf_bytes):
    r = PdfReader(__import__("io").BytesIO(pdf_bytes))
    return [(pg.extract_text() or "") for pg in r.pages], r


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


# --- PDF signature + geometry -------------------------------------------------
def test_pdf_signature():
    pdf = report.build_field_forms_pdf(DELIN_COMID, _mock_evidence())
    assert pdf[:5] == b"%PDF-"


def test_all_pages_us_letter_zero_rotation():
    pdf = report.build_field_forms_pdf(DELIN_COMID, _mock_evidence())
    _texts, r = _page_texts(pdf)
    assert len(r.pages) >= 6                              # 5 forms + >=1 appendix
    for pg in r.pages:
        w, h = float(pg.mediabox.width), float(pg.mediabox.height)
        assert (round(w), round(h)) == (round(LETTER[0]), round(LETTER[1]))
        assert (pg.get("/Rotate") or 0) == 0


def test_five_form_pages_before_appendix():
    pdf = report.build_field_forms_pdf(DELIN_COMID, _mock_evidence())
    texts, _ = _page_texts(pdf)
    appendix_idx = next(i for i, t in enumerate(texts) if "Desktop Metrics Summary" in t)
    assert appendix_idx >= 5                              # at least five pages precede it
    # No worksheet/appendix cross-contamination: the summary heading is on one page.
    assert sum("Desktop Metrics Summary" in t for t in texts) == 1


def test_continuous_page_x_of_y():
    pdf = report.build_field_forms_pdf(DELIN_COMID, _mock_evidence())
    texts, r = _page_texts(pdf)
    total = len(r.pages)
    for i, t in enumerate(texts, start=1):
        assert f"Page {i} of {total}" in _norm(t), f"page {i} numbering"


# --- Page-1 site identity -----------------------------------------------------
def test_page1_metadata_comid_and_coords():
    pdf = report.build_field_forms_pdf(DELIN_COMID, _mock_evidence())
    texts, _ = _page_texts(pdf)
    p1 = _norm(texts[0])
    assert "COMID 9311402" in p1
    assert "39.12345, -84.51234" in p1                   # snapped coordinates
    assert "200 ft" in p1                                # reach length


def test_coordinate_only_fallback():
    pdf = report.build_field_forms_pdf(DELIN_COORD, _mock_evidence())
    texts, _ = _page_texts(pdf)
    p1 = _norm(texts[0])
    assert "39.12345, -84.51234" in p1                   # coords stand in for Reach ID
    assert "COMID" not in p1


def test_filename_comid_and_fallback():
    assert report.field_forms_filename(DELIN_COMID) == "sfari-field-forms-comid-9311402.pdf"
    fn = report.field_forms_filename(DELIN_COORD)
    assert fn.startswith("sfari-field-forms-") and fn.endswith(".pdf")
    assert "comid" not in fn and "n39.12345" in fn and "w84.51234" in fn


# --- overlays + appendix ------------------------------------------------------
def test_form_overlays_present_no_scores():
    pdf = report.build_field_forms_pdf(DELIN_COMID, _mock_evidence())
    texts, _ = _page_texts(pdf)
    forms = " ".join(texts[:5])
    assert "DESKTOP:" in forms and "NOTES:" in forms
    assert "Impervious 12.3%" in _norm(forms)
    # assessor content never enters the packet overlays
    for banned in ("Likert", "Strongly Agree", "Function score"):
        assert banned not in forms


def test_print_safe_glyphs_in_overlay_and_appendix():
    pdf = report.build_field_forms_pdf(DELIN_COMID, _mock_evidence())
    texts, _ = _page_texts(pdf)
    whole = _norm(" ".join(texts))
    assert "km2" in whole and "tau" in whole             # km2/tau normalized
    for glyph in ("²", "τ", "→", "—"):
        assert glyph not in whole                        # no raw non-ASCII glyphs


def test_all_26_metrics_once_in_appendix():
    pdf = report.build_field_forms_pdf(DELIN_COMID, _mock_evidence())
    texts, _ = _page_texts(pdf)
    appendix = _norm(" ".join(t for t in texts if "Desktop" in t or "Discipline" in t))
    metrics = config.desktop_metrics()
    assert len(metrics) == 26
    found = [m for m in metrics if _norm(m["name"]) in appendix]
    assert len(found) == 26, f"missing from appendix: {[m['name'] for m in metrics if m not in found]}"


def test_empty_evidence_builds():
    pdf = report.build_field_forms_pdf(DELIN_COMID, {})
    assert pdf[:5] == b"%PDF-"
    texts, r = _page_texts(pdf)
    assert len(r.pages) >= 6
    assert "COMID 9311402" in _norm(texts[0])            # identity still prefilled


def test_desktop_metrics_pdf_wrapper_still_builds():
    pdf = report.build_desktop_metrics_pdf(DELIN_COMID, _mock_evidence())
    assert pdf[:5] == b"%PDF-"
    texts, _ = _page_texts(pdf)
    assert "SFARI Desktop Metrics" in _norm(" ".join(texts))


# --- layout manifest ----------------------------------------------------------
def _manifest():
    return report._load_field_form_manifest()


def test_manifest_five_pages_dimensions():
    m = _manifest()
    assert m["page_px"] == [1700, 2200] and m["dpi"] == 200
    assert len(m["pages"]) == 5
    disciplines = [p["discipline"] for p in m["pages"]]
    assert disciplines == list(config.CATEGORY_ORDER)
    for p in m["pages"]:
        assert (p["width"], p["height"], p["dpi"]) == (1700, 2200, 200)
        assert len(p["sha256"]) == 64


def test_manifest_maps_all_20_functions_once():
    m = _manifest()
    fids = [nr["functionId"] for p in m["pages"] for nr in p["notes_rows"]]
    assert len(fids) == 20 and len(set(fids)) == 20
    assert set(fids) == {f["id"] for f in config.functions()}


def test_manifest_maps_all_26_desktop_metrics_once():
    m = _manifest()
    mids = [d["metricId"] for d in m["desktop_metrics"]]
    assert len(mids) == 26 and len(set(mids)) == 26
    assert set(mids) == {mm["metricId"] for mm in config.desktop_metrics()}


def test_manifest_rects_within_raster():
    m = _manifest()
    W, H = m["page_px"]
    rects = [nr["rect"] for p in m["pages"] for nr in p["notes_rows"]]
    rects += list(m["metadata_rects_px"].values()) + [m["footer_mask_px"]]
    for x, y, w, h in rects:
        assert 0 <= x and 0 <= y and x + w <= W and y + h <= H


def test_manifest_checksums_match_assets():
    import hashlib
    m = _manifest()
    for p in m["pages"]:
        raw = (config.DATA_DIR / "FieldForm" / p["filename"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == p["sha256"], p["filename"]
