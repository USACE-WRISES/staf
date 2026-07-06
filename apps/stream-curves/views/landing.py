"""Landing page — port of app/modules/mod_landing_v2.R (4-phase orientation +
Reset App control)."""

from __future__ import annotations

from shiny import module, reactive, ui

from views import state as st
from views.state import AppState
from views.theme import fa


def workflow_phase_card(number: int, title: str, description: str, detail=None):
    phase_color = {1: "#0d6efd", 2: "#6610f2", 3: "#198754", 4: "#fd7e14"}.get(
        number, "#0d6efd"
    )
    detail_el = None
    if detail is not None:
        detail_el = ui.tags.details(
            ui.tags.summary("More detail", style="cursor: pointer; color: #0d6efd;"),
            ui.div(detail, style="margin-top: 4px;"),
            style="margin-top: 4px; font-size: 0.82rem;",
        )
    return ui.div(
        ui.div(
            str(number),
            class_="workflow-number flex-shrink-0 me-3",
            style=(
                f"width: 36px; height: 36px; border-radius: 50%;"
                f" background-color: {phase_color}; color: white; display: flex;"
                " align-items: center; justify-content: center;"
                " font-weight: 700; font-size: 0.9rem;"
            ),
        ),
        ui.div(
            ui.tags.strong(title, style="font-size: 0.95rem;"),
            ui.tags.div(description, class_="text-muted", style="font-size: 0.82rem;"),
            detail_el,
        ),
        class_="workflow-step d-flex align-items-start mb-1",
    )


def workflow_phase_arrow():
    return ui.div(
        "│", style="margin-left: 16px; color: #adb5bd; font-size: 1.1rem; line-height: 1;"
    )


@module.ui
def landing_ui():
    return ui.TagList(
        ui.card(
            ui.card_header("StreamCurves", class_="bg-primary text-white"),
            ui.card_body(
                ui.tags.p(
                    "This application guides you through a structured, 4-phase evaluation "
                    "of stratification variables and reference curve development for "
                    "geomorphic metrics. Upload your own dataset to begin the workflow "
                    "and run the analysis."
                ),
                ui.tags.p(
                    "For each metric, you will explore candidate stratifications, verify "
                    "consistency across metrics, confirm your selection, then build and "
                    "finalize reference curves scored on a 0–1 scale."
                ),
            ),
            class_="border-primary mb-3",
        ),
        ui.layout_columns(
            ui.card(
                ui.card_header("4-Phase Workflow"),
                ui.card_body(
                    ui.div(
                        workflow_phase_card(
                            1,
                            "Phase 1: Explore",
                            "Initial screening and effect size analysis",
                            detail=ui.TagList(
                                ui.tags.p(
                                    "Run Kruskal-Wallis tests and compute effect sizes for "
                                    "candidate stratifications. Review boxplots, mark candidates "
                                    "as Promising, Possible, or Not Promising."
                                ),
                                ui.tags.p(
                                    ui.tags.strong("Per-metric:"),
                                    " Select a metric, pick stratifications, and run screening "
                                    "+ effect size in a single pass.",
                                ),
                            ),
                        ),
                        workflow_phase_arrow(),
                        workflow_phase_card(
                            2,
                            "Phase 2: Compare",
                            "Cross-metric consistency analysis",
                            detail=ui.TagList(
                                ui.tags.p(
                                    "After completing Phase 1 for at least 2 metrics, compare "
                                    "stratification performance across all metrics using a "
                                    "support score heatmap."
                                ),
                                ui.tags.p(
                                    ui.tags.strong("Cross-metric:"),
                                    " Identify broad-use candidates that perform consistently "
                                    "vs. metric-specific ones.",
                                ),
                            ),
                        ),
                        workflow_phase_arrow(),
                        workflow_phase_card(
                            3,
                            "Phase 3: Verify",
                            "Focused verification of finalist stratifications",
                            detail=ui.TagList(
                                ui.tags.p(
                                    "For each metric, verify the top candidate stratifications "
                                    "through pattern stability (LOESS), feasibility assessment "
                                    "(sample sizes), and interpretability review."
                                ),
                                ui.tags.p(
                                    ui.tags.strong("Per-metric:"),
                                    " Confirm or reject finalist stratifications and select one "
                                    "to carry forward into model building.",
                                ),
                            ),
                        ),
                        workflow_phase_arrow(),
                        workflow_phase_card(
                            4,
                            "Phase 4: Finalize",
                            "Empirical scoring curves from reference-site distributions",
                            detail=ui.TagList(
                                ui.tags.p(
                                    "Compute descriptive statistics (Q25, Q75, IQR) per stratum "
                                    "and build the piecewise-linear scoring curve directly from "
                                    "the empirical distribution of reference-standard sites."
                                ),
                                ui.tags.p(
                                    ui.tags.strong("No model fitting required"),
                                    " — thresholds come directly from the observed data.",
                                ),
                            ),
                        ),
                        class_="workflow-roadmap",
                    )
                ),
            ),
            ui.card(
                ui.card_header("How to Use This App", class_="bg-info text-white"),
                ui.card_body(
                    ui.tags.ol(
                        ui.tags.li(
                            "Start at ",
                            ui.tags.strong("Data & Setup"),
                            " to review the dataset, upload custom data, or load a "
                            "previous session.",
                        ),
                        ui.tags.li(
                            "Open ",
                            ui.tags.strong("Reference Curves"),
                            " to track progress across metrics, edit carried-forward "
                            "stratifications, and recompute reference curves.",
                        ),
                        ui.tags.li(
                            "Expand a metric row and use ",
                            ui.tags.strong("Open Phase"),
                            " to launch the Phase 1, Phase 2, Phase 3, or Phase 4 workspace "
                            "for that workflow step.",
                        ),
                        ui.tags.li(
                            "Use the ",
                            ui.tags.strong("Phase 1"),
                            " workspace to run screening for the metric and mark promising "
                            "candidates.",
                        ),
                        ui.tags.li(
                            "Open ",
                            ui.tags.strong("Phase 2"),
                            " to compare stratification consistency across metrics after "
                            "screening at least 2 metrics.",
                        ),
                        ui.tags.li(
                            "Use ",
                            ui.tags.strong("Phase 3"),
                            " to review pattern stability, feasibility, and interpretability "
                            "before confirming a stratification.",
                        ),
                        ui.tags.li(
                            "Use ",
                            ui.tags.strong("Phase 4"),
                            " to review descriptive statistics and finalize the scoring curve.",
                        ),
                        ui.tags.li(
                            "Use ",
                            ui.tags.strong("Export"),
                            " from the Reference Curves toolbar to download results and "
                            "generate reports.",
                        ),
                    ),
                    ui.tags.hr(),
                    ui.div(
                        ui.input_action_button(
                            "reset_analysis",
                            ui.TagList(fa("arrows-rotate"), " Reset App"),
                            class_="btn btn-outline-danger",
                        ),
                        ui.tags.span(
                            "Clears loaded data, configuration changes, and all completed "
                            "analyses.",
                            class_="text-muted",
                            style="font-size: 0.85rem;",
                        ),
                        class_="d-flex align-items-center gap-2",
                    ),
                ),
                class_="border-info",
            ),
            col_widths=[7, 5],
        ),
    )


@module.server
def landing_server(input, output, session, state: AppState):
    @reactive.effect
    @reactive.event(input.reset_analysis)
    def _confirm_dialog():
        ui.modal_show(
            ui.modal(
                "This will return the app to its startup state. Loaded data, "
                "configuration changes, cached results, the decision log, and "
                "all phase outputs will be cleared. This cannot be undone.",
                title="Reset App",
                footer=ui.TagList(
                    ui.modal_button("Cancel"),
                    # dynamic UI in a module server needs explicit namespacing
                    ui.input_action_button(
                        session.ns("confirm_reset_analysis"),
                        "Reset App",
                        class_="btn btn-danger",
                    ),
                ),
            )
        )

    @reactive.effect
    @reactive.event(input.confirm_reset_analysis)
    def _do_reset():
        ui.modal_remove()
        st.reset_app_to_startup(state)
        ui.notification_show("App reset to startup state.", type="message", duration=3)
