"""Layer B metric families offline: riparian-buffer construction, road clip
math, dam polygon membership + normalized storage, and per-family resilience."""
from __future__ import annotations

from site_engine.metrics import baseflow, common, dams, landcover, roads
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
        {"type": "Feature", "properties": {"NAME": "In", "NID_STORAGE": 120.0,
                                           "NORMAL_STORAGE": 100.0},
         "geometry": {"type": "Point", "coordinates": [-83.05, 40.31]}},
        {"type": "Feature", "properties": {"NAME": "Out", "NID_STORAGE": 999.0,
                                           "NORMAL_STORAGE": 999.0},
         "geometry": {"type": "Point", "coordinates": [-83.10, 40.31]}},
        {"type": "Feature", "properties": {"NAME": "NoStorage"},
         "geometry": {"type": "Point", "coordinates": [-83.055, 40.305]}},
    ]
    fields: list[str] = []

    def fake(url, geom, out_fields, **k):
        fields.append(out_fields)
        return feats
    monkeypatch.setattr(dams, "post_query_features", fake)
    out = dams.compute(_record(), _TREE)
    assert "NORMAL_STORAGE" in fields[0]
    assert out["damCount"]["value"] == 2                    # "Out" excluded
    # Normal storage is the StreamCat DamNrmStor analog; NID storage rides
    # beside it under its own key.
    assert out["damStorageAcreFt"]["value"] == 100.0
    assert abs(out["damStoragePerSqkm"]["value"] - 100.0 / 3.78) < 0.01
    assert out["damNidStorageAcreFt"]["value"] == 120.0
    assert abs(out["damDensityPerSqkm"]["value"] - 2 / 3.78) < 0.001
    assert any("without normal storage" in w
               for w in out["damStorageAcreFt"]["warnings"])


def test_landcover_baseline_2001(monkeypatch):
    stats = {"imperviousPct": 5.0, "cropPct": 50.0, "hayPasturePct": 5.0,
             "forestPct": 25.0, "shrubPct": 1.0, "grasslandPct": 2.0,
             "woodyWetlandPct": 1.0, "herbWetlandPct": 0.5}
    monkeypatch.setattr(landcover, "_stats_for", lambda geom: dict(stats))
    monkeypatch.setattr(landcover, "_impervious_for",
                        lambda geom, year: 2.5 if year == 2001 else None)
    rec = _record()
    rec["input"] = {"config": {"landcoverBaseline": True}}
    out = landcover.compute(rec, _TREE)
    assert out["imperviousPct2001Watershed"]["value"] == 2.5
    assert out["imperviousPct2001Watershed"]["vintage"] == "2001"
    assert out["imperviousPct2001Riparian"]["spatialSupport"] == "riparianBuffer"
    # The default path emits no baseline keys at all.
    out2 = landcover.compute(_record(), _TREE)
    assert not any(k.startswith("imperviousPct2001") for k in out2)


def test_service_failure_degrades_with_reason(monkeypatch):
    monkeypatch.setattr(roads, "post_query_features",
                        lambda url, geom, fields, **k: None)
    out = roads.compute(_record(), _TREE)
    assert out["roadDensity"]["value"] is None
    assert out["roadDensity"]["warnings"]


def test_roads_crossings_count_points_not_shared_segments(monkeypatch):
    # An east-west road crossing the north-south stream once, a road parallel
    # to it, and a road lying on the channel (a shared segment): one crossing.
    def road(coords):
        return {"type": "Feature", "properties": {"MTFCC": "S1400"},
                "geometry": {"type": "LineString", "coordinates": coords}}
    feats = [road([[-83.07, 40.31], [-83.03, 40.31]]),
             road([[-83.045, 40.29], [-83.045, 40.33]]),
             road([[-83.05, 40.305], [-83.05, 40.31]])]
    monkeypatch.setattr(roads, "post_query_features",
                        lambda url, geom, fields, **k: feats if "/8/" in url else [])
    out = roads.compute(_record(), _TREE)
    assert out["roadCrossings"]["value"] == 1
    assert abs(out["roadCrossingDensity"]["value"] - 1 / 3.78) < 0.001
    assert out["roadCrossingDensity"]["unit"] == "crossings/km2"
    assert "NHDPlus HR" in out["roadCrossings"]["source"]


def test_roads_crossings_zero_without_streams_or_roads(monkeypatch):
    monkeypatch.setattr(roads, "post_query_features",
                        lambda url, geom, fields, **k: [])
    out = roads.compute(_record(), _TREE)
    assert out["roadCrossings"]["value"] == 0 and out["roadCrossingDensity"]["value"] == 0.0
    road = {"type": "Feature", "properties": {},
            "geometry": {"type": "LineString",
                         "coordinates": [[-83.07, 40.31], [-83.03, 40.31]]}}
    monkeypatch.setattr(roads, "post_query_features",
                        lambda url, geom, fields, **k: [road] if "/8/" in url else [])
    assert roads.compute(_record(), [])["roadCrossings"]["value"] == 0


def test_roads_unavailable_carries_both_densities(monkeypatch):
    monkeypatch.setattr(roads, "post_query_features",
                        lambda url, geom, fields, **k: None)
    out = roads.compute(_record(), _TREE)
    assert out["roadDensity"]["value"] is None
    assert out["roadCrossingDensity"]["value"] is None
    assert "primary road query failed" in out["roadCrossingDensity"]["warnings"][0]


def test_baseflow_reads_the_shipped_grid():
    # the Sugar Run box (about 1.7 by 2.2 km) holds a few 1 km cell centers
    rec = _record()
    rec["site"] = {"snapLat": 40.31, "snapLon": -83.05}
    out = baseflow.compute(rec, _TREE)
    e = out["baseflowIndexPct"]
    assert e["value"] is not None and 0 <= e["value"] <= 100
    assert e["unit"] == "%" and "Wolock 2003" in e["source"]
    assert e["warnings"] == []
    assert out["baseflowIndexCells"]["value"] >= 2
    assert baseflow.compute(rec, _TREE) == out          # repeatable, no network


def test_baseflow_small_watershed_falls_back_and_says_so():
    tiny = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[
            [-83.0505, 40.3095], [-83.0495, 40.3095], [-83.0495, 40.3105],
            [-83.0505, 40.3105], [-83.0505, 40.3095]]]}}]}
    rec = {"watershed": {"polygon": tiny, "areaSqkm": 0.01},
           "site": {"snapLat": 40.31, "snapLon": -83.05}}
    e = baseflow.compute(rec, _TREE)["baseflowIndexPct"]
    assert e["value"] is not None
    assert e["warnings"] and "smaller than a 1 km grid cell" in e["warnings"][0]


def test_baseflow_outside_the_grid_is_unavailable():
    sea = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[
            [-60.0, 30.0], [-59.9, 30.0], [-59.9, 30.1], [-60.0, 30.1], [-60.0, 30.0]]]}}]}
    rec = {"watershed": {"polygon": sea, "areaSqkm": 100.0},
           "site": {"snapLat": 30.05, "snapLon": -59.95}}
    e = baseflow.compute(rec, _TREE)["baseflowIndexPct"]
    assert e["value"] is None and "outside the base-flow index grid" in e["warnings"][0]
    assert baseflow.compute({"watershed": {}}, [])["baseflowIndexPct"]["value"] is None
