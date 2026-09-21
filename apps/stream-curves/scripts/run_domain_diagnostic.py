"""Step 2 of Pre-registration II: where a borrowed or modelled reference would be
predicted, and what keeps donors out.

No candidate is scored here and no reference anchor is read, so nothing in this
run can bias a verdict. It answers three questions the verdicts depend on:

* **Why C3 failed.** Round one's extrapolation share is the joint share over five
  natural covariates. Decomposed per covariate, and recomputed without the two
  climate covariates, it shows whether a region is outside the training data
  because its streams are unlike them or because its climate is. The round-one
  columns reproduce round one's C3 exactly, as a check on the decomposition.
* **How far a model must reach.** The disturbance-axis gap: each region's least
  disturbed station minus the prediction point, per pressure.
* **What blocks a donor.** For the two targets and every metric in scope, each
  envelope condition counted on its own, and whether the target's envelope on a
  covariate is narrow or shifted.

    py -3.12 scripts/run_domain_diagnostic.py --out <folder>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamcurves import basis_recovery as br          # noqa: E402
from streamcurves import basis_transfer as bt          # noqa: E402
from streamcurves import pressure_evidence as pe       # noqa: E402
from streamcurves import reference_pool as rp          # noqa: E402

NATURAL = ["drainage_area_sqkm", "nhd_slope", "tmean8110ws", "precip8110ws", "bfiws"]
TARGETS = {"55": "Eastern Corn Belt Plains", "71": "Interior Plateau"}
METRICS = ["bent_EPT_NTAX", "bent_HPRIME", "bent_TOLRPIND", "bent_TOTLNTAX",
           "fish_NAT_TOTLNTAX", "fish_NAT_NTOLNTAX", "chem_CHLA", "chem_COND",
           "chem_NTL", "chem_PH", "chem_PTL", "chem_TURB"]


def prereg_hash(path: Path | None) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path and path.exists() else None


def testable_split(frame: pd.DataFrame, *, min_reference: int, seed: int):
    testable = [c for c, g in frame.groupby(frame["l3"].astype(str))
                if int(g["pass_strict"].sum()) >= min_reference and len(g) >= 25]
    dev, ev = br.split_regions(testable, seed=seed)
    return testable, dev, ev


def run(out_dir: Path | None, *, seed: int, min_reference: int, prereg: Path | None) -> dict:
    inp = pe.national_inputs()
    frame = inp["frame"]
    testable, dev, ev = testable_split(frame, min_reference=min_reference, seed=seed)
    target_vec = br.reference_pressure_vector(frame)
    ecbp = frame[frame["l3"].astype(str) == "55"]
    threshold = float(pd.to_numeric(ecbp["agriculture_ws"], errors="coerce").min())
    l3 = frame["l3"].astype(str)
    in_eval = l3.isin(ev)
    dev_rows = frame[l3.isin(dev)]

    rows = []
    for code in sorted(set(testable) | set(TARGETS), key=lambda c: (len(c), c)):
        reg = frame[l3 == code]
        nonref = reg[~reg["pass_strict"].astype(bool)]
        # round two's training rows for this region: outside the evaluation regions
        # and outside the region, plus the region's own non-reference stations
        train2 = pd.concat([frame[~in_eval & (l3 != code)], nonref])
        role = "eval" if code in ev else "dev" if code in dev else "target"
        c1 = br.coverage_by_covariate(dev_rows, reg, NATURAL) if role == "eval" else {}
        c2 = br.coverage_by_covariate(train2, reg, NATURAL)
        rec = {"l3": code, "region": str(reg["l3_name"].iloc[0]), "role": role,
               "n_frame": int(len(reg)), "n_reference": int(reg["pass_strict"].sum()),
               "n_nonref": int(len(nonref)),
               "n_nonref_at_ecbp_agriculture": int(
                   (pd.to_numeric(nonref["agriculture_ws"], errors="coerce") >= threshold).sum()),
               "r1_joint": c1.get("joint"), "r1_joint_no_climate": c1.get("joint_no_climate"),
               **{f"r1_{k}": v for k, v in (c1.get("by") or {}).items()},
               "r2_joint": c2.get("joint"), "r2_joint_no_climate": c2.get("joint_no_climate"),
               **{f"r2_{k}": v for k, v in (c2.get("by") or {}).items()},
               **br.disturbance_gap(reg, target_vec)}
        rows.append(rec)
    domain = pd.DataFrame(rows)

    env_rows = []
    for code, name in TARGETS.items():
        reg = frame[l3 == code]
        donors = bt.national_reference(frame, exclude_l3=code)
        for metric in METRICS:
            if not rp.family_profile(metric):
                continue
            rec = {"l3": code, "region": name, "metric": metric,
                   **bt.envelope_failures(metric, reg, donors)}
            rec["lith_groups"] = ",".join(rec.get("lith_groups") or [])
            for cov in (rp.family_profile(metric) or {}).get("covariates") or []:
                pos = bt.envelope_position(reg, donors, cov)
                for k in ("width_ratio", "target_median_pct", "target_lo_pct", "target_hi_pct"):
                    rec[f"{cov}_{k}"] = pos.get(k)
            env_rows.append(rec)
    envelope = pd.DataFrame(env_rows)

    summary = {"generated": date.today().isoformat(), "seed": seed,
               "preregistration_sha256": prereg_hash(prereg),
               "development_regions": dev, "evaluation_regions": ev,
               "prediction_point": target_vec, "ecbp_agriculture_minimum": threshold,
               "regime_e_evaluable": sorted(
                   domain.loc[(domain["role"] != "target")
                              & (domain["n_nonref_at_ecbp_agriculture"] >= 10), "l3"].tolist(),
                   key=lambda c: (len(c), c))}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        domain.to_csv(out_dir / "domain_diagnostic.csv", index=False)
        envelope.to_csv(out_dir / "envelope_diagnostic.csv", index=False)
        (out_dir / "domain_diagnostic.json").write_text(
            json.dumps(summary, indent=1, default=str), encoding="utf-8")
    return {"summary": summary, "domain": domain, "envelope": envelope}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--min-reference", type=int, default=12)
    ap.add_argument("--prereg", default=None, help="the pre-registration this run is under")
    a = ap.parse_args(argv)
    got = run(Path(a.out) if a.out else None, seed=a.seed, min_reference=a.min_reference,
              prereg=Path(a.prereg) if a.prereg else None)
    print(json.dumps(got["summary"], indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
