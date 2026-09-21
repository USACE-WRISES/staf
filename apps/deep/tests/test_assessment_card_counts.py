"""The Basin card counts metrics, not (function, metric) pairs (2026-09-20).

The card summed the metric blocks of every function, so a metric serving three functions
was counted three times and the Northeastern Highlands assessment announced "36 metrics"
for the 29 it scores. The number a reader acts on is how many measurements the assessment
asks for, which is the distinct count. The pair count is a real number too, and the
technical report calls it metric entries, but it does not belong on a card labeled metrics.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as deep_app  # noqa: E402
from deep import assessments  # noqa: E402

CROSS_LISTED = {
    "metricsByFunction": [
        {"functionId": "a", "functionName": "A", "metrics": [
            {"metricId": "spring-shared", "metricName": "Shared"},
            {"metricId": "spring-only-a", "metricName": "Only A"}]},
        {"functionId": "b", "functionName": "B", "metrics": [
            {"metricId": "spring-shared", "metricName": "Shared"}]},
        {"functionId": "c", "functionName": "C", "metrics": [
            {"metricId": "spring-shared", "metricName": "Shared"}]},
    ],
    "functionCoverage": {"total": 20, "covered": 3, "excluded": 0, "exclusions": []},
    "assessmentId": "x", "assessmentName": "X",
}


def _facts(raw):
    return deep_app._assessment_facts(assessments.LoadedAssessment.from_dict(raw), "")


def test_a_metric_on_three_functions_counts_once():
    """Four blocks over three functions, two metrics."""
    assert _facts(CROSS_LISTED)["counts"].startswith("2 metrics")


def test_the_published_regions_announce_what_they_score():
    """The three methodology 0.13 versions (2026-09-21): 30, 30 and 25 metrics.
    Total nitrogen joined all three, and the Eastern Corn Belt Plains gained two
    modeled curves and two published nutrient benchmarks."""
    for aid, expected in (("northeastern-highlands", "30 metrics"),
                          ("interior-plateau", "30 metrics"),
                          ("eastern-corn-belt-plains", "25 metrics")):
        counts = _facts(assessments.load_predefined(aid).raw)["counts"]
        assert counts.startswith(expected), (aid, counts)


def test_a_documented_gap_is_still_named():
    """The coverage note is untouched by the counting change."""
    counts = _facts(assessments.load_predefined("eastern-corn-belt-plains").raw)["counts"]
    assert "19 of 20 functions" in counts and "(1 documented)" in counts


# --------------------------------------------------------------------------- #
# Every function is retained (2026-09-20)
# --------------------------------------------------------------------------- #
def test_the_unassessed_functions_are_named_not_merely_absent():
    """One Eastern Corn Belt Plains function has no scoring block. Methodology
    0.13 restored the other three (2026-09-21), and Population support has no
    admissible basis among the sources evaluated and carries a signed exception.
    An unassessed function used to be absent from the walk and from the bundle's
    function list alike, so the only trace was one line in the rail."""
    from deep import reference_support
    la = assessments.load_predefined("eastern-corn-belt-plains")
    un = reference_support.unassessed_functions(la)
    assert [u["functionName"] for u in un] == ["Population support"]
    # each says why, from the withheld records the bundle already carries
    assert all(u["metrics"] for u in un)
    assert all(m.get("statement") for u in un for m in u["metrics"])


def test_a_complete_assessment_has_nothing_unassessed():
    from deep import reference_support
    la = assessments.load_predefined("northeastern-highlands")
    assert reference_support.unassessed_functions(la) == []


def test_the_walk_covers_the_whole_framework():
    """Scoring blocks plus the unassessed ones equals the framework, so a reader
    meets twenty steps whichever assessment they open."""
    from deep import config, reference_support
    for aid in ("northeastern-highlands", "interior-plateau", "eastern-corn-belt-plains"):
        la = assessments.load_predefined(aid)
        n_blocks = len([fn for fn in la.metrics_by_function if fn.get("metrics")])
        assert n_blocks + len(reference_support.unassessed_functions(la)) == len(config.functions())



def test_a_curve_held_for_review_is_named_in_the_bundle():
    """Northeastern Highlands built curves for two benthic metrics that DATA-03
    holds for a reviewer (64 percent of the region's reference stations have no
    value). They used to vanish from the bundle without a trace (2026-09-21)."""
    from deep import reference_support
    la = assessments.load_predefined("northeastern-highlands")
    held = {w["metricKey"]: w for w in reference_support.withheld(la)
            if reference_support.is_held(w)}
    assert set(held) == {"bent_TOLRPIND", "bent_TOTLNTAX"}
    for w in held.values():
        assert w["statement"].startswith("Held for review.")
        assert "DATA-03" not in w["statement"]
    # held, not unassessed: their functions are scored by other metrics
    assert reference_support.unassessed_functions(la) == []
