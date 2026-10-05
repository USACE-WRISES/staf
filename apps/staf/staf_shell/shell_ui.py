"""The STAF page: one header for every tool and one section per tool.

The header holds the STAF name with the tier strip (Screening › Rapid › Detailed, the shown tool's
tier lit), the EASI | SFARI | DEEP switch (an input, ``staf_tool``; each button's tooltip is the
tool's full name), the shown tool's own control beside the switch (EASI's Nationwide screening) and
the shown tool's actions. Every tool's section is on the page from the start; only the shown
one is displayed (``html[data-staf-tool]``, set before the first paint), so switching is instant
and keeps each tool's work. A bookmark names the tool: ``?tool=sfari``; DEEP's assessment links
(``?assessment=<id>@<version>``) open DEEP.
"""
from __future__ import annotations

from htmltools import HTML
from shiny import ui

from . import head
from .loader import ORDER, SITE


def active_tool(query, tools) -> str:
    """The tool a request asks for, else DEEP for an assessment link, else EASI; never one that
    is unavailable while another is ready."""
    want = (query.get("tool") or "").strip().lower()
    if want not in ORDER:
        want = "deep" if query.get("assessment") else "easi"
    if not tools[want].ok:
        want = next((key for key in ORDER if tools[key].ok), want)
    return want


def _switch(tools, active):
    buttons = []
    for key in ORDER:
        tool, on = tools[key], key == active
        buttons.append(ui.tags.button(
            tool.name,
            {"type": "button", "role": "tab", "data-tool": key,
             "aria-selected": "true" if on else "false", "tabindex": "0" if on else "-1",
             "title": tool.full_name if tool.ok else f"{tool.name} is not available here",
             "aria-label": f"{tool.name}, {tool.full_name}, {tool.tier} tier"},
            class_="staf-tool-btn" + (" active" if on else ""), disabled=None if tool.ok else True))
    return ui.div(*buttons, id="staf_tool", class_="staf-tool-switch", role="tablist",
                  aria_label="STAF tools")


def _tiers(tools):
    """The STAF tiers in order, the shown tool's lit (by CSS, from html[data-staf-tool]). It only
    shows where the tool sits; the switch is what picks the tool, and its buttons name the tiers
    to screen readers, so the strip stays out of their way."""
    parts = []
    for i, key in enumerate(ORDER):
        if i:
            parts.append(ui.tags.span("›", class_="staf-tier-sep"))
        parts.append(ui.tags.span(tools[key].tier, {"data-staf-tool": key}, class_="staf-tier"))
    return ui.div(*parts, class_="staf-tiers", aria_hidden="true")


def _slots(tools, piece: str, cls: str) -> list:
    slots = []
    for key in ORDER:
        tool = tools[key]
        content = getattr(tool.module, piece)(key, prefix=f"{key}/") if tool.ok else None
        if content is not None:
            slots.append(ui.div(content, {"data-staf-tool": key}, class_=f"staf-slot {cls}"))
    return slots


def header(tools, active):
    return ui.div(
        ui.div(ui.tags.a("STAF", href=SITE, target="_blank", rel="noopener", class_="staf-brand",
                         title="Stream Tiered Assessment Framework"),
               _tiers(tools),
               class_="staf-header-left"),
        ui.div(_switch(tools, active), *_slots(tools, "tool_center_ui", "staf-center"),
               class_="staf-header-center"),
        ui.div(*_slots(tools, "tool_nav_ui", "staf-nav"),
               ui.tags.button("Menu", {"type": "button", "aria-expanded": "false"}, class_="staf-menu-btn"),
               class_="staf-header-right"),
        class_="staf-header easi-header",
    )


def _unavailable(tool):
    return ui.div(
        ui.div(ui.tags.h2(f"{tool.name} is not available here"),
               ui.tags.p(f"Open the standalone {tool.name} instead."),
               ui.tags.a(f"Open {tool.name}", href=tool.standalone, target="_blank", rel="noopener",
                         class_="btn btn-primary"),
               ui.tags.p(tool.error or "", class_="staf-unavailable-why"),
               class_="staf-unavailable-card"),
        class_="staf-unavailable")


def section(tool):
    body = tool.module.tool_body_ui(tool.key, prefix=f"{tool.key}/") if tool.ok else _unavailable(tool)
    return ui.div(body, {"data-staf-tool": tool.key}, class_="staf-section")


def make_ui(tools):
    ready = [tools[key] for key in ORDER if tools[key].ok]

    def app_ui(request):
        active = active_tool(request.query_params, tools)
        return ui.page_fillable(
            ui.head_content(
                # the shown tool is known before the first paint: no flash of another tool
                ui.tags.script(HTML(f'document.documentElement.setAttribute("data-staf-tool", "{active}");')),
                ui.tags.link(rel="icon", type="image/svg+xml", href="shell/favicon.svg"),
                *head.head_tags(ready, active),
                ui.tags.link(rel="stylesheet", href="shell/staf-shell.css?v=3"),
                ui.tags.script(src="shell/staf-shell.js?v=3", defer="")),
            ui.busy_indicators.use(pulse=False),
            header(tools, active),
            *[section(tools[key]) for key in ORDER],
            title=f"{tools[active].name} · STAF",
            padding=0,
            fillable=True,
        )

    return app_ui


def static_assets(tools, www) -> dict:
    """URL prefix -> folder. Never "/": a mount there would hide shinywidgets' routes."""
    mounts = {"/shell": www / "shell"}
    with_assets = [tools[key] for key in ORDER if (tools[key].www / "staf").is_dir()]
    if with_assets:
        mounts["/staf"] = with_assets[0].www / "staf"     # the same files in every tool
    for key in ORDER:
        if tools[key].www.is_dir():
            mounts[f"/{key}"] = tools[key].www
    return {prefix: folder.resolve() for prefix, folder in mounts.items()}
