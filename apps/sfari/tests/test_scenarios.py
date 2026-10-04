"""Scenarios in SFARI (owner, 2026-10-03): the session file keeps Existing Conditions at the top
level and the alternatives in an additive key; the workbook carries one calculator per scenario
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


def test_session_keeps_existing_conditions_at_the_top_level():
    sset = _two()
    ec = sset.baseline.state
    text = session.dump(DELIN, ec["metric_scores"], ec["function_scores"], {}, None, scenarios=sset.to_json())
    raw = json.loads(text)
    assert raw["function_scores"] == ec["function_scores"] and raw["schemaVersion"] == 1
    st = session.load(text)
    back = ScenarioSet.from_json(st["scenarios"], baseline_state={"function_scores": st["function_scores"]})
    assert [s.name for s in back.items] == ["Existing Conditions", "Restore riparian"]
    assert back.items[1].state["function_scores"][FIDS[0]]["score"] == 12


def test_a_file_without_scenarios_still_loads_and_writes_no_key():
    text = session.dump(DELIN, {}, {}, {}, None, scenarios=ScenarioSet().to_json())
    assert "scenarios" not in json.loads(text)
    assert [s.id for s in ScenarioSet.from_json(session.load(text)["scenarios"]).items] == [BASELINE_ID]


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


def test_the_page_wires_the_shared_chip():
    assert 'ui.output_ui("scenario_bar"), ui.output_ui("rollup_rail")' in SRC
    assert "@reactive.event(input.staf_scenario_evt)" in SRC
    assert "@reactive.event(input.staf_sc_save)" in SRC and "@reactive.event(input.staf_sc_delete)" in SRC
    assert 'href="staf/staf.css?v=4"' in SRC and 'src="staf/scenarios.js?v=1"' in SRC
    assert "_reset_scenarios()" in SRC.split("def _invalidate_selection():", 1)[1].split("def ", 1)[0]
    assert "scenario_nonce()" in SRC.split("def fn_panel():", 1)[1].split("def ", 1)[0]
    save = SRC.split("def save_session():", 1)[1].split("@render", 1)[0]
    assert "scenarios=scen" in save
    assert "yield _workbook_bytes()" in SRC


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
    from sfari import report, scoring
    monkeypatch.setattr(report.reportmap, "pdf_flowable", lambda *a, **k: None)
    named = _pdf_text(report.build_pdf(DELIN, {}, {}, {}, scoring.score_assessment({}),
                                       scenario=("Restore riparian", "Replant the buffer")))
    plain = _pdf_text(report.build_pdf(DELIN, {}, {}, {}, scoring.score_assessment({})))
    assert "Scenario: Restore riparian" in named and "Replant the buffer" in named
    assert "Scenario:" not in plain
    _report_wiring("sfari-report")
