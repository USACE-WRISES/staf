"""The worker: runs the queue (or one explicit job) with the heartbeat, the
control file, and the checkpoints. Also the command line for headless runs.

    python -m builder.worker national
    python -m builder.worker chunk --kind huc8 --value 02080204
    python -m builder.worker chunk --kind state --value VA --stages streamcat geometry
    python -m builder.worker queue                # drain state/queue.json
    python -m builder.worker tiles --vpu 02
    python -m builder.worker stage                # coverage + manifest into staging/
    python -m builder.worker publish
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import traceback
from typing import Optional

from . import pipeline
from .paths import DataRoot
from .state import (CancelRequested, Control, PauseRequested, Progress, Queue, Rates,
                    UnitStates, now_iso)
from .units import Chunk, make_chunk


def _chunk_for(root: DataRoot, kind: str, value: str) -> Chunk:
    chunk_id = f"{kind}-{value.upper().replace(' ', '_')}"
    return Chunk.load(root, chunk_id) or make_chunk(root, kind, value)


def job_from_unit(unit: str) -> Optional[dict]:
    """The job a heartbeat unit belongs to (``state-VA`` -> the state chunk),
    for putting a hard-stopped job back in the queue."""
    if not unit:
        return None
    if unit == "national":
        return {"job": "national"}
    if unit == "staging":
        return {"job": "stage"}
    if unit == "publish":
        return {"job": "publish"}
    if unit.startswith("vpu-"):
        return {"job": "tiles", "vpu": unit[4:], "force": True}
    kind, _, value = unit.partition("-")
    if kind in ("huc8", "huc4", "state", "vpu") and value:
        return {"job": "chunk", "kind": kind, "value": value}
    return None


def hard_stop(root: DataRoot) -> dict:
    """Kill the worker now, put its current job back in front of the queue and
    mark the heartbeat paused. Safe by design: every finished request is on
    disk; only the request in flight repeats on Resume."""
    import json
    from .paths import atomic_write_text
    from .state import pid_alive, read_progress
    progress = read_progress(root)
    pid = progress.get("pid")
    killed = False
    if pid_alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(int(pid)), "/F", "/T"],
                           capture_output=True, text=True, timeout=30)
        else:
            os.kill(int(pid), signal.SIGTERM)
        killed = True
    job = job_from_unit(progress.get("unit") or "")
    queue = Queue(root)
    if job is not None:
        queue.write([job] + [j for j in queue.read() if j != job])
    Control(root).clear()
    progress.update({"message": "paused", "unit": "", "stage": "", "job": "",
                     "done": 0, "total": 0, "updated": now_iso()})
    atomic_write_text(root.state / "progress.json", json.dumps(progress))
    return {"killed": killed, "pid": pid, "requeued": job}


def job_label(job: dict) -> str:
    kind = job.get("job")
    if kind == "chunk":
        return f"Chunk {job.get('kind', '')} {job.get('value', '')}".strip()
    if kind == "tiles":
        return f"Tiles {job.get('vpu', '')}".strip()
    return {"national": "National pulls", "stage": "Staging", "publish": "Publish"}.get(kind, str(kind))


def run_job(root: DataRoot, job: dict, states: UnitStates, progress: Progress,
            control: Control) -> None:
    kind = job.get("job")
    started = time.monotonic()
    progress.job = job_label(job)
    if kind == "national":
        from .stages import national
        national.run_national(root, states, progress, control, steps=job.get("steps") or None)
    elif kind == "chunk":
        chunk = _chunk_for(root, job["kind"], job["value"])
        pipeline.run_chunk(root, chunk, states, progress, control,
                           stages=job.get("stages") or None, huc8s=job.get("huc8s") or None,
                           force=bool(job.get("force")), keep_dem_windows=bool(job.get("keep_dem_windows")))
    elif kind == "tiles":
        from .stages import tiles
        tiles.run_tiles(root, job["vpu"], states, progress, control, force=bool(job.get("force")))
    elif kind == "stage":
        from .stages import coverage
        coverage.run_staging(root, states, progress)
    elif kind == "publish":
        from . import publish
        publish.run_publish(root, progress, dry_run=bool(job.get("dry_run")))
    else:
        raise ValueError(f"unknown job {kind!r}")
    Rates(root).record(f"job:{kind}", 1, time.monotonic() - started)


def drain_queue(root: DataRoot) -> int:
    """Run queued jobs until the queue is empty, paused, or cancelled. Returns
    0 on completion, 2 on pause, 3 on cancel, 1 on failure."""
    states, progress, control = UnitStates(root), Progress(root), Control(root)
    queue = Queue(root)
    control.clear()
    while True:
        job = queue.pop_front()
        if job is None:
            progress.finish("queue empty")
            return 0
        try:
            progress.say(f"job: {job}")
            run_job(root, job, states, progress, control)
        except PauseRequested:
            # back in front for the resume (the stage left its checkpoint behind)
            queue.write([job] + [j for j in queue.read() if j != job])
            progress.finish("paused")
            return 2
        except CancelRequested:
            progress.finish("cancelled")
            return 3
        except Exception:
            progress.say(traceback.format_exc()[-1500:])
            # the job goes back in front with a failure count so a plain resume
            # retries it (every stage resumes from its checkpoints and ledgers)
            failed = {**job, "failures": int(job.get("failures") or 0) + 1}
            queue.write([failed] + [j for j in queue.read() if j != job])
            progress.finish(f"failed: {job}")
            return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EASI national builder worker")
    ap.add_argument("--root", default=None, help="data root (default EASI_NATIONAL_ROOT)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("national")
    c = sub.add_parser("chunk")
    c.add_argument("--kind", required=True, choices=("huc8", "huc4", "state", "vpu"))
    c.add_argument("--value", required=True)
    c.add_argument("--stages", nargs="*", default=None)
    c.add_argument("--huc8s", nargs="*", default=None)
    c.add_argument("--force", action="store_true")
    c.add_argument("--keep-dem-windows", action="store_true",
                   help="also archive each reach's DEM window as a GeoTIFF (1 to 3 MB per reach at 1 m)")
    t = sub.add_parser("tiles")
    t.add_argument("--vpu", required=True)
    t.add_argument("--force", action="store_true")
    sub.add_parser("stage")
    p = sub.add_parser("publish")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("queue")
    args = ap.parse_args(argv)

    from pathlib import Path
    root = DataRoot(Path(args.root)) if args.root else DataRoot.default()
    root.ensure()
    if args.cmd == "queue":
        return drain_queue(root)
    job = {"job": args.cmd}
    if args.cmd == "chunk":
        job.update(kind=args.kind, value=args.value, stages=args.stages, huc8s=args.huc8s,
                   force=args.force, keep_dem_windows=args.keep_dem_windows)
    elif args.cmd == "tiles":
        job.update(vpu=args.vpu, force=args.force)
    elif args.cmd == "publish":
        job.update(dry_run=args.dry_run)
    states, progress, control = UnitStates(root), Progress(root), Control(root)
    control.clear()
    try:
        run_job(root, job, states, progress, control)
    except PauseRequested:
        progress.finish("paused")
        return 2
    except CancelRequested:
        progress.finish("cancelled")
        return 3
    except Exception:
        traceback.print_exc()
        progress.finish("failed")
        return 1
    progress.finish("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
