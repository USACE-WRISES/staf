"""Tests for streamcurves.effects (port of R/05b_effect_size.R).

Reference numbers marked "R:" come from R 4.x: ``kruskal.test`` (epsilon^2 =
H/(n-1)), ``aov`` Sum Sq (eta^2 = SSB/SST) and ``wilcox.test(exact = FALSE)``
(rank-biserial r = 1 - 2W/(n1 n2)).

Golden parity reproduces the export_golden.R loop over every metric's
``allowed_stratifications`` and compares to ``05_effects``.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from streamcurves.effects import compute_effect_sizes

from tests.golden_io import assert_frame_matches, has_golden, load_golden_df, load_golden_json

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

COLUMNS = [
    "metric", "stratification", "epsilon_squared", "eta_squared",
    "rank_biserial_r", "effect_size_label", "variance_explained_pct",
]

_CONFIG_LIST_FIELDS = {
    "allowed_predictors", "allowed_stratifications", "pairwise_comparisons",
    "levels", "group_definitions",
}


def unbox_config(cfg: dict) -> dict:
    out = {}
    for key, entry in (cfg or {}).items():
        e = {}
        for f, v in (entry or {}).items():
            if isinstance(v, list) and len(v) == 1 and f not in _CONFIG_LIST_FIELDS:
                e[f] = v[0]
            else:
                e[f] = v
        out[key] = e
    return out


def _mc():
    return {
        "m": {
            "column_name": "y",
            "display_name": "Metric",
            "metric_family": "continuous",
            "allowed_stratifications": ["g2", "g3"],
        }
    }


def _sc():
    return {
        "g2": {"column_name": "grp2", "display_name": "Two"},
        "g3": {"column_name": "grp3", "display_name": "Three"},
    }


# --------------------------------------------------------------------------- #
# eta-squared (direct sums of squares)
# --------------------------------------------------------------------------- #


def test_eta_squared_hand_worked_example():
    # y=[1,2,3 | 7,8,9]; grand mean 5. SSB = 3*(2-5)^2 + 3*(8-5)^2 = 54.
    # SSW = 2+2 = 4 -> SST = 58. eta^2 = 54/58.
    df = pd.DataFrame({"y": [1.0, 2, 3, 7, 8, 9], "grp2": ["a", "a", "a", "b", "b", "b"]})
    out = compute_effect_sizes(df, "m", ["g2"], _mc(), _sc())
    row = out.iloc[0]

    assert list(out.columns) == COLUMNS
    assert row["eta_squared"] == pytest.approx(54.0 / 58.0, abs=1e-12)
    # R: aov Sum Sq -> SSB 54, SST 58
    assert row["variance_explained_pct"] == pytest.approx(round(54.0 / 58.0 * 100, 2))
    # epsilon^2 = H/(n-1); R kruskal H = 3.85714285714286 -> /5
    assert row["epsilon_squared"] == pytest.approx(3.85714285714286 / 5, abs=1e-10)
    # es primary = epsilon^2 = 0.7714 -> "large"
    assert row["effect_size_label"] == "large"


def test_epsilon_squared_matches_scipy_kruskal():
    y = [1.1, 2.3, 2.3, 3.1, 4.0, 4.2, 5.5, 5.5, 6.1, 7.0, 7.2, 8.3]
    g = ["a"] * 4 + ["b"] * 4 + ["c"] * 4
    df = pd.DataFrame({"y": y, "grp3": g})
    out = compute_effect_sizes(df, "m", ["g3"], _mc(), _sc())
    row = out.iloc[0]
    n = len(df)
    H = float(stats.kruskal(y[:4], y[4:8], y[8:]).statistic)
    assert row["epsilon_squared"] == pytest.approx(H / (n - 1), abs=1e-12)


# --------------------------------------------------------------------------- #
# rank-biserial r (two-group only)
# --------------------------------------------------------------------------- #


def test_rank_biserial_r_matches_r_reference_two_groups():
    # groups picked in R factor order (sorted-unique): "hi" < "lo".
    hi = [2.0, 3.5, 4.0, 4.5, 6.0, 7.0]
    lo = [1.5, 2.0, 2.0, 3.0, 4.5]
    df = pd.DataFrame({"y": hi + lo, "grp2": ["hi"] * 6 + ["lo"] * 5})
    out = compute_effect_sizes(df, "m", ["g2"], _mc(), _sc())
    row = out.iloc[0]
    # first factor level "hi" is sample 1: W = U1(hi, lo)
    W = float(stats.mannwhitneyu(hi, lo, alternative="two-sided",
                                 method="asymptotic", use_continuity=True).statistic)
    expected = 1 - (2 * W) / (6 * 5)
    assert row["rank_biserial_r"] == pytest.approx(expected, abs=1e-12)


def test_rank_biserial_is_nan_for_three_groups():
    df = pd.DataFrame(
        {"y": [1.0, 2, 3, 4, 5, 6, 7, 8, 9], "grp3": ["a", "a", "a", "b", "b", "b", "c", "c", "c"]}
    )
    out = compute_effect_sizes(df, "m", ["g3"], _mc(), _sc())
    assert np.isnan(out.iloc[0]["rank_biserial_r"])


# --------------------------------------------------------------------------- #
# Effect-size labels (epsilon^2 thresholds) and NA fallbacks
# --------------------------------------------------------------------------- #


# Frozen deterministic datasets, one per epsilon^2 band. scipy.kruskal is
# deterministic so a literal array reproduces the same band on every platform.
# (bounds: negligible <0.01, small <0.06, medium <0.14, else large)
_MEDIUM_Y = [
    1.05, 1.78, -2.55, -0.14, 1.01, 1.35, 0.65, 1.5, 0.29, 0.55, 0.18, -1.07,
    -0.85, 0.38, -0.58, 1.27, 1.29, 1.8, -0.03, 1.38, -0.91, -0.82, 0.08, 0.28,
    -1.6, -1.73, 0.36, -0.86, 1.21, 0.39, 1.16, 1.06, 1.75, 0.7, 1.94, 0.32,
    0.63, 0.17, 1.45, 0.89, -0.01, 2.85, 0.49, 0.73, 1.24, 2.6, 0.7, 1.55,
    0.95, -0.61, 1.64, 1.87, 0.61, 0.83, -0.65, -0.67, 0.46, 1.48, 0.38, 1.14,
]


def _label_of(y, g):
    df = pd.DataFrame({"y": list(y), "grp2": list(g)})
    row = compute_effect_sizes(df, "m", ["g2"], _mc(), _sc()).iloc[0]
    return float(row["epsilon_squared"]), row["effect_size_label"]


def test_label_negligible():
    # n=40 perfectly interleaved ranks -> epsilon^2 ~ 0.002
    eps, label = _label_of(range(40), ["a", "b"] * 20)
    assert eps < 0.01
    assert label == "negligible"


def test_label_small():
    # n=10 interleaved -> epsilon^2 ~ 0.030
    eps, label = _label_of(range(1, 11), ["a", "b"] * 5)
    assert 0.01 <= eps < 0.06
    assert label == "small"


def test_label_medium():
    eps, label = _label_of(_MEDIUM_Y, ["a"] * 30 + ["b"] * 30)
    assert 0.06 <= eps < 0.14
    assert label == "medium"


def test_label_large():
    # hand-worked [1,2,3 | 7,8,9] -> epsilon^2 ~ 0.77
    eps, label = _label_of([1, 2, 3, 7, 8, 9], ["a"] * 3 + ["b"] * 3)
    assert eps >= 0.14
    assert label == "large"


# --------------------------------------------------------------------------- #
# Skips / NA rows
# --------------------------------------------------------------------------- #


def test_paired_strat_is_not_applicable():
    sc = {"paired": {"type": "paired", "primary": "a", "secondary": "b"}}
    mc = {"m": {"column_name": "y", "metric_family": "continuous"}}
    df = pd.DataFrame({"y": [1.0, 2, 3]})
    out = compute_effect_sizes(df, "m", ["paired"], mc, sc)
    row = out.iloc[0]
    assert row["effect_size_label"] == "not_applicable"
    assert np.isnan(row["epsilon_squared"])
    assert np.isnan(row["eta_squared"])
    assert np.isnan(row["variance_explained_pct"])


def test_missing_column_is_not_applicable():
    df = pd.DataFrame({"y": [1.0, 2, 3]})  # no strat column
    out = compute_effect_sizes(df, "m", ["g2"], _mc(), _sc())
    assert out.iloc[0]["effect_size_label"] == "not_applicable"


def test_too_few_rows_is_not_applicable():
    df = pd.DataFrame({"y": [1.0, 2], "grp2": ["a", "b"]})  # n < 3
    out = compute_effect_sizes(df, "m", ["g2"], _mc(), _sc())
    assert out.iloc[0]["effect_size_label"] == "not_applicable"


def test_single_group_is_not_applicable():
    df = pd.DataFrame({"y": [1.0, 2, 3, 4], "grp2": ["a"] * 4})
    out = compute_effect_sizes(df, "m", ["g2"], _mc(), _sc())
    assert out.iloc[0]["effect_size_label"] == "not_applicable"


def test_empty_strat_keys_returns_empty_frame_with_columns():
    out = compute_effect_sizes(pd.DataFrame({"y": [1.0]}), "m", [], _mc(), _sc())
    assert list(out.columns) == COLUMNS
    assert len(out) == 0


def test_dropna_spans_both_columns():
    df = pd.DataFrame(
        {"y": [1.0, 2, 3, np.nan, 8, 9], "grp2": ["a", "a", "a", "b", "b", "b"]}
    )
    out = compute_effect_sizes(df, "m", ["g2"], _mc(), _sc())
    # the NA row drops -> group a has 3, group b has 2; still n=5>=3, 2 groups
    assert out.iloc[0]["effect_size_label"] != "not_applicable"


# --------------------------------------------------------------------------- #
# Golden parity
# --------------------------------------------------------------------------- #


def test_golden_effects():
    if not (has_golden("05_effects") and has_golden("02_derived") and has_golden("01_bundle_meta")):
        pytest.skip("effects golden fixtures not present (run scripts/export_golden.R)")
    meta = load_golden_json("01_bundle_meta")
    metric_config = unbox_config(meta["metric_config"])
    strat_config = unbox_config(meta["strat_config"])
    data = load_golden_df("02_derived")

    # The JSON round-trip loses R's factor dtypes; clean_data factorizes the
    # recode source columns (BEHI_NBS, StreamType, ...) — reapply so the
    # factor-vs-character response distinction matches the live pipeline.
    for rc in unbox_config(meta["factor_recode_config"]).values():
        src = rc.get("source_column")
        if src and src in data.columns:
            data[src] = pd.Categorical(data[src])

    # Reproduce the export loop: every metric's allowed_stratifications,
    # with the R script's per-metric tryCatch(error -> drop) — factor
    # metrics (BEHI_NBS) raise in both R and the port.
    frames = []
    raised = []
    for m, mc in metric_config.items():
        strats = mc.get("allowed_stratifications")
        if not strats:
            continue
        try:
            frames.append(compute_effect_sizes(data, m, strats, metric_config, strat_config))
        except (ValueError, TypeError):
            raised.append(m)
    got = pd.concat(frames, ignore_index=True)

    # The one categorical metric errors out exactly like R's aov/summary path.
    assert raised == ["BEHI_NBS"]

    golden = load_golden_df("05_effects")
    assert len(got) == len(golden)
    assert_frame_matches(got, golden, keys=["metric", "stratification"])
