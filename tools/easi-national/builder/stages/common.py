"""Helpers every stage shares: input digests, atomic parquet writes, JSON
part files for resumable loops, bbox math, and the stage run wrapper."""
from __future__ import annotations

import json
import math
import shutil
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from ..paths import DataRoot
from ..state import (CancelRequested, Control, PauseRequested, Progress,
                     UnitStates, digest)
from ..units import Chunk

MI_PER_DEG_LAT = 69.0


def write_parquet(table, path: Path, **kwargs) -> Path:
    """Write ``table`` (pyarrow Table or pandas/GeoPandas frame) atomically."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    if hasattr(table, "to_parquet") and not isinstance(table, pa.Table):
        table.to_parquet(tmp, **kwargs)          # (Geo)DataFrame
    else:
        pq.write_table(table, tmp, compression=kwargs.get("compression", "zstd"),
                       use_byte_stream_split=kwargs.get("use_byte_stream_split", False))
    tmp.replace(path)
    return path


def parts_dir(root: DataRoot, chunk_id: str, source: str) -> Path:
    path = root.chunk_dir(chunk_id) / "raw" / f"{source}_parts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_part(folder: Path, key: str, payload) -> Path:
    path = folder / f"{key}.json"
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    return path


def read_parts(folder: Path) -> Iterable[tuple[str, object]]:
    for path in sorted(folder.glob("*.json")):
        try:
            yield path.stem, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue


def drop_parts(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


def buffer_bbox(bbox: list[float], miles: float) -> list[float]:
    """``[west, south, east, north]`` grown by ``miles`` on every side."""
    west, south, east, north = bbox
    dlat = miles / MI_PER_DEG_LAT
    mid = math.radians((south + north) / 2.0)
    dlon = miles / (MI_PER_DEG_LAT * max(0.2, math.cos(mid)))
    return [west - dlon, south - dlat, east + dlon, north + dlat]


def bbox_grid(bbox: list[float], cell_deg: float) -> list[list[float]]:
    """Split a bbox into cells at most ``cell_deg`` wide and tall."""
    west, south, east, north = bbox
    nx = max(1, math.ceil((east - west) / cell_deg))
    ny = max(1, math.ceil((north - south) / cell_deg))
    dx, dy = (east - west) / nx, (north - south) / ny
    cells = []
    for i in range(nx):
        for j in range(ny):
            cells.append([west + i * dx, south + j * dy, west + (i + 1) * dx, south + (j + 1) * dy])
    return cells


def chunk_inputs(stage: str, chunk: Chunk, *extra) -> str:
    """The stage's input identity: which HUC8s, plus anything else that would
    change the output (metric names, radii, the schema)."""
    return digest(stage, sorted(chunk.huc8s), *extra)


def run_stage(states: UnitStates, unit: str, stage: str, inputs: str, fn: Callable[[], object],
              progress: Progress, *, force: bool = False) -> bool:
    """Run ``fn`` unless the stage is already done for ``inputs``; record the
    outcome. Returns True when work ran. A pause leaves the stage pending so the
    next run resumes it; a cancel too; a failure is recorded with its message."""
    if not force and states.is_done(unit, stage, inputs):
        return False
    states.set(unit, stage, "running", inputs=inputs)
    started = time.monotonic()
    try:
        fn()
    except PauseRequested:
        states.set(unit, stage, "pending", inputs=inputs, note="paused")
        raise
    except CancelRequested:
        states.set(unit, stage, "pending", inputs=inputs, note="cancelled")
        raise
    except Exception as exc:
        states.set(unit, stage, "failed", inputs=inputs, note=str(exc)[:300])
        raise
    states.set(unit, stage, "done", inputs=inputs,
               note=f"{time.monotonic() - started:.0f} s")
    return True


def esri_bounds(geometry: dict) -> Optional[tuple[float, float, float, float]]:
    """``(minx, miny, maxx, maxy)`` of an ESRI JSON geometry (point, paths, rings)."""
    if not geometry:
        return None
    if geometry.get("x") is not None and geometry.get("y") is not None:
        x, y = float(geometry["x"]), float(geometry["y"])
        return (x, y, x, y)
    parts = geometry.get("rings") or geometry.get("paths") or []
    xs, ys = [], []
    for part in parts:
        for point in part:
            if len(point) >= 2:
                xs.append(float(point[0]))
                ys.append(float(point[1]))
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))
