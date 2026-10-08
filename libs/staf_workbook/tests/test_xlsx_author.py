"""The authored-sheet writer, the style book's additions and the validator's sheet checks: elements
in schema order, conditional formats, the printed page, differential formats, border styles; and
the Summary's page and text-wrap estimates."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from staf_workbook.sheets import _lines, page_fit
from staf_workbook.xlsx.author import SheetWriter
from staf_workbook.xlsx.package import Package
from staf_workbook.xlsx.styles import StyleBook
from staf_workbook.xlsx.validate import validate_package
from templates import main_templates


class _Pkg:
    """Just enough of a package for StyleBook: styles.xml in, styles.xml out."""

    def __init__(self, styles: str):
        self.parts = {"xl/styles.xml": styles}

    def text(self, name):
        return self.parts[name]

    def set_text(self, name, text):
        self.parts[name] = text


def _styles(dxfs: str) -> str:
    return ('<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill/><fill/></fills><borders count="1"><border/></borders>'
            '<cellXfs count="1"><xf/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0"/></cellStyles>'
            f'{dxfs}<tableStyles count="0"/></styleSheet>')


def _children(xml: str) -> list:
    return [c.tag.rsplit("}", 1)[-1] for c in ET.fromstring(xml)]


def test_the_sheet_follows_the_schema_order():
    w = SheetWriter()
    w.text("A1", "x")
    w.width(1, 12)
    w.merge("A1:B1")
    w.conditional("C1:C9", "AND(ISNUMBER(C1),C1>0)", 0)
    w.center_horizontally = True
    w.drawing_rid = "rId1"
    assert _children(w.xml()) == ["sheetPr", "dimension", "sheetViews", "sheetFormatPr", "cols", "sheetData",
                                  "mergeCells", "conditionalFormatting", "printOptions", "pageMargins",
                                  "pageSetup", "drawing"]


def test_rules_escape_comparisons_and_number_priorities_once_per_sheet():
    w = SheetWriter()
    w.text("A1", "x")
    for col in ("D", "F"):
        w.conditional(f"{col}2:{col}9", f"AND(ISNUMBER({col}2),{col}2>0)", 3)
        w.conditional(f"{col}2:{col}9", f"AND(ISNUMBER({col}2),{col}2<0)", 4)
    xml = w.xml()
    assert "&gt;0)" in xml and "&lt;0)" in xml
    root = ET.fromstring(xml)
    blocks = [c for c in root if c.tag.endswith("conditionalFormatting")]
    assert [b.get("sqref") for b in blocks] == ["D2:D9", "F2:F9"]
    rules = [r for b in blocks for r in b]
    assert [r.get("priority") for r in rules] == ["1", "2", "3", "4"]
    assert [r.get("dxfId") for r in rules] == ["3", "4", "3", "4"]
    assert rules[0][0].text == "AND(ISNUMBER(D2),D2>0)"


def test_the_page_defaults_stay_as_they_were():
    xml = SheetWriter().xml()
    assert '<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>' in xml
    assert "printOptions" not in xml and "conditionalFormatting" not in xml


def test_text_drops_characters_xml_forbids():
    w = SheetWriter()
    w.text("A1", "bank\x0bfull & <wide>\nnext")
    xml = w.xml()
    ET.fromstring(xml)                                       # parses
    assert "bankfull &amp; &lt;wide&gt;\nnext" in xml


@pytest.mark.parametrize("dxfs, index, count", [
    ('<dxfs count="2"><dxf><font><b/></font></dxf><dxf/></dxfs>', 2, 3),
    ('<dxfs count="0"/>', 0, 1),
    ("", 0, 1),
])
def test_dxfs_append_to_a_list_an_empty_tag_or_none(dxfs, index, count):
    pkg = _Pkg(_styles(dxfs))
    sb = StyleBook(pkg)
    assert sb.dxf(bold=True, color="2F7A4B") == index
    assert sb.dxf(bold=True, color="2F7A4B") == index        # memoized
    sb.save()
    xml = pkg.text("xl/styles.xml")
    m = re.search(r'<dxfs count="(\d+)">(.*?)</dxfs>', xml)
    assert int(m.group(1)) == count == len(re.findall(r"<dxf\b", m.group(2)))
    assert '<dxf><font><b/><color rgb="FF2F7A4B"/></font></dxf>' in m.group(2)
    assert xml.index("</cellStyles>") < xml.index("<dxfs") < xml.index("<tableStyles")
    ET.fromstring(xml)


def test_border_styles_are_distinct():
    sb = StyleBook(_Pkg(_styles("")))
    thin, medium = sb.border(bottom="D5DEEA"), sb.border(bottom="D5DEEA", bottom_style="medium")
    assert thin != medium and sb.border(bottom="D5DEEA", bottom_style="medium") == medium
    assert '<bottom style="medium"><color rgb="FFD5DEEA"/></bottom>' in sb.xml


def test_a_dxf_on_every_real_template_keeps_it_valid():
    templates = main_templates()
    if not templates:
        pytest.skip("no templates present")
    for path in templates:
        data = path.read_bytes()
        before = validate_package(data)
        pkg = Package(data)
        sb = StyleBook(pkg)
        sb.dxf(bold=True, color="2F7A4B")
        sb.dxf(bold=True, color="A33A3A")
        sb.save()
        assert validate_package(pkg.write()) == before, path.name


def test_the_validator_catches_order_dxf_range_and_counts():
    from staf_workbook.assemble import ScenarioInput
    from staf_workbook.model.compare import Measure, ScenarioScores
    from staf_workbook.xlsx.workbook import WorkbookModel
    from test_assemble_full import _deep
    sc = [ScenarioInput("existing", "Existing Conditions"), ScenarioInput("s2", "Alternative 1")]
    data, _fns = _deep(sc, [ScenarioScores(Measure(0.5)), ScenarioScores(Measure(0.6))])
    assert validate_package(data) == []

    def tampered(change_sheet=None, change_styles=None):
        pkg = Package(data)
        part = WorkbookModel(pkg).sheet("Summary").part
        if change_sheet:
            pkg.set_text(part, change_sheet(pkg.text(part)))
        if change_styles:
            pkg.set_text("xl/styles.xml", change_styles(pkg.text("xl/styles.xml")))
        return validate_package(pkg.write())

    def move_rules(xml):
        block = re.search(r"<conditionalFormatting\b.*</conditionalFormatting>", xml).group(0)
        return xml.replace(block, "").replace("</worksheet>", block + "</worksheet>")
    assert any("out of schema order" in p for p in tampered(change_sheet=move_rules))
    assert any("conditional format uses format 99" in p
               for p in tampered(change_sheet=lambda x: re.sub(r'dxfId="\d+"', 'dxfId="99"', x, count=1)))
    assert any("dxfs count" in p
               for p in tampered(change_styles=lambda x: re.sub(r'<dxfs count="\d+"', '<dxfs count="99"', x)))


def test_page_fit_prefers_one_portrait_page():
    assert page_fit(331, 785) == ("portrait", 1)            # one alternative, 20 functions
    assert page_fit(604, 785) == ("portrait", 1)            # three alternatives
    assert page_fit(1328, 785) == ("landscape", 0)          # nine alternatives: fit the width, flow down
    assert page_fit(400, 2000) == ("portrait", 0)           # long descriptions
    assert page_fit(604, 1200) == ("portrait", 0)           # three alternatives, long text: still portrait


def test_lines_wrap_words_newlines_and_long_words():
    assert _lines("Existing Conditions", 91, bold=True) == 2
    assert _lines("Alternative 1", 91, bold=True) == 1
    assert _lines("first\nsecond", 400) == 2
    assert _lines("x" * 60, 91) == 5
    assert _lines("", 91) == 1
