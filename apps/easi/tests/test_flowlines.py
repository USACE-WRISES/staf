"""Offline tests for flowline snapping (no network; hand-built FeatureCollection)."""
from __future__ import annotations

from easi.datasources import flowlines


def _fc(comid=None):
    # a short E-W flowline at latitude 40.0, near Columbus OH
    props = {"comid": comid} if comid is not None else {}
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": props,
         "geometry": {"type": "LineString",
                      "coordinates": [[-83.01, 40.00], [-82.99, 40.00]]}}]}


def test_nearest_point_snaps_close_click():
    # ~180 ft north of the line -> snaps onto it, small distance
    hit = flowlines.nearest_point_on_lines(_fc(), 40.0005, -83.00)
    assert hit is not None
    snap_lat, snap_lon, dist_ft, comid = hit
    assert abs(snap_lat - 40.0) < 1e-3          # snapped onto the lat-40.0 line
    assert dist_ft < 300
    assert -83.011 <= snap_lon <= -82.989
    assert comid is None                         # no comid in this feature


def test_nearest_point_returns_comid():
    hit = flowlines.nearest_point_on_lines(_fc(comid=5214981), 40.0005, -83.00)
    assert hit is not None and hit[3] == 5214981


def test_nearest_point_far_click_large_distance():
    hit = flowlines.nearest_point_on_lines(_fc(), 40.05, -83.00)  # ~3.5 mi north
    assert hit is not None
    assert hit[2] > 1000


def test_nearest_point_none_on_empty():
    assert flowlines.nearest_point_on_lines(None, 40, -83) is None
    assert flowlines.nearest_point_on_lines(
        {"type": "FeatureCollection", "features": []}, 40, -83) is None


def test_flowlines_in_bbox_guards():
    assert flowlines.flowlines_in_bbox(-90, 30, -80, 45) is None     # ~150 deg² — too large
    assert flowlines.flowlines_in_bbox(-83.0, 40.0, -83.0, 40.1) is None  # zero width (W==E)


# --- display_simplify (large-basin map rendering) -------------------------- #
def _ring_fc(n):
    import math
    # an n-vertex ~jagged circle polygon near (40, -83)
    coords = [[-83.0 + 0.02 * math.cos(t), 40.0 + 0.02 * math.sin(t) + 0.001 * math.sin(20 * t)]
              for t in [i * 2 * math.pi / n for i in range(n)]]
    coords.append(coords[0])
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [coords]}}]}


def _count(fc):
    import geopandas as gpd
    return int(gpd.GeoDataFrame.from_features(fc["features"], crs=4326).geometry.count_coordinates().sum())


def test_display_simplify_reduces_large_polygon():
    from easi import delineation
    big = _ring_fc(4000)
    out = delineation.display_simplify(big, max_vertices=1500)
    assert _count(out) < _count(big)            # simplified
    assert out["features"][0]["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_display_simplify_keeps_small_polygon_unchanged():
    from easi import delineation
    small = _ring_fc(80)
    assert delineation.display_simplify(small, max_vertices=1500) is small  # returned as-is


def test_display_simplify_handles_none():
    from easi import delineation
    assert delineation.display_simplify(None) is None


def test_geojson_bounds():
    from easi import delineation
    b = delineation.geojson_bounds(_ring_fc(80))   # circle r=0.02 around (40, -83)
    assert b is not None
    (s, w), (n, e) = b
    assert s < 40 < n and w < -83 < e              # encloses the center
    assert (n - s) > 0.04 and (e - w) > 0.04       # ~0.04 span + padding
    assert delineation.geojson_bounds(None) is None


# --- reach trimming: anchor the reach at the snapped point ------------------ #
def _line(*pts):
    from shapely.geometry import LineString
    return LineString(pts)


def _endpoints(geom):
    from shapely.geometry import Point
    return Point(geom.coords[0]), Point(geom.coords[-1])


def _reproj_5070(line4326):
    import geopandas as gpd
    return gpd.GeoSeries([line4326], crs=4326).to_crs(5070).iloc[0]


def test_trim_upstream_outlet_at_start_extends_toward_end():
    from shapely.geometry import Point
    from easi.delineation import _trim_upstream
    merged = _line((0, 0), (1000, 0))               # coords[0] is the outlet
    snap = Point(300, 0)
    seg = _trim_upstream(merged, snap, 400.0, outlet_at_start=True)
    assert abs(seg.length - 400.0) < 1e-6           # full requested length
    assert min(snap.distance(e) for e in _endpoints(seg)) < 1e-6   # snap = downstream end
    assert max(p.x for p in _endpoints(seg)) == 700.0  # upstream toward coords[-1]


def test_trim_upstream_outlet_at_end_extends_toward_start():
    from shapely.geometry import Point
    from easi.delineation import _trim_upstream
    merged = _line((0, 0), (1000, 0))               # coords[-1] is the outlet
    snap = Point(600, 0)
    seg = _trim_upstream(merged, snap, 400.0, outlet_at_start=False)
    assert abs(seg.length - 400.0) < 1e-6
    assert min(snap.distance(e) for e in _endpoints(seg)) < 1e-6
    assert min(p.x for p in _endpoints(seg)) == 200.0  # upstream toward coords[0]


def test_trim_upstream_none_uses_longer_side():
    from shapely.geometry import Point
    from easi.delineation import _trim_upstream
    merged = _line((0, 0), (1000, 0))
    snap = Point(200, 0)                            # shorter side (coords[0]) = outlet
    seg = _trim_upstream(merged, snap, 400.0, outlet_at_start=None)
    assert abs(seg.length - 400.0) < 1e-6
    assert min(snap.distance(e) for e in _endpoints(seg)) < 1e-6
    assert max(p.x for p in _endpoints(seg)) == 600.0  # longer side is upstream


def test_trim_upstream_clamps_to_available_length():
    from shapely.geometry import Point
    from easi.delineation import _trim_upstream
    merged = _line((0, 0), (200, 0))               # only 200 m of line
    seg = _trim_upstream(merged, Point(0, 0), 400.0, outlet_at_start=True)
    assert abs(seg.length - 200.0) < 1e-6          # clamped to available


def test_outlet_at_start_detects_comid_downstream_end():
    from easi.delineation import _outlet_at_start
    # merged = COMID (-83.00 -> -82.99) + upstream (-> -82.98); coords[0] = -83.00 (outlet)
    merged = _reproj_5070(_line((-83.00, 40.0), (-82.99, 40.0), (-82.98, 40.0)))
    own = [_line((-83.00, 40.0), (-82.99, 40.0))]   # the COMID itself
    assert _outlet_at_start(merged, own) is True


def test_outlet_at_start_detects_when_path_reversed():
    from easi.delineation import _outlet_at_start
    merged = _reproj_5070(_line((-82.98, 40.0), (-82.99, 40.0), (-83.00, 40.0)))  # coords[-1]=outlet
    own = [_line((-83.00, 40.0), (-82.99, 40.0))]
    assert _outlet_at_start(merged, own) is False


def test_outlet_at_start_none_when_undetermined():
    from easi.delineation import _outlet_at_start
    merged = _reproj_5070(_line((-83.00, 40.0), (-82.99, 40.0)))  # COMID == whole path
    assert _outlet_at_start(merged, [_line((-83.00, 40.0), (-82.99, 40.0))]) is None
    assert _outlet_at_start(merged, []) is None     # no comid geometry
