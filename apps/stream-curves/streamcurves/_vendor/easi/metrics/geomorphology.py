"""Geomorphology-discipline EASI metric adapters."""
from __future__ import annotations

from .. import geomorph, screening_methods, watershed
from . import base
from .base import AnalysisContext, MetricResult, unavailable

SEDIMENT_ID = "sediment-continuity-sediment-supply-potential-watershed-banks"
SUBSTRATE_ID = (
    "bed-composition-and-large-wood-substrate-condition-grain-size-"
    "embeddedness-fines-consolidation")
BANK_EROSION_ID = "channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition"
CHANNEL_EVOL_ID = "channel-evolution-channel-evolution-stage-and-trends"
CHANNELIZED_FCODES = {33600, 33601, 33603}


def rate_channel_evolution(bhr, er=None, fcode=None):
    """Pure channel-adjustment proxy helper used by tests and geometry edits."""
    variant = "channelized-fcode" if fcode in CHANNELIZED_FCODES else None
    values = {"fcode": fcode} if variant else {"bhr": bhr, "er": er,
                                                 "fcodeContext": fcode}
    return screening_methods.evaluate(
        CHANNEL_EVOL_ID, values, variant_key=variant,
        evidence_family="incision_geometry", used_fallback=True).rating


def channel_evolution(ctx: AnalysisContext) -> MetricResult:
    """Canal classification, otherwise the fixed BHR+ER susceptibility proxy."""
    geom = ctx.extras.get("reach_geomorph") or {}
    bhr = geom.get("bank_height_ratio")
    er = geom.get("entrenchment_ratio")
    edge = bool(geom.get("edge_limited"))
    confidence = "L"
    if ctx.fcode in CHANNELIZED_FCODES:
        ev = screening_methods.evaluate(
            CHANNEL_EVOL_ID, {"fcode": ctx.fcode},
            input_meta={"fcode": {"source": "NHDPlus FCODE"}},
            confidence="M", variant_key="channelized-fcode",
            source_tier="screening-proxy", evidence_family="channelization_class",
            used_fallback=True)
        return MetricResult(
            CHANNEL_EVOL_ID, value=ctx.fcode,
            value_text=f"canal/ditch classification (NHD FCODE {ctx.fcode})",
            rating=ev.rating, confidence="M", source="NHDPlus FCODE",
            note="Artificial channelization is directly identified; current adjustment severity is not measured.",
            scoring=ev.trace)

    source = base.xs_source(geom)
    reach_n = int((geom.get("reach") or {}).get("n") or 0)
    bhr_stats = (geom.get("reach") or {}).get("bank_height_ratio") or {}
    ev = screening_methods.evaluate(
        CHANNEL_EVOL_ID, {"bhr": bhr, "er": er, "fcodeContext": ctx.fcode},
        input_meta={
            "bhr": {"source": source},
            "er": {"source": source},
            "fcodeContext": {"source": "NHDPlus FCODE"},
        },
        confidence=confidence, source_tier="screening-proxy",
        evidence_family="incision_geometry", used_fallback=True)
    if ev.rating is None:
        return unavailable(
            CHANNEL_EVOL_ID,
            "both BHR and ER are required for the channel-adjustment proxy",
            confidence, scoring=ev.trace)
    note = ("Low-confidence susceptibility proxy; BHR and ER are channel-evolution clues, "
            "not a formal stage assessment. Observed stage evidence supersedes this result.")
    if reach_n >= 2:
        note += " " + base.XS_REACH_NOTE
    cap_note = base.xs_cap_note(bhr_stats)
    if cap_note:
        note += " " + cap_note
    if edge:
        note += " Flood-prone width reached the DEM buffer edge; ER may be underestimated."
    reach_txt = f", reach medians of {reach_n} sections" if reach_n >= 2 else ""
    bhr_txt = geomorph.fmt_bhr(bhr, bhr_stats.get("median_capped"), words=True)
    return MetricResult(
        CHANNEL_EVOL_ID, value={"bhr": bhr, "er": er},
        value_text=(f"channel-adjustment susceptibility (BHR {bhr_txt}, "
                    f"ER {float(er):.2f}{reach_txt}), {ev.trace.get('governingInput')} governs"),
        rating=ev.rating, confidence=confidence,
        source=f"{source} + NHDPlus FCODE",
        note=note,
        scoring=ev.trace)


def sediment_supply(ctx: AnalysisContext) -> MetricResult:
    """Most limiting of agriculture, soil erodibility, and road density."""
    agriculture = base.ag_pct(ctx)
    k_factor = watershed.value(ctx, "soilKFactor")
    road_density = watershed.value(ctx, "roadDensity")
    ev = screening_methods.evaluate(
        SEDIMENT_ID,
        {"agriculture": agriculture, "kFactor": k_factor,
         "roadDensity": road_density},
        input_meta={
            "agriculture": {"source": watershed.input_source(ctx, "sediment.agriculture")},
            "kFactor": {"source": watershed.input_source(ctx, "sediment.kFactor")},
            "roadDensity": {"source": watershed.input_source(ctx, "sediment.roadDensity")},
        },
        confidence="M")
    if ev.rating is None:
        return unavailable(
            SEDIMENT_ID,
            watershed.guidance(
                ctx, "agriculture, K-factor, and road density are all required"),
            "M", scoring=ev.trace)
    inputs = {x["key"]: x for x in ev.trace["inputs"]}
    governing = ev.trace["governingInput"]
    gov = inputs.get(governing) or {}
    parts = []
    for key, spec in (("agriculture", "agriculture {:.1f}%"),
                      ("kFactor", "K {:.2f}"),
                      ("roadDensity", "roads {:.2f} km/km²")):
        v = (inputs.get(key) or {}).get("value")
        if v is not None:
            parts.append(spec.format(float(v)))
    gov_label = {"agriculture": "agricultural cover",
                 "kFactor": "soil erodibility",
                 "roadDensity": "road density"}.get(governing, governing)
    return MetricResult(
        SEDIMENT_ID,
        value=(None if gov.get("value") is None else float(gov["value"])),
        value_text=f"{gov_label} governs ({', '.join(parts)})",
        rating=ev.rating, confidence="M",
        source=watershed.result_source(ctx, "sediment"),
        note=("The most limiting source indicator governs. K-factor is "
              "intrinsic erodibility and can lower the rating without "
              "disturbance."),
        scoring=ev.trace)


def substrate(ctx: AnalysisContext) -> MetricResult:
    """Observed NRSA embeddedness, then the StreamCat SED fallback."""
    nrsa = base.nrsa_evidence(ctx)
    embeddedness = (nrsa or {}).get("embeddednessPct")
    if embeddedness is not None:
        connected = nrsa.get("matchType") == "connected_nearby"
        confidence = nrsa.get("confidence") or ("M/L" if connected else "M")
        source_tier = "connected-nearby" if connected else "observed"
        source = (f"EPA NRSA 2018-19 site {nrsa.get('siteId')}"
                  + (f" ({float(nrsa.get('distanceMi') or 0):.2f} mi connected)"
                     if connected else " (exact COMID)"))
        ev = screening_methods.evaluate(
            SUBSTRATE_ID, {"embeddednessPct": embeddedness},
            input_meta={"embeddednessPct": {"source": source, "details": nrsa}},
            confidence=confidence, source_tier=source_tier,
            evidence_family="nrsa_field", used_fallback=False)
        if connected:
            ev.trace["completeness"] = "partial"
        note = f"NRSA survey date {nrsa.get('date')}. Embeddedness is one component of substrate condition."
        if nrsa.get("warning"):
            note += f" {nrsa['warning']}"
        return MetricResult(
            SUBSTRATE_ID, value=float(embeddedness),
            value_text=f"{float(embeddedness):.1f}% substrate embeddedness",
            rating=ev.rating, confidence=confidence, source=source,
            note=note, scoring=ev.trace)

    sed_cat, sed_ws = base.integrity_pair(ctx, "sed")
    ev = screening_methods.evaluate(
        SUBSTRATE_ID, {"sedCatchment": sed_cat, "sedWatershed": sed_ws},
        input_meta={
            "sedCatchment": {"source": "EPA StreamCat SEDcat"},
            "sedWatershed": {"source": "EPA StreamCat SEDws"},
        },
        confidence="L", variant_key="streamcat-sed-integrity",
        source_tier="screening-proxy", evidence_family="iwi_landscape",
        used_fallback=True)
    if ev.rating is None:
        return unavailable(
            SUBSTRATE_ID,
            "eligible NRSA embeddedness and both StreamCat SED components "
            "are unavailable",
            "L", scoring=ev.trace)
    value = float(ev.combined_value)
    return MetricResult(
        SUBSTRATE_ID, value=value,
        value_text=(f"SED integrity fallback {value:.2f} "
                    f"(catchment {float(sed_cat):.2f}, watershed {float(sed_ws):.2f})"),
        rating=ev.rating, confidence="L",
        source="EPA StreamCat SED catchment + watershed components",
        note=("Landscape-integrity fallback, not observed embeddedness or bed composition; "
              "the 0.40/0.70 classes are EASI integration tiers."),
        scoring=ev.trace)


def bank_erosion(ctx: AnalysisContext) -> MetricResult:
    """Low-confidence BHR bank-instability susceptibility fallback."""
    geom = ctx.extras.get("reach_geomorph") or {}
    bhr = geom.get("bank_height_ratio")
    reach = geomorph.describe_reach(geom.get("reach"), "bank_height_ratio")
    bhr_stats = (geom.get("reach") or {}).get("bank_height_ratio") or {}
    source = base.xs_source(geom)
    ev = screening_methods.evaluate(
        BANK_EROSION_ID, {"bhr": bhr},
        input_meta={"bhr": {"source": source}},
        confidence="L", source_tier="screening-proxy",
        evidence_family="incision_geometry", used_fallback=True)
    if ev.rating is None:
        return unavailable(
            BANK_EROSION_ID,
            "bank-height ratio unavailable for the bank-instability susceptibility proxy",
            "L", scoring=ev.trace)
    warning = " ".join(ev.trace.get("warnings") or [])
    cap_note = base.xs_cap_note(bhr_stats)
    bhr_txt = geomorph.fmt_bhr(bhr, bhr_stats.get("median_capped"), words=True)
    return MetricResult(
        BANK_EROSION_ID, value=float(bhr),
        value_text=(f"bank-instability susceptibility from BHR {bhr_txt}"
                    + (f", {reach}" if reach else "")),
        rating=ev.rating, confidence="L",
        source=source,
        note=((warning + " ") if warning else "")
             + ("BHR does not detect armoring or directly measure erosion. Complete observed "
                "erosion and armoring percentages supersede this proxy.")
             + ((" " + base.XS_REACH_NOTE) if reach else "")
             + ((" " + cap_note) if cap_note else ""),
        scoring=ev.trace)
