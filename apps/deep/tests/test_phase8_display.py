"""The 2026-08-21 display additions: the reference tier on the assessment card
and detail pane, the builder annotations (in the report since 2026-10-04), and
the thin-sample advisory (review findings ECO-5, ECO-10, ECO-14, STAT-6, and
the tier-display claim the report makes)."""
from __future__ import annotations

import app
from deep import curves
from deep.curves import metric_warning, sample_advisory
from deep.models import MeasuredValue

_PTS = [{"x": 0.0, "y": 0.0}, {"x": 5.0, "y": 0.7}, {"x": 10.0, "y": 1.0}]


def test_tier_label_covers_the_ladder_and_tolerates_absence():
    assert app._tier_label("least_disturbed") == "Least disturbed"
    assert app._tier_label("best_available") == "Best available (fallback)"
    assert app._tier_label("minimally_disturbed") == "Minimally disturbed"
    assert app._tier_label(None) == "" and app._tier_label("") == ""
    assert app._tier_label("some_future_tier") == "some future tier"


def test_sample_advisory_only_for_thin_curves():
    assert sample_advisory({"sampleDisposition": "adequate", "referenceN": 33}) is None
    assert sample_advisory({"sampleDisposition": "exploratory", "referenceN": 16}) is None
    msg = sample_advisory({"sampleDisposition": "insufficient", "referenceN": 9})
    assert "9 reference sites" in msg and "condition band" in msg
    assert sample_advisory({}) is None


def test_metric_warning_composes_domain_and_sample_advisories():
    spec = {"curve": {"points": _PTS}, "sampleDisposition": "insufficient", "referenceN": 9}
    inside = metric_warning(MeasuredValue("m", value=4.0), spec)
    assert "reference sites" in inside and "curve domain" not in inside
    outside = metric_warning(MeasuredValue("m", value=12.0), spec)
    assert "above the curve domain" in outside and "reference sites" in outside
    assert metric_warning(MeasuredValue("m", value=4.0), {"curve": {"points": _PTS}}) is None
    # The advisory never changes the index.
    assert curves.metric_index(MeasuredValue("m", value=4.0), spec) == curves.metric_index(
        MeasuredValue("m", value=4.0), {"curve": {"points": _PTS}})


def test_metric_tip_is_how_to_measure_and_the_annotations_go_to_the_report():
    """2026-10-04: the worksheet's (i) says how to measure the metric and nothing
    else; the builder's annotations ride in the report (the CSV's Uncertainty and
    Read with care columns)."""
    m = {"metricName": "Sinuosity", "metricId": "spring-phab-sinu",
         "howToMeasure": "Channel length over valley length.",
         "referenceTier": "best_available", "metricRole": "response",
         "referenceN": 9, "sampleDisposition": "insufficient", "confidenceLabel": "Low",
         "curveCaveats": ["Built from 9 reference sites: read the condition band."]}
    tip = app._metric_tip_html(m)
    assert "How to measure" in tip and "Channel length over valley length." in tip
    for gone in ("Best available", "Site-scale response", "Reference sites", "Builder confidence",
                 "Curve basis", "Read with care", "read the condition band"):
        assert gone not in tip, gone
    plain = app._metric_tip_html({"metricName": "Bare", "howToMeasure": ""})
    assert "has not been provided" in plain
