"""Download a USGS package once, then unzip it next to the build.

Streaming whole layers out of a zip on S3 re-inflates the tables from the start
on every seek (the 0512 conversion was still reading after 1 h 43 min), so the
version 2 build downloads each zip once into a shared folder, resuming partial
downloads with HTTP Range requests, and unzips one region at a time.
"""
from __future__ import annotations

import shutil
import time
import zipfile
from pathlib import Path
from typing import Callable

import requests


def fetch(pkg: dict, zips_dir: Path, *, log: Callable[[str], None] = print, retries: int = 4) -> Path:
    """The package zip in ``zips_dir``, downloaded (or finished) when needed;
    its size must match the S3 listing."""
    zips_dir.mkdir(parents=True, exist_ok=True)
    dest = zips_dir / pkg["name"]
    want = int(pkg["bytes"])
    if dest.exists() and dest.stat().st_size == want:
        return dest
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, retries + 1):
        have = part.stat().st_size if part.exists() else 0
        if have > want:
            part.unlink()
            have = 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        t0 = time.time()
        try:
            with requests.get(pkg["url"], headers=headers, stream=True, timeout=120) as r:
                if r.status_code == 200 and have:
                    have = 0                                   # server ignored the range: start over
                elif r.status_code not in (200, 206):
                    r.raise_for_status()
                with open(part, "ab" if have else "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
        except (requests.RequestException, OSError) as exc:
            log(f"[{pkg['vpu']}] download attempt {attempt} stopped: {exc}")
            time.sleep(min(30, 5 * attempt))
            continue
        size = part.stat().st_size
        if size == want:
            part.replace(dest)
            mb = (want - have) / 1e6
            log(f"[{pkg['vpu']}] downloaded {want / 1e6:.0f} MB ({mb / max(time.time() - t0, 1e-6):.0f} MB/s)")
            return dest
        log(f"[{pkg['vpu']}] download attempt {attempt} ended at {size} of {want} bytes")
    raise RuntimeError(f"could not download {pkg['name']}")


def unzip(zip_path: Path, work_dir: Path) -> Path:
    """Extract the zip into ``work_dir`` (emptied first) and return its ``.gdb`` folder."""
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(work_dir)
    found = sorted(p for p in work_dir.rglob("*.gdb") if p.is_dir())
    if not found:
        raise RuntimeError(f"{zip_path.name} holds no .gdb folder")
    return found[0]
