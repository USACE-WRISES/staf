"""Within-pool resampling diagnostics for IQR-seed reference curves.

CURVE-02 leave-one-site-out stability, CURVE-04 drop-one influence, CURVE-06
bootstrap percentile intervals, RED-06 redundancy-category stability, and the
STRAT-01/02/03/06 stratifier-improvement evidence all live here, sharing one
deterministic resampling core. Every stochastic procedure takes an explicit
seed (the agent derives it from the run's inputs digest, so identical runs
resample identically), and DATA-09's leakage guard lives here because these
are the fold-using procedures it protects.

Statistical positions, stated once:

- The IQR-seed curve is a deterministic summary of the reference distribution,
  so "cross-validation" means stability: rebuild the curve without each site
  and measure how much the held-out site's index score and the seed geometry
  move. There is no observed "true index" to score prediction error against.
- Stratifier improvement is evaluated as grouped location prediction: predict a
  held-out reference site's metric value with the training fold's median,
  unstratified versus within-stratum. RMSE and MAE reductions feed STRAT-01/02
  and the delta CV R-squared analog feeds STRAT-03. This is a standard grouped
  cross-validation, not an invented statistic.
- Bootstrap intervals are internal diagnostics on development data. They are
  never validation, and the confidence protocol keeps its caps accordingly.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Optional

import numpy as np
import pandas as pd

from . import curves
from . import methodology
from . import run_state


# --------------------------------------------------------------------------- #
# DATA-09 leakage guard
# --------------------------------------------------------------------------- #
def assert_one_row_per_site(data: pd.DataFrame, site_col: str = "site_id") -> None:
    """Raise when repeated site observations would leak across folds (DATA-09).

    The bundled NRSA frame is one row per site, so this currently never fires,
    but any future revisit or multi-year data must hit this wall before a
    fold-using procedure runs on it.
    """
    if site_col not in data.columns:
        return
    counts = data[site_col].astype(str).value_counts()
    dups = counts[counts > 1]
    if len(dups):
        raise ValueError(
            "DATA-09: repeated observations per site detected "
            f"({', '.join(dups.index[:5])}{'...' if len(dups) > 5 else ''}). "
            "Fold-using procedures require site-grouped splits, which are not "
            "implemented for repeated data. Refusing to resample."
        )


# --------------------------------------------------------------------------- #
# Engine adapters
# --------------------------------------------------------------------------- #
@contextmanager
def _engine_quiet():
    """Mute the engine's own warnings for one resample.

    The engine warns when it falls back on a degenerate Q25. That is worth saying
    once about the curve being published and useless once per resample: a single
    metric at n_boot=1000 produced 1038 copies in the Interior Plateau run, and
    they cannot even be attributed, because this adapter feeds the engine the
    placeholder column name "m". Only the log line is suppressed. The curve_status
    the engine returns is untouched, and that status is what the review queue, the
    packet and the confidence caps actually read.
    """
    log = logging.getLogger("streamcurves")
    prior = log.level
    log.setLevel(max(prior, logging.ERROR))
    try:
        yield
    finally:
        log.setLevel(prior)


def _build_points(values: pd.Series, entry: dict) -> tuple[Optional[list[dict]], str]:
    """(points as [{x, y}], curve_status) for one value series through the real
    engine, so every diagnostic exercises exactly the code that ships."""
    frame = pd.DataFrame({"m": pd.to_numeric(values, errors="coerce")})
    cfg = {"m": {**(entry or {}), "column_name": "m"}}
    with _engine_quiet():
        res = curves.build_reference_curve(frame, "m", cfg, build_plots=False)
    row = res["curve_row"]
    status = str(row.iloc[0].get("curve_status") or "")
    pts_df = res.get("curve_points")
    if pts_df is None or not len(pts_df):
        return None, status
    pts = [{"x": float(r["metric_value"]), "y": float(r["index_score"])}
           for _, r in pts_df.iterrows()]
    return pts, status


def _clean(values: Any) -> pd.Series:
    s = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    return s.astype(float)


# --------------------------------------------------------------------------- #
# CURVE-02: leave-one-site-out stability
# --------------------------------------------------------------------------- #
def loo_curve_stability(values: Any, entry: dict) -> dict:
    """Leave-one-site-out stability of the IQR-seed curve.

    Each fold drops one reference site, rebuilds the curve, and scores the
    held-out site's value on both curves. Reports the spread of those held-out
    index deltas and the largest seed-point movement (as a fraction of the full
    sample's IQR). One row per site is a precondition (DATA-09).
    """
    v = _clean(values)
    n = len(v)
    out = {"n": n, "n_folds": 0, "held_out_mean_abs_delta": None,
           "held_out_max_abs_delta": None, "seed_max_shift_frac": None,
           "structural_change": False, "evaluable": False}
    if n < 5:
        return out
    full_pts, full_status = _build_points(v, entry)
    if full_pts is None:
        return out
    q25, q75 = np.quantile(v.to_numpy(), [0.25, 0.75])
    iqr = float(q75 - q25)
    deltas: list[float] = []
    max_shift = 0.0
    structural = False
    for i in range(n):
        rest = v.drop(v.index[i])
        pts_i, status_i = _build_points(rest, entry)
        if pts_i is None:
            structural = True
            continue
        held = float(v.iloc[i])
        y_full = curves.interp_curve(full_pts, held)
        y_loo = curves.interp_curve(pts_i, held)
        if y_full is not None and y_loo is not None:
            deltas.append(abs(y_full - y_loo))
        if len(pts_i) == len(full_pts) and iqr > 0:
            shift = max(abs(a["x"] - b["x"]) for a, b in zip(full_pts, pts_i)) / iqr
            max_shift = max(max_shift, shift)
        else:
            structural = True
    if deltas:
        out.update({
            "n_folds": len(deltas),
            "held_out_mean_abs_delta": float(np.mean(deltas)),
            "held_out_max_abs_delta": float(np.max(deltas)),
            "seed_max_shift_frac": float(max_shift),
            "structural_change": structural,
            "evaluable": True,
        })
    return out


# --------------------------------------------------------------------------- #
# CURVE-04: drop-one influence
# --------------------------------------------------------------------------- #
def influence_check(values: Any, entry: dict,
                    change_frac: Optional[float] = None) -> dict:
    """Drop-one influence on the seed's key parameters (Q25, Q75, IQR).

    Flags when any single site moves a key parameter by more than the
    configured fraction, or flips the build between validating and not
    (the decision flip in CURVE-04). Names the driving site when the values
    series carries site identifiers in its index.
    """
    if change_frac is None:
        change_frac = float(methodology.threshold(
            "curve_rules.influence_param_change_frac"))
    v = _clean(values)
    n = len(v)
    out = {"n": n, "max_param_change_frac": None, "max_param_change_iqr": None,
           "driver": None, "decision_flip": False, "flagged": False,
           "evaluable": False}
    if n < 5:
        return out
    arr = v.to_numpy()
    q25, q75 = np.quantile(arr, [0.25, 0.75])
    base = {"q25": float(q25), "q75": float(q75), "iqr": float(q75 - q25)}
    _, full_status = _build_points(v, entry)
    full_ok = full_status == "complete"
    worst = 0.0
    worst_iqr = 0.0
    driver = None
    flip = False
    for i in range(n):
        rest = np.delete(arr, i)
        r25, r75 = np.quantile(rest, [0.25, 0.75])
        drop = {"q25": float(r25), "q75": float(r75), "iqr": float(r75 - r25)}
        for key, b in base.items():
            # Scale-free companion (2026-08-21, review STAT-15): the same shift
            # in IQR units, defined whenever the pool has spread, so a quartile
            # sitting at zero cannot silently blank the influence measure.
            if base["iqr"] > 1e-12:
                frac_iqr = abs(drop[key] - b) / base["iqr"]
                if frac_iqr > worst_iqr:
                    worst_iqr = frac_iqr
            denom = abs(b) if abs(b) > 1e-12 else None
            if denom is None:
                continue
            frac = abs(drop[key] - b) / denom
            if frac > worst:
                worst = frac
                driver = str(v.index[i])
        _, status_i = _build_points(v.drop(v.index[i]), entry)
        if (status_i == "complete") != full_ok:
            flip = True
            driver = driver or str(v.index[i])
    out.update({
        "max_param_change_frac": float(worst),
        "max_param_change_iqr": float(worst_iqr),
        "driver": driver,
        "decision_flip": flip,
        "flagged": bool(flip or worst > change_frac),
        "evaluable": True,
    })
    return out


# --------------------------------------------------------------------------- #
# CURVE-06: bootstrap percentile intervals + seed stability
# --------------------------------------------------------------------------- #
def bootstrap_curve(values: Any, entry: dict, *, n_boot: int = 200,
                    seed: int = 0) -> dict:
    """Bootstrap the reference pool and re-derive the seed.

    Returns per-point percentile intervals on the seed x-positions (metric
    values), matched by point order among resamples that reproduce the full
    curve's structure, plus the fraction of resamples whose curve keeps the
    full sample's structure and realized shape. Deterministic for a given seed.
    """
    v = _clean(values)
    n = len(v)
    out = {"n": n, "n_boot": int(n_boot), "seed": int(seed),
           "structure_stability": None, "shape_stability": None,
           "point_intervals": None, "evaluable": False}
    if n < 5:
        return out
    full_pts, _ = _build_points(v, entry)
    if full_pts is None:
        return out
    full_shape = run_state.realized_curve_shape(
        [{"metric_value": p["x"], "index_score": p["y"]} for p in full_pts])
    rng = np.random.default_rng(int(seed))
    arr = v.to_numpy()
    matched_xs: list[list[float]] = [[] for _ in full_pts]
    structure_hits = 0
    shape_hits = 0
    for _ in range(int(n_boot)):
        sample = pd.Series(arr[rng.integers(0, n, size=n)])
        pts_b, status_b = _build_points(sample, entry)
        if pts_b is None or len(pts_b) != len(full_pts) or status_b != "complete":
            continue
        structure_hits += 1
        shape_b = run_state.realized_curve_shape(
            [{"metric_value": p["x"], "index_score": p["y"]} for p in pts_b])
        if shape_b == full_shape:
            shape_hits += 1
        for k, p in enumerate(pts_b):
            matched_xs[k].append(p["x"])
    intervals = []
    for k, p in enumerate(full_pts):
        xs = matched_xs[k]
        if len(xs) >= 20:
            lo, hi = np.percentile(xs, [2.5, 97.5])
            intervals.append({"index_score": p["y"], "x": p["x"],
                              "x_lo": float(lo), "x_hi": float(hi),
                              "n_matched": len(xs)})
        else:
            intervals.append({"index_score": p["y"], "x": p["x"],
                              "x_lo": None, "x_hi": None,
                              "n_matched": len(xs)})
    # n_matched rides with every interval (2026-08-21, review STAT-4): the
    # percentiles condition on structure-reproducing resamples, so a reader must
    # be able to see how many resamples an interval actually summarizes. The
    # conditioning itself is reported, not hidden: structure_stability IS the
    # matched fraction.
    out.update({
        "structure_stability": structure_hits / float(n_boot) if n_boot else None,
        "shape_stability": shape_hits / float(n_boot) if n_boot else None,
        "point_intervals": intervals,
        "n_matched": structure_hits,
        "evaluable": structure_hits > 0,
    })
    return out


# --------------------------------------------------------------------------- #
# RED-06: redundancy-category stability under the bootstrap
# --------------------------------------------------------------------------- #
def _spearman_category(rho: float, strong: float, moderate: float) -> str:
    a = abs(rho)
    if a >= strong:
        return "strong"
    if a >= moderate:
        return "moderate"
    return "low"


def bootstrap_pair_category_stability(x: Any, y: Any, *, n_boot: int = 200,
                                      seed: int = 0) -> dict:
    """Fraction of bootstrap resamples that keep a pair's redundancy category
    (strong / moderate / low bands from the methodology config)."""
    strong = float(methodology.threshold("redundancy_rules.strong_abs_spearman"))
    moderate = float(methodology.threshold("redundancy_rules.moderate_abs_spearman"))
    frame = pd.DataFrame({"x": pd.to_numeric(pd.Series(x), errors="coerce"),
                          "y": pd.to_numeric(pd.Series(y), errors="coerce")}).dropna()
    n = len(frame)
    out = {"n": n, "n_boot": int(n_boot), "category": None, "stability": None,
           "evaluable": False}
    if n < 8:
        return out
    rho_full = frame["x"].corr(frame["y"], method="spearman")
    if rho_full is None or not np.isfinite(rho_full):
        return out
    cat_full = _spearman_category(float(rho_full), strong, moderate)
    rng = np.random.default_rng(int(seed))
    hits = 0
    usable = 0
    for _ in range(int(n_boot)):
        idx = rng.integers(0, n, size=n)
        b = frame.iloc[idx]
        rho_b = b["x"].corr(b["y"], method="spearman")
        if rho_b is None or not np.isfinite(rho_b):
            continue
        usable += 1
        if _spearman_category(float(rho_b), strong, moderate) == cat_full:
            hits += 1
    out.update({
        "category": cat_full,
        "stability": hits / float(usable) if usable else None,
        "evaluable": usable > 0,
    })
    return out


# --------------------------------------------------------------------------- #
# STRAT-01/02/03: grouped cross-validated improvement, STRAT-06 recurrence
# --------------------------------------------------------------------------- #
def _median_without_each(values: np.ndarray) -> np.ndarray:
    """For each element, the median of the other elements. O(n log n).

    Sorts once; the leave-one-out median of a sorted array is index arithmetic
    (drop position i, pick the middle of what remains), so the per-fold pandas
    filtering this replaced never has to run.
    """
    n = len(values)
    order = np.argsort(values, kind="stable")
    a = values[order]

    def pick(j: np.ndarray, i: np.ndarray) -> np.ndarray:
        return a[np.where(j < i, j, j + 1)]

    i = np.arange(n)
    n1 = n - 1
    if n1 % 2 == 1:
        k = (n1 - 1) // 2
        med_sorted = pick(np.full(n, k), i)
    else:
        k2 = n1 // 2
        med_sorted = 0.5 * (pick(np.full(n, k2 - 1), i) + pick(np.full(n, k2), i))
    out = np.empty(n, dtype=float)
    out[order] = med_sorted
    return out


def stratified_loo_improvement(data: pd.DataFrame, value_col: str,
                               stratum_col: str) -> dict:
    """Leave-one-out improvement from predicting a held-out site's metric value
    with its stratum's training median instead of the pool's training median.

    A stratum with fewer than two training members in a fold falls back to the
    pool median for that fold, so a sparse stratum cannot fake an improvement.
    """
    frame = data[[value_col, stratum_col]].copy()
    frame[value_col] = pd.to_numeric(frame[value_col], errors="coerce")
    frame = frame.dropna(subset=[value_col, stratum_col])
    n = len(frame)
    out = {"n": n, "baseline_rmse": None, "stratified_rmse": None,
           "baseline_mae": None, "stratified_mae": None,
           "rmse_improvement_frac": None, "mae_improvement_frac": None,
           "delta_cv_r2": None, "evaluable": False}
    if n < 8 or frame[stratum_col].nunique() < 2:
        return out
    vals = frame[value_col].to_numpy(dtype=float)
    pool_meds = _median_without_each(vals)
    strat_meds = np.empty(n, dtype=float)
    for _, grp_idx in frame.groupby(stratum_col, sort=False).indices.items():
        grp_idx = np.asarray(grp_idx)
        if len(grp_idx) < 3:
            # A held-out member leaves fewer than two training members: fall
            # back to the pool median for those folds.
            strat_meds[grp_idx] = pool_meds[grp_idx]
            continue
        strat_meds[grp_idx] = _median_without_each(vals[grp_idx])
    base = vals - pool_meds
    strat = vals - strat_meds
    sse_base = float(np.sum(base ** 2))
    sse_strat = float(np.sum(strat ** 2))
    rmse_b = float(np.sqrt(np.mean(base ** 2)))
    rmse_s = float(np.sqrt(np.mean(strat ** 2)))
    mae_b = float(np.mean(np.abs(base)))
    mae_s = float(np.mean(np.abs(strat)))
    out.update({
        "baseline_rmse": rmse_b, "stratified_rmse": rmse_s,
        "baseline_mae": mae_b, "stratified_mae": mae_s,
        "rmse_improvement_frac": (1.0 - rmse_s / rmse_b) if rmse_b > 0 else None,
        "mae_improvement_frac": (1.0 - mae_s / mae_b) if mae_b > 0 else None,
        "delta_cv_r2": (1.0 - sse_strat / sse_base) if sse_base > 0 else None,
        "evaluable": True,
    })
    return out


def aicc_from_rss(n: int, rss: float, k_params: int) -> Optional[float]:
    """Gaussian AICc from a residual sum of squares (STRAT-04/05 support).

    AICc = n ln(RSS/n) + 2k + 2k(k+1)/(n-k-1), additive constants dropped (they
    cancel in the deltas the rules test). None when the correction is undefined
    (n <= k+1) or the fit is degenerate.
    """
    n = int(n)
    k = int(k_params)
    if n <= k + 1 or rss is None or rss <= 0:
        return None
    return float(n * np.log(rss / n) + 2 * k + (2 * k * (k + 1)) / (n - k - 1))


def stratifier_ic_support(data: pd.DataFrame, value_col: str,
                          stratum_col: str) -> dict:
    """STRAT-04/05: AICc support for a by-stratum location model over the pooled
    one. Pooled model: one mean plus a variance (k = 2). Stratified model: one
    mean per populated stratum plus a variance (k = G + 1). A positive
    ``delta_aicc`` (pooled minus stratified) supports stratification, tested
    against the configured 4-and-10 bands."""
    frame = data[[value_col, stratum_col]].copy()
    frame[value_col] = pd.to_numeric(frame[value_col], errors="coerce")
    frame = frame.dropna(subset=[value_col, stratum_col])
    n = len(frame)
    out = {"n": n, "aicc_pooled": None, "aicc_stratified": None,
           "delta_aicc": None, "supports_min": None, "supports_strong": None,
           "evaluable": False}
    groups = frame.groupby(stratum_col)[value_col]
    n_groups = groups.ngroups
    if n < 8 or n_groups < 2:
        return out
    pooled_rss = float(((frame[value_col] - frame[value_col].mean()) ** 2).sum())
    strat_rss = float(sum(((g - g.mean()) ** 2).sum() for _, g in groups))
    aicc_pool = aicc_from_rss(n, pooled_rss, 2)
    aicc_strat = aicc_from_rss(n, strat_rss, n_groups + 1)
    if aicc_pool is None or aicc_strat is None:
        return out
    delta = aicc_pool - aicc_strat
    out.update({
        "aicc_pooled": aicc_pool, "aicc_stratified": aicc_strat,
        "delta_aicc": float(delta),
        "supports_min": bool(delta >= float(
            methodology.threshold("stratifier_rules.min_delta_aicc"))),
        "supports_strong": bool(delta >= float(
            methodology.threshold("stratifier_rules.strong_delta_aicc"))),
        "evaluable": True,
    })
    return out


def bootstrap_improvement_recurrence(data: pd.DataFrame, value_col: str,
                                     stratum_col: str, *, n_boot: int = 200,
                                     seed: int = 0) -> dict:
    """STRAT-06: the fraction of bootstrap resamples in which the stratifier's
    LOO RMSE improvement recurs (stays above zero and above the STRAT-01 floor)."""
    floor = float(methodology.threshold("stratifier_rules.min_cv_error_improvement"))
    frame = data[[value_col, stratum_col]].dropna()
    n = len(frame)
    out = {"n": n, "n_boot": int(n_boot), "recurrence_above_zero": None,
           "recurrence_above_floor": None, "evaluable": False}
    if n < 8 or frame[stratum_col].nunique() < 2:
        return out
    rng = np.random.default_rng(int(seed))
    above_zero = 0
    above_floor = 0
    usable = 0
    for _ in range(int(n_boot)):
        idx = rng.integers(0, n, size=n)
        b = frame.iloc[idx].reset_index(drop=True)
        if b[stratum_col].nunique() < 2:
            continue
        res = stratified_loo_improvement(b, value_col, stratum_col)
        imp = res.get("rmse_improvement_frac")
        if imp is None:
            continue
        usable += 1
        if imp > 0:
            above_zero += 1
        if imp >= floor:
            above_floor += 1
    out.update({
        "recurrence_above_zero": above_zero / float(usable) if usable else None,
        "recurrence_above_floor": above_floor / float(usable) if usable else None,
        "evaluable": usable > 0,
    })
    return out
