"""Hydrology-discipline EASI metric adapters."""
from __future__ import annotations

from .. import screening_methods
from . import base
from .base import AnalysisContext, MetricResult, unavailable

IMPERVIOUS_ID = "catchment-hydrology-impervious-surface-cover"
WETLANDS_ID = "surface-water-storage-percent-wetlands-in-watershed"
FLOW_ALTERATION_ID = "streamflow-regime-flow-alteration-regulation-water-use"
REACH_INFLOW_ID = "reach-inflow-concentrated-runoff-stormwater-inputs"


def _impervious_pct(ctx: AnalysisContext):
    value = base.sc(ctx).get("pctimp2019ws")
    if value is not None:
        return float(value), "EPA StreamCat pctimp2019 (watershed)"
    value = (ctx.extras.get("landcover") or {}).get("impervious_pct")
    return ((None, "") if value is None
            else (float(value), "NLCD 2021 impervious (watershed)"))


def _agriculture_pct(ctx: AnalysisContext):
    value = base.ag_pct(ctx)
    if value is not None:
        return float(value), "EPA StreamCat crop + hay (watershed)"
    value = (ctx.extras.get("landcover") or {}).get("ag_pct")
    return ((None, "") if value is None
            else (float(value), "NLCD 2021 agriculture (watershed)"))


def impervious(ctx: AnalysisContext) -> MetricResult:
    """Land-cover pressure; the worse available input governs."""
    imp, imp_source = _impervious_pct(ctx)
    ag, ag_source = _agriculture_pct(ctx)
    ev = screening_methods.evaluate(
        IMPERVIOUS_ID,
        {"impervious": imp, "agriculture": ag},
        input_meta={
            "impervious": {"source": imp_source},
            "agriculture": {"source": ag_source},
        },
        confidence="H",
    )
    if ev.rating is None:
        return unavailable(
            IMPERVIOUS_ID, "no land-cover data available", "H",
            scoring=ev.trace)

    inputs = {x["key"]: x for x in ev.trace["inputs"]}
    governing = ev.trace["governingInput"]
    gov = inputs[governing]
    available = [x for x in ev.trace["inputs"] if x["available"]]
    comparison = "; ".join(
        f"{x['label'].replace('Watershed ', '')} {float(x['value']):.1f}% ({x.get('rating')})"
        for x in available)
    value_text = (
        f"{float(gov['value']):.1f}% {gov['label'].replace('Watershed ', '').lower()} "
        f"— {governing} governs")
    detail = {
        "governing": governing,
        "impervious": (None if imp is None else
                       {"pct": round(imp, 1), "rating": inputs["impervious"].get("rating")}),
        "agriculture": (None if ag is None else
                        {"pct": round(ag, 1), "rating": inputs["agriculture"].get("rating")}),
    }
    sources = " + ".join(x["source"] for x in available if x.get("source"))
    return MetricResult(
        IMPERVIOUS_ID, value=float(gov["value"]), value_text=value_text,
        rating=ev.rating, confidence="H", source=sources,
        note=f"{comparison}. The more limiting input governs.",
        detail=detail, scoring=ev.trace)


def wetlands(ctx: AnalysisContext) -> MetricResult:
    """Combined StreamCat woody + herbaceous wetland extent.

    Both source fields are required. A missing class is unknown and is never
    converted to zero.
    """
    s = base.sc(ctx)
    woody = s.get("pctwdwet2019ws")
    herbaceous = s.get("pcthbwet2019ws")
    ev = screening_methods.evaluate(
        WETLANDS_ID,
        {"woodyWetland": woody, "herbaceousWetland": herbaceous},
        input_meta={
            "woodyWetland": {"source": "EPA StreamCat pctwdwet2019 (watershed)"},
            "herbaceousWetland": {"source": "EPA StreamCat pcthbwet2019 (watershed)"},
        },
        confidence="H",
    )
    if ev.rating is None:
        return unavailable(
            WETLANDS_ID,
            "both woody- and herbaceous-wetland source fields are required",
            "H", scoring=ev.trace)
    value = float(ev.combined_value)
    return MetricResult(
        WETLANDS_ID, value=round(value, 2),
        value_text=f"{value:.1f}% combined woody + herbaceous wetland cover",
        rating=ev.rating, confidence="H",
        source="EPA StreamCat wetlands (watershed)",
        note="Provisional national screening tiers; natural wetland abundance is regional.",
        scoring=ev.trace)


def flow_alteration(ctx: AnalysisContext) -> MetricResult:
    """Degree of regulation: storage divided by annual runoff volume."""
    s = base.sc(ctx)
    storage, runoff = s.get("damnrmstorws"), s.get("runoffws")
    ev = screening_methods.evaluate(
        FLOW_ALTERATION_ID,
        {"storage": storage, "runoff": runoff},
        input_meta={
            "storage": {"source": "EPA StreamCat DamNrmStorWs"},
            "runoff": {"source": "EPA StreamCat RunoffWs"},
        },
        confidence="M",
    )
    if ev.rating is None:
        note = ("annual runoff must be present and greater than zero"
                if runoff is not None else "storage and annual runoff are required")
        return unavailable(FLOW_ALTERATION_ID, note, "M", scoring=ev.trace)
    dor = float(ev.combined_value)
    return MetricResult(
        FLOW_ALTERATION_ID, value=round(dor, 3),
        value_text=(f"degree of regulation {dor:.2f}% "
                    f"(storage {float(storage):,.0f} m³/km²; runoff {float(runoff):,.0f} mm)"),
        rating=ev.rating, confidence="M",
        source="EPA StreamCat normalized storage + annual runoff",
        note="Storage/annual-runoff screen; does not represent operating rules or diversions.",
        scoring=ev.trace)


def reach_inflow(ctx: AnalysisContext) -> MetricResult:
    """Road-density proxy for concentrated inflow pressure."""
    road_density = base.sc(ctx).get("rddensws")
    ev = screening_methods.evaluate(
        REACH_INFLOW_ID, {"roadDensity": road_density},
        input_meta={"roadDensity": {"source": "EPA StreamCat RddensWs"}},
        confidence="L")
    if ev.rating is None:
        return unavailable(REACH_INFLOW_ID, "no road-density data", "L",
                           scoring=ev.trace)
    value = float(ev.combined_value)
    return MetricResult(
        REACH_INFLOW_ID, value=round(value, 2),
        value_text=f"{value:.2f} km/km² road density",
        rating=ev.rating, confidence="L",
        source="EPA StreamCat road density",
        note="Directional proxy for concentrated inflow pressure; does not count outfalls.",
        scoring=ev.trace)
