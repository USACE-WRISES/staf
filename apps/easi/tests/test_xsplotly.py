"""Offline tests for the interactive cross-section Plotly figure (no network)."""
from __future__ import annotations

import pytest

from easi import xsplotly


def _v_profile():
    stations = list(range(0, 101))
    elevs = [abs(x - 50) * 0.5 for x in stations]  # V channel, thalweg 0 at x=50
    return stations, elevs


def test_figure_returns_plain_figure_with_traces_and_lines():
    import plotly.graph_objects as go

    s, e = _v_profile()
    fw = xsplotly.figure(s, e, thalweg=0.0, bankfull_stage=2.0, floodplain_stage=3.0)
    # a plain go.Figure (NOT a FigureWidget) so it needs no Shiny session; shinywidgets
    # coerces it to a widget in-session at render time
    assert isinstance(fw, go.Figure) and not isinstance(fw, go.FigureWidget)
    # terrain baseline + terrain fill + water baseline + water fill + bed line
    assert len(fw.data) == 5
    # bed datum + bankfull + floodprone + low bank
    assert len(fw.layout.shapes) == 4
    labels = {a.text for a in fw.layout.annotations}
    assert {"bankfull", "floodprone", "low bank"} <= labels


def test_figure_no_bankfull_still_renders():
    s, e = _v_profile()
    fw = xsplotly.figure(s, e, thalweg=0.0)     # no stages -> transparent (zero-area) water
    assert len(fw.data) == 5                     # fixed 5-trace structure regardless
    # the water fill (trace 3) collapses to the bed and is drawn transparent
    assert fw.data[3].fillcolor == "rgba(0,0,0,0)"


def test_figure_fixed_five_trace_structure():
    """The report updates the live widget by position (zip), so figure() must always return
    the same 5 traces whether or not a bankfull stage is supplied."""
    s, e = _v_profile()
    assert len(xsplotly.figure(s, e, thalweg=0.0, bankfull_stage=2.0).data) == 5
    assert len(xsplotly.figure(s, e, thalweg=0.0).data) == 5


def test_figure_unit_scaling_ft_vs_m():
    s, e = _v_profile()
    ft = xsplotly.figure(s, e, thalweg=0.0, bankfull_stage=2.0, unit="ft")
    m = xsplotly.figure(s, e, thalweg=0.0, bankfull_stage=2.0, unit="m")
    # symmetric x-range; feet are ~3.28x the metres value
    assert ft.layout.xaxis.range[1] == pytest.approx(m.layout.xaxis.range[1] * xsplotly.FT_PER_M,
                                                     rel=1e-6)
    assert "ft" in ft.layout.xaxis.title.text
    assert "m" in m.layout.xaxis.title.text


def test_figure_dragmode_zoom_and_windowed_x():
    s, e = _v_profile()
    fw = xsplotly.figure(s, e, thalweg=0.0, bankfull_stage=2.0)
    assert fw.layout.dragmode == "zoom"                       # drag-a-box zoom
    lo, hi = fw.layout.xaxis.range
    assert lo == pytest.approx(-hi)                # symmetric profile -> symmetric window
    # default view is windowed, not the full +/-50 m transect: the V walls cross the
    # ceiling (1.5 x floodprone) 12 m out, so the 15 m min-half floor governs
    full = 50.0 * xsplotly.FT_PER_M
    assert hi < 0.5 * full


def test_figure_no_fixed_height_and_trimmed_modebar():
    s, e = _v_profile()
    fw = xsplotly.figure(s, e, thalweg=0.0, bankfull_stage=2.0)
    assert fw.layout.height is None                            # no fixed height -> fills container
    assert fw.layout.title.text is None                        # no in-figure title
    remove = set(fw.layout.modebar.remove or ())
    # native reset/autoscale removed (the app supplies labeled Reset view / Full extent
    # buttons); box-zoom, pan, and the +/- magnifiers are kept
    assert {"resetScale2d", "autoScale2d", "select2d", "lasso2d", "toImage"} <= remove
    assert not ({"zoom2d", "pan2d", "zoomIn2d", "zoomOut2d"} & remove)


def test_water_fill_flat_top_on_sparse_steep_bank():
    """Regression: the bankfull water top must be flat at bf and pinch where the bed
    crosses it — not slant up a steep, sparsely-sampled bank to the next high point."""
    # thalweg at 0, then a steep bank 0 -> 6.5 with NO data point at the bf crossing
    stations = [-80.0, -40.0, 0.0, 30.0, 50.0]
    elevs = [2.0, 0.8, 0.0, 6.5, 13.0]
    bf = 2.0
    fw = xsplotly.figure(stations, elevs, thalweg=0.0, bankfull_stage=bf, unit="m")
    bed, surf = fw.data[2], fw.data[3]                  # water baseline (bed) + water surface
    for b, s in zip(bed.y, surf.y):
        assert s == pytest.approx(max(b, bf))          # surface = max(bed, bf)
        if b < bf - 1e-9:
            assert s == pytest.approx(bf)              # flat top, never rides up the bank
    assert any(b == pytest.approx(bf) for b in bed.y)  # a bf crossing point was inserted
    submerged = [s for b, s in zip(bed.y, surf.y) if b < bf - 1e-9]
    assert submerged and max(submerged) == pytest.approx(bf)   # old bug drew 6.5 here


def test_water_polygon_inserts_crossing():
    from easi import geomorph

    span = geomorph.wetted_span([0.0, 30.0], [0.0, 6.5], 2.0)
    x2, bed2, surf2 = xsplotly._water_polygon([0.0, 30.0], [0.0, 6.5], 2.0, span=span)
    assert 2.0 in bed2                                  # crossing bed value inserted
    assert x2[bed2.index(2.0)] == pytest.approx(30.0 * 2.0 / 6.5)  # true crossing (~9.2 ft)
    assert surf2 == [2.0, 2.0, 6.5]                     # flat at bf until the crossing, then bed


def test_water_polygon_no_span_means_no_water():
    x2, bed2, surf2 = xsplotly._water_polygon([0.0, 30.0], [0.0, 6.5], 2.0)
    assert bed2 == surf2 == [0.0, 6.5]                  # surface hugs the bed (zero area)


def test_water_fill_excludes_disconnected_pocket():
    """A low pocket beyond the bank sits below bankfull but is walled off from the
    channel: flow_width/flow_area never count it, so the plot must not shade it."""
    stations = [-60.0, -50.0, -40.0, -20.0, 0.0, 20.0, 40.0]
    elevs = [3.0, 0.5, 3.0, 3.0, 0.0, 3.0, 3.0]   # pocket at -50; channel V at 0
    fw = xsplotly.figure(stations, elevs, thalweg=0.0, bankfull_stage=2.0, unit="m")
    bed, surf = fw.data[2], fw.data[3]
    # water present inside the channel span (+/-13.3 m around the thalweg)...
    assert any(s > b + 1e-9 for xv, b, s in zip(bed.x, bed.y, surf.y) if -14 <= xv <= 14)
    # ...but the pocket (and everything beyond the bank) stays dry: surface == bed
    assert all(s == pytest.approx(b) for xv, b, s in zip(bed.x, bed.y, surf.y) if xv <= -40)


def test_view_window_trims_high_valley_wall():
    """A valley wall climbing far above the reference lines must not stretch the
    default view: x stops where the terrain passes VIEW_HEADROOM x the highest
    line, and y tops out at that ceiling (the wall exits the top of the plot)."""
    stations = list(range(-100, 101, 2))
    elevs = [1.5 if x < 0 else x * 0.2 for x in stations]   # flat left; 20 m wall right
    fw = xsplotly.figure(stations, elevs, thalweg=0.0, bankfull_stage=2.0,
                         floodplain_stage=None, unit="m")
    x_lo, x_hi = fw.layout.xaxis.range
    # ceiling = 1.5 * (2 x bankfull) = 6 m, crossed at x=+30 -> right side trimmed there
    assert 29.0 <= x_hi <= 36.0
    assert x_lo <= -100.0                       # flat side keeps its full extent
    y_lo, y_hi = fw.layout.yaxis.range
    assert y_hi < 8.0                           # capped near the 6 m ceiling, not 20 m


def _annotations_by_text(fw):
    return {a.text: a for a in fw.layout.annotations}


def test_low_bank_label_shifts_left_when_overlapping_floodprone():
    # low bank at the floodprone stage (the capped default): its label shifts left
    # so it reads beside "floodprone" instead of overprinting it
    s, e = _v_profile()
    fw = xsplotly.figure(s, e, thalweg=0.0, bankfull_stage=2.0, floodplain_stage=4.0)
    ann = _annotations_by_text(fw)
    assert (ann["low bank"].xshift or 0) < 0
    assert not ann["floodprone"].xshift and not ann["bankfull"].xshift


def test_low_bank_label_shifts_left_when_overlapping_bankfull():
    # the slope-break default can land at/near bankfull (a low bench)
    s, e = _v_profile()
    fw = xsplotly.figure(s, e, thalweg=0.0, bankfull_stage=2.0, floodplain_stage=2.0)
    assert (_annotations_by_text(fw)["low bank"].xshift or 0) < 0


def test_low_bank_label_normal_when_separated():
    s, e = _v_profile()
    fw = xsplotly.figure(s, e, thalweg=0.0, bankfull_stage=2.0, floodplain_stage=3.0)
    assert not _annotations_by_text(fw)["low bank"].xshift


def test_figure_source_caption():
    s, e = _v_profile()
    fw = xsplotly.figure(s, e, thalweg=0.0, bankfull_stage=2.0, source="USGS 3DEP 1 m DEM")
    assert "USGS 3DEP 1 m DEM" in {a.text for a in fw.layout.annotations}  # bottom caption
    # no source -> no caption (and the bottom margin stays tight)
    fw2 = xsplotly.figure(s, e, thalweg=0.0, bankfull_stage=2.0)
    assert "USGS 3DEP 1 m DEM" not in {a.text for a in fw2.layout.annotations}
