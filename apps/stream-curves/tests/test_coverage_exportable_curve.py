"""Coverage counts a curve DEEP can interpolate (methodology 0.11, SELECT-03).

Owner decision, 2026-09-08. Before this, a metric whose curve carried the
status word ``degenerate_q25`` was dropped from the bundle even though its
three-point seed has points and scores, so its function was reported as an
uncovered gap and the publish gate refused the version. The rule now reads the
curve rather than the word: points decide. A curve with no points
(``degenerate_curve``) is still dropped and still covers nothing.

The flag is not lost, which is the other half of the decision, so these tests
also pin where it goes: ``curveStatus`` on the metric entry, and a caveat in
the annotations the scorer reads beside the number.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import deep_export, regional_agent
from streamcurves.deep_export import build_deep_assessment_bundle, deep_collect_curve_rows

# Surface water storage is the function this arose on: Interior Plateau's
# wadeable pool has almost no mapped wetland, so its lower quartile is zero.
WETLAND = "pctwet2019ws"
FUNCTION = "Surface water storage"
FUNCTION_ID = "surface-water-storage"


def _row(metric, status, *, points=True, stratum=""):
    """One finalized curve row: a three-point fallback seed, or none at all."""
    cp = None
    if points:
        cp = pd.DataFrame({"point_order": [1, 2, 3],
                           "metric_value": [0.0, 0.0645, 0.1505],
                           "index_score": [0.0, 0.3, 0.7]})
    return {"metric": metric, "display_name": np.nan, "higher_is_better": True,
            "curve_status": status, "stratum": stratum, "curve_points": cp}


def _completed(status, *, points=True):
    rows = pd.DataFrame([_row(WETLAND, status, points=points)])
    rows["curve_points"] = [_row(WETLAND, status, points=points)["curve_points"]]
    return {WETLAND: {"phase4_curve_rows": rows}}


def _mapping():
    return pd.DataFrame([(WETLAND, "Hydrology", FUNCTION, 1)],
                        columns=["metric_key", "discipline", "function_label",
                                 "sort_order"])


def _bundle(completed):
    return build_deep_assessment_bundle(
        deep_collect_curve_rows(completed), _mapping(), {}, {})


def _entry(bundle):
    return bundle["metricsByFunction"][0]["metrics"][0]


def test_a_fallback_curve_exports_and_covers_its_function():
    """The whole point of the change: degenerate_q25 has points, so it scores."""
    bundle = _bundle(_completed("degenerate_q25"))
    assert FUNCTION_ID in bundle["functionCoverage"]["coveredFunctionIds"]
    entry = _entry(bundle)
    assert entry["metricId"] == "spring-pctwet2019ws"
    assert len(entry["curve"]["points"]) == 3        # the seed DEEP interpolates


def test_the_exported_entry_carries_the_flag():
    """A flagged curve says so in the bundle, so DEEP can show it."""
    assert _entry(_bundle(_completed("degenerate_q25")))["curveStatus"] == "degenerate_q25"


def test_a_complete_curve_is_stamped_with_nothing():
    """The stamp is an exception report, not a field on every metric."""
    assert "curveStatus" not in _entry(_bundle(_completed("complete")))


def test_a_curve_with_no_points_still_covers_nothing():
    """degenerate_curve is empty by construction: it cannot be interpolated, so
    it is still skipped and the bundle has nothing to build."""
    with pytest.raises(ValueError, match="no exportable, mappable curves"):
        _bundle(_completed("degenerate_curve", points=False))


def test_quick_coverage_agrees_with_the_bundle_on_both():
    """The workflow strip's cheap read and the real bundle must not disagree:
    that divergence is what made the app show 20 of 20 while publish refused."""
    mapping = _mapping()
    ok = _completed("degenerate_q25")
    quick = deep_export.function_coverage_quick(ok, mapping)
    assert quick["coveredFunctionIds"] == \
        _bundle(ok)["functionCoverage"]["coveredFunctionIds"] == [FUNCTION_ID]

    empty = _completed("degenerate_curve", points=False)
    quick_empty = deep_export.function_coverage_quick(empty, mapping)
    assert quick_empty is not None                      # candidates exist
    assert quick_empty["covered"] == 0 and quick_empty["missing"] == 20


def test_a_complete_stratum_is_still_preferred_over_a_flagged_one():
    """Nothing about the pick order changed: a metric with both keeps the
    complete curve, and then carries no flag."""
    rows = pd.DataFrame([_row(WETLAND, "degenerate_q25", stratum="plateau"),
                         _row(WETLAND, "complete", stratum="")])
    rows["curve_points"] = [_row(WETLAND, "x")["curve_points"],
                            _row(WETLAND, "x")["curve_points"]]
    entry = _entry(_bundle({WETLAND: {"phase4_curve_rows": rows}}))
    assert "curveStatus" not in entry


def _annotations(status):
    return regional_agent.metric_annotations(
        intended=[WETLAND],
        curve_rows={WETLAND: {"curve_status": status, "n_reference": 39,
                              "min_val": 0.0, "max_val": 5.93}},
        metric_config={WETLAND: {"metric_role": "response"}},
        sample_sizes={WETLAND: {"disposition": "adequate"}},
        confidence_map={}, deferred_gradients={})[WETLAND]


def test_the_scorer_is_told_why_the_curve_is_a_fallback():
    caveats = _annotations("degenerate_q25")["curveCaveats"]
    assert any("lower quartile" in c and "coarse sort" in c for c in caveats)


def test_an_unflagged_curve_gains_no_caveat():
    assert _annotations("complete")["curveCaveats"] == []


def test_any_other_flag_is_reported_by_name():
    caveats = _annotations("data_review")["curveCaveats"]
    assert any("data_review" in c for c in caveats)
