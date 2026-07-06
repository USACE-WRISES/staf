"""Static configuration and data loaders for EASI.

Holds (a) constants that mirror the STAF screening methodology (rating index
midpoints, outcome weights, color bands) and (b) the per-metric *automation
registry* describing how each EASI metric is computed from national data,
its data-confidence, and whether it is overrideable. STAF-derived facts
(criteria, thresholds, function mapping) live in ``data/easi-metrics.json``;
this module holds the app's design metadata and loaders.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# --- STAF scoring constants (must match docs/assets/js/screening-assessment.js) ---
# rating -> index (0-1): midpoint of each bin's recommended range
RATING_INDEX: dict[str, float] = {"Good": 0.85, "Fair": 0.545, "Poor": 0.195}
RATINGS = ("Good", "Fair", "Poor")

# CWA outcome contribution weights: Direct, indirect, none
WEIGHTS: dict[str, float] = {"D": 1.0, "i": 0.1, "-": 0.0}
OUTCOMES = ("physical", "chemical", "biological")

FUNCTION_SCORE_MAX = 15

# Color bands (hex mirrors STAF). Index bands (0-1) and function-score bands (0-15).
INDEX_BANDS = [(0.39, "#f5b5b5"), (0.69, "#f5e7a6"), (1.01, "#c8d9f2")]
INDEX_BAND_LABELS = ("Non-Functioning", "Functioning-at-Risk", "Functioning")  # condition category per band (aligned with INDEX_BANDS)
FUNCTION_SCORE_BANDS = [(5, "#f5b5b5"), (10, "#f5e7a6"), (FUNCTION_SCORE_MAX, "#c8d9f2")]
# Short F/AR/NF condition codes per function-score band (aligned with FUNCTION_SCORE_BANDS);
# used for the optional badge next to a function score (STAF "Show F/AR/NF labels").
FUNCTION_SCORE_BAND_SHORT = ("NF", "AR", "F")

# Data-confidence levels for the report badges
CONFIDENCE = ("H", "M", "M/L", "L")


@functools.lru_cache(maxsize=None)
def _load(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def cwa_mapping() -> dict[str, dict[str, str]]:
    """function id -> {physical, chemical, biological} contribution codes."""
    rows = _load("cwa-mapping.json")
    return {r["id"]: {k: r[k] for k in OUTCOMES} for r in rows}


def functions() -> list[dict]:
    return _load("functions.json")


def easi_metrics() -> dict:
    return _load("easi-metrics.json")


def metrics_by_id() -> dict[str, dict]:
    return {m["metricId"]: m for m in easi_metrics()["metrics"]}


# --- Per-metric automation registry (verified plan; keyed by metricId) ---
# scale: 'W' watershed, 'R' reach, 'W/R' both
# confidence: H / M / M/L / L   |   proxy/overrideable: bool
# datasource: short adapter key string for the metric's source pipeline
METRIC_REGISTRY: dict[str, dict] = {
    "catchment-hydrology-impervious-surface-cover": {
        "scale": "W", "confidence": "H", "proxy": False, "overrideable": False,
        "datasource": "streamcat:pctimp2019"},
    "surface-water-storage-percent-wetlands-in-watershed": {
        "scale": "W", "confidence": "H", "proxy": False, "overrideable": False,
        "datasource": "streamcat:pctwdwet2019+pcthbwet2019|nwi"},
    "reach-inflow-concentrated-runoff-stormwater-inputs": {
        "scale": "R", "confidence": "L", "proxy": True, "overrideable": True,
        "datasource": "proxy:tiger_crossings+streamcat:rddens"},
    "streamflow-regime-flow-alteration-regulation-water-use": {
        "scale": "W/R", "confidence": "M", "proxy": True, "overrideable": True,
        "datasource": "nwis_waterdata|streamcat:damnrmstor,damdens"},
    "low-flow-and-baseflow-dynamics-low-flow-wetted-connectivity": {
        "scale": "R", "confidence": "L", "proxy": True, "overrideable": True,
        "datasource": "proxy:nhd_fcode+nwis_zeroflow"},
    "high-flow-dynamics-floodplain-engagement-frequency-bankfull-recurrence": {
        "scale": "R", "confidence": "M", "proxy": True, "overrideable": True,
        "datasource": "bankfull_curves+streamstats_nss+threedep"},
    "floodplain-connectivity-floodplain-access-entrenchment": {
        "scale": "R", "confidence": "M", "proxy": True, "overrideable": True,
        "datasource": "threedep:entrenchment"},
    "hyporheic-connectivity-hyporheic-exchange-indicators": {
        "scale": "R", "confidence": "L", "proxy": True, "overrideable": True,
        "datasource": "proxy:threedep_slope+sinuosity+sda_ksat"},
    "channel-evolution-channel-evolution-stage-and-trends": {
        "scale": "R", "confidence": "L", "proxy": True, "overrideable": True,
        "datasource": "proxy:threedep_incision+nhd_channelization"},
    "channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition": {
        "scale": "R", "confidence": "L", "proxy": True, "overrideable": True,
        "datasource": "proxy:streampower+sda_kfact+riparian_deficit"},
    "sediment-continuity-sediment-supply-potential-watershed-banks": {
        "scale": "W", "confidence": "M", "proxy": True, "overrideable": True,
        "datasource": "streamcat:kffact,pctcrop2019,pcthay2019,damnrmstor,rddens"},
    "bed-composition-and-large-wood-substrate-condition-grain-size-embeddedness-fines-consolidation": {
        "scale": "R", "confidence": "L", "proxy": True, "overrideable": True,
        "datasource": "proxy:threedep_gradient+streamcat_erodibility"},
    "light-and-thermal-regime-stream-temperature": {
        "scale": "R", "confidence": "M", "proxy": True, "overrideable": True,
        "datasource": "wqp_temperature|streamcat:tmean8110+riparian (climate surrogate)"},
    "carbon-processing-detrital-processing-cpom-retention-shredders": {
        "scale": "R", "confidence": "M", "proxy": True, "overrideable": True,
        "datasource": "streamcat:rp100_forest"},
    "nutrient-cycling-nitrogen-and-phosphorus-concentrations": {
        "scale": "R/W", "confidence": "M/L", "proxy": True, "overrideable": True,
        "datasource": "wqp_tn_tp|sparrow_lookup vs ecoregion_criteria"},
    "water-and-soil-quality-regulatory-impairment-status-305b-303d-tmdl": {
        "scale": "R", "confidence": "H", "proxy": False, "overrideable": True,
        "datasource": "attains|nearby vs landscape surrogate"},
    "habitat-provision-in-stream-habitat-complexity-and-cover": {
        "scale": "R", "confidence": "L", "proxy": True, "overrideable": True,
        "datasource": "proxy:sinuosity+gradient_var+rp100+stream_order"},
    "population-support-biological-integrity-ibi-community-condition": {
        "scale": "R", "confidence": "M", "proxy": True, "overrideable": True,
        "datasource": "streamcat:predicted_bio|nrsa_nearest"},
    "community-dynamics-invasive-non-native-species-presence": {
        "scale": "W", "confidence": "M", "proxy": True, "overrideable": True,
        "datasource": "nas_by_huc"},
    "watershed-connectivity-fish-passage-and-barrier-effects-longitudinal-connectivity": {
        "scale": "R", "confidence": "H", "proxy": False, "overrideable": False,
        "datasource": "nid+nabd+fws_sarp"},
}


# User-selectable data-source options for the metrics with >1 *implemented* source.
# {metricId: [(value, label), ...]} — adapters read ctx.extras["source_choices"][mid].
# Metrics absent here use their single (auto) source.
SOURCE_OPTIONS: dict[str, list[tuple[str, str]]] = {
    "surface-water-storage-percent-wetlands-in-watershed":
        [("streamcat", "EPA StreamCat wetlands"), ("nlcd", "NLCD 2021 wetlands")],
    "light-and-thermal-regime-stream-temperature":
        [("wqp", "Observed (WQP)"), ("surrogate", "Climate surrogate (PRISM + shade)")],
    "water-and-soil-quality-regulatory-impairment-status-305b-303d-tmdl":
        [("attains", "EPA ATTAINS 303(d)/305(b)"), ("surrogate", "Modeled risk (landscape)")],
}

# Documented-but-not-yet-built alternative sources, shown as a note in the configure
# UI so the user can see the planned indicator without it being a hollow choice.
PLANNED_ALT_SOURCE: dict[str, str] = {
    "streamflow-regime-flow-alteration-regulation-water-use": "NWIS gaged flow comparison",
    "nutrient-cycling-nitrogen-and-phosphorus-concentrations":
        "Ecoregion reference criteria / SPARROW model",
    "population-support-biological-integrity-ibi-community-condition":
        "NRSA / StreamCat predicted biological condition",
}

# Concise, plain-language definition of each metric (what it measures), shown in the
# report's ⓘ tooltip above the calculation method and scoring criteria. Curated here so
# the STAF source data stays untouched; keep one short sentence per metric.
METRIC_DEFINITIONS: dict[str, str] = {
    "catchment-hydrology-impervious-surface-cover":
        "Share of the contributing watershed covered by impervious surfaces (roads, roofs, "
        "parking), which speeds runoff and can degrade flow and water quality.",
    "surface-water-storage-percent-wetlands-in-watershed":
        "Share of the watershed that is wetland, which stores water, buffers peak flows, and "
        "sustains baseflow.",
    "reach-inflow-concentrated-runoff-stormwater-inputs":
        "Degree to which concentrated runoff (road–stream crossings, stormwater outfalls) "
        "delivers flashy, untreated flow directly to the reach.",
    "streamflow-regime-flow-alteration-regulation-water-use":
        "How much dams, diversions, and water use alter the natural magnitude and timing of "
        "streamflow.",
    "low-flow-and-baseflow-dynamics-low-flow-wetted-connectivity":
        "Whether the channel stays wetted and longitudinally connected during low-flow periods "
        "(flow permanence).",
    "high-flow-dynamics-floodplain-engagement-frequency-bankfull-recurrence":
        "How often high flows top the bank and engage the floodplain (recurrence interval of "
        "overbank flooding).",
    "floodplain-connectivity-floodplain-access-entrenchment":
        "Whether the channel has lateral access to a floodplain, via the entrenchment ratio "
        "(floodprone width ÷ bankfull width).",
    "hyporheic-connectivity-hyporheic-exchange-indicators":
        "Potential for surface water to exchange with the shallow subsurface (hyporheic zone), "
        "inferred from channel slope and sinuosity.",
    "channel-evolution-channel-evolution-stage-and-trends":
        "Whether the channel is stable or actively incising/widening — its stage in the "
        "channel-evolution sequence.",
    "channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition":
        "Susceptibility to accelerated bank erosion or hardening, from stream power, soil "
        "erodibility, and riparian condition.",
    "sediment-continuity-sediment-supply-potential-watershed-banks":
        "Potential for excess sediment from watershed sources (cropland, roads) and channel "
        "banks relative to natural supply.",
    "bed-composition-and-large-wood-substrate-condition-grain-size-embeddedness-fines-consolidation":
        "Quality of the streambed substrate (grain size, embeddedness, fines) that supports "
        "habitat and spawning.",
    "light-and-thermal-regime-stream-temperature":
        "Whether stream temperature stays within ranges suitable for aquatic life (observed "
        "data, or riparian-shade/climate surrogate).",
    "carbon-processing-detrital-processing-cpom-retention-shredders":
        "Capacity to capture and process coarse organic matter (leaf litter), supported by "
        "riparian forest input.",
    "nutrient-cycling-nitrogen-and-phosphorus-concentrations":
        "Whether nutrient (N and P) concentrations stay near reference levels rather than "
        "driving enrichment.",
    "water-and-soil-quality-regulatory-impairment-status-305b-303d-tmdl":
        "Whether the reach is on a Clean Water Act impaired-waters list (303(d)/305(b)/TMDL) "
        "for water-quality problems.",
    "habitat-provision-in-stream-habitat-complexity-and-cover":
        "Physical habitat diversity and cover (pools, riffles, structure, wood) available to "
        "aquatic organisms.",
    "population-support-biological-integrity-ibi-community-condition":
        "Overall condition of the aquatic biological community relative to reference (a "
        "screening surrogate for a measured IBI).",
    "community-dynamics-invasive-non-native-species-presence":
        "Extent to which invasive or non-native species are present in the watershed and may "
        "displace natives.",
    "watershed-connectivity-fish-passage-and-barrier-effects-longitudinal-connectivity":
        "Whether dams or barriers disrupt upstream–downstream movement of fish and aquatic "
        "organisms.",
}

# Short note on how each metric's value is computed, shown in the report ⓘ tooltip's
# Calculation section (the data Source is shown separately). Most screening metrics bin a
# dataset value directly, so only metrics with an extra computation or a composite/derived
# input are listed here; the rest fall back to the "used directly" default in the app.
METRIC_CALCULATIONS: dict[str, str] = {
    "reach-inflow-concentrated-runoff-stormwater-inputs":
        "From road–stream crossing and stormwater-outfall density along the reach.",
    "streamflow-regime-flow-alteration-regulation-water-use":
        "From upstream dam, diversion, and water-use indicators.",
    "low-flow-and-baseflow-dynamics-low-flow-wetted-connectivity":
        "From modeled flow permanence (low-flow wetted connectivity).",
    "high-flow-dynamics-floodplain-engagement-frequency-bankfull-recurrence":
        "From the bank-height ratio and the modeled bankfull-flow recurrence interval.",
    "floodplain-connectivity-floodplain-access-entrenchment":
        "Entrenchment ratio = flood-prone width ÷ bankfull width.",
    "hyporheic-connectivity-hyporheic-exchange-indicators":
        "Derived from channel slope and sinuosity.",
    "channel-evolution-channel-evolution-stage-and-trends":
        "From the bank-height ratio (low-bank height ÷ bankfull height).",
    "channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition":
        "Composite of stream power, soil erodibility, and riparian condition.",
    "sediment-continuity-sediment-supply-potential-watershed-banks":
        "Composite of watershed- and channel-bank sediment-supply potential.",
    "water-and-soil-quality-regulatory-impairment-status-305b-303d-tmdl":
        "From the reach's Clean Water Act impaired-waters listing (303(d)/305(b)/TMDL).",
    "watershed-connectivity-fish-passage-and-barrier-effects-longitudinal-connectivity":
        "From dam/barrier presence affecting upstream–downstream passage.",
}


def validate_registry() -> list[str]:
    """Return a list of consistency problems between registry and metric data."""
    problems: list[str] = []
    metric_ids = set(metrics_by_id())
    reg_ids = set(METRIC_REGISTRY)
    for mid in metric_ids - reg_ids:
        problems.append(f"metric {mid} missing from METRIC_REGISTRY")
    for mid in reg_ids - metric_ids:
        problems.append(f"registry id {mid} not in easi-metrics.json")
    for mid in reg_ids - set(METRIC_DEFINITIONS):
        problems.append(f"metric {mid} missing from METRIC_DEFINITIONS")
    mapping = cwa_mapping()
    for m in easi_metrics()["metrics"]:
        if m["functionId"] not in mapping:
            problems.append(f"functionId {m['functionId']} not in cwa-mapping")
    return problems
