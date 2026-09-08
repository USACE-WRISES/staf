"""The STAF site engine bridge: availability, one ``compute_site`` with the
interactive budget, DEEP's six families (no cross-sections: DEEP runs 3DEP on
the engine reach itself), the typed assessment reach length (2026-09-07),
never raising, the flattened metric values, the labels, and geometry
stripping. Fully offline. A port of SFARI's test of the same bridge."""
from __future__ import annotations

import sys
import types

from deep import engine_prefill


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

    eng = types.ModuleType("deep._vendor.site_engine.engine")
    eng.compute_site = compute_site
    prov = types.ModuleType("deep._vendor.site_engine.provenance")
    prov.INTERACTIVE_CONFIG = {"maxReaches": 60, "maxHops": 40, "includeGeometry": False}
    monkeypatch.setitem(sys.modules, "deep._vendor.site_engine.engine", eng)
    monkeypatch.setitem(sys.modules, "deep._vendor.site_engine.provenance", prov)
    return calls


def test_run_engine_uses_interactive_budget_and_deep_families(monkeypatch):
    calls = _stub_engine(monkeypatch, _rec(imperviousPctWatershed=12.3))
    events = []
    rec = engine_prefill.run_engine(40.0, -83.0, reach_length_ft=500.0, progress=events.append)
    assert rec["status"] == "ok"
    cfg = calls[0]["config"]
    assert cfg["maxReaches"] == 60 and cfg["maxHops"] == 40
    assert cfg["includeGeometry"] is True
    assert "landcoverBaseline" not in cfg           # only SFARI scores land-use change
    assert cfg["metricFamilies"] == ["baseflow", "dams", "landcover", "roads", "runoff", "soils"]
    # the typed assessment reach reaches the engine (2026-09-07; it was always 1,000 ft)
    assert cfg["reachLengthFt"] == 500.0
    assert events == [{"stage": "walk", "reaches": 3, "hops": 1}]


def test_run_engine_leaves_the_engine_reach_default_alone(monkeypatch):
    calls = _stub_engine(monkeypatch, _rec())
    engine_prefill.run_engine(40.0, -83.0)
    engine_prefill.run_engine(40.0, -83.0, reach_length_ft=None)
    engine_prefill.run_engine(40.0, -83.0, reach_length_ft=0)
    engine_prefill.run_engine(40.0, -83.0, reach_length_ft="abc")
    for call in calls:
        assert "reachLengthFt" not in call["config"]       # the engine's own 1,000 ft


def test_run_engine_never_raises(monkeypatch):
    _stub_engine(monkeypatch, raise_exc=RuntimeError("boom"))
    rec = engine_prefill.run_engine(40.0, -83.0)
    assert rec["status"] == "failed" and "boom" in rec["reason"]
    assert rec["metrics"] == {}


def test_run_engine_without_the_engine(monkeypatch):
    monkeypatch.setattr(engine_prefill, "site_engine_available", lambda: False)
    rec = engine_prefill.run_engine(40.0, -83.0)
    assert rec["status"] == "unavailable"


def test_engine_metrics_flatten_only_ok_records():
    rec = _rec(imperviousPctWatershed=12.3, roadDensity=1.59)
    assert engine_prefill.engine_metrics(rec) == {"imperviousPctWatershed": 12.3,
                                                  "roadDensity": 1.59}
    assert engine_prefill.engine_metrics({"status": "failed", "metrics": {"x": {"value": 1}}}) == {}
    assert engine_prefill.engine_metrics(None) == {}


def test_labels_name_the_engine():
    rec = _rec()
    assert engine_prefill.engine_label("0.2.0") == "STAF site engine v0.2.0"
    assert "STAF site engine" in engine_prefill.engine_source(rec)
    note = engine_prefill.engine_note(rec)
    assert "12.5 km2" in note
    assert not hasattr(engine_prefill, "anchor_label")     # the reach labels live in comid_anchor
    for text in (engine_prefill.engine_source(rec), note):
        assert "—" not in text and ";" not in text


def test_strip_geometry_keeps_everything_else():
    rec = _rec(imperviousPctWatershed=12.3)
    out = engine_prefill.strip_geometry(rec)
    assert out["watershed"]["polygon"] is None and out["reach"]["geometry"] is None
    assert out["watershed"]["areaSqkm"] == 12.5 and out["reach"]["lengthFt"] == 1000.0
    assert out["metrics"] == rec["metrics"]
    assert rec["watershed"]["polygon"] is not None          # the input is untouched
    assert engine_prefill.strip_geometry(None) is None
