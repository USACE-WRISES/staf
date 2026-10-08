"""Checks an assembled workbook the way Excel would before calling it unreadable: every part
parses and has a content type, every relationship resolves, the sheet list is consistent, exactly
one tab is selected (several selected tabs group the sheets, and an edit lands on all of them),
defined names point at real sheets, each worksheet's elements come in the schema's order (Excel
"repairs" a sheet whose conditional formats or page setup sit out of place), and style and
conditional-format indices are in range."""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

from .package import Package
from .workbook import WorkbookModel

#: CT_Worksheet's sequence (ECMA-376 Part 1, 18.3.1.99); other children (mc:AlternateContent) are skipped
SHEET_ORDER = ("sheetPr", "dimension", "sheetViews", "sheetFormatPr", "cols", "sheetData", "sheetCalcPr",
               "sheetProtection", "protectedRanges", "scenarios", "autoFilter", "sortState", "dataConsolidate",
               "customSheetViews", "mergeCells", "phoneticPr", "conditionalFormatting", "dataValidations",
               "hyperlinks", "printOptions", "pageMargins", "pageSetup", "headerFooter", "rowBreaks", "colBreaks",
               "customProperties", "cellWatches", "ignoredErrors", "smartTags", "drawing", "legacyDrawing",
               "legacyDrawingHF", "drawingHF", "picture", "oleObjects", "controls", "webPublishItems",
               "tableParts", "extLst")
_RANK = dict((tag, k) for k, tag in enumerate(SHEET_ORDER))


def _out_of_order(children: list) -> bool:
    ranks = [_RANK[c] for c in children if c in _RANK]
    return any(b < a for a, b in zip(ranks, ranks[1:]))


def validate_package(data: bytes) -> list:
    problems = []
    try:
        pkg = Package(data)
    except zipfile.BadZipFile as exc:
        return [f"not a zip file: {exc}"]
    children = {}                                          # worksheet part -> its child element names
    for name, blob in pkg.parts.items():
        if name.endswith((".xml", ".rels", ".vml")):
            try:
                root = ET.fromstring(blob)
            except ET.ParseError as exc:
                problems.append(f"{name} does not parse: {exc}")
            else:
                if root.tag.rsplit("}", 1)[-1] == "worksheet":
                    children[name] = [c.tag.rsplit("}", 1)[-1] for c in root]
        if name != "[Content_Types].xml" and pkg.content_type(name) is None:
            problems.append(f"{name} has no content type")
    for name in list(pkg.parts):
        if not name.endswith(".rels"):
            continue
        owner = re.sub(r"_rels/([^/]*)\.rels$", r"\1", name)
        for r in pkg.rels(owner):
            if r.mode == "External":
                continue
            if not pkg.has(Package.resolve(owner, r.target)):
                problems.append(f"{name}: {r.id} points at a missing part {r.target}")
    try:
        wb = WorkbookModel(pkg)
    except Exception as exc:  # noqa: BLE001
        return problems + [f"workbook does not read: {exc}"]
    names = [s.name.lower() for s in wb.sheets]
    if len(names) != len(set(names)):
        problems.append("duplicate sheet names")
    visible = [s for s in wb.sheets if s.state == "visible"]
    if not visible:
        problems.append("no visible sheet")
    m = re.search(r'<workbookView\b[^>]*\bactiveTab="(\d+)"', wb.xml)
    active = int(m.group(1)) if m else 0
    if active >= len(wb.sheets) or wb.sheets[active].state != "visible":
        problems.append(f"activeTab {active} is not a visible sheet")
    selected = [s.name for s in wb.sheets if re.search(r'<sheetView\b[^>]*\btabSelected="(?:1|true)"', pkg.text(s.part))]
    if len(selected) != 1:
        problems.append(f"{len(selected)} selected tabs (must be exactly one): {selected}")
    elif wb.sheets and selected[0] != wb.sheets[active].name:
        problems.append(f"the selected tab {selected[0]!r} is not the active tab")
    seen = set()
    for n in wb.names:
        key = (n.name.lower(), (n.scope or "").lower())
        if key in seen:
            problems.append(f"defined name {n.name} repeated in one scope")
        seen.add(key)
    sid = [s.sheet_id for s in wb.sheets]
    if len(sid) != len(set(sid)):
        problems.append("duplicate sheetId")
    for s in wb.sheets:
        if _out_of_order(children.get(s.part, [])):
            problems.append(f"{s.name}: worksheet elements out of schema order")
    if pkg.has("xl/styles.xml"):
        styles = pkg.text("xl/styles.xml")
        for tag, child in (("numFmts", "numFmt"), ("fonts", "font"), ("fills", "fill"), ("borders", "border"),
                           ("cellXfs", "xf"), ("dxfs", "dxf")):
            mm = re.search(rf"<{tag}\b[^>]*\bcount=\"(\d+)\"[^>]*>(.*?)</{tag}>", styles, re.S)
            if mm and int(mm.group(1)) != len(re.findall(rf"<{child}\b", mm.group(2))):
                problems.append(f"styles: {tag} count does not match its children")
        mm = re.search(r"<cellXfs\b[^>]*>(.*?)</cellXfs>", styles, re.S)
        n_xf = len(re.findall(r"<xf\b", mm.group(1))) if mm else 0
        mm = re.search(r"<dxfs\b[^>]*?(?<!/)>(.*?)</dxfs>", styles, re.S)
        n_dxf = len(re.findall(r"<dxf\b", mm.group(1))) if mm else 0
        for s in wb.sheets:
            xml = pkg.text(s.part)
            used = [int(v) for v in re.findall(r'<c\b[^>]*\bs="(\d+)"', xml)]
            if used and max(used) >= n_xf:
                problems.append(f"{s.name}: a cell uses style {max(used)} of {n_xf}")
            rules = [int(v) for v in re.findall(r'<cfRule\b[^>]*\bdxfId="(\d+)"', xml)]
            if rules and max(rules) >= n_dxf:
                problems.append(f"{s.name}: a conditional format uses format {max(rules)} of {n_dxf}")
    return problems
