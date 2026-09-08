"""NLCD land cover over the HR reach watershed and the 100 m riparian buffer.

Zonal statistics via ``pygeohydro`` (the same client EASI's NLCD fallback
uses, generalized to the engine's two supports). The riparian buffer is the
union of the upstream tree flowlines buffered 100 m, clipped to the watershed.
With ``config["landcoverBaseline"]`` the NLCD 2001 impervious cover is added
for both supports (the land-use-change baseline). Never raises; failed
supports contribute warning entries.

Outside the product's footprint (2026-09-08). NLCD's land-cover layer covers
the conterminous United States only, and the service answers a polygon beyond
it without complaining: cells outside the footprint come back as the raster's
nodata (127), and a polygon reaching into Canada also carries cells of 0, a
value that is not an NLCD class at all. That produced two silent wrongs. A
watershed straddling the border raised inside ``cover_statistics`` and the
whole support was dropped (seven Northeastern Highlands sites, all draining
into Quebec). A watershed entirely outside it, in Alaska or Hawaii, raised
nothing and reported **zero percent of every class** with a NaN impervious,
which reads as measurement rather than absence.

So the normal path is left exactly as it was, and a second pass runs only when
it fails or comes back empty: it counts how much of the polygon the product
actually covers, scores the classes over the covered cells alone, and reports
that fraction. Nothing covered means no value and a reason, never a zero.
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


def _class_sums(percent_by_class: dict) -> dict:
    """The engine's stems from NLCD class percentages, by keyword."""
    out = {}
    for stem, keywords in _CLASS_KEYWORDS.items():
        out[f"{stem}Pct"] = round(
            sum(v for k, v in percent_by_class.items()
                if any(kw in k for kw in keywords)), 2)
    return out


def _mean_impervious(imp_da) -> float | None:
    """Mean impervious percent over the cells that carry one.

    ``>= 0`` alone keeps the nodata 127, which a polygon reaching past the
    footprint is full of; a percentage cannot exceed 100.
    """
    try:
        valid = imp_da.where((imp_da >= 0) & (imp_da <= 100))
        value = float(valid.mean())
    except Exception:  # noqa: BLE001
        return None
    return None if value != value else round(value, 2)      # NaN means no cells


def _stats_for(geom) -> dict | None:
    """{imperviousPct, <stem>Pct...} for one polygon, or None on failure.

    A polygon the product covers takes the first path, unchanged. Anything
    else falls to :func:`_stats_covered_part`, which scores the covered cells
    alone and adds ``_coveredFraction``; a polygon with no covered cell at all
    yields None, so an uncovered watershed is absent rather than zero.
    """
    try:
        import geopandas as gpd
        import pygeohydro

        gs = gpd.GeoSeries([geom], crs=4326)
        ds = pygeohydro.nlcd_bygeom(
            gs, resolution=30,
            years={"impervious": [NLCD_YEAR], "cover": [NLCD_YEAR]})
        da = next(iter(ds.values()))
        classes = pygeohydro.cover_statistics(da[f"cover_{NLCD_YEAR}"]).classes
        if classes and sum(classes.values()) > 0:
            imp = _mean_impervious(da[f"impervious_{NLCD_YEAR}"])
            out = {"imperviousPct": imp}
            out.update(_class_sums(classes))
            return out
    except Exception:  # noqa: BLE001 - resilience by design
        da = None
    return _stats_covered_part(geom)


#: The raster's nodata, declared by the service and confirmed on every probe.
NLCD_NODATA = 127


def _nlcd_class_names() -> dict:
    """``{code: class name}`` from pygeohydro's own table, nodata excluded.

    The table's values are ``"Cultivated Crops - long description"`` (and
    occasionally ``"Developed, Low Intensity -Includes ..."`` with no space
    before the dash), so the name is everything up to the first dash. Matching
    on the name alone keeps a keyword out of the prose.
    """
    import re

    import pygeohydro

    out = {}
    for code, text in (pygeohydro.helpers.nlcd_helper().get("classes") or {}).items():
        try:
            value = int(code)
        except (TypeError, ValueError):
            continue
        if value == NLCD_NODATA:
            continue
        out[value] = re.split(r"\s+-\s*", str(text), maxsplit=1)[0].strip()
    return out


def _stats_covered_part(geom) -> dict | None:
    """Classes over the part of the polygon NLCD covers, or None if none of it.

    Cells outside the footprint are the nodata 127, and a polygon reaching into
    Canada also carries 0, which is no class at all; both are excluded, and the
    percentages are of the covered area, which is what ``_coveredFraction``
    says.
    """
    try:
        import geopandas as gpd
        import numpy as np
        import pygeohydro

        gs = gpd.GeoSeries([geom], crs=4326)
        ds = pygeohydro.nlcd_bygeom(
            gs, resolution=30,
            years={"impervious": [NLCD_YEAR], "cover": [NLCD_YEAR]})
        da = next(iter(ds.values()))
        cover = np.asarray(da[f"cover_{NLCD_YEAR}"].values).ravel()
        names = _nlcd_class_names()
        keep = np.isin(cover, list(names))
        n_valid, n_total = int(keep.sum()), int(cover.size)
        if not n_valid or not n_total:
            return None                       # nothing covered: absent, not zero
        codes, counts = np.unique(cover[keep], return_counts=True)
        percent = {names.get(int(c), str(c)): 100.0 * int(n) / n_valid
                   for c, n in zip(codes, counts)}
        out = {"imperviousPct": _mean_impervious(da[f"impervious_{NLCD_YEAR}"])}
        out.update(_class_sums(percent))
        out["_coveredFraction"] = round(n_valid / n_total, 4)
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
                ["NLCD reports no land cover for this polygon (outside the "
                 "product's footprint, or the service failed)"])
            continue
        stats = dict(stats)
        covered = stats.pop("_coveredFraction", None)
        warnings = ([] if covered is None else
                    [f"NLCD covers {covered * 100:.0f} percent of this "
                     "polygon; the percentages describe the covered part"])
        for key, value in stats.items():
            out[f"{key}{label}"] = metric_entry(
                value, "percent", _SRC, VINTAGES["nlcd"], support, list(warnings))
        if baseline:
            base = _impervious_for(geom, BASELINE_YEAR)
            out[f"imperviousPct{BASELINE_YEAR}{label}"] = metric_entry(
                base, "percent", _SRC, VINTAGES["nlcdBaseline"], support,
                [] if base is not None else
                [f"NLCD {BASELINE_YEAR} impervious unavailable"])
    return out
