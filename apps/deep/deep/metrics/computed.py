"""Desktop auto-compute registry for DEEP (Phase 3).

Maps the desktop-derivable detailed metricIds (as used in the predefined
state-SQT assessments) to adapters that compute the RAW measured value — the
value DEEP then runs through the metric's reference curve, exactly like a
field-entered value. Adapters reuse EASI's datasource + geomorphology code:

- watershed land cover / impervious  ← EPA StreamCat (NLCD fallback)
- reach geomorphic ratios ER / BHR / W:D  ← 3DEP DEM cross-section + Bieger bankfull

Only a curated, reliably-computable subset is registered; every other metric
stays field entry. Adapters never raise — a failure or missing datum yields
``None`` and the metric is left blank/manual. Heavy datasource imports are lazy
so this module (and the framework tests) load without the geospatial stack.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .base import AnalysisContext


@dataclass
class ComputedValue:
    value: float
    source: str
    confidence: str = "M"


_ADAPTERS: dict[str, Callable[[AnalysisContext], Optional[ComputedValue]]] = {}


def adapter(*metric_ids: str):
    def deco(fn):
        for mid in metric_ids:
            _ADAPTERS[mid] = fn
        return fn
    return deco


def computable_ids() -> set[str]:
    """The set of detailed metricIds DEEP can desktop-compute."""
    return set(_ADAPTERS)


# --------------------------------------------------------------------------- #
# Shared prefetch (cached on ctx.extras; lazy imports keep the module light)
# --------------------------------------------------------------------------- #
def _streamcat(ctx: AnalysisContext) -> dict:
    if "streamcat" not in ctx.extras:
        from ..datasources import streamcat
        names = ["pctimp2019", "pctcrop2019", "pcthay2019"]
        ctx.extras["streamcat"] = (streamcat.metrics_by_comid(ctx.comid, names)
                                   if ctx.comid else {}) or {}
    return ctx.extras["streamcat"]


def _landcover(ctx: AnalysisContext) -> dict:
    if "landcover" not in ctx.extras:
        from ..datasources import nlcd
        ctx.extras["landcover"] = nlcd.watershed_landcover(ctx.watershed_geojson) or {}
    return ctx.extras["landcover"]


def _reach_geom(ctx: AnalysisContext) -> dict:
    if "reach_geomorph" not in ctx.extras:
        geom = {}
        try:
            if ctx.reach_geojson and ctx.drainage_area_sqkm:
                from .. import bieger
                from ..datasources import threedep
                bf = bieger.bankfull_geometry(ctx.drainage_area_sqkm, ctx.lat, ctx.lon)
                geom = threedep.reach_geomorphology(
                    ctx.reach_geojson, ctx.drainage_area_sqkm,
                    bankfull=(bf["width_m"], bf["depth_m"]),
                    bankfull_area_m2=bf.get("area_m2"),
                    division=bf.get("division_name"),
                ) or {}
        except Exception:  # noqa: BLE001
            geom = {}
        ctx.extras["reach_geomorph"] = geom
    return ctx.extras["reach_geomorph"]


# --------------------------------------------------------------------------- #
# Adapters — watershed land cover (StreamCat, NLCD fallback)
# --------------------------------------------------------------------------- #
@adapter("catchment-hydrology-impervious-cover",
         "catchment-hydrology-percent-impervious-cover",
         "catchment-hydrology-effective-impervious-cover")
def _impervious(ctx):
    v = _streamcat(ctx).get("pctimp2019ws")
    src = "EPA StreamCat pctimp2019 (watershed)"
    if v is None:
        v = _landcover(ctx).get("impervious_pct")
        src = "NLCD 2021 impervious (watershed)"
    return ComputedValue(round(float(v), 2), src, "H") if v is not None else None


@adapter("catchment-hydrology-anthropogenic-land-cover")
def _anthropogenic(ctx):
    sc = _streamcat(ctx)
    crop, hay, imp = sc.get("pctcrop2019ws"), sc.get("pcthay2019ws"), sc.get("pctimp2019ws")
    if crop is not None or hay is not None or imp is not None:
        total = (crop or 0.0) + (hay or 0.0) + (imp or 0.0)
        return ComputedValue(round(float(total), 2),
                             "EPA StreamCat crop+hay+impervious (watershed)", "M")
    lc = _landcover(ctx)
    ag, imp = lc.get("ag_pct"), lc.get("impervious_pct")
    if ag is not None or imp is not None:
        return ComputedValue(round(float((ag or 0.0) + (imp or 0.0)), 2),
                             "NLCD 2021 agriculture + developed (watershed)", "M")
    return None


# --------------------------------------------------------------------------- #
# Adapters — reach geomorphic ratios (3DEP DEM cross-section)
# --------------------------------------------------------------------------- #
@adapter("floodplain-connectivity-entrenchment-ratio-er")
def _entrenchment(ctx):
    er = _reach_geom(ctx).get("entrenchment_ratio")
    return (ComputedValue(round(float(er), 2), "3DEP DEM cross-section — Rosgen ER (modeled)", "M")
            if er is not None else None)


@adapter("channel-and-floodplain-dynamics-bank-height-ratio-bhr")
def _bank_height(ctx):
    bhr = _reach_geom(ctx).get("bank_height_ratio")
    return (ComputedValue(round(float(bhr), 2), "3DEP DEM cross-section — bank-height ratio (modeled)", "M")
            if bhr is not None else None)


@adapter("channel-evolution-width-depth-ratio")
def _width_depth(ctx):
    g = _reach_geom(ctx)
    w, d = g.get("bankfull_width_m"), g.get("bankfull_depth_m")
    if w and d and d > 0:
        return ComputedValue(round(float(w) / float(d), 1),
                             "3DEP DEM cross-section — width/depth (modeled)", "M")
    return None


def compute_for(metric_ids, ctx: AnalysisContext) -> dict[str, ComputedValue]:
    """Run the registered adapters for the requested metricIds. Never raises."""
    out: dict[str, ComputedValue] = {}
    for mid in set(metric_ids):
        fn = _ADAPTERS.get(mid)
        if fn is None:
            continue
        try:
            cv = fn(ctx)
        except Exception:  # noqa: BLE001
            cv = None
        if cv is not None:
            out[mid] = cv
    return out
