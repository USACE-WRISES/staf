"""The two authored tabs: Summary (the summary block, the scores of every scenario, and the change
from Existing Conditions) and ReferenceCurves (each metric's curve as a chart beside its points,
then any tables). Scores are live formulas pointing at each scenario's calculator, with the app's
values stored as their results so they read correctly before Excel recalculates."""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Optional

from .model.compare import SUB_INDICES, ScenarioScores, excel_round
from .model.refcurves import CurveBlock, RefCurves, TableBlock
from .model.summary import SummaryInfo
from .xlsx.author import SheetWriter, col_letter
from .xlsx.charts import ScatterChart, Series, add_charts
from .xlsx.formula import quote_sheet
from .xlsx.styles import StyleBook

SUMMARY = "Summary"
CURVES = "ReferenceCurves"
INK, MUTED, RULE, HEAD, BAND, ZEBRA = "1F2933", "6B7780", "C9D2D9", "2F4858", "E8EEF2", "F5F8FA"


@dataclass
class SummaryCells:
    """Where a scenario's results sit in the template (template sheet names and cells)."""
    eci: tuple
    sub: dict                                      # physical/chemical/biological -> (sheet, cell)
    functions: list                                # [(fid, label, category, (sheet, cell)), ...]
    index_digits: int = 2
    function_digits: int = 0
    extra_columns: list = field(default_factory=list)   # [(header, [text per scenario]), ...]
    note: str = ""


class _Styles:
    def __init__(self, sb: StyleBook, function_digits: int):
        f = sb.font
        self.title = sb.xf(font=f(bold=True, size=14, color=INK))
        self.sub = sb.xf(font=f(italic=True, size=9, color=MUTED))
        self.label = sb.xf(font=f(bold=True, size=10, color="4A5560"), border=sb.border(bottom="E3E7EB"))
        self.value = sb.xf(font=f(size=10, color=INK), border=sb.border(bottom="E3E7EB"))
        self.section = sb.xf(font=f(bold=True, size=12, color=INK))
        self.band = sb.xf(font=f(bold=True, size=9, color=HEAD), fill=sb.fill(BAND), halign="center")
        self.head = sb.xf(font=f(bold=True, size=9, color="FFFFFF"), fill=sb.fill(HEAD), halign="center", wrap=True)
        self.head_left = sb.xf(font=f(bold=True, size=9, color="FFFFFF"), fill=sb.fill(HEAD), halign="left", wrap=True)
        rule = sb.border(bottom=RULE)
        self.name = sb.xf(font=f(bold=True, size=10, color=INK), border=rule)
        self.name_base = sb.xf(font=f(bold=True, size=10, color=INK), fill=sb.fill(ZEBRA), border=rule)
        self.desc = sb.xf(font=f(size=9, color="4A5560"), border=rule, wrap=True)
        self.desc_base = sb.xf(font=f(size=9, color="4A5560"), fill=sb.fill(ZEBRA), border=rule, wrap=True)
        idx, fn = sb.numfmt("0.00"), sb.numfmt("0" if function_digits == 0 else "0." + "0" * function_digits)
        self.idx = sb.xf(font=f(size=10, color=INK), numfmt=idx, border=rule, halign="center")
        self.idx_base = sb.xf(font=f(size=10, color=INK), numfmt=idx, fill=sb.fill(ZEBRA), border=rule, halign="center")
        self.fn = sb.xf(font=f(size=10, color=INK), numfmt=fn, border=rule, halign="center")
        self.fn_base = sb.xf(font=f(size=10, color=INK), numfmt=fn, fill=sb.fill(ZEBRA), border=rule, halign="center")
        self.text_cell = sb.xf(font=f(size=9, color=INK), border=rule, halign="center", wrap=True)
        self.text_base = sb.xf(font=f(size=9, color=INK), fill=sb.fill(ZEBRA), border=rule, halign="center", wrap=True)
        d_idx = sb.numfmt("[Color10]+0.00;[Red]-0.00;0.00")
        d_fn = sb.numfmt("[Color10]+0;[Red]-0;0" if function_digits == 0 else
                         "[Color10]+0.{0};[Red]-0.{0};0.{0}".format("0" * function_digits))
        self.d_idx = sb.xf(font=f(bold=True, size=10), numfmt=d_idx, border=rule, halign="center")
        self.d_fn = sb.xf(font=f(bold=True, size=10), numfmt=d_fn, border=rule, halign="center")
        self.note = sb.xf(font=f(italic=True, size=9, color=MUTED))
        self.cell = sb.xf(font=f(size=10, color=INK), border=sb.border(bottom="E3E7EB"), halign="center")
        self.cell_left = sb.xf(font=f(size=10, color=INK), border=sb.border(bottom="E3E7EB"), wrap=True)


def _ref(sheet: str, cell: str) -> str:
    col = "".join(ch for ch in cell if ch.isalpha())
    row = "".join(ch for ch in cell if ch.isdigit())
    return f"{quote_sheet(sheet)}!${col}${row}"


def _cached(measure, digits):
    if measure is None or measure.empty:
        return ""
    v = measure.value if measure.value is not None else None
    return "" if v is None else float(v)


def add_summary_sheet(built, spec, sb: StyleBook, info: SummaryInfo, cells: SummaryCells, scores: list,
                      scenarios: list, generated_on: Optional[_dt.date] = None) -> str:
    """Write the Summary tab into ``built``; returns its name."""
    from .assemble import sheet_for
    st = _Styles(sb, cells.function_digits)
    w = SheetWriter()
    date = (generated_on or _dt.date.today()).isoformat()
    w.text("A1", f"{spec.app_title} assessment summary", st.title)
    w.text("A2", f"Generated {date} by the {spec.app_title} web application.", st.sub)
    w.height(1, 24)
    row = 4
    for label, value in info.rows():
        w.text(f"A{row}", label, st.label)
        w.text(f"B{row}", value, st.value)
        for c in (3, 4):
            w.blank(f"{col_letter(c)}{row}", st.value)
        w.merge(f"B{row}:D{row}")
        row += 1
    # ---- scores
    measures = [("eci", "ECI", cells.eci, "idx")] + [(k, label, cells.sub[k], "idx") for k, label in SUB_INDICES]
    extra = list(cells.extra_columns)
    fns = list(cells.functions)
    first_measure_col = 3
    n_index_cols = len(measures) + len(extra)
    row += 1
    w.text(f"A{row}", "Scores", st.section)
    row += 1
    band_row, head_row = row, row + 1
    c = first_measure_col
    w.text(f"{col_letter(c)}{band_row}", "Index", st.band)
    for k in range(1, n_index_cols):
        w.blank(f"{col_letter(c + k)}{band_row}", st.band)
    if n_index_cols > 1:
        w.merge(f"{col_letter(c)}{band_row}:{col_letter(c + n_index_cols - 1)}{band_row}")
    c += n_index_cols
    groups = []
    for fid, label, cat, _cell in fns:
        if not groups or groups[-1][0] != cat:
            groups.append([cat, 0])
        groups[-1][1] += 1
    for cat, count in groups:
        w.text(f"{col_letter(c)}{band_row}", cat or "Functions", st.band)
        for k in range(1, count):
            w.blank(f"{col_letter(c + k)}{band_row}", st.band)
        if count > 1:
            w.merge(f"{col_letter(c)}{band_row}:{col_letter(c + count - 1)}{band_row}")
        c += count
    last_col = c - 1
    w.text(f"A{head_row}", "Scenario", st.head_left)
    w.text(f"B{head_row}", "Description", st.head_left)
    headers = [m[1] for m in measures] + [e[0] for e in extra] + [f[1] for f in fns]
    for i, h in enumerate(headers):
        w.text(f"{col_letter(first_measure_col + i)}{head_row}", h, st.head)
    w.height(head_row, 48)
    rows_by_scenario = []
    n_rows = max(len(scenarios), 2)
    for i in range(n_rows):
        r = head_row + 1 + i
        rows_by_scenario.append(r)
        base = i == 0
        if i >= len(scenarios):
            w.text(f"A{r}", "", st.name)
            w.text(f"B{r}", "", st.desc)
            for k in range(len(headers)):
                w.blank(f"{col_letter(first_measure_col + k)}{r}", st.idx)
            continue
        sc, scs = scenarios[i], scores[i] if i < len(scores) else ScenarioScores()
        w.text(f"A{r}", sc.name, st.name_base if base else st.name)
        w.text(f"B{r}", sc.description, st.desc_base if base else st.desc)
        col = first_measure_col
        for key, _label, (sheet, cell), _kind in measures:
            ref = _ref(sheet_for(built, i, sheet), cell)
            m = scs.eci if key == "eci" else scs.sub.get(key)
            cached = _cached(m, cells.index_digits)
            if cached != "":
                cached = excel_round(cached, 10)
            w.formula(f"{col_letter(col)}{r}", f"IF(ISNUMBER({ref}),{ref},\"\")", cached,
                      st.idx_base if base else st.idx)
            col += 1
        for header, values in extra:
            w.text(f"{col_letter(col)}{r}", values[i] if i < len(values) else "", st.text_base if base else st.text_cell)
            col += 1
        for fid, _label, _cat, (sheet, cell) in fns:
            ref = _ref(sheet_for(built, i, sheet), cell)
            cached = _cached(scs.functions.get(fid), cells.function_digits)
            w.formula(f"{col_letter(col)}{r}", f"IF(ISNUMBER({ref}),{ref},\"\")", cached,
                      st.fn_base if base else st.fn)
            col += 1
    # ---- change from Existing Conditions
    row = rows_by_scenario[-1] + 2
    w.text(f"A{row}", "Change from Existing Conditions", st.section)
    row += 1
    w.text(f"A{row}", "Scenario", st.head_left)
    w.text(f"B{row}", "", st.head_left)
    for i, h in enumerate(headers):
        w.text(f"{col_letter(first_measure_col + i)}{row}", h, st.head)
    w.height(row, 48)
    base_row = rows_by_scenario[0]
    if len(scenarios) < 2:
        r = row + 1
        w.text(f"A{r}", "No alternative scenarios are defined.", st.note)
        r_last = r
    else:
        r_last = row
        for i in range(1, len(scenarios)):
            r = row + i
            r_last = r
            src = rows_by_scenario[i]
            w.text(f"A{r}", scenarios[i].name, st.name)
            w.text(f"B{r}", "", st.desc)
            col = first_measure_col
            for key, _label, _cell, _kind in measures:
                a, b = f"{col_letter(col)}{src}", f"${col_letter(col)}${base_row}"
                d = _delta_cached(scores, i, key, cells.index_digits)
                w.formula(f"{col_letter(col)}{r}",
                          f"IF(AND(ISNUMBER({a}),ISNUMBER({b})),ROUND({a},{cells.index_digits})-ROUND({b},{cells.index_digits}),\"\")",
                          d, st.d_idx)
                col += 1
            for _e in extra:
                w.blank(f"{col_letter(col)}{r}", st.desc)
                col += 1
            for fid, _label, _cat, _cell in fns:
                a, b = f"{col_letter(col)}{src}", f"${col_letter(col)}${base_row}"
                d = _delta_cached(scores, i, "fn:" + fid, cells.function_digits)
                digits = cells.function_digits
                w.formula(f"{col_letter(col)}{r}",
                          f"IF(AND(ISNUMBER({a}),ISNUMBER({b})),ROUND({a},{digits})-ROUND({b},{digits}),\"\")",
                          d, st.d_fn)
                col += 1
    if cells.note:
        w.text(f"A{r_last + 2}", cells.note, st.note)
    w.width(1, 26)
    w.width(2, 34)
    first_extra = first_measure_col + len(measures)
    for k in range(first_measure_col, last_col + 1):
        if first_extra <= k < first_extra + len(extra):
            w.width(k, 30)                                 # a sentence (DEEP's condition claim)
        else:
            w.width(k, 11 if k < first_measure_col + n_index_cols else 12)
    sheet = built.wb.add_sheet(SUMMARY, w.xml())
    return sheet.name


def _delta_cached(scores, i, key, digits):
    base, alt = scores[0], scores[i]

    def pick(s):
        if key == "eci":
            return s.eci
        if key.startswith("fn:"):
            return s.functions.get(key[3:])
        return s.sub.get(key)
    a, b = pick(base), pick(alt)
    if a is None or b is None or a.value is None or b.value is None:
        return ""
    return round(excel_round(b.value, digits) - excel_round(a.value, digits), digits)


def add_curves_sheet(built, sb: StyleBook, curves: RefCurves) -> str:
    """Write the ReferenceCurves tab (charts and tables); returns its name."""
    st = _Styles(sb, 0)
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
