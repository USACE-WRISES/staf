"""Scenarios in DEEP (owner, 2026-10-03): the assessment file keeps every scenario with its own
values (the STAF assessment file, 2026-10-05); the workbook carries one calculator per scenario
behind a Summary tab whose numbers are the calculator's own; the page compares condition claims
(an interval where a function cannot be assessed); the assessment page wires the shared chip."""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path

import pytest

from deep import assessments, calculator, scoring, session, workbook
from deep._vendor.staf_workbook import assessment_file
from deep._vendor.staf_workbook.model.compare import Measure
from deep._vendor.staf_workbook.model.scenarios import BASELINE_ID, ScenarioSet

APP = Path(__file__).resolve().parents[1]
SRC = (APP / "app.py").read_text(encoding="utf-8")
REF = "acadian-plains-and-hills@v1"
DELIN = {"delineation": {"gnis_name": "Mink Brook", "reach_length_ft": 1000, "drainage_area_sqkm": 12.3,
                         "stream_order": 3, "snapped_lat": 43.6858, "snapped_lon": -72.2367,
                         "network": "nhdplus-hr", "nhdplus_id": 10000900049512}}
TODAY = dt.date(2026, 10, 3)


@pytest.fixture(scope="module")
def la():
    try:
        return assessments.load_ref(REF)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"{REF} is not baked here: {exc}")


@pytest.fixture(scope="module")
def template(la):
    data = calculator.template_for(la)
    if data is None:
        pytest.skip(f"no calculator for {REF}")
    return data


def _values(la, pick):
    """Field values for the first metrics of each function: ``pick(points)`` chooses the x."""
    out = {}
    for fn in la.metrics_by_function[:8]:
        for m in fn.get("metrics", [])[:2]:
            pts = sorted(((p["x"], p["y"]) for p in ((m.get("curve") or {}).get("points") or [])
                          if p.get("x") is not None), key=lambda p: p[0])
            if len(pts) >= 2 and m["metricId"] not in out:
                out[m["metricId"]] = {"value": round(pick(pts), 4), "origin": "field"}
    return out


def _low(pts):
    return (pts[0][0] + pts[1][0]) / 2


def _high(pts):
    return pts[-1][0]


def _set(la):
    sset = ScenarioSet()
    sset.baseline.state = {"measured_values": _values(la, _low)}
    alt = sset.add("Restore riparian", "Replant the buffer")
    alt.state = {"measured_values": _values(la, _high)}
    return sset


def test_the_file_keeps_every_scenario_with_its_values(la):
    sset = _set(la)
    text = session.dump(DELIN, {"assessmentId": la.assessment_id},
                        assessment_file.scenarios_block(sset, sset.current.state))
    raw = json.loads(text)
    assert raw["tool"] == "DEEP" and "measured_values" not in raw
    assert raw["scenarios"]["items"][0]["state"] == sset.baseline.state
    back = assessment_file.scenario_set(session.load(text)["scenarios"])
    assert [s.name for s in back.items] == ["Existing Conditions", "Restore riparian"]
    assert back.baseline.state == sset.baseline.state and back.items[1].state == sset.items[1].state


def test_a_file_without_alternatives_opens_as_existing_conditions_alone():
    block = assessment_file.scenarios_block(ScenarioSet(), {"measured_values": {"m": {"value": 1}}})
    st = session.load(session.dump(DELIN, {}, block))
    back = assessment_file.scenario_set(st["scenarios"])
    assert [s.id for s in back.items] == [BASELINE_ID]
    assert back.baseline.state == {"measured_values": {"m": {"value": 1}}}


def test_the_page_compares_claims_and_an_unassessed_function_is_an_interval():
    point = {"functionScores": {"a": 9.0}, "ecosystemConditionIndexRaw": 0.61,
             "ecosystemConditionIndexBounds": [0.61, 0.61], "subIndicesRaw": {"physical": 0.6}}
    gap = {"functionScores": {"a": 12.0}, "ecosystemConditionIndexRaw": None,
           "ecosystemConditionIndexBounds": [0.52, 0.71], "subIndicesRaw": {"physical": None}}
    a, b = workbook.claim_scores(point), workbook.claim_scores(gap)
    assert a.eci == Measure(0.61) and b.eci == Measure(low=0.52, high=0.71)
    assert b.sub["physical"].empty and a.functions["a"] == Measure(9.0)
    assert workbook.claim_scores({"functionScores": {}}).eci.empty


def test_workbook_has_a_calculator_per_scenario_behind_a_summary(la, template):
    sset = _set(la)
    rows = [(s, s.state["measured_values"]) for s in sset.items]
    data = workbook.build(template, la, DELIN, rows, today=TODAY)
    z = zipfile.ZipFile(io.BytesIO(data))
    book = z.read("xl/workbook.xml").decode("utf-8")
    tags = re.findall(r"<sheet\b[^>]*/>", book)
    names = [re.search(r'name="([^"]+)"', t).group(1) for t in tags]
    assert names[:4] == ["Summary", "Existing Conditions", "Restore riparian", "ReferenceCurves"]
    hidden = set(re.search(r'name="([^"]+)"', t).group(1) for t in tags if 'state="hidden"' in t)
    assert {"S2 Metrics", "S2 Results", "S2 ChartData", "Results", "ChartData"} <= hidden
    assert "Metrics" not in hidden                     # Existing Conditions keeps the template's tabs
    assert calculator.template_for(la) == template      # the published calculator is never changed
    text = z.read("xl/sharedStrings.xml").decode("utf-8") if "xl/sharedStrings.xml" in z.namelist() else ""
    summary = _sheet_xml(z, book, "Summary")
    assert workbook.CLAIM_HEADER in summary + text


def test_the_summary_lists_each_scenarios_claim_under_the_scores(la, template):
    """The scores read like the Compare dialog (each alternative followed by its change); the
    condition claims follow the table, one row per scenario, then the note."""
    openpyxl = pytest.importorskip("openpyxl")
    sset = _set(la)
    rows = [(s, s.state["measured_values"]) for s in sset.items]
    data = workbook.build(template, la, DELIN, rows, today=TODAY)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert wb.sheetnames[0] == "Summary"
    ws = wb["Summary"]
    head = next(c.row for c in ws["A"] if c.value == "Index") - 1
    assert [ws.cell(head, c).value for c in range(1, 6)] == [None, "Existing Conditions", "Restore riparian",
                                                             "Change", None]
    heading = next(c.row for c in ws["A"] if c.value == workbook.CLAIM_HEADER)
    assert heading > max(c.row for c in ws["A"] if c.value in ("ECI", "Biological"))
    claims = [scoring.index_claim(workbook.score(la, mv)) for _s, mv in rows]
    got = [(ws.cell(heading + k + 1, 1).value, ws.cell(heading + k + 1, 2).value) for k in range(len(rows))]
    assert got == [(s.name, claim) for (s, _mv), claim in zip(rows, claims)]
    note = next(c.row for c in ws["A"] if c.value == workbook.SUMMARY_NOTE)
    assert note > heading + len(rows) and any(str(m).startswith(f"A{note}:") for m in ws.merged_cells.ranges)
    assert len(list(ws.conditional_formatting)) == 1


def _sheet_xml(z, book, name):
    rid = re.search(r'<sheet\b[^>]*name="%s"[^>]*r:id="([^"]+)"' % re.escape(name), book)
    rid = rid or re.search(r'<sheet\b[^>]*r:id="([^"]+)"[^>]*name="%s"' % re.escape(name), book)
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    target = re.search(r'Id="%s"[^>]*Target="([^"]+)"' % rid.group(1), rels) \
        or re.search(r'Target="([^"]+)"[^>]*Id="%s"' % rid.group(1), rels)
    return z.read("xl/" + target.group(1).lstrip("/").replace("xl/", "", 1)).decode("utf-8")


def test_recalculated_workbook_matches_the_application(la, template):
    """Every scenario's calculator computes the application's numbers, and every Summary formula
    equals the result stored with it."""
    formulas = pytest.importorskip("formulas")
    pytest.importorskip("openpyxl")
    sset = _set(la)
    rows = [(s, s.state["measured_values"]) for s in sset.items]
    data = workbook.build(template, la, DELIN, rows, today=TODAY)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scenarios.xlsx"
        path.write_bytes(data)
        model = formulas.ExcelModel().loads(str(path)).finish()
        sol = model.calculate()
    book = path.name                                  # the book keeps its case, sheets are upper

    def value(sheet, cell):
        hit = sol.get(f"'[{book}]{sheet.upper()}'!{cell}")
        v = getattr(hit, "value", hit)
        while hasattr(v, "tolist"):
            v = v.tolist()
        while isinstance(v, list) and len(v) == 1:
            v = v[0]
        return v

    names = workbook._named_cells(template)
    for k, (s, mv) in enumerate(rows):
        sc = workbook.score(la, mv)
        res = "Results" if k == 0 else f"S{k + 1} Results"
        assert value(res, names["eci"][1]) == pytest.approx(sc["ecosystemConditionIndexOverScoredRaw"], abs=1e-9)
        for key, nm in workbook.SUB_NAMES.items():
            assert value(res, names[nm][1]) == pytest.approx(sc["subIndicesOverScoredRaw"][key], abs=1e-9)
        for fid, s_ in sc["functionScores"].items():
            assert value(res, names[f"fs_{calculator.metric_key(fid)}"][1]) == pytest.approx(s_, abs=1e-9)
    # the Summary's stored results equal what Excel will compute
    z = zipfile.ZipFile(io.BytesIO(data))
    summary = _sheet_xml(z, z.read("xl/workbook.xml").decode("utf-8"), "Summary")
    checked = 0
    for ref, cached in re.findall(r'<c r="([A-Z]+\d+)"[^>]*><f>[^<]*</f><v>([^<]*)</v></c>', summary):
        got = value("Summary", ref)
        if cached == "":
            assert got in (None, ""), ref                 # an unscored function stays blank
        else:
            assert got == pytest.approx(float(cached), abs=1e-9), ref
            checked += 1
    assert checked > 20


def test_reference_curves_name_the_curve_set_each_scenario_used(la):
    sset = _set(la)
    rc = workbook.reference_curves(la, [(s.name, s.state["measured_values"]) for s in sset.items])
    assert rc.blocks and all(b.series for b in rc.blocks)
    layered = [m for fn in la.metrics_by_function for m in fn.get("metrics", []) if m.get("curveLayers")]
    if layered:
        names = [m.get("metricName") for m in layered]
        block = next(b for b in rc.blocks if b.title in names)
        assert "Curve set used:" in block.subtitle and "Existing Conditions" in block.subtitle


def test_summary_info_names_the_regions():
    info = workbook.summary_info(DELIN)
    rows = dict(info.rows())
    assert rows["Assessment tier"] == "Detailed"
    assert "Northeastern Highlands" in rows["EPA Level III ecoregion"]
    assert "Northern Appalachians" in rows["NARS-9 region"]


def test_the_page_wires_the_shared_chip():
    assert 'ui.output_ui("scenario_bar"), ui.output_ui("rollup_rail")' in SRC
    assert "@reactive.event(input.staf_scenario_evt)" in SRC
    assert "@reactive.event(input.staf_sc_save)" in SRC and "@reactive.event(input.staf_sc_delete)" in SRC
    assert 'href="staf/staf.css?v=11"' in SRC and 'src="staf/scenarios.js?v=2"' in SRC
    for fn in ("_load_ref_into_state", "_do_reset"):
        assert "_reset_scenarios()" in SRC.split(f"def {fn}(", 1)[1].split("\n    def ", 1)[0]
    assert "scenario_nonce()" in SRC.split("def fn_panel():", 1)[1].split("\n    def ", 1)[0]
    save = SRC.split("def save_session():", 1)[1].split("@render", 1)[0]
    assert "_session_scenarios()" in save
    assert "yield _workbook_bytes(template)" in SRC
    load = SRC.split("def _load_session():", 1)[1].split("\napp = App(", 1)[0]
    assert load.index("_restore_scenarios(") < load.index("_saved_fp.set(_work_fp())")
    done = SRC.split("def _compute_done():", 1)[1].split("\n    @render", 1)[0]
    assert "_merge_into_stored(_prefill)" in done      # desktop values reach every scenario


# ---- the page's own helpers, run outside Shiny (the same extraction the report tests use)
class _Value:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = value


def _helpers():
    import ast
    import copy
    from contextlib import nullcontext
    from types import SimpleNamespace

    import app
    server = next(n for n in ast.parse(SRC).body if isinstance(n, ast.FunctionDef) and n.name == "server")
    ns = {**vars(app), "reactive": SimpleNamespace(isolate=nullcontext), "measured_values": _Value({}),
          "scenario_rev": _Value(0), "scenario_nonce": _Value(0), "_sc": {"set": ScenarioSet(), "dialog": None},
          "_desktop": {"fill": None}}
    ns["input"] = SimpleNamespace(staf_sc_name=lambda: ns["typed"][0], staf_sc_desc=lambda: ns["typed"][1],
                                  staf_sc_from=lambda: ns.get("start", BASELINE_ID))
    ns["ui"] = SimpleNamespace(modal_remove=lambda: None, modal_show=lambda m: None)
    ns["_scenario_dialog"] = lambda *a, **k: None
    for name in ("_bump_scenarios", "_values_now", "_show_values", "_scenario_values", "_stored_scenarios",
                 "_switch_scenario", "_session_scenarios", "_restore_scenarios", "_merge_into_stored",
                 "_desktop_start", "_scenario_save"):
        node = copy.deepcopy(next(n for n in ast.walk(server) if isinstance(n, ast.FunctionDef) and n.name == name))
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), app.__file__, "exec"), ns)
    return ns


def _new_scenario(ns, name, desc="", start=BASELINE_ID):
    """What the Add a scenario dialog's Add does on the page (``start``: a scenario id or "blank")."""
    ns["_sc"]["dialog"], ns["typed"], ns["start"] = "new", (name, desc), start
    ns["_scenario_save"]()


def test_add_starts_from_existing_conditions_or_from_what_the_app_fills_in(monkeypatch):
    """Owner, 2026-10-05: Add asks where a scenario starts. A copy of Existing Conditions while an
    alternative is shown is Existing Conditions'; Blank is a fresh assessment of the site: the
    desktop values and the preselected curve sets, none of the assessor's values, notes or photos."""
    import app
    ns = _helpers()
    la, d = object(), {"delineation": {}}
    ns["loaded_assessment"], ns["delin"] = (lambda: la), (lambda: d)
    ns["_auto_measure_key"] = lambda la_, d_: "site-1"
    monkeypatch.setattr(app.reference_support, "auto_strata", lambda la_, d_: {"m3": "Low gradient"})
    ns["_desktop"]["fill"] = ("site-1", {"m2": {"value": 7, "origin": "desktop", "engine": True}})
    ns["measured_values"].set({"m1": {"value": 4, "origin": "field", "note": "riffle", "photos": [{"id": "p"}]},
                               "m2": {"value": 9, "origin": "field"}})
    _new_scenario(ns, "Urban growth")
    ns["measured_values"].set({"m1": {"value": 30, "origin": "field"}})
    _new_scenario(ns, "Bank work", start=BASELINE_ID)                  # shown: Urban growth
    assert ns["measured_values"]()["m1"] == {"value": 4, "origin": "field", "note": "riffle"}   # no photos
    _new_scenario(ns, "Second visit", start="blank")
    assert ns["measured_values"]() == {"m2": {"value": 7, "origin": "desktop", "engine": True},
                                       "m3": {"stratum": "Low gradient", "stratumAuto": True}}
    ns["_desktop"]["fill"] = ("another site", {"m2": {"value": 1, "origin": "desktop"}})
    _new_scenario(ns, "Third visit", start="blank")
    assert ns["measured_values"]() == {"m3": {"stratum": "Low gradient", "stratumAuto": True}}  # never a stale fill
    sset = ns["_sc"]["set"]
    assert [s.name for s in sset.items] == ["Existing Conditions", "Urban growth", "Bank work", "Second visit",
                                            "Third visit"]
    assert sset.items[1].state["measured_values"]["m1"]["value"] == 30
    assert "hint=_ADD_HINT" in SRC and "Blank keeps only the desktop values." in SRC
    done = SRC.split("def _compute_done():", 1)[1].split("\n    def ", 1)[0]
    assert '_desktop["fill"] = (request_key, copy.deepcopy(res))' in done     # what Blank starts from


def test_save_and_open_restore_every_scenario_whichever_is_shown():
    ns = _helpers()
    ns["measured_values"].set({"m1": {"value": 4, "origin": "field"}})
    _new_scenario(ns, "Urban growth", "Build-out")             # the page copies, then shows the copy
    assert ns["measured_values"]() == {"m1": {"value": 4, "origin": "field"}}
    ns["measured_values"].set({"m1": {"value": 30, "origin": "field"}})
    assert ns["_sc"]["set"].current.name == "Urban growth"
    data = ns["_session_scenarios"]()
    assert data["items"][0]["state"] == {"measured_values": {"m1": {"value": 4, "origin": "field"}}}
    text = session.dump(DELIN, {}, data)
    # a fresh page opens the file
    st = session.load(text)
    fresh = _helpers()
    fresh["_restore_scenarios"](st["scenarios"])
    assert [s.name for s in fresh["_sc"]["set"].items] == ["Existing Conditions", "Urban growth"]
    assert fresh["measured_values"]()["m1"]["value"] == 30      # the scenario on screen when saved
    fresh["_switch_scenario"](BASELINE_ID)
    assert fresh["measured_values"]()["m1"]["value"] == 4


def test_desktop_values_reach_every_scenario_without_replacing_entries():
    ns = _helpers()
    ns["measured_values"].set({"m1": {"value": 4, "origin": "field"}})
    _new_scenario(ns, "Urban growth")
    ns["measured_values"].set({"m1": {"value": 30, "origin": "field"}})
    ns["_switch_scenario"](BASELINE_ID)
    res = {"m1": {"value": 99, "origin": "desktop"}, "m2": {"value": 7, "origin": "desktop"}}

    def prefill(mv):
        filled = False
        for mid, entry in res.items():
            cur = mv.get(mid) or {}
            if cur.get("value") in (None, "") and not cur.get("na"):
                mv[mid] = {**cur, **entry}
                filled = True
        return filled
    ns["_merge_into_stored"](prefill)
    stored = ns["_sc"]["set"].items[1].state["measured_values"]
    assert stored["m1"]["value"] == 30 and stored["m2"]["value"] == 7
    assert ns["scenario_rev"]() == 3                          # add + switch + the merge


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
    assert 'href="staf/staf.css?v=11"' in SRC
    for ext in ("pdf", "csv", "geojson"):
        assert f"{base}{{staf_web.scenario_suffix(_sc['set'])}}.{ext}" in SRC
    pdf = SRC.split("def dl_pdf():", 1)[1].split("@render", 1)[0]
    assert 'scenario=staf_web.report_scenario(_sc["set"])' in pdf


def test_each_report_names_its_scenario_and_compares_nothing(la, monkeypatch):
    from deep import report
    monkeypatch.setattr(report.reportmap, "pdf_flowable", lambda *a, **k: None)
    mv = _values(la, _low)
    sc = workbook.score(la, mv)
    named = _pdf_text(report.build_pdf(DELIN, la, mv, sc, scenario=("Restore riparian", "Replant the buffer")))
    plain = _pdf_text(report.build_pdf(DELIN, la, mv, sc))
    assert "Scenario: Restore riparian" in named and "Replant the buffer" in named
    assert "Scenario:" not in plain
    _report_wiring("deep-report")
