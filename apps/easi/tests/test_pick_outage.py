"""A pick whose HR request went unanswered is not a miss (2026-09-23), all three apps.

It behaves like a failed map fetch: the breaker trips (lines cleared, fetches
stop until the legend's Try again) and the notice points at Try again instead of
the miss text. A real miss keeps its text. The production closures are compiled
from each app.py by ``_function``; EASI's completions are exercised with its
geometric fixtures in ``test_map_snap_selection.py``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_map_pick_lifecycle import _function

MISS = "No nearby stream"
DOWN = "The stream service is down"


class Value:
    def __init__(self, value=None):
        self.value = value

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = value


@pytest.mark.parametrize("app_name", ["easi", "sfari", "deep"])
def test_a_pick_outage_trips_the_breaker_and_says_why(app_name):
    calls, notices = [], []
    ns = {
        "streams_task": SimpleNamespace(cancel=lambda: calls.append("cancel")),
        "_stream_layers": SimpleNamespace(clear_streams=lambda: calls.append("clear")),
        "flow_geojson": Value("v2 lines"), "hr_geojson": Value("hr lines"),
        "streams_mode": Value("segmented"), "streams_down": Value(False),
        "_STREAMS_DOWN_TEXT": DOWN,
        "ui": SimpleNamespace(notification_show=lambda *a, **k: notices.append((a, k))),
    }
    ns["_streams_outage"] = _function(app_name, "_streams_outage", ns)
    _function(app_name, "_pick_outage", ns)()
    assert calls == ["cancel", "clear"]
    assert ns["streams_down"]() is True and ns["streams_mode"]() == "hr-unavailable"
    assert ns["flow_geojson"]() is None and ns["hr_geojson"]() is None
    assert notices == [((DOWN,), {"type": "warning", "duration": 5, "id": "streams_down"})]


def _snap_result_handler(app_name):
    state = SimpleNamespace(point=Value(("stale",)), lookups=[], removed=[], outages=[],
                            notices=[])
    ns = {
        "SNAP_TOL_FT": 150.0, "_MISS_TEXT": MISS, "snapped_point": state.point,
        "_apply_snap": lambda *a, **k: pytest.fail("a failed request must not pin"),
        "_lookup_state": lambda status, **k: state.lookups.append(status),     # SFARI
        "_set_lookup": lambda status, **k: state.lookups.append(status),       # DEEP
        "_remove_layer": state.removed.append,
        "_pick_outage": lambda: state.outages.append(True),
        "ui": SimpleNamespace(notification_show=lambda *a, **k: state.notices.append(a[0])),
    }
    return state, _function(app_name, "_apply_snap_result", ns)


@pytest.mark.parametrize("app_name", ["sfari", "deep"])
@pytest.mark.parametrize("from_coords", [False, True])
def test_an_unanswered_pick_is_not_a_miss(app_name, from_coords):
    state, handler = _snap_result_handler(app_name)
    handler({"hit": None, "lat": 40.0, "lon": -83.0, "hrStatus": "failed"},
            from_coords=from_coords)
    assert state.outages == [True]
    assert state.notices == []                         # never the miss text
    assert state.point() is None and "marker" in state.removed
    assert state.lookups[-1] == "idle"                 # no stale "Finding" or "no_match"


@pytest.mark.parametrize("app_name", ["sfari", "deep"])
@pytest.mark.parametrize("from_coords", [False, True])
@pytest.mark.parametrize("status", ["empty", "ok"])
def test_a_real_miss_keeps_the_miss_text(app_name, from_coords, status):
    state, handler = _snap_result_handler(app_name)
    handler({"hit": None, "lat": 40.0, "lon": -83.0, "hrStatus": status},
            from_coords=from_coords)
    assert state.outages == []
    assert len(state.notices) == 1
    if from_coords:
        assert state.notices[0].startswith("No stream within 150 ft of those coordinates")
    else:
        assert state.notices[0] == MISS
