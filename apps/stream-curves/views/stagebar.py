"""Workflow strip — the app's only navigation, under the top bar.

Six numbered stages (Region & data / Screen sites / Build dataset /
Refine & map / Reference curves / Publish) rendered left to right with live
status from ``run_state.derive_stage_status`` and a "you are here" highlight
derived from the location mirrors (``current_tab`` / ``data_setup_view`` /
``wizard_current_step``). Mounted in the ``page_navbar`` header so it shows on
every page; clicks route the shell via the ``nav_request`` /
``wizard_step_request`` nonces using the uniform ``run_state.stage_landing``
targets: every stage lands on a page, never a modal. Stage 4 (Refine & map) is
the opened-project workspace; its pill additionally fires the
``workspace_open`` nonce so Data & Setup closes any open wizard back to it.

Past a divider sit the two side analyses (``run_state.TOOL_KEYS``): Regional
curves and Cross-sections. They are unnumbered because they are not steps --
they need the built dataset but produce no stage status and gate no publish.
While one is open no stage is current (``current_stage`` returns None there),
so the numbered pills dim instead: you have stepped out of the sequence, and
clicking any of them steps back in.

The current stage can carry a slim second row of chips: wizard sub-steps
(``run_state.STAGE_SUBSTEPS``) while the Data & Setup wizard is open — the
wizard has no stepper of its own — or page sections
(``run_state.STAGE_SECTIONS``) for the workspace's Workbook / Function
mapping / Pre-run validation panels, which likewise have no stepper of their
own in the page.

The strip assumes a Level III ecoregion; a state/custom region shows a
one-line hint under it (the stages and tools work with any region).
"""
from __future__ import annotations

from shiny import module, reactive, render, ui

from streamcurves import run_state as rs
from streamcurves.precheck import precheck_summary
from views import assessment_publish as ap
from views.state import AppState
from views.theme import bi

# Banner-local short labels; the full rs.STAGE_LABELS ride in the tooltip.
_SHORT = {
    "region_sources": "Region & data",
    "candidate_screening": "Screen sites",
    "enrichment_build": "Build dataset",
    "refine_map": "Refine & map",
    "curve_review": "Reference curves",
    "publish": "Publish",
}

# Stage status -> pill modifier class (see .stage-pill.stage-* in curves.css).
_STATUS_CLS = {
    rs.STAGE_DONE: "done",
    rs.STAGE_READY: "ready",
    rs.STAGE_RUNNING: "running",
    rs.STAGE_ATTENTION: "attention",
    rs.STAGE_BLOCKED: "blocked",
}

# Stage -> click input id.
_CLICK = {
    "region_sources": "stage_region",
    "candidate_screening": "stage_screen",
    "enrichment_build": "stage_enrich",
    "refine_map": "stage_refine",
    "curve_review": "stage_review",
    "publish": "stage_publish",
}

# Side analysis -> click input id. Kept parallel to _CLICK, not merged into it:
# these route straight to a nav value, with no stage landing and no wizard step.
_TOOL_CLICK = {"regional": "tool_regional", "xsec": "tool_xsec"}

# Both icons are already carried by the vendored www/vendor/bs-icons.json subset
# (they are the icons app.py hangs on the two nav panels), so bi() cannot raise.
_TOOL_ICON = {"regional": "bezier2", "xsec": "graph-down"}

# Why each one exists, and what it needs -- the strip is the only place that says so
# now that the tools have no tab of their own.
_TOOL_TITLE = {
    "regional": (
        "Regional / hydraulic geometry curves (Y = a * X^b). A side analysis "
        "outside the staged workflow; needs a built dataset."
    ),
    "xsec": (
        "Survey cross-sections. A side analysis outside the staged workflow; "
        "needs a built dataset."
    ),
}


def stagebar_ui(id: str):
    ns = module.resolve_id(id)
    return ui.output_ui(ns("stage_bar"))


@module.server
def stagebar_server(input, output, session, state: AppState):
    ns = session.ns

    def _request_nav(value: str, *, wizard_step: int | None = None):
        with reactive.isolate():
            state.nav_request.set(value)
            state.nav_request_nonce.set((state.nav_request_nonce() or 0) + 1)
            if wizard_step is not None:
                state.wizard_step_request.set(int(wizard_step))
                state.wizard_step_nonce.set((state.wizard_step_nonce() or 0) + 1)

    # ── the strip ───────────────────────────────────────────────────────────
    @render.ui
    def stage_bar():
        # run_snapshot() isolates every read it makes, and this output is
        # always mounted (header, never suspended), so declare the snapshot's
        # inputs as dependencies here. Reads only: this render fn must never
        # write a reactive, or it would loop like the old _screen_done bug.
        state.region_of_applicability()
        state.run_meta()
        state.easi_screening_sites()
        state.run_stage_status()
        state.data()
        state.curve_review()
        # Refine & map status inputs (mapping-level coverage + confirmed flag).
        state.discipline_function_mapping()
        state.discipline_function_mapping_confirmed()
        state.function_coverage_exceptions()
        state.metric_config()
        # Stratifier diagnostics: they drive the enrichment_build attention state.
        state.strat_config()
        state.all_layer1_results()
        state.phase2_ranking()
        state.summary_available_overrides()
        precheck = state.precheck_df()
        tasks = dict(state.tasks_running() or {})
        tab = state.current_tab()
        view = state.data_setup_view()
        wiz_step = state.wizard_current_step()
        active_section = state.workspace_section()
        curves_section = state.curves_section()

        snap = ap.run_snapshot(state)
        statuses = rs.derive_stage_status(snap, tasks)
        n_flagged = len(rs.flagged_metrics(snap.get("curve_review") or {}))
        current = rs.current_stage(tab, view, wiz_step)
        tool = rs.current_tool(tab)
        has_data = state.data() is not None
        n_precheck_warnings = precheck_summary(precheck)["n_warnings"]

        def _sub_row(stage_key: str):
            # Wizard sub-step chips for the current stage, rendered inside that
            # stage's group so they anchor under their parent pill. Wizard views
            # only: the workspace is stage 4's own surface and gets _section_row.
            if tab != "data" or view not in ("new", "wizard"):
                return None
            subs = rs.STAGE_SUBSTEPS.get(stage_key) or []
            if len(subs) < 2:
                return None
            chips = []
            for step_no, label in subs:
                chip_cls = "substep-chip"
                if wiz_step == step_no:
                    chip_cls += " active"
                chips.append(ui.input_action_link(
                    ns(f"substep_{step_no}"), label, class_=chip_cls,
                ))
            return ui.div(
                ui.tags.span(class_="substep-lead"),  # CSS elbow connector
                *chips,
                class_="stage-substeps",
            )

        def _section_row(stage_key: str):
            # Panel chips for a sectioned page stage (the workspace). Same look
            # and hang as the wizard sub-row -- one chip idiom -- but the values
            # are section names, not wizard steps, and one is always active.
            secs = rs.STAGE_SECTIONS.get(stage_key) or []
            # The workspace's chips show only while the workspace is the Data &
            # Setup view; the Reference Curves chips always show on their stage.
            if not secs or (stage_key == "refine_map" and view != "workspace"):
                return None
            active = {"refine_map": active_section,
                      "curve_review": curves_section}.get(stage_key)
            chips = []
            for value, label in secs:
                chip_cls = "substep-chip"
                if active == value:
                    chip_cls += " active"
                # Validation stays quiet until it has something to say: a count
                # only when metrics need a look (same badge the Reference curves
                # pill uses for flagged curves).
                badge = (
                    ui.tags.span(str(n_precheck_warnings), class_="stage-count")
                    if value == "validation" and n_precheck_warnings
                    else None
                )
                chips.append(ui.input_action_link(
                    ns(f"section_{value}"),
                    ui.TagList(label, badge) if badge is not None else label,
                    class_=chip_cls,
                ))
            return ui.div(
                ui.tags.span(class_="substep-lead"),
                *chips,
                class_="stage-substeps",
            )

        groups = []
        for i, key in enumerate(rs.STAGE_KEYS):
            info = statuses[key]
            mod = _STATUS_CLS.get(info["status"], "blocked")
            circle = "✓" if info["status"] == rs.STAGE_DONE else str(i + 1)
            count = None
            if (key == "curve_review" and n_flagged
                    and info["status"] == rs.STAGE_ATTENTION):
                count = ui.tags.span(str(n_flagged), class_="stage-count")
            cls = f"stage-pill stage-{mod}"
            if key == current:
                cls += " stage-current"
            pill = ui.input_action_link(
                ns(_CLICK[key]),
                ui.TagList(
                    ui.tags.span(circle, class_="stage-num"),
                    ui.tags.span(_SHORT[key], class_="stage-label"),
                    count,
                ),
                class_=cls,
                title=f"Step {i + 1}: {rs.STAGE_LABELS[key]}. {info['detail']}",
            )
            sub = (_sub_row(key) or _section_row(key)) if key == current else None
            # The reservation for the sub-row rides on the group that owns it, not
            # on the shell: the bar wraps at narrow widths, and a shell-level
            # reservation double-counts once the sub-row lands inside a wrapped
            # row -- leaving a band of dead grey under the strip.
            group_cls = "stage-group has-substeps" if sub is not None else "stage-group"
            groups.append(ui.div(pill, sub, class_=group_cls))
            if i < len(rs.STAGE_KEYS) - 1:
                groups.append(ui.tags.span(class_="stage-connector"))

        # Side analyses, past a divider. No numbers: only the six stages are
        # numbered, so nothing else ever reads as a step.
        chips = []
        for key in rs.TOOL_KEYS:
            chip_cls = "tool-chip"
            if key == tool:
                chip_cls += " active"
            elif not has_data:
                # Dimmed, never disabled: the page's no_data_alert() is what
                # explains the prerequisite, same as every stage.
                chip_cls += " tool-blocked"
            chips.append(ui.input_action_link(
                ns(_TOOL_CLICK[key]),
                ui.TagList(
                    bi(_TOOL_ICON[key]),
                    ui.tags.span(rs.TOOL_LABELS[key], class_="tool-label"),
                ),
                class_=chip_cls,
                title=_TOOL_TITLE[key],
            ))
        # A sibling of the pill row, not a member of it: inside .stage-bar the
        # chips join the pills' wrap, so a narrow window drops them onto a row of
        # their own BELOW the sub-step band. As a sibling they stay pinned to the
        # top right whatever the pills do.
        tools = ui.div(*chips, class_="stage-tools")

        hint = None
        if snap["has_region"] and not snap["region_is_ecoregion"]:
            hint = ui.div(
                bi("info-circle"),
                f" Your region is a {snap['region_kind']}. The staged workflow "
                "assumes a Level III ecoregion; the stages and tools still "
                "work with any region.",
                class_="stage-bar-hint")

        # No stage is current inside a side analysis, so dimming the numbered
        # pills is what says "you stepped out" -- they would otherwise sit there
        # fully lit with nothing highlighted.
        bar_cls = "stage-bar aside" if tool else "stage-bar"
        return ui.div(
            ui.div(ui.div(*groups, class_=bar_cls), tools, class_="stage-bar-row"),
            hint,
            class_="stage-bar-shell",
        )

    # ── navigation: every stage lands on its page ───────────────────────────
    def _go(stage_key: str):
        nav_value, wiz = rs.stage_landing(stage_key)
        _request_nav(nav_value, wizard_step=wiz)
        if stage_key == "refine_map":
            # Sectioned page stage (the workspace): also ask Data & Setup to
            # close any open wizard back to the workspace view -- entry_view is
            # sticky, so the nav switch alone would leave the wizard showing.
            with reactive.isolate():
                state.workspace_open_nonce.set(
                    (state.workspace_open_nonce() or 0) + 1)

    @reactive.effect
    @reactive.event(input.stage_region)
    def _stage_region():
        _go("region_sources")

    @reactive.effect
    @reactive.event(input.stage_screen)
    def _stage_screen():
        _go("candidate_screening")

    @reactive.effect
    @reactive.event(input.stage_enrich)
    def _stage_enrich():
        _go("enrichment_build")

    @reactive.effect
    @reactive.event(input.stage_refine)
    def _stage_refine():
        _go("refine_map")

    @reactive.effect
    @reactive.event(input.stage_review)
    def _stage_review():
        _go("curve_review")

    @reactive.effect
    @reactive.event(input.stage_publish)
    def _stage_publish():
        _go("publish")

    # Side analyses: a nav value with no stage landing and no wizard step.
    @reactive.effect
    @reactive.event(input.tool_regional)
    def _tool_regional():
        _request_nav("regional")

    @reactive.effect
    @reactive.event(input.tool_xsec)
    def _tool_xsec():
        _request_nav("xsec")

    # Sub-step chips jump straight to their wizard step.
    for _n in range(1, 8):

        def _mk(n):
            @reactive.effect
            @reactive.event(input[f"substep_{n}"])
            def _substep():
                _request_nav("data", wizard_step=n)

        _mk(_n)

    # Section chips pick a workspace panel (Data & Setup owns the switch).
    for _secs in rs.STAGE_SECTIONS.values():
        for _value, _ in _secs:

            def _mk_section(value):
                @reactive.effect
                @reactive.event(input[f"section_{value}"])
                def _section():
                    with reactive.isolate():
                        state.workspace_section_request.set(value)
                        state.workspace_section_nonce.set(
                            (state.workspace_section_nonce() or 0) + 1)

            _mk_section(_value)
