"""End-to-end: load the 8 predefined state-SQT assessments and score sites."""
from deep import assessments, config, curves
from deep.models import MeasuredValue

PREDEFINED = {"ak-sqt-adapted", "co-sqt-adapted", "mi-sqt-adapted", "mn-sqt-adapted",
              "nc-sqt-adapted", "sc-sqt-adapted", "wi-sqt-adapted", "wy-sqt-adapted"}


def _midpoint_measure(la) -> dict[str, MeasuredValue]:
    """Measure every metric at the midpoint of its curve's x-domain."""
    measured: dict[str, MeasuredValue] = {}
    for m in la.all_metrics():
        xs = [p["x"] for p in m["curve"]["points"]]
        measured[m["metricId"]] = MeasuredValue(m["metricId"], value=(min(xs) + max(xs)) / 2)
    return measured


def test_registry_lists_eight():
    cat = assessments.list_predefined()
    assert {c["assessmentId"] for c in cat} == PREDEFINED


def test_every_predefined_loads_with_valid_curves():
    for aid in PREDEFINED:
        la = assessments.load_predefined(aid)
        assert la.function_ids, f"{aid} has no functions"
        for m in la.all_metrics():
            pts = m["curve"]["points"]
            assert pts, f"{aid}/{m['metricId']} has no curve points"
            assert all(0.0 <= p["y"] <= 1.0 for p in pts)


def test_score_site_end_to_end_full_coverage():
    la = assessments.load_predefined("ak-sqt-adapted")  # covers all 20 functions
    result, fresults = curves.score_site(la, _midpoint_measure(la))
    assert 0.0 <= result["ecosystemConditionIndex"] <= 1.0
    for key in config.OUTCOMES:
        assert 0.0 <= result["subIndices"][key] <= 1.0
    # We measured every metric, so every listed function is scored (none NA).
    assert all(not fr.na for fr in fresults.values())
    assert len(result["functionScores"]) == 20


def test_scored_functions_match_assessment_coverage():
    # MN covers fewer than 20 functions; only covered functions appear in the rollup.
    la = assessments.load_predefined("mn-sqt-adapted")
    result, _ = curves.score_site(la, _midpoint_measure(la))
    covered = {fn["functionId"] for fn in la.metrics_by_function if fn.get("metrics")}
    assert set(result["functionScores"]) == covered
    assert len(covered) < 20


def test_upload_bundle_round_trip_matches_predefined():
    # A predefined assessment IS a valid upload bundle; loading either way scores identically.
    raw = config.assessments_by_id()["ak-sqt-adapted"]
    la_bundle = assessments.from_bundle(raw)
    la_registry = assessments.load_predefined("ak-sqt-adapted")
    measured = _midpoint_measure(la_registry)
    r_bundle, _ = curves.score_site(la_bundle, measured)
    r_registry, _ = curves.score_site(la_registry, measured)
    assert r_bundle["ecosystemConditionIndex"] == r_registry["ecosystemConditionIndex"]
    assert r_bundle["subIndices"] == r_registry["subIndices"]


def test_bundle_validation_rejects_bad_curve():
    bad = {
        "assessmentId": "bad",
        "metricsByFunction": [
            {"functionId": "catchment-hydrology",
             "metrics": [{"metricId": "x", "curve": {"points": [{"x": 0, "y": 5}]}}]},
        ],
    }
    problems = assessments.validate_bundle(bad)
    assert any("out of [0,1]" in p for p in problems)
