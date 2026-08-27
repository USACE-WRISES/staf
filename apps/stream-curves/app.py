"""StreamCurves — Reference & Regional Curve Development (Python Shiny).

Port of the R app's entry point (``app/app.R``): navbar shell, theme, STAF
cross-app nav, and the Help/About modal. View modules mount here as they are
ported (see ``views/``); the heavy lifting lives in the pure ``streamcurves``
package.
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
from streamcurves.mapping import realign_discipline_function_mapping
from streamcurves.paths import WWW_DIR
from streamcurves.staf_library import default_discipline_function_mapping
from views import state as st
from views import summary_state as sst
from views.analysis_workspace import analysis_workspace_server, analysis_workspace_ui
from views.cross_section import cross_section_server, cross_section_ui
from views.nrsa_explorer import nrsa_explorer_server, nrsa_explorer_ui
from views.region_builder import region_builder_server, region_builder_ui
from views.data_overview import data_overview_server, data_overview_ui
from views.stagebar import stagebar_server, stagebar_ui
from views.publish import publish_server, publish_ui
from views.phase1 import phase1_server, phase1_ui
from views.phase2 import phase2_server, phase2_ui
from views.phase3 import phase3_server, phase3_ui
from views.phase4 import phase4_server, phase4_ui
from views.regional_curve import regional_curve_server, regional_curve_ui
from views.state import AppState
from views.summary_export import summary_export_server, summary_export_ui
from views.summary_page import summary_page_server, summary_page_ui
from views.theme import STAF_LINKS, app_theme, bi, fa, versioned_www_asset
from views.uihelpers import WORKFLOW_GOTO_INPUT
from views.workspace_modal import register_workspace_modal

logger = logging.getLogger("streamcurves")

# The methodology config mirrors several engine constants (EASI presets, curve
# gate geometry, DEEP scoring contract). Drift means the published methodology
# misdescribes what the software does, so it is logged loudly at every startup.
# Non-strict here: an analyst can still open the app to inspect the problem.
methodology.verify_mirrors(strict=False)


# --------------------------------------------------------------------------- #
# Help / About modal content — port of app_help_content() (app/app.R:26-58).
# --------------------------------------------------------------------------- #


def app_help_content():
    return ui.TagList(
        ui.tags.p(
            "StreamCurves helps you develop ",
            ui.tags.strong("reference and regional curves"),
            " for geomorphic stream metrics, from your own measurements and/or published ",
            "monitoring data. No pre-formatted workbook is required: add raw data and the app ",
            "helps you classify your columns and set everything up.",
        ),
        ui.tags.h6("How it works", class_="fw-bold mt-3 mb-1"),
        ui.tags.p(
            "Follow the numbered workflow strip at the top; each stage is a page:",
            class_="text-muted mb-1",
        ),
        ui.tags.ul(
            ui.tags.li(
                ui.tags.strong("1. Region & data"),
                ": choose a region of applicability and gather candidate sites "
                "(published NRSA monitoring sites and/or your own upload).",
            ),
            ui.tags.li(
                ui.tags.strong("2. Screen sites"),
                ": run EASI reference screening and confirm the sites to keep.",
            ),
            ui.tags.li(
                ui.tags.strong("3. Build dataset"),
                ": choose metrics, pull and compile data, classify columns, "
                "and build.",
            ),
            ui.tags.li(
                ui.tags.strong("4. Reference curves"),
                ": run the 4-phase evaluation for each metric and resolve any "
                "flagged curves.",
            ),
            ui.tags.li(
                ui.tags.strong("5. Publish"),
                ": save your project as a Draft file, or publish it to the STAF "
                "assessment library as Preliminary or Final for DEEP to score against.",
            ),
            class_="mb-2",
        ),
        ui.tags.p(
            ui.tags.strong("Tools"),
            " (top bar): ",
            ui.tags.strong("Regional Curves"),
            " develops regional (e.g. bankfull) relationships and ",
            ui.tags.strong("Cross-Sections"),
            " builds per-site geomorphic cross-sections. Both are optional and "
            "ride into the published assessment.",
            class_="text-muted mb-0",
        ),
        ui.tags.p(
            "Use ",
            ui.tags.strong("New / Open / Save"),
            " in the top right to start a project, resume a saved one (or a "
            "library assessment), and save or publish your work.",
            class_="text-muted mb-0",
        ),
        ui.tags.h6("The 4-phase evaluation", class_="fw-bold mt-3 mb-1"),
        ui.tags.p(
            "For each metric you ",
            ui.tags.strong("explore"),
            " candidate stratifications, ",
            ui.tags.strong("verify"),
            " consistency across metrics, ",
            ui.tags.strong("confirm"),
            " your selection, then ",
            ui.tags.strong("build and finalize"),
            " reference curves scored on a 0-1 scale.",
            class_="mb-0",
        ),
        ui.tags.h6("Data sources", class_="fw-bold mt-3 mb-1"),
        ui.tags.p(
            "Use your own upload and/or published sources: NRSA field & lab data, "
            "EPA StreamCAT, USGS StreamStats, Model My Watershed, and USGS 3DEP / NLDI.",
            class_="mb-0",
        ),
    )


app_ui = ui.page_navbar(
    # The navset is a page container, not navigation: every tab header is hidden
    # in curves.css and the workflow strip (views/stagebar.py) does the switching
    # via ui.update_navset. What is left in the top bar is the brand plus the
    # header actions below -- the header shape EASI, SFARI and DEEP all use.
    ui.nav_panel(
        "Data & Setup",
        ui.div(data_overview_ui("data_overview"), class_="mt-3"),
        value="data",
        icon=bi("database"),
    ),
    ui.nav_panel(
        "Reference Curves",
        ui.div(summary_page_ui("summary"), class_="mt-3"),
        value="curves",
        icon=bi("table"),
    ),
    ui.nav_panel(
        "Publish",
        ui.div(publish_ui("publish"), class_="mt-3"),
        value="publish",
        icon=bi("file-earmark-arrow-up"),
    ),
    # Side analyses (run_state.TOOL_KEYS). They need the built dataset but are not
    # stages, so the strip renders them as unnumbered chips past a divider rather
    # than as steps 6 and 7 -- keep these nav values in sync with TOOL_KEYS.
    ui.nav_panel(
        "Regional Curves",
        ui.div(regional_curve_ui("regional"), class_="mt-3"),
        value="regional",
        icon=bi("bezier2"),
    ),
    ui.nav_panel(
        "Cross-Sections",
        ui.div(cross_section_ui("xsec"), class_="mt-3"),
        value="xsec",
        icon=bi("graph-down"),
    ),
    ui.nav_panel(
        "NRSA Explorer",
        ui.div(nrsa_explorer_ui("nrsa"), class_="mt-3"),
        value="nrsa",
        icon=bi("globe-americas"),
    ),
    ui.nav_panel(
        "Region Builder",
        ui.div(region_builder_ui("build"), class_="mt-3"),
        value="build",
        icon=bi("magic"),
    ),
    ui.nav_spacer(),
    # Header actions (mirrors the SFARI/DEEP New / Open / Save idiom; the
    # divider before Help is a border-left, not a glyph).
    ui.nav_control(
        ui.input_action_link(
            "nav_new",
            ui.TagList(bi("plus-circle-fill"), " New"),
            class_="nav-link app-hdr-link",
        )
    ),
    ui.nav_control(
        ui.input_action_link(
            "nav_open",
            ui.TagList(bi("folder2-open"), " Open"),
            class_="nav-link app-hdr-link",
        )
    ),
    ui.nav_control(
        ui.input_action_link(
            "nav_save",
            ui.TagList(fa("floppy-disk"), " Save"),
            class_="nav-link app-hdr-link",
        )
    ),
    # The divider sits here so it separates the file actions from the two meta
    # links. target=_blank keeps an unsaved session from being replaced by the
    # docs site, and is the path the desktop shell turns into "focus launcher"
    # (STAF_LINKS["home"] is rewritten to staf-desktop://home there).
    ui.nav_control(
        ui.tags.a(
            "STAF",
            href=STAF_LINKS["home"],
            target="_blank",
            rel="noopener",
            class_="nav-link app-hdr-link app-hdr-divider",
        )
    ),
    ui.nav_control(
        ui.input_action_link(
            "app_help",
            ui.TagList(bi("question-circle"), " Help"),
            class_="nav-link app-help-link",
        )
    ),
    id="main_navbar",
    selected="data",
    title="StreamCurves",
    window_title="StreamCurves - Reference & Regional Curve Development",
    theme=app_theme,
    header=ui.TagList(
        ui.head_content(
            ui.tags.link(rel="stylesheet", href=versioned_www_asset("styles.css")),
            ui.tags.link(rel="stylesheet", href=versioned_www_asset("curves.css")),
            ui.tags.script(src=versioned_www_asset("curves.js")),
        ),
        stagebar_ui("stagebar"),
        # Static ipywidget/ipyleaflet deps (see views/widget_deps.py) — the
        # TagList renders nothing visible; its dependencies hoist into <head>.
        (static_ipywidget_dependencies() if _HAS_WIDGETS else None),
    ),
    fillable=False,
)


def server(input, output, session):
    state = AppState.fresh()

    stagebar_server("stagebar", state)
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
    publish_server("publish", state)
    summary_export_server("summary_export", state)

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
        realigned = realign_discipline_function_mapping(current, metric_keys)
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

    # Location mirror for the workflow strip's "you are here" highlight.
    @reactive.effect
    def _mirror_current_tab():
        state.current_tab.set(input.main_navbar())

    # ── header actions: New / Open / Save ────────────────────────────────────
    def _do_new():
        st.reset_app_to_startup(state)
        with reactive.isolate():
            state.nav_request.set("data")
            state.nav_request_nonce.set((state.nav_request_nonce() or 0) + 1)
            state.wizard_step_request.set(1)
            state.wizard_step_nonce.set((state.wizard_step_nonce() or 0) + 1)

    @reactive.effect
    @reactive.event(input.nav_new)
    def _nav_new():
        with reactive.isolate():
            loaded = bool(state.app_data_loaded())
        if not loaded:
            _do_new()
            return
        ui.modal_show(
            ui.modal(
                "Start a new project? Unsaved changes are lost. Save first "
                "(top right) to keep the current one.",
                title="Start a new project?",
                easy_close=True,
                footer=ui.TagList(
                    ui.modal_button("Cancel"),
                    ui.input_action_button(
                        "nav_new_confirm", "Clear and start new", class_="btn btn-danger"
                    ),
                ),
            )
        )

    @reactive.effect
    @reactive.event(input.nav_new_confirm)
    def _nav_new_confirm():
        ui.modal_remove()
        _do_new()

    @reactive.effect
    @reactive.event(input.nav_open)
    def _nav_open():
        with reactive.isolate():
            state.open_dialog_nonce.set((state.open_dialog_nonce() or 0) + 1)

    @reactive.effect
    @reactive.event(input.nav_save)
    def _nav_save():
        # Save is the Publish page: Draft (file downloads), Preliminary, Final.
        with reactive.isolate():
            state.nav_request.set("publish")
            state.nav_request_nonce.set((state.nav_request_nonce() or 0) + 1)

    @reactive.effect
    @reactive.event(input.app_help)
    def _show_help():
        ui.modal_show(
            ui.modal(
                app_help_content(),
                title=ui.TagList(bi("info-circle"), " About StreamCurves"),
                easy_close=True,
                footer=ui.modal_button("Close"),
                size="m",
            )
        )

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
