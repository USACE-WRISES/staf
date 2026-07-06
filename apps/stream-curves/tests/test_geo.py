"""Tests for streamcurves.geo (port of app/helpers/geo_util.R)."""

import math

import numpy as np

from streamcurves import geo


# --------------------------------------------------------------------------- #
# point-in-polygon (hand-computed, incl. holes)
# --------------------------------------------------------------------------- #
_OUTER = [[0, 0], [4, 0], [4, 4], [0, 4]]
_HOLE = [[1, 1], [3, 1], [3, 3], [1, 3]]


def test_point_in_ring():
    assert geo.point_in_ring(2, 2, _OUTER) is True
    assert geo.point_in_ring(5, 5, _OUTER) is False
    # degenerate ring (<3 pts) is never inside
    assert geo.point_in_ring(0, 0, [[0, 0], [1, 1]]) is False


def test_point_in_polygon_with_hole():
    # inside the outer ring but outside the hole
    assert geo.point_in_polygon_rings(0.5, 0.5, [_OUTER, _HOLE]) is True
    # inside the hole -> not in the polygon
    assert geo.point_in_polygon_rings(2, 2, [_OUTER, _HOLE]) is False
    # outside the outer ring
    assert geo.point_in_polygon_rings(5, 5, [_OUTER, _HOLE]) is False
    # no rings
    assert geo.point_in_polygon_rings(0, 0, []) is False


# --------------------------------------------------------------------------- #
# haversine (known distance + vectorisation over the 2nd point)
# --------------------------------------------------------------------------- #
def test_haversine_known_distance():
    # 1 degree of latitude at the equator = R * pi/180 with R = 6_371_000 m.
    expected = 6371000.0 * math.pi / 180.0
    d = geo.haversine_m(0.0, 0.0, 0.0, 1.0)
    assert isinstance(d, float)
    assert abs(d - expected) < 1e-6
    # identical points -> 0
    assert geo.haversine_m(-83.0, 40.0, -83.0, 40.0) == 0.0


def test_haversine_vectorised_over_second_point():
    out = geo.haversine_m(0.0, 0.0, np.array([0.0, 0.0]), np.array([0.0, 1.0]))
    assert isinstance(out, np.ndarray)
    assert out[0] == 0.0
    assert abs(out[1] - 6371000.0 * math.pi / 180.0) < 1e-6


# --------------------------------------------------------------------------- #
# spherical polygon area (compare to the same formula the R file uses)
# --------------------------------------------------------------------------- #
def _reference_area(lon, lat):
    radius = 6378137.0
    rad = math.pi / 180.0
    lonr = [v * rad for v in lon]
    latr = [v * rad for v in lat]
    n = len(lon)
    total = 0.0
    for i in range(n):
        j = 0 if i == n - 1 else i + 1
        total += (lonr[j] - lonr[i]) * (2 + math.sin(latr[i]) + math.sin(latr[j]))
    return abs(total * radius * radius / 2.0)


def test_spherical_area_1x1_at_equator():
    lon = [0, 1, 1, 0]
    lat = [0, 0, 1, 1]
    area = geo.spherical_polygon_area_m2(lon, lat)
    assert area == _reference_area(lon, lat)  # exact parity with the R formula
    # ~12,300-12,400 km^2 for a 1x1 degree cell at the equator
    assert 12_000e6 < area < 12_500e6
    # <3 vertices -> 0
    assert geo.spherical_polygon_area_m2([0, 1], [0, 0]) == 0.0


# --------------------------------------------------------------------------- #
# GeoJSON extraction + indexed lookup (synthetic FeatureCollection)
# --------------------------------------------------------------------------- #
def _square(x0, y0, size):
    return [[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size], [x0, y0]]


def _fc():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"state": "AA"},
                "geometry": {"type": "Polygon", "coordinates": [_square(0, 0, 2)]},
            },
            {
                "type": "Feature",
                "properties": {"state": "BB"},
                "geometry": {"type": "Polygon", "coordinates": [_square(10, 10, 2)]},
            },
        ],
    }


def test_geom_polygons_polygon_and_multipolygon():
    poly = geo._geom_polygons({"type": "Polygon", "coordinates": [_square(0, 0, 1)]})
    assert len(poly) == 1 and len(poly[0]) == 1  # 1 polygon, 1 ring
    multi = geo._geom_polygons(
        {"type": "MultiPolygon", "coordinates": [[_square(0, 0, 1)], [_square(5, 5, 1)]]}
    )
    assert len(multi) == 2
    assert geo._geom_polygons({"type": "Point", "coordinates": [0, 0]}) == []
    assert geo._geom_polygons(None) == []


def test_prepare_index_and_locate():
    idx = geo.prepare_polygon_index(_fc()["features"], "state")
    assert len(idx) == 2
    assert idx[0]["bbox"] == {"xmin": 0, "ymin": 0, "xmax": 2, "ymax": 2}
    assert geo.locate_polygon_property(1, 1, idx) == "AA"
    assert geo.locate_polygon_property(11, 11, idx) == "BB"
    # outside every bbox -> default
    assert geo.locate_polygon_property(50, 50, idx, default="none") == "none"
    # non-finite -> default
    assert geo.locate_polygon_property(float("nan"), 1, idx, default=None) is None


# --------------------------------------------------------------------------- #
# state name -> abbreviation
# --------------------------------------------------------------------------- #
def test_state_abbr_from_name():
    assert geo.state_abbr_from_name("Ohio") == "OH"
    assert geo.state_abbr_from_name("  district of columbia ") == "DC"
    assert geo.state_abbr_from_name("Atlantis") is None
    assert geo.state_abbr_from_name(None) is None


def test_bieger_division_missing_file_returns_usa():
    assert geo.bieger_division_at(0, 0, "does_not_exist.geojson") == "USA"


def test_bieger_division_resolves_conus_point_with_geomorph_mapping():
    # Wiring regression: callers must pass geomorph's bieger_division_abbr —
    # without it every site silently falls back to the national "USA" curve
    # (wrong Bieger coefficients for the cross-section tool and the import
    # wizard's pred_BW/BD/BA columns). ECBP1 (OSAM) sits in the Interior
    # Plains on the bundled physio_divisions.geojson.
    from streamcurves.geomorph import bieger_division_abbr
    from streamcurves.paths import DATA_DIR

    physio = DATA_DIR / "physio_divisions.geojson"
    assert physio.exists()
    assert (
        geo.bieger_division_at(
            -82.8485, 39.7577, physio, division_abbr=bieger_division_abbr
        )
        == "IPL"
    )
    # without the mapping the same point degrades to the national curve
    assert geo.bieger_division_at(-82.8485, 39.7577, physio) == "USA"
