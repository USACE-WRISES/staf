"""``scripts/run_campaign.py`` end to end on synthetic campaign roots (campaign Round 3,
WP-R3): plan, run, index, eligibility, batch-summary, confirm, compare, package, and
``run_region_batch.py promote --status policy``. The code fingerprint, the config
fingerprints and the git helpers are patched; no region is staged, no subprocess runs, the
NRSA archive is never opened.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from streamcurves import campaign as camp
from streamcurves import decisions as dec
from streamcurves import library as lib
from streamcurves import owner_curves as oc
from streamcurves import session_io as sio
from test_campaign_helpers import (CODE_FP, COMMIT, LABEL, TESTER, WHY, digest_for, fake_manifest,
                                   load_runner, make_bundle, make_provenance, make_staged_region,
                                   pending_record, region_row, standard_root, write_batch_records)

FPS = {"methodology_version": "0.14-provisional", "config_path": "config/methodology/methodology_config.yaml",
       "config_sha256": "sha256:0409ff11" + "0" * 56, "rule_catalog_path": "config/methodology/rule_catalog.json",
       "rule_catalog_sha256": "sha256:87a75ffb" + "0" * 56}


@pytest.fixture
def runner(monkeypatch):
    mod = load_runner()
    monkeypatch.setattr(mod, "tree_fingerprint", lambda app_root: CODE_FP)
    monkeypatch.setattr(mod.rrb, "code_fingerprint", lambda: CODE_FP)
    monkeypatch.setattr(mod.code_identity, "git_dirty", lambda repo=None: False)
    monkeypatch.setattr(mod.code_identity, "git_head", lambda repo=None: COMMIT)
    monkeypatch.setattr(mod.methodology, "config_fingerprints", lambda: dict(FPS))
    return mod


def _fake_worktree(tmp_path) -> Path:
    wt = tmp_path / "wt"
    (wt / "apps" / "stream-curves" / "scripts").mkdir(parents=True)
    (wt / "apps" / "stream-curves" / "scripts" / "run_region_batch.py").write_text("# fake\n", encoding="utf-8")
    return wt


def _plan_inputs(tmp_path, codes=(("55", "Eastern Corn Belt Plains", 44, 0), ("65", "Northern Lakes and Forests", 60, 25),
                                  ("999", "Nowhere", 3, 0))) -> dict:
    """A crosswalk, a census and a station screen for a few codes (999 has no NRSA sites)."""
    crosswalk = tmp_path / "crosswalk.csv"
    crosswalk.write_text("\"us_l3code\",\"us_l3name\",\"abbr\"\n"
                         + "".join(f"\"{c}\",\"{n}\",\n" for c, n, _, _ in codes), encoding="utf-8")
    census = tmp_path / "census.csv"
    lines = ["l3,region,metric,status"]
    for c, n, _, _ in codes:
        for metric, status in (("bent_EPT_NTAX", "local"), ("chem_COND", "borrowed_l2"), ("bfiws", "national"),
                               ("chem_CHLA", "insufficient"), ("phab_XCMGW", "borrowed_nars9")):
            lines.append(f"{c},{n},{metric},{status}")
    census.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = []
    for c, _, n_frame, n_strict in codes:
        for i in range(max(n_frame, 1)):
            rows.append({"l3": c, "l2": "8.1", "l1": "8", "nars9": "NAP", "wadeable": i < n_frame,
                         "pass_strict": i < n_strict})
    screen = tmp_path / "screen.parquet"
    pd.DataFrame(rows).to_parquet(screen, index=False)
    return {"crosswalk": crosswalk, "census": census, "screen": screen}


def _plan_argv(root, wt, inputs, *extra) -> list[str]:
    return ["plan", "--root", str(root), "--purpose", "fast", "--worktree", str(wt), "--census", str(inputs["census"]),
            "--crosswalk", str(inputs["crosswalk"]), "--station-screen", str(inputs["screen"]), "--n-boot", "50",
            "--workers", "2", "--maintainer", LABEL, "--refit", "all", "--decisions-root", str(root.parent / "decisions"),
            *extra]


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #
def test_plan_refuses_a_dirty_tree_an_existing_root_and_a_persons_label(runner, tmp_path, monkeypatch, capsys):
    wt = _fake_worktree(tmp_path)
    inputs = _plan_inputs(tmp_path)
    root = tmp_path / "campaign"
    monkeypatch.setattr(runner.code_identity, "git_dirty", lambda repo=None: True)
    assert runner.main(_plan_argv(root, wt, inputs)) == 2
    assert "uncommitted changes" in capsys.readouterr().out and not (root / camp.MANIFEST_FILE).exists()
    monkeypatch.setattr(runner.code_identity, "git_dirty", lambda repo=None: None)
    assert runner.main(_plan_argv(root, wt, inputs)) == 2
    assert "git cannot say" in capsys.readouterr().out
    monkeypatch.setattr(runner.code_identity, "git_dirty", lambda repo=None: False)
    argv = _plan_argv(root, wt, inputs)
    argv[argv.index("--maintainer") + 1] = TESTER
    assert runner.main(argv) == 2
    assert "reads as a person's name" in capsys.readouterr().out
    assert runner.main(_plan_argv(root, wt, inputs)) == 0
    assert (root / camp.MANIFEST_FILE).is_file()
    assert runner.main(_plan_argv(root, wt, inputs)) == 2
    assert "the manifest is the frozen record" in capsys.readouterr().out
    assert runner.main(_plan_argv(root, wt, inputs, "--force")) == 0
    # a tree other than the one plan runs from
    monkeypatch.setattr(runner, "tree_fingerprint", lambda app_root: "0" * 64)
    assert runner.main(_plan_argv(root, wt, inputs, "--force")) == 2
    assert "run plan from the worktree it describes" in capsys.readouterr().out


def test_plan_writes_the_manifest_regions_in_order_and_the_commands(runner, tmp_path):
    wt = _fake_worktree(tmp_path)
    inputs = _plan_inputs(tmp_path)
    root = tmp_path / "campaign"
    assert runner.main(_plan_argv(root, wt, inputs)) == 0
    manifest = camp.read_manifest(root)
    ident = manifest["identity"]
    assert ident["campaignId"] == "r3-fast-0409ff11-b50" and ident["commit"] == COMMIT and ident["gitDirty"] is False
    assert ident["worktree"] == str(wt.resolve()) and ident["purpose"] == "fast"
    assert manifest["code"]["fingerprint"] == CODE_FP and manifest["inputs"]["code"] == CODE_FP
    assert manifest["inputs"]["methodology"] == FPS and manifest["inputs"]["flags"]["refit"] == "all"
    assert manifest["inputs"]["flags"]["n_boot"] == 50 and manifest["inputs"]["flags"]["maintainer"] == LABEL
    assert manifest["inputs"]["policy"]["version"] == dec.policy_version(dec.load_policy(None))
    assert manifest["inputs"]["promotionPolicy"]["sha256"] == camp.load_promotion_policy()["meta"]["sha256"]
    assert manifest["inputs"]["stationScreen"] and manifest["inputs"]["nrsaManifest"]
    assert manifest["run"] == {"workers": 2, "nBoot": 50, "maintainer": LABEL, "refit": "all",
                               "decisionsRoot": str(tmp_path / "decisions")}
    assert manifest["gateReport"] is None and "configRoot" not in manifest["run"]
    # frame size descending: 65 (60), 55 (44), 999 (3)
    codes = [r["l3"] for r in manifest["regions"]]
    assert codes == ["65", "55", "999"] and [r["order"] for r in manifest["regions"]] == [1, 2, 3]
    r65, r55, r999 = manifest["regions"]
    assert r65["supportClass"] == "local20plus" and r55["supportClass"] == "noLocal" and r999["supportClass"] == "frameUnder10"
    assert r55 == {**r55, "name": "Eastern Corn Belt Plains", "l2": "8.1", "l1": "8", "nars9": "NAP", "nFrame": 44,
                   "nStrict": 0, "nMetrics": 5, "nLocal": 1, "nBorrowedL2": 1, "nBorrowedNars9": 1, "nBorrowedL1": 0,
                   "nNational": 1, "nModeled": 0, "nPublished": 0, "nInsufficient": 1, "carriedFrom": None,
                   "runFolder": "runs/l3-55"}
    assert r55["decisionFiles"] == {n: None for n in camp.DECISION_FILE_NAMES}
    # the expected digest is stage-many's own: the same functions over the same flags
    tokens = manifest["commands"]["stageMany"]
    assert tokens[:6] == ["stage-many", "--out-root", str(root / "runs"), "--workers", "2", "--n-boot"]
    assert tokens[-6:] == ["--l3", "65", "--l3", "55", "--l3", "999"]
    many = runner.rrb.build_parser().parse_args(tokens)
    ns = runner.rrb.region_stage_namespace(many, "55", "Eastern Corn Belt Plains", root / "runs" / "l3-55", argv=tokens)
    expected = runner.rrb.region_digest("55", "Eastern Corn Belt Plains", runner.rrb.region_inputs(vars(ns)), None)
    assert r55["inputsDigest"] == expected and len(expected) == 64
    assert r999["inputsDigest"] is None and r999["stageName"] is None and r55["stageName"] == "Eastern Corn Belt Plains"
    assert "55" in manifest["commands"]["stage"] and "999" not in manifest["commands"]["stage"]
    assert manifest["commands"]["stage"]["55"][:4] == ["stage", "--l3", "55", "--name"]
    # the files beside the manifest
    commands = (root / camp.COMMANDS_FILE).read_text(encoding="utf-8")
    assert camp.render_command(tokens) in commands and "L3-999 Nowhere: no NRSA candidate sites" in commands
    assert "run_campaign.py index --root" in commands and "eligibility --root" in commands
    state = (root / camp.STATE_FILE).read_text(encoding="utf-8")
    assert ident["campaignId"] in state and COMMIT in state and "--dry-run" in state
    assert (root / camp.PROMOTION_POLICY_FILE).read_bytes() == camp.PROMOTION_POLICY_PATH.read_bytes()
    assert (root / camp.STANDING_POLICY_FILE).read_bytes() == dec.POLICY_PATH.read_bytes()
    assert (root / "runs").is_dir()
    for name in (camp.MANIFEST_FILE, camp.COMMANDS_FILE, camp.STATE_FILE):
        text = (root / name).read_text(encoding="utf-8")
        assert chr(0x2014) not in text and b"\r\n" not in (root / name).read_bytes()
    # ordered by an earlier index's seconds, and narrowed to two codes
    index_csv = tmp_path / "fast_index.csv"
    index_csv.write_text("l3,name,seconds\n55,x,900\n65,y,100\n999,z,\n", encoding="utf-8")
    assert runner.main(_plan_argv(root, wt, inputs, "--force", "--order-from", str(index_csv), "--l3", "55", "--l3", "65")) == 0
    manifest = camp.read_manifest(root)
    assert [r["l3"] for r in manifest["regions"]] == ["55", "65"]
    assert manifest["sources"]["orderFrom"]["path"] == str(index_csv.resolve())
    assert manifest["commands"]["stageMany"][-4:] == ["--l3", "55", "--l3", "65"]


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def test_run_checks_every_recorded_value_then_runs_the_line(runner, tmp_path, monkeypatch, capsys):
    wt = _fake_worktree(tmp_path)
    inputs = _plan_inputs(tmp_path)
    root = tmp_path / "campaign"
    assert runner.main(_plan_argv(root, wt, inputs)) == 0
    assert runner.main(["run", "--root", str(root), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "every recorded value matches the live tree" in out and "dry run: nothing started" in out
    calls = []

    class _Proc:
        returncode = 0

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return _Proc()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setenv("STREAMCURVES_CONFIG_ROOT", "")
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(tmp_path / "stale"))
    assert runner.main(["run", "--root", str(root)]) == 0
    argv, kw = calls[0]
    assert argv[1:3] == ["-B", str(wt.resolve() / "apps" / "stream-curves" / "scripts" / "run_region_batch.py")]
    assert argv[3:] == camp.read_manifest(root)["commands"]["stageMany"]
    assert kw["cwd"] == str(wt.resolve() / "apps" / "stream-curves")
    assert "STAF_LIBRARY_ROOT" not in kw["env"] and "STREAMCURVES_CONFIG_ROOT" not in kw["env"]
    assert kw["env"]["PYTHONIOENCODING"] == "utf-8"

    def refused(*patches, contains):
        for target, name, value in patches:
            monkeypatch.setattr(target, name, value)
        rc = runner.main(["run", "--root", str(root), "--dry-run"])
        text = capsys.readouterr().out
        assert rc == 2 and contains in text, text
        monkeypatch.undo()
        # the fixture's own patches are restored with the test's monkeypatch; re-apply them
        monkeypatch.setattr(runner, "tree_fingerprint", lambda app_root: CODE_FP)
        monkeypatch.setattr(runner.rrb, "code_fingerprint", lambda: CODE_FP)
        monkeypatch.setattr(runner.code_identity, "git_dirty", lambda repo=None: False)
        monkeypatch.setattr(runner.code_identity, "git_head", lambda repo=None: COMMIT)
        monkeypatch.setattr(runner.methodology, "config_fingerprints", lambda: dict(FPS))
        monkeypatch.setattr(runner.subprocess, "run", fake_run)

    refused((runner, "tree_fingerprint", lambda app_root: "0" * 64), (runner.rrb, "code_fingerprint", lambda: "0" * 64),
            contains="code.fingerprint (code)")
    refused((runner.code_identity, "git_dirty", lambda repo=None: True), contains="identity.gitDirty")
    refused((runner.code_identity, "git_head", lambda repo=None: "beef" * 10), contains="identity.commit")
    refused((runner.methodology, "config_fingerprints", lambda: {**FPS, "config_sha256": "sha256:moved"}),
            contains="inputs.methodology.config_sha256")
    policy_copy = root / camp.PROMOTION_POLICY_FILE
    original = policy_copy.read_bytes()
    policy_copy.write_bytes(original + b"\n# edited\n")
    refused(contains=f"{camp.PROMOTION_POLICY_FILE} beside the manifest")
    policy_copy.write_bytes(original)
    oc.save(tmp_path / "decisions" / "l3-55", oc.new_decision("chem_TURB", oc.REMOVE, rationale=WHY, recorded_by=TESTER))
    refused(contains="regions.l3-55.decisionFiles.curve_decisions")
    (tmp_path / "decisions" / "l3-55" / oc.DECISIONS_FILE).unlink()
    commands = root / camp.COMMANDS_FILE
    commands.write_text(commands.read_text(encoding="utf-8").replace("--n-boot 50", "--n-boot 51"), encoding="utf-8")
    refused(contains="commands.md: the stage-many line differs")
    assert runner.main(["run", "--root", str(tmp_path / "nowhere"), "--dry-run"]) == 2


# --------------------------------------------------------------------------- #
# index
# --------------------------------------------------------------------------- #
def _packet_without_a_version(run_dir, code, name, *, missing=0) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    camp.write_json(run_dir / camp.PACKET_FILE,
                    {"schemaVersion": 1, "region": {"kind": "ecoregion", "code": code, "name": name}, "staged": None,
                     "open_items": [], "hard_stops": [], "curves": [{"metric": "x"}], "decisions_applied": [],
                     "coverage": {"total": 20, "covered": 3, "excluded": 17 - missing, "missing": missing},
                     "policy": {"applied_ids": []}})


def test_index_writes_every_state(runner, tmp_path):
    root = tmp_path / "campaign"
    wt = _fake_worktree(tmp_path)
    decisions_root = tmp_path / "decisions"
    decisions_root.mkdir()
    names = {"55": "Eastern Corn Belt Plains", "65": "Northern Lakes and Forests", "71": "Interior Plateau",
             "58": "Northeastern Highlands", "50": "Northern Minnesota Wetlands", "13": "Central Basin and Range",
             "27": "Central Great Plains", "1": "Coast Range", "17": "Middle Rockies"}
    regions = [region_row(c, n, order=i + 1) for i, (c, n) in enumerate(names.items())]
    manifest = fake_manifest(root, regions, worktree=wt, decisions_root=decisions_root)
    make_staged_region(root, "55", names["55"], expect=digest_for("55"), decisions_root=decisions_root)
    make_staged_region(root, "65", names["65"], expect=digest_for("65"), decisions_root=decisions_root,
                       open_items=[{"item_id": "SELECT-01:habitat-provision", "trigger": "more_than_two_metrics",
                                    "blocking": False, "question": "Approve or trim?"}])
    runs = root / camp.RUNS_DIR
    _packet_without_a_version(runs / "l3-58", "58", names["58"])
    (runs / "l3-58" / camp.STAGE_LOG).write_text("[batch] evidence: 0 / 12 retained\n[batch] no bundle to stage: "
                                                 "no metric survived the hierarchy\n", encoding="utf-8")
    _packet_without_a_version(runs / "l3-50", "50", names["50"])
    (runs / "l3-50" / camp.STAGE_LOG).write_text("[batch] staged publish refused: Refusing to publish 'x': more than 2 "
                                                 "metrics per function requires a recorded human approval (SELECT-01).\n",
                                                 encoding="utf-8")
    _packet_without_a_version(runs / "l3-17", "17", names["17"], missing=2)
    rows = [{"l3": "55", "name": names["55"], "exit": 0, "seconds": 300.0, "error": None, "staged_version": 1},
            {"l3": "65", "name": names["65"], "exit": 0, "seconds": 200.0, "error": None, "staged_version": 1},
            {"l3": "71", "name": None, "exit": 1, "error": "no NRSA candidate sites for L3 ecoregion 71"},
            {"l3": "58", "name": names["58"], "exit": 0, "seconds": 50.0, "error": None},
            {"l3": "50", "name": names["50"], "exit": 0, "seconds": 60.0, "error": None},
            {"l3": "13", "name": names["13"], "exit": 1, "seconds": 20.0, "error": "[batch] REFUSED: 40 of 100 unresolved"},
            {"l3": "27", "name": names["27"], "exit": 1, "seconds": 5.0, "error": "Traceback (most recent call last)"},
            {"l3": "17", "name": names["17"], "exit": 0, "seconds": 70.0, "error": None}]
    jobs = [{"label": f"L3-55 {names['55']}", "state": "completed", "seconds": 300.0, "exit": None, "peakMemoryMB": 310.0},
            {"label": f"L3-65 {names['65']}", "state": "completed", "seconds": 200.0, "exit": None, "peakMemoryMB": 290.0},
            {"label": f"L3-58 {names['58']}", "state": "completed", "seconds": 50.0, "exit": None, "peakMemoryMB": 100.0},
            {"label": f"L3-50 {names['50']}", "state": "completed", "seconds": 60.0, "exit": None, "peakMemoryMB": 100.0},
            {"label": f"L3-13 {names['13']}", "state": "failed", "seconds": 20.0, "exit": 2, "peakMemoryMB": 90.0,
             "retries": 1, "log": "[batch] REFUSED: 40 of 100 candidates unresolved by the screen (40%)\n"},
            {"label": f"L3-27 {names['27']}", "state": "failed", "seconds": 5.0, "exit": 1, "peakMemoryMB": 80.0,
             "retries": 1, "log": "Traceback (most recent call last)\nValueError: boom\n"},
            {"label": f"L3-17 {names['17']}", "state": "completed", "seconds": 70.0, "exit": None, "peakMemoryMB": 100.0}]
    write_batch_records(root, rows, jobs)
    assert runner.main(["index", "--root", str(root)]) == 0
    doc = camp.read_json(root / camp.INDEX_JSON)
    assert doc["schema"] == camp.INDEX_SCHEMA and doc["campaign"]["campaignId"] == manifest["identity"]["campaignId"]
    by = {r["l3"]: r for r in doc["regions"]}
    states = {c: by[c]["state"] for c in names}
    assert states == {"55": "staged", "65": "staged-open", "71": "no-data", "58": "unsupported", "50": "incomplete",
                      "13": "refused", "27": "failed", "1": "not-started", "17": "unsupported"}
    assert doc["counts"] == {"failed": 1, "incomplete": 1, "no-data": 1, "not-started": 1, "refused": 1, "staged": 1,
                             "staged-open": 1, "unsupported": 2}
    assert by["55"]["exit"] == 0 and by["55"]["seconds"] == 300.0 and by["55"]["peakMemoryMB"] == 310.0
    assert by["55"]["stagedVersion"] == 1 and by["55"]["functionsCovered"] == 20 and by["55"]["promoteEligible"] is None
    assert by["55"]["inputsDigest"] == digest_for("55") and by["55"]["promoteCommand"].endswith("--rebake-deep")
    assert by["65"]["openItems"] == 1 and by["65"]["stateDetail"] == "1 open item(s), 0 hard stop(s)"
    assert by["65"]["openBlocking"] == 0 and by["65"]["openAdvisory"] == 1      # promotion policy 1.1
    assert by["13"]["exit"] == 2 and by["13"]["stateDetail"].startswith("exit 2 after 2 attempt(s): 40 of 100")
    assert by["27"]["exit"] == 1 and "after 2 attempt(s)" in by["27"]["stateDetail"]
    assert by["58"]["stateDetail"] == "no metric survived the hierarchy"
    assert "SELECT-01" in by["50"]["stateDetail"] and "2 function(s) neither covered nor documented" in by["17"]["stateDetail"]
    assert by["71"]["stateDetail"].startswith("no NRSA candidate sites") and by["1"]["stateDetail"] == ""
    assert all(by[c]["promoteCommand"] is None for c in ("71", "58", "50", "13", "27", "1", "17"))
    state = camp.read_json(runs / "l3-71" / camp.REGION_STATE_FILE)
    assert state["schema"] == camp.REGION_STATE_SCHEMA and state["state"] == "no-data"
    assert state["inputs"]["batch_summary.json"] == camp.sha256_file(runs / "batch_summary.json")
    csv_text = (root / camp.INDEX_CSV).read_text(encoding="utf-8")
    header = csv_text.splitlines()[0].split(",")
    assert header == list(camp.INDEX_COLUMNS) and len(csv_text.splitlines()) == 1 + len(names)
    assert doc["inputs"]["runs/batch_summary.json"] == camp.sha256_file(runs / "batch_summary.json")
    # a second index is the same bytes but for the stamp
    first = (root / camp.INDEX_JSON).read_text(encoding="utf-8")
    assert runner.main(["index", "--root", str(root)]) == 0
    second = (root / camp.INDEX_JSON).read_text(encoding="utf-8")
    strip = lambda t: "\n".join(ln for ln in t.splitlines() if '"generatedAt"' not in ln)  # noqa: E731
    assert strip(first) == strip(second)


# --------------------------------------------------------------------------- #
# eligibility, batch-summary, confirm
# --------------------------------------------------------------------------- #
def test_eligibility_batch_summary_and_confirm(runner, tmp_path, capsys):
    fx = standard_root(tmp_path)
    root = fx["root"]
    fx["manifest"]["regions"].append(region_row("65", "Northern Lakes and Forests", order=2))
    camp.write_json(root / camp.MANIFEST_FILE, fx["manifest"])
    make_staged_region(root, "65", "Northern Lakes and Forests", expect=digest_for("65"),
                       decisions_root=fx["decisions_root"], n_boot=200,
                       open_items=[{"item_id": "CURVE-07:phab_SINU", "trigger": "curve_needs_review", "blocking": True,
                                    "question": "Accept this curve as preliminary, adjust it, or drop the metric?"}])
    assert runner.main(["eligibility", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "L3-55 Eastern Corn Belt Plains: eligible" in out and "L3-65 Northern Lakes and Forests: not eligible" in out
    doc = camp.read_json(root / camp.ELIGIBILITY_FILE)
    assert doc["schema"] == camp.ELIGIBILITY_SCHEMA and doc["policy"]["version"] == "1.2"
    assert doc["policy"]["sha256"] == camp.load_promotion_policy()["meta"]["sha256"]
    assert doc["campaignId"] == fx["manifest"]["identity"]["campaignId"]
    assert doc["regions"]["55"]["eligible"] is True and doc["regions"]["55"]["blockers"] == []
    assert set(doc["regions"]["55"]["gates"]) == set(camp.GATE_IDS)
    r65 = doc["regions"]["65"]
    assert r65["eligible"] is False
    assert any(b.startswith("frozen-record: n-boot 200 is not the campaign's 1000") for b in r65["blockers"])
    assert any(b.startswith("rules-applied: 1 blocking open item(s): CURVE-07:phab_SINU") for b in r65["blockers"])
    assert r65["gates"]["equivalence-proven"]["passed"] is True
    assert doc["regions"]["55"]["advisoryOpen"] == [] and r65["advisoryOpen"] == []
    # the index reads the verdicts back
    assert runner.main(["index", "--root", str(root)]) == 0
    by = {r["l3"]: r for r in camp.read_json(root / camp.INDEX_JSON)["regions"]}
    assert by["55"]["promoteEligible"] is True and by["55"]["promoteReasons"] == []
    assert by["65"]["promoteEligible"] is False and by["65"]["state"] == "staged-open"
    # a narrowed run keeps the other region's verdict
    assert runner.main(["eligibility", "--root", str(root), "--l3", "65"]) == 0
    doc = camp.read_json(root / camp.ELIGIBILITY_FILE)
    assert set(doc["regions"]) == {"55", "65"} and doc["regions"]["55"]["eligible"] is True
    assert runner.main(["eligibility", "--root", str(root), "--l3", "999"]) == 2
    # the batch summary
    assert runner.main(["batch-summary", "--root", str(root), "--batch", "b1"]) == 0
    batch = camp.read_json(root / "promotion_batch_b1.json")
    assert [r["l3"] for r in batch["eligible"]] == ["55"] and [r["l3"] for r in batch["exceptions"]] == ["65"]
    assert batch["exceptions"][0]["openItems"][0]["item_id"] == "CURVE-07:phab_SINU"
    assert batch["inputs"]["index.json"] == camp.sha256_file(root / camp.INDEX_JSON)
    text = (root / "promotion_batch_b1.md").read_text(encoding="utf-8")
    assert "## Eligible (1)" in text and "CURVE-07:phab_SINU (curve_needs_review, blocking)" in text
    assert camp.PLACEHOLDER in text and "confirmedBy" in text and not list(root.glob("*.confirmation.json"))
    assert runner.main(["batch-summary", "--root", str(root), "--batch", "b two"]) == 2
    # the confirmation, written by hand
    confirmation = root / "promotion_batch_b1.confirmation.json"
    good = {"batchId": "b1", "policyVersion": "1.2", "campaignId": fx["manifest"]["identity"]["campaignId"],
            "regions": ["55"], "confirmedBy": TESTER, "confirmedAt": "2026-09-26",
            "statement": "Confirmed in chat on 2026-09-26."}
    camp.write_json(confirmation, dict(good, campaignId="r3-frozen-00000000-b1"))
    assert runner.main(["confirm", "--root", str(root), "--batch", "b1", "--confirmation", str(confirmation)]) == 2
    assert "campaignId" in capsys.readouterr().out
    camp.write_json(confirmation, dict(good, regions=["55", "65"]))
    assert runner.main(["confirm", "--root", str(root), "--batch", "b1", "--confirmation", str(confirmation)]) == 2
    assert "region 65 is not eligible" in capsys.readouterr().out
    camp.write_json(confirmation, dict(good, statement=""))
    assert runner.main(["confirm", "--root", str(root), "--batch", "b1", "--confirmation", str(confirmation)]) == 2
    assert "statement is empty" in capsys.readouterr().out
    camp.write_json(confirmation, good)
    assert runner.main(["confirm", "--root", str(root), "--batch", "b1", "--confirmation", str(confirmation)]) == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if " promote --out " in ln]
    assert len(lines) == 1
    assert f"--maintainer {TESTER} --status preliminary --date 2026-09-26 --publish-root apps/library --rebake-deep" in lines[0]
    assert str(fx["run_dir"]) in lines[0] and camp.PLACEHOLDER not in out
    assert runner.main(["confirm", "--root", str(root), "--batch", "b9", "--confirmation", str(confirmation)]) == 2


# --------------------------------------------------------------------------- #
# promote --status policy
# --------------------------------------------------------------------------- #
def test_promote_status_policy_resolves_from_the_gates(runner, tmp_path, monkeypatch):
    fx = standard_root(tmp_path)
    rrb = runner.rrb
    status, res = rrb.resolve_policy_status(fx["run_dir"])
    assert status == "preliminary" and res["blockers"] == [] and res["policyVersion"] == "1.2"
    assert res["campaignManifest"] == str(fx["root"] / camp.MANIFEST_FILE)
    assert res["gates"]["equivalence-proven"]["passed"] is True       # the manifest names the gate report
    assert res["expectation"].startswith("campaign manifest r3-frozen-")
    # outside a campaign root: the run's own record, the equivalence gate not evaluated
    alone = tmp_path / "alone" / "l3-55"
    import shutil
    shutil.copytree(fx["run_dir"], alone)
    status, res = rrb.resolve_policy_status(alone)
    assert status == "preliminary" and res["campaignManifest"] is None and res["expectation"] == "the run's own record"
    assert res["gates"]["equivalence-proven"]["passed"] is None
    status, res = rrb.resolve_policy_status(alone, gate_report=str(fx["gate_report"]))
    assert status == "preliminary" and res["gates"]["equivalence-proven"]["passed"] is True
    # the gates decide: patched to fail, the status is draft with the blockers
    monkeypatch.setattr(camp, "evaluate_gates", lambda *a, **k: {
        "eligible": False, "blockers": ["rules-applied: 1 open item(s): X"], "gates": {}, "evaluated": [], "skipped": []})
    status, res = rrb.resolve_policy_status(fx["run_dir"])
    assert status == "draft" and res["blockers"] == ["rules-applied: 1 open item(s): X"]
    monkeypatch.setattr(camp, "evaluate_gates", lambda *a, **k: {
        "eligible": True, "blockers": [], "gates": {}, "evaluated": [], "skipped": []})
    assert rrb.resolve_policy_status(fx["run_dir"])[0] == "preliminary"
    assert rrb.build_parser().parse_args(["promote", "--out", "x", "--maintainer", "y", "--status", "policy",
                                          "--gate-report", "r.json"]).gate_report == "r.json"


def test_promote_status_policy_publishes_preliminary_with_the_policy_on_the_record(runner, tmp_path):
    fx = standard_root(tmp_path)
    rrb = runner.rrb
    target = tmp_path / "isolated"
    (target / "assessments").mkdir(parents=True)
    ns = rrb.build_parser().parse_args(["promote", "--out", str(fx["run_dir"]), "--maintainer", TESTER,
                                        "--publish-root", str(target), "--status", "policy", "--date", "2026-09-26"])
    assert rrb.cmd_promote(ns) == 0
    record = camp.read_json(fx["run_dir"] / "promote_record.json")
    assert record["status"] == "preliminary" and record["contentDigestMatchesStaged"] is True
    assert record["policyStatus"]["status"] == "preliminary" and record["policyStatus"]["blockers"] == []
    assert record["policyStatus"]["sha256"] == camp.load_promotion_policy()["meta"]["sha256"]
    assert record["confirmedBy"] == TESTER
    pdir = target / "assessments" / fx["slug"] / f"v{record['publishedVersion']}"
    assert camp.read_json(pdir / camp.META_FILE)["author"] == TESTER
    assert dec.PENDING_SUFFIX not in json.dumps(camp.read_json(pdir / camp.PROVENANCE_FILE))
    status = camp.read_json(target / "assessments" / fx["slug"] / "status.json")
    assert status["history"][-1]["status"] == "preliminary"
    # with a blocker the same promote lands as a draft and names the blocker
    fresh = make_staged_region(fx["root"], "55", "Eastern Corn Belt Plains", expect=digest_for("55"),
                               decisions_root=fx["decisions_root"], n_boot=200)
    ns = rrb.build_parser().parse_args(["promote", "--out", str(fresh["run_dir"]), "--maintainer", TESTER,
                                        "--publish-root", str(target), "--status", "policy", "--date", "2026-09-26"])
    assert rrb.cmd_promote(ns) == 0
    record = camp.read_json(fresh["run_dir"] / "promote_record.json")
    assert record["status"] == "draft" and record["policyStatus"]["status"] == "draft"
    assert any(b.startswith("frozen-record: n-boot 200") for b in record["policyStatus"]["blockers"])
    # an explicit status carries no policy block
    fresh = make_staged_region(fx["root"], "55", "Eastern Corn Belt Plains", expect=digest_for("55"),
                               decisions_root=fx["decisions_root"])
    ns = rrb.build_parser().parse_args(["promote", "--out", str(fresh["run_dir"]), "--maintainer", TESTER,
                                        "--publish-root", str(target), "--status", "draft", "--date", "2026-09-26"])
    assert rrb.cmd_promote(ns) == 0
    assert camp.read_json(fresh["run_dir"] / "promote_record.json")["policyStatus"] is None


# --------------------------------------------------------------------------- #
# compare and package
# --------------------------------------------------------------------------- #
def _published_library(tmp_path, code, name, slug) -> Path:
    """A library root with one published version of the region: one metric fewer than the
    staged one, an owner decision (REF-15) and a documented gap on its record."""
    library = tmp_path / "library"
    vdir = library / "assessments" / slug / "v3"
    vdir.mkdir(parents=True)
    bundle = make_bundle(code, name, slug)
    bundle["metricsByFunction"][0]["metrics"] = bundle["metricsByFunction"][0]["metrics"][:1]
    bundle["contentDigest"] = lib.content_digest(bundle)
    ref15 = {"rule_id": "REF-15", "subject": "cd-abc", "subject_kind": "decision",
             "computed": {"metric": "phab_XCDENMID", "action": "remove", "functions": []},
             "reviewer": TESTER, "reviewed_at": "2026-09-01", "reviewer_rationale": WHY}
    doc = make_provenance(tmp_path, code, name, expect=digest_for(code), n_boot=1000,
                          decisions_root=tmp_path / "decisions", pending=False, records=[ref15, pending_record()])
    camp.write_json(vdir / camp.BUNDLE_FILE, bundle)
    camp.write_json(vdir / camp.PROVENANCE_FILE, doc)
    camp.write_json(vdir / camp.META_FILE, {"assessmentId": slug, "version": 3, "contentDigest": bundle["contentDigest"]})
    (vdir / camp.SESSION_FILE).write_text(sio.dumps_session(sio.dump_session_fields(
        {"region_of_applicability": {"kind": "ecoregion", "code": code, "name": name}}, session_name=name)),
        encoding="utf-8", newline="\n")
    camp.write_json(library / "assessments" / slug / "manifest.json",
                    {"schemaVersion": 2, "assessmentId": slug, "assessmentName": name,
                     "region": {"kind": "ecoregion", "code": code, "name": name}, "latestVersion": 3,
                     "versions": [{"version": 3, "contentDigest": bundle["contentDigest"]}]})
    return library


def test_compare_and_package_on_the_synthetic_root(runner, tmp_path, capsys):
    fx = standard_root(tmp_path)
    root = fx["root"]
    library = _published_library(tmp_path, "55", "Eastern Corn Belt Plains", fx["slug"])
    fx["manifest"]["regions"].append(region_row("65", "Northern Lakes and Forests", order=2))
    camp.write_json(root / camp.MANIFEST_FILE, fx["manifest"])
    assert runner.main(["compare", "--root", str(root), "--library", str(library)]) == 0
    out = capsys.readouterr().out
    assert f"L3-55 {fx['slug']} v3 vs staged" in out and "owner decisions" in out
    report = camp.read_json(root / camp.COMPARE_DIR / f"{fx['slug']}.json")
    assert report["schema"] == camp.COMPARE_SCHEMA and report["published"]["version"] == 3
    # five BLOCKS curves and the seventeen synthetic ones (the standard region scores all 20)
    assert report["report"]["curves"]["only_b"] == ["spring-phab-xcdenmid"] and len(report["report"]["curves"]["identical"]) == 22
    assert report["inputs"]["published/assessment.deep.json"] == camp.sha256_file(
        library / "assessments" / fx["slug"] / "v3" / camp.BUNDLE_FILE)
    diff = camp.read_json(root / camp.COMPARE_DIR / f"{fx['slug']}.owner_decision_diff.json")
    verdicts = {(it["rule"], it["subject"]): it["verdict"] for it in diff["items"]}
    assert verdicts[("REF-15", "cd-abc")] == "differ"          # removed there, scored here
    assert diff["counts"]["differ"] >= 1
    md = (root / camp.COMPARE_DIR / f"{fx['slug']}.owner_decision_diff.md").read_text(encoding="utf-8")
    assert "REF-15" in md and "Differs" in md and chr(0x2014) not in md
    summary = (root / camp.COMPARE_DIR / "summary.csv").read_text(encoding="utf-8").splitlines()
    assert summary[0].startswith("l3,name,assessmentId,publishedVersion,stagedVersion")
    assert summary[1].startswith(f"55,Eastern Corn Belt Plains,{fx['slug']},3,v1,") and "false" in summary[1]
    assert summary[2].startswith("65,Northern Lakes and Forests,,,,") and summary[2].endswith(",not staged")
    # the package
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "PROGRESS.md").write_text("progress\n", encoding="utf-8")
    assert runner.main(["package", "--root", str(root), "--out", str(tmp_path / "packages"), "--notes", str(notes)]) == 0
    zip_path = tmp_path / "packages" / f"{fx['manifest']['identity']['campaignId']}.zip"
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        inside = json.loads(zf.read("package.json").decode("utf-8"))
    assert {"manifest.json", "promotion_policy.yaml", "standing_decisions.yaml", "commands.md",
            "runs/l3-55/review_packet.json", "runs/l3-55/stage_complete.json", "runs/l3-55/evidence.json",
            f"compare/{fx['slug']}.json", "compare/summary.csv", "notes/PROGRESS.md", "package.json"} <= names
    assert not any(n.startswith("runs/l3-65") for n in names)
    assert inside["downloads"] == [{"l3": "55", "packageId": "deep-dev-l3-55", "version": "abc123",
                                    "packageDigest": inside["downloads"][0]["packageDigest"],
                                    "archive": "deep-dev-l3-55.evidence.zip", "sha256": "3" * 64, "bytes": 10,
                                    "where": inside["downloads"][0]["where"]}]
    assert runner.main(["package", "--root", str(root), "--out", str(tmp_path / "p2"), "--notes", str(tmp_path / "nope")]) == 2
