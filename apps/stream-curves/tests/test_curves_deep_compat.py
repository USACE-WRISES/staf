"""Byte-compatibility of streamcurves.curves.interp_curve with DEEP.

DEEP (apps/deep in this monorepo, deep/curves.py::interp_curve) is the executor
app that consumes curves exported from this app (see R/20_deep_export.R for the
shared contract), so the two interpolators must agree exactly — same stable
sort, same endpoint clamping, same coincident-x behavior, same arithmetic.

Skips gracefully when the DEEP app is not importable.
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

DEEP_ROOT = str(Path(__file__).resolve().parents[2] / "deep")
if DEEP_ROOT not in sys.path:
    sys.path.insert(0, DEEP_ROOT)

deep_curves = pytest.importorskip(
    "deep.curves", reason=f"DEEP app not importable from {DEEP_ROOT}"
)

from streamcurves import curves as rc  # noqa: E402


POINT_SETS = {
    "rising_two_point": [
        {"x": 0.0, "y": 0.0},
        {"x": 10.0, "y": 1.0},
    ],
    "falling_two_point": [
        {"x": 0.0, "y": 1.0},
        {"x": 10.0, "y": 0.0},
    ],
    "higher_seed_five_point": [
        {"x": 0.0, "y": 0.0},
        {"x": 3.0, "y": 0.3},
        {"x": 7.0, "y": 0.7},
        {"x": 14.0, "y": 1.0},
        {"x": 16.1, "y": 1.0},
    ],
    "lower_seed_five_point": [
        {"x": 7.0, "y": 1.0},
        {"x": 10.0, "y": 1.0},
        {"x": 20.0, "y": 0.7},
        {"x": 33.333333333333336, "y": 0.3},
        {"x": 43.333333333333336, "y": 0.0},
    ],
    "coincident_x_step": [
        {"x": 0.0, "y": 0.0},
        {"x": 5.0, "y": 0.2},
        {"x": 5.0, "y": 0.8},
        {"x": 10.0, "y": 1.0},
    ],
    "coincident_x_triple": [
        {"x": 0.0, "y": 0.0},
        {"x": 2.0, "y": 0.1},
        {"x": 2.0, "y": 0.5},
        {"x": 2.0, "y": 0.9},
        {"x": 4.0, "y": 1.0},
    ],
    "duplicates_only": [
        {"x": 5.0, "y": 0.2},
        {"x": 5.0, "y": 0.8},
    ],
    "single_point": [
        {"x": 3.0, "y": 0.4},
    ],
    "out_of_bounds_y_clamps": [
        {"x": 0.0, "y": -0.2},
        {"x": 10.0, "y": 1.4},
    ],
    "unsorted_input": [
        {"x": 10.0, "y": 1.0},
        {"x": 0.0, "y": 0.0},
        {"x": 5.0, "y": 0.2},
    ],
}


def _probe_grid(pts):
    """Dense linspace plus every point x with tiny/quarter offsets on each side."""
    xs = [p["x"] for p in pts]
    lo, hi = min(xs), max(xs)
    grid = list(np.linspace(lo - 2.0, hi + 2.0, 241))
    for x in sorted(set(xs)):
        grid += [x, x - 1e-12, x + 1e-12, x - 0.25, x + 0.25]
    return grid


@pytest.mark.parametrize("name", sorted(POINT_SETS))
def test_interp_curve_matches_deep(name):
    pts = POINT_SETS[name]
    df = pd.DataFrame(
        {
            "metric_value": [p["x"] for p in pts],
            "index_score": [p["y"] for p in pts],
        }
    )

    for x in _probe_grid(pts):
        expected = deep_curves.interp_curve(pts, x)
        got_records = rc.interp_curve(pts, x)
        got_frame = rc.interp_curve(df, x)
        assert got_records == expected, f"{name}: records form differs at x={x!r}"
        assert got_frame == expected, f"{name}: DataFrame form differs at x={x!r}"

        scored = rc.reference_curve_score_value({"curve_points": df}, x)
        assert scored == expected, f"{name}: score_value differs at x={x!r}"


def test_empty_points_match_deep():
    assert deep_curves.interp_curve([], 1.0) is None
    assert rc.interp_curve([], 1.0) is None
    assert math.isnan(
        rc.reference_curve_score_value(
            {"curve_points": rc.empty_reference_curve_points()}, 1.0
        )
    )


def test_missing_coordinates_dropped_like_deep():
    pts = [
        {"x": None, "y": 0.5},
        {"x": 0.0, "y": 0.0},
        {"x": 10.0, "y": None},
        {"x": 10.0, "y": 1.0},
    ]
    for x in (-1.0, 0.0, 2.5, 10.0, 11.0):
        assert rc.interp_curve(pts, x) == deep_curves.interp_curve(pts, x)


def test_deep_scores_this_apps_normalized_points_identically():
    """Round-trip shape check: normalized point tables converted to the DEEP
    record format score identically in both engines."""
    table = rc.normalize_reference_curve_points(
        {"metric_value": [0.0, 3.0, 7.0, 14.0, 16.1], "index_score": [0.0, 0.3, 0.7, 1.0, 1.0]}
    )
    records = [
        {"x": float(r.metric_value), "y": float(r.index_score)} for r in table.itertuples()
    ]
    for x in _probe_grid(records):
        assert rc.interp_curve(table, x) == deep_curves.interp_curve(records, x)
