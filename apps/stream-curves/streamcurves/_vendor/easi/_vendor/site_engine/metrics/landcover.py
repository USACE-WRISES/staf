"""NLCD land cover over the true watershed and the 100 m riparian buffer.

Zonal statistics via ``pygeohydro`` (the same client EASI's NLCD fallback
uses, generalized to the engine's two supports). The riparian buffer is the
union of the upstream tree flowlines buffered 100 m, clipped to the watershed.
With ``config["landcoverBaseline"]`` the NLCD 2001 impervious cover is added
for both supports (the land-use-change baseline). Never raises; failed
supports contribute warning entries.
"""
from __future__ import annotations

from ..provenance import VINTAGES, metric_entry
from . import register
from .common import watershed_geom

NLCD_YEAR = 2021
BASELINE_YEAR = 2001
RIPARIAN_BUFFER_M = 100.0
_SRC = "NLCD (MRLC via pygeohydro)"

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


def _impervious_for(geom, year: int) -> float | None:
    """Mean impervious percent of one polygon for another NLCD year, or None."""
    try:
        import geopandas as gpd
        import pygeohydro

        gs = gpd.GeoSeries([geom], crs=4326)
        ds = pygeohydro.nlcd_bygeom(gs, resolution=30,
                                    years={"impervious": [year]})
        da = next(iter(ds.values()))
        imp_da = da[f"impervious_{year}"]
        return round(float(imp_da.where(imp_da >= 0).mean()), 2)
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


def _baseline_wanted(record: dict) -> bool:
    cfg = ((record.get("input") or {}).get("config") or {})
    return bool(cfg.get("landcoverBaseline"))


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

    baseline = _baseline_wanted(record)
    for label, support, geom in supports:
        stats = _stats_for(geom)
        if stats is None:
            out[f"landcover{label}Unavailable"] = metric_entry(
                None, "", "NLCD", VINTAGES["nlcd"], support,
                ["NLCD zonal statistics failed"])
            continue
        for key, value in stats.items():
            out[f"{key}{label}"] = metric_entry(
                value, "percent", _SRC, VINTAGES["nlcd"], support)
        if baseline:
            base = _impervious_for(geom, BASELINE_YEAR)
            out[f"imperviousPct{BASELINE_YEAR}{label}"] = metric_entry(
                base, "percent", _SRC, VINTAGES["nlcdBaseline"], support,
                [] if base is not None else
                [f"NLCD {BASELINE_YEAR} impervious unavailable"])
    return out
