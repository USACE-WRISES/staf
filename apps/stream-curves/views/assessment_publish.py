"""Turn the live StreamCurves session into publishable artifacts.

One place to build the DEEP bundle and the session payload from ``AppState`` so the
export screen (Finalize / Test in DEEP) and the Publish page can't drift.
"""

from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path

import pandas as pd
from shiny import reactive

from streamcurves import easi_screening
from streamcurves import library as lib
from streamcurves import provenance as pv
from streamcurves import run_state as rs
from streamcurves import session_io as sio
from streamcurves.deep_export import (
    build_deep_assessment_bundle,
    deep_collect_curve_rows,
    deep_read_staf_crosswalk,
    deep_slug,
    function_coverage_quick,
    metrics_per_function_quick,
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
        curve_review = state.curve_review() or {}
        mapping = state.discipline_function_mapping()
        exceptions = state.function_coverage_exceptions() or []
        reference_build = state.reference_build()
    # the scope rule build_bundle_from_state applies, so a curve the review took
    # out covers nothing here either
    in_scope = {mk: cm for mk, cm in completed.items()
                if mk not in curve_review or rs.is_in_scope(curve_review[mk])}
    try:
        return function_coverage_quick(
            in_scope, mapping, exceptions,
            always_covered=_reference_function_ids(reference_build, mapping, completed),
            exclude_pairs=_not_selected(reference_build))
    except Exception:  # noqa: BLE001 - malformed exceptions are not a coverage verdict
        return None


def _reference_function_ids(reference_build, mapping, built) -> list[str]:
    """Functions covered by the curves a pressure-screen build scores without
    the session having fitted them: fixed criteria, carried-forward curves and
    the curves from a rung above the hierarchy. They join the bundle outside
    the session's own fits (pressure_evidence.apply_reference_build). Empty for
    a legacy session, so nothing changes there."""
    if not reference_build:
        return []
    from streamcurves import pressure_evidence as _pe
    return _pe.reference_function_ids(reference_build, mapping, built=built or ())


def _not_selected(reference_build) -> set:
    """SELECT-04's supported-not-selected pairs, which the bundle leaves out."""
    if not reference_build:
        return set()
    from streamcurves import pressure_evidence as _pe
    return _pe.not_selected_pairs(reference_build)


def pending_standing_decisions(state: AppState) -> dict:
    """What an opened build's standing decisions left for the owner to confirm:
    ``{"approvals": [...], "exceptions": [...]}`` (SELECT-01 approvals and COV-01
    documented gaps whose recorder is still the pending marker). Promote confirms
    them under the promoting owner; the workspace publish does the same."""
    from streamcurves import decisions as _dec
    with reactive.isolate():
        origin = state.assessment_source() or {}
        exceptions = state.function_coverage_exceptions() or []
    return {"approvals": _dec.pending_approvals(origin.get("portfolio_approvals")),
            "exceptions": _dec.pending_exceptions(exceptions)}


def pending_decisions_text(pending: dict) -> str:
    """The sentence over the confirmation checkbox."""
    names = {str(f.get("id")): f.get("name") or str(f.get("id"))
             for f in deep_read_staf_crosswalk()}
    parts = []
    if pending.get("approvals"):
        fns = ", ".join(names.get(str(a.get("functionId")), str(a.get("functionId")))
                        for a in pending["approvals"])
        parts.append(f"the portfolio approval of {fns}")
    if pending.get("exceptions"):
        fns = ", ".join(names.get(str(e.get("functionId")), str(e.get("functionId")))
                        for e in pending["exceptions"])
        n = len(pending["exceptions"])
        parts.append(f"{n} documented gap{'' if n == 1 else 's'} ({fns})")
    return ("The build's standing decisions left " + " and ".join(parts) + " for the owner "
            "to confirm. Publishing records them under your name, as the Region builder's "
            "publish does.")


def portfolio_approval_needed(state: AppState) -> list[dict]:
    """Functions this session would publish more metrics on than the portfolio maximum
    allows and that no recorded approval covers: ``[{functionId, functionName, nMetrics}]``.

    SELECT-01 (``library.publish_version``) refuses a publish without a named approval of
    each, which the batch runner takes as ``--approve-portfolio`` and an opened build
    carries on its origin. The Publish page asks the publisher for the rest instead of
    letting the click fail on a gate error, so this judges the same thing from the mapping
    walk and the fixed-criteria metrics rather than building the bundle on every render.
    The publish itself re-reads the real bundle (``library.functions_over_metric_limit``),
    so a miscount here can only ask for an approval that turns out to be unnecessary.
    """
    from streamcurves import methodology
    from streamcurves import pressure_evidence as _pe
    with reactive.isolate():
        completed = state.completed_metrics() or {}
        curve_review = state.curve_review() or {}
        mapping = state.discipline_function_mapping()
        reference_build = state.reference_build()
        origin = state.assessment_source() or {}
    # the same scope rule the bundle is built under, or the page asks for an approval
    # of a function whose third metric the review already took out
    out_of_scope = {mk for mk, entry in curve_review.items() if not rs.is_in_scope(entry)}
    counts = metrics_per_function_quick(
        {mk: cm for mk, cm in completed.items() if mk not in out_of_scope}, mapping,
        extra=(_pe.reference_metric_counts(reference_build, mapping, built=completed)
               if reference_build else None),
        exclude_pairs=_not_selected(reference_build))
    if not counts:
        return []
    limit = int(methodology.threshold(
        "metric_portfolio.default_maximum_metrics_per_function"))
    approved = {str(a.get("functionId"))
                for a in (origin.get("portfolio_approvals") or [])
                if a.get("functionId") and a.get("approvedBy")}
    names = {str(f.get("id")): f.get("name") or str(f.get("id"))
             for f in deep_read_staf_crosswalk()}
    return [{"functionId": fid, "functionName": names.get(fid, fid), "nMetrics": n}
            for fid, n in sorted(counts.items())
            if n > limit and fid not in approved]


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
        screening_criteria = state.easi_screening_criteria()
        reference_build = state.reference_build()
        completed = state.completed_metrics() or {}
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
            mapping, metric_config, coverage_exceptions,
            always_covered=_reference_function_ids(reference_build, mapping, completed),
            exclude_pairs=_not_selected(reference_build)))
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

    from streamcurves import pressure_evidence as _pe
    reference_text = _pe.reference_summary_text(
        _pe.reference_summary(reference_build, built=completed))
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
        # An all-sites screen retains everyone: fine for exploration, never a
        # reference set the library accepts (rule REF-03).
        "screening_publishable": easi_screening.screening_publishable(screening_criteria),
        "n_retained": n_retained,
        # Curves fitted on a comparable pool of a parent ecoregion (REF-05). An
        # ecoregion can retain none of its own candidates and still rest entirely on
        # screened least-disturbed stations, so the readiness check counts these too.
        "n_borrowed_curves": int(
            ((reference_build or {}).get("referenceMethod") or {}).get("nCurvesBorrowed") or 0),
        # "attention" still means a build happened; it flags missing diagnostics,
        # not a missing dataset. Reading it as not-enriched would block stages 4
        # to 6 and show "Build a dataset first" over a complete dataset.
        "enriched": bool(data is not None and enr.get("status") in ("done", "attention")),
        "n_enriched": int(enr.get("n_enriched") or 0),
        "curve_review": curve_review,
        # A staged run's session records its staged publish into the run's own
        # library; opened from the Region builder it is not published anywhere.
        "published": bool(pub.get("status") == "done")
                     and origin.get("kind") not in ("staged", "run"),
        # the curves this version scores that the session did not fit
        "reference_text": reference_text,
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


@functools.lru_cache(maxsize=8)
def _bundle_at(path: str, mtime: float) -> dict | None:
    # cached by path and modification time: the publish page asks on every render
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def origin_bundle(origin: dict | None) -> dict | None:
    """The bundle the opened assessment was loaded from: the staged build's, or
    the library version's. None when there is none on this machine."""
    origin = origin or {}
    path = None
    if origin.get("kind") == "staged" and origin.get("staged_path"):
        path = Path(origin["staged_path"]) / lib.BUNDLE_FILE
    elif origin.get("kind") == "library" and origin.get("library_id") and origin.get("version"):
        try:
            path = lib.version_dir(origin["library_id"], int(origin["version"])) / lib.BUNDLE_FILE
        except Exception:  # noqa: BLE001 - an unreadable library is no origin
            path = None
    if path is None or not path.is_file():
        return None
    return _bundle_at(str(path), path.stat().st_mtime)


def unchanged_reviews(state: AppState) -> set | None:
    """Metrics whose review entry is exactly what it was when the assessment was
    opened, or None when the opening recorded no baseline (nothing to compare)."""
    with reactive.isolate():
        origin = state.assessment_source() or {}
        curve_review = state.curve_review() or {}
    base = (origin.get("baselines") or {}).get("curve_fingerprints")
    if not isinstance(base, dict):
        return None
    return {str(m) for m, entry in curve_review.items() if base.get(str(m)) == _digest16(entry)}


def fitted_annotation_source(state: AppState, reference_build, fitted) -> dict:
    """Where the fitted curves' build-only annotations come from: the session,
    else the bundle the assessment was opened from."""
    stored = (reference_build or {}).get("fittedAnnotations")
    if stored:
        return dict(stored)
    with reactive.isolate():
        origin = state.assessment_source()
    from streamcurves import pressure_evidence as _pe
    return _pe.fitted_annotations_of(origin_bundle(origin), fitted)


def build_bundle_from_state(state: AppState, meta: dict | None = None) -> dict:
    """Build the DEEP bundle from the finalized curves in state.

    Raises ``ValueError`` when no metric has a complete Phase 4 curve yet. Region of
    applicability rides into the bundle automatically; state-kind regions also populate
    ``stateCode`` / ``stateName``. Any keys in ``meta`` override the defaults.
    """
    with reactive.isolate():
        completed = state.completed_metrics() or {}
        curve_review = state.curve_review() or {}
        mapping = state.discipline_function_mapping()
        metric_config = state.metric_config() or {}
        session_name = state.session_name()
        region = state.region_of_applicability()
        exceptions = state.function_coverage_exceptions() or []
        predictor_config = state.predictor_config() or {}
        reference_build = state.reference_build()

    # A curve a reviewer removed, or one still awaiting review, is not published.
    # `completed_metrics` holds every built curve on purpose (regional_agent.session_fields
    # keeps the flagged ones so a reviewer can see them), so scope has to be applied here
    # the way the headless path applies it to `intended_rows` (regional_agent.assemble).
    # Only a metric the review actually judged is dropped: a session with no curve_review
    # at all (the Advanced path) publishes everything it holds, as it always did.
    out_of_scope = {mk for mk, entry in curve_review.items() if not rs.is_in_scope(entry)}
    completed = {mk: cm for mk, cm in completed.items() if mk not in out_of_scope}
    if curve_review:
        # in the order the build exports them (run_state.intended_metrics_for_publish
        # sorts), so an untouched republish is the same content, entry for entry
        completed = {mk: completed[mk] for mk in sorted(completed)}

    curve_rows = deep_collect_curve_rows(completed)

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
    # A pressure-screen build (methodology 0.12) keeps its reference statement
    # through an interactive republish: the fixed-criteria metrics, each curve's
    # reference support, the withheld list. A legacy session passes through.
    from streamcurves import pressure_evidence as _pe
    curve_rows, mapping, metric_config = _pe.apply_reference_build(
        reference_build, curve_rows, mapping, metric_config, full_meta)
    # judged after the reference build is folded in: a version whose in-scope
    # curves are all carried forward (or fixed) publishes too
    if not curve_rows:
        raise ValueError(
            "No finalized reference curves in this session. Complete at least one "
            "metric's Phase 4 curve first."
        )
    bundle = build_deep_assessment_bundle(curve_rows, mapping, metric_config, meta=full_meta)
    # What only the build could say about the curves it fitted (the reference
    # sample, the caveats, the confidence) rides again on every curve that is
    # still exactly the one it describes and whose review nobody changed.
    stored = fitted_annotation_source(state, reference_build, completed)
    if stored:
        _pe.carry_fitted_annotations(bundle, stored, metrics=unchanged_reviews(state))
    return bundle
