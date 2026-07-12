"""Point -> Level III ecoregion / US state resolution (deep.geo, Part A2).

The resolvers back the Assessment step's region matching and the session/report region
stamp. They must be boundary-inclusive (a point exactly on an official boundary counts
as inside) and return None outside the mapped polygons.
"""
from __future__ import annotations

import json

from deep import geo


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


def test_boundary_vertex_is_included():
    # A vertex of a state polygon lies exactly on that polygon's boundary. A
    # boundary-inclusive resolver (shapely covers) must still place it inside; plain
    # ray-casting is undefined on a vertex. Pull Colorado's first exterior vertex from
    # the shipped data so the test tracks the real geometry.
    fc = json.loads(geo.STATES_PATH.read_text(encoding="utf-8"))
    feat = next(f for f in fc["features"] if (f["properties"] or {}).get("state") == "CO")
    geom = feat["geometry"]
    coords = geom["coordinates"]
    lon, lat = (coords[0][0] if geom["type"] == "Polygon" else coords[0][0][0])

    st = geo.state_at(lat, lon)
    assert st is not None, "a point on a state boundary vertex must resolve (boundary-inclusive)"
    assert len(st["code"]) == 2
    # The vertex is Colorado's own corner, so Colorado covers it.
    assert st["code"] == "CO"


def test_none_coordinates_resolve_to_none():
    assert geo.level3_at(None, None) is None
    assert geo.state_at(float("nan"), -83.0) is None
