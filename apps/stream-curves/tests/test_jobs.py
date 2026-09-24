"""The campaign job runner: serial, parallel and resumed runs give the same outputs.

Jobs have content-derived ids, run in their own processes with thread caps, write only in
their own folders, and are skipped once completed with the same spec and intact outputs; a
failed job is recorded without stopping the others. EASI refits and many-method evaluations
run on it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from streamcurves import jobs

APP = Path(__file__).resolve().parents[1]
VENDORED_DATA = APP / "streamcurves" / "_vendor" / "easi" / "data"


def _write(spec, out_dir):
    """A python job target (module-level so a worker can import it)."""
    Path(out_dir, "value.txt").write_text(f"{spec['n'] * 2}\n", encoding="utf-8")
    return {"double": spec["n"] * 2, "seed": jobs.seed_for(str(spec["n"]))}


def _cmd_jobs(n=4, fail=None):
    code = ("import os,sys,pathlib; n=int(sys.argv[1]); "
            "pathlib.Path(sys.argv[2],'v.txt').write_text(str(n*n)+'|'+os.environ['OMP_NUM_THREADS']); "
            "sys.exit(3 if n==int(sys.argv[3]) else 0)")
    return [jobs.Job(kind="command", spec={"n": i, "fail": fail},
                     argv=[sys.executable, "-c", code, str(i), "{out}", str(fail if fail is not None else -1)],
                     label=f"job {i}") for i in range(n)]


def test_ids_come_from_the_spec_and_seeds_from_the_id():
    a, b = jobs.Job(kind="command", spec={"x": 1}), jobs.Job(kind="command", spec={"x": 1}, label="other")
    assert a.id == b.id and jobs.Job(kind="command", spec={"x": 2}).id != a.id
    assert jobs.seed_for(a.id) == jobs.seed_for(a.id) != jobs.seed_for(a.id, "boot")


def test_serial_parallel_and_resumed_runs_give_the_same_outputs(tmp_path):
    serial = jobs.run(_cmd_jobs(), tmp_path / "serial", workers=1)
    parallel = jobs.run(_cmd_jobs(), tmp_path / "parallel", workers=4)
    assert serial["counts"]["completed"] == parallel["counts"]["completed"] == 4
    first = jobs.run(_cmd_jobs(), tmp_path / "resumed", workers=2, max_starts=2)
    assert first["counts"]["completed"] == 2 and first["counts"]["not-started"] == 2
    again = jobs.run(_cmd_jobs(), tmp_path / "resumed", workers=2)
    assert again["counts"] == {"completed": 2, "skipped": 2, "failed": 0, "not-started": 0}
    for job in _cmd_jobs():
        outs = [jobs.completed(tmp_path / c, job)["outputs"] for c in ("serial", "parallel", "resumed")]
        assert outs[0] == outs[1] == outs[2]
        assert (tmp_path / "parallel" / "jobs" / job.id / "out" / "v.txt").read_text() == \
            f"{job.spec['n'] ** 2}|1"                                  # thread caps reach the worker
    events = [json.loads(line) for line in (tmp_path / "resumed" / "index.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events].count("skipped") == 2


def test_a_failure_is_recorded_and_the_rest_finish(tmp_path):
    summary = jobs.run(_cmd_jobs(fail=1), tmp_path / "c", workers=2)
    assert summary["counts"]["failed"] == 1 and summary["counts"]["completed"] == 3
    bad = next(j for j in _cmd_jobs(fail=1) if j.spec["n"] == 1)
    assert (tmp_path / "c" / "jobs" / bad.id / "failed.json").is_file()
    assert jobs.completed(tmp_path / "c", bad) is None


def test_a_tampered_output_reruns_the_job(tmp_path):
    js = _cmd_jobs(2)
    jobs.run(js, tmp_path / "c", workers=1)
    (tmp_path / "c" / "jobs" / js[0].id / "out" / "v.txt").write_text("tampered")
    again = jobs.run(js, tmp_path / "c", workers=1)
    assert again["counts"] == {"completed": 1, "skipped": 1, "failed": 0, "not-started": 0}


def test_python_jobs_run_in_their_own_interpreter(tmp_path):
    js = [jobs.Job(kind="python", target="test_jobs:_write", spec={"n": n}, label=str(n),
                   env={"PYTHONPATH": str(Path(__file__).parent)}) for n in (2, 5)]
    summary = jobs.run(js, tmp_path / "c", workers=2)
    assert summary["counts"]["completed"] == 2
    rec = jobs.completed(tmp_path / "c", js[1])
    assert rec["result"]["double"] == 10


# --------------------------------------------------------------------------- #
# EASI on the runner
# --------------------------------------------------------------------------- #
def _members_package(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    rng = np.random.default_rng(5)
    rows, comid = [], 1
    for level, stratum in (("nars9", "nars9:CPL"), ("nars9", "nars9:NAP"), ("national", "national:national")):
        for _ in range(45):
            rows.append({"comid": comid, "huc12": f"h{comid}", "level": level, "stratum": stratum,
                         "slope_class": "ge_2", "fcode_class": "stream", "screen": "strict",
                         "panel_tier": "exploratory"})
            comid += 1
    d = tmp_path / "members" / "data"
    d.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), d / "panel_members.parquet")
    n = comid - 1
    pq.write_table(pa.table({"comid": list(range(1, n + 1)),
                             "woody_wsrp100": rng.uniform(30, 100, n).tolist(),
                             "natural_wsrp100": rng.uniform(40, 100, n).tolist(),
                             "q_cv_monthly": rng.uniform(0.2, 1.5, n).tolist(),
                             "composite_pressure": rng.uniform(0, 1, n).tolist()}), d / "member_values.parquet")
    panels = [{"level": lv, "stratum": st, "panel_tier": "exploratory", "screen": "strict"}
              for lv, st in {(r["level"], r["stratum"]) for r in rows}]
    pq.write_table(pa.Table.from_pylist(panels), d / "reference_panels.parquet")
    from streamcurves import evidence_store as es
    files = {f"data/{f.name}": {"bytes": f.stat().st_size, "sha256": es.sha_file(f)}
             for f in sorted(d.iterdir())}
    (d.parent / "evidence.json").write_text(json.dumps({
        "schema": es.SCHEMA, "schemaVersion": 1, "packageId": "easi-dev-members", "version": "test",
        "dataDigest": es.data_digest(files), "files": files}), encoding="utf-8")
    return d.parent


def test_a_parallel_refit_equals_the_serial_one(tmp_path):
    from streamcurves.easi_method import campaigns, refit
    folder = _members_package(tmp_path)
    members, values, panels = refit.load_members(folder)
    qs = ["woody_wsrp100", "natural_wsrp100", "q_cv_monthly"]
    serial = refit.fit_registry(members, values, panels, quantities=qs)
    from streamcurves import evidence_store as es
    digest = es.read_manifest(folder)["dataDigest"]
    with pytest.raises(ValueError, match="does not verify"):
        campaigns.refit_jobs(folder, members_digest="sha256:" + "0" * 64)
    rows, summary = campaigns.refit_campaign(folder, tmp_path / "c", members_digest=digest,
                                             workers=3, quantities=qs)
    assert summary["counts"]["completed"] == 3
    norm = lambda rs: json.loads(json.dumps(sorted(rs, key=lambda r: (r["quantity"], r["level"],  # noqa: E731
                                                                       r["stratum"])), default=float))
    assert norm(rows) == norm(serial)
    again, summary = campaigns.refit_campaign(folder, tmp_path / "c", members_digest=digest,
                                              workers=3, quantities=qs)
    assert summary["counts"]["skipped"] == 3 and norm(again) == norm(serial)


def test_many_methods_are_scored_at_once_each_in_its_own_process(tmp_path):
    from streamcurves.easi_method import campaigns, edit, io as eio
    project = eio.import_from_easi(VENDORED_DATA, imported_by="t", cases={"cases": []})
    draft = edit.set_band_edge(eio.fork(project, by="t"), "road-density-inflow-pressure", None, 0, 1.5,
                               by="t", reason="experimental test edit")
    if eio.easi_source(APP.parent.parent) is None:
        pytest.skip("apps/easi is not present (the preview cases come from it)")
    pkgs = {}
    for label, p in (("release", project), ("draft", draft)):
        pkgs[label] = tmp_path / f"{label}.zip"
        eio.export_zip(p, pkgs[label])
    cases_path = tmp_path / "cases.json"
    full = eio.export_cases(APP.parent / "easi")
    cases_path.write_text(json.dumps({"cases": full["cases"][:40]}), encoding="utf-8")
    results, summary = campaigns.evaluation_campaign(pkgs, cases_path, tmp_path / "c", workers=2)
    assert summary["counts"]["completed"] == 2
    assert results["release"]["identity"]["methodVersion"] == "b2e3033116e3"
    assert results["draft"]["identity"]["packageDigest"] == draft.package_digest
    assert set(results["release"]["results"]) == set(results["draft"]["results"])
    again, summary = campaigns.evaluation_campaign(pkgs, cases_path, tmp_path / "c", workers=2)
    assert summary["counts"]["skipped"] == 2 and again == results


def test_a_failed_job_is_retried_before_it_is_recorded(tmp_path):
    summary = jobs.run(_cmd_jobs(3, fail=2), tmp_path / "c", workers=1, retries=1)
    events = [json.loads(x) for x in (tmp_path / "c" / "index.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events].count("retrying") == 1
    assert summary["counts"]["failed"] == 1


# --------------------------------------------------------------------------- #
# stage-many: a region's stage is bound to everything it depends on
# --------------------------------------------------------------------------- #
def _batch_module():
    import importlib.util
    scripts = APP / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location("run_region_batch_under_test",
                                                      scripts / "run_region_batch.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(scripts))


def test_stage_inputs_move_with_flags_and_code_but_not_with_time():
    rb = _batch_module()
    base = {k: None for k in rb._STAGE_MANY_FLAGS}
    base.update(n_boot=50, enable_policy=[], approve_portfolio=[], nrsa_dataset="multi-cycle-v1")
    a = rb.region_inputs(dict(base))
    assert rb.region_inputs(dict(base)) == a                          # stable
    assert rb.region_inputs({**base, "n_boot": 51}) != a              # a flag moves it
    assert a["code"] == rb.code_fingerprint() and len(a["code"]) == 64
    assert set(a) >= {"flags", "methodology", "policy", "nrsaManifest", "code", "stationScreen"}


def test_an_exploration_cell_writes_register_candidates(tmp_path):
    from streamcurves import explore
    folder = _members_package(tmp_path)
    out = tmp_path / "cell"
    res = explore.easi_cell({"campaign": "t", "quantity": "woody_wsrp100", "level": "nars9",
                             "variant": "as-built", "members": str(folder)}, out)
    assert res["candidates"] == 2
    cands = [json.loads(x) for x in (out / "candidates.jsonl").read_text().splitlines()]
    assert {c["purpose"] for c in cands} == {"exploration"}
    assert all(c["candidateKey"].startswith("cand-") and c["basisDigest"].startswith("sha256:")
               for c in cands)
    assert (out / "grid.csv").read_text().splitlines()[0].startswith("assessmentType,subject,level")


def test_a_job_is_everything_that_runs_it():
    base = dict(kind="python", spec={"x": 1}, target="m:f", env={"A": "1"})
    ids = {jobs.Job(**base).id, jobs.Job(**{**base, "env": {"A": "2"}}).id,
           jobs.Job(**{**base, "target": "m:g"}).id, jobs.Job(**{**base, "cwd": "elsewhere"}).id}
    assert len(ids) == 4


def test_one_coordinator_holds_a_campaign(tmp_path):
    held = jobs.acquire(tmp_path)
    with pytest.raises(jobs.CampaignBusy, match="Another run is using"):
        jobs.run(_cmd_jobs(1), tmp_path, workers=1)
    jobs.release(held)
    # a lock whose process is gone is taken over
    import socket
    (tmp_path / "lock.json").write_text(json.dumps({"pid": 2 ** 31 - 7, "host": socket.gethostname(),
                                                    "startedAt": "2026-09-23T00:00:00Z"}),
                                        encoding="utf-8")
    summary = jobs.run(_cmd_jobs(1), tmp_path, workers=1)
    assert summary["counts"]["completed"] == 1 and not (tmp_path / "lock.json").exists()


def test_relaxed_first_panels_are_labelled_relaxed_and_ranked_like_the_builder():
    import pandas as pd
    from streamcurves import explore
    from streamcurves.easi_method import fit_recipe as fr
    rng = np.random.default_rng(11)
    n = 1200
    frame = pd.DataFrame({"comid": np.arange(1, n + 1), "huc12": [f"h{i}" for i in range(n)],
                          "l3": "50", "l2": "5.2", "l1": "5", "nars9": "NAP",
                          "fcode_class": "stream", "wadeable": True,
                          # every reach passes the relaxed screen and fails the strict one
                          "pctimp2019ws": 2.0, "agriculture_ws": rng.uniform(0, 9, n), "rddensws": 1.0,
                          "dor": 1.0, "sc__nabd_densws": 0.0, "sc__npdesdensws": 0.0, "mines_ws": 0.0,
                          "corridor_conversion_wsrp100": rng.uniform(0, 5, n)})
    as_built = explore.draw_panels(frame, "as-built")
    first = explore.draw_panels(frame, "relaxed-first")
    for level in fr.LEVELS:
        assert set(as_built[level][0]["panel_tier"]) == {"best_available"}
        panels, members = first[level]
        assert set(panels["screen"]) == {"relaxed"} and set(panels["panel_tier"]) == {"best_available"}
        assert set(members["screen"]) == {"relaxed"} and set(members["panel_tier"]) == {"best_available"}
    # the builder's rule for a relaxed panel: a pressure-driven fit is not usable
    q = fr.QUANTITIES["woody_wsrp100"]
    row = {"status": "complete", "x39": 40.0, "x69": 70.0, "q25": 50.0, "q75": 80.0, "rho_pressure": 0.45}
    assert fr.usable(q, row, "best_available")[0] is False
    per = explore.member_pressure(frame, first)
    assert len(per) == n and per["composite_pressure"].notna().all()


# --------------------------------------------------------------------------- #
# locks (review B round 2, N5): one holder, a race-safe takeover, only one's own release
# --------------------------------------------------------------------------- #
def _holder(folder: Path, *, release: bool) -> subprocess.Popen:
    """Another process holding ``folder``'s lock until told to go (then releasing it, or
    ending without releasing it)."""
    code = ("import os, sys, pathlib; sys.path.insert(0, sys.argv[2]); "
            "from streamcurves import jobs; h = jobs.acquire(pathlib.Path(sys.argv[1])); "
            "print('held', flush=True); sys.stdin.readline(); "
            "jobs.release(h) if sys.argv[3] == '1' else os._exit(0)")
    proc = subprocess.Popen([sys.executable, "-c", code, str(folder), str(APP), "1" if release else "0"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


def _locks(folder: Path) -> list:
    return sorted(p.name for p in folder.iterdir() if p.name.startswith("lock"))


def test_a_second_process_finds_the_lock_busy_until_the_first_is_done(tmp_path):
    proc = _holder(tmp_path, release=True)
    try:
        with pytest.raises(jobs.CampaignBusy, match="Another run is using"):
            jobs.acquire(tmp_path)
        with pytest.raises(jobs.CampaignBusy):
            jobs.run(_cmd_jobs(1), tmp_path, workers=1)
    finally:
        proc.communicate("go\n", timeout=120)
    jobs.release(jobs.acquire(tmp_path))
    # a holder that ends without releasing leaves a lock that is taken over, and nothing else
    proc = _holder(tmp_path, release=False)
    proc.communicate("go\n", timeout=120)
    assert _locks(tmp_path) == ["lock.json"]
    held = jobs.acquire(tmp_path)
    assert jobs.holds(held) and _locks(tmp_path) == ["lock.json"]
    jobs.release(held)
    assert _locks(tmp_path) == []


def test_a_takeover_that_loses_the_race_gives_the_lock_back(tmp_path):
    lock = tmp_path / "lock.json"
    stale = {"pid": 2 ** 31 - 7, "host": socket.gethostname(), "startedAt": "2026-09-23T00:00:00Z",
             "nonce": "old"}
    lock.write_text(json.dumps(stale), encoding="utf-8")
    # A and B both judged that record stale. A moves it aside first and takes the lock ...
    assert jobs._take_over(lock, stale)
    a = jobs.acquire(tmp_path)
    # ... then B's takeover finds A's fresh lock under the name: it puts it back and loses
    assert not jobs._take_over(lock, stale)
    assert jobs.holds(a) and _locks(tmp_path) == ["lock.json"]
    jobs.release(a)
    assert _locks(tmp_path) == []


def test_a_release_never_removes_a_lock_another_run_holds(tmp_path):
    held = jobs.acquire(tmp_path)
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({**json.loads(lock.read_text(encoding="utf-8")), "nonce": "another"}),
                    encoding="utf-8")
    jobs.release(held)
    assert lock.is_file() and not jobs.holds(held)


def test_a_lock_that_cannot_be_read_is_busy_and_kept(tmp_path):
    lock = tmp_path / "lock.json"
    for text in ("", "{", json.dumps({"host": "x", "startedAt": "y"}), json.dumps({"pid": "12"}),
                 json.dumps({"pid": True}), json.dumps([1])):
        lock.write_text(text, encoding="utf-8")
        with pytest.raises(jobs.CampaignBusy, match="cannot be read"):
            jobs.acquire(tmp_path)
        assert lock.read_text(encoding="utf-8") == text


def test_a_pid_is_the_holder_only_while_its_process_is_the_one_that_locked(tmp_path):
    lock = tmp_path / "lock.json"
    me = {"pid": os.getpid(), "host": socket.gethostname(), "startedAt": "x", "nonce": "n"}
    started = jobs._process_started(os.getpid())
    if started is None:
        pytest.skip("process start times cannot be read here")
    lock.write_text(json.dumps({**me, "processStarted": started + 1}), encoding="utf-8")
    held = jobs.acquire(tmp_path)                      # the pid now belongs to another process
    assert jobs.holds(held)
    jobs.release(held)
    lock.write_text(json.dumps({**me, "processStarted": started}), encoding="utf-8")
    with pytest.raises(jobs.CampaignBusy, match="Another run is using"):
        jobs.acquire(tmp_path)                         # that process is still running
    lock.write_text(json.dumps({**me, "pid": 2 ** 31 - 7, "host": "another-computer"}), encoding="utf-8")
    with pytest.raises(jobs.CampaignBusy, match="another-computer"):
        jobs.acquire(tmp_path)                         # another computer's lock is never judged


def test_a_run_inside_a_held_lock_keeps_it_for_the_merge(tmp_path):
    with jobs.lock(tmp_path) as held:
        summary = jobs.run(_cmd_jobs(2), tmp_path, workers=2, lock_held=held)
        assert summary["counts"]["completed"] == 2 and jobs.holds(held)
        with pytest.raises(ValueError, match="not this campaign"):
            jobs.run(_cmd_jobs(1), tmp_path / "other", workers=1, lock_held=held)
    assert _locks(tmp_path) == []
    held = jobs.acquire(tmp_path)
    (tmp_path / "lock.json").write_text(json.dumps({"pid": os.getpid(), "nonce": "x"}), encoding="utf-8")
    with pytest.raises(jobs.CampaignBusy, match="no longer"):
        jobs.run(_cmd_jobs(1), tmp_path, workers=1, lock_held=held)


# --------------------------------------------------------------------------- #
# stage-many resumes by region, and the stage holds its folder (N5, N11)
# --------------------------------------------------------------------------- #
def _many_args(rb, out_root: Path, codes: dict) -> argparse.Namespace:
    base = {k: None for k in rb._STAGE_MANY_FLAGS}
    base.update(maintainer="Rehearsal (not an owner decision)", workers=2, isolated=False,
                out_root=str(out_root), l3=list(codes), name=[f"{c}={n}" for c, n in codes.items()])
    return argparse.Namespace(**base)


def test_stage_many_skips_a_region_staged_from_the_same_inputs_whatever_the_list(tmp_path, monkeypatch):
    rb = _batch_module()
    monkeypatch.setattr(rb, "region_inputs", lambda args: {"fixed": 1})
    monkeypatch.setattr(rb, "carried_from", lambda code: None)
    seen = []

    def fake_run(js, campaign, **kw):
        seen.append(([j.label for j in js], Path(kw["lock_held"].path)))
        return {"jobs": {j.id: {"state": "completed", "seconds": 1.0} for j in js}}

    monkeypatch.setattr(jobs, "run", fake_run)
    out_root = tmp_path / "many"
    staged = out_root / "l3-55-region-a"
    staged.mkdir(parents=True)
    (staged / "review_packet.json").write_text("{}", encoding="utf-8")
    rec = {"inputsDigest": rb.region_digest("55", "Region A", {"fixed": 1}, None),
           "outputs": {"review_packet.json": hashlib.sha256(b"{}").hexdigest()}}
    (staged / rb.STAGE_COMPLETE).write_text(json.dumps(rec), encoding="utf-8")

    assert rb.cmd_stage_many(_many_args(rb, out_root, {"55": "Region A", "65": "Region B"})) == 0
    assert seen[-1] == (["L3-65 Region B"], out_root / ".campaign" / "lock.json")
    rows = {r["l3"]: r for r in json.loads((out_root / "batch_summary.json").read_text(encoding="utf-8"))["regions"]}
    assert rows["55"]["exit"] == 0 and "already staged" in rows["55"]["error"]
    # another region list: the finished region is still not staged again
    rb.cmd_stage_many(_many_args(rb, out_root, {"55": "Region A", "65": "Region B", "71": "Region C"}))
    assert seen[-1][0] == ["L3-65 Region B", "L3-71 Region C"]
    # a changed output stages it again
    (staged / "review_packet.json").write_text('{"x": 1}', encoding="utf-8")
    rb.cmd_stage_many(_many_args(rb, out_root, {"55": "Region A"}))
    assert seen[-1][0] == ["L3-55 Region A"]
    assert _locks(out_root / ".campaign") == []
    # a second batch on the same folder is refused while one runs
    held = jobs.acquire(out_root / ".campaign")
    try:
        assert rb.cmd_stage_many(_many_args(rb, out_root, {"55": "Region A"})) == rb.BUSY_EXIT
    finally:
        jobs.release(held)


def test_the_recorded_command_drops_every_spelling_of_the_scheduling_flags():
    rb = _batch_module()
    argv = ["stage-many", "--l3", "55", "--w", "3", "--wor=2", "--workers", "4", "--i", "--iso",
            "--isolated", "--out-root", "x", "--name", "55=W", "--workers=5", "--wo", "6"]
    assert rb.recorded_argv(argv) == ["stage-many", "--l3", "55", "--out-root", "x", "--name", "55=W"]


def test_a_stage_holds_its_folders_lock_and_a_busy_one_says_so(tmp_path, monkeypatch):
    from streamcurves import region_build
    rb = _batch_module()
    region = tmp_path / "region"
    during = []
    monkeypatch.setattr(rb, "cmd_stage", lambda a: (during.append(_locks(region)), 0)[1])
    ns = argparse.Namespace(out=str(region))
    assert rb.stage_locked(ns) == 0 and during == [["lock.json"]] and _locks(region) == []
    held = jobs.acquire(region)
    try:
        assert rb.stage_locked(ns) == rb.BUSY_EXIT and len(during) == 1
    finally:
        jobs.release(held)
    assert "Another run is staging this region" in region_build.exit_meaning(rb.BUSY_EXIT)


def test_a_stage_job_holds_the_region_lock_and_never_records_it(tmp_path, monkeypatch):
    rb = _batch_module()
    monkeypatch.setattr(rb, "region_inputs", lambda args: {"fixed": 1})
    monkeypatch.setattr(rb, "carried_from", lambda code: None)
    monkeypatch.setattr(rb, "code_fingerprint", lambda: "code")
    region = tmp_path / "l3-55"

    def fake_stage(ns):
        assert _locks(region) == ["lock.json"]
        (region / "review_packet.json").write_text("{}", encoding="utf-8")
        return 0

    monkeypatch.setattr(rb, "cmd_stage", fake_stage)
    spec = {"inputsDigest": rb.region_digest("55", "Region A", {"fixed": 1}, None),
            "args": {"l3": "55", "name": "Region A", "out": str(region)}}
    assert rb.stage_job(spec, tmp_path / "job-out")["exit"] == 0
    rec = json.loads((region / rb.STAGE_COMPLETE).read_text(encoding="utf-8"))
    assert list(rec["outputs"]) == ["review_packet.json"] and _locks(region) == []
    held = jobs.acquire(region)
    try:
        with pytest.raises(SystemExit, match="Another run is using"):
            rb.stage_job(spec, tmp_path / "job-out")
    finally:
        jobs.release(held)
