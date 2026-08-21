"""Endpoint-clamp domain warnings (C1).

``interp_curve`` clamps out-of-domain values to the nearest endpoint index. These
tests pin the *advisory* that now surfaces that clamp, and assert the clamp math
itself is unchanged (scoring must stay identical).
"""
import math

from deep import curves
from deep.curves import domain_warning, interp_curve, metric_warning, score_site
from deep.models import MeasuredValue

# domain [0, 8]: "lower-is-better" — index 1 at x=0 down to 0 at x=8.
PTS = [{"x": 0, "y": 1}, {"x": 8, "y": 0}]


def test_in_domain_has_no_warning():
    assert domain_warning(PTS, 0.0) is None      # on the lower endpoint
    assert domain_warning(PTS, 4.0) is None       # interior
    assert domain_warning(PTS, 8.0) is None        # on the upper endpoint


def test_above_domain_warns():
    msg = domain_warning(PTS, 12.0)
    assert isinstance(msg, str)
    assert msg == "value 12.0 is above the curve domain [0.0, 8.0]; score clamped to the endpoint"


def test_below_domain_warns():
    msg = domain_warning(PTS, -3.0)
    assert isinstance(msg, str)
    assert msg == "value -3.0 is below the curve domain [0.0, 8.0]; score clamped to the endpoint"


def test_empty_and_single_point_curves_are_safe():
    # no meaningful domain -> never warn (must not raise)
    assert domain_warning([], 5.0) is None
    assert domain_warning([{"x": 5, "y": 0.42}], 5.0) is None
    assert domain_warning([{"x": 5, "y": 0.42}], 999.0) is None
    # missing coords are filtered like interp_curve does -> single usable point
    assert domain_warning([{"x": 5, "y": 0.42}, {"x": None, "y": 0.1}], 999.0) is None


def test_interp_curve_index_is_unchanged_by_the_advisory():
    # The scoring return must still clamp exactly as before regardless of warnings.
    assert interp_curve(PTS, 12.0) == 0.0          # right clamp -> worst
    assert interp_curve(PTS, -3.0) == 1.0          # left clamp -> best
    assert interp_curve(PTS, 0.0) == 1.0
    assert interp_curve(PTS, 8.0) == 0.0
    assert math.isclose(interp_curve(PTS, 4.0), 0.5, rel_tol=1e-9)  # interior interpolation intact


def test_metric_warning_mirrors_metric_index_gating():
    spec = {"curve": {"points": PTS}}
    # unscored -> no warning (parallels metric_index returning None)
    assert metric_warning(None, spec) is None
    assert metric_warning(MeasuredValue("m", value=None), spec) is None
    assert metric_warning(MeasuredValue("m", value=12.0, na=True), spec) is None
    # scored in-domain -> no warning; scored out-of-domain -> warning
    assert metric_warning(MeasuredValue("m", value=4.0), spec) is None
    assert "above the curve domain" in metric_warning(MeasuredValue("m", value=12.0), spec)


def test_metric_warning_respects_selected_stratum():
    spec = {"curveLayers": [
        {"stratum": "A", "points": [{"x": 0, "y": 1}, {"x": 5, "y": 0}]},     # domain [0, 5]
        {"stratum": "B", "points": [{"x": 0, "y": 0}, {"x": 100, "y": 1}]},   # domain [0, 100]
    ], "activeStratum": "A"}
    # x=40 is above stratum A's domain but inside stratum B's
    assert "above the curve domain" in metric_warning(MeasuredValue("m", value=40.0, stratum="A"), spec)
    assert metric_warning(MeasuredValue("m", value=40.0, stratum="B"), spec) is None


def test_score_site_attaches_per_metric_warning_without_changing_score():
    asmt = {
        "assessmentId": "t", "assessmentName": "t",
        "metricsByFunction": [
            {"functionId": "catchment-hydrology",
             "metrics": [{"metricId": "m1", "curve": {"points": PTS}}]},
        ],
    }
    _sc, fres = score_site(asmt, {"m1": MeasuredValue("m1", value=12.0)})
    fr = fres["catchment-hydrology"]
    # index still clamps to the endpoint (0.0), and the warning rides alongside it
    assert fr.metric_indices["m1"] == 0.0
    assert fr.metric_warnings["m1"] == (
        "value 12.0 is above the curve domain [0.0, 8.0]; score clamped to the endpoint"
    )
    assert fr.to_dict()["metricWarnings"]["m1"] == fr.metric_warnings["m1"]


def test_score_site_no_warning_for_in_domain_value():
    asmt = {
        "assessmentId": "t", "assessmentName": "t",
        "metricsByFunction": [
            {"functionId": "catchment-hydrology",
             "metrics": [{"metricId": "m1", "curve": {"points": PTS}}]},
        ],
    }
    _sc, fres = score_site(asmt, {"m1": MeasuredValue("m1", value=4.0)})
    assert curves.score_site  # module import sanity
    assert fres["catchment-hydrology"].metric_warnings["m1"] is None


def test_domain_advisory_rounds_seed_edges_to_four_decimals():
    """A seed edge such as 2.086666666666667 reads 2.0867 in the advisory;
    whole-number edges keep their plain form (Phase 8 figure review)."""
    spec = {"curve": {"points": [{"x": 0.0, "y": 1.0}, {"x": 2.086666666666667, "y": 0.0}]}}
    msg = metric_warning(MeasuredValue("m", value=4.5), spec)
    assert "curve domain [0.0, 2.0867]" in msg
    assert "value 4.5 is above" in msg
