"""StreamCurves: Reference & Regional Curve Development (Python Shiny).

The app's shell, laid out like HYPE Desktop: a navy header (brand, version, the open project;
Projects / Save / Save As... / About / Help), the one-row stage strip, the Project panel at the
left and the page to its right, the only scroll container. Pages are the panels of a hidden
navset (``main_navbar``), switched by the strip, the panel and the pages themselves through
ui.update_navset. Projects, the start page and the dialogs live in views/project.py; the heavy
lifting lives in the pure ``streamcurves`` package.
"""

from __future__ import annotations

import matplotlib

# Force the non-interactive Agg backend before plotnine/matplotlib.pyplot is
# first imported (via the view modules below). On a machine with a display
# matplotlib otherwise selects an interactive backend (e.g. tkagg) whose
# GUI-loop init + cold-start font-cache build stalls the first big batch of
# server-side plot renders — the Reference Curves mega-table looked like a
# 60s+ deadlock (idle process). Headless deploys already default to Agg.
matplotlib.use("Agg")

from shiny import App, reactive, ui

# StreamCurves scores DEEP inputs with the EASI method it ships (_vendor/easi); a stray
# EASI_METHOD_PACKAGE / EASI_DATA_DIR / EASI_CRITERIA_SET would switch that copy silently.
# EASI method versions are scored only in evaluation workers (streamcurves.easi_method).
from streamcurves import easi_env as _easi_env  # noqa: E402

_easi_env.sanitize()

# ipywidgets front-end: the import-wizard maps (ipyleaflet) live in
# dynamically-rendered UI, so their JS must be on the page BEFORE the first
# widget model is created. views/widget_deps.py attaches the output binding +
# the eagerly-loaded jupyter-leaflet bundle statically — replacing the old
# hidden "_sw_warmup" primer widget and its page-load "Could not create a
# model" retry noise. ipyleaflet is the only shinywidgets library left —
# plotly figures render htmlwidgets-style via views/plotly_html.py instead.
try:
    from views.widget_deps import static_ipywidget_dependencies

    _HAS_WIDGETS = True
except Exception:  # noqa: BLE001
    _HAS_WIDGETS = False

import logging

from streamcurves import methodology
from streamcurves import pressure_evidence
from streamcurves.mapping import realign_discipline_function_mapping
from streamcurves.paths import WWW_DIR
from streamcurves.staf_library import default_discipline_function_mapping
from views import summary_state as sst
from views.analysis_workspace import analysis_workspace_server, analysis_workspace_ui
from views.cross_section import cross_section_server, cross_section_ui
from views.nrsa_explorer import nrsa_explorer_server, nrsa_explorer_ui
from views.region_builder import region_builder_server, region_builder_ui
from views.source_panel import PANEL_ID as SOURCE_PANEL_ID, source_panel_server
from views.source_dialog import DIALOG_ID as SOURCE_DIALOG_ID, source_dialog_server
from views.rules import rules_server, rules_ui
from views.validate_page import validate_server, validate_ui
from views.data_overview import data_overview_server, data_overview_ui
from views.stagebar import project_panel_ui, stagebar_server, stagebar_ui
from views.publish import publish_server, publish_ui
from views.phase1 import phase1_server, phase1_ui
from views.phase2 import phase2_server, phase2_ui
from views.phase3 import phase3_server, phase3_ui
from views.phase4 import phase4_server, phase4_ui
from views.regional_curve import regional_curve_server, regional_curve_ui
from views.state import AppState
from views.summary_export import summary_export_server, summary_export_ui
from views.summary_page import summary_page_server, summary_page_ui
from views.theme import app_theme, versioned_www_asset
from views import project as proj
# Re-exported: the Help dialog's content (tests/test_stagebar_nav reads it here).
from views.help import app_help_content  # noqa: F401
from views.uihelpers import RULES_GOTO_INPUT, WORKFLOW_GOTO_INPUT
from views.workspace_modal import register_workspace_modal

logger = logging.getLogger("streamcurves")

# The methodology config mirrors several engine constants (EASI presets, curve
# gate geometry, DEEP scoring contract). Drift means the published methodology
# misdescribes what the software does, so it is logged loudly at every startup.
# Non-strict here: an analyst can still open the app to inspect the problem.
methodology.verify_mirrors(strict=False)


# --------------------------------------------------------------------------- #
# The page. Every page is a panel of the hidden navset; nav values are the strip's
# vocabulary (run_state: stage landings and TOOL_KEYS), unchanged from the navbar days.
# --------------------------------------------------------------------------- #
_PANELS = [
    ui.nav_panel("Data & Setup", ui.div(data_overview_ui("data_overview"), class_="mt-3"),
                 value="data"),
    ui.nav_panel("Reference Curves", ui.div(summary_page_ui("summary"), class_="mt-3"),
                 value="curves"),
    ui.nav_panel("Publish", ui.div(publish_ui("publish"), class_="mt-3"), value="publish"),
    ui.nav_panel("Validate", ui.div(validate_ui("validate"), class_="mt-3"), value="validate"),
    # the tools (run_state.TOOL_KEYS): keep these nav values in sync with TOOL_KEYS
    ui.nav_panel("Regional Curves", ui.div(regional_curve_ui("regional"), class_="mt-3"),
                 value="regional"),
    ui.nav_panel("Cross-Sections", ui.div(cross_section_ui("xsec"), class_="mt-3"),
                 value="xsec"),
    ui.nav_panel("NRSA Explorer", ui.div(nrsa_explorer_ui("nrsa"), class_="mt-3"),
                 value="nrsa"),
    ui.nav_panel("Region Builder", ui.div(region_builder_ui("build"), class_="mt-3"),
                 value="build"),
    ui.nav_panel("Rules", ui.div(rules_ui("rules"), class_="mt-3"), value="rules"),
]

app_ui = ui.page_fillable(
    ui.head_content(
        # Declared rather than left to the browser's implicit /favicon.ico request, which
        # carries no version and is cached hard.
        ui.tags.link(rel="icon", href=versioned_www_asset("favicon.ico"), type="image/x-icon"),
        ui.tags.link(rel="stylesheet", href=versioned_www_asset("styles.css")),
        ui.tags.link(rel="stylesheet", href=versioned_www_asset("curves.css")),
        ui.tags.link(rel="stylesheet", href=versioned_www_asset("shell.css")),
        ui.tags.script(src=versioned_www_asset("curves.js")),
        ui.tags.script(src=versioned_www_asset("shell.js")),
        # Removes modals Bootstrap re-parents to <body> when two shows overlap (a dead copy
        # on top of the live one makes the app look frozen; see the file's header).
        ui.tags.script(src=versioned_www_asset("modal_guard.js")),
        # The desktop shell bridge (native pickers, window title); nothing in a browser.
        ui.tags.script(src=versioned_www_asset("desktop_bridge.js")),
    ),
    # Static ipywidget/ipyleaflet deps (see views/widget_deps.py): renders nothing visible;
    # its dependencies hoist into <head>.
    (static_ipywidget_dependencies() if _HAS_WIDGETS else None),
    ui.div(
        ui.div(proj.header_left_ui(), ui.div(class_="sc-header-center"),
               proj.header_nav_ui(), class_="sc-header"),
        stagebar_ui("stagebar"),
        ui.div(
            project_panel_ui("stagebar"),
            ui.div(ui.navset_hidden(*_PANELS, id="main_navbar", selected="data"),
                   class_="sc-content"),
            class_="sc-body"),
        class_="sc-shell"),
    proj.boot_veil_ui(),
    title="StreamCurves",
    padding=0,
    gap=0,
    theme=app_theme,
)


def server(input, output, session):
    state = AppState.fresh()

    stagebar_server("stagebar", state)
    # Projects: the start page, New/Open/Save/Save As, autosave, the gallery, dialogs.
    proj.project_server(input, output, session, state)
    data_overview_server("data_overview", state)
    summary_page_server("summary", state)
    regional_curve_server("regional", state)
    cross_section_server("xsec", state)
    # the map widget gates on this, so its ipywidget comm opens when the tool
    # shows rather than at session init, where it races the leaflet bundle
    nrsa_explorer_server("nrsa", state,
                         active=lambda: state.current_tab() == "nrsa")
    # Same gate: the page reads the site table and a run folder off disk, so it
    # should do that when it shows rather than at session init.
    region_builder_server("build", state,
                          active=lambda: state.current_tab() == "build")
    # Same gate: the page reads the three methodology files.
    rules_server("rules", state,
                 active=lambda: state.current_tab() == "rules")
    # Same gate: the page reads the library's validation records off disk.
    validate_server("validate", state,
                    active=lambda: state.current_tab() == "validate")
    publish_server("publish", state)
    summary_export_server("summary_export", state)
    # where a curve from another source comes from: opened by gallery tiles,
    # the Table and the mapping chips through one input
    source_panel_server(SOURCE_PANEL_ID, state)
    # and where the owner chooses one (REF-15): the panel, the Table and the
    # mapping open it through one input too
    source_dialog_server(SOURCE_DIALOG_ID, state)

    # Standalone phase workspace instances (app.R:246-249); the analysis
    # workspace hosts its own nested copies.
    phase1_server("phase1", state, dialog_mode=True, workspace_scope="standalone")
    phase2_server("phase2", state, workspace_scope="standalone")
    phase3_server("phase3", state, dialog_mode=True, workspace_scope="standalone")
    phase4_server("phase4", state, dialog_mode=True, workspace_scope="standalone")
    analysis_workspace_server("analysis", state)

    # Re-align the discipline/function mapping when metric_config changes
    # (workbook load, session restore, metric add/remove) — port of
    # app.R:260-295. Until the user (or a workbook's function_mappings sheet)
    # takes ownership, the mapping stays seeded from the STAF master library.
    @reactive.effect
    @reactive.event(state.metric_config, ignore_none=False, ignore_init=False)
    def _realign_mapping():
        metric_config = state.metric_config() or {}
        metric_keys = list(metric_config.keys())
        with reactive.isolate():
            current = state.discipline_function_mapping()
            # Curves a pressure-screen build scores without fitting them here
            # (carried forward, from a rung above the hierarchy) never sit in the
            # workbook, and their rows are what places them in the bundle.
            keep = pressure_evidence.reference_keys(state.reference_build())
        realigned = realign_discipline_function_mapping(current, metric_keys, keep)
        if realigned["added"] or realigned["dropped"]:
            state.discipline_function_mapping_confirmed.set(False)
            if realigned["dropped"]:
                logger.warning(
                    "function_mappings: dropping rows for metrics no longer in workbook: %s",
                    ", ".join(realigned["dropped"]),
                )
        with reactive.isolate():
            user_touched = state.mapping_user_touched()
        if not user_touched:
            try:
                seeded = default_discipline_function_mapping(metric_keys, metric_config)
            except Exception as e:  # noqa: BLE001
                logger.warning("STAF default seed failed: %s", e)
                seeded = None
            state.discipline_function_mapping.set(
                seeded if seeded is not None else realigned["mapping"]
            )
            state.discipline_function_mapping_confirmed.set(False)
        else:
            state.discipline_function_mapping.set(realigned["mapping"])
        with reactive.isolate():
            if state.startup_discipline_function_mapping() is None:
                state.startup_discipline_function_mapping.set(
                    state.discipline_function_mapping()
                )

    # Root navigation: the workflow strip requests a page switch via a nonce;
    # the wizard step request is consumed inside the Data & Setup wizard itself.
    @reactive.effect
    @reactive.event(state.nav_request_nonce, ignore_init=True)
    def _handle_nav_request():
        with reactive.isolate():
            target = state.nav_request()
        if target:
            ui.update_navset("main_navbar", selected=target)

    # Not-ready panels ask to jump to the stage that supplies what they are missing.
    # One root-level channel (uihelpers.WORKFLOW_GOTO_INPUT) so a panel rendered
    # inside any module needs no wiring of its own.
    @reactive.effect
    @reactive.event(input[WORKFLOW_GOTO_INPUT])
    def _workflow_goto():
        payload = input[WORKFLOW_GOTO_INPUT]() or {}
        target = payload.get("nav")
        if not target:
            return
        with reactive.isolate():
            state.nav_request.set(target)
            state.nav_request_nonce.set((state.nav_request_nonce() or 0) + 1)
            step = payload.get("step")
            if step is not None:
                state.wizard_step_request.set(int(step))
                state.wizard_step_nonce.set((state.wizard_step_nonce() or 0) + 1)

    # Rule chips anywhere in the app: switch to the Rules page, then hand the
    # rule id to its server, which scrolls to the card once the panel shows.
    @reactive.effect
    @reactive.event(input[RULES_GOTO_INPUT])
    def _rules_goto():
        payload = input[RULES_GOTO_INPUT]() or {}
        rule_id = payload.get("rule")
        if not rule_id:
            return
        with reactive.isolate():
            state.nav_request.set("rules")
            state.nav_request_nonce.set((state.nav_request_nonce() or 0) + 1)
            state.rules_anchor_request.set(str(rule_id))
            state.rules_anchor_nonce.set((state.rules_anchor_nonce() or 0) + 1)

    # Location mirror for the workflow strip's "you are here" highlight.
    @reactive.effect
    def _mirror_current_tab():
        state.current_tab.set(input.main_navbar())

    # ── workspace modal: real artifact backfills (app.R:366-392, 895-919) ───
    def _prepare_phase1(state_, metric, progress):
        if metric is not None and sst.metric_needs_phase1_artifact_refresh(state_, metric):
            sst.ensure_metric_phase1_artifacts(state_, metric, progress=progress)

    def _prepare_phase3(state_, metric, progress):
        if metric is None:
            return
        if sst.metric_needs_phase1_artifact_refresh(state_, metric):
            sst.ensure_metric_phase1_artifacts(state_, metric, progress=progress)
        if sst.metric_needs_phase3_artifact_refresh(state_, metric):
            sst.ensure_metric_phase3_artifacts(state_, metric, progress=progress)

    def _prepare_phase4(state_, metric, progress):
        if metric is not None:
            sst.preload_metric_phase4_workspace(state_, metric, progress=progress)

    def _steps_phase1(state_, metric):
        if sst.metric_needs_phase1_artifact_refresh(state_, metric):
            return sst.count_metric_phase1_backfill_steps(state_, metric)
        return 0

    def _steps_phase3(state_, metric):
        steps = 0
        if sst.metric_needs_phase1_artifact_refresh(state_, metric):
            steps += sst.count_metric_phase1_backfill_steps(state_, metric)
        if sst.metric_needs_phase3_artifact_refresh(state_, metric):
            steps += sst.count_metric_phase3_backfill_steps(state_, metric)
        return steps

    register_workspace_modal(
        input,
        output,
        session,
        state,
        ui_registry={
            "analysis": lambda: analysis_workspace_ui("analysis"),
            "phase1": lambda: phase1_ui("phase1", dialog_mode=True),
            "phase2": lambda: phase2_ui("phase2"),
            "phase3": lambda: phase3_ui("phase3", dialog_mode=True),
            "phase4": lambda: phase4_ui("phase4", dialog_mode=True),
            "summary_export": lambda: summary_export_ui("summary_export"),
        },
        prepare_registry={
            "phase1": _prepare_phase1,
            "phase3": _prepare_phase3,
            "phase4": _prepare_phase4,
        },
        steps_registry={
            "phase1": _steps_phase1,
            "phase3": _steps_phase3,
            "phase4": lambda s, m: sst.count_metric_phase4_preload_steps(s, m),
        },
    )


app = App(app_ui, server, static_assets=WWW_DIR)
