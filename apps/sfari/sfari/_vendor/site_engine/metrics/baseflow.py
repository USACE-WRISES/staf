"""Base-flow index over the true watershed (the USGS base-flow index grid).

The exact-watershed analog of StreamCat ``bfi``: the mean of Wolock's (2003)
1 km base-flow index grid (the percentage of streamflow that is base flow)
over the watershed polygon. The grid ships with the engine
(``data/bfi48grd.tif``: EPSG:5070, uint8 percent, nodata 255, 1.3 MB;
``data/BFI_GRID_INFO.json`` records the download), so this family needs no
network and repeats byte for byte. Cells count when their center lies inside
the polygon; a watershed too small to hold a cell center falls back to every
cell it touches, then to the cell under the site point, and says which.
Never raises.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..provenance import VINTAGES, metric_entry
from . import register
from .common import watershed_geom

GRID_PATH = Path(__file__).resolve().parent.parent / "data" / "bfi48grd.tif"
SOURCE = "USGS base-flow index grid (Wolock 2003)"
NODATA = 255
METHOD_CENTERS, METHOD_TOUCHED, METHOD_POINT, METHOD_NONE = (
    "cellCenters", "cellsTouched", "sitePoint", "none")


def grid_mean(geom, point: Optional[tuple[float, float]] = None
              ) -> tuple[Optional[float], str, int]:
    """``(mean_pct, method, n_cells)`` of the grid over a WGS84 polygon.

    ``method`` is ``cellCenters`` (cells whose center is inside), ``cellsTouched``
    (every cell the polygon touches, for polygons smaller than a cell),
    ``sitePoint`` (the cell under ``point``, ``(lat, lon)``), or ``none``.
    """
    import geopandas as gpd
    import rasterio
    import rasterio.mask
    from shapely.geometry import Point, mapping

    with rasterio.open(GRID_PATH) as ds:
        g = gpd.GeoSeries([geom], crs=4326).to_crs(ds.crs).iloc[0]
        for all_touched, method in ((False, METHOD_CENTERS), (True, METHOD_TOUCHED)):
            try:
                arr, _ = rasterio.mask.mask(ds, [mapping(g)], crop=True,
                                            all_touched=all_touched,
                                            nodata=NODATA, filled=True)
            except ValueError:          # the polygon misses the grid entirely
                break
            vals = arr[0][arr[0] != NODATA]
            if vals.size:
                return round(float(vals.mean()), 1), method, int(vals.size)
        if point is not None:
            lat, lon = point
            p = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(ds.crs).iloc[0]
            b = ds.bounds
            if b.left <= p.x <= b.right and b.bottom <= p.y <= b.top:
                v = int(next(ds.sample([(p.x, p.y)]))[0])
                if v != NODATA:
                    return float(v), METHOD_POINT, 1
    return None, METHOD_NONE, 0


def _entry(value, warnings=None) -> dict:
    return metric_entry(value, "%", SOURCE, VINTAGES["bfiGrid"], "pointWatershed",
                        warnings)


@register("baseflow")
def compute(record: dict, tree_geoms: list) -> dict:
    ws = watershed_geom((record.get("watershed") or {}).get("polygon"))
    if ws is None:
        return {"baseflowIndexPct": _entry(None, ["watershed polygon unavailable"])}
    if not GRID_PATH.exists():
        return {"baseflowIndexPct": _entry(
            None, ["the base-flow index grid is not shipped with this engine copy"])}
    site = record.get("site") or {}
    point = ((site["snapLat"], site["snapLon"])
             if site.get("snapLat") is not None and site.get("snapLon") is not None
             else None)
    try:
        value, method, n = grid_mean(ws, point)
    except Exception as exc:  # noqa: BLE001 - never break the record
        return {"baseflowIndexPct": _entry(None, [f"grid read failed: {exc}"])}
    warnings: list[str] = []
    if value is None:
        warnings.append("the watershed lies outside the base-flow index grid "
                        "(conterminous United States only)")
    elif method == METHOD_TOUCHED:
        warnings.append(f"watershed smaller than a 1 km grid cell: the mean of the "
                        f"{n} cells it touches")
    elif method == METHOD_POINT:
        warnings.append("watershed smaller than a 1 km grid cell: the cell under "
                        "the site point")
    return {
        "baseflowIndexPct": _entry(value, warnings),
        "baseflowIndexCells": metric_entry(n, "count", SOURCE, VINTAGES["bfiGrid"],
                                           "pointWatershed"),
    }
