"""When the map fetches stream vectors (2026-09-02).

A pan of a few dozen pixels used to refetch both networks and rebuild the
layers, which the user saw as "Loading streams" and a flash. The fetch box is
now wider than tall from zoom 15, and a fetch is due only when the viewport
leaves the box last fetched.
"""
from __future__ import annotations

from deep import viewport as vp

LAT, LON = 43.6865, -72.2371     # Mink Brook, NH


def _view(center_lat, center_lon, zoom, width_px=1200, height_px=800):
    """A viewport for a 1200 by 800 px map at the Web Mercator resolution."""
    import math
    m_per_px = 156543.03 * math.cos(math.radians(center_lat)) / (2 ** zoom)
    half_w_deg = (width_px / 2) * m_per_px / (111_320 * math.cos(math.radians(center_lat)))
    half_h_deg = (height_px / 2) * m_per_px / 110_574
    return (center_lat - half_h_deg, center_lon - half_w_deg,
            center_lat + half_h_deg, center_lon + half_w_deg)


def _size(box):
    w, s, e, n = box
    return e - w, n - s


def _close(a, b, tol=0.0025):
    # outward rounding to 3 decimals can widen a side by up to 0.001
    return abs(a - b) <= tol


def test_box_is_wide_from_zoom_15_and_square_at_14():
    width, height = _size(vp.fetch_box(LAT, LON, 15))
    assert _close(width, 0.12) and _close(height, 0.06)
    width, height = _size(vp.fetch_box(LAT, LON, 14))
    assert _close(width, 0.12) and _close(height, 0.12)
    width, height = _size(vp.fetch_box(LAT, LON, 16))
    assert _close(width, 0.06) and _close(height, 0.03)


def test_box_respects_the_hr_area_cap_at_every_fetch_zoom():
    for zoom in (14, 15, 16, 17, 18):
        assert vp.area_deg2(vp.fetch_box(LAT, LON, zoom)) <= 0.02, zoom


def test_box_contains_the_viewport_with_a_margin():
    for zoom in (14, 15, 16, 17):
        box = vp.fetch_box(LAT, LON, zoom)
        assert not vp.needs_fetch(_view(LAT, LON, zoom), box), zoom


def test_first_view_and_unknown_view_fetch():
    assert vp.needs_fetch(_view(LAT, LON, 16), None)
    assert vp.needs_fetch(None, vp.fetch_box(LAT, LON, 16))


def test_a_pan_inside_the_box_does_not_fetch_and_a_pan_past_it_does():
    box = vp.fetch_box(LAT, LON, 16)
    assert not vp.needs_fetch(_view(LAT, LON + 0.008, 16), box)     # about 350 px east
    assert vp.needs_fetch(_view(LAT, LON + 0.05, 16), box)          # well past the margin
    assert vp.needs_fetch(_view(LAT + 0.02, LON, 16), box)          # north past the margin


def test_zoom_in_stays_inside_and_a_wide_zoom_out_fetches():
    box = vp.fetch_box(LAT, LON, 16)
    assert not vp.needs_fetch(_view(LAT, LON, 17), box)
    # one level out still fits inside the padded box (the lines are the same
    # data at any zoom); two levels out does not
    assert not vp.needs_fetch(_view(LAT, LON, 15), box)
    assert vp.needs_fetch(_view(LAT, LON, 14), box)


def test_box_rounds_outward_so_it_never_shrinks():
    w, s, e, n = vp.fetch_box(43.68652, -72.23709, 16)
    half_lon, half_lat = vp.half_sizes(16)
    assert w <= -72.23709 - half_lon and e >= -72.23709 + half_lon
    assert s <= 43.68652 - half_lat and n >= 43.68652 + half_lat


def test_view_from_bounds_handles_the_trait_shape_and_junk():
    assert vp.view_from_bounds(((43.68, -72.24), (43.69, -72.23))) == (43.68, -72.24, 43.69, -72.23)
    assert vp.view_from_bounds(None) is None
    assert vp.view_from_bounds(((0, 0), (0, 0))) == (0.0, 0.0, 0.0, 0.0)
    assert vp.view_from_bounds(((1, 1), (0, 0))) is None
