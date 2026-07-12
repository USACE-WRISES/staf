"""Curve-automation scoring reducer (pure) + review lifecycle."""
from __future__ import annotations

import pandas as pd

from streamcurves import curve_automation as ca
from streamcurves import run_state as rs


def _complete_rows():
    return pd.DataFrame([{"stratum": "all", "curve_status": "complete",
                          "n_reference": 12, "q25": 1.0}])


def _insufficient_rows():
    return pd.DataFrame([{"stratum": "all", "curve_status": "insufficient_data",
                          "n_reference": 3}])


def test_reducer_splits_auto_and_flagged():
    proposals = {
        "mGood": {"curve_rows": _complete_rows(), "mapping_ok": True, "strat_ok": True},
        "mBad": {"curve_rows": _insufficient_rows(), "mapping_ok": True, "strat_ok": True},
        "mUnmapped": {"curve_rows": _complete_rows(), "mapping_ok": False, "strat_ok": True},
    }
    review = ca.reconcile_review_map({}, proposals)
    assert review["mGood"]["status"] == rs.CURVE_STATUS_AUTO_OK
    assert review["mGood"]["decision"] == rs.DECISION_AUTO
    assert review["mBad"]["status"] == rs.CURVE_STATUS_INSUFFICIENT
    assert review["mBad"]["decision"] == rs.DECISION_PENDING
    assert review["mUnmapped"]["status"] == rs.CURVE_STATUS_UNMAPPED

    assert rs.intended_metrics_for_publish(review) == ["mGood"]
    assert rs.flagged_metrics(review) == ["mBad", "mUnmapped"]


def test_reducer_preserves_finalized_on_noop():
    proposals = {"m": {"curve_rows": _insufficient_rows(), "mapping_ok": True}}
    review = ca.reconcile_review_map({}, proposals)
    # reviewer accepts it
    review["m"] = rs.apply_review_decision(review["m"], rs.DECISION_FINALIZED, note="ok")
    # a no-op recompute (identical rows) must keep the decision
    review2 = ca.reconcile_review_map(review, proposals)
    assert review2["m"]["decision"] == rs.DECISION_FINALIZED
    assert rs.is_in_scope(review2["m"])


def test_reducer_forces_rereview_on_tweak():
    proposals = {"m": {"curve_rows": _insufficient_rows(), "mapping_ok": True}}
    review = ca.reconcile_review_map({}, proposals)
    review["m"] = rs.apply_review_decision(review["m"], rs.DECISION_FINALIZED, note="ok")
    # a tweak that is still flagged but different content -> re-review, not auto
    tweaked = {"m": {"curve_rows": pd.DataFrame([
        {"stratum": "all", "curve_status": "degenerate_curve", "n_reference": 6}]),
        "mapping_ok": True}}
    review2 = ca.reconcile_review_map(review, tweaked)
    assert review2["m"]["decision"] == rs.DECISION_PENDING
    assert len(review2["m"]["history"]) == 1
    assert review2["m"]["history"][0]["decision"] == rs.DECISION_FINALIZED


def test_tweak_to_clean_auto_finalizes_and_archives():
    proposals = {"m": {"curve_rows": _insufficient_rows(), "mapping_ok": True}}
    review = ca.reconcile_review_map({}, proposals)
    review["m"] = rs.apply_review_decision(review["m"], rs.DECISION_FINALIZED, note="ok")
    # tweak makes it a clean curve -> auto_finalized, prior decision archived
    review2 = ca.reconcile_review_map(review, {"m": {"curve_rows": _complete_rows(),
                                                     "mapping_ok": True}})
    assert review2["m"]["decision"] == rs.DECISION_AUTO
    assert len(review2["m"]["history"]) == 1


def test_reducer_records_build_error():
    proposals = {"m": {"curve_rows": None, "exc": ValueError("kaboom")}}
    review = ca.reconcile_review_map({}, proposals)
    assert review["m"]["status"] == rs.CURVE_STATUS_ERROR
    assert "kaboom" in review["m"]["reasons"][0]


def test_reducer_strat_review():
    proposals = {"m": {"curve_rows": _complete_rows(), "mapping_ok": True, "strat_ok": False}}
    review = ca.reconcile_review_map({}, proposals)
    assert review["m"]["status"] == rs.CURVE_STATUS_STRAT_REVIEW
    assert rs.needs_review(review["m"])
