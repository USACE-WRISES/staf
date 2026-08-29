"""Layer B metric families offline: riparian-buffer construction, road clip
math, dam polygon membership + normalized storage, and per-family resilience."""
from __future__ import annotations

from site_engine.metrics import common, dams, landcover, roads
from site_engine.provenance import metric_entry  # noqa: F401  (shape import)

_WS_FC = {"type": "FeatureCollection", "features": [{
    "type": "Feature", "properties": {},
    "geometry": {"type": "Polygon", "coordinates": [[
        [-83.06, 40.30], [-83.04, 40.30], [-83.04, 40.32], [-83.06, 40.32],
        [-83.06, 40.30]]]}}]}
_TREE = [{"type": "LineString",
          "coordinates": [[-83.05, 40.301], [-83.05, 40.319]]}]


def _record():
    return {"watershed": {"polygon": _WS_FC, "areaSqkm": 3.78}}


def test_riparian_buffer_is_clipped_strip():
    ws = common.watershed_geom(_WS_FC)
    rip = landcover.riparian_buffer(ws, _TREE)
    assert rip is not None
    strip = common.albers(rip)
    area_km2 = float(strip.area.sum()) / 1e6
    # ~2 km of line x 200 m width = ~0.4 km2, clipped inside the box
    assert 0.3 < area_km2 < 0.5


def test_landcover_entries(monkeypatch):
    monkeypatch.setattr(landcover, "_stats_for",
                        lambda geom: {"imperviousPct": 5.0, "cropPct": 50.0,
                                      "hayPasturePct": 5.0, "forestPct": 25.0,
                                      "shrubPct": 1.0, "grasslandPct": 2.0,
                                      "woodyWetlandPct": 1.0,
                                      "herbWetlandPct": 0.5})
    out = landcover.compute(_record(), _TREE)
    assert out["imperviousPctWatershed"]["value"] == 5.0
    assert out["imperviousPctWatershed"]["spatialSupport"] == "pointWatershed"
    assert out["cropPctRiparian"]["spatialSupport"] == "riparianBuffer"
    assert out["cropPctRiparian"]["vintage"] == "2021"


def test_roads_clip_and_density(monkeypatch):
    # One local road crossing the 40.30-40.32 box north-south: ~2.22 km inside.
    # The three TIGER layers are disjoint classes; only the local layer answers.
    road = {"type": "Feature", "properties": {"MTFCC": "S1400"},
            "geometry": {"type": "LineString",
                         "coordinates": [[-83.05, 40.29], [-83.05, 40.33]]}}
    calls: list[str] = []

    def fake(url, geom, fields, **k):
        calls.append(url)
        return [road] if "/8/" in url else []
    monkeypatch.setattr(roads, "post_query_features", fake)
    out = roads.compute(_record(), _TREE)
    assert len(calls) == 3                      # primary + secondary + local
    assert 2.0 < out["roadLengthKm"]["value"] < 2.5
    assert abs(out["roadDensity"]["value"]
               - out["roadLengthKm"]["value"] / 3.78) < 0.01


def test_dams_membership_and_storage(monkeypatch):
    feats = [
        {"type": "Feature", "properties": {"NAME": "In", "NID_STORAGE": 120.0},
         "geometry": {"type": "Point", "coordinates": [-83.05, 40.31]}},
        {"type": "Feature", "properties": {"NAME": "Out", "NID_STORAGE": 999.0},
         "geometry": {"type": "Point", "coordinates": [-83.10, 40.31]}},
        {"type": "Feature", "properties": {"NAME": "NoStorage"},
         "geometry": {"type": "Point", "coordinates": [-83.055, 40.305]}},
    ]
    monkeypatch.setattr(dams, "post_query_features",
                        lambda url, geom, fields, **k: feats)
    out = dams.compute(_record(), _TREE)
    assert out["damCount"]["value"] == 2                    # "Out" excluded
    assert out["damStorageAcreFt"]["value"] == 120.0
    assert abs(out["damStoragePerSqkm"]["value"] - 120.0 / 3.78) < 0.01


def test_service_failure_degrades_with_reason(monkeypatch):
    monkeypatch.setattr(roads, "post_query_features",
                        lambda url, geom, fields, **k: None)
    out = roads.compute(_record(), _TREE)
    assert out["roadDensity"]["value"] is None
    assert out["roadDensity"]["warnings"]
