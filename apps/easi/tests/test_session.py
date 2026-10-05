"""The EASI assessment file (owner, 2026-10-05): EASI's header has the New, Open, Save, About and
Help of SFARI and DEEP, and Save writes the STAF assessment file they write. Open restores the
site, its screening, the notes and every scenario's ratings with no network call, as the page left
them; a file from another tool, or one EASI cannot score, opens nothing."""
from __future__ import annotations

import ast
import copy
import json
import math
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import calculator_cases as cc
from easi import method_package, session as session_io
from easi._vendor.staf_workbook import assessment_file
from easi._vendor.staf_workbook.model.scenarios import BASELINE_ID, ScenarioSet

app = pytest.importorskip("app")
SRC = Path(app.__file__).read_text(encoding="utf-8")
SERVER = next(n for n in ast.parse(SRC).body if isinstance(n, ast.FunctionDef) and n.name == "server")
HR_SITE = {"network": "nhdplus-hr", "nhdplusid": 24000800011817, "snapLat": 40.1002, "snapLon": -83.1003,
           "snapDistFt": 12.5}
ANCHOR = {"anchorKind": "v2Direct", "clickedPoint": {"lat": 40.1, "lon": -83.1},
          "scoredReach": {"network": "nhdplus-v2", "comid": 1234567, "gnisName": "Test Creek",
                          "snapLat": 40.1001, "snapLon": -83.1001, "snapDistFt": 3.0},
          "selectedSite": HR_SITE}
EROM = {f"qe_{m:02d}": 10.0 + m for m in range(1, 13)}
DELIN = {"status": "ok", "siteAnchor": ANCHOR, "input": {"lat": 40.1, "lon": -83.1, "reach_length_ft": 1000},
         "delineation": {"comid": 1234567, "gnis_name": "Test Creek", "huc8": "05060001", "huc12": None,
                         "drainage_area_sqkm": 50.0, "snapped_lat": 40.1001, "snapped_lon": -83.1001,
                         "reach_length_ft": 1000, "warnings": []},
         "watershed_geojson": {"type": "FeatureCollection", "features": []},
         "reach_geojson": {"type": "FeatureCollection", "features": []},
         "ctx_inputs": {"lat": 40.1001, "lon": -83.1001, "comid": 1234567, "erom": EROM, "siteAnchor": ANCHOR}}
METHOD = {"method_version": "b2e3033116e3", "alternative_id": "alt-2",
          "alternative_name": "Alternative 2: NARS-9 references", "criteria_set": "regional"}


@pytest.fixture(scope="module")
def report():
    return cc.score_case({"record": {}})


@pytest.fixture(scope="module")
def base(report):
    return session_io.screened(DELIN, "050600010101", report)


def _same(a, b) -> bool:
    """Equal as written to a file (a report may hold NaN, which never equals itself)."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _two(report):
    m01, m13 = cc.FUNCTIONS["m01"], cc.FUNCTIONS["m13"]
    sset = ScenarioSet()
    sset.baseline.state = {"overrides": {m01: "Poor"}}
    sset.add("Restore riparian", "Replant the buffer")
    return sset, {"overrides": {m13: "Good"}, "observed": {}, "xsSel": None}


# ---- the file ------------------------------------------------------------------------------
def test_the_file_has_the_structure_every_tool_writes(base):
    sset, shown = _two(base["report"])
    raw = json.loads(session_io.dump(DELIN, base, {"m": "bank slumping"}, METHOD,
                                     assessment_file.scenarios_block(sset, shown)))
    assert list(raw) == ["format", "formatVersion", "tool", "savedAt", "delineation", "toolData", "scenarios"]
    assert raw["tool"] == "EASI" and raw["delineation"] == DELIN          # ctx_inputs kept: it can screen again
    assert list(raw["toolData"]) == ["screening", "notes", "method"]
    assert raw["toolData"]["screening"]["huc12"] == "050600010101"
    assert raw["toolData"]["notes"] == {"m": "bank slumping"} and raw["toolData"]["method"] == METHOD
    assert [i["state"] for i in raw["scenarios"]["items"]] == [sset.baseline.state, shown]


def test_open_restores_the_screening_exactly_as_the_page_built_it(base, report):
    st = session_io.load(session_io.dump(DELIN, base, {}, METHOD, None))
    again = session_io.screened(st["delineation"], st["screening"]["huc12"], st["screening"]["report"])
    assert _same(again, base)
    assert again["eromMonthly"] == [10.0 + m for m in range(1, 13)] and "ctx_inputs" not in again
    assert again["delineation"]["huc12"] == "050600010101" and DELIN["delineation"]["huc12"] is None


def test_a_site_saved_before_its_screening_opens_without_one():
    st = session_io.load(session_io.dump(DELIN, None, {}, METHOD, None))
    assert st["screening"] is None and st["delineation"]["ctx_inputs"]["erom"] == EROM


@pytest.mark.parametrize("text,reason", [
    (assessment_file.dump("SFARI", {}, {}, None), "Open it in SFARI"),
    (json.dumps({"schemaVersion": 2, "method": "DEEP", "measured_values": {}}), "Open it in DEEP"),
    (json.dumps({"schemaVersion": 1, "delineation": {}}), "not a STAF assessment file"),
    (assessment_file.dump("EASI", DELIN, {"screening": {"huc12": None}}, None), "no report"),
    (assessment_file.dump("EASI", {}, {"screening": {"report": {"metricRows": []}}}, None), "no delineation"),
    (assessment_file.dump("EASI", DELIN, {"screening": {"report": {"metricRows": [{"rating": "Good"}]}}}, None),
     "cannot be scored"),
])
def test_a_file_easi_cannot_use_is_refused_with_a_reason(text, reason):
    with pytest.raises(assessment_file.AssessmentFileError, match=reason):
        session_io.load(text)


def test_notes_keep_only_text():
    raw = assessment_file.dump("EASI", {}, {"notes": {"a": "kept", "b": " ", "c": 4}, "method": "x"}, None)
    st = session_io.load(raw)
    assert st["notes"] == {"a": "kept"} and st["method"] == {}


@pytest.mark.parametrize("anchor,point", [
    (ANCHOR, (40.1002, -83.1003, 12.5, 1234567)),                                       # the HR site
    ({"anchorKind": "hrSurrogate", "scoredReach": {"comid": 7, "snapLat": 40.0, "snapLon": -83.0},
      "clickedStream": {"snapLat": 40.2, "snapLon": -83.2, "snapDistFt": 4.0}}, (40.2, -83.2, 4.0, 7)),
    ({"anchorKind": "v2Direct", "scoredReach": {"comid": "7", "snapLat": 40.0, "snapLon": -83.0}},
     (40.0, -83.0, 0.0, 7)),
    ({"scoredReach": {"comid": 7}}, (40.1001, -83.1001, 0.0, 7)),                       # the delineation's snap
    ({"scoredReach": {"comid": True, "snapLat": 40.0, "snapLon": -83.0}}, None),
    ({"scoredReach": {"comid": 0, "snapLat": 40.0, "snapLon": -83.0}}, None),
    ({"scoredReach": {"comid": 7, "snapLat": math.nan, "snapLon": -83.0}}, None),
    ({"scoredReach": {"comid": 7, "snapLat": 95.0, "snapLon": -83.0}}, None),
    ({}, None),
])
def test_the_saved_site_is_the_pin_on_the_picked_stream_and_its_source(anchor, point):
    assert session_io.saved_point(dict(DELIN, siteAnchor=anchor)) == point


def test_a_routed_site_draws_its_connector_and_a_covered_one_none():
    routed = {"clickedStream": {"snapLat": 40.2, "snapLon": -83.2}, "scoredReach": {"snapLat": 40.0, "snapLon": -83.0}}
    seg = session_io.route_segment(routed)
    assert seg["features"][0]["geometry"]["coordinates"] == [[-83.2, 40.2], [-83.0, 40.0]]
    assert session_io.route_segment(ANCHOR) is None and session_io.route_segment(None) is None
    assert "seg = session_io.route_segment(anchor)" in SRC.split("def _route_done():", 1)[1].split("def ", 1)[0]


def test_another_method_is_named_when_the_screening_opens():
    assert session_io.method_changed(METHOD, dict(METHOD)) is None
    assert session_io.method_changed({}, METHOD) is None
    text = session_io.method_changed(METHOD, dict(METHOD, method_version="0123456789ab", alternative_name="Alt 3"))
    assert "b2e3033116e3" in text and "0123456789ab" in text and "screen the site again" in text
    assert "—" not in text


def test_the_height_boxes_never_count_as_unsaved_work():
    st = {"overrides": {"m": "Good"}, "xsHeights": {"bankfull_m": 0.6, "lowbank_m": 0.5}}
    assert "xsHeights" not in session_io.fingerprint_state(st)
    assert session_io.fingerprint_state(st) == session_io.fingerprint_state({"overrides": {"m": "Good"}})


def test_the_file_code_stays_out_of_the_method_identity():
    root = method_package.package_root()
    assert (root / "session.py").resolve() not in {p.resolve() for p in method_package.acquisition_sources()}
    assert "session.py" not in method_package.DIGEST_SOURCES


# ---- the page --------------------------------------------------------------------------------
class _Value:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = value


def _page(path=None):
    """The page's own Save and Open closures, run outside Shiny with a page state of its own."""
    notices = []
    ns = {**vars(app), "reactive": SimpleNamespace(isolate=nullcontext),
          "base_result": _Value(None), "delin": _Value(None), "_notes": _Value({}),
          "_overrides": _Value({}), "_observed": _Value({}), "_geom_owned": _Value(set()), "_geom_text": _Value({}),
          "_geom_scoring": _Value({}), "_geom_reason": _Value("edited"), "_xs_sel": _Value(None),
          "_xs_unit_prev": _Value("ft"), "_xs_heights": _Value(None), "_xs_echo": {"want": None, "was": None},
          "scenario_rev": _Value(0), "scenario_nonce": _Value(0), "_sc": {"set": ScenarioSet(), "dialog": None},
          "_map_pick": {"generation": 7}, "_analysis_runs": {"delineate": 7, "assess": 7, "delin": 7},
          "snapped_point": _Value((41.0, -84.0, 1.0, 999)), "pending_anchor": _Value({"old": True}),
          "anchor_error": _Value("old"), "scored_reach": _Value({"comid": 999}),
          "source_lookup": _Value({"status": "ready", "generation": 7, "comid": 999, "point": (41.0, -84.0)}),
          "stage": _Value("old"), "current_fn": _Value(3), "current_step": _Value(app.STEP_IDENTIFY),
          "app_mode": _Value("single"), "_saved_fp": _Value(None), "_HAS_MAP": False,
          "_remove_layer": lambda key: None, "_cancel_report": lambda: None, "notices": notices}
    ns["current_overrides"] = lambda: dict(ns["_overrides"]())
    ns["_invalidate_analysis"] = lambda: (ns["delin"].set(None), ns["base_result"].set(None))
    ns["input"] = SimpleNamespace(load_session=lambda: [{"datapath": str(path)}],
                                  xs_bankfull=lambda: None, xs_lowbank=lambda: None)
    ns["ui"] = SimpleNamespace(notification_show=lambda *a, **k: notices.append((a[0], k.get("type"))),
                               update_numeric=lambda *a, **k: None, update_switch=lambda *a, **k: None)
    for name in ("_score_state", "_bump_scenarios", "_xs_per_m", "_capture_state", "_xs_is_echo", "_show_heights",
                 "_apply_state", "_xs_block", "_xs_candidates", "_xs_cross", "_xs_sel_idx", "_xs_default_sel",
                 "_source_ready", "_session_scenarios", "_restore_scenarios", "_work_fp", "_load_session"):
        node = copy.deepcopy(next(n for n in ast.walk(SERVER) if isinstance(n, ast.FunctionDef) and n.name == name))
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), app.__file__, "exec"), ns)
    return ns


def test_save_then_open_gives_back_the_same_work(tmp_path, base):
    m01, m13 = cc.FUNCTIONS["m01"], cc.FUNCTIONS["m13"]
    saved = _page()
    saved["delin"].set(DELIN)
    saved["base_result"].set(base)
    saved["_notes"].set({m01: "riprap on both banks"})
    sset = saved["_sc"]["set"]
    saved["_overrides"].set({m01: "Poor"})
    sset.set_state(sset.active, saved["_capture_state"]())
    sset.add("Restore riparian", "Replant the buffer")              # the copy is shown, then re-rated
    saved["_overrides"].set({m13: "Good"})
    path = tmp_path / "easi-assessment.json"
    path.write_text(session_io.dump(saved["delin"](), saved["base_result"](), dict(saved["_notes"]()), METHOD,
                                    saved["_session_scenarios"]()), encoding="utf-8")

    page = _page(path)
    page["_load_session"]()
    assert page["_map_pick"]["generation"] == 8                       # a pick still running is dropped
    assert page["snapped_point"]() == (40.1002, -83.1003, 12.5, 1234567)
    assert page["pending_anchor"]() == ANCHOR and page["anchor_error"]() is None
    assert page["scored_reach"]() == {"comid": 1234567, "name": "Test Creek"}
    assert page["_source_ready"]()                                     # no StreamCat lookup runs again
    assert page["delin"]() == DELIN
    assert _same(page["base_result"](), base)
    assert page["_analysis_runs"]["delin"] == page["_analysis_runs"]["assess"] == 8   # no screening starts
    assert page["_notes"]() == {m01: "riprap on both banks"}
    opened = page["_sc"]["set"]
    assert [s.name for s in opened.items] == ["Existing Conditions", "Restore riparian"]
    assert opened.current.name == "Restore riparian" and page["_overrides"]() == {m13: "Good"}
    assert opened.baseline.state["overrides"] == {m01: "Poor"}
    assert page["current_step"]() == app.STEP_ASSESS and page["current_fn"]() == 0 and page["stage"]() == ""
    assert page["_saved_fp"]() == page["_work_fp"]() == saved["_work_fp"]()     # nothing unsaved, same work
    assert ("Assessment loaded. Resuming.", "message") in page["notices"]


def test_a_site_saved_before_its_screening_opens_on_the_basin(tmp_path):
    path = tmp_path / "easi-assessment.json"
    path.write_text(session_io.dump(DELIN, None, {}, METHOD, None), encoding="utf-8")
    page = _page(path)
    page["_load_session"]()
    assert page["base_result"]() is None and page["delin"]() == DELIN
    assert page["_analysis_runs"]["delin"] == 8 and page["_analysis_runs"]["assess"] is None   # Run screening works
    assert page["current_step"]() == app.STEP_BASIN and page["_source_ready"]()


@pytest.mark.parametrize("content", ["not json", assessment_file.dump("DEEP", {}, {}, None)])
def test_a_refused_file_leaves_the_page_as_it_was(tmp_path, content):
    path = tmp_path / "other.json"
    path.write_text(content, encoding="utf-8")
    page = _page(path)
    page["_load_session"]()
    assert page["_map_pick"]["generation"] == 7 and page["snapped_point"]() == (41.0, -84.0, 1.0, 999)
    assert page["notices"][0][0].startswith("Could not load assessment: ") and page["notices"][0][1] == "error"


def test_new_asks_first_while_there_is_work():
    shown, starts = [], []
    scope = {**vars(app), "reactive": SimpleNamespace(isolate=nullcontext), "_cancel_report": lambda: None,
             "delin": lambda: DELIN, "base_result": lambda: None, "_start_over": lambda: starts.append(1),
             "ui": SimpleNamespace(modal_show=shown.append)}
    for name in ("_new_analysis", "_confirm_new"):
        node = copy.deepcopy(next(n for n in ast.walk(SERVER) if isinstance(n, ast.FunctionDef) and n.name == name))
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), app.__file__, "exec"), scope)
    scope["_new_analysis"]()
    assert starts == [] and "Start a new assessment?" in str(shown[0]) and "confirm_new" in str(shown[0])
    scope["_confirm_new"]()
    assert starts == [1]


def test_the_page_wires_save_open_about_and_help_like_sfari_and_deep():
    assert "staf_web.nav_actions(" in SRC.split("def _nav_actions(", 1)[1].split("\ndef ", 1)[0]
    assert '@render.download(filename="easi-assessment.json")' in SRC
    save = SRC.split("def save_session():", 1)[1].split("@reactive", 1)[0]
    assert "_saved_fp.set(_work_fp())" in save and "_session_scenarios()" in save
    load = SRC.split("def _load_session():", 1)[1].split("\n    # ===", 1)[0]
    assert load.index("_restore_scenarios(") < load.index("_saved_fp.set(_work_fp())")
    assert 'staf_web.info_dialog(\n            "About EASI",' in SRC
    assert 'staf_web.info_dialog(\n            "How to use EASI",' in SRC
    assert "_GUIDE_LINE" in SRC.split("def _about():", 1)[1].split("@reactive", 1)[0]
    assert "_GUIDE_LINE" in SRC.split("def _help():", 1)[1].split("@reactive", 1)[0]
    assert "nav_batch" not in SRC.split("def _nav_actions(", 1)[1].split("\ndef ", 1)[0]
    assert "dirty = (fp is not None and fp != _saved_fp())" in SRC
