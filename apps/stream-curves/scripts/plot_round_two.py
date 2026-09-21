"""Curve plots for round two: what a basis would actually propose at a target.

One panel per unassessed Eastern Corn Belt Plains function, showing the best-ranked
basis for that function: the region's own observations, the proposed anchors with
their resampling interval, the curve the engine builds from them, and the region's
local best-available comparison where there is one. A panel that reads "no basis
reached validated or promising" is as much a result as a curve.

    py -3.12 scripts/plot_round_two.py --out <folder>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                      # noqa: E402
import numpy as np                                   # noqa: E402
import pandas as pd                                  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamcurves import basis_recovery as br        # noqa: E402
from streamcurves import basis_transfer as bt        # noqa: E402
from streamcurves import curves as cv                # noqa: E402
from streamcurves import pressure_evidence as pe     # noqa: E402

FUNCTIONS = {"community-dynamics": "Community dynamics",
             "population-support": "Population support",
             "nutrient-cycling": "Nutrient cycling",
             "water-soil-quality": "Water and soil quality"}
RANK = {br.VALIDATED: 0, br.VALIDATED_EXTERNAL: 1, br.PROMISING: 2,
        br.UNSUPPORTED_COVERAGE: 3, br.NOT_QUANTIFIED: 4, br.NOT_EVALUATED: 5,
        br.UNSUPPORTED: 6}
TARGET = "55"


def panel(ax, *, cfg, obs, points, anchors, ci, local, title, subtitle):
    ax.axhspan(0, 0.39, color="#f4d6d2", alpha=.5, lw=0)
    ax.axhspan(0.39, 0.69, color="#fbecc2", alpha=.5, lw=0)
    ax.axhspan(0.69, 1.0, color="#d5ead3", alpha=.5, lw=0)
    hi = float(np.nanquantile(obs, 0.98)) if len(obs) else 1.0
    if points:
        hi = max(hi, max(p["x"] for p in points))
    xs = np.linspace(0, hi * 1.05, 400)
    if points:
        ax.plot(xs, [cv.interp_curve(points, float(x)) for x in xs], color="#1f4e8c",
                lw=2, zorder=5, label="proposed curve")
    for a in (anchors or []):
        if a is not None and np.isfinite(a):
            ax.axvline(a, color="#1f4e8c", ls="--", lw=1, zorder=4)
    for lo, high in (ci or []):
        if lo is not None and high is not None and np.isfinite(lo) and np.isfinite(high):
            ax.axvspan(lo, high, color="#1f4e8c", alpha=.16, lw=0)
    if local:
        ax.axvspan(local[0], local[1], color="#9a6a00", alpha=.14, lw=0,
                   label="local best available (not reference)")
    if len(obs):
        ax.plot(obs, np.full(len(obs), -0.045), "|", color="#33415c", ms=7, alpha=.8,
                label=f"{len(obs)} observations in region")
    ax.set_ylim(-0.08, 1.05)
    ax.set_xlim(0, hi * 1.05)
    units = str(cfg.get("units") or "").strip()
    ax.set_xlabel(f"{cfg.get('display_name', '')} ({units})" if units
                  else str(cfg.get("display_name", "")))
    ax.set_ylabel("index")
    ax.set_title(title + "\n" + subtitle, fontsize=8.5, loc="left")
    ax.legend(fontsize=6.5, loc="upper right", framealpha=.9)


def run(out_dir: Path) -> Path:
    verdicts = pd.read_csv(out_dir / "verdicts.csv")
    funcs = pd.read_csv(out_dir / "function_summary.csv")
    targets = pd.concat([pd.read_csv(out_dir / n) for n in
                         ("transfer_targets.csv", "model_targets.csv")
                         if (out_dir / n).exists()], ignore_index=True)
    tp = out_dir / "transportability.csv"
    transport = pd.read_csv(tp) if tp.exists() else pd.DataFrame(
        columns=["metric", "basis", "l3", "inside_tested_range"])
    inp = pe.national_inputs()
    frame, mc = inp["frame"], inp["metric_config"]
    values = inp["values"]
    wide = values.set_index(values["site_id"].astype(str))
    keys = frame["station_key"].astype(str)
    reg = frame[frame["l3"].astype(str) == TARGET]

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.0))
    for ax, (fid, label) in zip(axes.ravel(), FUNCTIONS.items()):
        # best tier first, and among equals the one tested on more cells
        sub = funcs[(funcs["function"] == fid)].sort_values(
            ["rank", "n_cells"], ascending=[True, False])
        best = sub.iloc[0] if len(sub) else None
        if best is None or RANK.get(best["verdict"], 9) > 2:
            ax.axis("off")
            worst = "" if best is None else (
                f"\nbest tried: {best['metric']} on {best['basis']} ({best['verdict']})")
            ax.text(0.02, 0.6, f"{label}\n\nNo basis reached validated or promising{worst}",
                    fontsize=10, va="top", wrap=True)
            continue
        metric, basis = str(best["metric"]), str(best["basis"])
        row = targets[(targets["metric"] == metric) & (targets["basis"] == basis)
                      & (targets["l3"].astype(str) == TARGET)]
        cfg = mc[metric]
        obs = pd.Series(keys.map(pd.to_numeric(wide[metric], errors="coerce")).to_numpy(),
                        index=frame.index).reindex(reg.index).dropna()
        if not len(row):
            ax.axis("off")
            ax.text(0.02, 0.6, f"{label}\n\n{metric} on {basis}: nothing proposed at the target",
                    fontsize=10, va="top")
            continue
        r = row.iloc[0]
        if basis.startswith("4_"):
            points = bt.nrsa_tp_points(bt.majority_nars9(reg)[0])
            anchors, ci = [], []
        else:
            anchors = [r.get("pred_q25"), r.get("pred_q75")]
            points = br.curve_from_anchors((anchors[0], anchors[1]), cfg) \
                if all(pd.notna(a) for a in anchors) else None
            ci = [(r.get("ci_q25_lo"), r.get("ci_q25_hi")), (r.get("ci_q75_lo"), r.get("ci_q75_hi"))]
        # the local best-available comparison is recorded once per target and metric
        lc = targets[(targets["metric"] == metric) & (targets["l3"].astype(str) == TARGET)
                     & targets.get("local_q25", pd.Series(dtype=float)).notna()]
        local = ((float(lc["local_q25"].iloc[0]), float(lc["local_q75"].iloc[0]))
                 if len(lc) else None)
        caveat = ""
        moved = transport[(transport["metric"] == metric) & (transport["basis"] == basis)
                          & (transport["l3"].astype(str) == TARGET)] if len(transport) else transport
        if len(moved) and moved["inside_tested_range"].iloc[0] in (False, "False"):
            m = moved.iloc[0]
            caveat += (f"\noutside the range it passed in: {m['measure']} {m['target_value']:g} "
                       f"against {m['passing_min']:g} to {m['passing_max']:g}")
        if basis == "1A_percentile":
            caveat += "\nestimates best available in the region, not least-disturbed condition"
        elif (pd.notna(r.get("pred_q75")) and len(obs)
              and float(r["pred_q75"]) < float(obs.quantile(0.25))):
            caveat += "\nproposed reference sits below the region's own observations"
        panel(ax, cfg=cfg, obs=obs, points=points, anchors=anchors, ci=ci, local=local,
              title=f"{label}: {cfg.get('display_name', metric)} on {basis}",
              subtitle=(f"{best['verdict']}, {best['n_cells']} cells, "
                        f"error {best['err_iqr']} reference IQR, "
                        f"net optimism {best['net_opt']}{caveat}"))
    fig.suptitle("Round two: what each basis would propose for the Eastern Corn Belt Plains",
                 fontsize=11, y=0.985)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    path = out_dir / "round_two_ecbp.png"
    fig.savefig(path, dpi=170)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    print(f"wrote {run(Path(a.out))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
