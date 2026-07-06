"""One-off data conversions for assets copied from the R repo.

1. ``nrsa_metrics.rds`` (1920 sites x 789 NRSA metric columns) is R's binary
   format; it is exported to CSV from R and converted to parquet here:

     PowerShell>  Rscript -e "write.csv(readRDS('D:/Code/Work/stream-curves/data/nrsa_metrics.rds'),
                              '<tmp>/nrsa_metrics.csv', row.names=FALSE, na='')"
     PowerShell>  .venv\\Scripts\\python.exe scripts\\convert_data.py <tmp>/nrsa_metrics.csv

2. The bootstrap-icon SVGs in ``www/vendor/bs-icons.json`` were dumped from the
   R bsicons package (exact markup parity with ``bsicons::bs_icon()``):

     Rscript -e "svgs <- setNames(lapply(names, \\(n) as.character(bsicons::bs_icon(n))), names);
                 jsonlite::write_json(svgs, 'www/vendor/bs-icons.json', auto_unbox=TRUE, pretty=TRUE)"
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamcurves.paths import DATA_DIR


def convert_nrsa_metrics(csv_path: str | Path) -> Path:
    df = pd.read_csv(csv_path)
    # Everything except site_id is a numeric NRSA metric; enforce dtype so the
    # parquet round-trips exactly like the R data.frame (character + numerics).
    for col in df.columns:
        if col != "site_id":
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["site_id"] = df["site_id"].astype("string")
    out = DATA_DIR / "nrsa_metrics.parquet"
    df.to_parquet(out, index=False)
    print(f"wrote {out} ({df.shape[0]} rows x {df.shape[1]} cols)")
    return out


if __name__ == "__main__":
    convert_nrsa_metrics(sys.argv[1])
