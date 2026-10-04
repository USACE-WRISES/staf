"""Measurements behind the bundle v2 review (notes/2026-10-01_HR_Mirror/precompute_review.md).

Each subcommand reads one USGS NHDPlus HR package that is already unzipped on disk
(the hr-data app's download cache, ``<temp>/hr_direct/<vpu>/*.gdb``, or ``--gdb``):

    python tools/hr-slim/scripts/geometry_research.py grid              # pilot QA geometry, every region
    python tools/hr-slim/scripts/geometry_research.py sizes 0710        # catchment storage options
    python tools/hr-slim/scripts/geometry_research.py arcs 0710         # exact step code with shared borders
    python tools/hr-slim/scripts/geometry_research.py accuracy 0710     # 0.1 and 1 mi2 watershed errors
    python tools/hr-slim/scripts/geometry_research.py snap 0710         # random clicks vs line tolerance

Sizes are compressed Parquet written in memory with the slim format's settings;
national figures in the review scale these to today's measured national size.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import tempfile
import time
import warnings
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "apps" / "hr-data"))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
warnings.filterwarnings("ignore")

from hrslim import fmt  # noqa: E402

SLIM_DATA = Path(r"D:\Data\nhdplus-hr\slim\data")
TOLERANCES = (10, 20, 30, 50, 75, 100)
GRIDS = (100_000, 20_000, 10_000)          # 1e-5, 5e-5 and 1e-4 degree


def gdb_path(vpu: str, gdb: str | None) -> str:
    if gdb:
        return gdb
    found = glob.glob(str(Path(tempfile.gettempdir()) / "hr_direct" / vpu / "*.gdb"))
    if not found:
        raise SystemExit(f"no unzipped package for {vpu}; download it with the hr-data app or pass --gdb")
    return found[0]


def read(gdb: str, layer: str, cols: list[str], geometry: bool):
    import pyogrio
    import pyogrio.raw
    names = {str(f).lower(): str(f) for f in pyogrio.read_info(gdb, layer=layer)["fields"]}
    meta, _, geom, data = pyogrio.raw.read(gdb, layer=layer, columns=[names[c] for c in cols],
                                           read_geometry=geometry, force_2d=True)
    return geom, {str(f).lower(): v for f, v in zip(meta["fields"], data)}


def ids(values) -> np.ndarray:
    return np.round(np.asarray(values, dtype="float64")).astype("int64")


def project(geoms, src: int, dst: int):
    from pyproj import Transformer
    tr = Transformer.from_crs(src, dst, always_xy=True)
    return shapely.transform(geoms, lambda q: np.column_stack(tr.transform(q[:, 0], q[:, 1])))


def hilbert(geoms) -> np.ndarray:
    import geopandas as gpd
    return np.argsort(gpd.GeoSeries(geoms).hilbert_distance().to_numpy(), kind="stable")


def nbytes(table: pa.Table, row_group: int) -> int:
    sink = pa.BufferOutputStream()
    fmt.write_table(table, sink, row_group)
    return sink.getvalue().size


def network_catchments(gdb: str, crs: int = 5070):
    """(all catchment ids, all polygons, the same projected to ``crs``, network rows in Hilbert order)."""
    g, c = read(gdb, "NHDPlusCatchment", ["nhdplusid"], True)
    _, v = read(gdb, "NHDPlusFlowlineVAA", ["nhdplusid"], False)
    cid = ids(c["nhdplusid"])
    poly = shapely.from_wkb(g)
    net = np.nonzero(np.isin(cid, ids(v["nhdplusid"])))[0]
    return cid, poly, project(poly, 4269, crs), net[hilbert(poly[net])]


# ---------------------------------------------------------------------- grid
def cmd_grid(args) -> None:
    """Share of original catchment vertices on each candidate grid, per pilot region."""
    man = json.loads((SLIM_DATA / "manifest.json").read_text(encoding="utf-8"))
    candidates = ((5070, 10, 5), (5070, 10, 0), (3338, 5, 0), (3338, 10, 5))
    for vpu in sorted(man["vpus"]):
        t = pq.read_table(SLIM_DATA / f"qa_{vpu}.parquet")
        kinds = np.asarray(t.column("kind").to_pylist())
        geo = shapely.from_wkb(t.column("wkb").to_numpy(zero_copy_only=False))[kinds == "catchment"]
        xy = shapely.get_coordinates(geo)
        shares = []
        for crs, step, off in candidates:
            p = project(shapely.points(xy), 4269, crs)
            x, y = shapely.get_x(p), shapely.get_y(p)
            rx = np.abs((x - off) - np.round((x - off) / step) * step)
            ry = np.abs((y - off) - np.round((y - off) / step) * step)
            shares.append(f"{crs}/{step} m/+{off}: {float(((rx < 0.01) & (ry < 0.01)).mean()):.3f}")
        print(vpu, f"{len(xy)} vertices |", " ".join(shares))


# ---------------------------------------------------------------------- sizes
def cmd_sizes(args) -> None:
    """Per-polygon catchment bytes by coverage-simplification tolerance and grid."""
    gdb = gdb_path(args.vpu, args.gdb)
    cid, poly, c5, order = network_catchments(gdb)
    print(args.vpu, "network catchments", len(order))
    print("tolerance_m, vertices, MB at 1e-5, 5e-5 and 1e-4 degree")
    for tol in (0,) + TOLERANCES:
        simp = c5 if tol == 0 else shapely.coverage_simplify(c5, tol, simplify_boundary=False)
        deg = project(simp[order], 5070, 4269)
        row = []
        for scale in GRIDS:
            fmt.SCALE = scale
            row.append(nbytes(pa.table(dict(nhdplusid=pa.array(cid[order]), **fmt.encode_polygons(deg))),
                              fmt.CATCHMENTS_ROW_GROUP) / 1e6)
        fmt.SCALE = GRIDS[0]
        print(tol, int(shapely.get_num_coordinates(deg).sum()), " ".join(f"{v:.2f}" for v in row), flush=True)


# ---------------------------------------------------------------------- arcs
def cmd_arcs(args) -> None:
    """Exact catchments as grid steps with each shared border stored once."""
    import shared_edges
    gdb = gdb_path(args.vpu, args.gdb)
    cid, poly, c5, order = network_catchments(gdb, args.crs)
    gt, xy, offs = shapely.to_ragged_array(c5[order])
    gx = np.round((xy[:, 0] - args.offset) / args.cell).astype("int32")
    gy = np.round((xy[:, 1] - args.offset) / args.cell).astype("int32")
    off_grid = float(np.max(np.abs(xy[:, 0] - (gx * args.cell + args.offset))))
    ring_off, poly_off, geom_off = offs
    start = ring_off[poly_off[geom_off]].astype("int32")
    table = pa.table(dict(
        x=pa.ListArray.from_arrays(pa.array(start), pa.array(gx)),
        y=pa.ListArray.from_arrays(pa.array(start), pa.array(gy)),
        rings=pa.ListArray.from_arrays(pa.array(poly_off[geom_off].astype("int32")),
                                       pa.array(np.diff(ring_off).astype("int32")))))
    t0 = time.time()
    vkeys, arcs, _owner, ref_ring, ref_arc, stats = shared_edges.build_arcs(table)
    vx = (vkeys >> 32).astype(np.int64)
    vy = (vkeys & 0xFFFFFFFF).astype(np.int64)
    vy = np.where(vy >= 2 ** 31, vy - 2 ** 32, vy)
    flat = np.concatenate([np.asarray(a, dtype=np.int64) for a in arcs])
    off = np.concatenate([[0], np.cumsum([len(a) for a in arcs])])
    ax, ay = vx[flat], vy[flat]
    dx, dy = np.diff(ax), np.diff(ay)
    inside = np.ones(len(dx), bool)
    inside[off[1:-1] - 1] = False
    diagonal = int(((dx != 0) & (dy != 0) & inside).sum())
    runs = np.where(dx != 0, dx, dy)[inside].astype("int16")

    def size(cols, enc):
        sink = pa.BufferOutputStream()
        pq.write_table(pa.table(cols), sink, row_group_size=4096, compression="zstd",
                       compression_level=19, use_dictionary=False, column_encoding=enc)
        return sink.getvalue().size / 1e6

    mb_runs = size(dict(r=pa.array(runs)), dict(r="PLAIN"))
    mb_heads = size(dict(x=pa.array(ax[off[:-1]].astype("int32")), y=pa.array(ay[off[:-1]].astype("int32")),
                         n=pa.array(np.diff(off).astype("int32"))),
                    dict(x="DELTA_BINARY_PACKED", y="DELTA_BINARY_PACKED", n="DELTA_BINARY_PACKED"))
    ring_row = np.repeat(np.arange(len(geom_off) - 1), np.diff(poly_off[geom_off]))
    roff = np.concatenate([[0], np.cumsum(np.bincount(ring_row[ref_ring], minlength=len(geom_off) - 1))])
    mb_refs = size(dict(a=pa.ListArray.from_arrays(pa.array(roff.astype("int32")), pa.array(ref_arc.astype("int32")))),
                   dict())
    print(args.vpu, f"largest off-grid residual {off_grid:.4f} m, diagonal moves {diagonal}, arcs in {time.time() - t0:.0f} s")
    print("stats", stats)
    print(f"MB: steps {mb_runs:.2f} + arc heads {mb_heads:.2f} + ring references {mb_refs:.2f} = {mb_runs + mb_heads + mb_refs:.2f}")


# ---------------------------------------------------------------------- accuracy
def cmd_accuracy(args) -> None:
    """Watershed outline error of simplified catchments, for 0.1 and 1 mi2 watersheds."""
    gdb = gdb_path(args.vpu, args.gdb)
    _, c = read(gdb, "NHDPlusCatchment", ["nhdplusid"], False)
    g, _ = read(gdb, "NHDPlusCatchment", ["nhdplusid"], True)
    cid = ids(c["nhdplusid"])
    c5 = project(shapely.from_wkb(g), 4269, 5070)
    _, v = read(gdb, "NHDPlusFlowlineVAA", ["nhdplusid", "hydroseq", "dnhydroseq", "totdasqkm"], False)
    vid, hs, dn, da = ids(v["nhdplusid"]), ids(v["hydroseq"]), ids(v["dnhydroseq"]), np.asarray(v["totdasqkm"], float)
    pos = {int(i): k for k, i in enumerate(cid)}
    o = np.argsort(dn, kind="stable")
    dns = dn[o]

    def tree(r):
        out, frontier = [r], [r]
        while frontier:
            nxt = []
            for k in frontier:
                lo, hi = np.searchsorted(dns, hs[k], "left"), np.searchsorted(dns, hs[k], "right")
                nxt.extend(int(p) for p in o[lo:hi])
            out.extend(nxt)
            frontier = nxt
        return [pos[int(vid[k])] for k in out if int(vid[k]) in pos]

    rng = np.random.default_rng(7)
    classes = {}
    for name, lo, hi in (("0.1 mi2", 0.22, 0.30), ("1 mi2", 2.2, 3.0)):
        cand = np.nonzero((da >= lo) & (da <= hi))[0]
        classes[name] = [tree(int(r)) for r in rng.choice(cand, size=min(args.samples, len(cand)), replace=False)]
    variants = {f"simplified {t} m": shapely.coverage_simplify(c5, t, simplify_boundary=False) for t in (20, 50, 100)}
    print("class | variant | area err % median (p90) | shape changed % | largest shift m | mean shift m")
    for name, trees in classes.items():
        truth = [shapely.union_all(c5[t]) for t in trees]
        for label, arr in variants.items():
            ae, sd, hd, mo = [], [], [], []
            for t, tu in zip(trees, truth):
                u = shapely.union_all(shapely.make_valid(arr[t]))
                a = tu.area
                diff = shapely.symmetric_difference(u, tu).area
                ae.append(abs(u.area - a) / a * 100)
                sd.append(diff / a * 100)
                mo.append(diff / tu.length)
                hd.append(shapely.hausdorff_distance(u, tu))
            q = lambda x, p: float(np.percentile(x, p))
            print(f"{name} | {label} | {q(ae, 50):.1f} ({q(ae, 90):.1f}) | {q(sd, 50):.1f} ({q(sd, 90):.1f}) | "
                  f"{q(hd, 50):.0f} ({q(hd, 90):.0f}) | {q(mo, 50):.1f}", flush=True)


# ---------------------------------------------------------------------- snap
def cmd_snap(args) -> None:
    """Random clicks near network flowlines: how often simplification changes the flowline."""
    gdb = gdb_path(args.vpu, args.gdb)
    g, f = read(gdb, "NHDFlowline", ["nhdplusid", "innetwork"], True)
    _, v = read(gdb, "NHDPlusFlowlineVAA", ["nhdplusid", "hydroseq", "dnhydroseq", "totdasqkm"], False)
    fid, vid = ids(f["nhdplusid"]), ids(v["nhdplusid"])
    keep = np.nonzero((np.asarray(f["innetwork"]) == 1) & np.isin(fid, vid))[0]
    row = {int(i): k for k, i in enumerate(vid)}
    vi = np.array([row[int(i)] for i in fid[keep]], dtype=np.int64)
    hs, dn, da = ids(v["hydroseq"])[vi], ids(v["dnhydroseq"])[vi], np.asarray(v["totdasqkm"], float)[vi]
    lines = project(shapely.from_wkb(g)[keep], 4269, 5070)
    rng = np.random.default_rng(11)
    n = args.clicks
    base = shapely.line_interpolate_point(lines[rng.integers(0, len(lines), n)], rng.random(n), normalized=True)
    angle = rng.random(n) * 2 * np.pi
    for reach in (10.0, 30.0):
        dist = rng.random(n) * reach
        pts = shapely.points(shapely.get_x(base) + dist * np.cos(angle), shapely.get_y(base) + dist * np.sin(angle))
        truth = shapely.STRtree(lines).query_nearest(pts, all_matches=False)[1]
        for tol in (2, 5, 10, 20):
            got = shapely.STRtree(shapely.simplify(lines, tol, preserve_topology=False)).query_nearest(
                pts, all_matches=False)[1]
            bad = np.nonzero(got != truth)[0]
            a, b = truth[bad], got[bad]
            updown = (dn[a] == hs[b]) | (dn[b] == hs[a])
            sibling = (dn[a] == dn[b]) & ~updown
            print(f"clicks within {reach:.0f} m | {tol} m lines | other flowline {100 * len(bad) / n:.2f}% "
                  f"(next reach {100 * updown.mean():.0f}%, other branch {100 * sibling.mean():.0f}%, "
                  f"another stream {100 * (~updown & ~sibling).mean():.0f}%)", flush=True)
    total = shapely.length(lines).sum()
    for tol in (2, 5, 10, 20):
        kept = shapely.length(shapely.simplify(lines, tol, preserve_topology=False)).sum() / total
        print(f"{tol} m lines keep {100 * kept:.2f}% of the length")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("grid")
    for name in ("sizes", "arcs", "accuracy", "snap"):
        s = sub.add_parser(name)
        s.add_argument("vpu")
        s.add_argument("--gdb", default=None, help="path to the unzipped .gdb (default: hr-data download cache)")
        if name == "arcs":
            s.add_argument("--crs", type=int, default=5070, help="grid CRS (3338 in Alaska)")
            s.add_argument("--cell", type=float, default=10.0, help="grid cell in metres (5 in Alaska)")
            s.add_argument("--offset", type=float, default=5.0, help="grid offset in metres (0 in Alaska)")
        if name == "accuracy":
            s.add_argument("--samples", type=int, default=150)
        if name == "snap":
            s.add_argument("--clicks", type=int, default=30000)
    args = ap.parse_args(argv)
    {"grid": cmd_grid, "sizes": cmd_sizes, "arcs": cmd_arcs, "accuracy": cmd_accuracy, "snap": cmd_snap}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
