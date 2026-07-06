"""Watershed Boundary Dataset (WBD) HUC12 lookup at a point (pygeohydro WBD).

Used to scope HUC-based queries (e.g. NAS invasives) to the local subwatershed.
Never raises — returns None on failure.
"""
from __future__ import annotations

from typing import Optional


def huc12_at_point(lat: float, lon: float) -> Optional[str]:
    try:
        import geopandas as gpd
        from shapely.geometry import Point
        from pygeohydro import WBD

        pt = gpd.GeoSeries([Point(lon, lat)], crs=4326).iloc[0]
        huc = WBD("huc12").bygeom(pt)
        if huc is None or huc.empty:
            return None
        cols = [c for c in huc.columns if c.lower() in ("huc12", "huc_12")]
        if not cols:
            return None
        return str(huc.iloc[0][cols[0]])
    except Exception:  # noqa: BLE001 - resilience by design
        return None
