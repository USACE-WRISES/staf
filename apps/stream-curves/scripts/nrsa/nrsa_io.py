"""Reading EPA's NRSA CSVs, with the two traps they set.

1. **Encoding.** The files are latin-1, not UTF-8, and some carry a UTF-8 BOM on
   top of that. Reading as UTF-8 raises on degree signs and accented place
   names; reading as latin-1 turns a BOM into a mangled first column name.
2. **VISIT_NO is not a number.** Most rows use ``1`` or ``2``, but some use
   ``R`` for a repeat sample. Coercing to int silently collapses an ``R`` row
   onto visit 1, which is how two South Dakota sites ended up with the wrong
   values: the site had one row for each and whichever came first won.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# what a UTF-8 BOM looks like once latin-1 has had its way with it
_BOM_ARTIFACTS = ("﻿", "ï»¿", "Ϗ»¿")


def clean_column(name: str) -> str:
    text = str(name)
    for artifact in _BOM_ARTIFACTS:
        text = text.replace(artifact, "")
    return text.strip().upper()


def read_epa_csv(path: Path, **kwargs) -> pd.DataFrame:
    """Read one NRSA CSV with upper-cased, BOM-free column names."""
    try:
        frame = pd.read_csv(path, low_memory=False, encoding="utf-8-sig", **kwargs)
    except UnicodeDecodeError:
        frame = pd.read_csv(path, low_memory=False, encoding="latin-1", **kwargs)
    frame.columns = [clean_column(c) for c in frame.columns]
    return frame


def normalize_visit_no(series: pd.Series) -> pd.Series:
    """VISIT_NO as a string, so ``R`` stays distinct from ``1``.

    A blank becomes ``"1"``, which is what a single-visit table means.
    """
    text = series.astype(str).str.strip().str.upper()
    text = text.replace({"": "1", "NAN": "1", "NONE": "1", "<NA>": "1"})
    # "1.0" from a float column reads back as "1"
    return text.str.replace(r"^(\d+)\.0$", r"\1", regex=True)
