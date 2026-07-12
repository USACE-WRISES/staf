"""Bounded, cancellable batch scheduler with retry classification.

Runs at most two sites concurrently. A site that fails with a retryable issue
(transient timeout / HTTP 429 / HTTP 5xx, classified via the ``diagnostics``
side channel) is retried once with backoff. Cancellation is cooperative: no new
site is scheduled, completed results are preserved, queued sites are marked
``cancelled``, and stale late completions are ignored. Each finished site is
qualified against the request criteria (default: the Functional preset).
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Callable, Optional

from . import diagnostics, qualify
from . import runtime
from .contracts import (BatchRequest, BatchResult, Issue, SiteRequest,
                        SiteResult)

EventCb = Optional[Callable[[str, str, dict], None]]

_MAX_CONCURRENCY = 2
_RETRY_BACKOFF_S = 1.5


def _is_cancelled(cancel) -> bool:
    if cancel is None:
        return False
    if callable(cancel):
        return bool(cancel())
    is_set = getattr(cancel, "is_set", None)
    return bool(is_set()) if callable(is_set) else False


def _resolve_criteria(criteria) -> tuple[Optional[dict], str]:
    if criteria is None:
        return qualify.PRESETS["functional"], "functional"
    if isinstance(criteria, str):
        return qualify.PRESETS.get(criteria), criteria
    if isinstance(criteria, dict):
        return criteria, str(criteria.get("id", "custom"))
    return None, "custom"


def _cancelled_result(site: SiteRequest) -> SiteResult:
    return SiteResult(
        site_id=site.site_id, state="cancelled",
        input={"lat": site.lat, "lon": site.lon, "comid": site.comid},
        issues=[Issue(code="cancelled", severity="info", stage="scheduler",
                      site_id=site.site_id, message="run cancelled before completion")])


async def _run_one_with_retry(site: SiteRequest, metric_ids, on_event,
                              diag: dict) -> SiteResult:
    from .api import run_site
    attempt = 0
    while True:
        with diagnostics.capture() as outcomes:
            res = await run_site(site, metric_ids=metric_ids, on_event=on_event)
        summary = diagnostics.summarize(outcomes)
        diag["timeouts"] += summary["timeouts"]
        diag["throttled"] += summary["throttled"]
        diag["server_errors"] += summary["server_errors"]
        # Retry once when the failure (or a partial result) is attributable to a
        # transient/throttled/5xx service outcome.
        transient = summary["transient"] or any(i.retryable for i in res.issues
                                                 if i.severity == "error")
        if attempt == 0 and res.state in ("failed", "partial") and transient:
            attempt += 1
            diag["retries"] += 1
            await asyncio.sleep(_RETRY_BACKOFF_S)
            continue
        res.metadata["attempts"] = attempt + 1
        return res


async def run_batch(request: BatchRequest, *, on_event: EventCb = None,
                    cancel=None) -> BatchResult:
    from .api import MAX_SITES, assign_ids
    runtime.ensure_cache()
    t0 = time.monotonic()

    sites, generated = assign_ids(request.sites)
    if len(sites) > MAX_SITES:
        raise ValueError(f"batch exceeds {MAX_SITES}-site limit ({len(sites)} sites)")

    metric_ids = request.config.metric_ids
    rule, criteria_id = _resolve_criteria(request.criteria)

    diag = {"total_sites": len(sites), "retries": 0, "timeouts": 0,
            "throttled": 0, "server_errors": 0}
    results: "OrderedDict[str, SiteResult]" = OrderedDict((s.site_id, None) for s in sites)
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def process(site: SiteRequest):
        if _is_cancelled(cancel):
            results[site.site_id] = _cancelled_result(site)
            return
        async with sem:
            if _is_cancelled(cancel):     # re-check after acquiring a slot
                results[site.site_id] = _cancelled_result(site)
                return
            res = await _run_one_with_retry(site, metric_ids, on_event, diag)
            qualify.qualify_site(res, rule, criteria_id=criteria_id)
            results[site.site_id] = res

    await asyncio.gather(*[process(s) for s in sites])

    ordered = [results[s.site_id] or _cancelled_result(s) for s in sites]
    diag["elapsed_s"] = round(time.monotonic() - t0, 2)
    for state in ("succeeded", "partial", "failed", "cancelled"):
        diag[state] = sum(1 for r in ordered if r.state == state)
    diag["qualified"] = sum(1 for r in ordered if r.qualification.auto == "qualified")

    return BatchResult(sites=ordered, config=request.config,
                       criteria=request.criteria, diagnostics=diag,
                       generated_ids=generated)
