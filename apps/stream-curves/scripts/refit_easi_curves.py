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
    """Refuse every open of a file under a developer path for the rest of the process: Python's
    own opens (an audit hook) and pyarrow's, which open files natively and never reach it."""
    prefixes = tuple(os.path.normcase(os.path.abspath(p)) for p in BLOCKED)
    refused: list[str] = []

    def check(target) -> None:
        if not isinstance(target, (str, bytes, os.PathLike)):
            return
        try:
            path = os.path.normcase(os.path.abspath(os.fsdecode(target)))
        except (TypeError, ValueError):
            return
        if path.startswith(prefixes):
            refused.append(path)
            raise PermissionError(f"developer path blocked during the refit: {path}")

    def hook(event, args):
        if event == "open" and args:
            check(args[0])

    sys.addaudithook(hook)
    import pyarrow.dataset as _ds
    import pyarrow.parquet as _pq
    for name in ("read_table", "read_schema", "read_metadata", "read_pandas"):
        original = getattr(_pq, name)

        def guarded(source, *args, _original=original, **kwargs):
            check(source)
            return _original(source, *args, **kwargs)

        setattr(_pq, name, guarded)
    parquet_file_init = _pq.ParquetFile.__init__

    def guarded_init(self, source, *args, **kwargs):
        check(source)
        return parquet_file_init(self, source, *args, **kwargs)

    _pq.ParquetFile.__init__ = guarded_init
    dataset_init = _pq.ParquetDataset.__init__

    def guarded_dataset(self, path_or_paths, *args, **kwargs):
        for x in (path_or_paths if isinstance(path_or_paths, (list, tuple)) else [path_or_paths]):
            check(x)
        return dataset_init(self, path_or_paths, *args, **kwargs)

    _pq.ParquetDataset.__init__ = guarded_dataset
    ds_dataset = _ds.dataset

    def guarded_ds(source, *args, **kwargs):
        for x in (source if isinstance(source, (list, tuple)) else [source]):
            check(x)
        return ds_dataset(source, *args, **kwargs)

    _ds.dataset = guarded_ds
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
        """A package folder, or the verified installed copy under an evidence store (the most
        recently installed when the store holds several versions)."""
        try:
            got = es.pick(a.evidence / package_id)
        except es.EvidenceError as exc:
            raise SystemExit(f"{package_id}: {exc}")
        print(f"{package_id}: {got}")
        return got

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
    report["recipe"] = refit.recipe_check(members_dir)
    words = refit.recipe_words(report["recipe"])
    if words:
        print(words)
    members, values, panels = refit.load_members(members_dir)
    if a.panels:
        universe_dir = package_dir("easi-dev-universe")
        got = es.verify_folder(universe_dir)
        report["universePackage"] = {k: got[k] for k in ("packageId", "version", "dataDigest",
                                                           "packageDigest", "ok")}
        if not got["ok"]:
            print(json.dumps(report, indent=1))
            raise SystemExit(f"{universe_dir} does not verify")
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
    from streamcurves import jobs
    report["peakWorkingSetMB"] = jobs.peak_memory_mb()
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
    print(f"timings {report['timings']}, peak working set {report['peakWorkingSetMB']} MB "
          f"(Python allocations {report['peakTracedMB']} MB)")
    ok = reg["identical"] and cur["allIdentical"] and (not a.panels or report["panels"]["identical"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
