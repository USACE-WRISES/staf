"""A tiny two-region version 2 dataset (``hrslim.fmt2``) for the tests.

The same network as ``fixture`` (a Y in region 9901 plus an L-shaped ditch, and
reach 5 in region 9902 draining into reach 4 across the region boundary), built on
the EPSG:5070 10 m grid: every catchment is a 1 km square (100 x 100 cells), so a
watershed of n catchments is exactly n square kilometres.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import shapely

from hrslim import arcs, fmt, fmt2
from hrslim.grid import Grid

GRID = Grid(5070, 10.0, 5.0)
IX0, IY0 = 100_000, 200_000            # cell origin (about 84 W, 40 N)
SIDE = 100                             # catchment side in cells (1 km)
ID = dict((n, 99010000000000 + n) for n in (1, 2, 3, 4, 6))
ID[5] = 99020000000005
#: reach -> (cell, hydroseq, dnhydroseq, uphydroseq, name, totdasqkm)
REACHES = {
    1: ((0, 0), 1001, 0, 1002, "Outlet Run", 5.0),
    2: ((0, 1), 1002, 1001, 1004, "Upper Run", 3.0),
    3: ((1, 0), 1003, 1001, 0, None, 1.0),
    4: ((0, 2), 1004, 1002, 2005, "Upper Run", 2.0),
    5: ((0, 3), 2005, 1004, 0, "Border Creek", 1.0),
    6: ((4, 4), 1006, 0, 0, "Corner Ditch", 1.0),
}
REGIONS = {"9901": [1, 2, 3, 4, 6], "9902": [5]}


def cell_box(i: int, j: int):
    x0, y0 = IX0 + i * SIDE, IY0 + j * SIDE
    return shapely.box(x0, y0, x0 + SIDE, y0 + SIDE)


def to_deg(geom):
    return shapely.transform(geom, lambda q: np.column_stack(GRID.to_lonlat(q[:, 0], q[:, 1])))


def line_cells(n: int):
    (i, j), *_ = REACHES[n]
    x0, y0 = IX0 + i * SIDE, IY0 + j * SIDE
    cx, cy = x0 + SIDE / 2, y0 + SIDE / 2
    if n == 3:
        return shapely.LineString([(cx, cy), (x0, cy)])
    if n == 6:          # an L hugging two sides of its cell, around CORNER_BOX's corner
        return shapely.MultiLineString([[(x0 + 10, y0 + 60), (x0 + 10, y0 + 10)],
                                        [(x0 + 10, y0 + 10), (x0 + 60, y0 + 10)]])
    return shapely.LineString([(cx, cy), (cx, y0)])


def _catchment_box(n: int, boxes: dict):
    if n not in boxes:
        return cell_box(*REACHES[n][0])
    i0, j0, i1, j1 = boxes[n]
    return shapely.box(IX0 + i0 * SIDE, IY0 + j0 * SIDE, IX0 + (i1 + 1) * SIDE, IY0 + (j1 + 1) * SIDE)


def corner_box() -> tuple[float, float, float, float]:
    """A box inside reach 6's L (its bounding box overlaps, its geometry does not)."""
    (i, j), *_ = REACHES[6]
    return box_deg(i * SIDE + 20, j * SIDE + 20, i * SIDE + 50, j * SIDE + 50)


def point_deg(dx: float, dy: float) -> tuple[float, float]:
    """Longitude and latitude of the point ``(dx, dy)`` cells from the origin corner."""
    lon, lat = GRID.to_lonlat([IX0 + dx], [IY0 + dy])
    return float(lon[0]), float(lat[0])


def box_deg(dx0: float, dy0: float, dx1: float, dy1: float) -> tuple[float, float, float, float]:
    """A lon/lat box around the cell rectangle ``(dx0, dy0)``-``(dx1, dy1)``."""
    lon, lat = GRID.to_lonlat([IX0 + dx0, IX0 + dx1, IX0 + dx0, IX0 + dx1],
                              [IY0 + dy0, IY0 + dy0, IY0 + dy1, IY0 + dy1])
    return float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())


def build(root: Path, minor: dict | None = None, *, regions: dict | None = None,
          boxes: dict | None = None, up: dict | None = None) -> Path:
    """The two-region dataset; ``minor`` maps a reach to the hydroseq of a minor-divergence branch
    it splits into (NHDPlus V2's ``DnMinorHyd``). ``regions`` replaces ``REGIONS`` (a network of
    fewer reaches), ``boxes`` gives a reach a catchment of several cells (``(i0, j0, i1, j1)``,
    inclusive) and ``up`` replaces reaches' ``uphydroseq``."""
    regions, boxes, up = regions or REGIONS, boxes or {}, up or {}
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"format": fmt2.FORMAT_VERSION,
                "recipe": {"format": 2, "scale": fmt.SCALE, "line_tolerance_m": 2.0, "catchments": "exact"},
                "vpus": {}, "built": "2026-10-01T00:00:00+00:00"}
    tables = []
    for vpu, reaches in regions.items():
        attrs = {
            "nhdplusid": np.array([ID[n] for n in reaches], dtype=np.int64),
            "gnis_name": [REACHES[n][4] for n in reaches],
            "reachcode": [f"0206000500{n:04d}" for n in reaches],
            "lengthkm": np.full(len(reaches), 0.5), "totdasqkm": np.array([REACHES[n][5] for n in reaches]),
            "slope": np.full(len(reaches), 0.001), "fcode": np.full(len(reaches), 46006),
            "streamorde": np.ones(len(reaches)),
            "hydroseq": np.array([REACHES[n][1] for n in reaches], dtype=np.int64),
            "dnhydroseq": np.array([REACHES[n][2] for n in reaches], dtype=np.int64),
            "uphydroseq": np.array([up.get(n, REACHES[n][3]) for n in reaches], dtype=np.int64),
            "qama": np.array([np.nan if n == 3 else 1.5 for n in reaches]),
        }
        if minor:
            attrs["dnminorhyd"] = np.array([minor.get(n, 0) for n in reaches], dtype=np.int64)
        lines = fmt2.lines_table(attrs, [to_deg(line_cells(n)) for n in reaches])
        lfile = root / fmt2.lines_file(vpu)
        fmt2.write(lines, lfile, "lines", row_group=2)       # tiny row groups exercise pruning
        tables.append((vpu, lines))
        polys = [shapely.MultiPolygon([_catchment_box(n, boxes)]) for n in reaches]
        _, coords, (ring_off, poly_off, row_off) = shapely.to_ragged_array(np.asarray(polys, dtype=object))
        enc = arcs.encode(coords[:, 0], coords[:, 1], ring_off, poly_off, row_off)
        deg = [to_deg(p) for p in polys]
        bbox = np.array([shapely.bounds(g) for g in deg])
        cats, arcs_t, steps = fmt2.catchment_tables(enc, attrs["nhdplusid"], bbox)
        cfile, afile, sfile = root / fmt2.cats_file(vpu), root / fmt2.arcs_file(vpu), root / fmt2.steps_file(vpu)
        fmt2.write(cats, cfile, "cats")
        fmt2.write(arcs_t, afile, "arcs")
        sfile.write_bytes(steps)
        b = np.array([shapely.bounds(g) for g in deg + [to_deg(line_cells(n)) for n in reaches]])
        hs = attrs["hydroseq"]
        manifest["vpus"][vpu] = {
            "vpu": vpu, "bounds": [float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 2].max()), float(b[:, 3].max())],
            "id_range": [int(attrs["nhdplusid"].min()), int(attrs["nhdplusid"].max())],
            "hydroseq_range": [int(hs.min()), int(hs.max())], "grid": GRID.to_dict(), "vpuid": vpu,
            "ftype_from_fcode": True,
            "lines": {"file": lfile.name, "bytes": lfile.stat().st_size, "rows": lines.num_rows, "tolerance_m": 2.0},
            "catchments": {"file": cfile.name, "bytes": cfile.stat().st_size, "rows": cats.num_rows},
            "arcs": {"file": afile.name, "bytes": afile.stat().st_size, "rows": arcs_t.num_rows},
            "steps": {"file": sfile.name, "bytes": sfile.stat().st_size},
        }
    links = fmt2.links_table(tables)
    pq.write_table(links, root / fmt2.LINKS_FILE)
    manifest["links"] = {"file": fmt2.LINKS_FILE, "rows": links.num_rows}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return root


#: NHDPlus V2 for ``build_v2(partial=True)``: reaches 2, 4 and 5 are HR-only streams; V2 has the
#: outlet, the tributary and the ditch, and the outlet's V2 catchment holds the land the HR-only
#: reaches drain (cells (0, 0) to (0, 3), the four square kilometres above its outlet but reach 3's)
V2_PARTIAL = {"9901": [1, 3, 6]}
V2_PARTIAL_BOXES = {1: (0, 0, 0, 3)}


def build_v2(root: Path, *, partial: bool = False) -> Path:
    """The bundle's ``v2/`` part for this network (its ids standing in for COMIDs): the same
    network, or with ``partial`` only the reaches of ``V2_PARTIAL``."""
    if not partial:
        return build(root / "v2")
    return build(root / "v2", regions=V2_PARTIAL, boxes=V2_PARTIAL_BOXES, up={1: 0})


#: the lookups beside the network (``write_lookups``): reach -> (HUC12, ATTAINS unit row at it
#: or -1, nearest unit row within 2 km or -1, its distance in metres, palustrine NWI area in its
#: 150 m strip in m2)
LOOKUPS = {
    1: (20600050101, 0, 0, 0.0, 30000.0),
    2: (20600050101, -1, 0, 450.0, 15000.0),
    3: (20600050102, -1, 1, 120.0, 0.0),
    4: (20600050101, -1, -1, None, 0.0),
    5: (20600050101, -1, -1, None, 0.0),
    6: (20600050103, -1, -1, None, 0.0),
}
UNITS = [("IA 02-TEST-0001", "Outlet Run", "5", "Impaired", "Y", 1),
         ("IA 02-TEST-0002", "Side Branch", "2", "Fully Supporting", "N", 1)]
STRIP_M2 = 60000.0                     # every strip: 150 m each side of a 0.5 km line, ends included


def write_lookups(root: Path) -> Path:
    """The STAF data bundle's per-flowline tables for this network: the extras (sinuosity,
    HUC12, ATTAINS units at three samples, 150 m NWI strips) and the ATTAINS unit table."""
    import pyarrow as pa

    folder = root / "values"
    folder.mkdir(parents=True, exist_ok=True)
    reach_of = dict((v, k) for k, v in ID.items())
    for vpu in REGIONS:
        ids = pq.read_table(root / fmt2.lines_file(vpu), columns=["nhdplusid"]).column("nhdplusid").to_pylist()
        rows = [LOOKUPS[reach_of[i]] + (reach_of[i],) for i in ids]
        extras = {"sinuosity": pa.array([1.234 if r[5] == 1 else 1.0 for r in rows])}
        for tag in ("05", "50", "95"):
            extras[f"au_exact_{tag}"] = pa.array([r[1] for r in rows], type=pa.int32())
            extras[f"au_near_{tag}"] = pa.array([r[2] for r in rows], type=pa.int32())
            extras[f"au_near_m_{tag}"] = pa.array([r[3] for r in rows], type=pa.float64())
            extras[f"huc12_{tag}"] = pa.array([r[0] for r in rows], type=pa.int64())
        for code in ("r", "p", "l", "e", "m"):
            extras[f"nwi_{code}_m2"] = pa.array([r[4] if code == "p" else 0.0 for r in rows])
        extras["strip_m2"] = pa.array([STRIP_M2] * len(rows))
        pq.write_table(pa.table(extras), folder / f"extras2_{vpu}.parquet")
        pq.write_table(pa.table({"layer": pa.array([u[5] for u in UNITS], type=pa.int64()),
                                 "assessment_unit": [u[0] for u in UNITS], "assessment_name": [u[1] for u in UNITS],
                                 "ircategory": [u[2] for u in UNITS], "overallstatus": [u[3] for u in UNITS],
                                 "isimpaired": [u[4] for u in UNITS]}), folder / f"au2_{vpu}.parquet")
    return root
