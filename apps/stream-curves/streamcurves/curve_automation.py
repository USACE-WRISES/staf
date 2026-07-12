"""Curve-build automation + flagged-review scoring for the guided workflow.

``run_curve_automation`` drives the existing phase1->phase4 recompute over a metric
set and, as each metric's curve finishes, classifies the proposal into one of the
six ``run_state`` statuses, fingerprints it, and records a ``curve_review`` entry.

``sync_curve_review_after_recompute`` is the single choke point every manual curve
edit routes through (summary row/bulk recompute + phase4 "mark all complete"), so a
tweak re-scores without clobbering a reviewer's decision unless the fingerprint
actually changed (then the prior decision is archived and re-review is forced).

The scoring reducer (:func:`reconcile_review_map`) is pure and unit-tested; the
state wrappers only marshal the reactive reads into it.
"""
from __future__ import annotations

from typing import Callable, Optional

from shiny import reactive

from streamcurves import run_state as rs
from views import summary_state as ss
from views.state import AppState


# --------------------------------------------------------------------------- #
# Pure scoring reducer
# --------------------------------------------------------------------------- #
def reconcile_review_map(old_review: Optional[dict], proposals: dict) -> dict:
    """Fold fresh ``proposals`` into an existing ``curve_review`` map.

    ``proposals`` maps ``metric -> {curve_rows, mapping_ok, strat_ok, mapping,
    strat, summary, exc?}``. Returns a new review map (does not mutate inputs).
    """
    review = dict(old_review or {})
    for metric, prop in (proposals or {}).items():
        status, reasons = rs.classify_curve_proposal(
            prop.get("curve_rows"),
            mapping_ok=prop.get("mapping_ok", True),
            strat_ok=prop.get("strat_ok", True),
            exc=prop.get("exc"),
        )
        fingerprint = rs.proposal_fingerprint(
            prop.get("curve_rows"), mapping=prop.get("mapping"), strat=prop.get("strat")
        )
        review[metric] = rs.reconcile_curve_review_entry(
            review.get(metric),
            status=status,
            reasons=reasons,
            fingerprint=fingerprint,
            proposal_summary=prop.get("summary") or {},
        )
    return review


# --------------------------------------------------------------------------- #
# State -> proposal marshalling
# --------------------------------------------------------------------------- #
def _metric_function(state: AppState, metric: str) -> Optional[str]:
    with reactive.isolate():
        cfun = state.column_functions() or {}
    if not cfun:
        return None
    val = cfun.get(metric)
    return str(val) if val else None


def _mapping_ok(state: AppState, metric: str) -> bool:
    """A metric is mapped when the run recorded a function for it. When no
    mapping info exists at all we do not flag (unknown != unmapped)."""
    with reactive.isolate():
        cfun = state.column_functions() or {}
    if not cfun:
        return True
    return bool(cfun.get(metric))


def _strat_ok(state: AppState, metric: str) -> bool:
    """False when the stratification the curve uses is flagged infeasible in
    phase 3 (needs a reviewer's eye before the curve is trusted)."""
    try:
        strat_used = ss.get_metric_curve_stratification(state, metric)
    except Exception:  # noqa: BLE001
        return True
    if not strat_used or str(strat_used) in ("none", "None", ""):
        return True
    phase3 = ss.get_metric_phase3_display_state(state, metric)
    feas = (phase3 or {}).get("feasibility")
    if feas is None or len(feas) == 0:
        return True
    try:
        flagged = set(
            feas.loc[feas["feasibility_flag"] != "feasible", "stratification"].astype(str)
        )
    except Exception:  # noqa: BLE001
        return True
    return str(strat_used) not in flagged


def _proposal_summary(metric: str, curve_rows, mapping, strat_used) -> dict:
    rows = rs._as_rows(curve_rows)
    statuses = [str(r.get("curve_status") or "") for r in rows]
    n_refs = [r.get("n_reference") for r in rows if r.get("n_reference") is not None]
    return {
        "metric": metric,
        "function": mapping,
        "strat": None if not strat_used or str(strat_used) in ("none", "None") else str(strat_used),
        "n_strata": len(rows),
        "curve_statuses": statuses,
        "min_n_reference": min(n_refs) if n_refs else None,
    }


def build_metric_proposal(state: AppState, metric: str, *, exc: Optional[BaseException] = None) -> dict:
    """Read the fresh phase4 curve rows + mapping + stratification for a metric."""
    if exc is not None:
        return {"curve_rows": None, "mapping_ok": True, "strat_ok": True,
                "mapping": None, "strat": None, "exc": exc,
                "summary": {"metric": metric, "error": str(exc)}}
    phase4 = ss.get_metric_phase4_display_state(state, metric)
    curve_rows = phase4.get("curve_rows")
    mapping = _metric_function(state, metric)
    try:
        strat_used = ss.get_metric_curve_stratification(state, metric)
    except Exception:  # noqa: BLE001
        strat_used = None
    return {
        "curve_rows": curve_rows,
        "mapping_ok": _mapping_ok(state, metric),
        "strat_ok": _strat_ok(state, metric),
        "mapping": mapping,
        "strat": None if not strat_used or str(strat_used) in ("none", "None") else str(strat_used),
        "summary": _proposal_summary(metric, curve_rows, mapping, strat_used),
    }


def sync_curve_review_after_recompute(
    state: AppState, metrics: Optional[list[str]] = None
) -> dict:
    """Re-score the given metrics (or all eligible) and store the review map.

    The single choke point for keeping ``curve_review`` in step with the live
    curves after any recompute or manual tweak. Returns the new review map.
    """
    with reactive.isolate():
        metric_config = state.metric_config() or {}
        old_review = dict(state.curve_review() or {})
    targets = (
        list(metrics)
        if metrics is not None
        else ss.eligible_summary_metrics(metric_config)
    )
    proposals = {m: build_metric_proposal(state, m) for m in targets}
    review = reconcile_review_map(old_review, proposals)
    state.curve_review.set(review)
    return review


def run_curve_automation(
    state: AppState,
    metrics: Optional[list[str]] = None,
    *,
    mode: str = "summary",
    progress_cb: Optional[Callable] = None,
) -> dict:
    """Build curves for a metric set and score each proposal as it finishes.

    Reuses ``summary_state.recompute_metrics_from_summary`` (phase1->phase4) with an
    ``on_metric_done`` that re-scores just that metric, so partial progress lands in
    ``curve_review`` incrementally. Returns the final review map.
    """
    with reactive.isolate():
        metric_config = state.metric_config() or {}
    targets = (
        list(metrics)
        if metrics is not None
        else ss.eligible_summary_metrics(metric_config)
    )

    def on_metric_done(metric):
        sync_curve_review_after_recompute(state, [metric])

    ss.recompute_metrics_from_summary(
        state, targets, mode=mode, progress_cb=progress_cb,
        on_metric_done=on_metric_done,
    )
    # Final full pass to catch metrics whose phase4 was skipped (e.g. ineligible).
    return sync_curve_review_after_recompute(state, targets)


# --------------------------------------------------------------------------- #
# Reviewer actions (guided review-queue modal)
# --------------------------------------------------------------------------- #
def set_review_decision(
    state: AppState, metric: str, decision: str, *, note: str | None = None,
    actor: str | None = None,
) -> dict:
    """Stamp a reviewer decision (finalize / remove) onto a metric's entry."""
    with reactive.isolate():
        review = dict(state.curve_review() or {})
    entry = review.get(metric)
    if entry is None:
        return review
    review[metric] = rs.apply_review_decision(entry, decision, note=note, actor=actor)
    state.curve_review.set(review)
    return review
