"""The STAF site computation engine as a StreamCurves predictor source.

Computes exact-watershed predictor columns (``se_*``) at training-site
coordinates with the vendored engine: true point-watershed on the
full-resolution NHD instead of StreamCat's per-COMID V2 summaries. This is the
recalibration-study mechanism: selecting the engine recomputes predictors at
the NRSA sites and the resulting curve version's provenance (including
``inputsDigest`` and the bundle ``predictorSource``) records it.

Availability is import-provable (the ``easi_screening.engine_available``
pattern): the vendored package plus the geospatial stack. Engine runs cost
usually under a minute per site (up to about five on a large basin), so both
compile paths label the cost and cache per site. Never raises; per-site failures leave NaN and are counted in the
honesty report.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

_VENDOR_ROOT = "streamcurves._vendor.site_engine"
_GEO_REQUIREMENTS = ("requests", "shapely", "geopandas")
_ACRE_FT_PER_KM2_TO_M3_PER_KM2 = 1233.48184

# se_* predictor column -> (label, extractor(record) -> value|None).
# Analog naming mirrors the StreamCat predictors the curves use today.
SE_PREDICTORS: dict[str, tuple[str, Callable]] = {
    "se_pctimpws": ("Impervious cover, exact watershed (%)",
                    lambda r: _m(r, "imperviousPctWatershed")),
    "se_agws": ("Agricultural cover, exact watershed (%)",
                lambda r: _sum(r, "cropPctWatershed", "hayPasturePctWatershed")),
    "se_rddensws": ("Road density, exact watershed (km/km2)",
                    lambda r: _m(r, "roadDensity")),
    "se_kffactws": ("Soil K-factor, exact watershed",
                    lambda r: _m(r, "soilKFactor")),
    "se_damnrmstor": ("Dam storage, exact watershed (m3/km2)",
                      lambda r: _scale(_m(r, "damStoragePerSqkm"),
                                       _ACRE_FT_PER_KM2_TO_M3_PER_KM2)),
    "se_runoffmm": ("Runoff depth, EROM-derived (mm/yr)",
                    lambda r: _m(r, "runoffDepthMm")),
    "se_wsareasqkm": ("Drainage area, exact watershed (km2)",
                      lambda r: (r.get("watershed") or {}).get("areaSqkm")),
}


def _m(record: dict, key: str):
    return ((record.get("metrics") or {}).get(key) or {}).get("value")


def _sum(record: dict, *keys):
    vals = [_m(record, k) for k in keys]
    if all(v is None for v in vals):
        return None
    return round(sum(v or 0.0 for v in vals), 2)


def _scale(value, factor):
    return None if value is None else round(float(value) * factor, 1)


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


def se_core_metrics() -> dict[str, dict]:
    """Picker catalog input, ``{code: {"label": ...}}`` (the MMW shape).
    Empty when the engine is unavailable so rows never dead-select."""
    if not site_engine_available():
        return {}
    return {code: {"label": label} for code, (label, _fn) in SE_PREDICTORS.items()}


def se_codes() -> list[str]:
    return list(SE_PREDICTORS)


def engine_identity() -> dict:
    """Engine id/version + the vendored manifest digest, for provenance."""
    out = {"id": "site-engine", "version": None, "vendorSha": None}
    try:
        from streamcurves._vendor import site_engine
        out["version"] = site_engine.ENGINE_VERSION
        info = Path(site_engine.__file__).parent / "VENDOR_INFO.json"
        if info.exists():
            out["vendorSha"] = hashlib.sha256(info.read_bytes()).hexdigest()
    except Exception:  # noqa: BLE001
        pass
    return out


def predictor_source_of(predictor_columns) -> str:
    """The declared predictor source, derived from the columns actually used.

    Deterministic and never user-chosen: any ``se_`` predictor column makes
    the build engine-sourced (mixed builds say so); otherwise the default
    StreamCat. This string rides the bundle ``predictorSource``, the manifest,
    and the digest (where the default contributes nothing).
    """
    cols = [str(c) for c in (predictor_columns or [])]
    has_se = any(c.startswith("se_") for c in cols)
    if not has_se:
        return "streamcat"
    ver = engine_identity().get("version") or "unknown"
    has_other = any(not c.startswith("se_") for c in cols)
    base = f"site-engine v{ver}"
    return f"mixed ({base} + streamcat)" if has_other else base


# se code -> the StreamCat predictor COLUMN it replaces in an engine-sourced
# build (ws-suffixed, the compiled column names). Codes with no analog
# (se_wsareasqkm) are engine-only additions.
SE_ANALOG_OF = {
    "se_pctimpws": "pctimp2019ws",
    "se_agws": None,                 # ag is scored from crop+hay; no single analog
    "se_rddensws": "rddensws",
    "se_kffactws": "kffactws",
    "se_damnrmstor": "damnrmstorws",
    "se_runoffmm": "runoffws",
    "se_wsareasqkm": None,
}


def attach_engine_columns(data, values_by_site: dict):
    """Join ``{site_id: {se_code: value}}`` onto the sites frame by site_id."""
    if data is None or not len(data) or "site_id" not in data.columns:
        return data
    sids = data["site_id"].astype(str)
    for code in se_codes():
        data[code] = [
            (values_by_site.get(sid) or {}).get(code) for sid in sids]
    return data


def replace_predictors(predictor_config: dict, columns) -> dict:
    """Swap engine predictor columns in for their StreamCat analogs.

    For every ``se_`` column present in ``columns``: add a predictor entry
    (the ``build_predictor_config`` shape) and drop the StreamCat analog it
    replaces. StreamCat predictors with no engine analog (bfi, precip) stay,
    which is why an engine-sourced build honestly reads as mixed until the
    engine covers them. Deterministic; never raises.
    """
    out = dict(predictor_config or {})
    cols = {str(c) for c in (columns or [])}
    for code, (label, _fn) in SE_PREDICTORS.items():
        if code not in cols:
            continue
        analog = SE_ANALOG_OF.get(code)
        if analog:
            out.pop(analog, None)
        out[code] = {
            "display_name": label,
            "column_name": code,
            "type": "continuous",
            "derived": False,
            "derivation_method": "",
            "source_columns": "",
            "constant": None,
            "expected_range": "",
            "missing_data_rule": "omit",
            "notes": f"{label}; STAF site engine (exact watershed)",
        }
    return out


def se_site_metrics(lat: float, lon: float) -> dict:
    """{se_code: value} for one site (one engine computation). Never raises."""
    try:
        from streamcurves._vendor.site_engine import compute_site
        rec = compute_site(float(lat), float(lon), {"includeGeometry": False})
        if rec.get("status") != "ok":
            return {}
        return {code: fn(rec) for code, (_label, fn) in SE_PREDICTORS.items()}
    except Exception:  # noqa: BLE001
        return {}


def enrich_site_engine(rows: list[dict], *, enabled: bool = True,
                       cache_path: Optional[str] = None,
                       progress: Optional[Callable] = None) -> tuple[dict, dict]:
    """Engine predictors for the agent path.

    ``rows`` are ``{"site_id", "lat", "lon"}`` dicts (the agent's candidate
    shape). Returns ``(values_by_site, report)`` where ``values_by_site`` maps
    site_id -> {se_code: value} and ``report`` is the honesty record that
    rides ``result["source_reports"]`` (the ``enrich_streamcat`` shape).
    A per-site JSON cache makes staged re-runs cheap.
    """
    report = {"source": "site_engine", "requested": se_codes(),
              "status": "skipped", "n_columns": 0, "reason": None,
              "cache": cache_path, "engine": engine_identity()}
    if not enabled:
        return {}, report
    if not site_engine_available():
        report.update(status="failed",
                      reason="STAF site engine unavailable: "
                             + ", ".join(missing_engine_requirements()))
        return {}, report
    cache: dict = {}
    if cache_path and Path(cache_path).exists():
        try:
            cache = json.loads(Path(cache_path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cache = {}
    out: dict[str, dict] = {}
    failures = 0
    for i, row in enumerate(rows or []):
        sid = str(row.get("site_id"))
        key = f"{sid}|{round(float(row['lat']), 6)}|{round(float(row['lon']), 6)}"
        if key in cache:
            out[sid] = cache[key]
        else:
            vals = se_site_metrics(row["lat"], row["lon"])
            if not vals:
                failures += 1
            out[sid] = vals
            cache[key] = vals
            if cache_path:
                try:
                    Path(cache_path).write_text(
                        json.dumps(cache, indent=0, sort_keys=True),
                        encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
        if progress is not None:
            try:
                progress(i + 1, len(rows))
            except Exception:  # noqa: BLE001
                pass
    n_ok = sum(1 for v in out.values() if v)
    report.update(
        status="ok" if failures == 0 else ("partial" if n_ok else "failed"),
        n_columns=len(se_codes()) if n_ok else 0,
        reason=None if failures == 0 else f"{failures} site(s) failed")
    return out, report
