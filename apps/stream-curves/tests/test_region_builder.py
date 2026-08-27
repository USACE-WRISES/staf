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
    assert "not repeat measurements" in note
    assert "reproducibility" in note, "must say why the older default stands"


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
    body = src[src.index("def packet_view():"):src.index("def _open_items(")]
    assert "finished()" in body, "must depend on the run ending"


def test_the_staged_row_does_not_print_none_when_a_gate_refused():
    """The Driftless run rendered "Staged vNone at None", which reads as a bug in
    the page rather than as the coverage gate doing its job."""
    import io as _io, pathlib as _pl
    src = _io.open(_pl.Path(__file__).resolve().parents[1] / "views"
                   / "region_builder.py", encoding="utf-8").read()
    body = src[src.index("def packet_view():"):src.index("def _open_items(")]
    assert 'if staged' in body, "the Staged row must branch on whether anything staged"
    assert "a gate refused this run" in body


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
    assert len(rb.coverage_reasons()) == 6


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
    body = src[src.index("def _open_staged("):src.index("# ── publish")] \
        if "# ── publish" in src else src[src.index("def _open_staged("):]
    assert "sio.load_session_payload" in body


def test_a_run_that_staged_nothing_is_still_openable():
    src = _view_src()
    body = src[src.index("def _session_path("):src.index("def _open_staged(")]
    assert "assessment.streamcurves.json" in body
