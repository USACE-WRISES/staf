"""The lean value files for the bundle (owner decision "lean values"; ``hrslim.lean``).

``lean_values`` encodes each region's exact files (``<v2 root>/values``) into
``<v2 root>/values_lean``, checks every decoded table against its exact source (land cover and
riparian shares within 0.1 point and cell totals exact, roads within 5 mm, dams and unit indices
identical, K within float32 precision, extras within their units), copies the crossings and the
ATTAINS unit tables unchanged, and merges its regions into ``values.json`` (encoding, regions, sizes),
so a run over the regions still missing keeps the earlier ones. Runs side by side can each drop the
other's regions from the index: run them one after another, or rebuild the index afterwards.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Callable

import numpy as np
import pyarrow.parquet as pq

from . import bootstrap_hrslim

bootstrap_hrslim()
from hrslim import lean  # noqa: E402


def log_default(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def _check(prefix: str, exact, small) -> None:
    if prefix in ("lc", "rip"):
        counts = np.column_stack([exact.column(c).to_numpy() for c in lean.LC_COLUMNS]).astype(np.float64)
        cells = counts.sum(axis=1)
        got, imp = lean.decode_lc(small)
        if not np.allclose(got.sum(axis=1), cells):
            raise RuntimeError(f"{prefix}: decoded cell totals differ")
        nz = cells > 0
        share = np.abs(got[nz] - counts[nz]) / cells[nz, None] * 100
        if share.max() > 0.1 + 1e-9:
            raise RuntimeError(f"{prefix}: a class share moved {share.max():.3f} points")
        if prefix == "rip" and not (np.array_equal(small.column("row").to_numpy(), exact.column("row").to_numpy())
                                    and np.array_equal(small.column("k").to_numpy(), exact.column("k").to_numpy())):
            raise RuntimeError("rip: rows or k changed")
    elif prefix == "roads":
        d = np.abs(lean.decode_roads(small) - exact.column("road_m").to_numpy().astype(np.float64))
        if d.max(initial=0) > 0.005 + 1e-9:
            raise RuntimeError(f"roads: moved {d.max():.3f} m")
    elif prefix == "dams":
        got = lean.decode_dams(small)
        for c in ("dams", "normal_missing", "normal_acft", "nid_acft"):
            if not np.array_equal(got[c], exact.column(c).to_numpy().astype(got[c].dtype)):
                raise RuntimeError(f"dams: {c} changed")
    elif prefix == "soils":
        got = lean.decode_soils(small)
        ks = exact.column("k_sum").to_numpy().astype(np.float64)
        if not np.allclose(got["k_sum"], ks, rtol=1e-6, atol=1e-9):
            raise RuntimeError("soils: k_sum moved")
        for c in ("k_cells", "ssurgo_cells", "statsgo_cells"):
            if not np.array_equal(got[c], exact.column(c).to_numpy()):
                raise RuntimeError(f"soils: {c} changed")
    elif prefix == "extras":
        got = lean.decode_extras(small)
        for c in got.column_names:
            a = got.column(c).to_numpy(zero_copy_only=False)
            b = exact.column(c).to_numpy(zero_copy_only=False)
            if a.dtype.kind == "f" or b.dtype.kind == "f":
                a, b = a.astype(np.float64), b.astype(np.float64)
                tol = 0.0005 if c == "sinuosity" else (0.05 if c.startswith("au_near_m_") else 0.5)
                if not np.array_equal(np.isnan(a), np.isnan(b)) or np.nanmax(np.abs(a - b), initial=0) > tol + 1e-6:
                    raise RuntimeError(f"extras: {c} moved")
            elif not np.array_equal(a, b):
                raise RuntimeError(f"extras: {c} changed")


def write_index(out_dir: Path, report: dict) -> dict:
    """``values.json``: the regions of ``report`` merged over those already listed, keeping only
    regions whose lean files are in ``out_dir``, with totals over all of them."""
    path = out_dir / "values.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    regions = dict(old.get("regions") or {}) if old.get("encoding") == report["encoding"] else {}
    regions.update(report["regions"])
    regions = dict((v, r) for v, r in sorted(regions.items()) if (out_dir / f"lc2_{v}.parquet").exists())
    exact = sum(r["exact_bytes"] for r in regions.values())
    small = sum(r["lean_bytes"] for r in regions.values())
    index = {"encoding": report["encoding"], "regions": regions, "exact_bytes": exact, "lean_bytes": small,
             "ratio": round(small / max(1, exact), 3)}
    path.write_text(json.dumps(index, indent=1), encoding="utf-8")
    return index


def lean_values(values_dir: Path, out_dir: Path, vpus: list[str], log: Callable = log_default) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"encoding": lean.ENCODING, "regions": {}}
    exact_total = lean_total = 0
    for vpu in vpus:
        sizes = {}
        for prefix, encode in lean.ENCODERS.items():
            src = values_dir / f"{prefix}2_{vpu}.parquet"
            exact = pq.read_table(src)
            small = encode(exact)
            _check(prefix, exact, small)
            dest = out_dir / src.name
            pq.write_table(small, dest, compression="zstd", compression_level=19,
                           column_encoding=({"row": "DELTA_BINARY_PACKED"} if "row" in small.column_names else None),
                           use_dictionary=[c for c in small.column_names if c.startswith("huc12_")] or False)
            sizes[prefix] = [src.stat().st_size, dest.stat().st_size]
        for prefix in ("xings", "au"):
            src = values_dir / f"{prefix}2_{vpu}.parquet"
            if src.exists():
                shutil.copy2(src, out_dir / src.name)
                sizes[prefix] = [src.stat().st_size, src.stat().st_size]
        e = sum(v[0] for v in sizes.values())
        s = sum(v[1] for v in sizes.values())
        exact_total += e
        lean_total += s
        report["regions"][vpu] = {"exact_bytes": e, "lean_bytes": s, "ratio": round(s / e, 3), "files": sizes}
        log(f"[{vpu}] lean values: {s / 1e6:.1f} MB from {e / 1e6:.1f} MB exact ({s / e:.3f})")
    report.update(exact_bytes=exact_total, lean_bytes=lean_total, ratio=round(lean_total / max(1, exact_total), 3))
    write_index(out_dir, report)
    return dict((k, v) for k, v in report.items() if k != "regions")
