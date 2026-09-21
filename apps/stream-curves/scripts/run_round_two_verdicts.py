"""Verdicts for round two, and round one re-scored beside them.

Three tables, all from committed inputs so every number in the memo regenerates:

* ``verdicts.csv`` applies Pre-registration II to ``transfer.csv`` and ``model.csv``.
* ``round_one_reproduction.csv`` rebuilds round one's labels from its own per-cell
  records and checks them against the file it wrote. Round one's "promising" label
  came from a rule that was never registered (containment in at least half of
  cells), and reproducing it is the only way to compare the rounds honestly.
* ``round_one_rescored.csv`` measures A1, round two's accuracy criterion, on round
  one's recorded anchors. None of those candidates stated an interval of its own,
  so none can reach a tier under Pre-registration II; the tier each would have
  reached is reported with that flag, as information.

``function_summary.csv`` and ``memo_tables.md`` collect the result by the STAF
function each metric feeds, which is what the decision is actually about.

    py -3.12 scripts/run_round_two_verdicts.py --out <folder> --jobs 10
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamcurves import basis_recovery as br          # noqa: E402
from streamcurves import metric_map as mm              # noqa: E402
from streamcurves import pressure_evidence as pe       # noqa: E402

ROUND_ONE = (Path(__file__).resolve().parents[3] / "notes" / "2026-07-23_StreamCurves_Methodology"
             / "basis_validation" / "2026-09-20")
N_DRAWS, N_BOOT_TRUTH = 100, 300
#: The four functions the Eastern Corn Belt Plains cannot score, and the metrics
#: that would restore each.
ECBP_FUNCTIONS = {
    "community-dynamics": ["bent_EPT_NTAX", "bent_HPRIME", "bent_TOLRPIND"],
    "population-support": ["fish_NAT_TOTLNTAX", "fish_NAT_NTOLNTAX", "bent_TOTLNTAX"],
    "nutrient-cycling": ["chem_PTL", "chem_NTL", "chem_NTL_DISS"],
    "water-soil-quality": ["chem_COND", "chem_TURB", "chem_PH"]}

_STATE: dict = {}


def _init() -> None:
    warnings.simplefilter("ignore")
    inp = pe.national_inputs()
    frame, values = inp["frame"], inp["values"]
    _STATE.update({"frame": frame, "mc": inp["metric_config"],
                   "wide": values.set_index(values["site_id"].astype(str)),
                   "keys": frame["station_key"].astype(str),
                   "l3": frame["l3"].astype(str)})


def _series(metric: str) -> pd.Series:
    s = _STATE
    return pd.Series(s["keys"].map(pd.to_numeric(s["wide"][metric], errors="coerce")).to_numpy(),
                     index=s["frame"].index)


def rescore_metric(metric: str, rows: pd.DataFrame, seed: int) -> list[dict]:
    """A1 for round one's recorded anchors, cell by cell.

    A metric round one tested may since have left the portfolio (dissolved
    nitrogen was replaced by total nitrogen in Pre-registration III). Its old
    evidence is still readable, so the re-scoring says so and moves on rather
    than failing the whole run.
    """
    frame, l3 = _STATE["frame"], _STATE["l3"]
    cfg = _STATE["mc"].get(metric)
    if cfg is None:
        return [{"metric": metric, "l3": None, "basis": None,
                 "info": "no longer a default-selected metric; round one evidence not re-scored"}]
    series = _series(metric)
    out = []
    for code, g in rows.groupby(rows["l3"].astype(str)):
        reg = frame[l3 == code]
        ref = series.reindex(reg.index)[reg["pass_strict"].astype(bool)].dropna()
        evald = series.reindex(reg.index).dropna()
        truth = br.cell_truth(ref, evald, cfg, clusters=reg["huc12"], n_boot=N_BOOT_TRUTH,
                              seed=br.cell_seed(metric, code, "truth", seed=seed))
        if truth is None:
            continue
        for _, r in g.iterrows():
            if not np.isfinite(r.get("pred_q25", np.nan)):
                continue
            anchors = (float(r["pred_q25"]), float(r["pred_q75"]))
            n_fit = None if str(r["option"]).startswith("1B") else r.get("n_fit")
            n_fit = None if n_fit is None or not np.isfinite(n_fit) else int(n_fit)
            rec = br.candidate_record(truth, anchors=anchors, n_fit=n_fit, n_draws=N_DRAWS)
            out.append({"metric": metric, "l3": code, "region": r.get("region"),
                        "basis": r["option"], "regime": "round-one",
                        "kind": "model" if str(r["option"]).startswith("1B") else "population",
                        "n_fit": r.get("n_fit"), "extrapolation_ok": r.get("extrapolation_ok"),
                        "flip": rec["flip"], "exceedance": rec["exceedance"],
                        "var_n_draw": rec["var_n_draw"], "both_in": rec["both_in"],
                        "net_optimism": rec["net_optimism"],
                        "err_abs_mean": rec["err_abs_mean"], "interval_ok": False})
    return out


#: The domain measure each basis is transported on, and whether more of it is
#: better ("min") or less is ("max"). Pre-registration II: a basis that passes
#: where it was tested is recommended for a target only when the target sits inside
#: the range the passing cells spanned.
DOMAIN_MEASURE = {"3a_envelope": ("n_fit", "min"), "3c_matched": ("distance_median", "max"),
                  "1A_percentile": ("n_fit", "min"), "3b_adjusted": ("extrapolation_ok", "min")}
REACHED = (br.VALIDATED, br.VALIDATED_EXTERNAL, br.PROMISING)


def transportability(records: pd.DataFrame, verdicts: pd.DataFrame,
                     targets: pd.DataFrame) -> pd.DataFrame:
    """For every basis that reached a tier, whether each target is inside the range
    of the cells where its accuracy criterion was actually met."""
    rows = []
    for _, v in verdicts[verdicts["verdict"].isin(REACHED)].iterrows():
        measure, side = DOMAIN_MEASURE.get(str(v["basis"]), ("extrapolation_ok", "min"))
        cells = records[(records["metric"] == v["metric"]) & (records["basis"] == v["basis"])
                        & (records["regime"] == v["regime"])]
        passing = cells[pd.to_numeric(cells["exceedance"], errors="coerce") >= 0.10]
        if measure not in passing.columns or not len(passing):
            continue
        vals = pd.to_numeric(passing[measure], errors="coerce").dropna()
        lo, hi = (float(vals.min()), float(vals.max())) if len(vals) else (np.nan, np.nan)
        got = targets[(targets["metric"] == v["metric"]) & (targets["basis"] == v["basis"])]
        for _, t in got.iterrows():
            val = pd.to_numeric(pd.Series([t.get(measure)]), errors="coerce").iloc[0]
            inside = (None if not np.isfinite(val) or not np.isfinite(lo)
                      else bool(val >= lo) if side == "min" else bool(val <= hi))
            rows.append({"metric": v["metric"], "basis": v["basis"], "regime": v["regime"],
                         "verdict": v["verdict"], "l3": t.get("l3"), "region": t.get("region"),
                         "measure": measure, "better": side, "target_value": val,
                         "passing_min": lo, "passing_max": hi, "n_passing_cells": int(len(vals)),
                         "inside_tested_range": inside})
    return pd.DataFrame(rows)


def function_rows(verdicts: pd.DataFrame) -> pd.DataFrame:
    """The verdicts arranged by the ECBP function each metric would restore."""
    rank = {br.VALIDATED: 0, br.VALIDATED_EXTERNAL: 1, br.PROMISING: 2,
            br.UNSUPPORTED_COVERAGE: 3, br.NOT_QUANTIFIED: 4, br.NOT_EVALUATED: 5,
            br.UNSUPPORTED: 6}
    out = []
    for function, metrics in ECBP_FUNCTIONS.items():
        names = {m: (mm.metric_map_functions_for(m) or [{}])[0].get("functionName", "")
                 for m in metrics}
        for m in metrics:
            got = verdicts[verdicts["metric"] == m]
            for _, r in got.iterrows():
                out.append({"function": function, "function_name": names.get(m, ""),
                            "metric": m, "basis": r["basis"], "regime": r["regime"],
                            "kind": r.get("kind"), "n_cells": r.get("n_cells"),
                            "verdict": r["verdict"], "rank": rank.get(r["verdict"], 9),
                            "err_iqr": r.get("err_iqr"), "net_opt": r.get("net_opt"),
                            "a1_share": r.get("a1_share"), "c1_share": r.get("c1_share"),
                            "exceed_median": r.get("exceed_median"), "info": r.get("info")})
    return pd.DataFrame(out).sort_values(["function", "rank", "metric", "basis"])


def memo_tables(verdicts: pd.DataFrame, funcs: pd.DataFrame, repro: pd.DataFrame) -> str:
    lines = ["# Round two, generated tables", "",
             f"Generated {date.today().isoformat()} by `run_round_two_verdicts.py`.", "",
             "## Verdicts by basis", ""]
    tally = (verdicts.groupby(["basis", "regime", "verdict"]).size()
             .rename("n").reset_index().sort_values(["basis", "regime"]))
    lines += ["| basis | regime | verdict | metrics |", "|---|---|---|---|"]
    lines += [f"| `{r.basis}` | {r.regime} | {r.verdict} | {r.n} |" for r in tally.itertuples()]
    lines += ["", "## The four Eastern Corn Belt Plains functions", ""]
    for function in ECBP_FUNCTIONS:
        sub = funcs[funcs["function"] == function]
        best = sub.iloc[0] if len(sub) else None
        lines += [f"### {function}", ""]
        if best is None or best["rank"] >= 3:
            lines += ["No basis reached validated or promising.", ""]
        lines += ["| metric | basis | regime | cells | verdict | err (IQR) | net optimism | A1 share |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in sub.itertuples():
            lines.append(f"| `{r.metric}` | `{r.basis}` | {r.regime} | {r.n_cells} | "
                         f"{r.verdict} | {r.err_iqr} | {r.net_opt} | {r.a1_share} |")
        lines.append("")
    same = int((repro["verdict_old"] == repro["verdict_new"]).sum()) if len(repro) else 0
    lines += ["## Round one, reproduced", "",
              f"{same} of {len(repro)} round-one verdicts rebuilt from its own per-cell "
              "records by the committed scorer.", ""]
    return "\n".join(lines)


def run(out_dir: Path, *, seed: int, jobs: int, round_one: Path) -> dict:
    _init()
    frames = []
    for name in ("transfer.csv", "model.csv"):
        p = out_dir / name
        if p.exists():
            frames.append(pd.read_csv(p))
    if not frames:
        raise SystemExit(f"no transfer.csv or model.csv in {out_dir}")
    records = pd.concat(frames, ignore_index=True)
    verdicts = br.score_verdicts(records, rule="prereg-2")

    three = pd.read_csv(round_one / "three_options.csv")
    rebuilt = br.score_verdicts(three, rule="round-one").rename(columns={"basis": "option"})
    old = pd.read_csv(round_one / "verdicts.csv")
    repro = old.merge(rebuilt, on=["metric", "option"], suffixes=("_old", "_new"))
    repro["matches"] = repro["verdict_old"] == repro["verdict_new"]

    metrics = sorted(three["metric"].unique())
    rescored: list[dict] = []
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs, initializer=_init) as pool:
            futs = {m: pool.submit(rescore_metric, m, three[three["metric"] == m], seed)
                    for m in metrics}
            for m in metrics:
                rescored += futs[m].result()
                print(f"[verdicts] re-scored {m}", flush=True)
    else:
        for m in metrics:
            rescored += rescore_metric(m, three[three["metric"] == m], seed)
            print(f"[verdicts] re-scored {m}", flush=True)
    rescored_df = pd.DataFrame(rescored)
    # Round one recorded no interval for any candidate, so under Pre-registration II
    # every one of them is "not quantified" and can reach no tier. To say what its
    # accuracy alone would have supported, the tier is computed with that one
    # requirement waived, and the waiver is carried in the table.
    r1_tiers = pd.DataFrame()
    if len(rescored_df):
        waived = rescored_df.assign(interval_ok=True)
        r1_tiers = br.score_verdicts(waived, rule="prereg-2")
        r1_tiers["interval_requirement"] = "waived: round one recorded no candidate interval"
        r1_tiers["verdict_under_prereg_2"] = br.NOT_QUANTIFIED

    targets = pd.concat([pd.read_csv(out_dir / n) for n in
                         ("transfer_targets.csv", "model_targets.csv")
                         if (out_dir / n).exists()], ignore_index=True)
    moved = transportability(records, verdicts, targets)
    funcs = function_rows(verdicts)
    out_dir.mkdir(parents=True, exist_ok=True)
    moved.to_csv(out_dir / "transportability.csv", index=False)
    verdicts.to_csv(out_dir / "verdicts.csv", index=False)
    repro.to_csv(out_dir / "round_one_reproduction.csv", index=False)
    rescored_df.to_csv(out_dir / "round_one_rescored.csv", index=False)
    if len(r1_tiers):
        r1_tiers.to_csv(out_dir / "round_one_tiers.csv", index=False)
    funcs.to_csv(out_dir / "function_summary.csv", index=False)
    (out_dir / "memo_tables.md").write_text(memo_tables(verdicts, funcs, repro), encoding="utf-8")
    summary = {"generated": date.today().isoformat(), "n_records": int(len(records)),
               "n_verdicts": int(len(verdicts)),
               "round_one_reproduced": int(repro["matches"].sum()), "round_one_rows": int(len(repro)),
               "verdict_tally": verdicts["verdict"].value_counts().to_dict()}
    (out_dir / "verdicts.json").write_text(json.dumps(summary, indent=1, default=str),
                                           encoding="utf-8")
    return {"summary": summary, "verdicts": verdicts, "functions": funcs}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--round-one", default=str(ROUND_ONE))
    a = ap.parse_args(argv)
    got = run(Path(a.out), seed=a.seed, jobs=a.jobs, round_one=Path(a.round_one))
    print(json.dumps(got["summary"], indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
