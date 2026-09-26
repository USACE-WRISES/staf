"""Refit the Round 4 candidate curve sets from the members package, as a resumable campaign.

    python apps/stream-curves/scripts/refit_easi_candidate_sets.py \\
        --members <easi-dev-members folder> --campaign <folder> --out <curve-sets.json> \\
        [--sets flow-min-ratio width-variability] [--workers 2]

The two sets the addendum's families E2 and E4 read (``refit.CANDIDATE_SETS``): ``q_min_ratio``
into ``flow-min-ratio`` and ``bankfull_width_cv`` into ``width-variability``, each fitted by
NARS-9 with a national fallback on the members package's strict panels with the vendored fit
recipe, grouped by stratum only (``fit_registry(by_stratum_only=...)``). ``--members`` is an
``easi-dev-members`` package folder, an evidence folder holding one, or an evidence store; it
is verified first and its data digest names the members the fits are on. The campaign folder
holds one job per quantity (``campaigns.refit_campaign``), so an interrupted or repeated run
redoes nothing already done. The output names each set in the method file's shape
(``refit.candidate_curves``) beside the recipe check, the members digest and every stratum
that gave no usable curve; ``build_easi_candidate_package.py --curve-sets`` reads it. Exit 1
when a requested set has no usable national curve.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def members_folder(source: Path):
    """The verified members package folder: the folder itself, the package under an
    evidence folder, or the installed copy under an evidence store."""
    from streamcurves import evidence_store as es
    source = Path(source)
    candidates = [source, source / "easi-dev-members"]
    last = None
    for c in candidates:
        try:
            return es.pick(c)
        except es.EvidenceError as exc:
            last = exc
    raise SystemExit(f"{source}: no easi-dev-members package found ({last})")


def main(argv=None) -> int:
    from streamcurves import evidence_store as es
    from streamcurves.easi_method import campaigns, refit
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--members", required=True, type=Path)
    ap.add_argument("--campaign", required=True, type=Path, help="the campaign folder (jobs, resumable)")
    ap.add_argument("--out", required=True, type=Path, help="the curve-sets.json to write")
    ap.add_argument("--sets", nargs="+", default=list(refit.CANDIDATE_SETS),
                    choices=list(refit.CANDIDATE_SETS), help="the sets to fit (default: both)")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args(argv)
    folder = members_folder(a.members)
    got = es.verify_folder(folder)
    if not got["ok"]:
        raise SystemExit(f"{folder} does not verify: damaged {got.get('damaged')}, unlisted {got.get('unlisted')}")
    wanted = {s: refit.CANDIDATE_SETS[s] for s in a.sets}
    quantities = sorted(set(wanted.values()))
    recipe = refit.recipe_check(folder)
    words = refit.recipe_words(recipe)
    if words:
        print(words)
    rows, summary = campaigns.refit_campaign(folder, a.campaign, members_digest=got["dataDigest"],
                                             workers=a.workers, quantities=quantities,
                                             by_stratum_only=quantities)
    sets, diagnostics = refit.candidate_curves(rows, sets=wanted)
    doc = {"schema": "staf-easi-candidate-curves", "schemaVersion": 1, "sets": sets,
           "provenance": {"membersPackage": str(folder), "membersDigest": got["dataDigest"],
                          "membersPackageDigest": got["packageDigest"], "recipe": recipe,
                          "fits": len(rows), "campaign": str(Path(a.campaign).resolve()),
                          "jobs": summary.get("counts"), "diagnostics": diagnostics,
                          "groupedBy": "stratum only (NARS-9 and national), the registry split "
                                       "and geometry rule set aside",
                          "builtAt": _now(),
                          "builder": "apps/stream-curves/scripts/refit_easi_candidate_sets.py"}}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(doc, indent=1, sort_keys=True, default=float) + "\n",
                     encoding="utf-8", newline="\n")
    missing = [s for s in wanted if s not in sets]
    for set_id in wanted:
        d = diagnostics.get(set_id) or {}
        print(f"{set_id}: curves {d.get('curves')}, not usable {d.get('notUsable')}"
              + (f", omitted: {d['omitted']}" if d.get("omitted") else ""))
    print(f"wrote {a.out} ({len(rows)} fits, jobs {summary.get('counts')})")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
