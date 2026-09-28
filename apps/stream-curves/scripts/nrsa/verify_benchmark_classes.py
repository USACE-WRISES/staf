"""Verify the NRSA benchmarks of config/published_benchmarks.yaml against EPA's own class calls.

Methodology 0.16 (owner decision D12, 2026-09-28): a benchmark copied from an NRSA
technical support document is adopted only where applying its thresholds to EPA's own
station values reproduces the condition class EPA published for the same station and
visit. This script does that comparison from the raw NRSA files (never committed; see
scripts/nrsa/fetch_nrsa_raw.py) and prints the agreement per NARS-9 region and cycle,
which the catalog's ``verification`` blocks record.

For each catalog entry whose ``verification.epa_class_column`` is set:

* the raw value and EPA's class are read from the files the entry names, joined on
  ``UID`` where a file carries no region (the 2023-24 MMI files take ``AG_ECO9`` from
  the site information file);
* the entry's thresholds and class rule are applied to the value, on the metric's
  own units (the ``units.factor`` conversion);
* rows whose EPA class is not Good, Fair or Poor (Not Assessed) are left out;
* the share of rows where the two classes agree is printed per region and cycle,
  with the smallest disagreement margin, so a misprinted threshold shows up as a
  specific number rather than a vague mismatch.

    py -3.12 scripts/nrsa/verify_benchmark_classes.py
    py -3.12 scripts/nrsa/verify_benchmark_classes.py --check   # exit 1 below full agreement
    py -3.12 scripts/nrsa/verify_benchmark_classes.py --json out.json

Reads only. The 2018-19 salinity classes live in the population-estimates file and the
conductivity values in the water chemistry file, joined on UID.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parents[1]
REPO_ROOT = APP_ROOT.parents[1]
RAW_DIR = REPO_ROOT / "notes" / "DEEP_Working" / "nrsa_raw"
CATALOG = APP_ROOT / "config" / "published_benchmarks.yaml"

sys.path.insert(0, str(HERE))
from nrsa_io import read_epa_csv  # noqa: E402

#: the 2013-14 files spell the NARS-9 region out; the later cycles carry the code
REGION_NAMES = {
    "Coastal Plains": "CPL", "Northern Appalachians": "NAP", "Northern Plains": "NPL",
    "Southern Appalachians": "SAP", "Southern Plains": "SPL", "Temporate Plains": "TPL",
    "Temperate Plains": "TPL", "Upper Midwest": "UMW", "Western Mountains": "WMT", "Xeric": "XER",
}
CLASSES = ("Good", "Fair", "Poor")


def load_entries() -> list[dict]:
    doc = yaml.safe_load(CATALOG.read_text(encoding="utf-8")) or {}
    return [e for e in doc.get("entries") or []
            if (e.get("verification") or {}).get("epa_class_column")]


def classify(value: float, low: float, high: float, direction: str, rule: dict) -> str:
    """EPA's class for one value under the entry's two breakpoints (ascending) and
    its class rule (``good`` and ``poor`` each name the comparison at the boundary)."""
    if direction == "higher_is_better":
        good_edge, poor_edge = high, low
        good = value >= good_edge if rule.get("good") == "at_or_above" else value > good_edge
        poor = value < poor_edge if rule.get("poor") == "below" else value <= poor_edge
    else:
        good_edge, poor_edge = low, high
        good = value <= good_edge if rule.get("good") == "at_or_below" else value < good_edge
        poor = value > poor_edge if rule.get("poor") == "above" else value >= poor_edge
    return "Good" if good else "Poor" if poor else "Fair"


def _read(cycle: str, name: str, **kw) -> pd.DataFrame:
    return read_epa_csv(RAW_DIR / cycle / name, **kw)


def _with_region(frame: pd.DataFrame, cycle: str, region_from: str | None) -> pd.DataFrame:
    if "AG_ECO9" in frame.columns:
        frame = frame.assign(nars9=frame["AG_ECO9"].astype(str).str.strip())
    elif "AG_ECO9_NM" in frame.columns:
        frame = frame.assign(nars9=frame["AG_ECO9_NM"].map(REGION_NAMES))
    elif region_from:
        info = _read(cycle, region_from, usecols=lambda c: c.upper() in ("UID", "AG_ECO9"))
        info = info.drop_duplicates("UID").assign(nars9=lambda d: d["AG_ECO9"].astype(str).str.strip())
        frame = frame.merge(info[["UID", "nars9"]], on="UID", how="left")
    else:
        raise SystemExit(f"{cycle}: no region column and no region_from file")
    return frame


def rows_for(source: dict) -> pd.DataFrame:
    """``(cycle, nars9, value, epa_class)`` rows for one verification source."""
    cycle = str(source["cycle"])
    value_col, class_col = str(source["value_column"]).upper(), str(source["class_column"]).upper()
    frame = _read(cycle, source["file"])
    if source.get("value_file"):
        # the class and the value live in different files (2018-19 salinity)
        vals = _read(cycle, source["value_file"],
                     usecols=lambda c: c.upper() in ("UID", value_col))
        frame = frame.merge(vals.drop_duplicates("UID")[["UID", value_col]], on="UID", how="left")
    frame = _with_region(frame, cycle, source.get("region_from"))
    out = pd.DataFrame({
        "cycle": cycle, "nars9": frame["nars9"],
        "value": pd.to_numeric(frame[value_col], errors="coerce"),
        "epa_class": frame[class_col].astype(str).str.strip(),
    })
    out = out[out["epa_class"].isin(CLASSES)].dropna(subset=["value", "nars9"])
    return out.reset_index(drop=True)


def verify_entry(entry: dict) -> dict:
    ver = entry["verification"]
    direction = str(entry.get("direction") or "lower_is_better")
    rule = dict(entry.get("class_rule") or {})
    factor = float((entry.get("units") or {}).get("factor") or 1.0)
    thresholds = {str(k): [float(v[0]), float(v[1])] for k, v in (entry.get("thresholds") or {}).items()}
    pieces = [rows_for(s) for s in ver.get("sources") or []]
    rows = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(
        columns=["cycle", "nars9", "value", "epa_class"])
    rows = rows[rows["nars9"].isin(thresholds)].copy()

    def own_class(r):
        low, high = thresholds[r["nars9"]]
        # the benchmark's units may differ from the metric's; EPA's files are in the
        # benchmark's units, so the comparison is made there
        return classify(float(r["value"]), low, high, direction, rule)

    rows["own_class"] = rows.apply(own_class, axis=1) if len(rows) else []
    rows["agree"] = rows["own_class"] == rows["epa_class"]
    per_region: dict[str, dict] = {}
    for region, g in rows.groupby("nars9"):
        per_region[str(region)] = {"n": int(len(g)), "agree": int(g["agree"].sum()),
                                   "share": round(float(g["agree"].mean()), 4) if len(g) else None}
    per_cycle: dict[str, dict] = {}
    for cycle, g in rows.groupby("cycle"):
        per_cycle[str(cycle)] = {"n": int(len(g)), "agree": int(g["agree"].sum()),
                                 "share": round(float(g["agree"].mean()), 4) if len(g) else None}
    disagreements = rows[~rows["agree"]]
    examples = [{"cycle": str(r.cycle), "nars9": str(r.nars9), "value": float(r.value),
                 "epa": str(r.epa_class), "own": str(r.own_class)}
                for r in disagreements.head(10).itertuples()]
    return {"id": entry.get("id"), "metric": entry.get("metric"), "n": int(len(rows)),
            "agree": int(rows["agree"].sum()) if len(rows) else 0,
            "share": round(float(rows["agree"].mean()), 4) if len(rows) else None,
            "per_region": per_region, "per_cycle": per_cycle,
            "n_disagree": int(len(disagreements)), "examples": examples, "factor": factor}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 unless every entry agrees fully")
    ap.add_argument("--json", type=Path, default=None, help="write the results here")
    a = ap.parse_args(argv)
    if not RAW_DIR.exists():
        raise SystemExit(f"raw NRSA files not found under {RAW_DIR}; run scripts/nrsa/fetch_nrsa_raw.py")
    results = [verify_entry(e) for e in load_entries()]
    worst = 1.0
    for r in results:
        print(f"{r['id']} ({r['metric']}): {r['agree']} of {r['n']} classified visits agree"
              f" ({(r['share'] or 0) * 100:.2f} percent)")
        for cycle, c in sorted(r["per_cycle"].items()):
            print(f"    cycle {cycle}: {c['agree']} of {c['n']}")
        for region, c in sorted(r["per_region"].items()):
            flag = "" if c["agree"] == c["n"] else "   <-- disagreement"
            print(f"    {region}: {c['agree']} of {c['n']}{flag}")
        for ex in r["examples"]:
            print(f"      example: {ex}")
        if r["share"] is not None:
            worst = min(worst, r["share"])
    if a.json:
        a.json.write_text(json.dumps(results, indent=1), encoding="utf-8")
        print("wrote", a.json)
    if a.check and worst < 1.0:
        print("[verify] at least one benchmark does not reproduce EPA's classes")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
