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
from types import SimpleNamespace

import pytest

from deep import reportmap


@pytest.fixture(autouse=True)
def clean_fetch_cache_and_skip_retry_wait(monkeypatch):
    reportmap._fetch.cache_clear()
    monkeypatch.setattr(reportmap.time, "sleep", lambda _: None)
    yield
    reportmap._fetch.cache_clear()

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
    assert s.startswith('<div class="reportmap-fallback"')
    assert 'viewBox="0 0 290 180"' in s
    assert s.index("</svg>") < s.index(reportmap.UNAVAILABLE_NOTE)
    assert s.count(reportmap.UNAVAILABLE_NOTE) == 1
    assert s[s.index("<svg"):s.index("</svg>") + 6] == reportmap.svg(WS, RC, basemap=False)


def test_no_geometry_returns_the_empty_string(basemap):
    """The sentinel every call site branches on: ui.HTML(m) if m else None."""
    assert reportmap.svg(None, None) == ""
    assert reportmap.svg({"features": []}, None) == ""


def test_the_basemap_can_be_switched_off_without_a_call(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("basemap=False must not fetch")

    monkeypatch.setattr(reportmap, "topo_png", _boom)
    result = reportmap.svg(WS, RC, basemap=False)
    assert "<image" not in result and result.startswith("<svg")
    assert reportmap.UNAVAILABLE_NOTE not in result


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


@pytest.mark.parametrize("status", [408, 429, 500, 503, 599])
def test_transient_status_retries_once_and_then_caches_success(monkeypatch, status):
    png = _png()
    answers = iter([SimpleNamespace(status_code=status, content=b"temporarily unavailable"),
                    SimpleNamespace(status_code=200, content=png)])
    calls, waits = [], []
    def get(*args, **kwargs):
        calls.append(kwargs)
        return next(answers)
    monkeypatch.setattr(reportmap.requests, "get", get)
    monkeypatch.setattr(reportmap.time, "sleep", waits.append)
    bbox = (1, 2, 1001, 622)
    assert reportmap.topo_png(bbox) == png
    assert reportmap.topo_png(bbox) == png
    assert len(calls) == 2 and waits == [1.0]
    assert [call["timeout"] for call in calls] == [12.0, 12.0]


@pytest.mark.parametrize("exception_type", [reportmap.requests.exceptions.Timeout,
                                             reportmap.requests.exceptions.ConnectionError])
def test_transient_transport_error_recovers_on_second_attempt(monkeypatch, exception_type):
    png = _png()
    answers = iter([exception_type("temporary transport failure"),
                    SimpleNamespace(status_code=200, content=png)])
    def get(*args, **kwargs):
        result = next(answers)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(reportmap.requests, "get", get)
    assert reportmap.topo_png((1, 2, 1001, 622)) == png


def test_failed_fetch_is_not_cached_and_later_open_can_recover(monkeypatch):
    png = _png()
    answers = iter([SimpleNamespace(status_code=503, content=b"busy"),
                    SimpleNamespace(status_code=503, content=b"busy"),
                    SimpleNamespace(status_code=200, content=png)])
    calls = []
    def get(*args, **kwargs):
        calls.append(1)
        return next(answers)
    monkeypatch.setattr(reportmap.requests, "get", get)
    bbox = (1, 2, 1001, 622)
    assert reportmap.topo_png(bbox) is None
    assert len(calls) == 2 and reportmap._fetch.cache_info().currsize == 0
    assert reportmap.topo_png(bbox) == png
    assert reportmap.topo_png(bbox) == png
    assert len(calls) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_non_transient_status_does_not_retry(monkeypatch, status):
    calls = []
    monkeypatch.setattr(reportmap.requests, "get", lambda *a, **k: calls.append(1) or
                        SimpleNamespace(status_code=status, content=b"refused"))
    assert reportmap.topo_png((1, 2, 1001, 622)) is None
    assert calls == [1]
    assert reportmap._fetch.cache_info().currsize == 0


@pytest.mark.parametrize("kind", ["json_error", "magic_only", "truncated_png"])
def test_success_cache_requires_a_fully_decoded_png(monkeypatch, kind):
    png = _png()
    content = {"json_error": b'{"error":{"code":500}}', "magic_only": b"\x89PNGinvalid",
               "truncated_png": png[:-20]}[kind]
    monkeypatch.setattr(reportmap.requests, "get", lambda *a, **k:
                        SimpleNamespace(status_code=200, content=content))
    assert reportmap.topo_png((1, 2, 1001, 622)) is None
    assert reportmap._fetch.cache_info().currsize == 0


def test_failure_logging_has_endpoint_status_elapsed_and_bounded_error(monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise ValueError("malformed response " + "x" * 600 + "\nsecond line")
    monkeypatch.setattr(reportmap.requests, "get", fail)
    assert reportmap.topo_png((1, 2, 1001, 622)) is None
    record = caplog.records[-1]
    message = record.getMessage()
    assert reportmap.EXPORT_URL in message and "status=transport" in message
    assert "elapsed=" in message and "attempt=1/2" in message
    assert len(message.split("error=", 1)[1]) <= 200
    assert "\n" not in message


def test_success_cache_is_bounded(monkeypatch):
    png = _png()
    monkeypatch.setattr(reportmap.requests, "get", lambda *a, **k:
                        SimpleNamespace(status_code=200, content=png))
    for index in range(34):
        assert reportmap.topo_png((index, 0, index + 1000, 620)) == png
    assert reportmap._fetch.cache_info().currsize == 32


def test_popup_and_pdf_share_the_same_validated_image(monkeypatch):
    png, calls = _png(), []
    monkeypatch.setattr(reportmap.requests, "get", lambda *a, **k: calls.append(k) or
                        SimpleNamespace(status_code=200, content=png))
    result = reportmap.svg(WS, RC)
    assert result.startswith("<svg") and reportmap.UNAVAILABLE_NOTE not in result
    flowable = reportmap.pdf_flowable(WS, RC, 360, 360 * 180 / 290)
    assert _build(flowable).startswith(b"%PDF")
    assert len(calls) == 1


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


def test_pdf_fallback_reserves_caption_space_without_stretching_map(no_basemap):
    class Canvas:
        def __init__(self):
            self.paths, self.rectangles, self.notes = [], [], []

        def beginPath(self):
            points = []
            self.paths.append(points)
            return SimpleNamespace(moveTo=lambda x, y: points.append((x, y)),
                                   lineTo=lambda x, y: points.append((x, y)), close=lambda: None)

        def rect(self, *args, **kwargs):
            self.rectangles.append(args)

        def drawString(self, *args):
            self.notes.append(args)

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    plain = reportmap.pdf_flowable(WS, RC, 360, 223, basemap=False)
    fallback = reportmap.pdf_flowable(WS, RC, 360, 223)
    assert plain.height == 223 and fallback.height == 235
    plain.canv, fallback.canv = Canvas(), Canvas()
    plain.draw()
    fallback.draw()
    assert plain.canv.notes == []
    assert fallback.canv.notes == [(0, 2, reportmap.UNAVAILABLE_NOTE)]
    assert plain.canv.rectangles == [(0, 0, 360, 223)]
    assert fallback.canv.rectangles == [(0, 12, 360, 223)]
    for original, shifted in zip(plain.canv.paths, fallback.canv.paths):
        assert len(original) == len(shifted)
        for (x0, y0), (x1, y1) in zip(original, shifted):
            assert x1 == pytest.approx(x0)
            assert y1 == pytest.approx(y0 + 12)
