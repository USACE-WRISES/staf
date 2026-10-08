"""Scenarios in SFARI (owner, 2026-10-03): the assessment file keeps every scenario with its own
entries (the STAF assessment file, 2026-10-05); the workbook carries one calculator per scenario
behind a Summary tab; the assessment page wires the shared scenario chip."""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from sfari import calculator, session, workbook
from sfari._vendor.staf_workbook import assessment_file
from sfari._vendor.staf_workbook.model.scenarios import BASELINE_ID, ScenarioSet

APP = Path(__file__).resolve().parents[1]
SRC = (APP / "app.py").read_text(encoding="utf-8")
FIDS = [fid for fid, _n, _c in workbook.functions()]
DELIN = {"delineation": {"gnis_name": "Mink Brook", "reach_length_ft": 1000, "drainage_area_sqkm": 12.3,
                         "stream_order": 3, "snapped_lat": 43.6858, "snapped_lon": -72.2367}}


def _two():
    sset = ScenarioSet()
    sset.baseline.state = {"metric_scores": {}, "function_scores": dict((f, {"score": 8}) for f in FIDS)}
    alt = sset.add("Restore riparian", "Replant the buffer")
    alt.state = {"metric_scores": {}, "function_scores": dict((f, {"score": 12}) for f in FIDS[:5])}
    return sset


def test_the_file_keeps_every_scenario_with_its_entries():
    sset = _two()
    shown = sset.current.state                                 # the alternative, on screen
    text = session.dump(DELIN, {}, None, assessment_file.scenarios_block(sset, shown))
    raw = json.loads(text)
    assert raw["tool"] == "SFARI" and "function_scores" not in raw
    assert raw["scenarios"]["items"][0]["state"] == sset.baseline.state
    back = assessment_file.scenario_set(session.load(text)["scenarios"])
    assert [s.name for s in back.items] == ["Existing Conditions", "Restore riparian"]
    assert back.baseline.state["function_scores"][FIDS[0]]["score"] == 8
    assert back.items[1].state["function_scores"][FIDS[0]]["score"] == 12 and back.active == back.items[1].id


def test_a_file_without_alternatives_opens_as_existing_conditions_alone():
    text = session.dump(DELIN, {}, None, assessment_file.scenarios_block(ScenarioSet(), {"metric_scores": {}}))
    assert [i["id"] for i in json.loads(text)["scenarios"]["items"]] == [BASELINE_ID]
    assert [s.id for s in assessment_file.scenario_set(session.load(text)["scenarios"]).items] == [BASELINE_ID]


def test_workbook_has_a_calculator_per_scenario_behind_a_summary():
    sset = _two()
    data = workbook.build(DELIN, [(s, s.state) for s in sset.items], today=dt.date(2026, 10, 3))
    z = zipfile.ZipFile(io.BytesIO(data))
    book = z.read("xl/workbook.xml").decode("utf-8")
    names = re.findall(r'<sheet\b[^>]*name="([^"]+)"', book)
    assert names[:4] == ["Summary", "Existing Conditions", "Restore riparian", "ReferenceCurves"]
    assert "fullCalcOnLoad=\"1\"" in book
    sheets = dict((n, None) for n in names)
    assert "S2 Instructions Block" in sheets                    # the alternative's chart data
    # the template itself is never changed
    assert calculator.blank_bytes() == calculator.TEMPLATE_PATH.read_bytes()


GROUPS = ["Index", "Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology"]


@pytest.mark.parametrize("n_alt", [0, 1, 3])
def test_the_summary_is_the_compare_table_on_its_side(n_alt):
    """The workbook builds (the app would otherwise fall back to the single calculator without a
    word) and its Summary reads like the Compare dialog: a row per measure under its group, a
    column per scenario, each alternative followed by its change."""
    openpyxl = pytest.importorskip("openpyxl")
    sset = ScenarioSet()
    sset.baseline.state = {"metric_scores": {}, "function_scores": dict((f, {"score": 8}) for f in FIDS)}
    for k in range(n_alt):
        alt = sset.add(f"Alternative {k + 1}", "Bank & bed work <phase 1>" if k == 0 else "")
        alt.state = {"metric_scores": {}, "function_scores": dict((f, {"score": 9 + k}) for f in FIDS[:4])}
    data = workbook.build(DELIN, [(s, s.state) for s in sset.items], today=dt.date(2026, 10, 3))
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert wb.sheetnames[0] == "Summary"
    ws = wb["Summary"]
    head = next(c.row for c in ws["A"] if c.value == "Index") - 1
    expect = [None, "Existing Conditions"]
    for k in range(n_alt):
        expect += [f"Alternative {k + 1}", "Change"]
    assert [ws.cell(head, c).value for c in range(1, len(expect) + 2)] == expect + [None]
    labels = [ws.cell(r, 1).value for r in range(head + 1, ws.max_row + 1)]
    assert [x for x in labels if x in GROUPS] == GROUPS
    assert len([x for x in labels if x not in GROUPS]) == 4 + len(FIDS)
    assert len(list(ws.conditional_formatting)) == n_alt
    if n_alt <= 1:
        assert ws.page_setup.orientation == "portrait" and str(ws.page_setup.fitToHeight) == "1"


def test_the_page_wires_the_shared_chip():
    assert 'ui.output_ui("scenario_bar"), ui.output_ui("rollup_rail")' in SRC
    assert "@reactive.event(input.staf_scenario_evt)" in SRC
    assert "@reactive.event(input.staf_sc_save)" in SRC and "@reactive.event(input.staf_sc_delete)" in SRC
    assert 'href="staf/staf.css?v=11"' in SRC and 'src="staf/scenarios.js?v=2"' in SRC
    assert "_reset_scenarios()" in SRC.split("def _invalidate_selection():", 1)[1].split("def ", 1)[0]
    assert "scenario_nonce()" in SRC.split("def fn_panel():", 1)[1].split("def ", 1)[0]
    save = SRC.split("def save_session():", 1)[1].split("@render", 1)[0]
    assert "_session_scenarios()" in save
    assert "yield _workbook_bytes()" in SRC


class _Value:
    def __init__(self, value=None):
        self.value = value

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = value


def _page():
    """The scenario code of SFARI's server, run on its own (the page's reactive values stubbed)."""
    import ast
    import copy
    from contextlib import nullcontext
    from types import SimpleNamespace

    import app
    server = next(n for n in ast.parse(SRC).body if isinstance(n, ast.FunctionDef) and n.name == "server")
    ns = {**vars(app), "reactive": SimpleNamespace(isolate=nullcontext), "metric_scores": _Value({}),
          "function_scores": _Value({}), "scenario_rev": _Value(0), "scenario_nonce": _Value(0),
          "_sc": {"set": ScenarioSet(), "dialog": None}, "shown": []}
    ns["input"] = SimpleNamespace(staf_sc_name=lambda: ns["typed"][0], staf_sc_desc=lambda: "",
                                  staf_sc_from=lambda: ns["typed"][1])
    ns["ui"] = SimpleNamespace(modal_remove=lambda: None, modal_show=lambda m: ns["shown"].append(str(m)))
    for name in ("_bump_scenarios", "_entries_now", "_show_entries", "_switch_scenario", "_scenario_dialog",
                 "_scenario_save"):
        node = copy.deepcopy(next(n for n in ast.walk(server) if isinstance(n, ast.FunctionDef) and n.name == name))
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), app.__file__, "exec"), ns)
    return ns


def _add(ns, name, start):
    """What the Add a scenario dialog's Add does on the page."""
    ns["_sc"]["dialog"], ns["typed"] = "new", (name, start)
    ns["_scenario_save"]()


def test_add_starts_from_the_chosen_scenario_or_blank():
    """Owner, 2026-10-05: Add asks where the scenario starts, Existing Conditions by default."""
    ns = _page()
    rated = {"m1": {"likert": 4, "note": "riffles", "photos": [{"id": "p1"}]}}
    ns["metric_scores"].set(rated)
    _add(ns, "Restore riparian", BASELINE_ID)
    assert ns["metric_scores"]() == {"m1": {"likert": 4, "note": "riffles"}}    # a copy, photos stay behind
    ns["metric_scores"].set({"m1": {"likert": 9}})                                 # the alternative changes
    _add(ns, "Bank work", BASELINE_ID)                                             # shown: Restore riparian
    assert ns["metric_scores"]()["m1"]["likert"] == 4                              # Existing Conditions'
    _add(ns, "Second visit", "blank")
    assert ns["metric_scores"]() == {} and ns["function_scores"]() == {}
    sset = ns["_sc"]["set"]
    assert [s.name for s in sset.items] == ["Existing Conditions", "Restore riparian", "Bank work", "Second visit"]
    assert sset.items[1].state["metric_scores"]["m1"]["likert"] == 9               # kept when Bank work was added
    _add(ns, "Ghost", "s99")                                                       # gone since: Existing Conditions
    assert ns["metric_scores"]()["m1"]["likert"] == 4
    _add(ns, "Ghost", "blank")                                                     # a taken name
    assert len(sset.items) == 5 and 'value="blank" selected=""' in ns["shown"][-1]   # the choice is kept
    assert "Blank starts with no scores." in ns["shown"][-1]


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


def test_each_report_names_its_scenario_and_compares_nothing(monkeypatch):
    from sfari import report, scoring
    monkeypatch.setattr(report.reportmap, "pdf_flowable", lambda *a, **k: None)
    named = _pdf_text(report.build_pdf(DELIN, {}, {}, {}, scoring.score_assessment({}),
                                       scenario=("Restore riparian", "Replant the buffer")))
    plain = _pdf_text(report.build_pdf(DELIN, {}, {}, {}, scoring.score_assessment({})))
    assert "Scenario: Restore riparian" in named and "Replant the buffer" in named
    assert "Scenario:" not in plain
    _report_wiring("sfari-report")
