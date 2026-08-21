"""The 2026-08-21 display additions: the reference tier on the assessment card
and detail pane, the builder annotations in the metric tip, and the
thin-sample advisory beside the score (review findings ECO-5, ECO-10, ECO-14,
STAT-6, and the tier-display claim the report makes)."""
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


def test_metric_tip_carries_the_builder_annotations():
    m = {"metricName": "Sinuosity", "metricId": "spring-phab-sinu",
         "howToMeasure": "Channel length over valley length.",
         "referenceTier": "best_available", "metricRole": "response",
         "referenceN": 9, "sampleDisposition": "insufficient", "confidenceLabel": "Low",
         "curveCaveats": ["Built from 9 reference sites: read the condition band."]}
    tip = app._metric_tip_html(m)
    assert "Best available (fallback)" in tip
    assert "Site-scale response measurement" in tip
    assert "Reference sites: 9 (insufficient)" in tip
    assert "Builder confidence: Low" in tip and "not a probability" in tip
    assert "Read with care" in tip and "read the condition band" in tip
    land = app._metric_tip_html({"metricName": "Crops", "metricRole": "stressor_surrogate"})
    assert "stressor surrogate" in land
    plain = app._metric_tip_html({"metricName": "Bare", "howToMeasure": ""})
    assert "Curve basis" not in plain and "Read with care" not in plain
