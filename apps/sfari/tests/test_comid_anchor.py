"""The StreamCat reach for a SFARI site (sfari.comid_anchor, 2026-09-07): the
classification through the vendored engine's click rule, the labels that ride
every StreamCat value, and the map helpers. Offline: the engine is stubbed."""
from __future__ import annotations

from sfari import comid_anchor as ca

V2 = {"anchorKind": "v2Direct", "anchorSchemaVersion": 1,
      "scoredReach": {"network": "nhdplus-v2", "comid": 9327042, "gnisName": "Mink Brook",
                      "snapLat": 43.68582, "snapLon": -72.23667, "snapDistFt": 12.0}}
ROUTED = {"anchorKind": "hrSurrogate", "anchorSchemaVersion": 1,
          "clickedStream": {"network": "nhdplus-hr", "nhdplusId": 24000800011817,
                            "gnisName": None, "drainageAreaSqkm": 1.02,
                            "snapLat": 43.6858, "snapLon": -72.2367, "snapDistFt": 9.0},
          "scoredReach": {"network": "nhdplus-v2", "comid": 9327042, "gnisName": "Mink Brook",
                          "drainageAreaSqkm": 32.6, "snapLat": 43.6830, "snapLon": -72.2300},
          # declined past the 10x bound: SFARI reports the ratio and never enforces it
          "routing": {"routedDistanceFt": 1688.0, "daRatio": 32.0, "declined": True,
                      "declineCode": "da-ratio"}}


def test_comid_and_kind():
    assert ca.comid(V2) == 9327042 and not ca.is_routed(V2)
    assert ca.comid(ROUTED) == 9327042 and ca.is_routed(ROUTED)     # declined is ignored
    assert ca.comid(None) is None and ca.comid({"scoredReach": {"comid": "x"}}) is None


def test_labels_on_a_covered_reach():
    assert ca.source_label(V2) == "StreamCat lookup engine, COMID 9327042 (this reach)"
    assert ca.describes(V2) == ""
    assert ca.reach_text(V2) == "COMID 9327042 (this reach)"
    assert ca.snap_line(V2) == "StreamCat values come from this reach (COMID 9327042)."
    assert ca.basin_suffix(V2) == " (NHDPlus V2 basin, COMID 9327042)"
    assert ca.basin_note(V2) == ("Describes the NHDPlus V2 basin of COMID 9327042, the reach "
                                 "this point sits on, as EPA StreamCat summarized it.")
    assert ca.legend_reach(V2) == {"comid": 9327042, "name": "Mink Brook"}
    assert ca.route_segment(V2) is None


def test_labels_on_a_routed_reach_report_the_ratio():
    assert ca.describes(ROUTED) == ("nearest StreamCat reach Mink Brook (COMID 9327042), "
                                    "1,688 ft downstream, which drains 32 times this stream")
    assert ca.source_label(ROUTED) == ("StreamCat lookup engine, nearest StreamCat reach "
                                       "Mink Brook (COMID 9327042)")
    assert ca.reach_text(ROUTED) == "Mink Brook (COMID 9327042), 1,688 ft downstream"
    assert ca.snap_line(ROUTED) == ("StreamCat values come from Mink Brook (COMID 9327042), "
                                    "1,688 ft downstream, which drains 32 times this stream.")
    note = ca.basin_note(ROUTED)
    assert note.startswith("Describes the NHDPlus V2 basin of Mink Brook (COMID 9327042)")
    assert "1,688 ft away" in note and "32 times this stream" in note and note.endswith(".")
    seg = ca.route_segment(ROUTED)
    assert seg["features"][0]["geometry"]["coordinates"] == [[-72.2367, 43.6858], [-72.23, 43.683]]
    for text in (ca.describes(ROUTED), ca.source_label(ROUTED), ca.snap_line(ROUTED), note):
        assert "—" not in text and "limit" not in text and "declined" not in text


def test_ratio_formatting_and_empty_anchors():
    assert ca.fmt_ratio(32.0) == "32" and ca.fmt_ratio(2.66) == "2.7"
    assert ca.fmt_ratio(None) is None and ca.fmt_ratio(0) is None and ca.fmt_ratio("x") is None
    assert ca.reach_text(None) == "none found" and ca.source_label(None) == "StreamCat lookup engine"
    assert ca.describes(None) == "" and ca.snap_line(None) is None
    assert ca.legend_reach(None) is None and ca.route_segment(None) is None
    unnamed = {"anchorKind": "hrSurrogate", "scoredReach": {"comid": 7},
               "routing": {"routedDistanceFt": None, "daRatio": None}}
    assert ca.reach_text(unnamed) == "an unnamed reach (COMID 7)"
    assert ca.describes(unnamed) == "nearest StreamCat reach an unnamed reach (COMID 7)"


def test_synthetic_anchor_from_a_bare_comid():
    syn = ca.synthetic(5214461)
    assert syn["anchorKind"] == "v2Direct" and ca.comid(syn) == 5214461
    assert ca.synthetic(None) is None and ca.synthetic("x") is None


FC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"comid": 1, "gnis_name": "Sugar Run"},
     "geometry": {"type": "LineString", "coordinates": [[-83.06, 40.31], [-83.05, 40.31]]}}]}
HR_HIT = (40.3101, -83.055, 12.0, 750012345)


def test_resolve_uses_the_viewport_v2_line_and_names_the_reach(monkeypatch):
    calls = []

    def classify(lat, lon, *, v2_hit, hr_hit, snap_tol_ft):
        calls.append({"v2": v2_hit, "hr": hr_hit, "tol": snap_tol_ft})
        return {"anchor": {"anchorKind": "v2Direct",
                           "scoredReach": {"comid": v2_hit[3], "gnisName": None}}}
    monkeypatch.setattr(ca, "_classify", classify)
    monkeypatch.setattr(ca.flowlines, "flowlines_in_bbox", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("the viewport layer must be used")))
    # the engine's v2Direct payload leaves the reach attributes to the consumer;
    # resolve fills them from the fabric API so a covered continuation keeps them
    asked = []
    monkeypatch.setattr(ca, "_v2_reach_attrs", lambda c: asked.append(c) or {
        "gnis_name": "Sugar Run", "drainage_area_sqkm": 32.6, "slope": 0.0123, "fcode": 46006,
        "stream_order": 2, "huc8": "01080106", "reachcode": "01080106000123"})
    res = ca.resolve(40.3101, -83.055, HR_HIT, v2_fc=FC)
    assert calls[0]["v2"][3] == 1 and calls[0]["v2"][2] < 100
    assert calls[0]["hr"] == HR_HIT and calls[0]["tol"] == 150.0
    scored = res["anchor"]["scoredReach"]
    assert scored["gnisName"] == "Sugar Run" and asked == [1]
    assert scored["drainageAreaSqkm"] == 32.6 and scored["slope"] == 0.0123
    assert scored["fcode"] == 46006 and scored["streamOrder"] == 2
    assert scored["reachcode"] == "01080106000123"


def test_resolve_tolerates_a_fabric_outage_on_a_covered_reach(monkeypatch):
    monkeypatch.setattr(ca, "_classify", lambda lat, lon, **k: {"anchor": {
        "anchorKind": "v2Direct", "scoredReach": {"comid": 1, "gnisName": None,
                                                  "drainageAreaSqkm": None}}})
    monkeypatch.setattr(ca, "_v2_reach_attrs", lambda c: {})
    res = ca.resolve(40.3101, -83.055, HR_HIT, v2_fc=FC)
    scored = res["anchor"]["scoredReach"]
    assert scored["gnisName"] == "Sugar Run"            # the viewport feature still names it
    assert scored["drainageAreaSqkm"] is None and "slope" not in scored


def test_resolve_fetches_the_v2_lines_when_the_viewport_has_none(monkeypatch):
    fetched = []
    monkeypatch.setattr(ca.flowlines, "flowlines_in_bbox",
                        lambda w, s, e, n: fetched.append((w, s, e, n)) or FC)
    monkeypatch.setattr(ca, "_classify", lambda lat, lon, **k: {"anchor": {
        "anchorKind": "hrSurrogate", "scoredReach": {"comid": 9, "gnisName": "X"},
        "routing": {"routedDistanceFt": 100.0, "daRatio": 1.2, "declined": False}}})
    # a routed payload already carries the reach's attributes: no fabric call
    monkeypatch.setattr(ca, "_v2_reach_attrs", lambda c: (_ for _ in ()).throw(
        AssertionError("never asked on a routed reach")))
    res = ca.resolve(40.3101, -83.055, HR_HIT, v2_fc=None)
    assert fetched and abs(fetched[0][0] - (-83.055 - 0.012)) < 1e-9
    assert res["anchor"]["anchorKind"] == "hrSurrogate"


def test_resolve_never_raises(monkeypatch):
    monkeypatch.setattr(ca.flowlines, "flowlines_in_bbox", lambda *a, **k: None)

    def boom(*a, **k):
        raise RuntimeError("NLDI down")
    monkeypatch.setattr(ca, "_classify", boom)
    res = ca.resolve(40.3, -83.0, HR_HIT)
    assert res == {"error": "snap_service_error", "detail": "NLDI down"}
    monkeypatch.setattr(ca, "_classify", lambda *a, **k: None)
    assert ca.resolve(40.3, -83.0, HR_HIT) == {"error": "no_stream_found"}
