"""Port of R/05d_cross_metric_consistency.R — cross-metric stratification
consistency.

Evaluates how consistently each stratification performs across metrics. The R
function returns ``list(consistency_matrix, summary, heatmap_plot)``; this
port returns ``{"consistency_matrix": ..., "summary": ...}`` — the heatmap
ggplot is not built in the pure layer (which is also why ``metric_config`` /
``strat_config`` end up unused here: R only needs them for display names on
the heatmap).
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

logger = logging.getLogger("streamcurves")

_SUMMARY_COLUMNS = [
    "stratification",
    "n_metrics_tested",
    "n_significant",
    "pct_significant",
    "mean_effect_size",
    "consistency_score",
]


def _bind_rows(x) -> pd.DataFrame:
    """R ``dplyr::bind_rows`` over a named list of tibbles (dict of DataFrames),
    a plain list, a single DataFrame, or None."""
    if x is None:
        return pd.DataFrame()
    if isinstance(x, pd.DataFrame):
        return x
    frames = list(x.values()) if isinstance(x, dict) else list(x)
    frames = [f for f in frames if f is not None and len(f) > 0]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def build_phase2_ranking(result: dict, phase1_cands: dict, support_threshold: float):
    """Rank stratifications into the three candidate tiers.

    Lives here rather than in ``views/`` because the headless regional agent
    needs it too and must never import shiny; ``views/summary_state.py``
    re-exports it so its own call sites are unchanged.
    """
    summary = (result or {}).get("summary")
    if summary is None or len(summary) == 0:
        return None
    summary = summary.copy()

    def count_status(sk: str, status: str) -> int:
        n = 0
        for df in (phase1_cands or {}).values():
            if df is None or len(df) == 0:
                continue
            row = df[df["stratification"] == sk]
            if len(row) > 0 and row["candidate_status"].iloc[0] == status:
                n += 1
        return n

    summary["n_promising"] = [count_status(sk, "promising") for sk in summary["stratification"]]
    summary["n_possible"] = [count_status(sk, "possible") for sk in summary["stratification"]]
    summary["n_not_promising"] = [
        count_status(sk, "not_promising") for sk in summary["stratification"]
    ]
    denom = (
        summary["n_promising"] + summary["n_possible"] + summary["n_not_promising"]
    ).clip(lower=1)
    summary["pct_promising_possible"] = (summary["n_promising"] + summary["n_possible"]) / denom
    summary["tier"] = np.select(
        [
            summary["consistency_score"] >= support_threshold,
            summary["consistency_score"] >= support_threshold / 2,
        ],
        ["Broad-Use Candidate", "Metric-Specific Candidate"],
        default="Weak Candidate",
    )
    return summary


def compute_strat_consistency(
    all_layer1_results,
    all_layer2_results,
    metric_config: dict,
    strat_config: dict,
    sig_threshold: float = 0.05,
) -> dict:
    """Compute stratification consistency across metrics.

    ``all_layer1_results``: screening result frames keyed by metric (dict of
    DataFrames, list, or a single combined DataFrame).
    ``all_layer2_results``: effect size frames, same shapes accepted.

    Returns ``{"consistency_matrix": wide DataFrame (metric rows, one column
    per stratification, 1 = significant / 0 = tested not significant / NaN =
    not tested), "summary": per-stratification DataFrame}``.
    """
    # ── Combine all Layer 1 results ──────────────────────────────────────────
    l1_combined = _bind_rows(all_layer1_results)

    if len(l1_combined) == 0:
        logger.warning("No screening results available for consistency analysis.")
        return {"consistency_matrix": pd.DataFrame(), "summary": pd.DataFrame()}

    # ── Build significance matrix ────────────────────────────────────────────
    sig_matrix = l1_combined[l1_combined["p_value"].notna()].copy()
    sig_matrix["significant"] = sig_matrix["p_value"] < sig_threshold
    sig_matrix = sig_matrix[["metric", "stratification", "significant", "p_value"]]

    # Early return if all p_values were NA (e.g., all paired/skipped)
    if len(sig_matrix) == 0:
        logger.warning("No valid screening results after filtering NA p-values.")
        return {"consistency_matrix": pd.DataFrame(), "summary": pd.DataFrame()}

    # ── Merge effect sizes if available ──────────────────────────────────────
    l2_combined = _bind_rows(all_layer2_results)
    if len(l2_combined) > 0:
        sig_matrix = sig_matrix.merge(
            l2_combined[["metric", "stratification", "epsilon_squared", "effect_size_label"]],
            on=["metric", "stratification"],
            how="left",
        )
    else:
        sig_matrix["epsilon_squared"] = np.nan
        sig_matrix["effect_size_label"] = None

    # ── Per-stratification summary ───────────────────────────────────────────
    summary_rows = []
    for strat, g in sig_matrix.groupby("stratification", sort=True, observed=True):
        n_metrics_tested = int(len(g))
        n_significant = int(g["significant"].sum())
        pct_significant = float(np.round(n_significant / n_metrics_tested * 100, 1))
        mean_effect_size = g["epsilon_squared"].mean()  # skipna; NaN when all NA
        summary_rows.append(
            {
                "stratification": strat,
                "n_metrics_tested": n_metrics_tested,
                "n_significant": n_significant,
                "pct_significant": pct_significant,
                "mean_effect_size": float(mean_effect_size)
                if pd.notna(mean_effect_size)
                else np.nan,
            }
        )
    strat_summary = pd.DataFrame(summary_rows)

    # Compute normalized effect size safely
    valid_es = strat_summary["mean_effect_size"].dropna()
    max_es = float(valid_es.max()) if len(valid_es) > 0 else float("-inf")
    if not math.isfinite(max_es) or max_es == 0:
        max_es = 1.0

    mes = strat_summary["mean_effect_size"]
    norm_effect_size = np.where(mes.isna(), 0.0, mes / max_es)
    strat_summary["consistency_score"] = np.round(
        strat_summary["pct_significant"] / 100 * 0.6 + norm_effect_size * 0.4, 3
    )
    strat_summary = strat_summary.sort_values(
        "consistency_score", ascending=False, kind="stable"
    ).reset_index(drop=True)
    strat_summary = strat_summary.loc[:, _SUMMARY_COLUMNS]

    # ── Wide-format matrix for heatmap ───────────────────────────────────────
    # Cell value: 1 = significant, 0 = tested but not significant, NaN = not
    # tested (the "0.5" in the R comment is stale — the R code uses 0).
    heatmap_data = sig_matrix[["metric", "stratification"]].copy()
    heatmap_data["cell_value"] = np.where(sig_matrix["significant"], 1.0, 0.0)

    # pivot_wider keeps rows/columns in first-appearance order
    metric_order = list(dict.fromkeys(heatmap_data["metric"]))
    strat_order = list(dict.fromkeys(heatmap_data["stratification"]))
    wide_matrix = heatmap_data.pivot(
        index="metric", columns="stratification", values="cell_value"
    )
    wide_matrix = wide_matrix.reindex(index=metric_order, columns=strat_order).reset_index()
    wide_matrix.columns.name = None

    return {"consistency_matrix": wide_matrix, "summary": strat_summary}
