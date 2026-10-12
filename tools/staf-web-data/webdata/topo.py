"""Each region's stream network topology on its own, for the browser.

A watershed walk (and EASI's point lookups) needs a region's whole topology: every flowline's id,
hydroseq, steps to its downstream and upstream neighbors, the outlets beyond the region and its drainage
area. The bundle keeps those columns in the region's lines file among the geometry, in row groups of
4,096 lines, so a browser reading them makes about three requests a row group (84 for 0108) and reads
their pages beside the geometry. Here they are written on their own (``topo/hr_<vpu>.parquet`` from
``lines2_<vpu>.parquet``, ``topo/v2_<vpu>.parquet`` from ``v2/lines2_<vpu>.parquet``): the same rows in the
same order, one row group, read in one request.

A copy is kept in the build's cache and written again only when its source or ``LAYOUT`` changes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

#: the layout's version: a cached copy written by another version is written again
LAYOUT = 2
#: the topology's columns (those a lines file has; ``dnminor_step`` only where minor divergences are kept)
COLUMNS = ("nhdplusid", "hydroseq", "dn_step", "up_step", "dn_out", "up_out", "totdasqkm", "dnminor_step")
LINES = re.compile(r"^(v2/)?lines2_(\w+)\.parquet$")


def logical(rel: str) -> str | None:
    """The topology's logical name for a bundle lines file (``lines2_0108.parquet`` -> ``hr_0108.parquet``,
    ``v2/lines2_0108.parquet`` -> ``v2_0108.parquet``), else None."""
    m = LINES.match(rel)
    if not m:
        return None
    return f"{'v2' if m.group(1) else 'hr'}_{m.group(2)}.parquet"


def write(src: Path, cache: Path, name: str) -> Path:
    """The topology of the lines file ``src`` (written into ``cache`` when missing or stale)."""
    import pyarrow.parquet as pq
    dst = Path(cache) / name
    stamp = dst.with_name(dst.name + ".json")
    st = Path(src).stat()
    key = {"layout": LAYOUT, "source_bytes": st.st_size, "source_mtime_ns": st.st_mtime_ns}
    if dst.exists() and stamp.exists() and json.loads(stamp.read_text(encoding="utf-8")) == key:
        return dst
    names = pq.read_schema(src).names
    table = pq.read_table(src, columns=[c for c in COLUMNS if c in names])
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    # no dictionaries: nearly every id and area is distinct (0108: 1.21 MB plain, 1.62 MB with dictionaries)
    pq.write_table(table, tmp, row_group_size=max(1, table.num_rows), compression="zstd", compression_level=19,
                   use_dictionary=False, write_statistics=False)
    tmp.replace(dst)
    stamp.write_text(json.dumps(key), encoding="utf-8")
    return dst
