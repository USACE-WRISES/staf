"""The offline reach line: the main-path walk, the reach built like the app's,
and continuity across a chunk border."""
from __future__ import annotations

import numpy as np
from shapely.geometry import LineString, MultiLineString, shape

from builder import reaches
from builder.network import NetworkIndex

LON = -78.5
DEG_PER_M = 1 / 111_000.0            # about, along a meridian


def _network():
    # three reaches on one main path: 1 (downstream) <- 2 <- 3 (headwater), 150 m each
    comid = np.array([1, 2, 3], dtype="int64")
    hydroseq = np.array([10, 20, 30], dtype="int64")
    up = np.array([20, 30, 0], dtype="int64")
    dn = np.array([0, 10, 20], dtype="int64")
    length = np.array([0.15, 0.15, 0.15])
    return NetworkIndex(comid, hydroseq, up, dn, length)


def _segment(lat0, lat1):
    return MultiLineString([[(LON, lat0), (LON, lat1)]])


def _geoms():
    lat = 38.0
    step = 150 * DEG_PER_M
    return {1: _segment(lat + step, lat), 2: _segment(lat + 2 * step, lat + step),
            3: _segment(lat + 3 * step, lat + 2 * step)}


def test_upstream_main_follows_the_uphydroseq_chain_within_the_distance():
    net = _network()
    assert net.upstream_main(1, 1.0) == [1, 2, 3]
    assert net.upstream_main(1, 0.2) == [1, 2]              # 0.15 travelled, the next hop would exceed
    assert net.upstream_main(3, 1.0) == [3]                  # a headwater
    assert net.upstream_main(99, 1.0) is None


def test_reach_line_builds_the_thousand_foot_reach_upstream_of_the_anchor():
    net, geoms = _network(), _geoms()
    lat0 = 38.0 + 10 * DEG_PER_M                              # the anchor: 10 m above reach 1's outlet
    fc, actual_ft, warnings, chain = reaches.reach_line(1, lat0, LON, geoms, net)
    assert chain == [1, 2, 3] and fc is not None
    assert abs(actual_ft - 1000.0) < 2.0 and warnings == []
    line = shape(fc["features"][0]["geometry"])
    lats = [pt[1] for pt in line.coords]
    assert min(lats) >= lat0 - 1e-6 and max(lats) > lat0     # extends upstream from the anchor only


def test_a_headwater_reach_is_reported_short_like_the_app():
    net, geoms = _network(), _geoms()
    lat_top = 38.0 + 3 * 150 * DEG_PER_M
    fc, actual_ft, warnings, chain = reaches.reach_line(3, lat_top - 10 * DEG_PER_M, LON, geoms, net)
    assert chain == [3] and actual_ft < 1000 and any("only" in w for w in warnings)


def test_a_chain_crossing_the_chunk_border_is_completed_from_outside_geometry():
    net = _network()
    inside = {1: _geoms()[1]}                                 # only reach 1 lives in this chunk
    lat0 = 38.0 + 10 * DEG_PER_M
    fc, actual_ft, warnings, chain = reaches.reach_line(1, lat0, LON, inside, net)
    assert chain == [1, 2, 3] and actual_ft < 1000 and any("no geometry" in w for w in warnings)
    assert reaches.missing_comids({1: chain}, inside) == [2, 3]
    completed = dict(inside)
    completed.update({k: v for k, v in _geoms().items() if k in (2, 3)})   # the national flowline read
    fc, actual_ft, warnings, chain = reaches.reach_line(1, lat0, LON, completed, net)
    assert abs(actual_ft - 1000.0) < 2.0 and warnings == []


def test_nav_km_matches_the_app_rule_and_explode_flattens_multilines():
    assert reaches.nav_km(0.0, 1000.0) == 1.5                # 4 x 0.3048 km + 0.3, rounded
    assert reaches.nav_km(2.0, 1000.0) == 2.6                # own length + reach + 0.3
    parts = reaches.explode([MultiLineString([[(0, 0), (1, 1)], [(1, 1), (2, 2)]]), LineString([(3, 3), (4, 4)]), None])
    assert len(parts) == 3 and all(p.geom_type == "LineString" for p in parts)
