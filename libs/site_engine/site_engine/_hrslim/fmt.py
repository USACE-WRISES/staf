"""The slim NHDPlus HR file format.

One set of Parquet files per VPU (the USGS processing unit: an HU4, or an HU8 in
Alaska):

- ``lines_<vpu>.parquet``: the network flowlines (``innetwork = 1``) with the 15
  fields the STAF site engine reads from the USGS layer 3, geometry simplified
  at the recipe's line tolerance;
- ``catchments<tol>_<vpu>.parquet``: the catchments of those flowlines, coverage-
  simplified at ``tol`` metres so neighbours still share their edges exactly;
- ``qa_<vpu>.parquet`` (optional): the original, full-precision geometry inside a
  few small boxes, for side-by-side checks in the viewer.

Coordinates are whole numbers of 1e-5 degree (about 1 m) of the NAD83 longitude
and latitude the USGS packages store; the USGS service serves the same numbers as
EPSG:4326. They live in two ``list<int32>`` columns, ``x`` and ``y``, which Parquet
writes with DELTA_BINARY_PACKED and zstd, so a vertex costs a byte or two. Rows
are sorted along a Hilbert curve, and four int32 columns carry each row's bounding
box, so row-group statistics say which row groups a map view touches.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

FORMAT_VERSION = 1
#: Coordinate units per degree (1e-5 degree, about 1.1 m north-south).
SCALE = 100_000
LINES_ROW_GROUP = 4096
CATCHMENTS_ROW_GROUP = 2048
ZSTD_LEVEL = 19

#: Line attributes and their stored types. ``innetwork`` is not stored: every
#: stored line is a network line, so answers carry ``innetwork = 1``.
LINE_ATTRS: tuple[tuple[str, pa.DataType], ...] = (
    ("nhdplusid", pa.int64()),
    ("gnis_name", pa.string()),
    ("reachcode", pa.string()),
    ("lengthkm", pa.float64()),
    ("totdasqkm", pa.float64()),
    ("slope", pa.float64()),
    ("fcode", pa.int32()),
    ("ftype", pa.int32()),
    ("streamorde", pa.int16()),
    ("hydroseq", pa.int64()),
    ("uphydroseq", pa.int64()),
    ("dnhydroseq", pa.int64()),
    ("vpuid", pa.string()),
    ("qama", pa.float64()),
)
LINE_ATTR_NAMES = tuple(name for name, _ in LINE_ATTRS)
#: The fields the USGS layer 3 answers carry, in the engine's order.
SERVICE_FIELDS = ("nhdplusid", "gnis_name", "reachcode", "lengthkm", "totdasqkm",
                  "slope", "fcode", "ftype", "streamorde", "hydroseq",
                  "uphydroseq", "dnhydroseq", "vpuid", "innetwork", "qama")
BBOX_COLUMNS = ("xmin", "ymin", "xmax", "ymax")
LINE_GEOM_COLUMNS = ("x", "y", "parts")
POLYGON_GEOM_COLUMNS = ("x", "y", "rings", "polys")


def lines_file(vpu: str) -> str:
    return f"lines_{vpu}.parquet"


def catchments_file(vpu: str, tol: float) -> str:
    return f"catchments{int(round(tol))}_{vpu}.parquet"


def qa_file(vpu: str) -> str:
    return f"qa_{vpu}.parquet"


# --------------------------------------------------------------------------- #
# encoding (the builder)
# --------------------------------------------------------------------------- #
def quantize(coords: np.ndarray) -> np.ndarray:
    """Degrees to whole 1e-5 degree units (int32)."""
    q = np.round(np.asarray(coords, dtype="float64") * SCALE)
    if q.size and np.abs(q).max() >= 2 ** 31:
        raise ValueError("coordinate outside the int32 range")
    return q.astype(np.int32)


def _list(offsets: np.ndarray, values: np.ndarray) -> pa.ListArray:
    return pa.ListArray.from_arrays(pa.array(np.asarray(offsets, dtype=np.int32)),
                                    pa.array(np.asarray(values, dtype=np.int32)))


def _bbox(q: np.ndarray, vert_off: np.ndarray) -> dict:
    starts = vert_off[:-1]
    return {
        "xmin": pa.array(np.minimum.reduceat(q[:, 0], starts), type=pa.int32()),
        "ymin": pa.array(np.minimum.reduceat(q[:, 1], starts), type=pa.int32()),
        "xmax": pa.array(np.maximum.reduceat(q[:, 0], starts), type=pa.int32()),
        "ymax": pa.array(np.maximum.reduceat(q[:, 1], starts), type=pa.int32()),
    }


def encode_lines(geoms: Iterable) -> dict:
    """(Multi)LineStrings in degrees to the ``x``, ``y``, ``parts`` and bbox
    columns. Every geometry must be non-empty."""
    geoms = np.asarray(list(geoms) if not isinstance(geoms, np.ndarray) else geoms, dtype=object)
    if shapely.is_empty(geoms).any() or shapely.is_missing(geoms).any():
        raise ValueError("empty line geometry")
    parts = shapely.get_parts(geoms)
    counts = shapely.get_num_geometries(geoms)
    _, coords, (part_off,) = shapely.to_ragged_array(shapely.force_2d(parts))
    geom_off = np.concatenate([[0], np.cumsum(counts)])
    q = quantize(coords)
    vert_off = part_off[geom_off]
    out = {"x": _list(vert_off, q[:, 0]), "y": _list(vert_off, q[:, 1]),
           "parts": _list(geom_off, np.diff(part_off))}
    out.update(_bbox(q, vert_off))
    return out


def encode_polygons(geoms: Iterable) -> dict:
    """(Multi)Polygons in degrees to the ``x``, ``y``, ``rings``, ``polys`` and
    bbox columns. Every geometry must be non-empty."""
    geoms = np.asarray(list(geoms) if not isinstance(geoms, np.ndarray) else geoms, dtype=object)
    if shapely.is_empty(geoms).any() or shapely.is_missing(geoms).any():
        raise ValueError("empty polygon geometry")
    parts = shapely.get_parts(geoms)
    counts = shapely.get_num_geometries(geoms)
    _, coords, (ring_off, poly_off) = shapely.to_ragged_array(shapely.force_2d(parts))
    geom_off = np.concatenate([[0], np.cumsum(counts)])
    q = quantize(coords)
    row_ring_off = poly_off[geom_off]
    row_vert_off = ring_off[row_ring_off]
    out = {"x": _list(row_vert_off, q[:, 0]), "y": _list(row_vert_off, q[:, 1]),
           "rings": _list(row_ring_off, np.diff(ring_off)),
           "polys": _list(geom_off, np.diff(poly_off))}
    out.update(_bbox(q, row_vert_off))
    return out


def write_table(table: pa.Table, where, row_group_size: int) -> None:
    """Write with the format's encodings: delta-packed coordinate lists, zstd,
    dictionary-encoded strings, statistics on (the bbox pruning needs them)."""
    list_cols = [n for n in table.column_names
                 if pa.types.is_list(table.schema.field(n).type)]
    encodings = dict((f"{n}.list.element", "DELTA_BINARY_PACKED") for n in list_cols)
    # Hilbert-sorted rows have neighbouring boxes, so the box columns delta-pack well.
    encodings.update((n, "DELTA_BINARY_PACKED") for n in BBOX_COLUMNS if n in table.column_names)
    dict_cols = [n for n in table.column_names if pa.types.is_string(table.schema.field(n).type)]
    pq.write_table(table, where, row_group_size=row_group_size, compression="zstd",
                   compression_level=ZSTD_LEVEL, use_dictionary=dict_cols,
                   column_encoding=encodings, write_statistics=True)


# --------------------------------------------------------------------------- #
# decoding (the reader)
# --------------------------------------------------------------------------- #
def _flat(column) -> tuple[np.ndarray, np.ndarray]:
    """Values and zero-based offsets of a (possibly sliced) list column."""
    arr = column.combine_chunks() if isinstance(column, pa.ChunkedArray) else column
    off = arr.offsets.to_numpy()
    return arr.flatten().to_numpy(zero_copy_only=False), (off - off[0])


def line_arrays(table: pa.Table) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(coords in degrees, part vertex offsets, row part offsets)``."""
    x, _ = _flat(table.column("x"))
    y, _ = _flat(table.column("y"))
    counts, row_part_off = _flat(table.column("parts"))
    coords = np.column_stack([x, y]).astype("float64") / SCALE
    part_off = np.concatenate([[0], np.cumsum(counts)])
    return coords, part_off, row_part_off


def polygon_arrays(table: pa.Table) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``(coords in degrees, ring vertex offsets, polygon ring offsets, row polygon offsets)``;
    whole 1e-5 degree units, or degrees when the columns hold floats (version 2's
    exact catchments)."""
    x, _ = _flat(table.column("x"))
    y, _ = _flat(table.column("y"))
    ring_counts, _ = _flat(table.column("rings"))
    poly_counts, row_poly_off = _flat(table.column("polys"))
    coords = np.column_stack([x, y]).astype("float64")
    if not np.issubdtype(x.dtype, np.floating):
        coords /= SCALE
    ring_off = np.concatenate([[0], np.cumsum(ring_counts)])
    poly_off = np.concatenate([[0], np.cumsum(poly_counts)])
    return coords, ring_off, poly_off, row_poly_off


def decode_lines(table: pa.Table) -> np.ndarray:
    """Shapely MultiLineStrings, one per row."""
    if table.num_rows == 0:
        return np.empty(0, dtype=object)
    coords, part_off, row_part_off = line_arrays(table)
    return shapely.from_ragged_array(shapely.GeometryType.MULTILINESTRING, coords,
                                     (part_off, row_part_off))


def decode_polygons(table: pa.Table) -> np.ndarray:
    """Shapely MultiPolygons, one per row."""
    if table.num_rows == 0:
        return np.empty(0, dtype=object)
    coords, ring_off, poly_off, row_poly_off = polygon_arrays(table)
    return shapely.from_ragged_array(shapely.GeometryType.MULTIPOLYGON, coords,
                                     (ring_off, poly_off, row_poly_off))


def line_geojson(table: pa.Table) -> list[dict]:
    """GeoJSON geometries (LineString when single-part), straight from the arrays."""
    if table.num_rows == 0:
        return []
    coords, part_off, row_part_off = line_arrays(table)
    pts = coords.tolist()
    out = []
    for i in range(table.num_rows):
        p0, p1 = row_part_off[i], row_part_off[i + 1]
        parts = [pts[part_off[p]:part_off[p + 1]] for p in range(p0, p1)]
        out.append({"type": "LineString", "coordinates": parts[0]} if len(parts) == 1
                   else {"type": "MultiLineString", "coordinates": parts})
    return out


def polygon_geojson(table: pa.Table) -> list[dict]:
    """GeoJSON geometries (Polygon when single-part), straight from the arrays."""
    if table.num_rows == 0:
        return []
    coords, ring_off, poly_off, row_poly_off = polygon_arrays(table)
    pts = coords.tolist()
    out = []
    for i in range(table.num_rows):
        polys = []
        for p in range(row_poly_off[i], row_poly_off[i + 1]):
            polys.append([pts[ring_off[r]:ring_off[r + 1]]
                          for r in range(poly_off[p], poly_off[p + 1])])
        out.append({"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1
                   else {"type": "MultiPolygon", "coordinates": polys})
    return out
