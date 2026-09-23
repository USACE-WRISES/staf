"""A direct StreamCat source must not pull the selected site off the HR stream."""
from __future__ import annotations

import asyncio
import copy
import math
from types import SimpleNamespace

import pytest

from easi import delineation, pipeline, routing
from test_map_pick_lifecycle import _function


CLICK = (40.101234, -83.201234)
V2 = (40.101100, -83.201400, 70.0, 1234567)
HR = (40.101230, -83.201230, 2.0, 41000100054321)
FEATURE = {"type": "Feature", "properties": {"comid": V2[3], "gnis_name": "Source Creek"},
           "geometry": {"type": "LineString", "coordinates": [[V2[1], V2[0]], [-83.2015, 40.1012]]}}
V2_FC = {"type": "FeatureCollection", "features": [FEATURE]}
HR_FC = {"type": "FeatureCollection", "features": [{"properties": {"nhdplusid": HR[3]}}]}


class Value:
    def __init__(self, value=None):
        self.value = value

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = value


class Task:
    def __init__(self, result=None):
        self.payload = result
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    def result(self):
        return self.payload

    def status(self):
        return "initial"

    def cancel(self):
        pass


def _scope(*, v2_fc=V2_FC, hr_fc=HR_FC, v2_hit=V2, hr_hit=HR, pending=None):
    state = SimpleNamespace(
        point=Value(), pending=Value(copy.deepcopy(pending)), scored=Value(),
        lookup=Value({"status": "idle"}), stage=Value(""), numeric={}, buttons=[],
        notifications=[], routes=[], fetches=[], layers={"marker": None, "scored": None},
        outages=[], hr_kwargs=[],
        map=SimpleNamespace(layers=[], center=CLICK, zoom=15))

    def add_layer(key, layer):
        state.layers[key] = layer
        state.map.layers.append(layer)

    def remove_layer(key):
        layer = state.layers.get(key)
        if layer in state.map.layers:
            state.map.layers.remove(layer)
        state.layers[key] = None

    def fetch_v2(*args):
        state.fetches.append(("v2", args))
        return V2_FC

    def fetch_hr(*args):
        state.fetches.append(("hr", args))
        return HR_FC

    def fetch_hr_status(*args, **kwargs):
        state.fetches.append(("hr", args))
        state.hr_kwargs.append(kwargs)
        return "ok", HR_FC

    namespace = {
        "math": math, "isfinite": math.isfinite, "routing": routing,
        "SNAP_TOL_FT": 150.0, "_map_pick": {"generation": 5},
        "snapped_point": state.point, "pending_anchor": state.pending,
        "scored_reach": state.scored, "source_lookup": state.lookup, "stage": state.stage,
        "_layers": state.layers, "_MAP": state.map, "_add_layer": add_layer,
        "_remove_layer": remove_layer,
        "_point_marker": lambda lat, lon: SimpleNamespace(location=(lat, lon)),
        "GeoJSON": lambda **kwargs: SimpleNamespace(**kwargs),
        "SCORED_REACH_STYLE": {}, "LAYER_SCORED": "StreamCat source reach",
        "source_geometry_task": Task(), "click_snap_task": Task(), "coord_snap_task": Task(),
        "delineate_task": Task(), "_analysis_runs": {},
        "clicked": Value(CLICK), "flow_geojson": Value(v2_fc), "hr_geojson": Value(hr_fc),
        "streams_mode": Value("segmented"), "streams_down": Value(False),
        "_STREAMS_DOWN_TEXT": "The stream service is down",
        "_pick_outage": lambda: state.outages.append(True),
        "_stream_layers": SimpleNamespace(flow=SimpleNamespace(data=V2_FC)),
        "current_step": lambda: "identify", "STEP_IDENTIFY": "identify",
        "_clear_route_state": lambda: None, "_invalidate_analysis": lambda: None,
        "_start_route": lambda *args: state.routes.append(args),
        "_FINDING_TEXT": "Finding stream", "_MISS_TEXT": "No nearby stream",
        "anchor_error": Value(), "_hr_route": {},
        "flowlines": SimpleNamespace(flowlines_in_bbox=fetch_v2,
                                     nearest_point_on_lines=lambda *args: v2_hit),
        "nhd_hr": SimpleNamespace(hr_flowlines_in_bbox=fetch_hr,
                                  hr_flowlines_in_bbox_status=fetch_hr_status,
                                  nearest_point_on_hr_lines=lambda *args: hr_hit),
        "network_display": SimpleNamespace(feature_by_id=lambda *args: FEATURE),
        "ui": SimpleNamespace(
            update_numeric=lambda name, **kwargs: state.numeric.update({name: kwargs["value"]}),
            update_action_button=lambda *args, **kwargs: state.buttons.append((args, kwargs)),
            notification_show=lambda *args, **kwargs: state.notifications.append((args, kwargs))),
    }
    for name in ("_valid_site_hit", "_visible_v2_hit", "_display_snap_result", "_place_pin",
                 "_apply_snap", "_source_ready", "_scored_feature_for"):
        namespace[name] = _function("easi", name, namespace)
    return state, namespace


def _assert_hr_site(state):
    assert state.point() == (*HR[:3], V2[3])
    assert state.layers["marker"].location == HR[:2]
    assert state.numeric == {"lat": round(HR[0], 5), "lon": round(HR[1], 5)}
    assert state.lookup()["comid"] == V2[3] and state.lookup()["point"] == HR[:2]
    anchor = state.pending()
    assert anchor["anchorKind"] == "v2Direct"
    assert anchor["clickedPoint"] == {"lat": CLICK[0], "lon": CLICK[1]}
    assert anchor["scoredReach"]["comid"] == V2[3]
    assert anchor["scoredReach"]["snapLat"] == V2[0]
    assert anchor["scoredReach"]["snapLon"] == V2[1]
    assert anchor["scoredReach"]["snapDistFt"] == V2[2]
    assert anchor["selectedSite"] == {"network": "nhdplus-hr", "nhdplusid": HR[3],
        "snapLat": HR[0], "snapLon": HR[1], "snapDistFt": HR[2]}
    assert not state.routes


def test_direct_v2_source_keeps_pin_on_offset_hr_stream():
    state, scope = _scope()
    scope["_apply_snap"](V2, FEATURE, site_hit=HR, clicked_at=CLICK)
    _assert_hr_site(state)
    assert scope["_source_ready"]()
    assert state.scored() == {"comid": V2[3], "name": "Source Creek"}
    assert state.layers["scored"].data["features"] == [FEATURE]


@pytest.mark.parametrize("distance", [0, 149.999, 150])
def test_hr_site_tolerance_includes_exact_boundary(distance):
    state, scope = _scope()
    hit = (*HR[:2], distance, HR[3])
    assert scope["_valid_site_hit"](hit)
    scope["_apply_snap"](V2, FEATURE, site_hit=hit, clicked_at=CLICK)
    assert state.point() == (*HR[:2], distance, V2[3])


@pytest.mark.parametrize("hit", [
    None, (), (40.0, -83.0), "bad", "1234", {"lat": 40},
    (*HR[:2], 150.001, HR[3]), (*HR[:2], -1, HR[3]),
    (float("nan"), HR[1], 2, HR[3]), (HR[0], float("inf"), 2, HR[3]),
    (91, HR[1], 2, HR[3]), (HR[0], -181, 2, HR[3]),
    (*HR[:2], float("nan"), HR[3]), (*HR[:2], float("inf"), HR[3]),
    (*HR[:3], None), (*HR[:3], 0), (*HR[:3], -1), (*HR[:3], True), (*HR[:3], "bad"),
    (*HR[:3], 1.5), (*HR[:3], float("nan")), (*HR[:3], float("inf")),
])
def test_unusable_hr_snap_falls_back_to_original_v2_point(hit):
    state, scope = _scope()
    assert not scope["_valid_site_hit"](hit)
    scope["_apply_snap"](V2, FEATURE, site_hit=hit, clicked_at=CLICK)
    assert state.point() == V2
    assert state.layers["marker"].location == V2[:2]
    assert state.pending()["anchorKind"] == "v2Direct"
    assert not state.pending().get("selectedSite")
    assert scope["_source_ready"]()


@pytest.mark.parametrize("identifier", [str(HR[3]), str(HR[3]) + ".0", "1e3"])
def test_site_id_validation_and_application_are_consistent(identifier):
    state, scope = _scope()
    hit = (*HR[:3], identifier)
    valid = scope["_valid_site_hit"](hit)
    scope["_apply_snap"](V2, FEATURE, site_hit=hit, clicked_at=CLICK)
    if valid:
        assert state.pending()["selectedSite"]["nhdplusid"] == int(float(identifier))
    else:
        assert state.point() == V2


def test_existing_hr_surrogate_anchor_is_preserved_without_new_site_hit():
    anchor = {"anchorKind": "hrSurrogate", "clickedStream": {"snapLat": HR[0], "snapLon": HR[1]},
              "scoredReach": {"comid": V2[3], "gnisName": "Downstream source"},
              "routing": {"method": "existing-route"}}
    state, scope = _scope(pending=anchor)
    original = state.pending()
    scope["_apply_snap"]((*HR[:3], V2[3]))
    assert state.pending() is original and state.pending() == anchor
    assert state.point() == (*HR[:3], V2[3])


def test_worker_fetches_both_networks_even_when_v2_hit_succeeds():
    state, scope = _scope()
    result = _function("easi", "_snap_both", scope)(*CLICK)
    assert {item[0] for item in state.fetches} == {"v2", "hr"}
    assert state.hr_kwargs == [{"fast_fail": True}]      # the map's one-attempt policy
    assert {key: result[key] for key in ("hit", "hitFeature", "hrHit", "lat", "lon")} == {
        "hit": V2, "hitFeature": FEATURE, "hrHit": HR, "lat": CLICK[0], "lon": CLICK[1]}


def test_cached_click_and_typed_completion_choose_identical_site_and_source():
    cached, cached_scope = _scope()
    _function("easi", "_handle_click", cached_scope)()
    _assert_hr_site(cached)
    assert not cached_scope["click_snap_task"].calls
    for handler, task in (("_apply_click_snap", "click_snap_task"),
                          ("_apply_coord_snap", "coord_snap_task")):
        state, scope = _scope()
        result = _function("easi", "_snap_both", scope)(*CLICK)
        scope[task].payload = (5, result)
        _function("easi", handler, scope)()
        _assert_hr_site(state)
        assert state.pending() == cached.pending()


def test_typed_completion_does_not_retrigger_itself_when_setting_pending_anchor():
    """Use real dependency tracking: a read-before-write made this effect loop."""
    from shiny import reactive

    async def run():
        state, scope = _scope()
        pending = reactive.value(None)
        state.pending = pending
        scope.update(reactive=reactive, pending_anchor=pending)
        scope["_apply_snap"] = _function("easi", "_apply_snap", scope)
        scope["coord_snap_task"].payload = (5, {
            "hit": V2, "hitFeature": FEATURE, "hrHit": HR,
            "lat": CLICK[0], "lon": CLICK[1], "hrAvailable": True, "display": None})
        complete = _function("easi", "_apply_coord_snap", scope)
        calls = []

        def guarded_completion():
            calls.append(len(calls) + 1)
            # Cap executions of the old faulty implementation so flush can
            # settle and report the assertion instead of hanging the suite.
            if len(calls) <= 3:
                complete()

        effect = reactive.effect(guarded_completion)
        try:
            await reactive.flush()
            assert calls == [1]
            with reactive.isolate():
                _assert_hr_site(state)
        finally:
            effect.destroy()

    asyncio.run(run())


@pytest.mark.parametrize("v2_fc,hr_fc", [(None, HR_FC), (V2_FC, None), (None, None)])
def test_partial_viewport_cache_fetches_both_before_resolving(v2_fc, hr_fc):
    state, scope = _scope(v2_fc=v2_fc, hr_fc=hr_fc)
    _function("easi", "_handle_click", scope)()
    assert scope["click_snap_task"].calls == [((*CLICK, 6), {})]
    assert state.point() is None and state.layers["marker"] is None
    assert not state.routes


@pytest.mark.parametrize("handler,task", [("_apply_click_snap", "click_snap_task"),
                                          ("_apply_coord_snap", "coord_snap_task")])
def test_hr_only_pick_still_routes_instead_of_inventing_v2_source(handler, task):
    state, scope = _scope(v2_hit=None)
    scope[task].payload = (5, {"hit": None, "hrHit": HR, "lat": CLICK[0], "lon": CLICK[1]})
    _function("easi", handler, scope)()
    assert state.routes == [(*CLICK, HR)]
    assert state.pending() is None


def test_late_source_geometry_draws_v2_glow_without_moving_hr_pin():
    state, scope = _scope()
    scope["_apply_snap"](V2, site_hit=HR, clicked_at=CLICK)
    assert scope["source_geometry_task"].calls == [((V2[3], 5), {})]
    before_anchor = copy.deepcopy(state.pending())
    before_point = state.point()
    scope["source_geometry_task"].payload = (5, {"comid": V2[3], "feature": FEATURE})
    _function("easi", "_source_geometry_done", scope)()
    assert state.layers["scored"].data["features"] == [FEATURE]
    assert state.layers["marker"].location == HR[:2]
    assert state.point() == before_point and state.pending() == before_anchor


def test_direct_pending_anchor_keeps_direct_labels_and_legend():
    from shiny import ui

    state, scope = _scope()
    scope["_apply_snap"](V2, FEATURE, site_hit=HR, clicked_at=CLICK)
    _function("easi", "_toggle_delineate", scope)()
    assert state.buttons[-1][1]["label"] == "Delineate Basin and Reach"
    assert state.buttons[-1][1]["disabled"] is False
    status_scope = {**scope, "ui": ui,
                    "hr_snap_card": lambda *args: pytest.fail("Direct anchor used routed-site card")}
    status = str(_function("easi", "snap_status", status_scope)())
    assert "StreamCat source reach resolved" in status
    legend_calls = []
    legend_scope = {**scope, "_HAS_MAP": True, "app_mode": lambda: "single",
        "source_geometry": lambda: (True, False), "zoomed_in": lambda: True,
        "streams_mode": lambda: "both", "coverage_enabled": lambda: False,
        "streams_visible": lambda: True, "streams_down": lambda: False,
        "_legend_ui": lambda *args, **kwargs: legend_calls.append((args, kwargs))}
    _function("easi", "stream_legend", legend_scope)()
    assert legend_calls[-1][0][4] is False


def test_delineation_receives_hr_point_v2_comid_and_direct_anchor(monkeypatch):
    state, scope = _scope()
    scope["_apply_snap"](V2, FEATURE, site_hit=HR, clicked_at=CLICK)
    scope.update(input=SimpleNamespace(reach_ft=lambda: 1000), _delin_prog={})
    _function("easi", "_start_delineate", scope)()
    args, kwargs = scope["delineate_task"].calls[-1]
    assert args[:4] == (*HR[:2], 1000.0, V2[3])
    assert args[4] is state.pending() and not kwargs
    calls = []

    def run(lat, lon, reach_ft, **kwargs):
        calls.append((lat, lon, reach_ft, kwargs))
        return delineation.Delineation(lat=lat, lon=lon, comid=kwargs["comid"],
            snapped_lat=kwargs["snapped_lat"], snapped_lon=kwargs["snapped_lon"],
            gnis_name="Source Creek", reach_length_ft=reach_ft)

    monkeypatch.setattr(delineation, "run_delineation", run)
    monkeypatch.setattr(routing, "reanchor_inputs", lambda *args: pytest.fail("Direct site was routed"))
    scope["pipeline"] = pipeline
    result = asyncio.run(_function("easi", "delineate_task", scope)(*args))
    assert result[0] == 5 and result[1]["status"] == "ok"
    assert calls == [(*HR[:2], 1000.0, {"comid": V2[3], "snapped_lat": HR[0], "snapped_lon": HR[1]})]
    assert result[1]["siteAnchor"]["selectedSite"] == state.pending()["selectedSite"]
    assert result[1]["siteAnchor"]["scoredReach"]["snapLat"] == V2[0]
    assert result[1]["ctx_inputs"]["comid"] == V2[3]


def _geometric_scope(*, hr_y=0, v2_y=20, click_y=50, hr_status=None):
    """Parallel real lines defined in metres, transformed to the map's CRS.
    ``hr_status`` is the worker's HR answer (default: ok with a line, else empty)."""
    from easi import network_display
    from easi.datasources import flowlines, nhd_hr

    x, y = network_display._TO_ALBERS.transform(-83, 40)

    def point(dx, dy):
        lon, lat = network_display._TO_WGS84.transform(x + dx, y + dy)
        return [lon, lat]

    def line(offset, properties):
        return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": properties,
            "geometry": {"type": "LineString", "coordinates": [point(-200, offset), point(200, offset)]}}]}

    v2 = line(v2_y, {"comid": V2[3]})
    hr = line(hr_y, {"nhdplusid": HR[3]}) if hr_y is not None else None
    if hr_status is None:
        hr_status = "ok" if hr is not None else "empty"
    lon, lat = point(0, click_y)
    display = network_display.build_display(v2, hr, tol_ft=150, hr_status=hr_status)
    state, scope = _scope(v2_fc=v2, hr_fc=hr)
    scope.update(network_display=network_display,
        flowlines=SimpleNamespace(nearest_point_on_lines=flowlines.nearest_point_on_lines,
                                  flowlines_in_bbox=lambda *args: v2),
        nhd_hr=SimpleNamespace(nearest_point_on_hr_lines=nhd_hr.nearest_point_on_hr_lines,
                               hr_flowlines_in_bbox=lambda *args: hr,
                               hr_flowlines_in_bbox_status=lambda *args, **k: (hr_status, hr)),
        _stream_layers=SimpleNamespace(flow=SimpleNamespace(data=display["covered"])),
        streams_mode=Value(display["mode"]))
    scope["clicked"].set((lat, lon))
    for name in ("_visible_v2_hit", "_display_snap_result", "_scored_feature_for"):
        scope[name] = _function("easi", name, scope)
    hit = flowlines.nearest_point_on_lines(v2, lat, lon)
    hr_hit = nhd_hr.nearest_point_on_hr_lines(hr, lat, lon)
    result = {"hit": hit, "hrHit": hr_hit, "hitFeature": v2["features"][0],
              "lat": lat, "lon": lon, "display": display,
              "hrAvailable": hr_status != "failed"}
    return state, scope, result


@pytest.mark.parametrize("path", ["cached-click", "worker-click", "worker-coordinates"])
def test_hidden_offset_v2_line_cannot_supply_a_site_pin(path):
    # The click is 98.4 ft from hidden V2, but 164 ft from the displayed HR.
    state, scope, result = _geometric_scope()
    assert result["hit"][2] == pytest.approx(98.4252, abs=.01)
    assert result["hrHit"][2] == pytest.approx(164.042, abs=.01)
    assert {feature["properties"]["cover"] for feature in result["display"]["covered"]["features"]} == {"v2"}
    if path == "cached-click":
        _function("easi", "_handle_click", scope)()
    else:
        task, handler = (("click_snap_task", "_apply_click_snap") if path == "worker-click"
                         else ("coord_snap_task", "_apply_coord_snap"))
        scope[task].payload = (5, result)
        _function("easi", handler, scope)()
    assert state.point() is None and state.layers["marker"] is None
    assert not state.routes
    assert not scope["_source_ready"]()


@pytest.mark.parametrize("hr_y", [0, None])
def test_visible_v2_orphans_remain_selectable(hr_y):
    # hr_y None: HR answered with no line in the box, so every V2 stretch is an orphan.
    state, scope, result = _geometric_scope(hr_y=hr_y, v2_y=100, click_y=100)
    assert result["display"]["mode"] == "segmented"
    assert any(feature["properties"]["cover"] == "v2-orphan"
               for feature in result["display"]["covered"]["features"])
    scope["click_snap_task"].payload = (5, result)
    _function("easi", "_apply_click_snap", scope)()
    assert state.point()[:2] == pytest.approx(result["hit"][:2])
    assert state.point()[3] == V2[3] and scope["_source_ready"]()
    assert state.pending()["anchorKind"] == "v2Direct"
    assert not state.pending().get("selectedSite")


@pytest.mark.parametrize("task,handler", [("click_snap_task", "_apply_click_snap"),
                                          ("coord_snap_task", "_apply_coord_snap")])
def test_hr_outage_offers_no_v2_pick(task, handler):
    # The worker's HR fetch failed. The V2 line right under the click is never a
    # stand-in for the HR network: no pin, and the pick trips the breaker (the
    # notice and Try again) instead of reading as "no stream nearby".
    state, scope, result = _geometric_scope(hr_y=None, v2_y=100, click_y=100,
                                            hr_status="failed")
    assert result["display"]["mode"] == "hr-unavailable" and result["hit"][2] < 1
    scope[task].payload = (5, result)
    _function("easi", handler, scope)()
    assert state.point() is None and state.layers["marker"] is None
    assert state.outages == [True] and state.lookup()["status"] == "idle"
    assert not state.notifications                       # never the miss text
    assert not scope["_source_ready"]()


EMPTY_FC = {"type": "FeatureCollection", "features": []}


def test_worker_hr_outage_keeps_valid_cached_hr_site():
    state, scope, result = _geometric_scope(click_y=0)
    expected_hr = result["hrHit"]
    worker = {**result, "hrHit": None, "hrAvailable": False,
              "display": {"mode": "hr-unavailable", "covered": EMPTY_FC}}
    scope["coord_snap_task"].payload = (5, worker)
    _function("easi", "_apply_coord_snap", scope)()
    assert state.point()[:3] == pytest.approx(expected_hr[:3])
    assert state.point()[3] == V2[3]
    assert state.pending()["selectedSite"]["nhdplusid"] == HR[3]


def test_worker_hr_outage_cannot_reinterpret_hidden_v2_as_visible_fallback():
    state, scope, result = _geometric_scope()
    worker = {**result, "hrHit": None, "hrAvailable": False,
              "display": {"mode": "hr-unavailable", "covered": EMPTY_FC}}
    scope["click_snap_task"].payload = (5, worker)
    _function("easi", "_apply_click_snap", scope)()
    assert state.point() is None and state.layers["marker"] is None
    assert state.outages == [True] and state.lookup()["status"] == "idle"
    assert not scope["_source_ready"]()
