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
from views.theme import bi, fa

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


def assign_functions(rows: Iterable[Mapping], mapping: Any = None) -> list[dict]:
    """Discipline and function keys on every tile from the session's
    discipline-function mapping (every function a metric serves, primary
    first), falling back to the tile's own label."""
    return cs.assign_functions(rows, mapping)


def gallery_rows(state: AppState, metrics: Optional[Iterable[str]] = None) -> list[dict]:
    """Headless path: every eligible metric's tile straight from the state, in
    the table's order, with its discipline and functions assigned. The page
    itself reads its row snapshots instead so the gallery invalidates exactly
    when a table row does."""
    with reactive.isolate():
        mc = state.metric_config() or {}
        review = state.curve_review() or {}
        functions = state.column_functions() or {}
        mapping = state.discipline_function_mapping()
    keys = list(metrics) if metrics is not None else ss.eligible_summary_metrics(mc)
    out = []
    for m in keys:
        try:
            rows = ss.get_metric_curve_rows(state, m)
        except (KeyError, TypeError, ValueError):
            rows = None
        out.append(tile_row(m, rows, metric_entry=mc.get(m), review_entry=review.get(m),
                            function_label=functions.get(m)))
    return assign_functions(out, mapping)


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


def _scroll_to(dom_id: str) -> str:
    """An onclick that scrolls to an element and focuses it without changing
    the hash, and without reaching the clickable tile around it."""
    return (f"event.stopPropagation();var el=document.getElementById('{dom_id}');"
            "if(el){el.scrollIntoView({behavior:'smooth',block:'center'});el.focus();}"
            "return false;")


def tile_ui(row: Mapping, *, channel_id: str, w: int = TILE_W, h: int = TILE_H,
            band_breaks: tuple[float, float] = cs.DEEP_INDEX_BANDS,
            cross: Mapping | None = None, under: Any = None, busy: bool = False):
    """A clickable tile: head (metric code and status pill), the SVG, and a
    foot with the other functions the metric serves (its primary function is
    the header above it), a stratum-count badge, a recompute button (primary
    tiles only; ``busy`` swaps it for the inline spinner while the row
    recomputes), and the table button. Both foot buttons stop the click from
    also opening the analysis.

    With ``cross`` (a ``group_tiles`` cross entry) the tile is a cross-listed
    copy placed under the function ``under``: dashed, marked "also under" the
    function it lives under with a link back to the primary tile, and with an
    id of its own so the two never collide."""
    metric = str(row.get("metric") or "")
    open_click = setinput_onclick(channel_id, {"metric": metric, "action": "open"})
    table_click = "event.stopPropagation();" + setinput_onclick(
        channel_id, {"metric": metric, "action": "table"})
    recompute_click = "event.stopPropagation();" + setinput_onclick(
        channel_id, {"metric": metric, "action": "recompute"})
    n_strata = len(row.get("strata") or [])
    right = []
    if n_strata > 1:
        right.append(ui.tags.span(f"{n_strata} strata", class_="curve-tile-strata"))
    if cross is None:
        right.append(ui.tags.button(
            (ui.tags.span(class_="streamcurves-inline-spinner", aria_hidden="true")
             if busy else fa("arrows-rotate")),
            type="button", class_="btn btn-link btn-sm curve-tile-recompute",
            onclick=recompute_click, title="Recompute this reference curve",
            **({"disabled": "disabled"} if busy else {})))
    right.append(ui.tags.button(
        bi("table"), type="button", class_="btn btn-link btn-sm curve-tile-table",
        onclick=table_click, title="Show this metric's row in the table"))
    also = [str(f) for f in (row.get("also_functions") or []) if f]
    if cross:
        primary = str(cross.get("primary_function_name") or "its primary function")
        head_note = ui.div(
            "also under ",
            ui.tags.a(primary, href="#", class_="curve-gallery-fn-link",
                      onclick=_scroll_to(cs.tile_dom_id(metric)),
                      title="Go to this curve's primary tile"),
            class_="curve-tile-cross")
        foot_left = ui.tags.span("cross-listed", class_="curve-tile-also",
                                 title=f"This curve lives under {primary}")
        dom_id = cs.cross_dom_id(metric, under if under is not None else primary)
        classes = ["curve-tile", *cs.tile_state_classes(row), "is-cross-listed"]
    else:
        head_note = None
        foot_left = ui.tags.span(("also: " + ", ".join(also)) if also else "", class_="curve-tile-also",
                                 title=("Also informs: " + ", ".join(also)) if also else None)
        dom_id = cs.tile_dom_id(metric)
        classes = ["curve-tile", *cs.tile_state_classes(row)]
    return ui.div(
        head_note,
        ui.div(
            ui.div(
                ui.tags.span(
                    str(row.get("short_name") or row.get("display_name") or metric),
                    class_="curve-tile-name",
                    title=str(row.get("display_name") or metric),
                ),
                ui.tags.span(metric, class_="curve-tile-code"),
                class_="curve-tile-id",
            ),
            ui.tags.span(cs.status_label(row), class_="curve-tile-status"),
            class_="curve-tile-head",
        ),
        ui.HTML(cs.tile_svg(row, w=w, h=h, band_breaks=band_breaks)),
        ui.div(foot_left, ui.div(*right, class_="curve-tile-foot-right"), class_="curve-tile-foot"),
        id=dom_id,
        class_=" ".join(classes),
        role="button", tabindex="0", title=cs.tile_title(row), data_metric=metric,
        data_role="cross" if cross else "primary",
        onclick=open_click,
        onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.click();}",
    )


def function_header_ui(fn: Mapping):
    """The full-width row above a function's tiles: its name, the number of
    curves that serve it, and how many of those are cross-listed from another
    function."""
    parts = [
        ui.tags.span(str(fn.get("function_name") or "No function"), class_="curve-gallery-fn-name"),
        ui.tags.span(cs._count_text(int(fn.get("n") or 0)), class_="curve-gallery-fn-count"),
    ]
    n_cross = int(fn.get("n_cross") or 0)
    if n_cross:
        parts.append(ui.tags.span(f"{n_cross} cross-listed", class_="curve-gallery-fn-cross",
                                  title="Drawn again here; the curve lives under another function"))
    return ui.div(*parts, class_="curve-gallery-fn")


def section_ui(section: Mapping, *, channel_id: str, w: int = TILE_W, h: int = TILE_H,
               band_breaks: tuple[float, float] = cs.DEEP_INDEX_BANDS,
               busy_metrics: frozenset | set = frozenset()):
    """One discipline: a divider head (name and count, the workbench's
    discipline colors) over one grid in which each function header spans the
    full width, so tiles stay column-aligned across functions. Each function
    row holds its primary tiles and then the cross-listed copies."""
    disc = str(section.get("discipline") or cs.UNMAPPED_DISCIPLINE)
    dcls = cs.discipline_class(disc)
    items = []
    for fn in section.get("functions") or []:
        items.append(function_header_ui(fn))
        under = fn.get("function_id") or fn.get("function_name") or "none"
        items.extend(tile_ui(t, channel_id=channel_id, w=w, h=h, band_breaks=band_breaks,
                             busy=str(t.get("metric") or "") in busy_metrics)
                     for t in fn.get("tiles") or [])
        items.extend(tile_ui(c["tile"], channel_id=channel_id, w=w, h=h, band_breaks=band_breaks,
                             cross=c, under=under)
                     for c in fn.get("cross") or [])
    head_parts = [
        ui.tags.span(disc, class_="curve-gallery-section-name"),
        ui.tags.span(cs._count_text(int(section.get("n") or 0)), class_="curve-gallery-section-count"),
    ]
    n_cross = int(section.get("n_cross") or 0)
    if n_cross:
        head_parts.append(ui.tags.span(f"{n_cross} cross-listed", class_="curve-gallery-section-cross"))
    head = ui.div(*head_parts, class_=f"curve-gallery-section-head {dcls}")
    return ui.tags.section(head, ui.div(*items, class_="curve-gallery"),
                           class_=f"curve-gallery-section {dcls}")


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
               band_breaks: tuple[float, float] = cs.DEEP_INDEX_BANDS,
               busy_metrics: Iterable[str] = (),
               recompute_all_id: str | None = None,
               recompute_all_disabled: bool = False):
    rows = [dict(r) for r in rows]
    busy = {str(m) for m in (busy_metrics or ())}
    c = gallery_counts(rows)
    mode = filter_mode if filter_mode in GALLERY_FILTERS else "all"
    counts = ui.div(
        ui.tags.strong(f"{c['n']} curve" + ("" if c["n"] == 1 else "s")),
        ui.tags.span(f", {c['flagged']} flagged, {c['out_of_scope']} not in scope", class_="text-muted"),
        class_="curve-gallery-counts",
    )
    actions = []
    if recompute_all_id:
        actions.append(ui.input_action_button(
            recompute_all_id,
            ui.TagList(fa("arrows-rotate"), " Recompute all"),
            class_="btn btn-sm btn-outline-primary curve-gallery-recompute",
            **({"disabled": "disabled"} if recompute_all_disabled else {})))
    actions.append(ui.input_radio_buttons(
        filter_input_id, None, GALLERY_FILTERS, selected=mode, inline=True))
    toolbar = ui.div(
        counts,
        ui.div(*actions, class_="curve-gallery-actions"),
        class_="curve-gallery-toolbar",
    )
    shown = filter_rows(rows, mode)
    if shown:
        if any("discipline" not in r for r in shown):
            cs.assign_functions(shown)
        # grouped from the tiles actually shown, so a filtered-out tile leaves
        # no empty header and no dead link behind
        grid = ui.div(*[section_ui(sec, channel_id=channel_id, w=w, h=h, band_breaks=band_breaks,
                                   busy_metrics=busy)
                        for sec in cs.group_tiles(shown)], class_="curve-gallery-sections")
    else:
        grid = ui.div("No curves match this filter.", class_="text-muted curve-gallery-empty")
    legend = ui.div(
        "Shaded column: the reference range. Dashed lines: the condition breaks at "
        f"{cs.fmt_num(band_breaks[0])} and {cs.fmt_num(band_breaks[1])}. Dotted red curve: not in "
        "scope. Orange marker: needs review. A curve that informs more than one function appears "
        "under each of them; the dashed copies are cross-listed and name the function the curve "
        "lives under. Click a tile to open its analysis.",
        class_="text-muted small curve-gallery-legend",
    )
    return ui.div(toolbar, grid, legend, class_="curve-gallery-wrap")


__all__ = [
    "REVIEW_STATUS_LABELS", "DECISION_LABELS", "GALLERY_FILTERS", "DEFAULT_SECTION",
    "curves_sections", "tile_row", "assign_functions", "gallery_rows", "filter_rows",
    "setinput_onclick", "tile_ui", "function_header_ui", "section_ui", "gallery_counts",
    "gallery_ui",
]
