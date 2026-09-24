"""Fit-only exploration grids, one resumable job per cell (``streamcurves.explore``).

    DEEP: every scored NRSA metric of each Level III region at the region, Level II and
    Level I reference pools (the fixed pressure screen, strict), from the in-app archive:

        python apps/stream-curves/scripts/explore_curves.py deep --out <campaign> \\
            --l3 50 --l3 58 [--metric phab_XEMBED ...] [--rung l3 --rung l2] [--workers 3]

    EASI: each fitted quantity at each stratification level, on the panels as built, or
    redrawn from the universe packages under another screen:

        python apps/stream-curves/scripts/explore_curves.py easi --out <campaign> \\
            --evidence <folder of package folders> [--quantity woody_wsrp100 ...] \\
            [--level nars9 --level l2] [--variant as-built --variant relaxed-first] [--workers 3]

The campaign folder gets ``candidates.jsonl`` (register-format candidates, purpose
exploration) and ``grid.csv``; running the same command again reuses every finished cell.
Nothing here builds, decides or publishes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from streamcurves import easi_env  # noqa: E402

easi_env.sanitize()

from streamcurves import explore, jobs  # noqa: E402


def _package(evidence: Path, package_id: str) -> Path:
    folder = evidence / package_id
    if (folder / "evidence.json").is_file():
        return folder
    copies = sorted(p for p in folder.glob("*") if (p / "evidence.json").is_file())
    if len(copies) != 1:
        raise SystemExit(f"{package_id}: expected one package under {folder}")
    return copies[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="kind", required=True)
    d = sub.add_parser("deep")
    d.add_argument("--out", required=True, type=Path)
    d.add_argument("--l3", action="append", required=True)
    d.add_argument("--metric", action="append", default=None)
    d.add_argument("--rung", action="append", default=None, choices=explore.RUNGS)
    d.add_argument("--dataset", default=None)
    d.add_argument("--workers", type=int, default=2)
    e = sub.add_parser("easi")
    e.add_argument("--out", required=True, type=Path)
    e.add_argument("--evidence", required=True, type=Path)
    e.add_argument("--quantity", action="append", default=None)
    e.add_argument("--level", action="append", default=None)
    e.add_argument("--variant", action="append", default=None, choices=explore.SCREEN_VARIANTS)
    e.add_argument("--workers", type=int, default=2)
    a = ap.parse_args(argv)
    campaign = a.out.name
    env = {"PYTHONPATH": str(APP)}
    if a.kind == "deep":
        from streamcurves import nrsa_dataset as nds
        dataset = a.dataset or nds.MULTI_CYCLE_DATASET_ID
        # the code, configuration and data a cell reads (the engine, the archive, the station
        # screen, the directions): a change to any of them is another cell
        code = jobs.tree_fingerprint(APP, ("streamcurves", "config", "data"))
        cells = [jobs.Job(kind="python", target="streamcurves.explore:deep_cell",
                          spec={"task": "explore-deep", "campaign": campaign, "l3": str(code_),
                                "dataset": dataset, "metrics": sorted(a.metric) if a.metric else None,
                                "rungs": list(a.rung) if a.rung else list(explore.RUNGS),
                                "code": code},
                          env=env, label=f"L3-{code_}") for code_ in a.l3]
    else:
        from streamcurves import evidence_store as evs
        from streamcurves.easi_method import fit_recipe as fr

        def verified(package_id: str):
            folder = _package(a.evidence, package_id)
            got = evs.verify_folder(folder)
            if not got["ok"]:
                raise SystemExit(f"{package_id} does not verify: {(got['damaged'] + got['unlisted'])[:3]}")
            return folder, got

        members, got_m = verified("easi-dev-members")
        digest = got_m["dataDigest"]
        variants = list(a.variant or ["as-built"])
        extra = {"membersPackageDigest": got_m["packageDigest"],
                 "code": jobs.tree_fingerprint(APP, ("streamcurves",))}
        if any(v != "as-built" for v in variants):
            universe, got_u = verified("easi-dev-universe")
            universe_values, got_v = verified("easi-dev-universe-values")
            extra.update({"universe": str(universe), "universeDigest": got_u["dataDigest"],
                          "universeValues": str(universe_values), "universeValuesDigest": got_v["dataDigest"]})
        quantities = list(a.quantity or ["woody_wsrp100", "natural_wsrp100", "q_cv_monthly", "er_median"])
        levels = list(a.level or ["nars9", "l2", "national"])
        cells = []
        for q in quantities:
            for level in levels:
                if fr.QUANTITIES[q].geometry and level != "national":
                    continue
                for v in variants:
                    cells.append(jobs.Job(
                        kind="python", target="streamcurves.explore:easi_cell",
                        spec={"task": "explore-easi", "campaign": campaign, "quantity": q, "level": level,
                              "variant": v, "members": str(members), "evidenceDigest": digest, **extra},
                        env=env, label=f"{q} {level} {v}"))
    summary = jobs.run(cells, a.out, workers=a.workers, meta={"task": f"explore-{a.kind}"},
                       on_event=lambda ev: print(f"[explore] {ev['label']}: {ev['event']}", flush=True))
    merged = explore.merge(a.out, [c.id for c in cells if jobs.completed(a.out, c) is not None])
    print(f"[explore] {merged['candidates']} candidates ({merged['built']} built) -> "
          f"{a.out / 'candidates.jsonl'}; {summary['counts']}")
    return 0 if summary["counts"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
