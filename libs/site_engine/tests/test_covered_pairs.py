"""Every engine key the covered-reach study compares is a key the stubbed
metric families emit, and every StreamCat side derives from the requested
base names (the study can never silently compare a missing column)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from site_engine.metrics import dams, landcover, roads, runoff, soils

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "covered_reach_comparison.py"
_WS_FC = {"type": "FeatureCollection", "features": [{
    "type": "Feature", "properties": {},
    "geometry": {"type": "Polygon", "coordinates": [[
        [-83.06, 40.30], [-83.04, 40.30], [-83.04, 40.32], [-83.06, 40.32],
        [-83.06, 40.30]]]}}]}
_TREE = [{"type": "LineString",
          "coordinates": [[-83.05, 40.301], [-83.05, 40.319]]}]


def _script():
    spec = importlib.util.spec_from_file_location("covered_reach_comparison", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pairs_are_emitted_by_the_families(monkeypatch):
    mod = _script()
    record = {"watershed": {"polygon": _WS_FC, "areaSqkm": 3.78},
              "site": {"eromQamaCfs": 1.0, "drainageAreaSqkm": 3.78},
              "input": {"config": {}}}
    monkeypatch.setattr(landcover, "_stats_for",
                        lambda geom: {"imperviousPct": 1.0, "cropPct": 1.0,
                                      "hayPasturePct": 1.0, "forestPct": 1.0,
                                      "shrubPct": 1.0, "grasslandPct": 1.0,
                                      "woodyWetlandPct": 1.0,
                                      "herbWetlandPct": 1.0})
    monkeypatch.setattr(roads, "post_query_features",
                        lambda url, geom, fields, **k: [])
    monkeypatch.setattr(dams, "post_query_features",
                        lambda url, geom, fields, **k: [])
    monkeypatch.setattr(soils, "_query_areas", lambda wkt: {"1": 10.0})
    monkeypatch.setattr(soils, "_query_kwfact_by_mukey",
                        lambda keys: {"1": [(0.3, 100.0)]})
    emitted: set[str] = set()
    for fam in (landcover, roads, dams, soils, runoff):
        emitted |= set(fam.compute(record, _TREE))
    missing = set(mod._PAIRS) - emitted
    assert not missing, f"study pairs without an engine key: {sorted(missing)}"


def test_streamcat_sides_derive_from_the_requested_names():
    mod = _script()
    suffixes = ("ws", "wsrp100")
    for cols, _t in mod._PAIRS.values():
        for col in cols:
            assert any(col == f"{name}{s}" for name in mod._SC_NAMES
                       for s in suffixes), col


def test_sum_requires_every_member():
    mod = _script()
    assert mod.sc_value({"a": 1.0, "b": 2.0}, ("a", "b")) == 3.0
    assert mod.sc_value({"a": 1.0}, ("a", "b")) is None
    assert mod.sc_column(("a", "b")) == "a+b"
