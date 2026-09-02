"""Biology-discipline EASI metric adapters."""
from __future__ import annotations

from .. import screening_methods, watershed
from ..datasources import nas, nid_barriers
from . import base
from .base import AnalysisContext, MetricResult, unavailable

INVASIVES_ID = "community-dynamics-invasive-non-native-species-presence"
BARRIERS_ID = (
    "watershed-connectivity-fish-passage-and-barrier-effects-"
    "longitudinal-connectivity")
HABITAT_ID = "habitat-provision-in-stream-habitat-complexity-and-cover"
BIOINTEGRITY_ID = "population-support-biological-integrity-ibi-community-condition"


def invasives(ctx: AnalysisContext) -> MetricResult:
    """Established nonindigenous aquatic taxa recorded by USGS NAS."""
    taxa = nas.established_taxa(huc12=ctx.huc12, huc8=ctx.huc8)
    scope = "HUC12" if ctx.huc12 else ("HUC8" if ctx.huc8 else "")
    confidence = "M" if scope == "HUC12" else "L"
    if taxa is None:
        ev = screening_methods.evaluate(
            INVASIVES_ID, {"taxaCount": None}, confidence=confidence,
            source_tier="connected-nearby", evidence_family="nas_records")
        return unavailable(INVASIVES_ID, "USGS NAS query unavailable", confidence,
                           scoring=ev.trace)
    count = len(taxa)
    ev = screening_methods.evaluate(
        INVASIVES_ID, {"taxaCount": count},
        input_meta={"taxaCount": {"source": f"USGS NAS ({scope})"}},
        confidence=confidence, source_tier="connected-nearby",
        evidence_family="nas_records")
    if count == 0:
        value_text = f"no established taxa recorded ({scope})"
    else:
        sample = ", ".join(taxa[:4]) + ("…" if count > 4 else "")
        value_text = f"{count} established non-native taxa recorded ({scope})"
        if sample:
            value_text += f": {sample}"
    note = "Record-based screening result; zero is not confirmed absence."
    if scope == "HUC8":
        note += " HUC8 fallback is less spatially specific."
    return MetricResult(
        INVASIVES_ID, value=count, value_text=value_text,
        rating=ev.rating, confidence=confidence, source=f"USGS NAS ({scope})",
        note=note, scoring=ev.trace)


def barriers(ctx: AnalysisContext) -> MetricResult:
    """NID dam proximity within a true one-mile geodesic search radius."""
    dams = nid_barriers.barriers_near(ctx.lat, ctx.lon, miles=1.0)
    if dams is None:
        ev = screening_methods.evaluate(
            BARRIERS_ID, {"damCount": None}, confidence="M",
            source_tier="connected-nearby", evidence_family="nid_proximity")
        return unavailable(BARRIERS_ID, "USACE NID query unavailable", "M",
                           scoring=ev.trace)
    count = len(dams)
    ev = screening_methods.evaluate(
        BARRIERS_ID, {"damCount": count},
        input_meta={"damCount": {"source": "USACE NID geodesic proximity query",
                                  "details": dams}},
        confidence="M", source_tier="connected-nearby",
        evidence_family="nid_proximity")
    value_text = (f"no mapped NID dams within 1 mile" if count == 0
                  else f"{count} mapped NID dam(s) within 1 mile (potential barrier)")
    return MetricResult(
        BARRIERS_ID, value=count, value_text=value_text,
        rating=ev.rating, confidence="M", source="USACE NID",
        note=("Proximity only. A mapped dam count does not establish passability or severity. "
              "Two or more dams within a mile rate Poor as a screening flag to verify."),
        scoring=ev.trace)


def habitat_complexity(ctx: AnalysisContext) -> MetricResult:
    """Woody riparian corridor screen of habitat support.

    Corridor woody cover alone is rated (RBP-derived 50/70 bands). Sinuosity
    rides along as a context-only input in the scoring trace."""
    woody = base.riparian_woody_pct(ctx)
    sinuosity = ctx.sinuosity
    ev = screening_methods.evaluate(
        HABITAT_ID,
        {"woodyRiparian": woody, "sinuosity": sinuosity},
        input_meta={
            "woodyRiparian": {"source": watershed.input_source(ctx, "habitat.woodyRiparian")},
            "sinuosity": {"source": "selected reach geometry"},
        },
        confidence="L")
    if ev.rating is None:
        return unavailable(
            HABITAT_ID, watershed.guidance(ctx, "woody riparian cover is required"),
            "L", scoring=ev.trace)
    value = float(ev.combined_value)
    sin_txt = ("" if sinuosity is None
               else f" (sinuosity {float(sinuosity):.2f} shown as context)")
    return MetricResult(
        HABITAT_ID, value=round(value, 1),
        value_text=f"woody riparian cover {value:.1f}%{sin_txt}",
        rating=ev.rating, confidence="L",
        source=watershed.result_source(ctx, "habitat"),
        note=("Corridor-cover proxy for habitat support, not a field habitat "
              "inventory. Grass-dominated natural channels can provide "
              "habitat this proxy does not credit."),
        scoring=ev.trace)


def biological_integrity(ctx: AnalysisContext) -> MetricResult:
    """Measured NRSA class, predicted BMMI, then published ICI/IWI products."""
    nrsa = base.nrsa_evidence(ctx)
    benthic = (nrsa or {}).get("benthicClass")
    fish = (nrsa or {}).get("fishClass")
    classes = [value for value in (benthic, fish) if value in {"Good", "Fair", "Poor"}]
    if classes:
        rank = {"Poor": 0, "Fair": 1, "Good": 2}
        governing = min(classes, key=rank.get)
        connected = nrsa.get("matchType") == "connected_nearby"
        source_tier = "connected-nearby" if connected else "observed"
        confidence = nrsa.get("confidence") or ("M/L" if connected else "M")
        source = (f"EPA NRSA 2018-19 site {nrsa.get('siteId')}"
                  + (f" ({float(nrsa.get('distanceMi') or 0):.2f} mi connected)"
                     if connected else " (exact COMID)"))
        ev = screening_methods.evaluate(
            BIOINTEGRITY_ID,
            {"conditionClass": governing, "benthicClass": benthic,
             "fishClass": fish},
            input_meta={
                "conditionClass": {"source": source, "details": nrsa},
                "benthicClass": {"source": "NRSA benthic MMI"},
                "fishClass": {"source": "NRSA fish MMI"},
            },
            confidence=confidence, source_tier=source_tier,
            evidence_family="nrsa_field", used_fallback=False)
        if connected or len(classes) == 1:
            ev.trace["completeness"] = "partial"
        components = []
        if benthic:
            components.append(
                f"benthic {benthic} (MMI {float(nrsa['benthicMmi']):.2f})"
                if nrsa.get("benthicMmi") is not None else f"benthic {benthic}")
        if fish:
            components.append(
                f"fish {fish} (MMI {float(nrsa['fishMmi']):.2f})"
                if nrsa.get("fishMmi") is not None else f"fish {fish}")
        note = f"Measured class from {nrsa.get('date')}. The worse available community governs."
        if nrsa.get("warning"):
            note += f" {nrsa['warning']}"
        return MetricResult(
            BIOINTEGRITY_ID, value={"benthic": benthic, "fish": fish},
            value_text=", ".join(components), rating=ev.rating,
            confidence=confidence, source=source, note=note, scoring=ev.trace)

    streamcat = base.sc(ctx)
    # StreamCat has returned this modeled probability under several column spellings, with
    # and without an AOI suffix. Miss it and the metric silently drops to the weaker
    # ICI/IWI tier, so accept every documented form.
    prg = next((streamcat[key] for key in
                ("prg_bmmi", "prgbmmi", "prg_bmmiws", "prgbmmiws",
                 "prg_bmmicat", "prgbmmicat")
                if streamcat.get(key) is not None), None)
    if prg is not None:
        ev = screening_methods.evaluate(
            BIOINTEGRITY_ID, {"prGBmmi": prg},
            input_meta={"prGBmmi": {"source": "EPA StreamCat prG_BMMI"}},
            confidence="M/L", variant_key="streamcat-prg-bmmi",
            source_tier="published-model",
            evidence_family="streamcat_biological_model", used_fallback=True)
        if ev.rating:
            return MetricResult(
                BIOINTEGRITY_ID, value=float(prg),
                value_text=f"predicted Good-BMMI probability {float(prg):.2f}",
                rating=ev.rating, confidence="M/L", source="EPA StreamCat prG_BMMI",
                note=("Published model probability, not a measured benthic MMI or "
                      "multi-community IBI; 0.33/0.67 are EASI integration tiers."),
                scoring=ev.trace)

    products = base.integrity_products(ctx)
    values = products or {}
    ev = screening_methods.evaluate(
        BIOINTEGRITY_ID, values,
        input_meta={key: {"source": f"EPA StreamCat {key}"} for key in values},
        confidence="L", variant_key="streamcat-integrity-products",
        source_tier="published-model", evidence_family="iwi_landscape",
        used_fallback=True)
    if ev.rating is None:
        return unavailable(
            BIOINTEGRITY_ID,
            base.comid_evidence_note(
                ctx, "measured condition, prG_BMMI, or all twelve ICI/IWI components "
                     "are required"),
            "L", scoring=ev.trace)
    product_values = ev.trace.get("context", {}).get("products") or {}
    return MetricResult(
        BIOINTEGRITY_ID, value=float(ev.combined_value),
        value_text=(f"landscape integrity fallback {float(ev.combined_value):.3f} "
                    f"(ICI {float(product_values.get('ICI')):.3f}, "
                    f"IWI {float(product_values.get('IWI')):.3f})"),
        rating=ev.rating, confidence="L",
        source="EPA StreamCat published ICI/IWI component products",
        note=("Predicted landscape condition, not a measured IBI. Multiplication reuses "
              "landscape evidence scored elsewhere; 0.40/0.70 are EASI integration tiers."),
        scoring=ev.trace)
