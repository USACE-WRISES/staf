"""The STAF site computation engine as a StreamCurves data source.

Two roles, both for engine-sourced builds (``--predictor-source site-engine``):

- **Predictors**: exact-watershed predictor columns (``se_*``) computed at the
  training-site coordinates with the vendored engine, the true point watershed
  on the full-resolution NHD instead of StreamCat's per-COMID V2 summaries.
  They replace their StreamCat analogs in the predictor configuration.
- **Scored landscape metrics** (2026-09-02): the six StreamCat landscape
  columns with an engine analog (:data:`SE_METRIC_ANALOGS`) keep their column
  names, and therefore their bundle metric ids and curated directions, but
  their VALUES at every retained site become the engine's exact-watershed
  values, so the curves are fitted on the same source DEEP will score with.
  Base-flow index and road-stream crossings have no engine analog and stay
  StreamCat. A retained site the engine cannot value keeps NaN, never a
  StreamCat fallback, and the batch refuses a partial engine run.

This is the recalibration-study mechanism: selecting the engine recomputes the
predictors and the scored landscape metrics at the NRSA sites, and the resulting
curve version's provenance (``inputsDigest``, the bundle ``predictorSource``,
the per-metric stamps on the re-sourced curves) records it.

Availability is import-provable (the ``easi_screening.engine_available``
pattern): the vendored package plus the geospatial stack. Engine runs cost
usually under a minute per site (up to about five on a large basin), so both
compile paths label the cost and cache per site. Nothing here raises: a
per-site failure, refusal, or incomplete record is reported by site id with
the engine's own status and reason, is never cached, and is retried on the
next run.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import importlib.util
import json
import time
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

# Scored StreamCat landscape COLUMN -> (engine record key, transform). Under
# --predictor-source site-engine the column keeps its name (the bundle metric
# id spring-<column>, DEEP's adapters, and the curated directions all key on
# it) and its VALUES become the engine's exact-watershed value at each retained
# site. Units match on both sides: percent, km/km2, dams/km2. No analog, and
# therefore StreamCat by design: bfiws, rdcrsws.
SE_METRIC_ANALOGS: dict[str, tuple[str, Callable]] = {
    "pctimp2019ws": ("imperviousPctWatershed", lambda v: v),
    "pctcrop2019ws": ("cropPctWatershed", lambda v: v),
    "pctwdwet2019ws": ("woodyWetlandPctWatershed", lambda v: v),
    "pcthbwet2019ws": ("herbWetlandPctWatershed", lambda v: v),
    "rddensws": ("roadDensity", lambda v: v),
    "damdensws": ("damDensityPerSqkm", lambda v: v),
}

#: The per-site cache layout. Version 2 stores the engine identity, both value
#: groups, and a retryable marker for every failure; version 1 (predictors only,
#: failures cached as empty dicts and later counted as ok) is ignored.
SE_CACHE_SCHEMA = 2


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


def se_metric_columns() -> list[str]:
    """The scored landscape columns an engine-sourced build recomputes, sorted."""
    return sorted(SE_METRIC_ANALOGS)


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


def engine_source_label() -> str:
    """The per-metric ``predictorSource`` stamp of a re-sourced curve,
    ``site-engine v0.2.2`` (the token never changes, the version rides)."""
    return f"site-engine v{engine_identity().get('version') or 'unknown'}"


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


def resource_metric_columns(data, values_by_site: dict):
    """Replace the scored landscape columns with the engine's values.

    For every :data:`SE_METRIC_ANALOGS` column present in ``data``: the value
    at each site becomes the engine's, joined by site_id, NaN where the engine
    produced none (never the StreamCat value the column held, so a gap is
    visible and the batch's honesty gate can refuse it). A column StreamCat did
    not return is not created. Returns ``(data, resourced_columns)`` with the
    columns actually replaced, sorted. Deterministic; never raises.
    """
    if data is None or not len(data) or "site_id" not in data.columns:
        return data, []
    import pandas as pd

    out = data.copy()
    sids = out["site_id"].astype(str)
    resourced: list[str] = []
    for col in se_metric_columns():
        if col not in out.columns:
            continue
        out[col] = pd.to_numeric(
            pd.Series([(values_by_site.get(sid) or {}).get(col) for sid in sids],
                      index=out.index, dtype="object"),
            errors="coerce")
        resourced.append(col)
    return out, resourced


def annotate_resourced_metric_config(metric_config: dict, columns) -> dict:
    """A copy of ``metric_config`` whose re-sourced entries say so.

    Each named column gains ``value_source`` (the engine stamp deep_export
    writes as that curve's per-metric ``predictorSource``) and a plain note
    appended to ``notes`` (which DEEP shows as how to measure). Entries not
    named, and the input, are untouched.
    """
    from . import engine_names

    out = {k: dict(v or {}) for k, v in (metric_config or {}).items()}
    label = engine_source_label()
    ver = engine_identity().get("version") or "unknown"
    note = (f"Values recomputed by the {engine_names.SITE_ENGINE} v{ver} over the "
            "exact watershed (NLCD 2021, TIGERweb roads, NID dams). The StreamCat "
            "column name is kept for the metric id.")
    for col in columns or []:
        entry = out.get(col)
        if entry is None:
            continue
        entry["value_source"] = label
        prev = str(entry.get("notes") or "").strip()
        entry["notes"] = (prev.rstrip(".") + ". " + note) if prev else note
    return out


# --------------------------------------------------------------------------- #
# One site
# --------------------------------------------------------------------------- #
def _engine_config(config: Optional[dict]) -> dict:
    return {"includeGeometry": False, **(config or {})}


def _budget_of(config: Optional[dict]) -> dict:
    """The reach and hop budget the run computes under (the vendored engine's
    defaults, overridden by ``config``), for the honesty report."""
    base: dict = {}
    try:
        from streamcurves._vendor.site_engine import provenance as _prov
        base = dict(getattr(_prov, "DEFAULT_CONFIG", None) or {})
    except Exception:  # noqa: BLE001
        base = {}
    cfg = {**base, **(config or {})}
    return {"maxReaches": cfg.get("maxReaches"), "maxHops": cfg.get("maxHops"),
            "snapTolFt": cfg.get("snapTolFt")}


def se_site_record(lat: float, lon: float, *, config: Optional[dict] = None,
                   compute: Optional[Callable] = None) -> dict:
    """One engine computation, reported honestly. Never raises.

    ``{"status": ok | refused | failed | incomplete, "reason", "values",
    "missing", "warnings", "seconds", "areaSqkm"}``. ``values`` holds every
    predictor code and every scored analog column. ``incomplete`` is an engine
    ``ok`` whose record lacks a scored analog (the curve would carry a silent
    gap); a missing predictor alone stays ``ok`` and is listed in ``missing``.
    ``compute`` stands in for the vendored ``compute_site`` in tests.
    """
    out = {"status": "failed", "reason": None, "values": {}, "missing": [],
           "warnings": [], "seconds": 0.0, "areaSqkm": None}
    t0 = time.monotonic()
    try:
        if compute is None:
            from streamcurves._vendor.site_engine import compute_site as compute
        rec = compute(float(lat), float(lon), _engine_config(config)) or {}
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"engine error: {exc}"
        out["seconds"] = round(time.monotonic() - t0, 1)
        return out
    out["seconds"] = round(time.monotonic() - t0, 1)
    out["warnings"] = [str(w) for w in (rec.get("warnings") or [])]
    status = str(rec.get("status") or "failed")
    if status != "ok":
        out["status"] = status if status in ("refused", "failed") else "failed"
        out["reason"] = str(rec.get("reason") or f"engine status {status}")
        return out
    values: dict = {}
    missing: list[str] = []
    for code, (_label, fn) in SE_PREDICTORS.items():
        v = fn(rec)
        values[code] = v
        if v is None:
            missing.append(code)
    metric_notes: list[str] = []
    for col, (key, fn) in SE_METRIC_ANALOGS.items():
        raw = _m(rec, key)
        v = None if raw is None else fn(raw)
        values[col] = v
        if v is None:
            missing.append(col)
            entry = (rec.get("metrics") or {}).get(key) or {}
            for w in entry.get("warnings") or []:
                metric_notes.append(str(w))
    out["values"] = values
    out["missing"] = sorted(missing)
    out["areaSqkm"] = (rec.get("watershed") or {}).get("areaSqkm")
    lacking = [c for c in out["missing"] if c in SE_METRIC_ANALOGS]
    if lacking:
        out["status"] = "incomplete"
        detail = f" ({', '.join(dict.fromkeys(metric_notes))})" if metric_notes else ""
        out["reason"] = f"engine record lacks {', '.join(lacking)}{detail}"
        return out
    out["status"] = "ok"
    return out


def se_site_metrics(lat: float, lon: float) -> dict:
    """{se_code: value} for one site (the Compile view's contract). Never
    raises; empty when the engine failed or refused."""
    rec = se_site_record(lat, lon)
    if rec.get("status") not in ("ok", "incomplete"):
        return {}
    vals = rec.get("values") or {}
    return {code: vals.get(code) for code in SE_PREDICTORS}


# --------------------------------------------------------------------------- #
# The agent path: every retained site, cached, reported
# --------------------------------------------------------------------------- #
def _cache_key(row: dict) -> str:
    return f"{row.get('site_id')}|{round(float(row['lat']), 6)}|{round(float(row['lon']), 6)}"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _load_cache(cache_path: Optional[str]) -> tuple[dict, Optional[str]]:
    """``(sites, ignored_reason)``. A cache from another schema or engine
    version is ignored (and overwritten), never partially trusted."""
    if not cache_path or not Path(cache_path).exists():
        return {}, None
    try:
        raw = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}, "unreadable cache file"
    if not isinstance(raw, dict) or raw.get("schemaVersion") != SE_CACHE_SCHEMA:
        found = raw.get("schemaVersion") if isinstance(raw, dict) else "none"
        return {}, f"cache schema {found} is not {SE_CACHE_SCHEMA}"
    mine = engine_identity().get("version")
    theirs = (raw.get("engine") or {}).get("version")
    if theirs != mine:
        return {}, f"cache engine version {theirs} is not {mine}"
    sites = raw.get("sites") or {}
    return (sites if isinstance(sites, dict) else {}), None


def _write_cache(cache_path: Optional[str], sites: dict) -> None:
    if not cache_path:
        return
    payload = {"schemaVersion": SE_CACHE_SCHEMA, "engine": engine_identity(),
               "sites": sites}
    try:
        Path(cache_path).write_text(json.dumps(payload, indent=0, sort_keys=True),
                                    encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _note_missing_predictors(report: dict, sid: str, values: dict) -> None:
    for code in SE_PREDICTORS:
        if values.get(code) is None:
            report["missing_predictor_values"].setdefault(code, []).append(sid)


def enrich_site_engine(rows: list[dict], *, enabled: bool = True,
                       cache_path: Optional[str] = None,
                       progress: Optional[Callable] = None,
                       compute: Optional[Callable] = None,
                       config: Optional[dict] = None) -> tuple[dict, dict]:
    """Engine values for the agent path.

    ``rows`` are ``{"site_id", "lat", "lon"}`` dicts (the agent's candidate
    shape). Returns ``(values_by_site, report)`` where ``values_by_site`` maps
    site_id -> {se_code: value, column: value} for every ok site (an empty dict
    for a site that failed, was refused, or came back incomplete) and
    ``report`` is the honesty record that rides ``result["source_reports"]``
    (the ``enrich_streamcat`` shape, extended with the per-site outcome).

    The per-site JSON cache (``SE_CACHE_SCHEMA``) reuses only ok results;
    every failure is stored as a marker with its reason and attempt count and
    is recomputed on the next run. ``progress(i, n, info)`` is called once per
    site with ``{"site_id", "status", "seconds", "cached", "reason"}`` and is
    guarded. ``compute`` and ``config`` are test and budget hooks.
    """
    requested = se_codes() + se_metric_columns()
    report: dict = {
        "source": "site_engine", "requested": requested,
        "requested_metrics": se_metric_columns(),
        "status": "skipped", "n_columns": 0, "reason": None,
        "cache": {"path": cache_path, "n_reused": 0, "ignored": None},
        "engine": engine_identity(), "config": _budget_of(config),
        "n_sites": 0, "n_ok": 0, "n_cached": 0,
        "failed_sites": [], "incomplete_sites": [],
        "missing_predictor_values": {},
        "seconds_total": 0.0, "resourced_metrics": [],
    }
    if not enabled:
        return {}, report
    if compute is None and not site_engine_available():
        report.update(status="failed",
                      reason="STAF site engine unavailable: "
                             + ", ".join(missing_engine_requirements()))
        return {}, report
    sites, ignored = _load_cache(cache_path)
    report["cache"]["ignored"] = ignored
    out: dict[str, dict] = {}
    rows = list(rows or [])
    t0 = time.monotonic()
    for i, row in enumerate(rows, start=1):
        sid = str(row.get("site_id"))
        key = _cache_key(row)
        entry = sites.get(key)
        info = {"site_id": sid, "status": None, "seconds": 0.0, "cached": False,
                "reason": None}
        if isinstance(entry, dict) and entry.get("status") == "ok":
            vals = dict(entry.get("values") or {})
            out[sid] = vals
            report["n_cached"] += 1
            report["n_ok"] += 1
            info.update(status="ok", cached=True)
            _note_missing_predictors(report, sid, vals)
        else:
            rec = se_site_record(row["lat"], row["lon"], config=config, compute=compute)
            status = rec["status"]
            info.update(status=status, seconds=rec["seconds"], reason=rec["reason"])
            if status == "ok":
                out[sid] = dict(rec["values"])
                report["n_ok"] += 1
                sites[key] = {"status": "ok", "values": rec["values"],
                              "seconds": rec["seconds"], "warnings": rec["warnings"],
                              "computedAt": _now()}
                _note_missing_predictors(report, sid, rec["values"])
            else:
                out[sid] = {}
                attempts = (int(entry.get("attempts") or 0) if isinstance(entry, dict) else 0) + 1
                sites[key] = {"status": status, "reason": rec["reason"],
                              "missing": rec["missing"], "attempts": attempts,
                              "lastAttempt": _now()}
                if status == "incomplete":
                    report["incomplete_sites"].append({
                        "site_id": sid,
                        "missing": [c for c in rec["missing"] if c in SE_METRIC_ANALOGS],
                        "reason": rec["reason"]})
                else:
                    report["failed_sites"].append({"site_id": sid, "status": status,
                                                   "reason": rec["reason"]})
            _write_cache(cache_path, sites)
        if progress is not None:
            try:
                progress(i, len(rows), info)
            except Exception:  # noqa: BLE001
                pass
    report["n_sites"] = len(rows)
    report["seconds_total"] = round(time.monotonic() - t0, 1)
    report["cache"]["n_reused"] = report["n_cached"]
    bad = list(report["failed_sites"]) + list(report["incomplete_sites"])
    n_ok = report["n_ok"]
    report["status"] = "ok" if not bad else ("partial" if n_ok else "failed")
    report["n_columns"] = len(requested) if n_ok else 0
    if bad:
        names = ", ".join(f"{b['site_id']} ({b.get('status') or 'incomplete'})"
                          for b in bad[:5])
        more = f" and {len(bad) - 5} more" if len(bad) > 5 else ""
        report["reason"] = f"{len(bad)} site(s) failed: {names}{more}"
    return out, report
