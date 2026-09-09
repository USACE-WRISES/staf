"""Interactive analysis must use a source resolved for the current selection."""
import asyncio
from types import SimpleNamespace

import pytest

from test_map_pick_lifecycle import _function, _stop


class Value:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = value


POINT = (43.68583, -72.23669, 0.0, 9327042)


@pytest.mark.parametrize("status", ["idle", "snapping", "finding", "retrying", "failed", "no_match"])
def test_only_resolved_current_source_is_ready(status):
    state = Value({"status": status, "generation": 3, "comid": POINT[3], "point": POINT[:2]})
    point = Value(POINT)
    ready = _function("easi", "_source_ready", {
        "source_lookup": state, "snapped_point": point, "_map_pick": {"generation": 3}})
    assert not ready()
    state.value["status"] = "ready"
    assert ready()
    state.value["generation"] = 2
    assert not ready()
    state.value["generation"] = 3
    point.value = (43.68, -72.23, 0.0, POINT[3])
    assert not ready()


@pytest.mark.parametrize("comid", [None, 0, -1, True, 1.5, "9327042"])
def test_ready_label_cannot_authorize_an_invalid_source(comid):
    point = (*POINT[:3], comid)
    ready = _function("easi", "_source_ready", {
        "source_lookup": lambda: {"status": "ready", "generation": 3,
                                   "comid": comid, "point": point[:2]},
        "snapped_point": lambda: point, "_map_pick": {"generation": 3}})
    assert not ready()


@pytest.mark.parametrize("handler", ["_start_delineate", "_run_screening", "_autostart_assess"])
def test_server_handlers_block_raw_coordinates_and_unresolved_sources(handler):
    fn = _function("easi", handler, {
        "_source_ready": lambda: False, "_analysis_ready": lambda: False,
        "current_step": lambda: "assess", "STEP_ASSESS": "assess",
        "input": SimpleNamespace(lat=_stop, lon=_stop), "snapped_point": _stop,
        "delineate_task": _stop, "assess_task": _stop,
    })
    fn()  # Neither task nor raw coordinate access is allowed.


@pytest.mark.parametrize("error,status", [("no_stream_found", "no_match"),
                                         ("snap_service_error", "failed")])
def test_failed_lookup_keeps_point_and_exposes_terminal_status(error, status):
    state = Value({"status": "finding"})
    point = Value((*POINT[:3], None))
    done = _function("easi", "_route_done", {
        "route_task": SimpleNamespace(result=lambda: (3, {"error": error, "detail": "HTTP 502"})),
        "_map_pick": {"generation": 3}, "source_lookup": state,
        "snapped_point": point, "stage": Value(""), "_remove_layer": _stop,
    })
    done()
    assert state()["status"] == status
    assert point() == (*POINT[:3], None)


def test_retry_reuses_snapped_point_and_starts_new_cycle():
    hit = (*POINT[:3], 10000900049501)
    request = {"lat": POINT[0], "lon": POINT[1], "hit": hit}
    generation = {"generation": 3}
    calls = []
    retry = _function("easi", "_retry_streamcat", {
        "source_lookup": lambda: {"status": "failed"}, "_hr_route": request,
        "_map_pick": generation, "_clear_route_state": request.clear,
        "_invalidate_analysis": lambda: None,
        "_start_route": lambda *args: calls.append(args),
    })
    retry()
    assert generation["generation"] == 4
    assert calls == [(POINT[0], POINT[1], hit)]


@pytest.mark.parametrize("task,handler,owner", [
    ("delineate_task", "_delineate_done", "delineate"),
    ("assess_task", "_assess_done", "assess"),
])
def test_stale_analysis_result_cannot_clear_current_progress(task, handler, owner):
    done = _function("easi", handler, {
        task: SimpleNamespace(status=lambda: "success", result=lambda: (2, {"status": "ok"})),
        "_analysis_runs": {owner: 3}, "_map_pick": {"generation": 3},
        "_source_ready": lambda: True, "_analysis_ready": lambda: True,
        "stage": SimpleNamespace(set=_stop), "ui": SimpleNamespace(notification_remove=_stop),
    })
    done()


def test_geometry_failure_does_not_invalidate_source():
    async def fail(fn):
        raise TimeoutError("optional geometry unavailable")
    task = _function("easi", "source_geometry_task", {
        "anyio": SimpleNamespace(to_thread=SimpleNamespace(run_sync=fail)),
    })
    result = asyncio.run(task(POINT[3], 3))
    assert result == (3, {"comid": POINT[3], "feature": None})
    done = _function("easi", "_source_geometry_done", {
        "source_geometry_task": SimpleNamespace(result=lambda: result),
        "_map_pick": {"generation": 3}, "_source_ready": lambda: True,
        "source_lookup": lambda: {"comid": POINT[3]}, "_add_layer": _stop,
    })
    done()


def test_lookup_poller_settles_when_worker_progress_has_not_changed():
    from shiny import reactive

    async def run():
        value = reactive.value({"status": "finding", "generation": 3, "attempt": 1})
        writes = []
        class Lookup:
            def __call__(self):
                return value()

            def set(self, state):
                writes.append(state)
                assert len(writes) < 3, "lookup progress invalidated itself repeatedly"
                value.set(state)
        poll = _function("easi", "_lookup_poll", {
            "reactive": reactive, "source_lookup": Lookup(), "_map_pick": {"generation": 3},
            "_lookup_progress": {"status": "retrying", "attempt": 2, "generation": 3},
        })
        effect = reactive.effect(poll)
        try:
            await reactive.flush()
            assert len(writes) == 1
        finally:
            effect.destroy()
    asyncio.run(run())


@pytest.mark.parametrize("status,detail,expected", [
    ("finding", "", "Finding the nearest StreamCat reach"),
    ("retrying", "", "Retrying StreamCat lookup (1 of 2)"),
    ("failed", "HTTP 502: private diagnostics", "Could not reach the StreamCat routing service"),
    ("failed", "flowtrace: unexpected response shape", "returned an invalid response"),
    ("no_match", "", "No StreamCat reach was found downstream"),
])
def test_persistent_accessible_status_and_manual_retry(status, detail, expected):
    from shiny import ui
    render = _function("easi", "snap_status", {
        "source_lookup": lambda: {"status": status, "detail": detail},
        "_hr_route": {"hit": POINT}, "ui": ui,
    })
    html = str(render())
    assert expected in html
    assert 'role="status"' in html and 'aria-live="polite"' in html
    assert ('id="retry_streamcat"' in html) == (status in ("failed", "no_match"))
    assert "private diagnostics" not in html and "unexpected response shape" not in html
