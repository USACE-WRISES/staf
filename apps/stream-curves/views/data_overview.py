"""Data & Setup tab — core port of app/modules/mod_data_overview.R.

Covers the Region & data wizard (the map-first import wizard), the opened
project's workspace (Workbook, Function mapping, Metric redundancy, Pre-run
validation; panels stay mounted and are shown or hidden by a reactive <style>
tag, as the R app's conditionalPanel did), and the three ways state is loaded,
kept as module-level functions so the project controller (views/project.py) and
the Region builder share them: ``restore_session`` (a session payload),
``apply_workbook_bundle`` (an xlsx workbook) and ``seed_origin`` (where an
opened assessment came from). Opening, saving and closing projects live in the
project controller and its start page.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timezone

import pandas as pd
from shiny import module, reactive, render, req, ui

from streamcurves import overlap
from streamcurves import owner_curves as oc
from streamcurves import region_build as rb
from streamcurves import rules_view
from streamcurves import run_state as rs
from streamcurves import session_io as sio
from streamcurves import workbook as wb
from streamcurves.cleaning import clean_data
from streamcurves.derive import derive_variables
from streamcurves.precheck import (
    precheck_summary,
    precheck_warning_rows,
    run_metric_precheck,
)
from views import assessment_publish as ap
from views import state as st
from views.discipline_map import discipline_map_server, discipline_map_ui
from views.import_map import import_map_server, import_map_ui
from views.state import AppState, deep_copy_value, empty_phase2_settings
from views.theme import bi
from views.uihelpers import linkify_rule_ids, status_badge
from views.workbook_grid import workbook_grid_server, workbook_grid_ui

logger = logging.getLogger("streamcurves")

# Workspace sections (not sequential steps): the panels of the Refine & map
# stage, switched by the workflow strip's section chips. Canonical list lives
# in run_state.STAGE_SECTIONS so the strip, this view, and tests share one
# vocabulary. The mapping section still hosts the Discipline > Function >
# Metric editor.
WORKSPACE_STEPS = [
    {"value": v, "label": label} for v, label in rs.STAGE_SECTIONS["refine_map"]
]


def _sanitize_file_stem(name: str | None, fallback: str | None = None) -> str:
    stem = (name or "").strip()
    if not stem and fallback:
        stem = re.sub(r"\.(xlsx|json|rds)$", "", str(fallback), flags=re.I)
    if not stem:
        stem = f"streamcurves_session_{date.today():%Y%m%d}"
    stem = re.sub(r"[^\w\- ]+", "_", stem).strip().replace(" ", "_")
    return stem or "streamcurves_session"


def _default_session_name(name: str | None, upload_filename: str | None) -> str:
    if name and name.strip():
        return name.strip()
    if upload_filename:
        return re.sub(r"\.(xlsx|json|rds)$", "", str(upload_filename), flags=re.I)
    return f"Session {date.today():%Y-%m-%d}"


# --------------------------------------------------------------------------- #
# Loading into the session: shared by the project controller (views/project.py),
# the Region builder's staged open and this module's own views.
# --------------------------------------------------------------------------- #


def apply_workbook_bundle(state: AppState, bundle: dict, source_name: str) -> None:
    """Load a read workbook (workbook.read_input_workbook) into the session (R:1973-2073)."""
    p = ui.Progress(min=0, max=5)
    try:
        p.set(value=0, message="Loading Workbook", detail="Loading workbook tables...")
        p.set(value=1, message="Loading Workbook", detail="Cleaning uploaded data...")
        cleaned, _ = clean_data(
            bundle["raw_data"],
            bundle["metric_config"],
            bundle["strat_config"],
            bundle["factor_recode_config"],
        )
        p.set(value=2, message="Loading Workbook", detail="Deriving analysis variables...")
        derived = derive_variables(
            cleaned,
            bundle["factor_recode_config"],
            bundle["predictor_config"],
            bundle["strat_config"],
        )
        p.set(value=3, message="Loading Workbook", detail="Running pre-run validation...")
        precheck = run_metric_precheck(derived, bundle["metric_config"])

        p.set(value=4, message="Loading Workbook", detail="Applying dataset to the app...")
        st.reset_all_analysis(state)

        state.metric_config.set(bundle["metric_config"])
        state.strat_config.set(bundle["strat_config"])
        state.predictor_config.set(bundle["predictor_config"])
        state.factor_recode_config.set(bundle["factor_recode_config"])
        state.input_metadata.set(bundle.get("metadata"))
        state.site_mask_config.set(bundle.get("site_mask_config"))
        state.data.set(derived)
        state.precheck_df.set(precheck)
        state.data_source.set("upload")
        state.upload_filename.set(source_name)
        state.data_fingerprint.set(
            hashlib.md5(
                pd.util.hash_pandas_object(derived, index=True).values.tobytes()
            ).hexdigest()
        )
        metric_keys = list(bundle["metric_config"].keys())
        with reactive.isolate():
            current_metric = state.current_metric()
        state.current_metric.set(metric_keys[0] if metric_keys else current_metric)
        state.config_version.set(0)

        mapping = bundle.get("discipline_function_mapping")
        if mapping is not None:
            state.discipline_function_mapping.set(mapping)
            state.discipline_function_mapping_confirmed.set(
                bool(bundle.get("mapping_covers_all_metrics"))
            )
            state.mapping_user_touched.set(True)
            state.workbook_provided_mapping.set(True)
        else:
            state.discipline_function_mapping.set(None)
            state.discipline_function_mapping_confirmed.set(False)
            state.mapping_user_touched.set(False)
            state.workbook_provided_mapping.set(False)
        state.startup_discipline_function_mapping.set(None)

        state.app_data_loaded.set(True)
        p.set(value=5, message="Loading Workbook", detail="Done.")

        ui.notification_show(
            f"Loaded {len(derived)} rows x {derived.shape[1]} cols from {source_name}",
            type="message",
            duration=5,
        )
    finally:
        p.close()


def restore_session(state: AppState, payload: dict, source_name: str | None = None, *,
                    decisions: str = "region", fields: dict | None = None) -> str:
    """Restore a session payload into the session (R:2841-2934); returns its name.

    ``decisions`` says how the owner's REF-15 curve decisions meet the region's record:

    * ``"region"`` (a staged run, a library version): the region's record, when it keeps
      one, is the standing record, so a decision saved while this session was closed
      applies here and one undone since does not;
    * ``"merge"`` (a project file, possibly one a reviewer revised and sent back): the
      project's decisions stand and the region's record only adds to them. A decision the
      record lacks is kept and reported, and a publish records it for the region.

    ``fields`` takes the payload already decoded (session_io.decode_session_fields), which a
    caller does off the event loop: decoding a large session takes a couple of seconds.
    """
    if fields is None:
        fields = sio.decode_session_fields(payload)

    st.reset_all_analysis(state)

    state.data.set(fields.get("data"))
    # precheck_df is restored further down: recomputing it needs metric_config,
    # which is not set until below.
    state.data_source.set(fields.get("data_source") or "session_file")
    state.data_fingerprint.set(fields.get("data_fingerprint"))
    state.upload_filename.set(fields.get("upload_filename"))
    state.site_mask_config.set(fields.get("site_mask_config"))

    with reactive.isolate():
        startup_mc = state.startup_metric_config()
        startup_sc = state.startup_strat_config()
        startup_pc = state.startup_predictor_config()
        startup_frc = state.startup_factor_recode_config()
        startup_oc = state.startup_output_config()
        startup_ver = state.startup_config_version()
    state.metric_config.set(fields.get("metric_config") or deep_copy_value(startup_mc))
    state.strat_config.set(fields.get("strat_config") or deep_copy_value(startup_sc))
    state.predictor_config.set(fields.get("predictor_config") or deep_copy_value(startup_pc))
    state.factor_recode_config.set(
        fields.get("factor_recode_config") or deep_copy_value(startup_frc)
    )
    if fields.get("output_config"):
        merged = deep_copy_value(startup_oc) or {}
        merged.update(fields["output_config"])
        state.output_config.set(merged)
    else:
        state.output_config.set(deep_copy_value(startup_oc))
    state.config_version.set(
        fields.get("config_version") if fields.get("config_version") is not None else startup_ver or 0
    )

    # Workbook tables. Sessions written headlessly (the regional agent, the
    # SQT migration) never had a workbook to save, so every published
    # library assessment carries input_metadata: null -- which left the
    # whole Workbook panel reading "No data loaded." over a perfectly good
    # dataset, and left Apply a silent no-op. Rebuild them from the configs
    # we just restored; tables_from_configs keeps each metric's real
    # settings, so this is a faithful reconstruction rather than defaults.
    restored_tables = fields.get("input_metadata")
    if not restored_tables and fields.get("data") is not None:
        with reactive.isolate():
            restored_tables = wb.tables_from_configs(
                state.data(),
                state.metric_config(),
                state.predictor_config(),
                state.strat_config(),
                state.factor_recode_config(),
            )
    state.input_metadata.set(restored_tables)

    # Pre-run validation. Same gap as input_metadata above: headless sessions
    # carry precheck_df: null, and the panel's only guard was a req() that
    # renders nothing -- so a never-computed precheck looked exactly like a
    # clean one, which would hide real failures on another dataset. Recompute
    # rather than leave it blank. Wrapped: a QA table is never worth aborting
    # an Open over, and the panel says "not run" if this fails.
    restored_precheck = fields.get("precheck_df")
    if restored_precheck is None and fields.get("data") is not None:
        with reactive.isolate():
            try:
                restored_precheck = run_metric_precheck(
                    state.data(), state.metric_config()
                )
            except Exception:  # noqa: BLE001
                logger.warning("Precheck recompute on restore failed", exc_info=True)
    state.precheck_df.set(restored_precheck)

    state.phase1_candidates.set(fields.get("phase1_candidates") or {})
    state.all_layer1_results.set(fields.get("all_layer1_results") or {})
    state.all_layer2_results.set(fields.get("all_layer2_results") or {})
    state.phase2_ranking.set(fields.get("phase2_ranking"))
    state.cross_metric_consistency.set(fields.get("cross_metric_consistency"))
    state.metric_redundancy.set(fields.get("metric_redundancy"))
    state.phase2_settings.set(fields.get("phase2_settings") or empty_phase2_settings())
    state.phase2_metric_overrides.set(fields.get("phase2_metric_overrides") or {})
    # A completed metric whose stored phase4_signature says decision_type
    # "none" was built unstratified, but get_metric_curve_stratification
    # falls back to the phase-1 screening recommendation whenever there is no
    # stored choice. Once a published session carries screening results that
    # fallback recomputes a "single" signature, it stops matching the stored
    # one, and every curve in a completed assessment renders as "not current,
    # recompute required". Pin the choice the curves were actually built with.
    restored_curve_strat = dict(fields.get("curve_stratification") or {})
    for metric, entry in (fields.get("completed_metrics") or {}).items():
        signature = (entry or {}).get("phase4_signature") or {}
        if metric not in restored_curve_strat and signature.get("decision_type") == "none":
            restored_curve_strat[metric] = "none"
    state.curve_stratification.set(restored_curve_strat)
    state.summary_available_overrides.set(fields.get("summary_available_overrides") or {})
    state.summary_edit_notes.set(fields.get("summary_edit_notes") or {})
    state.phase3_verification.set(fields.get("phase3_verification") or {})
    state.metric_phase_cache.set(fields.get("metric_phase_cache") or {})
    state.stratum_results.set(fields.get("stratum_results") or {})
    state.completed_metrics.set(fields.get("completed_metrics") or {})
    state.decision_log.set(
        fields.get("decision_log") if fields.get("decision_log") is not None else pd.DataFrame()
    )
    state.custom_groupings.set(fields.get("custom_groupings") or {})
    state.custom_grouping_counter.set(fields.get("custom_grouping_counter") or {})
    state.cross_sections.set(fields.get("cross_sections") or {})
    state.column_sources.set(fields.get("column_sources") or {})
    state.column_functions.set(fields.get("column_functions") or {})
    state.region_of_applicability.set(fields.get("region_of_applicability"))
    state.candidate_sites.set(fields.get("candidate_sites"))
    state.easi_screening_sites.set(fields.get("easi_screening_sites"))
    state.easi_screening_metrics.set(fields.get("easi_screening_metrics"))
    state.easi_screening_criteria.set(fields.get("easi_screening_criteria"))
    state.run_meta.set(fields.get("run_meta"))
    state.run_stage_status.set(fields.get("run_stage_status") or {})
    state.curve_review.set(fields.get("curve_review") or {})
    state.screening_run.set(fields.get("screening_run"))
    state.site_exclusions.set(fields.get("site_exclusions") or [])
    state.screening_skipped.set(bool(fields.get("screening_skipped")))
    state.validation_records.set(fields.get("validation_records") or [])
    # Origin + carried provenance: restored when a saved draft recorded them,
    # cleared otherwise so a fresh open never wears a stale origin. Open
    # paths that KNOW their origin (library picker, Region builder's staged
    # open) re-seed both right after this restore returns.
    state.assessment_source.set(fields.get("assessment_source"))
    state.source_provenance.set(fields.get("source_provenance"))
    # Standing-decision opt-ins: validated against the live policy, so a
    # renamed or newly-standing entry falls away instead of riding along.
    kept, dropped = rules_view.validate_selections(
        fields.get("rule_selections") or [])
    if dropped:
        logger.info("restore: dropped rule selections %s", dropped)
    state.rule_selections.set(kept)
    # Absent in every session built before methodology 0.12 -> None, which
    # reads as a legacy build (no fixed-criteria metrics to carry).
    state.reference_build.set(fields.get("reference_build"))
    # An unbuilt wizard (projects saved before Build dataset); None otherwise.
    state.wizard_draft.set(fields.get("wizard_draft") if fields.get("data") is None else None)
    # The owner's curve decisions (REF-15): how they meet the region's record
    # depends on what is being opened (see ``decisions`` above); the session's
    # copy still carries what its build computed.
    session_decisions = list(fields.get("owner_curve_decisions") or [])
    region_dir = (rb.region_run_dir(fields.get("region_of_applicability"))
                  if fields.get("reference_build") else None)
    record = oc.standing(region_dir)
    if decisions == "merge":
        chosen = oc.combine(session_decisions, record or [])
        withdrawn = []
        held = {d.get("id") for d in record or []}
        unrecorded = [d for d in session_decisions
                      if record is not None and d.get("id") not in held
                      and not str(d.get("id") or "").startswith(oc.FLAG_PREFIX)]
    else:
        chosen, withdrawn = oc.restore(session_decisions, record)
        unrecorded = []
    state.owner_curve_decisions.set(chosen)
    state.candidate_register.set(fields.get("candidate_register"))
    added = len({d["id"] for d in chosen} - {d.get("id") for d in session_decisions})
    if added:
        ui.notification_show(
            f"{added} curve decision{'' if added == 1 else 's'} saved for this region "
            f"{'is' if added == 1 else 'are'} applied here, as {'it' if added == 1 else 'they'}"
            " will be to the next build.", type="message", duration=8)
    if withdrawn:
        n = len(withdrawn)
        ui.notification_show(
            f"{n} curve decision{'' if n == 1 else 's'} this session applied "
            f"{'was' if n == 1 else 'were'} undone for this region since, so "
            f"{'it does' if n == 1 else 'they do'} not apply here.",
            type="message", duration=10)
    if unrecorded:
        n = len(unrecorded)
        ui.notification_show(
            f"This project carries {n} curve decision{'' if n == 1 else 's'} the region's "
            f"record does not hold. {'It applies' if n == 1 else 'They apply'} here, and "
            "publishing records them for the region.", type="message", duration=10)
    # Absent in a session written before gaps had to be justified -> no
    # exceptions, which is the honest reading of that file. A gap recorded with
    # a curve decision that no longer stands goes with it.
    state.function_coverage_exceptions.set(
        oc.live_exceptions(fields.get("function_coverage_exceptions") or [], chosen)
    )

    mapping = fields.get("discipline_function_mapping")
    if mapping is not None:
        state.discipline_function_mapping.set(mapping)
        state.discipline_function_mapping_confirmed.set(
            bool(fields.get("discipline_function_mapping_confirmed"))
        )
        state.mapping_user_touched.set(True)
        state.workbook_provided_mapping.set(bool(fields.get("workbook_provided_mapping")))
    else:
        state.discipline_function_mapping.set(None)
        state.discipline_function_mapping_confirmed.set(False)
        state.mapping_user_touched.set(False)
        state.workbook_provided_mapping.set(False)

    session_name = fields.get("session_name") or _default_session_name(
        source_name, fields.get("upload_filename")
    )
    state.session_name.set(session_name)

    metric_config = fields.get("metric_config") or {}
    current = fields.get("current_metric")
    if not current or current not in metric_config:
        current = next(iter(metric_config), None)
    if current is not None:
        state.current_metric.set(current)

    with reactive.isolate():
        cache = state.metric_phase_cache() or {}
    if current is not None and current in cache:
        st.restore_metric_phase_state(state, current)

    state.app_data_loaded.set(fields.get("data") is not None)
    st.notify_workspace_refresh(state)
    return session_name


def seed_origin(state: AppState, *, kind: str, library_id=None, version=None,
                staged_path=None, run_dir=None, content_digest=None, provenance=None,
                portfolio_approvals=None) -> None:
    """Stamp where the just-restored assessment came from (and its build's
    provenance, when it has one). Disclosure only: a failure here must
    never block the open. Called AFTER the restore so the baselines
    describe what actually loaded."""
    try:
        state.source_provenance.set(provenance)
        state.assessment_source.set(ap.build_origin(
            state, kind=kind, library_id=library_id, version=version,
            staged_path=staged_path, run_dir=run_dir,
            content_digest=content_digest,
            portfolio_approvals=portfolio_approvals,
            loaded_at=datetime.now(timezone.utc).isoformat()))
    except Exception:  # noqa: BLE001
        logger.exception("open: origin seeding failed")


@module.ui
def data_overview_ui():
    return ui.div(ui.output_ui("main_content"), class_="data-setup-shell")


@module.server
def data_overview_server(input, output, session, state: AppState):
    ns = session.ns

    entry_view = reactive.value("landing")

    discipline_map_server("discipline_map", state)
    workbook_grid_server("workbook", state)
    # The wizard's ipyleaflet comms open only while the wizard is mounted —
    # opening them at session init raced the leaflet bundle load and spammed
    # "Could not create a model" retries at page load.
    import_map_server(
        "import_map", state, active=lambda: entry_view() in ("new", "wizard")
    )
    ws_step = reactive.value("workbook")

    @reactive.effect
    def _mirror_view():
        # Location mirror for the workflow strip; same resolution order as
        # main_content below. One writer: this effect.
        if entry_view() == "wizard":
            resolved = "wizard"
        elif state.app_data_loaded():
            resolved = "workspace"
        elif entry_view() == "new":
            resolved = "new"
        else:
            resolved = "landing"
        state.data_setup_view.set(resolved)

    # ── landing / entry views ────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(state.app_reset_nonce, ignore_init=True)
    def _closed():
        entry_view.set("landing")

    @reactive.effect
    @reactive.event(state.wizard_step_nonce, ignore_init=True)
    def _wizard_request_opens_wizard():
        # Stage-banner clicks target wizard steps; mount the wizard when it is
        # not showing (fresh flow, or "wizard" re-entry over a loaded project)
        # and ask it to hydrate its widgets from the saved state.
        with reactive.isolate():
            loaded = bool(state.app_data_loaded())
            hydrate = (state.wizard_hydrate_nonce() or 0) + 1
        entry_view.set("wizard" if loaded else "new")
        state.wizard_hydrate_nonce.set(hydrate)

    @reactive.effect
    @reactive.event(state.workspace_refresh_nonce, ignore_init=True)
    def _wizard_build_returns_to_workspace():
        # The wizard's "Build dataset" (and any session restore) refreshes the
        # workspace; when the wizard was opened over a loaded project, fall
        # back to the workspace view.
        with reactive.isolate():
            loaded = bool(state.app_data_loaded())
            cur = entry_view()
        if loaded and cur == "wizard":
            entry_view.set("landing")

    # ── workspace sections (panels stay mounted; visibility via <style>) ─────
    # The workflow strip's stage-4 chips are the only switcher (the in-card
    # stepper is gone); they arrive here as section requests.
    @reactive.effect
    @reactive.event(state.workspace_open_nonce, ignore_init=True)
    def _workspace_open_request():
        # Stage-4 pill: close any open wizard back to the workspace view.
        entry_view.set("landing")

    @reactive.effect
    @reactive.event(state.workspace_section_nonce, ignore_init=True)
    def _workspace_section_request():
        with reactive.isolate():
            value = state.workspace_section_request()
        # The channel is shared with the Reference Curves sections (gallery,
        # table); only a workspace value touches this view.
        if value in {s["value"] for s in WORKSPACE_STEPS}:
            ws_step.set(value)
            entry_view.set("landing")

    @reactive.effect
    def _mirror_section():
        # Location mirror for the strip's chip highlight. One writer: this.
        state.workspace_section.set(ws_step())

    @reactive.effect
    @reactive.event(state.app_data_loaded)
    def _reset_step():
        if state.app_data_loaded():
            ws_step.set("workbook")

    @render.ui
    def ws_style():
        cur = ws_step()
        css = ".ws-panel {display: none;} " + f".ws-panel-{cur} {{display: block;}}"
        return ui.tags.style(css)

    @render.text
    def workspace_title():
        name = state.session_name()
        upload = state.upload_filename()
        return _default_session_name(name, upload)

    # ── landing / new / workspace main content ──────────────────────────────
    def landing_view():
        # Only ever seen behind the start page, or after a project-independent tool
        # (NRSA explorer, Rules) closed it with no project open: say what to do and
        # offer the one door.
        return ui.div(
            ui.div(
                ui.tags.p("No project is open.", class_="sc-empty-title"),
                ui.tags.p("Create a project, open one, or download an assessment from "
                          "the library.", class_="sc-empty-lead"),
                ui.tags.button(
                    "Projects", type="button", class_="btn btn-primary",
                    onclick=("Shiny.setInputValue('start_page_open', "
                             "Date.now() + Math.random(), {priority: 'event'})")),
                class_="sc-empty-card",
            ),
            class_="landing-shell sc-empty",
        )

    def new_project_view():
        # No back-out link: the workflow strip is the only navigation. Over a
        # loaded project the stage-4 pill returns to the workspace; on a fresh
        # project there is nothing behind the wizard to go back to.
        return ui.TagList(import_map_ui("import_map"))

    def workspace_view():
        panels = {
            "workbook": ui.TagList(
                ui.output_ui(ns("workbook_summary")),
                workbook_grid_ui("workbook"),
            ),
            "mapping": discipline_map_ui("discipline_map"),
            "redundancy": ui.output_ui(ns("redundancy_panel")),
            "validation": ui.output_ui(ns("validation_warnings")),
        }
        return ui.div(
            ui.div(
                ui.tags.span(
                    bi("folder-check"),
                    " ",
                    ui.tags.strong(ui.output_text(ns("workspace_title"), inline=True)),
                ),
                class_="card-header data-setup-card-header",
            ),
            ui.div(
                ui.output_ui(ns("ws_style")),
                *[
                    ui.div(
                        panels[s["value"]],
                        class_=f"ws-panel ws-panel-{s['value']}",
                    )
                    for s in WORKSPACE_STEPS
                ],
                class_="card-body",
            ),
            class_="card border-primary mb-3 import-map-card workspace-card",
        )

    @render.ui
    def main_content():
        # "wizard" is the re-entry mode: the guided wizard shown over a loaded
        # project (stage-banner clicks), hydrated from the saved state.
        if entry_view() == "wizard":
            return new_project_view()
        if state.app_data_loaded():
            return workspace_view()
        if entry_view() == "new":
            return new_project_view()
        return landing_view()

    # ── loads from outside this module ──────────────────────────────────────
    # The project controller (views/project.py) calls the module-level
    # restore_session / apply_workbook_bundle / seed_origin directly. The
    # Region builder's staged open still arrives on this nonce channel and opens
    # as an unsaved project: Save asks where to keep it.
    @reactive.effect
    @reactive.event(state.session_restore_nonce, ignore_init=True)
    def _restore_from_library_request():
        with reactive.isolate():
            rq = state.session_restore_request()
        if not rq or not rq.get("payload"):
            return
        before = state.hooks.get("before_replace")
        if before is not None:
            before()
        try:
            restore_session(state, rq["payload"], source_name=rq.get("source_name"))
        except Exception as e:  # noqa: BLE001
            ui.notification_show(
                f"Could not load the assessment: {e}", type="error", duration=8
            )
            return
        seed = rq.get("origin_seed")
        if seed:
            seed_origin(state, kind=seed.get("kind") or "run",
                        library_id=seed.get("library_id"),
                        version=seed.get("version"),
                        staged_path=seed.get("staged_path"),
                        run_dir=seed.get("run_dir"),
                        content_digest=seed.get("content_digest"),
                        provenance=seed.get("provenance"),
                        portfolio_approvals=seed.get("portfolio_approvals"))
        adopted = state.hooks.get("adopt_unsaved")
        if adopted is not None:
            adopted(rq.get("source_name") or "Staged run")
        ui.notification_show(
            f"Loaded {rq.get('source_name') or 'assessment'}. Use Save As to keep it as "
            "a project.", type="message", duration=6)

    # ── workspace step panels ────────────────────────────────────────────────
    @render.ui
    def workbook_summary():
        data = state.data()
        req(data is not None)
        mc = state.metric_config() or {}
        sc = state.strat_config() or {}
        pc = state.predictor_config() or {}
        return ui.div(
            ui.tags.p(
                f"{len(data)} sites × {data.shape[1]} columns | "
                f"{len(mc)} metrics | {len(sc)} stratifications | {len(pc)} predictors",
                class_="text-muted small mb-2",
            )
        )

    # Badge class per precheck_status. Anything unrecognized falls back to the
    # neutral badge rather than being dropped from the roll-up.
    _PRECHECK_BADGE = {
        "pass": "bg-success",
        "categorical": "bg-secondary",
        "caution": "bg-warning text-dark",
        "no_data": "bg-danger",
        "fail": "bg-danger",
        "missing_column": "bg-danger",
    }

    @render.ui
    def validation_warnings():
        # Three distinguishable states. The old version had one bare
        # req(precheck is not None), which rendered NOTHING when precheck had
        # never run -- indistinguishable from a clean dataset.
        precheck = state.precheck_df()
        summary = precheck_summary(precheck)
        if not summary["available"]:
            return ui.div(
                ui.tags.p("Validation has not run for this project.", class_="mb-1"),
                ui.tags.p(
                    "It runs when a project loads and whenever you apply workbook "
                    "changes. Open the Workbook section and apply to compute it now.",
                    class_="text-muted small mb-0",
                ),
                class_="alert alert-secondary py-2 px-3",
            )

        badges = [
            ui.tags.span(
                f"{n} {status.replace('_', ' ')}",
                class_=f"badge {_PRECHECK_BADGE.get(status, 'bg-secondary')} me-1",
            )
            for status, n in sorted(summary["counts"].items())
        ]
        header = ui.div(*badges, class_="mb-2")

        rows = precheck_warning_rows(precheck)
        if len(rows) == 0:
            return ui.TagList(
                header,
                ui.tags.p(
                    f"{summary['n_total']} metrics checked, no warnings.",
                    class_="text-muted small mb-0",
                ),
            )

        # Warning rows only: a full all-pass dump buried the rows that matter.
        show = rows[
            [
                "metric", "display_name", "n_obs", "n_missing", "pct_missing",
                "flag_low_n", "flag_low_variance", "flag_impossible_values",
                "precheck_status",
            ]
        ]
        return ui.TagList(
            header,
            ui.tags.p(
                f"{len(show)} of {summary['n_total']} metrics need a look.",
                class_="text-muted small mb-2",
            ),
            ui.tags.table(
                ui.tags.thead(
                    ui.tags.tr(*[ui.tags.th(c) for c in show.columns])
                ),
                ui.tags.tbody(
                    *[
                        ui.tags.tr(*[ui.tags.td(str(v)) for v in row])
                        for row in show.itertuples(index=False)
                    ]
                ),
                class_="table table-sm table-striped small",
            ),
        )

    def _redundancy_table(state) -> pd.DataFrame | None:
        """The stored RED-01 matrix, or one computed from the restored project.

        Published assessments written before this field existed carry no matrix,
        and there is no reason to make a reviewer republish to see one, so fall
        back to computing it from the data and configs already in hand.
        """
        stored = state.metric_redundancy()
        if stored is not None:
            return stored
        data = state.data()
        metric_config = state.metric_config() or {}
        if data is None or not metric_config:
            return None
        column_functions = state.column_functions() or {}
        metrics, _ = overlap.roles_from_configs(
            metric_config, None, data_columns=data.columns)
        if len(metrics) < 2:
            return None
        analysis = overlap.analyze_overlap(
            data, metric_columns=metrics, partner_columns=metrics,
            partner_role=overlap.PARTNER_METRIC, column_functions=column_functions,
        )
        return overlap.redundancy_view(analysis, column_functions)

    @render.ui
    def redundancy_panel():
        data = state.data()
        metric_config = state.metric_config() or {}
        n_metrics = len([m for m in metric_config if data is not None and m in data.columns])
        if data is None or n_metrics < 2:
            return ui.div(
                ui.tags.p(
                    "Redundancy needs at least two numeric metrics. This project "
                    f"has {n_metrics}.",
                    class_="mb-0",
                ),
                class_="alert alert-secondary py-2 px-3",
            )

        table = _redundancy_table(state)
        header = ui.TagList(
            ui.tags.p(
                linkify_rule_ids(
                    "Every pair of metric columns is correlated on this project's site "
                    "data. Spearman rank correlation is primary; Pearson is shown "
                    "alongside so you can see when the two disagree. RED-01 flags a pair "
                    f"at absolute Spearman {overlap.DEFAULT_RHO_THRESHOLD:.2f} or above."),
                class_="small mb-1",
            ),
            ui.tags.p(
                "A flagged pair is evidence that two metrics carry the same signal. "
                "It is not an automatic action. Decide which one to keep, then drop "
                "the other in Function mapping and record why.",
                class_="text-muted small mb-2",
            ),
        )

        if table is None:
            return ui.TagList(header, ui.div(
                ui.tags.p("Redundancy has not been computed for this project.",
                          class_="mb-0"),
                class_="alert alert-secondary py-2 px-3",
            ))
        if len(table) == 0:
            return ui.TagList(header, ui.tags.p(
                f"No metric pair reached the reporting floor of "
                f"{overlap.DEFAULT_REPORT_FLOOR:.2f}. Nothing to review here.",
                class_="text-muted small mb-0",
            ))

        def label(metric: str) -> str:
            return (metric_config.get(metric) or {}).get("display_name") or metric

        def function_cell(row) -> str:
            if row["same_function"]:
                return str(row["function_a"])
            return f"{row['function_a']} / {row['function_b']}"

        flagged = int(table["red01_spearman_flag"].sum())
        rows = []
        for row in table.itertuples(index=False):
            r = row._asdict()
            rows.append(ui.tags.tr(
                ui.tags.td(label(r["metric_a"])),
                ui.tags.td(label(r["metric_b"])),
                ui.tags.td(function_cell(r)),
                ui.tags.td(f"{r['spearman']:.2f}"),
                ui.tags.td("" if r["pearson"] is None else f"{r['pearson']:.2f}"),
                ui.tags.td(
                    status_badge("caution", "Flagged") if r["red01_spearman_flag"] else ""
                ),
            ))
        return ui.TagList(
            header,
            ui.tags.p(
                linkify_rule_ids(
                    f"{len(table)} pair(s) above the reporting floor, {flagged} flagged "
                    "by RED-01."),
                class_="text-muted small mb-2",
            ),
            ui.tags.table(
                ui.tags.thead(ui.tags.tr(*[
                    ui.tags.th(c) for c in
                    ("Metric A", "Metric B", "Function", "Spearman", "Pearson", "RED-01")
                ])),
                ui.tags.tbody(*rows),
                class_="table table-sm table-striped small",
            ),
        )

    # Session/workbook downloads live on the Publish page (views/publish.py,
    # Draft pane); the header Save link navigates there.
