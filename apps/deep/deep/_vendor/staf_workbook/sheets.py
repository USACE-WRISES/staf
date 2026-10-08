"""The two authored tabs: Summary (the summary block, then the scores of every scenario side by
side, each alternative followed by its change from Existing Conditions, as the Compare dialog shows
them) and ReferenceCurves (each metric's curve as a chart beside its points, then any tables).
Scores are live formulas pointing at each scenario's calculator, with the app's values stored as
their results so they read correctly before Excel recalculates."""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Optional

from .model.compare import ScenarioScores, excel_round
from .model.compare import build as compare_build
from .model.refcurves import CurveBlock, RefCurves, TableBlock
from .model.summary import SummaryInfo
from .xlsx.author import SheetWriter, col_letter
from .xlsx.charts import ScatterChart, Series, add_charts
from .xlsx.formula import quote_sheet
from .xlsx.styles import StyleBook
from .xlsx.workbook import DefinedName

SUMMARY = "Summary"
CURVES = "ReferenceCurves"
#: The Compare dialog's palette (assets/staf.css): text, labels, muted text, rules, the Existing
#: Conditions tint, and a change up or down
INK, LABEL, MUTED = "2B3445", "5A6578", "7A8699"
RULE, RULE_STRONG, TINT = "E5E8EE", "D5DEEA", "F6F8FB"
UP, DOWN = "2F7A4B", "A33A3A"
#: Summary column widths (characters) and row heights (points; Excel never fits a merged or a
#: custom-height row, so every row's height is set from its text)
VALUE_W, CHANGE_W, LABEL_W_MIN, LABEL_W_MAX = 13, 10, 24, 44
TITLE_H, LINE_H, ROW_H, GROUP_H, HEAD_H, HEADING_H, GAP_H = 28, 15, 18, 22, 21, 28, 10
#: printable area inside the margins (points), the smaller of Letter and A4
PRINTABLE = dict(portrait=(523.0, 705.0), landscape=(720.0, 509.0))
ONE_PAGE_SCALE = 0.75


@dataclass
class SummaryCells:
    """Where a scenario's results sit in the template (template sheet names and cells)."""
    eci: tuple
    sub: dict                                      # physical/chemical/biological -> (sheet, cell)
    functions: list                                # [(fid, label, category, (sheet, cell)), ...]
    index_digits: int = 2
    function_digits: int = 0
    statements: list = field(default_factory=list)  # [(heading, [text per scenario]), ...]: blocks under the table
    note: str = ""


class _Styles:
    """The authored tabs' common look: 11 pt text in the Compare dialog's palette, light headers."""

    def __init__(self, sb: StyleBook):
        f = sb.font
        self.title = sb.xf(font=f(bold=True, size=16, color=INK))
        self.sub = sb.xf(font=f(size=11, color=MUTED))
        self.section = sb.xf(font=f(bold=True, size=12, color=INK), valign="bottom")
        self.head_rule = sb.border(bottom=RULE_STRONG, bottom_style="medium")
        self.head = sb.xf(font=f(bold=True, size=11, color=INK), border=self.head_rule, halign="center",
                          valign="bottom", wrap=True)
        self.head_left = sb.xf(font=f(bold=True, size=11, color=INK), border=self.head_rule, halign="left",
                               valign="bottom", wrap=True)
        self.note = sb.xf(font=f(size=11, color=MUTED))
        self.rule = sb.border(bottom=RULE)
        self.cell = sb.xf(font=f(size=11, color=INK), border=self.rule, halign="center")
        self.cell_left = sb.xf(font=f(size=11, color=INK), border=self.rule, wrap=True)


class _SummaryStyles(_Styles):
    """The Summary tab's own styles: key/value blocks, group rows, values and changes."""

    def __init__(self, sb: StyleBook):
        super().__init__(sb)
        self.sb = sb
        f = sb.font
        self.key = sb.xf(font=f(size=11, color=LABEL), valign="top", wrap=True)
        self.val = sb.xf(font=f(size=11, color=INK), valign="top", wrap=True)
        self.note_block = sb.xf(font=f(size=11, color=MUTED), valign="top", wrap=True)
        self.head_change = sb.xf(font=f(size=11, color=MUTED), border=self.head_rule, halign="center",
                                 valign="bottom", wrap=True)
        group_rule = sb.border(bottom=RULE_STRONG)
        self.group = sb.xf(font=f(bold=True, size=11, color=LABEL), border=group_rule, valign="bottom")
        self.group_cell = sb.xf(border=group_rule, valign="bottom")
        self.label = sb.xf(font=f(size=11, color=INK), border=self.rule, wrap=True)
        self.label_bold = sb.xf(font=f(bold=True, size=11, color=INK), border=self.rule, wrap=True)
        self.up = sb.dxf(bold=True, color=UP)
        self.down = sb.dxf(bold=True, color=DOWN)

    def value(self, digits: int, bold: bool, base: bool) -> int:
        sb = self.sb
        return sb.xf(font=sb.font(bold=bold, size=11, color=INK), numfmt=sb.numfmt(_fmt(digits)),
                     fill=sb.fill(TINT if base else None), border=self.rule, halign="center")

    def change(self, digits: int, bold: bool) -> int:
        """A change from Existing Conditions: signed and muted; the conditional formats colour it."""
        sb = self.sb
        return sb.xf(font=sb.font(bold=bold, size=11, color=MUTED), numfmt=sb.numfmt(_signed(digits)),
                     border=self.rule, halign="center")


def _fmt(digits: int) -> str:
    return "0" if digits == 0 else "0." + "0" * digits


def _signed(digits: int) -> str:
    return "+{0};-{0};{0}".format(_fmt(digits))


def _ref(sheet: str, cell: str) -> str:
    col = "".join(ch for ch in cell if ch.isalpha())
    row = "".join(ch for ch in cell if ch.isdigit())
    return f"{quote_sheet(sheet)}!${col}${row}"


def _cached(measure):
    if measure is None or measure.empty or measure.value is None:
        return ""                                          # empty, or an interval (never a stored number)
    return float(measure.value)


# ---- text metrics (Calibri 11 at 100%; estimates err wide so wrapped text is never cut off)
_NARROW = set("fijlrtI.,:;'!|() ")
_WIDE = set("mwMW@%")


def _text_px(text: str, bold: bool = False) -> float:
    px = sum(3.5 if ch in _NARROW else 10.5 if ch in _WIDE else 7.5 if ch.isupper() else 6.6 for ch in str(text))
    return px * (1.06 if bold else 1.0)


def _col_px(width: float) -> float:
    return 7.0 * width


def _lines(text, px: float, bold: bool = False) -> int:
    """How many lines ``text`` takes wrapped in a cell ``px`` pixels wide (a greedy word wrap)."""
    room = max(px - 7.0, 21.0)
    space = _text_px(" ", bold)
    total = 0
    for para in str(text or "").split("\n"):
        lines, used = 1, 0.0
        for word in para.split():
            wpx = _text_px(word, bold)
            if used and used + space + wpx > room:
                lines += 1
                used = 0.0
            if not used and wpx > room:                     # a word wider than the cell breaks by itself
                full = math.ceil(wpx / room)
                lines += full - 1
                used = wpx - (full - 1) * room
            else:
                used += (space if used else 0.0) + wpx
        total += lines
    return max(total, 1)


def _height(lines: int, base: float) -> float:
    return base + LINE_H * (max(lines, 1) - 1)


def page_fit(width_pt: float, height_pt: float) -> tuple:
    """``(orientation, pages tall)``: one page when it prints at 75% or more (portrait unless
    landscape fits better); otherwise as wide as the page and as many pages tall as it needs,
    portrait while the width still prints at 75% or more (the table's header repeats on each page)."""
    def scale(orientation):
        pw, ph = PRINTABLE[orientation]
        return min(1.0, pw / width_pt, ph / height_pt)
    best = max(("portrait", "landscape"), key=scale)       # a tie goes to portrait
    if scale(best) >= ONE_PAGE_SCALE:
        return best, 1
    return ("portrait" if PRINTABLE["portrait"][0] / width_pt >= ONE_PAGE_SCALE else "landscape"), 0


def _extent(w: SheetWriter, last_col: int, last_row: int) -> tuple:
    """The sheet's printed size in points (columns 1..last_col, rows 1..last_row)."""
    width = sum(_col_px(w.widths.get(c, 8.43)) for c in range(1, last_col + 1)) * 0.75
    height = sum(w.heights.get(r, 15.0) for r in range(1, last_row + 1))
    return width, height


def add_summary_sheet(built, spec, sb: StyleBook, info: SummaryInfo, cells: SummaryCells, scores: list,
                      scenarios: list, generated_on: Optional[_dt.date] = None) -> str:
    """Write the Summary tab into ``built``; returns its name.

    Rows: the title, the summary block, the scenarios' descriptions (when any has one), then the
    scores as the Compare dialog shows them on its side: a row per measure (the index, then each
    function under its category), a column per scenario, each alternative followed by its change
    from Existing Conditions; then ``cells.statements`` and ``cells.note``."""
    from .assemble import sheet_for
    st = _SummaryStyles(sb)
    w = SheetWriter()
    n = len(scenarios)
    last = 2 * n                                           # A, Existing Conditions, then value + change per alternative
    span = max(last, 4)                                    # text blocks run at least to column D
    value_col = [2] + [2 * i + 1 for i in range(1, n)]
    sc_list = [scores[i] if i < len(scores) else ScenarioScores() for i in range(n)]
    cmp = compare_build([s.name for s in scenarios], sc_list,
                        [(fid, label, cat) for fid, label, cat, _cell in cells.functions],
                        index_digits=cells.index_digits, function_digits=cells.function_digits)
    where = dict([("eci", cells.eci)] + list(cells.sub.items())
                 + [("fn:" + fid, cell) for fid, _label, _cat, cell in cells.functions])
    # ---- columns: A fits its longest label; values and changes alternate after B
    site = info.rows()
    groups = []
    for r in cmp.rows:
        if not groups or groups[-1] != (r.group or "Functions"):
            groups.append(r.group or "Functions")
    label_px = max([_text_px(k) for k, _v in site] + [_text_px(g, True) for g in groups]
                   + [_text_px(r.label, r.key == "eci") for r in cmp.rows])
    widths = {1: min(LABEL_W_MAX, max(LABEL_W_MIN, math.ceil((label_px + 12) / 7)))}
    widths[2] = VALUE_W
    for c in range(3, span + 1):
        widths[c] = VALUE_W if c % 2 else CHANGE_W
    for c, wd in widths.items():
        w.width(c, wd)
    a_px = _col_px(widths[1])
    rest_px = sum(_col_px(widths[c]) for c in range(2, span + 1))

    def pair(row, key, value, key_style=st.key):
        w.text(f"A{row}", key, key_style)
        w.text(f"B{row}", value, st.val)
        for c in range(3, span + 1):
            w.blank(f"{col_letter(c)}{row}", st.val)
        w.merge(f"B{row}:{col_letter(span)}{row}")
        w.height(row, _height(max(_lines(key, a_px), _lines(value, rest_px)), ROW_H))
        return row + 1

    def heading(row, text):
        w.text(f"A{row}", text, st.section)
        w.height(row, HEADING_H)
        return row + 1

    # ---- title and the summary block
    date = (generated_on or _dt.date.today()).isoformat()
    w.text("A1", f"{spec.app_title} assessment summary", st.title)
    w.height(1, TITLE_H)
    w.text("A2", f"Generated {date} by the {spec.app_title} web application.", st.sub)
    w.height(2, ROW_H)
    w.height(3, GAP_H)
    row = 4
    for key, value in site:
        row = pair(row, key, value)
    described = [s for s in scenarios if (s.description or "").strip()]
    if described:
        row = heading(row, "Scenarios")
        for s in described:
            row = pair(row, s.name, s.description.strip())
    # ---- the scores
    row = heading(row, "Scores")
    head = row
    w.text(f"A{head}", "", st.head_left)
    name_lines = 1
    for i, s in enumerate(scenarios):
        c = value_col[i]
        w.text(f"{col_letter(c)}{head}", s.name, st.head)
        name_lines = max(name_lines, _lines(s.name, _col_px(widths[c]), bold=True))
        if i:
            w.text(f"{col_letter(c + 1)}{head}", "Change", st.head_change)
    w.height(head, _height(name_lines, HEAD_H))
    row = head + 1
    first, group = row, None
    for r in cmp.rows:
        if (r.group or "Functions") != group:
            group = r.group or "Functions"
            w.text(f"A{row}", group, st.group)
            for c in range(2, last + 1):
                w.blank(f"{col_letter(c)}{row}", st.group_cell)
            w.height(row, GROUP_H)
            row += 1
        bold = r.key == "eci"
        w.text(f"A{row}", r.label, st.label_bold if bold else st.label)
        sheet, cell = where[r.key]
        for i in range(n):
            c = value_col[i]
            ref = _ref(sheet_for(built, i, sheet), cell)
            cached = _cached(r.values[i])
            if cached != "" and not r.key.startswith("fn:"):
                cached = excel_round(cached, 10)
            w.formula(f"{col_letter(c)}{row}", f"IF(ISNUMBER({ref}),{ref},\"\")", cached,
                      st.value(r.digits, bold, base=i == 0))
            if i:
                a, b, d = f"{col_letter(c)}{row}", f"$B{row}", r.digits
                delta = r.deltas[i - 1]
                stored = delta[0] if delta is not None and delta[0] == delta[1] else ""
                w.formula(f"{col_letter(c + 1)}{row}",
                          f"IF(AND(ISNUMBER({a}),ISNUMBER({b})),ROUND({a},{d})-ROUND({b},{d}),\"\")",
                          stored, st.change(d, bold))
        w.height(row, _height(_lines(r.label, a_px, bold), ROW_H))
        row += 1
    end = row - 1
    for i in range(1, n):
        col = col_letter(value_col[i] + 1)
        rng, top = f"{col}{first}:{col}{end}", f"{col}{first}"
        w.conditional(rng, f"AND(ISNUMBER({top}),{top}>0)", st.up)
        w.conditional(rng, f"AND(ISNUMBER({top}),{top}<0)", st.down)
    # ---- statements (DEEP's condition claim) and the note
    for title, texts in cells.statements:
        shown = [(scenarios[i].name, str(texts[i] or "").strip()) for i in range(min(n, len(texts)))]
        shown = [(name, text) for name, text in shown if text]
        if not shown:
            continue
        row = heading(row, title)
        for name, text in shown:
            row = pair(row, name, text)
    if cells.note:
        w.height(row, GAP_H)
        row += 1
        w.text(f"A{row}", cells.note, st.note_block)
        for c in range(2, span + 1):
            w.blank(f"{col_letter(c)}{row}", st.note_block)
        w.merge(f"A{row}:{col_letter(span)}{row}")
        w.height(row, _height(_lines(cells.note, a_px + rest_px), ROW_H))
        row += 1
    # ---- the printed page: centred, fitted, the scores' header repeated on every page
    w.center_horizontally = True
    w.orientation, w.fit_height = page_fit(*_extent(w, span, row - 1))
    sheet = built.wb.add_sheet(SUMMARY, w.xml())
    built.wb.names.append(DefinedName("_xlnm.Print_Titles", f"{quote_sheet(sheet.name)}!${head}:${head}",
                                      scope=sheet.name))
    return sheet.name


def add_curves_sheet(built, sb: StyleBook, curves: RefCurves) -> str:
    """Write the ReferenceCurves tab (charts and tables); returns its name."""
    st = _Styles(sb)
    w = SheetWriter()
    w.text("A1", "Reference curves", st.title)
    if curves.intro:
        w.text("A2", curves.intro, st.sub)
    w.height(1, 24)
    row = 4
    charts = []
    for block in curves.blocks:
        if isinstance(block, CurveBlock):
            w.text(f"A{row}", block.title, st.section)
            if block.subtitle:
                w.text(f"A{row + 1}", block.subtitle, st.sub)
            top = row + 3
            col = 1
            series = []
            for name, points in block.series:
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                w.text(f"{col_letter(col)}{top - 1}", name if len(block.series) > 1 else block.x_label or "Value",
                       st.head_left)
                w.text(f"{col_letter(col + 1)}{top - 1}", block.y_label, st.head)
                for k, (x, y) in enumerate(points):
                    w.number(f"{col_letter(col)}{top + k}", x, st.cell)
                    w.number(f"{col_letter(col + 1)}{top + k}", y, st.cell)
                x_ref = f"{quote_sheet(CURVES)}!${col_letter(col)}${top}:${col_letter(col)}${top + len(points) - 1}"
                y_ref = f"{quote_sheet(CURVES)}!${col_letter(col + 1)}${top}:${col_letter(col + 1)}${top + len(points) - 1}"
                series.append(Series(name, x_ref, y_ref, xs, ys))
                col += 2
            longest = max([len(p) for _n, p in block.series] + [1])
            chart_col = max(col + 1, 6)
            height = 16
            charts.append(ScatterChart(block.title, block.x_label or "Value", block.y_label, series,
                                       anchor=(chart_col - 1, row - 1, chart_col + 7, row - 1 + height)))
            if block.note:
                w.text(f"A{top + longest + 1}", block.note, st.note)
            row += max(height + 2, longest + 6)
        elif isinstance(block, TableBlock):
            w.text(f"A{row}", block.title, st.section)
            row += 1
            for k, h in enumerate(block.header, start=1):
                w.text(f"{col_letter(k)}{row}", h, st.head_left if k == 1 else st.head)
            for values in block.rows:
                row += 1
                for k, v in enumerate(values, start=1):
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        w.number(f"{col_letter(k)}{row}", v, st.cell)
                    else:
                        w.text(f"{col_letter(k)}{row}", "" if v is None else str(v), st.cell_left)
            if block.note:
                row += 1
                w.text(f"A{row}", block.note, st.note)
            row += 3
    widths = [16, 10, 16, 10, 16, 10, 16, 10]
    for k, wd in enumerate(widths, start=1):
        w.width(k, wd)
    for block in curves.blocks:
        if isinstance(block, TableBlock) and block.widths:
            for k, wd in enumerate(block.widths, start=1):
                w.width(k, max(w.widths.get(k, 0), wd))
    sheet = built.wb.add_sheet(CURVES, w.xml())
    if charts:
        w.drawing_rid = add_charts(built.pkg, sheet.part, charts)
        built.pkg.set_text(sheet.part, w.xml())
    return sheet.name
