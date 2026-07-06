"""Port of R/11_cross_metric.R — cross-metric redundancy analysis.

Correlation matrix, PCA, and redundancy flagging across metrics. The ggplot
objects of the R version become data-only plot specs under ``"plots"``.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ._rcompat import is_true, r_num_str, r_round

logger = logging.getLogger("streamcurves")

_RESULT_COLUMNS = [
    "metric_1",
    "metric_2",
    "display_1",
    "display_2",
    "pearson_r",
    "spearman_rho",
    "abs_pearson",
    "redundant_flag",
]


def _prcomp_scaled(df: pd.DataFrame):
    """``prcomp(x, scale. = TRUE, center = TRUE)``: center by mean, scale by
    sd (ddof=1), then SVD. Returns None when a column cannot be rescaled
    (constant column — R errors, caught by tryCatch). Component signs are
    arbitrary (as in R)."""
    X = df.to_numpy(dtype=float)
    mu = X.mean(axis=0)
    sd = X.std(axis=0, ddof=1)
    if np.any(sd == 0) or not np.all(np.isfinite(sd)):
        return None
    Z = (X - mu) / sd
    try:
        _, S, Vt = np.linalg.svd(Z, full_matrices=False)
    except np.linalg.LinAlgError:
        return None
    sdev = S / np.sqrt(max(Z.shape[0] - 1, 1))
    rotation = Vt.T
    return {
        "sdev": sdev,
        "rotation": rotation,
        "x": Z @ rotation,
        "center": mu,
        "scale": sd,
        "columns": list(df.columns),
    }


def run_cross_metric_analysis(
    data: pd.DataFrame, metric_config, reference_registry=None
) -> dict:
    """Cross-metric redundancy analysis.

    Returns ``{"results": DataFrame, "cor_matrix": DataFrame | None,
    "plots": dict}``. Pairs are flagged redundant when |pearson r| > 0.80 and
    sorted by descending |r|.
    """
    logger.info("Running cross-metric redundancy analysis...")

    # -- Extract numeric metrics ---------------------------------------------
    numeric_metrics = [
        mk
        for mk, mc in (metric_config or {}).items()
        if (mc or {}).get("metric_family") in ("continuous", "proportion", "count")
        and (mc or {}).get("column_name") in data.columns
        and is_true((mc or {}).get("include_in_summary"))
    ]

    col_names = [metric_config[mk]["column_name"] for mk in numeric_metrics]
    display_names = [metric_config[mk].get("display_name") for mk in numeric_metrics]

    metric_data = data[col_names]

    # Need at least 5 complete cases
    complete_rows = metric_data.notna().all(axis=1)
    n_complete = int(complete_rows.sum())
    if n_complete < 5:
        logger.warning("Too few complete cases for cross-metric analysis")
        use_pairwise = True
    else:
        use_pairwise = False

    # -- Correlation matrices -------------------------------------------------
    # use = "complete.obs" -> corr on rows with no NA anywhere;
    # use = "pairwise.complete.obs" -> pandas' default pairwise .corr().
    # NOTE(parity): with zero selected metrics R errors inside dplyr::select
    # (uncaught crash); here that degenerates to cor = None gracefully.
    def _cor(method: str):
        if len(col_names) == 0:
            return None
        try:
            base = metric_data if use_pairwise else metric_data.dropna()
            return base.corr(method=method)
        except Exception:
            return None

    pearson_cor = _cor("pearson")
    spearman_cor = _cor("spearman")

    # -- Flag redundant pairs --------------------------------------------------
    pair_rows: list[dict] = []
    if pearson_cor is not None:
        n_metrics = pearson_cor.shape[1]
        for i in range(n_metrics - 1):
            for j in range(i + 1, n_metrics):
                r_pearson = float(pearson_cor.iat[i, j])
                r_spearman = (
                    float(spearman_cor.iat[i, j]) if spearman_cor is not None else np.nan
                )
                pair_rows.append(
                    {
                        "metric_1": col_names[i],
                        "metric_2": col_names[j],
                        "display_1": display_names[i],
                        "display_2": display_names[j],
                        "pearson_r": r_pearson,
                        "spearman_rho": r_spearman,
                        "abs_pearson": abs(r_pearson),
                        "redundant_flag": None
                        if np.isnan(r_pearson)
                        else bool(abs(r_pearson) > 0.80),
                    }
                )

    # NOTE(parity): with a single metric (no pairs) R crashes in
    # dplyr::arrange (object 'abs_pearson' not found); here the empty results
    # frame is returned with its columns intact.
    results = pd.DataFrame(pair_rows, columns=_RESULT_COLUMNS)
    if len(results):
        results = results.sort_values(
            "abs_pearson", ascending=False, kind="stable", na_position="last"
        ).reset_index(drop=True)

    # -- Plot specs (data-only stand-ins for the R ggplot objects) -------------
    plots: dict = {}

    if pearson_cor is not None:
        # pivot_longer row-major: metric_1 varies slowest
        cor_rows = [
            {"metric_1": r, "metric_2": c, "correlation": float(pearson_cor.at[r, c])}
            for r in pearson_cor.index
            for c in pearson_cor.columns
        ]
        plots["correlation_heatmap"] = {
            "type": "correlation_heatmap",
            "data": pd.DataFrame(cor_rows, columns=["metric_1", "metric_2", "correlation"]),
            "title": "Cross-Metric Correlation Matrix (Pearson)",
            "fill_label": "r",
            "fill_limits": (-1.0, 1.0),
        }

    # PCA biplot
    if not use_pairwise and n_complete >= 5 and len(col_names) >= 1:
        pca_data = metric_data.loc[complete_rows]
        pca_result = _prcomp_scaled(pca_data)

        # NOTE(parity): R subsets x[, 1:2] unguarded and crashes with a single
        # column; the k >= 2 guard here skips the biplot instead.
        if pca_result is not None and pca_data.shape[1] >= 2:
            # Variance explained (summary.prcomp rounds proportions to 5 dp)
            var = pca_result["sdev"] ** 2
            var_explained = np.round(var / var.sum(), 5)[: min(5, pca_data.shape[1])]

            scores = pd.DataFrame(
                {
                    "PC1": pca_result["x"][:, 0],
                    "PC2": pca_result["x"][:, 1],
                    "obs": np.arange(1, pca_result["x"].shape[0] + 1),
                }
            )
            loadings = pd.DataFrame(
                {
                    "PC1": pca_result["rotation"][:, 0],
                    "PC2": pca_result["rotation"][:, 1],
                    "variable": pca_result["columns"],
                }
            )

            scale_factor = (
                max(scores["PC1"].abs().max(), scores["PC2"].abs().max())
                / max(loadings["PC1"].abs().max(), loadings["PC2"].abs().max())
                * 0.8
            )

            pc1_pct = r_num_str(r_round(var_explained[0] * 100, 1))
            pc2_pct = r_num_str(r_round(var_explained[1] * 100, 1))
            plots["pca_biplot"] = {
                "type": "pca_biplot",
                "scores": scores,
                "loadings": loadings,
                "var_explained": var_explained,
                "scale_factor": float(scale_factor),
                "title": "PCA Biplot: Cross-Metric Analysis",
                "subtitle": f"PC1: {pc1_pct}% | PC2: {pc2_pct}%",
                "x_label": f"PC1 ({pc1_pct}%)",
                "y_label": f"PC2 ({pc2_pct}%)",
            }

    n_redundant = int(sum(1 for f in results["redundant_flag"] if f is True))
    logger.info(
        "Cross-metric analysis complete: %d pairs, %d flagged redundant (|r| > 0.80)",
        len(results),
        n_redundant,
    )

    return {
        "results": results,
        "cor_matrix": pearson_cor,
        "plots": plots,
    }
