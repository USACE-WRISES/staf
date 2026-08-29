"""compute_site determinism: with the network mocked, two runs of the whole
pipeline (Layer A + Layer B) produce byte-identical JSON."""
from __future__ import annotations

import json

from site_engine import engine, hr
from site_engine.metrics import dams, landcover, roads


def _rec(nid, hs, geom_lat0):
    return {"nhdplusid": nid, "hydroseq": hs, "dnhydroseq": hs - 1,
            "uphydroseq": hs + 1 if nid == 1 else None,
            "totdasqkm": 1.9, "lengthkm": 0.6, "gnis_name": "Demo Run",
            "reachcode": "05060001001737", "slope": 0.012, "fcode": 46003,
            "ftype": 460, "stream_order": 1, "vpuid": "0506", "qama": 0.8,
            "geometry": {"type": "LineString",
                         "coordinates": [[-83.056, geom_lat0],
                                         [-83.056, geom_lat0 + 0.006]]}}


def _wire(monkeypatch):
    anchor = _rec(1, 11, 40.310)
    upstream = _rec(2, 12, 40.316)
    monkeypatch.setattr(hr, "flowlines_in_bbox",
                        lambda *a, **k: [anchor, upstream])
    monkeypatch.setattr(hr, "feature_by_hydroseq",
                        lambda hs, **k: upstream if hs == 12 else None)
    monkeypatch.setattr(hr, "parents_by_dnhydroseq",
                        lambda hs, **k: [upstream] if 11 in hs else [])
    monkeypatch.setattr(hr, "catchments_by_ids", lambda ids, **k: [
        {"nhdplusid": 1, "areasqkm": None,
         "geometry": {"type": "Polygon", "coordinates": [[
             [-83.06, 40.305], [-83.05, 40.305], [-83.05, 40.315],
             [-83.06, 40.315], [-83.06, 40.305]]]}},
        {"nhdplusid": 2, "areasqkm": None,
         "geometry": {"type": "Polygon", "coordinates": [[
             [-83.06, 40.315], [-83.05, 40.315], [-83.05, 40.323],
             [-83.06, 40.323], [-83.06, 40.315]]]}},
    ])
    monkeypatch.setattr(landcover, "_stats_for",
                        lambda geom: {"imperviousPct": 3.1, "cropPct": 40.0,
                                      "hayPasturePct": 10.0, "forestPct": 20.0,
                                      "shrubPct": 1.0, "grasslandPct": 2.0,
                                      "woodyWetlandPct": 0.5,
                                      "herbWetlandPct": 0.2})
    # Patch the names the metric modules actually bound (a common-module patch
    # would silently let them reach the live network).
    monkeypatch.setattr(roads, "post_query_features",
                        lambda url, geom, fields, **k: [])
    monkeypatch.setattr(dams, "post_query_features",
                        lambda url, geom, fields, **k: [])
    from site_engine.metrics import soils
    monkeypatch.setattr(soils, "_query_kwfact",
                        lambda wkt: [(0.37, 85.0), (0.43, 90.0)])
    # The cross-section family pulls live 3DEP rasters; replace it in the
    # registry (the registry holds the original function reference).
    import site_engine.metrics as metrics_pkg
    from site_engine.provenance import metric_entry
    monkeypatch.setitem(
        metrics_pkg._REGISTRY, "xsection",
        lambda record, tree_geoms: {"entrenchmentRatio": metric_entry(
            1.4, "ratio", "3DEP (stubbed)", "test", "reach")})


def test_two_runs_are_byte_identical(monkeypatch):
    _wire(monkeypatch)
    a = engine.compute_site(40.3112, -83.0561)
    b = engine.compute_site(40.3112, -83.0561)
    assert a["status"] == "ok"
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["engineId"] == "site-engine"
    assert a["site"]["nhdplusId"] == 1
    assert a["site"]["ecoregionL3"]["code"] == "55"      # real bundled data
    assert a["watershed"]["status"] == "ok"
    assert a["reach"]["lengthFt"] is not None
    assert "imperviousPctWatershed" in a["metrics"]
    assert a["metrics"]["roadLengthKm"]["value"] == 0.0
    assert a["metrics"]["damCount"]["value"] == 0
    assert a["metrics"]["meanAnnualFlowCfs"]["value"] == 0.8
    assert a["metrics"]["runoffDepthMm"]["value"] > 0
    assert a["metrics"]["entrenchmentRatio"]["value"] == 1.4
    assert a["exclusions"][0]["code"] == "epa-modeled-indices"


def test_no_stream_is_failed_with_reason(monkeypatch):
    monkeypatch.setattr(hr, "flowlines_in_bbox", lambda *a, **k: [])
    out = engine.compute_site(40.0, -83.0)
    assert out["status"] == "failed"
    assert "no HR flowline" in out["reason"]


def test_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("wired wrong")
    monkeypatch.setattr(hr, "flowlines_in_bbox", boom)
    out = engine.compute_site(40.0, -83.0)
    assert out["status"] == "failed"
    assert "engine error" in out["reason"]
