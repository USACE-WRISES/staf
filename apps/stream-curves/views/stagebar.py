"""Workflow stage banner — a slim numbered strip under the app's top bars.

Five clickable steps (Region & Sources / Candidate Sites & EASI Screening /
Enrichment, Build & Classification / Curve Analysis & Flagged Review /
Preliminary Package & Publish) rendered left to right with live status from
``run_state.derive_stage_status`` over a snapshot of AppState. Mounted in the
``page_navbar`` header so it shows on every tab; clicks route the shell via the
``nav_request`` / ``wizard_step_request`` nonces. Step 4 opens the flagged-curve
review queue when curves need review, otherwise it opens Reference Curves.

The banner assumes a Level III ecoregion; a state/custom region shows a one-line
hint under the strip (the tabs themselves work with any region).
"""
from __future__ import annotations

from shiny import module, reactive, render, ui

from streamcurves import curve_automation as ca
from streamcurves import run_state as rs
from views import assessment_publish as ap
from views import state as st
from views.state import AppState
from views.theme import bi

# Wizard step numbers inside the Data & Setup import wizard (1-based).
STEP_REGION = 1
STEP_SITES = 3
STEP_COMPILE = 5

# Banner-local short labels; the full rs.STAGE_LABELS ride in the tooltip.
_SHORT = {
    "region_sources": "Region",
    "candidate_screening": "Screen sites",
    "enrichment_build": "Enrich & build",
    "curve_review": "Review curves",
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
    "curve_review": "stage_review",
    "publish": "stage_publish",
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

    # ── the banner ──────────────────────────────────────────────────────────
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
        tasks = dict(state.tasks_running() or {})

        snap = ap.run_snapshot(state)
        statuses = rs.derive_stage_status(snap, tasks)
        n_flagged = len(rs.flagged_metrics(snap.get("curve_review") or {}))

        pills = []
        for i, key in enumerate(rs.STAGE_KEYS):
            info = statuses[key]
            mod = _STATUS_CLS.get(info["status"], "blocked")
            circle = "✓" if info["status"] == rs.STAGE_DONE else str(i + 1)
            count = None
            if (key == "curve_review" and n_flagged
                    and info["status"] == rs.STAGE_ATTENTION):
                count = ui.tags.span(str(n_flagged), class_="stage-count")
            pills.append(ui.input_action_link(
                ns(_CLICK[key]),
                ui.TagList(
                    ui.tags.span(circle, class_="stage-num"),
                    ui.tags.span(_SHORT[key], class_="stage-label"),
                    count,
                ),
                class_=f"stage-pill stage-{mod}",
                title=f"Step {i + 1}: {rs.STAGE_LABELS[key]}. {info['detail']}",
            ))
            if i < len(rs.STAGE_KEYS) - 1:
                pills.append(ui.tags.span(class_="stage-connector"))

        hint = None
        if snap["has_region"] and not snap["region_is_ecoregion"]:
            hint = ui.div(
                bi("info-circle"),
                f" Your region is a {snap['region_kind']}. The staged workflow "
                "assumes a Level III ecoregion; the tabs work directly with any "
                "region.",
                class_="stage-bar-hint")

        return ui.div(ui.div(*pills, class_="stage-bar"), hint,
                      class_="stage-bar-shell")

    # ── navigation ──────────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.stage_region)
    def _stage_region():
        _request_nav("data", wizard_step=STEP_REGION)

    @reactive.effect
    @reactive.event(input.stage_screen)
    def _stage_screen():
        _request_nav("data", wizard_step=STEP_SITES)

    @reactive.effect
    @reactive.event(input.stage_enrich)
    def _stage_enrich():
        _request_nav("data", wizard_step=STEP_COMPILE)

    @reactive.effect
    @reactive.event(input.stage_review)
    def _stage_review():
        # Flagged curves open the review queue; otherwise go to the curves tab.
        choices = _flagged_choices()
        if choices:
            _open_review_modal(choices)
        else:
            _request_nav("curves")

    @reactive.effect
    @reactive.event(input.stage_publish)
    def _stage_publish():
        _request_nav("publish")

    # ── flagged-curve review queue modal ────────────────────────────────────
    def _flagged_choices() -> dict[str, str]:
        with reactive.isolate():
            review = state.curve_review() or {}
        out: dict[str, str] = {}
        for metric in rs.flagged_metrics(review):
            entry = review.get(metric) or {}
            reason = (entry.get("reasons") or ["needs review"])[0]
            out[metric] = f"{metric} ({entry.get('status', '')}: {reason})"
        return out

    def _open_review_modal(choices: dict[str, str]) -> None:
        ui.modal_show(ui.modal(
            ui.p("Resolve each flagged curve: adjust and rerun it, accept it with a "
                 "rationale, or remove it from the published scope.",
                 class_="text-muted small"),
            ui.input_select(ns("review_metric"), "Flagged curve", choices=choices,
                            width="100%"),
            ui.input_text_area(ns("review_note"), "Rationale (required to accept or remove)",
                               width="100%", height="70px"),
            ui.div(
                ui.input_action_button(ns("review_adjust"), "Adjust and rerun",
                                       class_="btn btn-sm btn-outline-primary"),
                ui.input_action_button(ns("review_accept"), "Accept with rationale",
                                       class_="btn btn-sm btn-outline-success ms-1"),
                ui.input_action_button(ns("review_remove"), "Remove from scope",
                                       class_="btn btn-sm btn-outline-danger ms-1"),
                class_="mt-2"),
            title="Flagged curve review",
            easy_close=True, size="l", footer=ui.modal_button("Close")))

    def _selected_metric() -> str | None:
        with reactive.isolate():
            try:
                return input.review_metric()
            except Exception:  # noqa: BLE001
                return None

    @reactive.effect
    @reactive.event(input.review_accept)
    def _review_accept():
        metric = _selected_metric()
        note = (input.review_note() or "").strip()
        if not metric:
            return
        if not note:
            ui.notification_show("Add a rationale before accepting.", type="warning",
                                 duration=5)
            return
        ca.set_review_decision(state, metric, rs.DECISION_FINALIZED, note=note,
                               actor="reviewer")
        ui.notification_show(f"Accepted {metric}.", type="message", duration=4)
        ui.modal_remove()

    @reactive.effect
    @reactive.event(input.review_remove)
    def _review_remove():
        metric = _selected_metric()
        note = (input.review_note() or "").strip()
        if not metric:
            return
        if not note:
            ui.notification_show("Add a rationale before removing.", type="warning",
                                 duration=5)
            return
        ca.set_review_decision(state, metric, rs.DECISION_REMOVED, note=note,
                               actor="reviewer")
        ui.notification_show(f"Removed {metric} from scope.", type="message", duration=4)
        ui.modal_remove()

    @reactive.effect
    @reactive.event(input.review_adjust)
    def _review_adjust():
        metric = _selected_metric()
        if not metric:
            return
        with reactive.isolate():
            state.current_metric.set(metric)
        ui.modal_remove()
        # Route to Reference Curves and open the analysis workspace for this metric.
        _request_nav("curves")
        st.launch_workspace_modal(state, "analysis", metric)
