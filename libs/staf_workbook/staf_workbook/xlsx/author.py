"""A small worksheet writer for the authored sheets (Summary, ReferenceCurves): inline strings,
numbers, formulas with stored results, merges, column widths, a frozen header, and an optional
drawing. Elements are written in the order the schema requires."""
from __future__ import annotations

import math
import re
from typing import Optional
from xml.sax.saxutils import escape

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def col_number(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n


def split_ref(ref: str) -> tuple:
    m = re.match(r"([A-Za-z]+)(\d+)$", ref)
    return col_number(m.group(1)), int(m.group(2))


def _num(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    f = float(v)
    if math.isfinite(f) and f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return repr(f)


class SheetWriter:
    def __init__(self):
        self.rows: dict = {}                 # row -> {col: cell xml}
        self.heights: dict = {}
        self.widths: dict = {}
        self.merges: list = []
        self.freeze: Optional[str] = None
        self.drawing_rid: Optional[str] = None

    def _put(self, ref: str, xml_body: str, style: Optional[int], cell_type: Optional[str] = None) -> None:
        col, row = split_ref(ref)
        attrs = f' r="{ref}"' + (f' s="{style}"' if style else "") + (f' t="{cell_type}"' if cell_type else "")
        self.rows.setdefault(row, {})[col] = f"<c{attrs}>{xml_body}</c>" if xml_body else f"<c{attrs}/>"

    def text(self, ref: str, value: str, style: Optional[int] = None) -> None:
        if value is None or value == "":
            self._put(ref, "", style)
            return
        self._put(ref, f'<is><t xml:space="preserve">{escape(str(value))}</t></is>', style, "inlineStr")

    def number(self, ref: str, value, style: Optional[int] = None) -> None:
        if value is None:
            self._put(ref, "", style)
            return
        self._put(ref, f"<v>{_num(value)}</v>", style)

    def formula(self, ref: str, text: str, cached=None, style: Optional[int] = None) -> None:
        """A live formula with the app's value stored as its result (shown before recalculation)."""
        f = escape(text[1:] if text.startswith("=") else text)
        if cached is None or cached == "":
            self._put(ref, f"<f>{f}</f><v></v>", style, "str")
        elif isinstance(cached, str):
            self._put(ref, f"<f>{f}</f><v>{escape(cached)}</v>", style, "str")
        else:
            self._put(ref, f"<f>{f}</f><v>{_num(cached)}</v>", style)

    def blank(self, ref: str, style: Optional[int]) -> None:
        self._put(ref, "", style)

    def merge(self, rng: str) -> None:
        self.merges.append(rng)

    def width(self, col: int, w: float) -> None:
        self.widths[col] = w

    def height(self, row: int, h: float) -> None:
        self.heights[row] = h

    def xml(self, *, show_grid: bool = False) -> str:
        cells = [(r, c) for r, cols in self.rows.items() for c in cols]
        if cells:
            r1, r2 = min(r for r, _ in cells), max(r for r, _ in cells)
            c1, c2 = min(c for _, c in cells), max(c for _, c in cells)
            dim = f'<dimension ref="{col_letter(c1)}{r1}:{col_letter(c2)}{r2}"/>'
        else:
            dim = '<dimension ref="A1"/>'
        view = '<sheetView workbookViewId="0"' + ("" if show_grid else ' showGridLines="0"') + ">"
        if self.freeze:
            col, row = split_ref(self.freeze)
            view += (f'<pane ySplit="{row - 1}" topLeftCell="{self.freeze}" activePane="bottomLeft" state="frozen"/>'
                     f'<selection pane="bottomLeft" activeCell="{self.freeze}" sqref="{self.freeze}"/>')
        view += "</sheetView>"
        cols = ""
        if self.widths:
            cols = "<cols>" + "".join(f'<col min="{c}" max="{c}" width="{w:g}" customWidth="1"/>'
                                      for c, w in sorted(self.widths.items())) + "</cols>"
        rows = []
        for r in sorted(self.rows):
            ht = f' ht="{self.heights[r]:g}" customHeight="1"' if r in self.heights else ""
            rows.append(f'<row r="{r}"{ht}>' + "".join(self.rows[r][c] for c in sorted(self.rows[r])) + "</row>")
        for r in sorted(set(self.heights) - set(self.rows)):
            rows.append(f'<row r="{r}" ht="{self.heights[r]:g}" customHeight="1"/>')
        rows.sort(key=lambda x: int(re.search(r'r="(\d+)"', x).group(1)))
        merges = ""
        if self.merges:
            merges = f'<mergeCells count="{len(self.merges)}">' + "".join(
                f'<mergeCell ref="{m}"/>' for m in self.merges) + "</mergeCells>"
        drawing = f'<drawing r:id="{self.drawing_rid}"/>' if self.drawing_rid else ""
        margins = '<pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>'
        setup = '<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
        return (f'<worksheet xmlns="{NS_MAIN}" xmlns:r="{NS_R}">'
                '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
                f"{dim}<sheetViews>{view}</sheetViews>"
                '<sheetFormatPr defaultRowHeight="15"/>'
                f"{cols}<sheetData>{''.join(rows)}</sheetData>{merges}{margins}{setup}{drawing}</worksheet>")
