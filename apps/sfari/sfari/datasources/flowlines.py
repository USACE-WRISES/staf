"""Click-to-stream snapping on the map's stream vectors.

``nearest_point_on_lines`` snaps a click to the nearest line of a GeoJSON
FeatureCollection and returns the distance in feet, so the UI can snap or
reject. The vectors themselves come from the STAF site engine's NHDPlus HR
client (``hr_site``); there is no second network (2026-09-05). Never raises:
returns ``None`` on any failure or no data. Distance math is in EPSG:5070
(Albers metres). ipyleaflet gives coordinates as (lat, lon); shapely uses
(lon, lat); the swap is handled here.
"""
from __future__ import annotations

from typing import Optional

CRS_WGS84 = 4326
CRS_ALBERS = 5070  # USGS CONUS Albers Equal Area (metres)
FT_PER_M = 3.28083989501312


def nearest_point_on_lines(geojson: Optional[dict], lat: float, lon: float,
                           id_prop: str = "nhdplusid"
                           ) -> Optional[tuple[float, float, float, Optional[int]]]:
    """Snap (lat, lon) to the nearest flowline in ``geojson``.

    Returns ``(snap_lat, snap_lon, distance_ft, ident)`` or ``None`` if there are
    no usable lines, where ``ident`` is the nearest line's ``id_prop`` property
    (``nhdplusid`` for the NHDPlus HR layer). Distance is the straight-line
    click-to-line distance in feet.
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
