"""Road density and road-stream crossings over the HR reach watershed.

The HR reach watershed analogs of StreamCat ``rddens`` and ``rdcrs``: TIGER road
features intersecting the watershed are fetched (paged POST polygon queries),
clipped to the full-resolution polygon locally, and reported as km of road per
km2; the same clipped roads are intersected with the walked NHDPlus HR
flowlines (the streams draining to the site) and every meeting point counts
as a crossing, reported as a count and per km2 (2026-09-05). A road running
along a channel shares a segment, not a point, and does not count.

TIGERweb splits roads across scale-banded layers, so all three feature layers
are queried: 2 (Primary Roads), 6 (Secondary Roads), 8 (Local Roads). Local
roads dominate the density in most watersheds; querying only layer 2 reads
zero almost everywhere rural. Never raises.
"""
from __future__ import annotations

from ..provenance import VINTAGES, metric_entry
from . import register
from .common import albers, esri_polygon, post_query_features, watershed_geom

_TIGER_BASE = ("https://tigerweb.geo.census.gov/arcgis/rest/services/"
               "TIGERweb/Transportation/MapServer")
# (layer id, label): primary + secondary + local covers the road network.
TIGER_ROAD_LAYERS = ((2, "primary"), (6, "secondary"), (8, "local"))


_SRC = "TIGERweb roads"
_SRC_CROSS = "TIGERweb roads x NHDPlus HR flowlines"


def _unavailable(reason: str) -> dict:
    return {
        "roadDensity": metric_entry(None, "km/km2", _SRC, VINTAGES["tigerRoads"],
                                    "pointWatershed", [reason]),
        "roadCrossingDensity": metric_entry(None, "crossings/km2", _SRC_CROSS,
                                            VINTAGES["tigerRoads"], "pointWatershed",
                                            [reason]),
    }


def _n_points(geom) -> int:
    """Points in an intersection result; shared segments count for nothing."""
    if geom is None or geom.is_empty:
        return 0
    kind = geom.geom_type
    if kind == "Point":
        return 1
    if kind == "MultiPoint":
        return len(geom.geoms)
    if kind == "GeometryCollection":
        return sum(_n_points(g) for g in geom.geoms)
    return 0


def count_crossings(roads, streams) -> int:
    """Road-stream crossings: the points where a road line meets a stream line
    (``roads`` and ``streams`` are GeoSeries in the same projected CRS)."""
    if streams is None or len(streams) == 0:
        return 0
    stream_union = streams.union_all()
    if stream_union.is_empty:
        return 0
    return sum(_n_points(road.intersection(stream_union))
               for road in roads if road is not None and not road.is_empty)


@register("roads")
def compute(record: dict, tree_geoms: list) -> dict:
    ws = watershed_geom((record.get("watershed") or {}).get("polygon"))
    area_sqkm = (record.get("watershed") or {}).get("areaSqkm")
    if ws is None or not area_sqkm:
        return _unavailable("watershed polygon or area unavailable")
    poly = esri_polygon(ws)
    if poly is None:
        return _unavailable("polygon could not be encoded for the query")
    feats: list[dict] = []
    for layer_id, label in TIGER_ROAD_LAYERS:
        page = post_query_features(f"{_TIGER_BASE}/{layer_id}/query", poly,
                                   "MTFCC", return_geometry=True)
        if page is None:
            return _unavailable(f"{label} road query failed or was truncated")
        feats.extend(page)
    try:
        from shapely.geometry import shape

        lines = [shape(f["geometry"]) for f in feats if f.get("geometry")]
        n_cross = 0
        if lines:
            ws_albers = albers(ws).iloc[0]
            clipped = albers(lines).intersection(ws_albers)
            length_km = float(clipped.length.sum()) / 1000.0
            streams = [shape(g) for g in (tree_geoms or []) if g]
            if streams:
                n_cross = count_crossings(clipped, albers(streams))
        else:
            length_km = 0.0
        density = round(length_km / float(area_sqkm), 4)
        return {
            "roadLengthKm": metric_entry(
                round(length_km, 3), "km", _SRC, VINTAGES["tigerRoads"],
                "pointWatershed"),
            "roadDensity": metric_entry(
                density, "km/km2", _SRC, VINTAGES["tigerRoads"], "pointWatershed"),
            "roadCrossings": metric_entry(
                int(n_cross), "count", _SRC_CROSS, VINTAGES["tigerRoads"],
                "pointWatershed"),
            "roadCrossingDensity": metric_entry(
                round(n_cross / float(area_sqkm), 4), "crossings/km2", _SRC_CROSS,
                VINTAGES["tigerRoads"], "pointWatershed"),
        }
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"road clip failed: {exc}")
