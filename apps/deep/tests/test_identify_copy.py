"""The Identify pane after the 2026-09-04 trim (the EASI changes ported to
DEEP) and the 2026-09-07 two-engine map (SFARI's): the pin lands on click, the
viewport's HR lines settle a click, the StreamCat reach resolves in the
background, one short instruction, no credit line, the attribution in Help,
short snap lines, middle dots in the pane titles. The pane is Shiny UI, so
this reads the source."""
from __future__ import annotations

from pathlib import Path

import pytest

app = pytest.importorskip("app")
SRC = Path(app.__file__).read_text(encoding="utf-8")
CSS = (Path(app.__file__).parent / "www" / "styles.css").read_text(encoding="utf-8")


def test_every_click_snaps_to_the_nhd_and_pins_at_once():
    # every click snaps to the HR line and the pin lands at once; the StreamCat
    # reach (the V2 line under the click, else the nearest StreamCat reach
    # downstream) resolves in the background and never moves the pin, and a
    # trace that fails is no error (the 502 the user hit on 2026-09-07)
    assert "def _place_pin(" in SRC
    assert 'nearest_point_on_lines(fc, lat, lon, id_prop="nhdplusid")' in SRC
    assert "hr_site.snap_point(lat, lon)" in SRC
    assert "async def anchor_task(" in SRC and "comid_anchor.resolve(" in SRC
    assert "network_display.fetch_streams(bbox, tol_ft=SNAP_TOL_FT)" in SRC
    for gone in ("snap_both", "route_from_hr", "pending_anchor", "_surrogate_offer",
                 "delineate_task", "hr_flow_task", "async def flow_task(", "_LOCATING_TEXT",
                 "engine_site", "_maybe_launch_engine", "_engine_wanted_for",
                 "substitution limit", "Could not locate this stream"):
        assert gone not in SRC, gone


def test_delineate_always_runs_the_engine_and_offers_the_streamcat_continuation():
    body = SRC.split("def _start_delineate():", 1)[1].split("def _with_anchor(", 1)[0]
    assert "_launch_engine(lat, lon, float(input.reach_ft() or DEFAULT_REACH_FT))" in body
    # the typed assessment reach reaches the engine (2026-09-07; it was always 1,000 ft)
    assert "engine_prefill.run_engine(lat, lon, reach_length_ft=reach_ft, progress=_cb)" in SRC
    assert "delineate_only" not in SRC and "pipeline.delineate_from_engine(rec, res[\"lat\"]" in SRC
    assert "pipeline.delineate_without_watershed(" in SRC
    assert '"Continue with StreamCat values"' in SRC and '"Watershed not available"' in SRC


def test_clear_keeps_the_map_view_and_the_coordinates():
    # SFARI's Clear (2026-09-07): the pane returns to Identify, the map stays
    # where it is, and the lat/lon inputs keep their values
    body = SRC.split("def _do_reset():", 1)[1].split("@reactive.effect", 1)[0]
    assert "current_step.set(STEP_IDENTIFY)" in body
    for gone in ("_MAP.center", "_MAP.zoom", "zoom = 4", 'update_numeric("lat"', 'update_numeric("lon"'):
        assert gone not in body, gone
    assert "39.5, -98.35" not in SRC.split("def _build_map():", 1)[1].split("_MAP = _build_map()", 1)[1]


def test_the_pane_copy_is_short_and_plain():
    assert "Zoom in and click a stream, search a place, or enter coordinates." in SRC
    assert "Zoom in until stream lines appear and click a stream to place" not in SRC
    assert "Type to search" not in SRC and "easi-ac-credit" not in SRC
    assert '"No point yet."' in SRC and "No point yet —" not in SRC
    assert "Address search uses OpenStreetMap data (Photon and Nominatim)." in SRC
    assert "Click “Delineate”" not in SRC
    assert '"DEEP · Assessment"' in SRC and 'f"DEEP · {head_label}"' in SRC
    # the module docstring may keep its dash; no string literal does
    assert '"DEEP — ' not in SRC and 'f"DEEP — ' not in SRC
    assert app._MISS_TEXT == "No stream line within 150 ft of the click. Zoom in and click a line."
    for notice in ("Place not found. Try a city",):
        assert notice in SRC


def test_the_snap_line_says_what_happens_next():
    body = SRC.split("def snap_status():", 1)[1].split("@render.ui", 1)[0]
    assert "The STAF site engine calculates the HR reach watershed" in body
    assert "comid_anchor.snap_line(site_anchor())" in body


def test_numbers_are_formatted():
    assert app._fmt_km2(0.9871999900000001) == "0.99 km²"
    assert app._fmt_km2(None) == "unknown"
    assert app._fmt_ft(1000.0) == "1,000 ft"
    assert 'row("Drainage area", _fmt_km2(d.get("drainage_area_sqkm")))' in SRC


def test_styles_carry_the_tighter_divider_and_the_new_version():
    assert 'href="styles.css?v=15"' in SRC
    assert ".easi-pane-body hr { margin: 8px 0; }" in CSS
    assert ".easi-ac-credit" not in CSS


def test_snap_helper_reads_the_hr_id():
    from deep.datasources import flowlines
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"nhdplusid": 10000600001216},
         "geometry": {"type": "LineString", "coordinates": [[-83.06, 40.31], [-83.05, 40.31]]}}]}
    hit = flowlines.nearest_point_on_lines(fc, 40.3101, -83.055, id_prop="nhdplusid")
    assert hit is not None and hit[3] == 10000600001216 and hit[2] < 100
    assert flowlines.nearest_point_on_lines(fc, 40.3101, -83.055)[3] is None   # no comid property


def test_the_selected_point_is_a_small_circle():
    assert "def _point_marker(" in SRC and "CircleMarker(location=(lat, lon)" in SRC
    assert 'Marker(location=' not in SRC.replace("CircleMarker(location=", "")
    assert app.POINT_STYLE["radius"] <= 8
    # the point is re-added above the watershed and reach after a draw
    assert '_add_layer("marker", _layers["marker"])' in SRC


def test_the_pane_clips_horizontal_overflow():
    assert "overflow-y: auto; overflow-x: hidden;" in CSS


def test_basin_pane_matches_easi_plus_the_nhdplusid():
    card = SRC.split("def basin_card():", 1)[1].split("def engine_line():", 1)[0]
    for gone in ('"Stream order"', '"Watershed basis"', '"Covered reach"',
                 '"Evidence reach COMID"', 'row("COMID"', "none within the substitution limit"):
        assert gone not in card, gone
    for kept in ('row("Watershed engine"', 'row("Drainage area"', 'row("Reach length"',
                 'row("NHDPlusID"', 'row("StreamCat reach"'):
        assert kept in card, kept
    assert app._watershed_engine_text({"watershedBasis": "site-engine",
                                       "siteEngine": {"engineVersion": "0.2.2"}}, {}, False) \
        == "STAF site engine v0.2.2"
    assert app._watershed_engine_text({}, {"status": "running"}, True) == "STAF site engine (calculating)"
    # a session from before 2026-09-07 drew the V2 basin with the engine beside it
    assert app._watershed_engine_text({}, {"status": "ok", "record": {"engineVersion": "0.3.0"}}, False) \
        == "StreamCat lookup engine (NHDPlus V2 basin), HR reach watershed computed"
    assert app._watershed_engine_text({}, {}, False) == "StreamCat lookup engine (NHDPlus V2 basin)"
    assert app._watershed_engine_text({"watershedBasis": "nhdplus-v2-basin-of-surrogate"}, {}, False) \
        == "StreamCat lookup engine (nearest StreamCat reach basin)"
    # the engine summary line is silent once the engine answered
    assert app._engine_line_ui({"status": "ok", "record": {"engineVersion": "0.2.2"}}, False, {}) is None
