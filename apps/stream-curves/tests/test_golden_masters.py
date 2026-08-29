"""Golden masters for the hard-coded algorithm internals (methodology 0.9).

The curve seed geometry, the confidence formulas, and the stratification
screening cuts are deliberately code, not config (the owner's capture decision,
2026-08-27): turning thirty seed coordinates into YAML would invite unreviewed
tuning. The trade is that any change to those internals must be LOUD, which is
this file's job: fixed synthetic inputs pinned to exact outputs. If one of
these fails, the engine changed; that requires a methodology version bump and a
rule-catalog note (CURVE-09), never a silent commit.

Values were generated from the engine itself on 2026-08-27; every operation on
the path is deterministic, so exact equality is intended.
"""
from __future__ import annotations

import pandas as pd

from streamcurves import confidence as conf
from streamcurves import curves, effects, screening


def _frame(vals):
    return pd.DataFrame({"site_id": [f"s{i}" for i in range(len(vals))], "m": vals})


def _points(res):
    pts = res["curve_row"]["curve_points"].iloc[0]
    if not isinstance(pts, pd.DataFrame):
        return []
    return [[float(x), float(y)] for x, y in
            zip(pts["metric_value"], pts["index_score"])]


def _status(res):
    return str(res["curve_row"]["curve_status"].iloc[0])


# --------------------------------------------------------------------------- #
# Curve seed geometry (CURVE-09): one case per structural branch of the seed.
# --------------------------------------------------------------------------- #
def test_rising_non_negative_seed_geometry():
    res = curves.build_reference_curve(
        _frame([float(v) for v in range(1, 21)]), "m",
        {"m": {"column_name": "m", "higher_is_better": True}}, build_plots=False)
    assert _status(res) == "complete"
    assert _points(res) == [[0.0, 0.0], [2.4642857142857144, 0.3],
                            [5.75, 0.7], [15.25, 1.0], [18.1, 1.0]]


def test_rising_signed_scale_seed_geometry():
    res = curves.build_reference_curve(
        _frame([-8.0, -6.0, -4.0, -2.0, -1.0, 0.0, 1.0, 2.0, 4.0, 6.0]), "m",
        {"m": {"column_name": "m", "higher_is_better": True, "signed_scale": True}},
        build_plots=False)
    assert _status(res) == "complete"
    assert _points(res) == [[-15.75, 0.0], [-10.5, 0.3], [-3.5, 0.7],
                            [1.75, 1.0], [3.325, 1.0]]


def test_falling_seed_geometry():
    res = curves.build_reference_curve(
        _frame([float(v) for v in range(1, 21)]), "m",
        {"m": {"column_name": "m", "higher_is_better": False}}, build_plots=False)
    assert _status(res) == "complete"
    assert _points(res) == [[2.9, 1.0], [5.75, 1.0], [15.25, 0.7],
                            [27.916666666666664, 0.3], [37.41666666666667, 0.0]]


def test_optimum_two_sided_seed_geometry():
    res = curves.build_reference_curve(
        _frame([2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]), "m",
        {"m": {"column_name": "m", "curve_form": "optimum"}}, build_plots=False)
    assert _status(res) == "complete"
    assert _points(res) == [[0.0, 0.0], [0.0, 0.3], [2.675, 0.7], [4.25, 1.0],
                            [8.75, 1.0], [10.325, 0.7], [13.25, 0.3], [17.75, 0.0]]


def test_optimum_flat_low_tail_seed_geometry():
    res = curves.build_reference_curve(
        _frame([2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]), "m",
        {"m": {"column_name": "m", "curve_form": "optimum", "low_tail": "flat"}},
        build_plots=False)
    assert _status(res) == "complete"
    assert _points(res) == [[0.0, 0.7], [2.675, 0.7], [4.25, 1.0], [8.75, 1.0],
                            [10.325, 0.7], [13.25, 0.3], [17.75, 0.0]]


def test_degenerate_q25_three_point_fallback():
    res = curves.build_reference_curve(
        _frame([0.0] * 6 + [1.0, 2.0, 3.0, 4.0]), "m",
        {"m": {"column_name": "m", "higher_is_better": True}}, build_plots=False)
    assert _status(res) == "degenerate_q25"
    assert _points(res) == [[0.0, 0.0], [0.0, 0.7], [1.75, 1.0]]


def test_below_the_hard_floor_builds_no_curve():
    res = curves.build_reference_curve(
        _frame([1.0, 2.0, 3.0, 4.0]), "m",
        {"m": {"column_name": "m", "higher_is_better": True}}, build_plots=False)
    assert _status(res) == "insufficient_data"
    assert _points(res) == []
    assert curves.CURVE_ENGINE_HARD_FLOOR_N == 5


# --------------------------------------------------------------------------- #
# Confidence assembly (CONF-01): the evidence-to-fraction formulas are code.
# --------------------------------------------------------------------------- #
def _evidence(**over):
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


def test_clean_evidence_assembles_exactly():
    r = conf.curve_confidence(_evidence())
    assert r["total"] == 99.0 and r["label"] == "High" and r["caps_applied"] == []
    assert r["components"] == {
        "data_adequacy_quality": 20.0, "statistical_strength": 25.0,
        "resampling_stability": 19.0, "ecological_plausibility": 20.0,
        "interpretability_feasibility": 10.0, "rule_completeness_agreement": 5.0}


def test_influence_flag_halves_the_stability_fraction():
    r = conf.curve_confidence(_evidence(
        influence={"evaluable": True, "flagged": True}))
    assert r["total"] == 89.5
    assert r["components"]["resampling_stability"] == 9.5


def test_missing_bootstrap_costs_stability_and_completeness():
    r = conf.curve_confidence(_evidence(bootstrap={"evaluable": False}))
    assert r["total"] == 81.5
    assert r["components"]["resampling_stability"] == 4.0
    assert r["components"]["rule_completeness_agreement"] == 2.5


def test_moderate_direction_costs_exactly_six_plausibility_points():
    r = conf.curve_confidence(_evidence(direction_confidence="moderate"))
    assert r["total"] == 93.0
    assert r["components"]["ecological_plausibility"] == 14.0


# --------------------------------------------------------------------------- #
# Stratification screening (STRAT-00 phase 1): cuts and labels are code.
# --------------------------------------------------------------------------- #
_STRAT_DATA = pd.DataFrame({
    "site_id": [f"s{i}" for i in range(20)],
    "m": [1.0, 2.0, 1.5, 2.5, 1.2, 2.2, 1.8, 2.8, 1.4, 2.4,
          7.0, 8.0, 7.5, 8.5, 7.2, 8.2, 7.8, 8.8, 7.4, 8.4],
    "G": ["a"] * 10 + ["b"] * 10,
})
_MC = {"m": {"column_name": "m", "metric_family": "continuous",
             "higher_is_better": True, "allowed_stratifications": ["G"]}}
_SC = {"G": {"column_name": "G", "display_name": "G", "strat_type": "categorical",
             "levels": ["a", "b"], "pairwise_comparisons": [["a", "b"]],
             "min_group_size": 5}}


def test_screening_classification_assembles_exactly():
    row = screening.screen_stratification(
        _STRAT_DATA, "m", "G", _MC, _SC)["result_row"].iloc[0]
    assert row["classification"] == "selected"
    assert row["test"] == "kruskal_wallis"
    assert float(row["statistic"]) == 14.285714285714278
    assert float(row["p_value"]) == 0.0001570522842307523
    assert int(row["n_groups"]) == 2 and int(row["min_group_n"]) == 10
    assert screening.SCREENING_ALPHA == 0.05


def test_effect_size_label_assembles_exactly():
    r0 = effects.compute_effect_sizes(_STRAT_DATA, "m", ["G"], _MC, _SC).iloc[0]
    assert float(r0["epsilon_squared"]) == 0.7518796992481199
    assert r0["effect_size_label"] == "large"
