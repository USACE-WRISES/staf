"""The workbook part: sheets (order, names, visibility), defined names and the book view.

A defined name's scope is held as the sheet's name while editing; ``localSheetId``, ``activeTab``
and ``firstSheet`` are computed from the final order only when written, so reordering can never
leave a stale index. Everything else in ``workbook.xml`` is kept as it was.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from xml.sax.saxutils import escape, unescape

from .package import (CT_WORKSHEET, R_CALC_CHAIN, R_OFFICE_DOCUMENT, R_WORKSHEET, OFFDOC, Package, Rel,
                      attr, attrs_of)

_UNESC = {"&quot;": '"', "&apos;": "'"}


@dataclass
class Sheet:
    name: str
    sheet_id: int
    rid: str
    part: str
    state: str = "visible"
    selected: bool = False                 # informational; tab selection lives in each sheet part


@dataclass
class DefinedName:
    name: str
    text: str                              # the formula, unescaped
    scope: Optional[str] = None            # sheet name for a local name, None for the workbook
    extra: dict = field(default_factory=dict)   # other attributes, in order (hidden, comment, ...)


class WorkbookModel:
    def __init__(self, pkg: Package):
        self.pkg = pkg
        office = [r for r in pkg.rels("") if r.type == R_OFFICE_DOCUMENT]      # the root _rels/.rels
        self.part = Package.resolve("", office[0].target) if office else "xl/workbook.xml"
        xml = pkg.text(self.part)
        self.xml = xml
        root = re.search(r"<workbook\b[^>]*>", xml).group(0)
        self.root_declares_r = 'xmlns:r="' + OFFDOC + '"' in root
        rels = dict((r.id, r) for r in pkg.rels(self.part))
        self.sheets: list = []
        for tag in re.findall(r"<sheet\b[^>]*?/>", self._block("sheets") or ""):
            a = attrs_of(tag)
            rid = a.get("r:id")
            part = Package.resolve(self.part, rels[rid].target)
            self.sheets.append(Sheet(a["name"], int(a["sheetId"]), rid, part, a.get("state", "visible")))
        self.names: list = []
        for start, text in re.findall(r"(<definedName\b[^>]*?)(?:/>|>(.*?)</definedName>)", self._block("definedNames") or "",
                                      flags=re.S):
            a = attrs_of(start + ">")
            local = a.pop("localSheetId", None)
            name = a.pop("name")
            scope = self.sheets[int(local)].name if local is not None else None
            self.names.append(DefinedName(name, unescape(text or "", _UNESC), scope, a))

    # ------------------------------------------------------------------ lookups
    def _block(self, tag: str) -> Optional[str]:
        m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", self.xml, flags=re.S)
        if m:
            return m.group(1)
        return "" if re.search(rf"<{tag}\b[^>]*/>", self.xml) else None

    def sheet(self, name: str) -> Sheet:
        low = name.lower()
        for s in self.sheets:
            if s.name.lower() == low:
                return s
        raise KeyError(name)

    def has_sheet(self, name: str) -> bool:
        return any(s.name.lower() == name.lower() for s in self.sheets)

    def names_in(self, sheet: Optional[str]) -> list:
        return [n for n in self.names if (n.scope or "").lower() == (sheet or "").lower()]

    # ------------------------------------------------------------------ edits
    def rename(self, old: str, new: str) -> None:
        s = self.sheet(old)
        for n in self.names:
            if n.scope is not None and n.scope.lower() == s.name.lower():
                n.scope = new
        s.name = new

    def add_sheet(self, name: str, xml: str, *, state: str = "visible") -> Sheet:
        """A new worksheet part with its relationship and content type; placed last."""
        part = self.pkg.unique("xl/worksheets/sheet{}.xml")
        self.pkg.add(part, xml.encode("utf-8"), CT_WORKSHEET)
        rels = self.pkg.rels(self.part)
        rid = Package.next_rel_id(rels)
        rels.append(Rel(rid, R_WORKSHEET, Package.relative(self.part, part)))
        self.pkg.write_rels(self.part, rels)
        sheet = Sheet(name, max([s.sheet_id for s in self.sheets] + [0]) + 1, rid, part, state)
        self.sheets.append(sheet)
        return sheet

    def reorder(self, names: list) -> None:
        by = dict((s.name.lower(), s) for s in self.sheets)
        ordered = [by.pop(n.lower()) for n in names]
        self.sheets = ordered + list(by.values())

    def remove_calc_chain(self) -> None:
        rels = self.pkg.rels(self.part)
        keep = []
        for r in rels:
            if r.type == R_CALC_CHAIN:
                self.pkg.remove(Package.resolve(self.part, r.target))
            else:
                keep.append(r)
        if len(keep) != len(rels):
            self.pkg.write_rels(self.part, keep)

    def force_full_calc(self) -> None:
        """``fullCalcOnLoad`` on the calculation properties (Excel recalculates every formula)."""
        xml = self.xml
        m = re.search(r"<calcPr\b[^>]*?/>", xml)
        if m:
            tag = m.group(0)
            new = re.sub(r'\s(calcId|fullCalcOnLoad)="[^"]*"', "", tag)
            new = new.replace("<calcPr", '<calcPr calcId="0" fullCalcOnLoad="1"', 1)
            self.xml = xml.replace(tag, new, 1)
        else:
            self.xml = xml.replace("</workbook>", '<calcPr calcId="0" fullCalcOnLoad="1"/></workbook>')

    # ------------------------------------------------------------------ writing
    def serialize(self, *, active: Optional[str] = None) -> None:
        index = dict((s.name.lower(), i) for i, s in enumerate(self.sheets))
        ns = "" if self.root_declares_r else f' xmlns:r="{OFFDOC}"'
        sheets = "".join(
            f'<sheet{ns} name="{attr(s.name)}" sheetId="{s.sheet_id}"'
            + (f' state="{s.state}"' if s.state != "visible" else "") + f' r:id="{attr(s.rid)}"/>'
            for s in self.sheets)
        xml = re.sub(r"<sheets\b[^>]*>.*?</sheets>|<sheets\b[^>]*/>", lambda m: f"<sheets>{sheets}</sheets>",
                     self.xml, count=1, flags=re.S)
        names = []
        for n in self.names:
            local = ""
            if n.scope is not None:
                local = f' localSheetId="{index[n.scope.lower()]}"'
            extra = "".join(f' {k}="{attr(v)}"' for k, v in n.extra.items())
            names.append(f'<definedName name="{attr(n.name)}"{local}{extra}>{escape(n.text)}</definedName>')
        block = f"<definedNames>{''.join(names)}</definedNames>" if names else ""
        if re.search(r"<definedNames\b", xml):
            xml = re.sub(r"<definedNames\b[^>]*>.*?</definedNames>|<definedNames\b[^>]*/>", lambda m: block, xml,
                         count=1, flags=re.S)
        elif block:
            xml = xml.replace("</sheets>", "</sheets>" + block, 1)
        if active is not None:
            tab = index[active.lower()]
            m = re.search(r"<workbookView\b[^>]*?/?>", xml)
            if m:
                tag = m.group(0)
                new = re.sub(r'\s(activeTab|firstSheet)="[^"]*"', "", tag)
                new = new.replace("<workbookView", f'<workbookView firstSheet="0" activeTab="{tab}"', 1)
                xml = xml.replace(tag, new, 1)
        self.xml = xml
        self.pkg.set_text(self.part, xml)
