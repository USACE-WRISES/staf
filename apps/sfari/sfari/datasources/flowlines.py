"""NHD flowline vectors for the current map view + click-to-stream snapping.

Two helpers for the StreamStats-style map:
- ``flowlines_in_bbox`` pulls NHD flowline vectors for the visible bounding box
  (only at high zoom; size-guarded + cached) so the map can draw crisp blue lines.
- ``nearest_point_on_lines`` snaps a click to the nearest flowline and returns the
  distance in feet, so the UI can snap-or-reject.

Both never raise — they return ``None`` on any failure/no-data. Distance math is in
EPSG:5070 (Albers metres), matching ``easi.delineation``. ipyleaflet gives
coordinates as (lat, lon); NHD/shapely use (lon, lat) — the swap is handled here.
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
    """Cached NHD flowline pull for a (rounded) bbox -> geometry-only GeoJSON."""
    try:
        from pynhd import WaterData
        gdf = WaterData("nhdflowline_network").bybox((west, south, east, north))
    except Exception:  # noqa: BLE001 - no flowlines / network / version guard
        return None
    if gdf is None or gdf.empty:
        return None
    has_comid = "comid" in gdf.columns
    feats = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        props = {}
        if has_comid and row["comid"] is not None:
            try:
                props["comid"] = int(row["comid"])
            except (TypeError, ValueError):
                pass
        feats.append({"type": "Feature", "properties": props,
                      "geometry": geom.__geo_interface__})
    return {"type": "FeatureCollection", "features": feats} if feats else None


def flowlines_in_bbox(west: float, south: float, east: float, north: float,
                      *, max_area_deg2: float = 0.25) -> Optional[dict]:
    """NHD flowline vectors (EPSG:4326 FeatureCollection) for a bbox, or None.

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


def nearest_point_on_lines(geojson: Optional[dict], lat: float, lon: float
                           ) -> Optional[tuple[float, float, float, Optional[int]]]:
    """Snap (lat, lon) to the nearest flowline in ``geojson``.

    Returns ``(snap_lat, snap_lon, distance_ft, comid)`` or ``None`` if there are
    no usable lines. The COMID of the nearest flowline lets the caller delineate
    directly (bypassing the less reliable NLDI point-snap). Distance is the
    straight-line click-to-line distance in feet.
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
        comid = gdf.loc[idx]["comid"] if "comid" in gdf.columns else None
        comid_val = int(comid) if comid is not None and comid == comid else None  # filter NaN
        return (float(back.y), float(back.x), float(dist_ft), comid_val)
    except Exception:  # noqa: BLE001 - resilience by design
        return None
