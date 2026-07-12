"""Guided-run state vocabulary + pure helpers (StreamCurves guided workflow).

Everything here is pure and unit-testable: no reactive access, no I/O, no Shiny.
The reactive AppState fields it describes (``run_meta``, ``run_stage_status``,
``curve_review``, ``screening_run``, ``site_exclusions``) are seeded and restored
in ``views/state.py`` + ``session_io.py``; this module owns the *shapes* and the
*derivations* over them so the guided cards, the review queue, and the publish
gate all agree on one vocabulary.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

# --------------------------------------------------------------------------- #
# Method-version stamps (travel into published bundles so a consumer can tell
# which algorithm produced a curve / a screening decision).
# --------------------------------------------------------------------------- #
CURVE_METHOD_VERSION = "iqr-seed-1"
SCREENING_METHOD_VERSION = "easi-batch-1"

# --------------------------------------------------------------------------- #
# The five guided stages, in order.
# --------------------------------------------------------------------------- #
STAGE_KEYS = [
    "region_sources",
    "candidate_screening",
    "enrichment_build",
    "curve_review",
    "publish",
]

STAGE_LABELS = {
    "region_sources": "Region & Sources",
    "candidate_screening": "Candidate Sites & EASI Screening",
    "enrichment_build": "Enrichment, Build & Classification",
    "curve_review": "Curve Analysis & Flagged Review",
    "publish": "Preliminary Package & Publish",
}

# Stage status vocabulary (badge colors are chosen in the view).
STAGE_BLOCKED = "blocked"
STAGE_READY = "ready"
STAGE_RUNNING = "in_progress"
STAGE_ATTENTION = "attention"
STAGE_DONE = "done"

# --------------------------------------------------------------------------- #
# Curve-proposal classification outcomes (the six statuses).
# --------------------------------------------------------------------------- #
CURVE_STATUS_AUTO_OK = "auto_ok"
CURVE_STATUS_INSUFFICIENT = "insufficient_data"
CURVE_STATUS_DEGENERATE = "degenerate"
CURVE_STATUS_UNMAPPED = "unmapped"
CURVE_STATUS_STRAT_REVIEW = "strat_review"
CURVE_STATUS_ERROR = "error"

CURVE_STATUSES = [
    CURVE_STATUS_AUTO_OK,
    CURVE_STATUS_INSUFFICIENT,
    CURVE_STATUS_DEGENERATE,
    CURVE_STATUS_UNMAPPED,
    CURVE_STATUS_STRAT_REVIEW,
    CURVE_STATUS_ERROR,
]

# Every classification except a clean auto_ok lands in the flagged-review queue.
CURVE_REVIEW_REQUIRED = {s for s in CURVE_STATUSES if s != CURVE_STATUS_AUTO_OK}

# Reviewer decisions on a proposal.
DECISION_AUTO = "auto_finalized"       # clean build, no review needed
DECISION_PENDING = "pending"           # flagged, awaiting a reviewer
DECISION_FINALIZED = "reviewer_finalized"
DECISION_REMOVED = "removed_from_scope"

IN_SCOPE_DECISIONS = {DECISION_AUTO, DECISION_FINALIZED}

REVIEW_REASONS = {
    "insufficient_data": "Fewer than 5 reference observations in a stratum.",
    "degenerate_q25": "Non-positive or non-finite Q25 produced a fallback curve.",
    "degenerate_curve": "The IQR seed did not validate as a monotone curve.",
    "unmapped": "Metric is not assigned to a STAF function.",
    "strat_review": "Stratification needs review before the curve is trusted.",
    "build_error": "The curve build raised an error.",
}


# --------------------------------------------------------------------------- #
# Run metadata
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_meta(*, region: Optional[dict] = None) -> dict:
    ts = _now_iso()
    return {
        "created": ts,
        "updated": ts,
        "curve_method_version": CURVE_METHOD_VERSION,
        "screening_method_version": SCREENING_METHOD_VERSION,
        "region": region,
    }


def touch_run_meta(run_meta: Optional[dict], *, region: Optional[dict] = None) -> dict:
    """Return a shallow-copied run_meta with a fresh ``updated`` stamp."""
    meta = dict(run_meta or {})
    if not meta:
        meta = new_run_meta(region=region)
        return meta
    meta["updated"] = _now_iso()
    meta.setdefault("created", meta["updated"])
    meta.setdefault("curve_method_version", CURVE_METHOD_VERSION)
    meta.setdefault("screening_method_version", SCREENING_METHOD_VERSION)
    if region is not None:
        meta["region"] = region
    return meta


# --------------------------------------------------------------------------- #
# Stage status derivation
# --------------------------------------------------------------------------- #
def derive_stage_status(
    snapshot: dict, tasks_running: Optional[dict] = None
) -> dict[str, dict]:
    """Map a run snapshot to a per-stage ``{status, detail}`` dict.

    ``snapshot`` carries plain facts (booleans + counts + the curve_review map);
    ``tasks_running`` marks stages whose extended task is live. Pure: the guided
    view turns each status into a badge + a primary/secondary action.
    """
    s = snapshot or {}
    running = tasks_running or {}
    cr = s.get("curve_review") or {}
    intended = intended_metrics_for_publish(cr)
    flagged = flagged_metrics(cr)

    out: dict[str, dict] = {}

    # 1 - Region & sources
    has_region = bool(s.get("has_region"))
    if has_region and s.get("has_data_source"):
        out["region_sources"] = {
            "status": STAGE_DONE,
            "detail": s.get("region_label") or "Region and candidate source ready.",
        }
    elif has_region:
        out["region_sources"] = {
            "status": STAGE_READY,
            "detail": "Region set. Load the candidate source next.",
        }
    else:
        out["region_sources"] = {
            "status": STAGE_READY,
            "detail": "Choose a Level III ecoregion to begin.",
        }

    # 2 - Candidate sites & EASI screening
    n_cand = int(s.get("n_candidates") or 0)
    n_ret = int(s.get("n_retained") or 0)
    if running.get("candidate_screening"):
        out["candidate_screening"] = {
            "status": STAGE_RUNNING,
            "detail": "Screening candidate sites...",
        }
    elif s.get("has_screening") and n_ret > 0:
        out["candidate_screening"] = {
            "status": STAGE_DONE,
            "detail": f"{n_ret} of {n_cand} candidates retained.",
        }
    elif s.get("has_screening"):
        out["candidate_screening"] = {
            "status": STAGE_ATTENTION,
            "detail": "No sites retained yet. Review screening decisions.",
        }
    elif n_cand > 0:
        out["candidate_screening"] = {
            "status": STAGE_READY,
            "detail": f"{n_cand} candidate sites ready to screen.",
        }
    else:
        out["candidate_screening"] = {
            "status": STAGE_BLOCKED,
            "detail": "Load candidate sites first.",
        }

    # 3 - Enrichment, build & classification
    if running.get("enrichment_build"):
        out["enrichment_build"] = {
            "status": STAGE_RUNNING,
            "detail": "Enriching retained sites...",
        }
    elif s.get("enriched"):
        out["enrichment_build"] = {
            "status": STAGE_DONE,
            "detail": f"{int(s.get('n_enriched') or n_ret)} sites enriched.",
        }
    elif n_ret > 0:
        out["enrichment_build"] = {
            "status": STAGE_READY,
            "detail": "Retained sites ready to enrich.",
        }
    else:
        out["enrichment_build"] = {
            "status": STAGE_BLOCKED,
            "detail": "Retain at least one screened site first.",
        }

    # 4 - Curve analysis & flagged review
    if running.get("curve_review"):
        out["curve_review"] = {
            "status": STAGE_RUNNING,
            "detail": "Building curves...",
        }
    elif not cr:
        status = STAGE_READY if s.get("enriched") else STAGE_BLOCKED
        out["curve_review"] = {
            "status": status,
            "detail": (
                "Enrich sites, then build curves."
                if status == STAGE_BLOCKED
                else "Ready to build reference curves."
            ),
        }
    elif flagged:
        out["curve_review"] = {
            "status": STAGE_ATTENTION,
            "detail": f"{len(flagged)} curve(s) need review; {len(intended)} in scope.",
        }
    else:
        out["curve_review"] = {
            "status": STAGE_DONE,
            "detail": f"{len(intended)} curve(s) in scope.",
        }

    # 5 - Preliminary package & publish
    if s.get("published"):
        out["publish"] = {
            "status": STAGE_DONE,
            "detail": s.get("published_label") or "Preliminary version published.",
        }
    elif is_ready_to_publish(s):
        out["publish"] = {
            "status": STAGE_READY,
            "detail": "Ready to publish a preliminary version.",
        }
    else:
        out["publish"] = {
            "status": STAGE_BLOCKED,
            "detail": "Finish the checklist to publish.",
        }

    return out


# --------------------------------------------------------------------------- #
# Curve proposal classification + fingerprint
# --------------------------------------------------------------------------- #
def _as_rows(curve_rows: Any) -> list[dict]:
    if curve_rows is None:
        return []
    # pandas DataFrame -> list[dict] without importing pandas here.
    to_dict = getattr(curve_rows, "to_dict", None)
    if to_dict is not None and hasattr(curve_rows, "columns"):
        try:
            return list(curve_rows.to_dict(orient="records"))
        except TypeError:
            pass
    if isinstance(curve_rows, dict):
        return [curve_rows]
    return [dict(r) if not isinstance(r, dict) else r for r in curve_rows]


def classify_curve_proposal(
    curve_rows: Any,
    precheck: Any = None,
    *,
    mapping_ok: bool = True,
    strat_ok: bool = True,
    exc: Optional[BaseException] = None,
) -> tuple[str, list[str]]:
    """Classify one metric's curve proposal into a status + human reasons.

    Precedence: build error > unmapped > insufficient data > degenerate >
    stratification review > clean (auto_ok).
    """
    if exc is not None:
        return CURVE_STATUS_ERROR, [f"{REVIEW_REASONS['build_error']} ({exc})"]

    if not mapping_ok:
        return CURVE_STATUS_UNMAPPED, [REVIEW_REASONS["unmapped"]]

    rows = _as_rows(curve_rows)
    statuses = [str(r.get("curve_status") or "") for r in rows]

    if not rows or any(st == "insufficient_data" for st in statuses):
        return CURVE_STATUS_INSUFFICIENT, [REVIEW_REASONS["insufficient_data"]]

    degen = [st for st in statuses if st in ("degenerate_q25", "degenerate_curve")]
    if degen:
        # dict.fromkeys keeps first-seen order, de-duplicated.
        return CURVE_STATUS_DEGENERATE, [REVIEW_REASONS[d] for d in dict.fromkeys(degen)]

    if not strat_ok:
        return CURVE_STATUS_STRAT_REVIEW, [REVIEW_REASONS["strat_review"]]

    return CURVE_STATUS_AUTO_OK, []


def _normalize_curve_rows(curve_rows: Any) -> list[dict]:
    """Stable subset of each curve row that defines the proposal's identity."""
    keep = (
        "stratum",
        "curve_status",
        "n_reference",
        "min_val",
        "q25",
        "median_val",
        "q75",
        "max_val",
        "iqr",
        "points",
    )
    out = []
    for r in _as_rows(curve_rows):
        out.append({k: r.get(k) for k in keep if k in r})
    return out


def proposal_fingerprint(
    curve_rows: Any, *, mapping: Any = None, strat: Any = None
) -> str:
    """16-hex-char digest over the curve content + mapping + stratification.

    A manual curve tweak changes the seed points, hence the fingerprint, hence
    forces the review queue to re-score a previously finalized proposal.
    """
    payload = {
        "curves": _normalize_curve_rows(curve_rows),
        "mapping": mapping,
        "strat": strat,
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# curve_review[metric] entries
# --------------------------------------------------------------------------- #
def new_curve_review_entry(
    status: str, reasons: Iterable[str], fingerprint: str, proposal_summary: dict
) -> dict:
    decision = DECISION_AUTO if status == CURVE_STATUS_AUTO_OK else DECISION_PENDING
    return {
        "status": status,
        "reasons": list(reasons or []),
        "fingerprint": fingerprint,
        "proposal_summary": dict(proposal_summary or {}),
        "decision": decision,
        "decision_note": None,
        "decision_actor": None,
        "history": [],
    }


def reconcile_curve_review_entry(
    old: Optional[dict],
    *,
    status: str,
    reasons: Iterable[str],
    fingerprint: str,
    proposal_summary: dict,
) -> dict:
    """Re-score a proposal without clobbering a reviewer decision on a no-op.

    - No prior entry -> a fresh entry.
    - Same fingerprint + a reviewer decision -> keep the decision, refresh status.
    - Changed fingerprint after a reviewer decision -> archive to history and
      force re-review (a fresh pending/auto entry).
    """
    if not old:
        return new_curve_review_entry(status, reasons, fingerprint, proposal_summary)

    old_decision = old.get("decision") or DECISION_PENDING
    old_fp = old.get("fingerprint")
    reviewer_set = old_decision in (DECISION_FINALIZED, DECISION_REMOVED)

    if reviewer_set and old_fp == fingerprint:
        entry = dict(old)
        entry["status"] = status
        entry["reasons"] = list(reasons or [])
        entry["proposal_summary"] = dict(proposal_summary or {})
        entry.setdefault("history", [])
        return entry

    entry = new_curve_review_entry(status, reasons, fingerprint, proposal_summary)
    if reviewer_set and old_fp != fingerprint:
        history = list(old.get("history") or [])
        history.append(
            {
                "fingerprint": old_fp,
                "decision": old_decision,
                "status": old.get("status"),
                "reasons": list(old.get("reasons") or []),
                "note": old.get("decision_note"),
            }
        )
        entry["history"] = history
    return entry


def apply_review_decision(
    entry: Optional[dict], decision: str, *, note: str | None = None, actor: str | None = None
) -> dict:
    """Return a copy of ``entry`` stamped with a reviewer decision."""
    if decision not in (DECISION_FINALIZED, DECISION_REMOVED, DECISION_PENDING):
        raise ValueError(f"invalid review decision {decision!r}")
    base = dict(entry or {})
    base.setdefault("history", [])
    base["decision"] = decision
    base["decision_note"] = note
    base["decision_actor"] = actor
    return base


# --------------------------------------------------------------------------- #
# Scope + review queries
# --------------------------------------------------------------------------- #
def _decision_of(entry: Optional[dict]) -> str:
    entry = entry or {}
    status = entry.get("status")
    decision = entry.get("decision")
    if decision:
        return decision
    return DECISION_AUTO if status == CURVE_STATUS_AUTO_OK else DECISION_PENDING


def is_in_scope(entry: Optional[dict]) -> bool:
    return _decision_of(entry) in IN_SCOPE_DECISIONS


def needs_review(entry: Optional[dict]) -> bool:
    return _decision_of(entry) == DECISION_PENDING


def intended_metrics_for_publish(curve_review: Optional[dict]) -> list[str]:
    return sorted(
        m for m, entry in (curve_review or {}).items() if is_in_scope(entry)
    )


def flagged_metrics(curve_review: Optional[dict]) -> list[str]:
    return sorted(
        m for m, entry in (curve_review or {}).items() if needs_review(entry)
    )


# --------------------------------------------------------------------------- #
# Site exclusions -> legacy site_mask_config
# --------------------------------------------------------------------------- #
def _excluded_id_set(exclusions: Any) -> set[str]:
    if not exclusions:
        return set()
    out: set[str] = set()
    for e in exclusions:
        if isinstance(e, dict):
            sid = e.get("site_id")
        else:
            sid = e
        if sid is not None:
            out.add(str(sid))
    return out


def site_mask_config_from_exclusions(
    raw_data: Any,
    exclusions: Any,
    *,
    site_id_column: str,
    site_label_column: Optional[str] = None,
) -> dict:
    """Map excluded external site ids to 1-based row positions in ``raw_data``.

    Produces the legacy ``site_mask_config`` shape (``site_label_column``,
    ``masked_site_ids`` as 1-based positions, ``site_labels``) so the workbook
    export still carries masks for the sites screening or a reviewer removed.
    """
    excluded = _excluded_id_set(exclusions)
    masked_positions: list[int] = []
    labels: list[str] = []
    cols_attr = getattr(raw_data, "columns", None)
    columns = list(cols_attr) if cols_attr is not None else []
    if raw_data is not None and site_id_column in columns and excluded:
        ids = [str(v) for v in raw_data[site_id_column].tolist()]
        has_label = bool(site_label_column) and site_label_column in columns
        label_values = (
            [str(v) for v in raw_data[site_label_column].tolist()] if has_label else None
        )
        for pos, sid in enumerate(ids, start=1):  # 1-based, workbook convention
            if sid in excluded:
                masked_positions.append(pos)
                labels.append(label_values[pos - 1] if label_values else sid)
    return {
        "site_label_column": site_label_column or site_id_column,
        "masked_site_ids": masked_positions,
        "site_labels": labels,
    }


# --------------------------------------------------------------------------- #
# Publish readiness
# --------------------------------------------------------------------------- #
def readiness_checklist(snapshot: dict) -> list[dict]:
    """Ordered checklist gating a preliminary publish. Each item: key/label/ok."""
    s = snapshot or {}
    cr = s.get("curve_review") or {}
    intended = intended_metrics_for_publish(cr)
    unresolved = flagged_metrics(cr)
    return [
        {
            "key": "region",
            "label": "Region of applicability set",
            "ok": bool(s.get("has_region")),
        },
        {
            "key": "screening",
            "label": "Reference screening complete with retained sites",
            "ok": bool(s.get("has_screening")) and int(s.get("n_retained") or 0) > 0,
        },
        {
            "key": "enriched",
            "label": "Retained sites enriched",
            "ok": bool(s.get("enriched")),
        },
        {
            "key": "curves",
            "label": "At least one curve in scope",
            "ok": len(intended) > 0,
        },
        {
            "key": "review",
            "label": "No unresolved flagged curves",
            "ok": len(unresolved) == 0,
        },
    ]


def is_ready_to_publish(snapshot: dict) -> bool:
    return all(item["ok"] for item in readiness_checklist(snapshot))
