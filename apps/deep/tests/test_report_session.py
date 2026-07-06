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
