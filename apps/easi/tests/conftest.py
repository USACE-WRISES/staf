import os

# owner decision D20: the suite scores with the bundled method and never reaches the
# library release at import (tests/test_adopted_method.py drives the loader itself)
os.environ.setdefault("EASI_ADOPTED_METHOD", "0")
# app.py defaults the STAF data source to "auto" (its release, fetched on demand): the suite asks the
# services, which the tests stub, unless a test chooses the bundle itself
os.environ.setdefault("STAF_DATA_SOURCE", "service")

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


@pytest.fixture(autouse=True)
def _hr_client_defaults():
    """The app selects the site engine's interactive HR policy when imported;
    every test starts from the engine's default (patient) with no remembered
    tiles, so no result depends on test order (2026-09-30)."""
    from easi._vendor.site_engine import hr
    hr.set_policy("patient")
    hr.clear_caches()
    yield
    hr.set_policy("patient")
    hr.clear_caches()
