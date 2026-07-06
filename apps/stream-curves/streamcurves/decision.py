"""Port of R/06_stratification_decision.R — stratification decision logic.

Selects the optimal stratification per metric from screening results, with
optional extended scoring from effect sizes, practical relevance and
feasibility layers.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("streamcurves")

_COLUMNS = [
    "metric",
    "decision_type",
    "selected_strat",
    "selected_p_value",
    "selected_n_groups",
    "selected_min_n",
    "runner_up_strat",
    "runner_up_p_value",
    "needs_review",
    "review_reason",
    "notes",
]


def _row(
    metric,
    decision_type,
    selected_strat=None,
    selected_p_value=np.nan,
    selected_n_groups=np.nan,
    selected_min_n=np.nan,
    runner_up_strat=None,
    runner_up_p_value=np.nan,
    needs_review=False,
    review_reason=None,
    notes=None,
) -> dict:
    return {
        "metric": metric,
        "decision_type": decision_type,
        "selected_strat": selected_strat,
        "selected_p_value": selected_p_value,
        "selected_n_groups": selected_n_groups,
        "selected_min_n": selected_min_n,
        "runner_up_strat": runner_up_strat,
        "runner_up_p_value": runner_up_p_value,
        "needs_review": needs_review,
        "review_reason": review_reason,
        "notes": notes,
    }


def make_stratification_decisions(
    screening_results: pd.DataFrame,
    pairwise_results: pd.DataFrame,
    metric_config: dict,
    strat_config: dict,
    effect_sizes: pd.DataFrame | None = None,
    relevance_scores: pd.DataFrame | None = None,
    feasibility: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Make stratification decisions for all metrics.

    ``pairwise_results`` and ``strat_config`` are accepted for signature parity
    with the R function, which also never uses them.

    Returns a DataFrame with one row per metric:
    ``[metric, decision_type, selected_strat, selected_p_value,
    selected_n_groups, selected_min_n, runner_up_strat, runner_up_p_value,
    needs_review, review_reason, notes]``.
    """
    logger.info("Making stratification decisions...")

    # Determine if extended scoring is available
    has_effect = effect_sizes is not None and len(effect_sizes) > 0
    has_relevance = relevance_scores is not None and len(relevance_scores) > 0
    has_feasibility = feasibility is not None and len(feasibility) > 0

    decision_rows = []
    for metric_key, mc in metric_config.items():

        # Skip categorical metrics
        if mc["metric_family"] in ("categorical",):
            decision_rows.append(
                _row(
                    metric_key,
                    "not_applicable",
                    notes="Categorical metric — no stratification screening",
                )
            )
            continue

        # Get this metric's screening results
        mr = screening_results[screening_results["metric"] == metric_key]

        if len(mr) == 0:
            decision_rows.append(
                _row(metric_key, "none", notes="No screening results available")
            )
            continue

        # ── Score candidates ─────────────────────────────────────────────────
        # Only consider single stratifications (not paired) for primary selection
        candidates = mr[
            ~mr["stratification"].str.contains("_x_", regex=False)
            & ~mr["classification"].str.contains("skipped", regex=False)
            & mr["p_value"].notna()
        ].copy()

        if len(candidates) == 0:
            decision_rows.append(
                _row(metric_key, "none", notes="No valid candidates")
            )
            continue

        p = candidates["p_value"]
        # Significance score (0-1, lower p = higher score)
        candidates["sig_score"] = np.select(
            [p < 0.01, p < 0.05, p < 0.10], [1.0, 0.7, 0.3], default=0.0
        )
        # Sample size adequacy (penalty for small groups)
        mgn = candidates["min_group_n"]
        candidates["size_score"] = np.select(
            [mgn >= 10, mgn >= 5, mgn >= 3], [1.0, 0.7, 0.3], default=0.0
        )
        # Simplicity bonus (fewer groups preferred)
        ng = candidates["n_groups"]
        candidates["simplicity_score"] = np.select(
            [ng <= 2, ng <= 3, ng <= 4], [1.0, 0.8, 0.6], default=0.4
        )

        # ── Extended scoring: add effect size, relevance, feasibility ────────
        if has_effect:
            es_metric = effect_sizes.loc[
                effect_sizes["metric"] == metric_key, ["stratification", "epsilon_squared"]
            ]
            candidates = candidates.merge(es_metric, on="stratification", how="left")
            eps = candidates["epsilon_squared"]
            candidates["effect_score"] = np.select(
                [eps.isna(), eps >= 0.14, eps >= 0.06, eps >= 0.01],
                [0.3, 1.0, 0.7, 0.4],
                default=0.1,
            )
        else:
            candidates["effect_score"] = 0.5  # neutral if not available

        if has_relevance:
            rel_metric = relevance_scores.loc[
                relevance_scores["metric"] == metric_key, ["stratification", "mean_score"]
            ]
            candidates = candidates.merge(rel_metric, on="stratification", how="left")
            ms = candidates["mean_score"]
            candidates["relevance_score"] = np.select(
                [ms.isna(), ms >= 4.0, ms >= 3.0, ms >= 2.0],
                [0.5, 1.0, 0.7, 0.4],
                default=0.1,
            )
        else:
            candidates["relevance_score"] = 0.5

        # ── Composite score (weights depend on available data) ───────────────
        if has_effect or has_relevance:
            # Extended weights: sig 0.35, size 0.10, simplicity 0.10,
            # effect 0.20, relevance 0.15 (base = 0.90; remaining 0.10 is the
            # feasibility demotion applied below)
            candidates["total_score"] = (
                candidates["sig_score"] * 0.35
                + candidates["size_score"] * 0.10
                + candidates["simplicity_score"] * 0.10
                + candidates["effect_score"] * 0.20
                + candidates["relevance_score"] * 0.15
            )
        else:
            # Original weights: sig 0.50, size 0.30, simplicity 0.20
            candidates["total_score"] = (
                candidates["sig_score"] * 0.5
                + candidates["size_score"] * 0.3
                + candidates["simplicity_score"] * 0.2
            )

        # ── Feasibility demotion ──────────────────────────────────────────────
        if has_feasibility:
            feas = feasibility[["stratification", "feasibility_flag"]]
            candidates = candidates.merge(feas, on="stratification", how="left")
            flag = candidates["feasibility_flag"]
            candidates["total_score"] = np.select(
                [flag == "infeasible", flag == "marginal"],
                [0.0, candidates["total_score"] * 0.8],
                default=candidates["total_score"],
            )

        candidates = candidates.sort_values(
            ["total_score", "p_value"], ascending=[False, True], kind="stable"
        ).reset_index(drop=True)

        # ── Decision ──────────────────────────────────────────────────────────
        best = candidates.iloc[0]

        any_sig = bool((candidates["p_value"] < 0.05).any())

        decision_type = "none"
        selected_strat = None
        # NOTE(parity): any candidate with p < 0.05 unlocks selection even when
        # the top-scored candidate itself is only borderline (sig_score 0.3).
        if any_sig and best["sig_score"] > 0:
            decision_type = "single"
            selected_strat = best["stratification"]

        # Runner-up
        runner_up_strat = None
        runner_up_p = np.nan
        if len(candidates) > 1:
            runner_up = candidates.iloc[1]
            runner_up_strat = runner_up["stratification"]
            runner_up_p = runner_up["p_value"]

        # ── Flags for review ──────────────────────────────────────────────────
        needs_review = False
        review_reasons = []

        if len(candidates) > 1:
            score_diff = candidates["total_score"].iloc[0] - candidates["total_score"].iloc[1]
            if score_diff < 0.1:
                needs_review = True
                review_reasons.append("tied_top_candidates")

        if selected_strat is not None and best["min_group_n"] < 5:
            needs_review = True
            review_reasons.append("sparse_groups")

        paired = mr[mr["stratification"].str.contains("_x_", regex=False)]
        if len(paired) > 0 and (paired["classification"] == "rejected_sparse").any():
            # NOTE(parity): R appends this reason without setting needs_review.
            review_reasons.append("paired_strat_sparse")

        if pd.notna(best["p_value"]) and 0.01 < best["p_value"] < 0.10:
            needs_review = True
            review_reasons.append("borderline_significance")

        decision_rows.append(
            _row(
                metric_key,
                decision_type,
                selected_strat=selected_strat,
                selected_p_value=best["p_value"],
                selected_n_groups=best["n_groups"],
                selected_min_n=best["min_group_n"],
                runner_up_strat=runner_up_strat,
                runner_up_p_value=runner_up_p,
                needs_review=needs_review,
                review_reason="; ".join(review_reasons) if review_reasons else None,
                notes=None,
            )
        )

    # NOTE(parity): with an empty metric_config R's map_dfr yields a 0x0
    # tibble; the port keeps the column skeleton.
    decisions = pd.DataFrame(decision_rows, columns=_COLUMNS)

    n_selected = int((decisions["decision_type"] == "single").sum())
    n_none = int((decisions["decision_type"] == "none").sum())
    n_review = int(decisions["needs_review"].fillna(False).sum()) if len(decisions) else 0

    logger.info(
        "Decisions: %d stratified, %d none, %d need review",
        n_selected, n_none, n_review,
    )

    return decisions
