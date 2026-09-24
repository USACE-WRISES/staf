"""State SQT curves after the second review of Phase 6 (review C, round 2).

The owner's decision 1: a curve its source leaves open at an end is a candidate with a note,
completed by the author with points to the index limit (keeping its direction, with initials
and a reason) before it can be selected, and the published curve names the points the SQT
publishes and the ones added. The source a selection records is rebuilt from the frozen record
alone. Any stratum but Default is a restriction. The picker checks each record as adding does.
Every rule reads as words. The REF-15 extension stays off; nothing here changes what a
published version scores (``test_ref15_extension``).
"""
from __future__ import annotations

import copy
import json

import pytest

from streamcurves import candidates as C
from streamcurves import owner_curves as oc

LAND = "sqt:ak:anthropogenic-land-cover:default"       # decreasing, open high end: 50 scores 0.3
DIATOM = "sqt:ak:diatom-index:default"                  # increasing, open low end: 58 scores 0.7
MI_FISH = "sqt:mi:fish:p51-wadeable-and-warm-and-warm-transitional-water"
NC_COND = "sqt:nc:specific-conductivity:piedmont"
WHY = "The source stops at 50 percent; past it the index keeps falling to zero at 80."


def _reg():
    from streamcurves import sqt_registry
    if not sqt_registry.available():
        pytest.skip("the SQT registry is not built")
    return sqt_registry


def _cand(key: str, **ctx):
    reg = _reg()
    rec = reg.record(key)
    fid = rec["function"]["id"]
    return C.sqt_candidate(rec, function_id=fid, region={"code": "0"},
                           context={"function": fid, "states": [rec["state"]], "scoreScale": "staf", **ctx})


def _considered(cand):
    return C.add_considered(None, cand, by="AB", at="2026-09-24T00:00:00Z")


# --------------------------------------------------------------------------- #
# completing an open end
# --------------------------------------------------------------------------- #
DOWN = [(78.0, 0.69), (229.0, 0.3), (342.9, 0.0)]
LOW_OPEN = [{"side": "low", "x": 78.0, "y": 0.69}]


@pytest.mark.parametrize("added,said", [
    ([], "Add at least one point past the low end"),
    ([{"x": 400, "y": 0.0, "side": "high"}], "The high end is not open"),
    ([{"x": 80, "y": 1.0, "side": "low"}], "lies below 78"),
    ([{"x": 40, "y": 1.2, "side": "low"}], "between 0 and 1"),
    ([{"x": 40, "y": 1.0, "side": "low"}, {"x": 40, "y": 1.0, "side": "low"}], "its own value"),
    ([{"x": -5, "y": 1.0, "side": "low"}], "below 0"),
    ([{"x": 60, "y": 0.5, "side": "low"}, {"x": 30, "y": 1.0, "side": "low"}], "keep its direction"),
    ([{"x": 40, "y": 0.9, "side": "low"}], "reaches index 1"),
    ([{"x": None, "y": 1.0, "side": "low"}], "needs a number"),
    ([{"x": 40, "y": 1.0, "side": "middle"}], "low end or the high end"),
])
def test_a_completion_is_refused_in_words(added, said):
    problems = C.check_completion(DOWN, LOW_OPEN, "decreasing", added)
    assert problems and any(said in p for p in problems), problems


def test_a_completion_that_reaches_the_limit_in_the_curves_direction_is_accepted():
    assert C.check_completion(DOWN, LOW_OPEN, "decreasing", [{"x": 40, "y": 1.0, "side": "low"}]) == []
    assert C.check_completion(DOWN, LOW_OPEN, "decreasing", [{"x": 60, "y": 0.85, "side": "low"},
                                                              {"x": 30, "y": 1.0, "side": "low"}]) == []
    # no stated direction: the published points' own
    assert C.check_completion(DOWN, LOW_OPEN, None, [{"x": 40, "y": 1.0, "side": "low"}]) == []
    flat = [(1.0, 0.5), (2.0, 0.5)]
    assert "flat" in C.check_completion(flat, [{"side": "low", "x": 1.0, "y": 0.5}], None,
                                        [{"x": 0.5, "y": 0.0, "side": "low"}])[0]
    assert "two-sided" in C.check_completion(DOWN, LOW_OPEN, "two-sided", [{"x": 40, "y": 1.0, "side": "low"}])[0]
    assert "no open end" in C.check_completion(DOWN, [], "decreasing", [])[0]


def test_an_open_curve_is_a_candidate_to_complete_whatever_the_target_holds():
    cand = _cand(LAND)
    ends = next(c for c in cand["eligibility"]["checks"] if c["id"] == "past-ends")
    assert cand["eligibility"]["status"] == "eligible" and cand["needsCompletion"]
    assert ends["status"] == "warn" and "high end (50 scores 0.3)" in ends["detail"]
    assert not any(c["id"] == "extrapolation" for c in cand["eligibility"]["checks"])
    # the register asks for the completion, and Select waits for it
    view = C.deep_register(tiles=[], build={"portfolioSelection": {}}, register=_considered(cand))
    row = next(r for r in view["rows"] if r["candidateKey"] == cand["candidateKey"])
    assert row["unresolved"] == "complete the curve" and "complete the curve" in row["decision"]["reason"]


def test_completing_a_curve_records_what_was_added_by_whom_and_why():
    cand = _cand(LAND)
    reg = C.complete_curve(_considered(cand), cand["candidateKey"], [{"x": 80, "y": 0.0, "side": "high"}],
                           by="AB", reason=WHY, at="2026-09-24T01:00:00Z")
    done = reg["considered"][0]
    assert not done["needsCompletion"]
    assert [(p["x"], p["y"]) for p in done["definition"]["points"]] == [(0.0, 1.0), (35.0, 0.7), (50.0, 0.3),
                                                                        (80.0, 0.0)]
    assert [(p["x"], p["y"]) for p in done["definition"]["publishedPoints"]] == [(0.0, 1.0), (35.0, 0.7),
                                                                                 (50.0, 0.3)]
    assert done["definition"]["addedPoints"] == [{"x": 80.0, "y": 0.0, "side": "high"}]
    assert done["completion"]["by"] == "AB" and done["completion"]["reason"] == WHY
    assert done["basisDigest"] != cand["basisDigest"]
    ends = next(c for c in done["eligibility"]["checks"] if c["id"] == "past-ends")
    assert ends["status"] == "pass" and "Completed by AB: 80 scores 0" in ends["detail"]
    assert any(x.startswith(C.COMPLETION_NOTE + " AB: 80 scores 0") for x in done["limitations"])
    # another completion is another basis (a decision made on the first asks for another look)
    again = C.complete_curve(reg, cand["candidateKey"], [{"x": 70, "y": 0.0, "side": "high"}], by="AB", reason=WHY)
    assert again["considered"][0]["basisDigest"] != done["basisDigest"]
    assert len([x for x in again["considered"][0]["limitations"] if x.startswith(C.COMPLETION_NOTE)]) == 1
    # the register reads it as undecided now, never as one to complete
    view = C.deep_register(tiles=[], build={"portfolioSelection": {}}, register=reg)
    assert next(r for r in view["rows"] if r["candidateKey"] == cand["candidateKey"])["unresolved"] == "undecided"


def test_a_completion_is_kept_through_a_recheck_and_an_add_and_refused_while_selected():
    from views import final_selection as fs
    cand = _cand(DIATOM)
    assert cand["needsCompletion"] and cand["definition"]["openEnds"][0]["side"] == "low"
    reg = C.complete_curve(_considered(cand), cand["candidateKey"], [{"x": 30, "y": 0.0, "side": "low"}],
                           by="AB", reason="The diatom index falls to zero well below the published 58.")
    done = reg["considered"][0]
    again = fs.recheck(done, done["functions"][0], states=["AK"])
    assert not again["needsCompletion"] and again["basisDigest"] == done["basisDigest"]
    readded = C.add_considered(reg, cand, by="AB")          # the same record added again
    assert readded["considered"][0]["completion"] == done["completion"]
    chosen = [{"id": "d1", "action": oc.SOURCE, "metric": "x",
               "source": {"kind": "sqt", "ref": {"candidateKey": cand["candidateKey"]}}}]
    with pytest.raises(ValueError, match="Undo that decision"):
        C.complete_curve(reg, cand["candidateKey"], [{"x": 20, "y": 0.0, "side": "low"}], by="AB",
                         reason="Another look at where the index reaches zero here.", decisions=chosen)
    with pytest.raises(ValueError, match="at least 20"):
        C.complete_curve(reg, cand["candidateKey"], [{"x": 20, "y": 0.0, "side": "low"}], by="AB", reason="short")
    # a recorded completion that no longer fits leaves the curve open and says why
    stale = C.with_completion(cand, {"points": [{"x": 70, "y": 0.0, "side": "low"}], "by": "AB", "reason": WHY})
    assert stale["needsCompletion"]
    assert "no longer completes" in next(c for c in stale["eligibility"]["checks"] if c["id"] == "past-ends")["detail"]


# --------------------------------------------------------------------------- #
# the source a selection records: rebuilt from the frozen record
# --------------------------------------------------------------------------- #
def test_the_source_is_rebuilt_from_the_frozen_record_and_names_the_added_points():
    from streamcurves import owner_sources as os_
    cand = _cand(LAND)
    with pytest.raises(ValueError, match="Complete the curve first"):
        os_.sqt_source(cand)
    done = C.complete_curve(_considered(cand), cand["candidateKey"], [{"x": 80, "y": 0.0, "side": "high"}],
                            by="AB", reason=WHY)["considered"][0]
    src = os_.sqt_source(done)
    assert src["ref"]["adoptionVersion"] == oc.SQT_ADOPTION_VERSION
    assert [(p["x"], p["y"]) for p in src["curve"]["points"]] == [(0.0, 1.0), (35.0, 0.7), (50.0, 0.3), (80.0, 0.0)]
    sqt = src["curve"]["annotations"]["sqt"]
    assert [(p["x"], p["y"]) for p in sqt["publishedPoints"]] == [(0.0, 1.0), (35.0, 0.7), (50.0, 0.3)]
    assert sqt["addedPoints"] == [{"x": 80.0, "y": 0.0, "side": "high"}] and sqt["completedBy"] == "AB"
    caveats = src["curve"]["annotations"]["curveCaveats"]
    assert any("The SQT publishes points from 0 to 50; the points past them were added by AB: 80 scores 0"
               in c for c in caveats)
    # the verification is said once, and never with a file name
    text = " ".join(caveats)
    assert text.count("against the original") == 1 and ".xls" not in text.lower()
    # nothing saved beside the record is trusted: an edited point list is ignored
    edited = copy.deepcopy(done)
    edited["definition"]["points"] = [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]
    edited["definition"]["direction"] = "increasing"
    edited["label"] = "Something else"
    assert os_.sqt_source(edited)["curve"]["points"] == src["curve"]["points"]
    assert os_.sqt_source(edited)["title"] == src["title"]
    # a completion that no longer fits the record is refused
    broken = copy.deepcopy(done)
    broken["completion"]["points"] = [{"x": 20, "y": 0.0, "side": "high"}]
    with pytest.raises(ValueError, match="Complete the curve first"):
        os_.sqt_source(broken)


def test_one_stratum_of_a_metric_is_never_a_source():
    from streamcurves import owner_sources as os_
    # a stratum with no siblings in the registry is still one stratum
    fish = _cand(MI_FISH)
    strata = next(c for c in fish["eligibility"]["checks"] if c["id"] == "strata")
    assert strata["status"] == "fail" and "P51 Wadeable" in strata["detail"] and "also" not in strata["detail"]
    assert fish["eligibility"]["status"] == "excluded"
    # recorded as covering the target in a context, a stratum passes the check; the source still
    # refuses it, because nothing records that coverage
    cond = _cand(NC_COND, stratumCoversTarget=True)
    assert next(c for c in cond["eligibility"]["checks"] if c["id"] == "strata")["status"] == "pass"
    done = C.complete_curve(_considered(cond), cond["candidateKey"], [{"x": 40, "y": 1.0, "side": "low"}],
                            by="AB", reason="Conductivity this low scores fully in the Piedmont.")["considered"][0]
    with pytest.raises(ValueError, match="one stratum"):
        os_.sqt_source(done)


def test_a_verified_curve_says_its_points_are_the_originals_without_a_file_name():
    from streamcurves import owner_sources as os_
    reg = _reg()
    bhr = C.sqt_candidate(reg.record("sqt:mn:bank-height-ratio-bhr:default"),
                          function_id="channel-floodplain-dynamics",
                          context={"function": "channel-floodplain-dynamics", "states": ["MN"], "scoreScale": "staf"})
    caveats = os_.sqt_source(bhr)["curve"]["annotations"]["curveCaveats"]
    assert "Points from the original SQT." in caveats
    assert not any(".xls" in c.lower() or "list of metrics" in c.lower() for c in caveats)
    assert sum("against the original" in c for c in caveats) == 1


# --------------------------------------------------------------------------- #
# the picker checks as adding checks; rules and editions read as words
# --------------------------------------------------------------------------- #
def test_the_picker_ranks_each_record_by_the_checks_adding_runs():
    from views import final_selection as fs
    reg = _reg()
    ctx = {"function": "light-thermal-regime", "states": ["MI", "MN", "WI"], "scoreScale": "staf"}
    recs = reg.records(function="light-thermal-regime")
    assert recs
    for r in recs:
        checks, _ = C.sqt_checks(r, ctx)
        cand = C.sqt_candidate(r, function_id="light-thermal-regime", context=ctx)
        # what the picker shows (worst check) and what adding gives always agree
        assert (reg.overall(checks) == "fail") == (cand["eligibility"]["status"] == "excluded"), r["key"]
    import inspect
    assert "C.sqt_checks(r, ctx)" in inspect.getsource(fs.picker_results_ui)
    html = str(fs.picker_results_ui(recs, ns=lambda x: x, function_id="light-thermal-regime", context=ctx))
    assert "Does not apply" in html or "Applies" in html


def test_every_rule_reads_as_words_and_the_editions_as_the_rows_show_them():
    from views import final_selection as fs
    for rid in ("SELECT-04", "REF-05", "REF-13", "REF-14", "mapping", "study-2026-09-15",
                "study-2026-09-15-override", "field-vs-desktop", "REF-06", "CURVE-07"):
        words = fs.rule_words(rid)
        assert words and words != rid
    assert fs.rule_words("REF-02").endswith("(REF-02)") and fs.rule_words("no-such-rule") == "Another rule"
    reg = _reg()
    recs = reg.records(state="MN")
    choices = fs.edition_choices(recs)
    assert set(choices) == {fs._edition_words(r) for r in recs}
    assert any(w.startswith("MN SQT") for w in choices)


def test_a_decision_the_version_cannot_apply_can_be_undone_from_its_function():
    from views import final_selection as fs
    fn = {"functionId": "habitat-provision", "functionName": "Habitat provision", "discipline": "Biology",
          "selected": [], "alternatives": [], "unassessed": True, "gap": None, "unresolved": 1, "waiting": [],
          "stale": [{"decision": {"id": "cd-0000000009", "metric": "sqt_x"}, "why": oc.PRE_ADOPTION}]}
    html = str(fs.function_row_ui(fn, {}, ns=lambda x: x, compare=[], extension_on=True))
    assert "cd-0000000009" in html and "Undo" in html and oc.PRE_ADOPTION in html


def test_a_completed_candidate_offers_select_and_an_open_one_waits():
    from views import final_selection as fs
    cand = _cand(LAND)
    reg = _considered(cand)
    view = C.deep_register(tiles=[], build={"portfolioSelection": {}}, register=reg)
    row = next(r for r in view["rows"] if r["candidateKey"] == cand["candidateKey"])
    shown = next(c for c in view["candidates"] if c["candidateKey"] == cand["candidateKey"])
    html = str(fs.alternative_row_ui(row, shown, ns=lambda x: x, compare=[], extension_on=True))
    assert "Complete the curve" in html and fs.COMPLETE_FIRST[:30] in html
    done = C.complete_curve(reg, cand["candidateKey"], [{"x": 80, "y": 0.0, "side": "high"}], by="AB", reason=WHY)
    view = C.deep_register(tiles=[], build={"portfolioSelection": {}}, register=done)
    row = next(r for r in view["rows"] if r["candidateKey"] == cand["candidateKey"])
    shown = next(c for c in view["candidates"] if c["candidateKey"] == cand["candidateKey"])
    html = str(fs.alternative_row_ui(row, shown, ns=lambda x: x, compare=[], extension_on=True))
    assert "Change the completion" in html and fs.COMPLETE_FIRST[:30] not in html
    dialog = str(fs.completion_modal(shown, "Land", ns=lambda x: x))
    assert "Points the SQT publishes" in dialog and "fs_cx_high_0" in dialog and "fs_completion_preview" in dialog
    # nothing here is JSON the session cannot hold
    assert json.loads(json.dumps(done)) == done
