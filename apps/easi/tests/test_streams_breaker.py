"""The stream map's capped automatic retries, run on Shiny's real reactive graph
(all three apps).

The map reads the site engine's tiles (2026-09-30). A view whose tiles did not
all answer draws what arrived (``hr-partial``) or nothing (``hr-unavailable``)
and asks again after 15, 30 and 60 seconds. After the third retry (the cap) the
session asks the service nothing more: pans draw stored tiles only, until the
legend's Try again (or an answer from the service) starts the count over. The
production closures are compiled from each app.py (``_function``) and wired with
their real decorators' logic.
"""
from __future__ import annotations

import asyncio
import time
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
DELAYS = (15.0, 30.0, 60.0)


def _result(bbox, mode, covered=EMPTY, offline=False):
    return {"bbox": bbox, "mode": mode, "v2": None, "hr": None,
            "covered": covered, "uncovered": EMPTY, "offline": offline}


def _wire(app_name, answered=False):
    from shiny import reactive
    from shiny.types import ActionButtonValue

    from easi import viewport            # the three apps carry identical viewport modules

    calls, layers = [], []
    payload = reactive.value(None)

    class Task:
        def __call__(self, bbox, offline=False):
            calls.append((bbox, offline))

        def result(self):
            res = payload()
            if res is None:
                raise RuntimeError("no result yet")
            return res

        def cancel(self):
            pass

    service = SimpleNamespace(answered_since=lambda moment: answered)
    ns = {
        "reactive": reactive, "viewport": viewport,
        "view_bbox": reactive.value(BOX), "last_view_change": reactive.value(-1e9),
        "view_bounds": reactive.value(VIEW), "fetched_bbox": reactive.value(None),
        "streams_down": reactive.value(False), "streams_mode": reactive.value(None),
        "streams_retry_due": reactive.value(None), "streams_kick": reactive.value(0),
        "_streams_retries": {"count": 0, "down_at": None},
        "STREAM_RETRY_DELAYS_S": DELAYS,
        "flow_geojson": reactive.value(None), "hr_geojson": reactive.value(None),
        "_stream_layers": SimpleNamespace(
            clear_streams=lambda: layers.append("clear"),
            set_data=lambda covered, uncovered: layers.append(("set", covered))),
        "streams_task": Task(),
        "hr_site": service, "nhd_hr": service,
        "input": SimpleNamespace(retry_streams=reactive.value(ActionButtonValue(0))),
    }
    # Compiled first: each compiled function sees the namespace as it was then.
    ns["_streams_gap"] = _function(app_name, "_streams_gap", ns)
    ns["_streams_reset"] = _function(app_name, "_streams_reset", ns)
    ns["_resume_if_answered"] = _function(app_name, "_resume_if_answered", ns)
    effects = [reactive.effect(_function(app_name, "_settle_and_fetch", ns)),
               reactive.effect(_function(app_name, "_apply_streams", ns)),
               reactive.effect(_function(app_name, "_streams_retry_timer", ns)),
               reactive.effect(reactive.event(ns["input"].retry_streams)(
                   _function(app_name, "_retry_streams", ns)))]
    return ns, payload, calls, layers, effects


def _get(value):
    """Read a reactive value from test code (outside any reactive context)."""
    from shiny import reactive
    with reactive.isolate():
        return value()


def _view(ns, bbox, view):
    ns["view_bbox"].set(bbox)
    ns["view_bounds"].set(view)
    ns["last_view_change"].set(_get(ns["last_view_change"]) + 1.0)   # long settled


async def _retry_now(ns):
    """The scheduled retry comes due (the timer's pause, without the wait)."""
    from shiny import reactive
    due = _get(ns["streams_retry_due"])
    assert due is not None
    ns["streams_retry_due"].set(time.monotonic() - 1.0)
    await reactive.flush()


@pytest.mark.parametrize("app_name", ["easi", "sfari", "deep"])
def test_missing_tiles_are_asked_again_three_times_then_the_session_stops(app_name):
    from shiny import reactive
    from shiny.types import ActionButtonValue

    async def run():
        ns, payload, calls, layers, effects = _wire(app_name)
        try:
            await reactive.flush()
            assert calls == [(BOX, False)]
            for n, pause in enumerate(DELAYS, start=1):
                before = time.monotonic()
                payload.set(_result(BOX, "hr-unavailable"))
                await reactive.flush()
                due = _get(ns["streams_retry_due"])
                assert due is not None and due - before == pytest.approx(pause, abs=1.0)
                assert _get(ns["streams_down"]) is False
                assert _get(ns["streams_mode"]) == "hr-unavailable" and layers[-1] == ("set", EMPTY)
                await _retry_now(ns)
                assert calls == [(BOX, False)] * (n + 1)
            # The third retry failed too: the cap. Nothing more is asked.
            payload.set(_result(BOX, "hr-unavailable"))
            await reactive.flush()
            assert _get(ns["streams_down"]) is True and _get(ns["streams_retry_due"]) is None
            assert len(calls) == 4
            # Pans draw stored tiles only; a zoom out and back in likewise.
            _view(ns, FAR_BOX, FAR_VIEW)
            await reactive.flush()
            _view(ns, None, None)
            await reactive.flush()
            _view(ns, BOX, VIEW)
            await reactive.flush()
            assert calls[4:] == [(FAR_BOX, True), (BOX, True)]
            # An offline (stored-tile) answer never schedules or resets anything.
            payload.set(_result(BOX, "hr-partial", LINES, offline=True))
            await reactive.flush()
            assert _get(ns["streams_down"]) is True and _get(ns["streams_retry_due"]) is None
            assert layers[-1] == ("set", LINES)
            # Try again starts the count over and asks for the box in view.
            ns["input"].retry_streams.set(ActionButtonValue(1))
            await reactive.flush()
            assert calls[-1] == (BOX, False) and _get(ns["streams_down"]) is False
            assert ns["_streams_retries"]["count"] == 0
            n_calls = len(calls)
            ns["input"].retry_streams.set(ActionButtonValue(2))   # a second press: no-op
            await reactive.flush()
            assert len(calls) == n_calls
            payload.set(_result(BOX, "segmented", LINES))
            await reactive.flush()
            assert _get(ns["streams_mode"]) == "segmented" and layers[-1] == ("set", LINES)
        finally:
            for effect in effects:
                effect.destroy()

    asyncio.run(run())


@pytest.mark.parametrize("app_name", ["easi", "sfari", "deep"])
def test_a_partial_view_draws_what_arrived_and_an_answer_starts_the_count_over(app_name):
    from shiny import reactive

    async def run():
        ns, payload, calls, layers, effects = _wire(app_name)
        try:
            await reactive.flush()
            payload.set(_result(BOX, "hr-partial", LINES))
            await reactive.flush()
            assert _get(ns["streams_mode"]) == "hr-partial" and layers[-1] == ("set", LINES)
            assert _get(ns["streams_retry_due"]) is not None
            assert ns["_streams_retries"]["count"] == 1
            await _retry_now(ns)
            assert calls == [(BOX, False), (BOX, False)]
            payload.set(_result(BOX, "segmented", LINES))           # every tile answered
            await reactive.flush()
            assert ns["_streams_retries"]["count"] == 0
            assert _get(ns["streams_retry_due"]) is None and _get(ns["streams_down"]) is False
        finally:
            for effect in effects:
                effect.destroy()

    asyncio.run(run())


@pytest.mark.parametrize("app_name", ["easi", "sfari", "deep"])
def test_a_stale_box_is_ignored(app_name):
    from shiny import reactive

    async def run():
        ns, payload, calls, layers, effects = _wire(app_name)
        try:
            await reactive.flush()
            payload.set(_result(FAR_BOX, "hr-unavailable"))       # not the box in view
            await reactive.flush()
            assert _get(ns["streams_down"]) is False and _get(ns["streams_mode"]) is None
            assert _get(ns["streams_retry_due"]) is None and layers == []
        finally:
            for effect in effects:
                effect.destroy()

    asyncio.run(run())


@pytest.mark.parametrize("app_name", ["easi", "sfari", "deep"])
def test_a_truncated_box_asks_again_on_zoom_in_only(app_name):
    from shiny import reactive

    async def run():
        ns, payload, calls, layers, effects = _wire(app_name)
        try:
            await reactive.flush()
            payload.set(_result(BOX, "hr-truncated"))
            await reactive.flush()
            assert _get(ns["streams_mode"]) == "hr-truncated" and _get(ns["streams_down"]) is False
            assert _get(ns["streams_retry_due"]) is None          # an answer, not a gap
            _view(ns, BOX, (43.68, -72.25, 43.69, -72.22))          # a pan inside the box
            await reactive.flush()
            assert calls == [(BOX, False)]
            _view(ns, SMALL_BOX, (43.68, -72.25, 43.69, -72.22))    # a zoom in
            await reactive.flush()
            assert calls == [(BOX, False), (SMALL_BOX, False)]
        finally:
            for effect in effects:
                effect.destroy()

    asyncio.run(run())


@pytest.mark.parametrize("app_name", ["easi", "sfari", "deep"])
@pytest.mark.parametrize("answered", [True, False])
def test_an_answer_from_the_service_brings_the_map_back(app_name, answered):
    from shiny import reactive

    async def run():
        ns, payload, calls, layers, effects = _wire(app_name, answered=answered)
        try:
            await reactive.flush()
            ns["streams_down"].set(True)                           # the retries ran out
            ns["_streams_retries"].update(count=3, down_at=time.monotonic())
            await reactive.flush()
            n_calls = len(calls)
            ns["_resume_if_answered"]()                            # a pick came back
            await reactive.flush()
            if answered:
                assert _get(ns["streams_down"]) is False and calls[-1] == (BOX, False)
                assert ns["_streams_retries"]["count"] == 0
            else:
                assert _get(ns["streams_down"]) is True and len(calls) == n_calls
        finally:
            for effect in effects:
                effect.destroy()

    asyncio.run(run())
