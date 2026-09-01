"""The shared anchoring policy: deterministic payloads, the declined (never
refused) routing, outages as errors, and payload parity with EASI's
``routing`` when the EASI source tree is present."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

from site_engine import anchor, hr

_EASI = Path(__file__).resolve().parents[3] / "apps" / "easi"
_SNAP_OK = {"comid": 555, "snap_lat": 40.001, "snap_lon": -83.001}
_ATTRS_OK = {"gnis_name": "Big Run", "drainage_area_sqkm": 10.0, "huc8": None,
             "slope": None, "fcode": None, "stream_order": None}
_HR_HIT = (40.0005, -83.0005, 42.0, 999)


def _hr_rec(da=2.0, **over) -> dict:
    rec = {"nhdplusid": 999, "gnis_name": "Little Trib",
           "reachcode": "05060001001737", "lengthkm": 1.0, "totdasqkm": da,
           "slope": 0.01, "fcode": 46003, "ftype": 460, "stream_order": 1,
           "hydroseq": 1, "uphydroseq": 2, "dnhydroseq": 3, "vpuid": "0506",
           "qama": None, "geometry": None}
    rec.update(over)
    return rec


def _stub(monkeypatch, *, hr_rec=..., snap=None, attrs=None):
    if hr_rec is ...:
        hr_rec = _hr_rec()
    monkeypatch.setattr(hr, "flowline_by_id", lambda nid, **k: hr_rec)
    monkeypatch.setattr(anchor, "hydrolocation_snap",
                        lambda lat, lon: dict(snap or {}))
    monkeypatch.setattr(anchor, "v2_flowline_attrs",
                        lambda comid: dict(attrs or {}))


def test_route_payload_and_determinism(monkeypatch):
    _stub(monkeypatch, snap=_SNAP_OK, attrs=_ATTRS_OK)
    a = anchor.route_from_hr(40.0, -83.0, _HR_HIT)
    b = anchor.route_from_hr(40.0, -83.0, _HR_HIT)
    assert a == b
    p = a["anchor"]
    assert p["anchorKind"] == "hrSurrogate"
    assert p["anchorSchemaVersion"] == anchor.ANCHOR_SCHEMA_VERSION
    assert p["clickedStream"]["nhdplusId"] == 999
    assert p["clickedStream"]["gnisName"] == "Little Trib"
    assert p["scoredReach"]["comid"] == 555
    assert p["scoredReach"]["gnisName"] == "Big Run"
    r = p["routing"]
    assert r["method"] == "nldi-hydrolocation-raindrop"
    assert r["daRatio"] == 5.0 and r["daRatioLimit"] == anchor.DA_RATIO_MAX
    assert r["declined"] is False and "declineCode" not in r
    assert r["routedDistanceFt"] and r["routedDistanceFt"] > 0


def test_declined_past_the_limit_is_an_anchor_not_a_refusal(monkeypatch):
    _stub(monkeypatch, hr_rec=_hr_rec(da=1.0), snap=_SNAP_OK,
          attrs={**_ATTRS_OK, "drainage_area_sqkm": anchor.DA_RATIO_MAX})
    assert anchor.route_from_hr(40.0, -83.0, _HR_HIT)["anchor"]["routing"]["declined"] is False

    _stub(monkeypatch, hr_rec=_hr_rec(da=1.0), snap=_SNAP_OK,
          attrs={**_ATTRS_OK, "drainage_area_sqkm": anchor.DA_RATIO_MAX + 0.05})
    res = anchor.route_from_hr(40.0, -83.0, _HR_HIT)
    assert "refused" not in res and "error" not in res
    r = res["anchor"]["routing"]
    assert r["declined"] is True
    assert r["declineCode"] == "surrogate_da_ratio_exceeded"
    assert "limit 10" in r["declineMessage"]


def test_missing_da_declines_with_reason(monkeypatch):
    _stub(monkeypatch, hr_rec=_hr_rec(da=None), snap=_SNAP_OK, attrs=_ATTRS_OK)
    r = anchor.route_from_hr(40.0, -83.0, _HR_HIT)["anchor"]["routing"]
    assert r["declined"] is True and r["daRatio"] is None
    assert r["declineCode"] == "surrogate_da_unavailable"


def test_attrs_outage_is_recorded_and_declines(monkeypatch):
    _stub(monkeypatch, snap=_SNAP_OK, attrs={"error": "attrs: HTTP 504"})
    r = anchor.route_from_hr(40.0, -83.0, _HR_HIT)["anchor"]["routing"]
    assert r["declined"] is True
    assert r["attrsError"] == "attrs: HTTP 504"


def test_outage_is_retryable_error_not_an_answer(monkeypatch):
    _stub(monkeypatch, snap={"error": "hydrolocation: HTTP 502"})
    assert anchor.route_from_hr(40.0, -83.0, _HR_HIT) == {
        "error": "snap_service_error", "detail": "hydrolocation: HTTP 502"}
    _stub(monkeypatch, snap={})
    assert anchor.route_from_hr(40.0, -83.0, _HR_HIT) == {"error": "no_stream_found"}


def test_classify_v2_short_circuits_without_hr(monkeypatch):
    def no_hr(*a, **k):
        raise AssertionError("HR must not be touched for a covered point")
    monkeypatch.setattr(anchor, "hr_snap", no_hr)
    monkeypatch.setattr(anchor, "hydrolocation_snap",
                        lambda lat, lon: {"comid": 777, "snap_lat": lat,
                                          "snap_lon": lon})
    p = anchor.classify(40.0, -83.0)["anchor"]
    assert p["anchorKind"] == "v2Direct"
    assert p["scoredReach"]["comid"] == 777
    assert p["scoredReach"]["snapDistFt"] == 0.0


def test_classify_routes_far_snap_through_hr(monkeypatch):
    monkeypatch.setattr(anchor, "hydrolocation_snap",
                        lambda lat, lon: {"comid": 555, "snap_lat": lat + 0.01,
                                          "snap_lon": lon})
    monkeypatch.setattr(anchor, "hr_snap", lambda lat, lon, **k: (lat, lon, 30.0, 999))
    monkeypatch.setattr(hr, "flowline_by_id", lambda nid, **k: _hr_rec(da=2.0))
    monkeypatch.setattr(anchor, "v2_flowline_attrs", lambda comid: dict(_ATTRS_OK))
    p = anchor.classify(40.0, -83.0)["anchor"]
    assert p["anchorKind"] == "hrSurrogate"
    assert p["clickedStream"]["nhdplusId"] == 999
    assert p["scoredReach"]["comid"] == 555


def test_classify_far_raindrop_without_hr_is_covered_with_note(monkeypatch):
    monkeypatch.setattr(anchor, "hydrolocation_snap",
                        lambda lat, lon: {"comid": 5218161,
                                          "snap_lat": lat + 0.001, "snap_lon": lon})
    monkeypatch.setattr(anchor, "hr_snap", lambda lat, lon, **k: None)
    p = anchor.classify(39.955, -83.003)["anchor"]
    assert p["anchorKind"] == "v2Direct"
    assert p["scoredReach"]["snapDistFt"] > anchor.HR_SNAP_TOL_FT
    assert any("drains to" in n for n in p["notes"])


def test_classify_no_stream_anywhere(monkeypatch):
    monkeypatch.setattr(anchor, "hydrolocation_snap", lambda lat, lon: {})
    monkeypatch.setattr(anchor, "hr_snap", lambda lat, lon, **k: None)
    assert anchor.classify(40.0, -83.0) == {"error": "no_stream_found"}
    monkeypatch.setattr(anchor, "hr_snap", lambda lat, lon, **k: (lat, lon, 500.0, 999))
    assert anchor.classify(40.0, -83.0) == {"error": "no_stream_found"}


def test_classify_click_uses_the_apps_own_snaps(monkeypatch):
    def no_service(*a, **k):
        raise AssertionError("a V2 click needs no service call")
    monkeypatch.setattr(anchor, "hydrolocation_snap", no_service)
    p = anchor.classify_click(40.0, -83.0, v2_hit=(40.0001, -83.0, 12.0, 777))["anchor"]
    assert p["anchorKind"] == "v2Direct" and p["scoredReach"]["comid"] == 777
    assert p["scoredReach"]["snapDistFt"] == 12.0
    _stub(monkeypatch, snap=_SNAP_OK, attrs=_ATTRS_OK)
    p = anchor.classify_click(40.0, -83.0, v2_hit=(40.0, -83.0, 900.0, 777),
                              hr_hit=_HR_HIT)["anchor"]
    assert p["anchorKind"] == "hrSurrogate"
    assert anchor.classify_click(40.0, -83.0) == {"error": "no_stream_found"}


def test_no_position_fallback_in_anchoring():
    src = inspect.getsource(anchor)
    assert "comid/position" not in src.replace("never the nearest-position", "")
    assert "feature_byloc" not in src


@pytest.mark.skipif(not _EASI.is_dir(), reason="EASI source not present")
def test_payload_parity_with_easi_routing(monkeypatch):
    sys.path.insert(0, str(_EASI))
    from easi import routing

    def strip(p: dict) -> dict:
        p = dict(p)
        p.pop("notes", None)
        r = dict(p.get("routing") or {})
        for k in ("declineCode", "declineMessage", "attrsError"):
            r.pop(k, None)
        if "routing" in p:
            p["routing"] = r
        return p

    cases = [
        (_hr_rec(), _SNAP_OK, _ATTRS_OK),                          # routed
        (_hr_rec(da=1.0), _SNAP_OK,
         {**_ATTRS_OK, "drainage_area_sqkm": 10.05}),              # refused/declined
        (_hr_rec(da=None), _SNAP_OK, _ATTRS_OK),                   # DA unknown
    ]
    for hr_rec, snap, attrs in cases:
        monkeypatch.setattr(routing.nhd_hr, "hr_flowline_by_id",
                            lambda nid, **k: hr_rec)
        monkeypatch.setattr(routing, "_hydrolocation_snap",
                            lambda lat, lon: dict(snap))
        monkeypatch.setattr(routing.delineation, "flowline_attrs",
                            lambda comid: dict(attrs))
        _stub(monkeypatch, hr_rec=hr_rec, snap=snap, attrs=attrs)
        # EASI's auto policy returns the same payload; its legacy policy is the
        # only one that still answers with the refusal dict.
        theirs = routing.route_from_hr(40.0, -83.0, _HR_HIT)
        legacy = routing.route_from_hr(40.0, -83.0, _HR_HIT,
                                       policy=routing.POLICY_STREAMCAT_LEGACY)
        ours = anchor.route_from_hr(40.0, -83.0, _HR_HIT)["anchor"]
        expected = theirs["anchor"]
        assert strip(ours) == strip(expected)
        assert ours["routing"]["declined"] == bool(legacy.get("refused"))
        assert ours["routing"]["declined"] == bool(expected["routing"].get("declined"))
