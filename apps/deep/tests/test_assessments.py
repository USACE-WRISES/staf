"""The 8 state-SQT assessments are RETAINED in the baked data but HIDDEN from DEEP's
registry surfaces (map coverage, picker, load_predefined) by ``deep.config._is_hidden``.

They must (a) not surface in any registry-derived list, yet (b) still load and score from
their retained baked bundles — they are hidden, not deleted."""
from deep import assessments, config, curves
from deep.models import MeasuredValue

SQT_IDS = {"ak-sqt-adapted", "co-sqt-adapted", "mi-sqt-adapted", "mn-sqt-adapted",
           "nc-sqt-adapted", "sc-sqt-adapted", "wi-sqt-adapted", "wy-sqt-adapted"}


def _baked_by_id() -> dict[str, dict]:
    """Raw baked records keyed by id (unfiltered). The SQT bundles are retained here even
    though the registry hides them from DEEP's surfaces."""
    return {a["assessmentId"]: a for a in config.assessments_doc()["assessments"]}


def _midpoint_measure(la) -> dict[str, MeasuredValue]:
    """Measure every metric at the midpoint of its curve's x-domain."""
    measured: dict[str, MeasuredValue] = {}
    for m in la.all_metrics():
        xs = [p["x"] for p in m["curve"]["points"]]
        measured[m["metricId"]] = MeasuredValue(m["metricId"], value=(min(xs) + max(xs)) / 2)
    return measured


def test_sqt_assessments_are_hidden_from_the_registry():
    # None of the SQTs surface in the picker/coverage catalog or resolve through the registry.
    listed = {c["assessmentId"] for c in assessments.list_predefined()}
    assert not (SQT_IDS & listed)
    assert not (SQT_IDS & set(config.assessments_by_id()))
    # ...but the reference assessment still does.
    assert "northeastern-highlands" in listed


def test_sqt_bundles_are_retained_in_baked_data():
    # Hidden, not deleted: every SQT bundle is still present in the baked file.
    assert SQT_IDS <= set(_baked_by_id())


def test_every_retained_sqt_loads_with_valid_curves():
    baked = _baked_by_id()
    for aid in SQT_IDS:
        la = assessments.from_bundle(baked[aid])
        assert la.function_ids, f"{aid} has no functions"
        for m in la.all_metrics():
            pts = m["curve"]["points"]
            assert pts, f"{aid}/{m['metricId']} has no curve points"
            assert all(0.0 <= p["y"] <= 1.0 for p in pts)


def test_score_site_end_to_end_full_coverage():
    la = assessments.from_bundle(_baked_by_id()["ak-sqt-adapted"])
    result, fresults = curves.score_site(la, _midpoint_measure(la))
    assert 0.0 <= result["ecosystemConditionIndex"] <= 1.0
    for key in config.OUTCOMES:
        assert 0.0 <= result["subIndices"][key] <= 1.0
    # We measured every metric, so every function the assessment covers is scored (none NA).
    # Coverage is the assessment's own function count, not a fixed 20.
    assert all(not fr.na for fr in fresults.values())
    covered = len([fn for fn in la.metrics_by_function if fn.get("metrics")])
    assert covered >= 1
    assert len(result["functionScores"]) == covered


def test_scored_functions_match_assessment_coverage():
    # MN covers fewer than 20 functions; only covered functions appear in the rollup.
    la = assessments.from_bundle(_baked_by_id()["mn-sqt-adapted"])
    result, _ = curves.score_site(la, _midpoint_measure(la))
    covered = {fn["functionId"] for fn in la.metrics_by_function if fn.get("metrics")}
    assert set(result["functionScores"]) == covered
    assert len(covered) < 20


def test_upload_bundle_round_trip_matches_registry():
    # A registry (predefined) assessment IS a valid upload bundle; loading either way scores
    # identically. Uses the visible reference assessment, since SQTs no longer resolve through
    # load_predefined.
    la_registry = assessments.load_predefined("northeastern-highlands")
    la_bundle = assessments.from_bundle(_baked_by_id()["northeastern-highlands"])
    measured = _midpoint_measure(la_registry)
    r_registry, _ = curves.score_site(la_registry, measured)
    r_bundle, _ = curves.score_site(la_bundle, measured)
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
