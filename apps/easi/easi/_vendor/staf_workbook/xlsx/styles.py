"""New cell styles appended to a workbook's ``styles.xml``.

Existing entries never move (every template cell keeps its style index); new fonts, fills,
borders, number formats and cell formats go at the end and the ``count`` attributes follow. The
workbook's own default font is reused so authored sheets sit naturally beside the template's.
"""
from __future__ import annotations

import re
from typing import Optional
from xml.sax.saxutils import escape

from .package import Package, attr

STYLES = "xl/styles.xml"
_BUILTIN_FORMATS = {"General": 0, "0": 1, "0.00": 2, "#,##0": 3, "#,##0.00": 4, "0%": 9, "0.00%": 10}


class StyleBook:
    def __init__(self, pkg: Package):
        self.pkg = pkg
        self.xml = pkg.text(STYLES)
        self._memo: dict = {}
        font0 = re.search(r"<fonts\b[^>]*>\s*<font\b[^>]*>(.*?)</font>", self.xml, re.S)
        name = re.search(r'<name val="([^"]+)"', font0.group(1)) if font0 else None
        size = re.search(r'<sz val="([^"]+)"', font0.group(1)) if font0 else None
        self.font_name = name.group(1) if name else "Calibri"
        self.font_size = float(size.group(1)) if size else 11.0

    # ------------------------------------------------------------------ collections
    def _append(self, tag: str, child_xml: str) -> int:
        m = re.search(rf"<{tag}\b([^>]*)>(.*?)</{tag}>", self.xml, re.S)
        if m is None:
            empty = re.search(rf"<{tag}\b([^>]*)/>", self.xml)
            if empty is None:
                raise ValueError(f"styles.xml has no {tag}")
            index = 0
            new = f"<{tag} count=\"1\">{child_xml}</{tag}>"
            self.xml = self.xml.replace(empty.group(0), new, 1)
            return index
        child = {"fonts": "font", "fills": "fill", "borders": "border", "cellXfs": "xf"}[tag]
        index = len(re.findall(rf"<{child}\b", m.group(2)))
        attrs = re.sub(r'\scount="\d+"', "", m.group(1))
        new = f"<{tag}{attrs} count=\"{index + 1}\">{m.group(2)}{child_xml}</{tag}>"
        self.xml = self.xml[:m.start()] + new + self.xml[m.end():]
        return index

    def numfmt(self, code: str) -> int:
        if code in _BUILTIN_FORMATS:
            return _BUILTIN_FORMATS[code]
        key = ("numfmt", code)
        if key in self._memo:
            return self._memo[key]
        m = re.search(r"<numFmts\b[^>]*>(.*?)</numFmts>", self.xml, re.S)
        existing = [int(v) for v in re.findall(r'numFmtId="(\d+)"', m.group(1))] if m else []
        for fid, fcode in (re.findall(r'<numFmt\b[^>]*numFmtId="(\d+)"[^>]*formatCode="([^"]*)"', m.group(1)) if m else []):
            if fcode == escape(code, {'"': "&quot;"}):
                self._memo[key] = int(fid)
                return int(fid)
        fid = max(existing + [163]) + 1
        tag = f'<numFmt numFmtId="{fid}" formatCode="{attr(code)}"/>'
        if m:
            inner = m.group(1) + tag
            self.xml = self.xml[:m.start()] + f'<numFmts count="{len(existing) + 1}">{inner}</numFmts>' + self.xml[m.end():]
        else:
            self.xml = re.sub(r"(<styleSheet\b[^>]*>)", lambda mm: mm.group(1) + f'<numFmts count="1">{tag}</numFmts>',
                              self.xml, count=1)
        self._memo[key] = fid
        return fid

    def font(self, *, bold=False, italic=False, size: Optional[float] = None, color: Optional[str] = None) -> int:
        key = ("font", bold, italic, size, color)
        if key not in self._memo:
            parts = ("<b/>" if bold else "") + ("<i/>" if italic else "")
            parts += f'<sz val="{size or self.font_size:g}"/>'
            parts += f'<color rgb="FF{color}"/>' if color else '<color theme="1"/>'
            parts += f'<name val="{attr(self.font_name)}"/><family val="2"/>'
            self._memo[key] = self._append("fonts", f"<font>{parts}</font>")
        return self._memo[key]

    def fill(self, rgb: Optional[str]) -> int:
        if rgb is None:
            return 0
        key = ("fill", rgb)
        if key not in self._memo:
            self._memo[key] = self._append(
                "fills", f'<fill><patternFill patternType="solid"><fgColor rgb="FF{rgb}"/><bgColor indexed="64"/>'
                         "</patternFill></fill>")
        return self._memo[key]

    def border(self, *, bottom: Optional[str] = None, top: Optional[str] = None) -> int:
        if bottom is None and top is None:
            return 0
        key = ("border", bottom, top)
        if key not in self._memo:
            def side(tag, rgb):
                return f'<{tag} style="thin"><color rgb="FF{rgb}"/></{tag}>' if rgb else f"<{tag}/>"
            self._memo[key] = self._append(
                "borders", "<border><left/><right/>" + side("top", top) + side("bottom", bottom) + "<diagonal/></border>")
        return self._memo[key]

    def xf(self, *, font: int = 0, fill: int = 0, border: int = 0, numfmt: int = 0, halign: Optional[str] = None,
           valign: str = "center", wrap: bool = False, indent: int = 0) -> int:
        key = ("xf", font, fill, border, numfmt, halign, valign, wrap, indent)
        if key not in self._memo:
            align = f' vertical="{valign}"' + (f' horizontal="{halign}"' if halign else "")
            align += ' wrapText="1"' if wrap else ""
            align += f' indent="{indent}"' if indent else ""
            self._memo[key] = self._append(
                "cellXfs", f'<xf numFmtId="{numfmt}" fontId="{font}" fillId="{fill}" borderId="{border}" xfId="0" '
                           'applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">'
                           f"<alignment{align}/></xf>")
        return self._memo[key]

    def save(self) -> None:
        self.pkg.set_text(STYLES, self.xml)
