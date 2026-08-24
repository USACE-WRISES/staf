"""Assemble the per-station metric values across all three NRSA cycles.

Writes one wide table keyed by (station_key, cycle, visit_no) with a column per
catalog metric, which is the shape ``streamcurves.nrsa.load_nrsa_values`` already
consumes, plus a crosswalk recording where each metric came from in each cycle.

What the cycles actually hold, measured from the downloaded files rather than
from the workbooks (which understate 2023-24):

| category          | of  | 2013-14 | 2018-19 | 2023-24 |
|-------------------|-----|---------|---------|---------|
| Physical habitat  | 155 |   140   |   155   |   155   |
| Water chemistry   |  25 |    21   |    25   |    21   |
| Benthic metrics   | 125 |   125   |     0   |   125   |
| Fish metrics      | 180 |   180   |     0   |   180   |
| Landscape         | 303 |     1   |   303   |     1   |

So no single cycle is complete and the three are complementary: 2018-19 is the
only source of NRSA landscape metrics, and it is the only cycle EPA publishes no
site-level benthic or fish metrics for.

    py -3.12 scripts/nrsa/build_values_table.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nrsa_io import normalize_visit_no, read_epa_csv  # noqa: E402

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parents[1]
RAW_DIR = REPO_ROOT / "notes" / "DEEP_Working" / "nrsa_raw"
OUT_DIR = APP_ROOT / "data" / "nrsa"

ENCODING = "latin-1"
CYCLES = ("1314", "1819", "2324")

# Where to look for metric columns, best source first. Several 2013-14 files
# repeat the same phab columns; the larger set wins so one metric resolves to one
# source per cycle.
SOURCE_PRIORITY = {
    "1314": ["PHAB_LARGER", "PHAB_COMMON", "PHAB_INDICATORS", "WATER_CHEMISTRY",
             "WATER_CHEM_INDICATORS", "BENTHIC_METRICS", "FISH_METRICS", "BENTHIC_MMI",
             "FISH_MMI", "LANDSCAPE", "KEY_VARIABLES", "FIELD_CHEMISTRY",
             "PERIPHYTON_CHLA", "PERIPHYTON_BIOMASS", "WATER_COLUMN_CHLA", "SITE_INFO"],
    "1819": ["PHAB", "WATER_CHEMISTRY", "LANDSCAPE", "BENTHIC_MMI", "FISH_MMI",
             "FIELD_CHEMISTRY", "PERIPHYTON_CHLA", "PERIPHYTON_BIOMASS", "SITE_INFO"],
    "2324": ["PHAB", "WATER_CHEMISTRY", "BENTHIC_METRICS", "FISH_METRICS", "BENTHIC_MMI",
             "FISH_MMI", "PERIPHYTON_CHLA", "PERIPHYTON_BIOMASS", "SITE_INFO"],
}

# taxon-level tables: their own files, never melted into the metric table
COUNT_TABLES = {
    "benthic_counts": "BENTHIC_COUNT",
    "benthic_taxa": "BENTHIC_TAXA",
    "fish_counts": "FISH_COUNT",
    "fish_taxa": "FISH_TAXA",
}


def load_lock() -> dict:
    path = OUT_DIR / "sources.lock.json"
    if not path.exists():
        raise SystemExit("no sources.lock.json. Run scripts/nrsa/fetch_nrsa_raw.py first.")
    return json.loads(path.read_text(encoding="utf-8"))


def data_files(lock: dict) -> dict[tuple[str, str], Path]:
    out = {}
    for rec in lock["files"].values():
        if rec["kind"] == "data":
            out[(rec["cycle"], rec["dataset_id"])] = RAW_DIR / rec["cycle"] / rec["file"]
    return out


def read_upper(path: Path, **kw) -> pd.DataFrame:
    return read_epa_csv(path, **kw)


def resolve_column(raw_name: str, available: set[str]) -> str | None:
    """Match a catalog raw name to a real column, case- and _RESULT-insensitively.

    Chemistry is ``ANC_RESULT`` in the wide 2018-19 and 2023-24 files but a bare
    ``ANC`` in the 2013-14 indicator file, and the phab files disagree on case
    (``LRBS_use`` against ``LRBS_USE``).
    """
    upper = str(raw_name).strip().upper()
    for candidate in (upper, upper[:-7] if upper.endswith("_RESULT") else f"{upper}_RESULT"):
        if candidate in available:
            return candidate
    return None


def build_crosswalk(catalog: pd.DataFrame, files: dict) -> pd.DataFrame:
    """One row per (metric_key, cycle) that resolves, naming its source column."""
    headers: dict[tuple[str, str], set[str]] = {}
    for cycle in CYCLES:
        for dataset in SOURCE_PRIORITY[cycle]:
            path = files.get((cycle, dataset))
            if path and path.exists():
                headers[(cycle, dataset)] = set(read_upper(path, nrows=0).columns)

    rows = []
    for _, metric in catalog.iterrows():
        for cycle in CYCLES:
            for dataset in SOURCE_PRIORITY[cycle]:
                available = headers.get((cycle, dataset))
                if not available:
                    continue
                column = resolve_column(metric["raw_name"], available)
                if column:
                    rows.append({
                        "metric_key": metric["name"], "cycle": cycle,
                        "dataset_id": dataset, "source_column": column,
                        "category": metric["category"],
                    })
                    break
    return pd.DataFrame(rows)


def build_values(catalog: pd.DataFrame, crosswalk: pd.DataFrame, files: dict,
                 visits: pd.DataFrame) -> pd.DataFrame:
    """Wide: one row per site-visit, one column per catalog metric."""
    metric_keys = list(catalog["name"])
    pieces = []

    for cycle in CYCLES:
        cycle_visits = visits[visits["cycle"] == cycle][
            ["station_key", "cycle", "site_id", "visit_no"]
        ].drop_duplicates(["site_id", "visit_no"])
        frame = cycle_visits.set_index(["site_id", "visit_no"])

        plan = crosswalk[crosswalk["cycle"] == cycle]
        for dataset, group in plan.groupby("dataset_id"):
            path = files.get((cycle, dataset))
            if not path or not path.exists():
                continue
            wanted = list(group["source_column"])
            raw = read_upper(path, usecols=lambda c, w=set(wanted) | {"SITE_ID", "VISIT_NO"}:
                             c.upper() in w)
            if "SITE_ID" not in raw.columns:
                continue
            if "VISIT_NO" not in raw.columns:
                raw["VISIT_NO"] = 1
            raw["SITE_ID"] = raw["SITE_ID"].astype(str).str.strip()
            raw["VISIT_NO"] = normalize_visit_no(raw["VISIT_NO"])
            raw = raw.drop_duplicates(["SITE_ID", "VISIT_NO"]).set_index(["SITE_ID", "VISIT_NO"])
            raw.index.names = ["site_id", "visit_no"]   # match the visit frame's index

            rename = dict(zip(group["source_column"], group["metric_key"]))
            keep = [c for c in raw.columns if c in rename]
            block = raw[keep].rename(columns=rename)
            for column in block.columns:
                block[column] = pd.to_numeric(block[column], errors="coerce")
            frame = frame.join(block, how="left")

        pieces.append(frame.reset_index())

    values = pd.concat(pieces, ignore_index=True)
    for key in metric_keys:
        if key not in values.columns:
            values[key] = pd.NA
    ordered = ["station_key", "cycle", "site_id", "visit_no"] + metric_keys
    values = values[ordered]
    for key in metric_keys:
        values[key] = pd.to_numeric(values[key], errors="coerce").astype("float32")
    return values


# Columns a cycle does not publish but that another column stands in for exactly.
# Each carries the agreement measured where both columns exist, so the fill is
# evidence rather than assumption, and apply_derivations re-measures it on every
# build and refuses a source that has drifted.
DERIVATIONS = [
    {
        "target": "phab_XSLOPE_use",
        "source": "phab_XSLOPE",
        "min_agreement": 0.99,
        "note": ("2013-14 publishes no XSLOPE_use column. Where both exist, XSLOPE is "
                 "identical to it on 1.000 of 2018-19 rows and 0.998 of 2023-24 rows."),
    },
]


def apply_derivations(values: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Fill a missing column from its declared stand-in, measuring the agreement first.

    Only blank cells are filled, so a published value is never overwritten, and a
    derivation whose source no longer agrees where both are present is skipped and
    reported rather than applied.
    """
    out = values.copy()
    report = []
    for rule in DERIVATIONS:
        target, source = rule["target"], rule["source"]
        if target not in out.columns or source not in out.columns:
            report.append({**rule, "status": "skipped", "reason": "column absent"})
            continue
        both = out[target].notna() & out[source].notna()
        agreement = (
            float(np.isclose(out.loc[both, target], out.loc[both, source],
                             rtol=1e-4, atol=1e-6).mean())
            if int(both.sum()) else 0.0
        )
        if agreement < rule["min_agreement"]:
            report.append({**rule, "status": "refused", "agreement": agreement,
                           "n_compared": int(both.sum()),
                           "reason": f"agreement {agreement:.3f} below "
                                     f"{rule['min_agreement']}"})
            continue
        fillable = out[target].isna() & out[source].notna()
        by_cycle = out.loc[fillable, "cycle"].value_counts().to_dict()
        out.loc[fillable, target] = out.loc[fillable, source]
        report.append({**rule, "status": "applied", "agreement": agreement,
                       "n_compared": int(both.sum()), "n_filled": int(fillable.sum()),
                       "by_cycle": by_cycle})
    return out, report


# EPA publishes no site-level benthic or fish metrics for 2018-19, so the pooled
# rows for that cycle come back empty for all 305 of them. The bundled legacy
# parquet does carry them: the prior R application computed them with aquamet, and
# they are the values the published assessments were built on. Backfilling from it
# is exact and carries no definitional risk, which recomputing them would.
LEGACY_BACKFILL_PREFIXES = ("bent_", "fish_")


def backfill_from_legacy(values: pd.DataFrame, legacy_path: Path,
                         cycle: str = "1819") -> tuple[pd.DataFrame, dict]:
    """Fill a cycle's empty metric families from the bundled legacy snapshot.

    Only columns the EPA files left entirely empty for that cycle are touched, and
    only blank cells, so nothing EPA published is ever overwritten.
    """
    report = {"cycle": cycle, "status": "skipped", "columns": 0, "cells": 0}
    if not legacy_path.exists():
        report["reason"] = "legacy snapshot not present"
        return values, report

    legacy = pd.read_parquet(legacy_path).drop_duplicates("site_id").set_index("site_id")
    out = values.copy()
    rows = out["cycle"] == cycle
    if not rows.any():
        report["reason"] = f"no {cycle} rows"
        return out, report

    keys = out.loc[rows, "site_id"].astype(str)
    filled_columns, filled_cells = [], 0
    for column in out.columns:
        if not column.startswith(LEGACY_BACKFILL_PREFIXES):
            continue
        if column not in legacy.columns:
            continue
        if out.loc[rows, column].notna().any():
            continue          # EPA published it for this cycle; leave it alone
        values_in = pd.to_numeric(keys.map(legacy[column]), errors="coerce")
        n = int(values_in.notna().sum())
        if not n:
            continue
        out.loc[rows, column] = values_in.astype("float32").to_numpy()
        filled_columns.append(column)
        filled_cells += n

    report.update(status="applied", columns=len(filled_columns), cells=filled_cells,
                  matched_sites=int(keys.isin(legacy.index).sum()),
                  n_rows=int(rows.sum()))
    return out, report


def write_value_origins(values: pd.DataFrame, out_dir: Path,
                        backfilled: list[str]) -> Path:
    """Record where each (metric, cycle) pair's values came from.

    Origin is uniform per metric and cycle, so one small table says it rather than a
    per-cell column: a curve built on an EPA-published value can be told from one
    built on the prior R application's output.
    """
    rows = []
    metric_cols = [c for c in values.columns
                   if c not in ("station_key", "cycle", "site_id", "visit_no")]
    backfilled_set = set(backfilled)
    for cycle, group in values.groupby("cycle"):
        for column in metric_cols:
            n = int(group[column].notna().sum())
            if not n:
                continue
            origin = ("legacy_r_app" if (column in backfilled_set and cycle == "1819")
                      else "epa_published")
            rows.append({"metric_key": column, "cycle": cycle, "origin": origin, "n": n})
    frame = pd.DataFrame(rows).sort_values(["metric_key", "cycle"])
    target = out_dir / "value_origins.csv"
    frame.to_csv(target, index=False, encoding="utf-8", lineterminator="\n")
    return target


def build_count_tables(files: dict, visits: pd.DataFrame, out_dir: Path) -> list[dict]:
    """The taxon-level tables, stamped with station_key and stored on their own."""
    lookup = visits[["cycle", "site_id", "visit_no", "station_key"]].drop_duplicates()
    written = []
    for name, dataset in COUNT_TABLES.items():
        pieces = []
        for cycle in CYCLES:
            path = files.get((cycle, dataset))
            if not path or not path.exists():
                continue
            raw = read_upper(path)
            raw.insert(0, "CYCLE", cycle)
            if "SITE_ID" in raw.columns:
                raw["SITE_ID"] = raw["SITE_ID"].astype(str).str.strip()
                if "VISIT_NO" not in raw.columns:
                    raw["VISIT_NO"] = 1
                raw["VISIT_NO"] = normalize_visit_no(raw["VISIT_NO"])
                raw = raw.merge(
                    lookup.rename(columns={"cycle": "CYCLE", "site_id": "SITE_ID",
                                           "visit_no": "VISIT_NO",
                                           "station_key": "STATION_KEY"}),
                    on=["CYCLE", "SITE_ID", "VISIT_NO"], how="left")
            pieces.append(raw)
        if not pieces:
            continue
        table = pd.concat(pieces, ignore_index=True)
        table.columns = [c.lower() for c in table.columns]
        # taxonomy strings repeat heavily; category dtype makes the dictionary
        # encoding explicit and shrinks the file by roughly an order of magnitude
        for column in table.columns:
            if table[column].dtype == object and table[column].nunique(dropna=True) < len(table) / 4:
                table[column] = table[column].astype("category")
        target = out_dir / f"{name}.parquet"
        table.to_parquet(target, index=False, compression="zstd")
        written.append({"file": target.name, "rows": len(table),
                        "mb": round(target.stat().st_size / 1e6, 2)})
    return written


DATASET_ID = "multi-cycle-v1"

# Everything under apps/ ships in the desktop payload through git archive, so the
# committed size is a real cost and the build reports it rather than letting it drift.
SIZE_BUDGET_MB = 40.0


def write_manifest(out_dir: Path, lock: dict) -> dict:
    """Hash every committed file so a run can record exactly what it read."""
    files = {}
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[path.relative_to(out_dir).as_posix()] = {
            "bytes": path.stat().st_size,
            "sha256": "sha256:" + digest,
        }
    manifest = {
        "schemaVersion": 1,
        "datasetId": DATASET_ID,
        "cycles": list(CYCLES),
        "sourceLockUpdatedAt": lock.get("updatedAt"),
        "sourceFileCount": len(lock.get("files") or {}),
        "files": files,
        "totalBytes": sum(f["bytes"] for f in files.values()),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--without-counts", action="store_true",
                    help="skip the taxon-level count and taxa tables")
    ap.add_argument("--manifest-only", action="store_true",
                    help="rehash data/nrsa/ and rewrite manifest.json, building nothing")
    args = ap.parse_args(argv)

    lock = load_lock()
    if args.manifest_only:
        # the metric dictionary is built by a separate script, so editing a name
        # leaves the manifest stale until it is rehashed
        manifest = write_manifest(args.out, lock)
        print(f"manifest: {len(manifest['files'])} files, "
              f"{manifest['totalBytes'] / 1e6:.1f} MB")
        return 0
    files = data_files(lock)
    catalog = pd.read_csv(APP_ROOT / "data" / "nrsa_metric_catalog.csv")
    visits_path = args.out / "site_visits.parquet"
    if not visits_path.exists():
        raise SystemExit("no site_visits.parquet. Run scripts/nrsa/build_station_tables.py first.")
    visits = pd.read_parquet(visits_path)

    crosswalk = build_crosswalk(catalog, files)
    crosswalk.to_csv(args.out / "metric_crosswalk.csv", index=False, lineterminator="\n")
    coverage = crosswalk.groupby(["category", "cycle"]).size().unstack(fill_value=0)
    print("metrics resolved per category and cycle:")
    print(coverage.to_string())
    print(f"\ncrosswalk rows: {len(crosswalk)}  "
          f"distinct metrics: {crosswalk.metric_key.nunique()} of {len(catalog)}")

    values = build_values(catalog, crosswalk, files, visits)
    values, derivations = apply_derivations(values)
    before = {c: bool(values.loc[values.cycle == "1819", c].notna().any())
              for c in values.columns
              if c.startswith(LEGACY_BACKFILL_PREFIXES)}
    values, backfill = backfill_from_legacy(
        values, APP_ROOT / "data" / "nrsa_metrics.parquet")
    backfilled = [c for c, had in before.items()
                  if not had and values.loc[values.cycle == "1819", c].notna().any()]
    if backfill["status"] == "applied":
        print(f"backfilled {backfill['columns']} benthic and fish columns for 2018-19 "
              f"from the legacy snapshot ({backfill['cells']:,} cells, "
              f"{backfill['matched_sites']} of {backfill['n_rows']} rows matched)")
    else:
        print(f"legacy backfill {backfill['status']}: {backfill.get('reason')}")
    for rule in derivations:
        if rule["status"] == "applied":
            print(f"derived {rule['target']} from {rule['source']}: "
                  f"agreement {rule['agreement']:.3f} on {rule['n_compared']} rows, "
                  f"filled {rule['n_filled']} {rule['by_cycle']}")
        else:
            print(f"derivation {rule['target']} {rule['status']}: {rule['reason']}")
    values_path = args.out / "values.parquet"
    values.to_parquet(values_path, index=False, compression="zstd")
    metric_cols = [c for c in values.columns
                   if c not in ("station_key", "cycle", "site_id", "visit_no")]
    filled = values[metric_cols].notna().sum(axis=1)
    print(f"\nvalues: {len(values)} site-visits x {len(metric_cols)} metrics, "
          f"{values_path.stat().st_size / 1e6:.2f} MB")
    print("metrics present per site-visit, by cycle:")
    print(values.assign(n=filled).groupby("cycle")["n"].describe()[["count", "mean", "min", "max"]]
          .round(0).to_string())

    written = [] if args.without_counts else build_count_tables(files, visits, args.out)
    for rec in written:
        print(f"  {rec['file']:<26} {rec['rows']:>7} rows {rec['mb']:>7.2f} MB")

    origins = write_value_origins(values, args.out, backfilled)
    print(f"wrote {origins.name}")
    manifest = write_manifest(args.out, lock)
    total_mb = manifest["totalBytes"] / 1e6
    print(f"\nmanifest: {len(manifest['files'])} files, dataset id {manifest['datasetId']}")
    print(f"committed total under data/nrsa/: {total_mb:.1f} MB "
          f"(budget {SIZE_BUDGET_MB:.0f} MB)")
    if total_mb > SIZE_BUDGET_MB:
        print("Over budget. Everything under apps/ ships in the desktop payload, "
              "so rebuild with --without-counts or raise the budget deliberately.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
