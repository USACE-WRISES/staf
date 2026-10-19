"""The blank workbook Get Forms serves and the Apply STAF page links: the owner's 2026-10-19
calculator (Instructions, Summary, Scenario), made from the template by ``workbook.build_blank`` and
written by ``scripts/build_blank_calculator.py``."""
from __future__ import annotations

import io
import re
import zipfile

import pytest

from sfari import calculator, workbook
from sfari._vendor.staf_workbook.xlsx.package import Package
from sfari._vendor.staf_workbook.xlsx.validate import validate_package
from sfari._vendor.staf_workbook.xlsx.workbook import WorkbookModel

SITE = ["Reach name", "Assessment tier", "Reach length", "Drainage area", "Stream order (Strahler)",
        "NARS-9 region", "EPA Level I ecoregion", "EPA Level II ecoregion", "EPA Level III ecoregion"]


def _parts(data: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return [(i.filename, z.read(i.filename)) for i in z.infolist()]


def test_the_shipped_blank_is_what_the_template_makes():
    """Re-run scripts/build_blank_calculator.py after the template, the shared toolkit or the
    metrics change (then apps/staf-web/parity/copy_page.py for the web app's copy)."""
    assert _parts(calculator.blank_bytes()) == _parts(workbook.build_blank())


def test_it_downloads_under_the_calculator_version():
    assert calculator.BLANK_PATH.name == calculator.blank_filename() == "2026-10-19_SFARI Calculator.xlsx"


def test_the_owners_tabs_and_the_one_it_opens_on():
    data = calculator.blank_bytes()
    pkg = Package(data)
    wb = WorkbookModel(pkg)
    assert [s.name for s in wb.sheets if s.state == "visible"] == ["Instructions", "Summary", "Scenario"]
    assert [s.name for s in wb.sheets if re.search(r'tabSelected="1"', pkg.text(s.part))] == ["Instructions"]
    assert re.search(r'<workbookView\b[^>]*\bactiveTab="0"', wb.xml)
    assert 'fullCalcOnLoad="1"' in wb.xml
    assert validate_package(data) == []


def test_the_scenario_sheet_is_the_empty_calculator():
    pkg = Package(calculator.blank_bytes())
    wb = WorkbookModel(pkg)
    xml = pkg.text(wb.sheet("Scenario").part)
    refs = (["B2", "B3", "B4", "G2"] + [f"H{r}" for _f, r, _l in calculator.FUNCTION_ROWS]
            + [f"J{r}" for _m, r, _l in calculator.METRIC_ROWS])
    for ref in refs:
        assert re.search(rf'<c r="{ref}"[^>]*/>', xml), ref         # still empty, so still unfilled
    assert "Scenario!F8" in pkg.text(wb.sheet("Instructions").part)   # the charts' block reads it


def test_the_summary_is_the_owners_layout():
    """Owner, 2026-10-19: the site block right under the title, Reach name and Reach length read
    live from the scenario sheet, then a gap, then the scores under "Scenario"."""
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.load_workbook(io.BytesIO(calculator.blank_bytes()))["Summary"]
    assert ws["A1"].value == "SFARI assessment summary" and ws["A2"].value is None
    assert [ws.cell(r, 1).value for r in range(3, 12)] == SITE
    assert ws["B3"].value == '=IF(ISBLANK(Scenario!B2),"",Scenario!B2)'
    assert ws["B5"].value == '=IF(ISBLANK(Scenario!G2),"",Scenario!G2)'
    assert ws["B4"].value == "Rapid"
    assert [ws.cell(r, 2).value for r in range(6, 12)] == [None] * 6
    assert ws["A12"].value is None and ws["A13"].value == "Scores" and ws["B14"].value == "Scenario"
    eci = next(c.row for c in ws["A"] if c.value == "ECI")
    assert ws[f"B{eci}"].value == '=IF(ISNUMBER(Scenario!$A$8),Scenario!$A$8,"")'
