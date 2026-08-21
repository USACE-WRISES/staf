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
    "refine_map",
    "curve_review",
    "publish",
]

STAGE_LABELS = {
    "region_sources": "Region & Data Sources",
    "candidate_screening": "Screen Candidate Sites (EASI)",
    "enrichment_build": "Build Dataset: Metrics, Compile & Classify",
    "refine_map": "Refine Workbook, Map Functions & Validate",
    "curve_review": "Reference Curves & Flagged Review",
    "publish": "Package & Publish",
}

# ── Workflow navigation vocabulary ─────────────────────────────────────────
# The workflow strip is the app's primary navigation: six stages, where
# stages 1-3 group the Data & Setup wizard's seven internal steps, stage 4 is
# the opened-project workspace, and stages 5-6 are whole pages. Everything
# here is pure so the strip, the wizard, and tests share one mapping.

# stage key -> ordered (wizard_step, sub-step label) pairs. Only the wizard
# stages have sub-steps; labels match import_map._STEP_LABELS.
STAGE_SUBSTEPS: dict[str, list[tuple[int, str]]] = {
    "region_sources": [(1, "Region"), (2, "Add data")],
    "candidate_screening": [(3, "Screen sites")],
    "enrichment_build": [
        (4, "Choose metrics"),
        (5, "Compile"),
        (6, "Classify"),
        (7, "Review & build"),
    ],
}

# stage key -> ordered (section value, label) pairs. Sections are the panels of
# a page stage, NOT wizard steps -- refine_map's are the workspace panels, and
# keeping them out of STAGE_SUBSTEPS is what keeps stage_for_wizard_step and
# the wizard's [1..7] step space honest.
STAGE_SECTIONS: dict[str, list[tuple[str, str]]] = {
    "refine_map": [
        ("workbook", "Workbook"),
        ("mapping", "Function mapping"),
        ("redundancy", "Metric redundancy"),
        ("validation", "Pre-run validation"),
    ],
}

# stage key -> (main_navbar nav value, wizard step to land on or None).
# Wizard stages land on their first sub-step; page stages just switch panels
# (refine_map additionally closes any open wizard -- the strip fires the
# workspace_open nonce so Data & Setup falls back to the workspace view).
STAGE_LANDINGS: dict[str, tuple[str, Optional[int]]] = {
    "region_sources": ("data", 1),
    "candidate_screening": ("data", 3),
    "enrichment_build": ("data", 4),
    "refine_map": ("data", None),
    "curve_review": ("curves", None),
    "publish": ("publish", None),
}


def stage_for_wizard_step(step: Optional[int]) -> Optional[str]:
    """Map a 1-based wizard step to the stage that owns it (None if unknown)."""
    if step is None:
        return None
    for key, subs in STAGE_SUBSTEPS.items():
        if any(s == int(step) for s, _ in subs):
            return key
    return None


def stage_landing(stage_key: str) -> tuple[str, Optional[int]]:
    """Uniform click target for a stage: ``(nav_value, wizard_step | None)``."""
    return STAGE_LANDINGS[stage_key]


def current_stage(
    tab: Optional[str],
    data_setup_view: Optional[str],
    wizard_step: Optional[int],
) -> Optional[str]:
    """Which stage the user is on right now (None on the tool tabs).

    ``tab`` is the active main_navbar value, ``data_setup_view`` the Data &
    Setup view mode (landing/new/wizard/workspace), ``wizard_step`` the
    wizard's current 1-based step.
    """
    if tab == "curves":
        return "curve_review"
    if tab == "publish":
        return "publish"
    if tab != "data":
        return None  # Regional Curves / Cross-Sections tools
    if data_setup_view in ("new", "wizard"):
        return stage_for_wizard_step(wizard_step) or "region_sources"
    if data_setup_view == "workspace":
        return "refine_map"  # the opened-project workspace IS stage 4
    return "region_sources"  # landing: the natural starting point


# Side analyses: real work surfaces that need a built dataset but sit outside the
# numbered sequence. They produce no stage status and gate no publish, so they stay
# out of STAGE_KEYS / STAGE_LANDINGS -- the strip renders them as unnumbered chips
# after the five stages, and current_stage() keeps returning None while one is open.
TOOL_KEYS = ["regional", "xsec"]  # == their main_navbar nav values
TOOL_LABELS = {
    "regional": "Regional curves",
    "xsec": "Cross-sections",
}


def current_tool(tab: Optional[str]) -> Optional[str]:
    """Which side analysis is open, or None while in the staged workflow."""
    return tab if tab in TOOL_KEYS else None


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
CURVE_STATUS_SHAPE_CONFLICT = "shape_conflict"
CURVE_STATUS_DATA_REVIEW = "data_review"
CURVE_STATUS_STRAT_REVIEW = "strat_review"
CURVE_STATUS_MULTI_CROSSING = "multi_crossing"
CURVE_STATUS_ERROR = "error"

CURVE_STATUSES = [
    CURVE_STATUS_AUTO_OK,
    CURVE_STATUS_INSUFFICIENT,
    CURVE_STATUS_DEGENERATE,
    CURVE_STATUS_UNMAPPED,
    CURVE_STATUS_SHAPE_CONFLICT,
    CURVE_STATUS_DATA_REVIEW,
    CURVE_STATUS_STRAT_REVIEW,
    CURVE_STATUS_MULTI_CROSSING,
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
    "degenerate_curve": "The IQR seed did not validate as a scoring curve.",
    "unmapped": "Metric is not assigned to a STAF function.",
    "shape_conflict": (
        "The curve's realized shape conflicts with the metric's approved "
        "ecological expectation (CURVE-05), so it must not ship without review."
    ),
    "data_review": (
        "Missing-data fraction exceeds the review threshold (DATA-03), so the "
        "curve must not be auto-recommended."
    ),
    "strat_review": "Stratification needs review before the curve is trusted.",
    "multi_crossing": (
        "The curve crosses a scoring threshold more than twice, so its condition "
        "bands cannot be expressed as ranges."
    ),
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
    elif s.get("enriched") and int(s.get("n_missing_diagnostics") or 0) > 0:
        # The dataset is built; the stratifier analysis this stage owns is not.
        # Without this the strip read "done" while every analysis tab reported
        # that nothing had run. Still counts as enriched downstream: stages 4 to
        # 6 gate on the build, not on the diagnostics.
        n_missing = int(s["n_missing_diagnostics"])
        out["enrichment_build"] = {
            "status": STAGE_ATTENTION,
            "detail": f"{n_missing} metric(s) have no screening or verification result.",
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

    # 4 - Refine workbook, map functions & validate (the workspace). No async
    # task, so no RUNNING state. Coverage here is mapping-level (functions with
    # no assigned metric and no documented exception) -- the bundle-level
    # snapshot["coverage"] is None until a curve is finalized, which happens
    # after this stage.
    n_unmapped = int(s.get("n_unmapped_functions") or 0)
    if not s.get("enriched"):
        out["refine_map"] = {
            "status": STAGE_BLOCKED,
            "detail": "Build a dataset first.",
        }
    elif n_unmapped > 0:
        out["refine_map"] = {
            "status": STAGE_ATTENTION,
            "detail": (
                f"{n_unmapped} function(s) need a metric or a documented "
                "exception."
            ),
        }
    elif not s.get("mapping_confirmed"):
        out["refine_map"] = {
            "status": STAGE_READY,
            "detail": "Review the workbook and confirm the function mapping.",
        }
    else:
        out["refine_map"] = {
            "status": STAGE_DONE,
            "detail": "Function mapping confirmed.",
        }

    # 5 - Curve analysis & flagged review
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

    # 6 - Preliminary package & publish
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
    strat_reason: Optional[str] = None,
    data_ok: bool = True,
    data_reason: Optional[str] = None,
    shape_ok: bool = True,
    shape_reason: Optional[str] = None,
    exc: Optional[BaseException] = None,
) -> tuple[str, list[str]]:
    """Classify one metric's curve proposal into a status + human reasons.

    Precedence: build error > unmapped > insufficient data > degenerate >
    multi-crossing > shape conflict (CURVE-05) > data review (DATA-03) >
    stratification review > clean (auto_ok). ``data_ok=False`` marks a metric
    whose missing-data fraction exceeds the DATA-03 review threshold,
    ``shape_ok=False`` a curve whose realized shape contradicts the approved
    expectation, and ``strat_reason`` lets a caller name the specific
    stratification problem (e.g. a stratum below the DATA-07 floor).
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

    # The curve engine escalates an over-wiggly curve to unsupported_multi_crossing;
    # without a bucket here it fell through to auto_ok and shipped unreviewed.
    if any(st == "unsupported_multi_crossing" for st in statuses):
        return CURVE_STATUS_MULTI_CROSSING, [REVIEW_REASONS["multi_crossing"]]

    if not shape_ok:
        return CURVE_STATUS_SHAPE_CONFLICT, [
            shape_reason or REVIEW_REASONS["shape_conflict"]]

    if not data_ok:
        return CURVE_STATUS_DATA_REVIEW, [data_reason or REVIEW_REASONS["data_review"]]

    if not strat_ok:
        return CURVE_STATUS_STRAT_REVIEW, [strat_reason or REVIEW_REASONS["strat_review"]]

    return CURVE_STATUS_AUTO_OK, []


def strata_floor_check(curve_rows: Any) -> tuple[bool, Optional[str]]:
    """DATA-07/08 per-stratum sample floors for a stratified curve proposal.

    Applies only when the proposal actually stratifies (more than one row, or a
    named stratum). An unstratified proposal passes. The region denominator for
    the DATA-08 ten-percent rule is the sum of the strata (the strata partition
    the reference pool's non-missing values). Reads the calibrated floors from
    the methodology config so the acting values have one home.
    """
    from . import methodology  # local import; keeps this module light at import

    rows = _as_rows(curve_rows)
    strat_rows = [
        r for r in rows
        if r.get("stratum") not in (None, "") and str(r.get("stratum")) != "nan"
    ]
    if len(rows) <= 1 and not strat_rows:
        return True, None
    if not strat_rows:
        return True, None

    def _n(r):
        try:
            return int(r.get("n_reference"))
        except (TypeError, ValueError):
            return None

    ns = [(str(r.get("stratum")), _n(r)) for r in strat_rows]
    known = [n for _, n in ns if n is not None]
    total = sum(known) if known else 0
    very_small = int(methodology.threshold("data_rules.very_small_stratum_n"))
    small_frac = float(methodology.threshold("data_rules.very_small_stratum_frac"))
    floor = int(methodology.threshold("data_rules.min_n_stratum"))

    for name, n in ns:
        if n is None:
            continue
        if n < very_small or (total > 0 and n < small_frac * total):
            return False, (
                f"Stratum '{name}' has n = {n}, below the very-small-stratum rule "
                f"(DATA-08: fewer than {very_small} or under {small_frac:.0%} of the pool)."
            )
    for name, n in ns:
        if n is not None and n < floor:
            return False, (
                f"Stratum '{name}' has n = {n}, below the automated stratified "
                f"floor (DATA-07: {floor})."
            )
    return True, None


# --------------------------------------------------------------------------- #
# CURVE-05: realized shape versus the approved ecological expectation
# --------------------------------------------------------------------------- #
EXPECTED_SHAPES = ("monotone_increasing", "monotone_decreasing", "optimum")


def expected_shape_from_entry(metric_entry: Any) -> Optional[str]:
    """The approved expected shape for a metric_config or registry entry.

    An explicit ``expected_shape`` wins. Otherwise the shape is derived from the
    curated declaration: ``curve_form: optimum`` means optimum, and a monotone
    metric's direction comes from ``higher_is_better``. Returns None when the
    entry declares nothing (an unresolved direction), so no conflict can be
    claimed against a metric that has no approved expectation.
    """
    entry = metric_entry or {}
    explicit = str(entry.get("expected_shape") or "").strip().lower()
    if explicit in EXPECTED_SHAPES:
        return explicit
    form = str(entry.get("curve_form") or "").strip().lower()
    if form == "optimum":
        return "optimum"
    hib = entry.get("higher_is_better")
    if hib is True:
        return "monotone_increasing"
    if hib is False:
        return "monotone_decreasing"
    return None


def _points_ys(curve_points: Any) -> list[float]:
    """Index scores ordered by metric value, from any of the point shapes the
    app passes around (DataFrame with metric_value/index_score, or dict lists)."""
    rows: list[tuple[float, float]] = []
    if curve_points is None:
        return []
    if hasattr(curve_points, "columns"):
        try:
            for rec in curve_points.to_dict(orient="records"):
                rows.append((float(rec["metric_value"]), float(rec["index_score"])))
        except (KeyError, TypeError, ValueError):
            return []
    else:
        for rec in curve_points:
            try:
                if "metric_value" in rec:
                    rows.append((float(rec["metric_value"]), float(rec["index_score"])))
                else:
                    rows.append((float(rec["x"]), float(rec["y"])))
            except (KeyError, TypeError, ValueError):
                return []
    rows.sort(key=lambda p: p[0])
    return [y for _, y in rows]


def realized_curve_shape(curve_points: Any, tol: float = 1e-9) -> Optional[str]:
    """The shape a curve's points actually trace: ``monotone_increasing``,
    ``monotone_decreasing``, ``optimum`` (rises and falls around an interior
    peak), or None when the points are absent, flat, or unreadable."""
    ys = _points_ys(curve_points)
    if len(ys) < 2:
        return None
    peak = max(ys)
    first, last = ys[0], ys[-1]
    if peak - first > tol and peak - last > tol:
        return "optimum"
    if last - first > tol:
        return "monotone_increasing"
    if first - last > tol:
        return "monotone_decreasing"
    return None


def shape_conflict_check(curve_rows: Any, metric_entry: Any) -> tuple[bool, Optional[str]]:
    """CURVE-05: does any built curve contradict the approved expectation?

    Only rows with points are judged, and a metric with no approved expectation
    can never conflict (its absence is the direction-review path instead). A
    flat or degenerate trace is left to the degenerate classification.
    """
    expected = expected_shape_from_entry(metric_entry)
    if expected is None:
        return True, None
    for row in _as_rows(curve_rows):
        realized = realized_curve_shape(row.get("curve_points"))
        if realized is None or realized == expected:
            continue
        stratum = row.get("stratum")
        where = f" (stratum '{stratum}')" if stratum not in (None, "") and str(stratum) != "nan" else ""
        return False, (
            f"Realized curve shape '{realized}'{where} conflicts with the approved "
            f"expectation '{expected}' (CURVE-05). An approved ecological "
            "expectation is never overridden silently."
        )
    return True, None


def _curve_points_digest(value: Any) -> Any:
    """The seed points as a plain, order-preserving list of (x, y) pairs.

    Real curve rows carry the points as a nested DataFrame under ``curve_points``;
    json.dumps(default=str) would stringify that to an unstable repr, so normalize it
    here instead.
    """
    if value is None:
        return None
    to_dict = getattr(value, "to_dict", None)
    if to_dict is not None and hasattr(value, "columns"):
        try:
            rows = value.to_dict(orient="records")
        except TypeError:
            return None
    elif isinstance(value, (list, tuple)):
        rows = [r for r in value if isinstance(r, dict)]
    else:
        return None
    out = []
    for r in rows:
        x = r.get("metric_value", r.get("x"))
        y = r.get("index_score", r.get("y"))
        out.append([None if x is None else float(x), None if y is None else float(y)])
    return out


def _normalize_curve_rows(curve_rows: Any) -> list[dict]:
    """Stable subset of each curve row that defines the proposal's identity."""
    keep = (
        "stratum",
        "curve_status",
        "curve_form",
        "n_reference",
        "min_val",
        "q25",
        "median_val",
        "q75",
        "max_val",
        "iqr",
    )
    out = []
    for r in _as_rows(curve_rows):
        entry = {k: r.get(k) for k in keep if k in r}
        # The seed points ARE the proposal: a manual tweak (or a monotone -> optimum
        # reshape) that leaves the summary stats untouched must still force re-review.
        # The historical key here was "points", which no real row has, so every edit
        # silently kept its old fingerprint.
        points = r.get("curve_points", r.get("points"))
        digest = _curve_points_digest(points)
        if digest is not None:
            entry["points"] = digest
        out.append(entry)
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
            "key": "mapping",
            "label": "Function mapping confirmed",
            "ok": bool(s.get("mapping_confirmed")),
            # Mirrors the hard gate in views/publish.py so the requirement shows
            # up in the checklist instead of only as a failed publish click.
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
        {
            "key": "coverage",
            "label": "Every STAF function covered or documented as excluded",
            "ok": int(((s.get("coverage") or {}).get("missing")) or 0) == 0,
            # Mirrors the gate in library.publish_version so the shortfall shows up
            # in the checklist rather than only as a failed publish. Absent coverage
            # in the snapshot reads as ok: a run that has not built a bundle yet has
            # nothing to judge, and the publisher re-checks from the bundle anyway.
        },
    ]


def is_ready_to_publish(snapshot: dict) -> bool:
    return all(item["ok"] for item in readiness_checklist(snapshot))
