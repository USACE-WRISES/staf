"""Runtime profile of ``compute_site``: stage timings and a budget calibration.

Runs the engine (the five watershed families, no cross-section) at the
acceptance-panel reaches and the covered-panel points, records per-stage
seconds from the progress callback, fits ``secs = a + b*reaches + c*hops`` on
the completed sites, and prints the largest ``maxReaches`` whose fitted p90
stays under the interactive envelope (300 s). ``provenance.INTERACTIVE_CONFIG``
takes its numbers from this output; the notes record the run.

Writes CSV+JSON to the gitignored out/ dir.

Examples:
  python scripts/engine_runtime_profile.py
  python scripts/engine_runtime_profile.py --per-box 1 --limit 4
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from site_engine import compute_site, hr, provenance       # noqa: E402

ENVELOPE_S = 300.0
FAMILIES = ["dams", "landcover", "roads", "runoff", "soils"]
_HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sites(per_box: int, limit: int | None) -> list[tuple[str, float, float]]:
    acceptance = _load("delineation_acceptance")
    comparison = _load("covered_reach_comparison")
    out: list[tuple[str, float, float]] = []
    boxes = acceptance.BOXES[:limit] if limit else acceptance.BOXES
    for label, blat, blon in boxes:
        recs = hr.flowlines_in_bbox(blon - acceptance.D, blat - acceptance.D,
                                    blon + acceptance.D, blat + acceptance.D)
        with_da = sorted((r for r in recs if r.get("totdasqkm")),
                         key=lambda r: r["totdasqkm"])
        picks = (with_da[: max(1, per_box // 2)]
                 + with_da[-(per_box - per_box // 2):]) if with_da else []
        for rec in picks:
            try:
                from shapely.geometry import shape
                pt = shape(rec["geometry"]).interpolate(0.5, normalized=True)
                out.append((f"{label} reach {rec['nhdplusid']}",
                            float(pt.y), float(pt.x)))
            except Exception:  # noqa: BLE001
                continue
    panel = comparison.PANEL[:limit] if limit else comparison.PANEL
    out.extend((label, lat, lon) for label, lat, lon in panel
               if not label.startswith(comparison.DAMMED_PREFIX))
    return out


def profile_site(label: str, lat: float, lon: float) -> dict:
    events: list[tuple[float, dict]] = []
    t0 = time.time()
    rec = compute_site(lat, lon, {"includeGeometry": False,
                                  "metricFamilies": FAMILIES},
                       progress=lambda e: events.append((time.time(), e)))
    total = round(time.time() - t0, 1)
    ws = rec.get("watershed") or {}
    row = {"label": label, "lat": lat, "lon": lon, "status": rec["status"],
           "reason": rec.get("reason") or "", "n_reaches": ws.get("nReaches"),
           "n_hops": ws.get("nHops"), "area_sqkm": ws.get("areaSqkm"),
           "secs_total": total}
    # Stage seconds: the time between an event and the next one.
    stamps = [(t, e.get("stage"), e.get("family")) for t, e in events]
    stamps.append((time.time(), "end", None))
    by_stage: dict[str, float] = {}
    for (t, stage, family), (t_next, _s, _f) in zip(stamps, stamps[1:]):
        key = f"secs_{stage}" if stage != "metrics" else f"secs_metrics_{family or 'all'}"
        by_stage[key] = round(by_stage.get(key, 0.0) + (t_next - t), 1)
    row.update(by_stage)
    print(f"  {label}: {rec['status']} reaches={row['n_reaches']} "
          f"hops={row['n_hops']} {total}s")
    return row


def fit(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["status"] == "ok" and r.get("n_reaches")]
    if len(ok) < 3:
        return {"n_fit": len(ok), "note": "too few completed sites to fit"}
    y = [float(r["secs_total"]) for r in ok]
    reaches = [float(r["n_reaches"]) for r in ok]
    hops = [float(r["n_hops"] or 0) for r in ok]
    try:
        import numpy as np
        X = np.column_stack([np.ones(len(ok)), reaches, hops])
        coef, *_ = np.linalg.lstsq(X, np.array(y), rcond=None)
        a, b, c = (float(v) for v in coef)
        resid = np.array(y) - X @ coef
        sd = float(resid.std(ddof=1)) if len(ok) > 3 else 0.0
    except Exception:  # noqa: BLE001 - numpy absent: per-reach ratio only
        b = statistics.median(t / r for t, r in zip(y, reaches))
        a, c, sd = 0.0, 0.0, 0.0
    hops_per_reach = statistics.median(h / r for h, r in zip(hops, reaches))
    n = 0
    while True:
        p90 = a + b * (n + 5) + c * hops_per_reach * (n + 5) + 1.28 * sd
        if p90 > ENVELOPE_S or n + 5 > 5000:
            break
        n += 5
    return {"n_fit": len(ok), "intercept_s": round(a, 1),
            "secs_per_reach": round(b, 2), "secs_per_hop": round(c, 2),
            "residual_sd_s": round(sd, 1),
            "hops_per_reach_median": round(hops_per_reach, 3),
            "envelope_s": ENVELOPE_S,
            "recommended_max_reaches": n,
            "recommended_max_hops": int(round(n * hops_per_reach)) + 5,
            "current_interactive": {
                "maxReaches": provenance.INTERACTIVE_CONFIG["maxReaches"],
                "maxHops": provenance.INTERACTIVE_CONFIG["maxHops"]}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--per-box", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    args = ap.parse_args()

    rows = []
    for label, lat, lon in _sites(args.per_box, args.limit):
        print(f"profiling {label} ...", flush=True)
        rows.append(profile_site(label, lat, lon))
    summary = {"n_sites": len(rows),
               "n_ok": sum(1 for r in rows if r["status"] == "ok"),
               "n_refused": sum(1 for r in rows if r["status"] == "refused"),
               "n_failed": sum(1 for r in rows if r["status"] == "failed"),
               "secs_median": (round(statistics.median(r["secs_total"] for r in rows), 1)
                               if rows else None),
               "fit": fit(rows)}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if rows:
        fieldnames = sorted({k for r in rows for k in r},
                            key=lambda k: (k not in ("label", "status", "reason"), k))
        with (out_dir / "engine_runtime_profile.csv").open(
                "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, restval="")
            w.writeheader()
            w.writerows(rows)
    (out_dir / "engine_runtime_profile.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2, default=str),
        encoding="utf-8")
    print("\n== summary ==")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
