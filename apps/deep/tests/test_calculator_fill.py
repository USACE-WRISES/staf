"""The completed Excel calculator: the worksheet's values typed into the workbook.

DEEP fills the workbook StreamCurves generated at publish, at the zip level,
with no spreadsheet library of its own. The template is built here by the real
generator (it lives in the sibling app and needs openpyxl, which the shared
development environment has and a DEEP deployment does not), filled by
``deep.calculator``, and evaluated with the ``formulas`` package: a completed
workbook has to recalculate to the application's own scores.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from deep import calculator, curves, measure
from deep.assessments import LoadedAssessment

pytest.importorskip("openpyxl")
_SC_ROOT = Path(__file__).resolve().parents[2] / "stream-curves"
_GENERATOR = _SC_ROOT / "streamcurves" / "deep_calculator.py"
pytestmark = pytest.mark.skipif(not _GENERATOR.is_file(),
                                reason="the StreamCurves generator is not in this checkout")

ASC = [{"x": 0, "y": 0}, {"x": 10, "y": 1}]
STEEP = [{"x": 0, "y": 0}, {"x": 5, "y": 1}]


def _generator():
    """The generator module, loaded by path: DEEP never imports the sibling app."""
    spec = importlib.util.spec_from_file_location("deep_calculator_generator", _GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bundle() -> dict:
    embed = {"metricId": "spring-phab-xembed", "metricName": "Embeddedness",
             "xLabel": "Embeddedness (%)", "curve": {"points": ASC}, "activeStratum": "",
             "curveLayers": [{"stratum": "", "points": ASC}, {"stratum": "ge_2", "points": STEEP}],
             "stratifier": {"variable": "nhd_slope", "breaks": [0.005, 0.02], "right": False,
                            "classes": [{"key": "lt_0.5", "label": "Low gradient"},
                                        {"key": "0.5_to_2", "label": "Moderate gradient"},
                                        {"key": "ge_2", "label": "Steep (2 percent and above)",
                                         "hasCurve": True}]}}
    fixed = {"metricId": "spring-pctimp2019ws", "metricName": "Impervious surface",
             "xLabel": "Impervious surface (%)", "criteriaBasis": "fixed",
             "curve": {"points": [{"x": 0, "y": 1}, {"x": 10, "y": 0.69},
                                  {"x": 25.005, "y": 0.39}, {"x": 44.5, "y": 0}]}}
    fitted = {"metricId": "spring-bfiws", "metricName": "Base flow index",
              "xLabel": "Base flow index (%)", "curve": {"points": ASC}}
    return {"assessmentId": "fill-test", "assessmentName": "Fill test", "version": 2,
            "assessmentRef": "fill-test@v2", "contentDigest": "sha256:abc",
            "library": {"version": 2, "updatedAt": "2026-09-19T00:00:00Z"},
            "metricsByFunction": [
                {"functionId": "catchment-hydrology", "functionName": "Catchment hydrology",
                 "discipline": "Hydrology", "metrics": [fixed, fitted]},
                {"functionId": "hyporheic-connectivity", "functionName": "Hyporheic connectivity",
                 "discipline": "Hydraulics", "metrics": [embed]},
                {"functionId": "bed-composition-bedform-dynamics",
                 "functionName": "Bed composition and bedform dynamics",
                 "discipline": "Geomorphology", "metrics": [embed]}]}


@pytest.fixture(scope="module")
def template() -> bytes:
    return _generator().build_calculator(_bundle())


DELIN = {"delineation": {"gnis_name": "Mink Brook", "network": "nhdplus-hr",
                         "nhdplus_id": 10000900015475, "comid": 9327576,
                         "snapped_lat": 43.68788, "snapped_lon": -72.2477}}


def test_the_two_apps_name_things_the_same_way():
    gen = _generator()
    for mid in ("spring-phab-xembed", "catchment-hydrology-impervious-cover", "a.b c/d"):
        assert gen.metric_key(mid) == calculator.metric_key(mid)
    m = _bundle()["metricsByFunction"][1]["metrics"][0]
    for stratum in ("", None, "ge_2", "something else"):
        assert gen.layer_label(stratum, m) == calculator.layer_label(stratum, m)
    assert gen.POOLED_LABEL == calculator.POOLED_LABEL
    assert gen.SCORE == calculator.SHEET_NAME


def test_the_entry_cells_come_from_the_workbooks_own_names(template):
    cells = calculator.entry_cells(template)
    assert {"in_spring_phab_xembed", "st_spring_phab_xembed", "in_spring_pctimp2019ws",
            "in_spring_bfiws", "site_name", "site_reach", "site_coords", "site_date",
            "site_notes"} <= set(cells)
    # a metric that serves two functions has one entry cell
    assert sum(1 for n in cells if n.startswith("in_spring_phab_xembed")) == 1


def test_entries_follow_the_worksheet_state():
    la = LoadedAssessment.from_dict(_bundle())
    measured = {"spring-phab-xembed": {"value": 2.5, "stratum": "ge_2", "note": "riffle only"},
                "spring-pctimp2019ws": {"value": 2.0, "origin": "desktop", "engine": True},
                # an engine value against a StreamCat-fitted curve: withheld by the pairing rule
                "spring-bfiws": {"value": 40.0, "origin": "desktop", "engine": True}}
    entries, disclosures = calculator.entries_from_state(la, measured, DELIN)
    assert entries["in_spring_phab_xembed"] == 2.5
    assert entries["st_spring_phab_xembed"] == "Steep (2 percent and above)"
    assert entries["in_spring_pctimp2019ws"] == 2.0          # fixed criteria score engine values
    assert "in_spring_bfiws" not in entries
    assert disclosures and "Base flow index" in disclosures[0]
    assert entries["site_reach"] == "NHDPlusID 10000900015475"
    assert entries["site_coords"] == "43.68788, -72.24770"
    blank, _ = calculator.entries_from_state(la, {"spring-phab-xembed": {"value": 3, "na": True}})
    assert "in_spring_phab_xembed" not in blank


def test_only_the_score_sheet_changes(template):
    la = LoadedAssessment.from_dict(_bundle())
    filled = calculator.build_filled(template, la, {"spring-phab-xembed": {"value": 2.5}},
                                     DELIN, today=dt.date(2026, 9, 19))
    with zipfile.ZipFile(io.BytesIO(template)) as a, zipfile.ZipFile(io.BytesIO(filled)) as b:
        assert a.namelist() == b.namelist()
        changed = [n for n in a.namelist() if a.read(n) != b.read(n)]
        part = calculator._sheet_part(a, calculator.SHEET_NAME)
    assert changed == [part]
    again = calculator.build_filled(template, la, {"spring-phab-xembed": {"value": 2.5}},
                                    DELIN, today=dt.date(2026, 9, 19))
    assert again == filled


def test_a_completed_workbook_recalculates_to_the_applications_scores(template, tmp_path):
    formulas = pytest.importorskip("formulas")
    import openpyxl
    la = LoadedAssessment.from_dict(_bundle())
    measured = {"spring-phab-xembed": {"value": 2.5, "stratum": "ge_2"},
                "spring-pctimp2019ws": {"value": 12.0},
                "spring-bfiws": {"value": 4.0}}
    path = tmp_path / "filled.xlsx"
    path.write_bytes(calculator.build_filled(template, la, measured, DELIN,
                                             today=dt.date(2026, 9, 19)))
    names = {k: v.attr_text for k, v in openpyxl.load_workbook(path).defined_names.items()}

    def key(name):
        sheet, addr = names[name].split("!")
        return f"'[{path.name}]{sheet.strip(chr(39)).upper()}'!{addr.replace('$', '')}"
    wanted = ["eci", "sub_index_physical", "fs_catchment_hydrology", "fs_hyporheic_connectivity",
              "idx_spring_phab_xembed"]
    sol = formulas.ExcelModel().loads(str(path)).finish().calculate(
        outputs=[key(n) for n in wanted])

    def val(name):
        v = sol[key(name)].value
        while hasattr(v, "__len__") and not isinstance(v, str):
            v = v[0]
        return float(v)
    sc, fres = curves.score_site(la, measure.measured_from_state(measured))
    assert val("idx_spring_phab_xembed") == pytest.approx(0.5, abs=1e-12)      # the steep layer
    assert val("fs_hyporheic_connectivity") == pytest.approx(
        fres["hyporheic-connectivity"].score, abs=1e-9)
    assert val("fs_catchment_hydrology") == pytest.approx(
        fres["catchment-hydrology"].score, abs=1e-9)
    assert val("sub_index_physical") == pytest.approx(sc["subIndicesRaw"]["physical"], abs=1e-9)
    # The workbook's ECI cell is the ratio over the rows that carry a score, which
    # is the running total, not the claim. This fixture scores three metrics, so
    # the chemical and biological outcomes have no direct contributor and DEEP
    # declines to state an index at all (test_scoring covers that rule). Parity is
    # asserted against the arithmetic the cell actually implements.
    assert sc["ecosystemConditionIndex"] is None
    assert val("eci") == pytest.approx(sc["ecosystemConditionIndexOverScoredRaw"], abs=1e-9)


def test_a_workbook_is_offered_only_for_the_version_it_was_built_from(template, tmp_path):
    calculator.clear_cache()
    (tmp_path / "fill-test@v2.xlsx").write_bytes(template)
    index = {"calculators": {"fill-test@v2": {"file": "fill-test@v2.xlsx",
                                              "contentDigest": "sha256:abc"}}}
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    la = LoadedAssessment.from_dict(_bundle())
    assert calculator.template_for(la, tmp_path) == template
    moved = LoadedAssessment.from_dict({**_bundle(), "contentDigest": "sha256:other"})
    assert calculator.template_for(moved, tmp_path) is None
    other = LoadedAssessment.from_dict({**_bundle(), "assessmentRef": "fill-test@v9"})
    assert calculator.template_for(other, tmp_path) is None
    calculator.clear_cache()
    assert calculator.template_for(la, tmp_path / "nowhere") is None
    assert calculator.blank_filename(la) == "deep-calculator-fill-test-v2.xlsx"
    assert calculator.filled_filename(la, DELIN) \
        == "deep-calculator-fill-test-v2-nhdplusid-10000900015475.xlsx"
    calculator.clear_cache()


def test_a_moved_entry_cell_fails_loudly(template):
    la = LoadedAssessment.from_dict(_bundle())
    with zipfile.ZipFile(io.BytesIO(template)) as z:
        part = calculator._sheet_part(z, calculator.SHEET_NAME)
        xml = z.read(part).decode("utf-8")
    cells = calculator.entry_cells(template)
    with pytest.raises(ValueError, match="does not carry the expected entry cells"):
        calculator._fill_cells(xml, {"ZZ999": 1.0})
    import re
    computed = re.search(r'<c r="([A-Z]+[0-9]+)"[^>]*><f>', xml).group(1)
    with pytest.raises(ValueError, match="is not empty in the template"):
        # a computed cell is never an entry cell
        calculator._fill_cells(xml, {computed: 1.0})
    assert cells["site_notes"]
    assert la.assessment_id == "fill-test"
