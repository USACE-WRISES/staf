"""Stable, UI-free entry points for the EASI batch engine.

``capabilities()`` describes the engine (version, metrics, source options, bands).
``run_site`` assesses one point; ``run_batch`` assesses a whole request. Both wrap
the existing ``easi.pipeline`` (``delineate_only`` + ``assess_only``) so the
single-site scoring path is unchanged; ``easi.pipeline.run_analysis`` also stays
intact for existing scripts/tests.

The async functions are the core (call them from Shiny's event loop). ``*_sync``
wrappers drive them via ``asyncio.run`` for plain scripts and the vendored
StreamCurves screening step.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional

from .. import config, pipeline, scoring
from ..metrics import registry
from . import ENGINE_API_VERSION, contracts, runtime
from .contracts import (BatchConfig, BatchRequest, Completeness,
                        DelineationSummary, Issue, MetricRecord, SiteRequest,
                        SiteResult)

EventCb = Optional[Callable[[str, str, dict], None]]

MAX_SITES = 150


def capabilities() -> dict:
    """Describe the engine for a UI or a vendoring consumer (JSON-serializable)."""
    from . import qualify
    return {
        "engine_api_version": ENGINE_API_VERSION,
        "contracts_schema_version": contracts.CONTRACTS_SCHEMA_VERSION,
        "metric_ids": list(config.metrics_by_id().keys()),
        "registered_metric_ids": list(registry.REGISTRY.keys()),
        "source_options": {mid: [list(o) for o in opts]
                           for mid, opts in config.SOURCE_OPTIONS.items()},
        "outcomes": list(config.OUTCOMES),
        "index_bands": [list(b) for b in config.INDEX_BANDS],
        "function_score_bands": [list(b) for b in config.FUNCTION_SCORE_BANDS],
        "defaults": {"reach_length_ft": pipeline.DEFAULT_REACH_FT,
                     "snap_tolerance_ft": 150.0},
        "criteria_fields": qualify.CRITERIA_FIELDS,
        "criteria_presets": list(qualify.PRESETS.keys()),
        "max_sites": MAX_SITES,
    }


def _emit(cb: EventCb, stage: str, site_id: str, **info) -> None:
    if cb is not None:
        try:
            cb(stage, site_id, info)
        except Exception:  # noqa: BLE001 - a UI callback must never break a run
            pass


def _availability(status: str) -> str:
    return {"ok": "available", "override": "available", "unavailable": "unavailable",
            "excluded": "excluded", "pending": "pending"}.get(status, status)


def _metric_records(rows: list[dict], source_choices: dict) -> list[MetricRecord]:
    out: list[MetricRecord] = []
    for r in rows:
        fscore = r.get("functionScore")
        band = (scoring.function_score_band_label(fscore)
                if fscore is not None else "")
        status = r.get("status", "")
        avail = _availability(status)
        missing = r.get("note", "") if avail in ("unavailable", "pending") else ""
        out.append(MetricRecord(
            metric_id=r["metricId"], name=r.get("name", ""),
            discipline=r.get("discipline", ""), function_id=r.get("functionId", ""),
            function_name=r.get("functionName", ""), scale=r.get("scale"),
            confidence=r.get("confidence", "L"),
            generated_rating=r.get("generatedRating"),
            final_rating=r.get("rating"), index=r.get("index"),
            function_score=fscore, band=band, value_text=r.get("valueText", ""),
            source=r.get("source", ""),
            source_mode=(source_choices or {}).get(r["metricId"], ""),
            status=status, availability=avail, missing_reason=missing,
            overrideable=bool(r.get("overrideable"))))
    return out


def _completeness(records: list[MetricRecord]) -> Completeness:
    c = Completeness(total=len(records))
    for m in records:
        if m.status == "override":
            c.overridden += 1
        elif m.status == "excluded":
            c.excluded += 1
        elif m.status in ("unavailable", "pending"):
            c.unavailable += 1
        elif m.status == "ok" and (m.source or "").lower().startswith("default"):
            c.defaulted += 1
        elif m.status == "ok":
            c.computed += 1
    return c


def _build_site_result(site: SiteRequest, delin: dict, report: dict) -> SiteResult:
    d = delin.get("delineation", {})
    records = _metric_records(report.get("metricRows", []), site.source_choices)
    completeness = _completeness(records)
    # raw (unrounded) ECI + sub-indices for qualification, from the same rollup the
    # report used (function scores pass through rollup unchanged).
    roll = scoring.rollup(report.get("functionScores", {}))
    state = "partial" if completeness.unavailable > 0 else "succeeded"
    return SiteResult(
        site_id=site.site_id, state=state,
        input={"lat": site.lat, "lon": site.lon, "comid": site.comid,
               "reach_length_ft": site.reach_length_ft, "metadata": site.metadata},
        delineation=DelineationSummary.from_dict(d),
        metrics=records,
        raw_eci=roll.ecosystem_condition_index,
        raw_sub_indices=dict(roll.sub_indices),
        eci=report.get("ecosystemConditionIndex"),
        sub_indices=dict(report.get("subIndices") or {}),
        function_scores=dict(report.get("functionScores") or {}),
        completeness=completeness,
        issues=[Issue(code="metric_unavailable", severity="info", stage="metrics",
                      metric_id=m.metric_id, site_id=site.site_id,
                      message=m.missing_reason or "no data")
                for m in records if m.availability == "unavailable"])


def _failed_result(site: SiteRequest, delin: dict) -> SiteResult:
    return SiteResult(
        site_id=site.site_id, state="failed",
        input={"lat": site.lat, "lon": site.lon, "comid": site.comid},
        issues=[Issue(code="delineation_failed", severity="error",
                      stage="delineation", site_id=site.site_id, retryable=True,
                      message=delin.get("message", "delineation failed"))])


async def run_site(site: SiteRequest, *, metric_ids: Optional[list[str]] = None,
                   on_event: EventCb = None) -> SiteResult:
    """Assess a single site end-to-end (delineate + assess) -> ``SiteResult``."""
    runtime.ensure_cache()
    _emit(on_event, "delineation", site.site_id)
    delin = await pipeline.delineate_only(
        site.lat, site.lon, site.reach_length_ft, comid=site.comid)
    if delin.get("status") != "ok":
        return _failed_result(site, delin)
    ctx_inputs = delin.pop("ctx_inputs")
    _emit(on_event, "metrics", site.site_id)
    a = await pipeline.assess_only(ctx_inputs, metric_ids=metric_ids,
                                   sources=site.source_choices,
                                   overrides=site.overrides)
    report = a["report"]
    delin.setdefault("delineation", {})["huc12"] = a.get("huc12")
    result = _build_site_result(site, delin, report)
    # Full single-site result (delineation + geometry + report) kept privately for
    # per-site artifact generation; excluded from the compact serialization.
    result.metadata["_artifacts"] = {
        "input": {"lat": site.lat, "lon": site.lon, "comid": site.comid,
                  "reach_length_ft": site.reach_length_ft},
        "delineation": delin.get("delineation", {}),
        "watershed_geojson": delin.get("watershed_geojson"),
        "reach_geojson": delin.get("reach_geojson"),
        "report": report,
    }
    _emit(on_event, "site_done", site.site_id, state=result.state)
    return result


def assign_ids(sites: list[SiteRequest]) -> tuple[list[SiteRequest], dict[str, str]]:
    """Retain unique supplied IDs; generate SITE-000N for blanks; reject dup IDs.

    Returns the id-normalized sites and a ``{original_index: assigned_id}`` map of
    the blanks that were auto-assigned.
    """
    seen: set[str] = set()
    generated: dict[str, str] = {}
    out: list[SiteRequest] = []
    n = 0
    for i, s in enumerate(sites):
        sid = (s.site_id or "").strip()
        if not sid:
            n += 1
            new = f"SITE-{n:04d}"
            while new in seen:
                n += 1
                new = f"SITE-{n:04d}"
            s = SiteRequest.from_dict({**s.to_dict(), "site_id": new})
            generated[str(i)] = new
            sid = new
        elif sid in seen:
            raise ValueError(f"duplicate site id: {sid!r}")
        seen.add(sid)
        out.append(s)
    return out, generated


async def run_batch(request: BatchRequest, *, on_event: EventCb = None,
                    cancel=None) -> "contracts.BatchResult":
    """Assess a whole batch. E2 runs sites sequentially; the bounded concurrent
    scheduler (with retry + cancellation) is layered in by ``batch.runner``."""
    from .runner import run_batch as _scheduled
    return await _scheduled(request, on_event=on_event, cancel=cancel)


# --- synchronous wrappers for scripts / vendored StreamCurves --------------- #
def run_site_sync(site: SiteRequest, **kw) -> SiteResult:
    return asyncio.run(run_site(site, **kw))


def run_batch_sync(request: BatchRequest, **kw) -> "contracts.BatchResult":
    return asyncio.run(run_batch(request, **kw))
