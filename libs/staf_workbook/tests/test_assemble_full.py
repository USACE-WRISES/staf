"""The whole workbook on the real DEEP template: tab order, the Summary's live formulas and their
stored results, the no-alternatives note, ReferenceCurves charts; and a recalculation with the
``formulas`` package showing each scenario's Summary row follows its own calculator."""
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


def _names(data: bytes) -> dict:
    wb = zipfile.ZipFile(io.BytesIO(data)).read("xl/workbook.xml").decode("utf-8")
    out = {}
    for m in re.finditer(r'<definedName name="([^"]+)"[^>]*>([^<]*)</definedName>', wb):
        mm = re.match(r"'?([^'!]+)'?!\$?([A-Z]+)\$?(\d+)$", m.group(2))
        if mm and "localSheetId" not in m.group(0):
            out[m.group(1)] = (mm.group(1), mm.group(2) + mm.group(3))
    return out


def _deep(scenarios, scores):
    if not DEEP.is_file():
        pytest.skip("DEEP template not present")
    data = DEEP.read_bytes()
    names = _names(data)
    fns = sorted(k[3:] for k in names if k.startswith("fs_"))[:3]
    cells = SummaryCells(eci=names["eci"], sub=dict((k, names["sub_index_" + k]) for k in ("physical", "chemical", "biological")),
                         functions=[(f, f, "Hydrology", names["fs_" + f]) for f in fns])
    curves = RefCurves("", [CurveBlock("Embeddedness", "", "Percent", "Index", [("All", [(0, 1), (100, 0)])])])
    out = assemble(data, AssemblySpec("DEEP", "DEEP Score"), scenarios, summary=SummaryInfo("DEEP", "Mink Brook", 1000, 5,
                   2, regions_for_l3("59")), cells=cells, scores=scores, curves=curves)
    return out, fns


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
    assert "No alternative scenarios are defined." not in summary
    curves = pkg.text(wb.sheet("ReferenceCurves").part)
    assert "<drawing " in curves


def test_no_alternatives_note():
    data, _fns = _deep([ScenarioInput("existing", "Existing Conditions")], [ScenarioScores(Measure(0.5))])
    pkg = Package(data)
    wb = WorkbookModel(pkg)
    assert "No alternative scenarios are defined." in pkg.text(wb.sheet("Summary").part)


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
    # find the ECI cells of the two scenario rows (third column of the scores table)
    refs = re.findall(r'<c r="(C\d+)"[^>]*><f>IF\(ISNUMBER\(([^,]+)\)', summary_part)
    assert len(refs) >= 2
    got = {}
    for cell, _ref in refs[:2]:
        key = next(k for k in sol if k.upper().endswith(f"[A.XLSX]SUMMARY'!{cell}") or
                   k.upper().endswith(f"SUMMARY'!{cell}"))
        got[cell] = sol[key].value[0, 0]
    assert len(set(map(str, got.values()))) == 1                 # blank inputs: both calculators agree
