"""The whole workbook on the real DEEP template: tab order, the Summary's live formulas and their
stored results, its layout (the Compare table on its side: a row per measure, a column per
scenario, each alternative followed by its change), ReferenceCurves charts; and a recalculation
with the ``formulas`` package showing each scenario's Summary column follows its own calculator."""
from __future__ import annotations

import io
import re
import zipfile

import pytest

from staf_workbook.assemble import AssemblySpec, ScenarioInput, assemble
from staf_workbook.model.compare import Measure, ScenarioScores
from staf_workbook.model.refcurves import CurveBlock, RefCurves
from staf_workbook.model.summary import SummaryInfo, regions_for_l3
from staf_workbook.sheets import SummaryCells
from staf_workbook.xlsx.package import Package
from staf_workbook.xlsx.workbook import WorkbookModel
from templates import DEEP

CATEGORIES = ["Hydrology", "Hydrology", "Hydraulics", "Hydraulics", "Geomorphology", "Geomorphology"]


def _names(data: bytes) -> dict:
    wb = zipfile.ZipFile(io.BytesIO(data)).read("xl/workbook.xml").decode("utf-8")
    out = {}
    for m in re.finditer(r'<definedName name="([^"]+)"[^>]*>([^<]*)</definedName>', wb):
        mm = re.match(r"'?([^'!]+)'?!\$?([A-Z]+)\$?(\d+)$", m.group(2))
        if mm and "localSheetId" not in m.group(0):
            out[m.group(1)] = (mm.group(1), mm.group(2) + mm.group(3))
    return out


def _deep(scenarios, scores, *, n_fns=3, statements=None, note="", banner=None, lead=(), blank=False):
    if not DEEP.is_file():
        pytest.skip("DEEP template not present")
    data = DEEP.read_bytes()
    names = _names(data)
    fns = sorted(k[3:] for k in names if k.startswith("fs_"))[:n_fns]
    cells = SummaryCells(eci=names["eci"], sub=dict((k, names["sub_index_" + k]) for k in ("physical", "chemical", "biological")),
                         functions=[(f, f, CATEGORIES[k % len(CATEGORIES)], names["fs_" + f]) for k, f in enumerate(fns)],
                         statements=statements or [], note=note)
    curves = RefCurves("", [CurveBlock("Embeddedness", "", "Percent", "Index", [("All", [(0, 1), (100, 0)])])])
    out = assemble(data, AssemblySpec("DEEP", "DEEP Score", lead=lead), scenarios,
                   summary=SummaryInfo("DEEP", "Mink Brook", 1000, 5, 2, regions_for_l3("59"), blank=blank), cells=cells,
                   scores=scores, curves=curves, generated_on=__import__("datetime").date(2026, 10, 14), banner=banner)
    return out, fns


def _summary(data: bytes):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(io.BytesIO(data))
    return wb, wb["Summary"]


def _row_of(ws, label) -> int:
    return next(c.row for c in ws["A"] if c.value == label)


def test_tab_order_formulas_and_stored_results():
    sc = [ScenarioInput("existing", "Existing Conditions"), ScenarioInput("s2", "Restore riparian")]
    scores = [ScenarioScores(Measure(0.52)), ScenarioScores(Measure(0.61))]
    data, _fns = _deep(sc, scores)
    pkg = Package(data)
    wb = WorkbookModel(pkg)
    visible = [s.name for s in wb.sheets if s.state == "visible"]
    assert visible[:4] == ["Summary", "Existing Conditions", "Restore riparian", "ReferenceCurves"]
    summary = pkg.text(wb.sheet("Summary").part)
    assert "IF(ISNUMBER('Existing Conditions'!" in summary or "IF(ISNUMBER(Results!" in summary
    assert "IF(ISNUMBER('S2 Results'!" in summary                 # the alternative reads its own copy
    assert "<v>0.52</v>" in summary and "<v>0.61</v>" in summary
    assert ">0.09<" in summary                                   # the stored change
    assert "<conditionalFormatting" in summary                   # the change is coloured up or down
    curves = pkg.text(wb.sheet("ReferenceCurves").part)
    assert "<drawing " in curves


def test_a_lead_sheet_comes_first_and_opens():
    """The template's Instructions ahead of Summary, and the tab the workbook opens on (owner, 2026-10-19)."""
    sc = [ScenarioInput("existing", "Existing Conditions"), ScenarioInput("s2", "Restore riparian")]
    data, _fns = _deep(sc, [ScenarioScores(Measure(0.52)), ScenarioScores(Measure(0.61))], lead=("instructions",))
    pkg = Package(data)
    wb = WorkbookModel(pkg)
    visible = [s.name for s in wb.sheets if s.state == "visible"]
    assert visible[:5] == ["Instructions", "Summary", "Existing Conditions", "Restore riparian", "ReferenceCurves"]
    assert [s.name for s in wb.sheets if re.search(r'tabSelected="1"', pkg.text(s.part))] == ["Instructions"]
    assert re.search(r'<workbookView\b[^>]*\bactiveTab="0"', wb.xml)
    plain, _fns = _deep(sc, [ScenarioScores(Measure(0.52)), ScenarioScores(Measure(0.61))])
    rest = [s.name for s in WorkbookModel(Package(plain)).sheets if s.name != "Instructions"]
    assert [s.name for s in wb.sheets if s.name != "Instructions"] == rest      # nothing else moves


def test_site_rows_can_read_the_calculator_live_right_under_the_title():
    """SFARI's layout (owner, 2026-10-19): the summary block from row 3, a gap after it, and rows
    that read Existing Conditions live, storing the value the calculator holds."""
    if not DEEP.is_file():
        pytest.skip("DEEP template not present")
    data = DEEP.read_bytes()
    names = _names(data)
    cells = SummaryCells(eci=names["eci"], sub=dict((k, names["sub_index_" + k]) for k in ("physical", "chemical", "biological")),
                         functions=[], site_cells={"Reach name": ("DEEP Score", "B2", "Reach 7"), "Reach length": ("DEEP Score", "G2", 1200)},
                         site_first=True)
    out = assemble(data, AssemblySpec("DEEP", "DEEP Score"), [ScenarioInput("existing", "Existing Conditions")],
                   summary=SummaryInfo("DEEP", "Mink Brook", 1000), cells=cells, scores=[ScenarioScores(Measure(0.5))],
                   generated_on=__import__("datetime").date(2026, 10, 19))
    _wb, ws = _summary(out)
    assert ws["A3"].value == "Reach name" and ws["A11"].value == "EPA Level III ecoregion" and ws["A12"].value is None
    assert ws["B3"].value == '=IF(ISBLANK(\'Existing Conditions\'!B2),"",\'Existing Conditions\'!B2)'
    assert ws["B5"].value == '=IF(ISBLANK(\'Existing Conditions\'!G2),"",\'Existing Conditions\'!G2)'
    assert ws["A13"].value == "Scores"
    xml = Package(out).text(WorkbookModel(Package(out)).sheet("Summary").part)
    assert "<v>Reach 7</v>" in xml and "<v>1200</v>" in xml               # the stored results


def _rules(cell) -> set:
    """The rules a cell shows (openpyxl keeps only a merged range's outline on its inner cells)."""
    b = cell.border
    return set((s.style, s.color.rgb[-6:]) for s in (b.left, b.right, b.top, b.bottom) if s.style)


def _fill(cell):
    return cell.fill.fgColor.rgb[-6:] if cell.fill.fill_type == "solid" else None


def test_the_site_block_under_the_title_follows_the_colour_key():
    """The owner's SFARI Summary (2026-10-19), EASI's too (owner, 2026-10-23): every label and value
    ruled, the rows read from the scenario sheet grey with a grey rule as linked cells, the tier the
    app sets plain, and every other value an orange entry; a summary block under a gap keeps its look."""
    if not DEEP.is_file():
        pytest.skip("DEEP template not present")
    data = DEEP.read_bytes()
    names = _names(data)
    cells = SummaryCells(eci=names["eci"], sub=dict((k, names["sub_index_" + k]) for k in ("physical", "chemical", "biological")),
                         functions=[], site_cells={"Reach name": ("DEEP Score", "B2", "Reach 7"), "Reach length": ("DEEP Score", "G2", 1200)},
                         site_first=True)
    out = assemble(data, AssemblySpec("DEEP", "DEEP Score"), [ScenarioInput("existing", "Existing Conditions")],
                   summary=SummaryInfo("DEEP", blank=True), cells=cells, scores=[ScenarioScores()],
                   generated_on=__import__("datetime").date(2026, 10, 23))
    _wb, ws = _summary(out)
    xml = Package(out).text(WorkbookModel(Package(out)).sheet("Summary").part)
    style = dict(re.findall(r'<c r="([A-Z]+\d+)" s="(\d+)"', xml))
    black, grey = set([("thin", "000000")]), set([("thin", "7F7F7F")])
    for r in range(3, 12):
        assert _rules(ws[f"A{r}"]) == black and _fill(ws[f"A{r}"]) is None, r
        kind = {3: "linked", 5: "linked", 4: "fixed"}.get(r, "entry")
        assert _fill(ws[f"B{r}"]) == {"linked": "F2F2F2", "fixed": None, "entry": "F8CBAD"}[kind], r
        assert style[f"C{r}"] == style[f"D{r}"] == style[f"B{r}"], r      # the merged value cell, edge to edge
        for col in "BCD":
            assert _rules(ws[f"{col}{r}"]) == (grey if kind == "linked" else black), (col, r)
    assert ws["B4"].value == "Detailed" and ws["B6"].value is None
    plain, _fns = _deep([ScenarioInput("existing", "Existing Conditions")], [ScenarioScores()], blank=True)
    _wb, ws = _summary(plain)
    for r in range(4, 13):
        assert _rules(ws[f"B{r}"]) == set() and _fill(ws[f"B{r}"]) is None, r


def test_a_blank_summary_leaves_the_site_and_the_day_out():
    data, _fns = _deep([ScenarioInput("existing", "Existing Conditions")], [ScenarioScores()], blank=True)
    _wb, ws = _summary(data)
    assert ws["A1"].value == "DEEP assessment summary" and ws["A2"].value is None
    site = [(ws.cell(r, 1).value, ws.cell(r, 2).value) for r in range(4, _row_of(ws, "Scores"))]
    assert site[0] == ("Reach name", None) and [kv for kv in site if kv[1]] == [("Assessment tier", "Detailed")]
    eci = _row_of(ws, "ECI")
    assert ws[f"B{eci}"].value.startswith("=IF(ISNUMBER(")                  # still live


def test_one_scenario_shows_existing_conditions_alone():
    data, _fns = _deep([ScenarioInput("existing", "Existing Conditions")], [ScenarioScores(Measure(0.5))])
    _wb, ws = _summary(data)
    head = _row_of(ws, "Index") - 1
    assert [ws.cell(head, c).value for c in range(1, 5)] == [None, "Existing Conditions", None, None]
    assert not any(c.value == "Change" for row in ws.iter_rows() for c in row)
    assert not list(ws.conditional_formatting)
    assert not any("alternative" in str(c.value or "").lower() for row in ws.iter_rows() for c in row)
    assert ws.page_setup.orientation == "portrait" and str(ws.page_setup.fitToHeight) == "1"


def test_the_summary_is_the_compare_table_on_its_side():
    sc = [ScenarioInput("existing", "Existing Conditions"),
          ScenarioInput("s2", "Restore riparian", "Replant the corridor & <stabilize> banks.\nPhase 2 adds wood."),
          ScenarioInput("s3", "Bank and bed work")]
    scores = [ScenarioScores(Measure(0.52)), ScenarioScores(Measure(0.61)), ScenarioScores(Measure(0.40))]
    data, fns = _deep(sc, scores, n_fns=6)
    wb, ws = _summary(data)
    head = _row_of(ws, "Index") - 1
    assert [ws.cell(head, c).value for c in range(1, 7)] == [None, "Existing Conditions", "Restore riparian",
                                                              "Change", "Bank and bed work", "Change"]
    labels = [ws.cell(r, 1).value for r in range(head + 1, ws.max_row + 1)]
    assert labels[:5] == ["Index", "ECI", "Physical", "Chemical", "Biological"]
    assert labels[5:14] == ["Hydrology", fns[0], fns[1], "Hydraulics", fns[2], fns[3], "Geomorphology", fns[4], fns[5]]
    eci = _row_of(ws, "ECI")
    assert ws[f"D{eci}"].value == f'=IF(AND(ISNUMBER(C{eci}),ISNUMBER($B{eci})),ROUND(C{eci},2)-ROUND($B{eci},2),"")'
    assert ws[f"B{eci}"].font.b and ws[f"B{eci}"].fill.fgColor.rgb == "FFF6F8FB"     # the headline row, the base tint
    blocks = dict((str(cf.sqref), cf.rules) for cf in ws.conditional_formatting)
    assert sorted(blocks) == [f"D{head + 1}:D{ws.max_row}", f"F{head + 1}:F{ws.max_row}"]
    for rules in blocks.values():
        assert [(r.dxf.font.b, r.dxf.font.color.rgb) for r in rules] == [(True, "FF2F7A4B"), (True, "FFA33A3A")]
    for row in ws.iter_rows():
        for c in row:
            if c.fill is not None and c.fill.fill_type == "solid":
                rgb = c.fill.fgColor.rgb[-6:]
                lum = sum(int(rgb[k:k + 2], 16) * w for k, w in ((0, 0.299), (2, 0.587), (4, 0.114))) / 255
                assert lum >= 0.85, (c.coordinate, rgb)                     # no dark fills
            if c.value not in (None, ""):
                assert c.font.sz >= 11, c.coordinate
                assert (c.font.color is None or c.font.color.rgb != "FFFFFFFF"), c.coordinate
    desc = _row_of(ws, "Restore riparian")
    assert f"B{desc}:F{desc}" in [str(m) for m in ws.merged_cells.ranges]
    assert ws.row_dimensions[desc].height > 18                            # two lines
    assert ws.page_setup.orientation == "portrait" and ws.print_options.horizontalCentered
    model = WorkbookModel(Package(data))
    titles = [n.text for n in model.names if n.name == "_xlnm.Print_Titles" and n.scope == "Summary"]
    assert titles == [f"Summary!${head}:${head}"]                         # the header repeats on each page
    xml = Package(data).text(model.sheet("Summary").part)
    assert "—" not in xml


def test_statements_and_note_follow_the_table():
    sc = [ScenarioInput("existing", "Existing Conditions"), ScenarioInput("s2", "Alternative 1")]
    claims = ["0.61 (Functioning)", "0.52 to 0.71, condition not determinable, 3 of 20 functions not assessed"]
    data, _fns = _deep(sc, [ScenarioScores(Measure(0.61)), ScenarioScores()],
                       statements=[("Condition claim", claims)], note="Index values are the calculator's arithmetic.")
    _wb, ws = _summary(data)
    heading = _row_of(ws, "Condition claim")
    last_measure = max(c.row for c in ws["A"] if c.value in ("ECI", "Biological"))
    assert heading > last_measure
    assert [(ws.cell(heading + k, 1).value, ws.cell(heading + k, 2).value) for k in (1, 2)] == \
        [("Existing Conditions", claims[0]), ("Alternative 1", claims[1])]
    note = _row_of(ws, "Index values are the calculator's arithmetic.")
    assert note > heading + 2 and f"A{note}:D{note}" in [str(m) for m in ws.merged_cells.ranges]


def test_ten_scenarios_go_landscape():
    sc = [ScenarioInput("existing", "Existing Conditions")] + [ScenarioInput(f"s{k}", f"Alternative {k}") for k in range(1, 10)]
    data, _fns = _deep(sc, [ScenarioScores(Measure(0.5))] * 10)
    _wb, ws = _summary(data)
    head = _row_of(ws, "Index") - 1
    assert ws.cell(head, 20).value == "Change" and ws.cell(head, 21).value is None    # A, then 1 + 9 x 2 columns
    assert ws.page_setup.orientation == "landscape"


def test_formulas_recalculate_each_scenario_from_its_own_calculator():
    formulas = pytest.importorskip("formulas")
    sc = [ScenarioInput("existing", "Existing Conditions"), ScenarioInput("s2", "Alternative 1")]
    data, _fns = _deep(sc, [ScenarioScores(), ScenarioScores()])
    pkg = Package(data)
    wb = WorkbookModel(pkg)
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp()) / "a.xlsx"
    tmp.write_bytes(data)
    model = formulas.ExcelModel().loads(str(tmp)).finish()
    sol = model.calculate()
    summary_part = pkg.text(wb.sheet("Summary").part)
    # the ECI row: Existing Conditions in column B reads Results, Alternative 1 in column C its own copy
    row = re.search(r'<c r="A(\d+)"[^>]*><is><t xml:space="preserve">ECI</t>', summary_part).group(1)
    refs = dict(re.findall(rf'<c r="([BC]{row})"[^>]*><f>IF\(ISNUMBER\(([^,]+)\)', summary_part))
    assert refs[f"B{row}"].startswith("Results!") and refs[f"C{row}"].startswith("'S2 Results'!")
    got = {}
    for cell in refs:
        key = next(k for k in sol if k.upper().endswith(f"[A.XLSX]SUMMARY'!{cell}") or
                   k.upper().endswith(f"SUMMARY'!{cell}"))
        got[cell] = sol[key].value[0, 0]
    assert len(set(map(str, got.values()))) == 1                 # blank inputs: both calculators agree


# --------------------------------------------------------------------------- #
# DEEP's assessment status across the authored tabs (owner, 2026-10-14)
# --------------------------------------------------------------------------- #
def test_a_banner_heads_the_authored_tabs_and_nothing_else_moves():
    from staf_workbook.sheets import Banner
    scenarios = [ScenarioInput("ec", "Existing Conditions"), ScenarioInput("a1", "Restore riparian")]
    scores = [ScenarioScores(), ScenarioScores()]
    line = "Assessment status: Draft. For research and regional assessment development only."
    banner = Banner(line, "475569", "Assessment status: Draft.\nFor research and regional "
                                    "assessment development only.")
    marked, _ = _deep(scenarios, scores, banner=banner)
    plain, _ = _deep(scenarios, scores)
    again, _ = _deep(scenarios, scores)
    assert plain == again, "without a banner the workbook is what it was"
    wb, ws = _summary(marked)
    assert ws["A3"].value == line and ws["A3"].font.bold
    assert ws["A3"].fill.fgColor.rgb.endswith("475569")
    assert any(str(r).startswith("A3:") for r in ws.merged_cells.ranges)
    assert ws["A5"].value == "Reach name", "the summary block starts under the band"
    assert ws.column_dimensions["D"].width == 15.7109, "column D widens (Excel shows 15) so the band fits one row"
    assert ws.row_dimensions[3].height == 21
    curves = wb["ReferenceCurves"]
    assert curves["A3"].value == line
    for name in ("Summary", "ReferenceCurves"):
        assert wb[name].oddHeader.center.text.startswith("Assessment status: Draft.")
        assert wb[name].sheet_properties.tabColor.rgb.endswith("475569")
    _pwb, pws = _summary(plain)
    assert pws["A3"].value is None and pws["A4"].value == "Reach name"
