"""Default stream presentation, optional coverage, and source-layer lifecycle."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

app = pytest.importorskip("app")


def _html(step="identify", zoomed=True, mode="segmented", reach=None, routed=False, **kwargs):
    return str(app._legend_ui(step, zoomed, mode, reach, routed, **kwargs) or "")


def test_default_legend_has_one_stream_style_and_no_source_details():
    html = _html(reach={"comid": 1, "name": "Mink Brook"}, routed=True,
                 source_visible=True, route_visible=True)
    assert "Map legend" in html and ">Streams<" in html
    assert app.FLOWLINE_STYLE["color"] in html
    assert app.HR_FLOWLINE_STYLE["color"] not in html
    assert "StreamCat" not in html and "Mink Brook" not in html
    assert "Connection to source" not in html


def test_coverage_legend_describes_the_network_without_promising_complete_data():
    html = _html(coverage=True)
    assert "StreamCat reaches" in html and "Other streams" in html
    assert app.FLOWLINE_STYLE["color"] in html and app.HR_FLOWLINE_STYLE["color"] in html
    assert "all data" not in html.lower()
    for jargon in ("STAF site engine", "COMID", "HR reach watershed"):
        assert jargon not in html


def test_source_rows_require_both_the_optional_view_and_available_geometry():
    reach = {"comid": 9327042, "name": "Mink Brook"}
    assert "Mink Brook" not in _html(reach=reach, coverage=True)
    html = _html(reach=reach, coverage=True, source_visible=True)
    assert "StreamCat source: Mink Brook" in html
    assert "easi-legend-sw-glow" in html
    html = _html(reach={"comid": 9327042, "name": None}, routed=True,
                 coverage=True, source_visible=True, route_visible=True)
    assert "Downstream source: unnamed stream" in html and "9327042" not in html
    assert "Connection to source" in html and "easi-legend-sw-dashed" in html
    route_only = _html(coverage=True, routed=True, route_visible=True)
    assert "Connection to source" in route_only and "easi-legend-sw-glow" not in route_only


def test_native_streams_visibility_hides_all_stream_and_source_legend_rows():
    html = _html(step="basin", streams_visible=False, coverage=True,
                 reach={"name": "Mink Brook"}, source_visible=True, route_visible=True)
    assert "Streams are hidden" in html
    assert "StreamCat reaches" not in html and "Other streams" not in html
    assert "Mink Brook" not in html and "Connection to source" not in html
    assert "Watershed" in html and "Assessment reach" in html


def test_notes_follow_zoom_and_fetch_state():
    assert "Zoom in to see streams" in _html(zoomed=False, mode=None)
    assert "Fine streams unavailable here. Zoom in." in _html(mode="v2-only")
    assert "easi-legend-note" not in _html(mode="hr-only")
    assert "StreamCat coverage unavailable here." in _html(mode="hr-only", coverage=True)
    assert "No streams in view." in _html(mode="empty")
    assert "easi-legend-note" not in _html(mode=None)


def test_basin_keeps_the_actual_assessment_reach_and_watershed():
    html = _html(step="basin")
    assert "Watershed" in html and "Assessment reach" in html
    assert app.WATERSHED_STYLE["fillColor"] in html and app.REACH_STYLE["color"] in html
    assert "easi-legend-sw-fill" in html
    assert "Assessment reach" not in _html(step="identify")


def test_hidden_outside_the_map_steps():
    for step in ("assess", "review", "measure", "report"):
        assert app._legend_ui(step, True, "segmented", None, False) is None


def test_dock_bridge_and_assets_are_wired():
    src = Path(app.__file__).read_text(encoding="utf-8")
    assert "legend-dock.js?v=3" in src
    assert 'id="easi-legend-panel"' in src.replace("'", '"')
    assert app.LAYER_STREAMS == "Streams"
    assert app.LAYER_COVERAGE == "StreamCat coverage"
    js = (Path(app.__file__).parent / "www" / "legend-dock.js").read_text(encoding="utf-8")
    assert "MutationObserver" in js and "disableClickPropagation" in js
    assert '"streamcat_coverage"' in js and '"streams_visible"' in js
    assert "insertAdjacentElement" in js and "shiny:connected" in js
    css = (Path(app.__file__).parent / "www" / "styles.css").read_text(encoding="utf-8")
    assert ".easi-legend-panel.leaflet-control" in css
    assert ".staf-coverage-toggle:focus-visible" in css
    assert ".easi-legend-sw-dashed" in css


@pytest.fixture
def streams(monkeypatch):
    # Use real ipyleaflet traits; only the Shiny session registration is disabled.
    from ipywidgets import Widget
    monkeypatch.setattr(Widget, "_widget_construction_callback", None)
    return app._StreamMapLayers()


def _fc(comid=1):
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"comid": comid},
        "geometry": {"type": "LineString", "coordinates": [[-83.05, 40.3], [-83.04, 40.3]]},
    }]}


def test_stream_group_starts_with_uniform_blue_and_no_source_children(streams):
    assert streams.group.name == "Streams"
    assert streams.group.layers == (streams.hrflow, streams.flow, streams.sources)
    assert streams.flow.style == streams.hrflow.style == app.FLOWLINE_STYLE
    assert streams.coverage is False and streams.sources.layers == ()


def test_toggling_coverage_keeps_geometry_and_reuses_cached_source_widgets(streams):
    covered, uncovered = _fc(1), _fc(2)
    original = copy.deepcopy((covered, uncovered))
    streams.set_data(covered, uncovered)
    glow = app.GeoJSON(data=_fc(3), style=app.SCORED_REACH_STYLE)
    route = app.GeoJSON(data=_fc(4), style=app.ROUTE_STYLE)
    streams.set_source("scored", glow)
    streams.set_source("route", route)
    assert streams.sources.layers == ()
    children = streams.group.layers
    for _ in range(3):
        streams.set_coverage(True)
        assert streams.hrflow.style == app.HR_FLOWLINE_STYLE
        assert streams.sources.layers == (glow, route)
        streams.set_coverage(False)
        assert streams.hrflow.style == streams.flow.style == app.FLOWLINE_STYLE
        assert streams.sources.layers == ()
    assert streams.group.layers == children
    assert (covered, uncovered) == original
    assert streams.flow.data["features"][0]["geometry"] == covered["features"][0]["geometry"]
    assert streams.hrflow.data["features"][0]["geometry"] == uncovered["features"][0]["geometry"]


def test_zoom_out_and_new_fetch_keep_group_identity_and_coverage_choice(streams):
    group, flow, hrflow, sources = streams.group, streams.flow, streams.hrflow, streams.sources
    streams.set_coverage(True)
    streams.set_data(_fc(1), _fc(2))
    streams.clear_streams()
    assert streams.flow.data["features"] == streams.hrflow.data["features"] == []
    streams.set_data(_fc(5), _fc(6))
    assert (streams.group, streams.flow, streams.hrflow, streams.sources) == (group, flow, hrflow, sources)
    assert streams.coverage is True and streams.hrflow.style == app.HR_FLOWLINE_STYLE


def test_new_pick_or_import_replaces_sources_without_resetting_preference(streams):
    old_glow = app.GeoJSON(data=_fc(1))
    old_route = app.GeoJSON(data=_fc(2))
    streams.set_coverage(True)
    streams.set_source("scored", old_glow)
    streams.set_source("route", old_route)
    streams.remove_source("route")
    streams.remove_source("scored")
    assert streams.sources.layers == () and streams.coverage is True
    replacement = app.GeoJSON(data=_fc(3))
    streams.set_source("scored", replacement)
    assert streams.sources.layers == (replacement,)
    streams.set_coverage(False)
    later = app.GeoJSON(data=_fc(4))
    streams.set_source("scored", later)  # async geometry arrives while coverage is off
    assert streams.sources.layers == ()
    streams.set_coverage(True)
    assert streams.sources.layers == (later,)


def test_source_overlays_are_nested_and_below_the_assessment_vectors(streams):
    mp = app.Map(panes=app.STREAM_PANES)
    mp.add(streams.group)
    glow = app.GeoJSON(data=_fc(1))
    route = app.GeoJSON(data=_fc(2))
    streams.set_source("scored", glow)
    streams.set_source("route", route)
    streams.set_coverage(True)
    assert streams.sources not in mp.layers and glow not in mp.layers and route not in mp.layers
    assert glow.pane == "staf-source-glow" and route.pane == "staf-source-route"
    assert streams.flow.pane == streams.hrflow.pane == "staf-streams"
    assert all(pane["zIndex"] < 400 for pane in app.STREAM_PANES.values())
    assert app.STREAM_PANES[glow.pane]["pointerEvents"] == "none"
    assert app.STREAM_PANES[route.pane]["pointerEvents"] == "none"
