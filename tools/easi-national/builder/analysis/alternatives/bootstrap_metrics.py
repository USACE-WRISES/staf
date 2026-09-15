"""Statistics for integer row multiplicities without expanding bootstrap rows.

These are frequency weights, not survey or importance weights. Each result is
mathematically the statistic in ``analysis.stats`` on explicitly repeated rows;
floating-point summation order can differ. Preparation fixes the finite/known
label cohort and the sorted tie groups. Evaluation never samples, drops draws,
or constructs intervals. Callers retain their existing bootstrap decisions.
"""
from __future__ import annotations

import numpy as np

from ..stats import ORDER


def _array(values, dtype):
    result = np.asarray(values, dtype=dtype)
    if result.ndim != 1:
        raise ValueError("Statistic inputs must be one-dimensional")
    return result


def _pair(a, b):
    if len(a) != len(b):
        raise ValueError("Statistic inputs have different lengths")


class _Prepared:
    def __init__(self, size, valid):
        self.size = size
        self.valid = np.flatnonzero(valid)

    def _weights(self, multiplicities):
        values = np.asarray(multiplicities)
        if values.ndim != 1 or len(values) != self.size:
            raise ValueError("Multiplicities must align with the original observations")
        if values.dtype.kind not in "biuf":
            raise ValueError("Multiplicities must be nonnegative integers")
        weights = values.astype(float, copy=False)
        if (not np.isfinite(weights).all() or (weights < 0).any()
                or (weights != np.floor(weights)).any()):
            raise ValueError("Multiplicities must be nonnegative integers")
        # Exact integer sums and half ranks are required, even on unused rows.
        if weights.sum() > 2 ** 52:
            raise ValueError("Total multiplicity exceeds exact half-rank precision")
        return weights[self.valid]

    def support(self, multiplicities):
        return {"n": int(self._weights(multiplicities).sum())}


class PreparedAUC(_Prepared):
    def __init__(self, values, positive):
        values, positive = _array(values, float), _array(positive, bool)
        _pair(values, positive)
        valid = np.isfinite(values)
        super().__init__(len(values), valid)
        unique, self.inverse = np.unique(values[valid], return_inverse=True)
        self.groups = len(unique)
        self.positive = positive[valid]

    def support(self, multiplicities):
        weights = self._weights(multiplicities)
        n, positive = int(weights.sum()), int(weights[self.positive].sum())
        return {"n": n, "n_positive": positive, "n_negative": n - positive}

    def evaluate(self, multiplicities):
        weights = self._weights(multiplicities)
        positives = np.bincount(self.inverse[self.positive], weights=weights[self.positive], minlength=self.groups)
        negatives = np.bincount(self.inverse[~self.positive], weights=weights[~self.positive], minlength=self.groups)
        n1, n0 = positives.sum(), negatives.sum()
        if n1 == 0 or n0 == 0:
            return None
        # Count positive-negative wins directly, assigning half a win to ties.
        wins = np.dot(positives, np.cumsum(negatives) - .5 * negatives)
        return float(wins / (n1 * n0))


class PreparedSpearman(_Prepared):
    def __init__(self, x, y):
        x, y = _array(x, float), _array(y, float)
        _pair(x, y)
        valid = np.isfinite(x) & np.isfinite(y)
        super().__init__(len(x), valid)
        unique_x, self.x_inverse = np.unique(x[valid], return_inverse=True)
        unique_y, self.y_inverse = np.unique(y[valid], return_inverse=True)
        self.x_groups, self.y_groups = len(unique_x), len(unique_y)

    @staticmethod
    def _ranks(inverse, groups, weights):
        counts = np.bincount(inverse, weights=weights, minlength=groups)
        # A group occupies cumulative-count minus count + 1 through cumulative-count.
        midranks = np.cumsum(counts) - .5 * (counts - 1.)
        return midranks[inverse]

    def evaluate(self, multiplicities):
        weights = self._weights(multiplicities)
        n = weights.sum()
        if n < 3:
            return None
        center = (n + 1.) / 2.
        rx = self._ranks(self.x_inverse, self.x_groups, weights) - center
        ry = self._ranks(self.y_inverse, self.y_groups, weights) - center
        denominator = np.sqrt(np.dot(weights, rx * rx) * np.dot(weights, ry * ry))
        return float(np.dot(weights, rx * ry) / denominator) if denominator > 0 else None


class PreparedKappa(_Prepared):
    def __init__(self, a, b, *, levels=ORDER, weights="linear"):
        a, b = _array(a, object), _array(b, object)
        _pair(a, b)
        levels = tuple(levels)
        if len(levels) < 2:
            raise ValueError("Kappa requires at least two ordinal levels")
        index = {level: i for i, level in enumerate(levels)}

        def category(value):
            try:
                return index.get(value, -1)
            except (TypeError, ValueError):
                return -1

        ia, ib = np.asarray([category(v) for v in a]), np.asarray([category(v) for v in b])
        valid = (ia >= 0) & (ib >= 0)
        super().__init__(len(a), valid)
        self.k = len(levels)
        self.cells = ia[valid] * self.k + ib[valid]
        i, j = np.indices((self.k, self.k))
        self.disagreement = np.abs(i - j) / (self.k - 1) if weights == "linear" else ((i - j) / (self.k - 1)) ** 2

    def evaluate(self, multiplicities):
        weights = self._weights(multiplicities)
        n = weights.sum()
        if n < 2:
            return None
        observed = np.bincount(self.cells, weights=weights, minlength=self.k * self.k).reshape(self.k, self.k)
        expected = np.outer(observed.sum(axis=1), observed.sum(axis=0)) / n
        denominator = (self.disagreement * expected).sum()
        if denominator == 0:
            return None
        return float(1. - (self.disagreement * observed).sum() / denominator)


def prepare_auc(values, positive):
    return PreparedAUC(values, positive)


def prepare_spearman(x, y):
    return PreparedSpearman(x, y)


def prepare_kappa(a, b, *, levels=ORDER, weights="linear"):
    return PreparedKappa(a, b, levels=levels, weights=weights)
