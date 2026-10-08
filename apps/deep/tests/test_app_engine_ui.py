"""The app's engine line, engine scheduling rule, and copy.
Imports the Shiny module without running a server."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytest.importorskip("shiny")
app = importlib.import_module("app")


def test_engine_line_states():
    assert app._engine_line_ui({"status": "idle"}, False, {}) is None
    assert app._engine_line_ui({"status": "ok", "record": {
        "engineVersion": "0.2.0", "watershed": {"areaSqkm": 4.19, "nReaches": 3}}}, False, {}) is None
    warn = str(app._engine_line_ui({"status": "refused", "reason": "over budget"}, False, {}))
    assert "refused: over budget" in warn and "warn" in warn
    assert "StreamCat values stand in" in warn and "Delineate again" in warn
    # five plain steps, the reach count while the trace and the union run, the
    # metric family with its position; never "hops" (2026-09-07)
    assert app._engine_progress_text({}) == "Delineating watershed · starting"
    assert app._engine_progress_text({"stage": "walk", "reaches": 43, "hops": 7}) \
        == "Delineating watershed · step 2 of 5 · tracing upstream, 43 reaches"
    assert app._engine_progress_text({"stage": "geometry", "reaches": 1180, "hops": 40}) \
        == "Delineating watershed · step 3 of 5 · joining catchments, 1,180 reaches"
    assert app._engine_progress_text({"stage": "metrics", "family": "landcover"}) \
        == "Delineating watershed · step 5 of 5 · computing metrics, land cover (3 of 6)"
    assert app._engine_progress_text({"stage": "done"}) == "Delineating watershed · finishing"
    running = str(app._engine_line_ui({"status": "running"}, True, {"stage": "site"}))
    assert "step 1 of 5 · finding the stream" in running
    for prog in ({}, {"stage": "walk", "reaches": 43, "hops": 7}, {"stage": "done"}):
        text = app._engine_progress_text(prog)
        assert "hops" not in text and "…" not in text and "—" not in text


def test_map_styles_legible_and_hover_highlighted():
    # two colors (2026-09-07, SFARI's map): dark blue where the StreamCat lookup
    # engine answers by COMID, cyan for every other NHD stream, a dashed route
    # to the nearest StreamCat reach, a glow under the StreamCat reach
    assert app.HR_FLOWLINE_STYLE["color"] != app.FLOWLINE_STYLE["color"]
    assert app.FLOWLINE_STYLE["weight"] >= 3
    assert app.HR_FLOWLINE_STYLE["weight"] == app.FLOWLINE_STYLE["weight"]
    assert not hasattr(app, "FLOWLINE_HOVER_STYLE")    # a hover restyle flashed the layer
    assert "dashArray" in app.ROUTE_STYLE and "dashArray" not in app.HR_FLOWLINE_STYLE
    assert app.SCORED_REACH_STYLE["opacity"] < 0.5
    assert not hasattr(app, "_engine_wanted_for")      # every site runs the engine at Delineate


def test_copy_and_cache_bust():
    assert app._MISS_TEXT == ("No stream line within 150 ft of the click. "
                              "Zoom in and click a line.")
    src = Path(app.__file__).read_text(encoding="utf-8")
    # v13 / v23 (2026-10-04): the metric rows EASI, SFARI and DEEP share (staf/metric-rows.css);
    # v16 / v27 / v7 (2026-10-08): the regions and badges in their status colors
    assert "deep.css?v=16" in src and "styles.css?v=27" in src and "coverage.js?v=7" in src
    assert 'href="staf/metric-rows.css?v=1"' in src and 'src="staf/metric-rows.js?v=1"' in src
    # the shared script loads before measure.js, which calls it after a photo changes
    assert src.index("staf/metric-rows.js") < src.index("measure.js?v=")
    # Source readiness has its own persistent row instead of sharing engine progress.
    assert '"Finding the nearest StreamCat reach…"' in src
    assert 'ui.output_ui("streamcat_lookup_status")' in src
    css = (Path(app.__file__).parent / "www" / "deep.css").read_text(encoding="utf-8")
    assert ".deep-engine-line" in css
    # where a value or a curve comes from is in the report, not on the worksheet
    assert ".deep-basis-tag" not in css and ".deep-source-row" not in css


def test_catchment_batches_and_the_plain_hr_failure_text():
    # 2026-09-30: the catchment step names its batch, and an unanswered USGS
    # HR service reads in plain words (the engine's record keeps its reason).
    lead = "Delineating watershed · step 3 of 5 · joining catchments"
    assert app._engine_progress_text(
        {"stage": "catchments", "reaches": 1180, "batch": 3, "batches": 12}) \
        == lead + ", 1,180 reaches, batch 3 of 12"
    assert app._engine_progress_text(
        {"stage": "catchments", "reaches": 40, "batch": 1, "batches": 1}) == lead + ", 40 reaches"
    assert app._engine_progress_text(
        {"stage": "union", "reaches": 40, "batch": 2, "batches": 2}) == lead + ", 40 reaches"
    for reason in ("catchment query failed", "upstream tree query failed",
                   "anchor reach geometry unavailable", "no HR flowline near the point"):
        assert app._engine_reason_text(reason) == app._HR_FAILURE_TEXT
    budget = "watershed exceeds the engine budget (3001 reaches, 12 hops)"
    assert app._engine_reason_text(budget) == budget
    assert "\u2014" not in app._HR_FAILURE_TEXT and "\u2014" not in app._STREAMS_DOWN_TEXT
    assert app.STREAM_RETRY_DELAYS_S == (15.0, 30.0, 60.0)
