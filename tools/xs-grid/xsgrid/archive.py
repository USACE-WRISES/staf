"""The archive and metrics files: schemas, writers and the transect reader."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import codec, config
from .derive import BOOL_KEYS, FLOAT_KEYS

ID_FIELDS = [("nhdplusid", pa.int64()), ("part", pa.int16()), ("k", pa.int32()), ("n_sec", pa.int32()),
             ("s_m", pa.float64())]
ARCHIVE_SCHEMA = pa.schema(ID_FIELDS + [
    ("x", pa.float64()), ("y", pa.float64()), ("nx", pa.float64()), ("ny", pa.float64()),
    ("da_sqkm", pa.float64()), ("division", pa.string()), ("bf_width_m", pa.float64()),
    ("bf_depth_m", pa.float64()), ("bf_area_m2", pa.float64()), ("bf_extrapolated", pa.bool_()),
    ("wide_m", pa.float64()), ("res_m", pa.int8()), ("n_pts", pa.int16()), ("n_finite", pa.int16()),
    ("source", pa.string()), ("tiles", pa.string()), ("z", pa.list_(pa.int32())),
])
METRICS_SCHEMA = pa.schema(ID_FIELDS + [("res_m", pa.int8()), ("status", pa.string())]
                           + [(k, pa.float64()) for k in FLOAT_KEYS]
                           + [(k, pa.bool_()) for k in BOOL_KEYS]
                           + [("bank_detection", pa.string()), ("thalweg_station_m", pa.float64()),
                              ("n_points_thinned", pa.int16()), ("verified", pa.bool_())])
BSS = ("s_m", "x", "y", "nx", "ny", "da_sqkm", "bf_width_m", "bf_depth_m", "bf_area_m2", "wide_m")
DICT = ("division", "source", "tiles", "status", "bank_detection")


def metadata(extra: dict | None = None) -> dict:
    from .engine import geomorph
    md = {"format": config.FORMAT, "sampling_version": config.SAMPLING_VERSION,
          "sampling_rule": config.SAMPLING_RULE,
          "codec": "z: float32 samples as order-preserving uint32, second differences mod 2^32, int32",
          "spacing_m": config.SPACING_M, "quality_rules": getattr(geomorph, "QUALITY_RULES", None)}
    md.update(extra or {})
    return {"xsgrid": json.dumps(md, sort_keys=True)}


def write(table: pa.Table, path: Path, md: dict | None = None) -> int:
    """Write ``table`` atomically (temp file, then rename); returns bytes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), **(md or {})})
    names = set(table.column_names)
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp, compression="zstd", compression_level=config.ZSTD_LEVEL,
                   use_dictionary=[c for c in DICT if c in names],
                   column_encoding=dict((c, "BYTE_STREAM_SPLIT") for c in BSS if c in names),
                   write_statistics=False, row_group_size=1 << 18)
    os.replace(tmp, path)
    return path.stat().st_size


def transects(table: pa.Table) -> list:
    """Every row's float32 samples, decoded."""
    z = table.column("z").combine_chunks()
    offs = z.offsets.to_numpy()
    vals = z.values.to_numpy()
    return [codec.decode(vals[offs[i]:offs[i + 1]]) for i in range(len(offs) - 1)]


def concat(paths: list, out: Path, md: dict | None = None) -> int:
    """Merge part files into one file (row groups streamed, same encodings)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    writer = None
    try:
        for p in paths:
            t = pq.read_table(p)
            if writer is None:
                names = set(t.column_names)
                schema = t.schema.with_metadata({**(t.schema.metadata or {}), **(md or {})})
                writer = pq.ParquetWriter(tmp, schema, compression="zstd",
                                          compression_level=config.ZSTD_LEVEL,
                                          use_dictionary=[c for c in DICT if c in names],
                                          column_encoding=dict((c, "BYTE_STREAM_SPLIT") for c in BSS if c in names),
                                          write_statistics=False)
            writer.write_table(t.replace_schema_metadata(schema.metadata), row_group_size=1 << 18)
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        return 0
    os.replace(tmp, out)
    return out.stat().st_size


def as_float32_lists(z32s: list) -> pa.Array:
    """The archive's ``z`` column from float32 transects."""
    codes = [codec.encode(z) if z is not None else np.zeros(0, dtype=np.int32) for z in z32s]
    lens = np.asarray([len(c) for c in codes], dtype=np.int32)
    offs = np.r_[0, np.cumsum(lens)].astype(np.int32)
    vals = np.concatenate(codes) if codes else np.zeros(0, dtype=np.int32)
    return pa.ListArray.from_arrays(pa.array(offs), pa.array(vals, type=pa.int32()))
