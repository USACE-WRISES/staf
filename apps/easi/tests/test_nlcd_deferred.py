"""NLCD over the watershed is requested only when an adapter reads it (2026-10-06).

``easi.assessment`` prefetches it beside StreamCat on every StreamCat-basis run; the adapters
read it only where StreamCat has no land-cover value, so a StreamCat row with land cover means
no MRLC request at all, and a row without it gets one request and the same ratings as before.
"""
from __future__ import annotations

import asyncio
import copy
import pickle

from easi import assessment
from easi.datasources import nlcd
from easi.metrics import base, biology, physicochemistry
from easi.metrics.hydrology import IMPERVIOUS_ID

POLY = {"type": "FeatureCollection", "features": [{
    "type": "Feature", "properties": {},
    "geometry": {"type": "Polygon", "coordinates": [[[-72.30, 43.60], [-72.20, 43.60],
                                                     [-72.20, 43.70], [-72.30, 43.60]]]}}]}
LC = {"impervious_pct": 40.0, "forest_pct": 50.0, "wetland_pct": 2.0, "ag_pct": 30.0}
STREAMCAT = {"pctimp2019ws": 8.0, "pctwdwet2019ws": 3.0, "pcthbwet2019ws": 2.0,
             "pctcrop2019ws": 15.0, "pcthay2019ws": 10.0, "kffactws": 0.3, "rddensws": 1.2,
             "damnrmstorws": 5000.0, "runoffws": 400.0}
NO_LAND_COVER = dict((k, v) for k, v in STREAMCAT.items()
                     if not k.startswith(("pctimp", "pctcrop", "pcthay")))


def _counting(monkeypatch, answer=None):
    calls = []
    monkeypatch.setattr(nlcd, "_fetch", lambda gj: calls.append(gj) or dict(LC if answer is None else answer))
    return calls


def _stub_sources(monkeypatch, row):
    monkeypatch.setattr(assessment.streamcat, "metrics_by_comid", lambda *a, **k: dict(row))
    monkeypatch.setattr(assessment.wbd, "huc12_at_point", lambda *a, **k: "010203040506")
    monkeypatch.setattr(assessment.threedep, "reach_geomorphology", lambda *a, **k: {})
    monkeypatch.setattr(assessment.nrsa, "evidence_for_reach", lambda *a, **k: None)
    monkeypatch.setattr(physicochemistry.attains, "impairment_at_point", lambda *a, **k: {})
    monkeypatch.setattr(physicochemistry.attains, "impairment_near_point", lambda *a, **k: {})
    monkeypatch.setattr(physicochemistry.wqp, "sample_summary", lambda *a, **k: None)
    monkeypatch.setattr(biology.nas, "established_taxa", lambda *a, **k: [])
    monkeypatch.setattr(biology.nid_barriers, "barriers_near", lambda *a, **k: [])


def _ctx() -> base.AnalysisContext:
    return base.AnalysisContext(lat=43.65, lon=-72.25, comid=1234567, huc8="01080106",
                                watershed_geojson=POLY, drainage_area_sqkm=50.0, slope=0.005,
                                fcode=46006, stream_order=3, sinuosity=1.2)


def _row(report: dict, mid: str) -> dict:
    return next(r for r in report["metricRows"] if r["metricId"] == mid)


def test_nothing_is_requested_until_a_value_is_read(monkeypatch):
    calls = _counting(monkeypatch)
    lc = nlcd.watershed_landcover(POLY)
    assert calls == [] and "not requested" in repr(lc)
    assert lc.get("impervious_pct") == 40.0 and lc["ag_pct"] == 30.0
    assert (lc or {}).get("forest_pct") == 50.0
    assert len(calls) == 1 and dict(lc) == LC        # asked once, then kept


def test_no_polygon_is_an_empty_answer_without_a_request(monkeypatch):
    calls = _counting(monkeypatch)
    assert nlcd.watershed_landcover(None) == {}
    assert nlcd.watershed_landcover({"type": "FeatureCollection", "features": []}) == {}
    assert calls == []


def test_a_failed_request_reads_as_missing(monkeypatch):
    _counting(monkeypatch, answer={})
    lc = nlcd.watershed_landcover(POLY)
    assert not lc and (lc or {}).get("impervious_pct") is None


def test_the_on_demand_answer_copies_and_pickles(monkeypatch):
    _counting(monkeypatch)
    lc = nlcd.watershed_landcover(POLY)
    assert dict(pickle.loads(pickle.dumps(lc))) == LC
    assert dict(copy.deepcopy(lc)) == LC


def test_an_assessment_with_streamcat_land_cover_never_asks_mrlc(monkeypatch):
    calls = _counting(monkeypatch)
    _stub_sources(monkeypatch, STREAMCAT)
    report = asyncio.run(assessment.assess(_ctx()))
    assert calls == []
    assert _row(report, IMPERVIOUS_ID)["rating"] in ("Good", "Fair", "Poor")


def test_without_streamcat_land_cover_it_asks_once_and_scores_as_the_prefetch_did(monkeypatch):
    calls = _counting(monkeypatch)
    _stub_sources(monkeypatch, NO_LAND_COVER)
    on_demand = _row(asyncio.run(assessment.assess(_ctx())), IMPERVIOUS_ID)
    assert len(calls) == 1
    # the eager prefetch (before 2026-10-06) handed the adapters the same values as a plain dict
    monkeypatch.setattr(assessment.nlcd, "watershed_landcover", lambda *a, **k: dict(LC))
    eager = _row(asyncio.run(assessment.assess(_ctx())), IMPERVIOUS_ID)
    assert on_demand["rating"] == eager["rating"] == "Poor"
    for key in ("valueText", "index", "functionScore", "scoring"):
        assert on_demand[key] == eager[key], key
