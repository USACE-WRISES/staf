"""Port of R/12_regional_curves.R — regional / hydraulic geometry curves.

Power-function fits for bankfull dimensions vs drainage area via
``lm(log10(y) ~ log10(x))`` (statsmodels OLS). The ggplot objects of the R
version become data-only plot specs (points + 95% confidence ribbon computed
on a 100-point log grid, back-transformed with ``10**``).
"""

from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

from ._rcompat import as_character_scalar, r_num_str, r_round, r_signif
from .screening import _r_char, _wilcox

logger = logging.getLogger("streamcurves")


def _ggpubr_p_format(p) -> str:
    """ggpubr's default ``p.format`` label: bare p-value at 2 significant
    digits (no "p =" prefix, no stars)."""
    if p is None or not np.isfinite(p):
        return "NA"
    return r_num_str(float(r_signif(p, 2)))

_MODEL_COLUMNS = [
    "response",
    "predictor",
    "group_var",
    "group_level",
    "n_obs",
    "intercept",
    "slope",
    "coefficient_a",
    "exponent_b",
    "r_squared",
    "adj_r2",
    "p_value",
    "fit_status",
]


def _na_model_row(response_var, predictor_var, group_var, group_level, n_obs, fit_status):
    return {
        "response": response_var,
        "predictor": predictor_var,
        "group_var": group_var,
        "group_level": group_level,
        "n_obs": n_obs,
        "intercept": np.nan,
        "slope": np.nan,
        "coefficient_a": np.nan,
        "exponent_b": np.nan,
        "r_squared": np.nan,
        "adj_r2": np.nan,
        "p_value": np.nan,
        "fit_status": fit_status,
    }


def _positive_complete(data, cols, response_var, predictor_var):
    """R: select cols |> drop_na() |> filter(response > 0, predictor > 0)."""
    df = data[cols].dropna()
    return df[(df[response_var] > 0) & (df[predictor_var] > 0)]


def _fit_loglog(df, response_var, predictor_var):
    """lm(log10(response) ~ log10(predictor)).

    NOTE(parity): with an aliased (constant) predictor R's lm drops the slope
    coefficient to NA while statsmodels' pinv solves it — not reachable with
    real regional data.
    """
    x = np.log10(df[predictor_var].to_numpy(dtype=float))
    y = np.log10(df[response_var].to_numpy(dtype=float))
    return sm.OLS(y, sm.add_constant(x, has_constant="add")).fit()


def _model_row_from_fit(fit, response_var, predictor_var, group_var, group_level, n_obs):
    intercept = float(fit.params[0])
    slope = float(fit.params[1]) if len(fit.params) >= 2 else np.nan
    p_value = float(fit.pvalues[1]) if len(fit.params) >= 2 else np.nan
    return {
        "response": response_var,
        "predictor": predictor_var,
        "group_var": group_var,
        "group_level": group_level,
        "n_obs": n_obs,
        "intercept": intercept,
        "slope": slope,
        "coefficient_a": 10.0**intercept,
        "exponent_b": slope,
        "r_squared": float(fit.rsquared),
        "adj_r2": float(fit.rsquared_adj),
        "p_value": p_value,
        "fit_status": "complete",
    }


def fit_regional_curve(
    data: pd.DataFrame,
    response_var: str,
    predictor_var: str,
    group_var: str | None = None,
) -> dict:
    """Fit a single regional curve (power function via log-log regression).

    Returns ``{"model_summary": DataFrame, "plot_spec": dict | None}``
    (the R version returns ``list(model_summary, plot)``).
    """
    if group_var is not None:
        # -- Stratified fit ---------------------------------------------------
        groups = [g for g in data[group_var].dropna().unique()]

        model_rows = []
        for g in groups:
            df = _positive_complete(
                data[data[group_var] == g],
                [response_var, predictor_var],
                response_var,
                predictor_var,
            )

            if len(df) < 3:
                model_rows.append(
                    _na_model_row(
                        response_var,
                        predictor_var,
                        group_var,
                        as_character_scalar(g),
                        len(df),
                        "insufficient_data",
                    )
                )
                continue

            try:
                fit = _fit_loglog(df, response_var, predictor_var)
            except Exception:
                fit = None

            if fit is None:
                model_rows.append(
                    _na_model_row(
                        response_var,
                        predictor_var,
                        group_var,
                        as_character_scalar(g),
                        len(df),
                        "fit_failed",
                    )
                )
                continue

            model_rows.append(
                _model_row_from_fit(
                    fit,
                    response_var,
                    predictor_var,
                    group_var,
                    as_character_scalar(g),
                    len(df),
                )
            )

        model_summary = pd.DataFrame(model_rows, columns=_MODEL_COLUMNS)

        # Stratified plot spec
        plot_data = _positive_complete(
            data,
            [response_var, predictor_var, group_var],
            response_var,
            predictor_var,
        )
        plot_spec = {
            "type": "regional_curve_stratified",
            "points": plot_data,
            "response_var": response_var,
            "predictor_var": predictor_var,
            "group_var": group_var,
            "title": f"Regional Curve: {response_var} vs {predictor_var}",
            "subtitle": f"Stratified by {group_var} (log-log power function)",
            "x_label": f"{predictor_var} (log scale)",
            "y_label": f"{response_var} (log scale)",
            "log_x": True,
            "log_y": True,
        }

        return {"model_summary": model_summary, "plot_spec": plot_spec}

    # -- Unstratified fit -------------------------------------------------------
    df = _positive_complete(
        data, [response_var, predictor_var], response_var, predictor_var
    )

    if len(df) < 3:
        return {
            "model_summary": pd.DataFrame(
                [
                    _na_model_row(
                        response_var,
                        predictor_var,
                        None,
                        "all",
                        len(df),
                        "insufficient_data",
                    )
                ],
                columns=_MODEL_COLUMNS,
            ),
            "plot_spec": None,
        }

    try:
        fit = _fit_loglog(df, response_var, predictor_var)
    except Exception:
        fit = None

    if fit is None:
        return {
            "model_summary": pd.DataFrame(
                [
                    _na_model_row(
                        response_var, predictor_var, None, "all", len(df), "fit_failed"
                    )
                ],
                columns=_MODEL_COLUMNS,
            ),
            "plot_spec": None,
        }

    model_row = _model_row_from_fit(
        fit, response_var, predictor_var, None, "all", len(df)
    )
    model_summary = pd.DataFrame([model_row], columns=_MODEL_COLUMNS)

    # Prediction for the 95% confidence ribbon on a 100-point log10 grid
    x_vals = df[predictor_var].to_numpy(dtype=float)
    pred_range = np.linspace(
        np.log10(x_vals.min()), np.log10(x_vals.max()), 100
    )
    prediction = fit.get_prediction(sm.add_constant(pred_range, has_constant="add"))
    conf = prediction.conf_int()  # 95% like R predict(interval = "confidence")
    ribbon_data = pd.DataFrame(
        {
            "x": 10.0**pred_range,
            "y": 10.0**prediction.predicted_mean,
            "ymin": 10.0 ** conf[:, 0],
            "ymax": 10.0 ** conf[:, 1],
        }
    )

    eq_label = (
        f"{response_var} = {r_num_str(r_round(model_row['coefficient_a'], 3))} × "
        f"{predictor_var}^{r_num_str(r_round(model_row['slope'], 3))}"
        f"\nR² = {r_num_str(r_round(model_row['r_squared'], 3))}, n = {len(df)}"
    )

    plot_spec = {
        "type": "regional_curve",
        "points": df,
        "ribbon": ribbon_data,
        "eq_label": eq_label,
        "annotation_xy": (float(x_vals.min()), float(df[response_var].max())),
        "response_var": response_var,
        "predictor_var": predictor_var,
        "title": f"Regional Curve: {response_var} vs {predictor_var}",
        "subtitle": "Power function fit (log-log)",
        "x_label": f"{predictor_var} (log scale)",
        "y_label": f"{response_var} (log scale)",
        "log_x": True,
        "log_y": True,
    }

    return {"model_summary": model_summary, "plot_spec": plot_spec}


def build_regional_boxplot_spec(
    data: pd.DataFrame,
    response_col: str,
    response_label: str,
    strat_col: str,
    strat_label: str,
    pairwise_comparisons=None,
) -> dict | None:
    """Port of R/12 build_regional_boxplot — data-only spec for the exploration
    boxplot of a regional response by a stratification variable.

    R draws a ggpubr boxplot (viridis fill) with an omnibus Kruskal-Wallis
    label and, when configured, pairwise Wilcoxon significance brackets
    (``wilcox.test(exact = FALSE)``, default ``p.format`` labels). The spec
    carries the KW label plus a ``brackets`` list — geometry and p-labels the
    renderer draws as segments/text, since plotnine has no bracket geom.

    Returns ``None`` when fewer than 3 complete rows are available (R's guard).
    """
    if data is None or response_col not in data.columns or strat_col not in data.columns:
        return None

    df = data[[response_col, strat_col]].dropna().copy()
    if len(df) < 3:
        return None

    df[response_col] = pd.to_numeric(df[response_col], errors="coerce")
    df = df.dropna(subset=[response_col])
    if len(df) < 3:
        return None

    df[strat_col] = df[strat_col].astype(str)
    # R factor() default orders levels alphabetically.
    levels = sorted(df[strat_col].unique())
    counts = df[strat_col].value_counts()
    label_map = {lvl: f"{lvl}\n(n={int(counts[lvl])})" for lvl in levels}
    ordered_labels = [label_map[lvl] for lvl in levels]
    df["group_label"] = pd.Categorical(
        df[strat_col].map(label_map), categories=ordered_labels, ordered=True
    )

    # Omnibus Kruskal-Wallis across groups (matches R stat_compare_means
    # method="kruskal.test"); scipy replicates R's tie correction.
    samples = [
        df.loc[df[strat_col] == lvl, response_col].to_numpy(dtype=float)
        for lvl in levels
    ]
    samples = [s for s in samples if len(s) > 0]
    kw_p = None
    if len(samples) >= 2:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                kw_p = float(stats.kruskal(*samples).pvalue)
        except (ValueError, FloatingPointError):
            kw_p = None
    if kw_p is not None and not np.isfinite(kw_p):
        kw_p = None

    kw_label = None
    if kw_p is not None:
        kw_label = f"Kruskal-Wallis, p = {r_num_str(r_round(kw_p, 4))}"

    # Pairwise Wilcoxon brackets (R:239-264) — configured pairs only, mapped
    # through the label map and dropped when either level is absent from the
    # data (R's Filter(all(!is.na))). Heights stagger above the data max the
    # way ggsignif stacks stat_compare_means comparisons.
    brackets = []
    if pairwise_comparisons:
        y_vals = df[response_col].to_numpy(dtype=float)
        y_max = float(np.nanmax(y_vals))
        y_min = float(np.nanmin(y_vals))
        rng = (y_max - y_min) or (abs(y_max) or 1.0)
        j = 0
        for pair in pairwise_comparisons:
            if pair is None or len(pair) < 2:
                continue
            g1, g2 = _r_char(pair[0]), _r_char(pair[1])
            if not (isinstance(g1, str) and isinstance(g2, str)):
                continue
            if g1 not in label_map or g2 not in label_map:
                continue
            d1 = df.loc[df[strat_col] == g1, response_col].to_numpy(dtype=float)
            d2 = df.loc[df[strat_col] == g2, response_col].to_numpy(dtype=float)
            wt = _wilcox(d1, d2) if (len(d1) and len(d2)) else None
            p_val = wt[1] if wt is not None else None
            if p_val is not None and not np.isfinite(p_val):
                p_val = None
            xa = levels.index(g1) + 1  # 1-based discrete axis positions
            xb = levels.index(g2) + 1
            x1, x2 = (xa, xb) if xa <= xb else (xb, xa)
            y = y_max + rng * (0.06 + 0.10 * j)
            brackets.append(
                {
                    "group1": g1,
                    "group2": g2,
                    "x1": x1,
                    "x2": x2,
                    "p_value": p_val,
                    "p_label": _ggpubr_p_format(p_val),
                    "y": y,
                    "tip": rng * 0.03,
                    "label_y": y + rng * 0.015,
                }
            )
            j += 1

    return {
        "type": "regional_boxplot",
        "data": df,
        "y_col": response_col,
        "x_col": "group_label",
        "x_levels": ordered_labels,
        "response_label": response_label,
        "strat_label": strat_label,
        "kw_p": kw_p,
        "kw_label": kw_label,
        "brackets": brackets,
        "title": f"{response_label} by {strat_label}",
    }


def run_regional_curves(data: pd.DataFrame, metric_config) -> dict:
    """Run all regional curves.

    Hard-coded responses BW_ft/BD_ft/BA_ft2 vs DA_km2, stratified by
    Ecoregion/DACAT/StreamType2 where present. ``metric_config`` is accepted
    but unused — faithful to the R signature.
    """
    logger.info("Running regional / hydraulic geometry curves...")

    responses = ["BW_ft", "BD_ft", "BA_ft2"]
    predictor = "DA_km2"
    strat_vars = ["Ecoregion", "DACAT", "StreamType2"]

    all_results: list[pd.DataFrame] = []
    all_plots: dict = {}

    for resp in responses:
        if resp not in data.columns:
            logger.warning("Response column %s not in data, skipping", resp)
            continue
        if predictor not in data.columns:
            logger.warning("Predictor column %s not in data, skipping", predictor)
            continue

        # Unstratified
        logger.info("Fitting: %s ~ %s (unstratified)", resp, predictor)
        result = fit_regional_curve(data, resp, predictor, group_var=None)
        all_results.append(result["model_summary"])
        if result["plot_spec"] is not None:
            all_plots[f"regional_{resp}_unstratified"] = result["plot_spec"]

        # Stratified
        for sv in strat_vars:
            if sv in data.columns:
                logger.info("Fitting: %s ~ %s | %s", resp, predictor, sv)
                result = fit_regional_curve(data, resp, predictor, group_var=sv)
                all_results.append(result["model_summary"])
                if result["plot_spec"] is not None:
                    all_plots[f"regional_{resp}_{sv}"] = result["plot_spec"]

    results = (
        pd.concat(all_results, ignore_index=True) if all_results else pd.DataFrame()
    )

    logger.info(
        "Regional curves complete: %d fits, %d plots", len(results), len(all_plots)
    )

    return {"results": results, "plots": all_plots}
