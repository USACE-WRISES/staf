"""Build the grid: place, prefetch, sample, score, write; resumable per 10 km cell.

Per region (an HR VPU) the sections are placed once (``WORK/sections/<vpu>.parquet``). The work
then flows per EPSG:5070 cell of 10 km through three stages that run at the same time:

1. **prefetch** (threads in the driver): the tiles a cell's sections need from their newest lidar
   project are downloaded whole into the tile cache, cells in order, a bounded distance ahead;
2. **sample** (``samplers`` processes): the cell's sections are sampled from the local tiles
   (other tiers downloaded on demand) and written as the cell's archive part;
3. **score** (``scorers`` processes): the archive part is decoded and every section scored into
   the cell's metrics part.

When every cell of a region has both parts they are merged into ``ARCHIVE/archive/xs_<vpu>.parquet``
and ``ARCHIVE/metrics/xsm_<vpu>.parquet`` (metrics also copied to ``WORK/metrics``), with
``ARCHIVE/regions/<vpu>.json`` beside them, and the parts are deleted. Parts that exist are never
redone, so a stopped run picks up where it was.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import zlib
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import archive, config, sections, tilecache

SECTION_COLUMNS = ("nhdplusid", "part", "k", "n_sec", "s_m", "x", "y", "nx", "ny", "da_sqkm", "division",
                   "bf_width_m", "bf_depth_m", "bf_area_m2", "bf_extrapolated", "wide_m", "cell")
LOG_LOCK = threading.Lock()


def log(msg: str) -> None:
    with LOG_LOCK:
        print(f"{datetime.now().strftime('%m-%d %H:%M:%S')} {msg}", flush=True)


def keep_awake() -> None:
    """Keep Windows from sleeping while the build runs (released when the process ends)."""
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)   # CONTINUOUS | SYSTEM


def below_normal_priority() -> None:
    """Run this process below normal priority (Windows) so the machine stays responsive."""
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    else:
        os.nice(10)


# ------------------------------------------------------------------ sections
def sections_path(vpu: str) -> Path:
    return config.WORK / "sections" / f"sections_{vpu}.parquet"


def region_sections(vpu: str) -> pa.Table:
    """The region's sections (placed once, then read back), ordered by cell."""
    path = sections_path(vpu)
    if path.exists():
        return pq.read_table(path)
    t0 = time.time()
    placed = sections.place(sections.flowlines(vpu))
    placed["cell"] = sections.cell_ids(placed["x"], placed["y"])
    cols = dict((k, placed[k]) for k in SECTION_COLUMNS)
    cols["division"] = pa.array(placed["division"].tolist(), type=pa.string())
    table = pa.table(cols)
    order = np.lexsort((table.column("k").to_numpy(), table.column("part").to_numpy(),
                        table.column("nhdplusid").to_numpy(), table.column("cell").to_numpy()))
    table = table.take(pa.array(order))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)
    log(f"[{vpu}] placed {table.num_rows:,} sections in {time.time() - t0:.0f} s")
    return table


def _rows(table: pa.Table, lo: int, hi: int) -> dict:
    sl = table.slice(lo, hi - lo)
    out = {}
    for name in SECTION_COLUMNS:
        col = sl.column(name)
        out[name] = col.to_pylist() if name == "division" else col.to_numpy()
    return out


def part_paths(vpu: str, cell: int) -> tuple:
    folder = config.ARCHIVE / "parts" / vpu
    return folder / f"c{cell}.parquet", folder / f"c{cell}.metrics.parquet"


# ------------------------------------------------------------------ worker processes
_CAT = None


def _worker_init(priority_low: bool, with_catalog: bool) -> None:
    global _CAT
    if priority_low:
        below_normal_priority()
    if with_catalog:
        from .engine import dem_tiles
        dem_tiles.GDAL_ENV.update(config.gdal_env())
        from .sample import Catalogs
        _CAT = Catalogs()


def sample_task(vpu: str, cell: int, rows: dict, cands: list) -> dict:
    """Sample one cell's sections and write its archive part."""
    from .engine import dem_tiles
    from .sample import Section, sample_cell
    t0 = time.time()
    n = len(rows["x"])
    secs = [Section(i=i, x=float(rows["x"][i]), y=float(rows["y"][i]), nx=float(rows["nx"][i]),
                    ny=float(rows["ny"][i]), wide=float(rows["wide_m"][i]), bbox4326=tuple(cands[i][0]),
                    candidates=cands[i][1]) for i in range(n)]
    try:
        counts = sample_cell(_CAT, secs)
    finally:
        for ds in list(dem_tiles._OPEN.values()):            # let the cache delete the tiles
            try:
                ds.close()
            except Exception:  # noqa: BLE001
                pass
        dem_tiles._OPEN.clear()
    t1 = time.time()
    arch = dict((k, rows[k]) for k in ("nhdplusid", "part", "k", "n_sec", "s_m", "x", "y", "nx", "ny", "da_sqkm",
                                       "bf_width_m", "bf_depth_m", "bf_area_m2", "bf_extrapolated", "wide_m"))
    arch["division"] = pa.array([str(v) for v in rows["division"]], type=pa.string())
    arch["res_m"] = np.asarray([s.res for s in secs], dtype=np.int8)
    arch["n_pts"] = np.asarray([len(s.z) if s.z is not None else 0 for s in secs], dtype=np.int16)
    arch["n_finite"] = np.asarray([int(np.isfinite(s.z).sum()) if s.z is not None else 0 for s in secs],
                                  dtype=np.int16)
    arch["source"] = pa.array([s.source for s in secs], type=pa.string())
    arch["tiles"] = pa.array([s.tiles for s in secs], type=pa.string())
    arch["z"] = archive.as_float32_lists([s.z for s in secs])
    nbytes = archive.write(pa.table(arch, schema=archive.ARCHIVE_SCHEMA), part_paths(vpu, cell)[0])
    return dict(stage="sample", vpu=vpu, cell=cell, sections=n, sample_s=round(t1 - t0, 1),
                write_s=round(time.time() - t1, 1), bytes=nbytes,
                samples=int(arch["n_pts"].astype(np.int64).sum()), **counts)


def score_task(vpu: str, cell: int) -> dict:
    """Score every section of a cell's archive part and write its metrics part."""
    from . import derive
    t0 = time.time()
    pa_, pm = part_paths(vpu, cell)
    t = pq.read_table(pa_)
    zs = archive.transects(t)
    cols = dict((name, t.column(name).to_pylist()) for name in
                ("nhdplusid", "part", "k", "n_sec", "s_m", "res_m", "wide_m", "n_pts", "da_sqkm", "bf_width_m",
                 "bf_depth_m", "bf_area_m2", "division"))
    mets = []
    for i, z in enumerate(zs):
        if cols["res_m"][i] == 0:
            mets.append({"status": "no_dem"})
            continue
        sid = int(cols["nhdplusid"][i]) * 1_000_003 + int(cols["k"][i]) * 7 + int(cols["part"][i])
        verify = (zlib.crc32(sid.to_bytes(16, "little", signed=True)) % config.VERIFY_EVERY) == 0
        mets.append(derive.metrics(z, cols["wide_m"][i], cols["n_pts"][i], cols["res_m"][i], cols["da_sqkm"][i],
                                   cols["bf_width_m"][i], cols["bf_depth_m"][i], cols["bf_area_m2"][i],
                                   cols["division"][i], verify=verify))
    met = dict((k, cols[k]) for k in ("nhdplusid", "part", "k", "n_sec", "s_m", "res_m"))
    for f in archive.METRICS_SCHEMA:
        if f.name not in met:
            met[f.name] = pa.array([m.get(f.name) for m in mets], type=f.type)
    archive.write(pa.table(met, schema=archive.METRICS_SCHEMA), pm)
    statuses: dict = {}
    for m in mets:
        statuses[m["status"]] = statuses.get(m["status"], 0) + 1
    return dict(stage="score", vpu=vpu, cell=cell, sections=len(zs), score_s=round(time.time() - t0, 1),
                verified=sum(1 for m in mets if m.get("verified")), reference_used=statuses.get("ok_reference", 0),
                statuses=statuses)


# ------------------------------------------------------------------ regions
def region_done(vpu: str) -> bool:
    return (config.ARCHIVE / "regions" / f"{vpu}.json").exists()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def _sum_dicts(ds: list) -> dict:
    out: dict = {}
    for d in ds:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v
    return out


def finish_region(vpu: str, cells: list, started: float) -> dict:
    """Merge the region's part files, record the region, delete the parts."""
    parts = [part_paths(vpu, c) for c in cells]
    md = archive.metadata({"vpu": vpu, "bundle": str(config.BUNDLE), "catalogs": str(config.CATALOGS)})
    xs = config.ARCHIVE / "archive" / f"xs_{vpu}.parquet"
    xm = config.ARCHIVE / "metrics" / f"xsm_{vpu}.parquet"
    b_xs = archive.concat([p[0] for p in parts], xs, md)
    b_xm = archive.concat([p[1] for p in parts], xm, md)
    work_copy = config.WORK / "metrics" / xm.name
    work_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(xm, work_copy)
    stats = [d for d in read_stats() if d.get("vpu") == vpu]
    samp = dict((d["cell"], d) for d in stats if d.get("stage") == "sample")
    score = dict((d["cell"], d) for d in stats if d.get("stage") == "score")
    rec = dict(vpu=vpu, sections=pq.ParquetFile(xs).metadata.num_rows, cells=len(cells), archive_bytes=b_xs,
               metrics_bytes=b_xm, archive_sha256=sha256(xs), metrics_sha256=sha256(xm),
               samples=sum(d.get("samples", 0) for d in samp.values()),
               tiers=dict((k, sum(d.get(k, 0) for d in samp.values())) for k in ("one", "three", "ten", "none")),
               statuses=_sum_dicts([d.get("statuses", {}) for d in score.values()]),
               verified=sum(d.get("verified", 0) for d in score.values()),
               reference_used=sum(d.get("reference_used", 0) for d in score.values()),
               wall_s=round(time.time() - started), finished=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               sampling_version=config.SAMPLING_VERSION, format=config.FORMAT)
    out = config.ARCHIVE / "regions" / f"{vpu}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8", newline="\n")
    os.replace(tmp, out)
    for a, b in parts:
        for p in (a, b):
            if p.exists():
                p.unlink()
    try:
        (config.ARCHIVE / "parts" / vpu).rmdir()
    except OSError:
        pass
    return rec


def stats_path() -> Path:
    return config.WORK / "logs" / "cells.jsonl"


def read_stats() -> list:
    path = stats_path()
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def write_stat(d: dict) -> None:
    path = stats_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOG_LOCK, open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(d) + "\n")


# ------------------------------------------------------------------ night runs
def parse_window(text: str) -> tuple:
    """``"20:00-07:00"`` -> minutes after midnight ``(1200, 420)``; a window may cross midnight."""
    start, end = (part.strip() for part in text.split("-"))

    def to_min(hm: str) -> int:
        h, m = hm.split(":")
        return int(h) * 60 + int(m)
    return to_min(start), to_min(end)


def in_window(window, now: datetime | None = None) -> bool:
    if window is None:
        return True
    now = now or datetime.now()
    m = now.hour * 60 + now.minute
    start, end = window
    return start <= m < end if start < end else (m >= start or m < end)


def fmt_window(window) -> str:
    return "-".join(f"{m // 60:02d}:{m % 60:02d}" for m in window)


def archive_writable() -> bool:
    """The archive drive is connected and takes a write."""
    try:
        config.ARCHIVE.mkdir(parents=True, exist_ok=True)
        probe = config.ARCHIVE / f".probe.{os.getpid()}"
        probe.write_bytes(b"ok")
        probe.unlink()
        return True
    except OSError:
        return False


def archive_missing() -> bool:
    """The archive folder is gone though regions are done (their metrics copies sit in WORK): the
    archive drive is unplugged, or another drive took its letter. No run may start a new archive
    then."""
    return not config.ARCHIVE.exists() and any((config.WORK / "metrics").glob("xsm_*.parquet"))


def lock_path() -> Path:
    return config.WORK / "build.lock"


def running_pid() -> int | None:
    """The process id of a build that is running now, else None."""
    import psutil
    try:
        pid = int(json.loads(lock_path().read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError):
        return None
    try:
        proc = psutil.Process(pid)
        return pid if "python" in proc.name().lower() else None
    except psutil.Error:
        return None


def acquire_lock() -> bool:
    if running_pid() is not None:
        return False
    lock_path().parent.mkdir(parents=True, exist_ok=True)
    lock_path().write_text(json.dumps(dict(pid=os.getpid(), started=datetime.now().isoformat(timespec="seconds"))),
                           encoding="utf-8")
    return True


def release_lock() -> None:
    try:
        if json.loads(lock_path().read_text(encoding="utf-8")).get("pid") == os.getpid():
            lock_path().unlink()
    except (OSError, ValueError):
        pass


def cleanup_partials() -> None:
    """Files a stopped run left half written: tile downloads and part files."""
    for pattern, folder in (("*.part", tilecache.cache_dir()), ("*.tmp", config.ARCHIVE / "parts")):
        if folder.exists():
            for f in folder.rglob(pattern):
                try:
                    f.unlink()
                except OSError:
                    pass


def stop_path() -> Path:
    return config.WORK / "STOP"


# ------------------------------------------------------------------ the driver
class Cell:
    __slots__ = ("vpu", "cell", "span", "cands", "urls", "fetched")

    def __init__(self, vpu, cell, span):
        self.vpu, self.cell, self.span = vpu, cell, span
        self.cands, self.urls, self.fetched = None, [], False


def run(vpus: list, *, samplers: int = 3, scorers: int = 7, priority_low: bool = True, cell_limit=None,
        cache_gb: float = 80.0, ahead: int = 16, fetch_threads: int = 48, window=None) -> int:
    """Build every region in ``vpus`` that is not done (``cell_limit``: a test run of that many
    cells per region, left unmerged). With ``window`` (minutes after midnight, start and end) the
    run starts only inside it and pauses at its end; ``run.py stop`` (the STOP file) pauses it any
    time. A pause finishes the cells in hand, so the next run picks up exactly there. A cell
    whose sampling fails is tried once more later in the run; if it fails again it stays to do,
    and its region merges at the next run."""
    if window is not None and not in_window(window):
        log(f"outside the run window {fmt_window(window)}; not starting")
        return 0
    if archive_missing():
        log(f"the archive {config.ARCHIVE} is not there, though regions are done (their metrics are in "
            f"{config.WORK / 'metrics'}); plug in the archive drive, or set XSGRID_ARCHIVE; not starting")
        return 2
    if not archive_writable():
        log(f"the archive drive ({config.ARCHIVE}) is not connected or not writable; not starting")
        return 2
    if not acquire_lock():
        log(f"another build is running (process {running_pid()}); not starting")
        return 0
    try:
        return _run(vpus, samplers=samplers, scorers=scorers, priority_low=priority_low, cell_limit=cell_limit,
                    cache_gb=cache_gb, ahead=ahead, fetch_threads=fetch_threads, window=window)
    finally:
        release_lock()


def _run(vpus: list, *, samplers, scorers, priority_low, cell_limit, cache_gb, ahead, fetch_threads, window) -> int:
    from .sample import Catalogs, Section, candidates, first_candidate_urls, transect_boxes
    try:
        stop_path().unlink()
    except OSError:
        pass
    cleanup_partials()
    if priority_low:
        below_normal_priority()
    keep_awake()
    todo = [v for v in vpus if not region_done(v)]
    if not todo:
        log("every region is done")
        return 0
    log(f"starting: {len(todo)} regions to do" + (f", run window {fmt_window(window)}" if window else ""))
    cat = Catalogs()
    chunk_pool = ThreadPoolExecutor(fetch_threads)
    tile_pool = ThreadPoolExecutor(6)
    cap = int(cache_gb * 1e9)
    tables: dict = {}
    region_cells: dict = {}
    region_started: dict = {}
    failures = [0]
    streak = [0]                                        # failures in a row: the drive may be gone
    retried: set = set()                                # cells already given their second try

    def cells_of(vpu: str) -> list:
        table = tables.get(vpu)
        if table is None:
            table = tables[vpu] = region_sections(vpu)
        cellcol = table.column("cell").to_numpy()
        starts = np.r_[0, np.flatnonzero(np.diff(cellcol)) + 1] if len(cellcol) else np.zeros(0, int)
        stops = np.r_[starts[1:], len(cellcol)]
        return [Cell(vpu, int(cellcol[a]), (int(a), int(b))) for a, b in zip(starts, stops)]

    def stream():
        for vpu in todo:
            cs = cells_of(vpu)
            region_cells[vpu] = [c.cell for c in cs]
            region_started[vpu] = time.time()
            left = [c for c in cs if not part_paths(vpu, c.cell)[0].exists()]
            scored = sum(1 for c in cs if part_paths(vpu, c.cell)[1].exists())
            if cell_limit is not None:
                left = left[:cell_limit]
            log(f"[{vpu}] {tables[vpu].num_rows:,} sections in {len(cs)} cells; {len(left)} to sample, "
                f"{len(cs) - scored} to score")
            for c in left:
                yield c
            if cell_limit is None:
                yield ("end", vpu)

    def prepare(c: Cell) -> None:
        table = tables[c.vpu]
        rows = _rows(table, *c.span)
        secs = [Section(i=i, x=float(rows["x"][i]), y=float(rows["y"][i]), nx=float(rows["nx"][i]),
                        ny=float(rows["ny"][i]), wide=float(rows["wide_m"][i])) for i in range(len(rows["x"]))]
        transect_boxes(secs)
        candidates(cat, secs)
        c.cands = [(s.bbox4326, s.candidates) for s in secs]
        c.urls = first_candidate_urls(cat, secs)

    fetching: dict = {}                                  # url -> future

    def fetch_cell(c: Cell) -> None:
        for url, size in c.urls:
            if tilecache.present(url) or url in fetching:
                continue
            fetching[url] = tile_pool.submit(tilecache.fetch, url, chunk_pool, size=size)

    def reap_fetches() -> None:
        for url, f in list(fetching.items()):
            if f.done():
                fetching.pop(url, None)
                try:
                    f.result()
                except Exception as exc:  # noqa: BLE001 - the sampler retries on demand
                    log(f"prefetch failed {url.rsplit('/', 1)[-1]}: {exc}")

    def cell_ready(c: Cell) -> bool:
        return all(url not in fetching for url, _ in c.urls)

    sp = ProcessPoolExecutor(max_workers=samplers, initializer=_worker_init, initargs=(priority_low, True))
    sc = ProcessPoolExecutor(max_workers=scorers, initializer=_worker_init, initargs=(priority_low, False))
    try:
        # cells sampled earlier but not scored yet
        backlog = []
        for vpu in todo:
            folder = config.ARCHIVE / "parts" / vpu
            if not folder.exists():
                continue
            for a in sorted(folder.glob("c*.parquet")):
                if a.name.endswith(".metrics.parquet"):
                    continue
                cell = int(a.stem[1:])
                if not part_paths(vpu, cell)[1].exists():
                    backlog.append((vpu, cell))
        scoring = dict((sc.submit(score_task, v, c), (v, c)) for v, c in backlog)
        sampling: dict = {}
        upcoming: deque = deque()
        gen = stream()
        exhausted = False
        ended: list = []
        t_start = time.time()
        n_sampled = n_scored = sec_sampled = 0
        last_report = time.time()
        stopping = None
        while True:
            if stopping is None:
                if stop_path().exists():
                    stopping = "the stop request"
                elif not in_window(window):
                    stopping = "the end of the run window"
                if stopping:
                    log(f"pausing at {stopping}: finishing {len(sampling)} cells being sampled and "
                        f"{len(scoring)} being scored")
                    upcoming.clear()
            while stopping is None and not exhausted and len(upcoming) < ahead:
                try:
                    item = next(gen)
                except StopIteration:
                    exhausted = True
                    break
                if isinstance(item, tuple):
                    ended.append(item[1])
                    continue
                prepare(item)
                upcoming.append(item)
            keep = set()
            for c in list(upcoming) + [v[2] for v in sampling.values()]:
                for url, _ in c.urls:
                    keep.add(str(tilecache.local_path(url)))
            if tilecache.usage() > cap:
                tilecache.evict(keep, cap)
            reap_fetches()
            for c in upcoming:
                if len(fetching) >= 12 or stopping:
                    break
                fetch_cell(c)
            while stopping is None and upcoming and len(sampling) < samplers and cell_ready(upcoming[0]):
                c = upcoming.popleft()
                rows = _rows(tables[c.vpu], *c.span)
                sampling[sp.submit(sample_task, c.vpu, c.cell, rows, c.cands)] = (c.vpu, c.cell, c)
            pending = list(sampling) + list(scoring)
            if stopping and not pending:
                break
            if not pending and not upcoming and exhausted:
                break
            if pending:
                done, _ = wait(pending, timeout=2.0, return_when=FIRST_COMPLETED)
            else:
                time.sleep(1.0)
                done = set()
            for f in done:
                if f in sampling:
                    vpu, cell, c = sampling.pop(f)
                    try:
                        s = f.result()
                    except Exception as exc:  # noqa: BLE001 - logged; one more try, else the cell stays to do
                        streak[0] += 1
                        again = stopping is None and (vpu, cell) not in retried
                        if again:                         # later in this run, so its region still merges
                            retried.add((vpu, cell))
                            upcoming.append(c)
                        else:
                            failures[0] += 1
                        log(f"[{vpu}] cell {cell} sampling FAILED {type(exc).__name__}: {exc}"
                            + ("; trying it once more" if again else ""))
                        continue
                    streak[0] = 0
                    write_stat(s)
                    n_sampled += 1
                    sec_sampled += s["sections"]
                    log(f"[{vpu}] sampled cell {cell}: {s['sections']} sections (1m {s['one']} 3m {s['three']} "
                        f"10m {s['ten']} none {s['none']}) in {s['sample_s']} s, "
                        f"{s['bytes'] / max(s['sections'], 1):.0f} B/section")
                    scoring[sc.submit(score_task, vpu, cell)] = (vpu, cell)
                elif f in scoring:
                    vpu, cell = scoring.pop(f)
                    try:
                        s = f.result()
                    except Exception as exc:  # noqa: BLE001
                        failures[0] += 1
                        streak[0] += 1
                        log(f"[{vpu}] cell {cell} scoring FAILED {type(exc).__name__}: {exc}")
                        continue
                    streak[0] = 0
                    write_stat(s)
                    n_scored += 1
            for vpu in list(ended):                       # merge a region once all its cells have both parts
                cs = region_cells.get(vpu, [])
                busy = (any(v == vpu for v, _c, _x in sampling.values()) or any(v == vpu for v, _c in scoring.values())
                        or any(c.vpu == vpu for c in upcoming))
                if busy:
                    continue
                if all(all(p.exists() for p in part_paths(vpu, c)) for c in cs):
                    rec = finish_region(vpu, cs, region_started.get(vpu, time.time()))
                    log(f"[{vpu}] DONE {rec['sections']:,} sections, archive {rec['archive_bytes'] / 1e9:.2f} GB "
                        f"({rec['archive_bytes'] / max(rec['sections'], 1):.0f} B/section), tiers {rec['tiers']}, "
                        f"verified {rec['verified']} (reference used {rec['reference_used']})")
                    tables.pop(vpu, None)
                elif not stopping:
                    log(f"[{vpu}] some cells failed; rerun to finish the region")
                ended.remove(vpu)
            if streak[0] >= 20:
                log("STOPPING: 20 cells failed in a row (is the archive drive connected and writable?)")
                break
            if time.time() - last_report > 300:
                hrs = (time.time() - t_start) / 3600
                log(f"progress: sampled {n_sampled} cells ({sec_sampled:,} sections, {sec_sampled / max(hrs, 1e-9):,.0f}"
                    f"/h), scored {n_scored}; scoring queue {len(scoring)}, prefetching {len(fetching)}, "
                    f"cache {tilecache.usage() / 1e9:.1f} GB")
                last_report = time.time()
        if stopping:
            log(f"PAUSED at {stopping}: sampled {n_sampled} cells ({sec_sampled:,} sections) and scored {n_scored} "
                f"this run; the next run picks up here")
        elif cell_limit is None and all(region_done(v) for v in vpus):
            log("FINISHED: every region is done; emptying the tile cache")
            shutil.rmtree(tilecache.cache_dir(), ignore_errors=True)
    finally:
        sp.shutdown(wait=True, cancel_futures=False)
        sc.shutdown(wait=True, cancel_futures=False)
        tile_pool.shutdown(wait=False, cancel_futures=True)
        chunk_pool.shutdown(wait=False, cancel_futures=True)
    return 1 if failures[0] else 0
