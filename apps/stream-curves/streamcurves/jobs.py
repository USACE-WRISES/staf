"""A bounded job runner for campaigns: many independent jobs, a few at a time, resumable.

A job is a spec (anything JSON can hold) of a kind; its id is the SHA-256 of the canonical
``{kind, spec}``, so the same work always has the same id and a job whose completion record
names that id is never run again. Every job runs in its own process, with the thread caps
of ``THREAD_CAPS`` and the environment it names, and writes only inside its own folder. The
coordinator (the process calling ``run``) is the only writer of the campaign's index and
summary. A failure is recorded and never stops the other jobs; an interrupted campaign resumes
by running ``run`` again: completed jobs are skipped, anything else starts over in a clean
output folder.

Layout under a campaign folder::

    campaign.json                    what the campaign is (written once)
    index.jsonl                      one line per job event, append-only
    summary.json                     every job's last state, rewritten as jobs finish
    jobs/<id>/job.json               the job's kind and spec
    jobs/<id>/out/                   what the job writes (cleaned before every start)
    jobs/<id>/log.txt                the job's output
    jobs/<id>/complete.json          written atomically when it succeeded: result and the
                                     SHA-256 of every file under out/
    jobs/<id>/failed.json            the last failure

Two kinds: ``command`` (an argv; ``{out}`` and ``{job}`` are replaced by the job's output and
job folders) and ``python`` (``module:function`` called in a fresh interpreter as
``fn(spec, out_dir) -> dict``; the dict is the job's result).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

THREAD_CAPS = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
               "NUMEXPR_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1"}
APP_ROOT = Path(__file__).resolve().parents[1]


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=str).encode("utf-8")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tree_digest(folder: Path) -> dict:
    """SHA-256 of every file under ``folder``, by relative path."""
    folder = Path(folder)
    if not folder.is_dir():
        return {}
    return {str(p.relative_to(folder)).replace("\\", "/"): sha_file(p)
            for p in sorted(folder.rglob("*")) if p.is_file()}


def seed_for(job_id: str, stream: str = "default") -> int:
    """A seed that belongs to a job (and a named stream within it), never to its position in
    a queue or to the worker that runs it."""
    return int(hashlib.sha256(f"{job_id}|{stream}".encode("utf-8")).hexdigest()[:8], 16)


@dataclass
class Job:
    kind: str                                   # "command" | "python"
    spec: dict
    argv: Optional[list] = None                 # command jobs
    target: Optional[str] = None                # python jobs: "module:function"
    env: dict = field(default_factory=dict)     # added to the worker's environment
    cwd: Optional[str] = None
    label: str = ""
    timeout: Optional[float] = None

    @property
    def id(self) -> str:
        return hashlib.sha256(canonical({"kind": self.kind, "spec": self.spec})).hexdigest()[:16]

    def describe(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label, "spec": self.spec,
                "argv": self.argv, "target": self.target, "env": self.env}


def _write_atomic(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def completed(campaign: Path, job: Job) -> Optional[dict]:
    """The job's completion record when it names this job and its outputs are intact."""
    d = Path(campaign) / "jobs" / job.id
    p = d / "complete.json"
    if not p.is_file():
        return None
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if rec.get("id") != job.id or rec.get("spec") != json.loads(canonical(job.spec)):
        return None
    if tree_digest(d / "out") != rec.get("outputs"):
        return None
    return rec


def _worker_env(job: Job, base: Optional[dict]) -> dict:
    env = dict(os.environ if base is None else base)
    env.update(THREAD_CAPS)
    env.update({k: str(v) for k, v in (job.env or {}).items()})
    env["STREAMCURVES_JOB_ID"] = job.id
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _run_one(campaign: Path, job: Job, base_env: Optional[dict]) -> dict:
    d = Path(campaign) / "jobs" / job.id
    out = d / "out"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for stale in ("complete.json", "failed.json", "result.json"):
        (d / stale).unlink(missing_ok=True)
    _write_atomic(d / "job.json", job.describe())
    env = _worker_env(job, base_env)
    if job.kind == "command":
        argv = [str(a).replace("{out}", str(out)).replace("{job}", str(d)) for a in job.argv or []]
    elif job.kind == "python":
        argv = [sys.executable, "-B", "-m", "streamcurves.jobs", "run-python", str(d)]
    else:
        raise ValueError(f"unknown job kind {job.kind!r}")
    t0 = time.perf_counter()
    with (d / "log.txt").open("w", encoding="utf-8") as log:
        proc = subprocess.run(argv, cwd=job.cwd or (str(APP_ROOT) if job.kind == "python" else None),
                              env=env, stdout=log, stderr=subprocess.STDOUT, timeout=job.timeout)
    seconds = round(time.perf_counter() - t0, 2)
    if proc.returncode != 0:
        tail = (d / "log.txt").read_text(encoding="utf-8", errors="replace")[-2000:]
        rec = {"id": job.id, "label": job.label, "exit": proc.returncode, "seconds": seconds,
               "failedAt": _now(), "tail": tail}
        _write_atomic(d / "failed.json", rec)
        return {"state": "failed", **rec}
    result = None
    if (d / "result.json").is_file():
        result = json.loads((d / "result.json").read_text(encoding="utf-8"))
    rec = {"id": job.id, "label": job.label, "spec": json.loads(canonical(job.spec)),
           "outputs": tree_digest(out), "result": result, "seconds": seconds, "finishedAt": _now()}
    _write_atomic(d / "complete.json", rec)
    return {"state": "completed", **rec}


def run(jobs: list[Job], campaign: Path, *, workers: int = 2, meta: Optional[dict] = None,
        base_env: Optional[dict] = None, stop: Optional[threading.Event] = None,
        on_event: Optional[Callable[[dict], None]] = None, max_starts: Optional[int] = None,
        retries: int = 1) -> dict:
    """Run every job not already completed, ``workers`` at a time. Returns the summary.
    ``stop`` (or ``max_starts``) ends the campaign early: no new job starts, running jobs
    finish; a later ``run`` resumes where this one stopped. A job that fails is started
    again up to ``retries`` times (a worker process can die of a transient native fault);
    every attempt is in the index."""
    campaign = Path(campaign)
    (campaign / "jobs").mkdir(parents=True, exist_ok=True)
    ids = [j.id for j in jobs]
    if len(set(ids)) != len(ids):
        raise ValueError("two jobs have the same spec")
    if not (campaign / "campaign.json").is_file():
        _write_atomic(campaign / "campaign.json", {"created": _now(), "meta": meta or {},
                                                   "jobs": len(jobs)})
    index = campaign / "index.jsonl"
    lock = threading.Lock()
    states: dict[str, dict] = {}

    def event(kind: str, job: Job, **extra) -> None:
        rec = {"at": _now(), "event": kind, "id": job.id, "label": job.label, **extra}
        with lock:
            with index.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
            states[job.id] = {"label": job.label, "state": kind, **{k: v for k, v in extra.items()
                                                                    if k in ("seconds", "exit")}}
        if on_event:
            on_event(rec)

    pending = []
    for job in jobs:
        if completed(campaign, job) is not None:
            event("skipped", job, reason="completed with the same spec and outputs")
        else:
            pending.append(job)
    started = 0
    attempts: dict[str, int] = {}
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        running = {}
        queue = list(pending)
        while queue or running:
            while queue and len(running) < max(1, int(workers)):
                if (stop is not None and stop.is_set()) or (max_starts is not None and started >= max_starts):
                    queue = []
                    break
                job = queue.pop(0)
                event("started", job)
                running[pool.submit(_run_one, campaign, job, base_env)] = job
                started += 1
            if not running:
                break
            done, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for fut in done:
                job = running.pop(fut)
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001 - the runner itself failed for this job
                    res = {"state": "failed", "exit": None, "tail": f"{type(exc).__name__}: {exc}"}
                    _write_atomic(Path(campaign) / "jobs" / job.id / "failed.json",
                                  {"id": job.id, "error": res["tail"], "failedAt": _now()})
                attempts[job.id] = attempts.get(job.id, 0) + 1
                if res["state"] == "failed" and attempts[job.id] <= int(retries):
                    event("retrying", job, seconds=res.get("seconds"), exit=res.get("exit"))
                    queue.insert(0, job)
                    continue
                event(res["state"], job, seconds=res.get("seconds"), exit=res.get("exit"))
    for job in jobs:
        states.setdefault(job.id, {"label": job.label, "state": "not-started"})
    summary = {"updated": _now(), "workers": int(workers), "seconds": round(time.perf_counter() - t0, 2),
               "counts": {s: sum(1 for v in states.values() if v["state"] == s)
                          for s in ("completed", "skipped", "failed", "not-started")},
               "jobs": states}
    _write_atomic(campaign / "summary.json", summary)
    return summary


# --------------------------------------------------------------------------- #
# the worker side of a python job
# --------------------------------------------------------------------------- #
def _run_python(job_dir: Path) -> int:
    doc = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    module, _, name = str(doc["target"]).partition(":")
    fn = getattr(importlib.import_module(module), name)
    result = fn(doc["spec"], job_dir / "out")
    _write_atomic(job_dir / "result.json", result if result is not None else {})
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "run-python":
        return _run_python(Path(argv[2]))
    print("usage: python -m streamcurves.jobs run-python <job folder>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))


__all__ = ["THREAD_CAPS", "Job", "run", "completed", "seed_for", "tree_digest", "canonical"]
