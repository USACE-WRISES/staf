"""Shared UI helpers — ports of app/helpers/badges.R, notifications.R, and
phase_tracker.R's phase_tracker_ui()."""

from __future__ import annotations

import functools
import inspect
import traceback

from shiny import ui
from shiny.types import (
    NotifyException,
    SilentCancelOutputException,
    SilentException,
)

from streamcurves import rules_view
from streamcurves.curves import CURVE_FORM_MONOTONE, CURVE_FORM_OPTIMUM, curve_form_of
from views.state import PHASE_LABELS
from views.theme import fa

# ── response shape ────────────────────────────────────────────────────────────
# A metric's curve direction and form are one question -- which shape scores well
# -- carried by two config fields, `higher_is_better` and `curve_form`. Written
# independently they could contradict: an optimum form with a TRUE direction reads
# as "higher is better" over a two-sided curve, and a null direction with the
# monotone form makes the metric silently unbuildable. Kept here so the settings
# control and every place that labels a direction agree, without views/phase4.py
# and views/ref_curve.py having to import each other.
SHAPE_HIGHER = "higher"
SHAPE_LOWER = "lower"
SHAPE_OPTIMUM = "optimum"

RESPONSE_SHAPE_CHOICES = {
    SHAPE_HIGHER: "Higher is better",
    SHAPE_LOWER: "Lower is better",
    SHAPE_OPTIMUM: "Two-sided (best mid-range)",
}

#: shape -> (higher_is_better, curve_form)
RESPONSE_SHAPE_CONFIG = {
    SHAPE_HIGHER: (True, CURVE_FORM_MONOTONE),
    SHAPE_LOWER: (False, CURVE_FORM_MONOTONE),
    SHAPE_OPTIMUM: (None, CURVE_FORM_OPTIMUM),
}

SHAPE_UNDER_REVIEW_LABEL = "Under review"


def response_shape_of(metric_entry) -> str | None:
    """The metric's current shape, or None when its direction is still under review.

    A curated entry with no agreed direction carries ``higher_is_better: None`` with
    the monotone form, and the engine deliberately builds no curve for it. That is a
    real state, so it reads back as unset rather than being forced into an answer.
    """
    mc = metric_entry or {}
    if curve_form_of(mc) == CURVE_FORM_OPTIMUM:
        return SHAPE_OPTIMUM
    hib = mc.get("higher_is_better")
    if hib is True:
        return SHAPE_HIGHER
    if hib is False:
        return SHAPE_LOWER
    return None


def response_shape_label(metric_entry) -> str:
    """Display label for a metric's direction. Never says "lower is better" about a
    two-sided curve, which is what `bool(higher_is_better)` used to produce."""
    return RESPONSE_SHAPE_CHOICES.get(
        response_shape_of(metric_entry), SHAPE_UNDER_REVIEW_LABEL
    )


# --------------------------------------------------------------------------- #
# Status badges (badges.R)
# --------------------------------------------------------------------------- #

_BADGE_CLASSES = {
    "pass": "bg-success",
    "caution": "bg-warning text-dark",
    "fail": "bg-danger",
    "not_applicable": "bg-secondary",
}


def status_badge(status: str, label: str | None = None):
    badge_class = _BADGE_CLASSES.get(status, "bg-secondary")
    return ui.tags.span(label if label is not None else status, class_=f"badge {badge_class}")


def p_value_badge(p):
    import math

    if p is None or (isinstance(p, float) and math.isnan(p)):
        return status_badge("not_applicable", "NA")
    if p < 0.01:
        return status_badge("pass", f"p = {p:.4f}")
    if p < 0.05:
        return status_badge("pass", f"p = {p:.3f}")
    if p < 0.10:
        return status_badge("caution", f"p = {p:.3f}")
    return status_badge("not_applicable", f"p = {p:.3f}")


# --------------------------------------------------------------------------- #
# Reactive-effect guard.
#
# py-shiny treats the two kinds of failure very differently. An exception raised
# inside a @render.* output is caught per output: the traceback prints and that
# one output box shows an error, and the session carries on. An exception raised
# inside a @reactive.effect reaches Session._unhandled_error, which calls
# session.close() -- the websocket goes and every reactive value with it. A
# project lives only in session memory here, so that is not an error message,
# it is the whole run gone with no way back.
#
# So a handler that can raise wears this. It is the innermost decorator, under
# @reactive.event, because reactive.event has to see the guarded callable:
#
#     @reactive.effect
#     @reactive.event(input.save_mapping)
#     @guard("save the mapping")
#     def _save():
#         ...
#
# `action` completes the sentence "Could not ...", so phrase it as the thing the
# user was trying to do, not the internal step that failed.
# --------------------------------------------------------------------------- #


#: Shiny's own control flow, not failures. SilentException and its subclasses are
#: what req() and an unset input raise to stop an effect quietly;
#: SilentOperationInProgressException and SilentCancelOutputException are its
#: siblings; NotifyException already carries its own message and its own decision
#: about whether to close. All of them must reach Shiny untouched -- swallowing a
#: SilentException turns "this input is not set yet" into a red error toast.
_SHINY_CONTROL_FLOW = (SilentException, SilentCancelOutputException, NotifyException)


def _report(action: str, exc: Exception) -> None:
    traceback.print_exc()
    ui.notification_show(f"Could not {action}: {exc}", type="error", duration=10)


def guard(action: str):
    """Report a reactive-effect failure instead of ending the session."""

    def decorate(fn):
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except _SHINY_CONTROL_FLOW:
                    raise
                except Exception as exc:  # noqa: BLE001 - the point is to catch all
                    _report(action, exc)
                    return None

            return awrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except _SHINY_CONTROL_FLOW:
                raise
            except Exception as exc:  # noqa: BLE001 - the point is to catch all
                _report(action, exc)
                return None

        return wrapper

    return decorate


# --------------------------------------------------------------------------- #
# "Not ready yet" panels.
#
# Every workflow stage is reachable at any time (the strip's pills stay clickable
# so you can look ahead), so every stage has to be able to say honestly that its
# inputs are not there yet. One component, one look, and a button that goes to the
# stage that actually supplies what is missing -- rather than a half-rendered
# screen, a blank map, or the silence a failed req() produces.
# --------------------------------------------------------------------------- #

# Root-level input the shell listens on (app.py). Fixed rather than namespaced so a
# panel rendered inside any module can request the jump without wiring of its own.
WORKFLOW_GOTO_INPUT = "workflow_goto"


def _goto_onclick(nav: str, step: int | None) -> str:
    """Inline handler for a not-ready panel's action button (house channel idiom)."""
    payload = f"{{nav:'{nav}'" + (f",step:{int(step)}" if step is not None else "") + "}"
    return (
        f"Shiny.setInputValue('{WORKFLOW_GOTO_INPUT}',{payload},{{priority:'event'}})"
    )


# Rules deep link: same fixed-input idiom as WORKFLOW_GOTO_INPUT, so a chip
# rendered inside any module reaches the Rules page with zero wiring of its own
# (app.py consumes it: nav to the rules panel, then scroll to the rule's card).
RULES_GOTO_INPUT = "rules_goto"


def _rules_goto_onclick(rule_id: str) -> str:
    safe = str(rule_id).replace("'", "")
    return (f"Shiny.setInputValue('{RULES_GOTO_INPUT}',{{rule:'{safe}'}},"
            "{priority:'event'})")


def rule_chip(rule_id: str, *, label: str | None = None):
    """A small inline chip that opens the Rules page at this rule's card."""
    return ui.tags.a(
        label or str(rule_id),
        class_="rule-chip",
        href="javascript:void(0)",
        onclick=_rules_goto_onclick(rule_id),
        title="Open this rule on the Rules page",
    )


def linkify_rule_ids(text: str):
    """The sentence with every rule id rendered as a chip. Render-time only:
    the string itself stays plain wherever it also lands in provenance."""
    return ui.TagList(*[
        rule_chip(value) if kind == "rule" else value
        for kind, value in rules_view.split_rule_ids(text or "")
    ])


#: Lifecycle badge colors: Draft amber (review debt), Final green, rest neutral.
_LIFECYCLE_BADGE_CLASSES = {"draft": "text-bg-warning", "certified": "text-bg-success"}


def lifecycle_badge(status):
    """A small badge showing a library lifecycle status by its display label
    (library.STATUS_LABELS). The one place the lifecycle color rule lives.
    Distinct from status_badge above, which renders the generic disposition
    chips (caution/ok/...)."""
    from streamcurves import library as lib  # local: keep this module import-light
    s = str(status or "").strip().lower()
    return ui.tags.span(
        lib.status_label(s),
        class_=f"badge {_LIFECYCLE_BADGE_CLASSES.get(s, 'text-bg-secondary')}")


def not_ready_panel(
    title: str,
    *message,
    action_label: str | None = None,
    goto_nav: str | None = None,
    goto_step: int | None = None,
    icon: str = "database",
):
    """A centered "this stage is not ready" panel, optionally with a jump button.

    ``message`` takes the same varargs a ui.tags.p does, so callers can bold parts
    of the sentence. The button is rendered only when both ``action_label`` and
    ``goto_nav`` are supplied.
    """
    action = None
    if action_label and goto_nav:
        action = ui.tags.button(
            action_label,
            class_="btn btn-outline-primary btn-sm mt-2",
            onclick=_goto_onclick(goto_nav, goto_step),
            type="button",
        )
    return ui.div(
        ui.div(
            fa(icon, height="3em"),
            ui.tags.h4(title, class_="mt-3"),
            ui.tags.p(*message),
            action,
            class_="text-center text-muted",
        ),
        class_="d-flex justify-content-center align-items-center",
        style="min-height: 300px;",
    )


def no_data_alert():
    """The no-dataset case: the shared panel, pointing back at the first stage."""
    return not_ready_panel(
        "No Data Loaded",
        "Start at ",
        ui.tags.strong("Region & data"),
        " in the workflow strip above, or use ",
        ui.tags.strong("Open"),
        " (top right) to load a saved project.",
        action_label="Go to Region & data",
        goto_nav="data",
        goto_step=1,
    )


# --------------------------------------------------------------------------- #
# Phase tracker dots (phase_tracker.R:36-68)
# --------------------------------------------------------------------------- #


def phase_tracker_ui(current_phase: int, completed_phases: list[int] | None = None):
    completed_phases = completed_phases or []
    dots = []
    for i, label in enumerate(PHASE_LABELS, start=1):
        if i in completed_phases:
            icon_class, icon = "phase-dot completed", "✓"
        elif i == current_phase:
            icon_class, icon = "phase-dot current", "●"
        else:
            icon_class, icon = "phase-dot pending", "○"
        dots.append(
            ui.tags.div(
                ui.tags.div(
                    icon,
                    class_=icon_class,
                    style=(
                        "width: 32px; height: 32px; border-radius: 50%; display: flex;"
                        " align-items: center; justify-content: center; font-size: 1rem;"
                        " font-weight: 700; border: 2px solid currentColor;"
                    ),
                ),
                ui.tags.span(
                    label,
                    style=(
                        "font-size: 0.72rem; text-align: center; margin-top: 4px;"
                        " line-height: 1.1;"
                    ),
                ),
                class_="d-flex flex-column align-items-center",
                style="flex: 1; min-width: 0;",
            )
        )
    return ui.tags.div(*dots, class_="phase-tracker d-flex justify-content-between gap-2 mb-3")


# --------------------------------------------------------------------------- #
# Loading notifications (notifications.R)
# --------------------------------------------------------------------------- #


def analysis_launch_spinner_notification_id(request_id) -> str:
    if request_id is None:
        return ""
    resolved = str(request_id)
    return f"analysis-launch-spinner-{resolved}" if resolved else ""


def final_loading_notification_ui(title, detail, close_button: bool = True):
    return ui.div(
        ui.div(
            ui.div(
                ui.tags.span(class_="streamcurves-final-loading-spinner", aria_hidden="true"),
                ui.tags.span(title, class_="streamcurves-final-loading-title"),
                class_="streamcurves-final-loading-heading",
            ),
            (
                ui.tags.button(
                    "x",
                    type="button",
                    class_="streamcurves-final-loading-close",
                    aria_label="Dismiss loading notification",
                    onclick=(
                        "var notification = this.closest('.shiny-notification');"
                        " if (notification) { notification.remove(); } return false;"
                    ),
                )
                if close_button
                else None
            ),
            class_="streamcurves-final-loading-header",
        ),
        ui.div(detail, class_="streamcurves-final-loading-detail"),
        class_="streamcurves-final-loading-notification",
    )


def show_final_loading_notification(id, title, detail, close_button: bool = True):
    notification_id = "" if id is None else str(id)
    if not notification_id:
        return None
    ui.notification_show(
        ui=final_loading_notification_ui(title, detail, close_button=close_button),
        id=notification_id,
        duration=None,
        close_button=False,
        type="message",
    )
    return notification_id


def remove_final_loading_notification(id) -> bool:
    notification_id = "" if id is None else str(id)
    if not notification_id:
        return False
    ui.notification_remove(notification_id)
    return True


def show_analysis_launch_spinner_notification(
    request_id, title, detail: str = "Loading page, please wait."
):
    notification_id = analysis_launch_spinner_notification_id(request_id)
    if not notification_id:
        return None
    return show_final_loading_notification(notification_id, title, detail, close_button=True)


def remove_analysis_launch_spinner_notification(request_id) -> bool:
    notification_id = analysis_launch_spinner_notification_id(request_id)
    if not notification_id:
        return False
    return remove_final_loading_notification(notification_id)


# --------------------------------------------------------------------------- #
# Compile progress (import wizard step 5 — bottom-right live counter)
# --------------------------------------------------------------------------- #


class CompileProgress:
    """Tracks the import-wizard compile so the bottom-right toast can show a
    live ``N of Total`` counter plus the source currently being fetched.

    Pure state + label formatting (no Shiny calls), so the arithmetic and the
    label strings are unit-testable; the effect drives notification_show /
    task_flush around it. Per-site sources (StreamStats, Model My Watershed)
    count one unit per site — they are the slow long pole — while batch
    sources (StreamCAT, NRSA, ...) count as one unit each.
    """

    def __init__(self, total: int):
        self.total = max(int(total), 1)
        self.done = 0
        self._source = ""
        self._site: int | None = None
        self._n_sites: int | None = None

    @classmethod
    def for_run(
        cls,
        n_sites: int,
        *,
        want_da: bool = False,
        want_elev: bool = False,
        streamcat: bool = False,
        nrsa: bool = False,
        streamstats: bool = False,
        mmw: bool = False,
        site_engine: bool = False,
    ) -> "CompileProgress":
        """Total work units for a run: NLDI snap + final assembly always run;
        per-site sources add one unit per site; other active sources add one."""
        n = max(int(n_sites), 0)
        total = 2  # NLDI flowline snap + final table assembly
        if want_da:
            total += 1
        if want_elev:
            total += 1
        if streamcat:
            total += 1
        if nrsa:
            total += 1
        if streamstats:
            total += n
        if mmw:
            total += n
        if site_engine:
            total += n
        return cls(total)

    def start(self, source: str, *, site: int | None = None, n_sites: int | None = None) -> None:
        """Name the source about to be fetched (shown before it completes)."""
        self._source = source
        self._site = site
        self._n_sites = n_sites

    def complete(self, units: int = 1) -> None:
        self.done = min(self.done + units, self.total)

    def title(self) -> str:
        return f"Compiling site data ({self.done} of {self.total})"

    def detail(self) -> str:
        base = self._source or "Working"
        if self._site is not None and self._n_sites:
            return f"{base}, site {self._site} of {self._n_sites}"
        return base
