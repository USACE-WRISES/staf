"""The slim NHDPlus HR file format, version 2.

One set of Parquet files per region (a USGS processing unit: an HU4, or an HU8 in
Alaska), plus one national table:

- ``lines2_<vpu>.parquet``: the network flowlines, Hilbert-sorted, geometry
  Douglas-Peucker simplified (2 m) and stored as whole 1e-5 degree units like
  version 1. The network is stored as row offsets: ``dn_step`` and ``up_step``
  point to the downstream and upstream-mainstem rows in the same region (0 when
  there is none there); ``dn_out`` and ``up_out`` hold the target's hydroseq when
  it lies in another region (null otherwise). ``ftype`` (``fcode // 100``),
  ``vpuid`` (the region's) and ``innetwork`` (always 1) are rebuilt on read; the
  manifest says when a region needs a stored ``ftype`` or text reach codes.
- ``cats2_<vpu>.parquet``: one row per network catchment: its NHDPlusID, exact
  area in grid cells (``cells``), a bounding box in 1e-5 degree units, and its
  rings as references to shared borders (``hrslim.arcs``).
- ``arcs2_<vpu>.parquet`` and ``steps2_<vpu>.bin``: the shared borders: start
  cell, first axis and number of moves per border; the moves themselves (one signed
  run per corner, almost all within a byte) packed as zigzag varints and
  compressed with zstd. Rings are stored with their catchment on the left, so the
  catchments on either side of a border are rebuilt from the references on load
  (and the areas too, for files without ``cells``).
- ``links2.parquet``: every flowline whose downstream reach lies in another region
  (target hydroseq, region, row), so an upstream walk can cross regions.

Catchments are exact: each region's grid (``hrslim.grid``) is in the manifest, and
decoded vertices sit within a millimetre of the USGS originals.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import arcs as arcs_mod
from . import fmt

FORMAT_VERSION = 2
LINES_ROW_GROUP = 4096
CATS_ROW_GROUP = 4096
ARCS_ROW_GROUP = 1 << 17
ZSTD_LEVEL = 19
LINE_COLUMNS = ("nhdplusid", "gnis_name", "reachcode", "lengthkm", "totdasqkm", "slope",
                "fcode", "streamorde", "hydroseq", "dn_step", "up_step", "dn_out", "up_out", "qama")
TOPOLOGY_COLUMNS = ("nhdplusid", "hydroseq", "dn_step", "up_step", "dn_out", "up_out", "totdasqkm")
#: present when the source routes minor divergences (NHDPlus V2 ``DnMinorHyd``): the walk then also
#: climbs from a minor-divergence branch to the line that splits into it, as NLDI's navigation does
MINOR_COLUMNS = ("dnminor_step", "dnminor_out")


def lines_file(vpu: str) -> str:
    return f"lines2_{vpu}.parquet"


def cats_file(vpu: str) -> str:
    return f"cats2_{vpu}.parquet"


def arcs_file(vpu: str) -> str:
    return f"arcs2_{vpu}.parquet"


def steps_file(vpu: str) -> str:
    return f"steps2_{vpu}.bin"


LINKS_FILE = "links2.parquet"


# --------------------------------------------------------------------------- #
# writing (the builder)
# --------------------------------------------------------------------------- #
def network_steps(hydroseq: np.ndarray, dnhydroseq: np.ndarray, uphydroseq: np.ndarray):
    """Row offsets to the downstream and upstream-mainstem rows in this region,
    and the hydroseq of targets that lie elsewhere (0 = none)."""
    hs = np.asarray(hydroseq, dtype=np.int64)
    order = np.argsort(hs, kind="stable")
    shs = hs[order]

    def resolve(target):
        target = np.asarray(target, dtype=np.int64)
        pos = np.searchsorted(shs, target)
        pos_c = np.minimum(pos, len(shs) - 1)
        found = (target != 0) & (len(shs) > 0) & (shs[pos_c] == target)
        row = np.where(found, order[pos_c], -1)
        step = np.where(found, row - np.arange(len(target)), 0).astype(np.int64)
        outside = np.where(~found & (target != 0), target, 0)
        return step, outside

    dn_step, dn_out = resolve(dnhydroseq)
    up_step, up_out = resolve(uphydroseq)
    return dn_step, up_step, dn_out, up_out


def reachcodes_as_int(codes: list) -> Optional[np.ndarray]:
    """Reach codes as int64 when every one is 14 digits (else None: keep text)."""
    out = np.zeros(len(codes), dtype=np.int64)
    for i, c in enumerate(codes):
        if c is None or len(c) != 14 or not c.isdigit():
            return None
        out[i] = int(c)
    return out


def lines_table(attrs: dict, geoms_deg) -> pa.Table:
    """The lines table from Hilbert-sorted attribute arrays (``nhdplusid``,
    ``gnis_name``, ``reachcode``, ``lengthkm``, ``totdasqkm``, ``slope``, ``fcode``,
    ``streamorde``, ``hydroseq``, ``dnhydroseq``, ``uphydroseq``, ``qama``) and
    simplified geometry in degrees."""
    dn_step, up_step, dn_out, up_out = network_steps(attrs["hydroseq"], attrs["dnhydroseq"], attrs["uphydroseq"])
    minor = None
    if "dnminorhyd" in attrs:
        minor = network_steps(attrs["hydroseq"], attrs["dnminorhyd"], np.zeros(len(attrs["hydroseq"])))
    codes = list(attrs["reachcode"])
    as_int = reachcodes_as_int(codes)
    cols = {
        "nhdplusid": pa.array(np.asarray(attrs["nhdplusid"], dtype=np.int64)),
        "gnis_name": pa.array(list(attrs["gnis_name"]), type=pa.string()),
        "reachcode": pa.array(as_int) if as_int is not None else pa.array(codes, type=pa.string()),
        # float64 throughout: the source values as the services answer them (float32 cannot hold
        # NHDPlus's 0.00001 slope floor, and a value on an app threshold could flip)
        "lengthkm": pa.array(np.asarray(attrs["lengthkm"], dtype=np.float64), type=pa.float64()),
        "totdasqkm": pa.array(np.asarray(attrs["totdasqkm"], dtype=np.float64)),
        "slope": pa.array(np.asarray(attrs["slope"], dtype=np.float64)),
        "fcode": pa.array(np.asarray(attrs["fcode"]), type=pa.int32()),
        "streamorde": pa.array(np.asarray(attrs["streamorde"], dtype=np.float64), from_pandas=True).cast(pa.int8()),
        "hydroseq": pa.array(np.asarray(attrs["hydroseq"], dtype=np.int64)),
        "dn_step": pa.array(dn_step.astype(np.int32)),
        "up_step": pa.array(up_step.astype(np.int32)),
        "dn_out": pa.array(dn_out, mask=dn_out == 0, type=pa.int64()),
        "up_out": pa.array(up_out, mask=up_out == 0, type=pa.int64()),
        "qama": pa.array(np.asarray(attrs["qama"], dtype=np.float64), from_pandas=True),
    }
    if minor is not None:
        cols["dnminor_step"] = pa.array(minor[0].astype(np.int32))
        cols["dnminor_out"] = pa.array(minor[2], mask=minor[2] == 0, type=pa.int64())
    cols.update(fmt.encode_lines(geoms_deg))
    return pa.table(cols)


_STEPS_MAGIC = b"HRSTEPS2"


def pack_steps(runs: np.ndarray) -> bytes:
    """Signed runs as zigzag varints, zstd-compressed, behind a small header."""
    v = np.asarray(runs, dtype=np.int64)
    zz = ((v << 1) ^ (v >> 63)).astype(np.uint64)
    nb = np.ones(len(zz), dtype=np.int64)
    for k in range(1, 10):
        nb += (zz >= np.uint64(1 << (7 * k))).astype(np.int64)
    total = int(nb.sum())
    owner = np.repeat(np.arange(len(zz)), nb)
    k = np.arange(total) - np.repeat(np.concatenate([[0], np.cumsum(nb)[:-1]]), nb)
    byte = (zz[owner] >> (7 * k).astype(np.uint64)) & np.uint64(0x7F)
    byte |= (k < nb[owner] - 1).astype(np.uint64) << np.uint64(7)
    raw = byte.astype(np.uint8).tobytes()
    payload = pa.Codec("zstd", compression_level=ZSTD_LEVEL).compress(pa.py_buffer(raw)).to_pybytes()
    return _STEPS_MAGIC + np.array([len(v), len(raw)], dtype="<u8").tobytes() + payload


def unpack_steps(data: bytes) -> np.ndarray:
    if data[:8] != _STEPS_MAGIC:
        raise ValueError("not a steps file")
    n, raw_len = np.frombuffer(data[8:24], dtype="<u8")
    raw = pa.Codec("zstd").decompress(pa.py_buffer(data[24:]), decompressed_size=int(raw_len))
    b = np.frombuffer(raw, dtype=np.uint8)
    cont = np.flatnonzero(b >= 0x80)                  # bytes followed by more of their value
    if not cont.size:                                 # every value fits in one byte
        zz = b.astype(np.int64)
    else:                                             # almost every value does: patch the rest
        tail = cont + 1
        keep = np.ones(len(b), dtype=bool)
        keep[tail] = False
        zz = b[keep].astype(np.int64)
        heads = cont[~np.isin(cont, tail)]            # first bytes of the longer values
        at = heads - np.searchsorted(tail, heads)     # their place among the values
        val = (b[heads] & 0x7F).astype(np.int64)
        live, pos, shift = np.arange(len(heads)), heads, 7
        while live.size:
            pos = pos + 1
            nb = b[pos]
            val[live] |= (nb & 0x7F).astype(np.int64) << shift
            more = nb >= 0x80
            live, pos, shift = live[more], pos[more], shift + 7
        zz[at] = val
    if len(zz) != int(n):
        raise ValueError("steps file is truncated")
    return (zz >> 1) ^ -(zz & 1)


def catchment_tables(enc: arcs_mod.Encoded, ids: np.ndarray, bbox_deg: np.ndarray) -> tuple[pa.Table, pa.Table, bytes]:
    """``(cats, arcs, steps)`` from an encoding (rows already Hilbert-sorted);
    ``bbox_deg`` is ``(n, 4)`` in degrees."""
    bbox_deg = np.asarray(bbox_deg, dtype=np.float64)
    q = np.column_stack([np.floor(bbox_deg[:, 0] * fmt.SCALE), np.floor(bbox_deg[:, 1] * fmt.SCALE),
                         np.ceil(bbox_deg[:, 2] * fmt.SCALE), np.ceil(bbox_deg[:, 3] * fmt.SCALE)]).astype(np.int32)

    def lst(off, values, typ):
        return pa.ListArray.from_arrays(pa.array(np.asarray(off, dtype=np.int32)), pa.array(values, type=typ))

    ring_counts = np.diff(enc.ring_off)
    poly_counts = np.diff(enc.poly_off)
    row_ring_off = enc.poly_off[enc.row_off]
    row_ref_off = enc.ring_off[row_ring_off]
    if np.any(enc.area2 % 2) or np.any(enc.area2 // 2 >= 2 ** 31):
        raise ValueError("catchment areas are not whole int32 cell counts")
    cats = pa.table({
        "nhdplusid": pa.array(np.asarray(ids, dtype=np.int64)),
        "cells": pa.array((enc.area2 // 2).astype(np.int32)),
        "xmin": pa.array(q[:, 0]), "ymin": pa.array(q[:, 1]), "xmax": pa.array(q[:, 2]), "ymax": pa.array(q[:, 3]),
        "refs": lst(row_ref_off, enc.refs.astype(np.int32), pa.int32()),
        "rings": lst(row_ring_off, ring_counts.astype(np.int32), pa.int32()),
        "polys": lst(enc.row_off, poly_counts.astype(np.int32), pa.int32()),
    })
    arcs = pa.table({
        "x0": pa.array(enc.x0.astype(np.int32)), "y0": pa.array(enc.y0.astype(np.int32)),
        "axis": pa.array(enc.axis.astype(np.uint8)),
        "n": pa.array(np.diff(enc.run_off).astype(np.int32)),
    })
    return cats, arcs, pack_steps(enc.runs)


def links_table(regions: list) -> pa.Table:
    """The national cross-region links from ``[(vpu, lines table), ...]``: every row
    whose downstream target lies in another region (target hydroseq, region, row)."""
    import pyarrow.compute as pc
    targets, vpus, rows = [], [], []
    for vpu, table in regions:
        for name in ("dn_out", "dnminor_out"):
            if name not in table.column_names:
                continue
            col = table.column(name)
            idx = np.nonzero(pc.is_valid(col).to_numpy(zero_copy_only=False))[0]
            targets.append(col.fill_null(0).to_numpy()[idx].astype(np.int64))
            vpus.extend([vpu] * len(idx))
            rows.append(idx.astype(np.int32))
    return pa.table({"target": pa.array(np.concatenate(targets) if targets else np.empty(0, np.int64)),
                     "vpu": pa.array(vpus, type=pa.string()),
                     "row": pa.array(np.concatenate(rows) if rows else np.empty(0, np.int32))})


def write(table: pa.Table, where, kind: str, row_group: Optional[int] = None) -> None:
    """Write one v2 table with its encodings (``kind``: lines, cats or arcs;
    ``row_group`` overrides the row group size, for tests)."""
    if kind == "lines":
        enc = {"x.list.element": "DELTA_BINARY_PACKED", "y.list.element": "DELTA_BINARY_PACKED",
               "parts.list.element": "DELTA_BINARY_PACKED"}
        enc.update((c, "DELTA_BINARY_PACKED") for c in fmt.BBOX_COLUMNS + ("nhdplusid", "hydroseq", "dn_step", "up_step"))
        dict_cols = [c for c in ("gnis_name", "fcode", "streamorde") if c in table.column_names]
        if pa.types.is_string(table.schema.field("reachcode").type):
            dict_cols.append("reachcode")
        rg = LINES_ROW_GROUP
    elif kind == "cats":
        enc = dict((c, "DELTA_BINARY_PACKED") for c in fmt.BBOX_COLUMNS)
        enc.update({"refs.list.element": "DELTA_BINARY_PACKED", "rings.list.element": "DELTA_BINARY_PACKED",
                    "polys.list.element": "DELTA_BINARY_PACKED", "nhdplusid": "DELTA_BINARY_PACKED"})
        if "cells" in table.column_names:
            enc["cells"] = "DELTA_BINARY_PACKED"
        dict_cols = []
        rg = CATS_ROW_GROUP
    elif kind == "arcs":
        enc = {"x0": "DELTA_BINARY_PACKED", "y0": "DELTA_BINARY_PACKED", "n": "DELTA_BINARY_PACKED"}
        dict_cols = []
        rg = ARCS_ROW_GROUP
    else:
        raise ValueError(kind)
    rg = row_group or rg
    pq.write_table(table, where, row_group_size=rg, compression="zstd", compression_level=ZSTD_LEVEL,
                   use_dictionary=dict_cols, column_encoding=enc, write_statistics=True)


# --------------------------------------------------------------------------- #
# reading (the reader)
# --------------------------------------------------------------------------- #
def read_encoded(cats_path, arcs_path, steps_path) -> tuple[arcs_mod.Encoded, np.ndarray, np.ndarray]:
    """``(encoding, catchment ids, bbox in 1e-5 degree units (n, 4))`` of a region;
    side owners are rebuilt from the references, areas read from ``cells`` (or
    rebuilt from the borders in files without it)."""
    a = pq.read_table(arcs_path)
    c = pq.read_table(cats_path)
    with open(steps_path, "rb") as fh:
        runs = unpack_steps(fh.read())
    n = a.column("n").to_numpy().astype(np.int64)
    refs, _ = fmt._flat(c.column("refs"))
    ring_counts, row_ring_off = fmt._flat(c.column("rings"))
    poly_counts, row_poly_off = fmt._flat(c.column("polys"))
    bbox = np.column_stack([c.column(name).to_numpy() for name in fmt.BBOX_COLUMNS]).astype(np.int64)
    n_rows = c.num_rows
    enc = arcs_mod.Encoded(
        x0=a.column("x0").to_numpy().astype(np.int64), y0=a.column("y0").to_numpy().astype(np.int64),
        axis=a.column("axis").to_numpy().astype(np.uint8),
        run_off=np.concatenate([[0], np.cumsum(n)]).astype(np.int64), runs=runs,
        left=np.empty(0, np.int64), right=np.empty(0, np.int64), refs=refs.astype(np.int64),
        ring_off=np.concatenate([[0], np.cumsum(ring_counts, dtype=np.int64)]),
        poly_off=np.concatenate([[0], np.cumsum(poly_counts, dtype=np.int64)]),
        row_off=row_poly_off.astype(np.int64), area2=np.zeros(n_rows, np.int64),
        bbox=np.zeros((n_rows, 4), dtype=np.int64))
    enc.left, enc.right = arcs_mod.owners_from_refs(enc)
    if "cells" in c.column_names:
        enc.area2 = c.column("cells").to_numpy().astype(np.int64) * 2
    else:
        enc.area2 = arcs_mod.area2_from_arcs(enc)
    return enc, c.column("nhdplusid").to_numpy().astype(np.int64), bbox
