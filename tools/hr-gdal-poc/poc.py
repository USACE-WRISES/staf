"""Proof of concept: read one NHDPlus HR flowline and its catchment straight from the
USGS zipped FileGDB on S3 with GDAL, and measure what it costs.

    python tools/hr-gdal-poc/poc.py --lat 41.016806 --lon -93.76691 --clicks 3
    python tools/hr-gdal-poc/poc.py --vpu 0710 --lat 41.016806 --lon -93.76691 --mode both --cache-mb 1024

The region comes from ``--vpu``, ``--url`` (any package zip), or the bundled
outline lookup. Each mode runs in a fresh child process (cold GDAL caches); see
``posit_app/gdalpoc.py``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "posit_app"))
import gdalpoc  # noqa: E402


def resolve(args) -> dict:
    if args.url:
        size = int(requests.head(args.url, timeout=60).headers.get("Content-Length") or 0)
        return {"vpu": args.vpu or "custom", "name": args.url.rsplit("/", 1)[-1], "url": args.url, "bytes": size}
    vpu = args.vpu
    if not vpu:
        found = gdalpoc.vpus_at(args.lon, args.lat)
        print("region lookup:", found or "no package outline here")
        if not found:
            raise SystemExit(1)
        vpu = found[0]
    for pkg in gdalpoc.list_packages():
        if pkg["vpu"] == vpu:
            return pkg
    raise SystemExit(f"no USGS package for VPU {vpu}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--vpu", help="package code, e.g. 0710 (default: look it up from the point)")
    ap.add_argument("--url", help="a package zip URL instead of --vpu")
    ap.add_argument("--mode", choices=["remote", "download", "both"], default="remote")
    ap.add_argument("--box-m", type=float, default=200.0, help="half-width of the click box")
    ap.add_argument("--cache-mb", type=int, default=0, help="GDAL HTTP and block cache (0 = GDAL default)")
    ap.add_argument("--clicks", type=int, default=3, help="clicks per run (point, 300 m, 10 km, point again)")
    ap.add_argument("--keep", action="store_true", help="download mode: reuse an existing local copy")
    ap.add_argument("--json", help="write the results here")
    args = ap.parse_args(argv)
    pkg = resolve(args)
    modes = ["remote", "download"] if args.mode == "both" else [args.mode]
    results = []
    for mode in modes:
        res = gdalpoc.run(package=pkg, lat=args.lat, lon=args.lon, mode=mode, box_m=args.box_m,
                          cache_mb=args.cache_mb, clicks=args.clicks, fresh=not args.keep)
        print(gdalpoc.table(res), flush=True)
        if res.get("error"):
            print("ERROR:", res["error"][-800:])
        results.append(res)
    if args.json:
        Path(args.json).write_text(json.dumps({"environment": gdalpoc.environment(), "runs": results},
                                              indent=1, default=str), encoding="utf-8")
        print("written", args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
