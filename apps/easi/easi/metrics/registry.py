"""Registry of implemented EASI metric adapters (metricId -> callable).

As adapters land (Phases 2-3) they are added here. Metrics absent from this
registry render as 'pending' in the report and are excluded from the rollup
(graceful degradation), so the app is always runnable end-to-end.
"""
from __future__ import annotations

from . import biology, geomorphology, hydraulics, hydrology, physicochemistry

REGISTRY = {
    # Hydrology
    hydrology.IMPERVIOUS_ID: hydrology.impervious,
    hydrology.WETLANDS_ID: hydrology.wetlands,
    hydrology.FLOW_ALTERATION_ID: hydrology.flow_alteration,
    hydrology.REACH_INFLOW_ID: hydrology.reach_inflow,
    # Hydraulics
    hydraulics.LOW_FLOW_ID: hydraulics.low_flow_connectivity,
    hydraulics.HYPORHEIC_ID: hydraulics.hyporheic,
    hydraulics.ENTRENCHMENT_ID: hydraulics.floodplain_access,
    hydraulics.FLOODPLAIN_ENGAGEMENT_ID: hydraulics.floodplain_engagement,
    # Geomorphology
    geomorphology.SEDIMENT_ID: geomorphology.sediment_supply,
    geomorphology.SUBSTRATE_ID: geomorphology.substrate,
    geomorphology.BANK_EROSION_ID: geomorphology.bank_erosion,
    geomorphology.CHANNEL_EVOL_ID: geomorphology.channel_evolution,
    # Physicochemistry
    physicochemistry.IMPAIRMENT_ID: physicochemistry.impairment,
    physicochemistry.CPOM_ID: physicochemistry.detrital_cpom,
    physicochemistry.NUTRIENTS_ID: physicochemistry.nutrients,
    physicochemistry.TEMPERATURE_ID: physicochemistry.stream_temperature,
    # Biology
    biology.INVASIVES_ID: biology.invasives,
    biology.BARRIERS_ID: biology.barriers,
    biology.HABITAT_ID: biology.habitat_complexity,
    biology.BIOINTEGRITY_ID: biology.biological_integrity,
}

# Metrics that make a live call to an external federal web service (as opposed to
# reading prefetched StreamCat/NLCD/3DEP data). These are the slow tail of a run;
# the assessment surfaces the friendly service name in its live progress label so a
# slow service reads as "waiting on <service>" instead of a frozen counter.
EXTERNAL_SERVICE = {
    physicochemistry.IMPAIRMENT_ID: "EPA ATTAINS",
    physicochemistry.NUTRIENTS_ID: "Water Quality Portal",
    physicochemistry.TEMPERATURE_ID: "Water Quality Portal",
    biology.INVASIVES_ID: "USGS NAS",
    biology.BARRIERS_ID: "USACE NID",
}

# StreamCat base metric names needed by the registered adapters (one batched call
# returns ws / cat / wsrp100 / catrp100 variants for each).
STREAMCAT_NAMES = [
    "pctimp2019", "pctwdwet2019", "pcthbwet2019",
    "pctcrop2019", "pcthay2019",
    "pctmxfst2019", "pctdecid2019", "pctconif2019",
    "kffact", "rddens", "damdens", "damnrmstor",
    "tmean8110",  # PRISM 1981-2010 mean-annual air-temp normal (climate surrogate for stream temp)
]
