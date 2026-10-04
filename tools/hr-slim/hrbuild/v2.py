"""NHDPlus V2 for EASI's covered streams, in the version 2 format (bundle v2, Phase 3).

EASI scores a stream NHDPlus V2 covers from V2 data: the click rule (a V2 line within 150 ft),
the V2 basin and upstream main stem (NLDI today), StreamCat and EROM by COMID. This writes, per
pilot region (the HU4 of the V2 reach codes), a second dataset in ``hrslim.fmt2``'s layout, with
COMIDs in the ``nhdplusid`` columns, so ``hrslim.Dataset2`` walks it, outlines basins and answers
boxes unchanged:

- ``lines2_<hu4>.parquet``: V2 network flowlines kept whole (coordinates to 1e-5 degree), V2
  attributes and routing (``DnHydroseq``/``UpHydroseq``), ``qama`` = EROM mean annual flow ``QA_MA``;
- ``cats2``/``arcs2``/``steps2``: the V2 catchments, exact on their 30 m grid (EPSG:5070, corners
  at 15 mod 30), checked against the originals like HR;
- ``v2attrs2_<hu4>.parquet``, per flowline row: the gage-adjusted EROM flows EASI's M5 reads from the
  fabric API (``qe_ma``, ``qe_01``..``qe_12``, cfs) and the sinuosity EASI computes (first part,
  length over endpoint distance in EPSG:5070, 3 decimals), from the original geometry.

Source: the local NHDPlus V2.1 national seamless geodatabase (``D:\\Data\\easi-national``).
Root: ``D:\\Data\\nhdplus-hr\\v2pilot`` (``HR_V2_ROOT``).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import pyarrow as pa
import shapely

from . import bootstrap_hrslim, config
from .convert import _hilbert_order, _project, _transformers
from .convert2 import _file_entry, _sha256, check_exact, simplify_lines

bootstrap_hrslim()
from hrslim import arcs, fmt, fmt2, grid  # noqa: E402

V2_GDB = Path(r"D:\Data\easi-national\national\nhdplus\NHDPlusNationalData"
              r"\NHDPlusV21_National_Seamless_Flattened_Lower48.gdb")
V2_ROOT = Path(os.environ.get("HR_V2_ROOT") or r"D:\Data\nhdplus-hr\v2pilot")
#: EASI reads the gage-adjusted EROM flows (``qe_ma``, ``qe_01``..``qe_12``, fabric API) for M5
EROM_FIELDS = ["QE_MA"] + [f"QE_{m:02d}" for m in range(1, 13)]
LINE_FIELDS = ["COMID", "GNIS_NAME", "REACHCODE", "LENGTHKM", "FCODE", "StreamOrde", "Hydroseq", "DnHydroseq",
               "UpHydroseq", "DnMinorHyd", "TotDASqKM", "SLOPE", "QA_MA"] + EROM_FIELDS


def log_default(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def _num(s) -> np.ndarray:
    return pd.to_numeric(s, errors="coerce").to_numpy("float64")


#: the regions NHDPlus V2 does not cover (``no_v2.json`` in the V2 root): HR units that hold only
#: Canadian land or open lake water, so EASI has no covered streams there
NO_V2_FILE = "no_v2.json"


class NoV2Network(RuntimeError):
    """The region holds no NHDPlus V2 network flowline."""


def no_v2_regions(root: Path = V2_ROOT) -> set:
    path = Path(root) / NO_V2_FILE
    return set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()


def record_no_v2(hu4s, root: Path = V2_ROOT) -> None:
    path = Path(root) / NO_V2_FILE
    path.write_text(json.dumps(sorted(no_v2_regions(root) | set(hu4s)), indent=1), encoding="utf-8", newline="\n")


def convert_v2_region(hu4: str, root: Path = V2_ROOT, *, line_tol: float = 0.0,
                      keep_ends: float = config.V2_LINE_KEEP_ENDS_M, log: Callable = log_default) -> dict:
    """One NHDPlus V2 region in the version 2 format. Flowlines are kept whole (``line_tol`` 0, the
    owner's D2 decision; the coordinates still round to 1e-5 degree), with ``v2attrs2_<hu4>``
    beside them: the gage-adjusted EROM flows EASI reads and the sinuosity EASI computes, from the
    original geometry."""
    import pyarrow.parquet as pq
    import pyogrio
    t0 = time.time()
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    fl = pyogrio.read_dataframe(V2_GDB, layer="NHDFlowline_Network", columns=LINE_FIELDS,
                                where=f"REACHCODE LIKE '{hu4}%'", force_2d=True)
    fl = fl[fl.geometry.notna() & ~fl.geometry.is_empty].reset_index(drop=True)
    if not len(fl):
        raise NoV2Network(f"{hu4}: no V2 network flowlines")
    fl = fl.iloc[_hilbert_order(fl.geometry.to_numpy())].reset_index(drop=True)
    to5070, to4269 = _transformers()
    lgeo = shapely.force_2d(fl.geometry.to_numpy())
    slope = _num(fl["SLOPE"])
    attrs = {
        "nhdplusid": fl["COMID"].to_numpy("int64"),
        "gnis_name": [None if (v is None or (isinstance(v, float) and np.isnan(v)) or not str(v).strip())
                      else str(v).strip() for v in fl["GNIS_NAME"].tolist()],
        "reachcode": [None if v is None else str(v) for v in fl["REACHCODE"].tolist()],
        "lengthkm": _num(fl["LENGTHKM"]), "totdasqkm": _num(fl["TotDASqKM"]),
        "slope": np.where(slope < -9000, np.nan, slope),          # -9998: no slope computed
        "fcode": np.nan_to_num(_num(fl["FCODE"]), nan=0).astype(np.int64),
        "streamorde": _num(fl["StreamOrde"]),
        "hydroseq": np.nan_to_num(_num(fl["Hydroseq"]), nan=0).round().astype(np.int64),
        "dnhydroseq": np.nan_to_num(_num(fl["DnHydroseq"]), nan=0).round().astype(np.int64),
        "uphydroseq": np.nan_to_num(_num(fl["UpHydroseq"]), nan=0).round().astype(np.int64),
        # NLDI's upstream navigation climbs through minor divergences; the V2 walk follows it
        "dnminorhyd": np.nan_to_num(_num(fl["DnMinorHyd"]), nan=0).round().astype(np.int64),
        "qama": _num(fl["QA_MA"]),
    }
    lines_m = _project(lgeo, to5070)
    simple = _project(simplify_lines(lines_m, line_tol, keep_ends), to4269) if line_tol > 0 else lgeo
    ltable = fmt2.lines_table(attrs, simple)
    lfile = data_dir / fmt2.lines_file(hu4)
    fmt2.write(ltable, lfile, "lines")
    from .extras import sinuosity
    v2attrs = dict((f.lower(), pa.array(_num(fl[f]), mask=np.isnan(_num(fl[f])))) for f in EROM_FIELDS)
    sin = sinuosity(lines_m)
    v2attrs["sinuosity"] = pa.array(sin, mask=np.isnan(sin))
    efile = data_dir / f"v2attrs2_{hu4}.parquet"
    pq.write_table(pa.table(v2attrs), efile, compression="zstd", compression_level=19)
    old_erom = data_dir / f"erom2_{hu4}.parquet"
    if old_erom.exists():
        old_erom.unlink()
    # catchments: the region box, then the flowlines' COMIDs
    lb = shapely.total_bounds(lgeo)
    cat = pyogrio.read_dataframe(V2_GDB, layer="Catchment", columns=["FEATUREID"],
                                 bbox=(lb[0] - 0.05, lb[1] - 0.05, lb[2] + 0.05, lb[3] + 0.05), force_2d=True)
    cat["FEATUREID"] = np.round(cat["FEATUREID"].to_numpy("float64")).astype(np.int64)
    cat = cat[cat["FEATUREID"].isin(set(attrs["nhdplusid"].tolist())) & cat.geometry.notna()
              & ~cat.geometry.is_empty].drop_duplicates("FEATUREID").reset_index(drop=True)
    cgeo = shapely.force_2d(cat.geometry.to_numpy())
    corder = _hilbert_order(cgeo)
    cgeo, cids = cgeo[corder], cat["FEATUREID"].to_numpy("int64")[corder]
    parts = shapely.get_parts(cgeo)
    multi = shapely.multipolygons(parts, indices=np.repeat(np.arange(len(cgeo)), shapely.get_num_geometries(cgeo)))
    _, coords, (ring_off, poly_off, row_off) = shapely.to_ragged_array(multi)
    g = grid.detect(coords[:, 0], coords[:, 1])
    if g is None or g.cell != 30.0:
        raise arcs.NotGridAligned(f"{hu4}: V2 catchments fit {g}, not the 30 m grid")
    ix, iy, moved = g.to_cells(coords[:, 0], coords[:, 1])
    enc = arcs.encode(ix, iy, ring_off, poly_off, row_off)
    cells_geoms = shapely.from_ragged_array(shapely.GeometryType.MULTIPOLYGON,
                                            np.column_stack([ix, iy]).astype(np.float64), (ring_off, poly_off, row_off))
    check = check_exact(enc, cells_geoms)
    if check["area_mismatch"] or check["unequal"]:
        raise RuntimeError(f"{hu4}: V2 catchments do not decode exactly: {check}")
    row_vert_off = ring_off[poly_off[row_off]]
    bbox_deg = np.column_stack([np.minimum.reduceat(coords[:, 0], row_vert_off[:-1]),
                                np.minimum.reduceat(coords[:, 1], row_vert_off[:-1]),
                                np.maximum.reduceat(coords[:, 0], row_vert_off[:-1]),
                                np.maximum.reduceat(coords[:, 1], row_vert_off[:-1])])
    cats_t, arcs_t, steps = fmt2.catchment_tables(enc, cids, bbox_deg)
    cfile, afile, sfile = data_dir / fmt2.cats_file(hu4), data_dir / fmt2.arcs_file(hu4), data_dir / fmt2.steps_file(hu4)
    fmt2.write(cats_t, cfile, "cats")
    fmt2.write(arcs_t, afile, "arcs")
    sfile.write_bytes(steps)
    back, _, _ = fmt2.read_encoded(cfile, afile, sfile)
    if not (np.array_equal(back.area2, enc.area2) and np.array_equal(back.runs, enc.runs)):
        raise RuntimeError(f"{hu4}: the written V2 catchment files do not read back to the encoding")
    llb = [ltable["xmin"].to_numpy().min(), ltable["ymin"].to_numpy().min(),
           ltable["xmax"].to_numpy().max(), ltable["ymax"].to_numpy().max()]
    bounds = [min(llb[0] / fmt.SCALE, float(bbox_deg[:, 0].min())), min(llb[1] / fmt.SCALE, float(bbox_deg[:, 1].min())),
              max(llb[2] / fmt.SCALE, float(bbox_deg[:, 2].max())), max(llb[3] / fmt.SCALE, float(bbox_deg[:, 3].max()))]
    hs = attrs["hydroseq"]
    hs_nz = hs[hs != 0]
    entry = {
        "vpu": hu4, "package": {"name": V2_GDB.name, "layer": "NHDFlowline_Network", "where": f"REACHCODE LIKE '{hu4}%'"},
        "bounds": [round(b, 5) for b in bounds],
        "id_range": [int(attrs["nhdplusid"].min()), int(attrs["nhdplusid"].max())],
        "hydroseq_range": [int(hs_nz.min()), int(hs_nz.max())] if hs_nz.size else None,
        "grid": g.to_dict(), "vpuid": hu4, "ftype_from_fcode": True,
        "lines": dict(_file_entry(lfile, ltable.num_rows), tolerance_m=line_tol, keep_ends_m=keep_ends),
        "catchments": _file_entry(cfile, cats_t.num_rows), "arcs": _file_entry(afile, arcs_t.num_rows),
        "steps": {"file": sfile.name, "bytes": sfile.stat().st_size, "values": int(len(enc.runs)), "sha256": _sha256(sfile)},
        "v2attrs": {"file": efile.name, "bytes": efile.stat().st_size},
    }
    stats = {"lines": int(ltable.num_rows), "catchments": int(enc.n_rows), "grid_moved_m": round(moved, 6),
             "check": check, "seconds": round(time.time() - t0),
             "bytes": sum(p.stat().st_size for p in (lfile, cfile, afile, sfile, efile))}
    recipe = {"format": fmt2.FORMAT_VERSION, "scale": fmt.SCALE, "line_tolerance_m": line_tol,
              "line_keep_ends_m": keep_ends, "catchments": "exact", "source": "NHDPlus V2.1 national seamless"}
    part = {"vpu": hu4, "entry": entry, "stats": stats, "recipe": recipe, "format": fmt2.FORMAT_VERSION,
            "built": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (root / "parts").mkdir(parents=True, exist_ok=True)
    (root / "parts" / f"{hu4}.json").write_text(json.dumps(part, indent=1), encoding="utf-8")
    log(f"[V2 {hu4}] {stats['lines']} flowlines, {stats['catchments']} catchments exact, "
        f"{stats['bytes'] / 1e6:.2f} MB in {stats['seconds']} s")
    return part
