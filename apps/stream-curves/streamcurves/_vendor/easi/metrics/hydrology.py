"""Hydrology-discipline EASI metric adapters."""
from __future__ import annotations

from . import base
from .base import AnalysisContext, MetricResult, unavailable

IMPERVIOUS_ID = "catchment-hydrology-impervious-surface-cover"
WETLANDS_ID = "surface-water-storage-percent-wetlands-in-watershed"
FLOW_ALTERATION_ID = "streamflow-regime-flow-alteration-regulation-water-use"
REACH_INFLOW_ID = "reach-inflow-concentrated-runoff-stormwater-inputs"


def impervious(ctx: AnalysisContext) -> MetricResult:
    """% impervious in the watershed. STAF: Good <10, Fair 10-25, Poor >25."""
    val = base.sc(ctx).get("pctimp2019ws")
    source = "EPA StreamCat pctimp2019 (watershed)"
    if val is None:
        val = (ctx.extras.get("landcover") or {}).get("impervious_pct")
        source = "NLCD 2021 impervious (watershed)"
    if val is None:
        return unavailable(IMPERVIOUS_ID, "no impervious data available", "H")
    rating = "Good" if val < 10 else ("Fair" if val <= 25 else "Poor")
    return MetricResult(IMPERVIOUS_ID, value=round(float(val), 2),
                        value_text=f"{round(float(val), 1)}% impervious",
                        rating=rating, confidence="H", source=source)


def wetlands(ctx: AnalysisContext) -> MetricResult:
    """% wetlands in the watershed. STAF: Good >5, Fair 1-5, Poor <1.

    Source is user-selectable (config.SOURCE_OPTIONS): EPA StreamCat (default) or
    NLCD 2021. Absent a choice, prefer StreamCat and fall back to NLCD.
    """
    src = (ctx.extras.get("source_choices") or {}).get(WETLANDS_ID)
    s = base.sc(ctx)
    wd, hb = s.get("pctwdwet2019ws"), s.get("pcthbwet2019ws")
    sc_val = (wd or 0.0) + (hb or 0.0) if (wd is not None or hb is not None) else None
    nlcd_val = (ctx.extras.get("landcover") or {}).get("wetland_pct")
    if src == "nlcd":
        val, source = nlcd_val, "NLCD 2021 wetlands (watershed)"
    elif src == "streamcat":
        val, source = sc_val, "EPA StreamCat wetlands (watershed)"
    elif sc_val is not None:  # auto: StreamCat preferred
        val, source = sc_val, "EPA StreamCat wetlands (watershed)"
    else:
        val, source = nlcd_val, "NLCD 2021 wetlands (watershed)"
    if val is None:
        return unavailable(WETLANDS_ID, "no wetland data available", "H")
    rating = "Good" if val > 5 else ("Fair" if val >= 1 else "Poor")
    return MetricResult(WETLANDS_ID, value=round(float(val), 2),
                        value_text=f"{round(float(val), 1)}% wetland cover",
                        rating=rating, confidence="H", source=source)


def flow_alteration(ctx: AnalysisContext) -> MetricResult:
    """Flow regulation proxy from upstream dam storage per unit drainage area.

    Ungaged proxy (StreamCat normal storage). A gaged NWIS current-vs-baseline
    comparison is a later refinement.
    """
    stor = base.sc(ctx).get("damnrmstorws")
    if stor is None:
        return unavailable(FLOW_ALTERATION_ID, "no dam-storage data", "M")
    da = max(ctx.drainage_area_sqkm or 1.0, 1.0)
    ratio = stor / da  # acre-ft normal storage per km^2
    rating = base.band(ratio, good_below=5.0, fair_below=100.0, higher_is_worse=True)
    return MetricResult(FLOW_ALTERATION_ID, value=round(ratio, 2),
                        value_text=f"{stor:.0f} ac-ft upstream storage ({ratio:.1f} ac-ft/km²)",
                        rating=rating, confidence="M",
                        source="EPA StreamCat dam storage (ungaged proxy)",
                        note="regulation proxy; gaged NWIS comparison refines")


def reach_inflow(ctx: AnalysisContext) -> MetricResult:
    """Concentrated runoff/stormwater proxy via watershed road density.

    Road-stream crossing counts (TIGER) are a later refinement.
    """
    rd = base.sc(ctx).get("rddensws")
    if rd is None:
        return unavailable(REACH_INFLOW_ID, "no road-density data", "L")
    rating = base.band(rd, good_below=1.0, fair_below=3.0, higher_is_worse=True)
    return MetricResult(REACH_INFLOW_ID, value=round(rd, 2),
                        value_text=f"{rd:.2f} km/km² road density",
                        rating=rating, confidence="L",
                        source="EPA StreamCat road density (proxy)",
                        note="stormwater-input proxy; road-stream crossings refine")
