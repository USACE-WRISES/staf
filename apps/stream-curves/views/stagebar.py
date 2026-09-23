"""The stage strip and the Project panel: the app's navigation, HYPE Desktop's shape.

The strip is one row under the header: the seven numbered stages (Region & data / Screen
sites / Build dataset / Refine & map / Reference curves / Publish / Validate) with live status
from ``run_state.derive_stage_status`` in HYPE's states (done, a check; running, a spinner;
attention, an amber ring; locked; todo) and the "you are here" stage filled navy.

The Project panel (views/project_panel.py) is the card at the left: the project, the same
seven stages with the current stage's steps (the wizard's sub-steps, or the workspace and
Reference curves sections) under it, and the tools. It replaces the strip's old second row of
sub-step chips and its Tools menu.

Both render from ONE snapshot (``_view``, a reactive calc: the snapshot is the expensive
part), and every click on either arrives on ONE input: the chips and rows are
``<button data-jump="...">`` and www/shell.js posts the target to ``jump``. Separate action
links for the same targets would put duplicate input ids on the page. The dispatcher routes
every stage to its page (``run_state.stage_landing``), refuses to move while a job runs, and
says why a blocked stage is blocked on the way in: stages stay reachable so you can look
ahead.

The strip assumes a Level III ecoregion; a state or custom region adds a one-line hint (the
stages and tools work with any region).
"""
from __future__ import annotations

from shiny import module, reactive, render, ui

from streamcurves import run_state as rs
from streamcurves import workspace as ws
from streamcurves.precheck import precheck_summary
from views import assessment_publish as ap
from views import project_panel as pp
from views.state import AppState
from views.theme import bi
from views.uihelpers import guard

# Short labels + tool tooltips are canonical in run_state (the About modal reads them too).
_SHORT = rs.STAGE_SHORT

#: Every stage's jump target (test_stagebar_nav pins one per stage key).
STAGE_TARGETS = {key: pp.stage_target(key) for key in rs.STAGE_KEYS}
#: Every tool's jump target.
TOOL_TARGETS = {key: pp.tool_target(key) for key in rs.TOOL_KEYS}


def stagebar_ui(id: str):
    ns = module.resolve_id(id)
    return ui.output_ui(ns("stage_bar"), class_="sc-stagebar-host")


def project_panel_ui(id: str):
    """The panel card. Its head collapses it (www/shell.js); its body is the tree."""
    ns = module.resolve_id(id)
    return ui.div(
        ui.div(ui.span(class_="sc-panel-caret"), ui.span("Project", class_="sc-panel-title"),
               class_="sc-panel-head", title="Show or hide the project panel"),
        ui.div(ui.output_ui(ns("panel_body")), class_="sc-panel-body",
               **{"data-jump-to": ns("jump")}),
        id="sc-panel", class_="sc-panel")


def tools_allowed() -> set[str]:
    """The Region builder needs a STAF checkout (its runs live under notes/); every other
    tool works in any copy."""
    keys = set(rs.TOOL_KEYS)
    if not ws.is_checkout():
        keys.discard("build")
    return keys


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

    # ── one snapshot for the strip and the panel ────────────────────────────
    @reactive.calc
    def _view() -> dict:
        # run_snapshot() isolates every read it makes, so declare the snapshot's
        # inputs as dependencies here. Reads only: nothing here may write a reactive,
        # or it would loop like the old _screen_done bug.
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
        state.owner_curve_decisions()
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
        snap = ap.run_snapshot(state)
        return {
            "snap": snap,
            "statuses": rs.derive_stage_status(snap, tasks),
            "n_flagged": len(rs.flagged_metrics(snap.get("curve_review") or {})),
            "current": rs.current_stage(tab, view, wiz_step),
            "tool": rs.current_tool(tab),
            "has_data": state.data() is not None,
            "n_precheck_warnings": precheck_summary(precheck)["n_warnings"],
            "tab": tab,
            "view": view,
            "wiz_step": wiz_step,
            "active_section": state.workspace_section(),
            "curves_section": state.curves_section(),
        }

    # ── the strip ───────────────────────────────────────────────────────────
    @render.ui
    def stage_bar():
        v = _view()
        snap, statuses = v["snap"], v["statuses"]
        chips = []
        for i, key in enumerate(rs.STAGE_KEYS):
            info = statuses[key]
            cls = "sc-stage " + pp.STATE_CLASS.get(info["status"], "st-locked")
            if key == v["current"]:
                cls += " active"
            count = None
            if key == "curve_review" and v["n_flagged"] and info["status"] == rs.STAGE_ATTENTION:
                count = ui.tags.span(str(v["n_flagged"]), class_="sc-stage-count")
            chips.append(ui.tags.button(
                ui.tags.span(str(i + 1), class_="sc-stage-num"),
                ui.tags.span(_SHORT[key], class_="sc-stage-name"),
                count,
                type="button", class_=cls,
                title=f"Step {i + 1}: {rs.STAGE_LABELS[key]}. {info['detail']}",
                **{"data-jump": STAGE_TARGETS[key]}))
            if i < len(rs.STAGE_KEYS) - 1:
                chips.append(ui.tags.span(class_="sc-stage-sep"))
        hint = None
        if snap["has_region"] and not snap["region_is_ecoregion"]:
            hint = ui.div(bi("info-circle"),
                          f" Your region is a {snap['region_kind']}; every stage and tool "
                          "still works.", class_="sc-stagebar-hint")
        # Inside a tool no stage is current, so the chips dim instead: you stepped out of
        # the sequence, and any chip steps back in.
        return ui.div(
            ui.div(*chips, class_="sc-stagebar-scroll"),
            hint,
            class_="sc-stagebar" + (" aside" if v["tool"] else ""),
            **{"data-jump-to": ns("jump")})

    # ── the Project panel ───────────────────────────────────────────────────
    @render.ui
    def panel_body():
        v = _view()
        project = pp.project_summary(state.project_meta(), state.project_file())
        return pp.tree(v, project=project, tools_allowed=tools_allowed())

    # ── navigation: one input, one guarded dispatcher ───────────────────────
    def _stage_detail(stage_key: str) -> dict:
        """This stage's status row for the click, from the shared snapshot; tasks_running
        is read fresh so RUNNING never lags."""
        with reactive.isolate():
            v = _view()
            tasks = dict(state.tasks_running() or {})
        return rs.derive_stage_status(v["snap"], tasks).get(stage_key) or {}

    # tasks_running is keyed by stage, so a job that is not one of the stages needs its
    # own phrase; without this the toast reads "Still working on region_build".
    _TASK_LABELS = {"region_build": "a region build"}

    def _refuse_while_busy() -> bool:
        """True (after saying so) while a job runs. A switch lands mid-flush, and
        py-shiny's flush has no re-entrancy guard (see the note on task_flush in
        views/state.py): two interleaved flushers wedge the session with every output
        stuck recalculating. Waiting is cheap; a wedged session is not."""
        with reactive.isolate():
            busy = [rs.STAGE_LABELS.get(k) or _TASK_LABELS.get(k) or k
                    for k, v in (state.tasks_running() or {}).items() if v]
        if not busy:
            return False
        ui.notification_show(
            f"Still working on {busy[0]}. The strip will move once it finishes.",
            type="message", duration=4)
        return True

    def _go(stage_key: str):
        if _refuse_while_busy():
            return
        info = _stage_detail(stage_key)
        if info.get("status") == rs.STAGE_BLOCKED:
            # Blocked stages stay reachable on purpose (see the note on the
            # not-ready panels in views/uihelpers.py) -- you can look ahead. Say
            # why on the way in, reusing the chip's own detail so the toast and
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

    #: Wizard steps a "substep:<n>" target may name, derived from the declared steps
    #: rather than a fixed range, so adding one cannot leave its row dead.
    _SUBSTEPS = sorted({n for steps in rs.STAGE_SUBSTEPS.values() for n, _ in steps})
    _SECTIONS = {value for secs in rs.STAGE_SECTIONS.values() for value, _ in secs}

    @reactive.effect
    @reactive.event(input.jump)
    @guard("navigate")
    def _jump():
        target = str((input.jump() or {}).get("target") or "")
        kind, _, key = target.partition(":")
        if kind == "stage" and key in STAGE_TARGETS:
            _go(key)
        elif kind == "substep" and key.isdigit() and int(key) in _SUBSTEPS:
            if _refuse_while_busy():
                return
            _request_nav("data", wizard_step=int(key))
        elif kind == "section" and key in _SECTIONS:
            # Data & Setup (workspace) and the Reference Curves page each take their own
            # values off this one channel.
            with reactive.isolate():
                state.workspace_section_request.set(key)
                state.workspace_section_nonce.set(
                    (state.workspace_section_nonce() or 0) + 1)
        elif kind == "tool" and key in TOOL_TARGETS:
            if key not in tools_allowed():
                return
            _request_nav(key)
        elif target == "project":
            opener = state.hooks.get("project_properties")
            if opener is not None:
                opener()
