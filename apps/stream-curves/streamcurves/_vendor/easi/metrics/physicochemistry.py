"""Physicochemistry-discipline EASI metric adapters."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .. import geo, screening_methods
from ..datasources import attains, wqp
from . import base
from .base import AnalysisContext, MetricResult, unavailable

IMPAIRMENT_ID = "water-and-soil-quality-regulatory-impairment-status-305b-303d-tmdl"
CPOM_ID = "carbon-processing-detrital-processing-cpom-retention-shredders"
NUTRIENTS_ID = "nutrient-cycling-nitrogen-and-phosphorus-concentrations"
TEMPERATURE_ID = "light-and-thermal-regime-stream-temperature"


def _attains_result(record: dict, confidence: str) -> MetricResult:
    category = record.get("ircategory")
    ev = screening_methods.evaluate(
        IMPAIRMENT_ID, {"category": category},
        input_meta={"category": {
            "source": "EPA ATTAINS",
            "details": {
                "assessmentUnit": record.get("assessment_unit"),
                "assessmentName": record.get("assessment_name"),
                "overallStatus": record.get("overallstatus"),
                "distanceM": record.get("distance_m"),
                "matchType": record.get("match_type"),
            },
        }},
        confidence=confidence,
        source_tier=("connected-nearby" if record.get("match_type") == "nearby"
                     else "observed"),
        evidence_family="attains_assessment", used_fallback=False)
    distance = record.get("distance_m")
    if record.get("match_type") == "nearby":
        prefix = f"nearest assessed unit {float(distance):,.0f} m away"
        warning = "Nearby, not necessarily this reach."
    else:
        prefix = "assessment unit intersects selected point"
        warning = ""
    text = (f"{prefix}: IR category {category or '—'}"
            f" (AU {record.get('assessment_unit') or '—'})")
    if ev.rating is None:
        return MetricResult(
            IMPAIRMENT_ID, value=category, value_text=text, rating=None,
            confidence=confidence, source="EPA ATTAINS", status="not_assessed",
            note=("Category 3 has insufficient evidence and remains unscored. "
                  + warning).strip(),
            scoring=ev.trace)
    return MetricResult(
        IMPAIRMENT_ID, value=category, value_text=text,
        rating=ev.rating, confidence=confidence, source="EPA ATTAINS",
        note=warning, scoring=ev.trace)


def _chem_fallback(ctx: AnalysisContext, metric_id: str, *, reason: str) -> MetricResult:
    chem_cat, chem_ws = base.integrity_pair(ctx, "chem")
    variant = ("streamcat-chem-integrity-nutrient" if metric_id == NUTRIENTS_ID
               else "streamcat-chem-integrity-regulatory")
    ev = screening_methods.evaluate(
        metric_id, {"chemCatchment": chem_cat, "chemWatershed": chem_ws},
        context={"fallbackReason": reason},
        input_meta={
            "chemCatchment": {"source": "EPA StreamCat CHEMcat"},
            "chemWatershed": {"source": "EPA StreamCat CHEMws"},
        },
        confidence="L", variant_key=variant,
        source_tier="screening-proxy", evidence_family="iwi_landscape",
        used_fallback=True)
    label = ("nutrient-condition" if metric_id == NUTRIENTS_ID
             else "water-quality condition")
    if ev.rating is None:
        return unavailable(
            metric_id,
            f"{reason} and both StreamCat CHEM components are also required",
            "L", scoring=ev.trace,
            value_text=f"{label} evidence unavailable")
    value = float(ev.combined_value)
    return MetricResult(
        metric_id, value=value,
        value_text=(f"CHEM integrity fallback {value:.2f} "
                    f"(catchment {float(chem_cat):.2f}, watershed {float(chem_ws):.2f})"),
        rating=ev.rating, confidence="L",
        source="EPA StreamCat CHEM catchment + watershed components",
        note=(f"{reason}. Landscape-integrity fallback for {label}. It is not "
              "a measured concentration or regulatory determination. The 0.40/0.70 "
              "classes are EASI integration tiers."),
        scoring=ev.trace)


def impairment(ctx: AnalysisContext) -> MetricResult:
    """Conclusive ATTAINS category, then StreamCat CHEM condition context."""
    exact = attains.impairment_at_point(ctx.lat, ctx.lon)
    if exact.get("assessment_unit"):
        result = _attains_result(exact, "H")
        if result.rating:
            return result
        return _chem_fallback(
            ctx, IMPAIRMENT_ID,
            reason="intersecting ATTAINS Category 3 is inconclusive")
    nearby = attains.impairment_near_point(ctx.lat, ctx.lon)
    if nearby.get("assessment_unit"):
        result = _attains_result(nearby, "M/L")
        if result.rating:
            return result
        return _chem_fallback(
            ctx, IMPAIRMENT_ID,
            reason="nearby ATTAINS Category 3 is inconclusive")
    return _chem_fallback(
        ctx, IMPAIRMENT_ID,
        reason="no qualifying ATTAINS assessed unit at the point or within 2 km")


def detrital_cpom(ctx: AnalysisContext) -> MetricResult:
    """Organic-matter supply potential from four complete riparian classes."""
    breakdown = base.riparian_veg_breakdown(ctx)
    values = ({
        "forest": breakdown["forest"], "shrub": breakdown["shrub"],
        "grassland": breakdown["grassland"], "wetland": breakdown["wetland"],
    } if breakdown else {
        "forest": None, "shrub": None, "grassland": None, "wetland": None,
    })
    ev = screening_methods.evaluate(
        CPOM_ID, values,
        input_meta={key: {"source": "EPA StreamCat rp100"}
                    for key in values},
        confidence="M")
    if ev.rating is None:
        return unavailable(
            CPOM_ID,
            "forest, shrub, grassland, and wetland source fields are all required",
            "M", scoring=ev.trace)
    total = float(ev.combined_value)
    return MetricResult(
        CPOM_ID, value=round(total, 1),
        value_text=f"{total:.1f}% organic-matter supply potential (100 m corridor)",
        rating=ev.rating, confidence="M",
        source="EPA StreamCat riparian land cover (rp100)",
        note=("Proxy for supply potential only; it does not measure CPOM retention "
              "or shredder condition."),
        detail={"kind": "riparian_veg", **breakdown},
        scoring=ev.trace)


def nutrients(ctx: AnalysisContext) -> MetricResult:
    """Regional NRSA TN/TP condition from normalized WQP observations."""
    with ThreadPoolExecutor(max_workers=2) as executor:
        tn_future = executor.submit(wqp.sample_summary, "tn", ctx.lat, ctx.lon)
        tp_future = executor.submit(wqp.sample_summary, "tp", ctx.lat, ctx.lon)
        tn_summary, tp_summary = tn_future.result(), tp_future.result()

    region = geo.nars9_at(ctx.lat, ctx.lon)
    region_code = (region or {}).get("code")
    tn = (tn_summary or {}).get("value")
    tp = (tp_summary or {}).get("value")
    available = [summary for summary in (tn_summary, tp_summary)
                 if summary and summary.get("value") is not None]
    observations = sum(int(summary.get("observation_count") or 0) for summary in available)
    nearest_values = [summary.get("nearest_distance_mi") for summary in available
                      if summary.get("nearest_distance_mi") is not None]
    nearest = min(nearest_values) if nearest_values else None
    # A weak analyte must not inherit confidence from a better-sampled companion
    # analyte when the lower index can govern the combined result.
    confidence = (
        "M" if available and all(
            int(summary.get("observation_count") or 0) >= 3
            and summary.get("nearest_distance_mi") is not None
            and float(summary["nearest_distance_mi"]) <= 1
            for summary in available)
        else "L")
    ev = screening_methods.evaluate(
        NUTRIENTS_ID, {"tn": tn, "tp": tp},
        context={"region": region_code, "regionName": (region or {}).get("name")},
        input_meta={
            "tn": {"source": "Water Quality Portal", "details": tn_summary},
            "tp": {"source": "Water Quality Portal", "details": tp_summary},
        },
        confidence=confidence, source_tier="connected-nearby",
        evidence_family="wqp_monitoring", used_fallback=False)

    parts = []
    if tn is not None:
        parts.append(f"TN {float(tn):.3f} mg/L")
    if tp is not None:
        parts.append(f"TP {float(tp):.4f} mg/L")
    if not parts:
        note = ("NARS region could not be resolved" if not region_code
                else "no qualifying total-fraction WQP TN/TP observations")
        return _chem_fallback(ctx, NUTRIENTS_ID, reason=note)
    if ev.rating is None:
        return _chem_fallback(
            ctx, NUTRIENTS_ID,
            reason="official NARS region is unavailable for WQP regional thresholds")

    station_count = sum(int(summary.get("station_count") or 0) for summary in available)
    excluded = sum(int(summary.get("excluded_count") or 0) for summary in
                   (tn_summary, tp_summary) if summary)
    dates = [d for summary in available
             for d in (summary.get("date_start"), summary.get("date_end")) if d]
    coverage = (
        f"{observations} valid observation(s), {station_count} station(s)"
        + (f", {min(dates)} to {max(dates)}" if dates else "")
        + (f", nearest {nearest:.2f} mi" if nearest is not None else "")
        + f"; {excluded} row(s) excluded")
    return MetricResult(
        NUTRIENTS_ID, value={"tn": tn, "tp": tp, "region": region_code},
        value_text=f"{', '.join(parts)} ({region_code} region)",
        rating=ev.rating, confidence=confidence,
        source="Water Quality Portal + EPA NARS nine-region thresholds",
        note=coverage, detail={"tn": tn_summary, "tp": tp_summary, "region": region},
        scoring=ev.trace)


def stream_temperature(ctx: AnalysisContext) -> MetricResult:
    """Thermal-regulation vulnerability, not stream-temperature scoring."""
    woody_breakdown = base.riparian_woody_breakdown(ctx)
    woody = None if woody_breakdown is None else woody_breakdown["total"]
    impervious = base.sc(ctx).get("pctimp2019ws")
    # Temperature observations are retained as context only.
    temperature = wqp.sample_summary("temp", ctx.lat, ctx.lon)
    ev = screening_methods.evaluate(
        TEMPERATURE_ID,
        {"woodyRiparian": woody, "impervious": impervious},
        input_meta={
            "woodyRiparian": {
                "source": "EPA StreamCat forest + shrub + woody wetland (rp100)",
                "details": woody_breakdown,
            },
            "impervious": {"source": "EPA StreamCat pctimp2019ws"},
        },
        confidence="L")
    ev.trace["context"]["wqpTemperature"] = temperature
    if ev.rating is None:
        return unavailable(
            TEMPERATURE_ID,
            "both woody riparian cover and watershed impervious cover are required",
            "L", scoring=ev.trace)
    inputs = {item["key"]: item for item in ev.trace["inputs"]}
    governing = ev.trace["governingInput"]
    context_note = ""
    if temperature and temperature.get("value") is not None:
        context_note = (
            f" Nearby WQP temperature context: {float(temperature['value']):.1f} °C "
            f"from {temperature.get('observation_count', 0)} observation(s), not scored.")
    return MetricResult(
        TEMPERATURE_ID, value=float(ev.combined_value),
        value_text=(f"thermal vulnerability: woody riparian {float(woody):.1f}%, "
                    f"impervious {float(impervious):.1f}% ({governing} governs)"),
        rating=ev.rating, confidence="L",
        source="EPA StreamCat woody riparian cover + watershed impervious cover",
        note=("Vulnerability proxy for thermal loading and shade loss; not stream temperature."
              + context_note),
        detail={"woody": woody_breakdown, "temperatureContext": temperature,
                "governing": governing, "inputs": inputs},
        scoring=ev.trace)
