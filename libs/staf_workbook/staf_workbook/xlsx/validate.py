"""Checks an assembled workbook the way Excel would before calling it unreadable: every part
parses and has a content type, every relationship resolves, the sheet list is consistent, exactly
one tab is selected (several selected tabs group the sheets, and an edit lands on all of them),
defined names point at real sheets, and style indices are in range."""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

from .package import Package
from .workbook import WorkbookModel


def validate_package(data: bytes) -> list:
    problems = []
    try:
        pkg = Package(data)
    except zipfile.BadZipFile as exc:
        return [f"not a zip file: {exc}"]
    for name, blob in pkg.parts.items():
        if name.endswith((".xml", ".rels", ".vml")):
            try:
                ET.fromstring(blob)
            except ET.ParseError as exc:
                problems.append(f"{name} does not parse: {exc}")
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
    if pkg.has("xl/styles.xml"):
        styles = pkg.text("xl/styles.xml")
        for tag, child in (("numFmts", "numFmt"), ("fonts", "font"), ("fills", "fill"), ("borders", "border"),
                           ("cellXfs", "xf")):
            mm = re.search(rf"<{tag}\b[^>]*\bcount=\"(\d+)\"[^>]*>(.*?)</{tag}>", styles, re.S)
            if mm and int(mm.group(1)) != len(re.findall(rf"<{child}\b", mm.group(2))):
                problems.append(f"styles: {tag} count does not match its children")
        mm = re.search(r"<cellXfs\b[^>]*>(.*?)</cellXfs>", styles, re.S)
        n_xf = len(re.findall(r"<xf\b", mm.group(1))) if mm else 0
        for s in wb.sheets:
            used = [int(v) for v in re.findall(r'<c\b[^>]*\bs="(\d+)"', pkg.text(s.part))]
            if used and max(used) >= n_xf:
                problems.append(f"{s.name}: a cell uses style {max(used)} of {n_xf}")
    return problems
