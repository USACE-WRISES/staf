"""Refit EASI's reference curves from evidence packages and report how they compare.

Reads ``easi-dev-members`` and ``easi-dev-fits`` (package folders, verified first), refits
every fit of the curve registry (or only the four quantities of the operational curves) with
the vendored fit recipe, and compares the refit against the package's registry and against
the 34 curves of a method file. ``--block-dev-paths`` refuses, for the whole run, every file
open under the builder's data root and tools, so a pass proves the packages alone suffice.

    python apps/stream-curves/scripts/refit_easi_curves.py --evidence <folder of package folders>
        [--operational] [--curves <reference-curves.json>] [--block-dev-paths] [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tracemalloc
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

BLOCKED = (r"D:\Data\easi-national", str(APP.parent.parent / "tools" / "easi-national"))
OPERATIONAL_QUANTITIES = ("natural_wsrp100", "woody_wsrp100", "q_cv_monthly", "er_median")


def block_dev_paths() -> list[str]:
    """Refuse every open of a file under a developer path for the rest of the process."""
    prefixes = tuple(os.path.normcase(os.path.abspath(p)) for p in BLOCKED)
    refused: list[str] = []

    def hook(event, args):
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            try:
                path = os.path.normcase(os.path.abspath(os.fsdecode(args[0])))
            except (TypeError, ValueError):
                return
            if path.startswith(prefixes):
                refused.append(path)
                raise PermissionError(f"developer path blocked during the refit: {path}")

    sys.addaudithook(hook)
    return refused


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--evidence", type=Path, required=True,
                    help="a folder holding the easi-dev-members and easi-dev-fits package folders")
    ap.add_argument("--operational", action="store_true",
                    help="refit only the quantities of the 34 operational curves")
    ap.add_argument("--curves", type=Path, default=APP / "streamcurves" / "_vendor" / "easi" / "data"
                    / "reference-curves.json", help="the method file's reference curves to compare with")
    ap.add_argument("--block-dev-paths", action="store_true")
    ap.add_argument("--panels", action="store_true",
                    help="also draw the panels again from easi-dev-universe and compare the members")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    refused = block_dev_paths() if a.block_dev_paths else None

    import pyarrow.parquet as pq
    from streamcurves import evidence_store as es
    from streamcurves.easi_method import refit

    t0 = time.perf_counter()
    tracemalloc.start()
    def package_dir(package_id: str) -> Path:
        """A package folder, or the single installed copy under an evidence store."""
        folder = a.evidence / package_id
        if (folder / "evidence.json").is_file():
            return folder
        copies = sorted(p for p in folder.glob("*") if (p / "evidence.json").is_file())
        if len(copies) != 1:
            raise SystemExit(f"{package_id}: expected one package under {folder}, found {len(copies)}")
        return copies[0]

    members_dir, fits_dir = package_dir("easi-dev-members"), package_dir("easi-dev-fits")
    report = {"evidence": str(a.evidence), "blockedDevPaths": bool(a.block_dev_paths)}
    for name, folder in (("members", members_dir), ("fits", fits_dir)):
        got = es.verify_folder(folder)
        report[f"{name}Package"] = {k: got[k] for k in ("packageId", "version", "dataDigest",
                                                          "packageDigest", "ok", "damaged", "unlisted")}
        if not got["ok"]:
            print(json.dumps(report, indent=1))
            raise SystemExit(f"{folder} does not verify")
    t_verify = time.perf_counter()
    members, values, panels = refit.load_members(members_dir)
    if a.panels:
        universe_dir = package_dir("easi-dev-universe")
        got = es.verify_folder(universe_dir)
        report["universePackage"] = {k: got[k] for k in ("packageId", "version", "dataDigest",
                                                           "packageDigest", "ok")}
        t_p = time.perf_counter()
        _, regenerated = refit.regenerate_members(universe_dir)
        report["panels"] = {**refit.same_members(regenerated, members),
                            "seconds": round(time.perf_counter() - t_p, 2)}
        print(f"panels: members regenerated from the universe, identical={report['panels']['identical']}")
    t_load = time.perf_counter()
    rows = refit.fit_registry(members, values, panels,
                              quantities=OPERATIONAL_QUANTITIES if a.operational else None)
    t_fit = time.perf_counter()
    stored = pq.read_table(fits_dir / "data" / "curve_registry.parquet").to_pylist()
    if a.operational:
        stored = [r for r in stored if r["quantity"] in OPERATIONAL_QUANTITIES]
    report["registry"] = refit.compare_registry(rows, stored)
    curves = refit.operational_curves(rows, members, values, panels)
    artifact = json.loads(a.curves.read_text(encoding="utf-8"))
    report["operationalCurves"] = refit.compare_curves(curves, artifact)
    current, peak = tracemalloc.get_traced_memory()
    report["timings"] = {"verifySeconds": round(t_verify - t0, 2), "loadSeconds": round(t_load - t_verify, 2),
                         "fitSeconds": round(t_fit - t_load, 2),
                         "totalSeconds": round(time.perf_counter() - t0, 2)}
    report["peakTracedMB"] = round(peak / 1e6, 1)
    report["fits"] = len(rows)
    if refused is not None:
        report["devPathOpensRefused"] = len(refused)
    text = json.dumps(report, indent=1, sort_keys=True, default=str)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n", encoding="utf-8")
    reg, cur = report["registry"], report["operationalCurves"]
    print(f"registry: {reg['compared']} fits compared, identical={reg['identical']} "
          f"({len(reg['differing'])} differing, {len(reg['onlyRefit'])} only refit, "
          f"{len(reg['onlyStored'])} only stored)")
    print(f"operational curves: {cur['identical']} of {cur['curves']} identical "
          f"({len(cur['differing'])} differing, {len(cur['missing'])} missing)")
    print(f"timings {report['timings']}, peak traced {report['peakTracedMB']} MB")
    ok = reg["identical"] and cur["allIdentical"] and (not a.panels or report["panels"]["identical"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
