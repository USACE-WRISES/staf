"""The SiteComputation record: shape, defaults, and pinned vintages.

The record is the engine's whole output contract: camelCase, JSON-serializable,
deterministic for fixed inputs and engine version (no timestamps in the body;
stamp them outside if a caller needs them). Per-metric entries carry value,
unit, source, vintage, spatial support, and warnings; sources the engine will
never approximate are listed as permanent exclusions.
"""
from __future__ import annotations

from typing import Any, Optional

from . import ENGINE_ID, ENGINE_VERSION

# Pinned data vintages for Layer B sources. Changing a vintage is an engine
# version bump (consumers record engineVersion in their provenance).
VINTAGES = {
    "nhdplusHr": "USGS NHDPlus HR (hydro.nationalmap.gov, live service)",
    "nlcd": "2021",
    "nlcdBaseline": "2001",
    "tigerRoads": "current TIGERweb service",
    "nid": "current NID service",
}

# Sources that exist only as per-COMID EPA model outputs and can never be
# recomputed here. Documented, never approximated.
PERMANENT_EXCLUSIONS = [
    {"code": "epa-modeled-indices",
     "reason": ("The EPA watershed/catchment integrity components "
                "(hyd, sed, chem, conn, temp, habt) and prG_BMMI exist only "
                "as per-COMID EPA model outputs on the NHDPlus V2 network.")},
]

DEFAULT_CONFIG = {
    "reachLengthFt": 1000.0,
    "snapTolFt": 150.0,
    "maxHops": 200,
    "maxReaches": 5000,
    "includeGeometry": True,
    # None = every registered metric family; else a list of family names
    # (see ``metrics.FAMILIES``). Consumers with their own cross-section path
    # pass the five watershed families and skip ``xsection``.
    "metricFamilies": None,
    # Also compute NLCD 2001 impervious cover (the land-use-change baseline).
    "landcoverBaseline": False,
}

# The budget a web app can wait for: a five-minute envelope. Calibrated on the
# 2026-09-01 runtime profile of the 0.2.1 node walk (scripts/
# engine_runtime_profile.py, 33 completed sites across the acceptance boxes and
# the covered panel): secs = 5.1 + 0.06 * reaches + 0.47 * hops, residual sd
# 3.0 s, hops at most 3.47 * sqrt(reaches); the fitted p90 stays under 300 s up
# to 3,190 reaches and 202 hops, rounded down here. The 1,124 km2 Ohio test
# basin (1,319 reaches, 123 hops) completed in 146 s. Under the 0.2.0 scan walk
# the same envelope bought about eight hops.
INTERACTIVE_CONFIG = {**DEFAULT_CONFIG, "maxReaches": 3000, "maxHops": 200}


def resolve_config(config: Optional[dict]) -> dict:
    out = dict(DEFAULT_CONFIG)
    for k, v in (config or {}).items():
        if k in out and v is not None:
            out[k] = v
    families = out.get("metricFamilies")
    if families is not None:
        out["metricFamilies"] = sorted({str(f) for f in families})
    out["landcoverBaseline"] = bool(out.get("landcoverBaseline"))
    return out


def metric_entry(value: Any, unit: str, source: str, vintage: str,
                 spatial_support: str, warnings: Optional[list[str]] = None
                 ) -> dict:
    """One Layer B metric: ``spatial_support`` is pointWatershed,
    riparianBuffer, or reach."""
    return {"value": value, "unit": unit, "source": source, "vintage": vintage,
            "spatialSupport": spatial_support, "warnings": list(warnings or [])}


def base_record(lat: float, lon: float, config: dict) -> dict:
    return {
        "engineId": ENGINE_ID,
        "engineVersion": ENGINE_VERSION,
        "status": "failed",
        "reason": None,
        "input": {"lat": round(float(lat), 6), "lon": round(float(lon), 6),
                  "config": {k: config[k] for k in sorted(config)}},
        "site": None,
        "watershed": None,
        "reach": None,
        "metrics": {},
        "exclusions": [dict(e) for e in PERMANENT_EXCLUSIONS],
        "warnings": [],
    }
