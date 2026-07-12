"""Service-outcome side channel for retry classification.

EASI datasources never raise to the orchestrator (a failed source degrades to an
``unavailable`` metric), so a failed run carries no exception the scheduler can
classify. This ``contextvars`` recorder lets datasources report *why* a call
failed (HTTP status, timeout, throttle) without changing their return values or
the scoring path. The batch runner opens a ``capture()`` around each site and uses
``summarize()`` to decide whether a failure/partial result is transient and worth
one retry.

Recording is a no-op when no ``capture()`` is active (e.g. the interactive
single-site app), so wiring it into datasources is safe everywhere.
"""
from __future__ import annotations

import contextlib
import contextvars

_OUTCOMES: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "easi_service_outcomes", default=None)


def record(service: str, *, status: int | None = None, latency: float | None = None,
           timed_out: bool = False, throttled: bool = False,
           retry_after: float | None = None) -> None:
    """Append a service outcome to the active capture (no-op if none active)."""
    lst = _OUTCOMES.get()
    if lst is None:
        return
    lst.append({"service": service, "status": status, "latency": latency,
                "timed_out": timed_out, "throttled": throttled,
                "retry_after": retry_after})


def record_exception(service: str, exc: BaseException) -> None:
    """Classify a requests/urllib exception into a service outcome."""
    name = type(exc).__name__.lower()
    timed_out = "timeout" in name or "timeout" in str(exc).lower()
    record(service, timed_out=timed_out, throttled=False)


def record_response(service: str, status: int, *,
                    retry_after: float | None = None) -> None:
    """Record an HTTP response status (marks 429 throttled)."""
    record(service, status=status, throttled=(status == 429), retry_after=retry_after)


@contextlib.contextmanager
def capture():
    """Collect service outcomes for the duration of the block into a fresh list."""
    outcomes: list = []
    token = _OUTCOMES.set(outcomes)
    try:
        yield outcomes
    finally:
        _OUTCOMES.reset(token)


def summarize(outcomes: list) -> dict:
    """Tally outcomes and decide whether the run hit a transient/retryable failure."""
    timeouts = sum(1 for o in outcomes if o.get("timed_out"))
    throttled = sum(1 for o in outcomes
                    if o.get("throttled") or o.get("status") == 429)
    server_errors = sum(1 for o in outcomes
                        if (o.get("status") or 0) // 100 == 5)
    return {"count": len(outcomes), "timeouts": timeouts, "throttled": throttled,
            "server_errors": server_errors,
            "transient": bool(timeouts or throttled or server_errors)}
