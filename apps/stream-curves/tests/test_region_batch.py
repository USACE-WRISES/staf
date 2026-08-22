"""The batch mode (methodology 0.7): run_evidence + assemble equal run, the
staged stage/promote path, the packet, and the canonical-library guard.

Offline (no screen, no StreamCat, 20 resamples) on the Eastern Corn Belt
Plains, the smallest pilot, so the module fixture runs once in under a minute.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from streamcurves import decisions as dec
from streamcurves import library as lib
from streamcurves import provenance as pv
from streamcurves import regional_agent as ra
from streamcurves import review_packet as rp

L3, NAME = "55", "Eastern Corn Belt Plains"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_region_batch.py"
KW = dict(do_screen=False, use_streamcat=False, diagnostics_n_boot=20)


@pytest.fixture(scope="module")
def evidence():
    return ra.run_evidence(L3, NAME, **KW)


@pytest.fixture(scope="module")
def assembled(evidence):
    return ra.assemble(evidence)


def test_run_equals_run_evidence_plus_assemble(evidence, assembled):
    whole = ra.run(L3, NAME, **KW)
    assert lib.content_digest(whole["bundle"]) == lib.content_digest(assembled["bundle"])
    assert whole["confidence"] == assembled["confidence"]
    assert whole["intended_metrics"] == assembled["intended_metrics"]
    assert whole["run_seed"] == evidence["run_seed"]
    assert set(whole) == set(assembled)


def test_assemble_does_not_mutate_the_evidence(evidence):
    before = json.dumps(evidence["curve_review"], sort_keys=True, default=str)
    flagged = ra.run_state.flagged_metrics(evidence["curve_review"])
    if flagged:
        ra.assemble(evidence, finalize_metrics={flagged[0]: "test finalization"},
                    finalize_actor="tester")
    ra.assemble(evidence, remove_metrics={evidence["curve_rows"] and next(iter(evidence["curve_rows"])): "x"},
                finalize_actor="tester")
    assert json.dumps(evidence["curve_review"], sort_keys=True, default=str) == before


def test_assemble_lifts_the_mandatory_review_cap_when_adjudicated(evidence, assembled):
    manifest = pv.build_run_manifest(assembled, started_at="a", finished_at="a")
    doc = pv.build_provenance(assembled, manifest, timestamp="a")
    items = [i for i in doc["reviewQueue"]["items"] if i["trigger"] == "n_exploratory"]
    assert items, "the 18-site unscreened pool is exploratory, so DATA-05 items exist"
    res = dec.apply_policy(doc, dec.load_policy(), result=assembled)
    assert res.decisions
    again = ra.assemble(evidence, reviewer_decisions=res.decisions)
    lifted = [mk for mk in assembled["confidence"]
              if "mandatory_review_open" in assembled["confidence"][mk]["caps_applied"]
              and "mandatory_review_open" not in again["confidence"][mk]["caps_applied"]]
    assert lifted, "a recorded adjudication must lift the mandatory_review_open cap"


def test_select01_records_count_from_the_bundle(assembled):
    manifest = pv.build_run_manifest(assembled, started_at="a", finished_at="a")
    doc = pv.build_provenance(assembled, manifest, timestamp="a")
    recs = [r for r in doc["records"] if r["rule_id"] == "SELECT-01"]
    assert recs and all("bundle_n_metrics" in r["computed"] for r in recs)
    blocks = {b["functionId"]: len(b["metrics"]) for b in assembled["bundle"]["metricsByFunction"]}
    for r in recs:
        if r["subject"] in blocks:
            assert r["computed"]["bundle_n_metrics"] == blocks[r["subject"]]
            assert r["review_required"] == (max(r["computed"]["n_metrics"], blocks[r["subject"]]) > 2)


def test_manifest_records_the_policy_block(assembled):
    assembled = dict(assembled)
    assembled["standing_decisions"] = {"policyVersion": "1.0", "sha256": "sha256:x",
                                       "enabledIds": [], "appliedCount": 3, "confirmedBy": None}
    manifest = pv.build_run_manifest(assembled, started_at="a", finished_at="a")
    assert manifest["standingDecisions"]["appliedCount"] == 3
    assert manifest["standingDecisions"]["confirmedBy"] is None


def test_packet_lists_every_policy_decision_and_open_item(assembled, tmp_path):
    manifest = pv.build_run_manifest(assembled, started_at="a", finished_at="a")
    doc = pv.build_provenance(assembled, manifest, timestamp="a")
    policy = dec.load_policy()
    res = dec.apply_policy(doc, policy, result=assembled)
    packet = rp.build_packet(assembled, doc, res.as_dict(), policy_meta=policy["meta"],
                             enabled=[], staged=None, promote_command="promote ...",
                             approvals=res.portfolio_approvals)
    assert len(packet["decisions_applied"]) == len(res.decisions)
    assert len(packet["open_items"]) == len(res.uncovered)
    assert {r["metric"] for r in packet["curves"]} == set(assembled["curve_rows"])
    assert len(packet["portfolio"]) >= 20
    jp, mp = rp.write_packet(packet, tmp_path)
    text = mp.read_text(encoding="utf-8")
    assert "## 1. Items left for you" in text and "## 9. Promote" in text
    assert "promote ..." in text
    for d in res.decisions[:3]:
        assert d["decision_class"] in text
    gallery = rp.write_curve_gallery(assembled, tmp_path / "gallery.png")
    assert gallery is None or gallery.stat().st_size > 1000


def test_canonical_publish_refuses_a_pending_marker(monkeypatch, tmp_path):
    """The library guard: a provenance still carrying a pending reviewer cannot
    be published to the canonical root, and publishes fine to a staging root."""
    from conftest import documented_exclusions
    import numpy as np
    import pandas as pd
    from streamcurves import session_io as sio
    from streamcurves.deep_export import build_deep_assessment_bundle

    rows = {"m": {"metric": "m", "curve_status": "complete", "stratum": np.nan,
                  "curve_points": pd.DataFrame({"metric_value": [0, 9, 25, 75],
                                                "index_score": [1, 0.7, 0.3, 0]})}}
    mapping = pd.DataFrame({"metric_key": ["m"], "discipline": ["Hydrology"],
                            "function_label": ["Catchment hydrology"], "sort_order": [1]})
    region = {"kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains"}
    bundle = build_deep_assessment_bundle(rows, mapping, {}, {
        "region": region, "functionCoverageExceptions": documented_exclusions()})
    payload = sio.dump_session_fields({"region_of_applicability": region, "app_data_loaded": True},
                                      session_name="t")
    doc = {"records": [{"reviewer": "standing-policy:x " + dec.PENDING_SUFFIX}],
           "reviewQueue": {"items": [], "counts": {"open": 0}}, "manifest": {}}
    staging = tmp_path / "staging"
    (staging / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(staging))
    assert lib.publish_version("t", {"assessmentName": "T", "region": region}, payload, bundle,
                               provenance=doc) == 1
    monkeypatch.setattr(lib, "is_canonical_root", lambda: True)
    with pytest.raises(ValueError, match="pending owner confirmation"):
        lib.publish_version("t", {"assessmentName": "T", "region": region}, payload, bundle,
                            provenance=doc)
    # The recorded command line quotes the owner's own --approve-portfolio input
    # verbatim; that is not a pending decision, so it publishes canonically.
    argv_only = {"records": [{"reviewer": "owner"}],
                 "reviewQueue": {"items": [], "counts": {"open": 0}},
                 "manifest": {"agent": {"argv": [
                     "--approve-portfolio",
                     "fn=owner-draft " + dec.PENDING_SUFFIX + ":Substrate size and wood."]}}}
    assert not dec.is_pending(argv_only)
    assert dec.is_pending(doc) and dec.pending_locations(doc) == ["records.0.reviewer"]
    assert lib.publish_version("t", {"assessmentName": "T", "region": region}, payload, bundle,
                               provenance=argv_only) == 2


@pytest.fixture(scope="module")
def staged_run(tmp_path_factory):
    from conftest import documented_exclusions
    base = tmp_path_factory.mktemp("batch")
    out = base / "run"
    # Offline, the StreamCat join is skipped, so the Hydrology functions need a
    # documented exception or the coverage gate refuses the staged publish.
    exceptions = base / "exceptions.json"
    exceptions.write_text(json.dumps(documented_exclusions(
        reason="data-unavailable",
        justification="Offline test run without the StreamCat landscape join.")),
        encoding="utf-8")
    env = dict(os.environ)
    env.pop("STAF_LIBRARY_ROOT", None)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "stage", "--l3", L3, "--name", NAME, "--out", str(out),
         "--no-screen", "--no-streamcat", "--n-boot", "20", "--maintainer", "tester",
         "--coverage-exceptions", str(exceptions),
         "--enable-policy", "curve07-thin-metric-finalized",
         "--enable-policy", "data03-thin-metric-finalized",
         "--enable-policy", "data06-insufficient-finalized"],
        capture_output=True, text=True, env=env, timeout=900)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    return out, proc.stdout


def test_stage_writes_the_packet_and_a_staged_version(staged_run):
    out, log = staged_run
    packet = json.loads((out / "review_packet.json").read_text(encoding="utf-8"))
    assert packet["staged"] and Path(packet["staged"]["path"]).is_dir()
    assert (out / "library" / "assessments").is_dir()
    assert (out / "review_packet.md").is_file()
    assert (out / "standing_decisions_applied.json").is_file()
    assert (out / "run_manifest.json").is_file()
    assert packet["decisions_applied"], "the exploratory pool must have produced standing decisions"
    prov = json.loads((Path(packet["staged"]["path"]) / "provenance.json").read_text(encoding="utf-8"))
    assert dec.is_pending(prov), "a staged version carries the pending marker"
    assert prov["manifest"]["standingDecisions"]["appliedCount"] == len(packet["decisions_applied"])
    assert "pass 1" in log


def test_stage_reaches_a_fixpoint_and_refuses_the_canonical_root(staged_run, tmp_path):
    out, log = staged_run
    assert "did not settle" not in log
    applied = json.loads((out / "standing_decisions_applied.json").read_text(encoding="utf-8"))
    keys = [(d["rule_id"], d["subject"]) for d in applied["decisions"]]
    assert len(keys) == len(set(keys)), "no item decided twice"
    # the staged root is always <out>/library; pointing it at apps/library is refused
    if str(SCRIPT.parent) not in sys.path:
        sys.path.insert(0, str(SCRIPT.parent))
    from run_region_batch import _staged_root  # noqa: E402
    with pytest.raises(SystemExit):
        _staged_root(ra.CANONICAL_LIBRARY.parent.parent / "apps")


def test_promote_keeps_the_digest_and_names_the_owner(staged_run, tmp_path):
    out, _ = staged_run
    target = tmp_path / "promoted"
    (target / "assessments").mkdir(parents=True)
    env = dict(os.environ)
    env.pop("STAF_LIBRARY_ROOT", None)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "promote", "--out", str(out), "--maintainer", "owner",
         "--publish-root", str(target), "--date", "2026-08-22"],
        capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    record = json.loads((out / "promote_record.json").read_text(encoding="utf-8"))
    assert record["contentDigestMatchesStaged"] is True
    vdir = target / "assessments" / "eastern-corn-belt-plains" / f"v{record['publishedVersion']}"
    prov = json.loads((vdir / "provenance.json").read_text(encoding="utf-8"))
    assert not dec.is_pending(prov)
    adjudicated = [r for r in prov["records"] if r.get("reviewer_action")]
    assert adjudicated and all(r["reviewer"] == "owner" for r in adjudicated)
    assert all(str(r["reviewer_rationale_origin"]).startswith("standing_policy:")
               for r in adjudicated if r.get("reviewer_decision_class") != "owner-override")
    assert prov["manifest"]["standingDecisions"]["confirmedBy"] == "owner"
    meta = json.loads((vdir / "meta.json").read_text(encoding="utf-8"))
    for a in meta.get("portfolioApprovals") or []:
        assert a["approvedBy"] == "owner"


def test_promote_refuses_scope_changing_overrides(staged_run, tmp_path):
    out, _ = staged_run
    applied = json.loads((out / "standing_decisions_applied.json").read_text(encoding="utf-8"))
    d = applied["decisions"][0]
    target = tmp_path / "promoted2"
    (target / "assessments").mkdir(parents=True)
    env = dict(os.environ)
    env.pop("STAF_LIBRARY_ROOT", None)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "promote", "--out", str(out), "--maintainer", "owner",
         "--publish-root", str(target), "--override",
         f"{d['rule_id']}:{d['subject']}=reject:I disagree with this one"],
        capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode != 0
    assert "changes the scope" in (proc.stdout + proc.stderr)
    assert not (target / "assessments" / "eastern-corn-belt-plains").exists()


def test_confirm_doc_turns_an_owner_draft_origin_into_owner_approved():
    """An owner-drafted entry staged ahead of the end review carries
    ``ai_drafted_pending_owner_approval``; the confirmation at promote renames it
    to the pilots' ``ai_drafted_owner_approved`` and drops the pending reviewer."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_region_batch", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    doc = {"records": [
        {"rule_id": "SELECT-01", "subject": "bed-composition-bedform-dynamics", "computed": {"n_metrics": 3},
         "reviewer": "owner-draft " + dec.PENDING_SUFFIX, "reviewer_action": "accept",
         "reviewer_rationale": "Complementary set accepted for review.",
         "reviewer_rationale_origin": "ai_drafted_pending_owner_approval", "reviewer_asserts": {}},
        {"rule_id": "CURVE-04", "subject": "x", "computed": {"decision_flip": False},
         "reviewer": "standing-policy:curve04-accept-with-flag " + dec.PENDING_SUFFIX,
         "reviewer_action": "accept", "reviewer_rationale": "Accepted with the flag carried.",
         "reviewer_rationale_origin": "standing_policy:1.0", "reviewer_asserts": {"decision_flip": False}},
    ], "reviewQueue": {"items": []}, "manifest": {"standingDecisions": {"confirmedBy": None}}}
    out, applied = mod._confirm_doc(doc, reviewer="owner", date="2026-08-22", overrides={})
    assert applied == []
    assert not dec.is_pending(out)
    by = {r["rule_id"]: r for r in out["records"]}
    assert by["SELECT-01"]["reviewer"] == "owner"
    assert by["SELECT-01"]["reviewer_rationale_origin"] == "ai_drafted_owner_approved"
    assert by["CURVE-04"]["reviewer_rationale_origin"] == "standing_policy:1.0"
    assert out["manifest"]["standingDecisions"]["confirmedBy"] == "owner"


def _batch_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_region_batch", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_approvals_refuses_prose_in_the_approver_slot():
    """The approver is a name or a pending marker, never the note: a rationale
    passed without an approver once reached a published meta as the approving
    person."""
    mod = _batch_module()
    ok = mod._parse_approvals(["fn-a=gtmenichino:Complementary set.",
                               "fn-b=owner-draft " + dec.PENDING_SUFFIX + ":Substrate size and wood."])
    assert [a["approvedBy"] for a in ok] == ["gtmenichino", "owner-draft " + dec.PENDING_SUFFIX]
    assert ok[0]["note"] == "Complementary set." and ok[1]["note"] == "Substrate size and wood."
    with pytest.raises(SystemExit, match="reads as prose"):
        mod._parse_approvals(["fn-a=Substrate size, wood volume, and embeddedness read different aspects."])


def test_confirm_approvals_resolves_every_approval_to_the_owner():
    mod = _batch_module()
    meta = {"portfolioApprovals": [
        {"functionId": "fn-a", "approvedBy": "owner-draft " + dec.PENDING_SUFFIX, "note": "x"},
        {"functionId": "fn-b", "approvedBy": "standing-policy:select01 " + dec.PENDING_SUFFIX, "note": "y"},
        {"functionId": "fn-c", "approvedBy": "owner", "note": None}]}
    out = mod._confirm_approvals(meta, maintainer="owner", date="2026-08-22")
    assert [a["approvedBy"] for a in out] == ["owner", "owner", "owner"]
    assert out[0]["confirmedAt"] == "2026-08-22" and "confirmedAt" not in out[2]
    bad = {"portfolioApprovals": [{"functionId": "fn-d", "approvedBy": "Substrate size and wood.", "note": None}]}
    with pytest.raises(SystemExit, match="fn-d"):
        mod._confirm_approvals(bad, maintainer="owner", date="2026-08-22")


def test_html_gallery_has_one_tile_per_curve_row(assembled, tmp_path):
    from streamcurves import curve_svg as cs
    p = rp.write_curve_gallery_html(assembled, tmp_path / "curve_gallery.html")
    assert p is not None and p.is_file()
    html = p.read_text(encoding="utf-8")
    rows = rp.curve_rows_for_packet(assembled)
    n_tiles = html.count('class="curve-tile ') + html.count('class="curve-tile"')
    assert n_tiles == len(rows) == len(assembled["curve_rows"])
    assert html.count("<svg ") == len(rows) and "<script" not in html
    for r in rows:
        assert r["metric"] in html
    dotted = sum(1 for r in rows if not r["in_scope"]
                 and cs.points_from_curve_row(assembled["curve_rows"][r["metric"]]))
    assert html.count("out-of-scope") == dotted
