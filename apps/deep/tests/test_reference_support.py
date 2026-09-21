"""What DEEP reads from a bundle built under StreamCurves methodology 0.12.

Fixed-criteria metrics score engine values (the pairing rule has nothing to
protect there), every reference curve says where its stations came from, a
metric with no defensible pool is shown and never scored, the curve set follows
the reach's own slope or drainage area, and the three new desktop adapters
compute the quantities EASI's criteria are written on. Every bundle published
before 0.12 reads exactly as it did. Fully offline.
"""
from __future__ import annotations

import sys
import types

import pytest

from deep import assessments, config, curves, measure, reference_support as rs
from deep.metrics import computed
from deep.metrics.base import AnalysisContext
from deep.models import MeasuredValue

FIXED_POINTS = [{"x": 0.0, "y": 1.0}, {"x": 10.0, "y": 0.69},
                {"x": 25.005, "y": 0.39}, {"x": 44.5115, "y": 0.0}]
ASC = [{"x": 0, "y": 0}, {"x": 10, "y": 1}]


def _fixed(mid="spring-pctimp2019ws"):
    return {"metricId": mid, "metricName": "Impervious surface", "criteriaBasis": "fixed",
            "confidenceLabel": "Fixed criteria", "metricRole": "pressure_fixed",
            "criteriaSource": {"title": "Catchment hydrology (land-cover pressure)",
                               "bands": [{"rating": "Good", "label": "<10%"},
                                         {"rating": "Fair", "label": "10-25%"},
                                         {"rating": "Poor", "label": ">25%"}],
                               "citations": [{"key": "schueler-1994", "text": "Schueler 1994"}]},
            "curve": {"points": FIXED_POINTS}}


def _reference(status="local", **sup):
    support = {"status": status, "level": "l3", "regionCode": "58",
               "regionName": "Northeastern Highlands",
               "screen": "least-disturbed-v1 (strict), wadeable non-canal frame",
               "nUsable": 66, "nLocal": 66, "transferRisk": "none", "transferNote": ""}
    support.update(sup)
    return {"metricId": "spring-chem-cond", "metricName": "Conductivity",
            "criteriaBasis": "reference", "referenceSupport": support,
            "curve": {"points": ASC}}


# --------------------------------------------------------------------------- #
# fixed criteria and the pairing rule
# --------------------------------------------------------------------------- #
def test_an_engine_value_scores_against_fixed_criteria():
    engine_value = MeasuredValue(metric_id="spring-pctimp2019ws", value=2.0,
                                 origin="desktop", engine=True)
    assert curves.engine_pairing_advisory(engine_value, _fixed()) is None
    assert curves.metric_index(engine_value, _fixed()) == pytest.approx(0.938, abs=1e-3)
    # the same value against a StreamCat-fitted reference curve is still withheld
    fitted = {"metricId": "spring-pctimp2019ws", "curve": {"points": ASC}}
    assert curves.metric_index(engine_value, fitted) is None
    assert "reference only" in curves.engine_pairing_advisory(engine_value, fitted)


def test_the_engine_gate_opens_for_fixed_metrics_only():
    bundle = {"assessmentId": "t", "metricsByFunction": [
        {"functionId": "catchment-hydrology", "metrics": [_fixed()]},
        {"functionId": "water-soil-quality", "metrics": [_reference()]}]}
    gate = assessments.engine_gate_by_metric(bundle)
    assert gate == {"spring-pctimp2019ws": True, "spring-chem-cond": False}


def test_a_value_past_the_last_fixed_point_is_not_a_domain_warning():
    far = MeasuredValue(metric_id="spring-pctimp2019ws", value=85.0)
    assert curves.metric_index(far, _fixed()) == 0.0
    assert curves.metric_warning(far, _fixed()) is None
    fitted = {"metricId": "m", "curve": {"points": ASC}}
    assert "above the curve domain" in curves.metric_warning(
        MeasuredValue(metric_id="m", value=85.0), fitted)


def test_boundary_values_land_in_the_class_easi_gives_them():
    # EASI: 10 percent impervious is Fair, 25 percent is still Fair
    from deep import scoring
    spec = _fixed()
    at_10 = curves.metric_index(MeasuredValue(metric_id="x", value=10.0), spec)
    at_25 = curves.metric_index(MeasuredValue(metric_id="x", value=25.0), spec)
    just_over = curves.metric_index(MeasuredValue(metric_id="x", value=25.01), spec)
    assert scoring.index_band_label(at_10) == "Functioning-at-Risk"
    assert scoring.index_band_label(at_25) == "Functioning-at-Risk"
    assert scoring.index_band_label(just_over) == "Non-Functioning"
    assert scoring.index_band_label(
        curves.metric_index(MeasuredValue(metric_id="x", value=9.99), spec)) == "Functioning"


# --------------------------------------------------------------------------- #
# the reference statement
# --------------------------------------------------------------------------- #
def test_the_support_line_says_what_the_metric_is_scored_against():
    assert rs.support_line(_fixed()) == "Fixed criteria, the same in every region"
    assert rs.support_line(_reference()) == (
        "Reference curve from 66 least-disturbed stations of this ecoregion")
    borrowed = _reference("borrowed_l1", level="l1", regionCode="8",
                          regionName="Eastern Temperate Forests", nUsable=26, nLocal=3,
                          transferRisk="moderate", transferNote="Borrowed and matched.")
    line = rs.support_line(borrowed)
    assert "26 least-disturbed stations borrowed from Level I ecoregion 8" in line
    assert "(Eastern Temperate Forests), 3 of them in this ecoregion" in line
    assert line.endswith("Transfer risk moderate")
    assert rs.is_borrowed(borrowed) and not rs.is_borrowed(_reference())
    assert "Borrowed and matched." in rs.tip_lines(borrowed)


def test_a_bundle_that_says_nothing_reads_as_nothing():
    legacy = {"metricId": "m", "referenceN": 94, "referenceTier": "least_disturbed",
              "curve": {"points": ASC}}
    assert rs.support_line(legacy) == "" and rs.tip_lines(legacy) == []
    assert rs.comparison_line(legacy) == "" and rs.discrimination_line(legacy) == ""
    assert rs.auto_stratum(legacy, {"delineation": {"slope": 0.01}}) is None
    assert rs.withheld({"metricsByFunction": []}) == []


def test_the_local_comparison_is_text_and_labeled():
    m = _reference()
    m["localComparison"] = {"label": "Local best-available comparison (not reference)",
                            "n": 98, "q25": 31.0, "q50": 58.5, "q75": 110.0}
    line = rs.comparison_line(m)
    assert line.startswith("Local best-available comparison (not reference):")
    assert "98 stations" in line and "31 to 110" in line and "median 58.5" in line


def test_the_fixed_tip_lists_the_criteria_and_their_sources():
    lines = rs.tip_lines(_fixed())
    assert lines[0] == "Fixed criteria, the same in every region"
    assert "Good <10%" in lines and "Poor >25%" in lines
    assert lines[-1] == "Sources: Schueler 1994"


def test_withheld_metrics_are_found_by_function():
    bundle = {"assessmentId": "t",
              "metricsByFunction": [{"functionId": "water-soil-quality",
                                     "functionName": "Water & soil quality",
                                     "metrics": [_reference()]}],
              "insufficientReferenceSupport": [
                  {"metricId": "spring-chem-ntl-diss", "metricName": "Dissolved nitrogen",
                   "functions": [{"functionId": "nutrient-cycling",
                                  "functionName": "Nutrient cycling"}],
                   "statement": "Insufficient reference support."},
                  {"metricId": "spring-chem-turb", "metricName": "Turbidity",
                   "functions": [{"functionId": "water-soil-quality",
                                  "functionName": "Water & soil quality"}]}]}
    la = assessments.LoadedAssessment.from_dict(bundle)
    assert [w["metricId"] for w in rs.withheld_for_function(la, "water-soil-quality")] \
        == ["spring-chem-turb"]
    only = rs.withheld_only_functions(la)
    assert [f["functionId"] for f in only] == ["nutrient-cycling"]
    assert only[0]["metrics"][0]["metricName"] == "Dissolved nitrogen"
    # a withheld metric has no curve, and the bundle still validates and scores
    assert assessments.validate_bundle(bundle) == []
    sc, _ = curves.score_site(la, {})
    assert "nutrient-cycling" not in sc.get("functionScores", {})


# --------------------------------------------------------------------------- #
# the curve set follows the reach
# --------------------------------------------------------------------------- #
def _stratified(variable="nhd_slope", breaks=(0.005, 0.02), right=False, have=(0, 2)):
    labels = ["Low gradient (under 0.5 percent)", "Moderate gradient (0.5 to 2 percent)",
              "Steep (2 percent and above)"]
    if variable == "drainage_area_sqkm":
        labels = ["Headwater (10 km2 and under)", "Small stream (10 to 100 km2)",
                  "Large stream (over 100 km2)"]
    keys = ["lt_0.5", "0.5_to_2", "ge_2"]
    if variable == "drainage_area_sqkm":
        keys = ["le_10", "10_to_100", "gt_100"]
    # a layer is named by its class key; the stratifier block carries the words
    layers = [{"stratum": "", "points": ASC}] + [
        {"stratum": keys[i], "points": [{"x": 0, "y": 0}, {"x": 5 + i, "y": 1}]}
        for i in have]
    return {"metricId": "spring-phab-xembed", "metricName": "Embeddedness",
            "curve": {"points": ASC}, "curveLayers": layers, "activeStratum": "",
            "stratifier": {"variable": variable, "breaks": list(breaks), "right": right,
                           "classes": [{"key": keys[i], "label": lab, "hasCurve": i in have}
                                       for i, lab in enumerate(labels)]}}


def test_the_reach_slope_picks_the_curve_set():
    m = _stratified()
    pick = lambda slope: rs.auto_stratum(m, {"delineation": {"slope": slope}})  # noqa: E731
    assert pick(0.001) == "lt_0.5"
    assert pick(0.03) == "ge_2"
    # a break belongs to the class above it, as the builder classed the stations
    assert pick(0.02) == "ge_2"
    assert pick(0.005) == ""            # the moderate class has no curve: pooled
    assert pick(None) == "" and pick("n/a") == ""
    # the chooser shows words, never the key
    assert rs.stratum_label("ge_2", m) == "Steep (2 percent and above)"
    assert rs.stratum_label("", m) == rs.POOLED_LABEL
    note = rs.stratifier_note(m, {"delineation": {"slope": 0.005}})
    assert "Moderate gradient (0.5 to 2 percent)" in note and "pooled curve applies" in note
    assert rs.stratifier_note(m, {"delineation": {"slope": 0.03}})         == "Chosen from the reach's NHDPlus slope (3.00 percent)"


def test_the_drainage_area_break_belongs_to_the_class_below():
    m = _stratified("drainage_area_sqkm", (10.0, 100.0), True, have=(0, 1, 2))
    pick = lambda da: rs.auto_stratum(m, {"delineation": {"drainage_area_sqkm": da}})  # noqa: E731
    assert pick(10.0) == "le_10"
    assert pick(10.01) == "10_to_100"
    assert pick(100.0) == "10_to_100"
    assert pick(250.0) == "gt_100"
    assert pick(0.0) == ""


def test_the_chosen_stratum_scores_on_its_own_layer():
    m = _stratified()
    label = rs.auto_stratum(m, {"delineation": {"slope": 0.03}})
    steep = measure.measured_from_state({m["metricId"]: {"value": 3.5, "stratum": label}})
    pooled = measure.measured_from_state({m["metricId"]: {"value": 3.5, "stratum": ""}})
    assert curves.metric_index(steep[m["metricId"]], m) == pytest.approx(0.5)     # x max 7
    assert curves.metric_index(pooled[m["metricId"]], m) == pytest.approx(0.35)   # x max 10
    assert label == "ge_2"
    assert rs.stratum_label("") == rs.POOLED_LABEL
    assert rs.stratum_label(label, m) == "Steep (2 percent and above)"
    assert rs.auto_strata({"metricsByFunction": [{"metrics": [m, _fixed()]}]},
                          {"delineation": {"slope": 0.03}}) == {m["metricId"]: label}


# --------------------------------------------------------------------------- #
# the three adapters
# --------------------------------------------------------------------------- #
def _ctx(allow_engine=False, record=None, **extras):
    ctx = AnalysisContext(lat=40.31125, lon=-83.05615, comid=5214461)
    ctx.extras["allow_engine"] = allow_engine
    if record:
        ctx.extras["site_engine_prefetched"] = record
    ctx.extras.update(extras)
    return ctx


def _sc(monkeypatch, row):
    monkeypatch.setitem(computed.__dict__, "_streamcat", lambda ctx: dict(row))


def test_agriculture_sums_crop_and_hay_on_each_layer(monkeypatch):
    record = {"status": "ok", "engineVersion": "0.4.1",
              "metrics": {"cropPctWatershed": {"value": 40.0},
                          "hayPasturePctWatershed": {"value": 5.5}}}
    _sc(monkeypatch, {"pctcrop2019ws": 75.32, "pcthay2019ws": 8.3})
    out = computed.compute_for(["spring-pctag2019ws"], _ctx(True, record))
    cv = out["spring-pctag2019ws"]
    assert cv.value == 45.5 and cv.engine is True and cv.basis == "site-engine"
    out = computed.compute_for(["spring-pctag2019ws"], _ctx(False, record))
    cv = out["spring-pctag2019ws"]
    assert cv.value == 83.62 and cv.engine is False and cv.basis == "streamcat"
    assert "pctcrop2019 plus pcthay2019" in cv.source


def test_agriculture_needs_both_classes(monkeypatch):
    _sc(monkeypatch, {"pctcrop2019ws": 75.32})                 # hay unpublished
    monkeypatch.setitem(computed.__dict__, "_landcover", lambda ctx: {"ag_pct": 81.0})
    cv = computed.compute_for(["spring-pctag2019ws"], _ctx())["spring-pctag2019ws"]
    assert cv.value == 81.0 and cv.basis == "nlcd"
    monkeypatch.setitem(computed.__dict__, "_landcover", lambda ctx: {})
    assert computed.compute_for(["spring-pctag2019ws"], _ctx()) == {}


def test_degree_of_regulation_is_easis_formula(monkeypatch):
    # 12,000 m3 of normal storage per km2 over 400 mm of runoff = 3 percent
    _sc(monkeypatch, {"damnrmstorws": 12000.0, "runoffws": 400.0})
    cv = computed.compute_for(["spring-dorws"], _ctx(True))["spring-dorws"]
    assert cv.value == 3.0 and cv.basis == "streamcat" and cv.engine is False
    _sc(monkeypatch, {"damnrmstorws": 0.0, "runoffws": 400.0})
    assert computed.compute_for(["spring-dorws"], _ctx())["spring-dorws"].value == 0.0
    for row in ({"damnrmstorws": 12000.0}, {"damnrmstorws": 12000.0, "runoffws": 0.0}, {}):
        _sc(monkeypatch, row)
        assert computed.compute_for(["spring-dorws"], _ctx()) == {}


def _stub_nid(monkeypatch, result):
    import deep.datasources as pkg
    stub = types.SimpleNamespace(barriers_near=lambda lat, lon, miles=1.0, timeout=10.0: result)
    monkeypatch.setitem(sys.modules, "deep.datasources.nid_barriers", stub)
    monkeypatch.setattr(pkg, "nid_barriers", stub, raising=False)


def test_nearby_dams_is_a_count_and_zero_is_a_value(monkeypatch):
    _stub_nid(monkeypatch, [{"name": "A", "distance_m": 410.0}, {"name": "B", "distance_m": 900.0}])
    cv = computed.compute_for(["spring-nid-dams-1mi"], _ctx())["spring-nid-dams-1mi"]
    assert cv.value == 2.0 and cv.basis == "nid" and cv.engine is False
    _stub_nid(monkeypatch, [])
    assert computed.compute_for(["spring-nid-dams-1mi"], _ctx())["spring-nid-dams-1mi"].value == 0.0
    _stub_nid(monkeypatch, None)                                  # the service is down
    assert computed.compute_for(["spring-nid-dams-1mi"], _ctx()) == {}


def test_the_dam_query_measures_a_geodesic_radius(monkeypatch):
    from deep.datasources import nid_barriers
    seen = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"features": [
                {"attributes": {"NAME": "Near"}, "geometry": {"x": -83.0500, "y": 40.3100}},
                # inside the service's buffer box, outside one mile
                {"attributes": {"NAME": "Far"}, "geometry": {"x": -83.0300, "y": 40.3300}}]}

    def fake_get(url, params=None, timeout=None):
        seen.update(params)
        return _Resp()
    monkeypatch.setattr(nid_barriers.requests, "get", fake_get)
    got = nid_barriers.barriers_near(40.31125, -83.05615, miles=1.0)
    assert [d["name"] for d in got] == ["Near"]
    assert seen["geometryType"] == "esriGeometryPoint"
    assert seen["distance"] == pytest.approx(1609.344)
    monkeypatch.setattr(nid_barriers.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert nid_barriers.barriers_near(40.3, -83.0) is None


def test_the_new_ids_are_registered():
    assert {"spring-pctag2019ws", "spring-dorws", "spring-nid-dams-1mi"} \
        <= computed.computable_ids()


# --------------------------------------------------------------------------- #
# every bundle published before 0.12 still reads and scores as it did
# --------------------------------------------------------------------------- #
def _all_baked():
    seen, out = set(), []
    for rec in list(config.assessments()) + list(config._registry_records()):
        key = rec.get("assessmentRef") or rec.get("assessmentId")
        if key not in seen:
            seen.add(key)
            out.append(rec)
    return out


@pytest.mark.parametrize("bundle", _all_baked(),
                         ids=lambda b: b.get("assessmentRef") or b.get("assessmentId"))
def test_a_published_bundle_still_validates_and_scores(bundle):
    assert assessments.validate_bundle(bundle) == []
    la = assessments.LoadedAssessment.from_dict(bundle)
    state = {}
    for m in la.all_metrics():
        pts = curves.active_points(m)
        xs = sorted(float(p["x"]) for p in pts)
        state[m["metricId"]] = {"value": xs[len(xs) // 2], "na": False}
    sc, fresults = curves.score_site(la, measure.measured_from_state(state))
    # A point index exactly when the bundle leaves nothing unassessed. Otherwise the
    # claim is the interval, which is what stops a partial assessment reading as
    # comparable to a complete one.
    if sc["nUnassessed"]:
        assert sc["ecosystemConditionIndex"] is None
        low, high = sc["ecosystemConditionIndexBounds"]
        assert 0.0 <= low <= high <= 1.0
    else:
        assert 0.0 <= sc["ecosystemConditionIndex"] <= 1.0
    assert all(fr.score is not None for fr in fresults.values())
    assert rs.withheld(la) == [] or "insufficientReferenceSupport" in la.raw
    assert rs.auto_strata(la, {"delineation": {"slope": 0.01, "drainage_area_sqkm": 50}}) == {} \
        or any("stratifier" in m for m in la.all_metrics())
    for m in la.all_metrics():
        if "criteriaBasis" not in m:
            assert rs.support_line(m) == "" and rs.tip_lines(m) == []
