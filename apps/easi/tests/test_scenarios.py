"""Scenarios in EASI (owner, 2026-10-03): a scenario changes ratings only (the override of any
function, the cross-section, the observed channel class and bank condition); each scores exactly
as the Assessment page always scored; the workbook carries one calculator per scenario behind a
Summary tab; none of it touches the method's identity."""
from __future__ import annotations

import ast
import copy
import datetime as dt
import io
import re
import tempfile
import zipfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import calculator_cases as cc
from easi import assessment, calculator, method_package, scenario_state, workbook
from easi._vendor.staf_workbook.model.scenarios import BASELINE_ID, ScenarioSet
from easi.metrics import geomorphology as g

app = pytest.importorskip("app")
SRC = Path(app.__file__).read_text(encoding="utf-8")
CHAN, BANK = g.CHANNEL_EVOL_ID, g.BANK_EROSION_ID
BASE_DELIN = {"gnis_name": "Test Creek", "comid": 1234567, "drainage_area_sqkm": 50.0, "stream_order": 3,
              "reach_length_ft": 1000, "snapped_lat": 40.1, "snapped_lon": -83.1}


@pytest.fixture(scope="module")
def report():
    return cc.score_case({"record": {}})


def _old_scored(report, overrides, owned, texts, traces, reason, station_block, observed):
    """The Assessment page's scored() before scenarios (2026-10-02), verbatim but for its inputs."""
    sc = assessment.rescore(report, dict(overrides))
    if owned:
        scrolled = reason == "scrolled"
        station = (station_block or {}).get("label") or "the shown station"
        where = f"cross-section at {station}" if scrolled else "edited cross-section"
        note = (f"scored from the section at {station}, not the reach median" if scrolled
                else "recomputed from your bankfull/floodplain heights")
        for row in sc["metricRows"]:
            mid = row["metricId"]
            if mid in owned:
                row["status"] = "xs-derived"
                row["source"] = where
                row["valueText"] = texts.get(mid) or f"from {where}: {row['rating']}"
                row["note"] = note
                trace = traces.get(mid)
                if trace:
                    row["scoring"] = trace
                    row["generatedRating"] = trace.get("generatedRating")
                    row["completeness"] = trace.get("completeness", row.get("completeness"))
    if observed:
        sc = assessment.apply_observed_evidence(sc, observed)
    return sc


def _with_sections(report):
    rep = copy.deepcopy(report)
    rep["crossSection"] = {"selected": 0, "candidates": [
        {"thalweg": 100.0, "bankfull_stage": 100.6, "floodplain_stage": 100.5, "label": "station 1"},
        {"thalweg": 100.2, "bankfull_stage": 100.9, "floodplain_stage": 100.7, "label": "station 2"}]}
    return rep


STATES = [
    {},
    {"overrides": {cc.FUNCTIONS["m01"]: "Poor", cc.FUNCTIONS["m13"]: "Good"}},
    {"observed": {CHAN: {"stageClass": "Poor", "indicators": "headcut and bars"},
                  BANK: {"erodingBankPct": 30.0, "armoredBankPct": 12.5}}},
    {"overrides": {cc.FUNCTIONS["m07"]: "Fair"}, "geomOwned": [cc.FUNCTIONS["m07"]], "geomReason": "scrolled",
     "geomText": {cc.FUNCTIONS["m07"]: "ER 1.10 at station 2"}, "xsSel": 1,
     "geomScoring": {cc.FUNCTIONS["m07"]: {"generatedRating": "Fair", "completeness": "complete", "inputs": []}}},
    {"overrides": {cc.FUNCTIONS["m06"]: "Poor", cc.FUNCTIONS["m02"]: "Good"}, "geomOwned": [cc.FUNCTIONS["m06"]],
     "geomText": {}, "geomScoring": {}, "geomReason": "edited",
     "observed": {BANK: {"erodingBankPct": 5.0, "armoredBankPct": 0.0}}},
]


@pytest.mark.parametrize("raw", STATES)
def test_scored_for_is_the_pages_old_scoring(report, raw):
    rep = _with_sections(report)
    st = scenario_state.normalized(raw)
    block = scenario_state.xs_block(rep, st["xsSel"])
    old = _old_scored(rep, st["overrides"], set(st["geomOwned"]), st["geomText"], st["geomScoring"],
                      st["geomReason"], block, st["observed"])
    assert scenario_state.scored_for(rep, raw) == old


def test_a_state_from_a_file_or_an_old_page_is_read_safely():
    st = scenario_state.normalized({"overrides": None, "geomOwned": "x", "geomReason": "odd", "xsSel": "2",
                                    "extra": 1})
    assert st == {**scenario_state.empty(), "xsSel": 2}
    assert scenario_state.normalized(None) == scenario_state.empty()


def _two(report):
    sset = ScenarioSet()
    alt = sset.add("Restore riparian", "Replant the corridor")
    alt.state = STATES[1]
    return sset, [(sset.items[0], scenario_state.scored_for(report, {})),
                  (sset.items[1], scenario_state.scored_for(report, alt.state))]


def _sheet_xml(z, name):
    book = z.read("xl/workbook.xml").decode("utf-8")
    tag = next(t for t in re.findall(r"<sheet\b[^>]*/>", book) if f'name="{name}"' in t)
    rid = re.search(r'r:id="([^"]+)"', tag).group(1)
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    target = next(re.search(r'Target="([^"]+)"', t).group(1) for t in re.findall(r"<Relationship\b[^>]*/>", rels)
                  if f'Id="{rid}"' in t)
    return z.read("xl/" + target.lstrip("/").replace("xl/", "", 1)).decode("utf-8")


def test_workbook_has_a_calculator_per_scenario_behind_a_summary(report):
    _sset, rows = _two(report)
    data = workbook.build({"delineation": BASE_DELIN, "report": report}, rows, {}, today=dt.date(2026, 10, 3))
    z = zipfile.ZipFile(io.BytesIO(data))
    tags = re.findall(r"<sheet\b[^>]*/>", z.read("xl/workbook.xml").decode("utf-8"))
    names = [re.search(r'name="([^"]+)"', t).group(1) for t in tags]
    assert names[:4] == ["Summary", "Existing Conditions", "Restore riparian", "ReferenceCurves"]
    hidden = set(re.search(r'name="([^"]+)"', t).group(1) for t in tags if 'state="hidden"' in t)
    assert {"S2 Metrics", "S2 Results", "S2 ChartData"} <= hidden
    assert calculator.blank_bytes() == calculator.template_path().read_bytes()    # never changed
    curves = _sheet_xml(z, "ReferenceCurves")
    assert curves.count("<drawing ") == 1                     # the six curve charts sit on one drawing


def test_recalculated_workbook_matches_the_application(report):
    formulas = pytest.importorskip("formulas")
    _sset, rows = _two(report)
    data = workbook.build({"delineation": BASE_DELIN, "report": report}, rows, {}, today=dt.date(2026, 10, 3))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scenarios.xlsx"
        path.write_bytes(data)
        sol = formulas.ExcelModel().loads(str(path)).finish().calculate()

    def value(sheet, cell):
        hit = sol.get(f"'[{path.name}]{sheet.upper()}'!{cell}")
        v = getattr(hit, "value", hit)
        while hasattr(v, "tolist"):
            v = v.tolist()
        while isinstance(v, list) and len(v) == 1:
            v = v[0]
        return v

    names = workbook._named_cells(calculator.blank_bytes())
    cells = workbook.summary_cells(calculator.blank_bytes())
    for k, (_s, sc) in enumerate(rows):
        res = "Results" if k == 0 else f"S{k + 1} Results"
        assert value(res, names["eci"][1]) == pytest.approx(sc["ecosystemConditionIndexRaw"], abs=1e-9)
        for key, nm in workbook.SUB_NAMES.items():
            assert value(res, names[nm][1]) == pytest.approx(sc["subIndicesRaw"][key], abs=1e-9)
        metrics = "Metrics" if k == 0 else f"S{k + 1} Metrics"
        for fid, _label, _cat, (_sheet, cell) in cells.functions:
            assert value(metrics, cell) == pytest.approx(sc["functionScores"][fid], abs=1e-9), fid
    summary = _sheet_xml(zipfile.ZipFile(io.BytesIO(data)), "Summary")
    checked = 0
    for ref, cached in re.findall(r'<c r="([A-Z]+\d+)"[^>]*><f>[^<]*</f><v>([^<]*)</v></c>', summary):
        got = value("Summary", ref)
        if cached == "":
            assert got in (None, ""), ref
        else:
            assert got == pytest.approx(float(cached), abs=1e-9), ref
            checked += 1
    assert checked > 40


def test_reference_curves_are_the_ones_this_site_was_scored_against(report):
    rc = workbook.reference_curves(report)
    curves = [b for b in rc.blocks if getattr(b, "series", None)]
    assert len(curves) == 6 and all(len(b.series) == 1 and b.series[0][1] for b in curves)
    assert any("Entrenchment ratio" in b.x_label for b in curves)
    table = next(b for b in rc.blocks if getattr(b, "rows", None) is not None and b.title == "Rating bands")
    assert table.header == ["Function", "Measure", "Good", "Fair", "Poor"] and table.rows


def test_summary_info_takes_easis_own_regions(report):
    info = workbook.summary_info({"delineation": BASE_DELIN, "report": report})
    rows = dict(info.rows())
    assert rows["Assessment tier"] == "Screening" and rows["Reach name"] == "Test Creek"
    assert "Temperate Plains" in rows["NARS-9 region"] and "(8)" in rows["EPA Level I ecoregion"]


def test_the_method_identity_is_untouched():
    root = method_package.package_root()
    sources = set(p.resolve() for p in method_package.acquisition_sources())
    for rel in ("workbook.py", "scenario_state.py", "calculator.py"):
        assert (root / rel).resolve() not in sources
    vend = root / "_vendor" / "staf_workbook"
    assert vend.is_dir() and not any(p.resolve() in sources for p in vend.rglob("*") if p.is_file())
    assert all(name not in method_package.DIGEST_SOURCES for name in ("workbook.py", "scenario_state.py"))


# ---- the page's own helpers, run outside Shiny
class _Value:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = value


def _helpers(report):
    server = next(n for n in ast.parse(SRC).body if isinstance(n, ast.FunctionDef) and n.name == "server")
    updates = []
    ns = {**vars(app), "reactive": SimpleNamespace(isolate=nullcontext),
          "base_result": _Value({"delineation": BASE_DELIN, "report": _with_sections(report)}),
          "_overrides": _Value({}), "_observed": _Value({}), "_geom_owned": _Value(set()), "_geom_text": _Value({}),
          "_geom_scoring": _Value({}), "_geom_reason": _Value("edited"), "_xs_sel": _Value(None),
          "_xs_unit_prev": _Value("ft"), "_xs_heights": _Value(None), "_xs_echo": {"want": None, "was": None},
          "scenario_rev": _Value(0), "scenario_nonce": _Value(0), "_sc": {"set": ScenarioSet(), "dialog": None},
          "boxes": {"xs_bankfull": 1.5, "xs_lowbank": 1.2}, "updates": updates}
    ns["current_overrides"] = lambda: dict(ns["_overrides"]())
    ns["input"] = SimpleNamespace(xs_bankfull=lambda: ns["boxes"]["xs_bankfull"],
                                  xs_lowbank=lambda: ns["boxes"]["xs_lowbank"])
    ns["ui"] = SimpleNamespace(update_numeric=lambda fid, value: updates.append((fid, value)))
    for name in ("_score_state", "_bump_scenarios", "_xs_per_m", "_capture_state", "_xs_is_echo", "_show_heights",
                 "_apply_state", "_switch_scenario", "_xs_block", "_xs_candidates", "_xs_cross", "_xs_sel_idx",
                 "_xs_default_sel"):
        node = copy.deepcopy(next(n for n in ast.walk(server) if isinstance(n, ast.FunctionDef) and n.name == name))
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), app.__file__, "exec"), ns)
    return ns


def test_switching_keeps_each_scenarios_ratings_and_heights(report):
    ns = _helpers(report)
    m01, m13 = cc.FUNCTIONS["m01"], cc.FUNCTIONS["m13"]
    ns["_overrides"].set({m01: "Poor"})
    ns["_xs_heights"].set((2.5, 1.8))                       # edited heights, feet
    ns["boxes"].update(xs_bankfull=2.5, xs_lowbank=1.8)
    sset = ns["_sc"]["set"]
    sset.set_state(sset.active, ns["_capture_state"]())      # what the New scenario dialog's Save does
    alt = sset.add("Restore riparian")                       # the copy is shown
    ns["_overrides"].set({m13: "Good"})                      # the user re-rates the copy and puts the
    ns["_xs_heights"].set((1.97, 1.64))                      # section back to its own heights
    ns["boxes"].update(xs_bankfull=1.97, xs_lowbank=1.64)
    ns["_switch_scenario"](BASELINE_ID)
    assert ns["_overrides"]() == {m01: "Poor"} and ns["_xs_heights"]() == (2.5, 1.8)
    assert ns["updates"][-2:] == [("xs_bankfull", 2.5), ("xs_lowbank", 1.8)]
    assert sset.get(alt.id).state["xsHeights"] is None       # the section's own heights are not kept
    # the boxes report the switch's values, one at a time: neither is an edit
    assert ns["_xs_is_echo"](2.5, 1.64) and ns["_xs_is_echo"](2.5, 1.8)
    assert ns["_xs_echo"]["want"] is None
    ns["boxes"].update(xs_bankfull=2.5, xs_lowbank=1.8)
    ns["_switch_scenario"](alt.id)
    assert ns["_overrides"]() == {m13: "Good"} and ns["_xs_heights"]() == (1.97, 1.64)
    assert not ns["_xs_is_echo"](3.0, 1.64)                  # a real edit is never taken for an echo
    assert ns["_xs_echo"]["want"] is None


def test_the_page_wires_the_shared_chip():
    assert 'ui.output_ui("scenario_bar"), ui.output_ui("rollup_rail")' in SRC
    assert "@reactive.event(input.staf_scenario_evt)" in SRC
    assert "@reactive.event(input.staf_sc_save)" in SRC and "@reactive.event(input.staf_sc_delete)" in SRC
    assert 'href="staf/staf.css?v=4"' in SRC and 'src="staf/scenarios.js?v=1"' in SRC
    assert SRC.count("_xs_heights.set(None); _reset_scenarios()") == 2      # fresh screening and reset
    panel = SRC.split("def fn_panel():", 1)[1].split("def _cur_row(", 1)[0]
    assert "scenario_nonce()" in panel
    rerate = SRC.split("def _xs_rerate():", 1)[1].split("def _select(", 1)[0]
    assert rerate.index("_xs_is_echo(") < rerate.index("current_geometry()")
    assert "summary=_summary_section(), title=_report_title()" in SRC


def test_summary_reads_the_stream_order_from_the_basin_rows_when_the_delineation_has_none(report):
    rep = {**report, "basin": {"rows": [["Drainage area", "1.00 km²"], ["Stream order", "2"]]}}
    delin = {k: v for k, v in BASE_DELIN.items() if k != "stream_order"}
    rows = dict(workbook.summary_info({"delineation": delin, "report": rep}).rows())
    assert rows["Stream order (Strahler)"] == "2"


def test_the_batch_report_opens_with_the_same_summary_block(report):
    base = {"delineation": BASE_DELIN, "report": report}
    html = str(app._batch_report_modal("site-1", base))
    assert "Assessment summary" in html and "staf-sum" in html and "Screening" in html


def _pdf_text(data: bytes) -> str:
    import io

    pypdf = pytest.importorskip("pypdf")
    return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(data)).pages)


def _report_wiring(base: str):
    """Owner, 2026-10-03 (second pass): a report is its scenario's alone and names it."""
    section = SRC.split("def _summary_section():", 1)[1].split("\n    def ", 1)[0]
    assert "comparison_table" not in section
    assert 'scenario=staf_web.report_scenario(_sc["set"])' in section
    assert 'can_delete=mode == "edit" and not cur.is_baseline' in SRC
    assert 'href="staf/staf.css?v=4"' in SRC
    for ext in ("pdf", "csv", "geojson"):
        assert f"{base}{{staf_web.scenario_suffix(_sc['set'])}}.{ext}" in SRC
    pdf = SRC.split("def dl_pdf():", 1)[1].split("@render", 1)[0]
    assert 'scenario=staf_web.report_scenario(_sc["set"])' in pdf


def test_each_report_names_its_scenario_and_compares_nothing(monkeypatch):
    from easi import report
    from test_report_anchor import _result
    monkeypatch.setattr(report.reportmap, "pdf_flowable", lambda *a, **k: None)
    named = _pdf_text(report.build_pdf(_result(), scenario=("Restore riparian", "Replant the buffer")))
    plain = _pdf_text(report.build_pdf(_result()))
    assert "Scenario: Restore riparian" in named and "Replant the buffer" in named
    assert "Scenario:" not in plain
    _report_wiring("easi_report")
