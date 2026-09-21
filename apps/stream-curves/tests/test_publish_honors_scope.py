"""An interactive publish ships the curves the review kept, and no others (2026-09-20).

`completed_metrics` holds every built curve on purpose, so a reviewer can open the flagged
ones. The headless path narrows that to the in-scope metrics before it exports
(`regional_agent.assemble` builds `intended_rows` from `run_state.intended_metrics_for_publish`),
but `build_bundle_from_state` used to read `completed_metrics` straight. A curve a reviewer
had removed therefore still shipped, and the interactive provenance stamped a
`removed_from_scope` record for a metric the same bundle carried.

These tests pin the rule in the place that publishes: a metric the review judged out of
scope is absent from the bundle, a session that was never reviewed is untouched.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from shiny import reactive  # noqa: F401  (AppState needs shiny importable)

from streamcurves import run_state as rs
from views import assessment_publish as ap
from views.state import AppState

METRICS = ("chem_COND", "chem_PH", "chem_TURB")
FUNCTION = "Water & soil quality"


def _row(metric):
    return {"metric": metric, "display_name": np.nan, "higher_is_better": False,
            "curve_status": "complete", "stratum": "",
            "curve_points": pd.DataFrame({"point_order": [1, 2, 3],
                                          "metric_value": [0.0, 10.0, 50.0],
                                          "index_score": [1.0, 0.7, 0.0]})}


def _state(review: dict | None) -> AppState:
    st = AppState.fresh()
    st.completed_metrics.set({m: {"phase4_curve_rows": pd.DataFrame([_row(m)]).assign(
        curve_points=[_row(m)["curve_points"]])} for m in METRICS})
    st.discipline_function_mapping.set(pd.DataFrame(
        [(m, "Physicochemistry", FUNCTION, i) for i, m in enumerate(METRICS, 1)],
        columns=["metric_key", "discipline", "function_label", "sort_order"]))
    st.region_of_applicability.set({"kind": "ecoregion", "code": "58", "name": "Northeastern Highlands"})
    st.curve_review.set(review or {})
    return st


def _metric_ids(state) -> set[str]:
    bundle = ap.build_bundle_from_state(state)
    return {m["metricId"] for blk in bundle["metricsByFunction"] for m in blk["metrics"]}


def _review(**decisions) -> dict:
    return {m: {"status": "auto_ok", "decision": decisions.get(m, rs.DECISION_AUTO)} for m in METRICS}


def test_every_in_scope_curve_publishes():
    assert _metric_ids(_state(_review())) == {"spring-chem-cond", "spring-chem-ph", "spring-chem-turb"}


def test_a_removed_curve_does_not_publish():
    ids = _metric_ids(_state(_review(chem_PH=rs.DECISION_REMOVED)))
    assert "spring-chem-ph" not in ids and len(ids) == 2


def test_a_curve_still_awaiting_review_does_not_publish():
    """What the staged runs carry: a flagged curve sits at `pending` until someone rules."""
    ids = _metric_ids(_state(_review(chem_TURB=rs.DECISION_PENDING)))
    assert "spring-chem-turb" not in ids and len(ids) == 2


def test_a_reviewer_finalized_curve_publishes():
    assert len(_metric_ids(_state(_review(chem_PH=rs.DECISION_FINALIZED)))) == 3


def test_a_session_with_no_review_publishes_everything():
    """The Advanced path never populates curve_review, and `is_in_scope(None)` is False,
    so filtering on absence instead of judgement would empty its bundle."""
    assert len(_metric_ids(_state(None))) == 3


def test_a_metric_the_review_never_mentions_publishes():
    """Half a review (one entry) must not drop the metrics it says nothing about."""
    assert len(_metric_ids(_state({"chem_COND": {"status": "auto_ok"}}))) == 3


def test_removing_every_curve_leaves_nothing_to_build():
    with pytest.raises(ValueError, match="No finalized reference curves"):
        _metric_ids(_state(_review(**{m: rs.DECISION_REMOVED for m in METRICS})))


def test_the_filter_reads_the_same_predicate_as_the_headless_path():
    """One rule, one implementation: views must not grow a second definition of scope."""
    from pathlib import Path
    src = Path(ap.__file__).read_text(encoding="utf-8")
    assert "rs.is_in_scope(entry)" in src
    assert "state.curve_review()" in src
