"""The hand-built SVG / decision renderers for the "Scoring method" panel."""
from __future__ import annotations

from easi import method_plot, methods
from easi.metrics.biology import INVASIVES_ID
from easi.metrics.hydraulics import ENTRENCHMENT_ID, HYPORHEIC_ID, LOW_FLOW_ID
from easi.metrics.hydrology import IMPERVIOUS_ID
from easi.metrics.physicochemistry import IMPAIRMENT_ID


def test_scalar_svg_regions_markers_breakpoints():
    svg = method_plot.scalar_svg(methods.METHODS[ENTRENCHMENT_ID], 2.5, "Good", 1.3, "Poor")
    assert svg.startswith("<svg")
    for color in ("#c8d9f2", "#f5e7a6", "#f5b5b5"):     # Good / Fair / Poor regions
        assert color in svg
    assert "Site 2.5" in svg and "Explore 1.3" in svg
    assert "ER 1.4" in svg and "ER 2.2" in svg          # authored breakpoint labels
    assert "function score" not in svg                  # the summary footer was removed


def test_scalar_svg_no_explored_marker_when_unchanged():
    svg = method_plot.scalar_svg(methods.METHODS[ENTRENCHMENT_ID], 2.5, "Good", 2.5, "Good")
    # footer gone: no "Explore" marker AND no site/explore summary line
    assert "Explore" not in svg
    assert "function score" not in svg and "unchanged from site" not in svg


def test_best_svg_flags_governing_pathway():
    method = methods.METHODS[HYPORHEIC_ID]
    assert method.mode == "best"
    svg = method_plot.worst_svg(method, {"slope": 0.001, "sinuosity": 1.6},
                                governing="sinuosity")
    assert svg.startswith("<svg") and "governs" in svg
    assert "Channel slope" in svg and "Reach sinuosity" in svg
    assert svg.count("<rect") >= 6                       # two pathway panels x 3 bands


def test_count_svg_integer_axis():
    svg = method_plot.count_svg(methods.METHODS[INVASIVES_ID], 3, "Poor")
    assert svg.startswith("<svg") and "Site 3" in svg


def test_worst_svg_flags_governing():
    svg = method_plot.worst_svg(methods.METHODS[IMPERVIOUS_ID],
                                {"impervious": 2.0, "agriculture": 61.0}, governing="agriculture")
    assert svg.startswith("<svg") and "governs" in svg
    assert svg.count("<rect") >= 6                       # two indicator panels x 3 bands


def test_decision_html_highlights_site_category():
    # Low flow is no longer a categorical FCODE lookup — it scores NRSA wetted channel
    # with a StreamCat HYD fallback. Regulatory impairment is the categorical method now.
    method = methods.METHODS[IMPAIRMENT_ID]
    html = method_plot.decision_html(method, "Fair")
    assert html.count("easi-method-decide-row") == len(method.decisions)
    assert "easi-method-decide-row on" in html          # exactly one highlighted row
    assert "this reach" in html


def test_low_flow_is_a_scalar_wetted_channel_method():
    method = methods.METHODS[LOW_FLOW_ID]
    assert method.mode == "scalar"
    assert method.decisions == ()
    svg = method_plot.scalar_svg(method, 90.0, "Good")
    assert svg.startswith("<svg") and "Site 90" in svg
