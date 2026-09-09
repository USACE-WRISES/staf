"""Report preparation preserves the workspace and rejects superseded requests."""
from __future__ import annotations

import ast
import asyncio
import copy
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

app = pytest.importorskip("app")
SERVER = next(n for n in ast.parse(Path(app.__file__).read_text(encoding="utf-8")).body
              if isinstance(n, ast.FunctionDef) and n.name == "server")
DEEP = hasattr(app, "STEP_MEASURE")
ORIGIN = app.STEP_MEASURE if DEEP else app.STEP_REVIEW
GEOMETRY = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {}, "geometry": {
        "type": "LineString", "coordinates": [[-72.3, 43.7], [-72.2, 43.6]]}}]}


class Value:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = value


class Task:
    def __init__(self):
        self.calls = []
        self.canceled = 0
        self.completed = None

    def __call__(self, *args):
        self.calls.append(args)

    def cancel(self):
        self.canceled += 1

    def result(self):
        if self.completed is None:
            raise RuntimeError("pending")
        return self.completed


def function(name, ns):
    node = copy.deepcopy(next(n for n in ast.walk(SERVER)
                              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                              and n.name == name))
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), app.__file__, "exec"), ns)
    return ns[name]


def scope():
    task = Task()
    shown, notices = [], []
    ns = {**vars(app), "reactive": SimpleNamespace(isolate=nullcontext),
          "current_step": Value(ORIGIN), "current_fn": Value(0),
          "delin": Value({"watershed_geojson": copy.deepcopy(GEOMETRY), "reach_geojson": None}),
          "loaded_assessment": Value(object()), "_map_pick": {"generation": 7},
          "source_lookup": Value({"status": "ready", "generation": 7}),
          "_report_serial": {"value": 0}, "_report_request": Value(None),
          "_report_state": Value({"busy": False, "requestId": 0, "opened": False}),
          "report_map_task": task, "latest_score": Value(2),
          "ui": SimpleNamespace(modal_show=lambda modal: shown.append(modal),
                                notification_show=lambda *a, **k: notices.append(a)),
          "shown": shown, "notices": notices}
    ns["_current_delineation"] = lambda: ns["delin"]() is not None
    ns["_any_scored"] = lambda: ns["latest_score"]() is not None
    ns["_has"] = lambda target: True
    ns["_report_modal"] = lambda **kwargs: (ns["latest_score"](), kwargs["minimap_html"])
    for name in ("_report_identity", "_finish_report", "_cancel_report", "_request_report",
                 "_open_report", "_cancel_obsolete_report", "_report_map_done", "_stepper_nav"):
        function(name, ns)
    return ns


def test_open_report_deduplicates_and_copies_geometry_without_changing_workspace():
    ns = scope()
    ns["_open_report"]()
    ns["_open_report"]()
    assert len(ns["report_map_task"].calls) == 1
    assert ns["current_step"]() == ORIGIN and ns["shown"] == []
    assert ns["_report_state"]() == {"busy": True, "requestId": 1, "opened": False}
    ns["delin"]()["watershed_geojson"]["features"].clear()
    assert ns["report_map_task"].calls[0][1]["features"]  # worker owns its snapshot


def test_completed_report_uses_latest_scores_once_and_keeps_origin():
    ns = scope()
    ns["_open_report"]()
    ns["latest_score"].set(11)
    ns["report_map_task"].completed = (1, "<svg>prepared</svg>", None)
    ns["_report_map_done"]()
    ns["_report_map_done"]()
    assert ns["shown"] == [(11, "<svg>prepared</svg>")]
    assert ns["current_step"]() == ORIGIN
    assert ns["_report_state"]() == {"busy": False, "requestId": 1, "opened": True}


@pytest.mark.parametrize("change", ["navigation", "new_pick", "clear", "geometry"]
                         + (["assessment"] if DEEP else []))
def test_changed_context_cancels_and_late_result_cannot_open(change):
    ns = scope()
    ns["_open_report"]()
    if change == "navigation":
        ns["current_step"].set(app.STEP_BASIN)
    elif change == "new_pick":
        ns["_map_pick"]["generation"] += 1  # imports and replacement picks advance this token
    elif change == "clear":
        ns["delin"].set(None)
    elif change == "geometry":
        ns["delin"].set({"watershed_geojson": None, "reach_geojson": GEOMETRY})
    else:
        ns["loaded_assessment"].set(object())
    ns["_cancel_obsolete_report"]()
    assert ns["report_map_task"].canceled == 1
    assert ns["_report_request"]() is None and ns["_report_state"]()["busy"] is False
    ns["report_map_task"].completed = (1, "stale", None)
    ns["_report_map_done"]()
    assert ns["shown"] == []


def test_late_previous_request_cannot_clear_or_replace_new_request():
    ns = scope()
    ns["_open_report"]()
    ns["_cancel_report"]()
    ns["_open_report"]()
    ns["report_map_task"].completed = (1, "old", None)
    ns["_report_map_done"]()
    assert ns["_report_state"]()["busy"] is True
    assert ns["_report_request"]()["id"] == 2 and ns["shown"] == []
    ns["report_map_task"].completed = (2, "new", None)
    ns["_report_map_done"]()
    assert ns["shown"] == [(2, "new")]


def test_report_stepper_opens_overlay_and_same_step_navigation_cancels_pending():
    ns = scope()
    ns["input"] = SimpleNamespace(step_nav=lambda: {"key": app.STEP_REPORT})
    ns["_stepper_nav"]()
    assert ns["current_step"]() == ORIGIN and len(ns["report_map_task"].calls) == 1
    ns["input"] = SimpleNamespace(step_nav=lambda: {"key": ORIGIN})
    ns["_stepper_nav"]()
    assert ns["_report_request"]() is None and ns["current_step"]() == ORIGIN


@pytest.mark.parametrize("event", ["_nav_move", "_nav_jump", "_help", "_about"])
def test_function_navigation_or_another_dialog_cancels_preparing_report(event):
    ns = scope()
    ns["_open_report"]()
    ns["input"] = SimpleNamespace(nav_move=lambda: {"d": 1}, nav_jump=lambda: {"i": 1})
    ns["_fns"] = lambda: [1, 2, 3]
    ns["ui"] = SimpleNamespace(**{**vars(app.ui), "modal_show": lambda modal: None})
    function(event, ns)()
    assert ns["_report_request"]() is None


@pytest.mark.parametrize("failure", ["map", "builder"])
def test_failure_keeps_workspace_and_releases_busy_for_retry(failure):
    ns = scope()
    ns["_open_report"]()
    ns["report_map_task"].completed = (1, "", "Could not prepare." if failure == "map" else None)
    if failure == "builder":
        ns["_report_modal"] = lambda **kwargs: (_ for _ in ()).throw(ValueError("bad markup"))
    ns["_report_map_done"]()
    assert ns["current_step"]() == ORIGIN and ns["shown"] == [] and ns["notices"]
    assert ns["_report_state"]() == {"busy": False, "requestId": 1, "opened": False}
    ns["_open_report"]()
    assert len(ns["report_map_task"].calls) == 2


def test_map_worker_uses_only_arguments_and_returns_prepared_fallback():
    async def run_sync(fn, **kwargs):
        assert kwargs["abandon_on_cancel"] is True
        return fn()
    ns = scope()
    calls = []
    ns["anyio"] = SimpleNamespace(to_thread=SimpleNamespace(run_sync=run_sync))
    ns["delin"] = lambda: pytest.fail("worker read reactive state")
    ns["reportmap"] = SimpleNamespace(svg=lambda watershed, reach: calls.append(
        (watershed, reach)) or "outline with Map background unavailable")
    ns["delineation"] = SimpleNamespace(display_simplify=lambda geometry, **k: geometry)
    result = asyncio.run(function("report_map_task", ns)(4, GEOMETRY, None))
    assert result == (4, "outline with Map background unavailable", None)
    assert calls == [(GEOMETRY, None)]


def test_busy_updates_and_completion_do_not_rebuild_worksheet_or_reopen_on_edits():
    from shiny import reactive

    async def run():
        ns = scope()
        ns["reactive"] = reactive
        for key in ("current_step", "source_lookup", "_report_request", "_report_state", "latest_score"):
            ns[key] = reactive.value(ns[key]())
        renders = []
        ns["ui"] = SimpleNamespace(**{**vars(app.ui), "modal_show": ns["shown"].append,
                                     "notification_show": lambda *a, **k: None})
        render_pane = function("worksheet", ns)
        render_effect = reactive.effect(lambda: renders.append(str(render_pane())))
        cancel_effect = reactive.effect(ns["_cancel_obsolete_report"])
        done_effect = None
        try:
            await reactive.flush()
            with reactive.isolate():
                ns["_open_report"]()
            await reactive.flush()
            assert len(renders) == 1
            ns["latest_score"].set(11)
            await reactive.flush()
            with reactive.isolate():
                assert ns["_report_request"]() is not None  # editing is not navigation
            ns["report_map_task"].completed = (1, "map", None)
            done_effect = reactive.effect(ns["_report_map_done"])
            await reactive.flush()
            assert len(renders) == 1 and ns["shown"] == [(11, "map")]
            ns["latest_score"].set(15)
            await reactive.flush()
            assert len(renders) == 1 and len(ns["shown"]) == 1
        finally:
            render_effect.destroy()
            cancel_effect.destroy()
            if done_effect:
                done_effect.destroy()

    asyncio.run(run())


def test_modal_builder_requires_prepared_map_and_never_calls_map_service():
    ns = scope()
    ns["ui"] = app.ui
    ns["reportmap"] = SimpleNamespace(svg=lambda *a, **k: pytest.fail("duplicate map fetch"))
    if DEEP:
        la = app.assessments.LoadedAssessment.from_dict({
            "assessmentId": "fixture", "assessmentName": "Fixture", "metricsByFunction": []})
        ns["loaded_assessment"].set(la)
        ns["_fns"] = lambda: []
        ns["measured_values"] = Value({})
        ns["scored"] = lambda: app.curves.score_site(la, {})
    else:
        ns["metric_scores"] = Value({})
        ns["function_scores"] = Value({})
        ns["evidence"] = Value({})
        ns["scored"] = lambda: app.scoring.score_assessment({})
    markup = str(function("_report_modal", ns)(minimap_html="<svg>prepared-marker</svg>"))
    assert "prepared-marker" in markup
