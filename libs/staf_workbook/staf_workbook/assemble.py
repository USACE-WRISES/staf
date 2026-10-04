"""One workbook for an assessment and its scenarios, built from an app's calculator template.

The template file is never changed. The result opens on the Summary tab; Existing Conditions (the
template's score sheet, renamed) comes next, then one tab per alternative (each a working copy of
the calculator), then ReferenceCurves, then the template's other sheets as they were. Each
alternative's helper sheets are hidden. Excel recalculates on open, as the template already asks.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from .xlsx.clone import ScenarioCloner, clear_tab_selected, set_tab_selected
from .xlsx.package import Package
from .xlsx.validate import validate_package
from .xlsx.workbook import WorkbookModel

EC_NAME = "Existing Conditions"


class AssemblyError(ValueError):
    """The assembled workbook failed its own checks (never served)."""


@dataclass
class AssemblySpec:
    app_title: str                         # "EASI", "SFARI", "DEEP"
    score_sheet: str                       # the template's score sheet
    blocks: dict = field(default_factory=dict)
    hide_helpers: bool = True
    force_recalc: bool = False


@dataclass
class ScenarioInput:
    id: str
    name: str
    description: str = ""
    fill: Callable[[str], str] = lambda xml: xml


@dataclass
class Built:
    pkg: Package
    wb: WorkbookModel
    ec_sheet: str
    alt_sheets: list                       # score-sheet copies, in scenario order
    helpers: dict                          # alt sheet -> {template sheet: its copy}
    score_template: str


def build(template: bytes, spec: AssemblySpec, scenarios: list) -> Built:
    """Fill the baseline in place, rename it, and add one calculator copy per alternative."""
    if not scenarios:
        raise ValueError("at least the Existing Conditions scenario is needed")
    pkg = Package(template)
    wb = WorkbookModel(pkg)
    cloner = ScenarioCloner(pkg, wb, spec.score_sheet, blocks=spec.blocks)
    cloner.capture()
    score_template = cloner.score
    part = wb.sheet(score_template).part
    pkg.set_text(part, scenarios[0].fill(pkg.text(part)))
    ec_name = scenarios[0].name or EC_NAME
    if ec_name != score_template:
        cloner.rename_score(ec_name)
    alt_sheets, helpers = [], {}
    others = [s for s in cloner.analysis.per_scenario if s != cloner.score]
    for k, alt in enumerate(scenarios[1:], start=2):
        created = cloner.add_alternative(k, alt.name, alt.fill, seed=alt.id, hide_helpers=spec.hide_helpers)
        alt_sheets.append(created[0])
        helpers[created[0]] = dict(zip(others, created[1:1 + len(others)]))
    return Built(pkg, wb, ec_name, alt_sheets, helpers, score_template)


def sheet_for(built: Built, index: int, template_sheet: str) -> str:
    """The sheet that holds scenario ``index``'s copy of ``template_sheet`` (0 = the baseline)."""
    is_score = template_sheet.lower() == built.score_template.lower()
    if index == 0:
        return built.ec_sheet if is_score else template_sheet
    alt = built.alt_sheets[index - 1]
    if is_score:
        return alt
    for orig, copy in built.helpers[alt].items():
        if orig.lower() == template_sheet.lower():
            return copy
    return template_sheet                                  # a shared sheet


def assemble(template: bytes, spec: AssemblySpec, scenarios: list, *, summary, cells, scores: list,
             curves=None, generated_on=None) -> bytes:
    """The whole workbook: Summary, Existing Conditions, the alternatives, ReferenceCurves, then
    the template's other sheets."""
    from .sheets import add_curves_sheet, add_summary_sheet
    from .xlsx.styles import StyleBook
    built = build(template, spec, scenarios)
    styles = StyleBook(built.pkg)
    first = [add_summary_sheet(built, spec, styles, summary, cells, scores, scenarios, generated_on)]
    after = [add_curves_sheet(built, styles, curves)] if curves is not None and curves.blocks else []
    styles.save()
    return finish(built, spec, first=first, after=after)


def finish(built: Built, spec: AssemblySpec, *, first: list, after: list) -> bytes:
    """Order the tabs (``first`` before the scenarios, ``after`` right after them), select the
    first, drop the calculation chain, and check the result."""
    pkg, wb = built.pkg, built.wb
    head = list(first) + [built.ec_sheet] + built.alt_sheets + list(after)
    head_low = set(h.lower() for h in head)
    rest = [s for s in wb.sheets if s.name.lower() not in head_low]
    wb.reorder(head + [s.name for s in rest if s.state == "visible"] + [s.name for s in rest if s.state != "visible"])
    active = head[0]
    for s in wb.sheets:
        xml = pkg.text(s.part)
        new = set_tab_selected(xml) if s.name == active else clear_tab_selected(xml)
        if new != xml:
            pkg.set_text(s.part, new)
    wb.remove_calc_chain()
    _drop_app_titles(pkg)
    if spec.force_recalc:
        wb.force_full_calc()
    wb.serialize(active=active)
    data = pkg.write()
    problems = validate_package(data)
    if problems:
        raise AssemblyError("; ".join(problems[:8]))
    return data


def _drop_app_titles(pkg: Package) -> None:
    """``docProps/app.xml`` lists the sheet names; Excel rebuilds the list, so drop a stale one."""
    if not pkg.has("docProps/app.xml"):
        return
    xml = pkg.text("docProps/app.xml")
    new = re.sub(r"<(HeadingPairs|TitlesOfParts)\b.*?</\1>", "", xml, flags=re.S)
    if new != xml:
        pkg.set_text("docProps/app.xml", new)


def template_key(template: bytes) -> str:
    return hashlib.sha256(template).hexdigest()
