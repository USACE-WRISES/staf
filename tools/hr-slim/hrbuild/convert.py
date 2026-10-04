"""Convert one USGS VPU package into the slim files.

Reads four tables straight out of the zip on S3 (``NHDFlowline``,
``NHDPlusFlowlineVAA``, ``NHDPlusEROMMA``, ``NHDPlusCatchment``), keeps the network
flowlines (``innetwork = 1`` joined to their VAA row, the content of the USGS
layer 3) and their catchments, simplifies, rounds to 1e-5 degree, and writes:

- ``data/lines_<vpu>.parquet`` and ``data/catchments<tol>_<vpu>.parquet`` (one per
  catchment tolerance),
- ``data/qa_<vpu>.parquet``: original full-precision geometry in a few small boxes,
- ``parts/<vpu>.json``: the manifest entry plus size and accuracy statistics.
"""
from __future__ import annotations

import json
import math
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from . import bootstrap_hrslim, config, source

bootstrap_hrslim()
from hrslim import fmt  # noqa: E402

LINE_FIELDS = ["nhdplusid", "gnis_name", "reachcode", "lengthkm", "ftype", "fcode", "innetwork", "vpuid"]
VAA_FIELDS = ["nhdplusid", "streamorde", "hydroseq", "uphydroseq", "dnhydroseq", "totdasqkm", "slope"]


def _transformers():
    from pyproj import Transformer
    return (Transformer.from_crs(4269, 5070, always_xy=True),
            Transformer.from_crs(5070, 4269, always_xy=True))


def _project(geoms, tr):
    return shapely.transform(geoms, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))


def _hilbert_order(geoms) -> np.ndarray:
    import geopandas as gpd
    return np.argsort(gpd.GeoSeries(geoms).hilbert_distance().to_numpy(), kind="stable")


def _ids(series: pd.Series) -> np.ndarray:
    return np.round(series.to_numpy("float64")).astype("int64")


def _text(series: pd.Series) -> pa.Array:
    vals = []
    for v in series.tolist():
        if v is None or (isinstance(v, float) and math.isnan(v)):
            vals.append(None)
        else:
            s = str(v).strip()
            vals.append(s or None)
    return pa.array(vals, type=pa.string())


def _whole(series: pd.Series, typ: pa.DataType) -> pa.Array:
    arr = pa.array(pd.to_numeric(series, errors="coerce").to_numpy("float64"), from_pandas=True)
    return arr.cast(typ)


def _real(series: pd.Series) -> pa.Array:
    return pa.array(pd.to_numeric(series, errors="coerce").to_numpy("float64"), from_pandas=True)


def _nbytes(table: pa.Table, row_group: int) -> int:
    sink = pa.BufferOutputStream()
    fmt.write_table(table, sink, row_group)
    return sink.getvalue().size


def _pct(values: np.ndarray) -> dict:
    if not values.size:
        return {}
    return {"median": round(float(np.median(values)) * 100, 4),
            "p95": round(float(np.quantile(values, 0.95)) * 100, 3),
            "p99": round(float(np.quantile(values, 0.99)) * 100, 3),
            "max": round(float(values.max()) * 100, 2)}


def convert_vpu(vpu: str, root: Path, *, line_tol: float = config.LINE_TOLERANCE_M,
                cat_tols=config.CATCHMENT_TOLERANCES_M, qa_boxes: int = config.QA_BOXES,
                qa_box_deg: float = config.QA_BOX_DEG,
                log: Callable[[str], None] = print) -> dict:
    t0 = time.time()
    pkg = source.package(vpu, root)
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    log(f"[{vpu}] reading {pkg['name']} ({pkg['bytes'] / 1e6:.0f} MB zip) from S3")
    fl = source.read_layer(pkg, "NHDFlowline", LINE_FIELDS, geometry=True)
    vaa = source.read_layer(pkg, "NHDPlusFlowlineVAA", VAA_FIELDS, geometry=False)
    erom = source.read_layer(pkg, "NHDPlusEROMMA", ["nhdplusid", "qama"], geometry=False)
    cat = source.read_layer(pkg, "NHDPlusCatchment", ["nhdplusid"], geometry=True)
    t_read = time.time() - t0
    log(f"[{vpu}] read {len(fl)} flowlines, {len(vaa)} VAA rows, {len(cat)} catchments in {t_read:.0f} s")

    for df in (fl, vaa, erom, cat):
        df.dropna(subset=["nhdplusid"], inplace=True)
        df["nhdplusid"] = _ids(df["nhdplusid"])
    network = pd.to_numeric(fl["innetwork"], errors="coerce") == 1
    lines = (fl[network].merge(vaa, on="nhdplusid", how="inner")
             .merge(erom.drop_duplicates("nhdplusid"), on="nhdplusid", how="left"))
    lines = lines[lines.geometry.notna() & ~lines.geometry.is_empty].reset_index(drop=True)
    dup_lines = int(lines["nhdplusid"].duplicated().sum())
    cat = cat[cat.geometry.notna() & ~cat.geometry.is_empty].reset_index(drop=True)
    is_net = cat["nhdplusid"].isin(set(lines["nhdplusid"].tolist())).to_numpy()
    to5070, to4269 = _transformers()

    # ---------------- lines
    t1 = time.time()
    order = _hilbert_order(lines.geometry.to_numpy())
    lines = lines.iloc[order].reset_index(drop=True)
    lgeo = shapely.force_2d(lines.geometry.to_numpy())
    l5070 = _project(lgeo, to5070)
    attrs = {
        "nhdplusid": pa.array(lines["nhdplusid"].to_numpy("int64")),
        "gnis_name": _text(lines["gnis_name"]),
        "reachcode": _text(lines["reachcode"]),
        "lengthkm": _real(lines["lengthkm"]),
        "totdasqkm": _real(lines["totdasqkm"]),
        "slope": _real(lines["slope"]),
        "fcode": _whole(lines["fcode"], pa.int32()),
        "ftype": _whole(lines["ftype"], pa.int32()),
        "streamorde": _whole(lines["streamorde"], pa.int16()),
        "hydroseq": _whole(lines["hydroseq"], pa.int64()),
        "uphydroseq": _whole(lines["uphydroseq"], pa.int64()),
        "dnhydroseq": _whole(lines["dnhydroseq"], pa.int64()),
        "vpuid": _text(lines["vpuid"]),
        "qama": _real(lines["qama"]),
    }

    def line_table(tol: float) -> pa.Table:
        geoms = lgeo if tol <= 0 else _project(shapely.simplify(l5070, tol, preserve_topology=False), to4269)
        cols = dict(attrs)
        cols.update(fmt.encode_lines(geoms))
        return pa.table(cols)

    ltable = line_table(line_tol)
    lfile = data_dir / fmt.lines_file(vpu)
    fmt.write_table(ltable, lfile, fmt.LINES_ROW_GROUP)
    line_variants = {}
    for tol in config.LINE_TOLERANCES_MEASURED:
        if tol != line_tol:
            line_variants[f"{tol:g}"] = _nbytes(line_table(tol), fmt.LINES_ROW_GROUP)
    line_variants[f"{line_tol:g}"] = lfile.stat().st_size
    attr_bytes = _nbytes(pa.table(attrs), fmt.LINES_ROW_GROUP)
    t_lines = time.time() - t1
    log(f"[{vpu}] lines: {len(lines)} rows, {lfile.stat().st_size / 1e6:.1f} MB in {t_lines:.0f} s")

    # ---------------- catchments (simplified as one coverage, sinks included,
    # so every shared edge is simplified once; then only network catchments kept)
    t2 = time.time()
    cgeo = shapely.force_2d(cat.geometry.to_numpy())
    invalid_original = int((~shapely.is_valid(cgeo[is_net])).sum())
    c5070 = _project(cgeo, to5070)
    net_idx = np.nonzero(is_net)[0]
    corder = net_idx[_hilbert_order(cgeo[net_idx])]
    area0 = shapely.area(c5070[corder])
    cat_ids = pa.array(cat["nhdplusid"].to_numpy("int64")[corder])
    cat_entries, cat_stats = {}, {}
    for tol in cat_tols:
        simp = c5070 if tol <= 0 else shapely.coverage_simplify(c5070, tol, simplify_boundary=False)
        deg = _project(simp[corder], to4269)
        cols = {"nhdplusid": cat_ids}
        cols.update(fmt.encode_polygons(deg))
        table = pa.table(cols)
        cfile = data_dir / fmt.catchments_file(vpu, tol)
        fmt.write_table(table, cfile, fmt.CATCHMENTS_ROW_GROUP)
        decoded = fmt.decode_polygons(table)
        area1 = shapely.area(_project(decoded, to5070))
        rel = np.abs(area1 - area0) / np.maximum(area0, 1.0)
        cat_entries[f"{tol:g}"] = {"file": cfile.name, "bytes": cfile.stat().st_size,
                                    "rows": table.num_rows,
                                    "row_groups": pq.ParquetFile(cfile).num_row_groups}
        cat_stats[f"{tol:g}"] = {"vertices": int(shapely.get_num_coordinates(decoded).sum()),
                                 "invalid": int((~shapely.is_valid(decoded)).sum()),
                                 "area_change_pct": _pct(rel)}
        log(f"[{vpu}] catchments {tol:g} m: {table.num_rows} rows, {cfile.stat().st_size / 1e6:.1f} MB")
    t_cat = time.time() - t2

    # ---------------- QA boxes: original geometry, full precision
    qa_entry = None
    if qa_boxes > 0 and len(lines):
        rng = np.random.default_rng(int(vpu) if vpu.isdigit() else 7)
        pick = rng.choice(len(lines), size=min(qa_boxes, len(lines)), replace=False)
        mids = shapely.line_interpolate_point(lgeo[pick], 0.5, normalized=True)
        half = qa_box_deg / 2
        boxes = [[round(float(p.x) - half, 5), round(float(p.y) - half, 5),
                  round(float(p.x) + half, 5), round(float(p.y) + half, 5)] for p in mids]
        region = shapely.union_all([shapely.box(*b) for b in boxes])
        line_hit = shapely.intersects(lgeo, region)
        cat_net = cgeo[corder]
        cat_hit = shapely.intersects(cat_net, region)
        kinds = ["line"] * int(line_hit.sum()) + ["catchment"] * int(cat_hit.sum())
        ids = list(lines["nhdplusid"].to_numpy("int64")[line_hit]) + list(cat_ids.to_numpy()[cat_hit])
        geoms = np.concatenate([lgeo[line_hit], cat_net[cat_hit]])
        qa_table = pa.table({"kind": pa.array(kinds, type=pa.string()),
                             "nhdplusid": pa.array(np.asarray(ids, dtype="int64")),
                             "wkb": pa.array(shapely.to_wkb(geoms), type=pa.binary())})
        qfile = data_dir / fmt.qa_file(vpu)
        pq.write_table(qa_table, qfile, row_group_size=max(1, qa_table.num_rows), compression="zstd")
        qa_entry = {"file": qfile.name, "bytes": qfile.stat().st_size, "boxes": boxes,
                    "rows": qa_table.num_rows}

    # ---------------- manifest entry
    lx = np.concatenate([ltable["xmin"].to_numpy(), ltable["xmax"].to_numpy()])
    ly = np.concatenate([ltable["ymin"].to_numpy(), ltable["ymax"].to_numpy()])
    bounds = [float(lx.min()) / fmt.SCALE, float(ly.min()) / fmt.SCALE,
              float(lx.max()) / fmt.SCALE, float(ly.max()) / fmt.SCALE]
    for entry in cat_entries.values():
        t = pq.read_table(data_dir / entry["file"], columns=list(fmt.BBOX_COLUMNS))
        bounds = [min(bounds[0], t["xmin"].to_numpy().min() / fmt.SCALE),
                  min(bounds[1], t["ymin"].to_numpy().min() / fmt.SCALE),
                  max(bounds[2], t["xmax"].to_numpy().max() / fmt.SCALE),
                  max(bounds[3], t["ymax"].to_numpy().max() / fmt.SCALE)]
    hs = ltable["hydroseq"].to_numpy(zero_copy_only=False)
    dn = ltable["dnhydroseq"].to_numpy(zero_copy_only=False)
    ids_l = ltable["nhdplusid"].to_numpy()

    def rng_of(a):
        a = a[np.isfinite(a.astype("float64"))]
        a = a[a != 0]
        return [int(a.min()), int(a.max())] if a.size else None

    entry = {
        "vpu": vpu,
        "package": {"name": pkg["name"], "key": pkg["key"], "bytes": pkg["bytes"],
                    "date": pkg["date"], "modified": pkg["modified"]},
        "bounds": [round(b, 5) for b in bounds],
        "id_range": [int(ids_l.min()), int(ids_l.max())],
        "hydroseq_range": rng_of(hs),
        "dnhydroseq_range": rng_of(dn),
        "lines": {"file": lfile.name, "bytes": lfile.stat().st_size, "rows": ltable.num_rows,
                  "row_groups": pq.ParquetFile(lfile).num_row_groups, "tolerance_m": line_tol},
        "catchments": cat_entries,
        "qa": qa_entry,
    }
    stats = {
        "seconds": {"read": round(t_read), "lines": round(t_lines), "catchments": round(t_cat),
                    "total": round(time.time() - t0)},
        "counts": {"flowlines_all": int(len(fl)), "network_lines": int(len(lines)),
                   "duplicate_line_ids": dup_lines, "catchments_all": int(len(cat)),
                   "network_catchments": int(len(corder))},
        "lines": {"vertices_original": int(shapely.get_num_coordinates(lgeo).sum()),
                  "bytes_by_tolerance": line_variants, "attribute_bytes": attr_bytes},
        "catchments": {"vertices_original": int(shapely.get_num_coordinates(cgeo[corder]).sum()),
                       "invalid_original": invalid_original, "by_tolerance": cat_stats},
    }
    recipe = {"scale": fmt.SCALE, "line_tolerance_m": line_tol,
              "catchment_tolerances_m": [float(t) for t in cat_tols],
              "default_catchment_tolerance_m": config.DEFAULT_CATCHMENT_TOLERANCE_M
              if config.DEFAULT_CATCHMENT_TOLERANCE_M in [float(t) for t in cat_tols]
              else float(cat_tols[-1]),
              "source": config.S3_BASE + config.S3_PREFIX}
    part = {"vpu": vpu, "entry": entry, "stats": stats, "recipe": recipe,
            "format": fmt.FORMAT_VERSION,
            "built": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    parts_dir = root / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    (parts_dir / f"{vpu}.json").write_text(json.dumps(part, indent=1), encoding="utf-8")
    err = parts_dir / f"{vpu}.error.txt"
    if err.exists():
        err.unlink()
    log(f"[{vpu}] done in {time.time() - t0:.0f} s")
    return part


def task(vpu: str, root: str, line_tol: float, cat_tols: tuple, qa_boxes: int) -> dict:
    """Process-pool entry: convert one VPU, logging to ``logs/<vpu>.log``; a
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
        part = convert_vpu(vpu, root_path, line_tol=line_tol, cat_tols=cat_tols,
                           qa_boxes=qa_boxes, log=log)
        return {"vpu": vpu, "ok": True, "seconds": part["stats"]["seconds"]["total"]}
    except Exception:
        text = traceback.format_exc()
        log(f"[{vpu}] FAILED\n{text}")
        (root_path / "parts").mkdir(parents=True, exist_ok=True)
        (root_path / "parts" / f"{vpu}.error.txt").write_text(text, encoding="utf-8")
        return {"vpu": vpu, "ok": False, "error": text.strip().splitlines()[-1]}
