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
#: custom message the server sends with ``{"dirty": bool}``
UNSAVED_MESSAGE = "staf-unsaved"


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


# --------------------------------------------------------------------------- scenarios on the page
#: input the scenario chip sends ({action, id}); the edit dialog's inputs and buttons
SCENARIO_EVENT = "staf_scenario_evt"
NAME_INPUT, DESC_INPUT, SAVE_BUTTON, DELETE_BUTTON = "staf_sc_name", "staf_sc_desc", "staf_sc_save", "staf_sc_delete"


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
    (each with its description under its name), then three small links: New, Edit and, once an
    alternative exists, Compare. Deleting a scenario lives in its Edit dialog."""
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
    links = [_scenario_link("new", "New", disabled=not scenarios.can_add(),
                            title="New scenario" if scenarios.can_add() else "Up to 10 scenarios"),
             _scenario_link("edit", "Edit",
                            title="Describe Existing Conditions" if cur.is_baseline else "Rename or describe")]
    if scenarios.has_alternatives():
        links.append(_scenario_link("compare", "Compare", title="Compare scenarios"))
    return tags.div(chip, tags.div(*links, class_="staf-scen-actions"),
                    class_="staf-scen" + (" solo" if not scenarios.has_alternatives() else ""))


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
                    can_delete: bool = False):
    """Name and description of a scenario; ``can_delete`` adds a Delete scenario button (an
    alternative's dialog), which asks for confirmation through :func:`delete_dialog`."""
    from shiny import ui
    name_field = (ui.div(ui.tags.label("Name", class_="control-label"), ui.div(name, class_="staf-scen-fixed"),
                         class_="form-group") if name_locked
                  else ui.input_text(NAME_INPUT, "Name", value=name, width="100%"))
    footer = [ui.modal_button("Cancel", class_="sfari-btn staf-btn"),
              ui.input_action_button(SAVE_BUTTON, "Save", class_="sfari-btn primary staf-btn")]
    if can_delete:
        footer.insert(0, ui.tags.button("Delete scenario", {"type": "button", "data-sc-action": "delete"},
                                        class_="btn sfari-btn staf-btn danger me-auto"))
    return ui.modal(
        ui.div(name_field,
               ui.input_text_area(DESC_INPUT, "Description", value=description, rows=3, width="100%",
                                  placeholder="Optional"),
               ui.div(error, class_="staf-scen-err") if error else None,
               class_="staf-scen-form"),
        title=title, easy_close=True, size="m", footer=ui.TagList(*footer), class_="staf-dialog")


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
