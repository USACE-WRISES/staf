"""Curve-scoring engine: interpolation, clamping, NA handling, function mean."""
import math

from deep.curves import (active_points, curve_strata, function_index,
                         interp_curve, metric_index)
from deep.models import MeasuredValue

# A real "lower-is-better" curve shape (NC SQT Percent Impervious): index falls as x rises.
DESC = [{"x": 0, "y": 1}, {"x": 9, "y": 0.7}, {"x": 25, "y": 0.3}, {"x": 75.77, "y": 0}]
# A "higher-is-better" curve: index rises with x.
ASC = [{"x": 0, "y": 0}, {"x": 1, "y": 1}]


def test_interp_hits_breakpoints():
    assert interp_curve(DESC, 0) == 1.0
    assert interp_curve(DESC, 9) == 0.7
    assert interp_curve(DESC, 25) == 0.3
    assert interp_curve(DESC, 75.77) == 0.0


def test_interp_clamps_beyond_domain():
    assert interp_curve(DESC, -5) == 1.0     # left clamp -> best
    assert interp_curve(DESC, 1000) == 0.0   # right clamp -> worst
    assert interp_curve(ASC, -1) == 0.0
    assert interp_curve(ASC, 5) == 1.0


def test_interp_linear_midpoints():
    # DESC between 9 and 25: 0.7 + (17-9)/(25-9) * (0.3-0.7) = 0.5
    assert math.isclose(interp_curve(DESC, 17), 0.5, rel_tol=1e-9)
    # ASC midpoint
    assert math.isclose(interp_curve(ASC, 0.5), 0.5, rel_tol=1e-9)


def test_single_point_is_constant():
    one = [{"x": 5, "y": 0.42}]
    assert interp_curve(one, -100) == 0.42
    assert interp_curve(one, 5) == 0.42
    assert interp_curve(one, 100) == 0.42


def test_empty_points_returns_none():
    assert interp_curve([], 3) is None


def test_index_values_clamped_into_unit_interval():
    assert interp_curve([{"x": 0, "y": 1.5}, {"x": 1, "y": 2}], 0.5) == 1.0
    assert interp_curve([{"x": 0, "y": -0.5}, {"x": 1, "y": -1}], 0.5) == 0.0


def test_metric_index_na_missing_and_curveless():
    spec = {"curve": {"points": DESC}}
    assert metric_index(None, spec) is None
    assert metric_index(MeasuredValue("m", value=None), spec) is None
    assert metric_index(MeasuredValue("m", value=5, na=True), spec) is None
    assert metric_index(MeasuredValue("m", value=9), spec) == 0.7
    assert metric_index(MeasuredValue("m", value=9), {"curve": {"points": []}}) is None


def test_function_index_is_mean_scaled_to_15():
    metrics = [{"metricId": "a", "curve": {"points": ASC}},
               {"metricId": "b", "curve": {"points": ASC}}]
    measured = {"a": MeasuredValue("a", value=0.5), "b": MeasuredValue("b", value=1.0)}
    score, indices = function_index(metrics, measured)
    assert indices == {"a": 0.5, "b": 1.0}
    assert math.isclose(score, 0.75 * 15)  # mean(0.5, 1.0) * 15 = 11.25


def test_function_index_na_metrics_drop_out_of_mean():
    metrics = [{"metricId": "a", "curve": {"points": ASC}},
               {"metricId": "b", "curve": {"points": ASC}}]
    # only 'a' scored; 'b' missing -> mean over just 'a'
    score, indices = function_index(metrics, {"a": MeasuredValue("a", value=1.0)})
    assert math.isclose(score, 15.0)
    assert indices["b"] is None
    # nothing scored -> function is NA
    score2, _ = function_index(metrics, {})
    assert score2 is None


def test_multi_stratum_curve_layer_selection():
    spec = {"curveLayers": [
        {"stratum": "A", "points": [{"x": 0, "y": 1}, {"x": 10, "y": 0}]},   # lower-is-better
        {"stratum": "B", "points": [{"x": 0, "y": 0}, {"x": 10, "y": 1}]},   # higher-is-better
    ], "activeStratum": "A"}
    assert curve_strata(spec) == ["A", "B"]
    # default falls back to activeStratum "A"
    assert active_points(spec, None) == spec["curveLayers"][0]["points"]
    # value 2 scores differently per chosen stratum: A -> 0.8, B -> 0.2
    assert math.isclose(metric_index(MeasuredValue("m", value=2), spec), 0.8)
    assert math.isclose(metric_index(MeasuredValue("m", value=2, stratum="B"), spec), 0.2)
    # single-curve metrics still work (back-compat, no strata)
    single = {"curve": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}}
    assert curve_strata(single) == []
    assert metric_index(MeasuredValue("m", value=0.5), single) == 0.5
