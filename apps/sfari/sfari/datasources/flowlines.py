"""Stream vectors for the map's NHDPlus V2 layer and click-to-stream snapping.

- ``flowlines_in_bbox`` pulls the NHDPlus V2 flowlines for the visible bounding
  box from the USGS fabric API (``fabric.py``, the successor of the WaterData
  WFS): the dark blue lines where the StreamCat lookup engine answers by COMID
  (size-guarded, cached on the rounded bbox). The high-resolution NHD comes
  from the vendored STAF site engine's HR client (``hr_site``); the two are
  split by the click rule in ``network_display``.
- ``nearest_point_on_lines`` snaps a click to the nearest line of a GeoJSON
  FeatureCollection and returns the distance in feet, so the UI can snap or
  reject.

Both never raise: they return ``None`` on any failure or no data. Distance
math is in EPSG:5070 (Albers metres). ipyleaflet gives coordinates as
(lat, lon); shapely uses (lon, lat); the swap is handled here.
"""
from __future__ import annotations

import functools
from typing import Optional

CRS_WGS84 = 4326
CRS_ALBERS = 5070  # USGS CONUS Albers Equal Area (metres)
FT_PER_M = 3.28083989501312


def _round_bbox(west, south, east, north, ndigits=3):
    return (round(west, ndigits), round(south, ndigits),
            round(east, ndigits), round(north, ndigits))


@functools.lru_cache(maxsize=64)
def _fetch(west: float, south: float, east: float, north: float) -> Optional[dict]:
    """Cached NHDPlus V2 flowline pull for a (rounded) bbox -> GeoJSON with
    ``comid`` and ``gnis_name`` per feature (the USGS fabric API)."""
    try:
        from . import fabric
        found = fabric.features_in_bbox(west, south, east, north)
    except Exception:  # noqa: BLE001 - network / version guard
        return None
    if not found:
        return None
    feats = []
    for f in found:
        geom = f.get("geometry") or {}
        if not geom.get("coordinates"):
            continue
        props = {}
        src = f.get("properties") or {}
        comid = src.get("comid")
        if comid is not None:
            try:
                props["comid"] = int(comid)
            except (TypeError, ValueError):
                pass
        name = src.get("gnis_name")
        if name:
            props["gnis_name"] = str(name).strip() or None
        feats.append({"type": "Feature", "properties": props, "geometry": geom})
    return {"type": "FeatureCollection", "features": feats} if feats else None


def flowlines_in_bbox(west: float, south: float, east: float, north: float,
                      *, max_area_deg2: float = 0.25) -> Optional[dict]:
    """NHDPlus V2 flowline vectors (EPSG:4326 FeatureCollection) for a bbox,
    or None.

    Returns None for an invalid or too-large bbox (guards against zoomed-out
    pulls) and when there are no flowlines. Cached on the rounded bbox so pan
    jitter reuses the last result.
    """
    # normalize order (ipyleaflet bounds ordering varies) so the guards/fetch are safe
    west, east = min(west, east), max(west, east)
    south, north = min(south, north), max(south, north)
    if west == east or south == north:
        return None
    if (east - west) * (north - south) > max_area_deg2:
        return None
    return _fetch(*_round_bbox(west, south, east, north))


def nearest_point_on_lines(geojson: Optional[dict], lat: float, lon: float,
                           id_prop: str = "nhdplusid"
                           ) -> Optional[tuple[float, float, float, Optional[int]]]:
    """Snap (lat, lon) to the nearest flowline in ``geojson``.

    Returns ``(snap_lat, snap_lon, distance_ft, ident)`` or ``None`` if there are
    no usable lines, where ``ident`` is the nearest line's ``id_prop`` property
    (``nhdplusid`` for the NHDPlus HR layer, ``comid`` for the V2 layer).
    Distance is the straight-line click-to-line distance in feet.
    """
    if not geojson or not geojson.get("features"):
        return None
    try:
        import geopandas as gpd
        from shapely.geometry import Point
        from shapely.ops import nearest_points

        gdf = gpd.GeoDataFrame.from_features(geojson["features"], crs=CRS_WGS84).to_crs(CRS_ALBERS)
        gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
        if gdf.empty:
            return None
        click = gpd.GeoSeries([Point(lon, lat)], crs=CRS_WGS84).to_crs(CRS_ALBERS).iloc[0]
        idx = gdf.geometry.distance(click).idxmin()        # nearest individual flowline
        line = gdf.geometry.loc[idx]
        snapped_m = nearest_points(line, click)[0]
        dist_ft = click.distance(snapped_m) * FT_PER_M
        back = gpd.GeoSeries([snapped_m], crs=CRS_ALBERS).to_crs(CRS_WGS84).iloc[0]
        ident = gdf.loc[idx][id_prop] if id_prop in gdf.columns else None
        ident_val = int(ident) if ident is not None and ident == ident else None  # filter NaN
        return (float(back.y), float(back.x), float(dist_ft), ident_val)
    except Exception:  # noqa: BLE001 - resilience by design
        return None
