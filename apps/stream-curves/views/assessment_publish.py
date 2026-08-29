"""Turn the live StreamCurves session into publishable artifacts.

One place to build the DEEP bundle and the session payload from ``AppState`` so the
export screen (Finalize / Test in DEEP) and the Publish page can't drift.
"""

from __future__ import annotations

import hashlib
import json

import pandas as pd
from shiny import reactive

from streamcurves import provenance as pv
from streamcurves import run_state as rs
from streamcurves import session_io as sio
from streamcurves.deep_export import (
    build_deep_assessment_bundle,
    deep_collect_curve_rows,
    deep_slug,
    function_coverage_quick,
    uncovered_functions_from_mapping,
)
from views.state import AppState
from views.summary_state import eligible_summary_metrics, get_metric_allowed_strats


def _has_rows(frame) -> bool:
    return frame is not None and len(frame) > 0

DEFAULT_SOURCE_CITATION = "StreamCurves reference-curve development"


def region_label(region: dict | None) -> str:
    """Short human label for a region_of_applicability dict (shared by the
    Open dialog and the Publish page)."""
    if not region or region.get("kind") == "none":
        return "No region set"
    kind = region.get("kind")
    name = region.get("name") or region.get("code") or ""
    if kind == "ecoregion":
        return f"{name} (L3 {region.get('code')})"
    if kind == "state":
        return f"{name} (state)"
    if kind == "polygon":
        return "Custom drawn area"
    return str(name)


def coverage_from_state(state: AppState) -> dict | None:
    """STAF function coverage the current session would publish, or None while
    there is nothing to judge yet.

    Computed by the quick mapping walk (deep_export.function_coverage_quick)
    rather than a full bundle build: run_snapshot calls this on every workflow-
    strip render, and building the whole DEEP bundle each time is what made
    stage clicks and the Validate/Publish pages feel seconds slow. The quick
    path mirrors the bundle's coverage contract (see its docstring for the one
    documented divergence); the publish gate in library.publish_version still
    judges the real bundle, so the two can never disagree where it counts.
    """
    with reactive.isolate():
        completed = state.completed_metrics() or {}
        mapping = state.discipline_function_mapping()
        exceptions = state.function_coverage_exceptions() or []
    try:
        return function_coverage_quick(completed, mapping, exceptions)
    except Exception:  # noqa: BLE001 - malformed exceptions are not a coverage verdict
        return None


def run_snapshot(state: AppState) -> dict:
    """Snapshot of the current run for ``run_state.derive_stage_status`` /
    ``is_ready_to_publish``. Shared by the stage banner and the Publish page gate
    so both read the same facts from AppState."""
    with reactive.isolate():
        region = state.region_of_applicability()
        meta = state.run_meta() or {}
        sc = state.easi_screening_sites()
        stage_status = state.run_stage_status() or {}
        data = state.data()
        curve_review = state.curve_review() or {}
        mapping = state.discipline_function_mapping()
        metric_config = state.metric_config() or {}
        mapping_confirmed = bool(state.discipline_function_mapping_confirmed())
        coverage_exceptions = state.function_coverage_exceptions() or []
        layer1 = state.all_layer1_results() or {}
        ranking = state.phase2_ranking()
        validation_records = state.validation_records() or []
        origin = state.assessment_source() or {}
        screening_skipped = bool(state.screening_skipped())
    kind = (region or {}).get("kind") if region else None
    n_candidates = int(meta.get("n_candidates") or 0)
    has_screening = sc is not None and not (hasattr(sc, "empty") and sc.empty)
    n_retained = 0
    if has_screening:
        df = sc if hasattr(sc, "columns") else pd.DataFrame(sc)
        if "final_decision" in df.columns:
            n_retained = int((df["final_decision"] == "retained").sum())
        n_candidates = max(n_candidates, int(len(df)))
    enr = stage_status.get("enrichment_build") or {}
    pub = stage_status.get("publish") or {}
    # Mapping-level coverage for the Refine & map stage: judged from the editable
    # mapping, so it exists before any curve is finalized (unlike "coverage"
    # below, which is bundle-based and None until then). No metrics yet -> 0:
    # the stage is blocked on the build anyway, not on unmapped functions.
    n_unmapped = (
        len(uncovered_functions_from_mapping(
            mapping, metric_config, coverage_exceptions))
        if metric_config
        else 0
    )
    # Metrics that have a stratification to screen but no screening result. A
    # metric with nothing to screen must not count: the strip would then report
    # missing work on an assessment where none is possible.
    n_missing_diagnostics = sum(
        1
        for metric in eligible_summary_metrics(metric_config)
        if get_metric_allowed_strats(state, metric)
        and not _has_rows(layer1.get(metric))
    )
    if sum(1 for df in layer1.values() if _has_rows(df)) >= 2 and not _has_rows(ranking):
        n_missing_diagnostics += 1

    return {
        "has_region": region is not None and kind not in (None, "none"),
        "region_is_ecoregion": kind == "ecoregion",
        "region_kind": kind,
        "region_label": (region or {}).get("name") if region else None,
        "has_data_source": has_screening or n_candidates > 0 or data is not None,
        "n_candidates": n_candidates,
        "has_screening": has_screening,
        # Skip only counts while no real screening table exists; running or
        # importing a screen supersedes the skip even if the flag lingers.
        "screening_skipped": screening_skipped and not has_screening,
        "n_retained": n_retained,
        # "attention" still means a build happened; it flags missing diagnostics,
        # not a missing dataset. Reading it as not-enriched would block stages 4
        # to 6 and show "Build a dataset first" over a complete dataset.
        "enriched": bool(data is not None and enr.get("status") in ("done", "attention")),
        "n_enriched": int(enr.get("n_enriched") or 0),
        "curve_review": curve_review,
        "published": bool(pub.get("status") == "done"),
        "published_label": pub.get("label"),
        "coverage": coverage_from_state(state),
        "mapping_confirmed": mapping_confirmed,
        "n_unmapped_functions": n_unmapped,
        "n_missing_diagnostics": n_missing_diagnostics,
        # The Validate stage: a published version is loaded (the origin points
        # into the library), and how many validation records it carries.
        "has_validation_target": origin.get("kind") == "library",
        "n_validation_records": len(validation_records),
    }


def region_from_state(state: AppState) -> dict | None:
    with reactive.isolate():
        return state.region_of_applicability()


# --------------------------------------------------------------------------- #
# Assessment origin (provenance carry)
# --------------------------------------------------------------------------- #
def _digest16(obj) -> str | None:
    """Short stable digest of a state value, None-safe. Coarse by design: it
    exists to say "this moved", never to verify what it became."""
    if obj is None:
        return None
    try:
        text = (obj.to_json(orient="split") if hasattr(obj, "to_json")
                else json.dumps(obj, sort_keys=True, default=str))
    except Exception:  # noqa: BLE001 - a digest failure must not block an open
        text = str(obj)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def origin_baselines(state: AppState) -> dict:
    """Change-detection baselines captured when an assessment is opened, diffed
    at publish time by ``provenance.revision_changes``."""
    with reactive.isolate():
        fingerprint = state.data_fingerprint()
        mapping = state.discipline_function_mapping()
        region = state.region_of_applicability()
        curve_review = state.curve_review() or {}
    return {
        "data_fingerprint": fingerprint,
        "mapping_digest": _digest16(mapping),
        "region_code": (region or {}).get("code") if region else None,
        "curve_fingerprints": {str(m): _digest16(entry)
                               for m, entry in curve_review.items()},
    }


def build_origin(state: AppState, *, kind: str, library_id: str | None = None,
                 version: int | None = None, staged_path: str | None = None,
                 run_dir: str | None = None, content_digest: str | None = None,
                 portfolio_approvals: list | None = None,
                 loaded_at: str | None = None) -> dict:
    """The ``assessment_source`` record for a just-restored assessment. Called
    AFTER the restore so the baselines describe what was actually loaded.

    ``portfolio_approvals`` are the origin's recorded SELECT-01 approvals
    (staged or published meta.json): the publish form builds fresh meta, so
    without carrying these an opened agent build with a >2-metric function
    would be refused by the very gate its own build already satisfied."""
    return {
        "kind": kind,
        "library_id": library_id,
        "version": int(version) if version else None,
        "staged_path": str(staged_path) if staged_path else None,
        "run_dir": str(run_dir) if run_dir else None,
        "content_digest": content_digest,
        "portfolio_approvals": list(portfolio_approvals or []) or None,
        "loaded_at": loaded_at,
        "baselines": origin_baselines(state),
    }


def origin_changes(state: AppState, origin: dict | None, *,
                   content_digest: str | None = None) -> dict:
    """What moved since the origin was captured, in the coarse flags an
    ``interactiveRevisions`` entry records."""
    now = origin_baselines(state)
    return pv.revision_changes(
        origin, content_digest=content_digest,
        data_fingerprint=now["data_fingerprint"],
        mapping_digest=now["mapping_digest"],
        region_code=now["region_code"],
        curve_fingerprints=now["curve_fingerprints"])


def default_assessment_id(state: AppState) -> str:
    with reactive.isolate():
        name = state.session_name()
    return deep_slug(name or "spring-assessment")


def session_payload_from_state(state: AppState) -> dict:
    """Materialize the full session payload (same content as Save > session
    download) so a library version can round-trip back into the app."""
    with reactive.isolate():
        session_name = state.session_name()
        fields = {name: state.get(name) for name in sio.SESSION_FIELDS}
    return sio.dump_session_fields(fields, session_name=session_name)


def build_bundle_from_state(state: AppState, meta: dict | None = None) -> dict:
    """Build the DEEP bundle from the finalized curves in state.

    Raises ``ValueError`` when no metric has a complete Phase 4 curve yet. Region of
    applicability rides into the bundle automatically; state-kind regions also populate
    ``stateCode`` / ``stateName``. Any keys in ``meta`` override the defaults.
    """
    with reactive.isolate():
        completed = state.completed_metrics() or {}
        mapping = state.discipline_function_mapping()
        metric_config = state.metric_config() or {}
        session_name = state.session_name()
        region = state.region_of_applicability()
        exceptions = state.function_coverage_exceptions() or []
        predictor_config = state.predictor_config() or {}

    curve_rows = deep_collect_curve_rows(completed)
    if not curve_rows:
        raise ValueError(
            "No finalized reference curves in this session. Complete at least one "
            "metric's Phase 4 curve first."
        )

    from streamcurves import site_engine_source as _ses
    full_meta: dict = {
        "assessmentId": deep_slug(session_name or "spring-assessment"),
        "assessmentName": session_name or "Spring Assessment",
        "sourceCitation": DEFAULT_SOURCE_CITATION,
        "functionCoverageExceptions": exceptions,
        # Derived from the predictors actually configured, never user-chosen;
        # deep_export omits the StreamCat default.
        "predictorSource": _ses.predictor_source_of(list(predictor_config)),
    }
    if region:
        full_meta["region"] = region
        if region.get("kind") == "state":
            full_meta["stateCode"] = region.get("code") or ""
            full_meta["stateName"] = region.get("name") or ""
    if meta:
        full_meta.update({k: v for k, v in meta.items() if v is not None})
    return build_deep_assessment_bundle(curve_rows, mapping, metric_config, meta=full_meta)
