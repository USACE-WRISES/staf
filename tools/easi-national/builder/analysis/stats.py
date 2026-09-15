"""Small statistics the analysis uses everywhere, in numpy only: the area
under the ROC curve (the Mann-Whitney statistic), Cliff's delta, Spearman's
rank correlation, the linear-weighted kappa, and cluster bootstraps."""
from __future__ import annotations

from typing import Optional

import numpy as np


def _ranks(values: np.ndarray) -> np.ndarray:
    """Average ranks (1-based) of a finite array."""
    order = values.argsort(kind="stable")
    sorted_vals = values[order]
    lo = np.searchsorted(sorted_vals, sorted_vals, side="left") + 1
    hi = np.searchsorted(sorted_vals, sorted_vals, side="right")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = (lo + hi) / 2.0
    return ranks


def auc(values, positive) -> Optional[float]:
    """Probability that a random positive scores above a random negative
    (ties count half); None with fewer than one case per group."""
    x = np.asarray(values, dtype=float)
    pos = np.asarray(positive, dtype=bool)
    ok = np.isfinite(x)
    x, pos = x[ok], pos[ok]
    n1, n0 = int(pos.sum()), int((~pos).sum())
    if n1 == 0 or n0 == 0:
        return None
    ranks = _ranks(x)
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def cliffs_delta(values, positive) -> Optional[float]:
    """2 x AUC - 1: the probability a positive exceeds a negative minus the reverse."""
    a = auc(values, positive)
    return None if a is None else 2.0 * a - 1.0


def spearman(x, y) -> Optional[float]:
    """Spearman's rho over the pairs where both are finite (None under 3 pairs)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3:
        return None
    rx, ry = _ranks(x[ok]), _ranks(y[ok])
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    denominator = np.sqrt((rx * rx).sum() * (ry * ry).sum())
    return float((rx * ry).sum() / denominator) if denominator > 0 else None


ORDER = ("Poor", "Fair", "Good")


def weighted_kappa(a, b, *, levels=ORDER, weights: str = "linear") -> Optional[float]:
    """Cohen's kappa with linear (or quadratic) disagreement weights over two
    ordinal class sequences (None where either is missing is dropped)."""
    index = {level: i for i, level in enumerate(levels)}
    pairs = [(index[x], index[y]) for x, y in zip(a, b) if x in index and y in index]
    if len(pairs) < 2:
        return None
    k = len(levels)
    observed = np.zeros((k, k))
    for i, j in pairs:
        observed[i, j] += 1
    n = observed.sum()
    expected = np.outer(observed.sum(axis=1), observed.sum(axis=0)) / n
    i, j = np.indices((k, k))
    w = np.abs(i - j) / (k - 1) if weights == "linear" else ((i - j) / (k - 1)) ** 2
    denominator = (w * expected).sum()
    if denominator == 0:
        return None
    return float(1.0 - (w * observed).sum() / denominator)


def cluster_bootstrap(clusters, statistic, *, n_boot: int = 200, seed: int = 7):
    """Resample whole clusters (an array of cluster labels aligned with the
    rows) and evaluate ``statistic(row_indices)`` each time; returns the array
    of statistics (NaN where the statistic returned None)."""
    clusters = np.asarray(clusters)
    labels, inverse = np.unique(clusters, return_inverse=True)
    members = [np.flatnonzero(inverse == i) for i in range(len(labels))]
    rng = np.random.default_rng(seed)
    out = np.full(n_boot, np.nan)
    for b in range(n_boot):
        picked = rng.integers(0, len(labels), size=len(labels))
        rows = np.concatenate([members[i] for i in picked]) if len(labels) else np.zeros(0, dtype=int)
        value = statistic(rows)
        out[b] = np.nan if value is None else float(value)
    return out


def interval(samples, level: float = 0.95) -> tuple[Optional[float], Optional[float]]:
    arr = np.asarray(samples, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None, None
    lo = (1.0 - level) / 2.0
    return float(np.quantile(arr, lo)), float(np.quantile(arr, 1.0 - lo))
