"""Guided home — a compact status board for the StreamCurves reference-curve run.

Five stage cards (Region & Sources / Candidate Sites & EASI Screening / Enrichment,
Build & Classification / Curve Analysis & Flagged Review / Preliminary Package &
Publish), each a status badge + one-line detail + a primary action, derived from
``run_state.derive_stage_status`` over a snapshot of AppState. Actions route the
shell via the ``nav_request`` / ``wizard_step_request`` nonces; the Stage-4 card
opens the flagged-curve review queue. The Advanced tabs stay the deep-dive surface.

Guided is built around a Level III ecoregion; a state/custom region shows a banner
routing to the Advanced tabs instead.
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
STEP_ADD_DATA = 2
STEP_SITES = 3
STEP_COMPILE = 5

# Stage status -> bootstrap badge class + label.
_BADGE = {
    rs.STAGE_DONE: ("bg-success", "Done"),
    rs.STAGE_READY: ("bg-primary", "Ready"),
    rs.STAGE_RUNNING: ("bg-info text-dark", "Running"),
    rs.STAGE_ATTENTION: ("bg-warning text-dark", "Needs review"),
    rs.STAGE_BLOCKED: ("bg-secondary", "Waiting"),
}

# Stage -> (primary action id, primary label). Secondary handled inline.
_PRIMARY = {
    "region_sources": ("go_region", "Open Data & Setup"),
    "candidate_screening": ("go_screening", "Load & screen sites"),
    "enrichment_build": ("go_enrich", "Compile retained sites"),
    "curve_review": ("open_review", "Review flagged curves"),
    "publish": ("go_publish", "Open Library"),
}


def guided_ui(id: str):
    ns = module.resolve_id(id)
    return ui.div(
        ui.output_ui(ns("guided_banner")),
        ui.output_ui(ns("guided_cards")),
        class_="guided-home",
    )


def _snapshot(state: AppState) -> dict:
    # Shared with the Library publish gate so both read the same facts.
    return ap.run_snapshot(state)


@module.server
def guided_server(input, output, session, state: AppState):
    ns = session.ns

    def _request_nav(value: str, *, wizard_step: int | None = None):
        with reactive.isolate():
            state.nav_request.set(value)
            state.nav_request_nonce.set((state.nav_request_nonce() or 0) + 1)
            if wizard_step is not None:
                state.wizard_step_request.set(int(wizard_step))
                state.wizard_step_nonce.set((state.wizard_step_nonce() or 0) + 1)

    # ── stage cards ─────────────────────────────────────────────────────────
    @render.ui
    def guided_banner():
        snap = _snapshot(state)
        if not snap["has_region"]:
            return ui.div(
                bi("globe-americas"),
                " Start by choosing a Level III ecoregion in Data & Setup. The guided "
                "workflow builds a reference-curve library for that ecoregion.",
                class_="alert alert-info py-2 mb-3")
        if not snap["region_is_ecoregion"]:
            return ui.div(
                bi("info-circle"),
                f" Your region is a {snap['region_kind']}. The guided workflow is built "
                "around a Level III ecoregion; use the Advanced tabs (Data & Setup, "
                "Reference Curves) to work with this region.",
                ui.input_action_button(ns("go_advanced"), "Open Data & Setup",
                                       class_="btn btn-sm btn-outline-primary ms-2"),
                class_="alert alert-warning py-2 mb-3")
        return None

    @render.ui
    def guided_cards():
        snap = _snapshot(state)
        statuses = rs.derive_stage_status(snap, {})
        cards = []
        for key in rs.STAGE_KEYS:
            info = statuses[key]
            badge_cls, badge_txt = _BADGE.get(info["status"], ("bg-secondary", "—"))
            action_id, action_label = _PRIMARY[key]
            disabled = info["status"] == rs.STAGE_BLOCKED and key != "region_sources"
            primary = ui.input_action_button(
                ns(action_id), action_label,
                class_="btn btn-sm btn-outline-primary mt-2",
                disabled="disabled" if disabled else None)
            secondary = None
            if key == "curve_review":
                secondary = ui.input_action_button(
                    ns("go_curves"), "Open Reference Curves",
                    class_="btn btn-sm btn-outline-secondary mt-2 ms-1")
            cards.append(ui.div(
                ui.div(
                    ui.tags.span(str(rs.STAGE_KEYS.index(key) + 1),
                                 class_="guided-step-num"),
                    ui.tags.span(rs.STAGE_LABELS[key], class_="fw-semibold ms-2"),
                    ui.tags.span(badge_txt, class_=f"badge {badge_cls} ms-auto"),
                    class_="d-flex align-items-center"),
                ui.div(info["detail"], class_="text-muted small mt-1"),
                ui.div(primary, secondary),
                class_="guided-card border rounded p-3 mb-2"))

        # Stage 5 readiness checklist.
        checklist = rs.readiness_checklist(snap)
        checklist_ui = ui.div(
            ui.tags.h6("Publish checklist", class_="mb-2 mt-3"),
            ui.tags.ul(
                *[ui.tags.li(
                    ui.tags.span("✓ " if i["ok"] else "○ ",
                                 class_="fw-bold"),
                    i["label"],
                    class_=("text-success" if i["ok"] else "text-muted"))
                  for i in checklist],
                class_="list-unstyled small mb-0"),
            class_="guided-checklist border rounded p-3")

        return ui.div(ui.div(*cards), checklist_ui)

    # ── navigation actions ──────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.go_region)
    def _go_region():
        _request_nav("data", wizard_step=STEP_REGION)

    @reactive.effect
    @reactive.event(input.go_advanced)
    def _go_advanced():
        _request_nav("data", wizard_step=STEP_REGION)

    @reactive.effect
    @reactive.event(input.go_screening)
    def _go_screening():
        _request_nav("data", wizard_step=STEP_SITES)

    @reactive.effect
    @reactive.event(input.go_enrich)
    def _go_enrich():
        _request_nav("data", wizard_step=STEP_COMPILE)

    @reactive.effect
    @reactive.event(input.go_curves)
    def _go_curves():
        _request_nav("curves")

    @reactive.effect
    @reactive.event(input.go_publish)
    def _go_publish():
        _request_nav("library")

    # ── flagged-curve review queue modal ────────────────────────────────────
    def _flagged_choices() -> dict[str, str]:
        with reactive.isolate():
            review = state.curve_review() or {}
        out: dict[str, str] = {}
        for metric in rs.flagged_metrics(review):
            entry = review.get(metric) or {}
            reason = (entry.get("reasons") or ["needs review"])[0]
            out[metric] = f"{metric} — {entry.get('status', '')}: {reason}"
        return out

    @reactive.effect
    @reactive.event(input.open_review)
    def _open_review():
        choices = _flagged_choices()
        if not choices:
            ui.notification_show("No flagged curves to review.", type="message",
                                 duration=4)
            return
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
