"""Port of R/05b_effect_size.R — effect sizes for stratification screening.

Computes epsilon-squared (Kruskal-Wallis), eta-squared (one-way ANOVA sums of
squares) and rank-biserial r (two-group Wilcoxon) per metric x stratification.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .screening import _factor, _kruskal, _wilcox

_COLUMNS = [
    "metric",
    "stratification",
    "epsilon_squared",
    "eta_squared",
    "rank_biserial_r",
    "effect_size_label",
    "variance_explained_pct",
]


def _na_row(metric_key: str, strat_key: str) -> dict:
    return {
        "metric": metric_key,
        "stratification": strat_key,
        "epsilon_squared": np.nan,
        "eta_squared": np.nan,
        "rank_biserial_r": np.nan,
        "effect_size_label": "not_applicable",
        "variance_explained_pct": np.nan,
    }


def compute_effect_sizes(
    data: pd.DataFrame,
    metric_key: str,
    strat_keys,
    metric_config: dict,
    strat_config: dict,
) -> pd.DataFrame:
    """Compute effect sizes for stratification candidates.

    Returns a DataFrame with one row per stratification key:
    ``[metric, stratification, epsilon_squared, eta_squared, rank_biserial_r,
    effect_size_label, variance_explained_pct]``.
    """
    mc = metric_config[metric_key]
    col_name = mc["column_name"]

    rows = []
    for strat_key in strat_keys:
        sc = strat_config[strat_key]

        # Skip paired stratifications
        if sc.get("type") == "paired":
            rows.append(_na_row(metric_key, strat_key))
            continue

        strat_col = sc["column_name"]

        # Check columns exist
        if col_name not in data.columns or strat_col not in data.columns:
            rows.append(_na_row(metric_key, strat_key))
            continue

        df = data.loc[:, [col_name, strat_col]].dropna().copy()

        df[strat_col] = _factor(df[strat_col])
        df = df[df[strat_col].notna()]

        n = len(df)
        groups = list(df[strat_col].cat.categories)
        n_groups = int(df[strat_col].nunique())

        if n < 3 or n_groups < 2:
            rows.append(_na_row(metric_key, strat_key))
            continue

        # ── Epsilon-squared (KW effect size): H / (n - 1) ───────────────────
        # NOTE(parity): the R kruskal.test runs on all groups; all-tied data
        # yields a NaN H in both R and scipy.
        # NOTE(parity): R's kruskal.test never checks the response type —
        # rank() ranks character responses lexicographically and factor
        # responses by level code, so epsilon^2 IS computed for categorical
        # metrics (e.g. CFPs "Y"/"N"). Rank-code such responses identically;
        # ranks are invariant under this order-preserving recode.
        y_kw = df[col_name]
        if isinstance(y_kw.dtype, pd.CategoricalDtype):
            y_kw = pd.Series(y_kw.cat.codes.astype(float), index=df.index)
        elif not pd.api.types.is_numeric_dtype(y_kw):
            codes = pd.Categorical(y_kw).codes  # categories = sorted unique
            y_kw = pd.Series(codes.astype(float), index=df.index)
        epsilon_sq = np.nan
        kw = _kruskal([y_kw[df[strat_col] == lev] for lev in groups
                       if (df[strat_col] == lev).any()])
        if kw is not None:
            epsilon_sq = kw[0] / (n - 1)

        # ── Eta-squared (one-way ANOVA): SS_between / SS_total ──────────────
        # Direct sums of squares — identical to R's aov() Sum Sq decomposition.
        # NOTE(parity): R's error handling differs by response type (R/05b:79-90).
        # A FACTOR metric column (e.g. BEHI_NBS after clean_data): aov() only
        # warns, and the unguarded summary(aov_fit) errors — the error escapes
        # compute_effect_sizes. A CHARACTER metric column (e.g. CFPs "Y"/"N"):
        # the aov() call itself errors and IS tryCatch-caught, leaving eta NA
        # and an all-NA "not_applicable" row. Mirror both paths exactly.
        eta_sq = np.nan
        if isinstance(df[col_name].dtype, pd.CategoricalDtype):
            raise ValueError(
                f"compute_effect_sizes: metric column {col_name!r} is a factor — "
                "R errors in summary(aov()) for factor responses (R/05b:84)"
            )
        try:
            y = df[col_name].to_numpy(dtype=float)
        except (ValueError, TypeError):
            y = None  # character response: R's aov() errors and is caught
        if y is not None:
            grand_mean = y.mean()
            ss_total = float(((y - grand_mean) ** 2).sum())
            ss_between = 0.0
            for _, g in df.groupby(strat_col, observed=True):
                gv = g[col_name].to_numpy(dtype=float)
                ss_between += len(gv) * (gv.mean() - grand_mean) ** 2
            if ss_total > 0:
                eta_sq = ss_between / ss_total

        # ── Rank-biserial r (for 2-group Wilcoxon): r = 1 - (2*W) / (n1*n2) ──
        rank_bis_r = np.nan
        if n_groups == 2:
            g1_vals = df.loc[df[strat_col] == groups[0], col_name]
            g2_vals = df.loc[df[strat_col] == groups[1], col_name]
            wt = _wilcox(g1_vals, g2_vals)
            if wt is not None:
                n1 = len(g1_vals)
                n2 = len(g2_vals)
                if n1 > 0 and n2 > 0:
                    rank_bis_r = 1 - (2 * wt[0]) / (n1 * n2)

        # ── Label effect size ────────────────────────────────────────────────
        # Use epsilon-squared as primary effect size measure
        es = epsilon_sq if not np.isnan(epsilon_sq) else eta_sq
        if np.isnan(es):
            label = "not_applicable"
        elif es < 0.01:
            label = "negligible"
        elif es < 0.06:
            label = "small"
        elif es < 0.14:
            label = "medium"
        else:
            label = "large"

        var_explained = (
            float(np.round(eta_sq * 100, 2)) if not np.isnan(eta_sq) else np.nan
        )

        rows.append(
            {
                "metric": metric_key,
                "stratification": strat_key,
                "epsilon_squared": float(epsilon_sq),
                "eta_squared": eta_sq,
                "rank_biserial_r": float(rank_bis_r),
                "effect_size_label": label,
                "variance_explained_pct": var_explained,
            }
        )

    # NOTE(parity): with no strat_keys R's map_dfr yields a 0x0 tibble; the
    # port keeps the column skeleton.
    return pd.DataFrame(rows, columns=_COLUMNS)
