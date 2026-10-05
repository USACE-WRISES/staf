"""Shiny helpers the three apps share.

Downloads never open a page: :func:`download_button` is Shiny's own markup without
``target="_blank"``, and workbook handlers send :data:`XLSX_MEDIA_TYPE`, a plain binary type, so no
browser shows the file in a viewer (where formulas without stored results read as blanks). The
leave-page guard (``assets/unsaved-guard.js``) listens for :data:`UNSAVED_MESSAGE`.

Shiny and htmltools are imported lazily, so the workbook code runs without them.
"""
from __future__ import annotations

import hashlib
import json
import re

#: media type for every workbook download: a plain file, never an in-browser viewer
XLSX_MEDIA_TYPE = "application/octet-stream"
#: custom message the server sends with ``{"dirty": bool, "ns": str}`` (``ns``: the sending tool's
#: Shiny id prefix, ``str(session.ns)``, empty in a standalone app)
UNSAVED_MESSAGE = "staf-unsaved"


def tool_root_attrs(tool: str) -> dict:
    """Attributes for a tool's body element. ``data-staf-tool`` names the tool and ``data-staf-ns``
    carries its Shiny id prefix: empty in a standalone app, the module id inside the STAF app. The
    shared scripts (``assets/staf-ns.js``) read both to post every input to the right tool."""
    from shiny.module import current_namespace
    return {"data-staf-tool": tool, "data-staf-ns": str(current_namespace())}


def _anchor(id: str, label, base_class: str, icon=None, width=None, **kwargs):
    from htmltools import css, tags
    from shiny.module import resolve_id
    return tags.a(icon, label, {"class": base_class, "style": css(width=width)},
                  id=resolve_id(id), href="", download=True, aria_disabled="true", tabindex="-1",
                  **kwargs)


def download_button(id: str, label, *, icon=None, width=None, **kwargs):
    """``shiny.ui.download_button`` without ``target="_blank"``: the file downloads in place."""
    return _anchor(id, label, "btn btn-default shiny-download-link disabled", icon, width, **kwargs)


def download_link(id: str, label, *, icon=None, width=None, **kwargs):
    """``shiny.ui.download_link`` without ``target="_blank"``."""
    return _anchor(id, label, "shiny-download-link disabled", icon, width, **kwargs)


def state_fingerprint(*parts) -> str:
    """A stable digest of JSON-able state, for "has anything changed since the last save"."""
    text = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- the header's actions
def nav_actions(*extra):
    """The header's actions, the same in every tool (owner, 2026-10-05): New, Open and Save (the
    assessment file, ``assessment_file``), then About and Help. ``extra`` follows Help."""
    from shiny import ui
    return ui.div(
        ui.input_action_link("nav_new", "New"),
        ui.input_file("load_session", None, accept=[".json"], multiple=False, button_label="Open"),
        download_button("save_session", "Save", class_="easi-nav-btn"),
        ui.input_action_link("nav_about", "About", class_="easi-nav-sep"),
        ui.input_action_link("nav_help", "Help"),
        *extra,
        class_="easi-nav",
    )


def new_dialog(clears: str):
    """New's question while there is work to lose. ``clears`` names it, for example "the
    delineation and every score, note and photo". Its button is ``confirm_new``."""
    from shiny import ui
    return ui.modal(
        ui.markdown(f"Clear {clears} and start a new assessment? Use **Save** first if you want "
                    "to keep it."),
        title="Start a new assessment?",
        footer=ui.TagList(ui.modal_button("Cancel"),
                          ui.input_action_button("confirm_new", "Clear & start new", class_="btn-danger")),
        easy_close=True)


def info_dialog(title: str, text: str):
    """About and Help: ``text`` is markdown."""
    from shiny import ui
    return ui.modal(ui.markdown(text), title=title, easy_close=True, footer=ui.modal_button("Close"))


# --------------------------------------------------------------------------- the zoom cue
#: what the cue says while streams are hidden (owner, 2026-10-05: the same in every tool, and
#: short: the apps' own phrase, as on the Identify card and the readout)
ZOOM_CUE_TEXT = "Zoom in and click a stream"
_MAGNIFIER = ('<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false" fill="none" '
              'stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="7" cy="7" r="4.6"/>'
              '<path d="M10.4 10.4L14 14M7 5v4M5 7h4"/></svg>')


def zoom_cue_output():
    """Where the zoom cue renders: a slot over the top of the map, centred on the page under the
    STAF header's tool switch (``assets/staf.css``). The slot itself is the live region, so a
    screen reader hears the cue when it appears."""
    from shiny import ui
    return ui.div(ui.output_ui("zoom_cue"), class_="staf-zoom-cue-slot", role="status", aria_live="polite")


def zoom_cue(nudge: int = 0, pulse: bool = False):
    """The cue while the map is too far out to show streams. ``nudge`` counts the map clicks made
    out there, so each one renders a new element; ``pulse`` plays the short pulse that answers
    such a click (only on the render that the click caused)."""
    from htmltools import HTML, tags
    return tags.div(HTML(_MAGNIFIER), tags.span(ZOOM_CUE_TEXT),
                    {"data-nudge": str(nudge)},
                    class_="staf-zoom-cue" + (" is-nudged" if pulse else ""))


class ZoomCue:
    """The cue's state on one page: :meth:`render` is the body of each app's ``zoom_cue`` output
    (``shown``: the map is out too far on the step that picks a stream), and a click out there
    calls :meth:`nudge` through the app's reactive counter."""

    def __init__(self):
        self.seen = 0

    def render(self, shown: bool, nudge: int):
        pulse = nudge > self.seen
        self.seen = nudge
        return zoom_cue(nudge, pulse=pulse) if shown else None


# --------------------------------------------------------------------------- scenarios on the page
#: input the scenario chip sends ({action, id}); the edit dialog's inputs and buttons
SCENARIO_EVENT = "staf_scenario_evt"
NAME_INPUT, DESC_INPUT, SAVE_BUTTON, DELETE_BUTTON = "staf_sc_name", "staf_sc_desc", "staf_sc_save", "staf_sc_delete"
#: the Add dialog's Start from select: a scenario id, or START_BLANK
START_INPUT, START_BLANK = "staf_sc_from", "blank"


#: small line icons for the scenario links, drawn in the text color so every app shows them alike
_ICONS = {
    "new": ('<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false">'
            '<path d="M8 3v10M3 8h10" fill="none" stroke="currentColor" stroke-width="1.6" '
            'stroke-linecap="round"/></svg>'),
    "edit": ('<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false">'
             '<path d="M11.2 2.6l2.2 2.2-7.7 7.7-2.9.7.7-2.9z" fill="none" stroke="currentColor" '
             'stroke-width="1.4" stroke-linejoin="round"/></svg>'),
    "compare": ('<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false">'
                '<rect x="2" y="3" width="5" height="10" rx="1" fill="none" stroke="currentColor" stroke-width="1.4"/>'
                '<rect x="9" y="3" width="5" height="10" rx="1" fill="none" stroke="currentColor" stroke-width="1.4"/>'
                '</svg>'),
}


def _scenario_link(action: str, label: str, *, title=None, disabled: bool = False):
    from htmltools import HTML, tags
    return tags.button(HTML(_ICONS[action]), tags.span(label), {"type": "button", "data-sc-action": action},
                       class_="staf-scen-link", title=title, disabled=True if disabled else None)


def scenario_bar(scenarios):
    """The scenario control at the top of the score rail: a dropdown that only switches scenarios
    (each with its description under its name), then three small links: Add (its dialog asks
    where the scenario starts, :func:`add_dialog`), Edit and, once an alternative exists, Compare.
    Deleting a scenario lives in its Edit dialog."""
    from htmltools import tags
    cur = scenarios.current
    items = []
    for s in scenarios.items:
        parts = [tags.span(s.name, class_="staf-scen-item-name")]
        if s.description:
            parts.append(tags.span(s.description, class_="staf-scen-item-desc"))
        items.append(tags.button(*parts, {"type": "button", "data-sc-action": "select", "data-sc-id": s.id,
                                          "role": "menuitemradio",
                                          "aria-checked": "true" if s.id == cur.id else "false"},
                                 class_="staf-scen-item" + (" active" if s.id == cur.id else ""),
                                 title=s.description or None))
    summary = tags.summary(tags.span("Scenario", class_="staf-scen-lab"), tags.span(cur.name, class_="staf-scen-name"),
                           class_="staf-scen-sum", title=cur.description or None)
    chip = tags.details(summary, tags.div(*items, class_="staf-scen-menu", role="menu"),
                        class_="staf-scen-chip" + ("" if cur.is_baseline else " alt"))
    links = [_scenario_link("new", "Add", disabled=not scenarios.can_add(),
                            title="Add a scenario" if scenarios.can_add() else "Up to 10 scenarios"),
             _scenario_link("edit", "Edit",
                            title="Describe Existing Conditions" if cur.is_baseline else "Rename or describe")]
    if scenarios.has_alternatives():
        links.append(_scenario_link("compare", "Compare", title="Compare scenarios"))
    return tags.div(chip, tags.div(*links, class_="staf-scen-actions"),
                    class_="staf-scen" + (" solo" if not scenarios.has_alternatives() else ""))


# --------------------------------------------------------------------------- metric rows
#: the buttons under every metric of a function page (assets/metric-rows.css and .js; owner,
#: 2026-10-04): one line icon each, drawn in the text color, so EASI, SFARI and DEEP show the
#: same buttons
_ROW_SVG = ('<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false" fill="none" '
            'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{}</svg>')
ROW_ICONS = {
    "scoring": _ROW_SVG.format('<path d="M2.5 2v11.5H14"/><path d="M4.5 4.5c2.5 0 3 4.5 5 6.2 1 .8 2.2 1 3.5 1"/>'),
    "note": _ROW_SVG.format('<path d="M11.2 2.6l2.2 2.2-7.7 7.7-2.9.7.7-2.9z"/><path d="M9.6 4.2l2.2 2.2"/>'),
    "photo": _ROW_SVG.format('<path d="M2 5.5h2.6l1.2-1.8h4.4l1.2 1.8H14v7.5H2z"/><circle cx="8" cy="9.1" r="2.3"/>'),
    "na": _ROW_SVG.format('<circle cx="8" cy="8" r="5.6"/><path d="M4.1 11.9l7.8-7.8"/>'),
}


def metric_icon(kind: str):
    from htmltools import HTML
    return HTML(ROW_ICONS[kind])


def metric_action(kind: str, label: str, *, on: bool = False, has: bool = False, count: int | None = None,
                  title: str | None = None, icon: str | None = None):
    """One button of a metric row: outlined, a line icon and a label, always visible. It opens and
    closes the row's ``kind`` panel in the browser (``assets/metric-rows.js``).

    ``on``: the panel starts open. ``has``: it holds something, which a note button shows as a dot.
    ``count``: the photos attached, shown as a number (and the label reads "Photos" past one)."""
    from htmltools import HTML, tags
    icon = icon or ("note" if kind == "fnnote" else kind)
    if count is not None and count > 1 and label == "Photo":
        label = "Photos"
    parts = [HTML(ROW_ICONS[icon]), tags.span(label, class_="staf-act-label")]
    if kind in ("note", "fnnote"):
        parts.append(tags.span(class_="staf-act-dot"))
    if count is not None:
        parts.append(tags.span(str(count) if count else "", class_="staf-act-count"))
    cls = "staf-act" + (" on" if on else "") + (" has" if has or (count or 0) > 0 else "")
    return tags.button(*parts, {"type": "button", "data-staf-panel": kind, "aria-expanded": "true" if on else "false",
                                "title": title or label}, class_=cls)


def metric_check(label: str, box, *, on: bool = False, title: str | None = None, icon: str = "na",
                 extra_class: str = ""):
    """A checkbox shaped like a row button (DEEP's N/A): ``box`` is the app's own
    ``<input type="checkbox">``, kept in the label so the app's change handler still reads it."""
    from htmltools import HTML, tags
    return tags.label(HTML(ROW_ICONS[icon]), tags.span(label, class_="staf-act-label"), box,
                      {"title": title or label},
                      class_="staf-act" + (" on" if on else "") + (f" {extra_class}" if extra_class else ""))


def metric_actions(*buttons):
    """The row of buttons under a metric's text (``None`` entries are skipped)."""
    from htmltools import tags
    return tags.div(*[b for b in buttons if b is not None], class_="staf-metric-acts")


def scoring_criteria(rows, *, title: str | None = None, sub: str | None = None):
    """Rating rungs for a Scoring panel: ``rows`` of ``(label, band, text)`` with ``band`` one of
    good, fair or poor (the dot's color)."""
    from htmltools import tags
    cells = []
    for label, band, text in rows:
        cells.append(tags.span(tags.span(class_=f"staf-crit-dot {band}"), label, class_="staf-crit-lbl"))
        cells.append(tags.span(text))
    return tags.div(tags.div(title, class_="staf-panel-title") if title else None,
                    tags.div(sub, class_="staf-panel-sub") if sub else None,
                    tags.div(*cells, class_="staf-crit"))


def report_scenario(scenarios):
    """``(name, description)`` of the scenario shown, for its report to name, or None while the
    assessment has only Existing Conditions without a description (its report reads as before)."""
    cur = scenarios.current
    if not scenarios.has_alternatives() and not cur.description:
        return None
    return cur.name, cur.description or ""


def scenario_suffix(scenarios) -> str:
    """``-<name>`` for a report download made while an alternative is shown, empty for Existing
    Conditions, so a file never reads as the wrong scenario."""
    cur = scenarios.current
    if cur.is_baseline:
        return ""
    slug = re.sub(r"[^a-z0-9]+", "-", cur.name.lower()).strip("-")
    return f"-{slug or cur.id}"


def scenario_dialog(*, title: str, name: str, description: str, name_locked: bool, error: str | None = None,
                    can_delete: bool = False, start=None, save_label: str = "Save"):
    """Name and description of a scenario; ``can_delete`` adds a Delete scenario button (an
    alternative's dialog), which asks for confirmation through :func:`delete_dialog`. ``start``,
    ``(choices, selected, hint)``, adds the Start from select under the name (:func:`add_dialog`)."""
    from shiny import ui
    name_field = (ui.div(ui.tags.label("Name", class_="control-label"), ui.div(name, class_="staf-scen-fixed"),
                         class_="form-group") if name_locked
                  else ui.input_text(NAME_INPUT, "Name", value=name, width="100%"))
    start_field = None
    if start is not None:
        choices, selected, hint = start
        start_field = ui.div(ui.input_select(START_INPUT, "Start from", choices, selected=selected, width="100%"),
                             ui.div(hint, class_="staf-scen-hint") if hint else None,
                             class_="staf-scen-start")
    footer = [ui.modal_button("Cancel", class_="sfari-btn staf-btn"),
              ui.input_action_button(SAVE_BUTTON, save_label, class_="sfari-btn primary staf-btn")]
    if can_delete:
        footer.insert(0, ui.tags.button("Delete scenario", {"type": "button", "data-sc-action": "delete"},
                                        class_="btn sfari-btn staf-btn danger me-auto"))
    return ui.modal(
        ui.div(name_field, start_field,
               ui.input_text_area(DESC_INPUT, "Description", value=description, rows=3, width="100%",
                                  placeholder="Optional"),
               ui.div(error, class_="staf-scen-err") if error else None,
               class_="staf-scen-form"),
        title=title, easy_close=True, size="m", footer=ui.TagList(*footer), class_="staf-dialog")


def add_dialog(scenarios, *, name: str, description: str = "", start: str | None = None, hint: str = "",
               error: str | None = None):
    """Add a scenario (owner, 2026-10-05): its name, where it starts and a description. Start from
    lists every scenario, Existing Conditions first and chosen unless ``start`` names another,
    then Blank, which each app defines as what a fresh assessment of the site starts with.
    ``hint`` is the app's own line on what a copy brings and what Blank keeps."""
    choices = dict((s.id, s.name) for s in scenarios.items)
    choices[START_BLANK] = "Blank"
    selected = start if start in choices else scenarios.baseline.id
    return scenario_dialog(title="Add a scenario", name=name, description=description, name_locked=False,
                           error=error, start=(choices, selected, hint), save_label="Add")


def delete_dialog(name: str):
    from shiny import ui
    return ui.modal(ui.p(f"Delete \"{name}\"? Its entries and scores are removed. Existing Conditions is not affected."),
                    title="Delete scenario", easy_close=True, size="m",
                    footer=ui.TagList(ui.modal_button("Cancel", class_="sfari-btn staf-btn"),
                                      ui.input_action_button(DELETE_BUTTON, "Delete",
                                                             class_="sfari-btn staf-btn danger-solid")),
                    class_="staf-dialog")


def comparison_table(cmp):
    """Scenarios side by side: the ECI, the sub-indices and every function, each alternative
    followed by its change from Existing Conditions."""
    from htmltools import tags
    from .model.compare import delta_sign, fmt_delta, fmt_measure
    head = [tags.th("", class_="staf-cmp-label"), tags.th(cmp.names[0], class_="staf-cmp-col base")]
    for name in cmp.names[1:]:
        head += [tags.th(name, class_="staf-cmp-col"), tags.th("Change", class_="staf-cmp-col delta")]
    body, group = [], None
    span = 2 + 2 * (len(cmp.names) - 1)
    for row in cmp.rows:
        if row.group != group:
            group = row.group
            body.append(tags.tr(tags.td(group or "Functions", colspan=span), class_="staf-cmp-group"))
        cells = [tags.td(row.label, class_="staf-cmp-label"),
                 tags.td(fmt_measure(row.values[0], row.digits), class_="staf-cmp-num base")]
        for v, d in zip(row.values[1:], row.deltas):
            sign = delta_sign(d)
            cells += [tags.td(fmt_measure(v, row.digits), class_="staf-cmp-num"),
                      tags.td(fmt_delta(d, row.digits),
                              class_="staf-cmp-num delta" + (" up" if sign > 0 else " down" if sign < 0 else ""))]
        body.append(tags.tr(*cells, class_="staf-cmp-row" + (" headline" if row.key == "eci" else "")))
    return tags.div(tags.table(tags.thead(tags.tr(*head)), tags.tbody(*body), class_="staf-cmp"), class_="staf-cmp-wrap")


def compare_dialog(cmp):
    from shiny import ui
    return ui.modal(comparison_table(cmp), title="Compare scenarios", easy_close=True, size="xl",
                    footer=ui.modal_button("Close", class_="sfari-btn staf-btn"), class_="staf-dialog")


def summary_block(info, scenario=None):
    """The summary block every report opens with. ``scenario`` (``(name, description)``, from
    :func:`report_scenario`) names the scenario the report is for."""
    from htmltools import tags
    rows = []
    if scenario:
        name, description = scenario
        rows.append(("Scenario", name))
        if description:
            rows.append(("Description", description))
    rows += list(info.rows())
    return tags.table(tags.tbody(*[tags.tr(tags.th(label), tags.td(value)) for label, value in rows]),
                      class_="staf-sum")


def rail_delta(cmp, index: int):
    """One line under the chip while an alternative is shown: its ECI against Existing Conditions."""
    from htmltools import tags
    from .model.compare import delta_sign, fmt_delta
    if cmp is None or index < 1 or not cmp.rows:
        return None
    d = cmp.rows[0].deltas[index - 1] if index - 1 < len(cmp.rows[0].deltas) else None
    if d is None:
        return tags.div("vs Existing Conditions: no ECI yet", class_="staf-scen-delta")
    sign = delta_sign(d)
    return tags.div("vs Existing Conditions: ECI ",
                    tags.span(fmt_delta(d, cmp.rows[0].digits), class_="up" if sign > 0 else "down" if sign < 0 else ""),
                    class_="staf-scen-delta")
