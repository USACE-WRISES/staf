"""Hydraulics-discipline EASI metric adapters."""
from __future__ import annotations

from .. import screening_methods
from . import base
from .base import AnalysisContext, MetricResult, unavailable

LOW_FLOW_ID = "low-flow-and-baseflow-dynamics-low-flow-wetted-connectivity"
HYPORHEIC_ID = "hyporheic-connectivity-hyporheic-exchange-indicators"
ENTRENCHMENT_ID = "floodplain-connectivity-floodplain-access-entrenchment"
FLOODPLAIN_ENGAGEMENT_ID = (
    "high-flow-dynamics-floodplain-engagement-frequency-bankfull-recurrence")

PERENNIAL, INTERMITTENT, EPHEMERAL = 46006, 46003, 46007


def rate_engagement(bhr):
    """Direct BHR rating, retained as a small pure helper for cross-section edits.

    Returns ``(rating, BHR)`` for compatibility with the existing call shape;
    no recurrence interval or fabricated flood-frequency transformation is used.
    """
    ev = screening_methods.evaluate(FLOODPLAIN_ENGAGEMENT_ID, {"bhr": bhr})
    return ev.rating, ev.combined_value


def floodplain_engagement(ctx: AnalysisContext) -> MetricResult:
    """Floodplain engagement from bank-height ratio directly."""
    geom = ctx.extras.get("reach_geomorph") or {}
    bhr = geom.get("bank_height_ratio")
    confidence = "L" if geom.get("edge_limited") else "M"
    ev = screening_methods.evaluate(
        FLOODPLAIN_ENGAGEMENT_ID, {"bhr": bhr},
        input_meta={"bhr": {"source": "USGS 3DEP representative cross section"}},
        confidence=confidence)
    if ev.rating is None:
        return unavailable(
            FLOODPLAIN_ENGAGEMENT_ID,
            "bank-height ratio unavailable for the representative cross section",
            confidence, scoring=ev.trace)
    warning = "; ".join(ev.trace.get("warnings") or [])
    return MetricResult(
        FLOODPLAIN_ENGAGEMENT_ID, value=round(float(bhr), 3),
        value_text=f"bank-height ratio {float(bhr):.2f} (low-bank height / max bankfull depth)",
        rating=ev.rating, confidence=confidence,
        source="USGS 3DEP representative cross section",
        note=warning or "Direct BHR screen; surveyed cross-section geometry may refine.",
        scoring=ev.trace)


def rate_entrenchment(er):
    """Direct entrenchment-ratio rating."""
    return screening_methods.evaluate(ENTRENCHMENT_ID, {"er": er}).rating


def floodplain_access(ctx: AnalysisContext) -> MetricResult:
    """Lateral floodplain access from entrenchment ratio directly."""
    geom = ctx.extras.get("reach_geomorph") or {}
    er = geom.get("entrenchment_ratio")
    edge = bool(geom.get("edge_limited"))
    confidence = "L" if edge else "M"
    ev = screening_methods.evaluate(
        ENTRENCHMENT_ID, {"er": er},
        input_meta={"er": {"source": "USGS 3DEP representative cross section"}},
        confidence=confidence)
    if ev.rating is None:
        return unavailable(
            ENTRENCHMENT_ID, "3DEP entrenchment ratio unavailable for reach",
            confidence, scoring=ev.trace)
    res = geom.get("dem_resolution_m") or 10
    note = f"DEM {res} m; bankfull from national curve"
    if edge:
        note += "; flood-prone width reached the buffer edge and ER may be underestimated"
    note += "; naturally confined valleys require field interpretation"
    return MetricResult(
        ENTRENCHMENT_ID, value=round(float(er), 3),
        value_text=(f"entrenchment ratio {float(er):.2f} "
                    "(flood-prone width / bankfull width)"),
        rating=ev.rating, confidence=confidence,
        source="USGS 3DEP representative cross section",
        note=note, scoring=ev.trace)


def low_flow_connectivity(ctx: AnalysisContext) -> MetricResult:
    """Observed NRSA wetted channel, then the StreamCat HYD fallback."""
    fcode = ctx.fcode
    descriptions = {
        PERENNIAL: "perennial classification",
        INTERMITTENT: "intermittent classification",
        EPHEMERAL: "ephemeral classification",
    }
    regime = descriptions.get(fcode, f"FCODE {fcode}") if fcode is not None else "unknown regime"
    nrsa = base.nrsa_evidence(ctx)
    wetted = (nrsa or {}).get("wettedPct")
    if wetted is not None:
        connected = nrsa.get("matchType") == "connected_nearby"
        source_tier = "connected-nearby" if connected else "observed"
        confidence = nrsa.get("confidence") or ("M/L" if connected else "M")
        source = (f"EPA NRSA 2018-19 site {nrsa.get('siteId')}"
                  + (f" ({float(nrsa.get('distanceMi') or 0):.2f} mi connected)"
                     if connected else " (exact COMID)"))
        ev = screening_methods.evaluate(
            LOW_FLOW_ID, {"wettedPct": wetted, "fcodeContext": fcode},
            input_meta={
                "wettedPct": {"source": source, "details": nrsa},
                "fcodeContext": {"source": "NHDPlus FCODE"},
            },
            confidence=confidence, source_tier=source_tier,
            evidence_family="nrsa_field", used_fallback=False)
        if connected:
            ev.trace["completeness"] = "partial"
        note = (f"Survey date {nrsa.get('date')}; {regime}. "
                "Interpret against the naturally expected flow regime.")
        if nrsa.get("warning"):
            note += f" {nrsa['warning']}"
        return MetricResult(
            LOW_FLOW_ID, value=float(wetted),
            value_text=f"{float(wetted):.1f}% wetted channel — {regime}",
            rating=ev.rating, confidence=confidence, source=source,
            note=note, scoring=ev.trace)

    hyd_cat, hyd_ws = base.integrity_pair(ctx, "hyd")
    ev = screening_methods.evaluate(
        LOW_FLOW_ID,
        {"hydCatchment": hyd_cat, "hydWatershed": hyd_ws},
        context={"fcode": fcode, "flowRegime": regime},
        input_meta={
            "hydCatchment": {"source": "EPA StreamCat HYDcat"},
            "hydWatershed": {"source": "EPA StreamCat HYDws"},
        },
        confidence="L", variant_key="streamcat-hyd-integrity",
        source_tier="screening-proxy", evidence_family="iwi_landscape",
        used_fallback=True)
    if ev.rating is None:
        return unavailable(
            LOW_FLOW_ID,
            "eligible NRSA wetted-channel evidence and both StreamCat HYD components are unavailable",
            "L", scoring=ev.trace,
            value_text=f"low-flow evidence unavailable — {regime}")
    value = float(ev.combined_value)
    return MetricResult(
        LOW_FLOW_ID, value=value,
        value_text=(f"HYD integrity fallback {value:.2f} "
                    f"(catchment {float(hyd_cat):.2f}; watershed {float(hyd_ws):.2f})"),
        rating=ev.rating, confidence="L",
        source="EPA StreamCat HYD catchment + watershed components",
        note=("Landscape-integrity fallback, not observed wetted-channel condition; "
              f"{regime}. The 0.40/0.70 classes are EASI integration tiers."),
        scoring=ev.trace)


def hyporheic(ctx: AnalysisContext) -> MetricResult:
    """Channel-gradient screen of hyporheic-exchange potential.

    Slope alone is rated (it drives bed exchange, the dominant pathway).
    Sinuosity rides along as a context-only input in the scoring trace."""
    slope, sinuosity = ctx.slope, ctx.sinuosity
    ev = screening_methods.evaluate(
        HYPORHEIC_ID, {"slope": slope, "sinuosity": sinuosity},
        input_meta={
            "slope": {"source": "NHDPlus slope"},
            "sinuosity": {"source": "selected reach geometry"},
        },
        confidence="L")
    if ev.rating is None:
        return unavailable(
            HYPORHEIC_ID, "channel slope is required", "L",
            scoring=ev.trace)
    value = float(ev.combined_value)
    sin_txt = ("" if sinuosity is None
               else f" (sinuosity {float(sinuosity):.2f} shown as context)")
    return MetricResult(
        HYPORHEIC_ID, value=round(value, 4),
        value_text=f"channel slope {value:.4f} m/m{sin_txt}",
        rating=ev.rating, confidence="L",
        source="NHDPlus slope",
        note=("Slope screens vertical exchange potential. Bed hydraulic "
              "conductivity is unavailable, so steep bedrock or fine-bedded "
              "reaches can overpredict exchange."),
        scoring=ev.trace)
