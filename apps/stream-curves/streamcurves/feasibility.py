"""Port of R/05e_feasibility.R — operational feasibility assessment.

Evaluates practical feasibility of stratification candidates from group sizes,
sparse-cell share and data completeness.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_COLUMNS = [
    "stratification",
    "n_levels",
    "min_group_n",
    "max_group_n",
    "pct_sparse_cells",
    "data_completeness_pct",
    "feasibility_flag",
]


def assess_feasibility(data: pd.DataFrame, strat_keys, strat_config: dict) -> pd.DataFrame:
    """Assess operational feasibility of stratification candidates.

    Returns a DataFrame with one row per stratification key:
    ``[stratification, n_levels, min_group_n, max_group_n, pct_sparse_cells,
    data_completeness_pct, feasibility_flag]`` with
    ``feasibility_flag in {"feasible", "marginal", "infeasible", "not_applicable"}``.
    """
    rows = []
    for strat_key in strat_keys:
        sc = strat_config[strat_key]

        # Skip paired stratifications
        if sc.get("type") == "paired":
            rows.append(
                {
                    "stratification": strat_key,
                    "n_levels": np.nan,
                    "min_group_n": np.nan,
                    "max_group_n": np.nan,
                    "pct_sparse_cells": np.nan,
                    "data_completeness_pct": np.nan,
                    "feasibility_flag": "not_applicable",
                }
            )
            continue

        strat_col = sc["column_name"]
        mgs = sc.get("min_group_size")
        min_group_size = mgs if mgs is not None else 5

        # Check column exists
        if strat_col not in data.columns:
            rows.append(
                {
                    "stratification": strat_key,
                    "n_levels": np.nan,
                    "min_group_n": np.nan,
                    "max_group_n": np.nan,
                    "pct_sparse_cells": np.nan,
                    "data_completeness_pct": np.nan,
                    "feasibility_flag": "infeasible",
                }
            )
            continue

        col_data = data[strat_col]
        n_total = len(col_data)
        n_complete = int(col_data.notna().sum())
        # NOTE(parity): R yields NaN for 0/0 when the frame is empty.
        completeness = (
            float(np.round(n_complete / n_total * 100, 1)) if n_total > 0 else np.nan
        )

        # Group sizes. R table(col, useNA = "no"): a factor column counts all
        # its levels (unused ones as 0) — value_counts on a Categorical does
        # the same; plain columns count observed values only.
        groups = col_data.value_counts(dropna=True)
        n_levels = int(len(groups))
        min_n = int(groups.min()) if n_levels > 0 else 0
        max_n = int(groups.max()) if n_levels > 0 else 0
        n_sparse = int((groups < min_group_size).sum())
        pct_sparse = (
            float(np.round(n_sparse / n_levels * 100, 1)) if n_levels > 0 else 100.0
        )

        # Feasibility classification (first match wins)
        if n_levels < 2:
            flag = "infeasible"
        elif min_n < 3:
            flag = "infeasible"
        elif pct_sparse > 50:
            flag = "infeasible"
        elif min_n < min_group_size:
            flag = "marginal"
        elif completeness < 80:
            flag = "marginal"
        elif min_n >= min_group_size and completeness >= 90 and pct_sparse == 0:
            flag = "feasible"
        else:
            flag = "marginal"

        rows.append(
            {
                "stratification": strat_key,
                "n_levels": n_levels,
                "min_group_n": min_n,
                "max_group_n": max_n,
                "pct_sparse_cells": pct_sparse,
                "data_completeness_pct": completeness,
                "feasibility_flag": flag,
            }
        )

    # NOTE(parity): with no strat_keys R's map_dfr yields a 0x0 tibble; the
    # port keeps the column skeleton.
    return pd.DataFrame(rows, columns=_COLUMNS)
