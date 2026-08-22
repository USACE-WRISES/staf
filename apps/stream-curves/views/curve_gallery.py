"""The curve gallery: the Reference Curves page's grid of curve thumbnails.

Pure UI builders plus one headless data adapter; no module server here. The
page (``views/summary_page.py``) renders ``gallery_ui`` from its per-row
snapshots and handles the one delegated click channel; the tests build tiles
from an ``AppState`` and read the markup. The drawing itself lives in
``streamcurves/curve_svg.py`` so the batch review packet shares it.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Optional

import pandas as pd
from shiny import reactive, ui

from streamcurves import curve_svg as cs
from streamcurves import run_state as rs
from views import summary_state as ss
from views.state import AppState
from views.theme import bi

REVIEW_STATUS_LABELS = cs.STATUS_LABELS
DECISION_LABELS = cs.DECISION_LABELS

GALLERY_FILTERS = {
    "all": "All",
    "flagged": "Flagged",
    "out_of_scope": "Not in scope",
    "stratified": "Stratified",
}
DEFAULT_SECTION = "gallery"
TILE_W, TILE_H = 240, 150


def curves_sections() -> list[str]:
    return [v for v, _ in rs.STAGE_SECTIONS.get("curve_review", [])]


def tile_row(metric: str, curve_rows: Any, *, metric_entry: Mapping | None,
             review_entry: Mapping | None, function_label: str | None) -> dict:
    """One gallery tile from a metric's curve rows (a DataFrame with one row per
    stratum, a list of row dicts, or nothing)."""
    if isinstance(curve_rows, pd.DataFrame):
        rows = curve_rows.to_dict("records") if len(curve_rows) else []
    elif curve_rows is None:
        rows = []
    else:
        rows = [dict(r) for r in curve_rows]
    return cs.tile_from_curve_rows(metric, rows, metric_entry=metric_entry,
                                   review_entry=review_entry, function_label=function_label)


def gallery_rows(state: AppState, metrics: Optional[Iterable[str]] = None) -> list[dict]:
    """Headless path: every eligible metric's tile straight from the state, in
    the table's order. The page itself reads its row snapshots instead so the
    gallery invalidates exactly when a table row does."""
    with reactive.isolate():
        mc = state.metric_config() or {}
        review = state.curve_review() or {}
        functions = state.column_functions() or {}
    keys = list(metrics) if metrics is not None else ss.eligible_summary_metrics(mc)
    out = []
    for m in keys:
        try:
            rows = ss.get_metric_curve_rows(state, m)
        except (KeyError, TypeError, ValueError):
            rows = None
        out.append(tile_row(m, rows, metric_entry=mc.get(m), review_entry=review.get(m),
                            function_label=functions.get(m)))
    return out


def filter_rows(rows: Iterable[Mapping], mode: str) -> list[dict]:
    rows = [dict(r) for r in rows]
    if mode == "flagged":
        return [r for r in rows if r.get("needs_review")]
    if mode == "out_of_scope":
        return [r for r in rows if r.get("in_scope") is False]
    if mode == "stratified":
        return [r for r in rows if len(r.get("strata") or []) > 1]
    return rows


def setinput_onclick(input_id: str, payload: Mapping) -> str:
    """The page's delegated-click idiom: one input id, a JSON payload, event
    priority so the same click can fire twice. Single quotes are escaped for
    the HTML attribute the way ``summary_page._action`` does it."""
    text = json.dumps(dict(payload))
    return (f"Shiny.setInputValue('{input_id}',"
            f"{text.replace(chr(39), chr(92) + chr(39))},"
            "{priority:'event'})")


def tile_ui(row: Mapping, *, channel_id: str, w: int = TILE_W, h: int = TILE_H,
            band_breaks: tuple[float, float] = cs.DEEP_INDEX_BANDS):
    """A clickable tile: head (metric code and status pill), the SVG, and a
    foot with the function label, a stratum-count badge, and the table button
    (which stops the click from also opening the analysis)."""
    metric = str(row.get("metric") or "")
    open_click = setinput_onclick(channel_id, {"metric": metric, "action": "open"})
    table_click = "event.stopPropagation();" + setinput_onclick(
        channel_id, {"metric": metric, "action": "table"})
    n_strata = len(row.get("strata") or [])
    right = []
    if n_strata > 1:
        right.append(ui.tags.span(f"{n_strata} strata", class_="curve-tile-strata"))
    right.append(ui.tags.button(
        bi("table"), type="button", class_="btn btn-link btn-sm curve-tile-table",
        onclick=table_click, title="Show this metric's row in the table"))
    return ui.div(
        ui.div(
            ui.tags.span(metric, class_="curve-tile-code", title=str(row.get("display_name") or metric)),
            ui.tags.span(cs.status_label(row), class_="curve-tile-status"),
            class_="curve-tile-head",
        ),
        ui.HTML(cs.tile_svg(row, w=w, h=h, band_breaks=band_breaks)),
        ui.div(
            ui.tags.span(str(row.get("function") or ""), class_="curve-tile-function"),
            ui.div(*right, class_="curve-tile-foot-right"),
            class_="curve-tile-foot",
        ),
        class_=" ".join(["curve-tile", *cs.tile_state_classes(row)]),
        role="button", tabindex="0", title=cs.tile_title(row), data_metric=metric,
        onclick=open_click,
        onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.click();}",
    )


def gallery_counts(rows: Iterable[Mapping]) -> dict:
    rows = list(rows)
    return {
        "n": len(rows),
        "flagged": sum(1 for r in rows if r.get("needs_review")),
        "out_of_scope": sum(1 for r in rows if r.get("in_scope") is False),
        "stratified": sum(1 for r in rows if len(r.get("strata") or []) > 1),
    }


def gallery_ui(rows: Iterable[Mapping], *, channel_id: str, filter_input_id: str,
               filter_mode: str = "all", w: int = TILE_W, h: int = TILE_H,
               band_breaks: tuple[float, float] = cs.DEEP_INDEX_BANDS):
    rows = [dict(r) for r in rows]
    c = gallery_counts(rows)
    mode = filter_mode if filter_mode in GALLERY_FILTERS else "all"
    counts = ui.div(
        ui.tags.strong(f"{c['n']} curve" + ("" if c["n"] == 1 else "s")),
        ui.tags.span(f", {c['flagged']} flagged, {c['out_of_scope']} not in scope", class_="text-muted"),
        class_="curve-gallery-counts",
    )
    toolbar = ui.div(
        counts,
        ui.input_radio_buttons(filter_input_id, None, GALLERY_FILTERS, selected=mode, inline=True),
        class_="curve-gallery-toolbar",
    )
    shown = filter_rows(rows, mode)
    if shown:
        grid = ui.div(*[tile_ui(r, channel_id=channel_id, w=w, h=h, band_breaks=band_breaks)
                        for r in shown], class_="curve-gallery")
    else:
        grid = ui.div("No curves match this filter.", class_="text-muted curve-gallery-empty")
    legend = ui.div(
        "Shaded column: the reference range. Dashed lines: the condition breaks at "
        f"{cs.fmt_num(band_breaks[0])} and {cs.fmt_num(band_breaks[1])}. Dotted red curve: not in "
        "scope. Orange marker: needs review. Click a tile to open its analysis.",
        class_="text-muted small curve-gallery-legend",
    )
    return ui.div(toolbar, grid, legend, class_="curve-gallery-wrap")


__all__ = [
    "REVIEW_STATUS_LABELS", "DECISION_LABELS", "GALLERY_FILTERS", "DEFAULT_SECTION",
    "curves_sections", "tile_row", "gallery_rows", "filter_rows", "setinput_onclick",
    "tile_ui", "gallery_counts", "gallery_ui",
]
