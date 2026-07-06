"""Shared UI helpers — ports of app/helpers/badges.R, notifications.R, and
phase_tracker.R's phase_tracker_ui()."""

from __future__ import annotations

from shiny import ui

from views.state import PHASE_LABELS
from views.theme import fa


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


def explanation_card(title, *body):
    return ui.card(
        ui.card_header(title, class_="bg-info text-white"),
        ui.card_body(*body),
        class_="border-info mb-2",
        fill=False,
    )


def no_data_alert():
    return ui.div(
        ui.div(
            fa("database", height="3em"),
            ui.tags.h4("No Data Loaded", class_="mt-3"),
            ui.tags.p(
                "Go to the ",
                ui.tags.strong("Data & Setup"),
                " tab and click ",
                ui.tags.strong("Load Data"),
                " to get started.",
            ),
            class_="text-center text-muted",
        ),
        class_="d-flex justify-content-center align-items-center",
        style="min-height: 300px;",
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
