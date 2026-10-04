"""Acceptance checks for the version 2 pilot (notes/2026-10-01_HR_Mirror/).

    python tools/hr-slim/scripts/accept_v2.py sizes               # v2 against v1 bytes, per region
    python tools/hr-slim/scripts/accept_v2.py trees  [--n 1000]   # v1 and v2 upstream walks agree
    python tools/hr-slim/scripts/accept_v2.py outline [--n 60]    # outline = polygon union; area vs VAA; timing
    python tools/hr-slim/scripts/accept_v2.py snap [--clicks 20000]   # stored 2 m lines vs the USGS lines
    python tools/hr-slim/scripts/accept_v2.py ends [--keep 25]    # size of lines kept whole near their ends

Reads the v1 pilot (``D:\\Data\\nhdplus-hr\\slim\\data``), the v2 pilot
(``D:\\Data\\nhdplus-hr\\slim2\\data``) and, for ``snap``, the downloaded USGS zips
(``D:\\Data\\nhdplus-hr\\zips``). Prints Markdown rows and saves
``D:\\Data\\nhdplus-hr\\slim2\\accept\\<command>.json``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import shapely

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "apps" / "hr-data"))
sys.path.insert(0, str(HERE.parent))
warnings.filterwarnings("ignore")

from hrbuild.convert2 import simplify_lines  # noqa: E402
from hrslim import Dataset1, Dataset2, arcs, fmt  # noqa: E402

V1 = Path(r"D:\Data\nhdplus-hr\slim\data")
V2 = Path(r"D:\Data\nhdplus-hr\slim2\data")
ZIPS = Path(r"D:\Data\nhdplus-hr\zips")
OUT = V2.parent / "accept"
#: The site engine's interactive budget.
MAX_REACHES, MAX_HOPS = 3000, 190


def save(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(obj, indent=1), encoding="utf-8")


def pct(x, p):
    return float(np.percentile(np.asarray(x, float), p)) if len(x) else float("nan")


# ---------------------------------------------------------------------- sizes
def cmd_sizes(args) -> None:
    m1 = json.loads((V1 / "manifest.json").read_text(encoding="utf-8"))
    m2 = json.loads((V2 / "manifest.json").read_text(encoding="utf-8"))
    tol = f"{m1['recipe'].get('default_catchment_tolerance_m', 20):g}"
    rows, t1, t2 = [], 0, 0
    print(f"| Region | v1 lines MB | v1 catchments ({tol} m) MB | v2 lines MB | v2 catchments MB | v1 total | v2 total | v2/v1 |")
    print("|---|---|---|---|---|---|---|---|")
    for vpu, e2 in sorted(m2["vpus"].items()):
        e1 = m1["vpus"].get(vpu)
        l2 = e2["lines"]["bytes"]
        c2 = e2["catchments"]["bytes"] + e2["arcs"]["bytes"] + e2["steps"]["bytes"]
        l1 = e1["lines"]["bytes"] if e1 else 0
        c1 = e1["catchments"][tol]["bytes"] if e1 and tol in e1["catchments"] else 0
        t1 += l1 + c1
        t2 += l2 + c2
        rows.append({"vpu": vpu, "v1_lines": l1, "v1_catchments": c1, "v2_lines": l2, "v2_catchments": c2,
                     "catchments": e2["catchments"]["rows"], "lines": e2["lines"]["rows"]})
        print(f"| {vpu} | {l1 / 1e6:.2f} | {c1 / 1e6:.2f} | {l2 / 1e6:.2f} | {c2 / 1e6:.2f} | "
              f"{(l1 + c1) / 1e6:.2f} | {(l2 + c2) / 1e6:.2f} | {(l2 + c2) / max(l1 + c1, 1):.2f} |")
    print(f"| all | | | | | {t1 / 1e6:.1f} | {t2 / 1e6:.1f} | {t2 / max(t1, 1):.2f} |")
    links = m2.get("links", {})
    print(f"links2.parquet: {links.get('rows')} rows, {links.get('bytes', 0) / 1e3:.1f} KB")
    save("sizes", {"rows": rows, "v1_total": t1, "v2_total": t2, "links": links})


# ---------------------------------------------------------------------- trees
def cmd_trees(args) -> None:
    ds1, ds2 = Dataset1(V1), Dataset2(V2)
    rng = np.random.default_rng(7)
    out = []
    print("| Region | walks | same | refused (both) | v1 ms median (p95) | v2 ms median (p95) | mismatches |")
    print("|---|---|---|---|---|---|---|")
    for vpu in ds2.vpus:
        if vpu not in ds1.vpus:
            continue
        ids = ds2._regions[vpu].topo()["ids"]
        pick = ids[rng.choice(len(ids), size=min(args.n, len(ids)), replace=False)]
        same = refused = 0
        bad, ms1, ms2 = [], [], []
        for nid in pick.tolist():
            a0 = time.perf_counter()
            a = ds1.upstream_tree(nid, max_reaches=MAX_REACHES, max_hops=MAX_HOPS)
            a1 = time.perf_counter()
            b = ds2.upstream_tree(nid, max_reaches=MAX_REACHES, max_hops=MAX_HOPS)
            a2 = time.perf_counter()
            ms1.append(1000 * (a1 - a0))
            ms2.append(1000 * (a2 - a1))
            key = lambda t: (t["status"], t["nReaches"], t["nHops"], tuple(t.get("ids") or ()))
            if key(a) == key(b):
                same += 1
                refused += a["status"] == "refused"
            else:
                bad.append({"nhdplusid": nid, "v1": [a["status"], a["nReaches"], a["nHops"]],
                            "v2": [b["status"], b["nReaches"], b["nHops"]]})
        out.append({"vpu": vpu, "walks": len(pick), "same": same, "refused": refused, "mismatches": bad[:20],
                    "v1_ms": [pct(ms1, 50), pct(ms1, 95)], "v2_ms": [pct(ms2, 50), pct(ms2, 95)]})
        print(f"| {vpu} | {len(pick)} | {same} | {refused} | {pct(ms1, 50):.1f} ({pct(ms1, 95):.1f}) | "
              f"{pct(ms2, 50):.1f} ({pct(ms2, 95):.1f}) | {len(bad)} |", flush=True)
    save("trees", out)


# ---------------------------------------------------------------------- outline
def cmd_outline(args) -> None:
    ds = Dataset2(V2)
    rng = np.random.default_rng(5)
    out = []
    print("| Region | watersheds | outline = union | area = union | area / VAA median (min, max) | "
          "largest tree | outline ms (largest) | region load s |")
    print("|---|---|---|---|---|---|---|---|")
    for vpu in ds.vpus:
        region = ds._regions[vpu]
        t0 = time.perf_counter()
        c = region.cats()
        topo = region.topo()
        load_s = time.perf_counter() - t0
        enc = c["enc"]
        ids = topo["ids"]
        # random reaches, plus the reaches with the most upstream area (big trees)
        da = np.nan_to_num(np.asarray(topo["da"], float), nan=0.0)
        big = np.argsort(-da)[:400]
        sample = list(rng.choice(len(ids), size=min(args.n, len(ids)), replace=False))
        trees = []
        for row in sample:
            res, members = ds._walk(int(ids[row]), MAX_REACHES, MAX_HOPS)
            if res["status"] == "ok" and list(members) == [vpu]:
                trees.append((row, members[vpu]))
        largest = None
        for row in big[::-1]:          # smallest of the 400 first, keep the largest that fits the budget
            res, members = ds._walk(int(ids[row]), MAX_REACHES, MAX_HOPS)
            if res["status"] == "ok" and list(members) == [vpu] and (largest is None or len(members[vpu]) > len(largest[1])):
                largest = (row, members[vpu])
        if largest is not None:
            trees.append(largest)
        equal = area_equal = 0
        ratios, worst = [], []
        for row, line_rows in trees:
            cat_rows = c["by_id"].rows(ids[np.asarray(line_rows)])
            member = np.zeros(enc.n_rows, dtype=bool)
            member[cat_rows] = True
            got = arcs.outline(enc, member)
            union = shapely.union_all(arcs.rows_geometry(enc, cat_rows))
            diff = shapely.symmetric_difference(got, union).area
            equal += diff == 0
            cells = arcs.area_cells(enc, member)
            area_equal += cells == union.area
            if diff:
                worst.append({"nhdplusid": int(ids[row]), "diff_cells": float(diff)})
            vaa = da[row]
            if vaa > 0:
                ratios.append(cells * region.grid.cell_area_m2 / 1e6 / vaa)
        ms = float("nan")
        n_big = 0
        if largest is not None:
            cat_rows = c["by_id"].rows(ids[np.asarray(largest[1])])
            member = np.zeros(enc.n_rows, dtype=bool)
            member[cat_rows] = True
            times = []
            for _ in range(3):
                a = time.perf_counter()
                arcs.outline(enc, member)
                times.append(1000 * (time.perf_counter() - a))
            ms, n_big = min(times), len(largest[1])
        out.append({"vpu": vpu, "watersheds": len(trees), "outline_equal": int(equal), "area_equal": int(area_equal),
                    "ratio": [pct(ratios, 50), min(ratios) if ratios else None, max(ratios) if ratios else None],
                    "largest_reaches": n_big, "outline_ms": ms, "load_s": load_s, "unequal": worst[:10]})
        print(f"| {vpu} | {len(trees)} | {equal} | {area_equal} | {pct(ratios, 50):.4f} "
              f"({min(ratios) if ratios else float('nan'):.4f}, {max(ratios) if ratios else float('nan'):.4f}) | "
              f"{n_big} | {ms:.0f} | {load_s:.1f} |", flush=True)
    save("outline", out)


# ---------------------------------------------------------------------- snap
def _zip_gdb(pkg: dict, vpu: str):
    """The package's geodatabase: inside the downloaded zip, else unzipped in the
    data app's download cache; None when neither is on disk."""
    if (ZIPS / pkg["name"]).exists():
        return "/vsizip/" + str(ZIPS / pkg["name"]).replace("\\", "/") + "/" + pkg["name"][:-4] + ".gdb"
    import glob
    import tempfile
    found = glob.glob(str(Path(tempfile.gettempdir()) / "hr_direct" / vpu / "*.gdb"))
    return found[0] if found else None


def cmd_snap(args) -> None:
    """Random clicks near the USGS network lines: how often the nearest stored line
    is another flowline, for the stored 2 m lines and for 2 m lines that keep full
    detail within 25 m of each flowline's ends (where confluences are)."""
    import pyarrow.parquet as pq
    import pyogrio
    from pyproj import Transformer
    m2 = json.loads((V2 / "manifest.json").read_text(encoding="utf-8"))
    ds = Dataset2(V2)
    fwd = Transformer.from_crs(4269, 5070, always_xy=True)

    def project(geoms):
        return shapely.transform(geoms, lambda q: np.column_stack(fwd.transform(q[:, 0], q[:, 1])))

    out = []
    print("| Region | clicks | lines | other flowline 10 m / 30 m (stored 2 m) | of those: next reach / other "
          "branch / other stream | same with full detail within 25 m of ends | vertices: stored, ends kept, "
          "original | length kept |")
    print("|---|---|---|---|---|---|---|---|")
    for vpu, e in sorted(m2["vpus"].items()):
        if args.vpus and vpu not in args.vpus:
            continue
        src = _zip_gdb(e["package"], vpu)
        if src is None:
            print(f"| {vpu} | package not on disk | | | | | | |")
            continue
        names = dict((str(f).lower(), str(f)) for f in pyogrio.read_info(src, layer="NHDFlowline")["fields"])
        df = pyogrio.read_dataframe(src, layer="NHDFlowline", columns=[names["nhdplusid"]], force_2d=True)
        df.columns = [c.lower() if c != "geometry" else c for c in df.columns]
        t = pq.read_table(V2 / e["lines"]["file"], columns=["nhdplusid"] + list(fmt.BBOX_COLUMNS) + list(fmt.LINE_GEOM_COLUMNS))
        sid = t.column("nhdplusid").to_numpy()
        df["nhdplusid"] = df["nhdplusid"].round().astype("int64")
        df = df[df["nhdplusid"].isin(set(sid.tolist()))].drop_duplicates("nhdplusid").set_index("nhdplusid").loc[sid]
        orig = project(shapely.force_2d(df.geometry.to_numpy()))
        stored = project(fmt.decode_lines(t))
        ends = simplify_lines(orig, e["lines"].get("tolerance_m", 2.0), 25.0)
        ends_vertices = int(shapely.get_num_coordinates(ends).sum())
        region = ds._regions[vpu]
        rows = np.arange(len(sid))
        hs, dn = region.topo()["hs"], region.dn_hydroseq(rows)
        rng = np.random.default_rng(11)
        n = args.clicks
        base = shapely.line_interpolate_point(orig[rng.integers(0, len(orig), n)], rng.random(n), normalized=True)
        angle = rng.random(n) * 2 * np.pi
        trees = dict(orig=shapely.STRtree(orig), stored=shapely.STRtree(stored), ends=shapely.STRtree(ends))
        res = {"vpu": vpu, "clicks": n, "lines": int(len(sid))}
        for reach in (10.0, 30.0):
            dist = rng.random(n) * reach
            pts = shapely.points(shapely.get_x(base) + dist * np.cos(angle), shapely.get_y(base) + dist * np.sin(angle))
            truth = trees["orig"].query_nearest(pts, all_matches=False)[1]
            for key in ("stored", "ends"):
                got = trees[key].query_nearest(pts, all_matches=False)[1]
                bad = np.nonzero(got != truth)[0]
                a, b = truth[bad], got[bad]
                updown = (dn[a] == hs[b]) | (dn[b] == hs[a])
                sibling = (dn[a] == dn[b]) & ~updown
                res[f"{key}_{reach:.0f}m"] = float(len(bad) / n)
                res[f"{key}_{reach:.0f}m_kinds"] = [float(updown.mean()) if bad.size else 0.0,
                                                    float(sibling.mean()) if bad.size else 0.0,
                                                    float((~updown & ~sibling).mean()) if bad.size else 0.0]
        res["vertices"] = [int(shapely.get_num_coordinates(stored).sum()), ends_vertices,
                           int(shapely.get_num_coordinates(orig).sum())]
        res["length_kept"] = float(shapely.length(stored).sum() / shapely.length(orig).sum())
        out.append(res)
        k = res["stored_30m_kinds"]
        print(f"| {vpu} | {n} | {len(sid)} | {100 * res['stored_10m']:.2f}% / {100 * res['stored_30m']:.2f}% | "
              f"{100 * k[0]:.0f}% / {100 * k[1]:.0f}% / {100 * k[2]:.0f}% | "
              f"{100 * res['ends_10m']:.2f}% / {100 * res['ends_30m']:.2f}% | "
              f"{res['vertices'][0]:,} / {res['vertices'][1]:,} / {res['vertices'][2]:,} | "
              f"{100 * res['length_kept']:.2f}% |", flush=True)
    save("snap", out)


def cmd_ends(args) -> None:
    """File size of the lines with full detail kept near flowline ends, against the
    stored 2 m lines (same table, geometry swapped, written with the v2 settings)."""
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pyogrio
    from pyproj import Transformer
    from hrslim import fmt2
    m2 = json.loads((V2 / "manifest.json").read_text(encoding="utf-8"))
    fwd = Transformer.from_crs(4269, 5070, always_xy=True)
    inv = Transformer.from_crs(5070, 4269, always_xy=True)
    out = []
    print(f"| Region | lines MB stored (2 m) | lines MB, full detail within {args.keep:g} m of ends | change | vertices stored / ends kept |")
    print("|---|---|---|---|---|")
    tot = [0, 0]
    for vpu, e in sorted(m2["vpus"].items()):
        src = _zip_gdb(e["package"], vpu)
        if src is None:
            continue
        names = dict((str(f).lower(), str(f)) for f in pyogrio.read_info(src, layer="NHDFlowline")["fields"])
        df = pyogrio.read_dataframe(src, layer="NHDFlowline", columns=[names["nhdplusid"]], force_2d=True)
        df.columns = [c.lower() if c != "geometry" else c for c in df.columns]
        table = pq.read_table(V2 / e["lines"]["file"])
        sid = table.column("nhdplusid").to_numpy()
        df["nhdplusid"] = df["nhdplusid"].round().astype("int64")
        df = df[df["nhdplusid"].isin(set(sid.tolist()))].drop_duplicates("nhdplusid").set_index("nhdplusid").loc[sid]
        orig = shapely.transform(shapely.force_2d(df.geometry.to_numpy()),
                                 lambda q: np.column_stack(fwd.transform(q[:, 0], q[:, 1])))
        ends = simplify_lines(orig, e["lines"].get("tolerance_m", 2.0), args.keep)
        deg = shapely.transform(ends, lambda q: np.column_stack(inv.transform(q[:, 0], q[:, 1])))
        geo = fmt.encode_lines(deg)
        t2 = table
        for name, col in geo.items():
            t2 = t2.set_column(t2.column_names.index(name), name, col)
        buf = io.BytesIO()
        fmt2.write(t2, buf, "lines")
        stored_vertices = int(np.diff(fmt._flat(table.column("x"))[1]).sum())
        new_bytes, old_bytes = len(buf.getvalue()), e["lines"]["bytes"]
        tot[0] += old_bytes
        tot[1] += new_bytes
        out.append({"vpu": vpu, "stored_bytes": old_bytes, "ends_bytes": new_bytes, "keep_m": args.keep,
                    "stored_vertices": stored_vertices, "ends_vertices": int(shapely.get_num_coordinates(deg).sum())})
        print(f"| {vpu} | {old_bytes / 1e6:.2f} | {new_bytes / 1e6:.2f} | {100 * (new_bytes / old_bytes - 1):+.0f}% | "
              f"{stored_vertices:,} / {out[-1]['ends_vertices']:,} |", flush=True)
    print(f"| all | {tot[0] / 1e6:.1f} | {tot[1] / 1e6:.1f} | {100 * (tot[1] / max(tot[0], 1) - 1):+.0f}% | |")
    save("ends", out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sizes")
    s = sub.add_parser("trees")
    s.add_argument("--n", type=int, default=1000)
    s = sub.add_parser("outline")
    s.add_argument("--n", type=int, default=60)
    s = sub.add_parser("snap")
    s.add_argument("vpus", nargs="*")
    s.add_argument("--clicks", type=int, default=20000)
    s = sub.add_parser("ends")
    s.add_argument("--keep", type=float, default=25.0, help="metres kept whole at each flowline end")
    args = ap.parse_args(argv)
    {"sizes": cmd_sizes, "trees": cmd_trees, "outline": cmd_outline, "snap": cmd_snap, "ends": cmd_ends}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
