"""Unit tests for metric-predictor overlap analysis (rule OVL-01). Pure, no Shiny."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import overlap as ov


def _frame(**cols) -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    return pd.DataFrame({"site_id": [f"s{i}" for i in range(n)], **cols})


def _linear(n=30, slope=2.0, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    x = np.linspace(1, 50, n)
    return x, slope * x + (rng.normal(0, noise, n) if noise else 0.0)


# --- flagging --------------------------------------------------------------- #
def test_flags_a_metric_that_tracks_its_predictor():
    x, y = _linear(noise=0.5)
    rng = np.random.default_rng(7)
    df = _frame(p1=x, m_redundant=y, m_independent=rng.normal(0, 1, len(x)))
    a = ov.analyze_overlap(df, metric_columns=["m_redundant", "m_independent"],
                           partner_columns=["p1"])
    assert a["by_metric"]["m_redundant"]["status"] == ov.STATUS_OVERLAP
    assert a["by_metric"]["m_redundant"]["worst_partner"] == "p1"
    assert "m_independent" not in a["by_metric"]      # nothing to say about it


def test_threshold_is_inclusive():
    # a pair sitting exactly at the threshold must flag (>=, not >)
    x = np.arange(20, dtype=float)
    y = x.copy()
    df = _frame(p1=x, m=y)
    at = ov.analyze_overlap(df, metric_columns=["m"], partner_columns=["p1"], threshold=1.0)
    assert at["by_metric"]["m"]["status"] == ov.STATUS_OVERLAP   # rho == 1.0 == threshold


def test_negative_correlation_flags_on_absolute_value():
    x, y = _linear()
    df = _frame(p1=x, m=-y)
    a = ov.analyze_overlap(df, metric_columns=["m"], partner_columns=["p1"])
    assert a["by_metric"]["m"]["status"] == ov.STATUS_OVERLAP
    assert a["by_metric"]["m"]["worst_spearman"] < 0


# --- exclusions and guards -------------------------------------------------- #
def test_self_pair_is_excluded_but_recorded():
    x, _ = _linear()
    df = _frame(shared=x, other=x * 3)
    a = ov.analyze_overlap(df, metric_columns=["shared"], partner_columns=["shared", "other"])
    assert a["by_metric"]["shared"]["self_role"] is True
    assert not ((a["pairs"]["metric"] == "shared") & (a["pairs"]["partner"] == "shared")).any()


def test_non_numeric_all_nan_and_constant_are_skipped_with_reasons():
    n = 12
    df = _frame(p1=np.linspace(0, 1, n),
                m_text=[f"cat{i%3}" for i in range(n)],
                m_nan=[np.nan] * n,
                m_const=[5.0] * n)
    a = ov.analyze_overlap(df, metric_columns=["m_text", "m_nan", "m_const"],
                           partner_columns=["p1"])
    reasons = {s["column"]: s["reason"] for s in a["skipped"]}
    assert reasons["m_text"] == "non_numeric"
    assert reasons["m_nan"] == "non_numeric"     # coerces to all-NaN
    assert reasons["m_const"] == "constant"
    assert len(a["pairs"]) == 0


def test_absent_column_is_reported_not_raised():
    df = _frame(p1=np.linspace(0, 1, 10))
    a = ov.analyze_overlap(df, metric_columns=["nope"], partner_columns=["p1"])
    assert {"column": "nope", "reason": "absent"} in a["skipped"]


def test_binary_column_is_reported_but_never_auto_flagged():
    n = 20
    flag = np.array([0.0, 1.0] * (n // 2))
    df = _frame(p1=flag, m=flag * 10 + 1)      # perfectly correlated by construction
    a = ov.analyze_overlap(df, metric_columns=["m"], partner_columns=["p1"])
    pair = a["pairs"].iloc[0]
    assert bool(pair["low_variety"]) and not bool(pair["flagged"])
    assert a["by_metric"]["m"]["status"] == ov.STATUS_CLEAR


def test_insufficient_pairs_are_not_flagged():
    x = np.arange(5, dtype=float)
    df = _frame(p1=x, m=x * 2)                 # rho == 1.0 but only 5 pairs
    a = ov.analyze_overlap(df, metric_columns=["m"], partner_columns=["p1"], min_n=8)
    pair = a["pairs"].iloc[0]
    assert bool(pair["low_n"]) and not bool(pair["flagged"])


def test_pair_n_is_per_pair_not_a_global_dropna():
    # m2 is almost entirely missing; that must not shrink the m1/p1 sample.
    n = 20
    x = np.linspace(1, 20, n)
    m2 = np.full(n, np.nan)
    m2[:3] = [1.0, 2.0, 3.0]
    df = _frame(p1=x, m1=x * 2 + 1, m2=m2)
    a = ov.analyze_overlap(df, metric_columns=["m1", "m2"], partner_columns=["p1"],
                           report_floor=0.0)
    n_m1 = int(a["pairs"].loc[a["pairs"]["metric"] == "m1", "n"].iloc[0])
    assert n_m1 == n          # not 3


# --- ordering, empties ------------------------------------------------------ #
def test_pairs_are_ordered_by_absolute_strength():
    n = 30
    x = np.linspace(1, 30, n)
    rng = np.random.default_rng(3)
    df = _frame(p1=x, strong=-x * 2, weak=x + rng.normal(0, 12, n))
    a = ov.analyze_overlap(df, metric_columns=["strong", "weak"], partner_columns=["p1"],
                           report_floor=0.0)
    assert a["pairs"].iloc[0]["metric"] == "strong"
    assert a["pairs"]["abs_spearman"].is_monotonic_decreasing


def test_empty_inputs_are_well_formed():
    df = _frame(p1=np.linspace(0, 1, 10))
    for metrics, partners in (([], ["p1"]), (["p1"], []), ([], [])):
        a = ov.analyze_overlap(df, metric_columns=metrics, partner_columns=partners)
        assert a["by_metric"] == {} and len(a["pairs"]) == 0
    a = ov.analyze_overlap(pd.DataFrame(), metric_columns=["m"], partner_columns=["p"])
    assert a["n_rows"] == 0


def test_identity_columns_are_never_analysed():
    n = 12
    a = ov.analyze_overlap(_frame(lat=np.linspace(1, 2, n), m=np.linspace(1, 2, n)),
                           metric_columns=["m", "site_id"], partner_columns=["lat"])
    assert a["metric_columns"] == ["m"] and a["partner_columns"] == []


# --- fingerprint ------------------------------------------------------------ #
def test_fingerprint_is_stable_sensitive_and_scoped():
    x, y = _linear(noise=0.5)
    rng = np.random.default_rng(11)
    df = _frame(p1=x, a=y, b=y + rng.normal(0, 3, len(x)))
    first = ov.analyze_overlap(df, metric_columns=["a", "b"], partner_columns=["p1"])
    again = ov.analyze_overlap(df, metric_columns=["a", "b"], partner_columns=["p1"])
    assert first["by_metric"]["a"]["fingerprint"] == again["by_metric"]["a"]["fingerprint"]

    # moving metric B must NOT disturb metric A's fingerprint
    df2 = df.copy()
    df2["b"] = rng.normal(0, 1, len(x))
    moved = ov.analyze_overlap(df2, metric_columns=["a", "b"], partner_columns=["p1"])
    assert moved["by_metric"]["a"]["fingerprint"] == first["by_metric"]["a"]["fingerprint"]


def test_fingerprint_changes_when_the_threshold_moves():
    x, y = _linear(noise=0.5)
    df = _frame(p1=x, m=y)
    a1 = ov.analyze_overlap(df, metric_columns=["m"], partner_columns=["p1"], threshold=0.80)
    a2 = ov.analyze_overlap(df, metric_columns=["m"], partner_columns=["p1"], threshold=0.99)
    assert a1["by_metric"]["m"]["fingerprint"] != a2["by_metric"]["m"]["fingerprint"]


# --- role adapters ---------------------------------------------------------- #
def test_role_adapters_agree_on_equivalent_inputs():
    assignments = pd.DataFrame({
        "column": ["a", "b", "c"],
        "is_metric": [True, False, True],
        "is_predictor": [False, True, True],     # c holds both roles
    })
    m1, p1 = ov.roles_from_assignments(assignments)
    m2, p2 = ov.roles_from_configs(
        {"a": {"column_name": "a"}, "c": {"column_name": "c"}},
        {"b": {"column_name": "b"}, "c": {"column_name": "c"}},
    )
    assert sorted(m1) == sorted(m2) == ["a", "c"]
    assert sorted(p1) == sorted(p2) == ["b", "c"]


def test_roles_from_configs_filters_to_present_columns():
    m, p = ov.roles_from_configs({"a": {}, "gone": {}}, {"b": {}}, data_columns=["a", "b"])
    assert m == ["a"] and p == ["b"]


# --- legacy redundancy view ------------------------------------------------- #
def test_redundancy_view_carries_the_legacy_columns():
    x, y = _linear()
    df = _frame(m1=x, m2=y)
    a = ov.analyze_overlap(df, metric_columns=["m1", "m2"], partner_columns=["m1", "m2"],
                           partner_role=ov.PARTNER_METRIC)
    view = ov.redundancy_view(a, {"m1": "Reach inflow", "m2": "Reach inflow"})
    assert list(view.columns) == [
        "metric_a", "metric_b", "function_a", "function_b", "same_function",
        "n", "spearman", "pearson", "p_value", "fdr_q",
        "red01_spearman_flag", "code_pearson_flag", "divergence"]
    assert bool(view.iloc[0]["same_function"])
    assert bool(view.iloc[0]["red01_spearman_flag"])
    # RED-07 support rides along for every reported pair.
    assert view.iloc[0]["fdr_q"] is not None


def test_redundancy_view_of_an_empty_analysis():
    a = ov.analyze_overlap(pd.DataFrame(), metric_columns=[], partner_columns=[])
    assert len(ov.redundancy_view(a)) == 0


def test_redundancy_view_honors_the_low_n_guard():
    """A perfectly correlated pair with n below the pair floor is reported but
    must NOT carry the RED-01 flag: the guards computed by pairwise_correlations
    invalidate the coefficient however it is consumed. (Regression: the view
    used to recompute the flag from the raw spearman and drop the guards.)"""
    x, y = _linear(n=5)                          # n=5 < DEFAULT_MIN_PAIR_N=8
    df = _frame(m1=x, m2=y)
    a = ov.analyze_overlap(df, metric_columns=["m1", "m2"],
                           partner_columns=["m1", "m2"],
                           partner_role=ov.PARTNER_METRIC)
    view = ov.redundancy_view(a)
    assert len(view) == 1
    assert abs(view.iloc[0]["spearman"]) >= 0.80          # strong on its face
    assert not bool(view.iloc[0]["red01_spearman_flag"])  # but guarded off
    assert not bool(view.iloc[0]["code_pearson_flag"])


def test_redundancy_view_honors_the_binary_coarseness_guard():
    """A two-valued column correlates perfectly with anything splitting the same
    way; that is coarseness, not redundancy, so the view must not flag it."""
    x = np.arange(20, dtype=float)
    binary = (x >= 10).astype(float)
    df = _frame(m1=binary, m2=x)
    a = ov.analyze_overlap(df, metric_columns=["m1", "m2"],
                           partner_columns=["m1", "m2"],
                           partner_role=ov.PARTNER_METRIC)
    view = ov.redundancy_view(a)
    assert len(view) == 1
    assert bool(view.iloc[0]["red01_spearman_flag"]) is False
    assert bool(view.iloc[0]["code_pearson_flag"]) is False
