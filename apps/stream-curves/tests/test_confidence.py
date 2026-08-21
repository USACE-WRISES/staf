"""Tests for streamcurves.confidence (CONF-01/02, Review Priority, SELECT-02)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import confidence as conf
from streamcurves import methodology
from streamcurves import regional_agent as ra


def _clean_evidence(**over):
    ev = {
        "sample_disposition": "adequate",
        "missingness_disposition": "auto",
        "curve_status": "auto_ok",
        "loo": {"evaluable": True, "held_out_mean_abs_delta": 0.01},
        "bootstrap": {"evaluable": True, "structure_stability": 0.95,
                      "shape_stability": 0.95},
        "influence": {"evaluable": True, "flagged": False},
        "direction_confidence": "high",
        "shape_ok": True,
        "mapped": True,
        "units_present": True,
        "reference_tier": "least_disturbed",
        "redundant_pairs": 0,
    }
    ev.update(over)
    return ev


def test_clean_adequate_curve_scores_high_with_no_caps():
    res = conf.curve_confidence(_clean_evidence())
    assert res["caps_applied"] == []
    assert res["label"] == "High"
    assert res["total"] >= 80
    weights = methodology.threshold("confidence_rules.components")
    assert set(res["components"]) == set(weights)


def test_best_available_reference_caps_at_59():
    res = conf.curve_confidence(_clean_evidence(reference_tier="best_available"))
    assert "best_available_reference" in res["caps_applied"]
    assert res["total"] <= 59
    assert res["label"] == "Low"


def test_exploratory_sample_caps_at_59():
    res = conf.curve_confidence(_clean_evidence(sample_disposition="exploratory"))
    assert "sample_below_minimum" in res["caps_applied"]
    assert res["total"] <= 59


def test_missing_stability_evidence_caps():
    res = conf.curve_confidence(_clean_evidence(loo={"evaluable": False}))
    assert "no_stability_evidence" in res["caps_applied"]
    assert res["total"] <= 59


def test_no_component_or_cap_claims_out_of_sample_evidence():
    """STAT-3 (2026-08-21): the heuristic reads within-pool diagnostics only,
    so nothing in its vocabulary may say out-of-sample or validation."""
    res = conf.curve_confidence(_clean_evidence(loo={"evaluable": False}))
    names = list(res["components"]) + list(res["caps_applied"])
    for name in names:
        assert "out_of_sample" not in name and "validation" not in name
    assert "resampling_stability" in res["components"]


def test_open_mandatory_review_caps_below_high_until_adjudicated():
    """STAT-5b: a curve under an unadjudicated mandatory-review trigger cannot
    read as streamlined. Once the adjudication is on the record the cap lifts."""
    open_ = conf.curve_confidence(_clean_evidence(mandatory_review_open=True))
    assert "mandatory_review_open" in open_["caps_applied"]
    assert open_["total"] <= 79 and open_["label"] == "Moderate"
    closed = conf.curve_confidence(_clean_evidence(mandatory_review_open=False))
    assert "mandatory_review_open" not in closed["caps_applied"]
    assert closed["label"] == "High"


def test_deferred_gradient_deducts_before_the_caps_and_is_recorded():
    """ECO-5: a known, unmodeled stratification gradient costs the configured
    points from the subtotal and the deduction is on the record."""
    base = conf.curve_confidence(_clean_evidence())
    ded = conf.curve_confidence(_clean_evidence(
        deferred_gradient={"stratification": "DrainageAreaClass",
                           "cv_error_improvement": 0.36, "resample_support": 0.96}))
    pts = float(methodology.threshold("confidence_rules.deductions")["deferred_gradient"])
    assert ded["deductions_applied"] == {"deferred_gradient": pts}
    assert ded["subtotal"] == base["subtotal"]
    assert abs((base["total"] - ded["total"]) - pts) < 1e-6
    assert ded["basis"] == "conf-01-v2"


def test_shape_conflict_zeroes_the_ecological_component():
    res = conf.curve_confidence(_clean_evidence(shape_ok=False))
    assert res["components"]["ecological_plausibility"] == 0


def test_review_priority_scales():
    top = conf.review_priority(confidence_label="Low", ref02=True,
                               novel_stratifier=True)
    assert top["priority"] == 27
    quiet = conf.review_priority(confidence_label="High")
    assert quiet["priority"] == 1


def test_metric_score_totals_are_bounded_and_ranked_sensibly():
    good = conf.metric_score(_clean_evidence())
    weights = methodology.threshold("metric_portfolio.metric_score_weights")
    assert 0 < good["total"] <= sum(weights.values())
    worse = conf.metric_score(_clean_evidence(
        sample_disposition="exploratory", direction_confidence="moderate",
        redundant_pairs=1))
    assert worse["total"] < good["total"]


# --- agent-side helpers ------------------------------------------------------ #
def test_tier_evaluation_reports_the_per_metric_trigger():
    data = pd.DataFrame({
        "site_id": [f"s{i}" for i in range(16)],
        "full": np.arange(16, dtype=float),
        "sparse": [1.0] * 5 + [np.nan] * 11,
    })
    tier = {"reference_tier": "best_available",
            "primary": {"retained_ids": [f"s{i}" for i in range(16)]}}
    mc = {"full": {"column_name": "full"}, "sparse": {"column_name": "sparse"}}
    rows = ra.tier_evaluation_table(data, mc, tier, "functional")
    by_metric = {r["metric"]: r for r in rows}
    # REF-02 tie-break (v0.6): 16 functional sites sit in the DATA-05 exploratory
    # band (10 to 19), so the trigger does NOT fire for "full"; the pool stays at
    # tier as exploratory and the note says so. The sparse metric (5 sites) is
    # below the exploratory floor, so its trigger fires.
    assert not by_metric["full"]["ref02_metric_trigger"]
    assert "exploratory" in by_metric["full"]["note"]
    assert by_metric["sparse"]["n_functional_pool"] == 5
    assert by_metric["sparse"]["ref02_metric_trigger"]
    assert "DATA-05" in by_metric["sparse"]["note"]


def test_tier_evaluation_reports_zero_when_the_functional_pool_is_empty():
    """The ECBP case: a REAL screen that retained zero Functioning sites must
    report functional counts of 0, never borrow the applied pool's numbers."""
    data = pd.DataFrame({
        "site_id": [f"s{i}" for i in range(16)],
        "m": np.arange(16, dtype=float),
    })
    tier = {"reference_tier": "best_available",
            "primary": {"retained_ids": []}}
    rows = ra.tier_evaluation_table(
        data, {"m": {"column_name": "m"}}, tier, "functional")
    assert rows[0]["n_functional_pool"] == 0
    assert rows[0]["n_applied_pool"] == 16
    assert rows[0]["ref02_metric_trigger"]


def test_stratifier_evidence_frame_shape():
    rng = np.random.default_rng(2)
    n = 24
    data = pd.DataFrame({
        "site_id": [f"s{i}" for i in range(n)],
        "m1": np.concatenate([rng.normal(10, 2, n // 2), rng.normal(50, 2, n // 2)]),
        "cls": ["A"] * (n // 2) + ["B"] * (n // 2),
    })
    mc = {"m1": {"column_name": "m1", "metric_family": "continuous"}}
    strat = {"eligible": ["ClsStrat"],
             "strat_config": {"ClsStrat": {"column_name": "cls"}}}
    ev = ra.stratifier_evidence(data, mc, strat, seed=5, n_boot=20)
    assert len(ev) == 1
    row = ev.iloc[0]
    assert row["strat01_supports"] and row["strat04_supports"]
    assert row["strat06_recurrence"] is not None


def test_run_seed_is_stable_and_order_insensitive():
    version = methodology.methodology_version()
    a = ra.run_seed("58", ["s2", "s1"], version)
    b = ra.run_seed("58", ["s1", "s2"], version)
    c = ra.run_seed("55", ["s1", "s2"], version)
    assert a == b
    assert a != c
    # The seed derives from the methodology version too, so a version bump
    # re-randomizes every diagnostic on purpose (the manifest records both).
    assert a != ra.run_seed("58", ["s1", "s2"], "0.0-test")


def test_deferred_gradient_candidates_are_mechanical():
    """ECO-5 (Phase 8 gate): every metric whose best candidate clears STRAT-01
    and STRAT-06 is a deferred gradient; none is hand-picked, none is missed."""
    floor_imp = float(methodology.threshold("stratifier_rules.min_cv_error_improvement"))
    floor_rec = float(methodology.threshold("stratifier_rules.min_resample_support"))
    ev = pd.DataFrame([
        {"metric": "canopy", "stratification": "DrainageAreaClass", "evaluable": True,
         "cv_rmse_improvement": floor_imp + 0.2, "strat06_recurrence": floor_rec + 0.1},
        {"metric": "canopy", "stratification": "ChannelSlopeClass", "evaluable": True,
         "cv_rmse_improvement": floor_imp + 0.1, "strat06_recurrence": floor_rec + 0.15},
        {"metric": "fines", "stratification": "DrainageAreaClass", "evaluable": True,
         "cv_rmse_improvement": floor_imp + 0.05, "strat06_recurrence": floor_rec - 0.2},
        {"metric": "wood", "stratification": "DrainageAreaClass", "evaluable": True,
         "cv_rmse_improvement": floor_imp - 0.05, "strat06_recurrence": None},
        {"metric": "ph", "stratification": "DrainageAreaClass", "evaluable": False,
         "cv_rmse_improvement": 0.9, "strat06_recurrence": 0.9},
    ])
    out = ra.deferred_gradient_candidates(ev)
    assert set(out) == {"canopy"}
    assert out["canopy"]["stratification"] == "DrainageAreaClass"
    assert out["canopy"]["rule_ids"] == ["STRAT-01", "STRAT-06"]
    assert ra.deferred_gradient_candidates(pd.DataFrame()) == {}


def test_mandatory_review_triggers_and_adjudication():
    """The cap's inputs: triggers derive from the run's own evidence, and a
    recorded accept/modify closes them while reject leaves them open."""
    diag = {"loo": {"evaluable": True}, "influence": {"flagged": True},
            "bootstrap": {"evaluable": True}}
    triggers = ra.mandatory_review_triggers(
        "m", review_entry={"status": "auto_ok"}, sample_disposition="adequate",
        missingness_disposition="auto", diag=diag, ref02_triggered=False)
    assert triggers == [("CURVE-04", "m")]
    closed = ra.adjudicated_keys([{"rule_id": "CURVE-04", "subject": "m", "action": "accept"}])
    assert ("CURVE-04", "m") in closed
    still_open = ra.adjudicated_keys([{"rule_id": "CURVE-04", "subject": "m", "action": "reject"}])
    assert ("CURVE-04", "m") not in still_open
    many = ra.mandatory_review_triggers(
        "m", review_entry={"status": "shape_conflict"}, sample_disposition="exploratory",
        missingness_disposition="review", diag={}, ref02_triggered=True)
    assert set(many) == {("CURVE-07", "m"), ("DATA-05", "m"), ("DATA-03", "m"),
                         ("REF-02", "reference_screen")}
