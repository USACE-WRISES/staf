"""One offline stage (Eastern Corn Belt Plains on the legacy snapshot, no screen,
20 resamples, as test_batch_hardening does), then everything campaign Round 1 asks
of its record: the candidate register, the rebuild ledger and the evidence
reference in the staged version, the schema-2 manifest, promote's refusal of a
decision file that changed after the stage, and ``verify`` re-staging the version
into a temp root and reporting it equal.
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
from streamcurves import nrsa_dataset as nd
from streamcurves import session_io as sio

APP = Path(__file__).resolve().parents[1]
SCRIPT = APP / "scripts" / "run_region_batch.py"
LABEL = "Rehearsal (not an owner decision)"
L3, NAME = "55", "Eastern Corn Belt Plains"
ENABLED = ["curve07-thin-metric-finalized", "data03-thin-metric-finalized",
           "data06-insufficient-finalized"]


def _env() -> dict:
    env = dict(os.environ)
    env.pop("STAF_LIBRARY_ROOT", None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(args, timeout=1200):
    return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True,
                          text=True, env=_env(), timeout=timeout)


@pytest.fixture(scope="module")
def staged_run(tmp_path_factory):
    from conftest import documented_exclusions
    base = tmp_path_factory.mktemp("verify")
    out = base / "l3-55"
    exceptions = base / "exceptions.json"
    exceptions.write_text(json.dumps(documented_exclusions(
        reason="data-unavailable",
        justification="Offline test run without the StreamCat landscape join.")),
        encoding="utf-8")
    proc = _run(["stage", "--l3", L3, "--name", NAME, "--out", str(out),
                 "--decisions-root", str(base), "--nrsa-dataset", "legacy-1819",
                 "--no-screen", "--no-streamcat", "--n-boot", "20", "--maintainer", LABEL,
                 "--coverage-exceptions", str(exceptions),
                 *[x for pid in ENABLED for x in ("--enable-policy", pid)]])
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    packet = json.loads((out / "review_packet.json").read_text(encoding="utf-8"))
    assert packet["staged"], proc.stdout[-3000:]
    return {"base": base, "out": out, "exceptions": exceptions, "log": proc.stdout,
            "vdir": Path(packet["staged"]["path"]), "packet": packet}


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# what a stage writes (H4, H5, H7)
# --------------------------------------------------------------------------- #
def test_a_stage_writes_the_register_the_ledger_and_the_evidence_reference(staged_run):
    out, vdir = staged_run["out"], staged_run["vdir"]
    doc = _read(vdir / lib.PROVENANCE_FILE)
    # the candidate register rides in the provenance from the batch (H4)
    reg = doc["candidateRegister"]
    assert reg.get("rows") and reg.get("counts")
    assert all(r.get("candidateKey", "").startswith("cand-") for r in reg["rows"])
    # the rebuild ledger: beside the packet and in the provenance, the same document (H7)
    ledger_path = out / "rebuild_ledger.json"
    assert ledger_path.is_file()
    ledger = _read(ledger_path)
    assert ledger["schema"] == "rebuild-ledger/1" and ledger["rows"]
    assert ledger == doc["metricLedger"]
    assert b"\r\n" not in ledger_path.read_bytes()
    assert all(r.get("disposition") for r in ledger["rows"])
    # the evidence package: written under the run, referenced by digest from the
    # provenance, the run folder and the staged version (H5)
    ref = _read(out / "evidence.json")
    assert ref["packageId"] == "deep-dev-l3-55" and ref["packageDigest"].startswith("sha256:")
    assert ref["dataDigest"].startswith("sha256:") and ref["archive"]["sha256"]
    assert (out / "evidence" / ref["archive"]["name"]).is_file()
    assert (out / "evidence" / ref["packageId"] / "evidence.json").is_file()
    assert _read(vdir / "evidence.json") == ref
    assert doc["evidenceReferences"] == [ref]
    assert staged_run["packet"]["evidence"] == ref
    assert staged_run["packet"]["ledger"] == "rebuild_ledger.json"
    assert staged_run["packet"]["refit"]["mode"] in ("missing", "all")
    assert "evidence package deep-dev-l3-55" in staged_run["log"]
    # the evidence package's ledger and candidates are the provenance's
    import zipfile
    with zipfile.ZipFile(out / "evidence" / ref["archive"]["name"]) as zf:
        names = set(zf.namelist())
        assert {"evidence.json", "data/ledger.json", "data/candidates.json",
                "data/decisions.json", "data/curves.json"} <= names
        assert json.loads(zf.read("data/ledger.json").decode("utf-8")) == ledger


def test_the_manifest_declares_schema_2_and_records_the_round_one_inputs(staged_run):
    base, vdir = staged_run["base"], staged_run["vdir"]
    manifest = _read(vdir / lib.PROVENANCE_FILE)["manifest"]
    assert manifest["digestSchema"] == 2
    assert manifest["agent"]["codeFingerprint"] and len(manifest["agent"]["codeFingerprint"]) == 64
    assert manifest["inputs"]["refit"]["mode"] in ("missing", "all")
    # the decision files read, by path relative to the decisions root and sha
    files = manifest["reviewerInputs"]["files"]
    assert list(files) == ["exceptions.json"] and files["exceptions.json"].startswith("sha256:")
    assert manifest["reviewerInputs"]["decisionsRoot"] == str(base)
    assert manifest["reviewerInputs"]["decisionsDigest"].startswith("sha256:")
    assert manifest["experimental"] is None
    # legacy data records no value policy, and the legacy method no reference block
    assert manifest["inputs"]["nrsa_dataset"]["policy"] is None
    assert "reference" not in manifest["inputs"]
    # the session carries the Round 1 fields
    fields = sio.decode_session_fields(sio.load_session_payload(vdir / lib.SESSION_FILE))
    assert fields["rule_selections"] == ENABLED
    assert fields["candidate_register"] is None
    approvals = fields["portfolio_approvals"] or []
    assert all(set(a) == {"functionId", "approver", "note", "date"} for a in approvals)
    assert fields["reference_build"] is None          # a legacy build
    # the run's record binds every file the packet and the ledger cite
    run_session = sio.decode_session_fields(sio.load_session_payload(
        staged_run["out"] / "assessment.streamcurves.json"))
    assert run_session["rule_selections"] == ENABLED


# --------------------------------------------------------------------------- #
# promote refuses a decision file that changed after the stage, then records
# the confirming maintainer as the author of a pending-labeled version
# --------------------------------------------------------------------------- #
def test_promote_refuses_changed_answer_files_then_publishes_with_the_evidence(staged_run, tmp_path):
    out, exceptions, vdir = staged_run["out"], staged_run["exceptions"], staged_run["vdir"]
    target = tmp_path / "promoted"
    (target / "assessments").mkdir(parents=True)
    original = exceptions.read_bytes()
    exceptions.write_bytes(original + b"\n")
    try:
        proc = _run(["promote", "--out", str(out), "--maintainer", "GM", "--publish-root",
                     str(target), "--date", "2026-09-25"], timeout=600)
        assert proc.returncode != 0
        assert "changed after the stage" in proc.stderr and "exceptions.json: changed" in proc.stderr
        assert not any((target / "assessments").iterdir())
    finally:
        exceptions.write_bytes(original)
    # a version staged under a pending label is the confirming maintainer's
    meta_path = vdir / lib.META_FILE
    meta = _read(meta_path)
    staged_author = meta["author"]
    meta["author"] = "policy-candidate " + dec.PENDING_SUFFIX
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    try:
        proc = _run(["promote", "--out", str(out), "--maintainer", "GM", "--publish-root",
                     str(target), "--date", "2026-09-25"], timeout=600)
    finally:
        meta["author"] = staged_author
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    record = _read(out / "promote_record.json")
    assert record["contentDigestMatchesStaged"] is True
    pdir = target / "assessments" / "eastern-corn-belt-plains" / f"v{record['publishedVersion']}"
    assert _read(pdir / lib.META_FILE)["author"] == "GM"
    assert _read(pdir / "evidence.json") == _read(vdir / "evidence.json")
    fields = sio.decode_session_fields(sio.load_session_payload(pdir / lib.SESSION_FILE))
    assert not any(dec.PENDING_SUFFIX in str(a.get("approver")) for a in fields["portfolio_approvals"] or [])


# --------------------------------------------------------------------------- #
# verify re-stages the version and reports it equal
# --------------------------------------------------------------------------- #
def test_verify_reports_equal_on_the_offline_stage(staged_run, tmp_path):
    vdir = staged_run["vdir"]
    root = tmp_path / "verify"
    proc = _run(["verify", "--version-dir", str(vdir), "--out", str(root)])
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-3000:]
    assert "[verify] EQUAL" in proc.stdout and "contentDigest equal; inputsDigest equal" in proc.stdout
    report = _read(root / "verify_report.json")
    assert report["equal"] is True and report["decisionFiles"] == []
    assert report["contentDigest"]["recorded"] == report["contentDigest"]["restaged"]
    assert report["inputsDigest"]["recorded"] == report["inputsDigest"]["restaged"]
    assert report["compare"]["curves"]["differ"] == {} and not report["compare"]["curves"]["only_b"]
    # the re-stage recorded the same command and the same decision files
    restaged = _read(Path(report["restagedDir"]) / lib.PROVENANCE_FILE)["manifest"]
    recorded = _read(vdir / lib.PROVENANCE_FILE)["manifest"]
    assert restaged["agent"]["argv"] == recorded["agent"]["argv"]
    assert restaged["reviewerInputs"]["files"] == recorded["reviewerInputs"]["files"]


def test_replay_names_the_recorded_value_policy(staged_run):
    proc = _run(["replay", "--version-dir", str(staged_run["vdir"])], timeout=300)
    assert "value policy none recorded (legacy data)" in proc.stdout, proc.stdout[-2000:]
    import importlib.util
    scripts = str(SCRIPT.parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("run_region_batch_verify", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.recorded_value_policy(staged_run["vdir"]) is None
    assert mod.recorded_value_policy_of(
        {"inputs": {"nrsa_dataset": {"policy": nd.VALUE_POLICY_V1}}}) == nd.VALUE_POLICY_V1


def test_open_prints_the_project_and_the_candidate_link(staged_run):
    out, vdir = staged_run["out"], staged_run["vdir"]
    doc = _read(vdir / lib.PROVENANCE_FILE)
    key = doc["candidateRegister"]["rows"][0]["candidateKey"]
    proc = _run(["open", "--out", str(out), "--candidate", key], timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == str(vdir / lib.SESSION_FILE)
    assert lines[1].startswith("http://127.0.0.1:8012/?project=") and lines[1].endswith(
        "&candidate=" + key)
    assert _run(["open", "--out", str(out), "--candidate", "cand-nope"], timeout=120).returncode == 2
