"""Command line: ``python tools/hr-slim/run.py <command>`` (or ``python -m hrbuild``
from ``tools/hr-slim``)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from . import HR_DATA_APP, config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hrbuild", description=__doc__)
    ap.add_argument("--root", default=None, help="data root (default: version 1 root, or the version 2 root for download, convert2 and manifest2)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("inventory", help="list the USGS VPU packages on S3")
    s.add_argument("--refresh", action="store_true")
    s = sub.add_parser("convert", help="convert VPU packages into slim files")
    s.add_argument("vpus", nargs="+")
    s.add_argument("--workers", type=int, default=config.WORKERS)
    s.add_argument("--line-tol", type=float, default=config.LINE_TOLERANCE_M)
    s.add_argument("--cat-tols", default=",".join(f"{t:g}" for t in config.CATCHMENT_TOLERANCES_M))
    s.add_argument("--qa-boxes", type=int, default=config.QA_BOXES)
    s.add_argument("--force", action="store_true", help="redo VPUs already converted")
    sub.add_parser("manifest", help="merge the converted VPUs into data/manifest.json")
    s = sub.add_parser("download", help="download USGS packages into the zips folder (version 2 build)")
    s.add_argument("vpus", nargs="+")
    s.add_argument("--zips", default=str(config.ZIPS_DIR))
    s = sub.add_parser("convert2", help="convert regions into version 2 files (exact catchments, 2 m lines)")
    s.add_argument("vpus", nargs="+")
    s.add_argument("--workers", type=int, default=config.V2_WORKERS)
    s.add_argument("--line-tol", type=float, default=config.V2_LINE_TOLERANCE_M)
    s.add_argument("--keep-ends", type=float, default=config.V2_LINE_KEEP_ENDS_M,
                   help="metres at each flowline end kept with every vertex (0: none)")
    s.add_argument("--zips", default=str(config.ZIPS_DIR))
    s.add_argument("--force", action="store_true", help="redo regions already converted")
    sub.add_parser("manifest2", help="merge version 2 regions into data/manifest.json and write links2.parquet")
    s = sub.add_parser("values", help="precompute per-catchment values for version 2 regions (bundle v2)")
    s.add_argument("what", choices=["landcover", "roads", "dams", "soils", "soils-patch", "extras", "nwi", "lean"])
    s.add_argument("--vpus", nargs="*", default=None, help="regions (default: the lower-48 regions of the manifest)")
    s.add_argument("--workers", type=int, default=3)
    s = sub.add_parser("v2", help="NHDPlus V2 regions in the version 2 format, for EASI's covered streams")
    s.add_argument("hu4s", nargs="*", help="regions (default: the lower-48 regions of the HR manifest)")
    s = sub.add_parser("tables", help="national lookup tables and V2 StreamCat slices (bundle v2)")
    s.add_argument("what", nargs="+", choices=["streamcat-extra", "streamcat", "nid", "dem-catalogs", "nas", "nwis",
                                                  "wqp-backfill", "wqp-recent", "wqp-temp", "wqp"])
    s.add_argument("--vpus", nargs="*", default=None, help="regions for nwis (default: the lower-48 regions of the manifest)")
    s = sub.add_parser("sources", help="download the source data for the precomputed values (bundle v2)")
    s.add_argument("what", nargs="+", choices=["nlcd", "nid", "nid-fs", "tiger", "wbd", "nwi", "gnatsgo", "gnatsgo-tables"])
    s.add_argument("--vpus", nargs="*", default=None, help="regions (default: the version 2 manifest's)")
    sub.add_parser("report", help="size and accuracy report, scaled to the nation")
    s = sub.add_parser("pack", help="copy the data into the data app's data/ folder")
    s.add_argument("--dest", default=str(HR_DATA_APP / "data"))
    s.add_argument("--tolerance", type=float, default=None, help="keep one catchment tolerance")
    s = sub.add_parser("release", help="the bundle as release assets (pack) and their upload (publish)")
    s.add_argument("what", choices=["pack", "publish"])
    s.add_argument("--bundle", default=str(HR_DATA_APP / "data"), help="the bundle folder to pack")
    s.add_argument("--out", default=str(config.RELEASE_DIR), help="the assets folder")
    s.add_argument("--yes", action="store_true", help="publish: the owner approved this upload")
    args = ap.parse_args(argv)
    v2 = args.cmd in ("download", "convert2", "manifest2")
    root = Path(args.root) if args.root else (config.data_root_v2() if v2 else config.data_root())

    if args.cmd == "download":
        from .download import fetch
        from .source import inventory, package
        inventory(root)
        for vpu in args.vpus:
            path = fetch(package(vpu, root), Path(args.zips))
            print(f"{vpu}: {path} ({path.stat().st_size / 1e6:.0f} MB)")
        return 0
    if args.cmd == "convert2":
        from .convert2 import task2
        from .manifest import build2
        from .source import inventory
        inventory(root)
        todo = [v for v in args.vpus if args.force or not (root / "parts" / f"{v}.json").exists()]
        skipped = sorted(set(args.vpus) - set(todo))
        if skipped:
            print("already converted (use --force to redo):", " ".join(skipped))
        t0 = time.time()
        failed = []
        with ProcessPoolExecutor(max_workers=max(1, min(args.workers, len(todo) or 1))) as pool:
            futs = [pool.submit(task2, v, str(root), args.line_tol, args.zips, args.keep_ends) for v in todo]
            for fut in as_completed(futs):
                res = fut.result()
                if not res["ok"]:
                    failed.append(res)
                    print(f"FAILED {res['vpu']}: {res['error']}", flush=True)
        if any((root / "parts").glob("*.json")):
            m = build2(root)
            print(f"manifest: {len(m['vpus'])} regions, {m['links']['rows']} cross-region links")
        print(f"convert2 finished in {time.time() - t0:.0f} s; {len(todo) - len(failed)} ok, {len(failed)} failed")
        return 1 if failed else 0
    if args.cmd == "values":
        from . import sources
        from .zonal import values_task
        data_dir = config.data_root_v2() / "data"
        vpus = args.vpus or sources.lower48(sorted(json.loads((data_dir / "manifest.json")
                                                              .read_text(encoding="utf-8"))["vpus"]))
        if args.what == "lean":
            # the bundle's form of the value files (whole percents, compact units), checked against the exact
            from .leanpack import lean_values
            print("lean", lean_values(data_dir.parent / "values", data_dir.parent / "values_lean", vpus), flush=True)
            return 0
        if args.what == "soils-patch":
            # current SSURGO for cells on map units retired since gNATSGO 2020 (then run `values soils`)
            from .soils import retired_tiles, ssurgo_patch
            tiles = sorted(set(t for v in vpus for t in retired_tiles(data_dir, v)))
            print("soils-patch", ssurgo_patch(tiles, workers=args.workers), flush=True)
            return 0
        failed = []
        with ProcessPoolExecutor(max_workers=max(1, min(args.workers, len(vpus)))) as pool:
            futs = [pool.submit(values_task, args.what, str(data_dir), v) for v in vpus]
            for fut in as_completed(futs):
                res = fut.result()
                print(res, flush=True)
                if not res.get("ok"):
                    failed.append(res["vpu"])
        print(f"values {args.what}: {len(vpus) - len(failed)} ok, {len(failed)} failed {failed}")
        return 1 if failed else 0
    if args.cmd == "v2":
        from . import sources, v2
        from .manifest import build2
        hu4s = args.hu4s or sources.lower48(sorted(json.loads((config.data_root_v2() / "data" / "manifest.json")
                                                              .read_text(encoding="utf-8"))["vpus"]))
        failed, uncovered = [], []
        for hu4 in hu4s:
            try:
                v2.convert_v2_region(hu4)
            except v2.NoV2Network:           # Canadian or open-lake units: nothing of V2's to build
                uncovered.append(hu4)
            except Exception as exc:  # report, keep going
                failed.append(hu4)
                print(f"FAILED V2 {hu4}: {type(exc).__name__}: {exc}", flush=True)
        if uncovered:
            v2.record_no_v2(uncovered)
            print(f"no V2 network in {len(uncovered)} regions (recorded): {' '.join(uncovered)}", flush=True)
        m = build2(v2.V2_ROOT)
        print(f"V2 manifest: {len(m['vpus'])} regions, {m['links']['rows']} cross-region links; failed {failed}")
        # V2's COMIDs and hydroseqs are not grouped by region: the index sends a lookup to its region
        from hrslim.reader2 import write_index
        print("V2 index", write_index(v2.V2_ROOT / "data"), flush=True)
        return 1 if failed else 0
    if args.cmd == "tables":
        from . import nwis, sources, tables, v2, wqp_backfill, wqp_temperature
        out_dir = config.data_root_v2() / "tables"
        vpus = args.vpus or sources.lower48(sorted(json.loads((config.data_root_v2() / "data" / "manifest.json")
                                                              .read_text(encoding="utf-8"))["vpus"]))
        steps = {"streamcat-extra": tables.streamcat_extra, "streamcat": lambda: tables.streamcat(v2.V2_ROOT),
                 "nid": lambda: tables.nid_points(out_dir),
                 "dem-catalogs": lambda: tables.dem_catalogs(out_dir),
                 "nas": lambda: tables.nas_taxa(out_dir), "nwis": lambda: nwis.gages(vpus, out_dir),
                 "wqp-backfill": lambda: (wqp_backfill.pull(), str(wqp_backfill.combine())),
                 "wqp-recent": lambda: str(wqp_backfill.pull_recent()),
                 "wqp-temp": lambda: (wqp_temperature.pull(), wqp_temperature.convert()),
                 "wqp": lambda: tables.wqp_tables(out_dir)}
        for name in args.what:
            print(name, steps[name](), flush=True)
        return 0
    if args.cmd == "sources":
        from . import sources
        vpus = args.vpus or sorted(json.loads((config.data_root_v2() / "data" / "manifest.json")
                                              .read_text(encoding="utf-8"))["vpus"])
        steps = {"nlcd": lambda: sources.nlcd(vpus), "nid": sources.nid, "nid-fs": sources.nid_featureserver,
                 "tiger": lambda: sources.tiger(vpus),
                 "wbd": sources.wbd, "nwi": lambda: sources.nwi(vpus),
                 "gnatsgo": lambda: sources.gnatsgo_mukey(vpus), "gnatsgo-tables": sources.gnatsgo_tables}
        for name in args.what:
            print(name, steps[name](), flush=True)
        return 0
    if args.cmd == "manifest2":
        from .manifest import build2
        m = build2(root)
        print(f"manifest: {len(m['vpus'])} regions, {m['links']['rows']} cross-region links")
        return 0

    if args.cmd == "inventory":
        from .source import inventory
        pkgs = inventory(root, refresh=args.refresh)
        print(f"{len(pkgs)} packages, {sum(p['bytes'] for p in pkgs) / 1e9:.1f} GB")
        return 0
    if args.cmd == "convert":
        from .convert import task
        from .manifest import build
        from .source import inventory
        inventory(root)                       # cache the listing before the workers start
        tols = tuple(float(t) for t in args.cat_tols.split(","))
        todo = [v for v in args.vpus if args.force or not (root / "parts" / f"{v}.json").exists()]
        skipped = sorted(set(args.vpus) - set(todo))
        if skipped:
            print("already converted (use --force to redo):", " ".join(skipped))
        t0 = time.time()
        failed = []
        with ProcessPoolExecutor(max_workers=max(1, min(args.workers, len(todo) or 1))) as pool:
            futs = [pool.submit(task, v, str(root), args.line_tol, tols, args.qa_boxes) for v in todo]
            for fut in as_completed(futs):
                res = fut.result()
                if not res["ok"]:
                    failed.append(res)
                    print(f"FAILED {res['vpu']}: {res['error']}", flush=True)
        if any((root / "parts").glob("*.json")):
            m = build(root)
            print(f"manifest: {len(m['vpus'])} VPUs")
        print(f"convert finished in {time.time() - t0:.0f} s; {len(todo) - len(failed)} ok, {len(failed)} failed")
        return 1 if failed else 0
    if args.cmd == "manifest":
        from .manifest import build
        m = build(root)
        print(f"manifest: {len(m['vpus'])} VPUs")
        return 0
    if args.cmd == "report":
        from .report import report
        print(report(root))
        return 0
    if args.cmd == "pack":
        from .manifest import pack
        res = pack(root, Path(args.dest), tolerance=args.tolerance)
        print(f"packed {res['files']} files, {res['bytes'] / 1e9:.2f} GB into {args.dest}")
        return 0
    if args.cmd == "release":
        from . import release
        if args.what == "pack":
            print("release", release.pack(Path(args.bundle), Path(args.out)), flush=True)
            return 0
        if not args.yes:
            print("publishing uploads to the public release: rerun with --yes once the owner approved it")
            return 2
        for cmd in release.publish(Path(args.out)):
            print(" ".join(cmd[:4]), "...", flush=True)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
