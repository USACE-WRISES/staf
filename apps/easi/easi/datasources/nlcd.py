"""NLCD land cover over a watershed polygon (pygeohydro / MRLC).

Fallback source for impervious / forest / wetland / agriculture percentages when
StreamCat is unavailable. Computes class statistics via cover_statistics and the
mean of the impervious-percent raster over the basin. Never raises — returns {}
on failure so adapters degrade gracefully.

Note: 30 m zonal stats over very large basins can be slow; StreamCat is the
preferred (fast, pre-computed) source when available.

Read on demand (2026-10-06): ``watershed_landcover`` answers at once with a mapping
that asks MRLC the first time an adapter reads it. ``easi.assessment`` prefetches it
beside StreamCat on every StreamCat-basis run, but the adapters read it only where
StreamCat has no value (``metrics/hydrology.py``), and StreamCat answers from the STAF
data bundle almost everywhere, so most runs no longer wait on MRLC. Every rating is
unchanged. ``_fetch`` is the request itself.
"""
from __future__ import annotations

import threading
from collections.abc import Mapping

YEAR = 2021
# One MRLC request at a time for the on-demand answers (only a run without StreamCat
# land cover makes one)
_LOCK = threading.Lock()


def watershed_landcover(watershed_geojson: dict | None):
    """{impervious_pct, forest_pct, wetland_pct, ag_pct}, requested on first read, or {}
    without a watershed polygon."""
    if not watershed_geojson:
        return {}
    try:
        if not (watershed_geojson.get("features") or []):
            return {}
    except AttributeError:
        return {}
    return _OnDemand(watershed_geojson)


class _OnDemand(Mapping):
    """The NLCD answer for one watershed, requested from MRLC the first time it is read."""

    __slots__ = ("_geojson", "_data")

    def __init__(self, watershed_geojson: dict):
        self._geojson = watershed_geojson
        self._data = None

    def _values(self) -> dict:
        if self._data is None:
            with _LOCK:
                if self._data is None:
                    self._data = _fetch(self._geojson)
        return self._data

    def __getitem__(self, key):
        return self._values()[key]

    def __iter__(self):
        return iter(self._values())

    def __len__(self):
        return len(self._values())

    def __repr__(self) -> str:
        return ("<NLCD land cover, not requested yet>" if self._data is None
                else f"<NLCD land cover {self._data!r}>")


def _fetch(watershed_geojson: dict | None) -> dict:
    """Return {impervious_pct, forest_pct, wetland_pct, ag_pct} or {} (one MRLC request)."""
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
