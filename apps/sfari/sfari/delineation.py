"""Map geometry helpers for the delineation overlays.

The delineation itself is the STAF site engine's (the exact watershed and
the assessment reach, see ``pipeline.delineate_from_engine``); this module
keeps the display-only helpers the map needs: a vertex-capped simplification
for very large polygons and combined bounds for ``fit_bounds``. Heavy imports
are local so the rest of the package stays importable without the geospatial
stack.
"""
from __future__ import annotations

from typing import Optional

FT_PER_M = 3.28083989501312
DEFAULT_REACH_FT = 1000.0
CRS_WGS84 = 4326
CRS_ALBERS = 5070  # USGS CONUS Albers Equal Area (metres)


def display_simplify(geojson: Optional[dict], max_vertices: int = 1500,
                     tol_deg: float = 0.0002) -> Optional[dict]:
    """Display-only simplification of a polygon FeatureCollection.

    Returns ``geojson`` unchanged when it has <= ``max_vertices`` (normal reaches
    stay fully faithful to the catchment boundary); otherwise simplifies the
    geometry (~22 m at ``tol_deg``, topology-preserving) so very large river
    basins render on the map without overwhelming the widget. Callers keep the
    full-resolution geometry for the reported area and exports. Never raises.
    """
    if not geojson or not geojson.get("features"):
        return geojson
    try:
        import geopandas as gpd
        g = gpd.GeoDataFrame.from_features(geojson["features"], crs=CRS_WGS84)
        if int(g.geometry.count_coordinates().sum()) <= max_vertices:
            return geojson
        g = g.copy()
        g["geometry"] = g.geometry.simplify(tol_deg, preserve_topology=True)
        return g.__geo_interface__
    except Exception:  # noqa: BLE001 - display nicety; fall back to full geometry
        return geojson


def geojson_bounds(*geojsons, pad: float = 0.06):
    """Combined bounds of one or more FeatureCollections as ``[[S, W], [N, E]]``
    (the format for ``ipyleaflet.Map.fit_bounds``), with a small margin. Returns
    None if nothing usable. Never raises.
    """
    try:
        import geopandas as gpd
        boxes = []
        for gj in geojsons:
            if gj and gj.get("features"):
                gdf = gpd.GeoDataFrame.from_features(gj["features"], crs=CRS_WGS84)
                gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
                if not gdf.empty:
                    boxes.append(gdf.total_bounds)  # (minx, miny, maxx, maxy)
        if not boxes:
            return None
        minx = min(b[0] for b in boxes); miny = min(b[1] for b in boxes)
        maxx = max(b[2] for b in boxes); maxy = max(b[3] for b in boxes)
        dx = (maxx - minx) * pad or 0.001
        dy = (maxy - miny) * pad or 0.001
        return [[miny - dy, minx - dx], [maxy + dy, maxx + dx]]
    except Exception:  # noqa: BLE001
        return None
