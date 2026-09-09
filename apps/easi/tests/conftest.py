import pytest


@pytest.fixture(autouse=True)
def _clear_fabric_feature_memo():
    """The fabric feature memo is per process (2026-09-04): a test's answer
    must never satisfy the next test's ask."""
    from easi.datasources import fabric
    fabric.clear_feature_memo()
    yield
    fabric.clear_feature_memo()


@pytest.fixture(autouse=True)
def _no_basemap_fetch(monkeypatch):
    """The report map must never reach USGS from a test.

    reportmap fetches lazily, so a fixture that gains a watershed geometry would
    otherwise start making live calls with nothing to notice it. This turns that
    into a failure instead of a slow, flaky suite. A test that wants a basemap
    stubs reportmap.topo_png itself.
    """
    from easi import reportmap

    def _refuse(*_a, **_k):
        raise AssertionError("a test tried to fetch the USGS basemap")

    monkeypatch.setattr(reportmap.requests, "get", _refuse)
