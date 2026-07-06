"""Port of R/05c_pattern_stability.R — LOESS-based pattern stability.

Assesses metric-predictor relationships with a LOESS fit (span 0.75, degree 2,
gaussian — via ``skmisc.loess``, whose fitted values match R's ``loess`` to
~1e-8). The R function returns ``list(results = tibble, plots = named list)``;
this port returns just the ``results`` DataFrame because the pure layer never
builds figures (``build_plots`` is dropped).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from skmisc.loess import loess

_COLUMNS = [
    "metric",
    "stratification",
    "predictor",
    "pattern_shape",
    "loess_r_squared",
    "n_sign_changes",
    "stability_rating",
]


def assess_pattern_stability(
    data: pd.DataFrame,
    metric_key: str,
    strat_key,
    predictor_keys,
    metric_config: dict,
    strat_config: dict,
    predictor_config: dict,
) -> pd.DataFrame:
    """Assess pattern stability for a metric across predictors within strata.

    ``strat_key`` may be None (R ``NULL``) or ``"none"`` for unstratified.
    Returns a DataFrame ``[metric, stratification, predictor, pattern_shape,
    loess_r_squared, n_sign_changes, stability_rating]`` with one row per
    predictor that passes the data checks (>= 6 complete rows, numeric
    predictor, columns present).
    """
    mc = metric_config[metric_key]
    col_name = mc["column_name"]

    # Resolve stratification column
    strat_col = None
    if strat_key is not None and strat_key != "none":
        sc = strat_config.get(strat_key)
        if sc is not None and sc.get("column_name") is not None:
            strat_col = sc["column_name"]

    rows = []

    for pred_key in predictor_keys:
        pc = predictor_config.get(pred_key)
        if pc is None:
            continue
        pred_col = pc["column_name"]

        # Check columns exist
        needed_cols = [col_name, pred_col]
        if strat_col is not None:
            needed_cols.append(strat_col)
        if not all(c in data.columns for c in needed_cols):
            continue

        # NOTE(parity): drop_na() spans every selected column, so rows with a
        # missing stratum are dropped even though the LOESS fit below is
        # unstratified (the strat column only colors the R plot).
        df = data.loc[:, list(dict.fromkeys(needed_cols))].dropna()

        if len(df) < 6:
            continue

        # Ensure predictor is numeric (R is.numeric() is FALSE for logicals)
        if not pd.api.types.is_numeric_dtype(df[pred_col]) or pd.api.types.is_bool_dtype(
            df[pred_col]
        ):
            continue

        # ── Fit LOESS overall ────────────────────────────────────────────────
        # NOTE(parity): R tryCatch(error -> NULL). skmisc raises in a few spots
        # where R merely warns (e.g. constant x); those land on the "unknown"
        # row here but produce a degenerate fitted row in R.
        fitted_vals = None
        x = y = None
        try:
            x = np.asarray(df[pred_col], dtype=float)
            y = np.asarray(df[col_name], dtype=float)
            lo = loess(x, y, span=0.75, degree=2, family="gaussian")
            lo.fit()
            fitted_vals = np.asarray(lo.outputs.fitted_values, dtype=float)
        except Exception:
            fitted_vals = None

        loess_r2 = np.nan
        n_sign_changes = np.nan
        shape = "unknown"
        stability = "unknown"

        if fitted_vals is not None:
            actual_vals = y

            # R-squared of LOESS
            ss_res = np.nansum((actual_vals - fitted_vals) ** 2)
            ss_tot = np.nansum((actual_vals - np.nanmean(actual_vals)) ** 2)
            if ss_tot > 0:
                loess_r2 = 1 - ss_res / ss_tot

            # Count sign changes in first differences (monotonicity check)
            sorted_idx = np.argsort(x, kind="stable")
            sorted_fitted = fitted_vals[sorted_idx]
            diffs = np.diff(sorted_fitted)
            diffs = diffs[~np.isnan(diffs) & (diffs != 0)]

            if len(diffs) > 1:
                signs = np.sign(diffs)
                n_sign_changes = int((np.diff(signs) != 0).sum())
            else:
                n_sign_changes = 0

            # Classify shape
            if len(diffs) == 0:
                shape = "flat"
            elif n_sign_changes == 0 and np.all(diffs >= 0):
                shape = "monotonic_increasing"
            elif n_sign_changes == 0 and np.all(diffs <= 0):
                shape = "monotonic_decreasing"
            elif n_sign_changes <= 1:
                shape = "humped"
            elif n_sign_changes <= 3:
                shape = "mildly_nonlinear"
            else:
                shape = "noisy"

            # Rate stability
            r2_is_na = np.isnan(loess_r2)
            if shape in ("monotonic_increasing", "monotonic_decreasing") and (
                not r2_is_na and loess_r2 > 0.2
            ):
                stability = "stable"
            elif shape in ("humped", "mildly_nonlinear") and (
                not r2_is_na and loess_r2 > 0.1
            ):
                stability = "marginal"
            elif shape == "flat" or (r2_is_na or loess_r2 < 0.05):
                stability = "unstable"
            else:
                stability = "marginal"

        rows.append(
            {
                "metric": metric_key,
                "stratification": strat_key if strat_key is not None else "none",
                "predictor": pred_key,
                "pattern_shape": shape,
                "loess_r_squared": loess_r2,
                "n_sign_changes": n_sign_changes,
                "stability_rating": stability,
            }
        )

    # NOTE(parity): R bind_rows(list()) yields a 0x0 tibble when nothing was
    # assessed; the port keeps the column skeleton.
    return pd.DataFrame(rows, columns=_COLUMNS)
