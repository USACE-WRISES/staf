"""Point -> Level III ecoregion / US state resolution (deep.geo, Part A2).

The resolvers back the Assessment step's region matching and the session/report region
stamp. They must be boundary-inclusive (a point exactly on an official boundary counts
as inside) and return None outside the mapped polygons.

The state layer is the Census 1:500,000 cartographic boundary file
(``scripts/build_us_states.py``). The coarse choropleth layer DEEP shipped before
2026-08-22 drew New Hampshire's western edge about 2.7 km east of the Connecticut
River and labeled Mink Brook, Hanover NH as Vermont, which is why the near-border
points below are pinned and not only polygon interiors.
"""
from __future__ import annotations

from deep import geo

# The 50 states plus the District of Columbia: the layer must carry every one.
STATES_AND_DC = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT "
    "NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)


def test_interior_point_resolves_l3_and_state():
    # Central Ohio: inside EPA Level III ecoregion 55 (Eastern Corn Belt Plains) and Ohio.
    l3 = geo.level3_at(40.0, -83.5)
    assert l3 == {"code": "55", "name": "Eastern Corn Belt Plains"}
    st = geo.state_at(40.0, -83.5)
    assert st["code"] == "OH" and st["abbr"] == "OH" and st["name"] == "Ohio"


def test_offshore_point_resolves_to_none():
    # Mid-Atlantic, well outside CONUS: no ecoregion and no state.
    assert geo.level3_at(30.0, -70.0) is None
    assert geo.state_at(30.0, -70.0) is None


def test_out_of_conus_pacific_is_none():
    assert geo.state_at(45.0, -140.0) is None


def test_border_point_mink_brook_is_new_hampshire():
    # Mink Brook, Hanover NH (COMID 9327030): about 2 km east of the Vermont line, which
    # follows the Connecticut River's west bank. The Region step's site line, the
    # session stamp, and the CSV/GeoJSON/PDF exports all print this value.
    st = geo.state_at(43.6896, -72.2840)
    assert st == {"code": "NH", "abbr": "NH", "name": "New Hampshire"}
    assert geo.level3_at(43.6896, -72.2840) == {"code": "58", "name": "Northeastern Highlands"}


def test_connecticut_river_west_bank_is_vermont():
    # Norwich VT village, about 0.9 km west of the same line: the other side resolves too.
    st = geo.state_at(43.7153, -72.3069)
    assert st is not None and st["code"] == "VT" and st["name"] == "Vermont"


def test_four_corners_quadrants():
    # About 1 km from the Four Corners monument in each diagonal direction: a second
    # near-border check that does not depend on a river's meanders.
    lat, lon, d = 36.998979, -109.045172, 0.01
    expected = {
        (lat + d, lon + d): "CO",
        (lat + d, lon - d): "UT",
        (lat - d, lon + d): "NM",
        (lat - d, lon - d): "AZ",
    }
    for (la, lo), code in expected.items():
        st = geo.state_at(la, lo)
        assert st is not None and st["code"] == code, (la, lo, st)


def test_states_layer_is_complete():
    # A regenerated layer cannot ship a missing state, a duplicate code, or no source.
    assert len(STATES_AND_DC) == 51
    fc = geo._read_feature_collection(geo.STATES_PATH)
    feats = fc["features"]
    codes = [f["properties"]["state"] for f in feats]
    assert len(codes) == len(set(codes))
    assert all(len(c) == 2 and c.isupper() for c in codes)
    assert all(f["properties"]["name"] for f in feats)
    assert STATES_AND_DC <= set(codes)
    assert fc["source"]["dataset"].startswith("Census")


def test_boundary_vertex_is_included():
    # A vertex of a state polygon lies exactly on that polygon's boundary. A
    # boundary-inclusive resolver (shapely covers) must still place it inside; plain
    # ray-casting is undefined on a vertex. Pull Colorado's first exterior vertex from
    # the shipped data so the test tracks the real geometry. The Census layer is
    # topologically consistent, so a vertex on a shared line is covered by both
    # neighbors and the first feature in file order wins: assert membership in the
    # covering set rather than one fixed neighbor.
    from shapely.geometry import Point

    fc = geo._read_feature_collection(geo.STATES_PATH)
    feat = next(f for f in fc["features"] if (f["properties"] or {}).get("state") == "CO")
    geom = feat["geometry"]
    coords = geom["coordinates"]
    lon, lat = (coords[0][0] if geom["type"] == "Polygon" else coords[0][0][0])

    st = geo.state_at(lat, lon)
    assert st is not None, "a point on a state boundary vertex must resolve (boundary-inclusive)"
    assert len(st["code"]) == 2
    pt = Point(lon, lat)
    covering = {value for value, _name, pg, _bounds in geo._index(str(geo.STATES_PATH), "state", "name")
                if pg.covers(pt)}
    assert "CO" in covering
    assert st["code"] in covering


def test_none_coordinates_resolve_to_none():
    assert geo.level3_at(None, None) is None
    assert geo.state_at(float("nan"), -83.0) is None
