"""Tests for streamcurves/xs_geometry.py.

Ports tests/xs_geometry_tests.R from the R repo verbatim (same profiles and
tolerances), plus extra hand-computed interpolation/normal cases, projection
round-trips, transect construction geometry, and half-width clamps.
"""

import math

import numpy as np

from streamcurves.xs_geometry import (
    build_transects,
    interpolate_along,
    polyline_cumlen,
    polyline_length,
    project_point_to_polyline,
    reach_from_bearing,
    reach_upstream_segment,
    to_ll,
    to_m,
    transect_half_width,
    unit_normal,
)

LON0, LAT0 = -82.8, 39.7


# --------------------------------------------------------------------------- #
# 1. equirectangular projection round-trips (xs_geometry_tests.R section 1)
# --------------------------------------------------------------------------- #


def test_projection_round_trip_scalar():
    m = to_m(-82.81, 39.71, LON0, LAT0)
    ll = to_ll(m[0, 0], m[0, 1], LON0, LAT0)
    assert abs(ll[0, 0] - (-82.81)) < 1e-9
    assert abs(ll[0, 1] - 39.71) < 1e-9


def test_projection_round_trip_vector():
    lons = np.array([-82.81, -82.79, -82.8])
    lats = np.array([39.71, 39.69, 39.7])
    m = to_m(lons, lats, LON0, LAT0)
    assert m.shape == (3, 2)
    ll = to_ll(m[:, 0], m[:, 1], LON0, LAT0)
    assert np.allclose(ll[:, 0], lons, atol=1e-9)
    assert np.allclose(ll[:, 1], lats, atol=1e-9)


def test_projection_scale_factors():
    # kx = 111320*cos(lat0), ky = 110540 — 0.01 deg east/north in metres.
    kx = 111320 * math.cos(math.radians(LAT0))
    m = to_m(LON0 + 0.01, LAT0 + 0.01, LON0, LAT0)
    assert abs(m[0, 0] - 0.01 * kx) < 1e-9
    assert abs(m[0, 1] - 0.01 * 110540) < 1e-9
    # origin maps to (0, 0)
    assert np.allclose(to_m(LON0, LAT0, LON0, LAT0), [[0.0, 0.0]])


# --------------------------------------------------------------------------- #
# 2. polyline helpers (xs_geometry_tests.R section 2 + extras)
# --------------------------------------------------------------------------- #

XY = np.array([[0.0, 0.0], [100.0, 0.0], [200.0, 0.0]])       # straight, 200 m
XY_L = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 100.0]])   # L-shape, 200 m


def test_polyline_length_and_cumlen():
    assert abs(polyline_length(XY) - 200) < 1e-9
    assert np.allclose(polyline_cumlen(XY), [0.0, 100.0, 200.0])


def test_polyline_cumlen_degenerate():
    # NOTE(parity): R returns scalar 0 for < 2 rows == a length-1 vector.
    one = np.array([[3.0, 4.0]])
    assert np.allclose(polyline_cumlen(one), [0.0])
    assert polyline_length(one) == 0.0


def test_interpolate_along_straight():
    p = interpolate_along(XY, 150)
    assert abs(p[0] - 150) < 1e-9
    assert abs(p[1]) < 1e-9


def test_interpolate_along_l_shape():
    q = interpolate_along(XY_L, 150)
    assert abs(q[0] - 100) < 1e-9
    assert abs(q[1] - 50) < 1e-9


def test_interpolate_along_clamps():
    assert np.allclose(interpolate_along(XY, -10), [0.0, 0.0])
    assert np.allclose(interpolate_along(XY, 1e9), [200.0, 0.0])
    # arc length exactly at an interior vertex -> that vertex
    assert np.allclose(interpolate_along(XY_L, 100), [100.0, 0.0])
    # zero-length polyline -> its first point
    assert np.allclose(interpolate_along(np.array([[7.0, 8.0]]), 5), [7.0, 8.0])


def test_interpolate_along_duplicate_vertex():
    # zero-length segment (dup vertex): interval walk skips it (t = 0 branch)
    xy = np.array([[0.0, 0.0], [0.0, 0.0], [100.0, 0.0]])
    assert np.allclose(interpolate_along(xy, 50), [50.0, 0.0])
    assert np.allclose(interpolate_along(xy, 0), [0.0, 0.0])


def test_project_point_to_polyline_basic():
    pr = project_point_to_polyline(XY, 150, 20)
    assert abs(pr["station"] - 150) < 1e-9
    assert abs(pr["dist"] - 20) < 1e-9


def test_project_point_beyond_ends():
    # beyond the far end: t clamps to 1 -> station = full length
    pr = project_point_to_polyline(XY, 250, 30)
    assert abs(pr["station"] - 200) < 1e-9
    assert abs(pr["dist"] - math.sqrt(50**2 + 30**2)) < 1e-9
    # before the start: t clamps to 0 -> station 0
    pr0 = project_point_to_polyline(XY, -50, 10)
    assert abs(pr0["station"]) < 1e-9
    assert abs(pr0["dist"] - math.sqrt(50**2 + 10**2)) < 1e-9


def test_project_point_single_point_polyline():
    pr = project_point_to_polyline(np.array([[1.0, 2.0]]), 4, 6)
    assert pr["station"] == 0.0
    assert abs(pr["dist"] - 5) < 1e-9


def test_unit_normal_straight_line():
    un = unit_normal(XY, 100)            # tangent +x -> normal (0, 1)
    assert abs(un["normal"][0]) < 1e-9
    assert abs(un["normal"][1] - 1) < 1e-9
    assert np.allclose(un["point"], [100.0, 0.0])


def test_unit_normal_diagonal_hand_computed():
    xy = np.array([[0.0, 0.0], [100.0, 100.0]])
    un = unit_normal(xy, 50)             # tangent (1,1)/sqrt2 -> normal (-1,1)/sqrt2
    r = 1 / math.sqrt(2)
    assert abs(un["normal"][0] + r) < 1e-9
    assert abs(un["normal"][1] - r) < 1e-9
    assert abs(np.hypot(*un["normal"]) - 1) < 1e-12


def test_unit_normal_l_corner_hand_computed():
    # centred difference across the corner at s = 100: a = (95, 0), b = (100, 5)
    un = unit_normal(XY_L, 100, ds=5)
    r = 1 / math.sqrt(2)
    assert abs(un["normal"][0] + r) < 1e-9
    assert abs(un["normal"][1] - r) < 1e-9
    assert np.allclose(un["point"], [100.0, 0.0])


def test_unit_normal_stable_at_endpoints():
    for s in (0, 200):
        un = unit_normal(XY, s)
        assert abs(un["normal"][0]) < 1e-9
        assert abs(un["normal"][1] - 1) < 1e-9


# --------------------------------------------------------------------------- #
# 3. reach_upstream_segment (xs_geometry_tests.R section 3 + extras)
# --------------------------------------------------------------------------- #


def test_reach_upstream_segment_trims_to_1000_ft():
    coords = np.array([[LON0, LAT0], [LON0, LAT0 + 0.03]])   # ~3316 m due north
    seg = reach_upstream_segment(coords, (LON0, LAT0), length_ft=1000)
    assert abs(seg["length_m"] - 304.8) < 1.0
    assert abs(seg["full_length_m"] - 0.03 * 110540) < 2.0
    assert seg["lon0"] == LON0 and seg["lat0"] == LAT0


def test_reach_upstream_segment_keeps_interior_vertices():
    coords = np.array([
        [LON0, LAT0],
        [LON0, LAT0 + 0.001],    # cumlen 110.54 m
        [LON0, LAT0 + 0.002],    # cumlen 221.08 m
        [LON0, LAT0 + 0.03],
    ])
    seg = reach_upstream_segment(coords, (LON0, LAT0), length_ft=1000)
    # interp(s0=0), two interior vertices inside (0, 304.8), interp(s1)
    assert seg["xy"].shape == (4, 2)
    assert np.allclose(seg["xy"][0], [0.0, 0.0], atol=1e-9)
    assert np.all(np.diff(seg["xy"][:, 1]) > 0)              # walks upstream
    assert abs(seg["length_m"] - 304.8) < 1e-6


def test_reach_upstream_segment_snap_mid_line():
    coords = np.array([[LON0, LAT0], [LON0, LAT0 + 0.03]])
    snap = (LON0, LAT0 + 0.01)
    seg = reach_upstream_segment(coords, snap, length_ft=1000)
    assert abs(seg["length_m"] - 304.8) < 1e-6               # not clamped mid-line
    # the metric frame is centred on the snap, so the segment starts at (0, 0)
    assert np.allclose(seg["xy"][0], [0.0, 0.0], atol=1e-6)
    assert abs(seg["xy"][-1, 1] - 304.8) < 1e-6              # walks 1000 ft upstream
    # ... which round-trips to the snap lon/lat
    start_ll = to_ll(seg["xy"][0, 0], seg["xy"][0, 1], seg["lon0"], seg["lat0"])
    assert np.allclose(start_ll, [snap], atol=1e-9)
    assert abs(seg["full_length_m"] - 0.03 * 110540) < 1e-6


def test_reach_upstream_segment_clamps_at_reach_end():
    coords = np.array([[LON0, LAT0], [LON0, LAT0 + 0.03]])
    seg = reach_upstream_segment(coords, (LON0, LAT0 + 0.028), length_ft=1000)
    expected = (0.03 - 0.028) * 110540
    assert abs(seg["length_m"] - expected) < 1e-6


def test_reach_upstream_segment_lon0_lat0_override():
    coords = np.array([[LON0, LAT0], [LON0, LAT0 + 0.03]])
    seg = reach_upstream_segment(coords, (LON0, LAT0), length_ft=1000,
                                 lon0=LON0 + 1, lat0=LAT0 + 1)
    assert seg["lon0"] == LON0 + 1 and seg["lat0"] == LAT0 + 1
    assert abs(seg["length_m"] - 304.8) < 1.0


# --------------------------------------------------------------------------- #
# 4. build_transects (xs_geometry_tests.R section 4 + hand-computed geometry)
# --------------------------------------------------------------------------- #


def _north_segment():
    coords = np.array([[LON0, LAT0], [LON0, LAT0 + 0.03]])
    return reach_upstream_segment(coords, (LON0, LAT0), length_ft=1000)


def test_build_transects_shape_and_orientation():
    seg = _north_segment()
    tr = build_transects(seg, n_transects=3, half_m=50, n_samp=21)
    assert len(tr) == 3
    assert tr[0]["lonlat"].shape == (21, 2)
    assert len(tr[0]["stations"]) == 21
    assert abs(tr[0]["stations"].min() + 50) < 1e-9
    assert abs(tr[0]["stations"].max() - 50) < 1e-9
    # reach is N-S, so each transect is E-W: lat ~constant across it, lon varies
    llt = tr[1]["lonlat"]
    assert abs(llt[0, 1] - llt[20, 1]) < 1e-4
    assert abs(llt[0, 0] - llt[20, 0]) > 1e-4
    assert abs(tr[1]["center"]["lon"] - LON0) < 1e-6


def test_build_transects_default_fracs_and_index():
    seg = _north_segment()
    tr = build_transects(seg, n_transects=3, half_m=50, n_samp=21)
    assert [t["index"] for t in tr] == [1, 2, 3]      # NOTE(parity): 1-based
    assert np.allclose([t["frac"] for t in tr], [0.15, 0.5, 0.85])
    L = polyline_length(seg["xy"])
    assert np.allclose([t["station_along"] for t in tr],
                       [0.15 * L, 0.5 * L, 0.85 * L])
    # single transect -> mid-reach
    t1 = build_transects(seg, n_transects=1, half_m=50, n_samp=5)
    assert len(t1) == 1 and t1[0]["frac"] == 0.5


def test_build_transects_explicit_fracs():
    seg = _north_segment()
    tr = build_transects(seg, n_transects=3, half_m=50, n_samp=11,
                         fracs=[0.25, 0.75])
    assert len(tr) == 2                                # fracs override n_transects
    assert [t["index"] for t in tr] == [1, 2]
    L = polyline_length(seg["xy"])
    assert abs(tr[0]["station_along"] - 0.25 * L) < 1e-9


def test_build_transects_hand_computed_metric_geometry():
    # Straight metric segment due north; at frac 0.5 the point is (0, 150),
    # the tangent is (0, 1), so the normal is (-1, 0): sample x = -stations.
    seg = {"xy": np.array([[0.0, 0.0], [0.0, 300.0]]), "lon0": LON0, "lat0": LAT0}
    tr = build_transects(seg, n_transects=3, half_m=50, n_samp=5)
    t2 = tr[1]
    assert abs(t2["station_along"] - 150) < 1e-9
    assert np.allclose(t2["stations"], [-50, -25, 0, 25, 50])
    expected = to_ll(-t2["stations"], np.full(5, 150.0), LON0, LAT0)
    assert np.allclose(t2["lonlat"], expected, atol=1e-12)
    # centre sample (station 0) is the centre point
    assert np.allclose(t2["lonlat"][2], [t2["center"]["lon"], t2["center"]["lat"]])
    assert np.allclose(to_m(t2["center"]["lon"], t2["center"]["lat"], LON0, LAT0),
                       [[0.0, 150.0]], atol=1e-6)


def test_build_transects_perpendicular_to_reach():
    seg = _north_segment()
    tr = build_transects(seg, n_transects=3, half_m=50, n_samp=21)
    for t in tr:
        m = to_m(t["lonlat"][:, 0], t["lonlat"][:, 1], LON0, LAT0)
        d = m[-1] - m[0]                        # transect direction (metric)
        tangent = np.array([0.0, 1.0])          # reach runs due north
        assert abs(float(d @ tangent)) < 1e-6 * float(np.hypot(*d))
        assert abs(float(np.hypot(*d)) - 100) < 1e-6   # full width = 2 * half_m


# --------------------------------------------------------------------------- #
# 5. reach_from_bearing (xs_geometry_tests.R section 5 + due-east extra)
# --------------------------------------------------------------------------- #


def test_reach_from_bearing_due_north():
    rb = reach_from_bearing(39.7, -82.8, 0, 1000)
    assert rb.shape == (2, 2)
    assert rb[1, 1] > rb[0, 1]                       # end is north of start
    assert abs(rb[1, 0] - rb[0, 0]) < 1e-9           # lon unchanged
    assert abs((rb[1, 1] - rb[0, 1]) * 110540 - 304.8) < 1.0


def test_reach_from_bearing_due_east():
    rb = reach_from_bearing(39.7, -82.8, 90, 1000)
    kx = 111320 * math.cos(math.radians(39.7))
    assert abs((rb[1, 0] - rb[0, 0]) * kx - 304.8) < 1e-6
    assert abs(rb[1, 1] - rb[0, 1]) < 1e-9           # lat ~unchanged


# --------------------------------------------------------------------------- #
# 6. transect_half_width clamps (xs_geometry_tests.R section 6 + extras)
# --------------------------------------------------------------------------- #


def test_transect_half_width_clamps():
    assert transect_half_width(5) == 80
    assert transect_half_width(20) == 160
    assert transect_half_width(40) == 250
    assert transect_half_width(float("nan")) == 80   # R: NA -> default bf 10
    assert transect_half_width(None) == 80
    assert transect_half_width(0) == 80
    assert transect_half_width(-3) == 80
    assert transect_half_width("abc") == 80          # R: as.numeric("abc") -> NA
    assert transect_half_width("20") == 160          # numeric strings convert
    assert transect_half_width([20]) == 160          # length-1 vector, like R
    assert transect_half_width([1, 2]) == 80         # length != 1 -> default
    assert transect_half_width(12.5) == 100
    assert transect_half_width(31.25) == 250         # 8 * 31.25 == upper clamp
