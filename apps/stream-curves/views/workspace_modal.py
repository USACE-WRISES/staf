"""Workspace modal orchestration — port of app/app.R:297-1148.

Simplified relative to R (see the port plan): every modal type, including
"analysis", renders its body through the reactive ``workspace_modal_body``
output, which py-shiny binds automatically. That removes the R app's
static-insert path and its hacks (``bindWorkspaceModalContent`` /
``Shiny.bindAll``, the ``shown.bs.modal`` client-ready signal, and the
~280-line launch spinner/final-toast adapter chain) — ``ui.Progress`` plus the
reactive loading shell cover the same UX.

The controller is registry-driven so view milestones can plug in without
touching this file:

- ``ui_registry[modal_type]() -> TagChild``  — the ready-stage body
- ``prepare_registry[modal_type](state, metric, progress)`` — artifact backfill
- ``steps_registry[modal_type](state, metric) -> int`` — progress step count
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable

from shiny import reactive, render, req, ui

from views import state as st
from views.state import AppState
from views.theme import fa

logger = logging.getLogger("streamcurves")

MODAL_TYPES = ("analysis", "phase1", "phase2", "phase3", "phase4", "summary_export")


# --------------------------------------------------------------------------- #
# Titles / labels / details (app.R:313-364)
# --------------------------------------------------------------------------- #


def workspace_modal_metric_label(state: AppState, metric: str | None) -> str | None:
    if metric is None or metric == "":
        return None
    with reactive.isolate():
        entry = (state.metric_config() or {}).get(metric) or {}
    return entry.get("display_name") or metric


def workspace_modal_title(state: AppState, modal_type: str, metric: str | None) -> str:
    label = workspace_modal_metric_label(state, metric)
    suffix = f" - {label}" if label else ""
    return {
        "analysis": f"Analysis{suffix}",
        "phase1": f"Phase 1: Explore{suffix}",
        "phase2": "Phase 2: Compare",
        "phase3": f"Phase 3: Verify{suffix}",
        "phase4": f"Phase 4: Finalize{suffix}",
        "summary_export": "Export",
    }.get(modal_type, "Workspace")


def workspace_modal_loading_label(modal_type: str) -> str:
    return {
        "analysis": "Loading analysis workspace...",
        "phase1": "Loading Phase 1 workspace...",
        "phase2": "Loading Phase 2 workspace...",
        "phase3": "Loading Phase 3 workspace...",
        "phase4": "Loading Phase 4 workspace...",
        "summary_export": "Loading Export workspace...",
    }.get(modal_type, "Loading workspace...")


def workspace_modal_loading_default_detail(
    state: AppState, modal_type: str, metric: str | None
) -> str:
    label = workspace_modal_metric_label(state, metric)
    for_label = f" for {label}" if label else ""
    return {
        "analysis": f"Preparing analysis workspace{for_label}.",
        "phase1": f"Preparing screening results{for_label}.",
        "phase2": "Preparing cross-metric consistency results.",
        "phase3": f"Preparing verification results{for_label}.",
        "phase4": f"Preparing reference curve results{for_label}.",
        "summary_export": "Preparing export outputs.",
    }.get(modal_type, "Preparing workspace.")


def workspace_modal_min_shell_seconds(modal_type: str, total_steps: int) -> float:
    """app.R:699-705 — phase2 keeps its loading shell painted >= 0.2 s when
    there is no real backfill work, so the transition doesn't flash."""
    if modal_type == "phase2" and int(total_steps or 0) == 0:
        return 0.2
    return 0.0


# --------------------------------------------------------------------------- #
# Progress notifier (app.R:394-416)
# --------------------------------------------------------------------------- #


class WorkspaceProgressNotifier:
    """Port of make_workspace_progress_notifier — drives ui.Progress and the
    reactive loading-detail line together.

    py-shiny note: unlike R, updates only reach the browser when the event
    loop gets a turn — preparers should ``await notifier.flush()`` between
    steps (heavy work between awaits still blocks, same as R's synchronous
    session, but each completed step paints)."""

    def __init__(self, state: AppState, total_steps: int, message: str, detail=None):
        self._state = state
        self._message = message
        self._current = 0
        self._total = max(int(total_steps), 1)
        self._progress = ui.Progress(min=0, max=self._total)
        self._progress.set(value=0, message=message, detail=detail)

    def set_detail(self, detail: str) -> None:
        self._state.workspace_modal_loading_detail.set(detail)
        self._progress.set(value=self._current, message=self._message, detail=detail)

    def advance(self, detail: str | None = None) -> None:
        if detail is None:
            with reactive.isolate():
                detail = self._state.workspace_modal_loading_detail()
        else:
            self._state.workspace_modal_loading_detail.set(detail)
        self._current = min(self._current + 1, self._total)
        self._progress.set(value=self._current, message=self._message, detail=detail)

    async def flush(self) -> None:
        """Propagate reactive updates + let the websocket transmit."""
        await st.task_flush()
        await asyncio.sleep(0)

    def close(self) -> None:
        self._progress.close()


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #


def register_workspace_modal(
    input,
    output,
    session,
    state: AppState,
    *,
    ui_registry: dict[str, Callable[[], Any]],
    prepare_registry: dict[str, Callable[..., None]] | None = None,
    steps_registry: dict[str, Callable[[AppState, str | None], int]] | None = None,
    ensure_analysis_server: Callable[[], None] | None = None,
) -> None:
    prepare_registry = prepare_registry or {}
    steps_registry = steps_registry or {}
    _prepare_tasks: dict[int, asyncio.Task] = {}  # keep refs; GC'd tasks vanish

    def modal_request_is_current(request_id: int) -> bool:
        with reactive.isolate():
            return (state.workspace_modal_nonce() or 0) == request_id

    def workspace_modal_progress_steps(modal_type: str, metric: str | None) -> int:
        if metric is None and modal_type in ("analysis", "phase1", "phase3", "phase4"):
            return 0
        counter = steps_registry.get(modal_type)
        if counter is None:
            return 0
        return int(counter(state, metric) or 0)

    # ── prepare (app.R:842-947) ─────────────────────────────────────────────
    # Async, unlike R — and NOT via session.on_flushed: calling
    # reactive.flush() inside an on_flushed callback re-enters the session's
    # flushed-callback chain and recurses. Instead _open_modal launches this
    # as a detached asyncio task (contextvars carry the session), so the flush
    # that paints the modal + loading shell transmits first, and our own
    # reactive.flush() calls here run outside any flush cycle.
    async def prepare_workspace_modal(request_id: int, modal_type: str, metric: str | None):
        try:
            # Let the just-completed flush (modal + loading shell) transmit.
            await asyncio.sleep(0)
            if not modal_request_is_current(request_id):
                return

            with reactive.isolate():
                state.workspace_modal_stage.set("loading")
                state.workspace_modal_error.set(None)
                target_metric = metric if metric is not None else state.current_metric()

            state.workspace_modal_loading_detail.set(
                workspace_modal_loading_default_detail(state, modal_type, target_metric)
            )

            # Metric switch: save the outgoing metric's phase state, restore
            # the incoming one (app.R:860-872).
            with reactive.isolate():
                old_metric = state.current_metric()
            if target_metric is not None and target_metric != old_metric:
                if old_metric:
                    st.save_metric_phase_state(state, old_metric)
                state.current_metric.set(target_metric)
                st.restore_metric_phase_state(state, target_metric)
            state.workspace_modal_metric.set(target_metric)

            total_steps = workspace_modal_progress_steps(modal_type, target_metric)
            progress = None
            if total_steps > 0:
                with reactive.isolate():
                    detail = state.workspace_modal_loading_detail()
                progress = WorkspaceProgressNotifier(
                    state, total_steps, workspace_modal_loading_label(modal_type), detail
                )
            try:
                await st.task_flush()
                await asyncio.sleep(0)

                if not modal_request_is_current(request_id):
                    return

                preparer = prepare_registry.get(modal_type)
                if preparer is not None:
                    result = preparer(state, target_metric, progress)
                    if inspect.isawaitable(result):
                        await result

                state.workspace_modal_loading_detail.set("Rendering workspace...")

                if not modal_request_is_current(request_id):
                    return

                # Min shell time (replaces R's later::later deferral).
                delay = workspace_modal_min_shell_seconds(modal_type, total_steps)
                if delay > 0:
                    await st.task_flush()
                    await asyncio.sleep(delay)

                if not modal_request_is_current(request_id):
                    return
                state.workspace_modal_stage.set("ready")
                state.workspace_modal_error.set(None)
                with reactive.isolate():
                    state.workspace_modal_ready_nonce.set(
                        (state.workspace_modal_ready_nonce() or 0) + 1
                    )
                await st.task_flush()
            finally:
                if progress is not None:
                    progress.close()
        except Exception as e:  # noqa: BLE001 — surfaced in the modal error panel
            logger.exception("workspace modal prepare failed")
            if modal_request_is_current(request_id):
                state.workspace_modal_error.set(str(e))
                state.workspace_modal_loading_detail.set(None)
                state.workspace_modal_stage.set("error")
                await st.task_flush()

    # ── body content (app.R:949-1069) ───────────────────────────────────────
    def analysis_loading_status_ui():
        labels = st.analysis_tab_labels()
        status = state.analysis_tab_status() or st.empty_analysis_tab_status("pending")
        rows = []
        for tab_key, label in labels.items():
            tab_status = status.get(tab_key, "pending")
            icon = {
                "ready": fa("circle-check"),
                "loading": ui.tags.span(
                    class_="streamcurves-inline-spinner", aria_hidden="true"
                ),
                "error": fa("circle-exclamation"),
            }.get(tab_status, fa("clock"))
            text = {"ready": "Ready", "loading": "Loading", "error": "Error"}.get(
                tab_status, "Waiting"
            )
            rows.append(
                ui.div(
                    ui.div(label, class_="workspace-analysis-loading-row-label"),
                    ui.div(
                        icon,
                        ui.tags.span(text),
                        class_="workspace-analysis-loading-row-state",
                    ),
                    class_=f"workspace-analysis-loading-row is-{tab_status}",
                )
            )
        return ui.TagList(*rows)

    def workspace_modal_body_content(modal_type: str, stage: str, metric: str | None):
        if stage == "error":
            with reactive.isolate():
                err = state.workspace_modal_error() or "Unknown error."
            return ui.div(
                ui.div(
                    ui.tags.strong("Could not open this workspace."),
                    ui.tags.br(),
                    err,
                    ui.div(
                        ui.input_action_button(
                            "workspace_modal_retry", "Retry", class_="btn btn-danger btn-sm"
                        ),
                        class_="mt-3",
                    ),
                    class_="alert alert-danger mb-0",
                ),
                class_="workspace-modal-error",
            )

        if stage != "ready":
            detail = state.workspace_modal_loading_detail() or (
                workspace_modal_loading_default_detail(state, modal_type, metric)
            )
            if modal_type == "analysis":
                labels = st.analysis_tab_labels()
                total_tabs = len(labels)
                status = state.analysis_tab_status() or st.empty_analysis_tab_status()
                ready_tabs = sum(1 for v in status.values() if v == "ready")
                pct = round(100 * ready_tabs / total_tabs) if total_tabs else 0
                return ui.div(
                    ui.div(
                        ui.tags.span(
                            class_="streamcurves-inline-spinner", aria_hidden="true"
                        ),
                        ui.tags.span(workspace_modal_loading_label(modal_type)),
                        class_="workspace-modal-loading-header",
                    ),
                    ui.div(
                        f"{ready_tabs} of {total_tabs} analysis tabs ready",
                        class_="workspace-modal-loading-meta",
                    ),
                    ui.div(
                        ui.div(
                            class_=(
                                "progress-bar progress-bar-striped progress-bar-animated"
                                " workspace-modal-loading-progress-bar"
                            ),
                            role="progressbar",
                            style=f"width: {pct}%;",
                            aria_valuemin="0",
                            aria_valuemax=str(total_tabs),
                            aria_valuenow=str(ready_tabs),
                        ),
                        class_="progress workspace-modal-loading-progress",
                    ),
                    ui.div(detail, class_="workspace-modal-loading-detail"),
                    ui.div(
                        analysis_loading_status_ui(),
                        class_="workspace-analysis-loading-status",
                    ),
                    class_="workspace-modal-loading-shell workspace-analysis-loading-shell",
                )

            return ui.div(
                ui.div(
                    ui.tags.span(class_="streamcurves-inline-spinner", aria_hidden="true"),
                    ui.tags.span(workspace_modal_loading_label(modal_type)),
                    class_="workspace-modal-loading-header",
                ),
                ui.div(
                    ui.div(
                        class_=(
                            "progress-bar progress-bar-striped progress-bar-animated"
                            " w-100 d-flex align-items-center justify-content-start"
                        )
                    ),
                    class_="progress workspace-modal-loading-progress",
                ),
                ui.div(detail, class_="workspace-modal-loading-detail"),
                class_="workspace-modal-loading-shell",
            )

        builder = ui_registry.get(modal_type)
        if builder is None:
            return ui.div("Unknown workspace requested.", class_="alert alert-warning")
        return builder()

    @output(suspend_when_hidden=False)
    @render.ui
    def workspace_modal_body():
        modal_type = state.workspace_modal_type()
        req(modal_type is not None)
        stage = state.workspace_modal_stage() or "loading"
        metric = state.workspace_modal_metric() or state.current_metric()
        return workspace_modal_body_content(modal_type, stage, metric)

    # ── retry (app.R:1082-1085) ─────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.workspace_modal_retry, ignore_init=True)
    def _retry():
        with reactive.isolate():
            modal_type = state.workspace_modal_type()
            metric = state.workspace_modal_metric()
        req(modal_type is not None)
        st.launch_workspace_modal(state, modal_type, metric)

    # ── nonce observer: open + prepare (app.R:1104-1148) ────────────────────
    @reactive.effect
    @reactive.event(state.workspace_modal_nonce, ignore_init=True)
    async def _open_modal():
        with reactive.isolate():
            modal_type = state.workspace_modal_type()
            metric = state.workspace_modal_metric() or state.current_metric()
            request_id = state.workspace_modal_nonce() or 0
        req(modal_type is not None)

        if modal_type == "analysis" and ensure_analysis_server is not None:
            ensure_analysis_server()

        ui.modal_remove()
        await session.send_custom_message("clearModalBackdrop", {})
        ui.modal_show(
            ui.modal(
                ui.output_ui("workspace_modal_body"),
                title=workspace_modal_title(state, modal_type, metric),
                easy_close=True,
                footer=ui.modal_button("Close"),
                size="xl",
            )
        )
        # The CSS targets .modal-dialog.workspace-modal-dialog (custom.css) —
        # tag the dialog after insertion (R passed class= to modalDialog).
        await session.send_custom_message(
            "workspaceModalDialogClass", {"className": "workspace-modal-dialog"}
        )

        task = asyncio.create_task(
            prepare_workspace_modal(request_id, modal_type, metric)
        )
        _prepare_tasks[request_id] = task
        task.add_done_callback(lambda t: _prepare_tasks.pop(request_id, None))
