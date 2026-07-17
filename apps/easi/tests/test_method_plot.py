"""The hand-built SVG / decision renderers for the "Scoring method" panel."""
from __future__ import annotations

from easi import method_plot, methods
from easi.metrics.biology import INVASIVES_ID
from easi.metrics.hydraulics import HYPORHEIC_ID, LOW_FLOW_ID
from easi.metrics.hydrology import IMPERVIOUS_ID


def test_scalar_svg_regions_markers_breakpoints():
    svg = method_plot.scalar_svg(methods.METHODS[HYPORHEIC_ID], 0.75, "Good", 0.42, "Fair")
    assert svg.startswith("<svg")
    for color in ("#c8d9f2", "#f5e7a6", "#f5b5b5"):     # Good / Fair / Poor regions
        assert color in svg
    assert "Site 0.75" in svg and "Explore 0.42" in svg
    assert "V 0.30" in svg and "V 0.60" in svg          # authored breakpoint labels
    assert "function score" not in svg                  # the summary footer was removed


def test_scalar_svg_no_explored_marker_when_unchanged():
    svg = method_plot.scalar_svg(methods.METHODS[HYPORHEIC_ID], 0.75, "Good", 0.75, "Good")
    # footer gone: no "Explore" marker AND no site/explore summary line
    assert "Explore" not in svg
    assert "function score" not in svg and "unchanged from site" not in svg


def test_count_svg_integer_axis():
    svg = method_plot.count_svg(methods.METHODS[INVASIVES_ID], 3, "Poor")
    assert svg.startswith("<svg") and "Site 3" in svg


def test_worst_svg_flags_governing():
    svg = method_plot.worst_svg(methods.METHODS[IMPERVIOUS_ID],
                                {"impervious": 2.0, "agriculture": 61.0}, governing="agriculture")
    assert svg.startswith("<svg") and "governs" in svg
    assert svg.count("<rect") >= 6                       # two indicator panels x 3 bands


def test_decision_html_highlights_site_category():
    html = method_plot.decision_html(methods.METHODS[LOW_FLOW_ID], "Fair")
    assert html.count("easi-method-decide-row") == 3
    assert "easi-method-decide-row on" in html          # exactly one highlighted row
    assert "this reach" in html
