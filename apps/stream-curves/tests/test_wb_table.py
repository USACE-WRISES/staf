"""Tests for the shared Discipline | Function | Metric renderer (views.wb_table)."""

from shiny import ui

from streamcurves.staf_library import staf_functions_by_discipline
from views.wb_table import render_wb_table


def _cells():
    def fn_cell(fn):
        return ui.tags.td(fn, class_="wb-fn")

    def metrics_cell(fn):
        return ui.tags.td("chip-" + fn, class_="wb-metrics")

    return fn_cell, metrics_cell


def test_render_wb_table_structure_and_skips_empty_disciplines():
    fn_cell, metrics_cell = _cells()
    by = {
        "Hydrology": ["Catchment hydrology", "Reach inflow"],
        "Biology": [],  # empty discipline -> tbody skipped
    }
    html = str(render_wb_table(by, fn_cell=fn_cell, metrics_cell=metrics_cell))
    assert "wb-table" in html
    assert "wb-disc" in html and "discipline-hydrology" in html
    assert "Catchment hydrology" in html
    assert "chip-Catchment hydrology" in html
    assert "Biology" not in html  # no functions -> discipline block omitted


def test_render_wb_table_covers_all_20_functions():
    fn_cell, metrics_cell = _cells()
    html = str(
        render_wb_table(staf_functions_by_discipline(), fn_cell=fn_cell, metrics_cell=metrics_cell)
    )
    # one representative function per discipline (canonical names, &-free so the
    # assertion doesn't trip over HTML-escaping of "&" -> "&amp;")
    for fn in [
        "Catchment hydrology",
        "Floodplain connectivity",
        "Channel and floodplain dynamics",
        "Nutrient cycling",
        "Habitat provision",
    ]:
        assert fn in html
