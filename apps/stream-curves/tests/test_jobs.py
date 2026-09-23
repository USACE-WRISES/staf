"""The campaign job runner: serial, parallel and resumed runs give the same outputs.

Jobs have content-derived ids, run in their own processes with thread caps, write only in
their own folders, and are skipped once completed with the same spec and intact outputs; a
failed job is recorded without stopping the others. EASI refits and many-method evaluations
run on it.
"""
from __future__ import annotations

import json
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
    return d.parent


def test_a_parallel_refit_equals_the_serial_one(tmp_path):
    from streamcurves.easi_method import campaigns, refit
    folder = _members_package(tmp_path)
    members, values, panels = refit.load_members(folder)
    qs = ["woody_wsrp100", "natural_wsrp100", "q_cv_monthly"]
    serial = refit.fit_registry(members, values, panels, quantities=qs)
    rows, summary = campaigns.refit_campaign(folder, tmp_path / "c", members_digest="sha256:test",
                                             workers=3, quantities=qs)
    assert summary["counts"]["completed"] == 3
    norm = lambda rs: json.loads(json.dumps(sorted(rs, key=lambda r: (r["quantity"], r["level"],  # noqa: E731
                                                                       r["stratum"])), default=float))
    assert norm(rows) == norm(serial)
    again, summary = campaigns.refit_campaign(folder, tmp_path / "c", members_digest="sha256:test",
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
