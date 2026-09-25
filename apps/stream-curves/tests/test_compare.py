"""streamcurves.compare: what differs between two versions, and what a re-derived run
decided where the owner had decided (campaign Round 1, decision D3).

Synthetic versions first (every branch, deterministic), then the two real Interior Plateau
versions the round's plan names (skipped when the library is not in this checkout).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from streamcurves import compare as cmp
from streamcurves import library as lib
from streamcurves import methodology
from streamcurves import provenance as pv

APP = Path(__file__).resolve().parents[1]
LIBRARY = APP.parent / "library" / "assessments"
V5, V6 = LIBRARY / "interior-plateau" / "v5", LIBRARY / "interior-plateau" / "v6"

FID = "water-soil-quality"
FID2 = "reach-inflow"
PTS_A = [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 1.0}]
PTS_B = [{"x": 0.0, "y": 0.0}, {"x": 12.0, "y": 1.0}]


def _metric(mid: str, pts, **extra) -> dict:
    return {"metricId": mid, "metricName": mid, "curve": {"points": pts}, "referenceRange": [2.0, 6.0],
            "referenceN": 24, "sampleDisposition": "adequate", "confidenceLabel": "Moderate", **extra}


def _manifest(seed: int) -> dict:
    manifest = {"region": {"code": "71", "name": "Interior Plateau"},
                "methodology": {"methodology_version": "0.14"}, "configs": [],
                "inputs": {"nrsa_values": f"v{seed}", "nrsa_sites": "s"},
                "stratifiers": {"registryVersion": 1}, "diagnostics": {"nBoot": 1000, "runSeed": seed},
                "standingDecisions": {"policyVersion": "1.2", "enabledIds": [], "appliedCount": seed}}
    manifest["inputsDigest"] = methodology.inputs_digest(pv.digest_payload_from_manifest(manifest))
    return manifest


def _record(rule, subject, computed, **extra) -> dict:
    return {"rule_id": rule, "subject": subject, "computed": computed, "verdict": "pass",
            "subject_kind": "metric", **extra}


def _write_version(folder: Path, bundle: dict, doc: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    bundle = dict(bundle)
    bundle["contentDigest"] = lib.content_digest(bundle)
    (folder / cmp.BUNDLE_FILE).write_text(json.dumps(bundle), encoding="utf-8")
    (folder / cmp.PROVENANCE_FILE).write_text(json.dumps(doc), encoding="utf-8")
    return folder


@pytest.fixture
def pair(tmp_path):
    """A (recorded) and B (re-derived): one identical curve, one moved curve, a metric only
    A scores (the owner removed it in A, B scores it anyway), a documented gap in A that B
    scores, an answered CURVE-07, an approved SELECT-01, registers and ledgers."""
    a_bundle = {"assessmentId": "t", "metricsByFunction": [
        {"functionId": FID, "metrics": [_metric("spring-chem-cond", PTS_A), _metric("spring-chem-ph", PTS_A)]}],
        "functionCoverage": {"total": 20, "covered": 1, "excluded": 1, "missing": 18,
                             "exclusions": [{"functionId": FID2, "reason": "no-suitable-metric",
                                             "justification": "Nothing in the crosswalk measures it here.",
                                             "recordedBy": "GM"}]}}
    b_bundle = {"assessmentId": "t", "metricsByFunction": [
        {"functionId": FID, "metrics": [_metric("spring-chem-cond", PTS_A), _metric("spring-chem-ph", PTS_B),
                                        _metric("spring-chem-turb", PTS_A)]},
        {"functionId": FID2, "metrics": [_metric("spring-rddensws", PTS_A)]}],
        "functionCoverage": {"total": 20, "covered": 2, "excluded": 0, "missing": 18, "exclusions": []}}
    a_doc = {"manifest": _manifest(1), "records": [
        _record("REF-15", "cd-1", {"metric": "chem_TURB", "action": "remove", "functions": []},
                subject_kind="owner_decision", inputs={"recordedBy": "GM", "rationale": "Turbidity is noise here."}),
        _record("CURVE-07", "chem_PH", {"curve_status": "degenerate", "reviewer_decision": "reviewer_finalized"},
                reviewer_action="accept", reviewer="GM", reviewer_rationale="Fine as proposed."),
        _record("CURVE-07", "chem_COND", {"curve_status": "auto_ok", "reviewer_decision": "auto_finalized"}),
        _record("COV-01", FID2, {"function_id": FID2}, subject_kind="function", reviewer_action="accept"),
        _record("SELECT-01", FID, {"n_metrics": 3, "bundle_metrics": ["spring-chem-cond", "spring-chem-ph", "x"]},
                subject_kind="function", reviewer_action="accept", review_required=True),
        _record("STRAT-09", "DA", {"tier": 1}, reviewer_action="accept"),
    ], "reviewQueue": {"items": [{"item_id": "CURVE-07:chem_PH"}, {"item_id": "SELECT-01:" + FID}]},
        "candidateRegister": {"schema": 1, "rows": [
            {"functionId": FID, "candidateKey": "k1", "status": "selected", "rule": "SELECT-04"},
            {"functionId": FID, "candidateKey": "k2", "status": "eligible", "rule": "SELECT-04"}],
            "counts": {"selected": 1}},
        "metricLedger": {"schema": "rebuild-ledger/1", "rows": [
            {"metric": "chem_COND", "functionId": FID, "function": "Water", "disposition": "refitted", "rule": "REF-11",
             "basisDigest": "sha256:a", "option": "local"},
            {"metric": "chem_PH", "functionId": FID, "function": "Water", "disposition": "refitted", "rule": "REF-11",
             "basisDigest": "sha256:b", "option": "local"}]}}
    b_doc = {"manifest": _manifest(2), "records": [
        _record("CURVE-07", "chem_PH", {"curve_status": "degenerate", "reviewer_decision": "pending"},
                review_required=True),
        _record("CURVE-07", "chem_COND", {"curve_status": "auto_ok", "reviewer_decision": "auto_finalized"}),
        _record("SELECT-01", FID, {"n_metrics": 3, "bundle_metrics": ["spring-chem-cond", "spring-chem-ph", "spring-chem-turb"]},
                subject_kind="function", review_required=True),
        _record("STRAT-09", "DA", {"tier": 1}, reviewer_action="reject"),
    ], "reviewQueue": {"items": [{"item_id": "CURVE-07:chem_PH"}, {"item_id": "RED-01:a|b"}]},
        "candidateRegister": {"schema": 1, "rows": [
            {"functionId": FID, "candidateKey": "k1", "status": "selected", "rule": "SELECT-04"},
            {"functionId": FID, "candidateKey": "k2", "status": "selected", "rule": "SELECT-04"},
            {"functionId": FID, "candidateKey": "k3", "status": "eligible", "rule": "SELECT-04"}],
            "counts": {"selected": 2}},
        "metricLedger": {"schema": "rebuild-ledger/1", "rows": [
            {"metric": "chem_COND", "functionId": FID, "function": "Water", "disposition": "refitted", "rule": "REF-11",
             "basisDigest": "sha256:a", "option": "local"},
            {"metric": "chem_PH", "functionId": FID, "function": "Water", "disposition": "carried", "rule": "REF-05",
             "basisDigest": "sha256:b", "option": "carried"},
            {"metric": "chem_TURB", "functionId": FID, "function": "Water", "disposition": "refitted", "rule": "REF-11",
             "basisDigest": "sha256:c", "option": "local"}]}}
    a = _write_version(tmp_path / "a" / "v1", a_bundle, a_doc)
    b = _write_version(tmp_path / "b" / "v2", b_bundle, b_doc)
    return a, b


# --------------------------------------------------------------------------- #
# reading a version
# --------------------------------------------------------------------------- #
def test_a_run_folder_resolves_to_the_version_it_staged_else_its_preview(pair, tmp_path):
    a, b = pair
    run = tmp_path / "l3-71"
    run.mkdir()
    (run / "review_packet.json").write_text(json.dumps({"staged": {"version": 2, "path": str(b)}}), encoding="utf-8")
    assert cmp.resolve_version_dir(run) == b
    bundle, doc = cmp.load_version(run)
    assert bundle["assessmentId"] == "t" and doc["manifest"]["diagnostics"]["runSeed"] == 2
    # a run a gate refused to stage: the preview bundle and the decision log, manifest inlined
    refused = tmp_path / "l3-9"
    refused.mkdir()
    (refused / "review_packet.json").write_text(json.dumps({"staged": None}), encoding="utf-8")
    (refused / "preview_bundle.deep.json").write_text((b / cmp.BUNDLE_FILE).read_text(encoding="utf-8"), encoding="utf-8")
    (refused / "decision_provenance_log.json").write_text(json.dumps({"records": [], "manifestRef": "run_manifest.json"}), encoding="utf-8")
    (refused / "run_manifest.json").write_text(json.dumps(_manifest(3)), encoding="utf-8")
    bundle, doc = cmp.load_version(refused)
    assert doc["manifest"]["diagnostics"]["runSeed"] == 3 and bundle["assessmentId"] == "t"
    assert cmp.version_label(b) == "b/v2" and cmp.version_label(refused) == "l3-9"
    with pytest.raises(FileNotFoundError):
        cmp.load_version(tmp_path / "nowhere")


# --------------------------------------------------------------------------- #
# curves, decisions, queue, manifest
# --------------------------------------------------------------------------- #
def test_compare_reports_curves_with_their_largest_delta_decisions_and_the_queue(pair):
    a, b = pair
    rep = cmp.compare(a, b)
    c = rep["curves"]
    assert c["identical"] == ["spring-chem-cond"] and list(c["differ"]) == ["spring-chem-ph"]
    assert c["max_delta"]["spring-chem-ph"] == pytest.approx(2.0)
    assert any("points (max delta 2)" in n for n in c["differ"]["spring-chem-ph"])
    assert c["only_a"] == [] and c["only_b"] == ["spring-chem-turb", "spring-rddensws"]
    d = rep["decisions"]
    assert d["differ"]["STRAT-09:DA"]["reviewer_action"] == ("accept", "reject")
    # a record both sides hold with different answers differs; only records a reviewer
    # acted on count as decisions one side lacks (REF-15 carries no reviewer field)
    assert "CURVE-07:chem_PH" in d["differ"] and "SELECT-01:" + FID in d["differ"]
    assert d["only_a"] == ["COV-01:" + FID2] and d["only_b"] == []
    assert rep["queue"] == {"only_a": ["SELECT-01:" + FID], "only_b": ["RED-01:a|b"], "common": 1}
    assert rep["manifest"]["methodology"]["equal"] and not rep["manifest"]["inputsDigest"]["equal"]
    assert rep["manifest"]["standingDecisions"]["a"]["appliedCount"] == 1
    # the numeric helpers
    assert cmp.num_delta([1, 2], [1, 4]) == 2 and cmp.num_delta({"a": 1}, {"b": 1}) is None
    assert cmp.num_delta(True, 1) is None
    assert cmp.curve_diff({"points": PTS_A}, {"points": PTS_A}) == []


# --------------------------------------------------------------------------- #
# registers and ledgers
# --------------------------------------------------------------------------- #
def test_registers_diff_through_candidates_diff_and_say_when_none_is_recorded(pair):
    a, b = pair
    reg = cmp.registers(a, b)
    assert reg["available"] and reg["a"]["rows"] == 2 and reg["b"]["rows"] == 3
    kinds = {(c.get("candidateKey") or (c.get("after") or {}).get("candidateKey")): c["change"] for c in reg["changes"]}
    assert kinds == {"k2": "changed", "k3": "added"}
    assert reg["counts"] == {"added": 1, "removed": 0, "changed": 1}
    changed = next(c for c in reg["changes"] if c["change"] == "changed")
    assert changed["fields"] == ["status"] and changed["before"]["status"] == "eligible"
    # a document without a register: the report says so, never an empty diff
    none = cmp.registers({"records": []}, {"candidateRegister": {"rows": []}})
    assert not none["available"] and none["changes"] == [] and "A records no candidate register" in none["note"]


def test_ledgers_diff_per_metric_and_function(pair):
    a, b = pair
    led = cmp.ledgers(a, b)
    assert led["available"] and led["same"] == 1
    by = {(c["metric"], c["functionId"]): c for c in led["changes"]}
    assert by[("chem_PH", FID)]["change"] == "changed"
    assert by[("chem_PH", FID)]["fields"] == ["disposition", "rule", "option"]
    assert by[("chem_PH", FID)]["before"]["disposition"] == "refitted" and by[("chem_PH", FID)]["after"]["disposition"] == "carried"
    assert by[("chem_TURB", FID)]["change"] == "added" and by[("chem_TURB", FID)]["after"]["disposition"] == "refitted"
    assert led["counts"] == {"added": 1, "removed": 0, "changed": 1}
    # ledger documents and provenance documents are accepted as well as paths
    same = cmp.ledgers(json.loads((a / cmp.PROVENANCE_FILE).read_text(encoding="utf-8")),
                       json.loads((a / cmp.PROVENANCE_FILE).read_text(encoding="utf-8"))["metricLedger"])
    assert same["changes"] == [] and same["same"] == 2
    missing = cmp.ledgers({"records": []}, a)
    assert not missing["available"] and "A carries no rebuild ledger" in missing["note"]


# --------------------------------------------------------------------------- #
# digests
# --------------------------------------------------------------------------- #
def test_digest_rederives_both_digests_from_the_stored_files(pair):
    a, _ = pair
    d = cmp.digest(a)
    assert d["inputsDigest"]["equal"] and d["inputsDigest"]["problem"] is None
    assert d["inputsDigest"]["recorded"] == d["inputsDigest"]["rederived"]
    bundle = json.loads((a / cmp.BUNDLE_FILE).read_text(encoding="utf-8"))
    assert d["contentDigest"]["rederived"] == lib.content_digest(bundle)
    assert d["contentDigest"]["recorded"] == bundle["contentDigest"]
    # a tampered manifest no longer replays, and the report says so rather than raising
    doc = json.loads((a / cmp.PROVENANCE_FILE).read_text(encoding="utf-8"))
    doc["manifest"]["inputs"]["nrsa_values"] = "other"
    (a / cmp.PROVENANCE_FILE).write_text(json.dumps(doc), encoding="utf-8")
    assert not cmp.digest(a)["inputsDigest"]["equal"]


# --------------------------------------------------------------------------- #
# D3: what the re-derived run decided where the owner had decided
# --------------------------------------------------------------------------- #
def test_anchors_are_where_the_curve_reaches_each_band_in_iqr_units():
    assert cmp.anchors(PTS_A) == pytest.approx([3.9, 6.9])
    rows = cmp.anchor_differences(_metric("m", PTS_A), _metric("m", PTS_B))
    assert [r["band"] for r in rows] == list(cmp.DEEP_INDEX_BANDS)
    # the IQR is 4 (referenceRange 2 to 6): band 0.39 moves from 3.9 to 4.68, +0.78 / 4 = +0.195 IQR
    assert rows[0]["a"] == pytest.approx(3.9) and rows[0]["b"] == pytest.approx(4.68)
    assert rows[0]["deltaIqr"] == pytest.approx(0.195) and rows[0]["iqr"] == 4.0
    # no curve on one side: nothing to compare; no range: the shift in the metric's units only
    assert cmp.anchor_differences(_metric("m", PTS_A), {"metricId": "m"}) == []
    bare = cmp.anchor_differences({"curve": {"points": PTS_A}}, {"curve": {"points": PTS_B}})
    assert bare[0]["delta"] == pytest.approx(0.78) and bare[0]["deltaIqr"] is None
    assert cmp.metric_id_of("phab_XCMGW") == "spring-phab-xcmgw" and cmp.metric_id_of("spring-x") == "spring-x"


def test_differences_from_owner_decisions_list_every_recorded_decision(pair):
    a, b = pair
    bundle_a, doc_a = cmp.load_version(a)
    bundle_b, doc_b = cmp.load_version(b)
    items = cmp.differences_from_owner_decisions(doc_a, doc_b, recorded_bundle=bundle_a, rederived_bundle=bundle_b)
    by = {(i["rule"], i["subject"]): i for i in items}
    assert [i["rule"] for i in items] == ["REF-15", "CURVE-07", "COV-01", "SELECT-01"]
    # REF-15: removed in A, scored by B
    ref15 = by[("REF-15", "cd-1")]
    assert ref15["verdict"] == cmp.DIFFER and ref15["metric"] == "chem_TURB"
    assert "scores it in " + FID in ref15["detail"] and ref15["recorded"]["by"] == "GM"
    # CURVE-07: accepted in A, held for review again by B, with the anchors of both curves
    c07 = by[("CURVE-07", "chem_PH")]
    assert c07["verdict"] == cmp.DIFFER and c07["rederived"]["outcome"] == "pending"
    assert "holds the curve for review again" in c07["detail"]
    assert c07["anchors"] and c07["anchors"][0]["deltaIqr"] == pytest.approx(0.195)
    # an auto-finalized CURVE-07 is not an owner decision
    assert ("CURVE-07", "chem_COND") not in by
    # COV-01: a documented gap in A that B scores
    cov = by[("COV-01", FID2)]
    assert cov["verdict"] == cmp.DIFFER and "spring-rddensws" in cov["detail"]
    assert cov["recorded"]["reason"] == "no-suitable-metric" and cov["recorded"]["by"] == "GM"
    # SELECT-01: the set changed, so the approval does not carry
    s01 = by[("SELECT-01", FID)]
    assert s01["verdict"] == cmp.DIFFER and "does not carry" in s01["detail"]
    assert cmp.owner_decision_counts(items) == {cmp.AGREE: 0, cmp.DIFFER: 4, cmp.NOT_APPLICABLE: 0}


def test_the_verdicts_read_agree_and_not_applicable_where_they_should(pair):
    a, b = pair
    bundle_a, doc_a = cmp.load_version(a)
    # B agrees with A on everything: the same bundle, the same records
    items = cmp.differences_from_owner_decisions(doc_a, doc_a, recorded_bundle=bundle_a, rederived_bundle=bundle_a)
    by = {(i["rule"], i["subject"]): i for i in items}
    assert by[("REF-15", "cd-1")]["verdict"] == cmp.AGREE
    assert by[("CURVE-07", "chem_PH")]["verdict"] == cmp.AGREE and by[("CURVE-07", "chem_PH")]["anchors"] == []
    assert by[("COV-01", FID2)]["verdict"] == cmp.AGREE and by[("SELECT-01", FID)]["verdict"] == cmp.AGREE
    # no bundle to read: REF-15 and COV-01 cannot be judged; a SELECT-01 B never records is not applicable
    items = cmp.differences_from_owner_decisions(doc_a, {"records": []})
    by = {(i["rule"], i["subject"]): i for i in items}
    assert by[("REF-15", "cd-1")]["verdict"] == cmp.NOT_APPLICABLE
    assert by[("CURVE-07", "chem_PH")]["verdict"] == cmp.NOT_APPLICABLE
    assert by[("SELECT-01", FID)]["verdict"] == cmp.NOT_APPLICABLE
    # a set within the maximum needs no approval
    within = {"records": [_record("SELECT-01", FID, {"n_metrics": 2, "bundle_metrics": ["a", "b"]},
                                  subject_kind="function", review_required=False)]}
    got = cmp.differences_from_owner_decisions(doc_a, within)
    assert next(i for i in got if i["rule"] == "SELECT-01")["verdict"] == cmp.NOT_APPLICABLE
    # a curve B carries without holding it agrees with an accepted answer
    carried = {"records": []}
    got = cmp.differences_from_owner_decisions(doc_a, carried, recorded_bundle=bundle_a, rederived_bundle=bundle_a)
    assert next(i for i in got if i["subject"] == "chem_PH")["verdict"] == cmp.AGREE
    # the other REF-15 actions
    for action, verdict in (("unmap", cmp.DIFFER), ("include", cmp.AGREE)):
        doc = {"records": [_record("REF-15", "cd-2", {"metric": "chem_PH", "action": action, "functions": [FID]},
                                   subject_kind="owner_decision")]}
        got = cmp.differences_from_owner_decisions(doc, {"records": []}, recorded_bundle=bundle_a, rederived_bundle=bundle_a)
        assert got[0]["verdict"] == verdict, action
    doc = {"records": [_record("REF-15", "cd-3", {"metric": "chem_PH", "action": "source", "functions": [FID],
                                                  "source": {"kind": "entered", "title": "Owner thresholds"}},
                               subject_kind="owner_decision")]}
    bundle_b, _ = cmp.load_version(b)
    got = cmp.differences_from_owner_decisions(doc, {"records": []}, recorded_bundle=bundle_a, rederived_bundle=bundle_b)
    assert got[0]["verdict"] == cmp.DIFFER and got[0]["anchors"] and got[0]["recorded"]["source"] == "Owner thresholds"


# --------------------------------------------------------------------------- #
# the whole report, its rows, its CSV, its markdown, and the command line
# --------------------------------------------------------------------------- #
def test_the_full_report_carries_every_section_and_flattens_to_rows(pair):
    a, b = pair
    rep = cmp.full_report(a, b, with_registers=True, with_ledger=True, with_digests=True, with_owner_decisions=True)
    assert set(rep) >= {"manifest", "curves", "decisions", "queue", "registers", "ledgers", "digests", "ownerDecisions"}
    rows = cmp.report_rows(rep)
    sections = {r["section"] for r in rows}
    assert sections >= {"manifest", "curves", "decisions", "queue", "register", "ledger", "digest", "owner decisions"}
    csv_text = cmp.report_csv(rep)
    assert csv_text.startswith("section,subject,field,a,b,note\n") and "spring-chem-ph" in csv_text
    md = cmp.report_markdown(rep)
    assert md.startswith("# Version comparison") and "## Owner decisions" in md and "| subject |" in md
    text = cmp.report_text(rep)
    assert "curves: 1 identical, 1 differ" in text and "owner decisions: 0 agree, 4 differ" in text
    assert "A inputsDigest: replays" in text
    for t in (csv_text, md, text):
        assert chr(8212) not in t


def test_the_command_line_is_a_thin_wrapper(pair, tmp_path):
    a, b = pair
    out_json, out_md = tmp_path / "rep.json", tmp_path / "rep.md"
    proc = subprocess.run([sys.executable, "-B", str(APP / "scripts" / "compare_runs.py"),
                           "--a", str(a), "--b", str(b), "--registers", "--ledger", "--digest",
                           "--owner-decisions", "--json", str(out_json), "--markdown", str(out_md)],
                          capture_output=True, text=True, cwd=str(APP), timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "owner decisions: 0 agree, 4 differ, 0 not applicable" in proc.stdout
    rep = json.loads(out_json.read_text(encoding="utf-8"))
    assert rep["ownerDecisions"]["counts"]["differ"] == 4 and rep["ledgers"]["available"]
    assert out_md.read_text(encoding="utf-8").startswith("# Version comparison")
    # the classic entry point is still importable by name
    import importlib.util
    spec = importlib.util.spec_from_file_location("compare_runs_under_test", APP / "scripts" / "compare_runs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.compare is cmp.compare


# --------------------------------------------------------------------------- #
# the two real versions the plan names
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not (V5 / cmp.BUNDLE_FILE).is_file() or not (V6 / cmp.BUNDLE_FILE).is_file(),
                    reason="interior-plateau v5 and v6 are not in this checkout")
def test_interior_plateau_v5_against_v6():
    rep = cmp.full_report(V5, V6, with_registers=True, with_ledger=True, with_digests=True,
                          with_owner_decisions=True)
    c = rep["curves"]
    assert len(c["identical"]) + len(c["differ"]) == 29 and c["only_a"] == ["spring-chem-chla"]
    assert all(c["max_delta"][m] is None or c["max_delta"][m] >= 0 for m in c["differ"])
    # published before the register entered provenance: said, never read as empty
    assert not rep["registers"]["available"] and "no candidate register" in rep["registers"]["note"]
    # the ledgers are built from the sessions: v6 carries what v5 fitted
    led = rep["ledgers"]
    assert led["available"] and led["counts"]["changed"] > 20
    carried = [ch for ch in led["changes"] if (ch.get("after") or {}).get("disposition") == "carried"]
    assert carried and all(ch["before"]["disposition"] == "refitted" for ch in carried)
    for side in ("a", "b"):
        assert rep["digests"][side]["inputsDigest"]["equal"] and rep["digests"][side]["contentDigest"]["equal"]
    od = rep["ownerDecisions"]
    assert {i["rule"] for i in od["items"]} <= set(cmp.OWNER_RULES)
    assert od["counts"][cmp.DIFFER] == 0
