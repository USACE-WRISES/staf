"""A tiny two-VPU slim dataset for the tests.

VPU 9901 holds a Y-shaped network near (-77.0, 39.0):

    reach 4 (hs 1004) -> reach 2 (hs 1002) -> reach 1 (hs 1001, outlet)
                         reach 3 (hs 1003) -> reach 1

plus an L-shaped line (reach 6) whose bounding box covers the box
``CORNER_BOX`` without its geometry touching it. VPU 9902 holds reach 5
(hs 2005), whose ``dnhydroseq`` is reach 4's hydroseq: a parent across the VPU
boundary. Every reach has a 0.01 degree square catchment.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from hrslim import fmt

X0, Y0 = -77.0, 39.0
D = 0.01
ID = dict((n, 99010000000000 + n) for n in (1, 2, 3, 4, 6))
ID[5] = 99020000000005
CORNER_BOX = (X0 + 0.0405, Y0 + 0.0405, X0 + 0.0445, Y0 + 0.0445)


def _square(i: int, j: int):
    return shapely.box(X0 + i * D, Y0 + j * D, X0 + (i + 1) * D, Y0 + (j + 1) * D)


def _line_in(i: int, j: int, *, to: tuple[float, float]):
    cx, cy = X0 + (i + 0.5) * D, Y0 + (j + 0.5) * D
    return shapely.LineString([(cx, cy), to])


#: reach -> (cell, hydroseq, dnhydroseq, uphydroseq, name, totdasqkm)
REACHES = {
    1: ((0, 0), 1001, 0, 1002, "Outlet Run", 4.6),
    2: ((0, 1), 1002, 1001, 1004, "Upper Run", 2.3),
    3: ((1, 0), 1003, 1001, 0, None, 1.1),
    4: ((0, 2), 1004, 1002, 2005, "Upper Run", 1.6),
    5: ((0, 3), 2005, 1004, 0, "Border Creek", 0.6),
    6: ((4, 4), 1006, 0, 0, "Corner Ditch", 1.0),
}


def _geom(n: int):
    (i, j), *_ = REACHES[n]
    if n == 1:
        return _line_in(i, j, to=(X0 + 0.5 * D, Y0))
    if n in (2, 4, 5):
        return _line_in(i, j, to=(X0 + 0.5 * D, Y0 + j * D))
    if n == 3:
        return _line_in(i, j, to=(X0 + D, Y0 + 0.5 * D))
    # an L hugging two sides of the cell, around CORNER_BOX's corner
    return shapely.MultiLineString([[(X0 + 0.0400, Y0 + 0.0450), (X0 + 0.0400, Y0 + 0.0400)],
                                    [(X0 + 0.0400, Y0 + 0.0400), (X0 + 0.0450, Y0 + 0.0400)]])


def _lines_table(reaches: list[int], vpu: str) -> pa.Table:
    cols = {
        "nhdplusid": pa.array([ID[n] for n in reaches], type=pa.int64()),
        "gnis_name": pa.array([REACHES[n][4] for n in reaches], type=pa.string()),
        "reachcode": pa.array([f"0206000500{n:04d}" for n in reaches], type=pa.string()),
        "lengthkm": pa.array([0.9 for _ in reaches], type=pa.float64()),
        "totdasqkm": pa.array([REACHES[n][5] for n in reaches], type=pa.float64()),
        "slope": pa.array([0.001 for _ in reaches], type=pa.float64()),
        "fcode": pa.array([46006 for _ in reaches], type=pa.int32()),
        "ftype": pa.array([460 for _ in reaches], type=pa.int32()),
        "streamorde": pa.array([1 for _ in reaches], type=pa.int16()),
        "hydroseq": pa.array([REACHES[n][1] for n in reaches], type=pa.int64()),
        "uphydroseq": pa.array([REACHES[n][3] for n in reaches], type=pa.int64()),
        "dnhydroseq": pa.array([REACHES[n][2] for n in reaches], type=pa.int64()),
        "vpuid": pa.array([vpu for _ in reaches], type=pa.string()),
        "qama": pa.array([None if n == 3 else 1.5 for n in reaches], type=pa.float64()),
    }
    cols.update(fmt.encode_lines([_geom(n) for n in reaches]))
    return pa.table(cols)


def _catchments_table(reaches: list[int]) -> pa.Table:
    cols = {"nhdplusid": pa.array([ID[n] for n in reaches], type=pa.int64())}
    cols.update(fmt.encode_polygons([_square(*REACHES[n][0]) for n in reaches]))
    return pa.table(cols)


def build(root: Path, tolerances=(10.0, 20.0)) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    vpus = {"9901": [1, 2, 3, 4, 6], "9902": [5]}
    manifest = {"format": fmt.FORMAT_VERSION,
                "recipe": {"scale": fmt.SCALE, "line_tolerance_m": 5.0,
                           "catchment_tolerances_m": list(tolerances),
                           "default_catchment_tolerance_m": tolerances[-1]},
                "vpus": {}, "built": "2026-10-01T00:00:00+00:00"}
    for vpu, reaches in vpus.items():
        lt = _lines_table(reaches, vpu)
        lfile = root / fmt.lines_file(vpu)
        fmt.write_table(lt, lfile, 2)                     # tiny row groups exercise pruning
        cats = {}
        for tol in tolerances:
            cfile = root / fmt.catchments_file(vpu, tol)
            fmt.write_table(_catchments_table(reaches), cfile, 2)
            cats[f"{tol:g}"] = {"file": cfile.name, "bytes": cfile.stat().st_size,
                                "rows": len(reaches), "row_groups": pq.ParquetFile(cfile).num_row_groups}
        geoms = [_geom(n) for n in reaches] + [_square(*REACHES[n][0]) for n in reaches]
        bounds = shapely.total_bounds(np.asarray(geoms, dtype=object)).tolist()
        hs = [REACHES[n][1] for n in reaches]
        dn = [REACHES[n][2] for n in reaches if REACHES[n][2]]
        entry = {"vpu": vpu, "bounds": bounds,
                 "id_range": [min(ID[n] for n in reaches), max(ID[n] for n in reaches)],
                 "hydroseq_range": [min(hs), max(hs)],
                 "dnhydroseq_range": [min(dn), max(dn)] if dn else None,
                 "lines": {"file": lfile.name, "bytes": lfile.stat().st_size, "rows": len(reaches),
                           "row_groups": pq.ParquetFile(lfile).num_row_groups, "tolerance_m": 5.0},
                 "catchments": cats, "qa": None}
        if vpu == "9901":
            box = [X0, Y0, X0 + D, Y0 + D]
            qa = pa.table({"kind": pa.array(["line", "catchment"]),
                           "nhdplusid": pa.array([ID[1], ID[1]], type=pa.int64()),
                           "wkb": pa.array(shapely.to_wkb([_geom(1), _square(0, 0)]).tolist(),
                                           type=pa.binary())})
            qfile = root / fmt.qa_file(vpu)
            pq.write_table(qa, qfile, row_group_size=10)
            entry["qa"] = {"file": qfile.name, "bytes": qfile.stat().st_size, "boxes": [box], "rows": 2}
        manifest["vpus"][vpu] = entry
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return root
