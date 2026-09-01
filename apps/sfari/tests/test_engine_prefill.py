"""The STAF site engine bridge: availability, one ``compute_site`` with the
interactive budget and SFARI's five families, never raising, the flattened
metric values, the labels, and geometry stripping. Fully offline."""
from __future__ import annotations

import sys
import types

from sfari import engine_prefill


def _rec(**metric_values) -> dict:
    metrics = {k: {"value": v, "unit": "", "source": "", "vintage": "",
                   "spatialSupport": "", "warnings": []}
               for k, v in metric_values.items()}
    return {"status": "ok", "engineVersion": "0.2.0",
            "site": {"nhdplusId": 750012345, "snapLat": 40.0, "snapLon": -83.0},
            "watershed": {"areaSqkm": 12.5, "areaAgreement": 1.0, "nReaches": 7,
                          "polygon": {"type": "FeatureCollection", "features": []}},
            "reach": {"lengthFt": 1000.0, "geometry": {"type": "LineString",
                                                      "coordinates": [[0, 0], [1, 1]]}},
            "metrics": metrics}


def _stub_engine(monkeypatch, record=None, *, raise_exc=None):
    """Install a fake ``engine`` and ``provenance`` under the vendored package."""
    monkeypatch.setattr(engine_prefill, "site_engine_available", lambda: True)
    calls: list[dict] = []

    def compute_site(lat, lon, config=None, *, progress=None):
        calls.append({"lat": lat, "lon": lon, "config": config})
        if raise_exc is not None:
            raise raise_exc
        if progress is not None:
            progress({"stage": "walk", "reaches": 3, "hops": 1})
        return record

    eng = types.ModuleType("sfari._vendor.site_engine.engine")
    eng.compute_site = compute_site
    prov = types.ModuleType("sfari._vendor.site_engine.provenance")
    prov.INTERACTIVE_CONFIG = {"maxReaches": 60, "maxHops": 40, "includeGeometry": False}
    monkeypatch.setitem(sys.modules, "sfari._vendor.site_engine.engine", eng)
    monkeypatch.setitem(sys.modules, "sfari._vendor.site_engine.provenance", prov)
    return calls


def test_run_engine_uses_interactive_budget_and_sfari_families(monkeypatch):
    calls = _stub_engine(monkeypatch, _rec(imperviousPctWatershed=12.3))
    events = []
    rec = engine_prefill.run_engine(40.0, -83.0, progress=events.append)
    assert rec["status"] == "ok"
    cfg = calls[0]["config"]
    assert cfg["maxReaches"] == 60 and cfg["maxHops"] == 40
    assert cfg["includeGeometry"] is True
    assert cfg["metricFamilies"] == ["dams", "landcover", "roads", "runoff", "soils"]
    assert "xsection" not in cfg["metricFamilies"]      # SFARI has its own Manning tool
    assert events == [{"stage": "walk", "reaches": 3, "hops": 1}]


def test_run_engine_never_raises(monkeypatch):
    _stub_engine(monkeypatch, raise_exc=RuntimeError("boom"))
    rec = engine_prefill.run_engine(40.0, -83.0)
    assert rec["status"] == "failed" and "boom" in rec["reason"]
    assert rec["metrics"] == {}


def test_run_engine_without_the_engine(monkeypatch):
    monkeypatch.setattr(engine_prefill, "site_engine_available", lambda: False)
    rec = engine_prefill.run_engine(40.0, -83.0)
    assert rec["status"] == "unavailable"
    assert engine_prefill.engine_version() is None


def test_engine_metrics_flatten_only_ok_records():
    rec = _rec(imperviousPctWatershed=12.3, roadDensity=1.59)
    assert engine_prefill.engine_metrics(rec) == {"imperviousPctWatershed": 12.3,
                                                  "roadDensity": 1.59}
    assert engine_prefill.engine_metrics({"status": "failed", "metrics": {"x": {"value": 1}}}) == {}
    assert engine_prefill.engine_metrics(None) == {}


def test_labels_name_the_engine_and_the_reach():
    rec = _rec()
    assert engine_prefill.engine_source(rec) == "STAF site engine v0.2.0 (exact watershed)"
    assert engine_prefill.engine_label("0.2.0") == "STAF site engine v0.2.0"
    note = engine_prefill.engine_note(rec)
    assert "12.5 km2" in note and "area agreement 1.0" in note
    hr = {"anchorKind": "hrSurrogate", "scoredReach": {"comid": 5214461},
          "routing": {"routedDistanceFt": 1240.0, "daRatio": 1.8, "declined": False}}
    label = engine_prefill.anchor_label(hr)
    assert label.startswith("nearest covered reach, COMID 5214461")
    assert engine_prefill.anchor_label({"anchorKind": "v2Direct"}) == ""
    assert engine_prefill.anchor_label(None) == ""
    for text in (engine_prefill.engine_source(rec), note, label):
        assert "—" not in text and ";" not in text


def test_strip_geometry_keeps_everything_else():
    rec = _rec(imperviousPctWatershed=12.3)
    out = engine_prefill.strip_geometry(rec)
    assert out["watershed"]["polygon"] is None and out["reach"]["geometry"] is None
    assert out["watershed"]["areaSqkm"] == 12.5 and out["reach"]["lengthFt"] == 1000.0
    assert out["metrics"] == rec["metrics"]
    assert rec["watershed"]["polygon"] is not None          # the input is untouched
    assert engine_prefill.strip_geometry(None) is None


def test_engine_url_is_the_staf_site():
    assert engine_prefill.ENGINE_URL.startswith("https://usace-wrises.github.io/staf/")


def test_evidence_result_roundtrips_new_fields():
    from sfari.models import EvidenceResult
    e = EvidenceResult("m", origin="engine", engine_version="0.2.0",
                       anchor_label="nearest covered reach, COMID 1",
                       fallback_reason="", upgrade_pending=True)
    back = EvidenceResult.from_dict(e.to_dict())
    assert back == e
    legacy = EvidenceResult.from_dict({"metric_id": "m", "value": 1})
    assert legacy.origin == "pull" and legacy.engine_version is None
    assert legacy.anchor_label == "" and legacy.fallback_reason == ""
    assert legacy.upgrade_pending is False
