"""The blank workbook Get Forms serves and the Apply STAF page links (owner, 2026-10-19): the
completed workbook's tabs (Instructions, Summary, Existing Conditions, ReferenceCurves) with one
empty Existing Conditions, made from the template by ``workbook.build_blank`` and written by
``scripts/build_blank_calculator.py``."""
from __future__ import annotations

import io
import re
import zipfile

import pytest

from sfari import calculator, workbook
from sfari._vendor.staf_workbook.xlsx.package import Package
from sfari._vendor.staf_workbook.xlsx.validate import validate_package
from sfari._vendor.staf_workbook.xlsx.workbook import WorkbookModel


def _parts(data: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return [(i.filename, z.read(i.filename)) for i in z.infolist()]


def test_the_shipped_blank_is_what_the_template_makes():
    """Re-run scripts/build_blank_calculator.py after the template, the shared toolkit or the
    metrics' criteria change (then apps/staf-web/parity/copy_page.py for the web app's copy)."""
    assert _parts(calculator.blank_bytes()) == _parts(workbook.build_blank())


def test_it_downloads_under_the_calculator_version():
    assert calculator.BLANK_PATH.name == calculator.blank_filename() == "2026-10-15_SFARI Calculator.xlsx"


def test_the_tabs_and_the_one_it_opens_on():
    data = calculator.blank_bytes()
    pkg = Package(data)
    wb = WorkbookModel(pkg)
    assert [s.name for s in wb.sheets if s.state == "visible"] == [
        "Instructions", "Summary", "Existing Conditions", "ReferenceCurves"]
    assert [s.name for s in wb.sheets if re.search(r'tabSelected="1"', pkg.text(s.part))] == ["Instructions"]
    assert re.search(r'<workbookView\b[^>]*\bactiveTab="0"', wb.xml)
    assert 'fullCalcOnLoad="1"' in wb.xml
    assert validate_package(data) == []


def test_existing_conditions_is_the_empty_calculator():
    pkg = Package(calculator.blank_bytes())
    wb = WorkbookModel(pkg)
    xml = pkg.text(wb.sheet("Existing Conditions").part)
    refs = (["B2", "B3", "B4", "G2"] + [f"H{r}" for _f, r, _l in calculator.FUNCTION_ROWS]
            + [f"J{r}" for _m, r, _l in calculator.METRIC_ROWS])
    for ref in refs:
        assert re.search(rf'<c r="{ref}"[^>]*/>', xml), ref         # still empty, so still unfilled
    instructions = pkg.text(wb.sheet("Instructions").part)
    assert "'Existing Conditions'!F8" in instructions                  # the charts' block follows the rename


def test_the_summary_leaves_the_site_and_the_day_to_the_assessor():
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.load_workbook(io.BytesIO(calculator.blank_bytes()))["Summary"]
    assert ws["A1"].value == "SFARI assessment summary" and ws["A2"].value is None
    scores = next(c.row for c in ws["A"] if c.value == "Scores")
    site = [(ws.cell(r, 1).value, ws.cell(r, 2).value) for r in range(4, scores)]
    assert site[0] == ("Reach name", None)
    assert [kv for kv in site if kv[1]] == [("Assessment tier", "Rapid")]
    eci = next(c.row for c in ws["A"] if c.value == "ECI")
    assert ws[f"B{eci}"].value == "=IF(ISNUMBER('Existing Conditions'!$A$8),'Existing Conditions'!$A$8,\"\")"
    curves = openpyxl.load_workbook(io.BytesIO(calculator.blank_bytes()))["ReferenceCurves"]
    assert any(c.value == "Scoring criteria" for row in curves.iter_rows() for c in row)
