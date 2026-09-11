"""Stage ordering and the chunk driver.

Per chunk: streamcat, geometry, huc12, wqp, attains, then per HUC8 derive,
then nas (it needs the HUC12 list), then per HUC8 joins and score. ``run_chunk``
runs whatever is not done yet, in that order, and stops at a pause or cancel.

The per-HUC8 steps are pure computation, so they run in a process pool
(``config.HUC8_WORKERS``); the chunk-level acquisition stages stay in the
worker process, where the pause/cancel polling lives.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import config
from .paths import DataRoot
from .state import CancelRequested, Control, PauseRequested, Progress, UnitStates
from .stages import (attains, derive, geometry, huc12, joins, nas, score,
                     streamcat, xs_derive, xs_sample)
from .units import Chunk

CHUNK_STAGES = ("streamcat", "geometry", "huc12", "wqp", "attains", "nas")
HUC8_STAGES = ("derive", "xs_sample", "xs_derive", "joins", "score")
ORDER = ("streamcat", "geometry", "huc12", "wqp", "attains", "derive", "nas",
         "xs_sample", "xs_derive", "joins", "score")
#: HUC8 stages that run back to back in one pool job (the joins' indexes stay
#: loaded); the cross-section stages run alone so each can be queued by itself
_HUC8_GROUPS = (("derive",), ("xs_sample",), ("xs_derive",), ("joins", "score"))


def _chunk_stage_fn(name: str) -> Callable:
    from .stages import wqp as wqp_stage
    from .stages import wqp_stations
    wqp_fn = wqp_stage.run_wqp if config.WQP_METHOD == "cells" else wqp_stations.run_wqp
    return {"streamcat": streamcat.run_streamcat, "geometry": geometry.run_geometry,
            "huc12": huc12.run_huc12, "wqp": wqp_fn,
            "attains": attains.run_attains, "nas": nas.run_nas}[name]


def _huc8_stage_fn(name: str) -> Callable:
    return {"derive": derive.run_derive, "joins": joins.run_joins, "score": score.run_score,
            "xs_sample": xs_sample.run_xs_sample, "xs_derive": xs_derive.run_xs_derive}[name]


def _stage_kwargs(name: str, keep_dem_windows: bool) -> dict:
    """Per-stage options: only the sampling stage takes the DEM window flag."""
    return {"keep_dem_windows": True} if name == "xs_sample" and keep_dem_windows else {}


def _huc8_job(root_path: str, chunk_id: str, huc8: str, stages: tuple, force: bool,
              keep_dem_windows: bool = False) -> str:
    """One pool job: the given HUC8 stages for one HUC8 (runs in a child process)."""
    root = DataRoot(Path(root_path))
    chunk = Chunk.load(root, chunk_id)
    if chunk is None:
        raise RuntimeError(f"chunk {chunk_id} not found")
    states, control = UnitStates(root), Control(root)
    progress = Progress(root, quiet=True)
    for name in stages:
        control.check()
        _huc8_stage_fn(name)(root, chunk, huc8, states, progress, control, force=force,
                             **_stage_kwargs(name, keep_dem_windows))
    return huc8


def largest_first(root: DataRoot, huc8s: list[str]) -> list[str]:
    """HUC8s by their derived reach count, descending, so a pool starts the
    long ones first (a HUC8 runs serially inside one process)."""
    import pyarrow.parquet as pq

    def size(huc8: str) -> int:
        path = root.huc8_file(huc8, "derived")
        try:
            return int(pq.read_metadata(path).num_rows)
        except Exception:  # noqa: BLE001 - not derived yet: sorts last
            return -1
    return sorted(huc8s, key=lambda h: (-size(h), h))


def _in_flight(root: DataRoot, huc8s) -> str:
    """The sampling heartbeats of the HUC8s in flight, summed ('' when none)."""
    import json
    done = total = 0
    for huc8 in huc8s:
        path = root.huc8_dir(huc8) / "xs_sample.progress.json"
        try:
            j = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        done += int(j.get("done") or 0)
        total += int(j.get("total") or 0)
    return f" ({done:,} of {total:,} reaches)" if total else ""


def _run_huc8_stages(root: DataRoot, chunk: Chunk, huc8s: list[str], stages: tuple,
                     states: UnitStates, progress: Progress, control: Control, *,
                     force: bool, workers: Optional[int] = None, keep_dem_windows: bool = False) -> None:
    label = " + ".join(stages)
    workers = workers or (config.XS_WORKERS if stages[0] in ("xs_sample", "xs_derive") else config.HUC8_WORKERS)
    if stages[0] == "xs_sample":
        huc8s = largest_first(root, huc8s)          # the biggest HUC8s cannot become the tail
    if workers <= 1 or len(huc8s) <= 1:
        for i, huc8 in enumerate(huc8s):
            control.check()
            for name in stages:
                _huc8_stage_fn(name)(root, chunk, huc8, states, progress, control, force=force,
                                     **_stage_kwargs(name, keep_dem_windows))
            progress.tick(done=i + 1, total=len(huc8s), message=f"{label}: {i + 1} of {len(huc8s)} HUC8s")
        return
    progress.begin(chunk.id, stages[0], total=len(huc8s),
                   message=f"{label}: {len(huc8s)} HUC8s on {workers} processes")
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = list(huc8s)
        running: dict = {}
        try:
            while pending or running:
                while pending and len(running) < workers:
                    control.check()
                    huc8 = pending.pop(0)
                    running[pool.submit(_huc8_job, str(root.root), chunk.id, huc8, stages, force,
                                        keep_dem_windows)] = huc8
                finished, _ = wait(list(running), timeout=10, return_when=FIRST_COMPLETED)
                if not finished:                   # keep the heartbeat live while the pool works
                    progress.tick(done=done, message=f"{label}: {done} of {len(huc8s)} HUC8s, "
                                                     f"{len(running)} in flight{_in_flight(root, running.values())}")
                    continue
                for future in finished:
                    huc8 = running.pop(future)
                    future.result()                       # re-raises Pause/Cancel/failures
                    done += 1
                    progress.tick(done=done, message=f"{label}: {done} of {len(huc8s)} HUC8s "
                                                     f"({huc8} finished)")
                states.reload()                            # pick up the children's checkpoints
        except (PauseRequested, CancelRequested):
            pool.shutdown(wait=True, cancel_futures=True)  # the HUC8s in flight finish first
            raise


def run_chunk(root: DataRoot, chunk: Chunk, states: UnitStates, progress: Progress,
              control: Control, *, stages: Optional[Iterable[str]] = None,
              huc8s: Optional[Iterable[str]] = None, force: bool = False,
              workers: Optional[int] = None, keep_dem_windows: bool = False) -> None:
    """Run the selected stages (default all, in order) for the chunk."""
    wanted = list(stages or ORDER)
    targets = list(huc8s or chunk.huc8s)
    for name in ORDER:
        if name not in wanted:
            continue
        control.check()
        if name in CHUNK_STAGES:
            _chunk_stage_fn(name)(root, chunk, states, progress, control, force=force)
            continue
        group = next(g for g in _HUC8_GROUPS if name in g)
        if name != group[0]:
            continue                                  # ran with its group's first stage
        group_stages = tuple(s for s in group if s in wanted)
        _run_huc8_stages(root, chunk, targets, group_stages, states, progress, control,
                         force=force, workers=workers, keep_dem_windows=keep_dem_windows)
    states.reload()
    progress.finish(f"{chunk.id}: done")


def chunk_status(states: UnitStates, chunk: Chunk) -> dict:
    """Per-stage status summary for the GUI table."""
    out: dict[str, str] = {}
    unit = states.unit(chunk.id)
    for name in CHUNK_STAGES:
        out[name] = (unit.get(name) or {}).get("status", "pending")
    for name in HUC8_STAGES:
        statuses = [(states.unit(h).get(name) or {}).get("status", "pending") for h in chunk.huc8s]
        if all(s == "done" for s in statuses):
            out[name] = "done"
        elif any(s == "running" for s in statuses):
            out[name] = "running"
        elif any(s == "failed" for s in statuses):
            out[name] = "failed"
        elif any(s == "done" for s in statuses):
            out[name] = f"{sum(1 for s in statuses if s == 'done')}/{len(statuses)}"
        else:
            out[name] = "pending"
    return out
