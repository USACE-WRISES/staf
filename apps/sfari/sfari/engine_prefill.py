"""The STAF site engine bridge for SFARI.

Everything SFARI asks of the vendored engine goes through here: availability,
one ``compute_site`` per site with all seven families (base flow, dams, land
cover with the NLCD 2001 impervious baseline for the land-use change, roads,
runoff, soils, and since 2026-09-07 the reach cross-sections: the
entrenchment and bank-height ratios as reach medians of nine 3DEP sections,
beside SFARI's own Manning tool), the flattened metric values the evidence
adapters read, the labels, and a geometry-stripped record for the session
file. The adapters in ``evidence.py`` map the values onto SFARI's metrics;
the engine answers every watershed metric first, and the StreamCat lookup
engine stands in by COMID, labeled, when the engine has no value for it.

Never raises; an unavailable engine means the watershed evidence falls to the
labeled StreamCat value where the reach has one, else unavailable.
"""
from __future__ import annotations

import importlib.util
from functools import lru_cache
from typing import Any, Callable, Optional

_VENDOR_ROOT = "sfari._vendor.site_engine"
_GEO_REQUIREMENTS = ("requests", "shapely", "geopandas")

# The engine has no page of its own; the STAF site documents both engines.
ENGINE_URL = "https://usace-wrises.github.io/staf/computation-engines/"
SFARI_FAMILIES = ["baseflow", "dams", "landcover", "roads", "runoff", "soils", "xsection"]


@lru_cache(maxsize=1)
def missing_engine_requirements() -> tuple[str, ...]:
    missing = []
    for mod in (_VENDOR_ROOT,) + _GEO_REQUIREMENTS:
        try:
            if importlib.util.find_spec(mod) is None:
                missing.append(mod)
        except (ImportError, ValueError):
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


def engine_label(version: Optional[str] = None) -> str:
    """``STAF site engine v0.2.0`` from the vendored vocabulary when present."""
    try:
        from sfari._vendor.site_engine import naming
        return naming.engine_label(version)
    except Exception:  # noqa: BLE001 - vocabulary fallback
        return f"STAF site engine v{version or engine_version() or 'unknown'}"


def _positive(value: Any) -> bool:
    """True for a finite number above zero (a typed reach length)."""
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def run_engine(lat: float, lon: float, *, reach_length_ft: Optional[float] = None,
               families: Optional[list[str]] = None,
               include_geometry: bool = True,
               progress: Optional[Callable[[dict], Any]] = None) -> dict:
    """One ``compute_site`` at the point with the interactive budget and the
    assessment reach length the assessor typed (the engine's 1,000 ft when
    none; until 2026-09-07 the typed length never reached the engine).

    Returns the engine record (``status`` ok | refused | failed) or a small
    failed record when the engine is not available here. Never raises.
    """
    if not site_engine_available():
        return {"status": "unavailable",
                "reason": "the STAF site engine is not available in this deployment",
                "engineVersion": None, "watershed": None, "reach": None, "metrics": {}}
    try:
        from sfari._vendor.site_engine.engine import compute_site
        from sfari._vendor.site_engine.provenance import INTERACTIVE_CONFIG

        cfg = {**INTERACTIVE_CONFIG, "includeGeometry": bool(include_geometry),
               "metricFamilies": list(families or SFARI_FAMILIES),
               # NLCD 2001 impervious beside 2021: the land-use change baseline
               # (off in the engine's default config; nobody else needs it)
               "landcoverBaseline": True}
        if _positive(reach_length_ft):
            cfg["reachLengthFt"] = float(reach_length_ft)
        return compute_site(float(lat), float(lon), cfg, progress=progress)
    except Exception as exc:  # noqa: BLE001 - the bridge never raises
        return {"status": "failed", "reason": f"engine error: {exc}",
                "engineVersion": engine_version(), "watershed": None,
                "reach": None, "metrics": {}}


def engine_metrics(record: Optional[dict]) -> dict[str, Any]:
    """``{metricKey: value}`` for an ok record, else ``{}``."""
    if not record or record.get("status") != "ok":
        return {}
    return {k: (v or {}).get("value") for k, v in (record.get("metrics") or {}).items()}


def engine_source(record: Optional[dict]) -> str:
    return f"{engine_label((record or {}).get('engineVersion'))} (HR reach watershed)"


def engine_note(record: Optional[dict]) -> str:
    ws = (record or {}).get("watershed") or {}
    area = ws.get("areaSqkm")
    agreement = ws.get("areaAgreement")
    parts = ["Watershed of the clicked NHD reach, HR catchments aggregated"]
    if area is not None:
        parts.append(f"{area} km2")
    if agreement is not None:
        parts.append(f"area agreement {agreement}")
    return ", ".join(parts) + ". NLCD 2021."


def strip_geometry(record: Optional[dict]) -> Optional[dict]:
    """The record without its polygon and reach geometry (session storage)."""
    if not record:
        return record
    out = dict(record)
    if out.get("watershed"):
        out["watershed"] = {**out["watershed"], "polygon": None}
    if out.get("reach"):
        out["reach"] = {**out["reach"], "geometry": None}
    return out
