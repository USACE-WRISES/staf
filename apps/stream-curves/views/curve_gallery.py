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

from streamcurves import curve_sources as src
from streamcurves import curve_svg as cs
from streamcurves import run_state as rs
from views import source_panel as sp
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
    "not_built": "Not built here",
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


def reference_tiles_for(build, mapping, *, built=(), decisions=()) -> list[dict]:
    """Tiles for every curve a session scores without having fitted it
    (``pressure_evidence.reference_rows``): carried forward, from a rung above
    the hierarchy, or a fixed criterion, each placed in the functions the bundle
    places it in under the owner's decisions (REF-15). Each can be removed; a
    removed one stays on the page, dimmed, with the decision to undo."""
    from streamcurves import owner_curves as oc
    from streamcurves import pressure_evidence as pe
    decisions = list(decisions or [])
    effective = oc.effective_build(build, decisions, built=built)
    tiles, placement = [], []
    for mk, entry in pe.reference_rows(effective, mapping, built=built).items():
        tile = cs.reference_tile(mk, entry)
        tile["source_title"] = src.source_title(mk, entry, build=effective)
        tile["removable"] = True
        if entry.get("owner"):
            # a curve the owner chose: its undo gives the build's choice back
            tile["owner_decision"] = entry["owner"].get("id")
            tile["owner_note"] = (f"Chosen by {entry['owner'].get('recordedBy')}: "
                                  f"{entry['owner'].get('rationale')}")
        tiles.append(tile)
        placement.extend(entry["mapping"])
    removed = oc.removed(decisions)
    if removed:
        base = pe.reference_rows(build, mapping, built=built)
        for mk, d in removed.items():
            entry = base.get(mk)
            if entry is None:
                continue
            tile = cs.reference_tile(mk, entry)
            tile["source_title"] = src.source_title(mk, entry, build=build)
            tile["status_text"] = "Removed by the owner"
            tile["removed_decision"] = d.get("id")
            tile["in_scope"] = False
            tiles.append(tile)
            placement.extend(entry["mapping"])
    return cs.assign_functions(tiles, placement)


def mark_not_selected(tiles: Iterable[dict], build) -> list[dict]:
    """Set ``not_selected_fids`` on every tile the session fitted: the functions
    SELECT-04 left it out of (``pressure_evidence.not_selected_pairs``). The
    gallery reads each placement against it, so a curve drawn under a function
    it does not score reads "Not selected here"."""
    from streamcurves import pressure_evidence as pe
    by_metric: dict[str, set] = {}
    for m, fid in pe.not_selected_pairs(build):
        by_metric.setdefault(str(m), set()).add(str(fid))
    included = owner_included(build)
    out = []
    for t in tiles:
        if not t.get("read_only"):
            mk = str(t.get("metric") or "")
            t["not_selected_fids"] = sorted(by_metric.get(mk, ()))
            t["owner_included"] = dict(included.get(mk) or {})
        out.append(t)
    return out


def owner_included(build) -> dict:
    """``{metric: {function id: decision id}}`` of the fitted curves the owner put
    back into a function the portfolio left them out of (REF-15)."""
    out: dict[str, dict] = {}
    for d in (build or {}).get("ownerDecisions") or []:
        if d.get("action") == "include":
            for fid in d.get("functions") or []:
                out.setdefault(str(d.get("metric")), {})[str(fid)] = d.get("id")
    return out


def not_selected_here(row: Mapping, here) -> bool:
    """The tile placed under function ``here`` (a function id) is a curve the
    portfolio left out of that function."""
    return here is not None and str(here) in {str(f) for f in row.get("not_selected_fids") or ()}


def reference_tiles(state: AppState) -> list[dict]:
    """Headless :func:`reference_tiles_for` from the state."""
    with reactive.isolate():
        build = state.reference_build()
        mapping = state.discipline_function_mapping()
        built = state.completed_metrics() or {}
        decisions = state.owner_curve_decisions() or []
    return reference_tiles_for(build, mapping, built=built, decisions=decisions)


def gallery_rows(state: AppState, metrics: Optional[Iterable[str]] = None, *,
                 include_reference: bool = False) -> list[dict]:
    """Headless path: every eligible metric's tile straight from the state, in
    the table's order, with its discipline and functions assigned. The page
    itself reads its row snapshots instead so the gallery invalidates exactly
    when a table row does. ``include_reference`` adds the read-only tiles of the
    curves the session did not fit (restricted to ``metrics`` when given)."""
    with reactive.isolate():
        mc = state.metric_config() or {}
        review = state.curve_review() or {}
        functions = state.column_functions() or {}
        mapping = state.discipline_function_mapping()
        from streamcurves import owner_curves as oc
        build = oc.effective_build(state.reference_build(), state.owner_curve_decisions() or [],
                                   built=state.completed_metrics() or {})
    keys = list(metrics) if metrics is not None else ss.eligible_summary_metrics(mc)
    reference = set()
    if include_reference:
        from streamcurves import pressure_evidence as pe
        with reactive.isolate():
            reference = set(pe.reference_keys(state.reference_build())) - set(mc)
    out = []
    for m in keys:
        if m in reference:
            continue
        try:
            rows = ss.get_metric_curve_rows(state, m)
        except (KeyError, TypeError, ValueError):
            rows = None
        out.append(tile_row(m, rows, metric_entry=mc.get(m), review_entry=review.get(m),
                            function_label=functions.get(m)))
    out = mark_not_selected(assign_functions(out, mapping), build)
    if include_reference:
        wanted = set(metrics) if metrics is not None else None
        out += [t for t in reference_tiles(state)
                if wanted is None or t.get("metric") in wanted]
    return out


def metric_basis(state: AppState, metric: str, decisions: Iterable[Mapping]) -> Optional[str]:
    """The basis digest the candidate register computes for ``metric``'s curve under
    ``decisions`` (the session's own with the decision being made merged in): its fitted
    tile, or the curve its source gives it, or the one a removal leaves dimmed. What a
    REF-15 decision records, so a later change to that curve asks for another look."""
    from streamcurves import candidates as _c
    with reactive.isolate():
        mc = state.metric_config() or {}
        build = state.reference_build()
        mapping = state.discipline_function_mapping()
        built = state.completed_metrics() or {}
    tiles = (gallery_rows(state, [metric]) if metric in mc else []) + \
        reference_tiles_for(build, mapping, built=built, decisions=list(decisions or []))
    tile = next((x for x in tiles if str(x.get("metric")) == str(metric)), None)
    return _c.tile_basis_digest(tile, mc.get(metric))


def filter_rows(rows: Iterable[Mapping], mode: str) -> list[dict]:
    rows = [dict(r) for r in rows]
    if mode == "flagged":
        return [r for r in rows if r.get("needs_review")]
    if mode == "out_of_scope":
        return [r for r in rows if r.get("in_scope") is False]
    if mode == "stratified":
        return [r for r in rows if len(r.get("strata") or []) > 1]
    if mode == "not_built":
        return [r for r in rows if r.get("read_only")]
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
    if row.get("read_only"):
        return reference_tile_ui(row, channel_id=channel_id, w=w, h=h,
                                 band_breaks=band_breaks, cross=cross, under=under)
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
    status = cs.status_label(row)
    title = cs.tile_title(row)
    here = under if cross else row.get("function_id")
    if not_selected_here(row, here):
        status = src.kind_label("not_selected")
        classes.append("is-not-selected")
        title = f"{status}. {src.kind_sentence('not_selected')} {title}"
        right.insert(0, ui.tags.button(
            fa("circle-plus"), " Use", type="button",
            class_="btn btn-link btn-sm curve-tile-use",
            onclick=sp.act_onclick(metric, "include", [here]),
            title="Use this curve in this function (recorded as your decision)"))
    elif str(here) in (row.get("owner_included") or {}):
        status = "Used by the owner"
        classes.append("is-owner-included")
        title = f"{status}: the two-per-function rule left it out here. {title}"
        right.insert(0, ui.tags.button(
            fa("rotate-left"), type="button", class_="btn btn-link btn-sm curve-tile-remove",
            onclick=sp.undo_onclick(row["owner_included"][str(here)]),
            title="Undo: leave this curve out of this function again"))
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
            ui.tags.span(status, class_="curve-tile-status"),
            class_="curve-tile-head",
        ),
        ui.HTML(cs.tile_svg(row, w=w, h=h, band_breaks=band_breaks)),
        ui.div(foot_left, ui.div(*right, class_="curve-tile-foot-right"), class_="curve-tile-foot"),
        id=dom_id,
        class_=" ".join(classes),
        role="button", tabindex="0", title=title, data_metric=metric,
        data_role="cross" if cross else "primary",
        onclick=open_click,
        onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.click();}",
    )


def reference_tile_ui(row: Mapping, *, channel_id: str, w: int = TILE_W, h: int = TILE_H,
                      band_breaks: tuple[float, float] = cs.DEEP_INDEX_BANDS,
                      cross: Mapping | None = None, under: Any = None):
    """A curve the session did not fit: no analysis to open and nothing to
    recompute, so a click opens its source panel instead. The status pill names
    the source with its icon. The primary tile carries the owner's action on it:
    remove the curve, or undo a removal (REF-15)."""
    metric = str(row.get("metric") or "")
    right = []
    n_strata = len(row.get("strata") or [])
    if n_strata > 1:
        right.append(ui.tags.span(f"{n_strata} strata", class_="curve-tile-strata"))
    if cross is None and row.get("removed_decision"):
        right.append(ui.tags.button(
            fa("rotate-left"), type="button", class_="btn btn-link btn-sm curve-tile-remove",
            onclick=sp.undo_onclick(row["removed_decision"]),
            title="Undo the removal"))
    elif cross is None and row.get("owner_decision"):
        right.append(ui.tags.button(
            fa("rotate-left"), type="button", class_="btn btn-link btn-sm curve-tile-remove",
            onclick=sp.undo_onclick(row["owner_decision"]),
            title="Undo this choice: the metric scores as the build left it"))
    elif cross is None and row.get("removable"):
        right.append(ui.tags.button(
            fa("trash-can"), type="button", class_="btn btn-link btn-sm curve-tile-remove",
            onclick=sp.act_onclick(metric, "remove"),
            title="Remove this curve from the assessment"))
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
        foot_left = ui.tags.span(("also: " + ", ".join(also)) if also else "",
                                 class_="curve-tile-also",
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
            ui.tags.span(_source_status(row), class_="curve-tile-status"),
            class_="curve-tile-head",
        ),
        ui.HTML(cs.tile_svg(row, w=w, h=h, band_breaks=band_breaks)),
        ui.div(foot_left, ui.div(*right, class_="curve-tile-foot-right"),
               class_="curve-tile-foot"),
        id=dom_id,
        class_=" ".join(classes),
        role="button", tabindex="0",
        title=cs.tile_title(row) + (f". {row['owner_note']}" if row.get("owner_note") else ""),
        data_metric=metric, data_role="cross" if cross else "primary",
        onclick=sp.open_onclick(metric),
        onkeydown=sp.open_onkeydown(),
    )


def _source_status(row: Mapping):
    """The status pill of a curve from another source: its kind's icon and label."""
    icon = src.kind_icon(row.get("source_kind"))
    return ui.TagList(fa(icon) if icon and row.get("status_text") == row.get("badge") else None,
                      cs.status_label(row))


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
        "not_built": sum(1 for r in rows if r.get("read_only")),
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
        ui.tags.span(f", {c['flagged']} flagged, {c['out_of_scope']} not in scope"
                     + (f", {c['not_built']} not built here" if c["not_built"] else ""),
                     class_="text-muted"),
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
    actions.append(sources_popover(rows))
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
        "lives under. Click a curve built here to open its analysis. A tile with a colored edge "
        "comes from another source: click it to see where it comes from and why. Removing one, "
        "or using a curve the build left out, is recorded as your decision and applies to "
        "every later build of this region.",
        class_="text-muted small curve-gallery-legend",
    )
    return ui.div(toolbar, grid, legend, class_="curve-gallery-wrap")


def sources_popover(rows: Iterable[Mapping]):
    """The toolbar's "About sources": every kind of curve on the page, how many
    there are and what each rests on."""
    rows = list(rows)
    counts = src.kind_counts(rows)
    left_out = {str(r.get("metric")) for r in rows if r.get("not_selected_fids")}
    # a fitted curve left out of every function it is drawn under is not used at
    # all, so it counts as not selected rather than as built here
    unused = {str(r.get("metric")) for r in rows
              if not r.get("read_only") and r.get("not_selected_fids")
              and {str(f) for f in [r.get("function_id")] + [x.get("id") for x in
                                                              r.get("also_function_refs") or []]
                   if f} <= {str(f) for f in r["not_selected_fids"]}}
    if unused and counts.get("built"):
        counts["built"] -= len(unused)
        if not counts["built"]:
            counts.pop("built")
    if left_out:
        counts["not_selected"] = len(left_out)
    items = [ui.tags.li(
        ui.div(sp.kind_badge(kind), ui.tags.span(str(n), class_="source-legend-n"),
               class_="source-legend-head"),
        ui.div(src.kind_sentence(kind), class_="source-legend-sentence"))
        for kind, n in counts.items()]
    return ui.popover(
        ui.tags.button(fa("circle-info"), " About sources", type="button",
                       class_="btn btn-sm btn-outline-secondary curve-gallery-sources"),
        ui.tags.ul(*items, class_="source-legend"),
        ui.div("Click a curve from another source to see where it comes from.",
               class_="text-muted small"),
        title="Where these curves come from", placement="bottom",
        options={"customClass": "source-legend-popover"})


__all__ = [
    "REVIEW_STATUS_LABELS", "DECISION_LABELS", "GALLERY_FILTERS", "DEFAULT_SECTION",
    "curves_sections", "tile_row", "assign_functions", "gallery_rows", "filter_rows", "metric_basis",
    "setinput_onclick", "tile_ui", "function_header_ui", "section_ui", "gallery_counts",
    "gallery_ui", "mark_not_selected", "not_selected_here", "sources_popover",
]
