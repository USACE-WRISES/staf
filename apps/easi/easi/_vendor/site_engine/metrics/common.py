"""Shared helpers for Layer B metric modules (Esri polygon queries, CRS math)."""
from __future__ import annotations

from typing import Optional

import requests

from ..geometry import CRS_ALBERS, CRS_WGS84

# Simplification (degrees) applied to the watershed before it rides a service
# query; the precise clip always happens locally against the full polygon.
_QUERY_SIMPLIFY_DEG = 0.0005
_MAX_PAGES = 20


def watershed_geom(fc: Optional[dict]):
    """Shapely geometry (EPSG:4326) of a watershed FeatureCollection, or None."""
    if not fc or not fc.get("features"):
        return None
    try:
        import geopandas as gpd
        g = gpd.GeoDataFrame.from_features(fc["features"], crs=CRS_WGS84)
        g = g[g.geometry.notna() & ~g.geometry.is_empty]
        if g.empty:
            return None
        return g.geometry.union_all()
    except Exception:  # noqa: BLE001
        return None


def esri_polygon(geom) -> Optional[dict]:
    """Esri JSON polygon (rings) for a shapely (Multi)Polygon, simplified for
    transport. Returns None on failure."""
    try:
        simple = geom.simplify(_QUERY_SIMPLIFY_DEG, preserve_topology=True)
        polys = list(simple.geoms) if simple.geom_type == "MultiPolygon" else [simple]
        rings = []
        for p in polys:
            rings.append([[round(x, 6), round(y, 6)] for x, y in p.exterior.coords])
            for hole in p.interiors:
                rings.append([[round(x, 6), round(y, 6)] for x, y in hole.coords])
        return {"rings": rings, "spatialReference": {"wkid": 4326}}
    except Exception:  # noqa: BLE001
        return None


def post_query_features(url: str, geometry: dict, out_fields: str,
                        *, return_geometry: bool, timeout: float = 60.0
                        ) -> Optional[list[dict]]:
    """POST an Esri polygon query, following result pages. None on failure."""
    import json as _json

    feats: list[dict] = []
    offset = 0
    for _ in range(_MAX_PAGES):
        data = None
        try:
            r = requests.post(url, data={
                "geometry": _json.dumps(geometry),
                "geometryType": "esriGeometryPolygon", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": out_fields,
                "returnGeometry": str(return_geometry).lower(),
                "outSR": "4326", "resultOffset": offset, "f": "geojson"},
                timeout=timeout)
            if r.status_code == 200:
                data = r.json()
        except Exception:  # noqa: BLE001
            data = None
        if not isinstance(data, dict) or "error" in data:
            return None
        page = data.get("features") or []
        feats.extend(page)
        exceeded = bool(data.get("exceededTransferLimit")
                        or (data.get("properties") or {}).get("exceededTransferLimit"))
        if not exceeded or not page:
            return feats
        offset += len(page)
    return None  # too many pages: incomplete, never silently partial


def albers(geom_or_series):
    """Project shapely geometry/list to EPSG:5070 as a GeoSeries."""
    import geopandas as gpd
    geoms = geom_or_series if isinstance(geom_or_series, list) else [geom_or_series]
    return gpd.GeoSeries(geoms, crs=CRS_WGS84).to_crs(CRS_ALBERS)
