"""The stream-service breaker, run on Shiny's real reactive graph (all three apps).

When the HR fetch comes back ``hr-unavailable`` the session draws nothing and
stops asking: pans and zooms fetch nothing until the legend's Try again, which
asks exactly once for the box in view. The production closures are compiled
from each app.py (``_function``) and wired with their real decorators' logic.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from test_map_pick_lifecycle import _function

BOX = (-72.297, 43.655, -72.176, 43.716)
VIEW = (43.67, -72.26, 43.70, -72.21)             # (south, west, north, east), inside BOX
FAR_BOX = (-71.297, 43.655, -71.176, 43.716)
FAR_VIEW = (43.67, -71.26, 43.70, -71.21)
SMALL_BOX = (-72.267, 43.670, -72.206, 43.701)    # a zoom in: inside BOX, a quarter of it
EMPTY = {"type": "FeatureCollection", "features": []}
LINES = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
         "geometry": {"type": "LineString", "coordinates": [[-72.25, 43.68], [-72.24, 43.69]]}}]}


def _result(bbox, mode, covered=EMPTY):
    return {"bbox": bbox, "mode": mode, "v2": None, "hr": None,
            "covered": covered, "uncovered": EMPTY}


def _wire(app_name):
    from shiny import reactive
    from shiny.types import ActionButtonValue

    from easi import viewport            # the three apps carry identical viewport modules

    calls, cancels, layers = [], [], []
    payload = reactive.value(None)

    class Task:
        def __call__(self, bbox):
            calls.append(bbox)

        def result(self):
            res = payload()
            if res is None:
                raise RuntimeError("no result yet")
            return res

        def cancel(self):
            cancels.append(True)

    ns = {
        "reactive": reactive, "viewport": viewport,
        "view_bbox": reactive.value(BOX), "last_view_change": reactive.value(-1e9),
        "view_bounds": reactive.value(VIEW), "fetched_bbox": reactive.value(None),
        "streams_down": reactive.value(False), "streams_mode": reactive.value(None),
        "flow_geojson": reactive.value(None), "hr_geojson": reactive.value(None),
        "_stream_layers": SimpleNamespace(
            clear_streams=lambda: layers.append("clear"),
            set_data=lambda covered, uncovered: layers.append(("set", covered))),
        "streams_task": Task(),
        "input": SimpleNamespace(retry_streams=reactive.value(ActionButtonValue(0))),
    }
    # Compiled first: each compiled function sees the namespace as it was then.
    ns["_streams_outage"] = _function(app_name, "_streams_outage", ns)
    effects = [reactive.effect(_function(app_name, "_settle_and_fetch", ns)),
               reactive.effect(_function(app_name, "_apply_streams", ns)),
               reactive.effect(reactive.event(ns["input"].retry_streams)(
                   _function(app_name, "_retry_streams", ns)))]
    return ns, payload, calls, cancels, layers, effects


def _get(value):
    """Read a reactive value from test code (outside any reactive context)."""
    from shiny import reactive
    with reactive.isolate():
        return value()


def _view(ns, bbox, view):
    ns["view_bbox"].set(bbox)
    ns["view_bounds"].set(view)
    ns["last_view_change"].set(_get(ns["last_view_change"]) + 1.0)   # long settled


@pytest.mark.parametrize("app_name", ["easi", "sfari", "deep"])
def test_an_outage_stops_the_fetches_until_try_again(app_name):
    from shiny import reactive
    from shiny.types import ActionButtonValue

    async def run():
        ns, payload, calls, cancels, layers, effects = _wire(app_name)
        try:
            await reactive.flush()
            assert calls == [BOX]
            payload.set(_result(BOX, "hr-unavailable"))
            await reactive.flush()
            assert _get(ns["streams_down"]) is True and cancels == [True]
            assert _get(ns["streams_mode"]) == "hr-unavailable" and layers[-1] == "clear"
            assert _get(ns["flow_geojson"]) is None and _get(ns["hr_geojson"]) is None
            # Pans, a zoom out and a zoom back in ask nothing while the service is down.
            _view(ns, FAR_BOX, FAR_VIEW)
            await reactive.flush()
            _view(ns, None, None)
            await reactive.flush()
            _view(ns, BOX, VIEW)
            await reactive.flush()
            assert calls == [BOX] and _get(ns["streams_down"]) is True
            # Try again asks exactly once, for the box in view.
            ns["input"].retry_streams.set(ActionButtonValue(1))
            await reactive.flush()
            assert calls == [BOX, BOX] and _get(ns["streams_down"]) is False
            ns["input"].retry_streams.set(ActionButtonValue(2))   # a second press: no-op
            await reactive.flush()
            assert calls == [BOX, BOX]
            payload.set(_result(BOX, "segmented", LINES))
            await reactive.flush()
            assert _get(ns["streams_mode"]) == "segmented" and layers[-1] == ("set", LINES)
        finally:
            for effect in effects:
                effect.destroy()

    asyncio.run(run())


@pytest.mark.parametrize("app_name", ["easi", "sfari", "deep"])
def test_a_stale_box_failure_still_trips_the_breaker(app_name):
    from shiny import reactive

    async def run():
        ns, payload, calls, cancels, layers, effects = _wire(app_name)
        try:
            await reactive.flush()
            payload.set(_result(FAR_BOX, "hr-unavailable"))       # not the box in view
            await reactive.flush()
            assert _get(ns["streams_down"]) is True and _get(ns["streams_mode"]) == "hr-unavailable"
            payload.set(_result(FAR_BOX, "segmented", LINES))       # a stale answer is ignored
            await reactive.flush()
            assert _get(ns["streams_mode"]) == "hr-unavailable" and ("set", LINES) not in layers
        finally:
            for effect in effects:
                effect.destroy()

    asyncio.run(run())


@pytest.mark.parametrize("app_name", ["easi", "sfari", "deep"])
def test_a_truncated_box_asks_again_on_zoom_in_only(app_name):
    from shiny import reactive

    async def run():
        ns, payload, calls, cancels, layers, effects = _wire(app_name)
        try:
            await reactive.flush()
            payload.set(_result(BOX, "hr-truncated"))
            await reactive.flush()
            assert _get(ns["streams_mode"]) == "hr-truncated" and _get(ns["streams_down"]) is False
            _view(ns, BOX, (43.68, -72.25, 43.69, -72.22))          # a pan inside the box
            await reactive.flush()
            assert calls == [BOX]
            _view(ns, SMALL_BOX, (43.68, -72.25, 43.69, -72.22))    # a zoom in
            await reactive.flush()
            assert calls == [BOX, SMALL_BOX]
        finally:
            for effect in effects:
                effect.destroy()

    asyncio.run(run())
