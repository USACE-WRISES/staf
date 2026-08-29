"""Validation study 2: V2/HR co-location agreement (plan gate G3).

For points on the covered network, compare the reach-scale inputs the two
networks supply for the SAME stream: slope, sinuosity, drainage area, and
FCode from the V2 flowline (what EASI has always used) against the nearest HR
flowline (what a Phase 2 re-anchored run would use). The seam between the two
is the method difference a routed site experiences, so this harness quantifies
it where both networks map the stream.

Expectation to verify (documented in the coverage plan): HR sinuosity reads
systematically higher than V2 because the 1:24k geometry is sharper; the
hyporheic best-of rule means that can only help a rating, never lower it.

Writes a CSV + JSON summary to the gitignored out/ dir. Diagnostic harness —
changes no production code path.

Examples:
  python scripts/compare_v2_hr_reaches.py                # default point set
  python scripts/compare_v2_hr_reaches.py --limit 3
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from easi import delineation                      # noqa: E402
from easi.datasources import flowlines, nhd_hr    # noqa: E402

# label, lat, lon — covered-network points spanning stream sizes and regions.
# The first three are the repo's standing diagnostic points.
DEFAULT_POINTS = [
    ("Scioto River at Columbus OH", 39.9550, -83.0030),
    ("Headwater trib near Worthington OH", 40.0962, -83.0203),
    ("Olentangy River at Delaware OH", 40.2987, -83.0680),
    ("Sugar Run near Waldo OH", 40.3101, -83.0563),
    ("Mid-Atlantic stream (PA)", 40.80, -76.50),
    ("South Atlantic stream (GA)", 33.50, -83.30),
    ("Great Lakes stream (MI)", 43.50, -84.60),
    ("Ozark stream (AR/OK)", 35.50, -94.50),
    ("Mountain stream (CO)", 39.00, -107.50),
    ("Pacific Northwest stream (WA)", 46.90, -122.20),
]

D = 0.012  # half-box for the local network fetches


def compare_point(label: str, lat: float, lon: float) -> dict | None:
    v2_fc = flowlines.flowlines_in_bbox(lon - D, lat - D, lon + D, lat + D)
    v2_hit = flowlines.nearest_point_on_lines(v2_fc, lat, lon)
    if v2_hit is None:
        print(f"  {label}: no V2 flowline nearby, skipped")
        return None
    v2_lat, v2_lon, v2_dist, comid = v2_hit
    hr_fc = nhd_hr.hr_flowlines_in_bbox(lon - D, lat - D, lon + D, lat + D)
    # Snap the HR comparison at the V2 snap point so both describe one place.
    hr_hit = nhd_hr.nearest_point_on_hr_lines(hr_fc, v2_lat, v2_lon)
    if hr_hit is None or comid is None:
        print(f"  {label}: no HR flowline nearby, skipped")
        return None
    v2 = delineation.flowline_attrs(comid)
    hr = nhd_hr.hr_attrs(hr_hit[3])

    def _delta(a, b):
        return (None if a is None or b is None else round(float(b) - float(a), 5))

    row = {
        "label": label, "lat": lat, "lon": lon,
        "comid": comid, "nhdplusid": hr_hit[3],
        "v2_hr_offset_ft": round(hr_hit[2], 1),
        "v2_slope": v2.get("slope"), "hr_slope": hr.get("slope"),
        "slope_delta": _delta(v2.get("slope"), hr.get("slope")),
        "v2_sinuosity": v2.get("sinuosity"), "hr_sinuosity": hr.get("sinuosity"),
        "sinuosity_delta": _delta(v2.get("sinuosity"), hr.get("sinuosity")),
        "v2_da_sqkm": v2.get("drainage_area_sqkm"),
        "hr_da_sqkm": hr.get("drainage_area_sqkm"),
        "da_ratio_hr_over_v2": (
            None if not v2.get("drainage_area_sqkm") or not hr.get("drainage_area_sqkm")
            else round(hr["drainage_area_sqkm"] / v2["drainage_area_sqkm"], 3)),
        "v2_fcode": v2.get("fcode"), "hr_fcode": hr.get("fcode"),
        "fcode_same_class": (None if v2.get("fcode") is None or hr.get("fcode") is None
                             else v2["fcode"] == hr["fcode"]),
        "huc8_match": (None if not v2.get("huc8") or not hr.get("huc8")
                       else v2["huc8"] == hr["huc8"]),
    }
    print(f"  {label}: offset {row['v2_hr_offset_ft']} ft, "
          f"sinuosity {row['v2_sinuosity']} -> {row['hr_sinuosity']}, "
          f"slope {row['v2_slope']} -> {row['hr_slope']}, "
          f"DA ratio {row['da_ratio_hr_over_v2']}")
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    args = ap.parse_args()

    points = DEFAULT_POINTS[: args.limit] if args.limit else DEFAULT_POINTS
    rows = []
    for label, lat, lon in points:
        print(f"comparing {label} ...", flush=True)
        row = compare_point(label, lat, lon)
        if row:
            rows.append(row)

    sin_deltas = [r["sinuosity_delta"] for r in rows
                  if r["sinuosity_delta"] is not None]
    slope_deltas = [r["slope_delta"] for r in rows if r["slope_delta"] is not None]
    summary = {
        "n_points": len(rows),
        "sinuosity_delta_median": round(statistics.median(sin_deltas), 4)
        if sin_deltas else None,
        "sinuosity_hr_higher_share": round(
            sum(1 for x in sin_deltas if x > 0) / len(sin_deltas), 2)
        if sin_deltas else None,
        "slope_delta_median": round(statistics.median(slope_deltas), 5)
        if slope_deltas else None,
        "fcode_same_class_share": round(
            sum(1 for r in rows if r["fcode_same_class"]) /
            max(1, sum(1 for r in rows if r["fcode_same_class"] is not None)), 2),
        "huc8_match_share": round(
            sum(1 for r in rows if r["huc8_match"]) /
            max(1, sum(1 for r in rows if r["huc8_match"] is not None)), 2),
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "compare_v2_hr_reaches.csv"
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    (out_dir / "compare_v2_hr_reaches.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2),
        encoding="utf-8")
    print("\n== summary ==")
    print(json.dumps(summary, indent=2))
    print(f"report: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
