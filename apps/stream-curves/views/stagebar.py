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
from views.uihelpers import guard

# Short labels + tool tooltips are canonical in run_state (the About modal
# reads them too); local names kept for the render code below.
_SHORT = rs.STAGE_SHORT

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
    "validate": "stage_validate",
}

# Side analysis -> click input id. Kept parallel to _CLICK, not merged into it:
# these route straight to a nav value, with no stage landing and no wizard step.
_TOOL_CLICK = {"regional": "tool_regional", "xsec": "tool_xsec",
               "nrsa": "tool_nrsa", "build": "tool_build",
               "rules": "tool_rules"}

# Every icon here must exist in the vendored www/vendor/bs-icons.json subset or
# bi() raises at render (test_rules_page_nav pins the whole map against it).
_TOOL_ICON = {"regional": "bezier2", "xsec": "graph-down",
              "nrsa": "globe-americas", "build": "magic",
              "rules": "ui-checks"}

# The Tools menu toggle glyph (same vendored-subset rule as _TOOL_ICON).
_TOOLS_MENU_ICON = "tools"

_TOOL_TITLE = rs.TOOL_TITLES


def stagebar_ui(id: str):
    ns = module.resolve_id(id)
    return ui.output_ui(ns("stage_bar"))


@module.server
def stagebar_server(input, output, session, state: AppState):
    ns = session.ns
    # The strip's last computed snapshot, for _stage_detail to reuse (plain
    # holder, deliberately not reactive: reading it must never add a
    # dependency).
    _last_snap: dict = {"snap": None}

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
        # The Validate stage's status inputs (run_snapshot isolates its reads).
        state.validation_records()
        state.assessment_source()
        precheck = state.precheck_df()
        tasks = dict(state.tasks_running() or {})
        tab = state.current_tab()
        view = state.data_setup_view()
        wiz_step = state.wizard_current_step()
        active_section = state.workspace_section()
        curves_section = state.curves_section()

        snap = ap.run_snapshot(state)
        _last_snap["snap"] = snap
        statuses = rs.derive_stage_status(snap, tasks)
        n_flagged = len(rs.flagged_metrics(snap.get("curve_review") or {}))
        current = rs.current_stage(tab, view, wiz_step)
        tool = rs.current_tool(tab)
        has_data = state.data() is not None
        n_precheck_warnings = precheck_summary(precheck)["n_warnings"]

        def _sub_row(stage_key: str):
            # Wizard sub-step chips for the current stage, hung directly under
            # its pill (absolute, overlaying the second line the subrow
            # reserves). Wizard views only: the workspace is stage 4's own
            # surface and gets _section_row.
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

        # The current stage's sub-step (or section) chips hang under its own
        # pill, so they read as that stage's children wherever it sits in the
        # bar. The subrow below reserves the line they overlay.
        sub = (_sub_row(current) or _section_row(current)) if current else None

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
            group_sub = sub if key == current else None
            group_cls = ("stage-group has-substeps" if group_sub is not None
                         else "stage-group")
            groups.append(ui.div(pill, group_sub, class_=group_cls))
            if i < len(rs.STAGE_KEYS) - 1:
                groups.append(ui.tags.span(class_="stage-connector"))

        # Side analyses, folded into one Tools menu. No numbers: only the
        # numbered stages are numbered, so nothing else ever reads as a step.
        # While a tool page is open the toggle wears that tool's name, which
        # (with the dimmed pills) is what says where you are.
        items = []
        for key in rs.TOOL_KEYS:
            item_cls = "dropdown-item tool-item"
            if key == tool:
                item_cls += " active"
            elif not has_data and key not in rs.TOOLS_WITHOUT_DATA:
                # Dimmed, never disabled: the page's no_data_alert() is what
                # explains the prerequisite, same as every stage.
                item_cls += " tool-blocked"
            items.append(ui.tags.li(ui.input_action_link(
                ns(_TOOL_CLICK[key]),
                ui.TagList(bi(_TOOL_ICON[key]), rs.TOOL_LABELS[key]),
                class_=item_cls,
                title=_TOOL_TITLE[key],
            )))
        toggle = ui.tags.a(
            bi(_TOOLS_MENU_ICON),
            ui.tags.span(rs.TOOL_LABELS[tool] if tool else "Tools",
                         class_="tool-label"),
            class_="tool-chip dropdown-toggle" + (" active" if tool else ""),
            href="#",
            title="Side analyses and tools",
            # display=static: pure-CSS right alignment (dropdown-menu-end)
            # instead of Popper, whose first async update can lag the click.
            **{"data-bs-toggle": "dropdown", "data-bs-display": "static",
               "aria-expanded": "false"},
        )
        tools = ui.div(
            toggle,
            ui.tags.ul(*items, class_="dropdown-menu dropdown-menu-end"),
            class_="stage-tools dropdown",
        )

        hint = None
        if snap["has_region"] and not snap["region_is_ecoregion"]:
            hint = ui.div(
                bi("info-circle"),
                f" Your region is a {snap['region_kind']}; every stage and tool "
                "still works.",
                class_="stage-bar-hint")

        # No stage is current inside a side analysis, so dimming the numbered
        # pills is what says "you stepped out" -- they would otherwise sit there
        # fully lit with nothing highlighted.
        bar_cls = "stage-bar aside" if tool else "stage-bar"
        # Row 1 is the numbered stages alone, full width. Row 2 is always
        # rendered and holds only the Tools menu, right-aligned: its min-height
        # reserves the line the current stage's sub-step chips overlay (they
        # hang absolutely from their stage group above), so the strip height
        # never jumps as the current stage changes and the steps are never
        # squeezed by the tools.
        subrow = ui.div(tools, class_="stage-bar-subrow")
        return ui.div(
            ui.div(*groups, class_=bar_cls),
            subrow,
            hint,
            class_="stage-bar-shell",
        )

    # ── navigation: every stage lands on its page ───────────────────────────
    def _stage_detail(stage_key: str) -> dict:
        """This pill's own status row for the click.

        Reuses the snapshot the strip computed on its last render: every
        snapshot input is a declared dependency of that render, so the cache is
        at most one flush stale, and it only feeds the blocked-stage toast.
        Recomputing it here doubled the click's cost (the snapshot is the
        expensive part). tasks_running is re-read fresh so RUNNING never lags.
        """
        snap = _last_snap["snap"]
        with reactive.isolate():
            tasks = dict(state.tasks_running() or {})
            if snap is None:
                snap = ap.run_snapshot(state)
        return rs.derive_stage_status(snap, tasks).get(stage_key) or {}

    # tasks_running is keyed by stage, so a job that is not one of the six needs its
    # own phrase; without this the toast reads "Still working on region_build".
    _TASK_LABELS = {"region_build": "a region build"}

    def _go(stage_key: str):
        with reactive.isolate():
            busy = [rs.STAGE_LABELS.get(k) or _TASK_LABELS.get(k) or k
                    for k, v in (state.tasks_running() or {}).items() if v]
        if busy:
            # A stage switch lands mid-flush here, and py-shiny's flush has no
            # re-entrancy guard (see the note on task_flush in views/state.py):
            # two interleaved flushers wedge the session with every output stuck
            # recalculating. Waiting is cheap; a wedged session is not.
            ui.notification_show(
                f"Still working on {busy[0]}. The strip will move once it finishes.",
                type="message", duration=4)
            return
        info = _stage_detail(stage_key)
        if info.get("status") == rs.STAGE_BLOCKED:
            # Blocked stages stay reachable on purpose (see the note on the
            # not-ready panels in views/uihelpers.py) -- you can look ahead. Say
            # why on the way in, reusing the pill's own detail so the toast and
            # the tooltip can never drift apart.
            detail = info.get("detail")
            if detail:
                ui.notification_show(detail, type="message", duration=4)
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
    @guard("open Region and data")
    def _stage_region():
        _go("region_sources")

    @reactive.effect
    @reactive.event(input.stage_screen)
    @guard("open Screen sites")
    def _stage_screen():
        _go("candidate_screening")

    @reactive.effect
    @reactive.event(input.stage_enrich)
    @guard("open Build dataset")
    def _stage_enrich():
        _go("enrichment_build")

    @reactive.effect
    @reactive.event(input.stage_refine)
    @guard("open Refine and map")
    def _stage_refine():
        _go("refine_map")

    @reactive.effect
    @reactive.event(input.stage_review)
    @guard("open Reference curves")
    def _stage_review():
        _go("curve_review")

    @reactive.effect
    @reactive.event(input.stage_publish)
    @guard("open Publish")
    def _stage_publish():
        _go("publish")

    @reactive.effect
    @reactive.event(input.stage_validate)
    @guard("open Validate")
    def _stage_validate():
        _go("validate")

    # Side analyses: a nav value with no stage landing and no wizard step.
    @reactive.effect
    @reactive.event(input.tool_regional)
    @guard("open Regional curves")
    def _tool_regional():
        _request_nav("regional")

    @reactive.effect
    @reactive.event(input.tool_xsec)
    @guard("open Cross-sections")
    def _tool_xsec():
        _request_nav("xsec")

    @reactive.effect
    @reactive.event(input.tool_nrsa)
    @guard("open the NRSA explorer")
    def _tool_nrsa():
        _request_nav("nrsa")

    @reactive.effect
    @reactive.event(input.tool_build)
    @guard("open the Region builder")
    def _tool_build():
        _request_nav("build")

    @reactive.effect
    @reactive.event(input.tool_rules)
    @guard("open the Rules page")
    def _tool_rules():
        _request_nav("rules")

    # Sub-step chips jump straight to their wizard step. Derived from the declared
    # steps rather than a fixed range, so adding one cannot leave its chip dead.
    for _n in sorted({n for steps in rs.STAGE_SUBSTEPS.values() for n, _ in steps}):

        def _mk(n):
            @reactive.effect
            @reactive.event(input[f"substep_{n}"])
            @guard(f"open wizard step {n}")
            def _substep():
                _request_nav("data", wizard_step=n)

        _mk(_n)

    # Section chips pick a workspace panel (Data & Setup owns the switch).
    for _secs in rs.STAGE_SECTIONS.values():
        for _value, _ in _secs:

            def _mk_section(value):
                @reactive.effect
                @reactive.event(input[f"section_{value}"])
                @guard(f"open the {value} panel")
                def _section():
                    with reactive.isolate():
                        state.workspace_section_request.set(value)
                        state.workspace_section_nonce.set(
                            (state.workspace_section_nonce() or 0) + 1)

            _mk_section(_value)
