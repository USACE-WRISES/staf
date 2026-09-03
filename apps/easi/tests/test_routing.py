"""Offline tests for the deterministic anchoring policy (easi.routing).

The policy invariants under test:
  * identical inputs produce identical payloads (no fallback endpoints);
  * a click resolving to the V2 network short-circuits without touching HR;
  * the DA-ratio refusal boundary sits exactly at DA_RATIO_MAX;
  * missing drainage area refuses with a reason instead of guessing;
  * outages surface as retryable errors, never as a different answer.
"""
from __future__ import annotations

import inspect

from easi import routing


def _hr_rec(da=2.0, **over) -> dict:
    rec = {"nhdplusid": 999, "gnis_name": "Little Trib", "reachcode": "05060001001737",
           "lengthkm": 1.0, "totdasqkm": da, "slope": 0.01, "fcode": 46003,
           "ftype": 460, "stream_order": 1, "hydroseq": 1, "uphydroseq": 2,
           "dnhydroseq": 3, "vpuid": "0506", "geometry": None}
    rec.update(over)
    return rec


def _stub(monkeypatch, *, hr_rec=..., snap=None, attrs=None):
    if hr_rec is ...:
        hr_rec = _hr_rec()
    monkeypatch.setattr(routing.nhd_hr, "hr_flowline_by_id",
                        lambda nid, **k: hr_rec)
    monkeypatch.setattr(routing, "_hydrolocation_snap",
                        lambda lat, lon: dict(snap or {}))
    monkeypatch.setattr(routing.delineation, "flowline_attrs",
                        lambda comid: dict(attrs or {}))


_SNAP_OK = {"comid": 555, "snap_lat": 40.001, "snap_lon": -83.001}
_ATTRS_OK = {"gnis_name": "Big Run", "drainage_area_sqkm": 10.0}
_HR_SNAP = (40.0005, -83.0005, 42.0, 999)


def test_route_payload_and_determinism(monkeypatch):
    _stub(monkeypatch, snap=_SNAP_OK, attrs=_ATTRS_OK)
    a = routing.route_from_hr(40.0, -83.0, _HR_SNAP)
    b = routing.route_from_hr(40.0, -83.0, _HR_SNAP)
    assert a == b                                        # deterministic
    anchor = a["anchor"]
    assert anchor["anchorKind"] == "hrSurrogate"
    assert anchor["anchorSchemaVersion"] == routing.ANCHOR_SCHEMA_VERSION
    assert anchor["clickedStream"]["nhdplusId"] == 999
    assert anchor["clickedStream"]["gnisName"] == "Little Trib"
    assert anchor["scoredReach"]["comid"] == 555
    assert anchor["scoredReach"]["gnisName"] == "Big Run"
    r = anchor["routing"]
    assert r["method"] == "nldi-hydrolocation-raindrop"
    assert r["daRatio"] == 5.0 and r["daRatioLimit"] == routing.DA_RATIO_MAX
    assert r["declined"] is False
    assert r["routedDistanceFt"] and r["routedDistanceFt"] > 0


_LEGACY = routing.POLICY_STREAMCAT_LEGACY


def test_refusal_boundary_is_exactly_the_limit(monkeypatch):
    # ratio == limit passes; anything above refuses under the legacy policy.
    _stub(monkeypatch, hr_rec=_hr_rec(da=1.0), snap=_SNAP_OK,
          attrs={"gnis_name": "Big Run",
                 "drainage_area_sqkm": routing.DA_RATIO_MAX * 1.0})
    res = routing.route_from_hr(40.0, -83.0, _HR_SNAP, policy=_LEGACY)
    assert "anchor" in res and res["anchor"]["routing"]["declined"] is False

    _stub(monkeypatch, hr_rec=_hr_rec(da=1.0), snap=_SNAP_OK,
          attrs={"gnis_name": "Big Run",
                 "drainage_area_sqkm": routing.DA_RATIO_MAX + 0.05})
    res = routing.route_from_hr(40.0, -83.0, _HR_SNAP, policy=_LEGACY)
    assert res["refused"] is True
    assert res["code"] == "surrogate_da_ratio_exceeded"
    assert "limit 10" in res["message"]
    assert res["anchor"]["routing"]["declined"] is True


def test_missing_da_refuses_with_reason(monkeypatch):
    _stub(monkeypatch, hr_rec=_hr_rec(da=None), snap=_SNAP_OK, attrs=_ATTRS_OK)
    res = routing.route_from_hr(40.0, -83.0, _HR_SNAP, policy=_LEGACY)
    assert res["refused"] is True
    assert res["code"] == "surrogate_da_unavailable"
    assert res["anchor"]["routing"]["daRatio"] is None


def test_auto_policy_declines_instead_of_refusing(monkeypatch):
    # The default (auto) policy never refuses: past the bound the routing is
    # declined with a code and a plain message, and the site still proceeds
    # (the exact watershed comes from the site engine; COMID-keyed evidence
    # is withheld).
    _stub(monkeypatch, hr_rec=_hr_rec(da=1.0), snap=_SNAP_OK,
          attrs={"gnis_name": "Big Run",
                 "drainage_area_sqkm": routing.DA_RATIO_MAX + 0.05})
    res = routing.route_from_hr(40.0, -83.0, _HR_SNAP)
    assert "refused" not in res and "error" not in res
    r = res["anchor"]["routing"]
    assert r["declined"] is True
    assert r["declineCode"] == "surrogate_da_ratio_exceeded"
    assert "limit 10" in r["declineMessage"]
    assert "low flow" in r["declineMessage"]

    _stub(monkeypatch, hr_rec=_hr_rec(da=None), snap=_SNAP_OK, attrs=_ATTRS_OK)
    r = routing.route_from_hr(40.0, -83.0, _HR_SNAP)["anchor"]["routing"]
    assert r["declined"] is True and r["declineCode"] == "surrogate_da_unavailable"


def test_policies_share_the_payload_but_not_the_note(monkeypatch):
    _stub(monkeypatch, snap=_SNAP_OK, attrs=_ATTRS_OK)
    auto = routing.route_from_hr(40.0, -83.0, _HR_SNAP)["anchor"]
    legacy = routing.route_from_hr(40.0, -83.0, _HR_SNAP, policy=_LEGACY)["anchor"]
    assert {k: v for k, v in auto.items() if k != "notes"} == \
        {k: v for k, v in legacy.items() if k != "notes"}
    assert auto["notes"] != legacy["notes"]
    assert routing.WATERSHED_ENGINE_POLICIES == ("auto", "streamcat-legacy")


def test_outage_is_retryable_error_not_an_answer(monkeypatch):
    _stub(monkeypatch, snap={"error": "hydrolocation: 502 Bad Gateway"})
    res = routing.route_from_hr(40.0, -83.0, _HR_SNAP)
    assert res == {"error": "snap_service_error",
                   "detail": "hydrolocation: 502 Bad Gateway"}


def test_raindrop_empty_is_no_stream(monkeypatch):
    _stub(monkeypatch, snap={})
    assert routing.route_from_hr(40.0, -83.0, _HR_SNAP) == {"error": "no_stream_found"}


def test_resolve_v2_short_circuits_without_hr(monkeypatch):
    def no_hr(*a, **k):
        raise AssertionError("HR must not be touched for a covered point")
    monkeypatch.setattr(routing.nhd_hr, "hr_flowlines_in_bbox", no_hr)
    monkeypatch.setattr(routing, "_hydrolocation_snap",
                        lambda lat, lon: {"comid": 777, "snap_lat": lat,
                                          "snap_lon": lon})
    res = routing.resolve_anchor(40.0, -83.0)
    anchor = res["anchor"]
    assert anchor["anchorKind"] == "v2Direct"
    assert anchor["scoredReach"]["comid"] == 777
    assert anchor["scoredReach"]["snapDistFt"] == 0.0


def test_resolve_comid_without_geometry_stays_v2(monkeypatch):
    # Hydrolocation answered with a comid but no snap point to measure: the
    # historical behavior used that comid, so the anchor does too.
    monkeypatch.setattr(routing.nhd_hr, "hr_flowlines_in_bbox",
                        lambda *a, **k: None)
    monkeypatch.setattr(routing, "_hydrolocation_snap",
                        lambda lat, lon: {"comid": 777})
    res = routing.resolve_anchor(40.0, -83.0)
    assert res["anchor"]["anchorKind"] == "v2Direct"
    assert res["anchor"]["scoredReach"]["snapDistFt"] is None


def test_resolve_routes_far_snap_through_hr(monkeypatch):
    # The raindrop lands ~0.01 deg away (well past 150 ft), an HR line sits at
    # the point: the resolution must go through the HR routing path.
    monkeypatch.setattr(routing, "_hydrolocation_snap",
                        lambda lat, lon: {"comid": 555, "snap_lat": lat + 0.01,
                                          "snap_lon": lon})
    monkeypatch.setattr(routing.nhd_hr, "hr_flowlines_in_bbox",
                        lambda *a, **k: {"type": "FeatureCollection",
                                         "features": [{"stub": True}]})
    monkeypatch.setattr(routing.nhd_hr, "nearest_point_on_hr_lines",
                        lambda fc, lat, lon: (lat, lon, 30.0, 999))
    monkeypatch.setattr(routing.nhd_hr, "hr_flowline_by_id",
                        lambda nid, **k: _hr_rec(da=2.0))
    monkeypatch.setattr(routing.delineation, "flowline_attrs",
                        lambda comid: dict(_ATTRS_OK))
    res = routing.resolve_anchor(40.0, -83.0)
    assert res["anchor"]["anchorKind"] == "hrSurrogate"
    assert res["anchor"]["clickedStream"]["nhdplusId"] == 999
    assert res["anchor"]["scoredReach"]["comid"] == 555


def test_resolve_far_raindrop_without_hr_is_covered_with_note(monkeypatch):
    # Wide-river case: the raindrop resolves, its snap point is far from the
    # input, and no HR line is near the point (the coordinates simply are not
    # on a mapped centerline). The historical engine accepted every raindrop
    # resolution; the anchor keeps that behavior and says so in a note.
    monkeypatch.setattr(routing, "_hydrolocation_snap",
                        lambda lat, lon: {"comid": 5218161,
                                          "snap_lat": lat + 0.001,
                                          "snap_lon": lon})
    monkeypatch.setattr(routing.nhd_hr, "hr_flowlines_in_bbox",
                        lambda *a, **k: None)
    monkeypatch.setattr(routing.nhd_hr, "nearest_point_on_hr_lines",
                        lambda fc, lat, lon: None)
    res = routing.resolve_anchor(39.955, -83.003)
    a = res["anchor"]
    assert a["anchorKind"] == "v2Direct"
    assert a["scoredReach"]["comid"] == 5218161
    assert a["scoredReach"]["snapDistFt"] > routing.HR_SNAP_TOL_FT
    assert any("drains to" in n for n in a["notes"])


def test_resolve_no_stream_anywhere(monkeypatch):
    monkeypatch.setattr(routing, "_hydrolocation_snap", lambda lat, lon: {})
    monkeypatch.setattr(routing.nhd_hr, "hr_flowlines_in_bbox",
                        lambda *a, **k: None)
    monkeypatch.setattr(routing.nhd_hr, "nearest_point_on_hr_lines",
                        lambda fc, lat, lon: None)
    assert routing.resolve_anchor(40.0, -83.0) == {"error": "no_stream_found"}

    # An HR line beyond the tolerance is still "no stream here".
    monkeypatch.setattr(routing.nhd_hr, "nearest_point_on_hr_lines",
                        lambda fc, lat, lon: (lat, lon, 500.0, 999))
    assert routing.resolve_anchor(40.0, -83.0) == {"error": "no_stream_found"}


def test_reanchor_noop_for_covered_anchors():
    assert routing.reanchor_inputs({"anchorKind": "v2Direct"}, 1000.0) == {}
    assert routing.reanchor_inputs(None, 1000.0) == {}


def test_reanchor_copies_hr_fields_including_unknowns(monkeypatch):
    # HR supplies slope/fcode/DA; sinuosity is unknown on the HR side and must
    # come through as None (the clicked stream's value is unknown, never the
    # surrogate's).
    monkeypatch.setattr(routing.nhd_hr, "hr_attrs",
                        lambda nid: {"gnis_name": None,
                                     "drainage_area_sqkm": 1.98,
                                     "huc8": "05060001", "slope": 0.024,
                                     "fcode": 46003, "stream_order": 1,
                                     "sinuosity": None})
    monkeypatch.setattr(routing.nhd_hr, "derive_reach_hr",
                        lambda nid, lat, lon, ft: (
                            {"type": "FeatureCollection", "features": [1]},
                            987.0, ["only 987 ft of mainstem available upstream"]))
    anchor = {"anchorKind": "hrSurrogate",
              "clickedStream": {"nhdplusId": 999, "snapLat": 40.31,
                                "snapLon": -83.05}}
    out = routing.reanchor_inputs(anchor, 1000.0)
    assert out["lat"] == 40.31 and out["lon"] == -83.05
    assert out["drainage_area_sqkm"] == 1.98
    assert out["slope"] == 0.024 and out["fcode"] == 46003
    assert out["sinuosity"] is None                     # unknown, not surrogate
    assert out["reach_geojson"] is not None
    assert out["reach_length_ft"] == 987.0
    assert "987 ft" in out["_warnings"][0]
    assert anchor["reanchored"]["applied"] is True


def test_reanchor_failure_degrades_to_phase1(monkeypatch):
    monkeypatch.setattr(routing.nhd_hr, "hr_attrs",
                        lambda nid: {"_hr_error": "no HR flowline for nhdplusid 999",
                                     "gnis_name": None, "drainage_area_sqkm": None,
                                     "huc8": None, "slope": None, "fcode": None,
                                     "stream_order": None, "sinuosity": None})
    anchor = {"anchorKind": "hrSurrogate",
              "clickedStream": {"nhdplusId": 999, "snapLat": 40.31,
                                "snapLon": -83.05}}
    assert routing.reanchor_inputs(anchor, 1000.0) == {}
    assert anchor["reanchored"]["applied"] is False
    assert "no HR flowline" in anchor["reanchored"]["warnings"][0]


def test_no_position_fallback_in_routing():
    # The nearest-position endpoint answers a different question; falling back
    # to it would make the routed reach depend on which service was up. The
    # docstring may explain the exclusion; a call is what must never appear.
    assert ".feature_byloc(" not in inspect.getsource(routing)


def test_the_two_lookups_overlap_and_the_payload_is_unchanged(monkeypatch):
    """The HR attributes and the NLDI raindrop run side by side (2026-09-02):
    two 0.2 s lookups finish in well under 0.4 s and the payload equals the
    sequential one."""
    import time

    def slow_rec(nid, **k):
        time.sleep(0.2)
        return _hr_rec()

    def slow_snap(lat, lon):
        time.sleep(0.2)
        return dict(_SNAP_OK)

    monkeypatch.setattr(routing.nhd_hr, "hr_flowline_by_id", slow_rec)
    monkeypatch.setattr(routing, "_hydrolocation_snap", slow_snap)
    monkeypatch.setattr(routing.delineation, "flowline_attrs", lambda comid: dict(_ATTRS_OK))
    t0 = time.monotonic()
    res = routing.route_from_hr(40.0, -83.0, _HR_SNAP)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.36, elapsed
    _stub(monkeypatch, snap=_SNAP_OK, attrs=_ATTRS_OK)
    assert res == routing.route_from_hr(40.0, -83.0, _HR_SNAP)
