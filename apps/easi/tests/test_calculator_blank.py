"""The blank workbook Get Forms serves and the Apply STAF page links: the template's Instructions, the
Summary, an empty Scenario sheet, Metrics and Reference (owner, 2026-10-23, laid out as SFARI's),
made from the template by ``workbook.build_blank`` and written by ``scripts/build_blank_calculator.py``."""
from __future__ import annotations

import io
import re
import zipfile

import pytest

from easi import calculator, workbook
from easi._vendor.staf_workbook.xlsx.package import Package
from easi._vendor.staf_workbook.xlsx.validate import validate_package
from easi._vendor.staf_workbook.xlsx.workbook import WorkbookModel

SITE = ["Reach name", "Assessment tier", "Reach length", "Drainage area", "Stream order (Strahler)",
        "NARS-9 region", "EPA Level I ecoregion", "EPA Level II ecoregion", "EPA Level III ecoregion"]


def _parts(data: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return [(i.filename, z.read(i.filename)) for i in z.infolist()]


def test_the_shipped_blank_is_what_the_template_makes():
    """Re-run scripts/build_blank_calculator.py after scripts/build_calculator.py or the shared
    toolkit change (then apps/staf-web/parity/copy_page.py for the web app's copy)."""
    assert _parts(calculator.blank_bytes()) == _parts(workbook.build_blank())


def test_it_downloads_under_the_calculator_date():
    assert calculator.BLANK_PATH.name == calculator.blank_filename() == "2026-10-23_EASI Calculator.xlsx"


def test_the_tabs_and_the_one_it_opens_on():
    data = calculator.blank_bytes()
    pkg = Package(data)
    wb = WorkbookModel(pkg)
    assert [s.name for s in wb.sheets if s.state == "visible"] == ["Instructions", "Summary", "Scenario",
                                                                    "Metrics", "Reference"]
    assert [s.name for s in wb.sheets if s.state != "visible"] == ["Results", "ChartData"]
    assert [s.name for s in wb.sheets if re.search(r'tabSelected="1"', pkg.text(s.part))] == ["Instructions"]
    assert re.search(r'<workbookView\b[^>]*\bactiveTab="0"', wb.xml)
    assert 'fullCalcOnLoad="1"' in wb.xml
    assert validate_package(data) == []
    # the template's identity rides along out of sight, so a returned workbook names its method
    assert calculator.workbook_stamps(data) == calculator.workbook_stamps(calculator.template_bytes())


def test_the_scenario_sheet_is_the_empty_calculator():
    pkg = Package(calculator.blank_bytes())
    wb = WorkbookModel(pkg)
    xml = pkg.text(wb.sheet("Scenario").part)
    for name, ref in calculator.entry_cells().items():
        if name == calculator.NOTES_NAME:
            continue
        assert re.search(rf'<c r="{ref}"[^>]*(?:/>|></c>)', xml), (name, ref)     # still empty, so still unfilled


def test_the_summary_is_sfaris_layout():
    """As SFARI's (owner, 2026-10-19 and 2026-10-23): the site block right under the title, Reach name
    and NARS-9 region read live from the scenario sheet, then a gap, then the scores under "Scenario"."""
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.load_workbook(io.BytesIO(calculator.blank_bytes()))["Summary"]
    assert ws["A1"].value == "EASI assessment summary" and ws["A2"].value is None
    assert [ws.cell(r, 1).value for r in range(3, 12)] == SITE
    cells = calculator.entry_cells()
    assert ws["B3"].value == f'=IF(ISBLANK(Scenario!{cells["site_name"]}),"",Scenario!{cells["site_name"]})'
    assert ws["B8"].value == f'=IF(ISBLANK(Scenario!{cells["ctx_region"]}),"",Scenario!{cells["ctx_region"]})'
    assert ws["B4"].value == "Screening"
    assert [ws.cell(r, 2).value for r in (5, 6, 7, 9, 10, 11)] == [None] * 6
    assert ws["A12"].value is None and ws["A13"].value == "Scores" and ws["B14"].value == "Scenario"
    eci = next(c.row for c in ws["A"] if c.value == "ECI")
    name, cell = workbook._named_cells(calculator.template_bytes())["eci"]
    assert ws[f"B{eci}"].value == f'=IF(ISNUMBER({name}!${cell[0]}${cell[1:]}),{name}!${cell[0]}${cell[1:]},"")'
