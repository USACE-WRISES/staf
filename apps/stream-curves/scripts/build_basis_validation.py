"""Freeze a pre-registered basis run into the evidence a build is allowed to read.

Rules REF-08 and REF-09 gate on a verdict. A build must not re-decide what counts
as validated every time it runs, and it must not read a table that lives in the
gitignored notes folder, so the verdicts are reduced here to one committed file,
``config/basis_validation.yaml``, carrying the hash of the pre-registration they
were produced under.

    py -3.12 scripts/build_basis_validation.py --run <folder> --prereg <file>
    py -3.12 scripts/build_basis_validation.py --run <folder> --prereg <file> --check

``--check`` exits 1 when the committed file differs from what the run gives,
which is what a drift gate needs.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamcurves import basis_recovery as br        # noqa: E402
from streamcurves.paths import CONFIG_DIR            # noqa: E402

OUT_PATH = CONFIG_DIR / "basis_validation.yaml"
#: only these bases gate a rung; the rest are recorded in the notes, not here
GATING_BASES = ("1B_mixed_local", "3a_envelope", "3b_adjusted", "3c_matched")
SOURCES = ("model.csv", "transfer.csv")

HEADER = """\
# basis_validation.yaml -- GENERATED, do not hand-edit.
#
# The verdicts that admit a curve to rung REF-08 (national comparable donors) or
# REF-09 (a fitted expectation). A build reads this file rather than re-scoring,
# so what counts as validated is fixed before a build runs and is auditable
# afterwards.
#
# Regenerate with:
#   py -3.12 scripts/build_basis_validation.py --run <folder> --prereg <file>
# Check with --check, which exits 1 on drift.
#
# Only a VALIDATED verdict admits a rung. Every other verdict on a gating basis
# is recorded too, because a withheld metric has to say what the test found:
# "tested and failed" and "too few regions to test" are different blockers.
"""


#: The domain measure each basis is transported on, and whether more of it is
#: better ("min": the target must reach at least the least favourable passing
#: cell) or less is ("max"). Mirrors run_round_two_verdicts.DOMAIN_MEASURE, which
#: is what Pre-registration II's transportability condition was computed with.
DOMAIN_MEASURE = {"3a_envelope": ("n_fit", "min"), "3c_matched": ("distance_median", "max"),
                  "1A_percentile": ("n_fit", "min"), "3b_adjusted": ("extrapolation_ok", "min")}
PASSING_EXCEEDANCE = 0.10


def transport_range(records: pd.DataFrame, metric: str, basis: str, regime: str) -> dict:
    """The range of the cells where the accuracy criterion was actually met, on the
    basis's own domain measure. A target outside it was never tested in
    conditions like its own, so the rung does not admit it there."""
    measure, side = DOMAIN_MEASURE.get(basis, ("extrapolation_ok", "min"))
    cells = records[(records["metric"] == metric) & (records["basis"] == basis)
                    & (records["regime"].astype(str) == str(regime))]
    passing = cells[pd.to_numeric(cells.get("exceedance"), errors="coerce") >= PASSING_EXCEEDANCE]
    if measure not in passing.columns:
        return {}
    vals = pd.to_numeric(passing[measure], errors="coerce").dropna()
    if not len(vals):
        return {}
    return {"measure": measure, "better": side,
            "bound": round(float(vals.min() if side == "min" else vals.max()), 4),
            "passing_min": round(float(vals.min()), 4),
            "passing_max": round(float(vals.max()), 4),
            "n_passing_cells": int(len(vals))}


def collect(run: Path) -> pd.DataFrame:
    frames = []
    for name in SOURCES:
        p = run / name
        if p.exists():
            frames.append(pd.read_csv(p))
    if not frames:
        raise SystemExit(f"no {' or '.join(SOURCES)} in {run}")
    return pd.concat(frames, ignore_index=True)


def transport_verdicts(run: Path) -> dict:
    """``{(metric, basis): {l3: inside}}`` from the scorer's own transportability
    table, which is Pre-registration II's condition evaluated at each target."""
    p = run / "transportability.csv"
    if not p.exists():
        return {}
    out: dict = {}
    for r in pd.read_csv(p).to_dict("records"):
        inside = r.get("inside_tested_range")
        if isinstance(inside, str):
            inside = inside.strip().lower() == "true"
        elif inside != inside:          # NaN: the target could not be placed
            inside = None
        out.setdefault((str(r["metric"]), str(r["basis"])), {})[str(r["l3"])] = inside
    return out


#: which verdict a metric keeps when it was scored under more than one regime:
#: the strongest, and among failures the one that says the most about why
RANK = {br.VALIDATED: 6, br.PROMISING: 5, br.UNSUPPORTED_COVERAGE: 4, br.UNSUPPORTED: 3,
        br.NOT_QUANTIFIED: 2, br.NOT_EVALUATED: 1}


def build(run: Path, prereg: Path | None) -> dict:
    records = collect(run)
    scored = br.score_verdicts(records, rule="prereg-2")
    transport_by_target = transport_verdicts(run)
    metrics: dict = {}
    for row in scored.to_dict("records"):
        basis = str(row["basis"])
        if basis not in GATING_BASES:
            continue
        verdict = str(row["verdict"])
        if verdict not in RANK:
            continue
        entry = metrics.setdefault(str(row["metric"]), {})
        keep = {"verdict": verdict, "regime": str(row.get("regime") or ""),
                "n_cells": int(row.get("n_cells") or 0)}
        for k in ("err_iqr", "net_opt", "exceed_median"):
            if row.get(k) is not None and row.get(k) == row.get(k):
                keep[k] = float(row[k])
        if row.get("contain") is not None and row.get("contain") == row.get("contain"):
            keep["contained"] = int(row["contain"])
        if verdict in (br.VALIDATED, br.PROMISING):
            # where a basis passed or nearly did, the range it was tested over
            rng = transport_range(records, str(row["metric"]), basis,
                                  str(row.get("regime") or ""))
            targets = transport_by_target.get((str(row["metric"]), basis))
            if targets:
                rng = dict(rng or {})
                rng["targets"] = {k: targets[k] for k in sorted(targets)}
            if rng:
                keep["transport"] = rng
        prev = entry.get(basis)
        # a metric can be scored under both regimes; keep the stronger verdict,
        # and on a tie the one tested on more cells
        if prev is None or RANK[verdict] > RANK[prev["verdict"]] or (
                prev["verdict"] == verdict and keep["n_cells"] > prev["n_cells"]):
            entry[basis] = keep
    doc = {
        "version": 1,
        "source": str(run.as_posix()),
        "preregistration": prereg.name if prereg else None,
        "preregistration_sha256": (hashlib.sha256(prereg.read_bytes()).hexdigest()
                                   if prereg and prereg.exists() else None),
        "gating_bases": list(GATING_BASES),
        "metrics": {k: metrics[k] for k in sorted(metrics)},
    }
    return doc


def render(doc: dict) -> str:
    return HEADER + "\n" + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, help="a basis-run output folder")
    ap.add_argument("--prereg", help="the pre-registration the run was made under")
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    doc = build(Path(a.run), Path(a.prereg) if a.prereg else None)
    text = render(doc)
    out = Path(a.out)
    if a.check:
        if not out.exists():
            print(f"[basis] {out} is missing")
            return 1
        have = out.read_text(encoding="utf-8")
        same = have == text
        print(f"[basis] {'up to date' if same else 'STALE'}: {out}")
        return 0 if same else 1
    out.write_text(text, encoding="utf-8")
    n = sum(len(v) for v in doc["metrics"].values())
    print(f"[basis] wrote {out} ({len(doc['metrics'])} metrics, {n} gating verdicts)")
    for mk, by_basis in doc["metrics"].items():
        for basis, rec in by_basis.items():
            print(f"    {mk:<20} {basis:<16} {rec['verdict']:<10} regime {rec['regime']}"
                  f"  {rec['n_cells']} cells")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
