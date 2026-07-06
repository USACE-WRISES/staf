"""Pure-helper tests for the manual curve editor (views/ref_curve.py) and the
phase-4 stratified threshold table (views/phase4.py)."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from streamcurves.curves import build_reference_curve
from views.phase4 import build_phase4_threshold_table
from views.ref_curve import (
    reference_curve_editor_move_row,
    reference_curve_editor_points_from_table,
    reference_curve_editor_seed_points,
    reference_curve_editor_table_df,
)

METRIC_CONFIG = {
    "m": {
        "column_name": "m",
        "display_name": "Metric M",
        "units": "u",
        "higher_is_better": True,
    }
}


def _sample_points() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "point_order": [1, 2, 3],
            "metric_value": [0.0, 5.0, 10.0],
            "index_score": [0.0, 0.5, 1.0],
        }
    )


class TestEditorTableDf:
    def test_round_trips_points(self):
        tbl = reference_curve_editor_table_df(_sample_points())
        assert list(tbl.columns) == ["point_order", "metric_value", "index_score"]
        assert list(tbl["point_order"]) == [1, 2, 3]
        assert list(tbl["metric_value"]) == [0.0, 5.0, 10.0]

    def test_none_gives_empty(self):
        tbl = reference_curve_editor_table_df(None)
        assert len(tbl) == 0
        assert list(tbl.columns) == ["point_order", "metric_value", "index_score"]

    def test_renumbers_after_normalize_sort(self):
        scrambled = pd.DataFrame(
            {
                "point_order": [2, 1],
                "metric_value": [10.0, 0.0],
                "index_score": [1.0, 0.0],
            }
        )
        tbl = reference_curve_editor_table_df(scrambled)
        assert list(tbl["point_order"]) == [1, 2]
        assert list(tbl["metric_value"]) == [0.0, 10.0]


class TestPointsFromTable:
    def test_coerces_non_numeric_to_nan(self):
        tbl = pd.DataFrame(
            {
                "point_order": [1, 2],
                "metric_value": ["3.5", "abc"],
                "index_score": [0.2, ""],
            }
        )
        pts = reference_curve_editor_points_from_table(tbl)
        assert pts["metric_value"].iloc[0] == 3.5
        assert math.isnan(pts["metric_value"].iloc[1])
        assert math.isnan(pts["index_score"].iloc[1])
        assert list(pts["point_order"]) == [1, 2]

    def test_empty_and_none(self):
        assert len(reference_curve_editor_points_from_table(None)) == 0
        assert len(reference_curve_editor_points_from_table(pd.DataFrame())) == 0


class TestMoveRow:
    def test_move_down_swaps_and_renumbers(self):
        tbl = reference_curve_editor_table_df(_sample_points())
        res = reference_curve_editor_move_row(tbl, 0, "down")
        assert res["status"] == "moved"
        assert res["changed"] is True
        assert res["selected_row"] == 1
        assert list(res["table_df"]["metric_value"]) == [5.0, 0.0, 10.0]
        assert list(res["table_df"]["point_order"]) == [1, 2, 3]

    def test_move_up_from_top_is_boundary(self):
        tbl = reference_curve_editor_table_df(_sample_points())
        res = reference_curve_editor_move_row(tbl, 0, "up")
        assert res["status"] == "boundary"
        assert res["changed"] is False
        assert res["selected_row"] == 0

    def test_move_down_from_bottom_is_boundary(self):
        tbl = reference_curve_editor_table_df(_sample_points())
        res = reference_curve_editor_move_row(tbl, 2, "down")
        assert res["status"] == "boundary"

    def test_no_selection(self):
        tbl = reference_curve_editor_table_df(_sample_points())
        for sel in (None, -1, 99, "x"):
            res = reference_curve_editor_move_row(tbl, sel, "up")
            assert res["status"] == "no_selection"
            assert res["selected_row"] is None

    def test_empty_table(self):
        res = reference_curve_editor_move_row(
            reference_curve_editor_table_df(None), 0, "up"
        )
        assert res["status"] == "empty"

    def test_bad_direction_raises(self):
        with pytest.raises(ValueError):
            reference_curve_editor_move_row(
                reference_curve_editor_table_df(_sample_points()), 0, "sideways"
            )


class TestSeedPoints:
    def test_prefers_stored_curve_points(self):
        result = {"curve_points": _sample_points(), "curve_row": None}
        pts = reference_curve_editor_seed_points(result, True)
        assert list(pts["metric_value"]) == [0.0, 5.0, 10.0]

    def test_falls_back_to_curve_row(self):
        data = pd.DataFrame({"m": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]})
        built = build_reference_curve(data, "m", METRIC_CONFIG)
        result = {"curve_points": None, "curve_row": built["curve_row"]}
        pts = reference_curve_editor_seed_points(result, True)
        assert len(pts) >= 2
        assert list(pts.columns[:3]) == ["point_order", "metric_value", "index_score"]

    def test_empty_when_nothing_available(self):
        assert len(reference_curve_editor_seed_points({}, True)) == 0
        assert len(reference_curve_editor_seed_points(None, True)) == 0


class TestPhase4ThresholdTable:
    def test_columns_per_stratum_and_na_for_insufficient(self):
        data_a = pd.DataFrame({"m": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]})
        built = build_reference_curve(data_a, "m", METRIC_CONFIG, stratum_label="A")
        row_a = built["curve_row"]
        row_b = row_a.copy()
        row_b["stratum"] = "B"
        row_b["curve_status"] = "insufficient_data"
        curve_rows = pd.concat([row_a, row_b], ignore_index=True)

        tbl = build_phase4_threshold_table(curve_rows)
        assert list(tbl.columns) == ["Category", "Score Range", "A", "B"]
        assert list(tbl["Category"]) == ["Functioning", "At-Risk", "Not Functioning"]
        assert list(tbl["B"]) == ["N/A"] * 3
        assert all(v != "N/A" for v in tbl["A"])
