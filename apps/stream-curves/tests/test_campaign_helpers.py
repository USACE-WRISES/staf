"""The national campaign's record (campaign Round 3, WP-R3): the pure helpers of
``streamcurves.campaign`` on synthetic campaign roots. Nothing here stages a region or
opens the NRSA archive; every folder is built under tmp_path.

The builders at the top are shared with ``test_run_campaign.py``.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

from streamcurves import campaign as camp
from streamcurves import decisions as dec
from streamcurves import library as lib
from streamcurves import methodology
from streamcurves import owner_curves as oc
from streamcurves import session_io as sio
from streamcurves.deep_export import deep_slug

APP = Path(__file__).resolve().parents[1]
RUNNER = APP / "scripts" / "run_campaign.py"
CODE_FP = "f" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"
LABEL = camp.REHEARSAL_LABEL
TESTER = "Tester"
WHY = "A reason long enough to be recorded here."
FUNCTIONS = camp.staf_function_ids()
PENDING_REVIEWER = "standing-policy:curve04-accept-with-flag " + dec.PENDING_SUFFIX


def load_runner():
    """``scripts/run_campaign.py`` as a module (it imports run_region_batch itself)."""
    scripts = str(RUNNER.parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("run_campaign_under_test", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def digest_for(code: str) -> str:
    """A region digest the way stage-many records it (bare hex)."""
    return hashlib.sha256(f"region-digest-{code}".encode("utf-8")).hexdigest()


def _metric(mk: str) -> dict:
    return {"metricId": "spring-" + deep_slug(mk), "metricName": mk, "referenceN": 20,
            "curve": {"form": "monotonic_increasing",
                      "points": [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 1.0}]}}


BLOCKS = (("catchment-hydrology", ("phab_XCMGW", "phab_XCDENMID")),
          ("low-flow-baseflow-dynamics", ("bfiws", "chem_COND", "phab_LSUB_DMM")),
          ("water-soil-quality", ("pctimp2019ws",)))


def make_bundle(code: str, name: str, slug: str, *, undocumented=()) -> dict:
    blocks = [{"functionId": fid, "metrics": [_metric(m) for m in metrics]} for fid, metrics in BLOCKS]
    covered = [b["functionId"] for b in blocks]
    exclusions = [{"functionId": f, "reason": "no-suitable-metric",
                   "justification": "Out of scope for this synthetic fixture; documented so the gate passes.",
                   "recordedBy": "test-suite"}
                  for f in FUNCTIONS if f not in covered and f not in undocumented]
    missing = [f for f in FUNCTIONS if f not in covered and f in undocumented]
    bundle = {"schemaVersion": 1, "assessmentId": slug, "assessmentName": name,
              "region": {"kind": "ecoregion", "code": code, "name": name},
              "metricsByFunction": blocks,
              "functionCoverage": {"framework": "staf-20", "total": len(FUNCTIONS), "covered": len(covered),
                                   "excluded": len(exclusions), "missing": len(missing),
                                   "exclusions": exclusions, "coveredFunctionIds": covered,
                                   "missingFunctionIds": missing},
              "insufficientReferenceSupport": [{"metricKey": "chem_CHLA"}]}
    bundle["contentDigest"] = lib.content_digest(bundle)
    return bundle


def _ledger_row(mk, fid, disposition, option, level=None, **extra) -> dict:
    return {"metric": mk, "functionId": fid, "function": fid, "disposition": disposition,
            "basis": "regional-reference", "option": option, "pool": {"level": level} if level else None,
            "engine": {"executed": disposition == "refitted"}, "basisDigest": "sha256:" + "1" * 64,
            "rule": "REF-05", "reason": "synthetic", "decidedBy": "automated", "who": "n/a",
            "when": "2026-09-25", "candidateKey": "cand-" + deep_slug(mk)[:8], **extra}


def make_ledger(expect: str, *, ok: bool = True) -> dict:
    rows = [_ledger_row("phab_XCMGW", "catchment-hydrology", "refitted", "l3_local", "l3"),
            _ledger_row("phab_XCDENMID", "catchment-hydrology", "carried", "carried", "l2"),
            _ledger_row("bfiws", "low-flow-baseflow-dynamics", "refitted", "l2_regional", "l2"),
            _ledger_row("chem_COND", "low-flow-baseflow-dynamics", "refitted", "nars9_regional", "nars9"),
            _ledger_row("phab_LSUB_DMM", "low-flow-baseflow-dynamics", "refitted", "national_3c", None),
            _ledger_row("pctimp2019ws", "water-soil-quality", "fixed", "fixed"),
            _ledger_row("chem_CHLA", "nutrient-cycling", "unsupported" if ok else "unknown", None)]
    return {"schema": "rebuild-ledger/1", "region": {"kind": "ecoregion", "code": "x"},
            "build": {"inputsDigest": expect, "refit": "all", "carriedFrom": None,
                      "methodologyVersion": "0.14-provisional", "protocolSha256": None},
            "rows": rows}


def pending_record() -> dict:
    """A standing decision the policy made, consistent with its record, still pending."""
    return {"rule_id": "CURVE-04", "subject": "phab_XCMGW", "subject_kind": "metric",
            "computed": {"decision_flip": False, "driver": "S1", "max_param_change_frac": 0.1},
            "reviewer": PENDING_REVIEWER, "reviewer_action": "accept",
            "reviewer_rationale": "Accepted with the flag: no decision flip, driver S1.",
            "reviewer_asserts": {"decision_flip": False}, "reviewer_decision_class": "curve04-accept-with-flag",
            "reviewer_rationale_origin": "standing_policy:1.2", "reviewed_at": "2026-09-25"}


def make_provenance(root, code, name, *, expect, n_boot, decisions_root, pending=True, ledger_ok=True,
                    register=True, records=None, agent_note=None) -> dict:
    standing = dec.load_policy(None)
    argv = ["stage-many", "--out-root", str(Path(root) / camp.RUNS_DIR), "--n-boot", str(n_boot),
            "--maintainer", LABEL, "--refit", "all", "--decisions-root", str(decisions_root), "--l3", code]
    agent = {"module": "streamcurves.regional_agent", "agentVersion": "regional-agent-2", "argv": argv,
             "gitCommit": COMMIT, "gitDirty": False, "codeFingerprint": CODE_FP}
    if agent_note:
        agent["note"] = agent_note
    manifest = {"schemaVersion": 1, "digestSchema": 2, "region": {"kind": "ecoregion", "code": code, "name": name},
                "agent": agent, "diagnostics": {"runSeed": 1, "nBoot": n_boot},
                "methodology": methodology.config_fingerprints(),
                "standingDecisions": {"policyVersion": dec.policy_version(standing), "sha256": standing["meta"]["sha256"],
                                      "enabledIds": [], "appliedIds": ["curve04-accept-with-flag"], "appliedCount": 1,
                                      "confirmedBy": None, "confirmedAt": None},
                "reviewerInputs": {"files": {}, "decisionsRoot": str(decisions_root), "decisionsDigest": "sha256:0"},
                "experimental": None,
                "inputs": {"refit": {"mode": "all"}, "reference": {"carryForward": {"mode": "off", "fromVersion": None}}},
                "inputsDigest": "sha256:" + hashlib.sha256(f"run-{code}".encode("utf-8")).hexdigest(),
                "startedAt": "2026-09-25T00:00:00Z", "finishedAt": "2026-09-25T00:10:00Z"}
    recs = list(records) if records is not None else ([pending_record()] if pending else [])
    queue_items = [{"item_id": "CURVE-04:phab_XCMGW", "rule_ids": ["CURVE-04"], "subject": "phab_XCMGW",
                    "trigger": "influential_site", "status": "resolved", "blocking": False,
                    "reviewer": PENDING_REVIEWER if pending else TESTER}]
    rows = [{"functionId": fid, "function": fid, "candidateKey": "cand-" + deep_slug(m)[:8], "candidate": m,
             "subject": m, "sourceKind": "fitted", "status": "selected", "rule": "REF-05",
             "decidedBy": "automated", "who": None, "when": None, "reason": "", "basisDigest": "sha256:" + "1" * 64,
             "needsReview": False} for fid, metrics in BLOCKS for m in metrics]
    doc = {"schemaVersion": 2, "inputsDigest": manifest["inputsDigest"], "manifest": manifest,
           "rules_applied": ["CURVE-04"], "rules_not_evaluated": [], "records": recs,
           "counts": {"total": len(recs)},
           "reviewQueue": {"schemaVersion": 1, "items": queue_items, "counts": {"open": 0, "blocking": 0}},
           "metricLedger": make_ledger(expect, ok=ledger_ok)}
    if register:
        doc["candidateRegister"] = {"schema": 1, "rows": rows,
                                    "counts": {"selected": len(rows), "functionsSelected": len(BLOCKS)}}
    return doc


def make_staged_region(root, code, name, *, expect, decisions_root, n_boot=1000, open_items=(), hard_stops=(),
                       pending=True, approvals=True, evidence=True, register=True, ledger_ok=True,
                       undocumented=(), decisions=(), code_end=None, stage_complete=True, agent_note=None,
                       records=None) -> dict:
    """A run folder stage-many would leave for a staged region, with its staged version."""
    root = Path(root)
    run_dir = root / camp.RUNS_DIR / camp.run_folder_name(code)
    slug = lib.slugify(name)
    vdir = run_dir / "library" / "assessments" / slug / "v1"
    vdir.mkdir(parents=True, exist_ok=True)
    region = {"kind": "ecoregion", "code": code, "name": name}
    bundle = make_bundle(code, name, slug, undocumented=undocumented)
    # the bundle's library block never carries the maintainer label (a stage records the
    # fixed author string there); the meta's author does, and promote rewrites it
    bundle["library"] = {"libraryId": slug, "version": 1, "author": "StreamCurves regional analysis (STAF)",
                         "contentDigest": bundle["contentDigest"], "region": region}
    doc = make_provenance(root, code, name, expect=expect, n_boot=n_boot, decisions_root=decisions_root,
                          pending=pending, ledger_ok=ledger_ok, register=register, records=records,
                          agent_note=agent_note)
    session = sio.dump_session_fields({"region_of_applicability": region, "owner_curve_decisions": list(decisions)},
                                      session_name=name)
    meta = {"assessmentId": slug, "assessmentName": name, "version": 1, "updatedAt": "2026-09-25T00:10:00Z",
            "author": camp.POLICY_CANDIDATE_LABEL, "region": region, "contentDigest": bundle["contentDigest"],
            "supersedesVersion": None, "provenance": "present"}
    if approvals:
        meta["portfolioApprovals"] = [
            {"functionId": "low-flow-baseflow-dynamics",
             "approvedBy": "standing-policy:select01-complementary-set " + dec.PENDING_SUFFIX,
             "note": "standing decision select01-complementary-set (policy 1.2): synthetic"},
            {"functionId": "catchment-hydrology", "approvedBy": TESTER, "note": "Carried from v3.", "carriedFrom": 3}]
    ref = {"packageId": f"deep-dev-l3-{code}", "version": "abc123", "reproducibility": "refittable",
           "packageDigest": "sha256:" + hashlib.sha256(f"pkg-{code}".encode("utf-8")).hexdigest(),
           "dataDigest": "sha256:" + "2" * 64, "bytes": 10,
           "archive": {"name": f"deep-dev-l3-{code}.evidence.zip", "sha256": "3" * 64, "bytes": 10}}
    camp.write_json(vdir / camp.BUNDLE_FILE, bundle)
    camp.write_json(vdir / camp.PROVENANCE_FILE, doc)
    (vdir / camp.SESSION_FILE).write_text(sio.dumps_session(session), encoding="utf-8", newline="\n")
    camp.write_json(vdir / camp.META_FILE, meta)
    if evidence:
        camp.write_json(vdir / camp.EVIDENCE_FILE, ref)
        camp.write_json(run_dir / camp.EVIDENCE_FILE, ref)
        (run_dir / "evidence").mkdir(exist_ok=True)
        (run_dir / "evidence" / ref["archive"]["name"]).write_bytes(b"0123456789")
    # the staged library's own manifest, so the version resolves without the packet too
    camp.write_json(run_dir / "library" / "assessments" / slug / "manifest.json",
                    {"schemaVersion": 2, "assessmentId": slug, "assessmentName": name, "region": region,
                     "latestVersion": 1, "versions": [{"version": 1, "contentDigest": bundle["contentDigest"]}]})
    decision = {"rule_id": "CURVE-04", "subject": "phab_XCMGW", "action": "accept", "reviewer": PENDING_REVIEWER,
                "rationale": "Accepted with the flag: no decision flip, driver S1.",
                "decision_class": "curve04-accept-with-flag", "rationale_origin": "standing_policy:1.2",
                "asserts": {"decision_flip": False}, "policy_entry": "curve04-accept-with-flag"}
    standing = dec.load_policy(None)
    packet = {"schemaVersion": 1, "region": region, "reference_tier": "least_disturbed", "ref02_triggered": False,
              "review_flags": [], "screening": {"n_candidates": 40, "n_retained": 12, "counts": {}},
              "policy": {"version": dec.policy_version(standing), "sha256": standing["meta"]["sha256"],
                         "enabled": [], "applied_ids": ["curve04-accept-with-flag"]},
              "decisions_applied": [decision], "open_items": list(open_items), "hard_stops": list(hard_stops),
              "queue_counts": {"open": len(open_items), "blocking": len(hard_stops)},
              "curves": [{"metric": m, "function": fid} for fid, metrics in BLOCKS for m in metrics],
              "excluded": [], "portfolio": [],
              "coverage": {k: bundle["functionCoverage"][k] for k in ("total", "covered", "excluded", "missing")},
              "reference": {"withheld": [{"metricKey": "chem_CHLA", "functions": []}],
                            "support": [{"metric": m, "status": "local", "in_bundle": True}
                                        for _, metrics in BLOCKS for m in metrics]},
              "staged": {"version": 1, "path": str(vdir), "root": str(run_dir / "library")},
              "gallery": "curve_gallery.png", "gallery_html": "curve_gallery.html",
              "promote_command": f"python run_region_batch.py promote --out {run_dir} --maintainer {LABEL}",
              "inputs_digest": doc["inputsDigest"], "run_seed": 1, "n_boot": n_boot,
              "refit": {"mode": "all", "carry_forward": {"mode": "off", "fromVersion": None}, "n_carried": 0,
                        "held": [], "held_pool_supported": {}},
              "evidence": ref if evidence else None, "ledger": "rebuild_ledger.json", "value_policy": "newest-nonnull-v2"}
    camp.write_json(run_dir / camp.PACKET_FILE, packet)
    (run_dir / "review_packet.md").write_text(f"# End-review packet: {name}\n", encoding="utf-8", newline="\n")
    (run_dir / "curve_gallery.png").write_bytes(b"PNG")
    (run_dir / "curve_gallery.html").write_text("<html></html>\n", encoding="utf-8", newline="\n")
    camp.write_json(run_dir / camp.APPLIED_FILE,
                    {"policy": standing["meta"], "enabled": [], "decisions": [decision], "finalize_metrics": {},
                     "portfolio_approvals": meta.get("portfolioApprovals") or [], "coverage_exceptions": [],
                     "open_items": list(open_items), "hard_stops": list(hard_stops)})
    camp.write_json(run_dir / camp.LEDGER_FILE, doc["metricLedger"])
    camp.write_json(run_dir / camp.RUN_MANIFEST_FILE, doc["manifest"])
    (run_dir / "assessment.streamcurves.json").write_text(sio.dumps_session(session), encoding="utf-8", newline="\n")
    (run_dir / "promote_command.txt").write_text(packet["promote_command"] + "\n", encoding="utf-8", newline="\n")
    if stage_complete:
        runner = load_runner()
        outputs = runner.rrb.stage_outputs(run_dir)
        camp.write_json(run_dir / camp.STAGE_COMPLETE,
                        {"l3": code, "name": name, "inputsDigest": expect,
                         "code": {"start": CODE_FP, "end": code_end or CODE_FP},
                         "startedAt": "2026-09-25T00:00:00Z", "finishedAt": "2026-09-25T00:10:00Z",
                         "outputs": outputs})
    return {"run_dir": run_dir, "vdir": vdir, "slug": slug, "bundle": bundle, "meta": meta, "packet": packet}


def region_row(code, name, *, n_frame=30, n_strict=12, order=1, expect=None, **over) -> dict:
    row = {"l3": code, "name": name, "stageName": name, "l2": "8.1", "l1": "8", "nars9": "NAP",
           "nStationsAll": n_frame + 5, "nFrame": n_frame, "nStrict": n_strict, "nMetrics": 31,
           "nLocal": 20, "nBorrowedL2": 3, "nBorrowedNars9": 1, "nBorrowedL1": 0, "nNational": 2,
           "nModeled": 0, "nPublished": 1, "nInsufficient": 4,
           "supportClass": camp.support_class(n_frame, n_strict),
           "decisionFiles": {n: None for n in camp.DECISION_FILE_NAMES},
           "carriedFrom": None, "inputsDigest": expect if expect is not None else digest_for(code),
           "order": order, "runFolder": f"{camp.RUNS_DIR}/{camp.run_folder_name(code)}"}
    row.update(over)
    return row


def fake_manifest(root, regions, *, worktree, decisions_root, n_boot=1000, maintainer=LABEL, gate_report=None,
                  purpose="frozen", refit="all", workers=2) -> dict:
    """A manifest as plan writes it, without running plan (the inputs are synthetic)."""
    root = Path(root)
    standing = dec.load_policy(None)
    promotion = camp.load_promotion_policy()
    tokens = camp.stage_many_tokens(root, workers=workers, n_boot=n_boot, maintainer=maintainer, refit=refit,
                                    decisions_root=str(decisions_root), codes=[r["l3"] for r in regions])
    inputs = {"flags": {"n_boot": n_boot, "maintainer": maintainer, "refit": refit},
              "methodology": methodology.config_fingerprints(),
              "policy": {"version": dec.policy_version(standing), "sha256": standing["meta"]["sha256"]},
              "coverageExceptions": None, "nrsaManifest": "sha256:" + "a" * 64,
              "legacyNrsa": {"nrsa_metrics.parquet": "sha256:" + "b" * 64, "nrsa_sites.csv": "sha256:" + "c" * 64},
              "stationScreen": "sha256:" + "d" * 64, "code": CODE_FP}
    manifest = camp.build_manifest(
        purpose=purpose, worktree=str(worktree), commit=COMMIT, git_dirty=False, code_fingerprint=CODE_FP,
        inputs=inputs, promotion_policy=promotion,
        run={"workers": workers, "nBoot": n_boot, "maintainer": maintainer, "refit": refit,
             "decisionsRoot": str(decisions_root)},
        gate_report=str(gate_report) if gate_report else None,
        sources={"census": {"path": "census.csv", "sha256": "sha256:" + "e" * 64}},
        commands={"stageMany": tokens, "stage": {}}, regions=[dict(r) for r in regions],
        created_at="2026-09-25T00:00:00Z")
    root.mkdir(parents=True, exist_ok=True)
    (root / camp.RUNS_DIR).mkdir(exist_ok=True)
    camp.write_json(root / camp.MANIFEST_FILE, manifest)
    (root / camp.PROMOTION_POLICY_FILE).write_bytes(Path(promotion["meta"]["path"]).read_bytes())
    (root / camp.STANDING_POLICY_FILE).write_bytes(Path(standing["meta"]["path"]).read_bytes())
    camp.write_text(root / camp.COMMANDS_FILE, camp.commands_markdown(manifest, python=sys.executable,
                                                                       app_root=str(worktree), root=str(root)))
    return manifest


def write_batch_records(root, rows, jobs=None) -> None:
    """``runs/batch_summary.json`` and the ``.campaign`` summary and index of a stage-many."""
    runs = Path(root) / camp.RUNS_DIR
    camp.write_json(runs / "batch_summary.json", {"schemaVersion": 1, "regions": list(rows)})
    campaign = runs / ".campaign"
    campaign.mkdir(parents=True, exist_ok=True)
    summary = {"updated": "2026-09-25T01:00:00Z", "workers": 2, "seconds": 100.0, "counts": {}, "jobs": {}}
    lines = []
    for j in jobs or []:
        job_id = hashlib.sha256(j["label"].encode("utf-8")).hexdigest()[:16]
        summary["jobs"][job_id] = {k: j.get(k) for k in ("label", "state", "seconds", "exit", "peakMemoryMB")}
        lines.append(json.dumps({"at": "2026-09-25T00:00:00Z", "event": "started", "id": job_id, "label": j["label"]}))
        for _ in range(int(j.get("retries") or 0)):
            lines.append(json.dumps({"at": "2026-09-25T00:05:00Z", "event": "retrying", "id": job_id,
                                     "label": j["label"], "exit": j.get("exit")}))
            lines.append(json.dumps({"at": "2026-09-25T00:05:01Z", "event": "started", "id": job_id, "label": j["label"]}))
        lines.append(json.dumps({"at": "2026-09-25T00:10:00Z", "event": j["state"], "id": job_id, "label": j["label"],
                                 "seconds": j.get("seconds"), "exit": j.get("exit"),
                                 "peakMemoryMB": j.get("peakMemoryMB")}))
        if j.get("log"):
            d = campaign / "jobs" / job_id
            d.mkdir(parents=True, exist_ok=True)
            (d / "log.txt").write_text(j["log"], encoding="utf-8", newline="\n")
    camp.write_json(campaign / "summary.json", summary)
    (campaign / "index.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")


def standard_root(tmp_path, *, gate=True):
    """One eligible staged region (55) under a frozen manifest, a gate report at the
    campaign's commit, the batch records of a completed stage-many."""
    root = tmp_path / "campaign"
    worktree = tmp_path / "wt"
    (worktree / "apps" / "stream-curves" / "scripts").mkdir(parents=True)
    (worktree / "apps" / "stream-curves" / "scripts" / "run_region_batch.py").write_text("# fake\n", encoding="utf-8")
    decisions_root = tmp_path / "decisions"
    decisions_root.mkdir()
    gate_report = None
    if gate:
        gate_dir = tmp_path / "gate" / COMMIT[:8]
        gate_dir.mkdir(parents=True)
        gate_report = gate_dir / "report.json"
        camp.write_json(gate_report, {"gatePassed": True, "regions": {}})
    regions = [region_row("55", "Eastern Corn Belt Plains", n_frame=44, n_strict=0, order=1)]
    manifest = fake_manifest(root, regions, worktree=worktree, decisions_root=decisions_root, gate_report=gate_report)
    staged = make_staged_region(root, "55", "Eastern Corn Belt Plains", expect=digest_for("55"),
                                decisions_root=decisions_root)
    write_batch_records(root, [{"l3": "55", "name": "Eastern Corn Belt Plains", "exit": 0, "curves": 6,
                                "decisions": 1, "open_items": 0, "hard_stops": 0, "staged_version": 1,
                                "seconds": 321.5, "out": str(staged["run_dir"]), "error": None}],
                        [{"label": "L3-55 Eastern Corn Belt Plains", "state": "completed", "seconds": 321.5,
                          "exit": None, "peakMemoryMB": 310.2}])
    return {"root": root, "worktree": worktree, "decisions_root": decisions_root, "manifest": manifest,
            "gate_report": gate_report, **staged}


def _expect(manifest, code):
    return camp.expectation_from_manifest(manifest, code)


# --------------------------------------------------------------------------- #
# the promotion policy file
# --------------------------------------------------------------------------- #
def test_the_promotion_policy_names_every_gate_and_its_check():
    policy = camp.load_promotion_policy()
    assert camp.validate_promotion_policy(policy) == []
    assert [g["id"] for g in policy["gates"]] == list(camp.GATE_IDS)
    assert policy["meta"]["version"] == "1.2" and policy["meta"]["status"] == "provisional"
    # promotion policy 1.2 (owner decision D14): the acceptance clause and the new gate
    assert camp.accepted_support_classes(policy) == ["noLocal", "frameUnder10"]
    assert camp.GATE_IDS[-1] == "flagged-transfer-disclosed"
    assert camp.promotion_policy_record(policy)["acceptedSupportClasses"] == ["noLocal", "frameUnder10"]
    assert policy["meta"]["sha256"].startswith("sha256:")
    broken = copy.deepcopy(policy)
    broken["gates"][0]["check"] = "nowhere"
    del broken["gates"][-1]
    broken["gates"].append({"id": "made-up", "title": "x", "evidence": "y", "check": "z"})
    problems = camp.validate_promotion_policy(broken)
    assert any("must name streamcurves.campaign.gate_frozen_record" in p for p in problems)
    # the last gate is promotion policy 1.2's flagged-transfer-disclosed
    assert any("no gate flagged-transfer-disclosed" in p for p in problems)
    assert any("no check implements it" in p for p in problems)


# --------------------------------------------------------------------------- #
# the plan's inputs
# --------------------------------------------------------------------------- #
def test_support_classes_census_counts_and_order():
    assert camp.support_class(5, 3) == "frameUnder10"
    assert camp.support_class(30, 0) == "noLocal"
    assert camp.support_class(30, 9) == "local1to9"
    assert camp.support_class(30, 19) == "local10to19"
    assert camp.support_class(30, 20) == "local20plus"
    rows = [{"status": s} for s in ("local", "local_relaxed", "borrowed_l2", "borrowed_nars9", "borrowed_l1",
                                    "national", "modeled", "published", "insufficient", "insufficient")]
    counts = camp.census_counts(rows)
    assert counts == {"nMetrics": 10, "nLocal": 2, "nBorrowedL2": 1, "nBorrowedNars9": 1, "nBorrowedL1": 1,
                      "nNational": 1, "nModeled": 1, "nPublished": 1, "nInsufficient": 2}
    regions = [{"l3": "2", "nFrame": 10}, {"l3": "10", "nFrame": 50}, {"l3": "1", "nFrame": 10}]
    assert [r["l3"] for r in camp.order_regions(regions)] == ["10", "1", "2"]
    assert [r["order"] for r in camp.order_regions(regions)] == [1, 2, 3]
    assert [r["l3"] for r in camp.order_regions(regions, seconds={"2": 900.0, "1": 5.0})] == ["2", "1", "10"]
    assert camp.campaign_id("frozen", "sha256:0409ff11deadbeef", 1000) == "r3-frozen-0409ff11-b1000"
    assert camp.maintainer_label_problem(LABEL) is None
    assert camp.maintainer_label_problem(camp.POLICY_CANDIDATE_LABEL) is None
    assert "reads as a person's name" in camp.maintainer_label_problem(TESTER)
    assert camp.maintainer_label_problem("") is not None


def test_station_screen_summary_reads_the_frame_and_the_parents(tmp_path):
    import pandas as pd
    df = pd.DataFrame({"l3": ["1", "1", "1", "2"], "l2": ["7.1", "7.1", "7.1", "6.2"], "l1": ["7", "7", "7", "6"],
                       "nars9": ["WMT", "WMT", "WMT", "XER"], "wadeable": [True, True, False, True],
                       "pass_strict": [True, False, False, True]})
    p = tmp_path / "screen.parquet"
    df.to_parquet(p, index=False)
    out = camp.station_screen_summary(p, ["1", "2", "3"])
    assert out["1"] == {"nars9": "WMT", "l2": "7.1", "l1": "7", "nStationsAll": 3, "nFrame": 2, "nStrict": 1}
    assert out["2"]["nFrame"] == 1 and "3" not in out


def test_manifest_drift_lists_every_moved_value(tmp_path):
    fx = standard_root(tmp_path)
    manifest = fx["manifest"]
    live = {"code": CODE_FP, "runningCode": CODE_FP, "gitDirty": False, "commit": COMMIT,
            "inputs": copy.deepcopy(manifest["inputs"]), "promotionPolicy": manifest["inputs"]["promotionPolicy"]["sha256"],
            "copies": {camp.PROMOTION_POLICY_FILE: manifest["inputs"]["promotionPolicy"]["sha256"],
                       camp.STANDING_POLICY_FILE: manifest["inputs"]["policy"]["sha256"]},
            "decisionFiles": {"55": {n: None for n in camp.DECISION_FILE_NAMES}},
            "stageManyLine": camp.render_command(manifest["commands"]["stageMany"])}
    assert camp.manifest_drift(manifest, live) == []
    moved = copy.deepcopy(live)
    moved.update(code="0" * 64, gitDirty=True, commit="ffff", promotionPolicy="sha256:9")
    moved["inputs"]["methodology"]["config_sha256"] = "sha256:changed"
    moved["copies"][camp.STANDING_POLICY_FILE] = "sha256:8"
    moved["decisionFiles"]["55"]["owner_decisions"] = "sha256:7"
    moved["stageManyLine"] = "(not in commands.md)"
    drift = camp.manifest_drift(manifest, moved)
    heads = [d.split(":")[0] for d in drift]
    assert "code.fingerprint (code)" in heads and "identity.gitDirty" in heads and "identity.commit" in heads
    assert "inputs.methodology.config_sha256" in heads and "inputs.promotionPolicy.sha256" in heads
    assert f"{camp.STANDING_POLICY_FILE} beside the manifest" in heads
    assert "regions.l3-55.decisionFiles.owner_decisions" in heads and "commands.md" in heads
    assert len(drift) == 8


# --------------------------------------------------------------------------- #
# reading run folders
# --------------------------------------------------------------------------- #
def test_classify_region_covers_every_state(tmp_path):
    fx = standard_root(tmp_path)
    run_dir = fx["run_dir"]
    packet = camp.read_json(run_dir / camp.PACKET_FILE)
    rec = camp.read_json(run_dir / camp.STAGE_COMPLETE)
    row = {"exit": 0, "error": None}
    assert camp.classify_region(run_dir, packet=packet, batch_row=row, job={"state": "completed"},
                                log_text="", stage_record=rec)[0] == "staged"
    opened = dict(packet, open_items=[{"item_id": "X"}])
    assert camp.classify_region(run_dir, packet=opened, batch_row=row, job=None, log_text="",
                                stage_record=rec) == ("staged-open", "1 open item(s), 0 hard stop(s)")
    state, detail = camp.classify_region(run_dir, packet=packet, batch_row=row, job=None, log_text="", stage_record=None)
    assert state == "incomplete" and "record is missing" in detail
    assert camp.classify_region(tmp_path / "none", packet=None, batch_row={"exit": 1, "error": camp.NO_DATA_ERROR + " for L3 999"},
                                job=None, log_text="", stage_record=None)[0] == "no-data"
    unstaged = dict(packet, staged=None)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert camp.classify_region(empty, packet=unstaged, batch_row=row, job=None,
                                log_text="[batch] no bundle to stage: no curve survived\n", stage_record=None) == \
        ("unsupported", "no curve survived")
    state, detail = camp.classify_region(
        empty, packet=unstaged, batch_row=row, job=None, stage_record=None,
        log_text="[batch] staged publish refused: Cannot publish x: 2 of 20 STAF functions have no metric and no "
                 "documented reason -- a, b.\n")
    assert state == "unsupported" and detail.startswith("Cannot publish")
    state, detail = camp.classify_region(
        empty, packet=unstaged, batch_row=row, job=None, stage_record=None,
        log_text="[batch] staged publish refused: Refusing to publish 'x': more than 2 metrics per function requires "
                 "a recorded human approval (SELECT-01).\n")
    assert state == "incomplete" and "SELECT-01" in detail
    assert camp.classify_region(empty, packet=unstaged, batch_row=row, job=None, log_text="", stage_record=None) == \
        ("incomplete", "the packet names no staged version")
    state, detail = camp.classify_region(empty, packet=None, batch_row={"exit": 1, "error": "tail"},
                                         job={"state": "failed", "exit": 2, "attempts": 2},
                                         log_text="[batch] REFUSED: 40 of 100 candidates unresolved\n", stage_record=None)
    assert state == "refused" and detail.startswith("exit 2 after 2 attempt(s)")
    state, detail = camp.classify_region(empty, packet=None, batch_row={"exit": 1, "error": "Traceback"},
                                         job={"state": "failed", "exit": 1, "attempts": 2}, log_text="", stage_record=None)
    assert state == "failed" and "Traceback" in detail
    assert camp.classify_region(empty, packet=None, batch_row={"exit": 3, "error": "busy"}, job=None,
                                log_text="", stage_record=None)[0] == "refused"
    assert camp.classify_region(empty, packet=None, batch_row=None, job=None, log_text="", stage_record=None) == \
        ("not-started", "")
    assert camp.classify_region(empty, packet=None, batch_row=None, job={"state": "started", "at": "t"},
                                log_text="", stage_record=None)[0] == "not-started"


def test_per_function_and_source_mix_read_the_ledger(tmp_path):
    fx = standard_root(tmp_path)
    ledger = camp.read_json(fx["run_dir"] / camp.LEDGER_FILE)
    per = camp.per_function(ledger, fx["bundle"], FUNCTIONS)
    assert per["catchment-hydrology"] == "fitted+carried"
    assert per["low-flow-baseflow-dynamics"] == "fitted" and per["water-soil-quality"] == "fixed"
    assert per["nutrient-cycling"] == "documented-gap"
    assert sum(1 for v in per.values() if v == "documented-gap") == len(FUNCTIONS) - 3
    undocumented = make_bundle("55", "x", "x", undocumented=("population-support",))
    assert camp.per_function(ledger, undocumented, FUNCTIONS)["population-support"] == "undocumented-gap"
    assert camp.per_function(None, fx["bundle"], FUNCTIONS)["catchment-hydrology"] == "scored"
    mix = camp.source_mix(ledger, fx["packet"])
    assert mix == {"local": 1, "l2": 2, "nars9": 1, "l1": 0, "national": 1, "modeled": 0, "published": 0, "fixed": 1}
    assert camp.source_mix(None, fx["packet"]) == {"local": 6, "l2": 0, "nars9": 0, "l1": 0, "national": 0,
                                                    "modeled": 0, "published": 0}


def test_index_rows_and_csv_on_the_standard_root(tmp_path):
    fx = standard_root(tmp_path)
    rows = camp.index_rows(fx["root"], fx["manifest"], eligibility=None, promote_command=lambda d: f"promote {d}")
    assert len(rows) == 1
    r = rows[0]
    assert r["state"] == "staged" and r["exit"] == 0 and r["seconds"] == 321.5 and r["peakMemoryMB"] == 310.2
    assert r["stagedVersion"] == 1 and r["contentDigest"] == fx["bundle"]["contentDigest"]
    assert r["functionsCovered"] == 3 and r["withheld"] == 1 and r["curves"] == 6 and r["decisionsApplied"] == 1
    assert r["openItems"] == 0 and r["hardStops"] == 0 and r["promoteEligible"] is None
    assert r["openBlocking"] == 0 and r["openAdvisory"] == 0
    assert r["inputsDigest"] == digest_for("55") and r["promoteCommand"].startswith("promote ")
    assert r["sourceMix"]["l2"] == 2 and r["perFunction"]["water-soil-quality"] == "fixed"
    text = camp.index_csv_text(rows)
    header = text.splitlines()[0].split(",")
    assert header == list(camp.INDEX_COLUMNS)
    assert '""l2"": 2' in text and "staged" in text
    doc = camp.index_document(fx["manifest"], rows, inputs={"manifest.json": "sha256:x"}, generated_at="t")
    assert doc["schema"] == camp.INDEX_SCHEMA and doc["counts"] == {"staged": 1}
    assert doc["promotionPolicy"]["version"] == "1.2" and doc["campaign"]["campaignId"].startswith("r3-frozen-")


# --------------------------------------------------------------------------- #
# every gate, passing and failing once
# --------------------------------------------------------------------------- #
def test_every_gate_passes_on_the_standard_root(tmp_path):
    fx = standard_root(tmp_path)
    policy = camp.load_promotion_policy()
    decisions_file = fx["decisions_root"] / "l3-55" / oc.DECISIONS_FILE
    res = camp.evaluate_gates(fx["run_dir"], expect=_expect(fx["manifest"], "55"), decisions_file=decisions_file,
                              policy=policy)
    assert res["eligible"] is True and res["blockers"] == [], res
    assert set(res["gates"]) == set(camp.GATE_IDS) and res["skipped"] == []
    assert res["stagedPath"] == str(fx["vdir"]) and res["advisoryOpen"] == []
    # the run folder alone: the equivalence gate is skipped and does not count
    partial = camp.evaluate_gates(fx["run_dir"], expect=camp.expectation_from_run(fx["run_dir"]),
                                  decisions_file=decisions_file, policy=policy, skip=("equivalence-proven",))
    assert partial["eligible"] is True and partial["skipped"] == ["equivalence-proven"]
    assert partial["gates"]["equivalence-proven"]["passed"] is None


def test_gate_frozen_record_fails_on_each_moved_value(tmp_path):
    fx = standard_root(tmp_path)
    run_dir = fx["run_dir"]
    good = _expect(fx["manifest"], "55")
    assert camp.gate_frozen_record(run_dir, good)[0] is True
    ok, detail = camp.gate_frozen_record(run_dir, dict(good, inputsDigest="0" * 64))
    assert not ok and "inputsDigest" in detail
    ok, detail = camp.gate_frozen_record(run_dir, dict(good, codeFingerprint="1" * 64))
    assert not ok and "code fingerprint" in detail
    ok, detail = camp.gate_frozen_record(run_dir, dict(good, nBoot=200))
    assert not ok and "n-boot 1000 is not the campaign's 200" in detail
    ok, detail = camp.gate_frozen_record(run_dir, dict(good, inputsDigest=None))
    assert not ok and "no expected inputs digest" in detail
    rec = camp.read_json(run_dir / camp.STAGE_COMPLETE)
    rec["code"]["end"] = "2" * 64
    camp.write_json(run_dir / camp.STAGE_COMPLETE, rec)
    ok, detail = camp.gate_frozen_record(run_dir, good)
    assert not ok and "moved during the stage" in detail
    (run_dir / camp.STAGE_COMPLETE).unlink()
    ok, detail = camp.gate_frozen_record(run_dir, good)
    assert not ok and "stage_complete.json is missing" in detail


def test_gate_rules_applied_passes_advisory_items_and_fails_blocking_ones(tmp_path):
    """Promotion policy 1.1: no hard stop and no blocking open item; advisory open items
    pass and are named, never hidden."""
    fx = standard_root(tmp_path)
    run_dir = fx["run_dir"]
    ok, detail = camp.gate_rules_applied(run_dir)
    assert ok and detail.endswith("no hard stop and no blocking open item; nothing left open")
    packet = camp.read_json(run_dir / camp.PACKET_FILE)
    advisory = [{"item_id": "CURVE-12:chem_PH", "trigger": "inverted_discrimination", "blocking": False,
                 "question": "?"},
                {"item_id": "RED-01:a|b", "trigger": "redundant_pair", "blocking": False, "question": "?"}]
    packet["open_items"] = list(advisory)
    camp.write_json(run_dir / camp.PACKET_FILE, packet)
    ok, detail = camp.gate_rules_applied(run_dir)
    assert ok and detail.endswith("2 advisory open item(s) listed: CURVE-12:chem_PH, RED-01:a|b")
    assert camp.advisory_open_items(run_dir) == ["CURVE-12:chem_PH", "RED-01:a|b"]
    # a curve held for review blocks through its trigger even with the item's own flag off
    packet["open_items"] = advisory + [{"item_id": "CURVE-07:phab_SINU", "trigger": "curve_needs_review",
                                        "blocking": False, "question": "?"}]
    camp.write_json(run_dir / camp.PACKET_FILE, packet)
    ok, detail = camp.gate_rules_applied(run_dir)
    assert not ok and detail == "1 blocking open item(s): CURVE-07:phab_SINU"
    # the queue's own blocking flag blocks too
    packet["open_items"] = [{"item_id": "SELECT-01:habitat-provision", "trigger": "more_than_two_metrics",
                             "blocking": True, "question": "?"}]
    camp.write_json(run_dir / camp.PACKET_FILE, packet)
    ok, detail = camp.gate_rules_applied(run_dir)
    assert not ok and "1 blocking open item(s): SELECT-01:habitat-provision" in detail
    # a hard stop fails regardless of what is open
    packet["open_items"] = []
    camp.write_json(run_dir / camp.PACKET_FILE, packet)
    applied = camp.read_json(run_dir / camp.APPLIED_FILE)
    applied["hard_stops"] = [{"item_id": "REF-02:reference_screen", "trigger": "reference_tier_fallback"}]
    camp.write_json(run_dir / camp.APPLIED_FILE, applied)
    ok, detail = camp.gate_rules_applied(run_dir)
    assert not ok and detail == "1 hard stop(s): REF-02:reference_screen"
    split = camp.split_open_items(applied, packet)
    assert split == {"blocking": ["REF-02:reference_screen"], "advisory": [], "hardStops": ["REF-02:reference_screen"]}
    (run_dir / camp.APPLIED_FILE).unlink()
    assert "missing" in camp.gate_rules_applied(run_dir)[1]
    # the vocabulary: the queue's blocking tier plus the uncovered hard-stop triggers
    for trigger in ("reference_tier_fallback", "curve_needs_review", "direction_unresolved"):
        assert camp.blocking_trigger(trigger), trigger
    for trigger in ("inverted_discrimination", "redundant_pair", "no_interval", "advisory_stratifier_not_applied",
                    "measurement_precision_floor", "missingness_review", "function_unassessed"):
        assert not camp.blocking_trigger(trigger), trigger
    assert camp.item_blocks({"item_id": "x", "trigger": "no_interval", "blocking": True})


def test_gate_pending_confirmable_confirms_a_copy_and_fails_on_a_survivor(tmp_path):
    fx = standard_root(tmp_path)
    before = (fx["vdir"] / camp.PROVENANCE_FILE).read_bytes()
    ok, detail = camp.gate_pending_confirmable(fx["run_dir"])
    assert ok and detail == "1 pending decision(s) confirm cleanly with no override"
    assert (fx["vdir"] / camp.PROVENANCE_FILE).read_bytes() == before        # never written
    doc = json.loads(before)
    doc["manifest"]["agent"]["note"] = "drafted " + dec.PENDING_SUFFIX
    camp.write_json(fx["vdir"] / camp.PROVENANCE_FILE, doc)
    ok, detail = camp.gate_pending_confirmable(fx["run_dir"])
    assert not ok and "still pending after confirmation at: manifest.agent.note" in detail
    doc = json.loads(before)
    doc["records"][0]["reviewer_asserts"] = {"decision_flip": True}
    camp.write_json(fx["vdir"] / camp.PROVENANCE_FILE, doc)
    ok, detail = camp.gate_pending_confirmable(fx["run_dir"])
    assert not ok and detail.startswith("confirmation would be refused: CURVE-04:phab_XCMGW")


def test_gate_owner_decisions_honored_reads_the_named_file(tmp_path):
    fx = standard_root(tmp_path)
    decisions_file = fx["decisions_root"] / "l3-55" / oc.DECISIONS_FILE
    ok, detail = camp.gate_owner_decisions_honored(fx["run_dir"], decisions_file)
    assert ok and detail.startswith("0 recorded decision(s)")
    d = oc.new_decision("chem_TURB", oc.REMOVE, rationale=WHY, recorded_by=TESTER)
    oc.save(fx["decisions_root"] / "l3-55", d)
    ok, detail = camp.gate_owner_decisions_honored(fx["run_dir"], decisions_file)
    assert not ok and "not the ones the staged build applied" in detail
    # the staged build applied it: the file and the session agree
    staged = make_staged_region(fx["root"], "55", "Eastern Corn Belt Plains", expect=digest_for("55"),
                                decisions_root=fx["decisions_root"], decisions=[d])
    ok, detail = camp.gate_owner_decisions_honored(staged["run_dir"], decisions_file)
    assert ok and detail.startswith("1 recorded decision(s)")
    # withdrawn after the stage: the staged version would publish with it
    oc.undo(fx["decisions_root"] / "l3-55", d["id"])
    assert camp.gate_owner_decisions_honored(staged["run_dir"], decisions_file)[0] is False


def test_gate_portfolio_approvals_needs_an_approval_per_wide_function(tmp_path):
    fx = standard_root(tmp_path)
    assert camp.gate_portfolio_approvals(fx["run_dir"])[0] is True
    meta = camp.read_json(fx["vdir"] / camp.META_FILE)
    meta["portfolioApprovals"] = [a for a in meta["portfolioApprovals"] if a["functionId"] != "low-flow-baseflow-dynamics"]
    camp.write_json(fx["vdir"] / camp.META_FILE, meta)
    ok, detail = camp.gate_portfolio_approvals(fx["run_dir"])
    assert not ok and detail == "no recorded approval on: low-flow-baseflow-dynamics (3 metrics)"
    (fx["vdir"] / camp.BUNDLE_FILE).unlink()
    ok, detail = camp.gate_portfolio_approvals(fx["run_dir"])
    assert not ok and "staged publish did not succeed" in detail


def test_gate_equivalence_proven_needs_a_passed_report_at_the_commit(tmp_path):
    fx = standard_root(tmp_path)
    ok, detail = camp.gate_equivalence_proven(fx["gate_report"], COMMIT)
    assert ok and detail.endswith(f"for commit {COMMIT[:8]}")
    assert camp.gate_equivalence_proven(None, COMMIT) == (False, "no gate report is named (the manifest's gateReport is null)")
    assert "no readable gate report" in camp.gate_equivalence_proven(tmp_path / "nope.json", COMMIT)[1]
    camp.write_json(fx["gate_report"], {"gatePassed": False})
    assert "records gatePassed False" in camp.gate_equivalence_proven(fx["gate_report"], COMMIT)[1]
    elsewhere = tmp_path / "other" / "report.json"
    camp.write_json(elsewhere, {"gatePassed": True})
    assert "does not name the campaign's commit" in camp.gate_equivalence_proven(elsewhere, COMMIT)[1]
    camp.write_json(elsewhere, {"gatePassed": True, "commit": COMMIT})
    assert camp.gate_equivalence_proven(elsewhere, COMMIT)[0] is True
    camp.write_json(elsewhere, {"gatePassed": True, "commit": "abcdef00"})
    assert "names commit abcdef00" in camp.gate_equivalence_proven(elsewhere, COMMIT)[1]


def test_gate_record_complete_checks_register_ledger_outputs_and_evidence(tmp_path):
    fx = standard_root(tmp_path)
    run_dir, vdir = fx["run_dir"], fx["vdir"]
    assert camp.gate_record_complete(run_dir)[0] is True
    doc = camp.read_json(vdir / camp.PROVENANCE_FILE)
    del doc["candidateRegister"]
    doc["metricLedger"]["rows"][0]["disposition"] = "unknown"
    camp.write_json(vdir / camp.PROVENANCE_FILE, doc)
    ok, detail = camp.gate_record_complete(run_dir)
    assert not ok and "no candidateRegister" in detail and "unknown disposition: phab_XCMGW/catchment-hydrology: unknown" in detail
    assert "not intact" in detail                    # the provenance changed under the record
    fresh = make_staged_region(fx["root"], "55", "Eastern Corn Belt Plains", expect=digest_for("55"),
                               decisions_root=fx["decisions_root"])
    (fresh["vdir"] / camp.EVIDENCE_FILE).unlink()
    ok, detail = camp.gate_record_complete(fresh["run_dir"])
    assert not ok and "evidence.json is missing from the staged version" in detail
    fresh = make_staged_region(fx["root"], "55", "Eastern Corn Belt Plains", expect=digest_for("55"),
                               decisions_root=fx["decisions_root"])
    rec = camp.read_json(fresh["run_dir"] / camp.STAGE_COMPLETE)
    del rec["outputs"]["curve_gallery.html"]
    camp.write_json(fresh["run_dir"] / camp.STAGE_COMPLETE, rec)
    ok, detail = camp.gate_record_complete(fresh["run_dir"])
    assert not ok and "does not name: curve_gallery.html" in detail


# --------------------------------------------------------------------------- #
# the batch summary and the confirmation
# --------------------------------------------------------------------------- #
def _docs_for_batch(fx):
    rows = camp.index_rows(fx["root"], fx["manifest"], eligibility=None, promote_command=lambda d: "promote")
    index_doc = camp.index_document(fx["manifest"], rows, inputs={}, generated_at="t")
    policy = camp.load_promotion_policy()
    elig = {"55": camp.evaluate_gates(fx["run_dir"], expect=_expect(fx["manifest"], "55"),
                                      decisions_file=None, policy=policy)}
    return index_doc, {"regions": elig, "policy": camp.promotion_policy_record(policy)}


def test_batch_summary_text_and_commands(tmp_path):
    fx = standard_root(tmp_path)
    fx["manifest"]["regions"].append(region_row("65", "Northern Lakes and Forests", order=2))
    index_doc, elig = _docs_for_batch(fx)
    doc = camp.batch_summary_document(batch_id="one", manifest=fx["manifest"], index_doc=index_doc,
                                      eligibility_doc=elig, codes=["55", "65"], python="py", script="batch.py",
                                      inputs={"index.json": "sha256:1"}, facts=camp.region_batch_facts,
                                      generated_at="t")
    assert doc["schema"] == camp.BATCH_SCHEMA and doc["batchId"] == "one"
    assert [r["l3"] for r in doc["eligible"]] == ["55"] and [r["l3"] for r in doc["exceptions"]] == ["65"]
    e = doc["eligible"][0]
    assert e["policyDecisionIds"] == ["curve04-accept-with-flag"] and e["carriedApprovals"] == ["catchment-hydrology"]
    assert e["functionsCovered"] == 3 and len(e["gaps"]) == 17 and e["contentDigest"] == fx["bundle"]["contentDigest"]
    assert doc["exceptions"][0]["state"] == "not-started" and doc["exceptions"][0]["blockers"] == ["not evaluated by eligibility"]
    assert len(doc["promoteCommands"]) == 1
    cmd = doc["promoteCommands"][0]
    assert f'--maintainer "{camp.PLACEHOLDER}"' in cmd and "--status preliminary" in cmd
    assert f'--date "{camp.PLACEHOLDER}"' in cmd and cmd.endswith("--rebake-deep")
    assert "confirmedBy" not in json.dumps(doc["eligible"]) and TESTER not in cmd
    text = camp.batch_summary_markdown(doc)
    assert "# Promotion batch one" in text and "## Eligible (1)" in text and "## Exceptions (1)" in text
    assert "| 55 | Eastern Corn Belt Plains | noLocal |" in text and "l2 2" in text
    assert "curve04-accept-with-flag" in text and "No script" not in text.split("## Confirmation")[0]
    assert chr(0x2014) not in text
    assert e["advisoryOpen"] == [] and "| advisory open |" in text
    # promotion policy 1.1: an advisory open item leaves the region eligible and is
    # listed in the eligible table, never hidden
    advisory = make_staged_region(fx["root"], "55", "Eastern Corn Belt Plains", expect=digest_for("55"),
                                  decisions_root=fx["decisions_root"],
                                  open_items=[{"item_id": "CURVE-12:chem_PH", "trigger": "inverted_discrimination",
                                               "blocking": False, "question": "Direction wrong?"}])
    index_doc, elig = _docs_for_batch(fx)
    assert elig["regions"]["55"]["eligible"] is True and elig["regions"]["55"]["advisoryOpen"] == ["CURVE-12:chem_PH"]
    doc = camp.batch_summary_document(batch_id="adv", manifest=fx["manifest"], index_doc=index_doc,
                                      eligibility_doc=elig, codes=["55"], python="py", script="b.py", inputs={},
                                      facts=camp.region_batch_facts, generated_at="t")
    assert doc["eligible"][0]["advisoryOpen"] == ["CURVE-12:chem_PH"] and doc["exceptions"] == []
    assert "| CURVE-12:chem_PH |" in camp.batch_summary_markdown(doc)
    assert {r["l3"]: (r["openBlocking"], r["openAdvisory"]) for r in index_doc["regions"]}["55"] == (0, 1)
    assert advisory["run_dir"].is_dir()
    # with a blocking open item the region is an exception, its items verbatim
    opened = make_staged_region(fx["root"], "55", "Eastern Corn Belt Plains", expect=digest_for("55"),
                                decisions_root=fx["decisions_root"],
                                open_items=[{"item_id": "CURVE-07:phab_SINU", "trigger": "curve_needs_review",
                                             "blocking": False, "question": "Accept, adjust, or drop?"}])
    index_doc, elig = _docs_for_batch(fx)
    doc = camp.batch_summary_document(batch_id="two", manifest=fx["manifest"], index_doc=index_doc,
                                      eligibility_doc=elig, codes=["55"], python="py", script="b.py", inputs={},
                                      facts=camp.region_batch_facts, generated_at="t")
    assert doc["eligible"] == [] and doc["exceptions"][0]["openItems"][0]["question"] == "Accept, adjust, or drop?"
    assert doc["exceptions"][0]["openItems"][0]["blocking"] is True
    assert "CURVE-07:phab_SINU (curve_needs_review, blocking): Accept, adjust, or drop?" in camp.batch_summary_markdown(doc)
    assert doc["promoteCommands"] == []
    assert opened["run_dir"].is_dir()


def test_confirmation_validation_and_the_confirmed_commands(tmp_path):
    fx = standard_root(tmp_path)
    index_doc, elig = _docs_for_batch(fx)
    batch = camp.batch_summary_document(batch_id="one", manifest=fx["manifest"], index_doc=index_doc,
                                        eligibility_doc=elig, codes=["55"], python="py", script="b.py", inputs={},
                                        facts=camp.region_batch_facts, generated_at="t")
    good = {"batchId": "one", "policyVersion": "1.2", "campaignId": fx["manifest"]["identity"]["campaignId"],
            "regions": ["55"], "confirmedBy": TESTER, "confirmedAt": "2026-09-26",
            "statement": "Confirmed in chat on 2026-09-26: promote batch one."}
    assert camp.validate_confirmation(good, batch=batch, manifest=fx["manifest"]) == []
    assert camp.validate_confirmation(dict(good, statement=""), batch=batch, manifest=fx["manifest"]) == \
        ["statement is empty (the owner's words and date)"]
    assert camp.validate_confirmation(dict(good, regions=["55", "65"]), batch=batch, manifest=fx["manifest"]) == \
        ["region 65 is not eligible in the batch summary"]
    problems = camp.validate_confirmation(dict(good, campaignId="r3-other"), batch=batch, manifest=fx["manifest"])
    assert len(problems) == 1 and problems[0].startswith("campaignId 'r3-other' is not the campaign's")
    assert camp.validate_confirmation(dict(good, confirmedBy=" "), batch=batch, manifest=fx["manifest"]) == ["confirmedBy is empty"]
    assert "ISO date" in camp.validate_confirmation(dict(good, confirmedAt="yesterday"), batch=batch, manifest=fx["manifest"])[0]
    assert "batchId" in camp.validate_confirmation(dict(good, batchId="two"), batch=batch, manifest=fx["manifest"])[0]
    assert "policyVersion" in camp.validate_confirmation(dict(good, policyVersion="0.9"), batch=batch, manifest=fx["manifest"])[0]
    assert camp.validate_confirmation(dict(good, regions=[]), batch=batch, manifest=fx["manifest"]) == \
        ["regions must list at least one region code"]
    assert camp.validate_confirmation("not a document", batch=batch, manifest=fx["manifest"]) == \
        ["the confirmation is not a JSON object"]
    commands = camp.confirmed_promote_commands(batch, good, python="py", script="b.py")
    assert len(commands) == 1
    assert f"--maintainer {TESTER} --status preliminary --date 2026-09-26" in commands[0]
    assert commands[0].endswith("--rebake-deep") and camp.PLACEHOLDER not in commands[0]


# --------------------------------------------------------------------------- #
# the package
# --------------------------------------------------------------------------- #
def test_package_members_and_downloads(tmp_path):
    fx = standard_root(tmp_path)
    oc.save(fx["decisions_root"] / "l3-55", oc.new_decision("chem_TURB", oc.REMOVE, rationale=WHY, recorded_by=TESTER))
    fx["manifest"]["regions"][0]["decisionFiles"]["curve_decisions"] = camp.sha256_file(
        fx["decisions_root"] / "l3-55" / oc.DECISIONS_FILE)
    camp.write_json(fx["root"] / camp.MANIFEST_FILE, fx["manifest"])
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "STATE.md").write_text("state\n", encoding="utf-8")
    members = dict(camp.package_members(fx["root"], fx["manifest"], notes=notes))
    assert {camp.MANIFEST_FILE, camp.PROMOTION_POLICY_FILE, camp.STANDING_POLICY_FILE, camp.COMMANDS_FILE,
            "runs/l3-55/review_packet.json", "runs/l3-55/stage_complete.json", "runs/l3-55/rebuild_ledger.json",
            "runs/l3-55/standing_decisions_applied.json", "runs/l3-55/evidence.json",
            "decisions/l3-55/curve_decisions.json", "notes/STATE.md"} <= set(members)
    assert not any(m.startswith("runs/l3-55/library") for m in members)
    zip_path, doc = camp.build_package(fx["root"], tmp_path / "packages", fx["manifest"], notes=notes)
    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        inside = json.loads(zf.read("package.json").decode("utf-8"))
    assert "package.json" in names and set(members) <= names
    assert inside["schema"] == camp.PACKAGE_SCHEMA and inside["campaign"]["campaignId"] == fx["manifest"]["identity"]["campaignId"]
    assert {m["path"] for m in inside["members"]} == set(members)
    assert all(m["sha256"].startswith("sha256:") for m in inside["members"])
    assert inside["downloads"][0]["archive"] == "deep-dev-l3-55.evidence.zip" and inside["downloads"][0]["sha256"] == "3" * 64
    first = zip_path.read_bytes()
    camp.build_package(fx["root"], tmp_path / "packages", fx["manifest"], notes=notes)
    assert zip_path.read_bytes() == first                       # byte-deterministic
    beside = camp.read_json(tmp_path / "packages" / f"{fx['manifest']['identity']['campaignId']}.package.json")
    assert beside["zipSha256"] == camp.sha256_file(zip_path)
