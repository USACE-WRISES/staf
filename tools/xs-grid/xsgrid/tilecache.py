"""A bounded local cache of whole 3DEP tile files, downloaded with parallel range requests.

Reading a window straight from USGS's bucket runs at about 4 MB/s per stream; downloading whole
files in parallel ranges fills the connection (about 30 MB/s measured, 2026-10-03), and the grid
reads nearly every block of a tile anyway. Files land in ``WORK/tilecache`` under their own names
(``.part`` while downloading, renamed when complete and the size matches) and are deleted again
once no upcoming cell needs them, so the cache stays under its cap. Nothing is kept afterwards.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config

CHUNK = 4 << 20
RETRIES = 8


def cache_dir() -> Path:
    return Path(os.environ.get("XSGRID_TILECACHE") or (config.WORK / "tilecache"))


def local_path(url: str) -> Path:
    name = url.rsplit("/", 1)[-1]
    if name.endswith(".vrt"):
        raise ValueError("a VRT is read in place")
    parent = url.rsplit("/", 2)[-2] if name.lower().endswith((".img", ".ige", ".rrd")) else ""
    return cache_dir() / (f"{parent}__{name}" if parent else name)


_local = threading.local()


def _session():
    import requests
    s = getattr(_local, "s", None)
    if s is None:
        s = _local.s = requests.Session()
    return s


def _size(url: str) -> int:
    for attempt in range(RETRIES):
        try:
            r = _session().head(url, timeout=60, allow_redirects=True)
        except Exception:  # noqa: BLE001 - retried
            time.sleep(min(60, 2 ** attempt))
            continue
        if r.status_code in (403, 404):
            raise FileNotFoundError(url)                  # no such tile (offshore, retired)
        if r.ok and "Content-Length" in r.headers:
            return int(r.headers["Content-Length"])
        time.sleep(min(60, 2 ** attempt))
    raise IOError(f"no size for {url}")


def _get_range(url: str, a: int, b: int) -> bytes:
    last = None
    for attempt in range(RETRIES):
        try:
            r = _session().get(url, headers={"Range": f"bytes={a}-{b}"}, timeout=120)
            r.raise_for_status()
            if len(r.content) != b - a + 1:
                raise IOError(f"short read {len(r.content)} of {b - a + 1}")
            return r.content
        except Exception as exc:  # noqa: BLE001 - retried with backoff
            last = exc
            time.sleep(min(60, 2 ** attempt))
    raise IOError(f"range {a}-{b} of {url} failed: {last}")


def fetch(url: str, pool: ThreadPoolExecutor, *, size: int | None = None) -> Path:
    """Download ``url`` whole into the cache (parallel ranges on ``pool``); returns the path."""
    path = local_path(url)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    size = size or _size(url)
    part = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.part")
    with open(part, "wb") as fh:
        fh.truncate(size)
    try:
        futures = {}
        for a in range(0, size, CHUNK):
            b = min(a + CHUNK, size) - 1
            futures[pool.submit(_get_range, url, a, b)] = a
        with open(part, "r+b") as fh:
            for f in as_completed(futures):
                fh.seek(futures[f])
                fh.write(f.result())
        if part.stat().st_size != size:
            raise IOError("size mismatch")
        try:
            os.replace(part, path)
        except OSError:
            if not path.exists():
                raise
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
    # a 1/9 arc-second .img keeps its pyramid and statistics beside it; they are optional
    return path


def present(url: str) -> bool:
    return local_path(url).exists()


def usage() -> int:
    d = cache_dir()
    return sum(p.stat().st_size for p in d.glob("*") if p.is_file()) if d.exists() else 0


def evict(keep: set, cap_bytes: int, min_age_s: float = 1800.0) -> int:
    """Delete cached files not in ``keep`` (paths), oldest first, until under ``cap_bytes``; a
    file younger than ``min_age_s`` stays (a sampler may have just fetched it)."""
    d = cache_dir()
    if not d.exists():
        return 0
    files = [p for p in d.glob("*") if p.is_file() and not p.name.endswith(".part")]
    total = sum(p.stat().st_size for p in d.glob("*") if p.is_file())
    freed = 0
    now = time.time()
    for p in sorted(files, key=lambda q: q.stat().st_mtime):
        if total <= cap_bytes:
            break
        if str(p) in keep or now - p.stat().st_mtime < min_age_s:
            continue
        try:
            n = p.stat().st_size
            p.unlink()
            total -= n
            freed += n
        except OSError:
            pass
    return freed
