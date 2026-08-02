"""Turn the live StreamCurves session into publishable artifacts.

One place to build the DEEP bundle and the session payload from ``AppState`` so the
export screen (Finalize / Test in DEEP) and the Publish page can't drift.
"""

from __future__ import annotations

import pandas as pd
from shiny import reactive

from streamcurves import run_state as rs
from streamcurves import session_io as sio
from streamcurves.deep_export import (
    build_deep_assessment_bundle,
    deep_collect_curve_rows,
    deep_slug,
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
    """STAF function coverage the current session would publish, or None if it
    cannot build a bundle yet.

    Reads the same builder the publisher uses so the checklist and the publish gate
    can never disagree; a build failure (no finalized curve yet) is not a coverage
    verdict, so it reports None rather than a fake shortfall.
    """
    try:
        bundle = build_bundle_from_state(state)
    except Exception:  # noqa: BLE001 - nothing to judge until a curve is finalized
        return None
    return bundle.get("functionCoverage")


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
    }


def region_from_state(state: AppState) -> dict | None:
    with reactive.isolate():
        return state.region_of_applicability()


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

    curve_rows = deep_collect_curve_rows(completed)
    if not curve_rows:
        raise ValueError(
            "No finalized reference curves in this session. Complete at least one "
            "metric's Phase 4 curve first."
        )

    full_meta: dict = {
        "assessmentId": deep_slug(session_name or "spring-assessment"),
        "assessmentName": session_name or "Spring Assessment",
        "sourceCitation": DEFAULT_SOURCE_CITATION,
        "functionCoverageExceptions": exceptions,
    }
    if region:
        full_meta["region"] = region
        if region.get("kind") == "state":
            full_meta["stateCode"] = region.get("code") or ""
            full_meta["stateName"] = region.get("name") or ""
    if meta:
        full_meta.update({k: v for k, v in meta.items() if v is not None})
    return build_deep_assessment_bundle(curve_rows, mapping, metric_config, meta=full_meta)
