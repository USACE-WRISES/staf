"""Run EASI's report request/completion functions against delayed task state."""
import asyncio
import copy
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from test_map_pick_lifecycle import _function
from test_streamcat_readiness import Value


class Task:
    def __init__(self):
        self.calls = []
        self.state = "initial"
        self.answer = None
        self.cancels = 0

    def __call__(self, *args):
        self.calls.append(args)
        self.state = "running"

    def status(self):
        return self.state

    def result(self):
        return self.answer

    def cancel(self):
        self.cancels += 1

    def finish(self, request_id, image="<svg>prepared</svg>", error=None):
        self.state = "success"
        self.answer = request_id, image, error


def harness(value=Value):
    base = {"delineation": {"comid": 9327042}, "report": {},
            "watershed_geojson": {"features": [{"geometry": {"coordinates": [[[1, 2], [2, 3]]]}}]},
            "reach_geojson": None}
    task = Task()
    shown, notices = [], []
    scores, notes, units = value(2), value({"m": "before"}), value("m")
    scope = {
        "copy": copy, "_report_request": value(None), "_report_counter": {"value": 0},
        "_report_ui": value({"busy": False, "requestId": 0}),
        "_map_pick": {"generation": 1}, "app_mode": value("single"),
        "current_step": value("assess"), "current_fn": value(0),
        "base_result": value(base), "batch_result": value(None), "batch_modal_site": value(None),
        "report_map_task": task, "_notes": notes, "_xs_unit_prev": units,
        "export_result": lambda: {**base, "report": {"score": scores()}},
        "_report_modal": lambda res, ns, minimap_html: (res["report"], ns, minimap_html),
        "_batch_report_modal": lambda site_id, res, minimap_html: (site_id, res, minimap_html),
        "ui": SimpleNamespace(modal_show=shown.append,
                              notification_show=lambda message, **kwargs: notices.append(message)),
        "reactive": SimpleNamespace(isolate=nullcontext),
    }
    for name in ("_report_context_matches", "_cancel_report", "_cancel_stale_report",
                 "_begin_report", "_show_report_modal", "_report_map_done"):
        scope[name] = _function("easi", name, scope)
    return SimpleNamespace(scope=scope, task=task, base=base, shown=shown,
                           scores=scores, notes=notes, units=units, notices=notices)


def test_prepare_is_deduplicated_and_does_not_change_workspace():
    h = harness()
    h.scope["_show_report_modal"]()
    h.scope["_show_report_modal"]()
    assert len(h.task.calls) == 1
    assert h.scope["current_step"]() == "assess" and not h.shown
    assert h.scope["_report_ui"]()["busy"]
    assert h.task.calls[0][1]["watershed"] is not h.base["watershed_geojson"]


def test_completion_uses_edits_made_while_waiting_and_preserves_units():
    h = harness()
    h.scope["_show_report_modal"]()
    h.scores.set(9)
    h.notes.set({"m": "edited while loading"})
    h.task.finish(1)
    h.scope["_report_map_done"]()
    assert h.shown == [({"score": 9}, {"m": "edited while loading"}, "<svg>prepared</svg>")]
    assert h.units() == "m"
    assert h.scope["current_step"]() == "assess"
    assert h.scope["_report_ui"]() == {"busy": False, "requestId": 1, "opened": True}
    h.scope["_report_map_done"]()
    assert len(h.shown) == 1


@pytest.mark.parametrize("change", ["selection", "step", "function", "mode", "result", "geometry"])
def test_invalidated_request_cannot_open_late(change):
    h = harness()
    h.scope["_show_report_modal"]()
    if change == "selection":
        h.scope["_map_pick"]["generation"] += 1
    elif change == "geometry":
        h.base["watershed_geojson"]["features"].clear()
    else:
        name, value = {"step": ("current_step", "basin"), "function": ("current_fn", 1),
                       "mode": ("app_mode", "batch"), "result": ("base_result", None)}[change]
        h.scope[name].set(value)
    h.task.finish(1)
    h.scope["_report_map_done"]()
    assert not h.shown
    assert h.scope["_report_request"]() is None
    assert not h.scope["_report_ui"]()["busy"]


def test_cancelled_worker_cannot_finish_a_new_request():
    h = harness()
    h.scope["_show_report_modal"]()
    h.scope["_cancel_report"]()
    h.scope["_show_report_modal"]()
    h.task.finish(1)
    h.scope["_report_map_done"]()
    assert not h.shown and h.scope["_report_request"]()["id"] == 2
    assert h.scope["_report_ui"]()["busy"]
    h.task.finish(2)
    h.scope["_report_map_done"]()
    assert len(h.shown) == 1


def test_rendering_failure_clears_busy_and_allows_retry():
    h = harness()
    h.scope["_show_report_modal"]()
    h.task.finish(1, error="Cannot prepare report")
    h.scope["_report_map_done"]()
    assert h.notices == ["Cannot prepare report"] and not h.shown
    assert not h.scope["_report_ui"]()["busy"]
    h.scope["_show_report_modal"]()
    assert len(h.task.calls) == 2


def test_batch_waits_without_changing_workspace_and_sets_download_site_on_open():
    h = harness()
    batch = SimpleNamespace(sites=[SimpleNamespace(site_id="example", metadata={"_artifacts": h.base})])
    h.scope["batch_result"].set(batch)
    h.scope["app_mode"].set("batch")
    h.scope["_begin_report"](h.base, batch=batch, index=0)
    assert h.scope["batch_modal_site"]() is None
    h.task.finish(1)
    h.scope["_report_map_done"]()
    assert h.shown[0][0] == "example"
    assert h.scope["batch_modal_site"]()["base"] is h.base
    assert h.scope["app_mode"]() == "batch"


def test_replaced_batch_rejects_pending_report():
    h = harness()
    batch = SimpleNamespace(sites=[SimpleNamespace(site_id="example", metadata={"_artifacts": h.base})])
    h.scope["batch_result"].set(batch)
    h.scope["app_mode"].set("batch")
    h.scope["_begin_report"](h.base, batch=batch, index=0)
    h.scope["batch_result"].set(None)
    h.task.finish(1)
    h.scope["_report_map_done"]()
    assert not h.shown and not h.scope["_report_ui"]()["busy"]


def test_prepared_map_never_fetches_again_during_modal_assembly(monkeypatch):
    import app
    monkeypatch.setattr(app.reportmap, "svg", lambda *args, **kwargs: pytest.fail("second map request"))
    assert "prepared" in str(app._header_with_map({}, {}, {"watershed": {}}, "<svg>prepared</svg>"))
    assert "sfari-minimap" not in str(app._header_with_map({}, {}, {"watershed": {}}, ""))


def test_actual_reactive_progress_does_not_rebuild_workspace_or_reopen_report():
    from shiny import reactive

    async def run():
        with reactive.isolate():
            h = harness(reactive.Value)
        renders = []

        @reactive.effect
        def workspace():
            renders.append(h.scope["current_step"]())

        await reactive.flush()
        with reactive.isolate():
            h.scope["_show_report_modal"]()
        await reactive.flush()
        with reactive.isolate():
            h.scores.set(6)
            h.task.finish(1)
            h.scope["_report_map_done"]()
        await reactive.flush()
        assert renders == ["assess"]
        assert len(h.shown) == 1 and h.shown[0][0]["score"] == 6
        workspace.destroy()

    asyncio.run(run())
