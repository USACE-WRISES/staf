"""The Nationwide viewer mode: wiring pinned at the source level, plus the
viewer branches of the report flow run through the AST harness."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

app = pytest.importorskip("app")
from test_report_opening import harness  # noqa: E402

SRC = Path(app.__file__).read_text(encoding="utf-8")


def test_the_viewer_is_a_third_mode_beside_single_and_batch():
    # a switch in the middle of the header, with the circled i's card beside it
    assert 'ui.input_switch("viewer_on", "Nationwide screening", value=False)' in SRC
    assert 'class_="easi-mode-toggle"' in SRC and 'ui.output_ui("viewer_info", inline=True)' in SRC
    assert "not reviewed" in SRC[SRC.index("VIEWER_INTRO = ("):SRC.index("VIEWER_INTRO = (") + 400]
    info = SRC[SRC.index("def viewer_info():"):SRC.index("def viewer_info():") + 900]
    assert "VIEWER_INTRO" in info and "_viewer_summary_text" in info and "_info(html_tip=card)" in info
    assert "def _toggle_viewer():" in SRC and "nav_viewer" not in SRC and "viewer_exit" not in SRC
    assert SRC.count('ui.update_switch("viewer_on", value=False)') >= 2   # Batch and New analysis
    assert 'ui.output_ui("viewer_workspace")' in SRC
    # every single-site guard hides in both takeovers, never only in batch
    assert 'if app_mode() != "single"' in SRC
    for stale in ('def leftpane():\n        if app_mode() == "batch"',
                  'def worksheet():\n        if app_mode() == "batch"'):
        assert stale not in SRC


def test_vendored_maplibre_and_viewer_script_are_loaded():
    assert 'href="vendor/maplibre-gl.css"' in SRC
    assert 'src="vendor/maplibre-gl.js"' in SRC
    assert re.search(r'src="viewer\.js\?v=\d+"', SRC)
    www = Path(app.__file__).parent / "www"
    assert (www / "vendor" / "maplibre-gl.js").stat().st_size > 500_000
    assert (www / "viewer.js").exists()


def test_tiles_come_through_a_session_route_not_a_public_host():
    assert 'session.dynamic_route("national-tiles", _national_tiles_handler)' in SRC
    handler = re.search(r"async def _national_tiles_handler\(request\):.*?return Response\(content=data",
                        SRC, re.S)
    assert handler, "tile handler not found"
    body = handler.group(0)
    assert "national_tiles.default_store()" in body
    assert "Content-Encoding" in body and "status_code=204" in body


def test_report_context_and_completion_know_the_viewer_mode():
    from test_map_pick_lifecycle import _function
    h = harness()
    base = {"delineation": {"comid": 8566387, "gnis_name": "Rivanna River"}, "report": {},
            "watershed_geojson": None, "reach_geojson": None}
    # the harness compiles each closure over a copy of its namespace, so the
    # viewer value has to exist before the report functions are extracted
    h.scope["viewer_base"] = h.scope["base_result"].__class__(base)
    for name in ("_report_context_matches", "_cancel_report", "_cancel_stale_report",
                 "_begin_report", "_show_report_modal", "_report_map_done"):
        h.scope[name] = _function("easi", name, h.scope)
    h.scope["app_mode"].set("viewer")
    h.scope["_begin_report"](base)
    assert h.scope["_report_request"]()["mode"] == "viewer"
    assert h.scope["_report_context_matches"](h.scope["_report_request"]())
    h.task.finish(1)
    h.scope["_report_map_done"]()
    assert h.shown and h.shown[0][0] == "Rivanna River (COMID 8566387)"
    assert h.scope["batch_modal_site"]()["base"] is base
    # a different reach opened meanwhile invalidates the pending request
    h.scope["_begin_report"](base)
    h.scope["viewer_base"].set({"delineation": {"comid": 1}, "report": {}})
    assert not h.scope["_report_context_matches"](h.scope["_report_request"]())


def test_precomputed_line_reaches_the_basin_table_and_the_pdf_facts():
    from easi import report
    text = app._precomputed_text({"vintage": "2026.09", "tier": 1, "method_current": True})
    assert text == "national dataset 2026.09, tier 1"
    assert "rescored" in app._precomputed_text({"vintage": "2026.09", "method_current": False})
    def pairs(rep):
        return report._summary_pairs({"delineation": {"comid": 1},
                                      "report": {"subIndices": {}, **rep}})
    assert ("Precomputed national dataset", "vintage 2026.09, tier 1") in pairs(
        {"precomputed": {"vintage": "2026.09", "tier": 1}})
    assert not any(label.startswith("Precomputed") for label, _ in pairs({}))


def test_viewer_copy_has_no_em_dashes():
    block = SRC[SRC.index("Nationwide screening: the precomputed national dataset"):]
    strings = re.findall(r'"([^"\n]*)"', block)
    assert not any("—" in s for s in strings)
    js = (Path(app.__file__).parent / "www" / "viewer.js").read_text(encoding="utf-8")
    assert "—" not in js


def test_viewer_workspace_render_never_reads_the_summary():
    """The workspace holds the map's container: a reactive read of the summary
    inside it re-renders the workspace when the summary arrives and leaves
    MapLibre drawing into a detached div (2026-09-11). The tier sentence and
    the header line are their own outputs."""
    start = SRC.index("def viewer_workspace():")
    end = SRC.index("class_=\"easi-viewer\")", start)
    body = SRC[start:end]
    assert "viewer_summary()" not in body
    assert 'ui.output_ui("viewer_tier_note"' in body and 'ui.output_ui("viewer_summary_line"' in body
    assert "def viewer_tier_note():" in SRC
    # no banner row: the takeover is the map, its legend (summary + Refresh) and the status pill
    assert "easi-batch-head" not in body and "easi-viewer-intro" not in body
    assert 'ui.input_action_link("viewer_refresh", "Refresh")' in body
    assert 'class_="easi-viewer-legend-foot"' in body


def test_viewer_reports_open_in_two_phases():
    """The record scores at once (no network) and the modal opens with a
    placeholder thumbnail; the live basin and reach follow in a second task
    that swaps the placeholder in place."""
    assert "geometry=False" in SRC[SRC.index("async def open_precomputed_task"):SRC.index("async def viewer_geometry_task")]
    assert "precomputed_geometry_async" in SRC and "def _viewer_geometry_done" in SRC
    assert 'id="easi-viewer-minimap-pending"' in SRC
    assert 'selector="#easi-viewer-minimap-pending"' in SRC and 'ui.remove_ui("#easi-viewer-minimap-pending")' in SRC
    assert '"pending": bool(base.get("geometry_pending"))' in SRC
    assert "viewer_geometry_task(comid, generation)" in SRC
