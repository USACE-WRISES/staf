"""Compare frequency-weighted calculations with independent row expansion."""
import time

import numpy as np
import pandas as pd
import pytest

from builder.analysis import stats
from builder.analysis.alternatives import bootstrap_metrics as weighted


def assert_same(actual, expected):
    if expected is None:
        assert actual is None
    else:
        assert actual == pytest.approx(expected, rel=1e-13, abs=1e-14)


@pytest.mark.parametrize("multiplicities", [[1, 1, 1, 1, 1, 1], [0, 3, 1, 2, 5, 0], [0] * 6, [0, 0, 0, 4, 0, 0]])
def test_ties_missing_values_and_unequal_repetition(multiplicities):
    x = np.array([0., 0., 2., 3., np.nan, np.inf])
    y = np.array([7., 4., 4., 4., 9., np.nan])
    positive = np.array([False, True, False, True, True, False])
    a = np.array(["Poor", "Fair", "Good", "Good", None, "Unknown"], object)
    b = np.array(["Poor", "Good", "Poor", "Good", "Good", np.nan], object)
    ids = np.repeat(np.arange(len(x)), multiplicities)
    auc = weighted.prepare_auc(x, positive)
    rho = weighted.prepare_spearman(x, y)
    kappa = weighted.prepare_kappa(a, b)
    assert_same(auc.evaluate(multiplicities), stats.auc(x[ids], positive[ids]))
    assert_same(rho.evaluate(multiplicities), stats.spearman(x[ids], y[ids]))
    assert_same(kappa.evaluate(multiplicities), stats.weighted_kappa(a[ids], b[ids]))
    finite = np.isfinite(x[ids])
    assert auc.support(multiplicities) == {"n": int(finite.sum()), "n_positive": int(positive[ids][finite].sum()),
                                            "n_negative": int((~positive[ids][finite]).sum())}
    assert rho.support(multiplicities)["n"] == int((np.isfinite(x[ids]) & np.isfinite(y[ids])).sum())
    assert kappa.support(multiplicities)["n"] == sum(aa in stats.ORDER and bb in stats.ORDER for aa, bb in zip(a[ids], b[ids]))


def test_empty_constant_single_class_and_minimum_pair_semantics():
    for prepared in [weighted.prepare_auc([], []), weighted.prepare_spearman([], []), weighted.prepare_kappa([], [])]:
        assert prepared.evaluate([]) is None and prepared.support([])["n"] == 0
    assert weighted.prepare_auc([1, 1], [False, True]).evaluate([5, 3]) == .5
    assert weighted.prepare_auc([1, 2], [True, True]).evaluate([5, 3]) is None
    assert weighted.prepare_spearman([1, 1], [1, 2]).evaluate([5, 3]) is None
    assert weighted.prepare_spearman([1, 2], [1, 2]).evaluate([1, 1]) is None
    assert weighted.prepare_spearman([1, 2], [1, 2]).evaluate([1, 2]) == 1.
    assert weighted.prepare_kappa(["Good"], ["Good"]).evaluate([20]) is None
    assert weighted.prepare_kappa(["Good"], ["Poor"]).evaluate([1]) is None
    assert weighted.prepare_kappa(["Good"], ["Poor"]).evaluate([2]) == 0.
    assert weighted.prepare_kappa([None, np.nan, pd.NA, "Unknown"], ["Good"] * 4).evaluate([2] * 4) is None


@pytest.mark.parametrize("kind", ["linear", "quadratic"])
def test_kappa_respects_order_and_disagreement_weights(kind):
    a = np.array(["Poor", "Poor", "Fair", "Good", "Good"], object)
    b = np.array(["Good", "Fair", "Poor", "Good", "Poor"], object)
    weights = np.array([7, 2, 1, 4, 3])
    ids = np.repeat(np.arange(5), weights)
    for levels in [stats.ORDER, ("Fair", "Poor", "Good")]:
        assert_same(weighted.prepare_kappa(a, b, levels=levels, weights=kind).evaluate(weights),
                    stats.weighted_kappa(a[ids], b[ids], levels=levels, weights=kind))


@pytest.mark.parametrize("bad", [[-1, 2], [.5, 2], [np.nan, 1], [np.inf, 0], [1], [[1, 2]], ["1", "2"], [2**53, 0]])
def test_invalid_multiplicities_are_rejected(bad):
    for prepared in [weighted.prepare_auc([1, 2], [False, True]), weighted.prepare_spearman([1, 2], [2, 1]),
                     weighted.prepare_kappa(["Poor", "Good"], ["Good", "Poor"])]:
        with pytest.raises(ValueError):
            prepared.evaluate(bad)


def _inputs(n, clusters, seed=123):
    rng = np.random.default_rng(seed)
    huc = np.arange(n) % clusters
    rng.shuffle(huc)
    ref = np.round(rng.normal(size=n), 1)
    alt = np.round(ref + rng.normal(scale=.3, size=n), 1)
    target = np.round(ref * .25 + rng.normal(size=n), 1)
    positive = target > .2
    ratings = np.asarray(stats.ORDER, object)
    ref_class = ratings[np.digitize(ref, [-.5, .5])]
    alt_class = ratings[np.digitize(alt, [-.5, .5])]
    target_class = ratings[np.digitize(target, [-.5, .5])]
    return huc, ref, alt, target, positive, ref_class, alt_class, target_class


def _prepare(data):
    _, ref, alt, target, positive, rc, ac, tc = data
    return [weighted.prepare_auc(ref, positive), weighted.prepare_auc(alt, positive),
            weighted.prepare_spearman(ref, target), weighted.prepare_spearman(alt, target),
            weighted.prepare_kappa(rc, tc), weighted.prepare_kappa(ac, tc)]


def _expanded(data, ids):
    _, ref, alt, target, positive, rc, ac, tc = data
    return [stats.auc(ref[ids], positive[ids]), stats.auc(alt[ids], positive[ids]),
            stats.spearman(ref[ids], target[ids]), stats.spearman(alt[ids], target[ids]),
            stats.weighted_kappa(rc[ids], tc[ids]), stats.weighted_kappa(ac[ids], tc[ids])]


def test_all_1000_paired_cluster_draws_match_including_valid_counts_and_intervals():
    data = _inputs(36, 8)
    huc = data[0]
    members = [np.flatnonzero(huc == k) for k in range(8)]
    prepared = _prepare(data)
    rng = np.random.default_rng(7)
    actual, expected = [], []
    for _ in range(1000):
        selected = rng.integers(0, 8, size=8)
        ids = np.concatenate([members[k] for k in selected])
        counts = np.bincount(selected, minlength=8)[huc]
        assert counts.sum() == len(ids)
        a, e = [p.evaluate(counts) for p in prepared], _expanded(data, ids)
        for one, other in zip(a, e):
            assert_same(one, other)
        actual.append(a); expected.append(e)
    actual, expected = np.asarray(actual, float), np.asarray(expected, float)
    assert np.array_equal(np.isfinite(actual), np.isfinite(expected))
    for offset in [0, 2, 4]:
        actual_delta = actual[:, offset+1] - actual[:, offset]
        expected_delta = expected[:, offset+1] - expected[:, offset]
        assert np.isfinite(actual_delta).sum() == np.isfinite(expected_delta).sum()
        assert stats.interval(actual_delta) == pytest.approx(stats.interval(expected_delta), abs=1e-14)


def test_cluster_draws_losing_a_class_or_all_variation_preserve_invalid_draws():
    huc = np.repeat(np.arange(3), 3)
    ref, alt = np.repeat([0., 0., 1.], 3), np.repeat([1., 0., 1.], 3)
    target = np.repeat([0., 1., 2.], 3)
    labels = np.asarray(stats.ORDER, object)
    data = (huc, ref, alt, target, target == 2., labels[ref.astype(int) * 2],
            labels[alt.astype(int) * 2], labels[target.astype(int)])
    prepared = _prepare(data)
    members = [np.flatnonzero(huc == k) for k in range(3)]
    rng = np.random.default_rng(7)
    actual, expected = [], []
    for _ in range(1000):
        selected = rng.integers(0, 3, size=3)
        counts = np.bincount(selected, minlength=3)[huc]
        ids = np.concatenate([members[k] for k in selected])
        a, e = [p.evaluate(counts) for p in prepared], _expanded(data, ids)
        for one, other in zip(a, e):
            assert_same(one, other)
        actual.append(a); expected.append(e)
    actual, expected = np.asarray(actual, float), np.asarray(expected, float)
    assert np.isnan(actual).any() and np.isfinite(actual).any()
    assert np.array_equal(np.isfinite(actual), np.isfinite(expected))
    for offset in [0, 2, 4]:
        a, e = actual[:, offset+1] - actual[:, offset], expected[:, offset+1] - expected[:, offset]
        assert np.isfinite(a).sum() == np.isfinite(e).sum()
        assert stats.interval(a) == pytest.approx(stats.interval(e), abs=1e-14)


def benchmark(n=3000, clusters=800, draws=1000):
    """Explicit opt-in benchmark, never collected as a pytest test."""
    data = _inputs(n, clusters)
    huc = data[0]
    rng = np.random.default_rng(7)
    selected_draws = rng.integers(0, clusters, size=(draws, clusters))
    start = time.perf_counter()
    members = [np.flatnonzero(huc == k) for k in range(clusters)]
    expanded = [_expanded(data, np.concatenate([members[k] for k in selected])) for selected in selected_draws]
    expanded_seconds = time.perf_counter() - start
    start = time.perf_counter()
    prepared = _prepare(data)
    prepare_seconds = time.perf_counter() - start
    start = time.perf_counter()
    frequency = []
    for selected in selected_draws:
        counts = np.bincount(selected, minlength=clusters)[huc]
        frequency.append([p.evaluate(counts) for p in prepared])
    weighted_seconds = time.perf_counter() - start
    expanded, frequency = np.asarray(expanded, float), np.asarray(frequency, float)
    if not np.array_equal(np.isfinite(expanded), np.isfinite(frequency)):
        raise AssertionError("Benchmark valid-draw counts differ")
    np.testing.assert_allclose(frequency, expanded, rtol=1e-13, atol=1e-14, equal_nan=True)
    return {"n": n, "clusters": clusters, "draws": draws, "statistics_per_draw": 6, "seed": 7,
            "expanded_seconds": expanded_seconds, "weighted_prepare_seconds": prepare_seconds,
            "weighted_evaluation_seconds": weighted_seconds,
            "observed_ratio": expanded_seconds / (weighted_seconds + prepare_seconds),
            "max_absolute_difference": float(np.nanmax(np.abs(frequency - expanded))),
            "valid_per_statistic": np.isfinite(frequency).sum(axis=0).tolist(),
            "scope": "synthetic tied inputs, paired AUC/Spearman/linear kappa; no scientific data or end-to-end extrapolation"}
