"""Turn the live StreamCurves session into publishable artifacts.

One place to build the DEEP bundle and the session payload from ``AppState`` so the
export screen (Finalize / Test in DEEP) and the Library tab (Publish) can't drift.
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
)
from views.state import AppState

DEFAULT_SOURCE_CITATION = "StreamCurves reference-curve development"


def run_snapshot(state: AppState) -> dict:
    """Snapshot of the current run for ``run_state.derive_stage_status`` /
    ``is_ready_to_publish``. Shared by the guided home and the Library publish gate
    so both read the same facts from AppState."""
    with reactive.isolate():
        region = state.region_of_applicability()
        meta = state.run_meta() or {}
        sc = state.easi_screening_sites()
        stage_status = state.run_stage_status() or {}
        data = state.data()
        curve_review = state.curve_review() or {}
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
    return {
        "has_region": region is not None and kind not in (None, "none"),
        "region_is_ecoregion": kind == "ecoregion",
        "region_kind": kind,
        "region_label": (region or {}).get("name") if region else None,
        "has_data_source": has_screening or n_candidates > 0 or data is not None,
        "n_candidates": n_candidates,
        "has_screening": has_screening,
        "n_retained": n_retained,
        "enriched": bool(data is not None and enr.get("status") == "done"),
        "n_enriched": int(enr.get("n_enriched") or 0),
        "curve_review": curve_review,
        "published": bool(pub.get("status") == "done"),
        "published_label": pub.get("label"),
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
    }
    if region:
        full_meta["region"] = region
        if region.get("kind") == "state":
            full_meta["stateCode"] = region.get("code") or ""
            full_meta["stateName"] = region.get("name") or ""
    if meta:
        full_meta.update({k: v for k, v in meta.items() if v is not None})
    return build_deep_assessment_bundle(curve_rows, mapping, metric_config, meta=full_meta)
