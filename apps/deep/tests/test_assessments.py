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


# --------------------------------------------------------------------------- #
# STAF function coverage
# --------------------------------------------------------------------------- #
def _bundle_with(n_functions: int, declared: dict | None = None) -> dict:
    b = {
        "assessmentId": "cov",
        "assessmentName": "Coverage",
        "metricsByFunction": [
            {"functionId": f"fn-{i}", "metrics": [{"metricId": f"m-{i}"}]}
            for i in range(n_functions)
        ],
    }
    if declared is not None:
        b["functionCoverage"] = declared
    return b


def test_coverage_of_derives_for_a_legacy_bundle():
    """Bundles published before the coverage gate carry no block; the framework
    denominator is still 20, and the gaps are undeclared rather than chosen."""
    cov = assessments.coverage_of(_bundle_with(12))
    assert cov["total"] == 20
    assert cov["covered"] == 12
    assert cov["missing"] == 8
    assert cov["excluded"] == 0
    assert cov["declared"] is False


def test_coverage_of_reads_a_declared_block():
    declared = {"framework": "staf-20", "total": 20, "covered": 12, "excluded": 8,
                "missing": 0, "exclusions": [{"functionId": "reach-inflow"}]}
    cov = assessments.coverage_of(_bundle_with(12, declared))
    assert cov["declared"] is True
    assert (cov["covered"], cov["excluded"], cov["missing"]) == (12, 8, 0)
    assert cov["exclusions"][0]["functionId"] == "reach-inflow"


def test_coverage_of_accepts_a_loaded_assessment():
    la = assessments.LoadedAssessment.from_dict(_bundle_with(12))
    assert assessments.coverage_of(la)["covered"] == 12


def test_a_function_block_with_no_metrics_is_not_covered():
    b = {"metricsByFunction": [{"functionId": "a", "metrics": []},
                               {"functionId": "b", "metrics": [{"metricId": "m"}]}]}
    assert assessments.coverage_of(b)["covered"] == 1


def test_coverage_caption_distinguishes_documented_from_undeclared():
    full = {"total": 20, "covered": 20, "excluded": 0, "missing": 0, "declared": True}
    assert assessments.coverage_caption(full) == "Assessment covers 20 of 20 STAF functions"

    documented = {"total": 20, "covered": 12, "excluded": 8, "missing": 0, "declared": True}
    assert "8 documented exclusions" in assessments.coverage_caption(documented)

    legacy = {"total": 20, "covered": 12, "excluded": 0, "missing": 8, "declared": False}
    assert "coverage not declared" in assessments.coverage_caption(legacy)


def test_validate_bundle_accepts_a_bundle_without_function_coverage():
    """Back-compat guarantee for the upload path: every published bundle predates
    the coverage block, and requiring it would reject all of them."""
    b = _bundle_with(1)
    b["metricsByFunction"] = [{
        "functionId": "catchment-hydrology",
        "functionName": "Catchment hydrology",
        "metrics": [{"metricId": "m", "metricName": "M",
                     "curve": {"points": [{"x": 0, "y": 1}, {"x": 1, "y": 0}]}}],
    }]
    assert "functionCoverage" not in b
    problems = assessments.validate_bundle(b)
    assert not [p for p in problems if "coverage" in p.lower()]
