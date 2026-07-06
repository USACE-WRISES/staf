"""Port of R/02_clean_data.R — data cleaning.

Standardizes factor handling and runs QA checks from workbook metadata.
``clean_data`` returns ``(data, qa_log)`` where ``qa_log`` is a DataFrame with
columns ``step``/``message``/``level`` (one row per R QA entry, same order).
"""

from __future__ import annotations

import logging

import pandas as pd

from ._rcompat import (
    as_character_scalar,
    is_na_scalar,
    is_true,
    or_,
    r_num_str,
    r_round,
    trim_character_columns,
)

logger = logging.getLogger("streamcurves")

_QA_COLUMNS = ["step", "message", "level"]


def factor_columns_from_metadata(strat_config, factor_recode_config) -> list[str]:
    """Columns that must become factors: categorical single-strat sources plus
    factor-recode source columns (unique, NA dropped, first-appearance order)."""
    cols: list = []
    for sk in strat_config or {}:
        sc = strat_config[sk] or {}
        if sc.get("type") != "single":
            cols.append(None)
            continue
        if sc.get("source_data_type") != "categorical":
            cols.append(None)
            continue
        cols.append(or_(sc.get("source_column"), or_(sc.get("column_name"), None)))
    for rk in factor_recode_config or {}:
        cols.append((factor_recode_config[rk] or {}).get("source_column"))
    out: list[str] = []
    for c in cols:
        if c is None or is_na_scalar(c):
            continue
        if c not in out:
            out.append(c)
    return out


def factor_recode_target_columns(factor_recode_config) -> list[str]:
    if not (factor_recode_config or {}):
        return []
    out: list[str] = []
    for rk in factor_recode_config:
        tgt = (factor_recode_config[rk] or {}).get("target_column")
        if tgt is None or is_na_scalar(tgt):
            continue
        if tgt not in out:
            out.append(tgt)
    return out


def expected_levels_for_column(strat_config, column_name) -> list[str]:
    """Union of declared levels across non-custom single stratifications whose
    column_name matches (unique, first-appearance order)."""
    out: list[str] = []
    for sc in (strat_config or {}).values():
        sc = sc or {}
        if sc.get("type") != "single":
            continue
        if is_true(sc.get("is_custom_grouping")):
            continue
        if sc.get("column_name") != column_name:
            continue
        levels = sc.get("levels") or []
        if len(levels) == 0:
            continue
        for lvl in levels:
            if lvl not in out:
                out.append(lvl)
    return out


def clean_data(
    raw_data: pd.DataFrame,
    metric_config,
    strat_config,
    factor_recode_config=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clean and validate data.

    Returns ``(data, qa_log)`` — the R version returns
    ``list(data = ..., qa_log = ...)``.
    """
    factor_recode_config = factor_recode_config if factor_recode_config is not None else {}

    logger.info("Cleaning data...")
    qa_rows: list[tuple[str, str, str]] = []

    data = trim_character_columns(raw_data)

    factor_vars = factor_columns_from_metadata(strat_config, factor_recode_config)
    derived_factor_targets = factor_recode_target_columns(factor_recode_config)

    for var in factor_vars:
        if var not in data.columns:
            if var in derived_factor_targets:
                continue
            msg = f"Expected categorical column '{var}' not found in data"
            qa_rows.append(("factor_conversion", msg, "warning"))
            logger.warning(msg)
            continue

        expected_levels = expected_levels_for_column(strat_config, var)
        if expected_levels:
            col = data[var]
            actual_levels = sorted(
                {as_character_scalar(v) for v in col[col.notna()]} - {None}
            )
            unexpected = [lvl for lvl in actual_levels if lvl not in expected_levels]
            if unexpected:
                msg = f"{var}: unexpected levels found: {', '.join(unexpected)}"
                qa_rows.append(("factor_conversion", msg, "warning"))
                logger.warning(msg)

        # R factor(): levels = sorted unique values. pd.Categorical sorts the
        # inferred categories the same way (code-point order; R uses locale
        # collation — identical for single-case ASCII data).
        data[var] = pd.Categorical(data[var])
        logger.info(
            "Converted %s to factor (%d levels)", var, len(data[var].cat.categories)
        )

    n_rows = len(data)
    missing_cols = [
        (col_name, int(data[col_name].isna().sum()))
        for col_name in data.columns
        if int(data[col_name].isna().sum()) > 0
    ]
    if missing_cols:
        logger.info("Columns with missing data:")
        for col_name, n_miss in missing_cols:
            pct = r_round(100 * n_miss / n_rows, 1)
            logger.info("  %s: %d (%s%%)", col_name, n_miss, r_num_str(pct))
            qa_rows.append(
                (
                    "missing_data",
                    f"{col_name}: {n_miss} missing ({r_num_str(pct)}%)",
                    "warning" if pct > 50 else "info",
                )
            )

    logger.info("Clean data: %d rows x %d columns", n_rows, data.shape[1])

    qa_rows.append(
        (
            "clean_summary",
            f"Cleaned data: {n_rows} rows x {data.shape[1]} columns",
            "info",
        )
    )

    qa_log = pd.DataFrame(qa_rows, columns=_QA_COLUMNS)
    return data, qa_log
