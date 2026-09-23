"""Verify the NRSA archive before any build decides which functions need a fallback.

Methodology 0.14 restores and verifies the data first: a metric that looks
unsupported because a cycle was never loaded is a data defect, not a reason to
borrow. This script checks the archive against EPA's own files and definitions
and writes two files beside the data:

* ``cycle_compatibility.csv``: per mapped metric, the cycles that carry it, where
  each cycle's values come from, EPA's definition in each cycle, the cycles it
  may pool (``config/nrsa_cycle_compatibility.yaml``, criterion ACC-02), and a
  units check.
* ``verification_report.md``: independent stations against visits, protocols,
  fish sampling flags, the reference classifications, and every check below.

Checks (``--check`` exits 1 when any fails, so it can gate a build):

1. every crosswalk row carries values in its cycle (no silently empty pair);
2. no metric resolves in another assemblage's or measurement's file;
3. no two metric keys share one source column;
4. every mapped NRSA metric is in the catalog and carries values;
5. the cycle medians of a mapped metric agree within a factor of ten, which a
   unit error breaks and no ecological change produces.

    py -3.12 scripts/nrsa/verify_inputs.py
    py -3.12 scripts/nrsa/verify_inputs.py --check
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(APP_ROOT))

from build_values_table import CATEGORY_DATASETS  # noqa: E402
from streamcurves.nrsa_dataset import cycle_compatibility  # noqa: E402

DATA = APP_ROOT / "data" / "nrsa"
CONFIG = APP_ROOT / "config"
CYCLES = ("1314", "1819", "2324")
UNIT_RATIO_LIMIT = 10.0


def mapped_nrsa_metrics() -> list[str]:
    doc = yaml.safe_load((CONFIG / "metric_map.yaml").read_text(encoding="utf-8"))
    return sorted({m["code"] for f in doc.get("functions") or [] for m in f.get("metrics") or []
                   if m.get("source") == "nrsa"})


def epa_definitions(short: str) -> dict:
    """EPA's definition of a short name in each cycle, from the comparison table."""
    cc = pd.read_csv(DATA / "reference" / "cycle_comparison.csv")
    rows = cc[cc["epa_short_name"].astype(str).str.upper().isin({short, short.removesuffix("_RESULT")})]
    out = {}
    for cycle, col in (("1314", "2013_2014_epa_definition_s"),
                       ("1819", "2018_2019_epa_definition_s"),
                       ("2324", "2023_2024_epa_definition_s")):
        vals = [str(v) for v in rows[col] if str(v) not in ("nan", "")] if col in rows else []
        out[cycle] = vals[0] if vals else ""
    flags = sorted({str(v) for v in rows.get("definition_comparison", [])})
    return {"definitions": out, "epa_flag": "; ".join(flags)}


def run(write: bool = True) -> tuple[list[str], dict]:
    problems: list[str] = []
    crosswalk = pd.read_csv(DATA / "metric_crosswalk.csv")
    values = pd.read_parquet(DATA / "values.parquet")
    catalog = pd.read_csv(APP_ROOT / "data" / "nrsa_metric_catalog.csv")
    origins = pd.read_csv(DATA / "value_origins.csv")
    cyc = values["cycle"].astype(str)

    # 1. every crosswalk row carries values
    for r in crosswalk.itertuples(index=False):
        if r.metric_key in values.columns and not values.loc[cyc == str(r.cycle), r.metric_key].notna().any():
            problems.append(f"empty pair: {r.metric_key} {r.cycle} ({r.dataset_id}.{r.source_column})")
    # 2. assemblage and measurement files
    for r in crosswalk.itertuples(index=False):
        allowed = CATEGORY_DATASETS.get(str(r.category))
        if allowed and not str(r.dataset_id).startswith(allowed):
            problems.append(f"{r.metric_key} {r.cycle} resolves in {r.dataset_id}, outside {r.category}")
    # 3. shared source columns
    shared = crosswalk[crosswalk.duplicated(["cycle", "dataset_id", "source_column"], keep=False)]
    for r in shared.itertuples(index=False):
        problems.append(f"shared column {r.cycle} {r.dataset_id}.{r.source_column} <- {r.metric_key}")

    # 4 and 5, and the compatibility table
    compat = cycle_compatibility()
    names = dict(zip(catalog["name"], catalog.get("units", pd.Series(dtype=str))))
    rows = []
    for mk in mapped_nrsa_metrics():
        if mk not in values.columns:
            problems.append(f"mapped metric {mk} is not in the archive")
            continue
        present = [c for c in CYCLES if values.loc[cyc == c, mk].notna().any()]
        if not present:
            problems.append(f"mapped metric {mk} carries no values")
        med = {c: float(values.loc[cyc == c, mk].median()) for c in present}
        nonzero = [abs(v) for v in med.values() if v == v and v != 0]
        ratio = max(nonzero) / min(nonzero) if len(nonzero) > 1 else 1.0
        if ratio >= UNIT_RATIO_LIMIT:
            problems.append(f"{mk}: cycle medians differ {ratio:.1f}-fold, a unit problem")
        src = crosswalk[crosswalk.metric_key == mk]
        short = str(src["source_column"].iloc[0]).upper() if len(src) else mk.split("_", 1)[1].upper()
        epa = epa_definitions(short)
        org = origins[origins.metric_key == mk]
        rows.append({
            "metric_key": mk, "units": names.get(mk, ""),
            "cycles_with_values": ",".join(present),
            "pooled_cycles": ",".join(sorted(compat.get(mk, set(present)) & set(present))),
            "origin": "; ".join(f"{o.cycle}:{o.origin}" for o in org.itertuples()),
            "sources": "; ".join(f"{s.cycle}:{s.dataset_id}.{s.source_column}" for s in src.itertuples()),
            "epa_flag": epa["epa_flag"],
            **{f"definition_{c}": epa["definitions"][c][:120] for c in CYCLES},
            "median_ratio": round(ratio, 3),
            "units_check": "ok" if ratio < UNIT_RATIO_LIMIT else "review",
        })
    table = pd.DataFrame(rows)

    # the report's counts
    visits = pd.read_parquet(DATA / "site_visits.parquet")
    screen = pd.read_parquet(DATA / "station_screen.parquet")
    fish = pd.read_parquet(DATA / "fish_counts.parquet", columns=["cycle", "station_key", "sampled_fish"])
    summary = {
        "visits": int(len(visits)), "independent_stations": int(visits["station_key"].nunique()),
        "visits_by_cycle": visits.groupby("cycle").size().to_dict(),
        "protocol_by_cycle": {f"{c} {p}": int(n) for (c, p), n in
                              visits.groupby(["cycle", "protocol"]).size().items()},
        "fish_sampling_flags": fish.drop_duplicates(["cycle", "station_key"])["sampled_fish"]
                                   .fillna("(not recorded)").value_counts().to_dict(),
        "strict_pass": int(screen["pass_strict"].sum()),
        "relaxed_pass": int(screen["pass_relaxed"].sum()),
        "epa_reference_by_nars9": screen[screen["rt_nrsa"].astype(str) == "R"]
                                      .groupby("nars9").size().to_dict(),
        "problems": problems,
    }
    if write:
        table.to_csv(DATA / "cycle_compatibility.csv", index=False, lineterminator="\n")
        # CRLF on every platform: the archive manifest records the report's CRLF bytes and
        # .gitattributes pins them (-text), so a rerun on any OS reproduces the recorded file
        with open(DATA / "verification_report.md", "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(report(summary, table))
    return problems, summary


def report(summary: dict, table: pd.DataFrame) -> str:
    lines = ["# NRSA archive verification", "",
             "Written by `scripts/nrsa/verify_inputs.py`. Do not edit by hand.", "",
             f"- Visits: {summary['visits']:,}, independent stations: "
             f"{summary['independent_stations']:,}. A pool counts stations, never visits.",
             f"- Visits by cycle: {summary['visits_by_cycle']}",
             f"- Protocol by cycle: {summary['protocol_by_cycle']}",
             f"- Fish sampling flags (one per station and cycle): {summary['fish_sampling_flags']}",
             f"- Strict screen passes: {summary['strict_pass']}, relaxed tier: "
             f"{summary['relaxed_pass']}",
             f"- EPA NRSA reference (R) sites by NARS-9 region, 2013-14 designations: "
             f"{summary['epa_reference_by_nars9']}", "",
             "## Checks", ""]
    lines += ([f"- FAIL: {p}" for p in summary["problems"]] or ["- All checks pass."])
    lines += ["", "## Cycle compatibility of the mapped metrics", "",
              "| Metric | Cycles with values | Pooled cycles | Origin | Units check |",
              "|---|---|---|---|---|"]
    for r in table.itertuples(index=False):
        lines.append(f"| {r.metric_key} | {r.cycles_with_values} | {r.pooled_cycles} | "
                     f"{r.origin} | {r.units_check} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when any check fails")
    a = ap.parse_args(argv)
    problems, summary = run(write=not a.check)
    print(f"[verify] {summary['independent_stations']:,} independent stations from "
          f"{summary['visits']:,} visits; {len(problems)} problem(s)")
    for p in problems:
        print("  -", p)
    return 1 if (a.check and problems) else 0


if __name__ == "__main__":
    raise SystemExit(main())
