"""Copying the calculator per scenario, on the three apps' real templates.

* isolation: an alternative's sheets and charts read only themselves and the shared sheets, and
  the baseline never reads an alternative;
* normalized equivalence: map the names back, blank regenerated ids and tab selection, and every
  copied sheet and chart equals the template byte for byte;
* exactly one selected tab; the package passes its own checks (``finish`` raises otherwise).
"""
from __future__ import annotations

import re

import pytest

from staf_workbook.assemble import AssemblySpec, ScenarioInput, build, finish
from staf_workbook.xlsx import formula as F
from staf_workbook.xlsx.clone import chart_formulas, rewrite_chart_xml, rewrite_sheet_xml, sheet_formulas
from staf_workbook.xlsx.package import R_CHART, R_DRAWING, Package
from staf_workbook.xlsx.workbook import WorkbookModel
from templates import DEEP, EASI, SFARI

CASES = [
    pytest.param(EASI, "EASI Score", {}, ["EASI Score", "Metrics", "Results", "ChartData"], id="easi"),
    pytest.param(DEEP, "DEEP Score", {}, ["DEEP Score", "Metrics", "Results", "ChartData"], id="deep"),
    pytest.param(SFARI, "SFARI Score", {"Instructions": "L4:P27"}, ["SFARI Score"], id="sfari"),
]
SCENARIOS = [ScenarioInput("existing", "Existing Conditions"), ScenarioInput("s2", "Alternative 1"),
             ScenarioInput("s3", "Restore riparian")]


def _norm(xml: str) -> str:
    xml = re.sub(r"'([A-Za-z_][A-Za-z0-9_.]*)'!", lambda m: m.group(1) + "!", xml)   # optional quotes
    xml = re.sub(r'="\{[0-9A-Fa-f-]{36}\}"', '="{ID}"', xml)
    xml = re.sub(r'\stabSelected="[^"]*"', "", xml)
    return re.sub(r'\scodeName="[^"]*"', "", xml)


def _charts(pkg, part):
    out = []
    for r in pkg.rels(part):
        if r.type == R_DRAWING:
            d = Package.resolve(part, r.target)
            out += [Package.resolve(d, c.target) for c in pkg.rels(d) if c.type == R_CHART]
    return out


def _assembled(path, score, blocks):
    if not path.is_file():
        pytest.skip(f"{path.name} not present")
    spec = AssemblySpec("T", score, blocks=blocks)
    built = build(path.read_bytes(), spec, SCENARIOS)
    data = finish(built, spec, first=[], after=[])
    return data, built


@pytest.mark.parametrize("path, score, blocks, per", CASES)
def test_copies_are_isolated(path, score, blocks, per):
    data, built = _assembled(path, score, blocks)
    pkg = Package(data)
    wb = WorkbookModel(pkg)
    alt_sets = {}
    for k, alt in enumerate(built.alt_sheets, start=2):
        own = {alt.lower()} | set(v.lower() for v in built.helpers[alt].values())
        own |= set(s.name.lower() for s in wb.sheets if s.name.lower().startswith(f"s{k} "))
        alt_sets[alt] = own
    ec_set = {built.ec_sheet.lower()} | set(p.lower() for p in per[1:])
    template_sheets = set(s.name.lower() for s in WorkbookModel(Package(path.read_bytes())).sheets)
    shared = template_sheets - set(p.lower() for p in per)
    for alt, own in alt_sets.items():
        for s in wb.sheets:
            if s.name.lower() not in own:
                continue
            texts = sheet_formulas(pkg.text(s.part))
            for chart in _charts(pkg, s.part):
                texts += chart_formulas(pkg.text(chart))
            for t in texts:
                bad = F.referenced_sheets(t) - own - shared
                assert not bad, (alt, s.name, t)
    everything_alt = set().union(*alt_sets.values())
    for s in wb.sheets:
        if s.name.lower() in ec_set or s.name.lower() in shared:
            for t in sheet_formulas(pkg.text(s.part)):
                assert not F.referenced_sheets(t) & everything_alt, (s.name, t)


@pytest.mark.parametrize("path, score, blocks, per", CASES)
def test_copies_equal_the_template_after_mapping_back(path, score, blocks, per):
    data, built = _assembled(path, score, blocks)
    tpl = Package(path.read_bytes())
    twb = WorkbookModel(tpl)
    pkg = Package(data)
    wb = WorkbookModel(pkg)
    for alt in built.alt_sheets:
        back = {alt.lower(): score}
        for orig, copy in built.helpers[alt].items():
            back[copy.lower()] = orig
        for orig in per:
            copy = alt if orig == score else built.helpers[alt][orig]
            got = rewrite_sheet_xml(pkg.text(wb.sheet(copy).part), back)
            want = tpl.text(twb.sheet(orig).part)
            assert _norm(got) == _norm(want), (alt, orig)
            for c_got, c_want in zip(_charts(pkg, wb.sheet(copy).part), _charts(tpl, twb.sheet(orig).part)):
                block_back = dict(back)
                for b in blocks:
                    block_back[f"s{alt and built.alt_sheets.index(alt) + 2} {b} block".lower()] = b
                assert _norm(rewrite_chart_xml(pkg.text(c_got), block_back)) == _norm(tpl.text(c_want))
    ec_back = {built.ec_sheet.lower(): score}
    for orig in per:
        name = built.ec_sheet if orig == score else orig
        got = rewrite_sheet_xml(pkg.text(wb.sheet(name).part), ec_back)
        assert _norm(got) == _norm(tpl.text(twb.sheet(orig).part)), orig


@pytest.mark.parametrize("path, score, blocks, per", CASES)
def test_one_selected_tab_and_tab_order(path, score, blocks, per):
    data, built = _assembled(path, score, blocks)
    pkg = Package(data)
    wb = WorkbookModel(pkg)
    selected = [s.name for s in wb.sheets if re.search(r'tabSelected="1"', pkg.text(s.part))]
    assert selected == ["Existing Conditions"]
    assert [s.name for s in wb.sheets][:3] == ["Existing Conditions", "Alternative 1", "Restore riparian"]
    assert all(s.state == "visible" for s in wb.sheets[:3])
    for alt in built.alt_sheets:
        for helper in built.helpers[alt].values():
            assert wb.sheet(helper).state == "hidden"
    assert not pkg.has("xl/calcChain.xml")


def test_a_fill_lands_on_its_own_scenario_only():
    if not EASI.is_file():
        pytest.skip("EASI template not present")
    marker = "<v>4242</v>"

    def fill(xml):
        # J8 is an entry cell on the EASI score sheet (written as <c ...></c>)
        new = re.sub(r'<c r="J8"([^>]*?)(?:/>|></c>)', lambda m: '<c r="J8"' + m.group(1) + ">" + marker + "</c>",
                     xml, count=1)
        assert new != xml
        return new
    spec = AssemblySpec("EASI", "EASI Score")
    built = build(EASI.read_bytes(), spec, [ScenarioInput("existing", "Existing Conditions"),
                                            ScenarioInput("s2", "Alternative 1", fill=fill),
                                            ScenarioInput("s3", "Alternative 2")])
    data = finish(built, spec, first=[], after=[])
    pkg = Package(data)
    wb = WorkbookModel(pkg)
    has = dict((n, marker in pkg.text(wb.sheet(n).part)) for n in ("Existing Conditions", "Alternative 1", "Alternative 2"))
    assert has == {"Existing Conditions": False, "Alternative 1": True, "Alternative 2": False}
