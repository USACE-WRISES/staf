"""The Project panel: HYPE's layer-tree card, listing the project, its seven stages and the tools.

Pure markup builders (no server state of their own). The stage strip's server
(views/stagebar.py) renders the panel body from the same snapshot the strip uses, so the two can
never disagree about where you are or what is done. Every row is a <button data-jump="..."> and
www/shell.js posts clicks to the one input the panel body names (data-jump-to), so the rows
carry no Shiny input ids of their own.

Targets: "project" (Project properties), "stage:<key>", "substep:<n>" (a wizard step),
"section:<value>" (a workspace or Reference curves section) and "tool:<key>".
"""
from __future__ import annotations

from shiny import ui

from streamcurves import project_meta
from streamcurves import run_state as rs
from streamcurves.easi_method import stages as es
from views.theme import bi

#: Status -> the state class HYPE's strip uses (shell.css .st-*). "ready" has none: todo.
STATE_CLASS = {
    rs.STAGE_DONE: "st-done",
    rs.STAGE_READY: "",
    rs.STAGE_RUNNING: "st-running",
    rs.STAGE_ATTENTION: "st-attention",
    rs.STAGE_BLOCKED: "st-locked",
}

# Every icon here must exist in the vendored www/vendor/bs-icons.json subset or bi() raises
# at render (test_rules_page_nav pins the whole map against it).
TOOL_ICON = {"regional": "bezier2", "xsec": "graph-down",
             "nrsa": "globe-americas", "build": "magic",
             "rules": "ui-checks"}


def stage_target(key: str) -> str:
    return f"stage:{key}"


def tool_target(key: str) -> str:
    return f"tool:{key}"


def _num(i: int, status: str):
    """The numbered circle; the state class on the row draws the check, ring or spinner."""
    return ui.tags.span(str(i + 1), class_="sc-stage-num")


def children(view: dict, stage_key: str) -> list[tuple[str, str, bool]]:
    """(target, label, current) rows under a stage, for the stage that is current."""
    tab, dview = view.get("tab"), view.get("view")
    wiz = view.get("wiz_step")
    out: list[tuple[str, str, bool]] = []
    subs = rs.STAGE_SUBSTEPS.get(stage_key) or []
    if len(subs) > 1:
        in_wizard = tab == "data" and dview in ("new", "wizard")
        for n, label in subs:
            out.append((f"substep:{n}", label, in_wizard and wiz == n))
    secs = rs.STAGE_SECTIONS.get(stage_key) or []
    if secs:
        active = {"refine_map": view.get("active_section") if dview == "workspace" else None,
                  "curve_review": view.get("curves_section") if tab == "curves" else None
                  }.get(stage_key)
        for value, label in secs:
            out.append((f"section:{value}", label, active == value))
    return out


def tree(view: dict, *, project: dict | None, tools_allowed: set[str]) -> ui.TagList:
    """The panel body: project row, Workflow (stages with the current one's children), Tools."""
    statuses = view["statuses"]
    current = view.get("current")
    tool = view.get("tool")
    has_data = bool(view.get("has_data"))
    rows: list = []

    # the project row
    name = (project or {}).get("name") or "No project open"
    sub = (project or {}).get("sub") or ""
    rows.append(ui.tags.button(
        ui.span(name, class_="sc-tree-project-name"),
        ui.span(sub, class_="sc-tree-project-sub") if sub else None,
        type="button", class_="sc-tree-project", title="Project properties",
        **{"data-jump": "project"}))

    rows.append(ui.div("Workflow", class_="sc-tree-sec"))
    for i, key in enumerate(rs.STAGE_KEYS):
        info = statuses.get(key) or {}
        status = info.get("status", rs.STAGE_BLOCKED)
        cls = "sc-tree-row " + STATE_CLASS.get(status, "st-locked")
        if key == current:
            cls += " is-current"
        count = None
        if key == "curve_review" and view.get("n_flagged") and status == rs.STAGE_ATTENTION:
            count = ui.tags.span(str(view["n_flagged"]), class_="sc-stage-count sc-tree-count")
        rows.append(ui.tags.button(
            _num(i, status),
            ui.span(rs.STAGE_SHORT[key], class_="sc-tree-label"),
            count,
            type="button", class_=cls,
            title=f"Step {i + 1}: {rs.STAGE_LABELS[key]}. {info.get('detail') or ''}".strip(),
            **{"data-jump": stage_target(key)}))
        if key == current:
            for target, label, is_cur in children(view, key):
                badge = None
                if target == "section:validation" and view.get("n_precheck_warnings"):
                    badge = ui.tags.span(str(view["n_precheck_warnings"]),
                                         class_="sc-stage-count sc-tree-count")
                rows.append(ui.tags.button(
                    ui.span(label, class_="sc-tree-label"), badge,
                    type="button",
                    class_="sc-tree-row is-child" + (" is-current" if is_cur else ""),
                    **{"data-jump": target}))

    rows.append(ui.div("Tools", class_="sc-tree-sec"))
    for key in rs.TOOL_KEYS:
        if key not in tools_allowed:
            continue
        cls = "sc-tree-row"
        if key == tool:
            cls += " is-current"
        elif not has_data and key not in rs.TOOLS_WITHOUT_DATA:
            cls += " is-dim"         # dimmed, never disabled: the page says what it needs
        rows.append(ui.tags.button(
            ui.span(bi(TOOL_ICON[key]), class_="sc-tree-mark"),
            ui.span(rs.TOOL_LABELS[key], class_="sc-tree-label"),
            type="button", class_=cls, title=rs.TOOL_TITLES[key],
            **{"data-jump": tool_target(key)}))
    return ui.TagList(*rows)


def _tool_rows(view: dict, tools_allowed: set[str], keys) -> list:
    tool = view.get("tool")
    has_data = bool(view.get("has_data"))
    rows = []
    for key in keys:
        if key not in tools_allowed:
            continue
        cls = "sc-tree-row"
        if key == tool:
            cls += " is-current"
        elif not has_data and key not in rs.TOOLS_WITHOUT_DATA:
            cls += " is-dim"
        rows.append(ui.tags.button(
            ui.span(bi(TOOL_ICON[key]), class_="sc-tree-mark"),
            ui.span(rs.TOOL_LABELS[key], class_="sc-tree-label"),
            type="button", class_=cls, title=rs.TOOL_TITLES[key],
            **{"data-jump": tool_target(key)}))
    return rows


def easi_tree(view: dict, *, project: dict | None, tools_allowed: set[str]) -> ui.TagList:
    """The panel body for an EASI method project: its five stages, then the tools that need
    no DEEP dataset."""
    ev = view.get("easi") or {}
    statuses = ev.get("statuses") or {}
    current = view.get("current")
    name = (project or {}).get("name") or "EASI method"
    rows: list = [ui.tags.button(
        ui.span(name, class_="sc-tree-project-name"),
        ui.span(ev.get("version_line") or "", class_="sc-tree-project-sub"),
        type="button", class_="sc-tree-project", title="Project properties",
        **{"data-jump": "project"})]
    rows.append(ui.div("Workflow", class_="sc-tree-sec"))
    n_pending = (ev.get("snap") or {}).get("n_pending") or 0
    for i, key in enumerate(es.STAGE_KEYS):
        info = statuses.get(key) or {}
        status = info.get("status", rs.STAGE_BLOCKED)
        cls = "sc-tree-row " + STATE_CLASS.get(status, "st-locked")
        if key == current:
            cls += " is-current"
        count = (ui.tags.span(str(n_pending), class_="sc-stage-count sc-tree-count")
                 if key == "selection" and n_pending else None)
        rows.append(ui.tags.button(
            _num(i, status), ui.span(es.STAGE_SHORT[key], class_="sc-tree-label"), count,
            type="button", class_=cls,
            title=f"Step {i + 1}: {es.STAGE_LABELS[key]}. {info.get('detail') or ''}".strip(),
            **{"data-jump": es.stage_target(key)}))
    tools = _tool_rows(view, tools_allowed,
                       [k for k in rs.TOOL_KEYS if k in rs.TOOLS_WITHOUT_DATA])
    if tools:
        rows.append(ui.div("Tools", class_="sc-tree-sec"))
        rows += tools
    return ui.TagList(*rows)


def project_summary(meta: dict | None, path: str | None) -> dict | None:
    """The project row's name and one-line subtitle."""
    if not meta and not path:
        return None
    meta = meta or {}
    name = meta.get("project_name") or "Untitled project"
    sub = project_meta.origin_label(meta.get("origin"))
    if not path:
        sub = "Not saved yet"
    return {"name": name, "sub": sub or ""}


__all__ = ["STATE_CLASS", "TOOL_ICON", "stage_target", "tool_target", "children", "tree",
           "easi_tree", "project_summary"]
