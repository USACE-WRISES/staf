"""The Identify pane after the 2026-09-04 trim (the EASI changes ported to
SFARI) and the 2026-09-07 two-engine map: the pin lands on click, the
viewport's HR lines settle a click, the StreamCat reach resolves in the
background, one short instruction, no credit line, the attribution in Help,
short snap lines, no cue printed twice, middle dots in the pane titles. The
pane is Shiny UI, so this reads the source."""
from __future__ import annotations

from pathlib import Path

import pytest

app = pytest.importorskip("app")
SRC = Path(app.__file__).read_text(encoding="utf-8")
CSS = (Path(app.__file__).parent / "www" / "styles.css").read_text(encoding="utf-8")


def test_lookup_spinner_is_small_inline_and_stationary_with_reduced_motion():
    import re
    rule = re.search(r"\.easi-spinner\.easi-lookup-spinner\s*\{([^}]+)", CSS).group(1)
    assert "width: 12px" in rule and "height: 12px" in rule
    assert "display: inline-block" in rule and "margin-right: 6px" in rule
    assert "animation: easi-spin .8s linear infinite" in CSS
    assert "border-top-color: var(--easi-accent)" in CSS
    reduced = re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([^}]+)", CSS).group(1)
    assert ".easi-lookup-spinner" in reduced and "animation: none" in reduced
    assert "5, 10, and 15 seconds" in SRC and "up to three" in SRC


def test_every_click_snaps_to_the_nhd_and_pins_at_once():
    # every click snaps to the HR line and the pin lands at once; the StreamCat
    # reach (the V2 line under the click, else the nearest StreamCat reach
    # downstream) resolves in the background and never moves the pin
    assert "def _place_pin(" in SRC
    assert 'nearest_point_on_lines(fc, lat, lon, id_prop="nhdplusid")' in SRC
    assert "hr_site.snap_point(lat, lon)" in SRC
    assert "async def anchor_task(" in SRC and "comid_anchor.resolve(" in SRC
    assert "network_display.fetch_streams(bbox, tol_ft=SNAP_TOL_FT)" in SRC
    for gone in ("snap_both", "route_from_hr", "pending_anchor", "_surrogate_offer",
                 "delineate_task", "hr_flow_task", "async def flow_task(",
                 "substitution limit"):
        assert gone not in SRC, gone

def test_delineate_passes_the_typed_reach_length_to_the_engine():
    # the typed assessment reach reaches the engine (2026-09-07; it was always 1,000 ft)
    body = SRC.split("def _start_delineate():", 1)[1].split("def _with_anchor(", 1)[0]
    assert "_launch_engine(lat, lon, float(input.reach_ft() or DEFAULT_REACH_FT))" in body
    assert "engine_prefill.run_engine(lat, lon, reach_length_ft=reach_ft, progress=_cb)" in SRC


def test_the_assessment_waits_for_the_streamcat_reach_before_the_pull():
    # A stopped task can mean failure: readiness, not task completion, gates work.
    enter = SRC.split("def _enter_review():", 1)[1].split("def _maybe_pull():", 1)[0]
    assert "pull_task(" not in enter
    body = SRC.split("def _maybe_pull():", 1)[1].split("def _pull_poll():", 1)[0]
    assert 'if not _source_ready(d):' in body
    assert '_launch_pull(d2, es)' in body
    assert 'pull_task.status() == "running"' in body            # never queue a second pull
    busy = SRC.split("def busy_text():", 1)[1].split("@render", 1)[0]
    assert "anchor_task.status()" not in busy  # source status has its own persistent output
    snap = SRC.split("def _apply_snap(hit, *, click=None):", 1)[1].split("def _apply_snap_result", 1)[0]
    assert "_begin_lookup()" in snap
    assert 'ui.output_ui("streamcat_lookup_status")' in SRC
    assert "upgrade_pending" not in SRC                            # legacy field, never read


def test_the_pane_copy_is_short_and_plain():
    assert "Zoom in and click a stream, search a place, or enter coordinates." in SRC
    assert "Zoom in until stream lines appear and click a stream to place" not in SRC
    assert "Type to search" not in SRC and "easi-ac-credit" not in SRC
    assert '"No point yet."' in SRC and "No point yet —" not in SRC
    assert "Address search uses OpenStreetMap data (Photon and Nominatim)." in SRC
    assert "Click \u201cDelineate\u201d" not in SRC
    assert '"SFARI · Assessment"' in SRC and 'f"SFARI · {head_label}"' in SRC
    # the module docstring may keep its dash; no string literal does
    assert '"SFARI — ' not in SRC and 'f"SFARI — ' not in SRC
    assert app._MISS_TEXT == "No stream line within 150 ft of the click. Zoom in and click a line."
    for notice in ("Place not found. Try a city",):
        assert notice in SRC


def test_the_snap_line_confirms_the_snap_and_nothing_more():
    """The engine timing note was dropped (2026-09-08): the snap line says the
    click landed on a stream, and the anchor line says where values come from
    once it is known. What the engine is doing is not the assessor's concern."""
    body = SRC.split("def snap_status():", 1)[1].split("@render.ui", 1)[0]
    assert "Snapped to a stream" in body
    assert "comid_anchor.snap_line(site_anchor())" in body
    assert "STAF site engine" not in body

def test_numbers_are_formatted():
    assert app._fmt_km2(0.9871999900000001) == "0.99 km²"
    assert app._fmt_km2(None) == "unknown"
    assert app._fmt_ft(1000.0) == "1,000 ft"
    assert 'row("Drainage area", _fmt_km2(d.get("drainage_area_sqkm")))' in SRC


def test_styles_carry_the_tighter_divider_and_the_new_version():
    assert 'href="styles.css?v=25"' in SRC
    assert ".easi-pane-body hr { margin: 8px 0; }" in CSS
    assert ".easi-ac-credit" not in CSS


def test_snap_helper_reads_the_hr_id():
    from sfari.datasources import flowlines
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"nhdplusid": 10000600001216},
         "geometry": {"type": "LineString", "coordinates": [[-83.06, 40.31], [-83.05, 40.31]]}}]}
    hit = flowlines.nearest_point_on_lines(fc, 40.3101, -83.055, id_prop="nhdplusid")
    assert hit is not None and hit[3] == 10000600001216 and hit[2] < 100
    assert flowlines.nearest_point_on_lines(fc, 40.3101, -83.055, id_prop="comid")[3] is None


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
                 "none within the substitution limit"):
        assert gone not in card, gone
    assert 'row("Watershed engine"' not in card
    assert 'ui.output_ui("engine_line")' in card  # retain progress and failure messages
    for kept in ('row("Drainage area"', 'row("Reach length"',
                 'row("NHDPlusID"', 'row("StreamCat reach"'):
        assert kept in card, kept
    assert app._watershed_engine_text({"watershedBasis": "site-engine",
                                       "siteEngine": {"engineVersion": "0.2.2"}}, {}, False) \
        == "STAF site engine v0.2.2"
    assert app._watershed_engine_text({}, {"status": "running"}, True) == "STAF site engine (calculating)"
    # a session from before 2026-09-07 drew the V2 basin with the engine beside it (DEEP's rule)
    assert app._watershed_engine_text({}, {"status": "ok", "record": {"engineVersion": "0.3.0"}}, False) \
        == "StreamCat lookup engine (NHDPlus V2 basin), HR reach watershed computed"
    assert app._watershed_engine_text({}, {}, False) == "StreamCat lookup engine (NHDPlus V2 basin)"
    assert app._watershed_engine_text({"watershedBasis": "nhdplus-v2-basin-of-surrogate"}, {}, False) \
        == "StreamCat lookup engine (nearest StreamCat reach basin)"
    # the NHDPlusID row only when known; a legacy session's bare COMID still names the reach
    assert 'if d.get("nhdplus_id") is not None' in card
    assert 'comid_anchor.saved_anchor(d_all)' in card
    # the engine summary line is silent once the engine answered
    assert app._engine_line_ui({"status": "ok", "record": {"engineVersion": "0.2.2"}}, False, {}) is None
