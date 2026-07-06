"""Private base-R compatibility shims shared by the ported streamcurves modules.

Not a port of any single R file. These replicate base-R semantics that several
ported modules (cleaning/derive/precheck/cross_metric/regional/profiler) rely
on so their outputs match the R app byte-for-byte where strings are built from
numbers:

- ``r_num_str``       — ``as.character()`` for doubles: up to 15 significant
                        digits, fixed vs scientific chosen by printed width
                        (``scipen = 0``). Verified against R 4.4:
                        ``1e5 -> "1e+05"``, ``123456 -> "123456"``,
                        ``1.234e-4 -> "0.0001234"``, ``8.0 -> "8"``.
- ``r_signif``        — ``signif()`` (round to N significant digits).
- ``r_format_common`` — ``format(<numeric vector>, trim = TRUE)``: a common
                        number of decimals across the vector (digits = 7),
                        e.g. ``c(1, 2.5, 10) -> "1.0" "2.5" "10.0"``.
- ``r_round``         — ``round()`` (IEC 60559 half-to-even, like numpy).
- ``compact_chr`` / ``trim_character_columns`` — helpers from
  R/00_input_workbook.R used across the pipeline (private copies; consolidate
  with the canonical workbook port when it lands).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

__all__ = [
    "or_",
    "is_true",
    "is_na_scalar",
    "as_character_scalar",
    "r_num_str",
    "r_round",
    "r_signif",
    "r_format_common",
    "as_numeric_r",
    "compact_chr",
    "trim_character_columns",
]


def or_(x, default):
    """R ``%||%`` — default only when x is None (NULL), not when falsy."""
    return x if x is not None else default


def is_true(x) -> bool:
    """R ``isTRUE()`` — True only for a literal logical TRUE."""
    return x is True or isinstance(x, np.bool_) and bool(x)


def is_na_scalar(v) -> bool:
    """Scalar NA check covering None, NaN, pd.NA, NaT."""
    if v is None or v is pd.NA:
        return True
    if isinstance(v, (float, np.floating)):
        return math.isnan(float(v))
    if isinstance(v, (str, bytes, int, np.integer, bool, np.bool_)):
        return False
    try:
        res = pd.isna(v)
    except (TypeError, ValueError):
        return False
    return bool(res) if np.isscalar(res) or isinstance(res, (bool, np.bool_)) else False


def r_num_str(x) -> str:
    """R ``as.character()`` for a single number.

    Up to 15 significant digits with trailing zeros trimmed; scientific
    notation is used iff it prints shorter than fixed notation (scipen = 0).
    """
    if is_na_scalar(x):
        return "NA"
    if isinstance(x, (bool, np.bool_)):
        return "TRUE" if x else "FALSE"
    if isinstance(x, (int, np.integer)):
        return str(int(x))
    v = float(x)
    if math.isinf(v):
        return "Inf" if v > 0 else "-Inf"
    fixed = np.format_float_positional(
        v, precision=15, unique=True, fractional=False, trim="-"
    )
    sci = np.format_float_scientific(v, precision=14, unique=True, trim="-", exp_digits=2)
    return sci if len(sci) < len(fixed) else fixed


def as_character_scalar(v):
    """R ``as.character()`` on a scalar; NA-ish values -> None (NA_character_)."""
    if is_na_scalar(v):
        return None
    if isinstance(v, (bool, np.bool_)):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return r_num_str(float(v))
    return str(v)


def r_round(x, digits: int = 0) -> float:
    """R ``round()`` — round-half-to-even, same as numpy."""
    return float(np.round(x, digits))


def r_signif(x, digits: int = 6):
    """R ``signif()`` — round to ``digits`` significant digits (vectorised)."""
    arr = np.asarray(x, dtype=float)
    out = arr.copy()
    mask = np.isfinite(arr) & (arr != 0)
    if mask.any():
        mag = np.floor(np.log10(np.abs(arr[mask])))
        dec = digits - 1 - mag
        out[mask] = np.round(arr[mask] * 10.0**dec) / 10.0**dec
    return out


def _sig_digits_needed(v: float, digits: int) -> int:
    """Minimal significant digits (<= digits) that reproduce ``v`` exactly."""
    if v == 0 or not math.isfinite(v):
        return 1
    for s in range(1, digits):
        rounded = float(
            np.format_float_positional(v, precision=s, unique=False, fractional=False)
        )
        if rounded == v:
            return s
    return digits


def r_format_common(values, digits: int = 7) -> list[str]:
    """R ``format(<double vector>, trim = TRUE)``.

    All elements share a common number of decimals — the maximum any element
    needs to show up to ``digits`` significant digits. Falls back to a common
    scientific format when that prints narrower (scipen = 0), e.g.
    ``c(1e6, 2.5) -> "1.0e+06" "2.5e+00"``.
    """
    vals = [float(v) for v in values]
    if not vals:
        return []
    decimals = []
    mant_decimals = []
    for v in vals:
        s = _sig_digits_needed(v, digits)
        if v == 0:
            left = 1
        else:
            left = int(math.floor(math.log10(abs(v)))) + 1
        decimals.append(max(0, s - left))
        mant_decimals.append(max(0, s - 1))
    rgt = max(decimals)
    fixed = [f"{v:.{rgt}f}" for v in vals]
    mdec = max(mant_decimals)
    sci = []
    for v in vals:
        if v == 0:
            exp = 0
        else:
            exp = int(math.floor(math.log10(abs(v))))
        mant = v / 10.0**exp
        sci.append(f"{mant:.{mdec}f}e{'+' if exp >= 0 else '-'}{abs(exp):02d}")
    if max(len(s) for s in sci) < max(len(s) for s in fixed):
        return sci
    return fixed


def as_numeric_r(s: pd.Series) -> pd.Series:
    """R ``suppressWarnings(as.numeric(x))``.

    NOTE(parity): for factors R returns the underlying level *codes* (1-based),
    not the parsed labels — ported as-is (classic R wart).
    """
    if isinstance(s.dtype, pd.CategoricalDtype):
        codes = s.cat.codes.to_numpy().astype(float)
        codes[codes == -1] = np.nan
        return pd.Series(codes + 1, index=s.index)
    if pd.api.types.is_bool_dtype(s.dtype):
        return s.astype(float)
    return pd.to_numeric(s, errors="coerce")


def compact_chr(x) -> list[str]:
    """Port of ``compact_chr()`` (R/00_input_workbook.R:149): as.character,
    trim, drop NA/empty."""
    if x is None:
        return []
    if isinstance(x, str):
        x = [x]
    elif isinstance(x, (pd.Series, pd.Index, np.ndarray)):
        x = list(x)
    out = []
    for v in x:
        s = as_character_scalar(v)
        if s is None:
            continue
        s = s.strip()
        if s:
            out.append(s)
    return out


def trim_character_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Port of ``trim_character_columns()`` (R/00_input_workbook.R:144):
    str_trim every character column (factors untouched, like R)."""
    df = df.copy()
    for col in df.columns:
        s = df[col]
        if isinstance(s.dtype, pd.CategoricalDtype):
            continue
        if pd.api.types.is_string_dtype(s.dtype) or s.dtype == object:
            df[col] = s.map(lambda v: v.strip() if isinstance(v, str) else v)
    return df
