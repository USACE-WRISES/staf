"""Desktop auto-compute framework (Phase 3) — registry, error handling, merge shape.

Tested with fake adapters (no network / no geospatial stack) so it runs under the
stdlib-only interpreter. The real StreamCat/3DEP adapters are smoke-tested live.
"""
from deep import measure
from deep.metrics import computed
from deep.metrics.base import AnalysisContext
from deep.metrics.computed import ComputedValue


def test_context_from_inputs():
    ctx = AnalysisContext.from_inputs(
        {"lat": 44.0, "lon": -123.0, "comid": 123, "drainage_area_sqkm": 42.0})
    assert ctx.lat == 44.0 and ctx.comid == 123 and ctx.drainage_area_sqkm == 42.0
    assert AnalysisContext.from_inputs(None).comid is None


def test_registry_covers_expected_metrics():
    ids = computed.computable_ids()
    for mid in ("catchment-hydrology-impervious-cover",
                "catchment-hydrology-percent-impervious-cover",
                "catchment-hydrology-effective-impervious-cover",
                "catchment-hydrology-anthropogenic-land-cover",
                "floodplain-connectivity-entrenchment-ratio-er",
                "channel-and-floodplain-dynamics-bank-height-ratio-bhr",
                "channel-evolution-width-depth-ratio",
                # the regional bundles' landscape metrics
                "spring-pctimp2019ws", "spring-pctcrop2019ws",
                "spring-pctwdwet2019ws", "spring-pcthbwet2019ws",
                "spring-rddensws", "spring-damdensws",
                "spring-bfiws", "spring-rdcrsws"):
        assert mid in ids


def test_compute_for_unknown_metric_returns_empty():
    ctx = AnalysisContext.from_inputs({"comid": 1})
    assert computed.compute_for(["not-a-real-metric"], ctx) == {}


def test_compute_for_runs_registered_adapter(monkeypatch):
    ctx = AnalysisContext.from_inputs({"comid": 1})
    monkeypatch.setitem(computed._ADAPTERS, "fake-metric",
                        lambda c: ComputedValue(12.5, "Test source", "H"))
    monkeypatch.setitem(computed._ADAPTERS, "fake-none", lambda c: None)
    out = computed.compute_for(["fake-metric", "fake-none"], ctx)
    assert "fake-metric" in out and out["fake-metric"].value == 12.5
    assert "fake-none" not in out            # None-returning adapter is excluded


def test_compute_for_swallows_adapter_errors(monkeypatch):
    ctx = AnalysisContext.from_inputs({"comid": 1})

    def boom(c):
        raise RuntimeError("network down")

    monkeypatch.setitem(computed._ADAPTERS, "fake-boom", boom)
    assert computed.compute_for(["fake-boom"], ctx) == {}   # never raises


def test_measure_compute_metrics_only_merge_shape(monkeypatch):
    monkeypatch.setattr(computed, "compute_for",
                        lambda ids, ctx: {"m1": ComputedValue(7.0, "StreamCat", "H")})
    out = measure.compute_metrics_only({"comid": 1}, ["m1", "m2"])
    assert out == {"m1": {"value": 7.0, "na": False, "note": "",
                          "origin": "desktop", "source": "StreamCat",
                          "engine": False, "basis": ""}}
