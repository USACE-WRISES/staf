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


# --------------------------------------------------------------------------- #
# DATA-03 in the interactive classification (campaign Round 1, item 7)
# --------------------------------------------------------------------------- #
def test_data_flags_word_the_rule_as_the_headless_path_does():
    ok, reason = ca.data_flags({"missing_fraction": 0.55, "disposition": "review"})
    assert not ok
    assert reason == ("Missing-data fraction 55% exceeds the DATA-03 review threshold, "
                      "so the curve must not be auto-recommended.")
    assert ca.data_flags({"missing_fraction": 0.2, "disposition": "caution"}) == (True, None)
    assert ca.data_flags(None) == (True, None), "unknown is not a flag"
    assert ca.data_flags({"disposition": "review"}) == (False, None)


def test_the_reducer_flags_data_review_exactly_as_the_headless_agent():
    from streamcurves import regional_agent as ra
    row = {"stratum": "all", "curve_status": "complete", "n_reference": 12, "q25": 1.0}
    miss = {"m": {"missing_fraction": 0.55, "disposition": "review"}}
    headless = ra.review_curves({"m": dict(row)}, {"m": "Catchment hydrology"},
                                missingness=miss)
    data_ok, data_reason = ca.data_flags(miss["m"])
    in_app = ca.reconcile_review_map({}, {"m": {
        "curve_rows": [dict(row)], "mapping_ok": True, "strat_ok": True,
        "data_ok": data_ok, "data_reason": data_reason}})
    assert in_app["m"]["status"] == headless["m"]["status"] == rs.CURVE_STATUS_DATA_REVIEW
    assert in_app["m"]["reasons"] == headless["m"]["reasons"]
    assert in_app["m"]["decision"] == rs.DECISION_PENDING
    # without the flag the same proposal is clean, which is what the app did before
    clean = ca.reconcile_review_map({}, {"m": {"curve_rows": [dict(row)], "mapping_ok": True}})
    assert clean["m"]["status"] == rs.CURVE_STATUS_AUTO_OK


def test_session_missingness_is_the_agents_function_over_the_session_frame():
    from streamcurves import regional_agent as ra

    class _State:
        def __init__(self, frame):
            self._frame = frame

        def data(self):
            return self._frame

    frame = pd.DataFrame({"m": [1.0, None, None, None], "k": [1.0, 2.0, 3.0, 4.0]})
    got = ca.session_missingness(_State(frame), ["m", "k", "absent"])
    assert got == ra.metric_missingness(frame, ["m", "k", "absent"])
    assert got["m"]["disposition"] == "review" and got["k"]["disposition"] == "auto"
    assert got["absent"]["missing_fraction"] == 1.0
    assert ca.session_missingness(_State(None), ["m"]) == {}
    assert ca.session_missingness(_State(frame.iloc[0:0]), ["m"]) == {}


def test_reducer_carries_the_stratum_floor_reason():
    """A proposal whose stratification fails the DATA-07/08 floors surfaces the
    specific floor reason in the review entry, not the generic one."""
    rows = [
        {"curve_status": "complete", "stratum": "A", "n_reference": 20},
        {"curve_status": "complete", "stratum": "B", "n_reference": 6},
    ]
    ok, reason = rs.strata_floor_check(rows)
    assert not ok
    proposals = {"m": {"curve_rows": rows, "mapping_ok": True,
                       "strat_ok": ok, "strat_reason": reason}}
    review = ca.reconcile_review_map({}, proposals)
    assert review["m"]["status"] == rs.CURVE_STATUS_STRAT_REVIEW
    assert "DATA-08" in review["m"]["reasons"][0]
