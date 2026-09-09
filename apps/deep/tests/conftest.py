import pytest


@pytest.fixture(autouse=True)
def _no_basemap_fetch(monkeypatch):
    """The report map must never reach USGS from a test.

    reportmap fetches lazily, so a fixture that gains a watershed geometry would
    otherwise start making live calls with nothing to notice it. This turns that
    into a failure instead of a slow, flaky suite. A test that wants a basemap
    stubs reportmap.topo_png itself.
    """
    from deep import reportmap

    def _refuse(*_a, **_k):
        raise AssertionError("a test tried to fetch the USGS basemap")

    monkeypatch.setattr(reportmap.requests, "get", _refuse)
