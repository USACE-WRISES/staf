"""Exercise the actual map task/completion functions without a live Shiny session.

The app's decorated closures are compiled without their decorators, so these
regressions run the production guard and worker wrappers rather than duplicating
their logic. Each app is inspected independently; no colliding app modules import.
"""
from __future__ import annotations

import ast
import asyncio
import copy
from contextlib import nullcontext
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

APPS = Path(__file__).resolve().parents[2]


class StateMutation(Exception):
    pass


def _stop(*args, **kwargs):
    raise StateMutation


def _function(app_name, name, namespace):
    source = ast.parse((APPS / app_name / "app.py").read_text(encoding="utf-8"))
    server = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "server")
    function = copy.deepcopy(next(n for n in ast.walk(server)
                                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                  and n.name == name))
    function.decorator_list = []
    # Worker launchers replace their per-run progress dictionaries via nonlocal;
    # the extracted function has no enclosing server frame.
    function.body = [node for node in function.body if not isinstance(node, ast.Nonlocal)]
    code = compile(ast.Module(body=[function], type_ignores=[]), f"{app_name}/app.py", "exec")
    scope = {"reactive": SimpleNamespace(isolate=nullcontext), **namespace}
    exec(code, scope)
    return scope[name]


TASKS = [
    ("easi", "click_snap_task", "_apply_click_snap"),
    ("easi", "coord_snap_task", "_apply_coord_snap"),
    ("easi", "route_task", "_route_done"),
    ("sfari", "click_snap_task", "_apply_click_snap"),
    ("sfari", "coord_snap_task", "_apply_coord_snap"),
    ("sfari", "anchor_task", "_anchor_done"),
    ("sfari", "scored_task", "_scored_done"),
    ("deep", "click_snap_task", "_apply_click_snap"),
    ("deep", "coord_snap_task", "_apply_coord_snap"),
    ("deep", "anchor_task", "_anchor_done"),
    ("deep", "scored_task", "_scored_done"),
]


@pytest.mark.parametrize("app_name,task_name,handler_name", TASKS)
def test_superseded_completion_cannot_touch_map_or_selection(app_name, task_name, handler_name):
    state = {"generation": 2}
    result = (1, {"anchor": {"sentinel": True}})
    handler = _function(app_name, handler_name, {
        "_map_pick": state,
        task_name: SimpleNamespace(result=lambda: result),
        "stage": SimpleNamespace(set=_stop),
        "_apply_snap_result": _stop,
        "_draw_anchor": _stop,
        "site_anchor": _stop,
        "comid_anchor": SimpleNamespace(comid=_stop),
    })
    handler()  # A previous click, Clear, or import made generation 1 obsolete.
    state["generation"] = 1
    with pytest.raises(StateMutation):
        handler()  # The same payload reaches the existing behavior when current.


@pytest.mark.parametrize("app_name,task_name,handler_name", TASKS)
def test_delayed_worker_retains_its_launch_generation(app_name, task_name, handler_name):
    async def run():
        release = asyncio.Event()
        payload = {"anchor": None, "hit": None}
        async def delayed(worker):
            await release.wait()
            return worker()
        task = _function(app_name, task_name, {
            "anyio": SimpleNamespace(to_thread=SimpleNamespace(run_sync=delayed)),
            "_snap_both": lambda *args: payload,
            "_route_with_reach": lambda *args: payload,
            "_map_pick": {"generation": 4}, "_lookup_progress": {},
            "hr_site": SimpleNamespace(snap_point=lambda *args: payload),
            "comid_anchor": SimpleNamespace(resolve=lambda *args, **kwargs: payload,
                                             comid=lambda anchor: None),
            "network_display": SimpleNamespace(v2_reach_feature=lambda comid: None,
                                                feature_by_id=lambda *args: None),
        })
        arguments = {"lat": 40.0, "lon": -83.0, "hr_hit": (40.0, -83.0, 0, 1),
                     "v2_fc": None, "comid": 1, "generation": 4}
        pending = asyncio.create_task(task(**{key: arguments[key]
                                              for key in inspect.signature(task).parameters}))
        await asyncio.sleep(0)
        state = {"generation": 5}  # A new pick/Clear/import occurs while work is blocked.
        release.set()
        result = await pending
        assert result[0] == 4
        assert result[1] == ({"comid": 1, "feature": None} if task_name == "scored_task" else payload)
        handler = _function(app_name, handler_name, {
            "_map_pick": state,
            task_name: SimpleNamespace(result=lambda: result),
            "stage": SimpleNamespace(set=_stop),
            "_apply_snap_result": _stop, "_draw_anchor": _stop, "site_anchor": _stop,
        })
        handler()  # The actual completion handler rejects the late result.
    asyncio.run(run())


@pytest.mark.parametrize("app_name", ["easi", "sfari", "deep"])
@pytest.mark.parametrize("event", ["clear", "click", "coordinates"])
def test_new_selection_and_clear_invalidate_before_state_changes(app_name, event):
    state = {"generation": 7}
    class Map:
        def __setattr__(self, name, value):
            _stop()
    name = {"clear": "_reset" if app_name == "easi" else "_do_reset",
            "click": "_handle_click", "coordinates": "_coords_entered"}[event]
    handler = _function(app_name, name, {
        "_map_pick": state, "current_step": lambda: "identify", "STEP_IDENTIFY": "identify",
        "clicked": _stop, "_clear_route_state": _stop, "_remove_layer": _stop,
        "_invalidate_selection": _stop, "_begin_pick": _stop,
        "_MAP": Map(), "input": SimpleNamespace(coords_entered=lambda: {"lat": 40, "lon": -83}),
    })
    with pytest.raises(StateMutation):
        handler()
    assert state["generation"] == 8


@pytest.mark.parametrize("app_name", ["sfari", "deep"])
def test_successful_import_invalidates_pending_pick_before_loading_state(app_name, tmp_path):
    saved = tmp_path / "assessment.json"
    saved.write_text("{}", encoding="utf-8")
    state = {"generation": 9}
    loader = SimpleNamespace(load=json.loads)
    handler = _function(app_name, "_load_session", {
        "_map_pick": state,
        "input": SimpleNamespace(load_session=lambda: [{"datapath": str(saved)}]),
        "session_io": loader, "session": loader, "delin": SimpleNamespace(set=_stop),
        "_lookup_request": SimpleNamespace(clear=_stop),
        "comid_anchor": SimpleNamespace(saved_anchor=lambda _: None, saved_point=lambda _: None),
    })
    with pytest.raises(StateMutation):
        handler()
    assert state["generation"] == 10

def test_easi_new_analysis_works_before_the_basin_clear_input_exists():
    from shiny import reactive
    from shiny.types import ActionButtonValue

    async def run():
        source = ast.parse((APPS / "easi" / "app.py").read_text(encoding="utf-8"))
        server = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "server")
        # Keep the real decorators for this test: reading both event inputs in
        # one effect used to suspend New analysis until clear_basin existed.
        functions = [copy.deepcopy(n) for n in ast.walk(server)
                     if isinstance(n, ast.FunctionDef) and n.name in ("_new_analysis", "_clear_basin")]
        assert len(functions) == 2
        events = SimpleNamespace(nav_new=reactive.value(ActionButtonValue(0)),
                                 clear_basin=reactive.value())
        resets = []
        scope = {"reactive": reactive, "input": events, "_reset": lambda: resets.append("reset")}
        exec(compile(ast.Module(body=functions, type_ignores=[]), "easi/app.py", "exec"), scope)
        try:
            await reactive.flush()
            assert resets == []
            events.nav_new.set(ActionButtonValue(1))
            await reactive.flush()
            assert resets == ["reset"]
            events.clear_basin.set(ActionButtonValue(0))  # Basin mounts its action input.
            await reactive.flush()
            assert resets == ["reset"]
            events.clear_basin.set(ActionButtonValue(1))
            await reactive.flush()
            assert resets == ["reset", "reset"]
            events.nav_new.set(ActionButtonValue(2))
            await reactive.flush()
            assert resets == ["reset", "reset", "reset"]
        finally:
            scope["_new_analysis"].destroy()
            scope["_clear_basin"].destroy()
    asyncio.run(run())
