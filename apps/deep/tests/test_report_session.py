"""Report exports, session round-trip, and the synthetic upload-bundle path."""
import json
import pathlib

import pytest

from deep import assessments, curves, measure, report, session
from deep.models import MeasuredValue

EXAMPLE = pathlib.Path(__file__).resolve().parents[1] / "examples" / "spring-sample.deep.json"


def _bundle():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _midpoint_state(la):
    state = {}
    for m in la.all_metrics():
        xs = [p["x"] for p in m["curve"]["points"]]
        state[m["metricId"]] = {"value": (min(xs) + max(xs)) / 2, "na": False, "note": ""}
    return state


def test_synthetic_bundle_loads_and_scores():
    la = assessments.from_bundle(_bundle())
    assert len(la.metrics_by_function) == 5          # one function per discipline
    measured = measure.measured_from_state(_midpoint_state(la))
    sc, fres = curves.score_site(la, measured)
    assert 0.0 <= sc["ecosystemConditionIndex"] <= 1.0
    assert all(not fr.na for fr in fres.values())     # every metric measured -> all scored


def test_bundle_is_valid():
    assert assessments.validate_bundle(_bundle()) == []


def test_report_csv_contains_headers_and_rows():
    la = assessments.from_bundle(_bundle())
    state = _midpoint_state(la)
    sc, _ = curves.score_site(la, measure.measured_from_state(state))
    txt = report.build_csv({}, la, state, sc)
    assert "DEEP Detailed Assessment" in txt
    assert "Ecosystem Condition Index" in txt
    assert "Percent Impervious Cover" in txt
    assert "Metric index (0-1)" in txt


def _bundle_with_provenance():
    b = _bundle()
    b["library"] = {"libraryId": b["assessmentId"], "version": 4}
    b["status"] = "certified"
    return b


def test_report_includes_version_status_region_and_digest():
    la = assessments.from_bundle(_bundle_with_provenance())
    state = _midpoint_state(la)
    sc, _ = curves.score_site(la, measure.measured_from_state(state))
    region = {"level3": {"code": "55", "name": "Eastern Corn Belt Plains"},
              "state": {"code": "OH", "abbr": "OH", "name": "Ohio"}}

    csv_txt = report.build_csv({}, la, state, sc, region=region)
    assert "Assessment version" in csv_txt
    assert "Certified" in csv_txt                      # lifecycle status, title-cased
    assert "Ohio (OH)" in csv_txt                      # state region match
    assert "55 Eastern Corn Belt Plains" in csv_txt    # Level III ecoregion match
    assert "Content digest" in csv_txt and "sha256:" in csv_txt

    delin = {"delineation": {"snapped_lat": 40.0, "snapped_lon": -83.5, "comid": 1}}
    gj = json.loads(report.build_geojson(delin, la, sc, region=region))
    ap = next(f["properties"] for f in gj["features"]
              if f["properties"].get("type") == "analysis_point")
    assert ap["assessment_version"] == 4
    assert ap["lifecycle_status"] == "certified"
    assert ap["region_state"] == "Ohio (OH)"
    assert ap["region_level3"] == "55 Eastern Corn Belt Plains"
    assert ap["content_digest"].startswith("sha256:")


def test_report_geojson_is_feature_collection():
    la = assessments.from_bundle(_bundle())
    sc, _ = curves.score_site(la, measure.measured_from_state(_midpoint_state(la)))
    delin = {"delineation": {"snapped_lat": 44.0, "snapped_lon": -123.0, "comid": 999},
             "watershed_geojson": None, "reach_geojson": None}
    gj = json.loads(report.build_geojson(delin, la, sc))
    assert gj["type"] == "FeatureCollection"
    pts = [f for f in gj["features"] if f["properties"].get("type") == "analysis_point"]
    assert len(pts) == 1
    # per-function scores are attached to the analysis point
    assert "catchment-hydrology" in pts[0]["properties"]


def test_report_pdf_when_reportlab_available():
    reportlab = pytest.importorskip("reportlab")  # noqa: F841 — skip in the stdlib-only env
    la = assessments.from_bundle(_bundle())
    state = _midpoint_state(la)
    sc, _ = curves.score_site(la, measure.measured_from_state(state))
    pdf = report.build_pdf({}, la, state, sc)
    assert isinstance(pdf, bytes) and pdf[:4] == b"%PDF"


def _png_1x1_data_uri():
    """A guaranteed-valid 1x1 PNG data-URI, built from stdlib so the gallery path is exercised
    whenever reportlab can read PNGs (and degrades gracefully to a smoke test otherwise)."""
    import base64 as b64
    import struct
    import zlib

    def _chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)   # 1x1, 8-bit truecolor RGB
    idat = zlib.compress(b"\x00\x40\x80\xc0")             # filter byte + one RGB pixel
    png = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
           + _chunk(b"IDAT", idat) + _chunk(b"IEND", b""))
    return "data:image/png;base64," + b64.b64encode(png).decode()


def test_field_forms_pdf_returns_pdf_bytes():
    pytest.importorskip("reportlab")
    la = assessments.from_bundle(_bundle())
    pdf = report.build_field_forms_pdf(la, ref="spring-sample@v1")
    assert isinstance(pdf, bytes) and pdf[:4] == b"%PDF"
    assert len(pdf) > 1000                                # real content, not an empty doc
    # tolerates a missing assessment (cover page only, no crash)
    assert report.build_field_forms_pdf(None)[:4] == b"%PDF"
    # filename derives from the assessment id
    fn = report.field_forms_filename(la)
    assert fn.startswith("deep-field-forms-") and fn.endswith(".pdf")


def test_pdf_includes_photo_gallery():
    pytest.importorskip("reportlab")
    la = assessments.from_bundle(_bundle())
    state = _midpoint_state(la)
    mid = la.all_metrics()[0]["metricId"]
    state[mid]["photos"] = [{"id": "p1", "uri": _png_1x1_data_uri()}]
    sc, _ = curves.score_site(la, measure.measured_from_state(state))
    pdf = report.build_pdf({}, la, state, sc)               # gallery loop must not crash
    assert isinstance(pdf, bytes) and pdf[:4] == b"%PDF"


def test_session_round_trip():
    la = assessments.from_bundle(_bundle())
    delin = {"delineation": {"comid": 123, "gnis_name": "Test Creek",
                             "snapped_lat": 44.0, "snapped_lon": -123.0}}
    mv = {"spring-catchment-hydrology-percent-impervious-cover":
          {"value": 12.0, "na": False, "note": "n"}}
    text = session.dump(delin, la.raw, mv)
    st = session.load(text)
    # session stores the full delineation result, which nests a "delineation" sub-dict
    assert st["delineation"]["delineation"]["comid"] == 123
    assert st["measured_values"]["spring-catchment-hydrology-percent-impervious-cover"]["value"] == 12.0
    la2 = assessments.LoadedAssessment.from_dict(st["assessment"])
    assert la2.assessment_id == la.assessment_id
    # resumed assessment scores identically
    measured = measure.measured_from_state(_midpoint_state(la))
    assert (curves.score_site(la, measured)[0]["ecosystemConditionIndex"]
            == curves.score_site(la2, measured)[0]["ecosystemConditionIndex"])
