"""The Identify pane after the 2026-09-04 trim: the pin lands on click, one
short instruction, no credit line, the attribution in Help, and no cue
printed twice. The pane is Shiny UI, so this reads the source."""
from __future__ import annotations

from pathlib import Path

import pytest

app = pytest.importorskip("app")
SRC = Path(app.__file__).read_text(encoding="utf-8")
CSS = (Path(app.__file__).parent / "www" / "styles.css").read_text(encoding="utf-8")


def test_the_pin_lands_before_the_routing_on_every_hr_branch():
    # the viewport hit, the click-box hit, and the typed-coordinate hit
    assert SRC.count("_place_pin(hr_hit[0], hr_hit[1])") == 3
    for branch in SRC.split("_place_pin(hr_hit[0], hr_hit[1])")[1:]:
        assert branch.lstrip().startswith("stage.set(_LOCATING_TEXT)")
    assert "def _place_pin(" in SRC
    # a failed routing takes its pin back
    assert SRC.count('_remove_layer("marker")') >= 3


def test_a_new_pick_invalidates_the_last_point():
    # a miss after a routed pick used to leave that point's pin and an enabled
    # Delineate button behind, and the status line then read it as a covered
    # snap (2026-09-04): both pick paths clear the point first, a miss takes
    # the pin
    block = "_clear_route_state()\n                snapped_point.set(None)"
    assert SRC.count(block) == 2
    miss = SRC.split("def _apply_click_snap():", 1)[1]
    miss = miss.split("ui.notification_show(_MISS_TEXT", 1)[0]
    assert miss.rstrip().endswith("see above")


def test_the_pane_copy_is_short():
    assert "Zoom in and click a stream, search a place, or enter coordinates." in SRC
    assert "Zoom in until stream lines appear" not in SRC
    assert "Type to search" not in SRC and "easi-ac-credit" not in SRC
    assert "No point yet." in SRC and "No point yet. Enter" not in SRC
    assert '"StreamCat lookup engine.", class_="easi-snap-note ok"' in SRC
    assert "Click “Delineate" not in SRC
    assert "Address search uses OpenStreetMap data (Photon and Nominatim)." in SRC


def test_the_cue_is_not_printed_twice():
    body = SRC.split("def snap_status():", 1)[1].split("@render.ui", 1)[0]
    assert "return None" in body.split("if cue in", 1)[1].split("\n", 2)[1]


def test_styles_carry_the_tighter_divider_and_the_new_version():
    assert 'href="styles.css?v=45"' in SRC
    assert ".easi-pane-body hr { margin: 8px 0; }" in CSS
    assert ".easi-ac-credit" not in CSS


def test_modules_used_at_effect_level_are_imported_at_module_level():
    # _sync_xsection_plot calls xsplotly.sync_payload without a local import; the
    # first candidate switch raised NameError live (2026-09-04)
    import re
    head = SRC.split("from easi import (", 1)[1].split(")", 1)[0]
    for name in ("notices", "xsplotly", "basin"):
        assert re.search(rf"\b{name}\b", head), name


def test_the_selected_point_is_a_small_circle():
    assert "def _point_marker(" in SRC and "CircleMarker(location=(lat, lon)" in SRC
    assert 'Marker(location=' not in SRC.replace("CircleMarker(location=", "")
    assert app.POINT_STYLE["radius"] <= 8
    # the point is re-added above the watershed and reach after a draw
    assert '_add_layer("marker", _layers["marker"])' in SRC


def test_the_pane_clips_horizontal_overflow():
    assert "overflow-y: auto; overflow-x: hidden;" in CSS
