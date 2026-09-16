"""Run the actual asynchronous viewer handlers against stale and cached picks."""
import asyncio
from types import SimpleNamespace

import pytest

from easi.national.client import DatasetError
from test_map_pick_lifecycle import _function
from test_report_opening import Task
from test_streamcat_readiness import Value


@pytest.mark.parametrize("tiles,expected", [
    ({"01": {"minzoom": 4, "maxzoom": 12}, "02": {"minzoom": 8, "maxzoom": 10}}, (8, 10)),
    ({"01": {"minzoom": 4}}, None),
    ({}, None),
])
def test_config_worker_derives_zoom_range_or_marks_dataset_unavailable(tiles, expected):
    from easi.national import tiles as national_tiles
    checked, stats_calls = [], []
    def require_current(key):
        checked.append(key)
        return {"tiles": tiles}
    ds = SimpleNamespace(summary_refreshed=lambda: {"available": True, "dataset_key": "current"},
                         require_current=require_current,
                         current_stats=lambda key: stats_calls.append(key) or {"count": 1})
    async def run_sync(fn, *args):
        return fn(*args)
    worker = _function("easi", "viewer_config_task", {
        "national_client": SimpleNamespace(default_dataset=lambda: ds), "national_tiles": national_tiles,
        "anyio": SimpleNamespace(to_thread=SimpleNamespace(run_sync=run_sync))})
    generation, summary, stats = asyncio.run(worker(3))
    assert generation == 3 and checked == ["current"]
    if expected:
        assert (summary["minzoom"], summary["maxzoom"]) == expected
        assert summary["available"] and stats == {"count": 1}
    else:
        assert not summary["available"] and "tile zoom range" in summary["error"]
        assert stats is None and not stats_calls


def config_request_harness(mode="viewer", count=None):
    action = {"count": count}
    def refresh():
        if action["count"] is None:
            raise RuntimeError("The dynamic input is not mounted")
        return action["count"]
    task, cancellations, removals = Task(), [], []
    scope = {"input": SimpleNamespace(viewer_refresh=refresh), "app_mode": Value(mode),
             "_viewer_config_trigger": {"active": False, "refresh": 0},
             "_cancel_report": lambda: cancellations.append(True),
             "viewer_base": Value(None), "batch_modal_site": Value(None),
             "viewer_stats": Value(None), "_viewer_records": {},
             "_viewer_gen": {"value": 1}, "_viewer_dataset_gen": {"value": 1},
             "viewer_config_task": task, "ui": SimpleNamespace(modal_remove=lambda: removals.append(True))}
    return SimpleNamespace(scope=scope, action=action, task=task, cancellations=cancellations,
                           removals=removals, request=_function("easi", "_viewer_config_request", scope))


@pytest.mark.parametrize("initial_count", [None, 0])
def test_config_entry_loads_once_and_initial_action_zero_preserves_current_work(initial_count):
    h = config_request_harness(count=initial_count)
    h.request()
    assert h.task.calls == [(2,)]
    base, stats = {"report": "already opened"}, {"reaches": 17}
    h.scope["viewer_base"].set(base)
    h.scope["viewer_stats"].set(stats)
    h.scope["_viewer_records"][17] = base
    h.action["count"] = 0
    h.request()
    h.request()
    assert h.task.calls == [(2,)] and len(h.cancellations) == 1 and not h.removals
    assert h.scope["_viewer_dataset_gen"]["value"] == 2
    assert h.scope["_viewer_gen"]["value"] == 2
    assert h.scope["viewer_base"]() is base and h.scope["viewer_stats"]() is stats
    assert h.scope["_viewer_records"] == {17: base}


def test_real_refresh_loads_once_per_increment_and_clears_bound_report():
    h = config_request_harness(count=0)
    h.request()
    h.scope["viewer_base"].set({"report": "previous"})
    h.scope["batch_modal_site"].set({"base": {"report": "previous"}})
    h.scope["viewer_stats"].set({"reaches": 17})
    h.scope["_viewer_records"][17] = {}
    for count in (1, 1, None, 1, 2, 2):
        h.action["count"] = count
        h.request()
    assert h.task.calls == [(2,), (3,), (4,)]
    assert len(h.cancellations) == 3 and len(h.removals) == 1
    assert h.scope["viewer_base"]() is None and h.scope["batch_modal_site"]() is None
    assert h.scope["viewer_stats"]() is None and not h.scope["_viewer_records"]


def test_action_input_remount_zero_is_ignored_but_its_first_click_refreshes():
    h = config_request_harness(count=0)
    h.request()
    for count in (1, 2, None, 0, 0):
        h.action["count"] = count
        h.request()
    assert h.task.calls == [(2,), (3,), (4,)]
    h.action["count"] = 1
    h.request()
    assert h.task.calls == [(2,), (3,), (4,), (5,)]


@pytest.mark.parametrize("remount_count", [None, 0, 2])
def test_off_on_always_loads_once_even_when_refresh_count_is_retained_or_reset(remount_count):
    h = config_request_harness(mode="single", count=None)
    h.request()
    assert not h.task.calls and not h.cancellations
    h.scope["app_mode"].set("viewer")
    h.action["count"] = 0
    h.request()
    h.action["count"] = 2
    h.request()
    h.scope["app_mode"].set("single")
    h.request()
    h.action["count"] = remount_count
    h.request()
    assert h.task.calls == [(2,), (3,)]
    h.scope["app_mode"].set("viewer")
    h.request()
    h.action["count"] = 0
    h.request()
    assert h.task.calls == [(2,), (3,), (4,)]
    h.action["count"] = 1
    h.request()
    assert h.task.calls == [(2,), (3,), (4,), (5,)]


def pick_harness(event):
    cached = {"delineation": {"comid": 17}, "geometry_pending": True,
              "_viewer_binding": {"dataset_key": "current", "generation": 2}}
    generation = {"value": 4}
    dataset_generation = {"value": 2}
    opened, checks = [], []
    geometry = Task()
    async def run_sync(fn, *args):
        return fn(*args)
    def require_current(key):
        checks.append(key)
    scope = {
        "input": SimpleNamespace(viewer_pick=lambda: event), "app_mode": lambda: "viewer",
        "viewer_summary": lambda: {"available": True, "dataset_key": "current"},
        "_viewer_gen": generation, "_viewer_dataset_gen": dataset_generation,
        "_viewer_records": {17: cached}, "_open_viewer_report": opened.append,
        "_cancel_report": lambda: None,
        "viewer_geometry_task": geometry, "open_precomputed_task": Task(),
        "anyio": SimpleNamespace(to_thread=SimpleNamespace(run_sync=run_sync)),
        "national_client": SimpleNamespace(DatasetError=DatasetError,
            default_dataset=lambda: SimpleNamespace(require_current=require_current)),
    }
    return scope, _function("easi", "_viewer_pick", scope), opened, checks, geometry


@pytest.mark.parametrize("event", [
    {"comid": 17, "datasetKey": "old", "generation": 2},
    {"comid": 17, "datasetKey": "current", "generation": 1},
    {"comid": "bad", "datasetKey": "current", "generation": 2},
])
def test_stale_pick_rejected_before_any_work(event):
    _, pick, opened, checks, geometry = pick_harness(event)
    asyncio.run(pick())
    assert not opened and not checks and not geometry.calls


def test_cached_pick_supersedes_old_geometry_and_restarts_pending_thumbnail():
    scope, pick, opened, checks, geometry = pick_harness(
        {"comid": 17, "datasetKey": "current", "generation": 2})
    asyncio.run(pick())
    assert checks == ["current"]
    assert scope["_viewer_gen"]["value"] == 5
    assert opened == [scope["_viewer_records"][17]]
    assert geometry.calls == [(17, 5, {"dataset_key": "current", "generation": 2})]


@pytest.mark.parametrize("cached", [True, False])
def test_accepted_pick_cancels_an_older_report_render_before_new_work(cached):
    scope, _, opened, _, _ = pick_harness({"comid": 17, "datasetKey": "current", "generation": 2})
    events = []
    scope["_cancel_report"] = lambda: events.append("cancel")
    scope["_open_viewer_report"] = lambda base: events.append("open")
    if not cached:
        scope["_viewer_records"].clear()
        class NewTask:
            def status(self): return "initial"
            def __call__(self, *args): events.append("score")
        scope["open_precomputed_task"] = NewTask()
    asyncio.run(_function("easi", "_viewer_pick", scope)())
    assert events == ["cancel", "open" if cached else "score"]


@pytest.mark.parametrize("different_selection,generation", [(True, 5), (False, 4)])
def test_geometry_completion_cannot_mutate_another_or_superseded_report(different_selection, generation):
    base = {"geometry_pending": True}
    task = SimpleNamespace(status=lambda: "success", result=lambda: (
        17, generation, {"dataset_key": "current", "generation": 2},
        {"status": "ok", "watershed_geojson": {"new": True}}, "<svg/>"))
    handler = _function("easi", "_viewer_geometry_done", {
        "viewer_geometry_task": task, "_viewer_gen": {"value": 5},
        "app_mode": lambda: "viewer", "_viewer_records": {17: base},
        "viewer_base": lambda: {} if different_selection else base,
    })
    handler()
    assert base == {"geometry_pending": True}


@pytest.mark.parametrize("change_during_read,expected_status", [(False, 200), (True, 409)])
def test_tile_response_is_withheld_if_generation_changes_during_read(change_during_read, expected_status):
    generation = {"value": 3}
    def tile(*args):
        if change_during_read:
            generation["value"] = 4
        return b"tile payload", "gzip", "application/x-protobuf"
    async def run_sync(fn, *args):
        return fn(*args)
    ds = SimpleNamespace(require_current=lambda *args: {"tiles": {"01": {}}},
                         is_current_key=lambda key: key == "current")
    handler = _function("easi", "_national_tiles_handler", {
        "_viewer_dataset_gen": generation,
        "national_client": SimpleNamespace(default_dataset=lambda: ds, DatasetError=DatasetError),
        "national_tiles": SimpleNamespace(default_store=lambda: SimpleNamespace(tile=tile), TileReadError=RuntimeError),
        "anyio": SimpleNamespace(to_thread=SimpleNamespace(run_sync=run_sync)),
    })
    request = SimpleNamespace(query_params={"datasetKey": "current", "generation": "3",
                                           "vpu": "01", "z": "4", "x": "3", "y": "5"})
    response = asyncio.run(handler(request))
    assert response.status_code == expected_status
    if change_during_read:
        assert response.body == b"" and response.headers["cache-control"] == "no-store"
    else:
        assert response.body == b"tile payload" and response.headers["content-encoding"] == "gzip"


def binding_harness(*, current=True, generation=2, mode="viewer", damaged=False):
    checked = []
    def require_current(key):
        checked.append(key)
        if damaged or not current:
            raise DatasetError("The bundle changed or is damaged")
    ds = SimpleNamespace(require_current=require_current, is_current_key=lambda key: current and key == "current")
    scope = {"viewer_summary": lambda: {"available": True, "dataset_key": "current"},
             "app_mode": lambda: mode, "_viewer_dataset_gen": {"value": generation},
             "national_client": SimpleNamespace(default_dataset=lambda: ds, DatasetError=DatasetError)}
    return _function("easi", "_viewer_binding_current", scope), checked


@pytest.mark.parametrize("patch", [{"current": False}, {"generation": 3}, {"mode": "single"}, {"damaged": True}])
def test_viewer_downloads_refuse_stale_replaced_or_damaged_bundle(patch):
    valid, checked = binding_harness(**patch)
    base = {"_viewer_binding": {"dataset_key": "current", "generation": 2}, "report": {}}
    notices = []
    getter = _function("easi", "_modal_download_base", {
        "batch_modal_site": lambda: {"base": base}, "_viewer_binding_current": valid,
        "ui": SimpleNamespace(notification_show=lambda text, **kw: notices.append(text))})
    assert getter() is None and notices
    for name, producer in (("dl_site_pdf", "build_pdf"), ("dl_site_csv", "build_csv"), ("dl_site_geojson", "build_geojson")):
        download = _function("easi", name, {"_modal_download_base": getter,
            "report": SimpleNamespace(**{producer: lambda value: pytest.fail("stale export was built")})})
        assert list(download()) == []


def test_download_guard_keeps_batch_exports_and_valid_viewer_exports():
    valid, checked = binding_harness()
    for base in ({"report": {}}, {"report": {}, "_viewer_binding": {"dataset_key": "current", "generation": 2}}):
        getter = _function("easi", "_modal_download_base", {
            "batch_modal_site": lambda: {"base": base}, "_viewer_binding_current": valid})
        assert getter() is base
    assert checked == ["current"]


def test_dashboard_download_does_not_export_cached_statistics_after_replacement():
    valid, _ = binding_harness(current=False)
    notices = []
    download = _function("easi", "dash_export", {
        "viewer_stats": lambda: {"reaches": 2}, "viewer_summary": lambda: {"dataset_key": "current"},
        "_viewer_dataset_gen": {"value": 2}, "_viewer_binding_current": valid,
        "ui": SimpleNamespace(notification_show=lambda text, **kw: notices.append(text)),
        "national_dashboard": SimpleNamespace(export_csv=lambda stats: pytest.fail("stale dashboard exported"))})
    assert list(download()) == [] and notices


def test_geometry_completion_cannot_mix_a_replaced_bundle_into_cached_report():
    binding = {"dataset_key": "current", "generation": 2}
    base = {"geometry_pending": True, "_viewer_binding": binding}
    valid, _ = binding_harness(current=False)
    task = SimpleNamespace(status=lambda: "success", result=lambda: (
        17, 5, binding, {"status": "ok", "watershed_geojson": {"new": True}}, "<svg/>"))
    handler = _function("easi", "_viewer_geometry_done", {
        "viewer_geometry_task": task, "_viewer_gen": {"value": 5}, "_viewer_binding_current": valid,
        "app_mode": lambda: "viewer", "_viewer_records": {17: base}, "viewer_base": lambda: base})
    handler()
    assert base == {"geometry_pending": True, "_viewer_binding": binding}


def test_geometry_task_rechecks_original_key_after_remote_geometry_finishes():
    checks = []
    def require_current(key):
        checks.append(key)
        if len(checks) == 2:
            raise DatasetError("Bundle was replaced during geometry fetch")
    async def geometry(comid):
        return {"status": "ok", "watershed_geojson": {"new": True}}
    async def run_sync(fn, *args):
        return fn(*args)
    task = _function("easi", "viewer_geometry_task", {
        "national_client": SimpleNamespace(default_dataset=lambda: SimpleNamespace(require_current=require_current),
                                           precomputed_geometry_async=geometry),
        "anyio": SimpleNamespace(to_thread=SimpleNamespace(run_sync=run_sync)),
        "reportmap": SimpleNamespace(svg=lambda *args: pytest.fail("stale thumbnail rendered"))})
    binding = {"dataset_key": "original", "generation": 2}
    answer = asyncio.run(task(17, 5, binding))
    assert checks == ["original", "original"] and answer[2] is binding
    assert answer[3]["status"] == "error" and answer[4] == ""


def test_initial_archive_read_failure_is_502_not_cached_empty_tile():
    from easi.national import tiles
    def tile(*args):
        raise tiles.TileReadError("Header connection failed")
    async def run_sync(fn, *args):
        return fn(*args)
    handler = _function("easi", "_national_tiles_handler", {
        "_viewer_dataset_gen": {"value": 3},
        "national_client": SimpleNamespace(DatasetError=DatasetError, default_dataset=lambda:
            SimpleNamespace(require_current=lambda *args: {"tiles": {"01": {}}})),
        "national_tiles": SimpleNamespace(default_store=lambda: SimpleNamespace(tile=tile), TileReadError=tiles.TileReadError),
        "anyio": SimpleNamespace(to_thread=SimpleNamespace(run_sync=run_sync))})
    response = asyncio.run(handler(SimpleNamespace(query_params={"datasetKey": "current", "generation": "3",
        "vpu": "01", "z": "4", "x": "3", "y": "5"})))
    assert response.status_code == 502 and response.headers["cache-control"] == "no-store"


def test_rejected_current_pick_clears_preparing_report_status():
    scope, _, _, _, _ = pick_harness({"comid": 17, "datasetKey": "current", "generation": 2})
    def reject(key):
        raise DatasetError("The bundle changed")
    state = Value({"busy": True})
    scope.update({"national_client": SimpleNamespace(DatasetError=DatasetError,
        default_dataset=lambda: SimpleNamespace(require_current=reject)),
        "ui": SimpleNamespace(notification_show=lambda *args, **kwargs: None),
        "_report_ui": state, "_report_counter": {"value": 1}})
    asyncio.run(_function("easi", "_viewer_pick", scope)())
    assert state()["busy"] is False


def test_deferred_report_cannot_open_after_its_original_bundle_changes():
    from test_report_opening import harness
    h = harness()
    current = {"value": True}
    h.base["_viewer_binding"] = {"dataset_key": "original", "generation": 2}
    h.scope["viewer_base"] = Value(h.base)
    h.scope["_viewer_binding_current"] = lambda binding: current["value"] and binding == h.base["_viewer_binding"]
    for name in ("_report_context_matches", "_cancel_report", "_begin_report", "_report_map_done"):
        h.scope[name] = _function("easi", name, h.scope)
    h.scope["app_mode"].set("viewer")
    h.scope["_begin_report"](h.base)
    current["value"] = False
    h.task.finish(1)
    h.scope["_report_map_done"]()
    assert not h.shown and not h.scope["_report_ui"]()["busy"]


def test_opened_report_captures_original_dataset_identity_for_later_downloads():
    checks = []
    async def open_record(comid, **kwargs):
        return {"status": "ok", "report": {}, "geometry_pending": True}
    async def run_sync(fn, *args):
        return fn(*args)
    task = _function("easi", "open_precomputed_task", {
        "national_client": SimpleNamespace(open_precomputed_async=open_record,
            default_dataset=lambda: SimpleNamespace(require_current=checks.append)),
        "anyio": SimpleNamespace(to_thread=SimpleNamespace(run_sync=run_sync))})
    comid, request_generation, base = asyncio.run(task(17, 5, "original", 2))
    assert (comid, request_generation) == (17, 5)
    assert base["_viewer_binding"] == {"dataset_key": "original", "generation": 2}
    assert checks == ["original"]


def test_current_geometry_completion_updates_only_its_bound_report():
    binding = {"dataset_key": "current", "generation": 2}
    base = {"geometry_pending": True, "_viewer_binding": binding, "delineation": {"gnis_name": "Stored name"}}
    inserted, removed = [], []
    task = SimpleNamespace(status=lambda: "success", result=lambda: (
        17, 5, binding, {"status": "ok", "watershed_geojson": {"current": True},
            "delineation": {"gnis_name": "Remote name", "watershed_area_sqkm": 10}}, "<svg/>"))
    handler = _function("easi", "_viewer_geometry_done", {
        "viewer_geometry_task": task, "_viewer_gen": {"value": 5}, "_viewer_binding_current": lambda b: b == binding,
        "app_mode": lambda: "viewer", "_viewer_records": {17: base}, "viewer_base": lambda: base,
        "ui": SimpleNamespace(HTML=lambda text: text, insert_ui=lambda value, **kwargs: inserted.append(value),
                              remove_ui=removed.append)})
    handler()
    assert not base["geometry_pending"] and base["watershed_geojson"] == {"current": True}
    assert base["delineation"] == {"gnis_name": "Stored name", "watershed_area_sqkm": 10}
    assert inserted == ["<svg/>"] and removed == ["#easi-viewer-minimap-pending"]
