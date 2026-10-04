"""Prototype: store each catchment boundary once (shared edges, "arcs").

Catchments tile the land, so almost every boundary segment belongs to two
catchments and the per-polygon files store it twice. This script rebuilds one
VPU's slim catchments as arcs (maximal boundary chains between junctions) plus,
per ring, the list of arcs it follows, and reports:

- bytes: per-polygon encoding (today) vs arcs + references (same Parquet tricks);
- watershed outlines built from arcs alone (arcs with exactly one side inside the
  watershed, assembled with GEOS BuildArea) vs the union of the polygons: area
  agreement and time, for upstream trees of several sizes.

    python tools/hr-slim/scripts/shared_edges.py 0108 20
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hrbuild import config  # noqa: E402
from hrslim import Dataset, fmt  # noqa: E402


def _nbytes(table: pa.Table) -> int:
    sink = pa.BufferOutputStream()
    fmt.write_table(table, sink, fmt.CATCHMENTS_ROW_GROUP)
    return sink.getvalue().size


def build_arcs(table: pa.Table):
    """Arcs from a per-polygon catchment table (integer coordinates)."""
    x, _ = fmt._flat(table.column("x"))
    y, _ = fmt._flat(table.column("y"))
    ring_counts, row_ring_off = fmt._flat(table.column("rings"))
    x = x.astype(np.int64)
    y = y.astype(np.int64)
    ring_off = np.concatenate([[0], np.cumsum(ring_counts)])
    n_rings = len(ring_counts)
    ring_row = np.repeat(np.arange(table.num_rows), np.diff(row_ring_off))
    # segments: every vertex but each ring's last (closing) one starts a segment
    is_last = np.zeros(len(x), dtype=bool)
    is_last[ring_off[1:] - 1] = True
    starts = np.nonzero(~is_last)[0]
    seg_ring = np.searchsorted(ring_off, starts, side="right") - 1
    key = (x << 32) ^ (y & 0xFFFFFFFF)
    ka, kb = key[starts], key[starts + 1]
    keep = ka != kb
    starts, seg_ring, ka, kb = starts[keep], seg_ring[keep], ka[keep], kb[keep]
    vkeys, inv = np.unique(np.concatenate([ka, kb]), return_inverse=True)
    va, vb = inv[: len(ka)], inv[len(ka):]
    lo, hi = np.minimum(va, vb), np.maximum(va, vb)
    edges, seg_edge, edge_count = np.unique(np.stack([lo, hi], axis=1), axis=0,
                                            return_inverse=True, return_counts=True)
    seg_edge = seg_edge.ravel()
    n_edges = len(edges)
    # owners (polygon rows) per edge: up to two
    owner = np.full((n_edges, 2), -1, dtype=np.int64)
    rows = ring_row[seg_ring]
    order = np.argsort(seg_edge, kind="stable")
    se, sr = seg_edge[order], rows[order]
    first = np.ones(len(se), dtype=bool)
    first[1:] = se[1:] != se[:-1]
    owner[se[first], 0] = sr[first]
    second = ~first
    owner[se[second], 1] = sr[second]
    # vertex degree in the planar graph of unique edges
    deg = np.bincount(edges.ravel(), minlength=len(vkeys))
    # adjacency (CSR)
    ends = np.concatenate([edges[:, 0], edges[:, 1]])
    eids = np.concatenate([np.arange(n_edges), np.arange(n_edges)])
    o = np.argsort(ends, kind="stable")
    adj_e = eids[o]
    adj_off = np.concatenate([[0], np.cumsum(np.bincount(ends, minlength=len(vkeys)))])
    edge_arc = np.full(n_edges, -1, dtype=np.int64)
    arcs: list[list[int]] = []

    def walk(start_v: int, e: int) -> list[int]:
        verts = [start_v]
        v = start_v
        while True:
            edge_arc[e] = len(arcs)
            a, b = edges[e]
            v = b if a == v else a
            verts.append(v)
            if deg[v] != 2 or v == start_v:
                return verts
            nxt = [f for f in adj_e[adj_off[v]:adj_off[v + 1]] if edge_arc[f] < 0]
            if not nxt:
                return verts
            e = nxt[0]

    for v in np.nonzero(deg != 2)[0]:
        for e in adj_e[adj_off[v]:adj_off[v + 1]]:
            if edge_arc[e] < 0:
                arcs.append(walk(int(v), int(e)))
    for e in range(n_edges):                      # closed loops of degree-2 vertices
        if edge_arc[e] < 0:
            arcs.append(walk(int(edges[e][0]), e))
    # store arcs in Hilbert order of their first vertex, so neighbours sit together
    import geopandas as gpd
    fx = (vkeys[[a[0] for a in arcs]] >> 32).astype(np.float64)
    fy = (vkeys[[a[0] for a in arcs]] & 0xFFFFFFFF).astype(np.float64)
    fy = np.where(fy >= 2 ** 31, fy - 2 ** 32, fy)
    hil = gpd.GeoSeries(shapely.points(fx, fy)).hilbert_distance().to_numpy()
    perm = np.argsort(hil, kind="stable")
    new_id = np.empty(len(arcs), dtype=np.int64)
    new_id[perm] = np.arange(len(arcs))
    arcs = [arcs[i] for i in perm]
    edge_arc = new_id[edge_arc]
    arc_owner = np.full((len(arcs), 2), -1, dtype=np.int64)
    arc_owner[edge_arc, :] = owner
    # ring references: runs of the same arc along each ring
    seg_arc = edge_arc[seg_edge]
    change = np.ones(len(seg_arc), dtype=bool)
    change[1:] = (seg_arc[1:] != seg_arc[:-1]) | (seg_ring[1:] != seg_ring[:-1])
    ref_ring = seg_ring[change]
    ref_arc = seg_arc[change]
    stats = {"segments": int(len(starts)), "unique_edges": n_edges,
             "shared_edges": int((edge_count >= 2).sum()), "arcs": len(arcs),
             "arc_vertices": int(sum(len(a) for a in arcs)), "refs": int(len(ref_arc)),
             "rings": int(n_rings)}
    return vkeys, arcs, arc_owner, ref_ring, ref_arc, stats


def arc_tables(vkeys, arcs, ref_ring, ref_arc, n_rows, ring_row):
    vx = (vkeys >> 32).astype(np.int64)
    vy = (vkeys & 0xFFFFFFFF).astype(np.int64)
    vy = np.where(vy >= 2 ** 31, vy - 2 ** 32, vy)
    flat = np.concatenate([np.asarray(a, dtype=np.int64) for a in arcs])
    off = np.concatenate([[0], np.cumsum([len(a) for a in arcs])])
    arcs_t = pa.table({
        "x": pa.ListArray.from_arrays(pa.array(off.astype(np.int32)), pa.array(vx[flat].astype(np.int32))),
        "y": pa.ListArray.from_arrays(pa.array(off.astype(np.int32)), pa.array(vy[flat].astype(np.int32))),
    })
    roff = np.concatenate([[0], np.cumsum(np.bincount(ring_row[ref_ring], minlength=n_rows))])
    refs_t = pa.table({"arcs": pa.ListArray.from_arrays(pa.array(roff.astype(np.int32)),
                                                        pa.array(ref_arc.astype(np.int32)))})
    return arcs_t, refs_t, vx, vy


def sizes_only(vpus: list[str], tols: list[float]) -> int:
    """Per-VPU bytes today vs shared edges, pooled to the national count."""
    root = config.data_root()
    pooled: dict = {}
    for vpu in vpus:
        for tol in tols:
            path = root / "data" / fmt.catchments_file(vpu, tol)
            if not path.exists():
                continue
            table = pq.read_table(path)
            vkeys, arcs, arc_owner, ref_ring, ref_arc, stats = build_arcs(table)
            _, row_ring_off = fmt._flat(table.column("rings"))
            ring_row = np.repeat(np.arange(table.num_rows), np.diff(row_ring_off))
            arcs_t, refs_t, _, _ = arc_tables(vkeys, arcs, ref_ring, ref_arc, table.num_rows, ring_row)
            shared = _nbytes(arcs_t) + _nbytes(refs_t) + _nbytes(pa.table({"nhdplusid": table.column("nhdplusid")}))
            today = path.stat().st_size
            p = pooled.setdefault(tol, [0, 0, 0])
            p[0] += today
            p[1] += shared
            p[2] += table.num_rows
            print(f"{vpu} {tol:g} m: today {today / 1e6:6.1f} MB, shared edges {shared / 1e6:6.1f} MB "
                  f"({100 * (1 - shared / today):.0f}% smaller)", flush=True)
    for tol, (today, shared, rows) in sorted(pooled.items()):
        print(f"national {tol:g} m catchments: today {today / rows * config.NATIONAL_CATCHMENTS / 1e9:.2f} GB, "
              f"shared edges {shared / rows * config.NATIONAL_CATCHMENTS / 1e9:.2f} GB")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--sizes":
        ds = Dataset(config.data_root() / "data")
        return sizes_only(argv[1:] or ds.vpus, ds.tolerances)
    vpu = argv[0] if argv else "0108"
    tol = float(argv[1]) if len(argv) > 1 else 20.0
    root = config.data_root()
    ds = Dataset(root / "data")
    path = root / "data" / fmt.catchments_file(vpu, tol)
    table = pq.read_table(path)
    t0 = time.time()
    vkeys, arcs, arc_owner, ref_ring, ref_arc, stats = build_arcs(table)
    t_build = time.time() - t0
    ring_counts, row_ring_off = fmt._flat(table.column("rings"))
    ring_row = np.repeat(np.arange(table.num_rows), np.diff(row_ring_off))
    arcs_t, refs_t, vx, vy = arc_tables(vkeys, arcs, ref_ring, ref_arc, table.num_rows, ring_row)
    today = path.stat().st_size
    ids_b = _nbytes(pa.table({"nhdplusid": table.column("nhdplusid")}))
    shared = _nbytes(arcs_t) + _nbytes(refs_t) + ids_b
    print(f"VPU {vpu} catchments {tol:g} m: {table.num_rows} polygons, build {t_build:.1f} s")
    print("  " + ", ".join(f"{k} {v:,}" for k, v in stats.items()))
    print(f"  bytes per polygon today {today / 1e6:.1f} MB  vs  shared edges {shared / 1e6:.1f} MB "
          f"(arcs {_nbytes(arcs_t) / 1e6:.1f} + refs {_nbytes(refs_t) / 1e6:.1f} + ids {ids_b / 1e6:.1f}); "
          f"saving {100 * (1 - shared / today):.0f}%")

    # watershed outlines from arcs vs the polygon union
    from pyproj import Transformer
    to5070 = Transformer.from_crs(4269, 5070, always_xy=True)
    proj = lambda g: shapely.transform(g, lambda c: np.column_stack(to5070.transform(c[:, 0], c[:, 1])))  # noqa: E731
    row_of = dict((int(v), i) for i, v in enumerate(table.column("nhdplusid").to_numpy()))
    polys = fmt.decode_polygons(table)
    lines = pq.read_table(root / "data" / fmt.lines_file(vpu), columns=["nhdplusid", "totdasqkm"])
    da = lines.column("totdasqkm").to_numpy()
    lids = lines.column("nhdplusid").to_numpy()
    rng = np.random.default_rng(3)
    for lo, hi in [(1, 5), (20, 60), (200, 600), (1000, 3000)]:
        cand = np.nonzero((da > lo) & (da < hi))[0]
        if not cand.size:
            continue
        for i in rng.choice(cand, size=min(2, cand.size), replace=False):
            tree = ds.upstream_tree(int(lids[i]), max_reaches=20000, max_hops=2000)
            if tree["status"] != "ok":
                continue
            rows = np.array([row_of[t] for t in tree["ids"] if t in row_of])
            inside = np.zeros(table.num_rows + 1, dtype=bool)
            inside[rows] = True
            t1 = time.time()
            try:
                u_poly = shapely.union_all(proj(polys[rows]))
            except shapely.errors.GEOSException:      # an invalid simplified polygon
                u_poly = shapely.union_all(shapely.make_valid(proj(polys[rows])))
            t_poly = time.time() - t1
            t2 = time.time()
            o = arc_owner
            n_in = inside[np.where(o[:, 0] >= 0, o[:, 0], -1)].astype(int) + inside[np.where(o[:, 1] >= 0, o[:, 1], -1)].astype(int)
            sel = np.nonzero(n_in == 1)[0]
            parts = [np.column_stack([vx[arcs[a]], vy[arcs[a]]]) / fmt.SCALE for a in sel]
            outline = shapely.build_area(shapely.MultiLineString(parts))
            u_arc = proj(outline)
            t_arc = time.time() - t2
            a1, a2 = u_poly.area, u_arc.area
            print(f"  tree {len(rows):5d} catchments: union of polygons {a1 / 1e6:9.3f} km2 in {1000 * t_poly:6.0f} ms"
                  f"  | outline from arcs {a2 / 1e6:9.3f} km2 in {1000 * t_arc:6.0f} ms  (diff {100 * abs(a2 - a1) / a1:.4f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
