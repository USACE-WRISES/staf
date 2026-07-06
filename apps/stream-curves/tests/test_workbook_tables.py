"""Tests for streamcurves/workbook_tables.py (the workbook-grid table toolkit)."""

from __future__ import annotations

import pandas as pd
import pytest

from streamcurves import workbook_tables as wt
from streamcurves.workbook import read_input_workbook
from tests.golden_io import GOLDEN_DIR

FIXTURE = GOLDEN_DIR.parent / "fixtures" / "OSAM_summarydata.xlsx"


@pytest.fixture(scope="module")
def tables() -> dict:
    return read_input_workbook(FIXTURE)["metadata"]


def test_metrics_editor_roundtrip(tables):
    editor = wt.build_metrics_editor_df(tables)
    assert list(editor.columns) == wt.metadata_editor_columns()
    assert len(editor) == 22
    # pipe-joined links populated
    assert editor["allowed_stratifications"].str.contains(r"\|").any()
    out = wt.apply_metrics_editor_df(tables, editor)
    again = wt.build_metrics_editor_df(out)
    pd.testing.assert_frame_equal(editor, again)


def test_add_and_delete_metric_row(tables):
    added = wt.add_metric_row_to_tables(tables)
    editor = wt.build_metrics_editor_df(added)
    assert len(editor) == 23
    assert "new_metric_1" in set(editor["metric_key"])
    # 1-based delete of the new last row
    removed = wt.delete_rows_from_tables(added, "metrics", [23])
    assert len(wt.build_metrics_editor_df(removed)) == 22
    # link sheets keep no orphan rows for the removed key
    mp = removed["metric_predictors"]
    assert not (mp["metric_key"] == "new_metric_1").any()


def test_add_stratification_predictor_recode_rows(tables):
    t = wt.add_stratification_row_to_tables(tables)
    t = wt.add_predictor_row_to_tables(t)
    t = wt.add_factor_recode_row_to_tables(t)
    assert "new_strat_1" in set(t["stratifications"]["strat_key"].astype(str))
    assert "new_predictor_1" in set(t["predictors"]["predictor_key"].astype(str))
    assert "new_recode_1" in set(t["factor_recodes"]["recode_key"].astype(str))


def test_add_custom_grouping(tables):
    t = wt.add_custom_grouping_to_tables(tables)
    strat = t["stratifications"]
    row = strat[strat["strat_key"] == "custom_group_1"]
    assert len(row) == 1
    assert row["strat_type"].iloc[0] == "custom_group"
    groups = t["strat_groups"]
    grow = groups[groups["strat_key"] == "custom_group_1"]
    assert len(grow) == 1 and grow["group_label"].iloc[0] == "All"


def test_expanded_removed_strat_keys_pulls_paired_dependents():
    strat = pd.DataFrame(
        {
            "strat_key": ["a", "b", "ab"],
            "strat_type": ["raw_single", "raw_single", "paired"],
            "primary_strat_key": ["", "", "a"],
            "secondary_strat_key": ["", "", "b"],
        }
    )
    out = wt.expanded_removed_strat_keys(strat, ["a"])
    assert set(out) == {"a", "ab"}


def test_delete_stratification_cascades(tables):
    strat = wt.metadata_table_to_editor_df(tables["stratifications"], "stratifications")
    # pick a strat referenced by metric_stratifications
    target = tables["metric_stratifications"]["strat_key"].iloc[0]
    row_1based = int(strat.index[strat["strat_key"] == target][0]) + 1
    out = wt.delete_rows_from_tables(tables, "stratifications", [row_1based])
    assert target not in set(out["stratifications"]["strat_key"].astype(str))
    assert not (out["metric_stratifications"]["strat_key"] == target).any()


def test_site_mask_selection_roundtrip(tables):
    out = wt.apply_site_mask_selection_to_tables(tables, [1, 3])
    sm = out["site_masks"]
    assert list(pd.to_numeric(sm["masked_sites"]).astype(int)) == [1, 3]
    assert len(sm["site_label"]) == 2
    # deleting one mask row (1-based) keeps the other
    out2 = wt.delete_rows_from_tables(out, "site_masks", [1])
    sm2 = out2["site_masks"]
    assert list(pd.to_numeric(sm2["masked_sites"]).astype(int)) == [3]


def test_next_unique_key_and_first_columns():
    assert wt.next_unique_key(["x1", "p2"], "p") == "p1"
    assert wt.next_unique_key(["p1"], "p") == "p2"
    df = pd.DataFrame({"name": ["a"], "val": [1.0]})
    assert wt.first_numeric_column_name(df) == "val"
    assert wt.first_categorical_column_name(df) == "name"
