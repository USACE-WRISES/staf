"""Exercise the app's real closures with controlled lookup/engine completion order."""
from __future__ import annotations

import ast
import asyncio
import copy
import re
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from shiny import ui
from sfari import comid_anchor

SOURCE = ast.parse((Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8"))
SERVER = next(n for n in SOURCE.body if isinstance(n, ast.FunctionDef) and n.name == "server")
POINT = (43.6858, -72.2367, 9.0, 24000800011817)
ANCHOR = {"anchorKind": "hrSurrogate", "scoredReach": {"comid": 9327042}}


class Value:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = value


def scope(*names, **extra):
    ns = {"reactive": SimpleNamespace(isolate=nullcontext, invalidate_later=lambda _: None),
          "comid_anchor": comid_anchor, "_map_pick": {"generation": 7},
          "source_lookup": Value({"status": "ready", "generation": 7, "attempt": 1}),
          "snapped_point": Value(POINT), "site_anchor": Value(ANCHOR),
          "_delin_generation": {"generation": 7}, **extra}
    for name in names:
        node = copy.deepcopy(next(n for n in ast.walk(SERVER)
                                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name))
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), "sfari/app.py", "exec"), ns)
    return ns


@pytest.mark.parametrize("status", ["idle", "snapping", "finding", "retrying", "failed", "no_match"])
def test_pending_and_failed_source_block_every_delineate_path(status):
    calls = []
    ns = scope("_source_ready", "_start_delineate", "_launch_engine",
               source_lookup=Value({"status": status, "generation": 7}),
               input=SimpleNamespace(lat=lambda: POINT[0], lon=lambda: POINT[1]),
               ui=SimpleNamespace(notification_show=lambda *a, **k: calls.append("notice")),
               engine_task=lambda *a: pytest.fail("engine must not launch"))
    assert not ns["_source_ready"]()
    ns["_start_delineate"]()
    ns["_launch_engine"](*POINT[:2], 1000)
    assert calls == ["notice"]


def test_readiness_requires_current_point_source_and_delineation():
    ns = scope("_source_ready", "_with_anchor")
    d = {"ctx_inputs": {"lat": POINT[0], "lon": POINT[1], "comid": 1},
         "delineation": {"comid": 1}}
    assert ns["_source_ready"]()
    assert not ns["_source_ready"](d)
    attached = ns["_with_anchor"](d)
    assert attached["ctx_inputs"]["comid"] == attached["delineation"]["comid"] == 9327042
    assert d["ctx_inputs"]["comid"] == 1  # copies instead of modifying the saved data
    assert ns["_source_ready"](attached)
    ns["_map_pick"]["generation"] += 1
    assert not ns["_source_ready"]()
    assert ns["_with_anchor"](d) is d


@pytest.mark.parametrize("result,status", [({"error": "no_stream_found"}, "no_match"),
                                            ({"error": "snap_service_error", "detail": "timeout"}, "failed"),
                                            ({"anchor": ANCHOR}, "ready")])
def test_source_completion_keeps_pin_and_separates_errors_from_no_match(result, status):
    drawn, geometry = [], []
    ns = scope("_lookup_state", "_source_ready", "_anchor_done",
               anchor_task=SimpleNamespace(result=lambda: (7, result)),
               _lookup_progress={"generation": 7, "attempt": 3}, delin=Value(None),
               _draw_anchor=lambda res: drawn.append(res), scored_task=lambda *a: geometry.append(a))
    ns["_anchor_done"]()
    assert ns["snapped_point"]() == POINT
    assert ns["source_lookup"]()["status"] == status
    assert drawn == [result]
    assert geometry == ([(9327042, 7)] if status == "ready" else [])


def test_source_readiness_does_not_wait_for_highlight_geometry():
    async def run_sync(worker):
        return worker()
    progress = []
    def resolve(*a, **kw):
        kw["progress"]({"status": "retrying", "attempt": 2})
        return {"anchor": ANCHOR}
    ns = scope("anchor_task", anyio=SimpleNamespace(to_thread=SimpleNamespace(run_sync=run_sync)),
               _lookup_progress={"generation": 7},
               comid_anchor=SimpleNamespace(resolve=resolve, comid=comid_anchor.comid),
               network_display=SimpleNamespace(feature_by_id=lambda *a: None,
                   v2_reach_feature=lambda *a: pytest.fail("highlight network fetch must be separate")))
    assert asyncio.run(ns["anchor_task"](*POINT[:2], POINT, None, 7)) == (7, {"anchor": ANCHOR})
    assert ns["_lookup_progress"] == {"generation": 7, "status": "retrying", "attempt": 2}


@pytest.mark.parametrize("task_name", ["click_snap_task", "coord_snap_task", "anchor_task"])
def test_unexpected_worker_errors_retain_generation_and_reach_a_terminal_state(task_name):
    async def fail(worker):
        raise RuntimeError("service stopped")
    ns = scope(task_name, anyio=SimpleNamespace(to_thread=SimpleNamespace(run_sync=fail)))
    args = (*POINT[:2], POINT, None, 7) if task_name == "anchor_task" else (*POINT[:2], 7)
    assert asyncio.run(ns[task_name](*args)) == (7, {"error": "snap_service_error", "detail": "service stopped"})


@pytest.mark.parametrize("name,task", [("_engine_done", "engine_task"), ("_pull_done", "pull_task")])
def test_late_engine_or_evidence_completion_cannot_restore_a_cleared_or_imported_site(name, task):
    ns = scope(name, **{task: SimpleNamespace(status=lambda: "success", result=lambda: (6, {})),
                       "ui": SimpleNamespace(notification_remove=lambda *a: pytest.fail("stale mutation")),
                       "stage": Value("new site's progress")})
    ns[name]()
    assert ns["stage"]() == "new site's progress"


def test_fallback_confirmation_cannot_apply_to_a_replacement_point():
    ns = scope("_use_streamcat", _no_watershed={"generation": 6, "anchor": ANCHOR, "hr_hit": POINT},
               ui=SimpleNamespace(modal_remove=lambda: None),
               pipeline=SimpleNamespace(delineate_without_watershed=lambda *a: pytest.fail("stale fallback")))
    ns["_use_streamcat"]()
    assert ns["_no_watershed"] == {}


@pytest.mark.parametrize("status", ["finding", "retrying", "failed", "no_match"])
def test_manual_and_automatic_evidence_launch_requires_ready_source(status):
    ns = scope("_source_ready", "_launch_pull", source_lookup=Value({"status": status, "generation": 7}),
               pull_task=lambda *a: pytest.fail("evidence must not launch"))
    assert not ns["_launch_pull"]({"ctx_inputs": {"comid": 9327042}}, {})


def test_retry_belongs_to_same_point_but_invalidates_prior_completions():
    starts = []
    ns = scope("_retry_lookup", "_retry_streamcat", source_lookup=Value({"status": "failed", "generation": 7}),
               _lookup_request={"hr_hit": POINT}, _no_watershed={"generation": 7},
               _begin_lookup=lambda: starts.append(True))
    ns["_retry_streamcat"]()
    assert ns["_map_pick"]["generation"] == ns["_delin_generation"]["generation"] == 8
    assert ns["snapped_point"]() == POINT and starts == [True]
    assert ns["_no_watershed"] == {}


@pytest.mark.parametrize("saved_comid", [None, 9327042])
def test_import_replaces_old_point_and_source_before_any_new_work(tmp_path, saved_comid):
    saved = {"delineation": {"delineation": {"snapped_lat": 40, "snapped_lon": -83, "comid": saved_comid},
                              "ctx_inputs": {"lat": 40, "lon": -83, "comid": saved_comid}},
             "evidence": {"field-note": {"value": "saved"}}}
    path = tmp_path / "saved.json"
    path.write_text("{}")
    lookups = []
    ns = scope("_lookup_state", "_source_ready", "_with_anchor", "_load_session",
               input=SimpleNamespace(load_session=lambda: [{"datapath": str(path)}]),
               session_io=SimpleNamespace(load=lambda _: saved), _lookup_request={"old": True},
               _lookup_progress={"old": True}, _no_watershed={"old": True},
               _HAS_MAP=True, _layers={"marker": None}, _remove_layer=lambda _: None,
               _add_layer=lambda *a: None, _point_marker=lambda *a: None,
               _MAP=SimpleNamespace(fit_bounds=lambda _: None),
               delineation=SimpleNamespace(geojson_bounds=lambda *a: None),
               scored_task=lambda *a: None, coord_snap_task=lambda *a: lookups.append(a),
               ui=SimpleNamespace(notification_show=lambda *a, **k: None), STEP_REVIEW="review",
               **{name: Value(None) for name in ("delin", "metric_scores", "function_scores", "evidence",
                   "xs_geom", "engine_state", "evidence_reach", "current_fn", "current_step", "stage")})
    ns["_load_session"]()
    assert ns["snapped_point"]()[:2] == (40, -83)
    assert ns["evidence"]() == saved["evidence"]
    assert ns["current_step"]() == "review"  # saved results remain available
    assert ns["_no_watershed"] == {} and ns["_lookup_request"] == {}
    if saved_comid is None:
        assert ns["site_anchor"]() is None and not ns["_source_ready"]()
        assert lookups == [(40, -83, 8)]
    else:
        assert ns["_source_ready"](ns["delin"]())
        assert lookups == []


@pytest.mark.parametrize("detail,phrase", [("Read timed out", "Could not reach"),
                                             ("Unexpected response shape: []", "invalid response"),
                                             ("invalid JSON response: bad", "invalid response")])
def test_lookup_status_is_persistent_accessible_and_hides_raw_details(detail, phrase):
    ns = scope("_streamcat_lookup_status_ui", "streamcat_lookup_status", ui=ui,
               source_lookup=Value({"status": "failed", "generation": 7, "detail": detail}))
    html = str(ns["streamcat_lookup_status"]())
    assert phrase in html and "Retry StreamCat lookup" in html
    assert 'role="status"' in html and 'aria-live="polite"' in html
    assert detail not in html


def test_success_is_announced_by_the_source_note_without_a_second_ready_line():
    ns = scope("snap_status", "_streamcat_lookup_status_ui", "streamcat_lookup_status", ui=ui)
    assert ns["streamcat_lookup_status"]() is None
    html = str(ns["snap_status"]())
    assert 'role="status"' in html and 'aria-live="polite"' in html
    assert "COMID 9327042" in html
    assert "StreamCat source ready" not in html


@pytest.mark.parametrize("surface", ["streamcat_lookup_status", "streamcat_lookup_status_ws"])
@pytest.mark.parametrize("status,attempt,retry", [
    ("snapping", 1, None), ("finding", 1, None),
    ("retrying", 2, 1), ("retrying", 3, 2), ("retrying", 4, 3),
    ("idle", 0, None), ("ready", 1, None), ("failed", 4, None), ("no_match", 1, None),
])
def test_lookup_activity_on_identify_and_imported_worksheet_surfaces(surface, status, attempt, retry):
    ns = scope("_streamcat_lookup_status_ui", surface, ui=ui,
               source_lookup=Value({"status": status, "generation": 7, "attempt": attempt,
                                    "wait_seconds": retry * 5 if retry else 0}))
    rendered = ns[surface]()
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


@pytest.mark.parametrize("status", ["snapping", "finding", "retrying", "ready", "failed"])
def test_field_forms_lookup_wait_has_the_same_accessible_activity_indicator(status):
    ns = scope("ff_status", ui=ui, source_lookup=Value({"status": status}),
               evidence=Value({}), pull_task=SimpleNamespace(status=lambda: "initial"),
               engine_task=SimpleNamespace(status=lambda: "initial"),
               _pull_prog={"generation": 7}, _engine_prog={"generation": 7},
               config=SimpleNamespace(desktop_metrics=lambda: []), METRICS_BY_ID={},
               _ff_rows=lambda *args: ([], {}))
    html = str(ns["ff_status"]())
    pending = status in ("snapping", "finding", "retrying")
    assert ("easi-lookup-spinner" in html) is pending
    assert 'role="status"' in html and 'aria-live="polite"' in html
    if pending:
        assert 'aria-hidden="true"' in html and "finding the StreamCat source" in html


@pytest.mark.parametrize("worker_status,attempt,expected_writes", [("finding", 1, 0), ("retrying", 2, 1)])
def test_progress_poll_settles_in_a_real_reactive_flush(worker_status, attempt, expected_writes):
    from shiny import reactive

    async def run():
        state = reactive.Value({"status": "finding", "generation": 7, "attempt": 1})
        ns = scope("_lookup_state", "_lookup_poll", reactive=reactive, source_lookup=state,
                   _lookup_progress={"generation": 7, "status": worker_status, "attempt": attempt})
        writes = []
        setter = ns["_lookup_state"]
        def counted_setter(*args, **kwargs):
            writes.append((args, kwargs))
            if len(writes) > 3:
                pytest.fail("progress observer repeatedly invalidates itself")
            setter(*args, **kwargs)
        ns["_lookup_state"] = counted_setter
        effect = reactive.effect(ns["_lookup_poll"])
        try:
            await asyncio.wait_for(reactive.flush(), timeout=1)
            assert len(writes) == expected_writes
            with reactive.isolate():
                assert state()["status"] == worker_status
                assert state()["attempt"] == attempt
        finally:
            effect.destroy()
    asyncio.run(run())


def test_malformed_import_is_rejected_before_replacing_the_current_site(tmp_path):
    path = tmp_path / "malformed.json"
    path.write_text("{}")
    notices = []
    ns = scope("_load_session", input=SimpleNamespace(load_session=lambda: [{"datapath": str(path)}]),
               session_io=SimpleNamespace(load=lambda _: {"delineation": {"ctx_inputs": []}}),
               ui=SimpleNamespace(notification_show=lambda *a, **k: notices.append(a[0])))
    ns["_load_session"]()
    assert ns["_map_pick"]["generation"] == 7
    assert ns["snapped_point"]() == POINT and ns["site_anchor"]() == ANCHOR
    assert "Could not load assessment" in notices[0]


def test_assessment_to_basin_transition_has_unique_output_and_retry_ids():
    ns = scope("_source_ready", "leftpane", "worksheet", "_streamcat_lookup_status_ui",
               "streamcat_lookup_status", "streamcat_lookup_status_ws", ui=ui,
               source_lookup=Value({"status": "failed", "generation": 7}),
               delin=Value({"siteAnchor": ANCHOR, "ctx_inputs": {"comid": 9327042}}),
               current_step=Value("review"), STEP_IDENTIFY="identify", STEP_BASIN="basin",
               STEP_REVIEW="review", STEP_REPORT="report",
               STEP_LABELS=[("identify", "Identify"), ("basin", "Basin"), ("review", "Assessment")],
               _stepper=lambda _: ui.div("Steps"))
    worksheet = str(ns["worksheet"]()) + str(ns["streamcat_lookup_status_ws"]())
    ns["current_step"].set("basin")
    basin = str(ns["leftpane"]()) + str(ns["streamcat_lookup_status"]())
    # Shiny can briefly retain the outgoing component while binding the incoming one.
    ids = re.findall(r'\bid="([^"]+)"', worksheet + basin)
    assert len(ids) == len(set(ids))
    assert {"streamcat_lookup_status", "streamcat_lookup_status_ws",
            "retry_streamcat", "retry_streamcat_ws"}.issubset(ids)


def test_each_retry_event_works_when_the_other_pane_input_is_missing():
    from shiny import reactive
    from shiny.types import ActionButtonValue

    async def run():
        calls = []
        input = SimpleNamespace(retry_streamcat=reactive.Value(),
                                retry_streamcat_ws=reactive.Value(ActionButtonValue(0)))
        ns = {"reactive": reactive, "input": input, "_retry_lookup": lambda: calls.append("retry")}
        for name in ("_retry_streamcat", "_retry_streamcat_ws"):
            node = copy.deepcopy(next(n for n in ast.walk(SERVER)
                                      if isinstance(n, ast.FunctionDef) and n.name == name))
            # Keep both real Shiny decorators: this reproduces absent dynamic inputs.
            exec(compile(ast.Module(body=[node], type_ignores=[]), "sfari/app.py", "exec"), ns)
        try:
            await reactive.flush()
            assert calls == []
            input.retry_streamcat_ws.set(ActionButtonValue(1))
            await reactive.flush()
            assert calls == ["retry"]
            input.retry_streamcat_ws.unset()
            input.retry_streamcat.set(ActionButtonValue(1))
            await reactive.flush()
            assert calls == ["retry", "retry"]
        finally:
            ns["_retry_streamcat"].destroy()
            ns["_retry_streamcat_ws"].destroy()
    asyncio.run(run())


@pytest.mark.parametrize("status,generation,allowed", [("finding", 7, False), ("retrying", 7, False),
    ("failed", 7, False), ("no_match", 7, False), ("ready", 6, False), ("ready", 7, True)])
def test_explicit_continue_button_and_server_actions_require_the_same_current_source(status, generation, allowed):
    updates, notices = [], []
    ns = scope("_source_ready", "_toggle_continue", "_go_review", "_enter_review", "leftpane",
               source_lookup=Value({"status": status, "generation": generation}),
               delin=Value({"siteAnchor": ANCHOR, "ctx_inputs": {"comid": 9327042}}),
               current_step=Value("basin"), current_fn=Value(4), STEP_BASIN="basin",
               STEP_IDENTIFY="identify", STEP_REVIEW="review", STEP_LABELS=[("basin", "Basin")],
               _stepper=lambda _: ui.div("Steps"), ui=ui)
    html = str(ns["leftpane"]())
    continue_button = re.search(r'<button\b[^>]*\bid="to_review"[^>]*>', html).group()
    assert ("disabled" in continue_button) is not allowed
    ns["ui"] = SimpleNamespace(update_action_button=lambda name, **kwargs: updates.append((name, kwargs)),
                                notification_show=lambda *a, **k: notices.append(a))
    ns["_toggle_continue"]()
    assert updates == [("to_review", {"disabled": not allowed})]
    ns["_go_review"]()
    ns["_enter_review"]()
    assert ns["current_step"]() == ("review" if allowed else "basin")
    assert ns["current_fn"]() == (0 if allowed else 4)


def test_continue_readiness_updates_do_not_trigger_navigation_or_reset_the_click_count():
    from shiny import reactive
    from shiny.types import ActionButtonValue

    async def run():
        updates = []
        state = reactive.Value({"status": "finding", "generation": 7})
        step, fn = reactive.Value("basin"), reactive.Value(4)
        input = SimpleNamespace(to_review=reactive.Value(ActionButtonValue(0)))
        ns = scope("_source_ready", reactive=reactive, source_lookup=state,
                   delin=reactive.Value({"siteAnchor": ANCHOR, "ctx_inputs": {"comid": 9327042}}),
                   snapped_point=reactive.Value(POINT), site_anchor=reactive.Value(ANCHOR),
                   current_step=step, current_fn=fn, input=input, STEP_REVIEW="review",
                   ui=SimpleNamespace(update_action_button=lambda name, **kwargs: updates.append((name, kwargs)),
                                      notification_show=lambda *a, **k: None))
        names = ("_toggle_continue", "_go_review", "_enter_review")
        for name in names:
            node = copy.deepcopy(next(n for n in ast.walk(SERVER)
                                      if isinstance(n, ast.FunctionDef) and n.name == name))
            exec(compile(ast.Module(body=[node], type_ignores=[]), "sfari/app.py", "exec"), ns)
        try:
            await reactive.flush()
            assert updates[-1] == ("to_review", {"disabled": True})
            input.to_review.set(ActionButtonValue(1))
            await reactive.flush()
            state.set({"status": "failed", "generation": 7})
            input.to_review.set(ActionButtonValue(2))
            await reactive.flush()
            state.set({"status": "ready", "generation": 7})
            await reactive.flush()
            assert updates[-1] == ("to_review", {"disabled": False})
            with reactive.isolate():
                assert step() == "basin" and fn() == 4  # enabling is not a click
                assert input.to_review() == 2
            input.to_review.set(ActionButtonValue(3))
            await reactive.flush()
            with reactive.isolate():
                assert step() == "review" and fn() == 0
        finally:
            for name in names:
                ns[name].destroy()
    asyncio.run(run())


def test_saved_results_remain_viewable_via_stepper_while_source_is_unresolved():
    ns = scope("_stepper_nav", source_lookup=Value({"status": "failed", "generation": 7}),
               _cancel_report=lambda: None,
               current_step=Value("basin"), delin=Value({"saved": True}),
               input=SimpleNamespace(step_nav=lambda: {"key": "review"}),
               STEP_IDENTIFY="identify", STEP_BASIN="basin", STEP_REVIEW="review", STEP_REPORT="report",
               STEP_LABELS=[("basin", "Basin"), ("review", "Assessment")])
    ns["_stepper_nav"]()
    assert ns["current_step"]() == "review"
