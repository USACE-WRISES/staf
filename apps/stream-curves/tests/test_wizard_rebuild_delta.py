"""Delta-safe wizard rebuild — profiler.reconcile_tables_with_new_data.

The wizard's step-7 Build used to regenerate every workbook sheet from role
membership alone, silently discarding derived predictors, factor recodes, site
masks, custom/paired stratifications, curated allow-lists and function
mappings when re-run over a loaded project. These tests pin the reconcile
path that replaced it: existing tables are reconciled onto the freshly
compiled frame, preserving everything the workbook grid's Apply preserves.
"""

from __future__ import annotations

import pandas as pd
import pytest

from streamcurves import workbook_tables as wt
from streamcurves.cleaning import clean_data
from streamcurves.derive import derive_variables
from streamcurves.profiler import (
    build_config_tables_from_roles,
    reconcile_tables_with_new_data,
)
from streamcurves.workbook import (
    build_input_bundle_from_tables,
    ensure_workbook_sheet_columns,
    site_mask_tables_from_config,
)


# --------------------------------------------------------------------------- #
# fixture builders
# --------------------------------------------------------------------------- #
def _frame(**overrides) -> pd.DataFrame:
    base = {
        "site_id": ["S1", "S2", "S3", "S4", "S5", "S6"],
        "m1": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "m2": [2.0, 4.0, 6.0, 8.0, 10.0, 12.0],
        "p1": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        "s1": ["a", "a", "b", "b", "a", "b"],
        "cat": ["x", "y", "x", "y", "x", "y"],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def _asg(rows) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["column", "is_metric", "is_predictor", "is_stratifier", "family"]
    )


_BASE_ROLES = [
    {"column": "m1", "is_metric": True, "is_predictor": False,
     "is_stratifier": False, "family": "continuous"},
    {"column": "m2", "is_metric": True, "is_predictor": False,
     "is_stratifier": False, "family": "continuous"},
    {"column": "p1", "is_metric": False, "is_predictor": True,
     "is_stratifier": False, "family": None},
    {"column": "s1", "is_metric": False, "is_predictor": False,
     "is_stratifier": True, "family": None},
]


def _append_row(tables: dict, sheet: str, row: dict) -> dict:
    df = ensure_workbook_sheet_columns(tables.get(sheet), sheet)
    out = dict(tables)
    out[sheet] = ensure_workbook_sheet_columns(
        pd.concat([df, pd.DataFrame([row])], ignore_index=True), sheet
    )
    return out


def _key_for_column(tables: dict, sheet: str, key_col: str, column_name: str) -> str:
    df = tables[sheet]
    hits = [
        str(k) for k, c in zip(df[key_col], df["column_name"])
        if str(c) == column_name
    ]
    assert hits, f"no {sheet} row for column {column_name}"
    return hits[0]


def _strat_key_for_source(tables: dict, source_column: str) -> str:
    df = tables["stratifications"]
    hits = [
        str(k) for k, sc in zip(df["strat_key"], df["source_column"])
        if str(sc) == source_column
    ]
    assert hits, f"no stratification sourced on {source_column}"
    return hits[0]


def _curated_tables() -> dict:
    """Base tables from roles, then every kind of curated content the old
    from-scratch rebuild used to discard."""
    data = _frame()
    tables = build_config_tables_from_roles(data, _asg(_BASE_ROLES))

    # Curated metric settings: direction flip + an extra column (curve_form
    # rides as an extra, exactly as the two-sided curve workbooks carry it).
    m = tables["metrics"].copy()
    m.loc[m["column_name"] == "m1", "higher_is_better"] = "FALSE"
    m["curve_form"] = ["optimum" if c == "m1" else "" for c in m["column_name"]]
    tables["metrics"] = m

    # Curated (non-allow-all) predictor links: m1 -> p1 only.
    m1_key = _key_for_column(tables, "metrics", "metric_key", "m1")
    p1_key = _key_for_column(tables, "predictors", "predictor_key", "p1")
    tables["metric_predictors"] = ensure_workbook_sheet_columns(
        pd.DataFrame([{"metric_key": m1_key, "predictor_key": p1_key}]),
        "metric_predictors",
    )

    # Derived predictor (dormant-capable): sourced on m1 + p1.
    tables = _append_row(tables, "predictors", {
        "predictor_key": "dp1", "display_name": "Derived one",
        "column_name": "dp1_col", "type": "continuous", "derived": "TRUE",
        "derivation_method": "ratio", "source_columns": "m1, p1",
    })

    # Factor recode on a live categorical column.
    tables = _append_row(tables, "factor_recodes", {
        "recode_key": "rc1", "source_column": "cat", "target_column": "cat_rc",
        "target_level": "GroupX", "source_values": "x",
    })

    # Custom grouping on "cat" (prunable when "cat" vanishes).
    tables = _append_row(tables, "stratifications", {
        "strat_key": "cg1", "display_name": "cg1", "strat_type": "custom_group",
        "source_column": "cat", "source_data_type": "categorical",
        "derived_column_name": "cg1", "levels": "All", "min_group_size": "5",
    })
    tables = _append_row(tables, "strat_groups", {
        "strat_key": "cg1", "group_label": "All", "sort_order": "1",
        "source_values": "x|y",
    })

    # Paired stratification depending on the raw s1 strat.
    s1_key = _strat_key_for_source(tables, "s1")
    tables = _append_row(tables, "stratifications", {
        "strat_key": "pair1", "display_name": "pair1", "strat_type": "paired",
        "primary_strat_key": s1_key, "secondary_strat_key": "cg1",
    })

    # Function mappings sheet (reconcile must carry it verbatim).
    tables["function_mappings"] = ensure_workbook_sheet_columns(
        pd.DataFrame([{
            "discipline": "Hydrology", "function_label": "Reach inflow",
            "metric_key": m1_key, "metric_display_name": "m1",
        }]),
        "function_mappings",
    )
    return tables


def _run_pipeline(tables: dict) -> pd.DataFrame:
    """rebuild_app_from_tables' pure pipeline (views/rebuild.py) — must not raise."""
    bundle = build_input_bundle_from_tables(tables)
    cleaned, _ = clean_data(
        bundle["raw_data"], bundle["metric_config"], bundle["strat_config"],
        bundle["factor_recode_config"],
    )
    return derive_variables(
        cleaned, bundle["factor_recode_config"], bundle["predictor_config"],
        bundle["strat_config"],
    )


# --------------------------------------------------------------------------- #
# preservation
# --------------------------------------------------------------------------- #
def test_rebuild_over_loaded_preserves_everything():
    tables = _curated_tables()
    new_data = _frame(m1=[10.0, 20.0, 30.0, 40.0, 50.0, 60.0])  # re-compiled values
    out = reconcile_tables_with_new_data(tables, new_data, _asg(_BASE_ROLES))

    # The data sheet IS the new frame.
    assert list(out["data"]["m1"]) == [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]

    # Derived predictor row survives.
    p = out["predictors"]
    assert "dp1" in set(map(str, p["predictor_key"]))
    # Factor recode survives.
    assert "rc1" in set(map(str, out["factor_recodes"]["recode_key"]))
    # Custom group + its groups + the paired strat survive.
    sk = set(map(str, out["stratifications"]["strat_key"]))
    assert {"cg1", "pair1"} <= sk
    assert "cg1" in set(map(str, out["strat_groups"]["strat_key"]))
    # Curated allow-list survives exactly (one row, not allow-all).
    mp = out["metric_predictors"]
    assert len(mp) == 1
    # Curated metric settings + extra column survive.
    m = out["metrics"]
    m1 = m[m["column_name"] == "m1"].iloc[0]
    assert str(m1["higher_is_better"]).upper() == "FALSE"
    assert "curve_form" in m.columns and str(m1["curve_form"]) == "optimum"
    # Function mappings ride through verbatim.
    fm = out["function_mappings"]
    assert len(fm) == 1 and str(fm["function_label"].iloc[0]) == "Reach inflow"


def test_new_metric_gets_defaults_and_allow_all_links():
    tables = _curated_tables()
    new_data = _frame(m3=[7.0, 7.5, 8.0, 8.5, 9.0, 9.5])
    roles = _BASE_ROLES + [
        {"column": "m3", "is_metric": True, "is_predictor": False,
         "is_stratifier": False, "family": "continuous"},
    ]
    out = reconcile_tables_with_new_data(tables, new_data, _asg(roles))

    m = out["metrics"]
    assert "m3" in set(map(str, m["column_name"]))
    m3 = m[m["column_name"] == "m3"].iloc[0]
    assert str(m3["metric_family"]) == "continuous"
    m3_key = str(m3["metric_key"])
    # New metric linked to existing predictors; m1's curated single link intact.
    mp = out["metric_predictors"]
    m1_key = _key_for_column(out, "metrics", "metric_key", "m1")
    m1_rows = mp[mp["metric_key"].astype(str) == m1_key]
    assert len(m1_rows) == 1
    assert m3_key in set(map(str, mp["metric_key"]))


def test_dropped_metric_rows_disappear():
    tables = _curated_tables()
    m2_key = _key_for_column(tables, "metrics", "metric_key", "m2")
    # Give m2 a link row so the cascade is observable.
    tables = _append_row(tables, "metric_predictors", {
        "metric_key": m2_key,
        "predictor_key": _key_for_column(tables, "predictors", "predictor_key", "p1"),
    })
    new_data = _frame().drop(columns=["m2"])
    roles = [r for r in _BASE_ROLES if r["column"] != "m2"]
    out = reconcile_tables_with_new_data(tables, new_data, _asg(roles))

    assert "m2" not in set(map(str, out["metrics"]["column_name"]))
    assert m2_key not in set(map(str, out["metric_predictors"]["metric_key"]))


def test_dropped_stratifier_source_cascades_to_paired():
    tables = _curated_tables()
    s1_key = _strat_key_for_source(tables, "s1")
    new_data = _frame().drop(columns=["s1"])
    roles = [r for r in _BASE_ROLES if r["column"] != "s1"]
    out = reconcile_tables_with_new_data(tables, new_data, _asg(roles))

    sk = set(map(str, out["stratifications"]["strat_key"]))
    assert s1_key not in sk
    assert "pair1" not in sk  # paired dependent cascades with its primary
    assert "cg1" in sk  # unrelated custom group survives


def test_custom_group_with_vanished_source_is_pruned_and_build_succeeds():
    tables = _curated_tables()
    new_data = _frame().drop(columns=["cat"])
    out = reconcile_tables_with_new_data(tables, new_data, _asg(_BASE_ROLES))

    sk = set(map(str, out["stratifications"]["strat_key"]))
    assert "cg1" not in sk  # derive_variables raises on a dead custom_group
    assert "cg1" not in set(map(str, out["strat_groups"]["strat_key"]))
    # The dead recode (also sourced on "cat") is kept -- derive warns + skips --
    # and the full pipeline over the result must not raise.
    assert "rc1" in set(map(str, out["factor_recodes"]["recode_key"]))
    derived = _run_pipeline(out)
    assert len(derived) == 6


def test_dormant_derived_predictor_is_kept_and_pipeline_runs():
    tables = _curated_tables()
    # m1 (a dp1 source) vanishes: dp1 must survive dormant, not be pruned.
    new_data = _frame().drop(columns=["m1"])
    roles = [r for r in _BASE_ROLES if r["column"] != "m1"]
    out = reconcile_tables_with_new_data(tables, new_data, _asg(roles))

    assert "dp1" in set(map(str, out["predictors"]["predictor_key"]))
    derived = _run_pipeline(out)
    assert "dp1_col" not in derived.columns  # skipped, not materialized


def test_references_to_producible_columns_are_not_pruned():
    tables = _curated_tables()
    # A raw_single strat sourced on the recode's target column: producible, so
    # it must survive even though "cat_rc" is not a data column.
    tables = _append_row(tables, "stratifications", {
        "strat_key": "rc_strat", "display_name": "rc_strat",
        "strat_type": "raw_single", "source_column": "cat_rc",
        "source_data_type": "categorical",
    })
    out = reconcile_tables_with_new_data(tables, _frame(), _asg(_BASE_ROLES))
    assert "rc_strat" in set(map(str, out["stratifications"]["strat_key"]))

    # When "cat" (the recode's source) vanishes, the recode is dead, its target
    # is no longer producible, and the dependent strat prunes with it.
    new_data = _frame().drop(columns=["cat"])
    out2 = reconcile_tables_with_new_data(tables, new_data, _asg(_BASE_ROLES))
    assert "rc_strat" not in set(map(str, out2["stratifications"]["strat_key"]))


def test_two_sided_metrics_survive_the_rebuild():
    """A two-sided (optimum) curve carries higher_is_better = blank/None. The
    from-scratch builder defaults every metric to TRUE, which silently converts
    an optimum curve to monotone-increasing; the reconcile path must carry the
    blank through untouched, all the way into the built metric_config."""
    tables = _curated_tables()
    m = tables["metrics"].copy()
    m.loc[m["column_name"] == "m2", "higher_is_better"] = ""
    m.loc[m["column_name"] == "m2", "curve_form"] = "optimum"
    tables["metrics"] = m

    out = reconcile_tables_with_new_data(tables, _frame(), _asg(_BASE_ROLES))
    row = out["metrics"][out["metrics"]["column_name"] == "m2"].iloc[0]
    assert str(row["higher_is_better"]).strip() in ("", "None")
    assert str(row["curve_form"]) == "optimum"

    cfg = build_input_bundle_from_tables(out)["metric_config"]
    m2_key = _key_for_column(out, "metrics", "metric_key", "m2")
    assert cfg[m2_key]["higher_is_better"] is None
    # The one-sided sibling keeps its curated FALSE.
    m1_key = _key_for_column(out, "metrics", "metric_key", "m1")
    assert cfg[m1_key]["higher_is_better"] is False


def test_assignments_without_family_do_not_raise():
    tables = _curated_tables()
    new_data = _frame(m3=[1, 2, 3, 4, 5, 6])
    # Hydration shape: current_role_membership has no family column.
    roles = pd.DataFrame([
        {"column": c, "is_metric": c in ("m1", "m2", "m3"),
         "is_predictor": c == "p1", "is_stratifier": c == "s1"}
        for c in new_data.columns
    ])
    out = reconcile_tables_with_new_data(tables, new_data, roles)
    assert "m3" in set(map(str, out["metrics"]["column_name"]))


# --------------------------------------------------------------------------- #
# site masks
# --------------------------------------------------------------------------- #
def test_site_masks_fresh_config_wins_and_is_sanitized():
    tables = _curated_tables()
    new_data = _frame()
    cfg = {"masked_site_ids": [2, 99], "site_label_column": "nope"}
    out = reconcile_tables_with_new_data(
        tables, new_data, _asg(_BASE_ROLES), site_mask_config=cfg
    )
    expected = site_mask_tables_from_config(
        new_data, {"masked_site_ids": [2], "site_label_column": "site_id"}
    )
    assert list(out["site_masks"]["masked_sites"]) == [2]
    assert list(out["site_masks"]["site_label"]) == list(
        expected["site_masks"]["site_label"]
    )
    # Bad label column fell back to the frame's first column.
    assert str(out["site_mask_settings"]["site_label_column"].iloc[0]) == "site_id"


def test_site_masks_reset_when_config_none():
    tables = _curated_tables()
    tables["site_masks"] = ensure_workbook_sheet_columns(
        pd.DataFrame({"masked_sites": pd.Series([3], dtype="int64"),
                      "site_label": ["S3"]}),
        "site_masks",
    )
    tables["site_mask_settings"] = ensure_workbook_sheet_columns(
        pd.DataFrame({"site_label_column": ["site_id"]}), "site_mask_settings"
    )
    out = reconcile_tables_with_new_data(
        tables, _frame(), _asg(_BASE_ROLES), site_mask_config=None
    )
    # Positions into the old frame never transplant: masks reset, label carried.
    assert len(out["site_masks"]) == 0
    assert str(out["site_mask_settings"]["site_label_column"].iloc[0]) == "site_id"


# --------------------------------------------------------------------------- #
# fresh path unchanged
# --------------------------------------------------------------------------- #
def test_fresh_build_matches_from_scratch_builder():
    """The wizard's fresh-project branch still calls
    build_config_tables_from_roles directly; reconciling EMPTY tables onto the
    same frame must agree with it on the governed sheets."""
    data = _frame()
    fresh = build_config_tables_from_roles(data, _asg(_BASE_ROLES))
    via_reconcile = reconcile_tables_with_new_data({}, data, _asg(_BASE_ROLES))
    for sheet in ("metrics", "predictors", "stratifications"):
        a = fresh[sheet].reset_index(drop=True)
        b = via_reconcile[sheet][a.columns].reset_index(drop=True)
        pd.testing.assert_frame_equal(
            a.astype(str), b.astype(str), check_dtype=False
        )
