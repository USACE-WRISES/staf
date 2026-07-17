"""Generate data/easi-metrics.json from the STAF screening-metrics.tsv source.

EASI (Ecosystem Assessment Screening Index) = the 20 screening metrics flagged
``Predefined EASI = Yes`` (one proxy metric per stream function). This script is
the single source of truth that keeps EASI's metric definitions, Good/Fair/Poor
criteria, and bin index ranges in lockstep with STAF.

Run:  python scripts/build_easi_metrics.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_TSV = ROOT / "data" / "source" / "screening-metrics.tsv"
FUNCTIONS_JSON = ROOT / "data" / "functions.json"
OUT_JSON = ROOT / "data" / "easi-metrics.json"

# Bin N -> rating label (the STAF TSV orders bins Poor, Fair, Good)
BIN_TO_RATING = {1: "Poor", 2: "Fair", 3: "Good"}
# STAF screening default index ranges (used when a row omits explicit ranges)
DEFAULT_RANGES = {"Good": [0.70, 1.0], "Fair": [0.40, 0.69], "Poor": [0.0, 0.39]}


def _txt(v: str | None) -> str:
    """Normalize a free-text TSV cell: trim whitespace and drop a matched pair of
    wrapping straight quotes (some source cells are written as "..." and the quotes
    would otherwise render verbatim in the apps). Internal quotes are kept."""
    s = (v or "").strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1].strip()
    return s


def parse_range(text: str) -> list[float] | None:
    """Parse a '0.40-0.69' style index-range cell into [min, max]."""
    if not text:
        return None
    cleaned = text.replace("(0-1.0)", "").strip()
    # split on the hyphen separating the two numbers
    parts = cleaned.split("-")
    if len(parts) != 2:
        return None
    try:
        lo, hi = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    return [lo, hi]


def load_function_name_to_id() -> dict[str, str]:
    data = json.loads(FUNCTIONS_JSON.read_text(encoding="utf-8"))
    return {f["name"].strip(): f["id"] for f in data}


def main() -> int:
    if not SRC_TSV.exists():
        print(f"ERROR: source TSV not found: {SRC_TSV}", file=sys.stderr)
        return 1

    name_to_id = load_function_name_to_id()

    with SRC_TSV.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        rows = list(reader)

    metrics: list[dict] = []
    warnings: list[str] = []

    for row in rows:
        if (row.get("Predefined EASI") or "").strip().lower() != "yes":
            continue

        fn_name = (row.get("Function") or "").strip()
        fn_id = name_to_id.get(fn_name)
        if fn_id is None:
            warnings.append(f"no functionId for function name {fn_name!r} "
                            f"(metric {row.get('Metric ID')})")

        # bin index ranges
        ranges: dict[str, list[float]] = {}
        for n in (1, 2, 3):
            rating = BIN_TO_RATING[n]
            rng = parse_range(row.get(f"Bin {n} Recommended Index (0-1.0)", ""))
            ranges[rating] = rng if rng else DEFAULT_RANGES[rating]
        midpoints = {r: round((v[0] + v[1]) / 2, 3) for r, v in ranges.items()}

        metrics.append({
            "metricId": (row.get("Metric ID") or "").strip(),
            "name": (row.get("Metric") or "").strip(),
            "discipline": (row.get("Discipline") or "").strip(),
            "functionName": fn_name,
            "functionId": fn_id,
            "functionStatement": _txt(row.get("Function statement")),
            "metricStatement": _txt(row.get("Metric statement")),
            "context": _txt(row.get("Context")),
            "method": _txt(row.get("Method")),
            "howToMeasure": _txt(row.get("How to measure")),
            "criteria": {
                "Good": _txt(row.get("Good")),
                "Fair": _txt(row.get("Fair")),
                "Poor": _txt(row.get("Poor")),
            },
            "indexRanges": ranges,
            "indexMidpoints": midpoints,
            "references": _txt(row.get("References")),
            "source": _txt(row.get("Source")),
            "dataSource": _txt(row.get("Metric Data Source")),
        })

    out = {
        "schemaVersion": 1,
        "method": "Ecosystem Assessment Screening Index (EASI)",
        "generatedFrom": "data/source/screening-metrics.tsv",
        "count": len(metrics),
        "ratingIndexDefault": {"Good": 0.85, "Fair": 0.545, "Poor": 0.195},
        "metrics": metrics,
    }
    OUT_JSON.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- report ----
    print(f"Wrote {OUT_JSON.relative_to(ROOT)} with {len(metrics)} EASI metrics")
    funcs = [m["functionId"] for m in metrics]
    dupes = {f for f in funcs if funcs.count(f) > 1}
    print(f"Distinct functions covered: {len(set(funcs))}")
    if dupes:
        print(f"WARNING: functions with >1 EASI metric: {sorted(dupes)}")
    for m in metrics:
        print(f"  - {m['functionId']:<32} <- {m['metricId']}")
    for w in warnings:
        print(f"WARNING: {w}")
    if len(metrics) != 20:
        print(f"WARNING: expected 20 EASI metrics, found {len(metrics)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
