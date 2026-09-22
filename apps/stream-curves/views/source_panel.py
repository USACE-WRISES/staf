"""The curve source panel: where a curve this build did not fit comes from.

Opened from a gallery tile, a row of the Table's "Curves from other sources" or a
source chip in Function mapping, all through one input any page can set
(``OPEN_INPUT``). The body is built from the session's reference build and the
opened version's provenance by ``streamcurves.curve_sources``; opening it
changes nothing.
"""
from __future__ import annotations

import json
import re
from typing import Mapping, Optional

from shiny import module, reactive, ui

from streamcurves import curve_sources as src
from streamcurves import curve_svg as cs
from streamcurves import metric_names
from streamcurves import pressure_evidence as pe
from views.state import AppState
from views.theme import fa
from views.uihelpers import guard

PANEL_ID = "source_panel"
#: the one input every page sets to open the panel for a metric
OPEN_INPUT = f"{PANEL_ID}-open"
PANEL_W, PANEL_H = 300, 184


def open_onclick(metric: str, *, stop: bool = False) -> str:
    """The onclick that opens the panel for ``metric`` from any page. ``stop``
    keeps the click from also reaching an element around it."""
    payload = json.dumps({"metric": str(metric)}).replace(chr(39), chr(92) + chr(39))
    return (("event.stopPropagation();" if stop else "")
            + f"Shiny.setInputValue('{OPEN_INPUT}',{payload},{{priority:'event'}})")


def open_onkeydown() -> str:
    return "if(event.key==='Enter'||event.key===' '){event.preventDefault();this.click();}"


def kind_class(kind) -> str:
    return "src-" + re.sub(r"[^a-z0-9]+", "-", str(kind or "").lower()).strip("-")


def kind_badge(kind, label: Optional[str] = None):
    """The source badge: the kind's icon and its label (or ``label``)."""
    icon = src.kind_icon(kind)
    return ui.tags.span(fa(icon) if icon else None,
                        ui.tags.span(str(label or src.kind_label(kind))),
                        class_=f"source-badge {kind_class(kind)}")


def display_name(metric: str, entry: Mapping) -> str:
    name = (entry.get("config") or {}).get("display_name")
    if metric_names.is_placeholder_name(name, metric):
        name = metric_names.display_name_for(metric, name)
    return str(name or metric)


def function_names(entry: Mapping) -> list[str]:
    """The functions the curve is placed in, primary first."""
    out: list[str] = []
    for r in entry.get("mapping") or []:
        label = str(r.get("function_label") or "").strip()
        if label and label not in out:
            out.append(label)
    return out


def _section(title: str, *body):
    return ui.tags.section(ui.tags.h6(title, class_="source-panel-h"), *body,
                           class_="source-panel-section")


def breakpoint_value(x: float) -> str:
    """A breakpoint to four significant figures (0.5815, 1.069, 219.5), so the
    table agrees with the thresholds the facts quote."""
    return f"{x:.1f}" if abs(x) >= 1000 else f"{x:.4g}"


def _breakpoint_table(points, units: str):
    head = f"Value ({units})" if units else "Value"
    return ui.div(ui.tags.table(
        ui.tags.tbody(
            ui.tags.tr(ui.tags.th(head), *[ui.tags.td(breakpoint_value(x)) for x, _ in points]),
            ui.tags.tr(ui.tags.th("Score"), *[ui.tags.td(f"{y:.2f}") for _, y in points])),
        class_="table table-sm source-breakpoints"), class_="source-breakpoints-wrap")


def _trail(steps):
    items = []
    for s in steps:
        verdict = str(s.get("verdict") or "")
        items.append(ui.tags.li(
            ui.tags.span(str(s.get("step") or ""), class_="source-trail-step"),
            ui.tags.span(verdict, class_="source-trail-verdict is-"
                         + re.sub(r"[^a-z]+", "-", verdict.lower()).strip("-")),
            ui.tags.span(str(s.get("why") or ""), class_="source-trail-why")
            if s.get("why") else None))
    return ui.tags.ol(*items, class_="source-trail")


def panel_title(metric: str, entry: Mapping):
    return ui.div(
        ui.div(ui.tags.span(display_name(metric, entry), class_="source-panel-name"),
               ui.tags.code(metric, class_="source-panel-code"), class_="source-panel-id"),
        kind_badge(entry.get("kind"), entry.get("label")),
        class_="source-panel-head")


def panel_body(metric: str, entry: Mapping, *, provenance: Optional[Mapping] = None,
               build: Optional[Mapping] = None):
    """Everything the panel shows about one curve, read-only."""
    kind = entry.get("kind")
    units = src.units_of(entry)
    tile = cs.reference_tile(metric, entry)
    svg = cs.tile_svg(tile, w=PANEL_W, h=PANEL_H, x_label=units or None, point_labels=True)
    rows = list(src.source_facts(metric, entry, provenance=provenance, build=build))
    chosen = src.chosen_by(metric, entry)
    if chosen:
        rows.append(("Chosen by", chosen))
    facts = ui.tags.dl(*[t for label, value in rows
                         for t in (ui.tags.dt(label), ui.tags.dd(value))],
                       class_="source-panel-facts")
    parts = [
        ui.p(src.kind_sentence(kind), class_="source-panel-sentence"),
        ui.div(ui.div(ui.HTML(svg), class_="source-panel-curve"), facts,
               class_="source-panel-grid"),
    ]
    trail = src.build_trail(metric, entry, provenance=provenance, build=build)
    if trail:
        parts.append(_section("How the build got here", _trail(trail)))
    limits = src.limits(entry)
    if limits:
        parts.append(_section("Limits", ui.tags.ul(*[ui.tags.li(t) for t in limits],
                                                   class_="source-panel-limits")))
    points = src.breakpoints(entry)
    if points:
        parts.append(_section("Breakpoints", _breakpoint_table(points, units)))
    fns = function_names(entry)
    parts.append(_section("Functions", ui.div(
        *([ui.tags.span(f, class_="source-fn-chip") for f in fns]
          or [ui.tags.span("Not used in any function in this version.", class_="text-muted")]),
        class_="source-panel-functions")))
    return ui.div(*parts, class_="source-panel")


def panel_modal(metric: str, entry: Mapping, *, provenance: Optional[Mapping] = None,
                build: Optional[Mapping] = None):
    return ui.modal(
        panel_body(metric, entry, provenance=provenance, build=build),
        title=panel_title(metric, entry),
        footer=ui.modal_button("Close"),
        size="l", easy_close=True)


def entry_for(state: AppState, metric: str) -> Optional[dict]:
    """The metric's ``reference_rows`` entry in the open session, or None."""
    with reactive.isolate():
        build = state.reference_build()
        mapping = state.discipline_function_mapping()
        built = state.completed_metrics() or {}
    return pe.reference_rows(build, mapping, built=built).get(str(metric))


@module.server
def source_panel_server(input, output, session, state: AppState):
    @reactive.effect
    @reactive.event(input.open)
    @guard("open the curve source")
    def _open():
        metric = str((input.open() or {}).get("metric") or "")
        if not metric:
            return
        entry = entry_for(state, metric)
        if entry is None:
            ui.notification_show(f"{metric} has no curve from another source in this version.",
                                 type="warning", duration=5)
            return
        with reactive.isolate():
            provenance = state.source_provenance()
            build = state.reference_build()
        ui.modal_show(panel_modal(metric, entry, provenance=provenance, build=build))


__all__ = ["PANEL_ID", "OPEN_INPUT", "open_onclick", "open_onkeydown", "kind_class",
           "kind_badge", "display_name", "function_names", "panel_title", "panel_body",
           "panel_modal", "entry_for", "source_panel_server"]
