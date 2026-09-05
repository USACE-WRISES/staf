"""The Identify pane after the 2026-09-04 trim (the EASI changes ported to
SFARI): the pin lands on click, the viewport's HR lines settle a cyan click,
one short instruction, no credit line, the attribution in Help, short snap
lines, no cue printed twice, middle dots in the pane titles. The pane is
Shiny UI, so this reads the source."""
from __future__ import annotations

from pathlib import Path

import pytest

app = pytest.importorskip("app")
SRC = Path(app.__file__).read_text(encoding="utf-8")
CSS = (Path(app.__file__).parent / "www" / "styles.css").read_text(encoding="utf-8")


def test_the_pin_lands_before_the_routing_on_every_hr_branch():
    # the viewport hit and the click-box or typed-coordinate hit
    assert SRC.count("_place_pin(hr_hit[0], hr_hit[1])") == 2
    for branch in SRC.split("_place_pin(hr_hit[0], hr_hit[1])")[1:]:
        assert branch.lstrip().startswith("stage.set(_LOCATING_TEXT)")
    assert "def _place_pin(" in SRC
    assert 'nearest_point_on_lines(hr_fc, lat, lon, id_prop="nhdplusid")' in SRC


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
    for notice in ("Place not found. Try a city", "Delineation failed. Try another point"):
        assert notice in SRC


def test_the_cue_is_not_printed_twice():
    body = SRC.split("def snap_status():", 1)[1].split("@render.ui", 1)[0]
    assert "if stage() == _LOCATING_TEXT:" in body and "return None" in body


def test_numbers_are_formatted():
    assert app._fmt_km2(0.9871999900000001) == "0.99 km²"
    assert app._fmt_km2(None) == "unknown"
    assert app._fmt_ft(1000.0) == "1,000 ft"
    assert 'row("Drainage area", _fmt_km2(d.get("drainage_area_sqkm")))' in SRC


def test_styles_carry_the_tighter_divider_and_the_new_version():
    assert 'href="styles.css?v=18"' in SRC
    assert ".easi-pane-body hr { margin: 8px 0; }" in CSS
    assert ".easi-ac-credit" not in CSS


def test_snap_helper_reads_the_hr_id():
    from sfari.datasources import flowlines
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"nhdplusid": 10000600001216},
         "geometry": {"type": "LineString", "coordinates": [[-83.06, 40.31], [-83.05, 40.31]]}}]}
    hit = flowlines.nearest_point_on_lines(fc, 40.3101, -83.055, id_prop="nhdplusid")
    assert hit is not None and hit[3] == 10000600001216 and hit[2] < 100
    assert flowlines.nearest_point_on_lines(fc, 40.3101, -83.055)[3] is None   # no comid property
