import pytest


@pytest.fixture
def legacy_criteria(monkeypatch):
    """Keep the old fixed-band and NRSA tier tests as explicit legacy regressions."""
    from easi import config
    monkeypatch.setenv("EASI_CRITERIA_SET", "legacy")
    config.reset_caches()
    yield
    config.reset_caches()


@pytest.fixture
def criteria_set(request, monkeypatch):
    """Exercise either catalog without leaking cached projections across tests."""
    from easi import config

    selected = getattr(request, "param", "regional")
    monkeypatch.setenv("EASI_CRITERIA_SET", selected)
    config.reset_caches()
    yield selected
    config.reset_caches()


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
