"""Workbook-table editing helpers — port of the pure table toolkit in
app/modules/mod_data_overview.R (editor_df_for_tab, apply_editor_df_to_tables,
add_*_row_to_tables, add_custom_grouping_to_tables, delete_rows_from_tables,
site-mask selection helpers). Shared by the workbook grid and (later) the
setup wizard. All functions take/return the ``tables`` dict of sheet
DataFrames and never touch app state.

Row indices are 1-based to match the R call sites (DT selections).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .workbook import (
    auto_pairwise_values,
    compact_chr,
    default_site_label_source_column,
    ensure_workbook_sheet_columns,
    parse_pipe_values,
    resolve_site_label_values,
    workbook_sheet_columns,
)


def _as_editor_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if v.is_integer():
            return str(int(v))
        return f"{v:g}"
    if v is pd.NaT or (isinstance(v, float) and pd.isna(v)):
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, (bool, np.bool_)):
        return "TRUE" if v else "FALSE"
    return str(v)


def metadata_editor_columns() -> list[str]:
    return list(workbook_sheet_columns()["metrics"]) + [
        "allowed_predictors",
        "allowed_stratifications",
    ]


def metadata_table_to_editor_df(df, sheet_name: str) -> pd.DataFrame:
    df = ensure_workbook_sheet_columns(df, sheet_name)
    out = pd.DataFrame({c: df[c].map(_as_editor_str) for c in df.columns})
    return out.reset_index(drop=True)


def collapse_pipe_text(values) -> str:
    vals = compact_chr(values)
    return "|".join(vals)


def collapse_pairwise_text(levels) -> str:
    levels = compact_chr(levels)
    if len(levels) < 2:
        return ""
    pairs = auto_pairwise_values(levels)
    return "|".join("~".join(p) for p in pairs)


def _metric_link_text(link_df: pd.DataFrame, metric_key: str, value_col: str) -> str:
    if len(link_df) == 0:
        return ""
    rows = link_df[link_df["metric_key"] == metric_key]
    if len(rows) == 0:
        return ""
    return collapse_pipe_text(rows[value_col])


def build_metrics_editor_df(tables: dict) -> pd.DataFrame:
    metrics_df = metadata_table_to_editor_df(tables.get("metrics"), "metrics")
    preds_df = metadata_table_to_editor_df(tables.get("metric_predictors"), "metric_predictors")
    strats_df = metadata_table_to_editor_df(
        tables.get("metric_stratifications"), "metric_stratifications"
    )
    if "metric_key" not in metrics_df.columns:
        metrics_df["metric_key"] = pd.Series([], dtype=object)
    metrics_df["allowed_predictors"] = [
        _metric_link_text(preds_df, mk, "predictor_key") for mk in metrics_df["metric_key"]
    ]
    metrics_df["allowed_stratifications"] = [
        _metric_link_text(strats_df, mk, "strat_key") for mk in metrics_df["metric_key"]
    ]
    for col in metadata_editor_columns():
        if col not in metrics_df.columns:
            metrics_df[col] = ""
    return metrics_df[metadata_editor_columns()]


def apply_metrics_editor_df(tables: dict, editor_df: pd.DataFrame) -> dict:
    editor_df = metadata_table_to_editor_df(editor_df, "metrics")
    for col in metadata_editor_columns():
        if col not in editor_df.columns:
            editor_df[col] = ""
    metric_cols = workbook_sheet_columns()["metrics"]
    metrics_df = editor_df[metric_cols]

    pred_rows, strat_rows = [], []
    for _, row in editor_df.iterrows():
        mk = row["metric_key"]
        if not mk:
            continue
        for i, pk in enumerate(parse_pipe_values(row["allowed_predictors"]), start=1):
            pred_rows.append({"metric_key": mk, "predictor_key": pk, "sort_order": i})
        for i, sk in enumerate(parse_pipe_values(row["allowed_stratifications"]), start=1):
            strat_rows.append({"metric_key": mk, "strat_key": sk, "sort_order": i})

    tables = dict(tables)
    tables["metrics"] = ensure_workbook_sheet_columns(metrics_df, "metrics")
    tables["metric_predictors"] = ensure_workbook_sheet_columns(
        pd.DataFrame(pred_rows), "metric_predictors"
    )
    tables["metric_stratifications"] = ensure_workbook_sheet_columns(
        pd.DataFrame(strat_rows), "metric_stratifications"
    )
    return tables


def editor_df_for_tab(tables: dict, tab_key: str) -> pd.DataFrame:
    if tab_key == "metrics":
        return build_metrics_editor_df(tables)
    sheet = {
        "stratifications": "stratifications",
        "predictors": "predictors",
        "factor_recodes": "factor_recodes",
        "custom_groups": "strat_groups",
        "site_masks": "site_masks",
    }.get(tab_key)
    if sheet is None:
        raise ValueError(f"Unsupported metadata tab: {tab_key}")
    return metadata_table_to_editor_df(tables.get(sheet), sheet)


def apply_editor_df_to_tables(tables: dict, tab_key: str, editor_df: pd.DataFrame) -> dict:
    if tab_key == "metrics":
        return apply_metrics_editor_df(tables, editor_df)
    sheet = {
        "stratifications": "stratifications",
        "predictors": "predictors",
        "factor_recodes": "factor_recodes",
        "custom_groups": "strat_groups",
        "site_masks": "site_masks",
    }.get(tab_key)
    if sheet is None:
        raise ValueError(f"Unsupported metadata tab: {tab_key}")
    tables = dict(tables)
    tables[sheet] = ensure_workbook_sheet_columns(editor_df, sheet)
    return tables


def next_unique_key(existing_keys, prefix: str) -> str:
    existing = set(compact_chr(existing_keys))
    idx = 1
    while True:
        candidate = f"{prefix}{idx}"
        if candidate not in existing:
            return candidate
        idx += 1


def first_numeric_column_name(data: pd.DataFrame | None) -> str:
    if data is None or data.shape[1] == 0:
        return ""
    for col in data.columns:
        if pd.api.types.is_numeric_dtype(data[col]):
            return str(col)
    return str(data.columns[0]) if len(data.columns) else ""


def first_categorical_column_name(data: pd.DataFrame | None) -> str:
    if data is None or data.shape[1] == 0:
        return ""
    for col in data.columns:
        s = data[col]
        if (
            isinstance(s.dtype, pd.CategoricalDtype)
            or pd.api.types.is_object_dtype(s)
            or pd.api.types.is_string_dtype(s)
            or pd.api.types.is_bool_dtype(s)
        ):
            return str(col)
    return str(data.columns[0]) if len(data.columns) else ""


def observed_values_for_column(data: pd.DataFrame | None, column_name: str) -> list[str]:
    if not column_name or data is None or column_name not in data.columns:
        return []
    vals = data[column_name].dropna().astype(str)
    vals = [v for v in vals if v.strip()]
    return sorted(set(vals))


# --------------------------------------------------------------------------- #
# Add-row helpers (R:811-954)
# --------------------------------------------------------------------------- #


def _blank_row(sheet: str) -> dict:
    return {c: "" for c in workbook_sheet_columns()[sheet]}


def add_metric_row_to_tables(tables: dict) -> dict:
    metrics_df = build_metrics_editor_df(tables)
    data = tables.get("data")
    new_key = next_unique_key(metrics_df["metric_key"], "new_metric_")
    new_row = {c: "" for c in metadata_editor_columns()}
    new_row.update(
        metric_key=new_key,
        display_name=new_key,
        column_name=first_numeric_column_name(data),
        metric_family="continuous",
        higher_is_better="TRUE",
        monotonic_linear="TRUE",
        preferred_transform="none",
        min_sample_size="10",
        best_subsets_allowed="TRUE",
        count_model="FALSE",
        stratification_mode="subset",
        include_in_summary="TRUE",
    )
    editor = pd.concat([metrics_df, pd.DataFrame([new_row])], ignore_index=True)
    return apply_metrics_editor_df(tables, editor)


def add_stratification_row_to_tables(tables: dict) -> dict:
    strat_df = metadata_table_to_editor_df(tables.get("stratifications"), "stratifications")
    data = tables.get("data")
    new_key = next_unique_key(strat_df["strat_key"], "new_strat_")
    source_column = first_categorical_column_name(data)
    is_num = (
        bool(source_column)
        and data is not None
        and source_column in data.columns
        and pd.api.types.is_numeric_dtype(data[source_column])
    )
    new_row = _blank_row("stratifications")
    new_row.update(
        strat_key=new_key,
        display_name=new_key,
        strat_type="raw_single",
        source_column=source_column,
        source_data_type="continuous" if is_num else "categorical",
        min_group_size="5",
    )
    tables = dict(tables)
    tables["stratifications"] = ensure_workbook_sheet_columns(
        pd.concat([strat_df, pd.DataFrame([new_row])], ignore_index=True), "stratifications"
    )
    return tables


def add_predictor_row_to_tables(tables: dict) -> dict:
    preds_df = metadata_table_to_editor_df(tables.get("predictors"), "predictors")
    data = tables.get("data")
    new_key = next_unique_key(preds_df["predictor_key"], "new_predictor_")
    new_row = _blank_row("predictors")
    new_row.update(
        predictor_key=new_key,
        display_name=new_key,
        column_name=first_numeric_column_name(data),
        type="continuous",
        derived="FALSE",
        derivation_method="none",
        missing_data_rule="error",
    )
    tables = dict(tables)
    tables["predictors"] = ensure_workbook_sheet_columns(
        pd.concat([preds_df, pd.DataFrame([new_row])], ignore_index=True), "predictors"
    )
    return tables


def add_factor_recode_row_to_tables(tables: dict) -> dict:
    recodes_df = metadata_table_to_editor_df(tables.get("factor_recodes"), "factor_recodes")
    data = tables.get("data")
    new_key = next_unique_key(recodes_df["recode_key"], "new_recode_")
    source_column = first_categorical_column_name(data)
    observed = observed_values_for_column(data, source_column)
    new_row = _blank_row("factor_recodes")
    new_row.update(
        recode_key=new_key,
        source_column=source_column,
        target_column=new_key,
        target_level="Group1",
        source_values=observed[0] if observed else "",
    )
    tables = dict(tables)
    tables["factor_recodes"] = ensure_workbook_sheet_columns(
        pd.concat([recodes_df, pd.DataFrame([new_row])], ignore_index=True), "factor_recodes"
    )
    return tables


def add_custom_grouping_to_tables(tables: dict) -> dict:
    strat_df = metadata_table_to_editor_df(tables.get("stratifications"), "stratifications")
    groups_df = metadata_table_to_editor_df(tables.get("strat_groups"), "strat_groups")
    data = tables.get("data")

    source_column = first_categorical_column_name(data)
    source_data_type = "categorical"
    if not source_column or data is None or source_column not in data.columns:
        source_column = first_numeric_column_name(data)
        source_data_type = "continuous"
    if not source_column or data is None or source_column not in data.columns:
        raise ValueError("No source column is available to create a custom grouping.")

    new_key = next_unique_key(strat_df["strat_key"], "custom_group_")

    strat_row = _blank_row("stratifications")
    strat_row.update(
        strat_key=new_key,
        display_name=new_key,
        strat_type="custom_group",
        source_column=source_column,
        source_data_type=source_data_type,
        derived_column_name=new_key,
        min_group_size="5",
        levels="All",
    )
    group_row = _blank_row("strat_groups")
    group_row.update(strat_key=new_key, group_label="All", sort_order="1")

    if source_data_type == "continuous":
        numeric = pd.to_numeric(data[source_column], errors="coerce").dropna()
        if len(numeric) == 0:
            raise ValueError(
                f"Column '{source_column}' has no numeric values for a continuous "
                "custom grouping."
            )
        lo, hi = float(numeric.min()), float(numeric.max())
        group_row["rule_expression"] = f">= {lo:.10g} & <= {hi:.10g}"
    else:
        observed = observed_values_for_column(data, source_column)
        if not observed:
            raise ValueError(
                f"Column '{source_column}' has no observed values for a categorical "
                "custom grouping."
            )
        group_row["source_values"] = collapse_pipe_text(observed)

    tables = dict(tables)
    tables["stratifications"] = ensure_workbook_sheet_columns(
        pd.concat([strat_df, pd.DataFrame([strat_row])], ignore_index=True), "stratifications"
    )
    tables["strat_groups"] = ensure_workbook_sheet_columns(
        pd.concat([groups_df, pd.DataFrame([group_row])], ignore_index=True), "strat_groups"
    )
    return tables


# --------------------------------------------------------------------------- #
# Delete-rows + site-mask helpers (R:956-1057, 716-782)
# --------------------------------------------------------------------------- #


def expanded_removed_strat_keys(stratifications_df, removed_keys) -> list[str]:
    removed = list(compact_chr(removed_keys))
    if not removed:
        return []
    df = metadata_table_to_editor_df(stratifications_df, "stratifications")
    while True:
        mask = df["strat_key"].isin(removed) | (
            (df["strat_type"] == "paired")
            & (df["primary_strat_key"].isin(removed) | df["secondary_strat_key"].isin(removed))
        )
        dependent = compact_chr(df.loc[mask, "strat_key"])
        updated = list(dict.fromkeys(removed + dependent))
        if len(updated) == len(removed):
            return updated
        removed = updated


def site_mask_label_column_from_tables(tables: dict) -> str:
    settings = ensure_workbook_sheet_columns(
        tables.get("site_mask_settings"), "site_mask_settings"
    )
    labels = compact_chr(settings["site_label_column"])
    label_column = labels[0] if labels else None
    data = tables.get("data")
    if data is None:
        data = pd.DataFrame()
    if not label_column or label_column not in data.columns:
        return default_site_label_source_column(data)
    return label_column


def site_mask_labels_for_ids(tables: dict, site_ids, label_column: str | None = None) -> list[str]:
    if label_column is None:
        label_column = site_mask_label_column_from_tables(tables)
    data = tables.get("data")
    if data is None:
        data = pd.DataFrame()
    ids = sorted(
        {int(i) for i in (site_ids or []) if not pd.isna(i) and 0 < int(i) <= len(data)}
    )
    if not ids:
        return []
    return resolve_site_label_values(data, ids, label_column)


def apply_site_mask_selection_to_tables(
    tables: dict, site_ids, label_column: str | None = None
) -> dict:
    data = tables.get("data")
    if data is None:
        data = pd.DataFrame()
    resolved = label_column or site_mask_label_column_from_tables(tables)
    if resolved not in data.columns:
        raise ValueError(
            f"Site label column '{resolved}' was not found in the data sheet."
        )
    ids = sorted({int(i) for i in (site_ids or []) if not pd.isna(i)})
    if ids and any(i <= 0 or i > len(data) for i in ids):
        raise ValueError("Selected masked site ids must be within the workbook data row range.")
    labels = site_mask_labels_for_ids(tables, ids, resolved)
    tables = dict(tables)
    tables["site_masks"] = ensure_workbook_sheet_columns(
        pd.DataFrame({"masked_sites": ids, "site_label": labels}), "site_masks"
    )
    tables["site_mask_settings"] = ensure_workbook_sheet_columns(
        pd.DataFrame({"site_label_column": [resolved]}), "site_mask_settings"
    )
    return tables


def delete_rows_from_tables(tables: dict, tab_key: str, selected_rows) -> dict:
    """``selected_rows`` are 1-based (DT/R convention)."""
    rows = sorted({int(r) for r in (selected_rows or [])})
    if not rows:
        return tables
    idx0 = [r - 1 for r in rows]
    tables = dict(tables)

    if tab_key == "metrics":
        editor = build_metrics_editor_df(tables)
        removed = compact_chr(editor["metric_key"].iloc[idx0])
        keep = editor.drop(index=[i for i in idx0 if i < len(editor)])
        tables["metrics"] = ensure_workbook_sheet_columns(
            keep[[c for c in keep.columns if c not in ("allowed_predictors", "allowed_stratifications")]],
            "metrics",
        )
        mp = metadata_table_to_editor_df(tables.get("metric_predictors"), "metric_predictors")
        tables["metric_predictors"] = ensure_workbook_sheet_columns(
            mp[~mp["metric_key"].isin(removed)], "metric_predictors"
        )
        ms = metadata_table_to_editor_df(
            tables.get("metric_stratifications"), "metric_stratifications"
        )
        tables["metric_stratifications"] = ensure_workbook_sheet_columns(
            ms[~ms["metric_key"].isin(removed)], "metric_stratifications"
        )
        return tables

    if tab_key == "stratifications":
        editor = metadata_table_to_editor_df(tables.get("stratifications"), "stratifications")
        removed = expanded_removed_strat_keys(editor, editor["strat_key"].iloc[idx0])
        tables["stratifications"] = ensure_workbook_sheet_columns(
            editor[~editor["strat_key"].isin(removed)], "stratifications"
        )
        ms = metadata_table_to_editor_df(
            tables.get("metric_stratifications"), "metric_stratifications"
        )
        tables["metric_stratifications"] = ensure_workbook_sheet_columns(
            ms[~ms["strat_key"].isin(removed)], "metric_stratifications"
        )
        gg = metadata_table_to_editor_df(tables.get("strat_groups"), "strat_groups")
        tables["strat_groups"] = ensure_workbook_sheet_columns(
            gg[~gg["strat_key"].isin(removed)], "strat_groups"
        )
        return tables

    if tab_key == "predictors":
        editor = metadata_table_to_editor_df(tables.get("predictors"), "predictors")
        removed = compact_chr(editor["predictor_key"].iloc[idx0])
        tables["predictors"] = ensure_workbook_sheet_columns(
            editor.drop(index=[i for i in idx0 if i < len(editor)]), "predictors"
        )
        mp = metadata_table_to_editor_df(tables.get("metric_predictors"), "metric_predictors")
        tables["metric_predictors"] = ensure_workbook_sheet_columns(
            mp[~mp["predictor_key"].isin(removed)], "metric_predictors"
        )
        return tables

    if tab_key == "factor_recodes":
        editor = metadata_table_to_editor_df(tables.get("factor_recodes"), "factor_recodes")
        tables["factor_recodes"] = ensure_workbook_sheet_columns(
            editor.drop(index=[i for i in idx0 if i < len(editor)]), "factor_recodes"
        )
        return tables

    if tab_key == "custom_groups":
        editor = metadata_table_to_editor_df(tables.get("strat_groups"), "strat_groups")
        removed_parents = compact_chr(editor["strat_key"].iloc[idx0])
        remaining = editor.drop(index=[i for i in idx0 if i < len(editor)])
        orphan = [k for k in removed_parents if k not in set(compact_chr(remaining["strat_key"]))]
        if orphan:
            removed = expanded_removed_strat_keys(tables.get("stratifications"), orphan)
            strat = metadata_table_to_editor_df(tables.get("stratifications"), "stratifications")
            tables["stratifications"] = ensure_workbook_sheet_columns(
                strat[~strat["strat_key"].isin(removed)], "stratifications"
            )
            ms = metadata_table_to_editor_df(
                tables.get("metric_stratifications"), "metric_stratifications"
            )
            tables["metric_stratifications"] = ensure_workbook_sheet_columns(
                ms[~ms["strat_key"].isin(removed)], "metric_stratifications"
            )
            remaining = remaining[~remaining["strat_key"].isin(removed)]
        tables["strat_groups"] = ensure_workbook_sheet_columns(remaining, "strat_groups")
        return tables

    if tab_key == "site_masks":
        editor = metadata_table_to_editor_df(tables.get("site_masks"), "site_masks")
        keep = editor.drop(index=[i for i in idx0 if i < len(editor)])
        remaining_ids = pd.to_numeric(keep["masked_sites"], errors="coerce").dropna().astype(int)
        return apply_site_mask_selection_to_tables(
            tables, remaining_ids.tolist(), site_mask_label_column_from_tables(tables)
        )

    raise ValueError(f"Unsupported metadata tab: {tab_key}")
