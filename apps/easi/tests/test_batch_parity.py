"""E0 scoring-parity harness: lock the full assess() -> scored report output.

This is the regression net for the batch-engine refactor (easi.batch). It drives
``assessment.assess`` end-to-end with a fixed, deterministic set of offline stubs
(StreamCat row, NLCD, WBD, 3DEP reach geomorph, Bieger bankfull, and the external
ATTAINS/WQP/NAS/NID datasources) over the REAL 20-metric registry, then asserts the
scored output (ECI, sub-indices, function scores, and every metric's
rating/index/functionScore/status) matches a committed golden snapshot byte-for-byte.

Regenerate the golden intentionally (only when a scoring change is expected) with:
    EASI_WRITE_GOLDEN=1 python -m pytest tests/test_batch_parity.py -q
Review the diff to confirm the change is intended before committing.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from easi import assessment
from easi.metrics import base, biology, physicochemistry

GOLDEN = Path(__file__).parent / "data" / "parity_golden.json"

# --- deterministic prefetch data (keys mirror what the real adapters read) --- #
_SC_WS = {
    "pctimp2019ws": 8.0,          # impervious < 10 -> Good
    "pctwdwet2019ws": 3.0, "pcthbwet2019ws": 2.0,   # wetlands sum 5.0 -> Fair
    "pctcrop2019ws": 15.0, "pcthay2019ws": 10.0,    # ag_pct 25
    "kffactws": 0.3, "rddensws": 1.2,
    "damnrmstorws": 5000.0, "runoffws": 400.0,      # DOR 1.25% -> Good
}
_SC_RP100 = {   # 100 m riparian buffer classes (all required by the veg accessors)
    "pctconif2019wsrp100": 8.0,
    "pctdecid2019wsrp100": 20.0,
    "pctmxfst2019wsrp100": 12.0,
    "pctgrs2019wsrp100": 15.0,
    "pctshrb2019wsrp100": 10.0,
    "pctwdwet2019wsrp100": 3.0,
    "pcthbwet2019wsrp100": 2.0,
}
_SC_INTEGRITY = {   # published StreamCat integrity components + biological model
    "hydcat": 0.82, "hydws": 0.78, "sedcat": 0.55, "sedws": 0.52,
    "chemcat": 0.74, "chemws": 0.71, "conncat": 0.90, "connws": 0.88,
    "tempcat": 0.85, "tempws": 0.83, "habtcat": 0.80, "habtws": 0.77,
    "prg_bmmiws": 0.71,
}
STREAMCAT = {**_SC_WS, **_SC_RP100, **_SC_INTEGRITY}

REACH_GEOMORPH = {
    "entrenchment_ratio": 2.5,    # >= 2.2 -> Good (floodplain access)
    "bank_height_ratio": 1.1,     # engagement ~Good, channel-evolution < 1.3 -> Good
    "edge_limited": False,
    "dem_resolution_m": 10,
    # deliberately no "profile"/"thalweg" -> _build_cross_section returns None
}

BIEGER = {"width_m": 5.0, "depth_m": 1.0, "area_m2": 5.0,
          "division": "USA", "division_name": "National curve", "regional": False}


def _stub(monkeypatch):
    monkeypatch.setattr(assessment.streamcat, "metrics_by_comid",
                        lambda *a, **k: dict(STREAMCAT))
    monkeypatch.setattr(assessment.nlcd, "watershed_landcover", lambda *a, **k: {})
    monkeypatch.setattr(assessment.wbd, "huc12_at_point",
                        lambda *a, **k: "010203040506")
    monkeypatch.setattr(assessment.threedep, "reach_geomorphology",
                        lambda *a, **k: dict(REACH_GEOMORPH))
    monkeypatch.setattr(assessment.bieger, "bankfull_geometry",
                        lambda *a, **k: dict(BIEGER))
    # No connected NRSA visit for this synthetic reach, so low flow, substrate and
    # biological integrity exercise their documented published fallbacks.
    monkeypatch.setattr(assessment.nrsa, "evidence_for_reach", lambda *a, **k: None)
    # external services -> deterministic "no data" so those metrics take their
    # documented fallback branch offline (never a live network call).
    monkeypatch.setattr(physicochemistry.attains, "impairment_at_point",
                        lambda *a, **k: {})
    monkeypatch.setattr(physicochemistry.attains, "impairment_near_point",
                        lambda *a, **k: {})
    monkeypatch.setattr(physicochemistry.wqp, "sample_summary", lambda *a, **k: None)
    monkeypatch.setattr(biology.nas, "established_taxa", lambda *a, **k: [])
    monkeypatch.setattr(biology.nid_barriers, "barriers_near", lambda *a, **k: [])


def _ctx() -> base.AnalysisContext:
    return base.AnalysisContext(
        lat=40.10, lon=-83.10, comid=1234567, huc8="01020304",
        drainage_area_sqkm=50.0, slope=0.005, fcode=46006,
        stream_order=3, sinuosity=1.2)


def _parity_view(report: dict) -> dict:
    return {
        "ecosystemConditionIndex": report["ecosystemConditionIndex"],
        "subIndices": report["subIndices"],
        "functionScores": report["functionScores"],
        "computedCount": report["computedCount"],
        "totalCount": report["totalCount"],
        "metrics": [
            {"metricId": r["metricId"], "rating": r["rating"],
             "index": r["index"], "functionScore": r["functionScore"],
             "status": r["status"]}
            for r in report["metricRows"]
        ],
    }


def test_scoring_parity(monkeypatch):
    _stub(monkeypatch)
    report = asyncio.run(assessment.assess(_ctx()))
    view = _parity_view(report)

    if os.environ.get("EASI_WRITE_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(view, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert view == golden


def test_parity_determinism(monkeypatch):
    # Same inputs -> identical scored output across runs (no hidden nondeterminism).
    _stub(monkeypatch)
    a = _parity_view(asyncio.run(assessment.assess(_ctx())))
    _stub(monkeypatch)
    b = _parity_view(asyncio.run(assessment.assess(_ctx())))
    assert a == b
