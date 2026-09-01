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

# The budget a web app can wait for: about five minutes on a typical site
# after the 0.2.0 geometry-free walk. Calibrated by
# ``scripts/engine_runtime_profile.py`` (its output names the numbers); the
# values below are provisional until that profile runs. A refusal past the
# budget is a structured result, never an error.
INTERACTIVE_CONFIG = {**DEFAULT_CONFIG, "maxReaches": 60, "maxHops": 40}


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
