"""The watershed evidence layer: the StreamCat lookup engine reproduces the
historical accessor values and source strings byte for byte, the STAF site
engine maps a compute_site record with its own labels, a missing class is
never zero, an unavailable layer never falls back to a proxy, and
``recompute_watershed_rows`` reproduces a report on the same context."""
from __future__ import annotations

import asyncio
import json

from easi import assessment, watershed
from easi.metrics import base, biology, geomorphology, hydrology, physicochemistry
from easi.metrics.base import AnalysisContext
from test_batch_parity import STREAMCAT, _ctx, _parity_view, _stub

_ENGINE_RECORD = {
    "engineId": "site-engine", "engineVersion": "0.2.0", "status": "ok",
    "watershed": {"areaSqkm": 3.78, "vaaAreaSqkm": 3.8, "areaAgreement": 0.995,
                  "nReaches": 5, "nHops": 3, "polygon": None},
    "metrics": {
        "imperviousPctWatershed": {"value": 12.5}, "cropPctWatershed": {"value": 20.0},
        "hayPasturePctWatershed": {"value": 5.5}, "woodyWetlandPctWatershed": {"value": 1.0},
        "herbWetlandPctWatershed": {"value": 0.5}, "roadDensity": {"value": 2.25},
        "soilKFactor": {"value": 0.28}, "damStoragePerSqkm": {"value": 2.0},
        "runoffDepthMm": {"value": 350.0}, "forestPctRiparian": {"value": 40.0},
        "shrubPctRiparian": {"value": 5.0}, "grasslandPctRiparian": {"value": 10.0},
        "woodyWetlandPctRiparian": {"value": 3.0}, "herbWetlandPctRiparian": {"value": 2.0},
    },
}


def _bare_ctx(row=None) -> AnalysisContext:
    c = AnalysisContext(lat=40.1, lon=-83.1, comid=1, sinuosity=1.2)
    c.extras["streamcat"] = dict(STREAMCAT if row is None else row)
    return c


def _layer_ctx(layer) -> AnalysisContext:
    c = _bare_ctx()
    c.extras["watershed"] = layer
    return c


def test_streamcat_layer_reproduces_the_accessors():
    bare = _bare_ctx()
    layered = _layer_ctx(watershed.from_streamcat(STREAMCAT))
    for fn in (base.ag_pct, base.riparian_forest_pct, base.riparian_veg_breakdown,
               base.riparian_woody_breakdown, base.riparian_woody_pct,
               base.riparian_natural_veg_pct):
        assert fn(bare) == fn(layered)
    assert base.ag_pct(bare) == 25.0
    assert base.riparian_forest_pct(bare) == 40.0
    assert base.riparian_veg_breakdown(bare)["total"] == 70.0
    assert watershed.provider(bare) == "streamcat" == watershed.provider(layered)


def test_streamcat_labels_are_the_historical_strings():
    ctx = _bare_ctx()
    assert watershed.input_source(ctx, "impervious.impervious") == \
        "EPA StreamCat pctimp2019 (watershed)"
    assert watershed.input_source(ctx, "temperature.impervious") == \
        "EPA StreamCat pctimp2019ws"
    assert watershed.result_source(ctx, "sediment") == \
        "EPA StreamCat agriculture + K-factor + road density"
    rows = {
        "wetlands": hydrology.wetlands(ctx), "flow": hydrology.flow_alteration(ctx),
        "roads": hydrology.reach_inflow(ctx), "sediment": geomorphology.sediment_supply(ctx),
        "cpom": physicochemistry.detrital_cpom(ctx), "habitat": biology.habitat_complexity(ctx),
    }
    assert rows["wetlands"].source == "EPA StreamCat wetlands (watershed)"
    assert rows["flow"].source == "EPA StreamCat normalized storage + annual runoff"
    assert rows["roads"].source == "EPA StreamCat road density"
    assert rows["cpom"].source == "EPA StreamCat riparian land cover (rp100)"
    assert rows["habitat"].source == "EPA StreamCat woody riparian cover (rp100)"
    assert all(r.rating in {"Good", "Fair", "Poor"} for r in rows.values())


def test_engine_layer_maps_the_record():
    layer = watershed.from_engine(_ENGINE_RECORD)
    assert layer["provider"] == "site-engine"
    v = layer["values"]
    assert v["imperviousPct"] == 12.5 and v["roadDensity"] == 2.25
    assert v["damStorageM3PerSqkm"] == round(2.0 * 1233.48184, 3)
    assert v["ripConifPct"] is None and v["ripForestPct"] == 40.0
    assert layer["meta"]["areaSqkm"] == 3.78 and layer["meta"]["nReaches"] == 5
    assert layer["label"] == "STAF site engine v0.2.0"
    assert layer["inputSources"]["impervious.impervious"].startswith(
        "STAF site engine v0.2.0, exact watershed")
    ctx = _layer_ctx(layer)
    assert base.ag_pct(ctx) == 25.5
    assert base.riparian_forest_pct(ctx) == 40.0
    assert base.riparian_veg_breakdown(ctx) == {
        "forest": 40.0, "shrub": 5.0, "grassland": 10.0, "wetland": 5.0, "total": 60.0}
    res = hydrology.flow_alteration(ctx)
    assert res.rating and "EROM" in res.note
    assert res.source.startswith("STAF site engine v0.2.0")


def test_missing_class_is_none_never_zero():
    rec = json.loads(json.dumps(_ENGINE_RECORD))
    del rec["metrics"]["herbWetlandPctWatershed"]
    del rec["metrics"]["shrubPctRiparian"]
    ctx = _layer_ctx(watershed.from_engine(rec))
    assert hydrology.wetlands(ctx).status == "unavailable"
    assert base.riparian_veg_breakdown(ctx) is None
    assert base.riparian_woody_breakdown(ctx) is None


def test_unavailable_layer_never_uses_a_proxy():
    ctx = _layer_ctx(watershed.unavailable("the engine refused: 61 reaches"))
    ctx.extras["landcover"] = {"impervious_pct": 3.0, "ag_pct": 10.0}   # must be ignored
    res = hydrology.impervious(ctx)
    assert res.status == "unavailable"
    assert "61 reaches" in res.note and "SFARI or DEEP" in res.note
    assert hydrology.reach_inflow(ctx).status == "unavailable"
    assert geomorphology.sediment_supply(ctx).status == "unavailable"
    # The StreamCat provider keeps today's NLCD outage fallback.
    ctx2 = _bare_ctx({})
    ctx2.extras["landcover"] = {"impervious_pct": 3.0, "ag_pct": 10.0}
    res2 = hydrology.impervious(ctx2)
    assert res2.rating and "NLCD 2021" in res2.source


def test_build_picks_the_provider_from_the_engine_block():
    ctx = _bare_ctx()
    assert watershed.build(ctx, STREAMCAT)["provider"] == "streamcat"
    assert watershed.wants_nlcd_fallback(ctx)
    ctx.extras["watershedEngine"] = {"status": "ok", "record": _ENGINE_RECORD,
                                     "engineVersion": "0.2.0"}
    assert watershed.build(ctx, STREAMCAT)["provider"] == "site-engine"
    assert not watershed.wants_nlcd_fallback(ctx)
    ctx.extras["watershedEngine"] = {"status": "refused", "reason": "budget"}
    lyr = watershed.build(ctx, STREAMCAT)
    assert lyr["provider"] is None and lyr["unavailableReason"] == "budget"
    assert not watershed.wants_nlcd_fallback(ctx)


def test_recompute_watershed_rows_reproduces_the_report(monkeypatch):
    _stub(monkeypatch)
    ctx = _ctx()
    report = asyncio.run(assessment.assess(ctx))
    again = assessment.recompute_watershed_rows(report, ctx)
    assert _parity_view(again) == _parity_view(report)
    for a, b in zip(report["metricRows"], again["metricRows"]):
        assert a["source"] == b["source"] and a["note"] == b["note"]
        assert a["anchorLabel"] == b["anchorLabel"]
    assert again["ecosystemConditionIndex"] == report["ecosystemConditionIndex"]


def test_engine_provider_changes_only_the_watershed_rows(monkeypatch):
    _stub(monkeypatch)
    ctx = _ctx()
    ctx.extras["watershedEngine"] = {"status": "ok", "record": _ENGINE_RECORD,
                                     "engineVersion": "0.2.0"}
    report = asyncio.run(assessment.assess(ctx))
    watershed_ids = set(assessment.WATERSHED_METRIC_IDS)
    for row in report["metricRows"]:
        if row["metricId"] in watershed_ids:
            assert "STAF site engine v0.2.0" in row["source"], row["metricId"]
        else:
            assert "STAF site engine" not in (row["source"] or "")
    _stub(monkeypatch)
    plain = asyncio.run(assessment.assess(_ctx()))
    plain_rows = {r["metricId"]: r for r in plain["metricRows"]}
    for row in report["metricRows"]:
        if row["metricId"] not in watershed_ids:
            assert row["rating"] == plain_rows[row["metricId"]]["rating"]
