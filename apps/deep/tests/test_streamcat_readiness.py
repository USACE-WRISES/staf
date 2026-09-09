"""Strict StreamCat readiness and stale-work rejection in the real app closures."""
from __future__ import annotations

import ast
import asyncio
import copy
from contextlib import nullcontext
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

app = pytest.importorskip("app")
SOURCE = ast.parse(Path(app.__file__).read_text(encoding="utf-8"))
SERVER = next(n for n in SOURCE.body if isinstance(n, ast.FunctionDef) and n.name == "server")
POINT = (43.69, -72.2785, 0.0, 1234)
ANCHOR = {"anchorKind": "v2Direct", "scoredReach": {"comid": 9327042}}


class Value:
    def __init__(self, value=None):
        self.value = value

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = value


def function(name, scope):
    fn = copy.deepcopy(next(n for n in ast.walk(SERVER)
                            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name))
    fn.decorator_list = []
    exec(compile(ast.Module(body=[fn], type_ignores=[]), app.__file__, "exec"), scope)
    return scope[name]


def state_scope():
    notices, removed, launches = [], [], []
    scope = {**vars(app),
             "reactive": SimpleNamespace(isolate=nullcontext, invalidate_later=lambda _: None),
             "_map_pick": {"generation": 7},
             "source_lookup": Value({"status": "ready", "generation": 7}),
             "snapped_point": Value(POINT), "site_anchor": Value(copy.deepcopy(ANCHOR)),
             "evidence_reach": Value(None), "_delin_generation": Value(7),
             "_lookup_progress": {}, "_lookup_request": {}, "_no_watershed": {},
             "_engine_generation": {"value": None}, "engine_state": Value({"status": "idle"}),
             "stage": Value(""), "delin": Value(None), "flow_geojson": Value(None),
             "_layers": {}, "_remove_layer": lambda key: removed.append(key),
             "scored_task": lambda *args: launches.append(args),
             "ui": SimpleNamespace(notification_show=lambda *a, **k: notices.append(a),
                                   notification_remove=lambda *a: None, modal_remove=lambda: None),
             "notices_seen": notices, "removed": removed, "launches": launches}
    for name in ("_set_lookup", "_source_ready", "_current_delineation", "_with_anchor", "_clear_anchor_state",
                 "_retry_lookup", "_streamcat_lookup_ui", "_request_report"):
        function(name, scope)
    return scope


@pytest.mark.parametrize("status", ["idle", "snapping", "finding", "retrying", "no_match", "failed"])
def test_only_ready_current_source_can_unlock_new_work(status):
    assert not app._lookup_is_ready({"status": status, "generation": 7}, POINT, ANCHOR, 7)


@pytest.mark.parametrize("point,anchor,generation", [
    (None, ANCHOR, 7), ((float("nan"), -72), ANCHOR, 7), (POINT, ANCHOR, 8),
    (POINT, {}, 7), (POINT, {"scoredReach": {"comid": 0}}, 7),
    (POINT, {"scoredReach": {"comid": "not-a-comid"}}, 7),
])
def test_ready_flag_alone_cannot_bypass_valid_point_source_and_generation(point, anchor, generation):
    assert not app._lookup_is_ready({"status": "ready", "generation": 7}, point, anchor, generation)
    assert app._lookup_is_ready({"status": "ready", "generation": 7}, POINT, ANCHOR, 7)


def test_delineation_readiness_also_checks_the_result_generation_and_comid():
    scope = state_scope()
    d = {"siteAnchor": ANCHOR}
    assert scope["_source_ready"](d)
    scope["_delin_generation"].set(6)
    assert not scope["_source_ready"](d)
    scope["_delin_generation"].set(7)
    assert not scope["_source_ready"]({"siteAnchor": {"scoredReach": {"comid": 5}}})


def test_server_delineate_rejects_forged_click_until_ready():
    scope = state_scope()
    launches = []
    scope.update(input=SimpleNamespace(reach_ft=lambda: 800, lat=lambda: 1, lon=lambda: 2),
                 _launch_engine=lambda *args: launches.append(args))
    start = function("_start_delineate", scope)
    scope["source_lookup"].set({"status": "failed", "generation": 7})
    start()
    assert launches == [] and scope["notices_seen"]
    scope["source_lookup"].set({"status": "ready", "generation": 7})
    start()
    assert launches == [(POINT[0], POINT[1], 800.0)]


@pytest.mark.parametrize("error,expected", [("snap_service_error", "failed"), ("no_stream_found", "no_match")])
def test_failed_lookup_is_persistent_and_keeps_the_selected_point(error, expected):
    scope = state_scope()
    scope["anchor_task"] = SimpleNamespace(result=lambda: (7, {"error": error, "detail": "fixture detail"}))
    function("_anchor_done", scope)()
    assert scope["snapped_point"]() == POINT
    assert scope["source_lookup"]()["status"] == expected
    assert scope["site_anchor"]() is None and not scope["_source_ready"]()
    assert "marker" not in scope["removed"] and scope["launches"] == []


def test_lookup_becomes_ready_before_optional_geometry_fetch():
    scope = state_scope()
    scope.update(anchor_task=SimpleNamespace(result=lambda: (7, {"anchor": ANCHOR})),
                 _draw_anchor=lambda res: scope["site_anchor"].set(res["anchor"]),
                 network_display=SimpleNamespace(feature_by_id=lambda *args: None))
    calls = []
    scope["scored_task"] = lambda *args: calls.append((args, scope["_source_ready"]()))
    function("_anchor_done", scope)()
    assert calls == [((9327042, 7), True)]
    scope["scored_task"] = SimpleNamespace(result=lambda: (_ for _ in ()).throw(RuntimeError("geometry down")))
    function("_scored_done", scope)()
    assert scope["_source_ready"]()


def test_retry_reuses_point_in_new_generation_without_moving_marker():
    scope = state_scope()
    scope["source_lookup"].set({"status": "failed", "generation": 7})
    scope["_lookup_request"].update(lat=POINT[0], lon=POINT[1], hit=POINT, generation=7)
    starts = []
    scope["_start_lookup"] = lambda *args: starts.append(args)
    function("_retry_streamcat", scope)()
    assert scope["_map_pick"]["generation"] == scope["_delin_generation"]() == 8
    assert starts == [(POINT[0], POINT[1], POINT)]
    assert scope["snapped_point"]() == POINT and scope["removed"] == []


@pytest.mark.parametrize("task", ["click_snap_task", "coord_snap_task"])
def test_unexpected_snap_exception_completes_as_failure(task):
    async def sync(fn):
        return fn()
    scope = state_scope()
    scope.update(anyio=SimpleNamespace(to_thread=SimpleNamespace(run_sync=sync)),
                 hr_site=SimpleNamespace(snap_point=lambda *args: (_ for _ in ()).throw(RuntimeError("offline"))))
    result = asyncio.run(function(task, scope)(POINT[0], POINT[1], 7))
    assert result == (7, {"error": "snap_service_error", "detail": "offline"})


def test_worker_progress_is_generation_scoped_without_reactive_reads():
    async def sync(fn):
        return fn()
    scope = state_scope()
    def resolve(*args, progress, **kwargs):
        progress({"status": "retrying", "attempt": 2})
        return {"anchor": ANCHOR}
    scope.update(anyio=SimpleNamespace(to_thread=SimpleNamespace(run_sync=sync)),
                 comid_anchor=SimpleNamespace(resolve=resolve))
    result = asyncio.run(function("anchor_task", scope)(POINT[0], POINT[1], POINT, None, 6))
    assert result == (6, {"anchor": ANCHOR})
    assert scope["_lookup_progress"] == {6: {"status": "retrying", "attempt": 2}}
    assert scope["source_lookup"]()["status"] == "ready"


def test_source_attach_cannot_mix_generations_and_canonicalizes_legacy_comids():
    scope = state_scope()
    d = {"siteAnchor": {}, "ctx_inputs": {"comid": 99}, "delineation": {"comid": 99}}
    assert scope["_with_anchor"](d, 6) is d
    out = scope["_with_anchor"](d, 7)
    assert out["ctx_inputs"]["comid"] == out["delineation"]["comid"] == 9327042
    assert out["siteAnchor"] == ANCHOR and d["ctx_inputs"]["comid"] == 99


def test_stale_engine_result_cannot_offer_fallback_or_replace_state():
    scope = state_scope()
    scope["engine_task"] = SimpleNamespace(status=lambda: "success", result=lambda: {
        "generation": 6, "record": {"status": "failed", "reason": "offline"}})
    scope["_offer_no_watershed"] = lambda *args: pytest.fail("stale fallback offered")
    function("_engine_done", scope)()
    assert scope["engine_state"]() == {"status": "idle"}


def test_stale_fallback_button_cannot_use_previous_sites_anchor():
    scope = state_scope()
    scope["_no_watershed"].update(generation=6, anchor=ANCHOR)
    scope["pipeline"] = SimpleNamespace(delineate_without_watershed=lambda *a: pytest.fail("stale continuation"))
    function("_use_streamcat", scope)()
    assert scope["delin"]() is None


@pytest.mark.parametrize("generation,key", [(6, "current"), (7, "previous-assessment")])
def test_late_compute_cannot_fill_values_after_new_pick_or_assessment(generation, key):
    scope = state_scope()
    scope.update(compute_task=SimpleNamespace(status=lambda: "success", result=lambda: (
        generation, key, {"metric": {"value": 12}})),
        delin=Value({"siteAnchor": ANCHOR}), loaded_assessment=Value(object()),
        measured_values=Value({}), _auto_measure_key=lambda *args: "current")
    function("_compute_done", scope)()
    assert scope["measured_values"]() == {}


@pytest.mark.parametrize("status", ["finding", "failed", "no_match"])
def test_auto_compute_waits_for_successful_source_resolution(status):
    scope = state_scope()
    scope.update(current_step=Value(app.STEP_MEASURE),
                 loaded_assessment=Value(object()), delin=Value({"siteAnchor": ANCHOR}),
                 compute_task=lambda *args, **kwargs: pytest.fail("compute started before ready"))
    scope["source_lookup"].set({"status": status, "generation": 7})
    function("_maybe_compute", scope)()


@pytest.mark.parametrize("d,expected", [
    ({"delineation": {"snapped_lat": 43.69, "snapped_lon": -72.2785}}, POINT[:2]),
    ({"siteAnchor": {"anchorKind": "hrSurrogate", "clickedStream": {"snapLat": 43.7, "snapLon": -72.3},
                     "scoredReach": {"snapLat": 43.0, "snapLon": -72.0}}}, (43.7, -72.3)),
    ({"siteAnchor": {"anchorKind": "hrSurrogate", "scoredReach": {"snapLat": 43.0, "snapLon": -72.0}}}, None),
    ({}, None),
])
def test_import_point_never_substitutes_a_downstream_source_for_missing_assessment_point(d, expected):
    point = app._saved_lookup_point(d)
    assert (point[:2] if point else None) == expected


@pytest.mark.parametrize("legacy", [False, True])
def test_import_restores_current_point_and_source_atomically(tmp_path, legacy):
    d = {"delineation": {"snapped_lat": POINT[0], "snapped_lon": POINT[1], "comid": 9327042}}
    if not legacy:
        d["siteAnchor"] = copy.deepcopy(ANCHOR)
    scope = import_scope(tmp_path, d)
    function("_load_session", scope)()
    assert scope["snapped_point"]()[:2] == POINT[:2]
    assert scope["_map_pick"]["generation"] == 8
    assert scope["_source_ready"](scope["delin"]())
    assert scope["delin"]()["ctx_inputs"]["comid"] == 9327042
    assert scope["_no_watershed"] == {}
    assert scope["measured_values"]() == {"saved": {"value": 4}}


def import_scope(tmp_path, d):
    saved = tmp_path / "assessment.json"
    saved.write_text(json.dumps({"delineation": d, "measured_values": {"saved": {"value": 4}}}))
    scope = state_scope()
    scope.update(input=SimpleNamespace(load_session=lambda: [{"datapath": str(saved)}]),
                 session=SimpleNamespace(load=json.loads), measured_values=Value({"old": {"value": 9}}),
                 computed_for=Value("old"), loaded_assessment=Value(object()), selected_ref=Value("old"),
                 current_fn=Value(1), current_step=Value("identify"), _HAS_MAP=False)
    scope["_no_watershed"].update(generation=7, anchor=ANCHOR)
    return scope


def test_import_without_source_keeps_saved_values_but_blocks_new_work(tmp_path):
    scope = import_scope(tmp_path, {"delineation": {"snapped_lat": POINT[0], "snapped_lon": POINT[1]}})
    scope["_HAS_MAP"] = True
    starts = []
    scope.update(coord_snap_task=lambda *args: starts.append(args), _place_pin=lambda *args: None,
                 _start_lookup=lambda *args: pytest.fail("must restore HR ID before source lookup"),
                 delineation=SimpleNamespace(geojson_bounds=lambda *a: None))
    function("_load_session", scope)()
    assert len(starts) == 1 and starts[0][:2] == POINT[:2]
    assert not scope["_source_ready"]()
    assert scope["measured_values"]() == {"saved": {"value": 4}}
    assert {"marker", "route", "scored"}.issubset(scope["removed"])


def test_import_restores_routed_anchor_from_legacy_context(tmp_path):
    routed = {"anchorKind": "hrSurrogate", "scoredReach": {"comid": 9327042},
              "clickedStream": {"snapLat": POINT[0], "snapLon": POINT[1], "nhdplusId": POINT[3]}}
    scope = import_scope(tmp_path, {"ctx_inputs": {"siteAnchor": routed}})
    function("_load_session", scope)()
    assert scope["snapped_point"]()[:2] == POINT[:2]
    assert scope["site_anchor"]()["anchorKind"] == "hrSurrogate"
    assert scope["_source_ready"](scope["delin"]())


def test_unchanged_lookup_progress_flushes_without_self_invalidation():
    from shiny import reactive
    async def run():
        scope = state_scope()
        scope["reactive"] = reactive
        scope["source_lookup"] = reactive.value({"status": "finding", "generation": 7,
                                                  "attempt": 1, "detail": ""})
        scope["_lookup_progress"][7] = {"status": "finding", "attempt": 1}
        function("_set_lookup", scope)
        poll = function("_lookup_poll", scope)
        runs = []
        def check():
            runs.append(1)
            if len(runs) > 3:
                pytest.fail("lookup status invalidated itself repeatedly")
            poll()
        effect = reactive.effect(check)
        try:
            await asyncio.wait_for(reactive.flush(), timeout=1)
            assert len(runs) == 1
        finally:
            effect.destroy()
    asyncio.run(run())


def test_empty_import_cannot_reuse_previous_point_or_source(tmp_path):
    scope = import_scope(tmp_path, {})
    function("_load_session", scope)()
    assert scope["snapped_point"]() is None and scope["site_anchor"]() is None
    assert not scope["_source_ready"]() and scope["loaded_assessment"]() is None


def test_downstream_navigation_rejects_previous_site_but_allows_imported_results():
    scope = state_scope()
    scope.update(delin=Value({"siteAnchor": ANCHOR}), loaded_assessment=Value(object()))
    has = function("_has", scope)
    scope["_delin_generation"].set(6)
    for step in (app.STEP_BASIN, app.STEP_MEASURE, app.STEP_REPORT):
        assert not has(step)
    assert has(app.STEP_IDENTIFY)
    # An imported current-site report remains viewable while its missing source resolves.
    scope["_delin_generation"].set(7)
    scope["source_lookup"].set({"status": "finding", "generation": 7})
    assert has(app.STEP_BASIN) and has(app.STEP_MEASURE) and has(app.STEP_REPORT)


def test_previous_site_cannot_trigger_continuation_or_report_events():
    scope = state_scope()
    scope.update(delin=Value({"siteAnchor": ANCHOR}), loaded_assessment=Value(object()),
                 current_step=Value(app.STEP_IDENTIFY))
    scope["_delin_generation"].set(6)
    function("_go_measure", scope)()
    function("_open_report", scope)()
    assert scope["current_step"]() == app.STEP_IDENTIFY


@pytest.mark.parametrize("status", ["idle", "snapping", "finding", "retrying", "failed", "no_match"])
def test_continue_requires_ready_source_but_saved_results_remain_viewable(status):
    scope = state_scope()
    scope.update(delin=Value({"siteAnchor": ANCHOR}), loaded_assessment=Value(object()),
                 current_step=Value(app.STEP_BASIN), current_fn=Value(3))
    scope["source_lookup"].set({"status": status, "generation": 7})
    go = function("_go_measure", scope)
    go()
    assert scope["current_step"]() == app.STEP_BASIN and scope["current_fn"]() == 3
    assert "StreamCat lookup" in scope["notices_seen"][-1][0]
    # The stepper still supports reviewing saved results while this explicit
    # continuation into new work is blocked.
    has = function("_has", scope)
    assert has(app.STEP_MEASURE) and has(app.STEP_REPORT)
    scope["source_lookup"].set({"status": "ready", "generation": 7})
    go()
    assert scope["current_step"]() == app.STEP_MEASURE and scope["current_fn"]() == 0


def test_continue_updates_in_place_after_lookup_and_seeds_disabled_when_basin_mounts():
    from shiny import reactive

    async def run():
        scope = state_scope()
        updates, renders = [], []
        scope.update(
            reactive=reactive, delin=Value({"siteAnchor": ANCHOR}),
            current_step=reactive.value(app.STEP_BASIN),
            source_lookup=reactive.value({"status": "failed", "generation": 7}),
            ui=SimpleNamespace(**{**vars(app.ui), "update_action_button":
                                 lambda name, **kwargs: updates.append((name, kwargs))}))
        render_pane = function("leftpane", scope)
        render_effect = reactive.effect(lambda: renders.append(str(render_pane())))
        update_effect = reactive.effect(function("_toggle_continue", scope))
        try:
            await reactive.flush()
            button = re.search(r'<button\b[^>]*\bid="to_measure"[^>]*>', renders[0]).group()
            assert "disabled" in button
            assert updates == [("to_measure", {"disabled": True})]
            scope["source_lookup"].set({"status": "ready", "generation": 7})
            await reactive.flush()
            assert updates[-1] == ("to_measure", {"disabled": False})
            assert len(renders) == 1  # no button replacement or click-count reset
            scope["source_lookup"].set({"status": "retrying", "generation": 7})
            await reactive.flush()
            assert updates[-1] == ("to_measure", {"disabled": True})
            assert len(renders) == 1
            with reactive.isolate():
                assert scope["current_step"]() == app.STEP_BASIN
        finally:
            render_effect.destroy()
            update_effect.destroy()

    asyncio.run(run())


def test_explicit_replacement_pick_clears_previous_sites_values_and_geometry():
    scope = state_scope()
    scope.update(delin=Value({"siteAnchor": ANCHOR}),
                 measured_values=Value({"desktop": {"value": 12}, "field": {"value": 8}}),
                 computed_for=Value("old-site"), current_fn=Value(2))
    function("_begin_pick", scope)()
    assert scope["delin"]() is None and scope["_delin_generation"]() is None
    assert scope["measured_values"]() == {} and scope["computed_for"]() is None
    assert {"marker", "ws", "reach", "scored", "route"}.issubset(scope["removed"])


@pytest.mark.parametrize("detail", ["invalid JSON response: private details", "unexpected response shape: private details"])
def test_lookup_failure_has_retry_and_clear_sanitized_status(detail):
    scope = state_scope()
    scope["ui"] = app.ui
    scope["source_lookup"].set({"status": "failed", "generation": 7, "detail": detail})
    markup = str(function("streamcat_lookup_status", scope)())
    assert "returned an invalid response" in markup and "Retry StreamCat lookup" in markup
    assert 'aria-live="polite"' in markup and 'role="status"' in markup
    assert "private details" not in markup


@pytest.mark.parametrize("surface", ["streamcat_lookup_status", "streamcat_lookup_status_ws"])
@pytest.mark.parametrize("status,attempt,retry", [
    ("snapping", 1, None), ("finding", 1, None),
    ("retrying", 2, 1), ("retrying", 3, 2), ("retrying", 4, 3),
    ("idle", 0, None), ("ready", 1, None), ("failed", 4, None), ("no_match", 1, None),
])
def test_lookup_activity_on_identify_and_imported_worksheet_surfaces(surface, status, attempt, retry):
    scope = state_scope()
    scope["ui"] = app.ui
    scope["source_lookup"].set({"status": status, "generation": 7, "attempt": attempt,
                               "wait_seconds": retry * 5 if retry else 0})
    rendered = function(surface, scope)()
    if status in ("idle", "ready"):
        assert rendered is None
        return
    html = str(rendered)
    pending = status in ("snapping", "finding", "retrying")
    assert ("easi-spinner easi-lookup-spinner" in html) is pending
    assert 'role="status"' in html and 'aria-live="polite"' in html
    retry_id = "retry_streamcat_ws" if surface.endswith("_ws") else "retry_streamcat"
    assert (f'id="{retry_id}"' in html) == (status in ("failed", "no_match"))
    if pending:
        assert 'aria-hidden="true"' in html
        text = "Retrying StreamCat lookup" if retry else "Finding"
        assert html.index("easi-lookup-spinner") < html.index(text)
    if retry:
        assert f"Retrying StreamCat lookup ({retry} of 3)" in html


def test_navigation_can_overlap_old_worksheet_and_new_basin_without_duplicate_ids():
    scope = state_scope()
    scope.update(ui=app.ui, current_step=Value(app.STEP_MEASURE))
    scope["source_lookup"].set({"status": "failed", "generation": 7})
    # Shiny mounts one independently rendered output before unmounting the other.
    # Render both real shells and both status bodies to model that transition.
    worksheet = str(function("worksheet", scope)())
    worksheet_status = str(function("streamcat_lookup_status_ws", scope)())
    scope["current_step"].set(app.STEP_BASIN)
    basin = str(function("leftpane", scope)())
    basin_status = str(function("streamcat_lookup_status", scope)())
    old_ids = set(re.findall(r'\bid="([^"]+)"', worksheet + worksheet_status))
    new_ids = set(re.findall(r'\bid="([^"]+)"', basin + basin_status))
    assert {"streamcat_lookup_status_ws", "retry_streamcat_ws"} <= old_ids
    assert {"streamcat_lookup_status", "retry_streamcat"} <= new_ids
    assert old_ids.isdisjoint(new_ids)


@pytest.mark.parametrize("active,missing", [("retry_streamcat", "retry_streamcat_ws"),
                                            ("retry_streamcat_ws", "retry_streamcat")])
def test_retry_event_works_when_other_panes_input_has_never_mounted(active, missing):
    from shiny import reactive
    from shiny.types import ActionButtonValue
    async def run():
        names = ("_retry_streamcat", "_retry_streamcat_ws")
        functions = [copy.deepcopy(n) for n in ast.walk(SERVER)
                     if isinstance(n, ast.FunctionDef) and n.name in names]
        events = SimpleNamespace(**{active: reactive.value(ActionButtonValue(0)),
                                    missing: reactive.value()})
        calls = []
        scope = {"reactive": reactive, "input": events, "_retry_lookup": lambda: calls.append(active)}
        exec(compile(ast.Module(body=functions, type_ignores=[]), app.__file__, "exec"), scope)
        try:
            await reactive.flush()
            assert calls == []
            getattr(events, active).set(ActionButtonValue(1))
            await reactive.flush()
            assert calls == [active]
        finally:
            for name in names:
                scope[name].destroy()
    asyncio.run(run())
