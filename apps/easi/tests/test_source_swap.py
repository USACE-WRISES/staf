"""assessment.apply_source_choices — instant, synchronous source swapping.

The single-site worksheet lets the user switch a multi-source metric's data source
(wetlands, temperature, impairment) with no re-run. ``assess(prefetch=True)`` attaches
every variant to the row; ``apply_source_choices`` merges the chosen one into the
generated view, and ``rescore`` (run after) still lets an explicit override win.
"""
from __future__ import annotations

from easi import assessment, config, scoring
from easi.metrics import hydrology
from easi.metrics.base import AnalysisContext

WETLANDS_ID = hydrology.WETLANDS_ID


def _wetlands_ctx():
    """A context whose prefetched data makes StreamCat=Fair (3.0%) and NLCD=Good (8.0%)."""
    c = AnalysisContext(lat=40.0, lon=-83.0, comid=1)
    c.extras["streamcat"] = {"pctwdwet2019ws": 2.0, "pcthbwet2019ws": 1.0}
    c.extras["landcover"] = {"wetland_pct": 8.0}
    c.extras["prefetch_variants"] = True
    return c


def _row_from_result(res):
    """Serialize a wetlands MetricResult into the row shape assess() would emit."""
    meta = config.metrics_by_id()[WETLANDS_ID]
    return {
        "metricId": WETLANDS_ID, "functionId": meta["functionId"],
        "rating": res.rating, "generatedRating": res.rating,
        "index": round(scoring.rating_to_index(res.rating, meta.get("indexMidpoints")), 3),
        "functionScore": scoring.function_score(
            scoring.rating_to_index(res.rating, meta.get("indexMidpoints"))),
        "valueText": res.value_text, "source": res.source, "confidence": res.confidence,
        "note": res.note, "status": res.status, "criteria": "", "criteriaBands": {},
        "landCover": None, "ripVeg": None,
        "sourceVariants": {k: assessment._serialize_variant(WETLANDS_ID, meta, v)
                           for k, v in res.variants.items()},
        "sourceChoice": res.source_key,
    }


def _base_report():
    res = hydrology.wetlands(_wetlands_ctx())
    return {"metricRows": [_row_from_result(res)], "totalCount": 1}


def test_swap_overwrites_generated_view():
    rep = _base_report()
    assert rep["metricRows"][0]["rating"] == "Fair"          # StreamCat default
    swapped = assessment.apply_source_choices(rep, {WETLANDS_ID: "nlcd"})
    row = swapped["metricRows"][0]
    assert row["rating"] == "Good" and row["generatedRating"] == "Good"
    assert "NLCD" in row["source"] and row["sourceChoice"] == "nlcd"
    assert row["functionScore"] == scoring.function_score(
        scoring.rating_to_index("Good", None))               # score follows the swapped rating


def test_swap_is_pure_original_untouched():
    rep = _base_report()
    assessment.apply_source_choices(rep, {WETLANDS_ID: "nlcd"})
    assert rep["metricRows"][0]["rating"] == "Fair"          # input dict not mutated


def test_empty_or_unknown_choice_is_noop():
    rep = _base_report()
    assert assessment.apply_source_choices(rep, {}) is rep    # no choices -> same object
    assert (assessment.apply_source_choices(rep, {WETLANDS_ID: "zzz"})["metricRows"][0]["rating"]
            == "Fair")                                        # unknown key ignored


def test_explicit_override_wins_after_rescore():
    rep = _base_report()
    swapped = assessment.apply_source_choices(rep, {WETLANDS_ID: "nlcd"})   # -> Good
    rescored = assessment.rescore(swapped, {WETLANDS_ID: "Poor"})           # user forces Poor
    row = rescored["metricRows"][0]
    assert row["rating"] == "Poor" and row["status"] == "override"
    # a non-overridden swap still shows through when no override is present
    plain = assessment.rescore(swapped, {})
    assert plain["metricRows"][0]["rating"] == "Good"


def test_swap_carries_scoring_trace():
    # each variant carries its own Scoring-method trace, and a swap brings it along so the
    # panel's inputs/plot follow the chosen source.
    rep = _base_report()
    assert rep["metricRows"][0]["sourceVariants"]["streamcat"]["scoring"]["inputs"]["wetland"] == 3.0
    row = assessment.apply_source_choices(rep, {WETLANDS_ID: "nlcd"})["metricRows"][0]
    assert row["scoring"]["model"] == "scalar"
    assert row["scoring"]["inputs"]["wetland"] == 8.0        # NLCD variant's input


def test_swap_updates_rollup_via_rescore():
    rep = _base_report()
    lo = assessment.rescore(assessment.apply_source_choices(rep, {WETLANDS_ID: "streamcat"}), {})
    hi = assessment.rescore(assessment.apply_source_choices(rep, {WETLANDS_ID: "nlcd"}), {})
    # Good (NLCD) scores at least as high as Fair (StreamCat) on this single-metric report
    assert hi["ecosystemConditionIndex"] >= lo["ecosystemConditionIndex"]
