"""The STAF data bundle's national build, stage by stage and resumable (Phase 4, step 7).

Each stage hands the builder (``hrbuild.cli``) only the lower-48 regions still missing that
stage's output, so a stopped run picks up where it left off. Stages, in order:

- ``convert``: download each region's NHDPlus HR package (USGS, about 50 GB for the 228 regions)
  and write its version 2 files (exact catchments, 2 m lines); the manifest is merged at the end.
- ``sources``: the inputs of the precomputed values for every region (NLCD tiles from MRLC, TIGER
  roads by county, NWI by state, gNATSGO map units); files already downloaded are kept.
- ``values``: land cover and riparian pieces, roads and crossings, dams, the soils patch and soil
  K, and the per-flowline extras (sinuosity, ATTAINS, HUC12, NWI strips), then the lean encoding.
- ``v2``: the NHDPlus V2 regions (EASI's covered streams) and their StreamCat slices.
- ``tables``: SFARI's NWIS gage statistics for every region (resumable: NWIS answers 503 at times).
- ``pack``: the bundle folder (``apps/hr-data/data``) and its release assets.

    python tools/hr-slim/scripts/national_build.py status
    python tools/hr-slim/scripts/national_build.py convert --workers 4
    python tools/hr-slim/scripts/national_build.py sources
    python tools/hr-slim/scripts/national_build.py values --workers 3

The build runs below normal priority (its workers inherit it), so it takes only the CPU other
work on the machine leaves free; ``--full-priority`` runs it at normal priority.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from hrbuild import config, sources  # noqa: E402
from hrbuild.cli import main as cli  # noqa: E402

#: each value kind and the per-region file that shows it is done
VALUE_KINDS = (("landcover", "lc2"), ("roads", "roads2"), ("dams", "dams2"), ("soils", "soils2"),
               ("extras", "extras2"))


def regions() -> list[str]:
    """Every lower-48 region of the USGS package inventory."""
    root = config.data_root_v2()
    inv = json.loads((root / "inventory.json").read_text(encoding="utf-8"))
    return sources.lower48(sorted(set(p["vpu"] for p in inv)))


def missing(vpus: list[str], folder: Path, prefix: str) -> list[str]:
    return [v for v in vpus if not (folder / f"{prefix}_{v}.parquet").exists()]


def status(vpus: list[str]) -> dict:
    root = config.data_root_v2()
    out = {"regions": len(vpus),
           "convert": len(vpus) - len([v for v in vpus if not (root / "parts" / f"{v}.json").exists()])}
    for kind, prefix in VALUE_KINDS:
        out[kind] = len(vpus) - len(missing(vpus, root / "values", prefix))
    out["lean"] = len(vpus) - len(missing(vpus, root / "values_lean", "lc2"))
    from hrbuild import v2
    out["v2"] = len(vpus) - len(v2_missing(vpus))
    return out


def v2_missing(vpus: list[str]) -> list[str]:
    """Regions without V2 files that NHDPlus V2 does cover (``hrbuild.v2.no_v2_regions``)."""
    from hrbuild import v2
    skip = v2.no_v2_regions()
    return [v for v in missing(vpus, v2.V2_ROOT / "data", "lines2") if v not in skip]


def below_normal_priority() -> None:
    """This process, and the workers it starts, below normal priority (Windows)."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000)   # BELOW_NORMAL_PRIORITY_CLASS


def run(args: list[str]) -> int:
    print(">", " ".join(args[:4]), f"... ({len(args)} arguments)" if len(args) > 4 else "", flush=True)
    t0 = time.time()
    rc = cli(args)
    print(f"  exit {rc} in {(time.time() - t0) / 60:.1f} min", flush=True)
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["status", "convert", "sources", "values", "v2", "tables", "pack"])
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--full-priority", action="store_true", help="run at normal priority")
    args = ap.parse_args(argv)
    if not args.full_priority:
        below_normal_priority()
    vpus = regions()
    root = config.data_root_v2()
    print(json.dumps(status(vpus)), flush=True)
    if args.stage == "status":
        return 0
    if args.stage == "convert":
        left = [v for v in vpus if not (root / "parts" / f"{v}.json").exists()]
        return run(["convert2", *left, "--workers", str(args.workers)]) if left else 0
    if args.stage == "sources":
        return run(["sources", "nlcd", "tiger", "nwi", "gnatsgo", "--vpus", *vpus])
    if args.stage == "values":
        rc = 0
        for kind, prefix in VALUE_KINDS:
            left = missing(vpus, root / "values", prefix)
            if not left:
                continue
            if kind == "soils":                  # current SSURGO where the 2020 map units retired, first
                rc |= run(["values", "soils-patch", "--vpus", *left, "--workers", str(args.workers)])
            rc |= run(["values", kind, "--vpus", *left, "--workers", str(args.workers)])
        left = missing(vpus, root / "values_lean", "lc2")
        if left:
            rc |= run(["values", "lean", "--vpus", *left])
        return rc
    if args.stage == "v2":
        left = v2_missing(vpus)
        rc = run(["v2", *left]) if left else 0
        return rc | run(["tables", "streamcat"])
    if args.stage == "tables":
        # NWIS stops answering now and then; the pull resumes where it stopped, so wait and go on
        for attempt in range(1, 41):
            try:
                return run(["tables", "nwis", "--vpus", *vpus])
            except RuntimeError as exc:
                print(f"  NWIS stopped answering ({exc}); attempt {attempt} of 40, again in 5 minutes", flush=True)
                time.sleep(300)
        return 1
    if args.stage == "pack":
        rc = run(["--root", str(root), "pack"])           # the CLI's default root is version 1's
        return rc | run(["release", "pack"])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
