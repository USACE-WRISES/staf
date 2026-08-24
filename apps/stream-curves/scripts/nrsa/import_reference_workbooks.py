"""Convert the three NRSA variable-lookup workbooks into tracked CSVs.

Source: ``notes/NRSA reference material/*.xlsx`` (outside the app, not shipped).
Output: ``apps/stream-curves/data/nrsa/reference/*.csv`` (tracked, shipped).

The workbooks carry, per survey cycle, EPA's own short names and definitions for
every field in every public NRSA dataset, plus the download URLs for those
datasets. That makes them the source for two things the app needs: readable
metric names (``build_metric_dictionary.py``) and the download manifest
(``config/nrsa_sources.yaml``).

Nothing here reaches the network. Run:

    py -3.12 scripts/nrsa/import_reference_workbooks.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parents[1]
DEFAULT_SOURCE = REPO_ROOT / "notes" / "NRSA reference material"
DEFAULT_OUT = APP_ROOT / "data" / "nrsa" / "reference"

# cycle id -> workbook file name
WORKBOOKS = {
    "1314": "NRSA_2013_2014_Variable_Lookup_and_Cycle_Comparison.xlsx",
    "1819": "NRSA_2018_2019_Variable_Lookup_and_2023_2024_Comparison.xlsx",
    "2324": "NRSA_2023_2024_Variable_Lookup.xlsx",
}

# per-cycle sheets exported one file each
PER_CYCLE_SHEETS = {
    "Variable Lookup": "variable_lookup",
    "Metadata Detail": "metadata_detail",
    "Dataset Index": "dataset_index",
}

# the three-cycle comparison lives only in the 2013-14 workbook and supersedes
# the two-cycle sheet in the 2018-19 one
COMPARISON_SHEET = ("1314", "Three-Cycle Comparison", "cycle_comparison")

# characters the workbooks use that the repo's plain-ASCII data files should not
_PUNCT = {
    "–": "-",   # en dash
    "—": "-",   # em dash
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    " ": " ",   # non-breaking space
}


def normalize_text(value):
    """Fold the workbooks' typographic punctuation to ASCII, collapse spaces."""
    if not isinstance(value, str):
        return value
    for bad, good in _PUNCT.items():
        value = value.replace(bad, good)
    return re.sub(r"\s+", " ", value).strip()


def snake(name: str) -> str:
    """'EPA definition / what it measures' -> 'epa_definition_what_it_measures'."""
    name = normalize_text(str(name))
    name = name.replace("#", "count").replace("%", "pct")
    name = re.sub(r"[^0-9A-Za-z]+", "_", name)
    return re.sub(r"_+", "_", name).strip("_").lower()


def tidy(df: pd.DataFrame, *, drop_urls: bool = False) -> pd.DataFrame:
    out = df.copy()
    out.columns = [snake(c) for c in out.columns]
    for col in out.columns:
        if out[col].dtype == object:
            out[col] = out[col].map(normalize_text)
    if drop_urls:
        # The per-field sheets repeat their dataset's data and metadata URL on
        # every row, which is most of their bytes. The URLs live once per
        # dataset in dataset_index_<cycle>.csv; join on source_dataset.
        out = out.drop(columns=[c for c in out.columns if "_url" in c])
    # drop rows that are entirely blank (the workbooks pad some sheets)
    return out.dropna(how="all").reset_index(drop=True)


def convert(source: Path, out_dir: Path) -> list[tuple[Path, int, int]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[Path, int, int]] = []

    for cycle, filename in WORKBOOKS.items():
        path = source / filename
        if not path.exists():
            raise SystemExit(f"workbook not found: {path}")
        book = pd.ExcelFile(path)
        for sheet, stem in PER_CYCLE_SHEETS.items():
            if sheet not in book.sheet_names:
                raise SystemExit(f"{filename}: expected sheet {sheet!r}")
            frame = tidy(book.parse(sheet), drop_urls=(stem != "dataset_index"))
            frame.insert(0, "cycle", cycle)
            target = out_dir / f"{stem}_{cycle}.csv"
            frame.to_csv(target, index=False, encoding="utf-8", lineterminator="\n")
            written.append((target, len(frame), len(frame.columns)))

        cyc, sheet, stem = COMPARISON_SHEET
        if cycle == cyc:
            if sheet not in book.sheet_names:
                raise SystemExit(f"{filename}: expected sheet {sheet!r}")
            frame = tidy(book.parse(sheet), drop_urls=True)
            target = out_dir / f"{stem}.csv"
            frame.to_csv(target, index=False, encoding="utf-8", lineterminator="\n")
            written.append((target, len(frame), len(frame.columns)))

    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    written = convert(args.source, args.out)
    total = 0
    for path, rows, cols in written:
        size = path.stat().st_size
        total += size
        print(f"  {path.relative_to(APP_ROOT).as_posix():<48} {rows:>6} rows {cols:>3} cols "
              f"{size/1024:>8.1f} KB")
    print(f"\n{len(written)} files, {total/1024/1024:.2f} MB total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
