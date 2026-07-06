"""Physicochemistry-discipline EASI metric adapters."""
from __future__ import annotations

from . import base
from ..datasources import attains, wqp
from .base import AnalysisContext, MetricResult, unavailable

IMPAIRMENT_ID = "water-and-soil-quality-regulatory-impairment-status-305b-303d-tmdl"
CPOM_ID = "carbon-processing-detrital-processing-cpom-retention-shredders"
NUTRIENTS_ID = "nutrient-cycling-nitrogen-and-phosphorus-concentrations"
TEMPERATURE_ID = "light-and-thermal-regime-stream-temperature"


def impairment(ctx: AnalysisContext) -> MetricResult:
    """CWA 303(d)/305(b) use-support, always rated via a fallback chain.

    EPA ATTAINS at the reach's catchment (exact, high confidence) -> nearby assessed
    waters within ~2 km (keyless buffered query, medium confidence, not reach-specific)
    -> a modeled landscape risk surrogate (low confidence). Source-selectable
    (config.SOURCE_OPTIONS): "attains" (default chain) or "surrogate" (force modeled).
    """
    src = (ctx.extras.get("source_choices") or {}).get(IMPAIRMENT_ID)
    if src == "surrogate":
        return _impairment_surrogate(ctx)
    a = attains.impairment_at_point(ctx.lat, ctx.lon)
    if a and a.get("assessment_unit"):
        return _attains_result(
            a, "H",
            f"{a.get('overallstatus') or 'assessed'} (IR {a.get('ircategory') or '-'}) "
            f"- AU {a['assessment_unit']}")
    near = attains.impairment_near_point(ctx.lat, ctx.lon)
    if near and near.get("assessment_unit"):
        return _attains_result(
            near, "M",
            f"nearest assessed waters within ~2 km: {near.get('overallstatus') or 'assessed'} "
            f"(IR {near.get('ircategory') or '-'}, AU {near['assessment_unit']}); "
            f"reach not individually assessed")
    return _impairment_surrogate(ctx)


def _attains_result(a: dict, confidence: str, txt: str) -> MetricResult:
    """Map an ATTAINS assessment dict to a Good/Fair/Poor MetricResult."""
    ir = str(a.get("ircategory") or "")
    impaired = str(a.get("isimpaired") or "").upper() == "Y"
    status = str(a.get("overallstatus") or "")
    if impaired or ir.startswith("5") or ir.startswith("4"):
        rating = "Poor"
    elif "Fully Supporting" in status:
        rating = "Good"
    else:
        rating = "Fair"
    return MetricResult(IMPAIRMENT_ID, value=ir, value_text=txt, rating=rating,
                        confidence=confidence, source="EPA ATTAINS 303(d)/305(b)")


def _impairment_surrogate(ctx: AnalysisContext) -> MetricResult:
    """Modeled water-quality-impairment risk from landscape stressors (low confidence).

    Where no assessed waters exist in/near the reach, combine already-fetched
    StreamCat signals — impervious cover, agriculture, and road density raise NPS
    pollution risk; a riparian buffer mitigates it. Explicitly NOT a regulatory
    303(d) listing — a screening risk, overrideable with state data.
    """
    s = base.sc(ctx)
    imp, ag, rd = s.get("pctimp2019ws"), base.ag_pct(ctx), s.get("rddensws")
    rip = base.riparian_forest_pct(ctx)
    if all(v is None for v in (imp, ag, rd, rip)):
        return MetricResult(
            IMPAIRMENT_ID, value=None,
            value_text="not assessed (ATTAINS) and no landscape data — screening default",
            rating="Fair", confidence="L", source="default (no ATTAINS / landscape data)",
            note="not a regulatory 303(d) listing — overrideable")
    stress = (0.45 * min((imp or 0.0) / 25.0, 1.0)
              + 0.35 * min((ag or 0.0) / 60.0, 1.0)
              + 0.20 * min((rd or 0.0) / 5.0, 1.0))
    risk = max(0.0, min(1.0, stress - 0.15 * min((rip or 0.0) / 60.0, 1.0)))
    rating = base.band(risk, good_below=0.25, fair_below=0.5, higher_is_worse=True)
    return MetricResult(
        IMPAIRMENT_ID, value=round(risk, 2),
        value_text=f"modeled impairment risk {risk:.2f} (impervious {imp}%, ag {ag}%) "
                   f"— not ATTAINS-assessed",
        rating=rating, confidence="L",
        source="Modeled water-quality risk (landscape surrogate)",
        note="no assessed waters in/near reach — modeled risk, not a regulatory 303(d) "
             "listing; override with state data")


def detrital_cpom(ctx: AnalysisContext) -> MetricResult:
    """Riparian-forest proxy (100 m buffer) for CPOM input/retention."""
    rip = base.riparian_forest_pct(ctx)
    if rip is None:
        return unavailable(CPOM_ID, "no riparian forest data", "M")
    rating = base.band(rip, good_below=50, fair_below=20, higher_is_worse=False)
    return MetricResult(CPOM_ID, value=round(rip, 1),
                        value_text=f"{rip:.1f}% riparian forest (100 m buffer)",
                        rating=rating, confidence="M",
                        source="EPA StreamCat riparian forest (rp100)",
                        note="riparian-forest proxy for CPOM input/retention")


def nutrients(ctx: AnalysisContext) -> MetricResult:
    """Observed TN/TP near the reach (WQP), generic thresholds.

    Ecoregion reference criteria + SPARROW modeled backfill are later refinements.
    """
    # Fetch TN and TP concurrently so the metric waits max(tn, tp), not their sum
    # (each WQP call can take several seconds; this runs on its own worker thread).
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_tn = ex.submit(wqp.median_value, "tn", ctx.lat, ctx.lon)
        f_tp = ex.submit(wqp.median_value, "tp", ctx.lat, ctx.lon)
        tn, tp = f_tn.result(), f_tp.result()
    if tn is None and tp is None:
        return unavailable(NUTRIENTS_ID, "no WQP TN/TP observations near reach", "M")
    ratings = []
    if tn is not None:
        ratings.append(base.band(tn, good_below=0.5, fair_below=1.5, higher_is_worse=True))
    if tp is not None:
        ratings.append(base.band(tp, good_below=0.05, fair_below=0.10, higher_is_worse=True))
    rating = "Poor" if "Poor" in ratings else ("Fair" if "Fair" in ratings else "Good")
    parts = []
    if tn is not None:
        parts.append(f"TN {tn} mg/L")
    if tp is not None:
        parts.append(f"TP {tp} mg/L")
    return MetricResult(NUTRIENTS_ID, value={"tn": tn, "tp": tp},
                        value_text="; ".join(parts), rating=rating, confidence="M",
                        source="WQP observed (generic thresholds)",
                        note="ecoregion criteria + SPARROW backfill refine")


def stream_temperature(ctx: AnalysisContext) -> MetricResult:
    """Stream temperature: observed (WQP) with a climate/landscape surrogate fallback.

    Primary: median observed 'Temperature, water' near the reach (WQP), binned on
    generic coldwater thresholds. Where no nearby samples exist, fall back to a
    coarse, clearly-labeled climate surrogate (PRISM mean-annual air-temp normal
    tempered by riparian shading), so the metric is always rated and overrideable.

    Source is user-selectable (config.SOURCE_OPTIONS): "wqp" (observed, default) or
    "surrogate" (force the climate surrogate).
    """
    src = (ctx.extras.get("source_choices") or {}).get(TEMPERATURE_ID)
    if src == "surrogate":
        return _temperature_surrogate(ctx)
    t = wqp.median_value("temp", ctx.lat, ctx.lon)
    if t is not None:
        rating = base.band(t, good_below=20, fair_below=25, higher_is_worse=True)
        return MetricResult(TEMPERATURE_ID, value=t,
                            value_text=f"median {t} °C (observed)", rating=rating,
                            confidence="M", source="WQP observed temperature",
                            note="generic coldwater thresholds; species/season criteria refine")
    return _temperature_surrogate(ctx)


def _temperature_surrogate(ctx: AnalysisContext) -> MetricResult:
    """Climate/landscape thermal surrogate when no WQP samples exist (low confidence).

    Warmer climates raise baseline thermal stress; riparian canopy shades and
    buffers it (credited up to ~2 deg C of relief at full cover). Banded on the
    mean-annual air-temp normal, this is a coarse screening signal — explicitly
    NOT a measured water temperature — and is overrideable with monitoring data.
    """
    tair = base.sc(ctx).get("tmean8110ws")            # PRISM mean-annual air temp (deg C)
    rip = base.riparian_forest_pct(ctx)               # higher cover -> more shade/relief
    if tair is None:
        return MetricResult(
            TEMPERATURE_ID, value=None,
            value_text="no WQP samples and no climate data — screening default",
            rating="Fair", confidence="L",
            source="default (no WQP samples / StreamCat climate)",
            note="not a measured temperature; conservative screening default — overrideable")
    idx = tair - 2.0 * min((rip or 0.0) / 60.0, 1.0)
    rating = base.band(idx, good_below=12.0, fair_below=17.0, higher_is_worse=True)
    return MetricResult(
        TEMPERATURE_ID, value=round(idx, 1),
        value_text=f"climate thermal index {idx:.1f} °C "
                   f"(air-temp normal {tair:.1f} °C, riparian {rip}%)",
        rating=rating, confidence="L",
        source="Modeled climate/landscape surrogate (PRISM air-temp normal + riparian shade)",
        note="no nearby WQP samples — coarse climate surrogate, not measured water "
             "temperature; override with observed/continuous monitoring where available")
