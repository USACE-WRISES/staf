"""Catalog completeness + drift guard for :mod:`easi.methods`.

The worksheet "Scoring method" panel recomputes ratings from perturbed inputs via
``easi.methods.evaluate``, which mirrors the metric adapters. This test asserts (a) every metric
has a catalog entry, and (b) ``evaluate`` reproduces each adapter's own value + rating from the
inputs the adapter recorded in its ``scoring`` trace — so the panel can never silently diverge
from the pipeline. Uses the same deterministic offline stubs as ``test_batch_parity``.
"""
from __future__ import annotations

import asyncio

from easi import assessment, config, methods
from easi.metrics import base, biology, physicochemistry
from easi.metrics.hydraulics import HYPORHEIC_ID
from easi.metrics.hydrology import IMPERVIOUS_ID

_SC_WS = {"pctimp2019ws": 8.0, "pctwdwet2019ws": 3.0, "pcthbwet2019ws": 2.0,
          "pctcrop2019ws": 15.0, "pcthay2019ws": 10.0, "kffactws": 0.3, "rddensws": 1.2,
          "damdensws": 0.1, "damnrmstorws": 5.0, "tmean8110ws": 11.0}
_SC_RP100 = {"pctconif2019wsrp100": 8.0, "pctdecid2019wsrp100": 20.0, "pctmxfst2019wsrp100": 12.0}
STREAMCAT = {**_SC_WS, **_SC_RP100}
REACH_GEOMORPH = {"entrenchment_ratio": 2.5, "bank_height_ratio": 1.1, "edge_limited": False,
                  "dem_resolution_m": 10}
BIEGER = {"width_m": 5.0, "depth_m": 1.0, "area_m2": 5.0, "division": "USA",
          "division_name": "National curve", "regional": False}


def _stub(monkeypatch):
    monkeypatch.setattr(assessment.streamcat, "metrics_by_comid", lambda *a, **k: dict(STREAMCAT))
    monkeypatch.setattr(assessment.nlcd, "watershed_landcover", lambda *a, **k: {})
    monkeypatch.setattr(assessment.wbd, "huc12_at_point", lambda *a, **k: "010203040506")
    monkeypatch.setattr(assessment.threedep, "reach_geomorphology", lambda *a, **k: dict(REACH_GEOMORPH))
    monkeypatch.setattr(assessment.bieger, "bankfull_geometry", lambda *a, **k: dict(BIEGER))
    monkeypatch.setattr(physicochemistry.attains, "impairment_at_point", lambda *a, **k: {})
    monkeypatch.setattr(physicochemistry.attains, "impairment_near_point", lambda *a, **k: {})
    monkeypatch.setattr(physicochemistry.wqp, "median_value", lambda *a, **k: None)
    monkeypatch.setattr(biology.nas, "established_taxa", lambda *a, **k: [])
    monkeypatch.setattr(biology.nid_barriers, "barriers_near", lambda *a, **k: [])


def _ctx() -> base.AnalysisContext:
    return base.AnalysisContext(lat=40.10, lon=-83.10, comid=1234567, huc8="01020304",
                               drainage_area_sqkm=50.0, slope=0.005, fcode=46006,
                               stream_order=3, sinuosity=1.2)


def test_every_metric_has_a_method():
    assert set(config.metrics_by_id()) <= set(methods.METHODS)


def test_evaluate_matches_adapters(monkeypatch):
    _stub(monkeypatch)
    report = asyncio.run(assessment.assess(_ctx()))
    checked = 0
    for r in report["metricRows"]:
        sc = r.get("scoring")
        if not sc:
            continue
        ev = methods.evaluate(r["metricId"],
                              {**sc["inputs"], "rating": r["generatedRating"], "value": sc["value"]},
                              sc.get("model"))
        assert ev["rating"] == r["generatedRating"], r["metricId"]
        if sc["value"] is not None and ev["value"] is not None:
            assert abs(float(ev["value"]) - float(sc["value"])) < 1e-9, r["metricId"]
        checked += 1
    assert checked >= 15   # most metrics compute in the deterministic fixture


def test_evaluate_hyporheic_example():
    # the design's worked example: slope 0.023, sinuosity 1.187 -> V 0.75, Good, score 13
    ev = methods.evaluate(HYPORHEIC_ID, {"slope": 0.023, "sinuosity": 1.187})
    assert ev["value"] == 0.75 and ev["rating"] == "Good" and ev["functionScore"] == 13


def test_evaluate_worst_of_governs():
    # impervious 2% (Good) but agriculture 61% (Poor) -> the worse governs
    ev = methods.evaluate(IMPERVIOUS_ID, {"impervious": 2.0, "agriculture": 61.0})
    assert ev["rating"] == "Poor"


def test_slider_specs_expand_max():
    method = methods.METHODS[HYPORHEIC_ID]
    # a site slope above the default 0.02 max expands the slider ceiling
    specs = methods.slider_specs(method, {"slope": 0.05, "sinuosity": 1.2})
    slope_spec = next(s for s in specs if s[0].key == "slope")
    assert slope_spec[2][1] >= 0.05


def test_band_range_texts_scalar_combined():
    from easi.metrics.geomorphology import SEDIMENT_ID
    r = methods.band_range_texts(methods.METHODS[SEDIMENT_ID])
    assert r["Good"] == "Supply risk < 0.33"
    assert r["Fair"] == "Supply risk 0.33-0.66"
    assert r["Poor"] == "Supply risk > 0.66"


def test_band_range_texts_count():
    from easi.metrics.biology import BARRIERS_ID, INVASIVES_ID
    r = methods.band_range_texts(methods.METHODS[INVASIVES_ID])
    assert r == {"Good": "0 taxa", "Fair": "1-2 taxa", "Poor": "> 2 taxa"}
    b = methods.band_range_texts(methods.METHODS[BARRIERS_ID])
    assert b["Good"] == "0 dams" and b["Fair"] == "1 dam" and b["Poor"] == "> 1 dams"


def test_band_range_texts_worst_joins_indicators():
    from easi.metrics.physicochemistry import NUTRIENTS_ID
    r = methods.band_range_texts(methods.METHODS[NUTRIENTS_ID])
    for rating in ("Good", "Fair", "Poor"):
        assert "TN" in r[rating] and "TP" in r[rating] and " · " in r[rating]
    assert r["Good"] == "TN < 0.5 mg/L · TP < 0.05 mg/L"


def test_band_range_texts_categorical_is_empty():
    from easi.metrics.hydraulics import LOW_FLOW_ID
    assert methods.band_range_texts(methods.METHODS[LOW_FLOW_ID]) == {}
