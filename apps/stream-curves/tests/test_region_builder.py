"""The Region builder's logic: viability, the command, and writing decisions.

The build itself is a subprocess running scripts/run_region_batch.py, so what is
worth testing here is the thin layer around it: whether a candidate count is
described by the rule it actually runs into, whether the command is one the batch
runner parses, and whether a reviewer's answer is refused HERE rather than 35
minutes later inside promote.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from streamcurves import methodology, region_build as rb

RUNS = Path(__file__).resolve().parents[3] / "notes" / "DEEP_Working" / "analysis" / "runs"
IP71 = RUNS / "ip-71"


# --------------------------------------------------------------------------- #
# Viability: named by rule, at the real thresholds
# --------------------------------------------------------------------------- #
def test_the_bands_come_from_the_methodology_not_a_literal():
    """If someone retunes DATA-04 in the config, the picker has to follow."""
    minimum = int(methodology.threshold("data_rules.min_n_unstratified"))
    exploratory = int(methodology.threshold("data_rules.exploratory_n_unstratified"))
    assert rb.viability(minimum)["band"] == "adequate"
    assert rb.viability(exploratory)["band"] == "exploratory"
    assert rb.viability(exploratory - 1)["band"] == "insufficient"


@pytest.mark.parametrize("n,band,rule", [
    (0, "insufficient", "DATA-06"),
    (9, "insufficient", "DATA-06"),
    (10, "exploratory", "DATA-05"),
    (19, "exploratory", "DATA-05"),
    (20, "adequate", "DATA-04"),
    (244, "adequate", "DATA-04"),
])
def test_the_boundaries_land_where_the_rules_say(n, band, rule):
    v = rb.viability(n)
    assert (v["band"], v["rule"]) == (band, rule)


def test_the_label_names_the_rule_and_the_number():
    """The user asked for specific, so "exploratory" alone is not enough: the label
    has to say which band and what it costs."""
    assert "DATA-04" in rb.viability(30)["label"]
    lbl = rb.viability(12)["label"]
    assert "DATA-05" in lbl and "10 to 19" in lbl and "59" in lbl
    assert "DATA-06" in rb.viability(4)["label"] and "10" in rb.viability(4)["label"]


def test_an_adequate_label_is_conditional_on_the_screen():
    """The count is candidates before screening. ECBP had 18 candidates and zero
    Functioning sites, so a promise here would be a lie."""
    assert "if the screen retains enough" in rb.viability(100)["label"]


# --------------------------------------------------------------------------- #
# The picker
# --------------------------------------------------------------------------- #
SITES = pd.DataFrame({
    "us_l3code": ["58", "58", "58", "55", "55", "09"],
    "us_l3name": ["Northeastern Highlands"] * 3 + ["Eastern Corn Belt Plains"] * 2
                 + ["Thin Region"],
    "site_id": ["a", "b", "c", "d", "e", "f"],
})


def test_every_ecoregion_in_the_table_is_offered():
    """All 85, not just the viable ones: hiding a region means you only learn it is
    too thin after spending the compute."""
    codes = {r["code"] for r in rb.region_choices(SITES)}
    assert codes == {"58", "55", "09"}


def test_choices_are_ordered_by_ecoregion_code():
    rows = rb.region_choices(SITES)
    assert [r["code"] for r in rows] == ["09", "55", "58"]


def test_the_code_order_is_numeric_not_lexical():
    """EPA codes are strings, so a plain sort gives 1, 10, 11, 2 and scatters the
    numbering. On the real table that is the difference between starting at 1, 2, 3
    and starting at 1, 10, 11."""
    sites = pd.DataFrame({
        "us_l3code": ["8", "43", "10", "2", "85"],
        "us_l3name": ["a", "b", "c", "d", "e"],
        "site_id": ["s1", "s2", "s3", "s4", "s5"],
    })
    assert [r["code"] for r in rb.region_choices(sites)] == ["2", "8", "10", "43", "85"]


def test_a_non_numeric_code_sorts_last_rather_than_raising():
    sites = pd.DataFrame({
        "us_l3code": ["43", "unknown", "2"],
        "us_l3name": ["a", "b", "c"],
        "site_id": ["s1", "s2", "s3"],
    })
    assert [r["code"] for r in rb.region_choices(sites)] == ["2", "43", "unknown"]


def test_repeat_visits_do_not_inflate_a_region():
    """The multi-cycle archive has several visits per station; a station is one
    candidate, not three."""
    repeated = pd.DataFrame({
        "us_l3code": ["58"] * 4, "us_l3name": ["Northeastern Highlands"] * 4,
        "site_id": ["a", "a", "b", "b"],
    })
    assert rb.region_choices(repeated)[0]["n_candidates"] == 2


def test_an_empty_table_yields_no_choices():
    assert rb.region_choices(pd.DataFrame()) == []


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #
def test_the_command_targets_the_batch_runner_stage_subcommand():
    argv = rb.stage_command("71", "Interior Plateau", "/tmp/run", maintainer="me")
    assert argv[2].endswith("run_region_batch.py")
    assert argv[3] == "stage"
    assert "--l3" in argv and argv[argv.index("--l3") + 1] == "71"
    assert argv[argv.index("--maintainer") + 1] == "me"


def test_optional_policies_ride_as_repeated_flags():
    argv = rb.stage_command("71", "IP", "/tmp/run", maintainer="me",
                            enable_policies=["ref02-accept-best-available",
                                             "data06-insufficient-finalized"])
    assert argv.count("--enable-policy") == 2
    assert "ref02-accept-best-available" in argv


def test_no_policies_means_no_flag():
    argv = rb.stage_command("71", "IP", "/tmp/run", maintainer="me")
    assert "--enable-policy" not in argv
    assert "--nrsa-dataset" not in argv
    assert "--reviewer-decisions" not in argv


def test_out_is_a_run_folder_never_a_library_root():
    """cmd_stage derives its staged root as <out>/library and refuses if that
    resolves to the canonical one. Pointing --out at a library would nest one
    inside the other."""
    out = rb.run_folder("/tmp/runs", "71")
    assert out.name == "l3-71"
    argv = rb.stage_command("71", "IP", out, maintainer="me")
    target = Path(argv[argv.index("--out") + 1])
    assert target.name != "library"
    assert "assessments" not in target.parts


def test_each_optional_policy_is_described_not_just_named():
    """A checkbox reading "data06-insufficient-finalized" asks the user to know the
    catalog. Each one carries its rule and what it accepts."""
    assert len(rb.OPTIONAL_POLICIES) == 4
    for pid, label, detail in rb.OPTIONAL_POLICIES:
        assert pid and label and detail
        assert pid not in label
        assert any(fam in detail for fam in ("REF-", "DATA-", "CURVE-"))


def test_exit_codes_read_as_sentences():
    assert rb.exit_meaning(0) == "Staged."
    assert "did not settle" in rb.exit_meaning(1)
    assert "landscape" in rb.exit_meaning(2)
    assert "Another run is staging this region" in rb.exit_meaning(3)   # the region's lock is held
    assert "7" in rb.exit_meaning(7)


def test_progress_reads_the_runners_own_narration():
    log = ("loading sites\n"
           "[batch] pass 1: queue open 9, policy decided 32 new item(s), 9 left open\n"
           "some noise\n"
           "[batch] pass 2: queue open 1, policy decided 2 new item(s), 1 left open\n")
    assert rb.progress_from_log(log).startswith("[batch] pass 2:")
    assert rb.progress_from_log("nothing here") is None
    assert rb.progress_from_log("") is None


# --------------------------------------------------------------------------- #
# Writing decisions. This is the part that earns the feature its keep.
# --------------------------------------------------------------------------- #
DOC = {
    "records": [
        {"rule_id": "CURVE-04", "subject": "phab_XEMBED",
         "computed": {"decision_flip": True, "driver": "NRS18_KY_10008"}},
        {"rule_id": "REF-02", "subject": "reference_screen",
         "computed": {"reference_tier": "best_available", "n_retained": 23}},
    ],
}
ITEM = {"rule_id": "CURVE-04", "subject": "phab_XEMBED",
        "evidence": {"decision_flip": True, "driver": "NRS18_KY_10008"},
        "question": "Accept the influence flag?", "blocking": False}


def test_asserts_are_taken_from_the_record_not_from_the_user():
    """Hand-authoring this field is exactly what got a staged build refused."""
    d = rb.build_decision(DOC, ITEM, "accept", "Accepted with the flag.", reviewer="me")
    assert d["asserts"] == {"decision_flip": True, "driver": "NRS18_KY_10008"}
    assert rb.decision_problems(DOC, d) == []


def test_a_decision_matches_the_shape_the_pipeline_consumes():
    d = rb.build_decision(DOC, ITEM, "accept", "Fine.", reviewer="me")
    assert set(d) == {"rule_id", "subject", "action", "rationale", "reviewer",
                      "rationale_origin", "asserts"}
    assert d["rationale_origin"] == "owner_written"


def test_an_unknown_action_is_refused_before_it_reaches_the_file():
    with pytest.raises(ValueError):
        rb.build_decision(DOC, ITEM, "looks_fine", "x", reviewer="me")


def test_every_allowed_action_is_accepted():
    for action in rb.REVIEWER_ACTIONS:
        assert rb.build_decision(DOC, ITEM, action, "x", reviewer="me")["action"] == action


def test_an_empty_rationale_is_refused():
    d = rb.build_decision(DOC, ITEM, "accept", "   ", reviewer="me")
    assert "A rationale is required." in rb.decision_problems(DOC, d)


def test_a_rationale_contradicting_its_record_is_caught_here():
    """provenance lints the wording for templated phrases: writing "no decision flip"
    over a record that computed one makes the run raise. Catching it at write time is
    the difference between an inline message and a wasted build."""
    d = rb.build_decision(DOC, ITEM, "accept",
                          "Accepted: no decision flip, so the site stays.", reviewer="me")
    problems = rb.decision_problems(DOC, d)
    assert problems, "the contradiction must be caught before the file is written"
    assert any("decision_flip" in p for p in problems)


def test_an_answer_with_no_matching_record_is_named_as_such():
    d = rb.build_decision(DOC, ITEM, "accept", "ok", reviewer="me")
    d["subject"] = "not_a_metric"
    assert any("No record" in p for p in rb.decision_problems(DOC, d))


def test_evidence_fields_the_record_does_not_compute_are_dropped():
    """asserts may only name computed fields; anything else is refused downstream as
    "the record does not compute"."""
    item = dict(ITEM, evidence={"decision_flip": True, "invented_field": 3})
    d = rb.build_decision(DOC, item, "accept", "ok", reviewer="me")
    assert "invented_field" not in d["asserts"]
    assert rb.decision_problems(DOC, d) == []


# A CURVE-07 item as the staged Northern Lakes and Forests run records it.
CURVE07_COMPUTED = {"curve_status": "degenerate",
                    "reasons": ["Non-positive or non-finite Q25 produced a fallback curve."],
                    "domain_min": 0.0, "domain_max": 100.0, "domain_violations": 0,
                    "reviewer_decision": "pending"}
CURVE07_DOC = {"records": [{"rule_id": "CURVE-07", "subject": "phab_PCT_FAST",
                            "computed": dict(CURVE07_COMPUTED)}]}
CURVE07_ITEM = {"rule_id": "CURVE-07", "subject": "phab_PCT_FAST",
                "evidence": dict(CURVE07_COMPUTED), "blocking": False,
                "question": "Accept this curve as preliminary, adjust it, or drop the metric?"}


def test_an_answer_never_asserts_the_decision_it_makes():
    """Answering CURVE-07 publishes or drops the curve, so the build the answer
    feeds records another reviewer_decision than "pending"; asserting it would
    refuse that whole run."""
    d = rb.build_decision(CURVE07_DOC, CURVE07_ITEM, "accept_with_conditions",
                          "Publishes as preliminary, marked for field verification.",
                          reviewer="me")
    assert "reviewer_decision" not in d["asserts"]
    assert d["asserts"]["curve_status"] == "degenerate"
    assert rb.decision_problems(CURVE07_DOC, d) == []


def test_a_curve07_item_offers_the_outcomes_its_question_names():
    choices = rb.action_choices("CURVE-07")
    assert set(choices) == {"accept", "accept_with_conditions", "reject"}
    assert "publish the curve" in choices["accept"]
    assert "drop the metric" in choices["reject"]
    # every other rule keeps the reviewer's five answers
    assert set(rb.action_choices("CURVE-04")) == set(rb.REVIEWER_ACTIONS)
    assert set(rb.action_choices(None)) == set(rb.REVIEWER_ACTIONS)
    # the list that offers them is Select final curves' (one decision authority)
    src = (Path(__file__).resolve().parents[1] / "views" / "final_selection.py").read_text(
        encoding="utf-8")
    assert 'rb.action_choices(item.get("rule_id"))' in src


def test_saving_keeps_the_answers_an_earlier_build_resolved():
    """A build resolves what it was answered, so the list shows only what is still
    open; saving must add to the region's answers, not replace them."""
    def key(d):
        return d.get("rule_id"), str(d.get("subject"))
    old = [{"rule_id": "CURVE-07", "subject": "phab_PCT_FAST", "action": "accept"},
           {"rule_id": "CURVE-06", "subject": "phab_PCT_FAST", "action": "accept"}]
    new = [{"rule_id": "CURVE-06", "subject": "phab_PCT_FAST", "action": "reject"},
           {"rule_id": "CURVE-12", "subject": "bfiws", "action": "accept"}]
    merged = rb.merge_answers(old, new, key=key)
    assert [(d["rule_id"], d["action"]) for d in merged] == [
        ("CURVE-07", "accept"), ("CURVE-06", "reject"), ("CURVE-12", "accept")]
    assert rb.merge_answers(None, new, key=key) == new
    # the writers (save_answer, save_gap) merge the same way
    src = Path(rb.__file__).read_text(encoding="utf-8")
    assert "merge_answers(read_answers(run_dir), [decision], key=_answer_key)" in src
    assert "merge_answers(read_gaps(run_dir), [exception]" in src


# --------------------------------------------------------------------------- #
# The region's decision files: what Select final curves writes, what the next
# build reads (one decision authority, 2026-09-25)
# --------------------------------------------------------------------------- #
def test_an_answer_reaches_owner_decisions_json_one_per_rule_and_subject(tmp_path):
    run_dir = tmp_path / "l3-71"
    d = rb.build_decision(DOC, ITEM, "accept", "Accepted with the flag.", reviewer="GM")
    path = rb.save_answer(run_dir, d)
    assert path == run_dir / rb.OWNER_DECISIONS_FILE and path.is_file()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == [d]
    assert rb.read_answers(run_dir) == [d]
    # a second answer to the same item replaces it; another item is added
    again = rb.build_decision(DOC, ITEM, "reject", "On reflection, no.", reviewer="GM")
    other = rb.build_decision(DOC, {"rule_id": "REF-02", "subject": "reference_screen",
                                    "evidence": {"reference_tier": "best_available"}},
                              "accept", "Best available accepted.", reviewer="GM")
    rb.save_answer(run_dir, again)
    rb.save_answer(run_dir, other)
    answers = rb.read_answers(run_dir)
    assert [(a["rule_id"], a["action"]) for a in answers] == [("CURVE-04", "reject"), ("REF-02", "accept")]
    # the shape --reviewer-decisions reads: the same keys build_decision writes
    assert set(answers[0]) == {"rule_id", "subject", "action", "rationale", "reviewer",
                               "rationale_origin", "asserts"}
    with pytest.raises(ValueError):
        rb.save_answer(run_dir, {"action": "accept"})
    assert rb.read_answers(tmp_path / "nowhere") == []


def test_a_gap_reaches_coverage_exceptions_json_and_can_be_withdrawn(tmp_path):
    run_dir = tmp_path / "l3-52"
    gap = rb.build_coverage_exception(
        "channel-floodplain-dynamics", "no-suitable-metric",
        "Sinuosity and bank angle both failed their curve checks in this region.",
        recorded_by="GM")
    path = rb.save_gap(run_dir, gap)
    assert path == run_dir / rb.COVERAGE_EXCEPTIONS_FILE
    assert rb.read_gaps(run_dir) == [gap]
    # one per function: recording again replaces; another function is added
    rb.save_gap(run_dir, dict(gap, justification="A fuller justification of the same gap."))
    rb.save_gap(run_dir, rb.build_coverage_exception(
        "hyporheic-connectivity", "no-suitable-metric",
        "No hyporheic metric is measured at NRSA stations.", recorded_by="GM"))
    gaps = rb.read_gaps(run_dir)
    assert [g["functionId"] for g in gaps] == ["channel-floodplain-dynamics", "hyporheic-connectivity"]
    assert gaps[0]["justification"].startswith("A fuller")
    # the shape the build's --coverage-exceptions validator accepts
    assert rb.coverage_problems(gaps) == []
    rb.remove_gap(run_dir, "channel-floodplain-dynamics")
    assert [g["functionId"] for g in rb.read_gaps(run_dir)] == ["hyporheic-connectivity"]
    # withdrawing everything leaves an empty file, never a missing one
    rb.remove_gap(run_dir, "hyporheic-connectivity")
    assert path.is_file() and rb.read_gaps(run_dir) == []
    with pytest.raises(ValueError):
        rb.save_gap(run_dir, {"reason": "no-suitable-metric"})


def test_the_candidate_register_is_written_beside_the_curve_decisions(tmp_path):
    from streamcurves import owner_curves as oc
    run_dir = tmp_path / "l3-58"
    register = {"schema": 1, "considered": [{"candidateKey": "sqt:mn:x", "label": "X"}],
                "dispositions": []}
    path = rb.save_candidate_register(run_dir, register)
    assert path == run_dir / rb.CANDIDATE_REGISTER_FILE
    assert json.loads(path.read_text(encoding="utf-8")) == register
    # beside curve_decisions.json, in the same folder a build reads its decisions from
    assert (run_dir / oc.DECISIONS_FILE).parent == path.parent
    assert path.name == "candidate_register.json"
    # an empty register is a register, not an absent file
    rb.save_candidate_register(run_dir, None)
    assert json.loads(path.read_text(encoding="utf-8"))["considered"] == []


def test_the_open_queue_items_read_as_the_packet_shapes_them():
    """The build's provenance carries rule_ids (a list) and a status per item; the
    list Select final curves shows uses the packet's shape (rule_id, question,
    blocking, evidence) and only the open ones."""
    doc = {"records": [], "reviewQueue": {"items": [
        {"item_id": "REF-02:reference_screen", "rule_ids": ["REF-02"], "subject": "reference_screen",
         "trigger": "reference_tier_fallback", "blocking": True, "status": "open",
         "question": "Accept the best-available tier?", "evidence": {"reference_tier": "best_available"}},
        {"item_id": "STRAT-09:DrainageAreaClass", "rule_ids": ["STRAT-09"], "subject": "DrainageAreaClass",
         "trigger": "advisory_stratifier_not_applied", "blocking": False, "status": "resolved",
         "question": "Split by it?", "evidence": {}},
        {"item_id": "CURVE-07:phab_X", "rule_ids": ["CURVE-07"], "subject": "phab_X",
         "trigger": "curve_needs_review", "blocking": False, "status": "open",
         "question": "Accept, adjust or drop?", "evidence": {"curve_status": "degenerate"}},
    ]}}
    items = rb.open_queue_items(doc)
    assert [i["item_id"] for i in items] == ["REF-02:reference_screen", "CURVE-07:phab_X"]
    assert items[0]["rule_id"] == "REF-02" and items[0]["blocking"] is True
    assert items[0]["evidence"] == {"reference_tier": "best_available"}
    assert {"item_id", "rule_id", "subject", "trigger", "question", "blocking", "evidence"} <= set(items[0])
    assert rb.open_queue_items(None) == [] and rb.open_queue_items({}) == []
    # an answer built from a queue item is one the pipeline accepts
    d = rb.build_decision({"records": [{"rule_id": "REF-02", "subject": "reference_screen",
                                        "computed": {"reference_tier": "best_available"}}]},
                          items[0], "accept", "Accepted for this region.", reviewer="GM")
    assert d["asserts"] == {"reference_tier": "best_available"}


# --------------------------------------------------------------------------- #
# The campaign index over a runs root
# --------------------------------------------------------------------------- #
def _fake_region(root: Path, code: str, name: str, *, staged=True, open_items=0, hard_stops=0,
                 complete=True, evidence=None):
    d = root / f"l3-{code}"
    d.mkdir(parents=True)
    staged_dir = d / "library" / "assessments" / "x" / "v2"
    staged_dir.mkdir(parents=True)
    packet = {"region": {"code": code, "name": name},
              "curves": [{"metric": "a"}, {"metric": "b"}],
              "decisions_applied": [{"rule_id": "CURVE-06"}] * 3,
              "open_items": [{"item_id": f"X:{i}"} for i in range(open_items)],
              "hard_stops": [{"item_id": f"H:{i}"} for i in range(hard_stops)],
              "staged": {"version": 2, "path": str(staged_dir)} if staged else None}
    (d / "review_packet.json").write_text(json.dumps(packet), encoding="utf-8")
    (d / "standing_decisions_applied.json").write_text(
        json.dumps({"decisions": [{"rule_id": "CURVE-06"}] * 4}), encoding="utf-8")
    if complete:
        (d / "stage_complete.json").write_text(
            json.dumps({"l3": code, "outputs": {"review_packet.json": "sha"}}), encoding="utf-8")
    if evidence is not None:
        (d / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    return d


def test_campaign_rows_read_every_region_of_a_runs_root(tmp_path):
    root = tmp_path / "runs"
    _fake_region(root, "71", "Interior Plateau", evidence={"id": "deep-dev-l3-71", "sha256": "abc"})
    _fake_region(root, "9", "Thin Region", staged=False, open_items=2, hard_stops=1, complete=False)
    _fake_region(root, "55", "Eastern Corn Belt Plains", complete=False, evidence={"id": "x"})
    (root / "batch_summary.json").write_text(json.dumps({"schemaVersion": 1, "regions": [
        {"l3": "71", "name": "Interior Plateau", "exit": 0, "curves": 2, "decisions": 3,
         "open_items": 0, "hard_stops": 0, "staged_version": 2},
        {"l3": "80", "name": "Never staged", "exit": 2, "error": "landscape source failed"},
    ]}), encoding="utf-8")
    rows = {r["region"]: r for r in rb.campaign_rows(root)}
    # numeric order, the summary's regions included even without a folder
    assert list(rows) == ["9", "55", "71", "80"]
    ip = rows["71"]
    assert ip["name"] == "Interior Plateau" and ip["version"] == 2 and ip["curves"] == 2
    # decisions applied come from the applied file when it is there
    assert ip["decisions_applied"] == 4 and ip["open_items"] == 0 and ip["hard_stops"] == 0
    assert ip["promote_eligible"] is True and ip["evidence"] == "ready"
    assert ip["stage_complete"] is True and ip["run_dir"].endswith("l3-71")
    thin = rows["9"]
    assert thin["version"] is None and thin["open_items"] == 2 and thin["hard_stops"] == 1
    assert thin["promote_eligible"] is False and thin["evidence"] == "missing"
    # staged and clean, but a batch region whose stage record is missing is not eligible
    ecbp = rows["55"]
    assert ecbp["version"] == 2 and ecbp["promote_eligible"] is False
    assert ecbp["evidence"] == "unreadable"          # a manifest with no digest
    never = rows["80"]
    assert never["run_dir"] is None and never["version"] is None and never["promote_eligible"] is False
    # no root, or an empty one: no rows
    assert rb.campaign_rows(tmp_path / "missing") == []
    (tmp_path / "empty").mkdir()
    assert rb.campaign_rows(tmp_path / "empty") == []


def test_campaign_rows_without_a_batch_summary_judge_single_runs(tmp_path):
    """The app's Build writes no stage_complete.json (only a stage-many job does), so a
    single staged run with nothing open is eligible on its packet alone."""
    root = tmp_path / "runs"
    _fake_region(root, "58", "Northeastern Highlands", complete=False)
    rows = rb.campaign_rows(root)
    assert len(rows) == 1 and rows[0]["promote_eligible"] is True
    assert rows[0]["name"] == "Northeastern Highlands" and rows[0]["decisions_applied"] == 4


# --------------------------------------------------------------------------- #
# Against the one real batch run in the repo
# --------------------------------------------------------------------------- #
def _ip71(name: str):
    p = IP71 / name
    if not p.exists():
        pytest.skip(f"{name} not present (notes/ is gitignored)")
    return json.loads(p.read_text(encoding="utf-8"))


def test_the_real_packet_carries_what_the_page_renders():
    packet = _ip71("review_packet.json")
    for key in ("region", "reference_tier", "screening", "curves", "coverage",
                "open_items", "hard_stops", "queue_counts", "staged",
                "promote_command"):
        assert key in packet, key


def test_open_items_carry_the_seven_fields_a_card_needs():
    packet = _ip71("review_packet_stage1.json")
    assert packet["open_items"], "stage 1 had 9 open items"
    for item in packet["open_items"]:
        assert {"item_id", "rule_id", "subject", "trigger", "question", "blocking",
                "evidence"} <= set(item)


def test_a_curve_rows_sample_size_can_arrive_as_a_string():
    """The packet is written with default=str, so a numpy scalar serializes as a
    string. Anything formatting it has to coerce."""
    packet = _ip71("review_packet.json")
    kinds = {type(c.get("n_reference")).__name__ for c in packet["curves"]}
    assert kinds, "no curves in the packet"
    for c in packet["curves"]:
        assert int(float(c["n_reference"])) >= 0


def test_answering_a_real_open_item_produces_a_clean_decision():
    """End to end on the actual run: an open item plus its provenance record makes a
    decision the pipeline would accept."""
    packet = _ip71("review_packet_stage1.json")
    doc = json.loads((IP71.parent.parent.parent / "analysis" / "runs" / "ip-71"
                      / "decision_provenance_log.json").read_text(encoding="utf-8")) \
        if (IP71 / "decision_provenance_log.json").exists() else None
    if doc is None:
        pytest.skip("decision_provenance_log.json not present")
    records = doc if isinstance(doc, dict) else {"records": doc}
    item = next(i for i in packet["open_items"] if i["rule_id"] == "REF-02")
    d = rb.build_decision(records, item, "accept",
                          "Best-available reference accepted for this region.",
                          reviewer="tester")
    assert d["rule_id"] == "REF-02"
    assert rb.decision_problems(records, d) == []


# --------------------------------------------------------------------------- #
# Progress during the silent phase.
#
# The first live run of this page sat on "Starting the build..." for thirty
# minutes: Python block-buffers a piped stdout, so the runner's narration never
# arrived, and the screen prints nothing per site anyway. A banner that cannot
# distinguish working from hung is the failure this app has already had once.
# --------------------------------------------------------------------------- #
def test_the_subprocess_runs_unbuffered():
    """Without -u the first progress line can be half an hour late."""
    argv = rb.stage_command("52", "Driftless Area", "/tmp/run", maintainer="me")
    assert argv[1] == "-u"
    assert argv[2].endswith("run_region_batch.py")


def test_the_screen_phase_is_named_even_though_it_narrates_nothing():
    """The screen is most of the wall clock and prints nothing per site; its DEM
    warnings on stderr are the only sign it is alive."""
    noise = "a pygeoutils FutureWarning\n" * 4
    phase = rb.phase_from_log(noise)
    assert "Screening" in phase
    assert "4" in phase, "the elevation-read count is the liveness signal"


def test_a_batch_line_wins_over_the_phase_guess():
    log = ("pygeoutils noise\n"
           "[batch] evidence: 23 / 25 retained (adequate)\n")
    assert rb.phase_from_log(log).startswith("[batch] evidence:")


def test_the_empty_and_early_cases_read_sensibly():
    assert rb.phase_from_log("") == "Starting the build."
    assert "Loading" in rb.phase_from_log("some unrelated output\n")


# --------------------------------------------------------------------------- #
# The two option labels.
#
# Both shipped as their raw ids ("legacy-1819", "Resamples"), which ask the
# reader to already know the codebase. The dataset one is worse than opaque: it
# reads as "one cycle or three", when pooling actually adds different places
# rather than repeat measurements.
# --------------------------------------------------------------------------- #
def test_the_datasets_are_named_by_what_they_contain():
    from streamcurves import nrsa_dataset as nd

    for did in nd.available_datasets():
        label = rb.DATASET_LABELS.get(did, "")
        assert label and label != did, f"{did} has no readable label"
    assert "2018-19" in rb.DATASET_LABELS["legacy-1819"]
    for year in ("2013-14", "2018-19", "2023-24"):
        assert year in rb.DATASET_LABELS["multi-cycle-v1"]


def test_the_dataset_note_corrects_the_obvious_misreading():
    """Only 11 stations appear in all three cycles, so pooling is not repeat
    measurement. A reader who assumes otherwise misreads the sample size."""
    note = rb.DATASET_NOTE
    assert "not repeat visits" in note
    assert "pooled" in note and "default" in note, "must state the new-build default"
    assert "reproduce" in note, "must say why 2018-19 stays selectable"


def test_the_dataset_labels_list_the_build_default_first():
    """The select renders in dict order, so the pooled archive leads."""
    from streamcurves import nrsa_dataset as nd
    assert list(rb.DATASET_LABELS) == [nd.MULTI_CYCLE_DATASET_ID, nd.LEGACY_DATASET_ID]


def test_an_explicit_dataset_always_rides_on_the_command():
    """The builder passes the dataset unconditionally, so every recorded argv is
    self-describing even when the choice equals the default."""
    argv = rb.stage_command("71", "IP", "/tmp/run", maintainer="me",
                            dataset_id="legacy-1819")
    assert argv[argv.index("--nrsa-dataset") + 1] == "legacy-1819"


def test_the_resamples_note_names_the_rules_it_feeds():
    note = rb.RESAMPLES_NOTE
    for rule in ("CURVE-06", "RED-06", "STRAT-06"):
        assert rule in note


def test_the_resamples_note_states_the_real_cost_and_the_real_gain():
    """Measured from the pilots' own manifests: same region, only n_boot differs,
    7.3 vs 32.4 minutes (ECBP) and 8.6 vs 32.9 (NEH). The setting is nearly the
    whole runtime, so the tradeoff belongs beside the box rather than in a runbook."""
    note = rb.RESAMPLES_NOTE
    assert "8 minutes" in note and "33" in note
    assert "5.5" in note and "2.5" in note, "the precision gain, not just the cost"
    assert "0.80" in note, "the threshold that makes the precision matter"


# --------------------------------------------------------------------------- #
# Engine warnings during resampling.
#
# The Interior Plateau run logged "Q25 <= 0, scoring curve is degenerate" 2,076
# times for 2 metrics: 2 real builds and 2,074 resamples, and the resamples say
# "m" because the diagnostic adapter feeds the engine a placeholder column name.
# The note is worth reading once per curve; a thousand anonymous copies bury the
# runner's own narration, which is exactly what the builder page shows you.
# --------------------------------------------------------------------------- #
def test_a_resample_does_not_log_the_engines_degenerate_warning(caplog):
    import logging
    import numpy as np
    import pandas as pd
    from streamcurves import curve_stability as cstab

    zero_heavy = pd.Series([0.0] * 8 + [1.0, 2.0, 3.0, 5.0])   # Q25 is 0
    entry = {"higher_is_better": True, "curve_form": "monotone"}
    with caplog.at_level(logging.WARNING, logger="streamcurves"):
        _, status = cstab._build_points(zero_heavy, entry)
    assert "Q25" not in caplog.text, "a resample must not narrate the fallback"
    assert status, "the status the queue reads is still returned"


def test_the_real_build_still_warns(caplog):
    """Muting the resample must not mute the curve that ships."""
    import logging
    import pandas as pd
    from streamcurves import curves

    frame = pd.DataFrame({"phab_PCT_FAST": [0.0] * 8 + [1.0, 2.0, 3.0, 5.0]})
    cfg = {"phab_PCT_FAST": {"higher_is_better": True, "curve_form": "monotone",
                             "column_name": "phab_PCT_FAST"}}
    with caplog.at_level(logging.WARNING, logger="streamcurves"):
        curves.build_reference_curve(frame, "phab_PCT_FAST", cfg, build_plots=False)
    assert "Q25 <= 0" in caplog.text
    assert "phab_PCT_FAST" in caplog.text, "and it names the metric"


def test_the_logger_level_is_restored_even_on_a_raise():
    import logging
    from streamcurves import curve_stability as cstab

    log = logging.getLogger("streamcurves")
    before = log.level
    try:
        with cstab._engine_quiet():
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert log.level == before


# --------------------------------------------------------------------------- #
# What the banner says when the run ends.
#
# cmd_stage exits 0 whenever it completed and wrote a packet, INCLUDING a run a
# gate refused to stage. The first live build exited 0 with staged=null and an
# empty library folder, and the page announced "Staged." in green over it.
# --------------------------------------------------------------------------- #
def test_a_refused_publish_is_not_reported_as_staged():
    packet = {"staged": None}
    log = ("[batch] staged publish refused: Cannot publish driftless-area: 1 of 20 "
           "STAF functions have no metric and no documented reason\n")
    headline, severity, detail = rb.outcome(0, packet, log)
    assert "Staged" not in headline
    assert severity == "warning"
    assert "1 of 20 STAF functions" in (detail or ""), "say what the gate wants"


def test_a_real_stage_reports_its_version_and_path():
    packet = {"staged": {"version": 3, "path": "/runs/l3-52/library/assessments/x/v3"}}
    headline, severity, detail = rb.outcome(0, packet, "")
    assert headline == "Staged v3."
    assert severity == "success"
    assert "v3" in (detail or "")


def test_a_nonzero_exit_keeps_its_own_meaning():
    headline, severity, _ = rb.outcome(2, None, "")
    assert "landscape" in headline and severity == "warning"


def test_a_missing_packet_is_not_read_as_success():
    headline, severity, _ = rb.outcome(0, None, "")
    assert "Staged" not in headline and severity == "warning"


def test_the_refusal_line_is_pulled_out_of_the_log():
    log = ("noise\n"
           "[batch] staged publish refused: Cannot publish x: reasons here\n"
           "more noise\n")
    assert rb.refusal_from_log(log) == "Cannot publish x: reasons here"
    assert rb.refusal_from_log("nothing\n") is None


def test_the_packet_view_rerenders_when_the_run_ends():
    """_packet() reads a file, and a file appearing is not a reactive event. The
    first live build left the page on the form with a finished packet on disk."""
    import io as _io, pathlib as _pl
    src = _io.open(_pl.Path(__file__).resolve().parents[1] / "views"
                   / "region_builder.py", encoding="utf-8").read()
    body = src[src.index("def packet_view():"):src.index("def _curves_fact(")]
    assert "finished()" in body, "must depend on the run ending"


def test_the_staged_row_does_not_print_none_when_a_gate_refused():
    """The Driftless run rendered "Staged vNone at None", which reads as a bug in
    the page rather than as the coverage gate doing its job."""
    import io as _io, pathlib as _pl
    src = _io.open(_pl.Path(__file__).resolve().parents[1] / "views"
                   / "region_builder.py", encoding="utf-8").read()
    body = src[src.index("def packet_view():"):src.index("def _curves_fact(")]
    assert 'if staged' in body, "the Staged row must branch on whether anything staged"
    assert "a gate refused this run" in body


# --------------------------------------------------------------------------- #
# One build path (2026-09-25): the builder is the ecoregion's Build step
# --------------------------------------------------------------------------- #
def test_the_builder_takes_the_region_from_stage_one_and_lands_on_select_final_curves():
    """The module accepts the region chosen in Region & data (no select of its own
    then), a finished stage run opens its assessment by itself, and the landing is
    Reference curves' Select final curves section."""
    from views import final_selection as fs
    from views import region_builder as view
    src = _view_src()
    assert "def region_builder_server(input, output, session, state: AppState, active=None, region=None)" in src
    assert "def _region_code()" in src and "code, _name = region()" in src
    assert view.FINAL_SECTION == fs.SECTION == "final"
    run = src[src.index("async def run_stage("):src.index("@reactive.event(input.build_run)")]
    assert 'if log_name == "stage.log" and code == 0:' in run
    assert "_open_staged_now(land=True)" in run
    land = src[src.index("def _land_on_final_selection("):src.index("def _open_staged_now(")]
    assert 'state.nav_request.set("curves")' in land
    assert "state.workspace_section_request.set(FINAL_SECTION)" in land


def test_the_predictor_source_control_renders_only_under_the_legacy_method():
    src = _view_src()
    control = src[src.index("def predictor_source_control():"):src.index("def frame_summary():")]
    assert "!= rs.REFERENCE_METHOD_EASI" in control and "return None" in control
    assert '"streamcat":' in control and '"site-engine":' in control
    # the build reads it only under that method, and never raises when it is off the page
    build = src[src.index("def _build():"):src.index("def _poll():")]
    assert 'if method == rs.REFERENCE_METHOD_EASI else "streamcat"' in build
    assert '_inp("build_predictor_source")' in build and "input.build_predictor_source()" not in src


def test_the_builder_answers_nothing_itself_any_more():
    """Undo, the gap cards and "Items left for you" moved to Select final curves; the
    page counts what is left and jumps there. The one-flag REF-02 re-stage stays,
    because it is a build, not a decision."""
    from views import region_builder as view
    src = _view_src()
    for gone in ("def _undo_decision", "def _decisions_block", "def _save_decisions",
                 "def _gap_cards", "def _open_items", "Items left for you", "input.undo_decision",
                 'ns(f"act_{i}")', 'ns(f"gap_r_{j}")'):
        assert gone not in src, f"the builder still carries {gone!r}"
    assert "def _left_for_you(" in src and "def _decisions_line(" in src
    assert "def _restage_ref02" in src and 'ns("restage_ref02")' in src
    assert "campaign_rows(out_root())" in src
    packet = {"open_items": [{"item_id": "a", "blocking": True}, {"item_id": "b", "blocking": False}]}
    text = view.left_for_you_text(packet, 1)
    assert text.startswith("3 items left for you (1 blocking item).")
    assert "Select final curves" in text
    assert "Nothing is left for you to answer" in view.left_for_you_text({}, 0)
    assert chr(8212) not in text


# --------------------------------------------------------------------------- #
# Coverage gaps.
#
# An undocumented STAF function is what refused the Driftless Area run, and it is
# answerable as a BUILD INPUT (stage takes --coverage-exceptions) rather than as an
# edit made afterwards in the app. That distinction is the whole reason the run can
# then stage cleanly and publish with its own provenance instead of the thin
# interactive one views/publish.py writes.
# --------------------------------------------------------------------------- #
PACKET_WITH_GAP = {
    "coverage": {"total": 20, "covered": 19, "missing": 1,
                 "missingFunctionIds": ["channel-floodplain-dynamics"]},
    "uncovered_functions": [
        {"function": "Channel and floodplain dynamics",
         "candidates": ["phab_SINU (nrsa)", "phab_XBKA (nrsa)"]},
    ],
}


def test_a_gap_carries_its_id_label_and_candidates():
    gaps = rb.coverage_gaps(PACKET_WITH_GAP)
    assert len(gaps) == 1
    g = gaps[0]
    assert g["function_id"] == "channel-floodplain-dynamics"
    assert g["label"] == "Channel and floodplain dynamics"
    assert g["candidates"] == ["phab_SINU (nrsa)", "phab_XBKA (nrsa)"]
    assert g["item_id"] == "COVERAGE:channel-floodplain-dynamics"


def test_gaps_are_keyed_on_ids_not_on_list_position():
    """missingFunctionIds and uncovered_functions are parallel lists; pairing them
    positionally would mislabel a gap the moment either is reordered."""
    packet = {
        "coverage": {"missingFunctionIds": ["channel-floodplain-dynamics",
                                            "hyporheic-connectivity"]},
        "uncovered_functions": [
            {"function": "Hyporheic connectivity", "candidates": ["a"]},
            {"function": "Channel and floodplain dynamics", "candidates": ["b"]},
        ],
    }
    by_id = {g["function_id"]: g for g in rb.coverage_gaps(packet)}
    assert by_id["channel-floodplain-dynamics"]["candidates"] == ["b"]
    assert by_id["hyporheic-connectivity"]["candidates"] == ["a"]


def test_a_fully_covered_run_has_no_gaps():
    assert rb.coverage_gaps({"coverage": {"missingFunctionIds": []}}) == []
    assert rb.coverage_gaps({}) == []
    assert rb.coverage_gaps(None) == []


def test_the_reasons_come_from_the_exporter():
    """A local copy would drift from the vocabulary the gate actually enforces."""
    from streamcurves import deep_export

    assert rb.coverage_reasons() is deep_export.FUNCTION_EXCLUSION_REASONS
    # seven since methodology 0.12: a function whose every candidate metric was
    # withheld for insufficient reference support says so (rule REF-06)
    assert len(rb.coverage_reasons()) == 7
    assert "insufficient-reference-support" in rb.coverage_reasons()


def test_a_valid_exception_passes_the_gates_own_validator():
    exc = rb.build_coverage_exception(
        "channel-floodplain-dynamics", "no-suitable-metric",
        "Sinuosity and bank angle both failed their curve checks in this region, so "
        "nothing in the crosswalk informs it here.", recorded_by="tester")
    assert rb.coverage_problems([exc]) == []
    assert exc["functionId"] == "channel-floodplain-dynamics"
    assert exc["recordedBy"] == "tester"


def test_a_short_justification_is_refused_here_not_by_the_build():
    exc = rb.build_coverage_exception("channel-floodplain-dynamics",
                                      "no-suitable-metric", "too short")
    problems = rb.coverage_problems([exc])
    assert problems and "20 characters" in problems[0]


def test_an_off_vocabulary_reason_is_refused():
    exc = rb.build_coverage_exception("channel-floodplain-dynamics", "seemed-fine",
                                      "A justification of more than twenty characters.")
    assert rb.coverage_problems([exc])


def test_an_unknown_function_is_refused():
    exc = rb.build_coverage_exception("not-a-staf-function", "no-suitable-metric",
                                      "A justification of more than twenty characters.")
    problems = rb.coverage_problems([exc])
    assert problems and "canonical STAF functions" in problems[0]


def test_no_exceptions_is_not_a_problem():
    assert rb.coverage_problems([]) == []


# --------------------------------------------------------------------------- #
# The build input and the publish command
# --------------------------------------------------------------------------- #
def test_answered_gaps_ride_into_the_next_build():
    argv = rb.stage_command("52", "Driftless Area", "/tmp/run", maintainer="me",
                            coverage_exceptions="/tmp/run/coverage_exceptions.json")
    assert "--coverage-exceptions" in argv


# --------------------------------------------------------------------------- #
# One-click REF-02 re-stage: the one-flag fix for the commonest blocker
# (3 of the 4 regions built so far had zero Functioning sites).
# --------------------------------------------------------------------------- #
def _refused_packet(enabled=()):
    """The l3-52 shape: staged refused, one blocking reference-tier item."""
    return {
        "region": {"code": "52", "name": "Driftless Area"},
        "policy": {"version": "1.0", "enabled": list(enabled)},
        "open_items": [
            {"item_id": "REF-02:reference_screen", "rule_id": "REF-02",
             "trigger": "reference_tier_fallback", "blocking": True,
             "question": "Accept the best-available tier for this region?",
             "evidence": {"reference_tier": "best_available", "n_retained": 46}},
            {"item_id": "RED-01:a|b", "rule_id": "RED-01", "trigger": "strong_pair",
             "blocking": False, "question": "Keep which one?", "evidence": {}},
        ],
        "staged": None,
        "n_boot": 200,
    }


def test_the_blocking_ref02_item_is_found_and_only_when_not_yet_enabled():
    item = rb.blocking_ref02_item(_refused_packet())
    assert item and item["item_id"] == "REF-02:reference_screen"
    assert rb.blocking_ref02_item(_refused_packet(enabled=[rb.REF02_POLICY_ID])) is None
    assert rb.blocking_ref02_item({"open_items": []}) is None
    assert rb.blocking_ref02_item(None) is None


def test_restage_args_recovers_the_run_record_and_adds_exactly_one_flag():
    manifest = {"inputs": {"nrsa_dataset": {"datasetId": "multi-cycle-v1"}},
                "diagnostics": {"nBoot": 1000}}
    kw = rb.restage_args(_refused_packet(enabled=["data03-thin-metric-finalized"]),
                         manifest)
    # the frame is recovered, never defaulted: this manifest recorded no frame,
    # so the re-stage draws from every stream exactly as the original run did
    assert kw["reference_frame"] == "all"
    # the reference method likewise (methodology 0.12): this manifest carries no
    # reference block, so it was a legacy ECI run and is rebuilt as one. Left to
    # the default, a pooled-archive re-stage would switch to the pressure screen.
    assert kw["reference_method"] == "easi-eci"
    kw = {k: v for k, v in kw.items() if k not in ("reference_frame", "reference_method")}
    assert kw == {"l3_code": "52", "name": "Driftless Area", "n_boot": 1000,
                  "enable_policies": ["data03-thin-metric-finalized",
                                      rb.REF02_POLICY_ID],
                  "dataset_id": "multi-cycle-v1"}
    pressure = {"inputs": {"nrsa_dataset": {"datasetId": "multi-cycle-v1"},
                           "reference": {"method": "pressure-screen"}}}
    assert rb.restage_args(_refused_packet(), pressure)["reference_method"] == "pressure-screen"
    argv = rb.stage_command("52", "Driftless Area", "out", maintainer="t",
                            reference_method="pressure-screen")
    assert argv[argv.index("--reference-method") + 1] == "pressure-screen"
    assert "--reference-method" not in rb.stage_command("52", "Driftless Area", "out",
                                                        maintainer="t")


def test_restage_args_falls_back_to_the_packet_and_the_legacy_dataset():
    """A run staged before the manifest recorded the dataset: n_boot from the
    packet, dataset the absence default (legacy)."""
    kw = rb.restage_args(_refused_packet(), None)
    assert kw["n_boot"] == 200
    assert kw["dataset_id"] == "legacy-1819"
    assert kw["enable_policies"] == [rb.REF02_POLICY_ID]


def test_the_restage_command_is_the_same_constructor_with_one_more_flag():
    kw = rb.restage_args(_refused_packet(), {"diagnostics": {"nBoot": 1000}})
    # The handler probes the run folder and passes any answered gaps back in,
    # exactly like a first build (see region_builder._restage_ref02).
    argv = rb.stage_command(kw["l3_code"], kw["name"], "/tmp/run", maintainer="me",
                            n_boot=kw["n_boot"],
                            enable_policies=kw["enable_policies"],
                            dataset_id=kw["dataset_id"],
                            coverage_exceptions="/tmp/run/coverage_exceptions.json")
    assert argv[3] == "stage"
    assert argv[argv.index("--l3") + 1] == "52"
    assert argv[argv.index("--enable-policy") + 1] == rb.REF02_POLICY_ID
    assert argv[argv.index("--nrsa-dataset") + 1] == "legacy-1819"
    assert argv[argv.index("--n-boot") + 1] == "1000"
    assert argv[argv.index("--coverage-exceptions") + 1].endswith(
        "coverage_exceptions.json")


def test_promote_names_the_subcommand_and_runs_unbuffered():
    argv = rb.promote_command("/tmp/run", maintainer="me")
    assert argv[1] == "-u"
    assert argv[2].endswith("run_region_batch.py")
    assert argv[3] == "promote"
    assert argv[argv.index("--maintainer") + 1] == "me"
    assert argv[argv.index("--publish-root") + 1] == "apps/library"
    assert "--rebake-deep" in argv


def test_promote_can_target_a_scratch_root():
    """Verification must never point at the canonical library."""
    argv = rb.promote_command("/tmp/run", maintainer="me", publish_root="/tmp/scratch",
                              rebake=False)
    assert argv[argv.index("--publish-root") + 1] == "/tmp/scratch"
    assert "--rebake-deep" not in argv


# --------------------------------------------------------------------------- #
# The page's own promises
# --------------------------------------------------------------------------- #
def _view_src() -> str:
    import io as _io, pathlib as _pl
    return _io.open(_pl.Path(__file__).resolve().parents[1] / "views"
                    / "region_builder.py", encoding="utf-8").read()


def test_the_page_carries_no_command_line():
    """The page used to end by handing you a promote command to paste."""
    src = _view_src()
    assert "To publish" not in src
    assert 'packet.get("promote_command")' not in src


def test_publish_is_offered_only_for_a_staged_run():
    src = _view_src()
    block = src[src.index("def _publish_block("):src.index("@reactive.event(input.publish_run)")]
    assert 'staged' in block and "publish_gate_reason" in block
    assert "nothing to publish yet" in block


def test_opening_uses_the_validating_loader():
    """Every other restore path validates the schema and migrates a v1 file; a raw
    json.loads here would skip both."""
    src = _view_src()
    body = src[src.index("def _open_staged_now("):src.index("# ── publish")] \
        if "# ── publish" in src else src[src.index("def _open_staged_now("):]
    assert "sio.load_session_payload" in body


def test_a_run_that_staged_nothing_is_still_openable():
    src = _view_src()
    body = src[src.index("def _session_path("):src.index("def _open_staged(")]
    assert "assessment.streamcurves.json" in body
