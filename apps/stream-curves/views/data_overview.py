"""Data & Setup tab — core port of app/modules/mod_data_overview.R.

Covers the primary flows: the landing screen (Start New Project / Open
Project), the xlsx workbook load pipeline (read → clean → derive → precheck →
apply to state), JSON session save/restore (replacing the R app's .rds
snapshots), and the opened-project workspace shell — header with Save
Project / Save Workbook / Close Project plus the 3-step stepper (Workbook,
Mapping, Pre-Run Validation). Step sections stay mounted and are shown/hidden
via a reactive <style> tag (the R app used conditionalPanel).

Deferred pieces of the R module (ported separately): the metadata editor
tabs, the excel-like workbook grid, custom-grouping builder, site-mask
manager, and the map-first import wizard (M7).
"""

from __future__ import annotations

import hashlib
import io
import re
from datetime import date

import pandas as pd
from shiny import module, reactive, render, req, ui

from streamcurves import session_io as sio
from streamcurves.cleaning import clean_data
from streamcurves.derive import derive_variables
from streamcurves.precheck import run_metric_precheck
from streamcurves.workbook import read_input_workbook, write_input_workbook
from views import state as st
from views.discipline_map import discipline_map_server, discipline_map_ui
from views.import_map import import_map_server, import_map_ui
from views.state import AppState, deep_copy_value, empty_phase2_settings
from views.theme import bi, fa
from views.workbook_grid import workbook_grid_server, workbook_grid_ui

WORKSPACE_STEPS = [
    {"value": "workbook", "label": "Workbook"},
    {"value": "mapping", "label": "Discipline → Function → Metric"},
    {"value": "validation", "label": "Pre-Run Validation"},
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


def _upload_format_tooltip():
    rows = [
        ("File type:", "XLSX workbook."),
        ("Structure:", "Separate sheets for data, metrics, stratifications, predictors, and recodes."),
        ("Custom strata:", "Categorical and continuous grouping rules are materialized at import."),
        ("Runtime config:", "Workbook metadata replaces the old YAML registries."),
    ]
    return ui.div(
        *[
            ui.div(ui.tags.strong(k), ui.tags.span(f" {v}"), class_="upload-format-row")
            for k, v in rows
        ],
        class_="upload-format-tooltip",
    )


def _session_tooltip():
    rows = [
        ("File type:", "StreamCurves session snapshot (.streamcurves.json)."),
        (
            "Includes:",
            "Current derived analysis data, workbook tables/metadata, site masks, current "
            "app selections/settings, saved results/caches, reference-curve choices/results, "
            "and decision history.",
        ),
        (
            "Original workbook:",
            "Stores the workbook tables and upload filename, but not the original .xlsx "
            "file itself as an attached binary.",
        ),
        ("Restore:", "Uploading the .json restores the saved workspace state."),
    ]
    return ui.div(
        *[
            ui.div(ui.tags.strong(k), ui.tags.span(f" {v}"), class_="upload-format-row")
            for k, v in rows
        ],
        class_="upload-format-tooltip",
    )


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
    import_map_server("import_map", state, active=lambda: entry_view() == "new")
    upload_error = reactive.value(None)
    metadata_status = reactive.value(None)
    ws_step = reactive.value("workbook")

    # ── landing / entry views ────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.start_new)
    def _start_new():
        entry_view.set("new")

    @reactive.effect
    @reactive.event(input.back_to_landing)
    def _back():
        entry_view.set("landing")

    @reactive.effect
    @reactive.event(state.app_reset_nonce, ignore_init=True)
    def _closed():
        entry_view.set("landing")

    # ── workspace stepper (sections stay mounted; visibility via <style>) ────
    for s in WORKSPACE_STEPS:

        def _mk(v):
            @reactive.effect
            @reactive.event(input[f"ws_step_{v}"], ignore_init=True)
            def _step():
                ws_step.set(v)

        _mk(s["value"])

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

    @render.ui
    def workspace_stepper():
        cur = ws_step()
        links = []
        for i, s in enumerate(WORKSPACE_STEPS, start=1):
            links.append(
                ui.input_action_link(
                    ns(f"ws_step_{s['value']}"),
                    ui.TagList(
                        ui.tags.span(str(i), class_="ws-step-num"),
                        ui.tags.span(s["label"], class_="ws-step-label"),
                    ),
                    class_="ws-step" + (" active" if cur == s["value"] else ""),
                )
            )
        return ui.div(*links, class_="workspace-stepper d-flex flex-wrap gap-2")

    @render.text
    def workspace_title():
        name = state.session_name()
        upload = state.upload_filename()
        return _default_session_name(name, upload)

    # ── landing / new / workspace main content ──────────────────────────────
    def landing_view():
        return ui.div(
            ui.div(
                ui.div(
                    ui.card(
                        ui.card_body(
                            ui.div(bi("plus-circle-fill"), class_="landing-card-icon text-primary"),
                            ui.tags.h4("Start New Project", class_="landing-card-title"),
                            ui.tags.p(
                                "Build a new stream reference-curve dataset — choose a region, "
                                "gather monitoring sites, pull metrics, classify columns, and build.",
                                class_="text-muted landing-card-blurb",
                            ),
                            ui.input_action_button(
                                ns("start_new"),
                                ui.TagList(bi("arrow-right-circle"), " Start New Project"),
                                class_="btn btn-primary",
                            ),
                        ),
                        class_="h-100 landing-card border-primary",
                    ),
                    class_="col-12 col-lg-5",
                ),
                ui.div(
                    ui.card(
                        ui.card_body(
                            ui.div(bi("folder2-open"), class_="landing-card-icon text-secondary"),
                            ui.tags.h4("Open Project", class_="landing-card-title"),
                            ui.tags.p(
                                "Open a saved project — a StreamCurves workbook (.xlsx) or a "
                                "saved session (.json).",
                                class_="text-muted landing-card-blurb",
                            ),
                            ui.div(
                                ui.tags.span("Workbook (.xlsx) or saved session (.json)"),
                                ui.tooltip(
                                    ui.tags.button(
                                        "?", type="button", class_="upload-format-help",
                                        aria_label="Expected file format",
                                    ),
                                    "Saved projects can be opened from a StreamCurves workbook "
                                    "(.xlsx) or a saved session (.json) file.",
                                    placement="right",
                                ),
                                class_="data-upload-label",
                            ),
                            ui.input_file(
                                ns("open_project_file"),
                                None,
                                accept=[".xlsx", ".json"],
                                button_label="Choose File",
                                placeholder="No file selected",
                            ),
                            ui.output_ui(ns("upload_status")),
                        ),
                        class_="h-100 landing-card",
                    ),
                    class_="col-12 col-lg-5",
                ),
                class_="row g-3 justify-content-center align-items-stretch",
            ),
            class_="landing-shell",
        )

    def new_project_view():
        return ui.TagList(
            ui.div(
                ui.input_action_link(
                    ns("back_to_landing"),
                    ui.TagList(bi("arrow-left"), " Back to start"),
                    class_="small text-decoration-none",
                ),
                class_="mb-2",
            ),
            import_map_ui("import_map"),
        )

    def workspace_view():
        panels = {
            "workbook": ui.TagList(
                ui.output_ui(ns("workbook_summary")),
                workbook_grid_ui("workbook"),
            ),
            "mapping": discipline_map_ui("discipline_map"),
            "validation": ui.output_ui(ns("validation_warnings")),
        }
        return ui.div(
            ui.div(
                ui.tags.span(
                    bi("folder-check"),
                    " ",
                    ui.tags.strong(ui.output_text(ns("workspace_title"), inline=True)),
                ),
                ui.div(
                    ui.input_action_button(
                        ns("save_project_menu"),
                        ui.TagList(fa("floppy-disk"), " Save"),
                        class_="btn btn-outline-primary btn-sm",
                    ),
                    ui.input_action_button(
                        ns("reset_analysis"),
                        ui.TagList(fa("xmark"), " Close Project"),
                        class_="btn btn-outline-danger btn-sm",
                    ),
                    class_="d-flex gap-2",
                ),
                class_="card-header data-setup-card-header",
            ),
            ui.div(
                ui.output_ui(ns("ws_style")),
                ui.output_ui(ns("workspace_stepper")),
                ui.tags.hr(class_="mt-2 mb-3"),
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
        if state.app_data_loaded():
            return workspace_view()
        if entry_view() == "new":
            return new_project_view()
        return landing_view()

    @render.ui
    def upload_status():
        err = upload_error()
        if err is None:
            return None
        return ui.div(
            fa("triangle-exclamation"),
            f" {err}",
            class_="alert alert-danger py-1 px-2 mt-1",
        )

    # ── workbook (xlsx) load pipeline (R:1973-2073) ─────────────────────────
    def apply_workbook_bundle(bundle: dict, source_name: str):
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
            metadata_status.set(None)
            p.set(value=5, message="Loading Workbook", detail="Done.")

            ui.notification_show(
                f"Loaded {len(derived)} rows x {derived.shape[1]} cols from {source_name}",
                type="message",
                duration=5,
            )
        finally:
            p.close()

    # ── session (.json) restore (R:2841-2934) ───────────────────────────────
    def restore_session_into_state(payload: dict, source_name: str | None = None):
        fields = sio.decode_session_fields(payload)

        st.reset_all_analysis(state)

        state.data.set(fields.get("data"))
        state.precheck_df.set(fields.get("precheck_df"))

        state.data_source.set(fields.get("data_source") or "session_file")
        state.data_fingerprint.set(fields.get("data_fingerprint"))
        state.upload_filename.set(fields.get("upload_filename"))
        state.input_metadata.set(fields.get("input_metadata"))
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

        state.phase1_candidates.set(fields.get("phase1_candidates") or {})
        state.all_layer1_results.set(fields.get("all_layer1_results") or {})
        state.all_layer2_results.set(fields.get("all_layer2_results") or {})
        state.phase2_ranking.set(fields.get("phase2_ranking"))
        state.cross_metric_consistency.set(fields.get("cross_metric_consistency"))
        state.phase2_settings.set(fields.get("phase2_settings") or empty_phase2_settings())
        state.phase2_metric_overrides.set(fields.get("phase2_metric_overrides") or {})
        state.curve_stratification.set(fields.get("curve_stratification") or {})
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
        metadata_status.set(None)
        st.notify_workspace_refresh(state)
        return session_name

    @reactive.effect
    @reactive.event(state.session_restore_nonce, ignore_init=True)
    def _restore_from_library_request():
        # The Library tab loads a version's session payload and bumps the nonce;
        # reuse the exact same restore path as opening a .streamcurves.json file.
        with reactive.isolate():
            rq = state.session_restore_request()
        if not rq or not rq.get("payload"):
            return
        try:
            restore_session_into_state(rq["payload"], source_name=rq.get("source_name"))
        except Exception as e:  # noqa: BLE001
            ui.notification_show(
                f"Could not load the assessment: {e}", type="error", duration=8
            )
            return
        ui.notification_show(
            f"Loaded {rq.get('source_name') or 'assessment'}. Open Reference Curves to keep "
            "working, or review inputs in Data & Setup.",
            type="message",
            duration=7,
        )

    @reactive.effect
    @reactive.event(input.open_project_file)
    async def _open_project():
        finfo = input.open_project_file()
        req(finfo)
        f = finfo[0]
        name = f.get("name", "")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        upload_error.set(None)
        metadata_status.set(None)

        if ext == "json":
            try:
                p = ui.Progress(min=0, max=3)
                p.set(value=0, message="Loading Session", detail="Reading saved session snapshot...")
                payload = sio.load_session_payload(f["datapath"])
                p.set(value=1, message="Loading Session", detail="Restoring saved analysis state...")
                stem = re.sub(r"\.json$", "", name, flags=re.I)
                session_name = restore_session_into_state(payload, source_name=stem)
                p.set(value=3, message="Loading Session", detail="Done.")
                p.close()
                ui.update_text("session_name", value=session_name or "")
                await session.send_custom_message(
                    "clearFileInput", {"id": ns("open_project_file")}
                )
                with reactive.isolate():
                    n_completed = len(state.completed_metrics() or {})
                ui.notification_show(
                    f"Session '{session_name}' loaded. {n_completed} completed metrics restored.",
                    type="message",
                    duration=5,
                )
            except Exception as e:  # noqa: BLE001
                metadata_status.set({"type": "danger", "text": f"Session load failed: {e}"})
                await session.send_custom_message(
                    "clearFileInput", {"id": ns("open_project_file")}
                )
                ui.notification_show(f"Session load failed: {e}", type="error", duration=8)
            return

        if ext != "xlsx":
            msg = "Unsupported file type. Choose a workbook (.xlsx) or a saved session (.json)."
            upload_error.set(msg)
            ui.notification_show(msg, type="error", duration=6)
            return

        try:
            bundle = read_input_workbook(f["datapath"])
            apply_workbook_bundle(bundle, name)
        except Exception as e:  # noqa: BLE001
            upload_error.set(str(e))
            metadata_status.set({"type": "danger", "text": f"Upload failed: {e}"})
            ui.notification_show(f"Upload failed: {e}", type="error", duration=8)

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

    @render.ui
    def validation_warnings():
        precheck = state.precheck_df()
        req(precheck is not None)
        n_fail = int((precheck["precheck_status"] == "fail").sum())
        n_caution = int((precheck["precheck_status"] == "caution").sum())
        n_pass = int((precheck["precheck_status"] == "pass").sum())
        header = ui.div(
            ui.tags.span(f"{n_pass} pass", class_="badge bg-success me-1"),
            ui.tags.span(f"{n_caution} caution", class_="badge bg-warning text-dark me-1"),
            ui.tags.span(f"{n_fail} fail", class_="badge bg-danger"),
            class_="mb-2",
        )
        show = precheck[
            [
                "metric", "display_name", "n_obs", "n_missing", "pct_missing",
                "flag_low_n", "flag_low_variance", "flag_impossible_values",
                "precheck_status",
            ]
        ]
        return ui.TagList(
            header,
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

    # ── session save / workbook save ─────────────────────────────────────────
    # One Save button; the format choice (and its tradeoff) lives in a modal.
    # The download handlers below are unchanged — only the buttons moved here.
    @reactive.effect
    @reactive.event(input.save_project_menu)
    def _show_save_options():
        ui.modal_show(
            ui.modal(
                ui.div(
                    ui.download_button(
                        ns("download_workbook"),
                        ui.TagList(fa("file-excel"), " Workbook (.xlsx)"),
                        class_="btn btn-primary w-100",
                    ),
                    ui.tags.small(
                        "Data + setup sheets. Open and edit in Excel. Re-opening "
                        "rebuilds the dataset and recomputes analyses from scratch.",
                        class_="text-muted d-block mt-1",
                    ),
                    class_="mb-3",
                ),
                ui.div(
                    ui.download_button(
                        ns("download_session"),
                        ui.TagList(fa("floppy-disk"), " Project (.json)"),
                        class_="btn btn-outline-primary w-100",
                    ),
                    ui.tags.small(
                        "Complete session — data, setup, and all analysis progress "
                        "(screening results, curves, decisions, cross-sections). "
                        "Resume exactly where you left off.",
                        class_="text-muted d-block mt-1",
                    ),
                ),
                title="Save your work",
                easy_close=True,
                footer=ui.modal_button("Close"),
                size="m",
            )
        )

    # suspend_when_hidden=False: these links live in the Save modal and bind
    # during its insert frame (display:none) — default suspension would leave
    # them permanently disabled (the bb98c92 wedge).
    @output(suspend_when_hidden=False)
    @render.download(
        filename=lambda: _sanitize_file_stem(
            state.isolate_get("session_name") or (input.session_name() if "session_name" in input else None),
            state.isolate_get("upload_filename"),
        )
        + sio.SESSION_SUFFIX
    )
    def download_session():
        with reactive.isolate():
            req(state.app_data_loaded())
            session_name = _default_session_name(
                state.session_name(), state.upload_filename()
            )
            state.session_name.set(session_name)
            fields = {name: state.get(name) for name in sio.SESSION_FIELDS}
        payload = sio.dump_session_fields(fields, session_name=session_name)
        yield sio.dumps_session(payload).encode("utf-8")

    @output(suspend_when_hidden=False)
    @render.download(
        filename=lambda: _sanitize_file_stem(
            state.isolate_get("session_name"), state.isolate_get("upload_filename")
        )
        + f"_workbook_{date.today():%Y%m%d}.xlsx"
    )
    def download_workbook():
        with reactive.isolate():
            req(state.app_data_loaded())
            tables = state.input_metadata()
        req(tables is not None)
        buf = io.BytesIO()
        write_input_workbook(tables, buf)
        yield buf.getvalue()

    # ── close project ─────────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.reset_analysis)
    def _close_confirm():
        ui.modal_show(
            ui.modal(
                "Close this project? Unsaved changes will be lost. The app returns "
                "to the start screen.",
                title="Close Project",
                footer=ui.TagList(
                    ui.modal_button("Cancel"),
                    ui.input_action_button(
                        ns("confirm_reset_analysis"), "Close Project", class_="btn btn-danger"
                    ),
                ),
            )
        )

    @reactive.effect
    @reactive.event(input.confirm_reset_analysis)
    def _close_do():
        ui.modal_remove()
        st.reset_app_to_startup(state)
        ui.notification_show("Project closed.", type="message", duration=3)
