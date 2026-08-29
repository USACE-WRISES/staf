"""Engine prefill: availability gate, the exact-watershed evidence mapping,
provenance labeling, and the pull() override. Fully offline."""
from __future__ import annotations

import asyncio

from sfari import engine_prefill, evidence


def _rec(**metric_values) -> dict:
    metrics = {k: {"value": v, "unit": "", "source": "", "vintage": "",
                   "spatialSupport": "", "warnings": []}
               for k, v in metric_values.items()}
    return {"status": "ok", "engineVersion": "0.1.0",
            "watershed": {"areaAgreement": 1.0}, "metrics": metrics}


def _stub_engine(monkeypatch, record):
    monkeypatch.setattr(engine_prefill, "site_engine_available", lambda: True)

    class _FakePkg:
        @staticmethod
        def compute_site(lat, lon, config=None):
            return record
    import sys
    import types
    vendor = types.ModuleType("sfari._vendor.site_engine")
    vendor.compute_site = _FakePkg.compute_site
    vendor.ENGINE_VERSION = "0.1.0"
    monkeypatch.setitem(sys.modules, "sfari._vendor.site_engine", vendor)


def test_mapping_and_provenance(monkeypatch):
    _stub_engine(monkeypatch, _rec(
        imperviousPctWatershed=12.3, roadDensity=1.59, damCount=2,
        damStorageAcreFt=150.0, woodyWetlandPctWatershed=1.5,
        herbWetlandPctWatershed=0.5, forestPctRiparian=42.0,
        shrubPctRiparian=3.0, grasslandPctRiparian=5.0,
        woodyWetlandPctRiparian=2.0, herbWetlandPctRiparian=1.0))
    out = engine_prefill.pull_engine_evidence({"lat": 40.0, "lon": -83.0})
    imp = out["catchment-hydrology-impervious-surface-area"]
    assert imp["origin"] == "engine"
    assert imp["engine_version"] == "0.1.0"
    assert imp["value"] == 12.3
    assert imp["suggested_likert"] is not None          # break table exists
    assert "exact watershed" in imp["source"]
    assert out["surface-water-storage-wetland-coverage"]["value"] == 2.0
    veg = out["carbon-processing-riparian-corridor-width-and-quality"]
    assert veg["value"] == 53.0                          # forest+shrub+grass+wet
    assert out["nutrient-cycling-vegetated-riparian-corridor-width"]["value"] == 53.0
    assert out["catchment-hydrology-impoundments"]["value"] == 2


def test_missing_values_do_not_map(monkeypatch):
    _stub_engine(monkeypatch, _rec(imperviousPctWatershed=5.0))
    out = engine_prefill.pull_engine_evidence({"lat": 40.0, "lon": -83.0})
    assert "catchment-hydrology-impervious-surface-area" in out
    assert "catchment-hydrology-road-density" not in out
    assert "light-thermal-regime-riparian-canopy-cover" not in out


def test_engine_failure_means_no_upgrade(monkeypatch):
    _stub_engine(monkeypatch, {"status": "failed", "reason": "no stream"})
    assert engine_prefill.pull_engine_evidence({"lat": 40.0, "lon": -83.0}) == {}

    monkeypatch.setattr(engine_prefill, "site_engine_available", lambda: False)
    assert engine_prefill.pull_engine_evidence({"lat": 40.0, "lon": -83.0}) == {}


def test_pull_overrides_mapped_entries(monkeypatch):
    # Stub every datasource the standard pull touches, then let the engine
    # branch replace the impervious proxy with the exact-watershed entry.
    monkeypatch.setattr(evidence.streamcat, "metrics_by_comid",
                        lambda comid, names, aoi=None: {"pctimp2019ws": 7.0})
    for name in ("barriers_near",):
        monkeypatch.setattr(evidence.nid_barriers, name, lambda *a, **k: [])
    monkeypatch.setattr(evidence.wqp, "median_value", lambda *a, **k: None)
    monkeypatch.setattr(evidence.nwis, "flow_stats", lambda *a, **k: None)
    monkeypatch.setattr(evidence.nwi, "wetlands_near", lambda *a, **k: None)
    monkeypatch.setattr(evidence.tiger_roads, "roads_near", lambda *a, **k: None)
    monkeypatch.setattr(evidence.engine_prefill if hasattr(evidence, "engine_prefill")
                        else engine_prefill, "site_engine_available", lambda: True)
    monkeypatch.setattr(engine_prefill, "site_engine_available", lambda: True)
    monkeypatch.setattr(engine_prefill, "pull_engine_evidence",
                        lambda ci: {"catchment-hydrology-impervious-surface-area": {
                            "metric_id": "catchment-hydrology-impervious-surface-area",
                            "value": 12.3, "origin": "engine",
                            "engine_version": "0.1.0", "status": "ok",
                            "value_text": "", "field_value_text": "",
                            "suggested_likert": None, "confidence": "M",
                            "source": "engine", "source_url": "", "note": ""}})
    out = asyncio.run(evidence.pull({"lat": 40.0, "lon": -83.0, "comid": 1}))
    imp = out["catchment-hydrology-impervious-surface-area"]
    assert imp["origin"] == "engine" and imp["value"] == 12.3
    # unmapped entries keep their standard-pull origin
    assert out["catchment-hydrology-road-density"].get("origin", "pull") == "pull"


def test_evidence_result_roundtrips_new_fields():
    from sfari.models import EvidenceResult
    e = EvidenceResult("m", origin="engine", engine_version="0.1.0")
    back = EvidenceResult.from_dict(e.to_dict())
    assert back.origin == "engine" and back.engine_version == "0.1.0"
    # legacy dicts (no new keys) default safely
    legacy = EvidenceResult.from_dict({"metric_id": "m", "value": 1})
    assert legacy.origin == "pull" and legacy.engine_version is None
