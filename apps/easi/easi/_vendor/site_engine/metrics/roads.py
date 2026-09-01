"""Road density over the true watershed (TIGERweb roads, clipped length/area).

The exact-watershed analog of StreamCat ``rddens``: TIGER road features
intersecting the watershed are fetched (paged POST polygon queries), clipped
to the full-resolution polygon locally, and reported as km of road per km2.

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


@register("roads")
def compute(record: dict, tree_geoms: list) -> dict:
    ws = watershed_geom((record.get("watershed") or {}).get("polygon"))
    area_sqkm = (record.get("watershed") or {}).get("areaSqkm")
    if ws is None or not area_sqkm:
        return {"roadDensity": metric_entry(
            None, "km/km2", "TIGERweb roads", VINTAGES["tigerRoads"],
            "pointWatershed", ["watershed polygon or area unavailable"])}
    poly = esri_polygon(ws)
    if poly is None:
        return {"roadDensity": metric_entry(
            None, "km/km2", "TIGERweb roads", VINTAGES["tigerRoads"],
            "pointWatershed", ["polygon could not be encoded for the query"])}
    feats: list[dict] = []
    for layer_id, label in TIGER_ROAD_LAYERS:
        page = post_query_features(f"{_TIGER_BASE}/{layer_id}/query", poly,
                                   "MTFCC", return_geometry=True)
        if page is None:
            return {"roadDensity": metric_entry(
                None, "km/km2", "TIGERweb roads", VINTAGES["tigerRoads"],
                "pointWatershed",
                [f"{label} road query failed or was truncated"])}
        feats.extend(page)
    try:
        from shapely.geometry import shape

        lines = [shape(f["geometry"]) for f in feats if f.get("geometry")]
        if lines:
            ws_albers = albers(ws).iloc[0]
            clipped = albers(lines).intersection(ws_albers)
            length_km = float(clipped.length.sum()) / 1000.0
        else:
            length_km = 0.0
        density = round(length_km / float(area_sqkm), 4)
        return {
            "roadLengthKm": metric_entry(
                round(length_km, 3), "km", "TIGERweb roads",
                VINTAGES["tigerRoads"], "pointWatershed"),
            "roadDensity": metric_entry(
                density, "km/km2", "TIGERweb roads", VINTAGES["tigerRoads"],
                "pointWatershed"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"roadDensity": metric_entry(
            None, "km/km2", "TIGERweb roads", VINTAGES["tigerRoads"],
            "pointWatershed", [f"road clip failed: {exc}"])}
