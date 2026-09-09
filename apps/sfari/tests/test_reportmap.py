"""The report watershed map over a USGS topo basemap.

Byte-identical in EASI, SFARI and DEEP, like the module it tests. Nothing here
touches the network: the suite-wide guard in conftest turns a real fetch into a
failure, and every case that wants a basemap stubs the bytes.

The assertion that matters most is the fallback: a refused service has to leave
the outline drawn, because that is what a desktop with no network and a Connect
outage both look like.
"""
from __future__ import annotations

import io

import pytest

from sfari import reportmap

WS = {"type": "FeatureCollection", "features": [{"geometry": {
    "type": "Polygon", "coordinates": [[[-72.25, 43.68], [-72.22, 43.685],
                                        [-72.225, 43.695], [-72.248, 43.692],
                                        [-72.25, 43.68]]]}}]}
RC = {"type": "FeatureCollection", "features": [{"geometry": {
    "type": "LineString", "coordinates": [[-72.24, 43.685], [-72.235, 43.69]]}}]}


def _png() -> bytes:
    """A real PNG, small: the module checks the magic bytes before believing it."""
    PIL = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    PIL.new("RGB", (8, 5), (120, 140, 110)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def basemap(monkeypatch):
    png = _png()
    monkeypatch.setattr(reportmap, "topo_png", lambda *a, **k: png)
    return png


@pytest.fixture
def no_basemap(monkeypatch):
    monkeypatch.setattr(reportmap, "topo_png", lambda *a, **k: None)


# --------------------------------------------------------------------------- #
# the frame: what keeps the outline on the right valley
# --------------------------------------------------------------------------- #
def test_the_frame_matches_the_card_so_the_raster_cannot_stretch():
    bbox, _project = reportmap.frame(WS, RC, 290, 180)
    assert (bbox[2] - bbox[0]) / (bbox[3] - bbox[1]) == pytest.approx(290 / 180)


def test_the_projection_is_web_mercator_and_lands_inside_the_card():
    """The basemap is drawn in Mercator, so the rings have to be too. The old
    thumbnail used an equirectangular fit, which does not register against it."""
    bbox, project = reportmap.frame(WS, RC, 290, 180)
    x, y = reportmap.mercator(-72.25, 43.68)
    assert bbox[0] < x < bbox[2] and bbox[1] < y < bbox[3]
    px, py = project(-72.25, 43.68)
    assert 0 <= px <= 290 and 0 <= py <= 180
    # north is up: a higher latitude is a smaller y
    assert project(-72.25, 43.70)[1] < project(-72.25, 43.66)[1]


def test_a_tiny_watershed_is_widened_to_a_scale_the_service_can_draw():
    """The USGS caches stop near zoom 16, so a headwater basin asking for a
    closer scale would come back blank."""
    tiny = {"type": "FeatureCollection", "features": [{"geometry": {
        "type": "Polygon", "coordinates": [[[-72.2500, 43.6800], [-72.2499, 43.6800],
                                            [-72.2499, 43.6801], [-72.2500, 43.6800]]]}}]}
    bbox, _ = reportmap.frame(tiny, None, 290, 180)
    assert (bbox[3] - bbox[1]) >= reportmap.MIN_EXTENT_M


def test_no_geometry_has_no_frame():
    assert reportmap.frame(None, None, 290, 180) is None
    assert reportmap.frame({"features": []}, {"features": []}, 290, 180) is None


# --------------------------------------------------------------------------- #
# the SVG
# --------------------------------------------------------------------------- #
def test_the_basemap_sits_behind_the_outline(basemap):
    s = reportmap.svg(WS, RC)
    assert s.startswith("<svg") and s.endswith("</svg>")
    assert s.count("<image") == 1
    assert "data:image/png;base64," in s
    # order is what puts the topo behind rather than over the watershed
    assert s.index("<image") < s.index("<path")
    assert s.count("<path") == 2                      # one watershed, one reach
    assert reportmap.WATERSHED_FILL in s and reportmap.REACH_STROKE in s


def test_a_refused_service_still_draws_the_outline(no_basemap):
    """The fallback. An offline desktop and a Connect outage both land here."""
    s = reportmap.svg(WS, RC)
    assert "<image" not in s and "base64" not in s
    assert s.count("<path") == 2
    assert reportmap.WATERSHED_FILL in s


def test_no_geometry_returns_the_empty_string(basemap):
    """The sentinel every call site branches on: ui.HTML(m) if m else None."""
    assert reportmap.svg(None, None) == ""
    assert reportmap.svg({"features": []}, None) == ""


def test_the_basemap_can_be_switched_off_without_a_call(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("basemap=False must not fetch")

    monkeypatch.setattr(reportmap, "topo_png", _boom)
    assert "<image" not in reportmap.svg(WS, RC, basemap=False)


# --------------------------------------------------------------------------- #
# the fetch itself
# --------------------------------------------------------------------------- #
def test_the_fetch_never_raises_and_refuses_a_non_png(monkeypatch):
    class _Resp:
        def __init__(self, status, content):
            self.status_code, self.content = status, content

    reportmap._fetch.cache_clear()
    monkeypatch.setattr(reportmap.requests, "get",
                        lambda *a, **k: _Resp(500, b"upstream is down"))
    assert reportmap.topo_png((0, 0, 1000, 620)) is None

    reportmap._fetch.cache_clear()
    # a 200 carrying an error page, which the service does return
    monkeypatch.setattr(reportmap.requests, "get",
                        lambda *a, **k: _Resp(200, b'{"error":{"code":400}}'))
    assert reportmap.topo_png((0, 0, 1000, 620)) is None

    reportmap._fetch.cache_clear()

    def _explode(*_a, **_k):
        raise OSError("no route to host")

    monkeypatch.setattr(reportmap.requests, "get", _explode)
    assert reportmap.topo_png((0, 0, 1000, 620)) is None
    reportmap._fetch.cache_clear()


def test_the_fetch_is_memoised_so_the_pdf_reuses_the_modal_s_image(monkeypatch):
    png = _png()
    calls = []

    class _Resp:
        status_code = 200
        content = png

    def _count(*a, **k):
        calls.append(k.get("params"))
        return _Resp()

    reportmap._fetch.cache_clear()
    monkeypatch.setattr(reportmap.requests, "get", _count)
    bbox = (1.0, 2.0, 1001.0, 622.0)
    assert reportmap.topo_png(bbox) == png
    assert reportmap.topo_png(bbox) == png
    assert len(calls) == 1
    assert calls[0]["bboxSR"] == "3857" and calls[0]["imageSR"] == "3857"
    reportmap._fetch.cache_clear()


# --------------------------------------------------------------------------- #
# the PDF
# --------------------------------------------------------------------------- #
def _build(flowable):
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate

    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=letter).build([flowable])
    return out.getvalue()


def test_the_pdf_map_draws_with_and_without_a_basemap(basemap, monkeypatch):
    fl = reportmap.pdf_flowable(WS, RC, 360, 223)
    assert fl is not None and _build(fl)[:4] == b"%PDF"
    monkeypatch.setattr(reportmap, "topo_png", lambda *a, **k: None)
    fl = reportmap.pdf_flowable(WS, RC, 360, 223)
    assert fl is not None and _build(fl)[:4] == b"%PDF"


def test_the_pdf_map_is_absent_without_geometry(basemap):
    assert reportmap.pdf_flowable(None, None, 360, 223) is None
