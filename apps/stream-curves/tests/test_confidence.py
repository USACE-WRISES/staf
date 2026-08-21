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


def test_missing_out_of_sample_evaluation_caps():
    res = conf.curve_confidence(_clean_evidence(loo={"evaluable": False}))
    assert "no_out_of_sample_validation" in res["caps_applied"]
    assert res["total"] <= 59


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
    # 16 functional sites < the auto floor (20): the trigger fires for both.
    assert by_metric["full"]["ref02_metric_trigger"]
    assert by_metric["sparse"]["n_functional_pool"] == 5
    assert by_metric["sparse"]["ref02_metric_trigger"]


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
    a = ra.run_seed("58", ["s2", "s1"], "0.3-provisional")
    b = ra.run_seed("58", ["s1", "s2"], "0.3-provisional")
    c = ra.run_seed("55", ["s1", "s2"], "0.3-provisional")
    assert a == b
    assert a != c
