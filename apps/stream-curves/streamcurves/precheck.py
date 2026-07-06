"""Port of R/04_metric_precheck.R — per-metric summary statistics and quality flags."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ._rcompat import as_numeric_r, r_round

logger = logging.getLogger("streamcurves")

_PRECHECK_COLUMNS = [
    "metric",
    "display_name",
    "column_name",
    "metric_family",
    "n_obs",
    "n_missing",
    "pct_missing",
    "min",
    "q25",
    "median",
    "mean",
    "q75",
    "max",
    "sd",
    "iqr",
    "flag_low_variance",
    "flag_impossible_values",
    "flag_low_n",
    "precheck_status",
]

_NA_STATS = {
    "min": np.nan,
    "q25": np.nan,
    "median": np.nan,
    "mean": np.nan,
    "q75": np.nan,
    "max": np.nan,
    "sd": np.nan,
    "iqr": np.nan,
}


def run_metric_precheck(data: pd.DataFrame, metric_config) -> pd.DataFrame:
    """One row per metric with summary stats and flags.

    ``precheck_status`` ∈ {pass, caution, fail, no_data, categorical,
    missing_column}. Quantiles are numpy default (R type 7); sd uses ddof=1.
    """
    logger.info("Running metric precheck...")

    rows: list[dict] = []
    for metric_key, mc in (metric_config or {}).items():
        mc = mc or {}
        col_name = mc.get("column_name")
        base = {
            "metric": metric_key,
            "display_name": mc.get("display_name"),
            "column_name": col_name,
            "metric_family": mc.get("metric_family"),
        }

        # Skip if column not in data
        if col_name not in data.columns:
            logger.warning("Column %s not found for metric %s", col_name, metric_key)
            rows.append(
                {
                    **base,
                    "n_obs": np.nan,
                    "n_missing": np.nan,
                    "pct_missing": np.nan,
                    **_NA_STATS,
                    "flag_low_variance": None,
                    "flag_impossible_values": None,
                    "flag_low_n": None,
                    "precheck_status": "missing_column",
                }
            )
            continue

        vals = data[col_name]

        # Categorical metrics
        if mc.get("metric_family") in ("categorical",):
            n_obs = int(vals.notna().sum())
            n_missing = int(vals.isna().sum())
            rows.append(
                {
                    **base,
                    "n_obs": n_obs,
                    "n_missing": n_missing,
                    "pct_missing": r_round(100 * n_missing / len(vals), 1)
                    if len(vals)
                    else np.nan,
                    **_NA_STATS,
                    "flag_low_variance": False,
                    "flag_impossible_values": False,
                    "flag_low_n": bool(n_obs < mc["min_sample_size"]),
                    "precheck_status": "categorical",
                }
            )
            continue

        # Numeric metrics (as.numeric on factors -> level codes; see _rcompat)
        vals_num = as_numeric_r(vals)
        n_obs = int(vals_num.notna().sum())
        n_missing = int(vals_num.isna().sum())
        pct_missing = (
            r_round(100 * n_missing / len(vals_num), 1) if len(vals_num) else np.nan
        )

        if n_obs == 0:
            rows.append(
                {
                    **base,
                    "n_obs": 0,
                    "n_missing": n_missing,
                    "pct_missing": pct_missing,
                    **_NA_STATS,
                    "flag_low_variance": None,
                    "flag_impossible_values": None,
                    "flag_low_n": True,
                    "precheck_status": "no_data",
                }
            )
            continue

        v = vals_num.dropna().to_numpy(dtype=float)
        q25, q75 = np.quantile(v, [0.25, 0.75])

        # Flags. R sd() of a single value is NA -> flag is NA (None here).
        sd_v = float(np.std(v, ddof=1)) if v.size > 1 else np.nan
        flag_low_var = None if np.isnan(sd_v) else bool(sd_v < 0.001)
        flag_impossible = False
        if mc.get("metric_family") == "proportion":
            flag_impossible = bool(np.any((v < 0) | (v > 100)))
        flag_low_n = bool(n_obs < mc["min_sample_size"])

        status = "pass"
        if flag_low_n:
            status = "caution"
        if n_obs == 0:  # NOTE(parity): unreachable, ported 1:1 from R
            status = "fail"

        rows.append(
            {
                **base,
                "n_obs": n_obs,
                "n_missing": n_missing,
                "pct_missing": pct_missing,
                "min": float(v.min()),
                "q25": float(q25),
                "median": float(np.median(v)),
                "mean": float(v.mean()),
                "q75": float(q75),
                "max": float(v.max()),
                "sd": sd_v,
                "iqr": float(q75 - q25),
                "flag_low_variance": flag_low_var,
                "flag_impossible_values": flag_impossible,
                "flag_low_n": flag_low_n,
                "precheck_status": status,
            }
        )

    # R purrr::map_dfr over an empty config yields a 0x0 tibble.
    results = (
        pd.DataFrame(rows, columns=_PRECHECK_COLUMNS) if rows else pd.DataFrame()
    )

    logger.info("Precheck complete: %d metrics evaluated", len(results))

    return results
