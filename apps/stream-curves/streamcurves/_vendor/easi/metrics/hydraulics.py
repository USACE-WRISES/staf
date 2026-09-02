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
    extrapolated = bool(geom.get("bankfull_extrapolated"))
    confidence = "L" if (geom.get("edge_limited") or extrapolated) else "M"
    ev = screening_methods.evaluate(
        FLOODPLAIN_ENGAGEMENT_ID, {"bhr": bhr},
        input_meta={"bhr": {"source": "USGS 3DEP representative cross section"}},
        confidence=confidence)
    if ev.rating is None:
        return unavailable(
            FLOODPLAIN_ENGAGEMENT_ID,
            "bank-height ratio unavailable for the representative cross section",
            confidence, scoring=ev.trace)
    warning = " ".join(ev.trace.get("warnings") or [])
    note = warning or "Direct BHR screen; surveyed cross-section geometry may refine."
    if extrapolated:
        note += (" Drainage area is outside the Bieger fit range;"
                 " bankfull is extrapolated.")
    return MetricResult(
        FLOODPLAIN_ENGAGEMENT_ID, value=round(float(bhr), 3),
        value_text=f"bank-height ratio {float(bhr):.2f} (low-bank height / max bankfull depth)",
        rating=ev.rating, confidence=confidence,
        source="USGS 3DEP representative cross section",
        note=note,
        scoring=ev.trace)


def rate_entrenchment(er):
    """Direct entrenchment-ratio rating."""
    return screening_methods.evaluate(ENTRENCHMENT_ID, {"er": er}).rating


def floodplain_access(ctx: AnalysisContext) -> MetricResult:
    """Lateral floodplain access from entrenchment ratio directly."""
    geom = ctx.extras.get("reach_geomorph") or {}
    er = geom.get("entrenchment_ratio")
    edge = bool(geom.get("edge_limited"))
    extrapolated = bool(geom.get("bankfull_extrapolated"))
    confidence = "L" if (edge or extrapolated) else "M"
    ev = screening_methods.evaluate(
        ENTRENCHMENT_ID, {"er": er},
        input_meta={"er": {"source": "USGS 3DEP representative cross section"}},
        confidence=confidence)
    if ev.rating is None:
        return unavailable(
            ENTRENCHMENT_ID, "3DEP entrenchment ratio unavailable for reach",
            confidence, scoring=ev.trace)
    res = geom.get("dem_resolution_m") or 10
    note = f"DEM {res} m, bankfull from national curve."
    if edge:
        note += " Flood-prone width reached the buffer edge and ER may be underestimated."
    if extrapolated:
        note += " Drainage area is outside the Bieger fit range; bankfull is extrapolated."
    note += " Naturally confined valleys require field interpretation."
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
        note = (f"Survey date {nrsa.get('date')}, {regime}. "
                "Interpret against the naturally expected flow regime.")
        if nrsa.get("warning"):
            note += f" {nrsa['warning']}"
        return MetricResult(
            LOW_FLOW_ID, value=float(wetted),
            value_text=f"{float(wetted):.1f}% wetted channel ({regime})",
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
            base.comid_evidence_note(
                ctx, "eligible NRSA wetted-channel evidence and both StreamCat HYD "
                     "components are unavailable"),
            "L", scoring=ev.trace,
            value_text=f"low-flow evidence unavailable ({regime})")
    value = float(ev.combined_value)
    return MetricResult(
        LOW_FLOW_ID, value=value,
        value_text=(f"HYD integrity fallback {value:.2f} "
                    f"(catchment {float(hyd_cat):.2f}, watershed {float(hyd_ws):.2f})"),
        rating=ev.rating, confidence="L",
        source="EPA StreamCat HYD catchment + watershed components",
        note=("Landscape-integrity fallback, not observed wetted-channel condition "
              f"({regime}). The 0.40/0.70 classes are EASI integration tiers."),
        scoring=ev.trace)


def hyporheic(ctx: AnalysisContext) -> MetricResult:
    """Best of the slope and sinuosity exchange pathways.

    Slope screens vertical bedform-driven exchange and sinuosity screens
    lateral meander-driven exchange. Either mechanism alone indicates exchange
    potential, so the better pathway governs (max of the two rating indices)."""
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
            HYPORHEIC_ID,
            "channel slope and reach sinuosity are both unavailable", "L",
            scoring=ev.trace)
    inputs = {x["key"]: x for x in ev.trace["inputs"]}
    governing = ev.trace["governingInput"]
    gov = inputs.get(governing) or {}
    parts = []
    # sinuosity at 3 decimals, matching the delineation rounding, so a value
    # just under a band boundary (1.196) never displays as sitting on it (1.20)
    for key, spec in (("slope", "slope {:.4f} m/m"),
                      ("sinuosity", "sinuosity {:.3f}")):
        row = inputs.get(key) or {}
        if row.get("value") is not None and row.get("rating"):
            parts.append(f"{spec.format(float(row['value']))} ({row['rating']})")
    partial = ev.trace.get("completeness") == "partial"
    gov_value = float(gov["value"])
    gov_text = (f"channel slope {gov_value:.4f} m/m" if governing == "slope"
                else f"sinuosity {gov_value:.3f}")
    suffix = ", single pathway" if partial else ""
    sources = [x.get("source") for x in ev.trace["inputs"]
               if x.get("available") and x.get("source")]
    return MetricResult(
        HYPORHEIC_ID, value=round(gov_value, 4),
        value_text=f"{gov_text} ({governing} pathway governs{suffix})",
        rating=ev.rating, confidence="L",
        source=" + ".join(sources),
        note=(f"{', '.join(parts)}. The better pathway governs. Slope screens "
              "vertical bedform-driven exchange and sinuosity screens lateral "
              "meander-driven exchange. Bed hydraulic conductivity is "
              "unavailable, so steep bedrock or fine-bedded reaches can "
              "overpredict exchange."),
        scoring=ev.trace)
