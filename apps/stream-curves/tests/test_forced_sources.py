"""A source the build refused, accepted by the owner (REF-15, owner decision 2026-09-22).

The owner may accept a source the build refused: a station pool below the floor
or refused by the recovery test, a national option, a modeled specification that
waits for approval, a catalog criterion that failed a fitness condition. The
decision waits for the next build, which computes the curve beside its own
choices without changing them, runs every check the source faces and records
each one it fails. These tests hold the computation to the record.
"""
from __future__ import annotations

import pytest

from streamcurves import acceptance
from streamcurves import basis_ladder as bl
from streamcurves import curve_basis as cb
from streamcurves import owner_curves as oc
from streamcurves import owner_sources as osrc
from streamcurves import pressure_evidence as pe
from streamcurves import reference_pool as rp

WHY = "A reason long enough to be recorded."
EM_DASH = chr(8212)


@pytest.fixture(scope="module")
def inputs():
    try:
        return pe.national_inputs()
    except FileNotFoundError as exc:          # the station table is not built here
        pytest.skip(str(exc))


def _config(inputs, metric):
    return inputs["metric_config"].get(metric) or osrc.agent_config(metric)


def _force(inputs, metric, rule, option, l3="27"):
    return bl.force_source(metric, {"rule": rule, "option": option}, frame=inputs["frame"],
                           values_wide=inputs["values"], target_l3=l3,
                           config=_config(inputs, metric),
                           validation=bl.load_validation(), region_name="Central Great Plains")


def test_every_check_runs_to_its_verdict():
    got = acceptance.all_checks("phab_XCMGW", "regional_l2", list(range(3)), {}, {})
    checks = [c["check"] for c in got]
    assert checks == ["ACC-01", "ACC-04", "ACC-05/06"]
    assert not got[0]["pass"] and "below the floor" in got[0]["why"]
    national = acceptance.all_checks("phab_XCMGW", "3c_matched", list(range(12)), {}, {})
    assert [c["check"] for c in national][-1] == "ACC-03"


def test_a_refused_pool_is_computed_on_its_own_stations(inputs):
    got = _force(inputs, "phab_XCMGW", "REF-11", "regional_l2")
    assert got["row"] is not None and got["row"]["curve_status"] == "complete"
    d = got["decision"]
    assert d["status"] == "borrowed_l2" and d["n_usable"] >= bl.MIN_FORCED_N
    assert any(f["check"] == "ACC-05/06" for f in got["failed"])
    assert all(not f["pass"] for f in got["failed"])


def test_a_pool_too_small_to_draw_fails_at_the_build(inputs):
    got = _force(inputs, "phab_XCMGW", "REF-04", "local")
    assert got["row"] is None and "could not be formed" in got["why"]


def test_a_refused_national_option_is_computed_on_its_donors(inputs):
    got = _force(inputs, "phab_XCMGW", "REF-12", "3c_matched")
    assert got["row"] is not None
    assert got["decision"]["status"] == "national" and got["decision"]["basis"] == cb.NATIONAL
    assert got["decision"]["screen_detail"]["forced"] is True
    assert any(f["check"] == "ACC-05/06" for f in got["failed"])
    assert osrc.forced_annotations("phab_XCMGW", got)["criteriaBasis"] == "reference"


def test_a_metric_the_catalog_holds_nothing_for_cannot_be_forced(inputs):
    got = _force(inputs, "phab_XCMGW", "REF-14", "catalog")
    assert got["row"] is None and got["why"]


def test_a_forced_curve_never_changes_what_the_build_fits(inputs):
    """force_source reads the frame and the values and writes nothing back."""
    frame, values = inputs["frame"], inputs["values"]
    before = (len(frame), list(frame.columns), len(values), list(values.columns))
    _force(inputs, "phab_XCMGW", "REF-11", "regional_l2")
    assert (len(frame), list(frame.columns), len(values), list(values.columns)) == before


def test_a_fitted_metric_is_not_forced():
    got = pe.forced_sources({"chem_PTL": {"rule": "REF-12", "option": "3c_matched"}},
                            metric_config={"chem_PTL": {}}, carried={}, insufficient={},
                            ladder_rows={}, frame=None, values=None, l3_code="27", name="x",
                            validation=None, registry=None, excluded=None)
    assert got["chem_PTL"]["row"] is None and "fits the metric itself" in got["chem_PTL"]["why"]


def test_the_decision_waits_then_holds_what_the_build_computed(inputs):
    opt = {"kind": osrc.REFUSED, "key": "refused_source:REF-11:regional_l2",
           "title": "The Level II pool (9.4)", "ref": {"rule": "REF-11", "option": "regional_l2"},
           "points": [], "citation": ""}
    cfg = _config(inputs, "phab_XCMGW")
    d = oc.new_decision("phab_XCMGW", oc.SOURCE, rationale=WHY, recorded_by="owner",
                        functions=["carbon-processing"],
                        source=osrc.decision_source("phab_XCMGW", opt, config=cfg))
    assert oc.forced_sources([d]) == {"phab_XCMGW": {"rule": "REF-11", "option": "regional_l2"}}
    assert set(oc.pending([d])) == {"phab_XCMGW"}
    assert oc.effective_build({"method": pe.METHOD}, [d]).get("ownerMetrics") is None
    got = _force(inputs, "phab_XCMGW", "REF-11", "regional_l2")
    got["config"] = cfg
    filled = oc.with_forced([d], {"phab_XCMGW": got})[0]
    curve = oc.source_curve(filled)
    assert len(curve["points"]) >= 2 and filled["source"]["failed"]
    ann = curve["annotations"]
    assert ann["ownerException"]["rule"] == "REF-11"
    assert ann["referenceSupport"]["status"] == "borrowed_l2"
    # it rests on stations, and says so as every fitted curve does; DEEP prints a
    # curve's station support only beside a stated basis
    assert ann["criteriaBasis"] == "reference"
    assert "refused this source" in ann["curveCaveats"][0] and EM_DASH not in ann["curveCaveats"][0]
    assert oc.pending([filled]) == {}
    eff = oc.effective_build({"method": pe.METHOD}, [filled])
    assert set(eff["ownerMetrics"]) == {"phab_XCMGW"}
    # the region's record holds the request; opening the staged session keeps the curve
    assert oc.source_curve(oc.combine([filled], [d])[0])["points"] == curve["points"]
    # nothing to build from: the decision records why and applies nothing
    failed = oc.with_forced([d], {"phab_XCMGW": {"row": None, "why": "Too few."}})[0]
    assert failed["source"]["failedAtBuild"] == "Too few." and oc.pending([failed]) == {}
    assert [why for _d, why in oc.stale([failed], {"method": pe.METHOD})][0].startswith(
        "Nothing could be built")


def test_the_pool_offers_the_refusals_the_provenance_records():
    provenance = {"records": [
        {"rule_id": "REF-06", "subject": "m", "subject_kind": "metric", "verdict": "fail",
         "computed": {"options_tried": [
             {"option": "local", "level": "l3", "region_code": "27", "n_usable": 2,
              "why": "2 usable stations, below the floor of 10"},
             {"option": "regional_l2", "level": "l2", "region_code": "9.4", "n_usable": 52,
              "why": "In the recovery test the source did not agree."}]}},
        {"rule_id": "REF-12", "subject": "m", "subject_kind": "metric", "verdict": "fail",
         "computed": {"why": "refused", "options": [
             {"option": "3c_matched", "n": 117, "accepted": False, "why": "Recovery test."}]}},
        {"rule_id": "REF-13", "subject": "m", "subject_kind": "metric", "verdict": "fail",
         "computed": {"why": "A modeled specification for this metric passed the recovery test "
                             "and waits for the owner's approval, so it is not used yet.",
                      "candidate": True}},
        {"rule_id": "REF-14", "subject": "m", "subject_kind": "metric", "verdict": "fail",
         "computed": {"why": "None for this metric.", "condition": ["catalog", "no entry"]}}]}
    opts = {o["key"]: o for o in osrc.refused_options("m", provenance=provenance)}
    assert not opts["refused_source:REF-04:local"]["available"]
    assert "Too few stations" in opts["refused_source:REF-04:local"]["why_not"]
    assert opts["refused_source:REF-11:regional_l2"]["available"]
    assert opts["refused_source:REF-11:regional_l2"]["title"] == "The Level II pool (9.4)"
    assert opts["refused_source:REF-12:3c_matched"]["available"]
    assert opts["refused_source:REF-13:modeled"]["available"]
    assert not opts["refused_source:REF-14:catalog"]["available"]
    assert all(o["ref"]["refusal"] for o in opts.values())


def test_the_request_joins_the_inputs_digest_only_when_there_is_one():
    from streamcurves import provenance as pv
    base = {"inputs": {"reference": {"method": "pressure-screen"}}}
    plain = pv.digest_payload_from_manifest(base)
    assert "forcedSources" not in plain["reference"]
    forced = {"inputs": {"reference": {"method": "pressure-screen", "forcedSources": {
        "m": {"rule": "REF-12", "option": "3c_matched"}}}}}
    assert pv.digest_payload_from_manifest(forced)["reference"]["forcedSources"]


def test_the_refusal_records_say_when_the_owner_gave_the_metric_a_curve():
    from streamcurves import provenance as pv
    chosen = {"metric": "phab_XCMGW", "action": "source",
              "source": {"kind": osrc.ENTERED, "title": "Owner thresholds",
                         "curve": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}}}
    records = []

    def add(rule, kind, subject, **kw):
        records.append({"rule": rule, "subject": subject, **kw})

    result = {"reference_method": pe.METHOD,
              "reference_support": {"phab_XCMGW": {"status": "insufficient"}}}
    pv._pressure_records(result, add)
    plain = next(r for r in records if r["rule"] == "REF-06")["recommendation"]
    assert plain.endswith("No curve is forced.")
    records.clear()
    pv._pressure_records({**result, "curve_decisions": [chosen]}, add)
    told = next(r for r in records if r["rule"] == "REF-06")["recommendation"]
    assert "The owner chose a source for it under REF-15 (Owner thresholds)" in told


def test_a_drafted_gap_names_an_accepted_source_nothing_could_be_built_from():
    unbuilt = {"metric": "phab_XCMGW", "action": "source",
               "source": {"kind": osrc.REFUSED, "title": "The Level II pool (9.4)",
                          "failedAtBuild": "The pool could not be formed here."}}
    result = {"coverage": {"missingFunctionIds": ["carbon-processing"]},
              "insufficient_support": {"phab_XCMGW": {"config": {"display_name": "Woody cover"},
                                                      "decision": {}}},
              "curve_decisions": [unbuilt]}
    text = pe.coverage_exceptions_draft(result)[0]["justification"]
    assert "No curve was forced." in text
    assert text.endswith("The owner accepted The Level II pool (9.4) for Woody cover, and "
                         "nothing could be built from it: The pool could not be formed here.")
    assert EM_DASH not in text


def test_the_table_card_is_still_rendered_and_names_what_waits_for_a_build():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "views" / "summary_page.py").read_text(
        encoding="utf-8").replace(chr(13), "")
    assert "    @render.ui\n    def reference_table():" in src
    assert "    def _waiting_for_build():" in src
    assert "@render.ui\n    def _waiting_for_build" not in src
