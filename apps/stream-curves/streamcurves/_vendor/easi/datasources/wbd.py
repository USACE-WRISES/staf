"""Watershed Boundary Dataset (WBD) HUC12 lookup at a point (pygeohydro WBD).

Used to scope HUC-based queries (e.g. NAS invasives) to the local subwatershed.
The STAF data bundle answers first where it covers the point (the HUC12 of the HR
flowline there, precomputed from the current WBD). Never raises — returns None on
failure.
"""
from __future__ import annotations

from typing import Optional


def _bundle_extras(lat: float, lon: float) -> Optional[dict]:
    """The STAF data bundle's precomputed lookups for the HR flowline at the point (the vendored
    site engine's ``bundle.point_extras``), or None where the bundle cannot answer."""
    try:
        from .._vendor.site_engine import bundle
        return bundle.point_extras(lat, lon)
    except Exception:  # noqa: BLE001 - the service answers instead
        return None


def huc12_at_point(lat: float, lon: float) -> Optional[str]:
    local = _bundle_extras(lat, lon)
    if local is not None and local.get("huc12"):
        return local["huc12"]
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
