"""Flagged transfers (StreamCurves methodology 0.16, REF-16, owner decision D11): the
metric's limitation line, the function's line, the report's count, and the flagged
function's place in the ecosystem condition index. Offline, synthetic bundles."""
from __future__ import annotations

from deep import assessments, curves, measure, reference_support as rs, report

ASC = [{"x": 0, "y": 0}, {"x": 10, "y": 1}]
VALIDATION = {"basis": "2r_l1", "evidence": "metric", "accepted": False, "verdict": "failed",
              "a1_share": 0.56, "net_opt": 0.09, "n_cells": 6, "min_cells": 4,
              "min_share": 0.6667, "max_net_optimism": 0.05,
              "why": "In the recovery test the source's class calls did not agree with reference often enough."}
LIMITATION = ("Scored on a reference pool whose transfer to this ecoregion was not confirmed by "
              "the recovery test (class agreement 0.56 of evaluation regions; two thirds required; "
              "net optimism +0.09, at most 0.05 allowed); confidence capped at 39.")
NOTE = ("40 stations passing the regional least-disturbed screen from Level I 8 (Eastern Temperate "
        "Forests), 7 of them inside this ecoregion. Flagged transfer: the recovery test did not "
        "confirm this source for this ecoregion.")
EM_DASH = chr(0x2014)


def _flagged(mid="spring-bent-ept-ntax", name="EPT taxa richness", at_entry=True):
    support = {"status": "borrowed_l1", "level": "l1", "regionCode": "8",
               "regionName": "Eastern Temperate Forests",
               "screen": "least-disturbed-regional-v1 (relaxed tier, watershed agriculture at most 25 percent)",
               "screenId": "least-disturbed-regional-v1", "agricultureLimit": 25.0,
               "nUsable": 40, "nLocal": 7, "transferRisk": "unvalidated", "transferNote": NOTE,
               "transferValidation": VALIDATION, "confidenceCap": 39, "rule": "REF-16",
               "basis": "regional-reference", "basisLabel": "Regional reference"}
    m = {"metricId": mid, "metricName": name, "criteriaBasis": "reference",
         "referenceSupport": support, "referenceN": 40, "sampleDisposition": "adequate",
         "confidenceLabel": "Low", "confidenceTotal": 39.0,
         "curveCaveats": ["The reference stations for this curve were borrowed from Level I "
                          "ecoregion 8 (Eastern Temperate Forests) because this ecoregion has too "
                          "few least-disturbed stations of its own.", LIMITATION],
         "curve": {"points": ASC}}
    if at_entry:
        m.update({"transferRisk": "unvalidated", "transferValidation": VALIDATION,
                  "transferNote": NOTE, "confidenceCap": 39})
    return m


def _plain(mid="spring-chem-cond", name="Conductivity"):
    return {"metricId": mid, "metricName": name, "criteriaBasis": "reference",
            "referenceSupport": {"status": "local", "level": "l3", "regionCode": "58",
                                 "regionName": "Northeastern Highlands",
                                 "screen": "least-disturbed-v1 (strict), wadeable non-canal frame",
                                 "nUsable": 66, "nLocal": 66, "transferRisk": "none", "transferNote": ""},
            "referenceN": 66, "sampleDisposition": "adequate", "confidenceLabel": "Moderate",
            "curve": {"points": ASC}}


def _bundle():
    return {"assessmentId": "flagged-test", "assessmentName": "Flagged transfer test",
            "metricsByFunction": [
                {"functionId": "community-dynamics", "functionName": "Community dynamics",
                 "discipline": "Biology", "metrics": [_flagged()]},
                {"functionId": "water-soil-quality", "functionName": "Water & soil quality",
                 "discipline": "Physicochemistry", "metrics": [_plain()]}],
            "functionCoverage": {"framework": "staf-20", "total": 20, "covered": 2, "excluded": 0,
                                 "missing": 18,
                                 "coveredFunctionIds": ["community-dynamics", "water-soil-quality"],
                                 "missingFunctionIds": [], "exclusions": []}}


def test_the_flag_line_says_what_the_test_found_and_that_confidence_is_capped():
    m = _flagged()
    assert rs.is_flagged(m) and rs.confidence_cap(m) == 39
    assert rs.transfer_validation(m)["a1_share"] == 0.56
    assert rs.flag_line(m) == LIMITATION
    # the limitation reads once, whether the bundle already carries it as a caveat or not
    assert rs.limitations_line(m).count(LIMITATION) == 1
    bare = dict(m, curveCaveats=[])
    assert rs.limitations_line(bare) == LIMITATION
    assert rs.practitioner_lines(m)[-1].endswith(LIMITATION)
    # the support line spells the risk in words
    assert rs.support_line(m).endswith("Transfer risk unvalidated (transfer not confirmed by the recovery test)")
    # a flag carried only inside referenceSupport (an older exporter shape) still reads
    inside = _flagged(at_entry=False)
    assert rs.is_flagged(inside) and rs.flag_line(inside) == LIMITATION
    # no numbers: the words say the test holds no evidence
    none = dict(m, transferValidation={"basis": "2r_l1", "evidence": "none"})
    none["referenceSupport"] = dict(m["referenceSupport"], transferValidation={})
    line = rs.flag_line(none)
    assert line.startswith("Scored on a reference pool whose transfer to this ecoregion was not confirmed")
    assert "no recovery evidence for this source and metric" in line and line.endswith("confidence capped at 39.")
    # no cap recorded: the sentence still says capped
    uncapped = dict(m, confidenceCap=None)
    uncapped["referenceSupport"] = dict(m["referenceSupport"], confidenceCap=None)
    assert rs.flag_line(uncapped).endswith("; confidence capped.")
    # nothing on a validated curve
    assert not rs.is_flagged(_plain()) and rs.flag_line(_plain()) == "" and rs.flag_line(None) == ""
    for text in (rs.flag_line(m), rs.support_line(m), rs.limitations_line(m)):
        assert EM_DASH not in text


def test_the_function_line_and_the_report_count():
    la = assessments.LoadedAssessment.from_dict(_bundle())
    assert rs.flagged_functions(la) == [{"functionId": "community-dynamics",
                                        "functionName": "Community dynamics",
                                        "metrics": ["EPT taxa richness"]}]
    line = rs.function_flag_line(la, "community-dynamics")
    assert line == ("This function is rated on a curve whose reference pool transfer to this ecoregion "
                    "was not confirmed by the recovery test (EPT taxa richness); the score enters the "
                    "index with confidence capped.")
    assert rs.function_flag_line(la, "water-soil-quality") == ""
    assert rs.flagged_summary(la) == (
        "1 of the 2 rated functions rest on a reference pool whose transfer to this ecoregion was "
        "not confirmed by the recovery test (Community dynamics); each is rated with confidence "
        "capped and flagged on its metric row.")
    clean = _bundle()
    clean["metricsByFunction"][0]["metrics"] = [_plain("spring-bent-ept-ntax", "EPT taxa richness")]
    assert rs.flagged_functions(clean) == [] and rs.flagged_summary(clean) == ""
    for text in (line, rs.flagged_summary(la)):
        assert EM_DASH not in text


def test_the_flagged_function_is_rated_and_enters_the_index():
    la = assessments.LoadedAssessment.from_dict(_bundle())
    state = {"spring-bent-ept-ntax": {"value": 8.0, "na": False, "note": ""},
             "spring-chem-cond": {"value": 5.0, "na": False, "note": ""}}
    sc, fres = curves.score_site(la, measure.measured_from_state(state))
    assert fres["community-dynamics"].score is not None and not fres["community-dynamics"].na
    assert fres["community-dynamics"].metric_indices["spring-bent-ept-ntax"] == 0.8
    assert "community-dynamics" in sc["functionScores"]
    # the flag is a limitation, not a gap: the function is not among the unassessed
    assert "community-dynamics" not in [f["functionId"] for f in rs.unassessed_functions(la)]
    assert sc["nUnassessed"] == 18


def test_the_report_prints_the_count_and_the_function_limitation():
    la = assessments.LoadedAssessment.from_dict(_bundle())
    state = {"spring-bent-ept-ntax": {"value": 8.0, "na": False, "note": ""},
             "spring-chem-cond": {"value": 5.0, "na": False, "note": ""}}
    sc, _ = curves.score_site(la, measure.measured_from_state(state))
    txt = report.build_csv({}, la, state, sc)
    assert "Flagged transfers" in txt and "1 of the 2 rated functions rest on a reference pool" in txt
    assert "Function,Function score (0-15),Condition,Limitation" in txt
    assert "This function is rated on a curve whose reference pool transfer" in txt
    assert "Transfer risk unvalidated (transfer not confirmed by the recovery test)" in txt
    assert EM_DASH not in txt
    pairs = dict(report._header_pairs({}, la, sc, None, state))
    assert pairs["Flagged transfers"].startswith("1 of the 2 rated functions")
    # a bundle with no flag says none
    clean = _bundle()
    clean["metricsByFunction"][0]["metrics"] = [_plain("spring-bent-ept-ntax", "EPT taxa richness")]
    la2 = assessments.LoadedAssessment.from_dict(clean)
    sc2, _ = curves.score_site(la2, measure.measured_from_state(state))
    assert dict(report._header_pairs({}, la2, sc2, None, state))["Flagged transfers"] == "none"
    pdf = report.build_pdf({}, la, state, sc)
    assert pdf[:5] == b"%PDF-"
