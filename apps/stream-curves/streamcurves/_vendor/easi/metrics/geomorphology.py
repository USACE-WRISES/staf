"""Geomorphology-discipline EASI metric adapters.

Note: channel evolution stage (3DEP incision) is implemented in a later wave and
remains pending.
"""
from __future__ import annotations

from . import base
from .base import AnalysisContext, MetricResult, unavailable

SEDIMENT_ID = "sediment-continuity-sediment-supply-potential-watershed-banks"
SUBSTRATE_ID = ("bed-composition-and-large-wood-substrate-condition-grain-size-"
                "embeddedness-fines-consolidation")
BANK_EROSION_ID = "channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition"
CHANNEL_EVOL_ID = "channel-evolution-channel-evolution-stage-and-trends"
CHANNELIZED_FCODES = {33600, 33601, 33603}  # canal/ditch


def rate_channel_evolution(bhr) -> "str | None":
    """Bank-height ratio -> channel-evolution stage rating (incision proxy).

    BHR ~1 floodplain-connected/stable (Good); 1.3-1.7 adjusting (Fair); >1.7
    incised (Poor). Shared by the default metric and the editable cross-section
    recompute so the two stay consistent.
    """
    if bhr is None:
        return None
    return "Good" if bhr < 1.3 else ("Fair" if bhr < 1.7 else "Poor")


def channel_evolution(ctx: AnalysisContext) -> MetricResult:
    """Channel evolution stage proxy: channelization flag + DEM bank-height ratio.

    BHR ~1 floodplain-connected/stable (Good); 1.3-1.7 adjusting (Fair); >1.7
    incised (Poor).
    """
    if ctx.fcode in CHANNELIZED_FCODES:
        return MetricResult(CHANNEL_EVOL_ID, value=ctx.fcode,
                            value_text=f"channelized reach (NHD FCODE {ctx.fcode})",
                            rating="Poor", confidence="M",
                            source="NHD FCODE (canal/ditch)", note="artificial/channelized")
    g = ctx.extras.get("reach_geomorph") or {}
    bhr = g.get("bank_height_ratio")
    if bhr is None:
        return unavailable(CHANNEL_EVOL_ID, "3DEP incision unavailable for reach", "L")
    rating = rate_channel_evolution(bhr)
    res = g.get("dem_resolution_m") or 10
    return MetricResult(CHANNEL_EVOL_ID, value=bhr,
                        value_text=f"bank-height ratio {bhr} (3DEP {res} m)", rating=rating,
                        confidence="L", source="USGS 3DEP incision proxy",
                        note="bank-height-ratio proxy for channel evolution stage")


def sediment_supply(ctx: AnalysisContext) -> MetricResult:
    """Anthropogenic sediment-supply risk: agriculture + erodibility + roads."""
    s = base.sc(ctx)
    ag, kf, rd = base.ag_pct(ctx), s.get("kffactws"), s.get("rddensws")
    if ag is None and kf is None and rd is None:
        return unavailable(SEDIMENT_ID, "no StreamCat sediment inputs", "M")
    score = (0.5 * min((ag or 0) / 50, 1) + 0.3 * min((kf or 0) / 0.4, 1)
             + 0.2 * min((rd or 0) / 5, 1))
    rating = base.band(score, good_below=0.33, fair_below=0.66, higher_is_worse=True)
    return MetricResult(SEDIMENT_ID, value=round(score, 2),
                        value_text=f"supply risk {score:.2f} "
                                   f"(ag {ag}%, K {kf}, roads {rd})",
                        rating=rating, confidence="M",
                        source="EPA StreamCat (ag/erodibility/roads)",
                        note="anthropogenic sediment-supply composite")


def substrate(ctx: AnalysisContext) -> MetricResult:
    """Fines/embeddedness risk from low gradient + ag fines supply + erodibility."""
    s = base.sc(ctx)
    kf, ag, slope = s.get("kffactws"), base.ag_pct(ctx), ctx.slope
    if slope is None and kf is None and ag is None:
        return unavailable(SUBSTRATE_ID, "no slope/erodibility data", "L")
    fines = (0.4 * (1 - min((slope or 0) / 0.01, 1)) + 0.4 * min((ag or 0) / 50, 1)
             + 0.2 * min((kf or 0) / 0.4, 1))
    rating = base.band(fines, good_below=0.4, fair_below=0.7, higher_is_worse=True)
    return MetricResult(SUBSTRATE_ID, value=round(fines, 2),
                        value_text=f"fines/embedding risk {fines:.2f} "
                                   f"(slope {slope}, ag {ag}%)",
                        rating=rating, confidence="L",
                        source="NHDPlus slope + StreamCat (proxy)",
                        note="fines/embeddedness proxy; field pebble counts refine")


def bank_erosion(ctx: AnalysisContext) -> MetricResult:
    """Bank-erosion risk: erodibility x riparian-veg deficit x slope."""
    kf = base.sc(ctx).get("kffactws")
    rip, slope = base.riparian_forest_pct(ctx), ctx.slope
    if kf is None and rip is None and slope is None:
        return unavailable(BANK_EROSION_ID, "no erodibility/riparian/slope data", "L")
    risk = (0.4 * min((kf or 0) / 0.4, 1) + 0.4 * (1 - min((rip or 0) / 100, 1))
            + 0.2 * min((slope or 0) / 0.02, 1))
    rating = base.band(risk, good_below=0.4, fair_below=0.7, higher_is_worse=True)
    return MetricResult(BANK_EROSION_ID, value=round(risk, 2),
                        value_text=f"erosion risk {risk:.2f} "
                                   f"(K {kf}, riparian {rip}%, slope {slope})",
                        rating=rating, confidence="L",
                        source="StreamCat K-factor + riparian (proxy)",
                        note="stream-power/erodibility/riparian proxy")
