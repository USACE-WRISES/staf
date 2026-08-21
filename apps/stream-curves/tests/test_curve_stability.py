"""Tests for streamcurves.curve_stability (CURVE-02/04/06, RED-06, STRAT-01..06
support, DATA-09 guard). All resampling is seeded, so every assertion is
deterministic."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import curve_stability as cs

RNG = np.random.default_rng(42)
ENTRY = {"higher_is_better": True, "metric_family": "continuous"}


def _values(n=30, loc=50.0, scale=8.0, seed=1):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(loc, scale, size=n),
                     index=[f"site{i}" for i in range(n)])


# --- DATA-09 guard ----------------------------------------------------------- #
def test_one_row_per_site_passes_clean_data():
    df = pd.DataFrame({"site_id": ["a", "b", "c"], "v": [1, 2, 3]})
    cs.assert_one_row_per_site(df)  # no raise


def test_repeated_sites_are_refused():
    df = pd.DataFrame({"site_id": ["a", "a", "b"], "v": [1, 2, 3]})
    with pytest.raises(ValueError, match="DATA-09"):
        cs.assert_one_row_per_site(df)


# --- CURVE-02 leave-one-out stability ---------------------------------------- #
def test_loo_stability_on_a_well_sampled_metric_is_small():
    res = cs.loo_curve_stability(_values(40), ENTRY)
    assert res["evaluable"]
    assert res["n_folds"] == 40
    assert res["held_out_mean_abs_delta"] < 0.05
    assert res["seed_max_shift_frac"] < 0.5


def test_loo_stability_declines_tiny_samples():
    res = cs.loo_curve_stability(_values(4), ENTRY)
    assert not res["evaluable"]


# --- CURVE-04 influence ------------------------------------------------------ #
def test_influence_stable_sample_not_flagged():
    res = cs.influence_check(_values(40), ENTRY)
    assert res["evaluable"]
    assert not res["flagged"]
    assert res["max_param_change_frac"] < 0.2


def test_influence_decision_flip_is_flagged():
    # n = 5 builds, any drop-one leaves n = 4 (< engine floor) -> flip.
    v = pd.Series([10.0, 12.0, 14.0, 16.0, 18.0],
                  index=[f"s{i}" for i in range(5)])
    res = cs.influence_check(v, ENTRY)
    assert res["evaluable"]
    assert res["decision_flip"]
    assert res["flagged"]


# --- CURVE-06 bootstrap intervals -------------------------------------------- #
def test_bootstrap_curve_is_deterministic_and_covers_the_seed():
    v = _values(30)
    a = cs.bootstrap_curve(v, ENTRY, n_boot=100, seed=7)
    b = cs.bootstrap_curve(v, ENTRY, n_boot=100, seed=7)
    assert a == b
    assert a["evaluable"]
    assert 0 < a["structure_stability"] <= 1
    covered = [p for p in a["point_intervals"] if p["x_lo"] is not None]
    assert covered
    for p in covered:
        assert p["x_lo"] <= p["x"] <= p["x_hi"]


def test_bootstrap_seed_changes_the_resamples():
    v = _values(30)
    a = cs.bootstrap_curve(v, ENTRY, n_boot=100, seed=7)
    c = cs.bootstrap_curve(v, ENTRY, n_boot=100, seed=8)
    assert a != c


# --- RED-06 pair-category stability ------------------------------------------ #
def test_strong_pair_is_stable_under_the_bootstrap():
    x = _values(40, seed=3)
    y = x * 2.0 + RNG.normal(0, 0.5, size=40)
    res = cs.bootstrap_pair_category_stability(x, y, n_boot=50, seed=5)
    assert res["category"] == "strong"
    assert res["stability"] >= 0.9


def test_pair_stability_is_deterministic():
    x = _values(30, seed=4)
    y = -x + RNG.normal(0, 5.0, size=30)
    a = cs.bootstrap_pair_category_stability(x, y, n_boot=50, seed=9)
    b = cs.bootstrap_pair_category_stability(x, y, n_boot=50, seed=9)
    assert a == b


# --- STRAT-01/02/03 grouped LOO improvement ---------------------------------- #
def _strat_frame(separated: bool, n=24, seed=11):
    rng = np.random.default_rng(seed)
    half = n // 2
    a = rng.normal(10, 2, size=half)
    b = rng.normal(50 if separated else 10, 2, size=half)
    return pd.DataFrame({"v": np.concatenate([a, b]),
                         "s": ["A"] * half + ["B"] * half})


def test_separated_strata_show_a_real_improvement():
    res = cs.stratified_loo_improvement(_strat_frame(True), "v", "s")
    assert res["evaluable"]
    assert res["rmse_improvement_frac"] > 0.5
    assert res["delta_cv_r2"] > 0.5


def test_identical_strata_show_no_improvement():
    res = cs.stratified_loo_improvement(_strat_frame(False), "v", "s")
    assert res["evaluable"]
    assert res["rmse_improvement_frac"] < 0.1


# --- STRAT-04/05 AICc support ------------------------------------------------ #
def test_aicc_from_rss_known_value():
    # n=20, rss=20, k=2: 20*ln(1) + 4 + 12/17
    v = cs.aicc_from_rss(20, 20.0, 2)
    assert v == pytest.approx(4 + 12 / 17)
    assert cs.aicc_from_rss(3, 10.0, 2) is None


def test_ic_support_favors_real_strata_only():
    good = cs.stratifier_ic_support(_strat_frame(True), "v", "s")
    assert good["evaluable"] and good["supports_strong"]
    flat = cs.stratifier_ic_support(_strat_frame(False), "v", "s")
    assert flat["evaluable"] and not flat["supports_min"]


# --- STRAT-06 recurrence ------------------------------------------------------ #
def test_recurrence_of_a_real_improvement():
    res = cs.bootstrap_improvement_recurrence(
        _strat_frame(True), "v", "s", n_boot=30, seed=13)
    assert res["evaluable"]
    assert res["recurrence_above_zero"] >= 0.9


# --- iqr-seed-2: the signed-scale guard refinement --------------------------- #
def test_signed_scale_metric_builds_a_real_seed_not_the_origin_fallback():
    """LRBS-shaped values (log ratio, legitimately negative) must build the
    standard seed. The origin-anchored degenerate fallback is for scales that
    cannot go negative (iqr-seed-2, owner decision 2026-08-21)."""
    import pandas as pd
    from streamcurves import curves as c
    lrbs_like = pd.DataFrame({"m": [-3.35, -1.2, -0.6, -0.23, -0.1, 0.1, 0.5, 1.35]})
    cfg = {"m": {"column_name": "m", "higher_is_better": True,
                 "metric_family": "continuous"}}
    res = c.build_reference_curve(lrbs_like, "m", cfg, build_plots=False)
    assert str(res["curve_row"].iloc[0]["curve_status"]) == "complete"
    xs = [p["metric_value"] for p in res["curve_points"].to_dict("records")]
    assert min(xs) < 0  # the seed genuinely spans the negative range


def test_nonnegative_scale_zero_q25_still_degenerates():
    import pandas as pd
    from streamcurves import curves as c
    zeros = pd.DataFrame({"m": [0.0, 0.0, 0.0, 0.0, 2.0, 5.0, 9.0]})
    cfg = {"m": {"column_name": "m", "higher_is_better": True,
                 "metric_family": "continuous"}}
    res = c.build_reference_curve(zeros, "m", cfg, build_plots=False)
    assert str(res["curve_row"].iloc[0]["curve_status"]) == "degenerate_q25"
