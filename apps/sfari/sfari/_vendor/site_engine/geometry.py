"""Shared geometry helpers (EPSG:5070 math, snapping, reach trimming).

Self-contained ports of the EASI delineation geometry (same math, asserted by
parity tests when the EASI source tree is present). All inputs/outputs are
EPSG:4326; distance math happens in EPSG:5070 (USGS CONUS Albers, metres).
Heavy imports are function-local so the package imports without the geo stack.
"""
from __future__ import annotations

from typing import Optional

CRS_WGS84 = 4326
CRS_ALBERS = 5070
FT_PER_M = 3.28083989501312


def line_sinuosity(geom) -> Optional[float]:
    """Flowline length / straight-line endpoint distance, or None."""
    try:
        import geopandas as gpd
        from shapely.geometry import Point
        g = gpd.GeoSeries([geom], crs=CRS_WGS84).to_crs(CRS_ALBERS).iloc[0]
        line = g.geoms[0] if g.geom_type == "MultiLineString" else g
        straight = Point(line.coords[0]).distance(Point(line.coords[-1]))
        if straight > 0:
            return round(line.length / straight, 3)
    except Exception:  # noqa: BLE001 - context is best-effort
        pass
    return None


def nearest_point_on_records(records: list[dict], lat: float, lon: float
                             ) -> Optional[tuple[float, float, float, Optional[int]]]:
    """Snap (lat, lon) to the nearest record's geometry.

    ``records`` are parsed HR flowline dicts (``hr.parse_feature`` shape).
    Returns ``(snap_lat, snap_lon, distance_ft, nhdplusid)`` or None.
    """
    if not records:
        return None
    try:
        import geopandas as gpd
        from shapely.geometry import Point, shape
        from shapely.ops import nearest_points

        geoms = [shape(r["geometry"]) for r in records]
        gs = gpd.GeoSeries(geoms, crs=CRS_WGS84).to_crs(CRS_ALBERS)
        click = gpd.GeoSeries([Point(lon, lat)],
                              crs=CRS_WGS84).to_crs(CRS_ALBERS).iloc[0]
        dists = gs.distance(click)
        idx = int(dists.idxmin())
        snapped_m = nearest_points(gs.iloc[idx], click)[0]
        dist_ft = click.distance(snapped_m) * FT_PER_M
        back = gpd.GeoSeries([snapped_m],
                             crs=CRS_ALBERS).to_crs(CRS_WGS84).iloc[0]
        return (float(back.y), float(back.x), float(dist_ft),
                records[idx].get("nhdplusid"))
    except Exception:  # noqa: BLE001 - resilience by design
        return None


def _outlet_at_start(merged, own_geoms: list) -> Optional[bool]:
    """Which end of ``merged`` is the anchor reach's outlet node (port of the
    EASI orientation logic; ~1 m endpoint match in EPSG:5070)."""
    if not own_geoms:
        return None
    try:
        import geopandas as gpd
        from shapely.geometry import Point
        from shapely.ops import linemerge
        parts: list = []
        for g in own_geoms:
            parts.extend(g.geoms if g.geom_type == "MultiLineString" else [g])
        cl = gpd.GeoSeries(parts, crs=CRS_WGS84).to_crs(CRS_ALBERS)
        c = cl.iloc[0] if len(cl) == 1 else linemerge(cl.tolist())
        if c.geom_type == "MultiLineString":
            c = max(c.geoms, key=lambda g: g.length)
        m0, m1 = Point(merged.coords[0]), Point(merged.coords[-1])
        c_ends = (Point(c.coords[0]), Point(c.coords[-1]))
        hits0 = any(ce.distance(m0) < 1.0 for ce in c_ends)
        hits1 = any(ce.distance(m1) < 1.0 for ce in c_ends)
        if hits0 and not hits1:
            return True
        if hits1 and not hits0:
            return False
    except Exception:  # noqa: BLE001 - orientation is best-effort
        pass
    return None


def _trim_upstream(merged, snap, length_m: float, outlet_at_start: Optional[bool]):
    from shapely.ops import substring
    total = merged.length
    proj = merged.project(snap)
    if outlet_at_start is None:
        outlet_at_start = proj <= (total - proj)
    if outlet_at_start:
        return substring(merged, proj, min(proj + length_m, total))
    return substring(merged, max(0.0, proj - length_m), proj)


def reach_from_lines(geoms: list, own_geoms: list, lat: float, lon: float,
                     length_ft: float, warnings: list[str]
                     ) -> tuple[Optional[dict], Optional[float], list[str]]:
    """Merge/orient/trim a mainstem to ``length_ft`` upstream of the snap.

    Port of EASI's ``delineation._reach_from_lines`` (same math; parity test
    guards it). Returns ``(reach_geojson, actual_ft, warnings)``.
    """
    import geopandas as gpd
    from shapely.geometry import Point
    from shapely.ops import linemerge

    length_m = length_ft / FT_PER_M
    lines = gpd.GeoSeries(geoms, crs=CRS_WGS84).to_crs(CRS_ALBERS)
    snap = gpd.GeoSeries([Point(lon, lat)],
                         crs=CRS_WGS84).to_crs(CRS_ALBERS).iloc[0]
    merged = lines.iloc[0] if len(lines) == 1 else linemerge(lines.tolist())
    if merged.geom_type == "MultiLineString":
        merged = min(merged.geoms, key=lambda g: g.distance(snap))
        warnings.append("flowline had gaps; used nearest mainstem component")
    seg = _trim_upstream(merged, snap, length_m,
                         _outlet_at_start(merged, own_geoms))
    actual_ft = seg.length * FT_PER_M
    if actual_ft < length_ft - 1:
        warnings.append(f"only {actual_ft:.0f} ft of mainstem available upstream")
    reach = gpd.GeoSeries([seg], crs=CRS_ALBERS).to_crs(CRS_WGS84)
    return reach.__geo_interface__, round(actual_ft, 1), warnings
