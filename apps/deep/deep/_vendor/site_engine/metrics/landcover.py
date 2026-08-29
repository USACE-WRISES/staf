"""NLCD land cover over the true watershed and the 100 m riparian buffer.

Zonal statistics via ``pygeohydro`` (the same client EASI's NLCD fallback
uses, generalized to the engine's two supports). The riparian buffer is the
union of the upstream tree flowlines buffered 100 m, clipped to the watershed.
Never raises; failed supports contribute warning entries.
"""
from __future__ import annotations

from ..provenance import VINTAGES, metric_entry
from . import register
from .common import watershed_geom

NLCD_YEAR = 2021
RIPARIAN_BUFFER_M = 100.0

# metric key stem -> NLCD class-name keywords (cover_statistics naming)
_CLASS_KEYWORDS = {
    "crop": ("Crop",),
    "hayPasture": ("Hay", "Pasture"),
    "forest": ("Forest",),
    "shrub": ("Shrub",),
    "grassland": ("Grassland",),
    "woodyWetland": ("Woody Wetland",),
    "herbWetland": ("Herbaceous Wetland", "Emergent Herbaceous"),
}


def _stats_for(geom) -> dict | None:
    """{imperviousPct, <stem>Pct...} for one polygon, or None on failure."""
    try:
        import geopandas as gpd
        import pygeohydro

        gs = gpd.GeoSeries([geom], crs=4326)
        ds = pygeohydro.nlcd_bygeom(
            gs, resolution=30,
            years={"impervious": [NLCD_YEAR], "cover": [NLCD_YEAR]})
        da = next(iter(ds.values()))
        classes = pygeohydro.cover_statistics(da[f"cover_{NLCD_YEAR}"]).classes
        imp_da = da[f"impervious_{NLCD_YEAR}"]
        imp = float(imp_da.where(imp_da >= 0).mean())

        def _sum(keywords) -> float:
            return round(sum(v for k, v in classes.items()
                             if any(kw in k for kw in keywords)), 2)

        out = {"imperviousPct": round(imp, 2)}
        for stem, keywords in _CLASS_KEYWORDS.items():
            out[f"{stem}Pct"] = _sum(keywords)
        return out
    except Exception:  # noqa: BLE001 - resilience by design
        return None


def riparian_buffer(watershed, tree_geoms: list):
    """Union of the tree flowlines buffered 100 m, clipped to the watershed."""
    try:
        from shapely.geometry import shape

        from .common import albers
        lines = [shape(g) for g in tree_geoms if g]
        if not lines or watershed is None:
            return None
        buffered = albers(lines).buffer(RIPARIAN_BUFFER_M).union_all()
        ws_albers = albers(watershed).iloc[0]
        clipped = buffered.intersection(ws_albers)
        if clipped.is_empty:
            return None
        import geopandas as gpd
        return gpd.GeoSeries([clipped], crs=5070).to_crs(4326).iloc[0]
    except Exception:  # noqa: BLE001
        return None


@register("landcover")
def compute(record: dict, tree_geoms: list) -> dict:
    out: dict = {}
    ws_fc = (record.get("watershed") or {}).get("polygon")
    ws = watershed_geom(ws_fc)
    supports = []
    if ws is not None:
        supports.append(("Watershed", "pointWatershed", ws))
        rip = riparian_buffer(ws, tree_geoms)
        if rip is not None:
            supports.append(("Riparian", "riparianBuffer", rip))
        else:
            out["landcoverRiparianUnavailable"] = metric_entry(
                None, "", "NLCD", VINTAGES["nlcd"], "riparianBuffer",
                ["riparian buffer could not be built"])
    else:
        out["landcoverUnavailable"] = metric_entry(
            None, "", "NLCD", VINTAGES["nlcd"], "pointWatershed",
            ["watershed polygon unavailable"])
        return out

    for label, support, geom in supports:
        stats = _stats_for(geom)
        if stats is None:
            out[f"landcover{label}Unavailable"] = metric_entry(
                None, "", "NLCD", VINTAGES["nlcd"], support,
                ["NLCD zonal statistics failed"])
            continue
        for key, value in stats.items():
            out[f"{key}{label}"] = metric_entry(
                value, "percent", "NLCD (MRLC via pygeohydro)",
                VINTAGES["nlcd"], support)
    return out
