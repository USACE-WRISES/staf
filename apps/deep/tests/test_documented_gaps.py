"""Documented gaps on the function card (campaign Round 1): a function the assessment
leaves unassessed for a recorded reason reads "Not assessed: <reason>", never a score,
and rendering changes nothing a digest covers."""
from __future__ import annotations

import copy

from deep import assessments, reference_support as rs, session

ASC = [{"x": 0, "y": 0}, {"x": 10, "y": 1}]
EXCLUSION = {"functionId": "reach-inflow", "functionName": "Reach inflow", "discipline": "Hydrology",
             "reason": "no-suitable-metric",
             "justification": "Nothing in the crosswalk measures reach inflow at NRSA stations.",
             "recordedBy": "GM", "recordedAt": "2026-09-25T10:00:00+00:00"}
BUNDLE = {"assessmentId": "t", "metricsByFunction": [
    {"functionId": "water-soil-quality", "functionName": "Water & soil quality",
     "metrics": [{"metricId": "spring-chem-cond", "metricName": "Conductivity", "criteriaBasis": "reference",
                  "referenceN": 24, "sampleDisposition": "adequate", "confidenceLabel": "Moderate",
                  "curveCaveats": ["Built from 24 reference sites."], "curve": {"points": ASC}}]}],
    "functionCoverage": {"framework": "staf-20", "total": 20, "covered": 1, "excluded": 1, "missing": 18,
                         "coveredFunctionIds": ["water-soil-quality"], "missingFunctionIds": [],
                         "exclusions": [EXCLUSION]}}


def test_the_gap_is_read_from_the_bundle_and_worded_as_not_assessed():
    la = assessments.LoadedAssessment.from_dict(BUNDLE)
    gap = assessments.documented_gap(la, "reach-inflow")
    assert gap["reason"] == "no-suitable-metric" and gap["recordedBy"] == "GM"
    assert assessments.documented_gap(la, "water-soil-quality") is None
    assert assessments.documented_gap(la, "carbon-processing") is None
    assert assessments.documented_gap({"metricsByFunction": []}, "reach-inflow") is None
    assert assessments.gap_line(gap) == "Not assessed: no suitable metric measures it here"
    assert assessments.gap_line({"reason": "consolidated-into", "consolidatedInto": "streamflow-regime"}) \
        == "Not assessed: folded into another function (streamflow-regime)"
    assert assessments.gap_line({"reason": "some-new-reason"}) == "Not assessed: some new reason"
    assert assessments.gap_line(None) == "Not assessed"


def test_every_exporter_reason_has_words():
    """The seven reasons the StreamCurves exporter allows (deep_export.FUNCTION_EXCLUSION_REASONS)
    each read as words, so no card ever shows a raw token."""
    tokens = ("not-applicable-to-region", "no-suitable-metric", "data-unavailable", "direction-unresolved",
              "consolidated-into", "deferred-to-other-tier", "insufficient-reference-support")
    assert set(assessments.GAP_REASON_WORDS) == set(tokens)
    for t in tokens:
        assert assessments.gap_words(t) and "-" not in assessments.gap_words(t)


def test_the_function_card_shows_the_reason_and_never_a_score():
    import app as deep_app
    la = assessments.LoadedAssessment.from_dict(BUNDLE)
    gaps = {f["functionId"]: f for f in rs.unassessed_functions(la)}
    fn = {"functionId": "reach-inflow", "functionName": "Reach inflow", "metrics": [],
          "unassessed": gaps["reach-inflow"]}
    html = str(deep_app._unassessed_panel(fn, la))
    assert "Not assessed: no suitable metric measures it here" in html
    assert "Nothing in the crosswalk measures reach inflow" in html
    assert "Recorded by GM on 2026-09-25" in html
    assert 'data-function-gap="no-suitable-metric"' in html
    assert "no defensible basis" not in html and "No metric is assigned" not in html
    for score_mark in ("deep-fscore-knob", "Function score", "Non-Functioning", "Functioning-at-Risk"):
        assert score_mark not in html
    assert chr(8212) not in html
    # a function with no documented reason still reads as it did
    plain = str(deep_app._unassessed_panel({"functionId": "carbon-processing", "functionName": "Carbon processing",
                                            "unassessed": {"metrics": []}}, la))
    assert "no defensible basis" in plain and "Not assessed:" not in plain
    assert "No metric is assigned" in plain


def test_rendering_changes_nothing_the_digest_covers():
    import app as deep_app
    bundle = copy.deepcopy(BUNDLE)
    before = session.content_digest(bundle)
    la = assessments.LoadedAssessment.from_dict(bundle)
    m = la.all_metrics()[0]
    gaps = {f["functionId"]: f for f in rs.unassessed_functions(la)}
    str(deep_app._unassessed_panel({"functionId": "reach-inflow", "functionName": "Reach inflow",
                                    "unassessed": gaps["reach-inflow"]}, la))
    deep_app._metric_tip_html(m)
    rs.practitioner_lines(m)
    rs.uncertainty_line(m)
    rs.limitations_line(m)
    assessments.documented_gap(la, "reach-inflow")
    assert session.content_digest(bundle) == before
    assert bundle == BUNDLE
