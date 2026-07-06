"""NLCD land cover over a watershed polygon (pygeohydro / MRLC).

Fallback source for impervious / forest / wetland / agriculture percentages when
StreamCat is unavailable. Computes class statistics via cover_statistics and the
mean of the impervious-percent raster over the basin. Never raises — returns {}
on failure so adapters degrade gracefully.

Note: 30 m zonal stats over very large basins can be slow; StreamCat is the
preferred (fast, pre-computed) source when available.
"""
from __future__ import annotations

YEAR = 2021


def watershed_landcover(watershed_geojson: dict | None) -> dict:
    """Return {impervious_pct, forest_pct, wetland_pct, ag_pct} or {}."""
    if not watershed_geojson:
        return {}
    try:
        import geopandas as gpd
        import pygeohydro

        feats = watershed_geojson.get("features") or []
        if not feats:
            return {}
        gdf = gpd.GeoDataFrame.from_features(feats, crs=4326)
        ds = pygeohydro.nlcd_bygeom(
            gdf.geometry, resolution=30,
            years={"impervious": [YEAR], "cover": [YEAR]},
        )
        da = next(iter(ds.values()))
        classes = pygeohydro.cover_statistics(da[f"cover_{YEAR}"]).classes
        imp_da = da[f"impervious_{YEAR}"]
        imp = float(imp_da.where(imp_da >= 0).mean())

        def _sum(*keywords: str) -> float:
            return round(sum(v for k, v in classes.items()
                             if any(kw in k for kw in keywords)), 2)

        return {
            "impervious_pct": round(imp, 2),
            "forest_pct": _sum("Forest"),
            "wetland_pct": _sum("Wetland"),
            "ag_pct": _sum("Crop", "Hay", "Pasture"),
        }
    except Exception:  # noqa: BLE001 - resilience by design
        return {}
