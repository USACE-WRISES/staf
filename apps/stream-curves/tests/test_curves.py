"""Tests for streamcurves.curves (port of R/10_reference_curves.R).

Hand-computed unit cases plus golden-fixture parity tests (against the real R
run exported by scripts/export_golden.R) that skip when the fixtures under
tests/golden/ are not present.
"""

import logging
import math

import numpy as np
import pandas as pd
import pytest

import golden_io
from streamcurves import curves as rc

MC = {
    "epi": {
        "column_name": "epi_sub",
        "display_name": "Episodic substrate",
        "units": "m",
        "metric_family": "instream",
        "higher_is_better": True,
    },
    "fines": {
        "column_name": "pct_fines",
        "display_name": "Percent fines",
        "units": "%",
        "metric_family": "instream",
        "higher_is_better": False,
    },
}

# Reference values seeding q25=7, q75=14 (type-7 quantiles hit sorted indexes).
HIGHER_VALUES = [0.0, 7.0, 7.0, 14.0, 14.0]
# Reference values seeding q25=10, q75=20.
LOWER_VALUES = [10.0, 10.0, 20.0, 20.0, 30.0]

ROW_COLUMNS = [
    "metric", "display_name", "n_reference", "median_val", "mean_val", "sd_val",
    "min_val", "max_val", "q25", "q75", "iqr",
    "functioning_min", "functioning_max", "at_risk_min", "at_risk_max",
    "not_functioning_min", "not_functioning_max", "higher_is_better",
    "curve_point1_x", "curve_point1_y", "curve_point2_x", "curve_point2_y",
    "curve_point3_x", "curve_point3_y",
    "curve_n_points", "curve_source", "score_30_metric", "score_70_metric",
    "score_30_crossings", "score_70_crossings", "score_100_crossings",
    "score_30_crossing_count", "score_70_crossing_count", "score_100_crossing_count",
    "score_30_crossings_display", "score_70_crossings_display", "score_100_crossings_display",
    "functioning_ranges", "at_risk_ranges", "not_functioning_ranges",
    "functioning_ranges_display", "at_risk_ranges_display", "not_functioning_ranges_display",
    "curve_status", "stratum", "curve_points",
]


def data_frame(**cols):
    return pd.DataFrame(cols)


# --------------------------------------------------------------------------- #
# normalize_reference_curve_points
# --------------------------------------------------------------------------- #


class TestNormalizePoints:
    def test_none_gives_typed_empty(self):
        out = rc.normalize_reference_curve_points(None)
        assert list(out.columns) == ["point_order", "metric_value", "index_score"]
        assert len(out) == 0
        assert out["point_order"].dtype == np.int64
        assert out["metric_value"].dtype == float

    def test_x_y_aliases(self):
        out = rc.normalize_reference_curve_points({"x": [1.0, 2.0], "y": [0.0, 1.0]})
        assert out["metric_value"].tolist() == [1.0, 2.0]
        assert out["index_score"].tolist() == [0.0, 1.0]
        assert out["point_order"].tolist() == [1, 2]

    def test_explicit_columns_beat_aliases(self):
        out = rc.normalize_reference_curve_points(
            {"x": [9.0], "metric_value": [1.0], "index_score": [0.5]}
        )
        assert out["metric_value"].tolist() == [1.0]

    def test_missing_value_columns_gives_empty(self):
        assert len(rc.normalize_reference_curve_points({"a": [1]})) == 0
        assert len(rc.normalize_reference_curve_points({})) == 0

    def test_missing_point_order_is_row_number(self):
        out = rc.normalize_reference_curve_points(
            {"metric_value": [5.0, 3.0], "index_score": [0.1, 0.2]}
        )
        # no reordering: point_order was assigned 1..n
        assert out["metric_value"].tolist() == [5.0, 3.0]

    def test_uncoercible_point_order_falls_back_to_position(self):
        out = rc.normalize_reference_curve_points(
            {
                "point_order": ["2", "abc", 1],
                "metric_value": [10.0, 20.0, 30.0],
                "index_score": [0.1, 0.2, 0.3],
            }
        )
        # "abc" -> original position 2; stable sort by (order, original):
        # (1, row3), (2, row1), (2, row2)
        assert out["metric_value"].tolist() == [30.0, 10.0, 20.0]
        assert out["point_order"].tolist() == [1, 2, 3]

    def test_float_point_order_truncates_like_r_as_integer(self):
        out = rc.normalize_reference_curve_points(
            {"point_order": [2.7, 1.2], "metric_value": [1.0, 2.0], "index_score": [0.1, 0.2]}
        )
        assert out["metric_value"].tolist() == [2.0, 1.0]  # 2.7 -> 2, 1.2 -> 1

    def test_duplicate_point_order_stable_by_original_position(self):
        out = rc.normalize_reference_curve_points(
            {"point_order": [1, 1, 1], "metric_value": [7.0, 8.0, 9.0], "index_score": [0, 0, 0]}
        )
        assert out["metric_value"].tolist() == [7.0, 8.0, 9.0]

    def test_drops_rows_where_both_values_na(self):
        out = rc.normalize_reference_curve_points(
            {
                "metric_value": [1.0, np.nan, np.nan],
                "index_score": [np.nan, np.nan, 3.0],
            }
        )
        assert len(out) == 2  # only the both-NA middle row dropped
        assert out["point_order"].tolist() == [1, 2]
        assert math.isnan(out["index_score"].iloc[0])
        assert math.isnan(out["metric_value"].iloc[1])

    def test_bad_type_raises_r_message(self):
        with pytest.raises(ValueError, match="Curve points must be NULL, a data frame, or a list."):
            rc.normalize_reference_curve_points("nope")

    def test_dataframe_passthrough_and_renumbering(self):
        df = pd.DataFrame({"point_order": [10, 5], "metric_value": [2.0, 1.0], "index_score": [1.0, 0.0]})
        out = rc.normalize_reference_curve_points(df)
        assert out["metric_value"].tolist() == [1.0, 2.0]
        assert out["point_order"].tolist() == [1, 2]


# --------------------------------------------------------------------------- #
# validate_reference_curve_points — each error, in R's order/gating
# --------------------------------------------------------------------------- #


class TestValidate:
    def test_too_few_points(self):
        res = rc.validate_reference_curve_points({"metric_value": [1.0], "index_score": [0.5]}, True)
        assert res["valid"] is False
        assert res["errors"] == ["At least 2 curve points are required."]

    def test_too_few_points_and_bounds_both_fire(self):
        # The bounds check is NOT gated on earlier errors in R.
        res = rc.validate_reference_curve_points({"metric_value": [5.0], "index_score": [2.0]}, True)
        assert res["errors"] == [
            "At least 2 curve points are required.",
            "Index score values must be between 0 and 1.",
        ]

    def test_na_values(self):
        res = rc.validate_reference_curve_points(
            {"metric_value": [0.0, np.nan], "index_score": [0.0, 1.0]}, True
        )
        assert res["errors"] == ["Metric score and index score must be numeric for every point."]

    def test_decreasing_metric_value(self):
        res = rc.validate_reference_curve_points(
            {"metric_value": [0.0, 2.0, 1.0], "index_score": [0.0, 0.5, 1.0]}, True
        )
        assert res["errors"] == [
            "Metric score must be non-decreasing from top to bottom. "
            "Equal consecutive values are allowed."
        ]

    def test_equal_consecutive_x_allowed_vertical_step_valid(self):
        res = rc.validate_reference_curve_points(
            {"metric_value": [0.0, 0.0, 5.0], "index_score": [0.0, 0.7, 1.0]}, True
        )
        assert res["valid"] is True
        assert res["errors"] == []

    def test_index_out_of_bounds(self):
        res = rc.validate_reference_curve_points(
            {"metric_value": [0.0, 1.0], "index_score": [-0.2, 1.0]}, True
        )
        assert res["errors"] == ["Index score values must be between 0 and 1."]

    def test_multi_crossing_030(self):
        res = rc.validate_reference_curve_points(
            {
                "metric_value": [0, 1, 2, 3, 4, 5, 6],
                "index_score": [0.0, 0.5, 0.1, 0.5, 0.1, 0.5, 1.0],
            },
            True,
        )
        assert res["errors"] == ["Manual curves can cross index score 0.30 at most twice."]

    def test_multi_crossing_070(self):
        res = rc.validate_reference_curve_points(
            {
                "metric_value": [0, 1, 2, 3, 4, 5, 6],
                "index_score": [1.0, 0.5, 0.9, 0.5, 0.9, 0.5, 0.0],
            },
            False,
        )
        assert res["errors"] == ["Manual curves can cross index score 0.70 at most twice."]

    def test_span_error_when_min_above_030(self):
        res = rc.validate_reference_curve_points(
            {"metric_value": [0.0, 1.0], "index_score": [0.4, 0.9]}, True
        )
        assert res["errors"] == ["Curve points must span index scores 0.30 and 0.70."]

    def test_span_error_when_max_below_070(self):
        res = rc.validate_reference_curve_points(
            {"metric_value": [0.0, 1.0], "index_score": [0.0, 0.5]}, True
        )
        assert res["errors"] == ["Curve points must span index scores 0.30 and 0.70."]

    def test_valid_curve(self):
        res = rc.validate_reference_curve_points(
            {"metric_value": [0.0, 10.0], "index_score": [0.0, 1.0]}, True
        )
        assert res["valid"] is True
        assert res["errors"] == []
        assert res["points"]["point_order"].tolist() == [1, 2]

    def test_higher_is_better_is_ignored(self):
        pts = {"metric_value": [0.0, 10.0], "index_score": [1.0, 0.0]}  # falling curve
        assert rc.validate_reference_curve_points(pts, True)["valid"] is True
        assert rc.validate_reference_curve_points(pts, False)["valid"] is True


# --------------------------------------------------------------------------- #
# reference_curve_threshold_crossings
# --------------------------------------------------------------------------- #


class TestThresholdCrossings:
    def test_fewer_than_two_points(self):
        assert rc.reference_curve_threshold_crossings(None, 0.30) == []
        assert (
            rc.reference_curve_threshold_crossings(
                {"metric_value": [1.0], "index_score": [0.5]}, 0.30
            )
            == []
        )

    def test_strict_flip_interpolates(self):
        pts = {"metric_value": [0.0, 4.0], "index_score": [0.0, 1.0]}
        assert rc.reference_curve_threshold_crossings(pts, 0.5) == [2.0]

    def test_vertical_segment_contributes_x1(self):
        pts = {"metric_value": [1.0, 1.0], "index_score": [0.0, 1.0]}
        assert rc.reference_curve_threshold_crossings(pts, 0.5) == [1.0]

    def test_touching_run_rising_takes_run_start(self):
        pts = {"metric_value": [0.0, 1.0, 2.0, 3.0], "index_score": [0.0, 0.3, 0.3, 0.8]}
        assert rc.reference_curve_threshold_crossings(pts, 0.3) == [1.0]

    def test_touching_run_falling_takes_run_end(self):
        pts = {"metric_value": [0.0, 1.0, 2.0, 3.0], "index_score": [0.8, 0.3, 0.3, 0.0]}
        assert rc.reference_curve_threshold_crossings(pts, 0.3) == [2.0]

    def test_one_sided_touch_adds_nothing(self):
        pts = {"metric_value": [0.0, 1.0, 2.0, 3.0], "index_score": [0.0, 0.3, 0.3, 0.1]}
        assert rc.reference_curve_threshold_crossings(pts, 0.3) == []

    def test_run_at_curve_start_adds_nothing(self):
        pts = {"metric_value": [0.0, 1.0], "index_score": [0.3, 0.8]}
        assert rc.reference_curve_threshold_crossings(pts, 0.3) == []

    def test_run_at_curve_end_adds_nothing(self):
        # standard higher-is-better seed vs target 1.0: the trailing plateau
        # has no non-zero right neighbour
        pts = {
            "metric_value": [0.0, 3.0, 7.0, 14.0, 16.1],
            "index_score": [0.0, 0.3, 0.7, 1.0, 1.0],
        }
        assert rc.reference_curve_threshold_crossings(pts, 1.0) == []

    def test_single_point_touch_between_opposite_sides(self):
        pts = {"metric_value": [0.0, 2.0, 4.0], "index_score": [0.0, 0.3, 1.0]}
        assert rc.reference_curve_threshold_crossings(pts, 0.3) == [2.0]

    def test_multiple_crossings_sorted(self):
        pts = {
            "metric_value": [0, 1, 2, 3, 4, 5, 6],
            "index_score": [0.0, 0.5, 0.1, 0.5, 0.1, 0.5, 1.0],
        }
        out = rc.reference_curve_threshold_crossings(pts, 0.3)
        assert out == pytest.approx([0.6, 1.5, 2.5, 3.5, 4.5], abs=1e-9)

    def test_na_relations_break_runs_and_skip_flips(self):
        pts = {"metric_value": [0.0, 1.0, 2.0], "index_score": [0.0, np.nan, 1.0]}
        assert rc.reference_curve_threshold_crossings(pts, 0.5) == []


# --------------------------------------------------------------------------- #
# reference_curve_metric_at_score
# --------------------------------------------------------------------------- #


class TestMetricAtScore:
    def test_fewer_than_two_points_is_nan(self):
        assert math.isnan(rc.reference_curve_metric_at_score(None, 0.3))

    def test_basic_interpolation(self):
        pts = {"metric_value": [0.0, 10.0], "index_score": [0.0, 1.0]}
        assert rc.reference_curve_metric_at_score(pts, 0.3, prefer="left") == pytest.approx(3.0)
        assert rc.reference_curve_metric_at_score(pts, 0.3, prefer="right") == pytest.approx(3.0)

    def test_flat_segment_prefer_left_right(self):
        pts = {"metric_value": [0.0, 2.0, 4.0, 6.0], "index_score": [0.0, 0.5, 0.5, 1.0]}
        assert rc.reference_curve_metric_at_score(pts, 0.5, prefer="left") == 2.0
        assert rc.reference_curve_metric_at_score(pts, 0.5, prefer="right") == 4.0

    def test_target_not_reached_is_nan(self):
        pts = {"metric_value": [0.0, 1.0], "index_score": [0.0, 0.2]}
        assert math.isnan(rc.reference_curve_metric_at_score(pts, 0.5))

    def test_default_prefer_is_left(self):
        pts = {"metric_value": [0.0, 2.0, 4.0, 6.0], "index_score": [0.0, 0.5, 0.5, 1.0]}
        assert rc.reference_curve_metric_at_score(pts, 0.5) == 2.0

    def test_invalid_prefer_raises(self):
        pts = {"metric_value": [0.0, 1.0], "index_score": [0.0, 1.0]}
        with pytest.raises(ValueError):
            rc.reference_curve_metric_at_score(pts, 0.5, prefer="middle")

    def test_vertical_segment_hits_target(self):
        pts = {"metric_value": [0.0, 0.0, 5.0], "index_score": [0.0, 0.7, 1.0]}
        assert rc.reference_curve_metric_at_score(pts, 0.3, prefer="left") == 0.0
        assert rc.reference_curve_metric_at_score(pts, 0.7, prefer="left") == 0.0


# --------------------------------------------------------------------------- #
# Interval / text helpers
# --------------------------------------------------------------------------- #


class TestIntervalHelpers:
    def test_merge_overlapping(self):
        out = rc.reference_curve_merge_intervals({"min": [0.0, 0.5], "max": [1.0, 2.0]})
        assert out["min"].tolist() == [0.0]
        assert out["max"].tolist() == [2.0]

    def test_merge_within_tolerance(self):
        out = rc.reference_curve_merge_intervals({"min": [0.0, 1.0 + 1e-10], "max": [1.0, 2.0]})
        assert len(out) == 1

    def test_disjoint_stay_separate(self):
        out = rc.reference_curve_merge_intervals({"min": [0.0, 1.1], "max": [1.0, 2.0]})
        assert out["min"].tolist() == [0.0, 1.1]

    def test_swapped_min_max_normalized(self):
        out = rc.reference_curve_as_interval_tbl({"min": [2.0], "max": [1.0]})
        assert out["min"].tolist() == [1.0]
        assert out["max"].tolist() == [2.0]

    def test_unusable_inputs_empty(self):
        assert len(rc.reference_curve_as_interval_tbl(None)) == 0
        assert len(rc.reference_curve_as_interval_tbl("nope")) == 0
        assert len(rc.reference_curve_as_interval_tbl({"lo": [1]})) == 0
        assert len(rc.reference_curve_merge_intervals({"min": [np.nan], "max": [1.0]})) == 0

    def test_format_number(self):
        assert rc.reference_curve_format_number(None) == "N/A"
        assert rc.reference_curve_format_number(float("nan")) == "N/A"
        assert rc.reference_curve_format_number([]) == "N/A"
        assert rc.reference_curve_format_number(3) == "3.00"
        assert rc.reference_curve_format_number(3.456) == "3.46"
        assert rc.reference_curve_format_number(float("inf")) == "Inf"

    def test_unique_numeric_keeps_first_occurrence_order(self):
        out = rc.reference_curve_unique_numeric([5.0, 1.0, 5.00000000001, 2.0, np.nan, np.inf])
        assert out == [5.0, 1.0, 2.0]

    def test_crossings_text(self):
        assert rc.reference_curve_crossings_text([]) == "N/A"
        assert rc.reference_curve_crossings_text(None) == "N/A"
        assert rc.reference_curve_crossings_text([1.234, 5.0]) == "1.23, 5.00"

    def test_interval_ranges_text(self):
        assert rc.reference_curve_interval_ranges_text(None) == "N/A"
        assert (
            rc.reference_curve_interval_ranges_text({"min": [0.0, 3.0], "max": [1.0, 3.0]})
            == "0.00 - 1.00, 3.00"
        )

    def test_score_band_for_value(self):
        assert rc.reference_curve_score_band_for_value(0.9) == "functioning"
        assert rc.reference_curve_score_band_for_value(0.7) == "functioning"
        assert rc.reference_curve_score_band_for_value(0.5) == "at_risk"
        assert rc.reference_curve_score_band_for_value(0.3) == "at_risk"
        assert rc.reference_curve_score_band_for_value(0.1) == "not_functioning"
        assert rc.reference_curve_score_band_for_value(float("nan")) is None
        assert rc.reference_curve_score_band_for_value(float("inf")) is None

    def test_segment_breaks(self):
        out = rc.reference_curve_segment_breaks(0.0, 0.0, 10.0, 1.0)
        assert out == pytest.approx([0.0, 3.0, 7.0, 10.0], abs=1e-9)
        # flat segment: no threshold breaks
        assert rc.reference_curve_segment_breaks(0.0, 0.5, 10.0, 0.5) == [0.0, 10.0]


# --------------------------------------------------------------------------- #
# reference_curve_band_intervals
# --------------------------------------------------------------------------- #


class TestBandIntervals:
    def test_fewer_than_two_points_all_empty(self):
        out = rc.reference_curve_band_intervals(None)
        assert set(out) == {"functioning", "at_risk", "not_functioning"}
        assert all(len(v) == 0 for v in out.values())

    def test_simple_rising_curve(self):
        pts = {"metric_value": [0.0, 10.0], "index_score": [0.0, 1.0]}
        out = rc.reference_curve_band_intervals(pts)
        assert out["not_functioning"]["min"].tolist() == pytest.approx([0.0], abs=1e-9)
        assert out["not_functioning"]["max"].tolist() == pytest.approx([3.0], abs=1e-9)
        assert out["at_risk"]["min"].tolist() == pytest.approx([3.0], abs=1e-9)
        assert out["at_risk"]["max"].tolist() == pytest.approx([7.0], abs=1e-9)
        assert out["functioning"]["min"].tolist() == pytest.approx([7.0], abs=1e-9)
        assert out["functioning"]["max"].tolist() == pytest.approx([10.0], abs=1e-9)

    def test_multi_crossing_curve_splits_and_merges(self):
        pts = {"metric_value": [0.0, 1.0, 2.0, 3.0, 4.0], "index_score": [0.0, 0.5, 0.1, 0.8, 1.0]}
        out = rc.reference_curve_band_intervals(pts)
        b23 = 2.0 + 0.2 / 0.7  # 0.30 break in segment 3
        b27 = 2.0 + 0.6 / 0.7  # 0.70 break in segment 3
        assert out["functioning"]["min"].tolist() == pytest.approx([b27], abs=1e-9)
        assert out["functioning"]["max"].tolist() == pytest.approx([4.0], abs=1e-9)
        assert out["at_risk"]["min"].tolist() == pytest.approx([0.6, b23], abs=1e-9)
        assert out["at_risk"]["max"].tolist() == pytest.approx([1.5, b27], abs=1e-9)
        assert out["not_functioning"]["min"].tolist() == pytest.approx([0.0, 1.5], abs=1e-9)
        assert out["not_functioning"]["max"].tolist() == pytest.approx([0.6, b23], abs=1e-9)

    def test_vertical_step_emits_zero_width_intervals_in_touched_bands(self):
        pts = {
            "metric_value": [0.0, 1.0, 1.0, 1.0, 2.0],
            "index_score": [0.0, 0.0, 0.5, 1.0, 1.0],
        }
        out = rc.reference_curve_band_intervals(pts)
        # flat y=0 run [0,1] + vertical touches -> nf [0,1]; vertical at-risk point [1,1];
        # vertical functioning point merges into flat y=1 run [1,2]
        assert out["not_functioning"]["min"].tolist() == pytest.approx([0.0], abs=1e-9)
        assert out["not_functioning"]["max"].tolist() == pytest.approx([1.0], abs=1e-9)
        assert out["at_risk"]["min"].tolist() == pytest.approx([1.0], abs=1e-9)
        assert out["at_risk"]["max"].tolist() == pytest.approx([1.0], abs=1e-9)
        assert out["functioning"]["min"].tolist() == pytest.approx([1.0], abs=1e-9)
        assert out["functioning"]["max"].tolist() == pytest.approx([2.0], abs=1e-9)


# --------------------------------------------------------------------------- #
# reference_curve_summary_stats
# --------------------------------------------------------------------------- #


class TestSummaryStats:
    def test_known_values_r_type7_quantiles(self):
        stats = rc.reference_curve_summary_stats(list(range(1, 11)))
        assert stats["n_reference"] == 10
        assert stats["q25"] == pytest.approx(3.25)
        assert stats["q75"] == pytest.approx(7.75)
        assert stats["iqr"] == pytest.approx(4.5)
        assert stats["median_val"] == pytest.approx(5.5)
        assert stats["mean_val"] == pytest.approx(5.5)
        assert stats["sd_val"] == pytest.approx(3.0276503540974917)
        assert stats["min_val"] == 1.0
        assert stats["max_val"] == 10.0

    def test_empty(self):
        stats = rc.reference_curve_summary_stats([])
        assert stats["n_reference"] == 0
        assert all(
            math.isnan(stats[k])
            for k in ("median_val", "mean_val", "sd_val", "min_val", "max_val", "q25", "q75", "iqr")
        )

    def test_single_value_sd_is_na(self):
        stats = rc.reference_curve_summary_stats([4.2])
        assert stats["n_reference"] == 1
        assert math.isnan(stats["sd_val"])
        assert stats["q25"] == 4.2

    def test_non_finite_values_filtered(self):
        stats = rc.reference_curve_summary_stats([1.0, 2.0, 3.0, np.inf, np.nan])
        assert stats["n_reference"] == 3
        assert stats["max_val"] == 3.0


# --------------------------------------------------------------------------- #
# build_reference_curve — IQR seeding and degenerate branches
# --------------------------------------------------------------------------- #


class TestBuildReferenceCurve:
    def test_higher_is_better_seed(self):
        data = data_frame(epi_sub=HIGHER_VALUES)
        res = rc.build_reference_curve(data, "epi", MC)
        row = res["curve_row"]
        pts = res["curve_points"]

        q25, q75, iqr = 7.0, 14.0, 7.0
        assert pts["metric_value"].tolist() == [0.0, q25 * 3 / 7, q25, q75, q75 + iqr * 0.3]
        assert pts["index_score"].tolist() == [0.00, 0.30, 0.70, 1.00, 1.00]

        assert row["curve_status"].iloc[0] == "complete"
        assert row["curve_source"].iloc[0] == "auto"
        assert row["n_reference"].iloc[0] == 5
        assert bool(row["higher_is_better"].iloc[0]) is True
        assert row["curve_n_points"].iloc[0] == 5
        assert row["score_30_metric"].iloc[0] == 3.0
        assert row["score_70_metric"].iloc[0] == 7.0
        # first three points flattened; the rest only inside curve_points
        assert row["curve_point1_x"].iloc[0] == 0.0
        assert row["curve_point2_x"].iloc[0] == 3.0
        assert row["curve_point3_x"].iloc[0] == 7.0
        assert row["curve_point3_y"].iloc[0] == 0.7
        nested = row["curve_points"].iloc[0]
        assert len(nested) == 5

        # direction-aware band edges: functioning_max = score_100 (14), not points_max
        assert row["functioning_min"].iloc[0] == 7.0
        assert row["functioning_max"].iloc[0] == 14.0
        assert row["at_risk_min"].iloc[0] == 3.0
        assert row["at_risk_max"].iloc[0] == 7.0
        assert row["not_functioning_min"].iloc[0] == 0.0
        assert row["not_functioning_max"].iloc[0] == 3.0

        assert row["score_30_crossings"].iloc[0] == [3.0]
        assert row["score_70_crossings"].iloc[0] == [7.0]
        assert row["score_100_crossings"].iloc[0] == []  # trailing plateau: one-sided
        assert row["score_30_crossing_count"].iloc[0] == 1
        assert row["score_100_crossing_count"].iloc[0] == 0
        assert row["score_30_crossings_display"].iloc[0] == "3.00"
        assert row["score_100_crossings_display"].iloc[0] == "N/A"

        assert row["functioning_ranges_display"].iloc[0] == "7.00 - 16.10"
        assert row["at_risk_ranges_display"].iloc[0] == "3.00 - 7.00"
        assert row["not_functioning_ranges_display"].iloc[0] == "0.00 - 3.00"
        assert row["stratum"].iloc[0] is None
        assert list(row.columns) == ROW_COLUMNS

    def test_lower_is_better_seed(self):
        data = data_frame(pct_fines=LOWER_VALUES)
        res = rc.build_reference_curve(data, "fines", MC)
        row = res["curve_row"]
        pts = res["curve_points"]

        q25, q75, iqr = 10.0, 20.0, 10.0
        expected_x = [
            max(0, q25 - iqr * 0.3),
            q25,
            q75,
            q75 + iqr * 4 / 3,
            q75 + iqr * 7 / 3,
        ]
        assert pts["metric_value"].tolist() == pytest.approx(expected_x, abs=1e-12)
        assert pts["index_score"].tolist() == [1.00, 1.00, 0.70, 0.30, 0.00]

        assert row["curve_status"].iloc[0] == "complete"
        assert bool(row["higher_is_better"].iloc[0]) is False
        # prefer="right" for lower-is-better
        assert row["score_70_metric"].iloc[0] == 20.0
        assert row["score_30_metric"].iloc[0] == pytest.approx(
            float(np.round(q75 + iqr * 4 / 3, 10)), abs=1e-9
        )
        # functioning_min comes from score_100 (right edge of the y=1 plateau)
        assert row["functioning_min"].iloc[0] == 10.0
        assert row["functioning_max"].iloc[0] == 20.0
        assert row["at_risk_min"].iloc[0] == 20.0
        assert row["not_functioning_max"].iloc[0] == pytest.approx(q75 + iqr * 7 / 3, abs=1e-9)

        assert rc.reference_curve_threshold_crossings(pts, 0.70) == [20.0]
        assert row["functioning_ranges_display"].iloc[0] == "7.00 - 20.00"
        assert row["at_risk_ranges_display"].iloc[0] == "20.00 - 33.33"
        assert row["not_functioning_ranges_display"].iloc[0] == "33.33 - 43.33"

    def test_degenerate_q25_branch(self):
        data = data_frame(epi_sub=[0.0, 0.0, 0.0, 5.0, 5.0, 5.0, 5.0])  # q25 = 0
        res = rc.build_reference_curve(data, "epi", MC)
        row = res["curve_row"]
        pts = res["curve_points"]

        assert row["curve_status"].iloc[0] == "degenerate_q25"  # no escalation applies
        assert pts["metric_value"].tolist() == [0.0, 0.0, 5.0]
        assert pts["index_score"].tolist() == [0.00, 0.70, 1.00]
        assert row["curve_n_points"].iloc[0] == 3

        assert row["score_30_metric"].iloc[0] == 0.0
        assert row["score_70_metric"].iloc[0] == 0.0
        assert row["score_30_crossings"].iloc[0] == [0.0]
        # vertical first segment emits zero-width intervals in every touched band
        assert row["functioning_ranges_display"].iloc[0] == "0.00 - 5.00"
        assert row["at_risk_ranges_display"].iloc[0] == "0.00"
        assert row["not_functioning_ranges_display"].iloc[0] == "0.00"

    def test_degenerate_q25_with_all_inf_values(self):
        # 5 non-NA values pass the n>=5 gate, but no finite values -> NaN q25
        data = data_frame(epi_sub=np.full(5, np.inf))
        res = rc.build_reference_curve(data, "epi", MC)
        row = res["curve_row"]
        assert row["curve_status"].iloc[0] == "degenerate_q25"
        assert row["n_reference"].iloc[0] == 0  # stats count finite values only
        assert row["curve_n_points"].iloc[0] == 3  # x = [0, NaN, NaN] rows survive
        assert math.isnan(row["score_30_metric"].iloc[0])
        assert row["functioning_ranges_display"].iloc[0] == "N/A"

    def test_degenerate_curve_when_iqr_not_finite(self):
        data = data_frame(pct_fines=np.full(5, np.inf))  # lower: skips the q25 branch
        res = rc.build_reference_curve(data, "fines", MC)
        row = res["curve_row"]
        assert row["curve_status"].iloc[0] == "degenerate_curve"
        assert row["curve_n_points"].iloc[0] == 0
        assert len(res["curve_points"]) == 0

    def test_insufficient_data(self, caplog):
        data = data_frame(epi_sub=[1.0, 2.0, 3.0, 4.0])
        with caplog.at_level(logging.WARNING, logger="streamcurves"):
            res = rc.build_reference_curve(data, "epi", MC)
        assert "epi: too few reference values (4)" in caplog.text
        row = res["curve_row"]
        assert row["curve_status"].iloc[0] == "insufficient_data"
        assert row["n_reference"].iloc[0] == 4
        assert row["median_val"].iloc[0] == pytest.approx(2.5)  # stats still computed
        assert row["curve_n_points"].iloc[0] == 0
        assert math.isnan(row["curve_point1_x"].iloc[0])
        assert math.isnan(row["score_30_metric"].iloc[0])
        assert row["score_30_crossings_display"].iloc[0] == "N/A"
        assert len(res["curve_points"]) == 0
        assert res["bar_chart_plot"] is None and res["curve_plot"] is None

    def test_na_values_dropped_before_n_gate(self):
        data = data_frame(epi_sub=[7.0, 7.0, 14.0, 14.0, np.nan, np.nan])
        res = rc.build_reference_curve(data, "epi", MC)
        assert res["curve_row"]["curve_status"].iloc[0] == "insufficient_data"

    def test_stratum_label_recorded(self):
        data = data_frame(epi_sub=HIGHER_VALUES)
        res = rc.build_reference_curve(data, "epi", MC, stratum_label="Coastal")
        assert res["curve_row"]["stratum"].iloc[0] == "Coastal"


# --------------------------------------------------------------------------- #
# build_reference_curve_row — status escalation
# --------------------------------------------------------------------------- #


class TestRowStatusEscalation:
    def test_multi_crossing_escalates(self):
        stats = rc.reference_curve_summary_stats(HIGHER_VALUES)
        pts = {
            "metric_value": [0.0, 1.0, 2.0, 3.0, 4.0],
            "index_score": [0.0, 0.5, 0.1, 0.8, 1.0],
        }  # crosses 0.30 three times
        row = rc.build_reference_curve_row("epi", MC, stats, pts)
        assert row["curve_status"].iloc[0] == "unsupported_multi_crossing"
        assert row["score_30_crossing_count"].iloc[0] == 3

    def test_nan_threshold_metric_escalates_to_degenerate(self):
        stats = rc.reference_curve_summary_stats(HIGHER_VALUES)
        pts = {"metric_value": [0.0, 1.0], "index_score": [0.8, 1.0]}  # never reaches 0.30
        row = rc.build_reference_curve_row("epi", MC, stats, pts)
        assert row["curve_status"].iloc[0] == "degenerate_curve"

    def test_non_complete_status_not_escalated(self):
        stats = rc.reference_curve_summary_stats(HIGHER_VALUES)
        pts = {"metric_value": [0.0, 1.0], "index_score": [0.8, 1.0]}
        row = rc.build_reference_curve_row("epi", MC, stats, pts, curve_status="degenerate_q25")
        assert row["curve_status"].iloc[0] == "degenerate_q25"


# --------------------------------------------------------------------------- #
# build_reference_curve_from_points (manual path)
# --------------------------------------------------------------------------- #


class TestManualCurve:
    def test_valid_manual_curve(self):
        data = data_frame(epi_sub=HIGHER_VALUES)
        res = rc.build_reference_curve_from_points(
            data, "epi", MC, {"metric_value": [0.0, 10.0], "index_score": [0.0, 1.0]}
        )
        assert res["curve_source"] == "manual"
        assert res["curve_row"]["curve_source"].iloc[0] == "manual"
        assert res["curve_row"]["curve_status"].iloc[0] == "complete"
        assert res["curve_row"]["n_reference"].iloc[0] == 5

    def test_invalid_manual_curve_raises_joined_errors(self):
        data = data_frame(epi_sub=HIGHER_VALUES)
        with pytest.raises(ValueError) as exc:
            rc.build_reference_curve_from_points(
                data, "epi", MC, {"metric_value": [5.0], "index_score": [2.0]}
            )
        assert str(exc.value) == (
            "At least 2 curve points are required. "
            "Index score values must be between 0 and 1."
        )


# --------------------------------------------------------------------------- #
# Row round-trips: points_from_row / row_range_display
# --------------------------------------------------------------------------- #


class TestRowRoundTrips:
    def _row(self):
        data = data_frame(epi_sub=HIGHER_VALUES)
        return rc.build_reference_curve(data, "epi", MC)["curve_row"]

    def test_points_from_row_prefers_nested(self):
        row = self._row()
        pts = rc.reference_curve_points_from_row(row)
        assert len(pts) == 5
        assert pts["metric_value"].iloc[0] == 0.0

    def test_points_from_row_flattened_fallback(self):
        row = self._row().drop(columns=["curve_points"])
        pts = rc.reference_curve_points_from_row(row)
        assert len(pts) == 3  # only curve_point1..3 columns exist
        assert pts["metric_value"].tolist() == [0.0, 3.0, 7.0]
        assert pts["index_score"].tolist() == [0.0, 0.3, 0.7]

    def test_points_from_row_none(self):
        assert len(rc.reference_curve_points_from_row(None)) == 0
        assert len(rc.reference_curve_points_from_row(rc.empty_reference_curve_points())) == 0

    def test_row_range_display_uses_display_column(self):
        row = self._row()
        assert rc.reference_curve_row_range_display(row, "functioning") == "7.00 - 16.10"

    def test_row_range_display_falls_back_to_list_column(self):
        row = self._row()
        row.loc[0, "functioning_ranges_display"] = ""
        assert rc.reference_curve_row_range_display(row, "functioning") == "7.00 - 16.10"

    def test_row_range_display_min_max_fallback(self):
        assert (
            rc.reference_curve_row_range_display(
                {"functioning_min": 1.0, "functioning_max": 2.0}, "functioning"
            )
            == "1.00 - 2.00"
        )

    def test_row_range_display_none(self):
        assert rc.reference_curve_row_range_display(None, "functioning") == "N/A"
        assert rc.reference_curve_row_range_display({"other": 1}, "functioning") == "N/A"


# --------------------------------------------------------------------------- #
# reference_curve_rows_for_export
# --------------------------------------------------------------------------- #


class TestRowsForExport:
    def test_none_gives_empty(self):
        out = rc.reference_curve_rows_for_export(None)
        assert isinstance(out, pd.DataFrame) and len(out) == 0

    def test_export_flattening(self):
        data = data_frame(epi_sub=HIGHER_VALUES)
        row = rc.build_reference_curve(data, "epi", MC)["curve_row"]
        out = rc.reference_curve_rows_for_export(row)

        assert out["score_30_crossings"].iloc[0] == "3.00"
        assert out["score_70_crossings"].iloc[0] == "7.00"
        assert out["score_100_crossings"].iloc[0] == "N/A"
        assert out["functioning_ranges"].iloc[0] == "7.00 - 16.10"
        assert out["at_risk_ranges"].iloc[0] == "3.00 - 7.00"

        for col in (
            "score_30_crossings_display",
            "functioning_ranges_display",
            "curve_points",
        ):
            assert col not in out.columns
        expected_cols = [
            c
            for c in ROW_COLUMNS
            if not c.endswith("_display") and c != "curve_points"
        ]
        assert list(out.columns) == expected_cols

    def test_string_columns_left_alone(self):
        df = pd.DataFrame({"score_30_crossings": ["already text"], "other": [1]})
        out = rc.reference_curve_rows_for_export(df)
        assert out["score_30_crossings"].iloc[0] == "already text"


# --------------------------------------------------------------------------- #
# run_all_reference_curves
# --------------------------------------------------------------------------- #


class TestRunAll:
    def test_eligibility_and_registry(self):
        config = {
            "epi": MC["epi"],
            "cat": {
                "column_name": "cat_col",
                "display_name": "Categorical",
                "metric_family": "categorical",
                "higher_is_better": True,
            },
            "nohib": {
                "column_name": "nohib_col",
                "display_name": "No direction",
                "metric_family": "instream",
                "higher_is_better": None,
            },
        }
        data = data_frame(
            epi_sub=HIGHER_VALUES,
            cat_col=[1.0] * 5,
            nohib_col=[1.0, 2.0, 3.0, 4.0, 5.0],
        )
        out = rc.run_all_reference_curves(data, config)
        assert list(out["registry"]["metric"]) == ["epi"]
        assert out["bar_chart_plots"] == {}
        assert out["curve_plots"] == {}

    def test_empty_config(self):
        out = rc.run_all_reference_curves(data_frame(x=[1.0]), {})
        assert len(out["registry"]) == 0

    def test_registry_binds_mixed_statuses(self):
        config = {"epi": MC["epi"], "fines": MC["fines"]}
        # fines: only 2 non-NA values -> insufficient_data
        data = data_frame(
            epi_sub=HIGHER_VALUES, pct_fines=[1.0, 2.0, np.nan, np.nan, np.nan]
        )
        out = rc.run_all_reference_curves(data, config)
        reg = out["registry"]
        assert reg["metric"].tolist() == ["epi", "fines"]
        assert reg["curve_status"].tolist() == ["complete", "insufficient_data"]
        assert list(reg.columns) == ROW_COLUMNS


# --------------------------------------------------------------------------- #
# Result-shape helpers: normalize / strip / hydrate
# --------------------------------------------------------------------------- #


class TestResultHelpers:
    def _result(self):
        data = data_frame(epi_sub=HIGHER_VALUES)
        return rc.build_reference_curve(data, "epi", MC)

    def test_normalize_none(self):
        assert rc.normalize_reference_curve_result(None) is None

    def test_normalize_descends_into_reference_curve(self):
        res = self._result()
        out = rc.normalize_reference_curve_result({"reference_curve": res}, metric_config=MC)
        assert out["curve_source"] == "auto"
        assert out["curve_row"]["metric"].iloc[0] == "epi"
        assert len(out["curve_points"]) == 5

    def test_normalize_rebuilds_and_keeps_extra_columns(self):
        res = self._result()
        row = res["curve_row"].copy()
        row["extra_col"] = ["keep me"]
        out = rc.normalize_reference_curve_result(
            {**res, "curve_row": row}, metric_config=MC
        )
        rebuilt = out["curve_row"]
        assert rebuilt["extra_col"].iloc[0] == "keep me"
        assert rebuilt["score_30_metric"].iloc[0] == 3.0
        # extra columns come after the canonical ones
        assert list(rebuilt.columns)[: len(ROW_COLUMNS)] == ROW_COLUMNS
        assert list(rebuilt.columns)[-1] == "extra_col"

    def test_normalize_recovers_points_from_row(self):
        res = self._result()
        out = rc.normalize_reference_curve_result(
            {"curve_row": res["curve_row"]}, metric_config=MC
        )
        assert len(out["curve_points"]) == 5

    def test_strip_nulls_plots(self):
        res = self._result()
        out = rc.strip_reference_curve_result(res, metric_config=MC, metric_key="epi")
        assert out["bar_chart_plot"] is None
        assert out["curve_plot"] is None

    def test_hydrate_summary_mode(self):
        res = self._result()
        data = data_frame(epi_sub=HIGHER_VALUES)
        out = rc.hydrate_reference_curve_result(
            res, data, "epi", MC, artifact_mode="summary"
        )
        assert out["bar_chart_plot"] is None
        assert out["curve_row"]["curve_status"].iloc[0] == "complete"

    def test_hydrate_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            rc.hydrate_reference_curve_result(None, None, "epi", MC, artifact_mode="bogus")

    def test_hydrate_non_complete_returns_normalized(self):
        data = data_frame(epi_sub=[1.0, 2.0, 3.0, 4.0])
        res = rc.build_reference_curve(data, "epi", MC)
        out = rc.hydrate_reference_curve_result(res, data, "epi", MC)
        assert out["curve_row"]["curve_status"].iloc[0] == "insufficient_data"

    def test_hydrate_manual_rebuilds_via_manual_path(self):
        data = data_frame(epi_sub=HIGHER_VALUES)
        res = rc.build_reference_curve_from_points(
            data, "epi", MC, {"metric_value": [0.0, 10.0], "index_score": [0.0, 1.0]}
        )
        out = rc.hydrate_reference_curve_result(res, data, "epi", MC)
        assert out["curve_source"] == "manual"
        assert out["curve_row"]["curve_status"].iloc[0] == "complete"

    def test_hydrate_auto_rebuilds(self):
        data = data_frame(epi_sub=HIGHER_VALUES)
        res = self._result()
        out = rc.hydrate_reference_curve_result(res, data, "epi", MC)
        assert out["curve_source"] == "auto"
        assert out["curve_row"]["score_30_metric"].iloc[0] == 3.0


# --------------------------------------------------------------------------- #
# reference_curve_x_range (shared plot data-prep)
# --------------------------------------------------------------------------- #


class TestXRange:
    def test_padded_range(self):
        pts = rc.normalize_reference_curve_points(
            {"metric_value": [0.0, 10.0], "index_score": [0.0, 1.0]}
        )
        lo, hi = rc.reference_curve_x_range([1.0, 5.0], pts)
        assert lo == pytest.approx(-0.8)
        assert hi == pytest.approx(10.8)

    def test_empty_defaults(self):
        assert rc.reference_curve_x_range([], rc.empty_reference_curve_points()) == (0.0, 1.0)

    def test_degenerate_single_value_widens(self):
        pts = rc.normalize_reference_curve_points(
            {"metric_value": [3.0, 3.0], "index_score": [0.0, 1.0]}
        )
        lo, hi = rc.reference_curve_x_range([], pts)
        assert lo == pytest.approx(1.5)
        assert hi == pytest.approx(4.5)


# --------------------------------------------------------------------------- #
# interp_curve / reference_curve_score_value
# --------------------------------------------------------------------------- #


class TestInterpCurve:
    def test_empty_and_single(self):
        assert rc.interp_curve([], 1.0) is None
        assert rc.interp_curve([{"x": 3.0, "y": 0.4}], -100.0) == 0.4
        assert rc.interp_curve([{"x": 3.0, "y": 0.4}], 100.0) == 0.4

    def test_clamps_outside_domain(self):
        pts = [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 1.0}]
        assert rc.interp_curve(pts, -5.0) == 0.0
        assert rc.interp_curve(pts, 15.0) == 1.0
        assert rc.interp_curve(pts, 5.0) == pytest.approx(0.5)

    def test_clamps_result_into_unit_interval(self):
        pts = [{"x": 0.0, "y": -0.2}, {"x": 10.0, "y": 1.4}]
        assert rc.interp_curve(pts, 0.0) == 0.0
        assert rc.interp_curve(pts, 10.0) == 1.0
        assert 0.0 <= rc.interp_curve(pts, 5.0) <= 1.0

    def test_coincident_x_step(self):
        pts = [
            {"x": 0.0, "y": 0.0},
            {"x": 5.0, "y": 0.2},
            {"x": 5.0, "y": 0.8},
            {"x": 10.0, "y": 1.0},
        ]
        # at the exact step x the first (left) segment containing x wins
        assert rc.interp_curve(pts, 5.0) == pytest.approx(0.2)
        assert rc.interp_curve(pts, 5.0 + 1e-9) == pytest.approx(0.8, abs=1e-8)
        assert rc.interp_curve(pts, 4.999999999) == pytest.approx(0.2, abs=1e-8)

    def test_accepts_module_point_table(self):
        pts = rc.normalize_reference_curve_points(
            {"metric_value": [0.0, 10.0], "index_score": [0.0, 1.0]}
        )
        assert rc.interp_curve(pts, 3.0) == pytest.approx(0.3)

    def test_unsorted_input(self):
        pts = [{"x": 10.0, "y": 1.0}, {"x": 0.0, "y": 0.0}, {"x": 5.0, "y": 0.2}]
        assert rc.interp_curve(pts, 7.5) == pytest.approx(0.6)

    def test_score_value_on_result_and_row(self):
        data = data_frame(epi_sub=HIGHER_VALUES)
        res = rc.build_reference_curve(data, "epi", MC)
        assert rc.reference_curve_score_value(res, 7.0) == pytest.approx(0.7)
        assert rc.reference_curve_score_value(res, 0.0) == 0.0
        assert rc.reference_curve_score_value(res, 100.0) == 1.0
        assert rc.reference_curve_score_value(res["curve_row"], 7.0) == pytest.approx(0.7)

    def test_score_value_unscorable(self):
        assert math.isnan(rc.reference_curve_score_value(None, 1.0))
        assert math.isnan(rc.reference_curve_score_value({"curve_points": None}, 1.0))
        assert math.isnan(
            rc.reference_curve_score_value(
                {"curve_points": rc.empty_reference_curve_points()}, 1.0
            )
        )
        pts = [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 1.0}]
        assert math.isnan(rc.reference_curve_score_value(pts, float("nan")))
        assert math.isnan(rc.reference_curve_score_value(pts, None))


# --------------------------------------------------------------------------- #
# Golden fixtures (generated by scripts/export_golden.R from the real R run)
# --------------------------------------------------------------------------- #

_GOLDEN_LIST_FIELDS = {"allowed_predictors", "allowed_stratifications"}
_GOLDEN_NAMES = ("01_bundle_meta", "02_derived", "10_curve_registry", "10_curve_points")


def _golden_metric_config():
    """metric_config from 01_bundle_meta.json (jsonlite boxes scalars)."""
    meta = golden_io.load_golden_json("01_bundle_meta")
    out = {}
    for key, entry in (meta.get("metric_config") or {}).items():
        e = {}
        for field, v in (entry or {}).items():
            if isinstance(v, list) and field not in _GOLDEN_LIST_FIELDS and len(v) == 1:
                e[field] = v[0]
            else:
                e[field] = v
        out[key] = e
    return out


@pytest.fixture(scope="module")
def golden_run():
    for name in _GOLDEN_NAMES:
        if not golden_io.has_golden(name):
            pytest.skip(
                f"golden fixture {name}.json not present (export with scripts/export_golden.R)"
            )
    derived = golden_io.load_golden_df("02_derived")
    return rc.run_all_reference_curves(derived, _golden_metric_config())


class TestGolden:
    def test_registry_parity(self, golden_run):
        golden = golden_io.load_golden_df("10_curve_registry")
        exported = rc.reference_curve_rows_for_export(golden_run["registry"])
        assert list(exported.columns) == list(golden.columns)
        # The *_crossings / *_ranges columns are 2-dp DISPLAY strings of
        # numerics asserted at 1e-9 below. R's fround() long-double
        # half-to-even handling can differ from Python by one display penny
        # when a value sits exactly on a half (avgBasalarea's q25 crossing of
        # 19.925 -> R "19.92", Python "19.93") — allow ±0.011 there only.
        display_cols = [
            c for c in golden.columns if c.endswith("_crossings") or c.endswith("_ranges")
        ]
        strict_cols = [c for c in golden.columns if c not in display_cols]
        golden_io.assert_frame_matches(
            exported[strict_cols],
            golden[strict_cols],
            keys=["metric"],
            rtol=1e-9,
            atol=1e-9,
            check_extra_py_cols=True,
        )
        golden_io.assert_display_matches(
            exported[["metric"] + display_cols],
            golden[["metric"] + display_cols],
            keys=["metric"],
            atol=0.011,
        )

    def test_registry_metric_order_matches_r(self, golden_run):
        golden = golden_io.load_golden_df("10_curve_registry")
        assert golden_run["registry"]["metric"].tolist() == golden["metric"].tolist()

    def test_curve_points_parity(self, golden_run):
        all_points = golden_io.load_golden_json("10_curve_points")
        registry = golden_run["registry"]
        assert len(all_points) == len(registry)

        for key, tbl in all_points.items():
            metric = key.split("@@", 1)[0]
            rows = registry[registry["metric"] == metric]
            assert len(rows) == 1, key
            pts = rows["curve_points"].iloc[0]
            assert pts["point_order"].tolist() == [int(v) for v in tbl["point_order"]], key
            np.testing.assert_allclose(
                pts["metric_value"].to_numpy(dtype=float),
                np.asarray(tbl["metric_value"], dtype=float),
                rtol=1e-9,
                atol=1e-9,
                err_msg=f"{key}: metric_value",
            )
            np.testing.assert_allclose(
                pts["index_score"].to_numpy(dtype=float),
                np.asarray(tbl["index_score"], dtype=float),
                rtol=1e-9,
                atol=1e-9,
                err_msg=f"{key}: index_score",
            )

    def test_interp_reproduces_golden_curves(self, golden_run):
        """interp_curve over the R-exported point sets: exact points, segment
        midpoints, and clamping beyond both ends of the domain."""
        all_points = golden_io.load_golden_json("10_curve_points")

        def clamp(y):
            return min(1.0, max(0.0, y))

        for key, tbl in all_points.items():
            xs = [float(v) for v in tbl["metric_value"]]
            ys = [float(v) for v in tbl["index_score"]]
            curve = pd.DataFrame({"metric_value": xs, "index_score": ys})

            seen_x = set()
            for x, y in zip(xs, ys):
                if x in seen_x:  # coincident x: only the first point's y holds
                    continue
                seen_x.add(x)
                assert rc.interp_curve(curve, x) == pytest.approx(clamp(y), abs=1e-9), (key, x)

            for (x1, y1), (x2, y2) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
                if x2 - x1 <= 0:
                    continue
                xm = (x1 + x2) / 2
                expect = y1 + ((xm - x1) / (x2 - x1)) * (y2 - y1)
                assert rc.interp_curve(curve, xm) == pytest.approx(clamp(expect), abs=1e-9), (
                    key,
                    xm,
                )

            assert rc.interp_curve(curve, xs[0] - 100.0) == pytest.approx(clamp(ys[0]), abs=1e-12)
            assert rc.interp_curve(curve, xs[-1] + 100.0) == pytest.approx(
                clamp(ys[-1]), abs=1e-12
            )

            # the scoring wrapper agrees when fed the registry-built curve row
            registry = golden_run["registry"]
            row = registry[registry["metric"] == key.split("@@", 1)[0]]
            mid = (xs[0] + xs[-1]) / 2
            assert rc.reference_curve_score_value(row, mid) == pytest.approx(
                rc.interp_curve(curve, mid), abs=1e-9
            ), key
