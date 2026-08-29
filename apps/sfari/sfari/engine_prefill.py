"""Field-form prefill from the vendored STAF site computation engine.

When the vendored engine and its geospatial stack are importable, the desktop
pull runs one ``compute_site`` at the assessment point and upgrades a mapped
subset of evidence entries from COMID-catchment proxies to EXACT-watershed
values (true point watershed + 100 m riparian buffer on the full-resolution
NHD). Entries are labeled with the engine version and data vintage, carry
``origin="engine"``, and remain evidence: the assessor scores, the suggested
Likert is a chip, and an edit always wins.

Only metrics whose engine value is a strict analog of the existing evidence
basis are mapped, so the number the assessor sees stays comparable. Never
raises; unavailable simply means no upgrade.
"""
from __future__ import annotations

import importlib.util
from functools import lru_cache
from typing import Optional

from . import likert
from .models import EvidenceResult

_VENDOR_ROOT = "sfari._vendor.site_engine"
_GEO_REQUIREMENTS = ("requests", "shapely", "geopandas")

_SC_URL = "https://www.epa.gov/national-aquatic-resource-surveys/streamcat-dataset"


@lru_cache(maxsize=1)
def missing_engine_requirements() -> tuple[str, ...]:
    missing = []
    for mod in (_VENDOR_ROOT,) + _GEO_REQUIREMENTS:
        if importlib.util.find_spec(mod) is None:
            missing.append(mod)
    return tuple(missing)


def site_engine_available() -> bool:
    """The vendored engine and its geospatial dependencies are importable."""
    return not missing_engine_requirements()


def engine_version() -> Optional[str]:
    if not site_engine_available():
        return None
    from sfari._vendor import site_engine
    return site_engine.ENGINE_VERSION


def _metric(record: dict, key: str):
    entry = (record.get("metrics") or {}).get(key) or {}
    return entry.get("value")


def _entry(mid: str, value, value_text: str, field_text: str,
           note: str, rec: dict) -> EvidenceResult:
    ver = rec.get("engineVersion") or ""
    label = f"STAF site engine v{ver} (exact watershed)"
    return EvidenceResult(
        mid, value=value, value_text=value_text, field_value_text=field_text,
        suggested_likert=likert.suggest(mid, value) if value is not None else None,
        confidence="M", source=label, source_url=_SC_URL,
        note=note, origin="engine", engine_version=ver)


def pull_engine_evidence(ctx_inputs: dict) -> dict[str, dict]:
    """{metricId: evidence-dict} of exact-watershed upgrades, or {}.

    Sync (call from a worker thread): one engine computation per site, then a
    fixed mapping onto the SFARI metrics whose basis it strictly matches.
    """
    if not site_engine_available():
        return {}
    try:
        from sfari._vendor.site_engine import compute_site

        rec = compute_site(ctx_inputs["lat"], ctx_inputs["lon"],
                           {"includeGeometry": False})
        if rec.get("status") != "ok":
            return {}
        out: dict[str, dict] = {}
        ws_note = ("True point watershed on the full-resolution NHD "
                   "(area agreement "
                   f"{(rec.get('watershed') or {}).get('areaAgreement')}). "
                   "NLCD 2021.")

        imp = _metric(rec, "imperviousPctWatershed")
        if imp is not None:
            out["catchment-hydrology-impervious-surface-area"] = _entry(
                "catchment-hydrology-impervious-surface-area", imp,
                f"{imp:.1f}% impervious (exact watershed)",
                f"Impervious {imp:.1f}% (engine)", ws_note, rec).to_dict()

        rd = _metric(rec, "roadDensity")
        if rd is not None:
            out["catchment-hydrology-road-density"] = _entry(
                "catchment-hydrology-road-density", rd,
                f"{rd:.2f} km/km2 road density (exact watershed)",
                f"Roads {rd:.2f} km/km2 (engine)",
                "TIGERweb primary + secondary + local roads clipped to the "
                "watershed. " + ws_note, rec).to_dict()

        dams = _metric(rec, "damCount")
        storage = _metric(rec, "damStorageAcreFt")
        if dams is not None:
            txt = (f"{dams} NID dam(s) in the watershed"
                   + (f", {storage:,.0f} acre-ft storage"
                      if storage is not None else ""))
            out["catchment-hydrology-impoundments"] = _entry(
                "catchment-hydrology-impoundments", dams, txt,
                f"Dams {dams} in watershed (engine)", ws_note, rec).to_dict()

        wet = None
        woody = _metric(rec, "woodyWetlandPctWatershed")
        herb = _metric(rec, "herbWetlandPctWatershed")
        if woody is not None or herb is not None:
            wet = round((woody or 0.0) + (herb or 0.0), 2)
            out["surface-water-storage-wetland-coverage"] = _entry(
                "surface-water-storage-wetland-coverage", wet,
                f"{wet:.1f}% wetland (exact watershed)",
                f"Wetlands {wet:.1f}% (engine)", ws_note, rec).to_dict()

        rip_forest = _metric(rec, "forestPctRiparian")
        if rip_forest is not None:
            out["light-thermal-regime-riparian-canopy-cover"] = _entry(
                "light-thermal-regime-riparian-canopy-cover", rip_forest,
                f"{rip_forest:.1f}% forest in the 100 m riparian buffer",
                f"Riparian forest {rip_forest:.1f}% (engine)",
                "100 m buffer of the upstream network, exact watershed. "
                "NLCD 2021.", rec).to_dict()

        rip_parts = [_metric(rec, k) for k in
                     ("forestPctRiparian", "shrubPctRiparian",
                      "grasslandPctRiparian", "woodyWetlandPctRiparian",
                      "herbWetlandPctRiparian")]
        if any(v is not None for v in rip_parts):
            veg = round(sum(v or 0.0 for v in rip_parts), 1)
            for mid in ("carbon-processing-riparian-corridor-width-and-quality",
                        "nutrient-cycling-vegetated-riparian-corridor-width",
                        "community-dynamics-riparian-communities"):
                out[mid] = _entry(
                    mid, veg,
                    f"{veg:.1f}% natural vegetation in the 100 m riparian "
                    "buffer (forest + shrub + grassland + wetland)",
                    f"Riparian natural veg {veg:.1f}% (engine)",
                    "100 m buffer of the upstream network, exact watershed. "
                    "NLCD 2021.", rec).to_dict()
        return out
    except Exception:  # noqa: BLE001 - prefill must never break the pull
        return {}
