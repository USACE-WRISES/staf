"""Port of R/05_stratification_screening.R — stratification screening.

Kruskal-Wallis / pairwise Wilcoxon tests for each metric x stratification
pair. The R module also builds ggplot boxplots (``build_screening_plot_from_spec``,
the ``plot`` result entry and the ``build_plot`` arguments); this port keeps the
*plot spec* — the plain dict of data + labels the R code assembles before
plotting — but never builds figures. Consequently ``build_plot`` is dropped
from signatures and ``run_all_stratification_screening`` returns ``plot_specs``
(dict of specs) where R returns ``plots`` (named list of ggplots).
"""

from __future__ import annotations

import logging
import math
import warnings

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

logger = logging.getLogger("streamcurves")

# R/00_input_workbook.R:15-16 — internal site id/label columns carried through
# into the plot-spec data so masked sites can be dropped at render time.
streamcurves_site_id_column = "..streamcurves_site_id"
streamcurves_site_label_column = "..streamcurves_site_label"

_RESULT_COLUMNS = [
    "metric",
    "stratification",
    "test",
    "statistic",
    "p_value",
    "n_groups",
    "min_group_n",
    "classification",
    "reason",
]

_PAIRWISE_COLUMNS = [
    "metric",
    "stratification",
    "group1",
    "group2",
    "n1",
    "n2",
    "statistic",
    "p_value",
    "p_adjusted",
]

# The R original's phase-1 selection cut (raw Kruskal-Wallis p, not the BH q of
# the pairwise table). Mirrored by stratifier_rules.screening_significance_alpha
# and verified by methodology.mirror_drift; the parity port keeps the value
# structural rather than injected.
SCREENING_ALPHA = 0.05


# --------------------------------------------------------------------------- #
# Small R-semantics helpers (shared with effects.py).
# --------------------------------------------------------------------------- #


def _r_char(v):
    """R ``as.character()`` for the scalar types seen in strat columns/configs.

    Integral floats render without the decimal point (R ``as.character(5.0)``
    is ``"5"``). NA-ish input maps to ``np.nan`` (R ``NA_character_``).
    """
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return np.nan
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _factor(series: pd.Series) -> pd.Series:
    """R ``factor()``: values coerced to character, levels = sorted unique.

    An input that is already categorical keeps its category order with unused
    categories dropped, matching R's ``factor()`` on an existing factor.
    (Categorical inputs are expected to carry string categories, as R factor
    levels are always character.)
    """
    if isinstance(series.dtype, pd.CategoricalDtype):
        return series.cat.remove_unused_categories()
    as_chr = series.map(_r_char)
    cats = sorted(pd.unique(as_chr.dropna()))
    return pd.Series(pd.Categorical(as_chr, categories=cats), index=series.index)


def _kruskal(samples) -> tuple[float, float] | None:
    """R ``kruskal.test`` via scipy (identical tie correction).

    Returns ``(statistic, p_value)`` or ``None`` on error, mirroring the R
    ``tryCatch(..., error = function(e) NULL)``. With all-tied data both R and
    scipy yield NaN/NaN (scipy additionally warns; R does not — suppressed).
    """
    try:
        arrays = [np.asarray(s, dtype=float) for s in samples]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            res = stats.kruskal(*arrays)
        return float(res.statistic), float(res.pvalue)
    except Exception:
        return None


def _wilcox(d1, d2) -> tuple[float, float] | None:
    """R ``wilcox.test(d1, d2, exact = FALSE)``: two-sided normal approximation
    with continuity correction. R's ``W`` equals scipy's U1 of the first sample.

    Returns ``(statistic, p_value)`` or ``None`` on error (R tryCatch parity).
    All-tied data yields ``W = n1*n2/2`` with a NaN p in both implementations.
    """
    try:
        a1 = np.asarray(d1, dtype=float)
        a2 = np.asarray(d2, dtype=float)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            res = stats.mannwhitneyu(
                a1, a2, alternative="two-sided", method="asymptotic", use_continuity=True
            )
        return float(res.statistic), float(res.pvalue)
    except Exception:
        return None


def _p_adjust_bh(p) -> np.ndarray:
    """R ``p.adjust(p, method = "BH")``: NaNs are masked out of the adjustment
    (n = number of non-missing p-values) and reinserted as NaN."""
    p = np.asarray(p, dtype=float)
    out = np.full(p.shape, np.nan)
    mask = ~np.isnan(p)
    if mask.any():
        out[mask] = multipletests(p[mask], method="fdr_bh")[1]
    return out


def _result_row(
    metric,
    stratification,
    test,
    statistic,
    p_value,
    n_groups,
    min_group_n,
    classification,
    reason,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "metric": metric,
                "stratification": stratification,
                "test": test,
                "statistic": statistic,
                "p_value": p_value,
                "n_groups": n_groups,
                "min_group_n": min_group_n,
                "classification": classification,
                "reason": reason,
            }
        ],
        columns=_RESULT_COLUMNS,
    )


# --------------------------------------------------------------------------- #
# Screening.
# --------------------------------------------------------------------------- #


def screen_stratification(
    data: pd.DataFrame,
    metric_key: str,
    strat_key: str,
    metric_config: dict,
    strat_config: dict,
    compute_pairwise: bool = True,
) -> dict:
    """Screen a single metric against a single stratification variable.

    Returns a dict with:

    - ``result_row``: one-row DataFrame ``[metric, stratification, test,
      statistic, p_value, n_groups, min_group_n, classification, reason]``
    - ``pairwise_df``: pairwise Wilcoxon DataFrame (empty when not computed)
    - ``plot_spec``: dict describing the boxplot the R app would draw, or None
    """
    mc = metric_config[metric_key]
    sc = strat_config[strat_key]
    col_name = mc["column_name"]

    # ── Handle paired stratifications ──────────────────────────────────────
    if sc.get("type") == "paired":
        return screen_paired_stratification(
            data, metric_key, strat_key, metric_config, strat_config
        )

    strat_col = sc["column_name"]

    # Check columns exist
    if col_name not in data.columns or strat_col not in data.columns:
        return {
            "result_row": _result_row(
                metric_key, strat_key, None, np.nan, np.nan, np.nan, np.nan,
                "skipped", "column_missing",
            ),
            "pairwise_df": pd.DataFrame(),
            "plot_spec": None,
        }

    # Prepare data: drop NAs in both metric and stratification.
    # NOTE(parity): tidyr::drop_na() drops rows with NA in ANY selected column,
    # including the site id/label columns when present.
    plot_cols = list(
        dict.fromkeys(
            [col_name, strat_col]
            + [
                c
                for c in (streamcurves_site_id_column, streamcurves_site_label_column)
                if c in data.columns
            ]
        )
    )
    df = data.loc[:, plot_cols].dropna().copy()

    # Ensure stratification is factor
    df[strat_col] = _factor(df[strat_col])
    df = df[df[strat_col].notna()]

    # Group sizes
    group_n = df.groupby(strat_col, observed=True).size()

    n_groups = int(len(group_n))
    # NOTE(parity): R min(integer(0)) is Inf (with a warning) when no groups.
    min_n = int(group_n.min()) if n_groups > 0 else float("inf")
    mgs = sc.get("min_group_size")
    min_group_size = mgs if mgs is not None else 5

    # Skip if too few groups or insufficient group size
    if n_groups < 2:
        return {
            "result_row": _result_row(
                metric_key, strat_key, None, np.nan, np.nan, n_groups, min_n,
                "rejected_sparse", "fewer_than_2_groups",
            ),
            "pairwise_df": pd.DataFrame(),
            "plot_spec": None,
        }

    # min_group_size only gates whether the combination is attempted; the test
    # itself runs on ALL groups present (any n >= 1).
    valid_groups = group_n[group_n >= min_group_size]
    if len(valid_groups) < 2:
        return {
            "result_row": _result_row(
                metric_key, strat_key, None, np.nan, np.nan, n_groups, min_n,
                "rejected_sparse",
                "fewer_than_2_groups_with_n>=" + _r_char(min_group_size),
            ),
            "pairwise_df": pd.DataFrame(),
            "plot_spec": None,
        }

    # ── Kruskal-Wallis test (on all groups, not just the valid ones) ───────
    samples = [g[col_name] for _, g in df.groupby(strat_col, observed=True)]
    kw = _kruskal(samples)

    if kw is None:
        test_name = None
        test_stat = np.nan
        test_p = np.nan
    else:
        test_name = "kruskal_wallis"
        test_stat, test_p = kw

    # ── Pairwise Wilcoxon ───────────────────────────────────────────────────
    pairwise_df = pd.DataFrame()
    pairwise_comparisons = sc.get("pairwise_comparisons")
    if compute_pairwise and pairwise_comparisons:
        pairwise_rows = []
        for pair in pairwise_comparisons:
            g1, g2 = pair[0], pair[1]
            d1 = df.loc[df[strat_col] == _r_char(g1), col_name]
            d2 = df.loc[df[strat_col] == _r_char(g2), col_name]

            if len(d1) < 2 or len(d2) < 2:
                pairwise_rows.append(
                    {
                        "metric": metric_key, "stratification": strat_key,
                        "group1": g1, "group2": g2,
                        "n1": len(d1), "n2": len(d2),
                        "statistic": np.nan, "p_value": np.nan, "p_adjusted": np.nan,
                    }
                )
                continue

            wt = _wilcox(d1, d2)
            pairwise_rows.append(
                {
                    "metric": metric_key, "stratification": strat_key,
                    "group1": g1, "group2": g2,
                    "n1": len(d1), "n2": len(d2),
                    "statistic": wt[0] if wt is not None else np.nan,
                    "p_value": wt[1] if wt is not None else np.nan,
                    "p_adjusted": np.nan,
                }
            )

        pairwise_out = pd.DataFrame(pairwise_rows, columns=_PAIRWISE_COLUMNS)

        # BH adjustment
        if len(pairwise_out) > 0 and pairwise_out["p_value"].notna().any():
            pairwise_out["p_adjusted"] = _p_adjust_bh(pairwise_out["p_value"])

        pairwise_df = pairwise_out

    # ── Classification ──────────────────────────────────────────────────────
    classification = "not_selected"
    if not np.isnan(test_p) and test_p < SCREENING_ALPHA:
        classification = "selected"
    if min_n < min_group_size:
        classification = classification + "_sparse"

    # ── Boxplot spec (figure building dropped in the port) ──────────────────
    label_map = {
        str(level): f"{level}\n(n={int(n)})" for level, n in group_n.items()
    }
    df["group_label"] = [label_map.get(str(v), np.nan) for v in df[strat_col]]

    comparisons = None
    if pairwise_comparisons:
        comps = []
        for pair in pairwise_comparisons:
            l1 = label_map.get(_r_char(pair[0]))
            l2 = label_map.get(_r_char(pair[1]))
            if l1 is not None and l2 is not None:
                comps.append([l1, l2])
        if comps:
            comparisons = comps

    plot_spec = {
        "type": "standard",
        "metric_key": metric_key,
        "strat_key": strat_key,
        "data": df,
        "x_col": "group_label",
        "y_col": col_name,
        "fill_col": "group_label",
        "comparisons": comparisons,
        "title": "{} by {}".format(
            mc.get("display_name") or "", sc.get("display_name") or ""
        ),
        "x_label": sc.get("display_name"),
        "y_label": mc.get("display_name"),
        "palette": "viridis",
    }

    return {
        "result_row": _result_row(
            metric_key, strat_key, test_name, test_stat, test_p,
            n_groups, min_n, classification, None,
        ),
        "pairwise_df": pairwise_df,
        "plot_spec": plot_spec,
    }


def screen_paired_stratification(
    data: pd.DataFrame,
    metric_key: str,
    strat_key: str,
    metric_config: dict,
    strat_config: dict,
) -> dict:
    """Screen a paired stratification (faceted boxplot in the R app).

    Returns the same dict shape as :func:`screen_stratification`; the
    ``pairwise_df`` is always empty and no test is run (``paired_kruskal`` is
    recorded with NA statistic/p-value).
    """
    mc = metric_config[metric_key]
    sc = strat_config[strat_key]
    col_name = mc["column_name"]

    primary_key = sc["primary"]
    secondary_key = sc["secondary"]
    primary_col = strat_config[primary_key]["column_name"]
    secondary_col = strat_config[secondary_key]["column_name"]

    # Check columns exist
    if not all(c in data.columns for c in (col_name, primary_col, secondary_col)):
        return {
            "result_row": _result_row(
                metric_key, strat_key, None, np.nan, np.nan, np.nan, np.nan,
                "skipped", "column_missing",
            ),
            "pairwise_df": pd.DataFrame(),
            "plot_spec": None,
        }

    plot_cols = list(
        dict.fromkeys(
            [col_name, primary_col, secondary_col]
            + [
                c
                for c in (streamcurves_site_id_column, streamcurves_site_label_column)
                if c in data.columns
            ]
        )
    )
    df = data.loc[:, plot_cols].dropna().copy()

    df[primary_col] = _factor(df[primary_col])
    df[secondary_col] = _factor(df[secondary_col])

    # Cell sizes
    cell_n = df.groupby([primary_col, secondary_col], observed=True).size()

    # NOTE(parity): R min(integer(0)) is Inf (with a warning) when df is empty.
    min_cell_n = int(cell_n.min()) if len(cell_n) > 0 else float("inf")
    n_cells = int(len(cell_n))

    # Get secondary comparisons
    sec_comparisons = strat_config[secondary_key].get("pairwise_comparisons")

    comparisons_list = None
    if sec_comparisons:
        comparisons_list = [[_r_char(pair[0]), _r_char(pair[1])] for pair in sec_comparisons]

    plot_spec = {
        "type": "paired",
        "metric_key": metric_key,
        "strat_key": strat_key,
        "data": df,
        "primary_key": primary_key,
        "secondary_key": secondary_key,
        "primary_col": primary_col,
        "secondary_col": secondary_col,
        "y_col": col_name,
        "fill_col": secondary_col,
        "comparisons": comparisons_list,
        "title": "{} by {}".format(
            mc.get("display_name") or "", sc.get("display_name") or ""
        ),
        "x_label": strat_config[secondary_key].get("display_name"),
        "y_label": mc.get("display_name"),
        "palette": "viridis",
    }

    # Classification
    mgs = sc.get("min_group_size")
    classification = "exploratory_only"
    if min_cell_n < (mgs if mgs is not None else 5):
        classification = "rejected_sparse"

    return {
        "result_row": _result_row(
            metric_key, strat_key, "paired_kruskal", np.nan, np.nan,
            n_cells, min_cell_n, classification,
            # NOTE(parity): the reason check is hard-coded at 5 in R even when
            # min_group_size differs from 5.
            "cells_with_n_lt_5" if min_cell_n < 5 else None,
        ),
        "pairwise_df": pd.DataFrame(),
        "plot_spec": plot_spec,
    }


def run_all_stratification_screening(
    data: pd.DataFrame, metric_config: dict, strat_config: dict
) -> dict:
    """Run stratification screening for all metric x stratification combinations.

    Returns a dict with:

    - ``results``: DataFrame of all result rows
    - ``pairwise``: DataFrame of all non-empty pairwise results
    - ``plot_specs``: dict keyed ``"{metric}_{strat}"`` of non-None plot specs
      (the R function returns built ggplots under ``plots`` instead)
    """
    logger.info("Running stratification screening for all metrics...")

    # Build work list: all metric x stratification combinations
    work_items = []
    for metric_key, mc in metric_config.items():
        if mc["metric_family"] in ("categorical",):
            continue
        allowed_strats = mc.get("allowed_stratifications")
        if allowed_strats is None:
            continue
        for strat_key in allowed_strats:
            if strat_key not in strat_config:
                continue
            work_items.append((metric_key, strat_key))

    logger.info(
        "Screening %d metric x stratification combinations...", len(work_items)
    )

    # The R version optionally fans out with furrr; the port is sequential.
    results_list = [
        screen_stratification(data, metric_key, strat_key, metric_config, strat_config)
        for metric_key, strat_key in work_items
    ]

    # Combine results
    result_rows = [r["result_row"] for r in results_list]
    # NOTE(parity): R bind_rows(list()) yields a 0x0 tibble; the port keeps the
    # column skeleton for friendlier downstream handling.
    all_results = (
        pd.concat(result_rows, ignore_index=True)
        if result_rows
        else pd.DataFrame(columns=_RESULT_COLUMNS)
    )
    pairwise_frames = [r["pairwise_df"] for r in results_list if len(r["pairwise_df"]) > 0]
    all_pairwise = (
        pd.concat(pairwise_frames, ignore_index=True)
        if pairwise_frames
        else pd.DataFrame(columns=_PAIRWISE_COLUMNS)
    )

    plot_specs = {
        f"{metric_key}_{strat_key}": r["plot_spec"]
        for (metric_key, strat_key), r in zip(work_items, results_list)
        if r["plot_spec"] is not None
    }

    logger.info(
        "Screening complete: %d combinations, %d plot specs",
        len(all_results), len(plot_specs),
    )

    return {
        "results": all_results,
        "pairwise": all_pairwise,
        "plot_specs": plot_specs,
    }


# --------------------------------------------------------------------------- #
# Phase-1 candidate table
#
# Lives here rather than in views/ because the headless regional agent builds the
# same table and must never import shiny; views/summary_state.py re-exports both
# functions so its own call sites are unchanged.
# --------------------------------------------------------------------------- #


def _first_value(df, column, default=None):
    if df is None or not isinstance(df, pd.DataFrame) or column not in df.columns or len(df) == 0:
        return default
    value = df[column].iloc[0]
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    return value


def auto_phase1_candidate_status(p_val, es_label) -> str:
    p_ok = p_val is not None and not (isinstance(p_val, float) and math.isnan(p_val))
    if p_ok and p_val < 0.05 and es_label in ("medium", "large"):
        return "promising"
    if p_ok and p_val < 0.10:
        return "possible"
    return "not_promising"


def build_metric_phase1_candidate_table_from_sources(
    metric: str,
    allowed,
    existing: pd.DataFrame | None = None,
    l1: pd.DataFrame | None = None,
    l2: pd.DataFrame | None = None,
    include_all_allowed: bool = True,
) -> pd.DataFrame:
    strat_keys: list[str] = []

    def add(keys):
        for k in keys:
            if k not in strat_keys:
                strat_keys.append(k)

    if include_all_allowed:
        add(allowed or [])
    if existing is not None and len(existing) > 0:
        add(existing["stratification"].astype(str).tolist())
    if l1 is not None and len(l1) > 0:
        add(l1["stratification"].astype(str).tolist())
    if not strat_keys:
        return pd.DataFrame()

    def first_match(df, sk):
        if df is None or len(df) == 0:
            return pd.DataFrame()
        return df[df["stratification"] == sk].head(1)

    rows = []
    for sk in strat_keys:
        existing_row = first_match(existing, sk)
        p_row = first_match(l1, sk)
        es_row = first_match(l2, sk)
        p_val = _first_value(existing_row, "p_value", _first_value(p_row, "p_value", np.nan))
        es_label = _first_value(
            existing_row, "effect_size_label",
            _first_value(es_row, "effect_size_label", "negligible"),
        )
        rows.append(
            {
                "metric": metric,
                "stratification": sk,
                "p_value": p_val if p_val is not None else np.nan,
                "epsilon_squared": _first_value(
                    existing_row, "epsilon_squared",
                    _first_value(es_row, "epsilon_squared", np.nan),
                ),
                "effect_size_label": es_label,
                "min_group_n": _first_value(
                    existing_row, "min_group_n", _first_value(p_row, "min_group_n", np.nan)
                ),
                "candidate_status": auto_phase1_candidate_status(p_val, es_label),
                "reviewer_note": "",
            }
        )
    return pd.DataFrame(rows)
