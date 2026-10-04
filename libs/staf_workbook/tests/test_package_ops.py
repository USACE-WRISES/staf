"""Package and workbook operations on the real templates: a no-op round trip keeps every part,
renames and reorders keep names scoped to the right sheet, calcChain removal is complete."""
from __future__ import annotations

import zipfile
import io

import pytest

from staf_workbook.xlsx.package import Package
from staf_workbook.xlsx.workbook import WorkbookModel
from templates import EASI, SFARI, main_templates


def _parts(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return dict((n, z.read(n)) for n in z.namelist())


@pytest.mark.parametrize("path", main_templates(), ids=lambda p: p.name)
def test_noop_round_trip_keeps_every_part(path):
    data = path.read_bytes()
    pkg = Package(data)
    out = pkg.write()
    assert _parts(out) == _parts(data)
    assert [i.filename for i in zipfile.ZipFile(io.BytesIO(out)).infolist()] == pkg.order


@pytest.mark.parametrize("path", main_templates(), ids=lambda p: p.name)
def test_workbook_model_reads_sheets_and_names(path):
    wb = WorkbookModel(Package(path.read_bytes()))
    names = [s.name for s in wb.sheets]
    assert len(names) == len(set(n.lower() for n in names)) >= 3
    for s in wb.sheets:
        assert wb.pkg.has(s.part)
    for n in wb.names:
        assert n.scope is None or wb.has_sheet(n.scope)


def test_rename_reorder_and_local_names_follow_the_sheet():
    if not EASI.is_file():
        pytest.skip("EASI template not present")
    pkg = Package(EASI.read_bytes())
    wb = WorkbookModel(pkg)
    local = [n for n in wb.names if n.scope is not None]
    assert local and local[0].scope == "EASI Score"            # the print area
    wb.rename("EASI Score", "Existing Conditions")
    order = [s.name for s in wb.sheets]
    order.remove("Existing Conditions")
    wb.reorder(["Existing Conditions"] + order)
    wb.serialize(active="Existing Conditions")
    again = WorkbookModel(Package(pkg.write()))
    assert again.sheets[0].name == "Existing Conditions"
    assert [n.scope for n in again.names if n.scope] == ["Existing Conditions"]
    assert 'activeTab="0"' in again.xml and 'firstSheet="0"' in again.xml


def test_calc_chain_removal_is_complete():
    if not SFARI.is_file():
        pytest.skip("SFARI template not present")
    pkg = Package(SFARI.read_bytes())
    assert pkg.has("xl/calcChain.xml")
    wb = WorkbookModel(pkg)
    wb.remove_calc_chain()
    assert not pkg.has("xl/calcChain.xml")
    assert "calcChain" not in pkg.text("[Content_Types].xml")
    assert all("calcChain" not in r.target for r in pkg.rels(wb.part))


def test_add_sheet_registers_part_rel_and_content_type():
    if not EASI.is_file():
        pytest.skip("EASI template not present")
    pkg = Package(EASI.read_bytes())
    wb = WorkbookModel(pkg)
    s = wb.add_sheet("Summary", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                     "<sheetData/></worksheet>")
    wb.serialize(active="Summary")
    again = WorkbookModel(Package(pkg.write()))
    assert again.sheet("Summary").part == s.part
    assert "worksheet+xml" in (Package(pkg.write()).content_type(s.part) or "")
