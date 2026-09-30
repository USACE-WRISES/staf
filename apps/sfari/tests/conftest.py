import pytest


@pytest.fixture(autouse=True)
def _no_basemap_fetch(monkeypatch):
    """The report map must never reach USGS from a test.

    reportmap fetches lazily, so a fixture that gains a watershed geometry would
    otherwise start making live calls with nothing to notice it. This turns that
    into a failure instead of a slow, flaky suite. A test that wants a basemap
    stubs reportmap.topo_png itself.
    """
    from sfari import reportmap

    def _refuse(*_a, **_k):
        raise AssertionError("a test tried to fetch the USGS basemap")

    monkeypatch.setattr(reportmap.requests, "get", _refuse)


@pytest.fixture(autouse=True)
def _hr_client_defaults():
    """The app selects the site engine's interactive HR policy when imported;
    every test starts from the engine's default (patient) with no remembered
    tiles, so no result depends on test order (2026-09-30)."""
    from sfari._vendor.site_engine import hr
    hr.set_policy("patient")
    hr.clear_caches()
    yield
    hr.set_policy("patient")
    hr.clear_caches()
