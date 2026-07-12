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

from streamcurves.mapping import realign_discipline_function_mapping
from streamcurves.paths import WWW_DIR
from streamcurves.staf_library import default_discipline_function_mapping
from views import state as st
from views import summary_state as sst
from views.analysis_workspace import analysis_workspace_server, analysis_workspace_ui
from views.cross_section import cross_section_server, cross_section_ui
from views.data_overview import data_overview_server, data_overview_ui
from views.guided import guided_server, guided_ui
from views.library import library_server, library_ui
from views.phase1 import phase1_server, phase1_ui
from views.phase2 import phase2_server, phase2_ui
from views.phase3 import phase3_server, phase3_ui
from views.phase4 import phase4_server, phase4_ui
from views.regional_curve import regional_curve_server, regional_curve_ui
from views.state import AppState
from views.summary_export import summary_export_server, summary_export_ui
from views.summary_page import summary_page_server, summary_page_ui
from views.theme import app_theme, bi, staf_topnav, versioned_www_asset
from views.workspace_modal import register_workspace_modal

logger = logging.getLogger("streamcurves")


# --------------------------------------------------------------------------- #
# Help / About modal content — port of app_help_content() (app/app.R:26-58).
# --------------------------------------------------------------------------- #


def app_help_content():
    return ui.TagList(
        ui.tags.p(
            "StreamCurves helps you develop ",
            ui.tags.strong("reference and regional curves"),
            " for geomorphic stream metrics — from your own measurements and/or published ",
            "monitoring data. No pre-formatted workbook is required: add raw data and the app ",
            "helps you classify your columns and set everything up.",
        ),
        ui.tags.h6("How it works", class_="fw-bold mt-3 mb-1"),
        ui.tags.p("Work left to right through the tabs:", class_="text-muted mb-1"),
        ui.tags.ul(
            ui.tags.li(
                ui.tags.strong("Data & Setup"),
                " — choose a region of applicability, bring or select site data, "
                "pull metrics, and build the dataset.",
            ),
            ui.tags.li(
                ui.tags.strong("Reference Curves"),
                " — run the 4-phase evaluation for each metric.",
            ),
            ui.tags.li(
                ui.tags.strong("Regional Curves"),
                " — develop regional (e.g. bankfull) relationships.",
            ),
            ui.tags.li(
                ui.tags.strong("Cross-Sections"),
                " — build per-site geomorphic cross-sections on demand.",
            ),
            class_="mb-2",
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
    ui.nav_panel(
        "Guided",
        ui.div(guided_ui("guided"), class_="mt-3"),
        value="guided",
        icon=bi("bullseye"),
    ),
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
        "Library",
        ui.div(library_ui("library"), class_="mt-3"),
        value="library",
        icon=bi("layers"),
    ),
    ui.nav_spacer(),
    ui.nav_control(
        ui.input_action_link(
            "app_help",
            ui.TagList(bi("question-circle"), " Help"),
            class_="nav-link app-help-link",
        )
    ),
    id="main_navbar",
    selected="guided",
    title="StreamCurves",
    window_title="StreamCurves - Reference & Regional Curve Development",
    theme=app_theme,
    header=ui.TagList(
        ui.head_content(
            ui.tags.link(rel="stylesheet", href=versioned_www_asset("styles.css")),
            ui.tags.link(rel="stylesheet", href=versioned_www_asset("curves.css")),
            ui.tags.script(src=versioned_www_asset("curves.js")),
        ),
        staf_topnav(),
        # Static ipywidget/ipyleaflet deps (see views/widget_deps.py) — the
        # TagList renders nothing visible; its dependencies hoist into <head>.
        (static_ipywidget_dependencies() if _HAS_WIDGETS else None),
    ),
    fillable=False,
)


def server(input, output, session):
    state = AppState.fresh()

    guided_server("guided", state)
    data_overview_server("data_overview", state)
    summary_page_server("summary", state)
    regional_curve_server("regional", state)
    cross_section_server("xsec", state)
    library_server("library", state)
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

    # Root navigation: the guided home requests a tab switch via a nonce; the
    # wizard step request is consumed inside the Data & Setup wizard itself.
    @reactive.effect
    @reactive.event(state.nav_request_nonce, ignore_init=True)
    def _handle_nav_request():
        with reactive.isolate():
            target = state.nav_request()
        if target:
            ui.update_navs("main_navbar", selected=target)

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
