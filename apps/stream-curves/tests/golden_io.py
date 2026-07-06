"""Readers for the R-generated golden fixtures in tests/golden/.

Fixtures are written by scripts/export_golden.R with jsonlite:
``dataframe="columns"``, ``na="null"``, ``auto_unbox=FALSE`` (so scalars arrive
as length-1 lists), max float precision. ``20_deep_bundle.json`` alone uses
``auto_unbox=TRUE`` (it IS the deep-export contract format).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


def has_golden(name: str) -> bool:
    return golden_path(name).exists()


def load_golden_json(name: str) -> Any:
    return json.loads(golden_path(name).read_text(encoding="utf-8"))


def unbox(x: Any) -> Any:
    """Undo jsonlite auto_unbox=FALSE for a known-scalar field."""
    if isinstance(x, list) and len(x) == 1:
        return x[0]
    return x


def load_golden_df(name: str) -> pd.DataFrame:
    """Load a dataframe="columns" fixture as a DataFrame (null -> NaN/None)."""
    obj = load_golden_json(name)
    if not isinstance(obj, dict):
        raise TypeError(f"{name}.json is not a columns-oriented frame")
    cols = {}
    for col, values in obj.items():
        if not isinstance(values, list):
            values = [values]
        cols[col] = values
    df = pd.DataFrame(cols)
    return df.replace({None: np.nan})


def assert_frame_matches(
    py: pd.DataFrame,
    golden: pd.DataFrame,
    *,
    keys: list[str] | None = None,
    rtol: float = 1e-9,
    atol: float = 1e-12,
    check_extra_py_cols: bool = False,
) -> None:
    """Compare a Python-produced frame to a golden frame.

    Compares only columns present in the golden frame (the Python port may add
    columns unless check_extra_py_cols). If `keys` given, both frames are
    sorted by them first so row order differences don't fail parity.
    """
    missing = [c for c in golden.columns if c not in py.columns]
    assert not missing, f"columns missing from python output: {missing}"
    if check_extra_py_cols:
        extra = [c for c in py.columns if c not in golden.columns]
        assert not extra, f"unexpected extra columns: {extra}"

    g = golden.copy()
    p = py[list(golden.columns)].copy()
    if keys:
        g = g.sort_values(keys, kind="mergesort").reset_index(drop=True)
        p = p.sort_values(keys, kind="mergesort").reset_index(drop=True)
    else:
        g = g.reset_index(drop=True)
        p = p.reset_index(drop=True)

    assert len(p) == len(g), f"row count {len(p)} != golden {len(g)}"

    for col in g.columns:
        gv, pv = g[col], p[col]
        g_num = pd.to_numeric(gv, errors="coerce")
        if g_num.notna().sum() > 0 and (g_num.notna() == gv.notna()).all():
            p_num = pd.to_numeric(pv, errors="coerce")
            na_match = (g_num.isna() == p_num.isna()).all()
            assert na_match, f"{col}: NA pattern mismatch"
            both = g_num.notna()
            np.testing.assert_allclose(
                p_num[both].astype(float).to_numpy(),
                g_num[both].astype(float).to_numpy(),
                rtol=rtol,
                atol=atol,
                err_msg=f"column {col!r}",
            )
        else:
            gs = gv.map(_norm_cell)
            ps = pv.map(_norm_cell)
            bad = (gs != ps) & ~(gs.isna() & ps.isna())
            assert not bad.any(), (
                f"{col}: value mismatch at rows {list(bad[bad].index[:5])}: "
                f"py={ps[bad].head().tolist()} golden={gs[bad].head().tolist()}"
            )


_NUM_RE = __import__("re").compile(r"-?\d+(?:\.\d+)?")


def assert_display_matches(
    py: pd.DataFrame,
    golden: pd.DataFrame,
    *,
    keys: list[str],
    atol: float = 0.011,
) -> None:
    """Compare display-string columns (e.g. "19.92 - 31.11", "1.55, 20.50"):
    numeric tokens must match within ``atol``; the non-numeric skeleton must
    match exactly. Needed because R's fround() can differ from Python by one
    display penny on exact-half values."""
    g = golden.sort_values(keys, kind="mergesort").reset_index(drop=True)
    p = py[list(golden.columns)].sort_values(keys, kind="mergesort").reset_index(drop=True)
    assert len(p) == len(g)
    for col in g.columns:
        if col in keys:
            continue
        for i in range(len(g)):
            gv, pv = g[col].iat[i], p[col].iat[i]
            g_na = gv is None or (isinstance(gv, float) and math.isnan(gv))
            p_na = pv is None or (isinstance(pv, float) and math.isnan(pv))
            assert g_na == p_na, f"{col}[{i}]: NA mismatch (py={pv!r}, golden={gv!r})"
            if g_na:
                continue
            gs, ps = str(gv), str(pv)
            g_nums = [float(m) for m in _NUM_RE.findall(gs)]
            p_nums = [float(m) for m in _NUM_RE.findall(ps)]
            g_skel = _NUM_RE.sub("#", gs)
            p_skel = _NUM_RE.sub("#", ps)
            assert g_skel == p_skel and len(g_nums) == len(p_nums), (
                f"{col}[{i}]: structure mismatch (py={ps!r}, golden={gs!r})"
            )
            for a, b in zip(p_nums, g_nums):
                assert abs(a - b) <= atol + 1e-9 * abs(b), (
                    f"{col}[{i}]: {a} vs golden {b} (py={ps!r}, golden={gs!r})"
                )


def _norm_cell(v: Any) -> Any:
    if v is None:
        return np.nan
    if isinstance(v, float) and math.isnan(v):
        return np.nan
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, str):
        return v
    return v
