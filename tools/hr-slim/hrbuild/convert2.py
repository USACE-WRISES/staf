"""Convert one USGS package into the slim version 2 files (``hrslim.fmt2``).

Downloads the package once (``download.fetch``), unzips it to ``<root>/work/<vpu>``,
reads four tables locally (``NHDFlowline``, ``NHDPlusFlowlineVAA``, ``NHDPlusEROMMA``,
``NHDPlusCatchment``), keeps the network flowlines and their catchments, and writes:

- ``data/lines2_<vpu>.parquet``: flowlines simplified at 2 m, slim attributes,
  network as row offsets;
- ``data/cats2_<vpu>.parquet`` and ``data/arcs2_<vpu>.parquet``: the catchments,
  exact, as shared borders on the region's elevation grid;
- ``parts/<vpu>.json``: the manifest entry plus counts, sizes and timings.

Every catchment is checked to decode to its original polygon (equal area in cells,
geometrically equal; polygons USGS published invalid must differ by zero area).
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from . import bootstrap_hrslim, config, download, source
from .convert import LINE_FIELDS, VAA_FIELDS, _hilbert_order, _ids, _project, _transformers

bootstrap_hrslim()
from hrslim import arcs, fmt, fmt2, grid  # noqa: E402


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _text_list(series: pd.Series) -> list:
    out = []
    for v in series.tolist():
        if v is None or (isinstance(v, float) and math.isnan(v)):
            out.append(None)
        else:
            s = str(v).strip()
            out.append(s or None)
    return out


def _num(series: pd.Series) -> np.ndarray:
    return pd.to_numeric(series, errors="coerce").to_numpy("float64")


def _file_entry(path: Path, rows: int) -> dict:
    return {"file": path.name, "bytes": path.stat().st_size, "rows": int(rows),
            "row_groups": pq.ParquetFile(path).num_row_groups, "sha256": _sha256(path)}


def _ranges(starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    counts = np.asarray(counts, dtype=np.int64)
    if not counts.sum():
        return np.empty(0, dtype=np.int64)
    off = np.concatenate([[0], np.cumsum(counts)[:-1]])
    return np.repeat(np.asarray(starts, dtype=np.int64) - off, counts) + np.arange(int(counts.sum()))


def simplify_lines(lines: np.ndarray, tol: float, keep_ends: float = 0.0) -> np.ndarray:
    """Douglas-Peucker at ``tol`` (projected metres), keeping every vertex within
    ``keep_ends`` metres (along the line) of each part's two ends, where flowlines
    meet at confluences; lines keep their direction. ``keep_ends = 0`` is plain
    Douglas-Peucker."""
    if keep_ends <= 0:
        return shapely.simplify(lines, tol, preserve_topology=False)
    parts, owner = shapely.get_parts(lines, return_index=True)
    xy, vid = shapely.get_coordinates(parts, return_index=True)
    counts = np.bincount(vid, minlength=len(parts))
    start = np.concatenate([[0], np.cumsum(counts)[:-1]]).astype(np.int64)
    end = start + counts - 1
    seg = np.zeros(len(xy))
    seg[1:] = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    seg[start] = 0.0
    cum = np.cumsum(seg)
    d = cum - cum[start][vid]
    pos = np.arange(len(xy))
    i1 = np.maximum.reduceat(np.where(d <= keep_ends, pos, -1), start)                  # last head vertex
    i2 = np.minimum.reduceat(np.where(d >= (d[end] - keep_ends)[vid], pos, len(xy)), start)  # first tail vertex
    mid = np.nonzero(i2 - i1 >= 2)[0]
    whole = np.nonzero(i2 - i1 < 2)[0]
    mids = shapely.linestrings(xy[_ranges(i1[mid], i2[mid] - i1[mid] + 1)],
                               indices=np.repeat(np.arange(len(mid)), i2[mid] - i1[mid] + 1))
    mxy, mvid = shapely.get_coordinates(shapely.simplify(mids, tol, preserve_topology=False), return_index=True)
    # every kept vertex with (part, stage, place): whole parts, then head, simplified middle, tail
    pieces = [
        (_ranges(start[whole], counts[whole]), np.repeat(whole, counts[whole]), 0),
        (_ranges(start[mid], i1[mid] - start[mid]), np.repeat(mid, i1[mid] - start[mid]), 0),
        (None, mid[mvid], 1),
        (_ranges(i2[mid] + 1, end[mid] - i2[mid]), np.repeat(mid, end[mid] - i2[mid]), 2),
    ]
    coords, part_of, stage = [], [], []
    for idx, part, st in pieces:
        coords.append(mxy if idx is None else xy[idx])
        part_of.append(part)
        stage.append(np.full(len(part), st, dtype=np.int8))
    coords, part_of, stage = np.concatenate(coords), np.concatenate(part_of), np.concatenate(stage)
    order = np.lexsort((np.arange(len(part_of)), stage, part_of))
    out_parts = shapely.linestrings(coords[order], indices=part_of[order])
    merged = shapely.multilinestrings(out_parts, indices=owner)
    single = np.bincount(owner, minlength=len(lines)) == 1
    merged[single] = shapely.get_geometry(merged[single], 0)
    return merged


def check_exact(enc: arcs.Encoded, cells_geoms: np.ndarray) -> dict:
    """Decoded catchments against the originals (both in cells)."""
    decoded = arcs.rows_geometry(enc, np.arange(enc.n_rows))
    area_ok = np.abs(enc.area2 / 2 - shapely.area(cells_geoms)) < 1e-6
    valid = shapely.is_valid(cells_geoms)
    equal = np.zeros(len(decoded), dtype=bool)
    equal[valid] = shapely.equals(decoded[valid], cells_geoms[valid])
    inv = np.nonzero(~valid)[0]
    if inv.size:
        diff = shapely.area(shapely.symmetric_difference(shapely.make_valid(decoded[inv]),
                                                         shapely.make_valid(cells_geoms[inv])))
        equal[inv] = diff == 0
    bad = np.nonzero(~(area_ok & equal))[0]
    return {"rows": int(enc.n_rows), "invalid_original": int(inv.size), "area_mismatch": int((~area_ok).sum()),
            "unequal": int((~equal).sum()), "first_bad_rows": bad[:10].tolist()}


def convert_vpu2(vpu: str, root: Path, *, line_tol: float = config.V2_LINE_TOLERANCE_M,
                 keep_ends: float = config.V2_LINE_KEEP_ENDS_M, gdb: Optional[Path] = None, zips_dir: Path = config.ZIPS_DIR, keep_work: bool = False,
                 log: Callable[[str], None] = print) -> dict:
    t0 = time.time()
    pkg = source.package(vpu, root)
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    work = None
    if gdb is None:
        zpath = download.fetch(pkg, zips_dir, log=log)
        work = root / "work" / vpu
        t_dl = time.time()
        gdb = download.unzip(zpath, work)
        log(f"[{vpu}] unzipped in {time.time() - t_dl:.0f} s")
    t_read0 = time.time()
    src = str(gdb)
    fl = source.read_layer(pkg, "NHDFlowline", LINE_FIELDS, geometry=True, path=src)
    vaa = source.read_layer(pkg, "NHDPlusFlowlineVAA", VAA_FIELDS, geometry=False, path=src)
    erom = source.read_layer(pkg, "NHDPlusEROMMA", ["nhdplusid", "qama"], geometry=False, path=src)
    cat = source.read_layer(pkg, "NHDPlusCatchment", ["nhdplusid"], geometry=True, path=src)
    t_read = time.time() - t_read0
    log(f"[{vpu}] read {len(fl)} flowlines, {len(vaa)} VAA rows, {len(cat)} catchments in {t_read:.0f} s")

    for df in (fl, vaa, erom, cat):
        df.dropna(subset=["nhdplusid"], inplace=True)
        df["nhdplusid"] = _ids(df["nhdplusid"])
    catchments_all = int(len(cat))
    network = pd.to_numeric(fl["innetwork"], errors="coerce") == 1
    lines = (fl[network].merge(vaa, on="nhdplusid", how="inner")
             .merge(erom.drop_duplicates("nhdplusid"), on="nhdplusid", how="left"))
    lines = lines[lines.geometry.notna() & ~lines.geometry.is_empty].reset_index(drop=True)
    to5070, to4269 = _transformers()

    # ---------------- lines
    t1 = time.time()
    lines = lines.iloc[_hilbert_order(lines.geometry.to_numpy())].reset_index(drop=True)
    lgeo = shapely.force_2d(lines.geometry.to_numpy())
    fcode = np.nan_to_num(_num(lines["fcode"]), nan=0).astype(np.int64)
    ftype = np.nan_to_num(_num(lines["ftype"]), nan=0).astype(np.int64)
    vpuids = sorted(set(v for v in _text_list(lines["vpuid"]) if v))
    attrs = {
        "nhdplusid": lines["nhdplusid"].to_numpy("int64"),
        "gnis_name": _text_list(lines["gnis_name"]),
        "reachcode": _text_list(lines["reachcode"]),
        "lengthkm": _num(lines["lengthkm"]), "totdasqkm": _num(lines["totdasqkm"]),
        "slope": _num(lines["slope"]), "fcode": fcode, "streamorde": _num(lines["streamorde"]),
        "hydroseq": np.nan_to_num(_num(lines["hydroseq"]), nan=0).round().astype(np.int64),
        "dnhydroseq": np.nan_to_num(_num(lines["dnhydroseq"]), nan=0).round().astype(np.int64),
        "uphydroseq": np.nan_to_num(_num(lines["uphydroseq"]), nan=0).round().astype(np.int64),
        "qama": _num(lines["qama"]),
    }
    simple = _project(simplify_lines(_project(lgeo, to5070), line_tol, keep_ends), to4269)
    ltable = fmt2.lines_table(attrs, simple)
    if not (ftype == fcode // 100).all():
        ltable = ltable.append_column("ftype", pa.array(ftype.astype(np.int32)))
    lfile = data_dir / fmt2.lines_file(vpu)
    fmt2.write(ltable, lfile, "lines")
    t_lines = time.time() - t1
    log(f"[{vpu}] lines: {ltable.num_rows} rows, {lfile.stat().st_size / 1e6:.2f} MB in {t_lines:.0f} s")

    # ---------------- catchments: exact, as shared borders
    t2 = time.time()
    cat = cat[cat.geometry.notna() & ~cat.geometry.is_empty].reset_index(drop=True)
    cat = cat[cat["nhdplusid"].isin(set(attrs["nhdplusid"].tolist()))].reset_index(drop=True)
    cgeo = shapely.force_2d(cat.geometry.to_numpy())
    corder = _hilbert_order(cgeo)
    cgeo = cgeo[corder]
    cids = cat["nhdplusid"].to_numpy("int64")[corder]
    _, coords, (ring_off, poly_off, row_off) = shapely.to_ragged_array(
        shapely.multipolygons(shapely.get_parts(cgeo), indices=np.repeat(np.arange(len(cgeo)), shapely.get_num_geometries(cgeo))))
    g = grid.detect(coords[:, 0], coords[:, 1])
    if g is None:
        raise arcs.NotGridAligned(f"{vpu}: catchment vertices fit no known grid")
    ix, iy, moved = g.to_cells(coords[:, 0], coords[:, 1])
    enc = arcs.encode(ix, iy, ring_off, poly_off, row_off)
    t_enc = time.time() - t2
    row_vert_off = ring_off[poly_off[row_off]]
    bbox_deg = np.column_stack([np.minimum.reduceat(coords[:, 0], row_vert_off[:-1]),
                                np.minimum.reduceat(coords[:, 1], row_vert_off[:-1]),
                                np.maximum.reduceat(coords[:, 0], row_vert_off[:-1]),
                                np.maximum.reduceat(coords[:, 1], row_vert_off[:-1])])
    t3 = time.time()
    cells_geoms = shapely.from_ragged_array(shapely.GeometryType.MULTIPOLYGON,
                                            np.column_stack([ix, iy]).astype(np.float64),
                                            (ring_off, poly_off, row_off))
    check = check_exact(enc, cells_geoms)
    t_check = time.time() - t3
    if check["area_mismatch"] or check["unequal"]:
        raise RuntimeError(f"{vpu}: catchments do not decode exactly: {check}")
    cats_t, arcs_t, steps = fmt2.catchment_tables(enc, cids, bbox_deg)
    cfile, afile, sfile = (data_dir / fmt2.cats_file(vpu), data_dir / fmt2.arcs_file(vpu),
                           data_dir / fmt2.steps_file(vpu))
    fmt2.write(cats_t, cfile, "cats")
    fmt2.write(arcs_t, afile, "arcs")
    sfile.write_bytes(steps)
    back, _, _ = fmt2.read_encoded(cfile, afile, sfile)
    if not (np.array_equal(back.area2, enc.area2) and np.array_equal(arcs.area2_from_arcs(back), enc.area2)
            and np.array_equal(back.left, enc.left) and np.array_equal(back.right, enc.right)
            and np.array_equal(back.runs, enc.runs)):
        raise RuntimeError(f"{vpu}: the written catchment files do not read back to the encoding")
    cat_bytes = cfile.stat().st_size + afile.stat().st_size + sfile.stat().st_size
    log(f"[{vpu}] catchments: {enc.n_rows} rows exact, {enc.n_arcs} shared borders, "
        f"{cat_bytes / 1e6:.2f} MB (encode {t_enc:.0f} s, check {t_check:.0f} s)")

    # ---------------- manifest entry
    lb = [ltable["xmin"].to_numpy().min(), ltable["ymin"].to_numpy().min(),
          ltable["xmax"].to_numpy().max(), ltable["ymax"].to_numpy().max()]
    bounds = [min(lb[0] / fmt.SCALE, float(bbox_deg[:, 0].min())), min(lb[1] / fmt.SCALE, float(bbox_deg[:, 1].min())),
              max(lb[2] / fmt.SCALE, float(bbox_deg[:, 2].max())), max(lb[3] / fmt.SCALE, float(bbox_deg[:, 3].max()))]
    hs = attrs["hydroseq"]
    hs_nz = hs[hs != 0]
    entry = {
        "vpu": vpu,
        "package": {"name": pkg["name"], "key": pkg["key"], "bytes": pkg["bytes"], "date": pkg["date"],
                    "modified": pkg["modified"]},
        "bounds": [round(b, 5) for b in bounds],
        "id_range": [int(attrs["nhdplusid"].min()), int(attrs["nhdplusid"].max())],
        "hydroseq_range": [int(hs_nz.min()), int(hs_nz.max())] if hs_nz.size else None,
        "grid": g.to_dict(),
        "vpuid": vpuids[0] if len(vpuids) == 1 else vpu,
        "ftype_from_fcode": "ftype" not in ltable.column_names,
        "lines": dict(_file_entry(lfile, ltable.num_rows), tolerance_m=line_tol, keep_ends_m=keep_ends),
        "catchments": _file_entry(cfile, cats_t.num_rows),
        "arcs": _file_entry(afile, arcs_t.num_rows),
        "steps": {"file": sfile.name, "bytes": sfile.stat().st_size, "values": int(len(enc.runs)),
                  "sha256": _sha256(sfile)},
    }
    stats = {
        "seconds": {"read": round(t_read), "lines": round(t_lines), "encode": round(t_enc),
                    "check": round(t_check), "total": round(time.time() - t0)},
        "counts": {"flowlines_all": int(len(fl)), "network_lines": int(ltable.num_rows),
                   "duplicate_line_ids": int(pd.Series(attrs["nhdplusid"]).duplicated().sum()),
                   "duplicate_hydroseq": int(pd.Series(hs_nz).duplicated().sum()),
                   "catchments_all": catchments_all, "network_catchments": int(enc.n_rows),
                   "vpuids": vpuids, "links_out": int(ltable.num_rows - ltable["dn_out"].null_count)},
        "grid_moved_m": round(moved, 6),
        "encode": enc.stats,
        "check": check,
        "lines": {"vertices_original": int(shapely.get_num_coordinates(lgeo).sum()),
                  "vertices_stored": int(shapely.get_num_coordinates(simple).sum())},
    }
    recipe = {"format": fmt2.FORMAT_VERSION, "scale": fmt.SCALE, "line_tolerance_m": line_tol,
              "line_keep_ends_m": keep_ends,
              "catchments": "exact", "source": config.S3_BASE + config.S3_PREFIX}
    part = {"vpu": vpu, "entry": entry, "stats": stats, "recipe": recipe, "format": fmt2.FORMAT_VERSION,
            "built": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    parts_dir = root / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    (parts_dir / f"{vpu}.json").write_text(json.dumps(part, indent=1), encoding="utf-8")
    err = parts_dir / f"{vpu}.error.txt"
    if err.exists():
        err.unlink()
    if work is not None and not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    log(f"[{vpu}] done in {time.time() - t0:.0f} s")
    return part


def task2(vpu: str, root: str, line_tol: float, zips_dir: str, keep_ends: float = 0.0) -> dict:
    """Process-pool entry: convert one region, logging to ``logs/<vpu>.log``; a
    failure is written to ``parts/<vpu>.error.txt`` and returned, never raised."""
    root_path = Path(root)
    (root_path / "logs").mkdir(parents=True, exist_ok=True)
    log_path = root_path / "logs" / f"{vpu}.log"

    def log(msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {msg}\n")
        print(f"{stamp} {msg}", flush=True)

    try:
        part = convert_vpu2(vpu, root_path, line_tol=line_tol, keep_ends=keep_ends, zips_dir=Path(zips_dir), log=log)
        return {"vpu": vpu, "ok": True, "seconds": part["stats"]["seconds"]["total"]}
    except Exception as exc:  # report, keep the pool going
        msg = f"{type(exc).__name__}: {exc}"
        (root_path / "parts").mkdir(parents=True, exist_ok=True)
        (root_path / "parts" / f"{vpu}.error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        log(f"[{vpu}] FAILED {msg}")
        return {"vpu": vpu, "ok": False, "error": msg}
