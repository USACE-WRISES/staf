"""Port of R/10_reference_curves.R — reference curve development (Phase 4).

Reference-curve helpers: point-table normalization/validation, threshold
crossings, score-band geometry, curve seeding from reference-distribution IQR
stats, manual point editing support, the registry row every exporter consumes,
and the batch runner. This module is the app's parity anchor — behavior is a
1:1 port of the R source, including quirks (marked ``NOTE(parity)``).

Deferred to the views layer (ggplot builders are NOT ported here):
  - build_reference_distribution_plot   (R/10_reference_curves.R:743)
  - build_reference_curve_plot          (R/10_reference_curves.R:865)
  - build_overlay_curve_plot            (R/10_reference_curves.R:1293)
  - build_overlay_bar_chart             (R/10_reference_curves.R:1368)
Their shared pure data-prep helper ``reference_curve_x_range`` IS ported below.
The ``build_plots`` arguments are accepted for signature parity but plots are
always returned as ``None`` (the ``bar_chart_plot`` / ``curve_plot`` slots stay
in every result dict so downstream shapes line up).

Scoring additions (no R equivalent): ``interp_curve`` and
``reference_curve_score_value`` — byte-compatible with DEEP's
``deep/curves.py::interp_curve`` (see R/20_deep_export.R:12-15 for the shared
contract: points ascending in x, direction encoded in y).
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("streamcurves")

__all__ = [
    "empty_reference_curve_points",
    "normalize_reference_curve_points",
    "empty_reference_curve_intervals",
    "reference_curve_format_number",
    "reference_curve_unique_numeric",
    "reference_curve_as_interval_tbl",
    "reference_curve_merge_intervals",
    "reference_curve_score_band_for_value",
    "reference_curve_segment_breaks",
    "reference_curve_band_intervals",
    "reference_curve_score_relation",
    "reference_curve_threshold_crossings",
    "reference_curve_crossings_text",
    "reference_curve_interval_ranges_text",
    "reference_curve_row_range_display",
    "reference_curve_points_from_row",
    "reference_curve_metric_at_score",
    "validate_reference_curve_points",
    "reference_curve_summary_stats",
    "build_reference_curve_row",
    "reference_curve_x_range",
    "normalize_reference_curve_result",
    "strip_reference_curve_result",
    "hydrate_reference_curve_result",
    "reference_curve_rows_for_export",
    "build_reference_curve_from_components",
    "build_reference_curve",
    "build_reference_curve_from_points",
    "run_all_reference_curves",
    "interp_curve",
    "reference_curve_score_value",
]


# --------------------------------------------------------------------------- #
# Small coercion helpers (R as.integer / as.numeric semantics)
# --------------------------------------------------------------------------- #


def _is_vector(v: Any) -> bool:
    return isinstance(v, (list, tuple, np.ndarray, pd.Series, range))


def _tibble_from_mapping(mapping: Mapping) -> pd.DataFrame:
    """tibble::as_tibble(list) — scalars make a 1-row frame, length-1 recycles."""
    cols = dict(mapping)
    if cols and not any(_is_vector(v) for v in cols.values()):
        return pd.DataFrame(cols, index=[0])
    return pd.DataFrame(cols)


def _r_as_numeric(v: Any) -> float:
    """suppressWarnings(as.numeric(v)) for a scalar: uncoercible -> NaN."""
    if v is None or v is pd.NA:
        return float("nan")
    if isinstance(v, (bool, np.bool_)):
        return float(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return float("nan")


def _r_as_integer(v: Any) -> Optional[int]:
    """suppressWarnings(as.integer(v)): truncate toward zero; NA -> None."""
    f = _r_as_numeric(v)
    if not math.isfinite(f):
        return None
    if abs(f) > 2**31 - 1:  # NOTE(parity): R integer overflow -> NA with warning
        return None
    return math.trunc(f)


def _first_row(curve_row: Any) -> Optional[pd.DataFrame]:
    """tibble::as_tibble(curve_row)[1, , drop = FALSE], or None for NULL/empty."""
    if curve_row is None:
        return None
    if isinstance(curve_row, Mapping):
        row = pd.DataFrame(index=[0])
        for k, v in curve_row.items():
            _assign_cell(row, k, v)
        return row
    df = pd.DataFrame(curve_row) if not isinstance(curve_row, pd.DataFrame) else curve_row
    if len(df) == 0:
        return None
    return df.head(1).reset_index(drop=True)


def _assign_cell(row: pd.DataFrame, col: str, value: Any) -> None:
    """Assign one cell of a 1-row frame; exotic values (frames/lists) stored as
    object cells like R list-columns."""
    if isinstance(value, (pd.DataFrame, pd.Series, list, tuple, dict, np.ndarray)):
        row[col] = pd.Series([value], index=row.index, dtype=object)
    else:
        row[col] = [value]


def _is_na_scalar(v: Any) -> bool:
    if v is None or v is pd.NA:
        return True
    return isinstance(v, (float, np.floating)) and math.isnan(v)


# --------------------------------------------------------------------------- #
# Point-table normalization (R/10_reference_curves.R:8-62)
# --------------------------------------------------------------------------- #


def empty_reference_curve_points() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "point_order": pd.Series(dtype=np.int64),
            "metric_value": pd.Series(dtype=float),
            "index_score": pd.Series(dtype=float),
        }
    )


def normalize_reference_curve_points(curve_points: Any) -> pd.DataFrame:
    """Coerce anything point-shaped into the canonical 3-column point table.

    Accepts None, a DataFrame, a mapping of columns (dict-of-lists / scalars),
    or a list of point records. ``x``/``y`` alias to ``metric_value``/
    ``index_score``; missing ``point_order`` becomes the row number and
    uncoercible ``point_order`` falls back to the original position. Rows where
    BOTH values are NA are dropped, the rest are stably sorted by
    (point_order, original order) and renumbered 1..n.
    """
    if curve_points is None:
        return empty_reference_curve_points()

    if isinstance(curve_points, pd.DataFrame):
        points = curve_points.copy()
    elif isinstance(curve_points, Mapping):
        points = _tibble_from_mapping(curve_points)
    elif isinstance(curve_points, (list, tuple)) and all(
        isinstance(p, Mapping) for p in curve_points
    ):
        # R as_tibble(list(...)); record-lists are the Python-side equivalent.
        points = pd.DataFrame(list(curve_points))
    else:
        raise ValueError("Curve points must be NULL, a data frame, or a list.")

    if "metric_value" not in points.columns and "x" in points.columns:
        points["metric_value"] = points["x"]
    if "index_score" not in points.columns and "y" in points.columns:
        points["index_score"] = points["y"]

    if "metric_value" not in points.columns or "index_score" not in points.columns:
        return empty_reference_curve_points()

    n = len(points)
    if "point_order" not in points.columns:
        points["point_order"] = np.arange(1, n + 1)

    original_order = np.arange(1, n + 1, dtype=np.int64)
    point_order_raw = [_r_as_integer(v) for v in points["point_order"].tolist()]
    point_order = np.array(
        [orig if po is None else po for po, orig in zip(point_order_raw, original_order)],
        dtype=np.int64,
    )

    metric_value = np.array([_r_as_numeric(v) for v in points["metric_value"].tolist()], dtype=float)
    index_score = np.array([_r_as_numeric(v) for v in points["index_score"].tolist()], dtype=float)

    out = pd.DataFrame(
        {
            "point_order": point_order,
            "metric_value": metric_value,
            "index_score": index_score,
            "original_order": original_order,
        }
    )
    out = out[~(np.isnan(metric_value) & np.isnan(index_score))]
    out = out.sort_values(["point_order", "original_order"], kind="stable").reset_index(drop=True)
    out["point_order"] = np.arange(1, len(out) + 1, dtype=np.int64)
    return out[["point_order", "metric_value", "index_score"]]


# --------------------------------------------------------------------------- #
# Interval helpers (R/10_reference_curves.R:64-156)
# --------------------------------------------------------------------------- #


def empty_reference_curve_intervals() -> pd.DataFrame:
    return pd.DataFrame({"min": pd.Series(dtype=float), "max": pd.Series(dtype=float)})


def reference_curve_format_number(x: Any, digits: int = 2) -> str:
    """R format(round(x, digits), nsmall = digits, trim = TRUE) with N/A for NA.

    NOTE(parity): R's format() switches to scientific notation for very large
    magnitudes (scipen = 0); the fixed-point f-string does not. App values never
    reach that regime.

    NOTE(parity): R's fround() (R >= 4.0) does long-double candidate arithmetic
    whose half-to-even handling can differ from Python/numpy by one display
    penny when x sits exactly on a half at `digits` (e.g. 19.925 -> R "19.92",
    Python "19.93"). Cosmetic display divergence only — the underlying numeric
    columns are exact; golden tests compare these strings with ±0.011.
    """
    if x is None:
        return "N/A"
    if _is_vector(x):
        if len(x) == 0:
            return "N/A"
        x = list(x)[0]
    xf = _r_as_numeric(x)
    if math.isnan(xf):
        return "N/A"
    if math.isinf(xf):
        return "Inf" if xf > 0 else "-Inf"
    return f"{xf:.{digits}f}"


def reference_curve_unique_numeric(values: Any, digits: int = 10) -> list[float]:
    """Finite values rounded to ``digits``, de-duplicated keeping first
    occurrence (input order preserved — the R version does not sort)."""
    if values is None:
        return []
    arr = np.atleast_1d(np.asarray(values, dtype=float))
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return []
    rounded = np.round(arr, digits)
    seen: set[float] = set()
    out: list[float] = []
    for v in rounded:
        fv = float(v)
        if fv not in seen:
            seen.add(fv)
            out.append(fv)
    return out


def reference_curve_as_interval_tbl(intervals: Any) -> pd.DataFrame:
    """Coerce to a finite (min <= max) interval table; anything unusable -> empty."""
    if intervals is None:
        return empty_reference_curve_intervals()

    if isinstance(intervals, pd.DataFrame):
        tbl = intervals.copy()
    elif isinstance(intervals, Mapping):
        tbl = _tibble_from_mapping(intervals)
    elif isinstance(intervals, (list, tuple)) and all(isinstance(i, Mapping) for i in intervals):
        tbl = pd.DataFrame(list(intervals))
    else:
        return empty_reference_curve_intervals()

    if not {"min", "max"}.issubset(tbl.columns):
        return empty_reference_curve_intervals()

    mn = np.array([_r_as_numeric(v) for v in tbl["min"].tolist()], dtype=float)
    mx = np.array([_r_as_numeric(v) for v in tbl["max"].tolist()], dtype=float)
    keep = np.isfinite(mn) & np.isfinite(mx)
    mn, mx = mn[keep], mx[keep]
    if mn.size == 0:
        return empty_reference_curve_intervals()
    return pd.DataFrame({"min": np.minimum(mn, mx), "max": np.maximum(mn, mx)})


def reference_curve_merge_intervals(intervals: Any, tol: float = 1e-9) -> pd.DataFrame:
    """Merge overlapping intervals (overlap when next_min <= current_max + tol)."""
    intervals = reference_curve_as_interval_tbl(intervals)
    if len(intervals) == 0:
        return empty_reference_curve_intervals()

    intervals = intervals.sort_values(["min", "max"], kind="stable").reset_index(drop=True)
    mins = intervals["min"].to_numpy(dtype=float)
    maxs = intervals["max"].to_numpy(dtype=float)

    merged: list[tuple[float, float]] = []
    current_min = float(mins[0])
    current_max = float(maxs[0])
    for i in range(1, len(intervals)):
        next_min = float(mins[i])
        next_max = float(maxs[i])
        if next_min <= current_max + tol:
            current_max = max(current_max, next_max)
        else:
            merged.append((current_min, current_max))
            current_min = next_min
            current_max = next_max
    merged.append((current_min, current_max))
    return pd.DataFrame(merged, columns=["min", "max"], dtype=float)


# --------------------------------------------------------------------------- #
# Score bands & segment geometry (R/10_reference_curves.R:158-278)
# --------------------------------------------------------------------------- #


def reference_curve_score_band_for_value(index_score: Any, tol: float = 1e-9) -> Optional[str]:
    v = _r_as_numeric(index_score)
    if not math.isfinite(v):
        return None
    if v >= 0.70 - tol:
        return "functioning"
    if v >= 0.30 - tol:
        return "at_risk"
    return "not_functioning"


def reference_curve_segment_breaks(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    thresholds: tuple[float, ...] = (0.30, 0.70),
    tol: float = 1e-9,
) -> list[float]:
    """Segment endpoints plus interpolated x where the segment hits a threshold
    (rounded to 10 digits, sorted ascending)."""
    breaks = [x1, x2]

    if abs(y2 - y1) > tol:
        lo = min(y1, y2)
        hi = max(y1, y2)
        for target in thresholds:
            if (target + tol) < lo or (target - tol) > hi:
                continue
            x_val = x1 + (target - y1) * (x2 - x1) / (y2 - y1)
            if math.isfinite(x_val):
                breaks.append(x_val)

    return sorted(reference_curve_unique_numeric(breaks))


def reference_curve_band_intervals(points: Any, tol: float = 1e-9) -> dict[str, pd.DataFrame]:
    """Split curve segments at the 0.30/0.70 breakpoints and classify each
    sub-interval by its midpoint score. Vertical segments emit zero-width
    point-intervals into every band their y-span touches."""
    points = normalize_reference_curve_points(points)
    empty = {
        "functioning": empty_reference_curve_intervals(),
        "at_risk": empty_reference_curve_intervals(),
        "not_functioning": empty_reference_curve_intervals(),
    }
    if len(points) < 2:
        return empty

    buckets: dict[str, list[tuple[float, float]]] = {
        "functioning": [],
        "at_risk": [],
        "not_functioning": [],
    }

    def add_interval(bucket: str, x_min: float, x_max: float) -> None:
        if not (math.isfinite(x_min) and math.isfinite(x_max)):
            return
        buckets[bucket].append((min(x_min, x_max), max(x_min, x_max)))

    mv = points["metric_value"].to_numpy(dtype=float)
    isc = points["index_score"].to_numpy(dtype=float)

    for i in range(len(points) - 1):
        x1, y1 = float(mv[i]), float(isc[i])
        x2, y2 = float(mv[i + 1]), float(isc[i + 1])

        if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
            continue

        if abs(x2 - x1) <= tol:
            y_low = min(y1, y2)
            y_high = max(y1, y2)
            if y_high >= (0.70 - tol):
                add_interval("functioning", x1, x1)
            if y_low < (0.70 - tol) and y_high >= (0.30 - tol):
                add_interval("at_risk", x1, x1)
            if y_low < (0.30 - tol):
                add_interval("not_functioning", x1, x1)
            continue

        breaks = reference_curve_segment_breaks(x1, y1, x2, y2, tol=tol)
        if len(breaks) < 2:
            continue

        for j in range(len(breaks) - 1):
            x_min = breaks[j]
            x_max = breaks[j + 1]
            if x_max < (x_min - tol):
                continue

            mid_x = x_min if abs(x_max - x_min) <= tol else (x_min + x_max) / 2
            mid_y = y1 + (mid_x - x1) * (y2 - y1) / (x2 - x1)
            bucket = reference_curve_score_band_for_value(mid_y, tol=tol)

            if bucket is not None:
                add_interval(bucket, x_min, x_max)

    return {
        name: reference_curve_merge_intervals(
            pd.DataFrame(vals, columns=["min", "max"], dtype=float)
            if vals
            else empty_reference_curve_intervals(),
            tol=tol,
        )
        for name, vals in buckets.items()
    }


# --------------------------------------------------------------------------- #
# Threshold crossings (R/10_reference_curves.R:280-371)
# --------------------------------------------------------------------------- #


def reference_curve_score_relation(
    index_score: Any, target_score: float, tol: float = 1e-9
) -> Optional[int]:
    """-1/0/+1 relation of a point's index score to the target (None for NA)."""
    if _is_na_scalar(index_score):
        return None
    v = float(index_score)
    if math.isnan(v):
        return None
    if v < (target_score - tol):
        return -1
    if v > (target_score + tol):
        return 1
    return 0


def reference_curve_threshold_crossings(
    points: Any, target_score: float, tol: float = 1e-9
) -> list[float]:
    """Metric values where the curve crosses ``target_score``.

    Strict sign flips interpolate (vertical segments contribute the shared x);
    runs of points sitting exactly on the target contribute the run-start x
    when the curve rises through the run, else the run-end x; one-sided touches
    contribute nothing. Result is rounded to 10 digits, de-duplicated, sorted.
    """
    points = normalize_reference_curve_points(points)
    n = len(points)
    if n < 2:
        return []

    mv = points["metric_value"].to_numpy(dtype=float)
    isc = points["index_score"].to_numpy(dtype=float)
    relations = [reference_curve_score_relation(y, target_score, tol=tol) for y in isc]
    candidates: list[float] = []

    for i in range(n - 1):
        rel_1 = relations[i]
        rel_2 = relations[i + 1]
        if rel_1 is None or rel_2 is None or rel_1 == 0 or rel_2 == 0 or rel_1 == rel_2:
            continue

        x1, x2 = float(mv[i]), float(mv[i + 1])
        y1, y2 = float(isc[i]), float(isc[i + 1])

        if not all(math.isfinite(v) for v in (x1, x2, y1, y2)):
            continue

        if abs(x2 - x1) <= tol:
            candidates.append(x1)
        else:
            x_val = x1 + (target_score - y1) * (x2 - x1) / (y2 - y1)
            if math.isfinite(x_val):
                candidates.append(x_val)

    idx = 0
    while idx < n:
        if relations[idx] != 0:  # None (NA) also breaks/skips runs, as in R
            idx += 1
            continue

        run_start = idx
        while idx < n and relations[idx] == 0:
            idx += 1
        run_end = idx - 1

        left_candidates = [j for j in range(run_start) if relations[j] not in (None, 0)]
        right_candidates = [j for j in range(idx, n) if relations[j] not in (None, 0)]

        if not left_candidates or not right_candidates:
            continue

        left_rel = relations[left_candidates[-1]]
        right_rel = relations[right_candidates[0]]
        if left_rel == right_rel:
            continue

        if left_rel < right_rel:
            candidates.append(float(mv[run_start]))
        else:
            candidates.append(float(mv[run_end]))

    return sorted(reference_curve_unique_numeric(candidates))


# --------------------------------------------------------------------------- #
# Display text helpers (R/10_reference_curves.R:373-447)
# --------------------------------------------------------------------------- #


def reference_curve_crossings_text(crossings: Any, digits: int = 2) -> str:
    crossings = reference_curve_unique_numeric(crossings)
    if not crossings:
        return "N/A"
    return ", ".join(
        reference_curve_format_number(c, digits=digits) for c in crossings
    )


def reference_curve_interval_ranges_text(ranges: Any, digits: int = 2, tol: float = 1e-9) -> str:
    ranges = reference_curve_merge_intervals(ranges, tol=tol)
    if len(ranges) == 0:
        return "N/A"

    parts = []
    for i in range(len(ranges)):
        x_min = float(ranges["min"].iloc[i])
        x_max = float(ranges["max"].iloc[i])
        if abs(x_max - x_min) <= tol:
            parts.append(reference_curve_format_number(x_min, digits=digits))
        else:
            parts.append(
                reference_curve_format_number(x_min, digits=digits)
                + " - "
                + reference_curve_format_number(x_max, digits=digits)
            )
    return ", ".join(parts)


def reference_curve_row_range_display(curve_row: Any, range_name: str, digits: int = 2) -> str:
    row = _first_row(curve_row)
    if row is None:
        return "N/A"

    display_field = f"{range_name}_ranges_display"
    list_field = f"{range_name}_ranges"
    min_field = f"{range_name}_min"
    max_field = f"{range_name}_max"

    if display_field in row.columns:
        display_raw = row[display_field].iloc[0]
        display_value = "" if _is_na_scalar(display_raw) else str(display_raw)
        if display_value and display_value != "NA":
            return display_value

    if list_field in row.columns:
        return reference_curve_interval_ranges_text(row[list_field].iloc[0], digits=digits)

    if min_field in row.columns and max_field in row.columns:
        return reference_curve_interval_ranges_text(
            pd.DataFrame(
                {
                    "min": [_r_as_numeric(row[min_field].iloc[0])],
                    "max": [_r_as_numeric(row[max_field].iloc[0])],
                }
            ),
            digits=digits,
        )

    return "N/A"


def reference_curve_points_from_row(curve_row: Any, higher_is_better: Any = None) -> pd.DataFrame:
    """Recover a point table from a registry row: prefer the nested
    ``curve_points`` cell, else rebuild from the flattened
    ``curve_point{N}_x/_y`` columns. ``higher_is_better`` is accepted for
    signature parity but unused (as in R)."""
    row = _first_row(curve_row)
    if row is None:
        return empty_reference_curve_points()

    if "curve_points" in row.columns:
        stored_points = row["curve_points"].iloc[0]
        if _is_na_scalar(stored_points):
            stored_points = None
        normalized = normalize_reference_curve_points(stored_points)
        if len(normalized) >= 2:
            return normalized

    point_x_cols = [c for c in row.columns if re.fullmatch(r"curve_point[0-9]+_x", str(c))]
    if not point_x_cols:
        return empty_reference_curve_points()

    point_ids = sorted(
        int(re.fullmatch(r"curve_point([0-9]+)_x", str(c)).group(1)) for c in point_x_cols
    )

    records = []
    for idx in point_ids:
        x_col = f"curve_point{idx}_x"
        y_col = f"curve_point{idx}_y"
        if x_col not in row.columns or y_col not in row.columns:
            continue

        x_val = _r_as_numeric(row[x_col].iloc[0])
        y_val = _r_as_numeric(row[y_col].iloc[0])
        if math.isnan(x_val) or math.isnan(y_val):
            continue

        records.append({"point_order": idx, "metric_value": x_val, "index_score": y_val})

    return normalize_reference_curve_points(records if records else None)


# --------------------------------------------------------------------------- #
# Metric value at a target score (R/10_reference_curves.R:495-536)
# --------------------------------------------------------------------------- #


def reference_curve_metric_at_score(points: Any, target_score: float, prefer: str = "left") -> float:
    """Metric value where the curve reaches ``target_score``.

    Flat segments sitting on the target contribute their left (min x) or right
    (max x) end per ``prefer``; sloped segments interpolate. The result is the
    min (prefer="left") or max (prefer="right") candidate after rounding to 10
    digits. NaN when no segment reaches the target.
    """
    if prefer not in ("left", "right"):
        raise ValueError("'prefer' should be one of \"left\", \"right\"")
    points = normalize_reference_curve_points(points)
    if len(points) < 2:
        return float("nan")

    tol = 1e-9
    mv = points["metric_value"].to_numpy(dtype=float)
    isc = points["index_score"].to_numpy(dtype=float)
    candidates: list[float] = []

    for i in range(len(points) - 1):
        x1, y1 = float(mv[i]), float(isc[i])
        x2, y2 = float(mv[i + 1]), float(isc[i + 1])

        # NOTE(parity): R errors outright if an index_score here is NA (the
        # band check hits `if (NA)`); NaN comparisons are False in Python so
        # NA segments simply contribute nothing.
        lo = min(y1, y2)
        hi = max(y1, y2)
        if (target_score + tol) < lo or (target_score - tol) > hi:
            continue

        if abs(y2 - y1) <= tol:
            if abs(y1 - target_score) <= tol:
                candidates.append(
                    float(np.minimum(x1, x2)) if prefer == "left" else float(np.maximum(x1, x2))
                )
            continue

        x_val = x1 + (target_score - y1) * (x2 - x1) / (y2 - y1)
        if math.isfinite(x_val):
            candidates.append(x_val)

    if not candidates:
        return float("nan")

    # R: unique(round(candidates, 10)) then min/max — the rounding sticks.
    rounded = np.round(np.asarray(candidates, dtype=float), 10)
    return float(np.min(rounded)) if prefer == "left" else float(np.max(rounded))


# --------------------------------------------------------------------------- #
# Validation (R/10_reference_curves.R:538-590)
# --------------------------------------------------------------------------- #


def validate_reference_curve_points(points: Any, higher_is_better: Any) -> dict:
    """Validate a (manual or auto) point set. ``higher_is_better`` is accepted
    for signature parity but unused by the checks (as in R). Returns
    ``{"valid": bool, "errors": list[str], "points": DataFrame}``."""
    points = normalize_reference_curve_points(points)
    errors: list[str] = []
    tol = 1e-9

    mv = points["metric_value"].to_numpy(dtype=float)
    isc = points["index_score"].to_numpy(dtype=float)

    if len(points) < 2:
        errors.append("At least 2 curve points are required.")

    if np.isnan(mv).any() or np.isnan(isc).any():
        errors.append("Metric score and index score must be numeric for every point.")

    if not errors:
        metric_diffs = np.diff(mv)
        if bool((metric_diffs < -tol).any()):
            errors.append(
                "Metric score must be non-decreasing from top to bottom. "
                "Equal consecutive values are allowed."
            )

    # NOTE(parity): this bounds check is NOT gated on earlier errors in R.
    with np.errstate(invalid="ignore"):
        if bool(((isc < -tol) | (isc > (1 + tol))).any()):
            errors.append("Index score values must be between 0 and 1.")

    if not errors:
        score_30_crossings = reference_curve_threshold_crossings(points, 0.30, tol=tol)
        score_70_crossings = reference_curve_threshold_crossings(points, 0.70, tol=tol)

        if len(score_30_crossings) > 2:
            errors.append("Manual curves can cross index score 0.30 at most twice.")
        if len(score_70_crossings) > 2:
            errors.append("Manual curves can cross index score 0.70 at most twice.")

    if not errors:
        score_min = float(np.min(isc))
        score_max = float(np.max(isc))
        if score_min > (0.30 + tol) or score_max < (0.70 - tol):
            errors.append("Curve points must span index scores 0.30 and 0.70.")

    return {"valid": len(errors) == 0, "errors": errors, "points": points}


# --------------------------------------------------------------------------- #
# Summary stats (R/10_reference_curves.R:592-622)
# --------------------------------------------------------------------------- #


def reference_curve_summary_stats(ref_values: Any) -> dict:
    """Distribution stats of the finite reference values (R type-7 quantiles)."""
    if ref_values is None:
        arr = np.array([], dtype=float)
    else:
        arr = np.atleast_1d(np.asarray(pd.Series(ref_values), dtype=float))
    arr = arr[np.isfinite(arr)]

    if arr.size == 0:
        return {
            "n_reference": 0,
            "median_val": float("nan"),
            "mean_val": float("nan"),
            "sd_val": float("nan"),
            "min_val": float("nan"),
            "max_val": float("nan"),
            "q25": float("nan"),
            "q75": float("nan"),
            "iqr": float("nan"),
        }

    q25 = float(np.quantile(arr, 0.25))  # np default (linear) == R type 7
    q75 = float(np.quantile(arr, 0.75))

    return {
        "n_reference": int(arr.size),
        "median_val": float(np.median(arr)),
        "mean_val": float(np.mean(arr)),
        # R sd() of a length-1 vector is NA
        "sd_val": float(np.std(arr, ddof=1)) if arr.size >= 2 else float("nan"),
        "min_val": float(np.min(arr)),
        "max_val": float(np.max(arr)),
        "q25": q25,
        "q75": q75,
        "iqr": float(q75 - q25),
    }


# --------------------------------------------------------------------------- #
# Registry row (R/10_reference_curves.R:624-723)
# --------------------------------------------------------------------------- #

_STATS_KEYS = (
    "n_reference",
    "median_val",
    "mean_val",
    "sd_val",
    "min_val",
    "max_val",
    "q25",
    "q75",
    "iqr",
)


def build_reference_curve_row(
    metric_key: str,
    metric_config: Mapping,
    stats: Mapping,
    curve_points: Any,
    curve_source: str = "auto",
    stratum_label: Any = None,
    curve_status: str = "complete",
) -> pd.DataFrame:
    """The one-row registry record every exporter consumes."""
    mc = metric_config.get(metric_key) or {}
    higher_is_better = mc.get("higher_is_better") is True
    points = normalize_reference_curve_points(curve_points)

    if len(points) > 0:
        mv = points["metric_value"].to_numpy(dtype=float)
        non_na = mv[~np.isnan(mv)]
        # NOTE(parity): R min/max(x, na.rm=TRUE) of an all-NA vector -> +/-Inf.
        points_min = float(np.min(non_na)) if non_na.size else float("inf")
        points_max = float(np.max(non_na)) if non_na.size else float("-inf")
    else:
        points_min = float("nan")
        points_max = float("nan")

    band_ranges = reference_curve_band_intervals(points)

    prefer_threshold = "left" if higher_is_better else "right"
    score_30_metric = reference_curve_metric_at_score(points, 0.30, prefer=prefer_threshold)
    score_70_metric = reference_curve_metric_at_score(points, 0.70, prefer=prefer_threshold)
    score_100_metric = reference_curve_metric_at_score(points, 1.00, prefer=prefer_threshold)
    score_30_crossing_values = reference_curve_threshold_crossings(points, 0.30)
    score_70_crossing_values = reference_curve_threshold_crossings(points, 0.70)
    score_100_crossing_values = reference_curve_threshold_crossings(points, 1.00)
    score_30_crossings_display = reference_curve_crossings_text(score_30_crossing_values)
    score_70_crossings_display = reference_curve_crossings_text(score_70_crossing_values)
    score_100_crossings_display = reference_curve_crossings_text(score_100_crossing_values)

    if higher_is_better:
        functioning_min = score_70_metric
        functioning_max = points_max if math.isnan(score_100_metric) else score_100_metric
        at_risk_min = score_30_metric
        at_risk_max = score_70_metric
        not_functioning_min = points_min
        not_functioning_max = score_30_metric
    else:
        functioning_min = points_min if math.isnan(score_100_metric) else score_100_metric
        functioning_max = score_70_metric
        at_risk_min = score_70_metric
        at_risk_max = score_30_metric
        not_functioning_min = score_30_metric
        not_functioning_max = points_max

    if curve_status == "complete":
        if len(score_30_crossing_values) > 2 or len(score_70_crossing_values) > 2:
            curve_status = "unsupported_multi_crossing"
        elif math.isnan(score_30_metric) or math.isnan(score_70_metric):
            curve_status = "degenerate_curve"

    point_fields: dict[str, float] = {}
    for idx in range(1, 4):
        if len(points) >= idx:
            point_fields[f"curve_point{idx}_x"] = float(points["metric_value"].iloc[idx - 1])
            point_fields[f"curve_point{idx}_y"] = float(points["index_score"].iloc[idx - 1])
        else:
            point_fields[f"curve_point{idx}_x"] = float("nan")
            point_fields[f"curve_point{idx}_y"] = float("nan")

    # R: stratum NULL -> NA_character_, and as.character(NA) is still NA.
    if stratum_label is None or _is_na_scalar(stratum_label):
        stratum = None
    else:
        stratum = str(stratum_label)

    row = pd.DataFrame(index=[0])
    row["metric"] = [metric_key]
    row["display_name"] = [mc.get("display_name")]
    for key in _STATS_KEYS:
        row[key] = [stats[key]]
    row["functioning_min"] = [_r_as_numeric(functioning_min)]
    row["functioning_max"] = [_r_as_numeric(functioning_max)]
    row["at_risk_min"] = [_r_as_numeric(at_risk_min)]
    row["at_risk_max"] = [_r_as_numeric(at_risk_max)]
    row["not_functioning_min"] = [_r_as_numeric(not_functioning_min)]
    row["not_functioning_max"] = [_r_as_numeric(not_functioning_max)]
    row["higher_is_better"] = [higher_is_better]
    for name, value in point_fields.items():
        row[name] = [value]
    row["curve_n_points"] = [len(points)]
    row["curve_source"] = [curve_source]
    row["score_30_metric"] = [_r_as_numeric(score_30_metric)]
    row["score_70_metric"] = [_r_as_numeric(score_70_metric)]
    _assign_cell(row, "score_30_crossings", list(score_30_crossing_values))
    _assign_cell(row, "score_70_crossings", list(score_70_crossing_values))
    _assign_cell(row, "score_100_crossings", list(score_100_crossing_values))
    row["score_30_crossing_count"] = [len(score_30_crossing_values)]
    row["score_70_crossing_count"] = [len(score_70_crossing_values)]
    row["score_100_crossing_count"] = [len(score_100_crossing_values)]
    row["score_30_crossings_display"] = [score_30_crossings_display]
    row["score_70_crossings_display"] = [score_70_crossings_display]
    row["score_100_crossings_display"] = [score_100_crossings_display]
    _assign_cell(row, "functioning_ranges", band_ranges["functioning"])
    _assign_cell(row, "at_risk_ranges", band_ranges["at_risk"])
    _assign_cell(row, "not_functioning_ranges", band_ranges["not_functioning"])
    row["functioning_ranges_display"] = [
        reference_curve_interval_ranges_text(band_ranges["functioning"])
    ]
    row["at_risk_ranges_display"] = [reference_curve_interval_ranges_text(band_ranges["at_risk"])]
    row["not_functioning_ranges_display"] = [
        reference_curve_interval_ranges_text(band_ranges["not_functioning"])
    ]
    row["curve_status"] = [curve_status]
    row["stratum"] = [stratum]
    _assign_cell(row, "curve_points", points)
    return row


# --------------------------------------------------------------------------- #
# Plot data-prep helper (R/10_reference_curves.R:725-741) — shared by the
# deferred ggplot builders; ported so the views layer reuses one implementation.
# --------------------------------------------------------------------------- #


def reference_curve_x_range(ref_values: Any, points: Any) -> tuple[float, float]:
    if points is not None and isinstance(points, pd.DataFrame) and "metric_value" in points.columns:
        pv = points["metric_value"].to_numpy(dtype=float)
    else:
        pv = np.array([], dtype=float)
    if ref_values is None:
        rv = np.array([], dtype=float)
    else:
        rv = np.atleast_1d(np.asarray(pd.Series(ref_values), dtype=float))

    x_values = np.concatenate([rv, np.atleast_1d(pv)])
    x_values = x_values[np.isfinite(x_values)]
    if x_values.size == 0:
        return (0.0, 1.0)

    x_min = float(np.min(x_values))
    x_max = float(np.max(x_values))
    if x_min == x_max:
        x_min = x_min - 1
        x_max = x_max + 1

    padding = max((x_max - x_min) * 0.08, 0.5)
    return (x_min - padding, x_max + padding)


# --------------------------------------------------------------------------- #
# Result-shape normalization (R/10_reference_curves.R:942-1093)
# --------------------------------------------------------------------------- #


def normalize_reference_curve_result(
    result: Any,
    metric_config: Optional[Mapping] = None,
    metric_key: Optional[str] = None,
    stratum_label: Any = None,
) -> Optional[dict]:
    """Rebuild a stored/restored curve result into the canonical shape,
    re-deriving the registry row from its stored stats + points."""
    if result is None:
        return None

    target = result
    if (
        isinstance(result, Mapping)
        and result.get("reference_curve") is not None
        and result.get("curve_row") is None
    ):
        target = result["reference_curve"]

    curve_row = None
    target_row = target.get("curve_row") if isinstance(target, Mapping) else None
    if target_row is not None and len(target_row) > 0:
        curve_row = _first_row(target_row)

    if metric_key is None and curve_row is not None and "metric" in curve_row.columns:
        metric_key = curve_row["metric"].iloc[0]

    higher_is_better = None
    metric_config = metric_config or {}
    if curve_row is not None and "higher_is_better" in curve_row.columns:
        cell = curve_row["higher_is_better"].iloc[0]
        higher_is_better = bool(cell) if isinstance(cell, (bool, np.bool_)) else False
    elif metric_key is not None and metric_config.get(metric_key) is not None:
        higher_is_better = metric_config[metric_key].get("higher_is_better") is True

    curve_points = normalize_reference_curve_points(
        target.get("curve_points") if isinstance(target, Mapping) else None
    )
    if len(curve_points) < 2 and curve_row is not None:
        curve_points = reference_curve_points_from_row(curve_row, higher_is_better)

    if isinstance(target, Mapping) and target.get("curve_source") is not None:
        curve_source = str(target["curve_source"])
    elif curve_row is not None and "curve_source" in curve_row.columns:
        curve_source = str(curve_row["curve_source"].iloc[0])
    else:
        curve_source = "auto"

    if (
        curve_row is not None
        and metric_key is not None
        and metric_config.get(metric_key) is not None
    ):
        stats = {
            key: (curve_row[key].iloc[0] if key in curve_row.columns else float("nan"))
            for key in _STATS_KEYS
        }
        current_status = (
            curve_row["curve_status"].iloc[0] if "curve_status" in curve_row.columns else "complete"
        )
        current_stratum = (
            stratum_label
            if stratum_label is not None
            else (curve_row["stratum"].iloc[0] if "stratum" in curve_row.columns else None)
        )

        rebuilt_row = build_reference_curve_row(
            metric_key,
            metric_config=metric_config,
            stats=stats,
            curve_points=curve_points,
            curve_source=curve_source,
            stratum_label=current_stratum,
            curve_status=current_status,
        )

        # NOTE(parity): the original row's nested curve_points cell overrides
        # the rebuilt one, and unknown extra columns are carried over.
        for nm in curve_row.columns:
            if nm not in rebuilt_row.columns or nm == "curve_points":
                _assign_cell(rebuilt_row, nm, curve_row[nm].iloc[0])
        curve_row = rebuilt_row

    return {
        "curve_row": curve_row,
        "curve_points": curve_points,
        "curve_source": curve_source,
        "bar_chart_plot": target.get("bar_chart_plot") if isinstance(target, Mapping) else None,
        "curve_plot": target.get("curve_plot") if isinstance(target, Mapping) else None,
    }


def strip_reference_curve_result(
    result: Any,
    metric_config: Optional[Mapping] = None,
    metric_key: Optional[str] = None,
    stratum_label: Any = None,
) -> Optional[dict]:
    normalized = normalize_reference_curve_result(
        result,
        metric_config=metric_config,
        metric_key=metric_key,
        stratum_label=stratum_label,
    )

    if normalized is None:
        return None

    normalized["bar_chart_plot"] = None
    normalized["curve_plot"] = None
    return normalized


def hydrate_reference_curve_result(
    result: Any,
    data: Any,
    metric_key: str,
    metric_config: Mapping,
    stratum_label: Any = None,
    artifact_mode: str = "full",
) -> Optional[dict]:
    if artifact_mode not in ("full", "summary"):
        raise ValueError("'artifact_mode' should be one of \"full\", \"summary\"")
    normalized = normalize_reference_curve_result(
        result,
        metric_config=metric_config,
        metric_key=metric_key,
        stratum_label=stratum_label,
    )

    if normalized is None:
        return None

    if artifact_mode == "summary":
        normalized["bar_chart_plot"] = None
        normalized["curve_plot"] = None
        return normalized

    curve_source = normalized["curve_source"] if normalized["curve_source"] is not None else "auto"
    curve_points = normalize_reference_curve_points(normalized["curve_points"])
    curve_row = normalized["curve_row"]
    curve_status = None
    if curve_row is not None and "curve_status" in curve_row.columns:
        curve_status = curve_row["curve_status"].iloc[0]
    if curve_status is None:
        curve_status = "complete"

    if curve_status != "complete":
        return normalized

    if normalized["bar_chart_plot"] is not None and normalized["curve_plot"] is not None:
        return normalized

    if curve_source == "manual" and len(curve_points) >= 2:
        return build_reference_curve_from_points(
            data=data,
            metric_key=metric_key,
            metric_config=metric_config,
            curve_points=curve_points,
            stratum_label=stratum_label,
            build_plots=True,
        )

    return build_reference_curve(
        data=data,
        metric_key=metric_key,
        metric_config=metric_config,
        stratum_label=stratum_label,
        build_plots=True,
    )


# --------------------------------------------------------------------------- #
# Export flattening (R/10_reference_curves.R:1095-1130)
# --------------------------------------------------------------------------- #

_CROSSING_FIELDS = ("score_30_crossings", "score_70_crossings", "score_100_crossings")
_RANGE_FIELDS = ("functioning_ranges", "at_risk_ranges", "not_functioning_ranges")
_DISPLAY_DROP_FIELDS = (
    "score_30_crossings_display",
    "score_70_crossings_display",
    "score_100_crossings_display",
    "functioning_ranges_display",
    "at_risk_ranges_display",
    "not_functioning_ranges_display",
)


def _is_list_column(series: pd.Series) -> bool:
    """R is.list(column): true for list-columns, false for plain vectors."""
    if series.dtype != object:
        return False
    return any(
        isinstance(v, (list, tuple, np.ndarray, pd.Series, pd.DataFrame)) or v is None
        for v in series
    )


def reference_curve_rows_for_export(rows: Any) -> pd.DataFrame:
    """Stringify crossing/range list-columns, drop _display columns and the
    nested curve_points, ready for CSV export."""
    if rows is None:
        return pd.DataFrame()

    rows = pd.DataFrame(rows).copy()

    for field in _CROSSING_FIELDS:
        if field in rows.columns and _is_list_column(rows[field]):
            rows[field] = [reference_curve_crossings_text(v) for v in rows[field]]

    for field in _RANGE_FIELDS:
        if field in rows.columns and _is_list_column(rows[field]):
            rows[field] = [reference_curve_interval_ranges_text(v) for v in rows[field]]

    keep = [c for c in rows.columns if c not in _DISPLAY_DROP_FIELDS]
    rows = rows[keep]

    if "curve_points" in rows.columns:
        rows = rows.drop(columns=["curve_points"])
    return rows


# --------------------------------------------------------------------------- #
# Curve builders (R/10_reference_curves.R:1132-1291)
# --------------------------------------------------------------------------- #


def _column_values(data: Any, col_name: Optional[str]) -> np.ndarray:
    """data[[col_name]] with R's !is.na filter (drops NA/NaN, keeps +/-Inf)."""
    if col_name is None:
        raw: Any = []
    elif isinstance(data, pd.DataFrame):
        raw = data[col_name] if col_name in data.columns else []
    elif isinstance(data, Mapping):
        raw = data.get(col_name, [])
    else:
        raw = []
    arr = np.atleast_1d(np.asarray(pd.Series(raw), dtype=float))
    return arr[~np.isnan(arr)]


def build_reference_curve_from_components(
    data: Any,
    metric_key: str,
    metric_config: Mapping,
    curve_points: Any,
    curve_source: str = "auto",
    stratum_label: Any = None,
    curve_status: str = "complete",
    build_plots: bool = True,
) -> dict:
    mc = metric_config.get(metric_key) or {}
    col_name = mc.get("column_name")
    ref_values = _column_values(data, col_name)
    stats = reference_curve_summary_stats(ref_values)

    curve_row = build_reference_curve_row(
        metric_key=metric_key,
        metric_config=metric_config,
        stats=stats,
        curve_points=curve_points,
        curve_source=curve_source,
        stratum_label=stratum_label,
        curve_status=curve_status,
    )

    points = normalize_reference_curve_points(curve_points)
    # NOTE(parity): R builds ggplot objects here when build_plots is TRUE and
    # the row is "complete"; plot construction is deferred to the views layer.
    bar_chart_plot = None
    curve_plot = None

    return {
        "curve_row": curve_row,
        "curve_points": points,
        "curve_source": curve_source,
        "bar_chart_plot": bar_chart_plot,
        "curve_plot": curve_plot,
    }


def build_reference_curve(
    data: Any,
    metric_key: str,
    metric_config: Mapping,
    stratum_label: Any = None,
    build_plots: bool = True,
) -> dict:
    """Auto-build a metric's reference curve from its reference distribution.

    n<5 non-NA values -> "insufficient_data" (empty points). Higher-is-better
    with non-finite or <=0 Q25 -> 3-point "degenerate_q25" curve. Non-finite
    IQR -> "degenerate_curve" (empty points). Otherwise the 5-point IQR seed,
    validated; invalid seeds keep their points but get "degenerate_curve".
    """
    mc = metric_config.get(metric_key) or {}
    col_name = mc.get("column_name")
    higher_is_better = mc.get("higher_is_better") is True
    ref_values = _column_values(data, col_name)
    stats = reference_curve_summary_stats(ref_values)

    if len(ref_values) < 5:
        logger.warning(f"{metric_key}: too few reference values ({len(ref_values)})")
        empty_row = build_reference_curve_row(
            metric_key=metric_key,
            metric_config=metric_config,
            stats=stats,
            curve_points=empty_reference_curve_points(),
            curve_source="auto",
            stratum_label=stratum_label,
            curve_status="insufficient_data",
        )

        return {
            "curve_row": empty_row,
            "curve_points": empty_reference_curve_points(),
            "curve_source": "auto",
            "bar_chart_plot": None,
            "curve_plot": None,
        }

    if higher_is_better and (not math.isfinite(stats["q25"]) or stats["q25"] <= 0):
        logger.warning(f"{metric_key}: Q25 <= 0, scoring curve is degenerate")
        return build_reference_curve_from_components(
            data=data,
            metric_key=metric_key,
            metric_config=metric_config,
            curve_points=pd.DataFrame(
                {
                    "point_order": [1, 2, 3],
                    "metric_value": [0.0, stats["q25"], stats["q75"]],
                    "index_score": [0.00, 0.70, 1.00],
                }
            ),
            curve_source="auto",
            stratum_label=stratum_label,
            curve_status="degenerate_q25",
            build_plots=build_plots,
        )

    if not math.isfinite(stats["iqr"]) or stats["iqr"] < 0:
        return build_reference_curve_from_components(
            data=data,
            metric_key=metric_key,
            metric_config=metric_config,
            curve_points=empty_reference_curve_points(),
            curve_source="auto",
            stratum_label=stratum_label,
            curve_status="degenerate_curve",
            build_plots=build_plots,
        )

    if higher_is_better:
        auto_points = pd.DataFrame(
            {
                "point_order": [1, 2, 3, 4, 5],
                "metric_value": [
                    0.0,
                    stats["q25"] * 3 / 7,
                    stats["q25"],
                    stats["q75"],
                    stats["q75"] + stats["iqr"] * 0.3,
                ],
                "index_score": [0.00, 0.30, 0.70, 1.00, 1.00],
            }
        )
    else:
        auto_points = pd.DataFrame(
            {
                "point_order": [1, 2, 3, 4, 5],
                "metric_value": [
                    max(0, stats["q25"] - stats["iqr"] * 0.3),
                    stats["q25"],
                    stats["q75"],
                    stats["q75"] + stats["iqr"] * 4 / 3,
                    stats["q75"] + stats["iqr"] * 7 / 3,
                ],
                "index_score": [1.00, 1.00, 0.70, 0.30, 0.00],
            }
        )

    validation = validate_reference_curve_points(auto_points, higher_is_better)
    status = "complete" if validation["valid"] else "degenerate_curve"

    return build_reference_curve_from_components(
        data=data,
        metric_key=metric_key,
        metric_config=metric_config,
        curve_points=validation["points"] if validation["valid"] else auto_points,
        curve_source="auto",
        stratum_label=stratum_label,
        curve_status=status,
        build_plots=build_plots,
    )


def build_reference_curve_from_points(
    data: Any,
    metric_key: str,
    metric_config: Mapping,
    curve_points: Any,
    stratum_label: Any = None,
    build_plots: bool = True,
) -> dict:
    """Manual-curve path: validation failure raises ValueError with the joined
    error strings (R stop())."""
    higher_is_better = (metric_config.get(metric_key) or {}).get("higher_is_better") is True
    validation = validate_reference_curve_points(curve_points, higher_is_better)
    if not validation["valid"]:
        raise ValueError(" ".join(validation["errors"]))

    return build_reference_curve_from_components(
        data=data,
        metric_key=metric_key,
        metric_config=metric_config,
        curve_points=validation["points"],
        curve_source="manual",
        stratum_label=stratum_label,
        curve_status="complete",
        build_plots=build_plots,
    )


# --------------------------------------------------------------------------- #
# Batch runner (R/10_reference_curves.R:1396-1439)
# --------------------------------------------------------------------------- #


def run_all_reference_curves(
    data: Any,
    metric_config: Mapping,
    model_selections: Any = None,
    diagnostic_summary: Any = None,
    all_models: Any = None,
) -> dict:
    """Build reference curves for every eligible metric (non-categorical with a
    declared higher_is_better). Extra arguments are accepted for signature
    parity and unused, as in R. The R furrr parallel branch is not ported —
    metrics are mapped sequentially."""
    logger.info("Building reference curves for all metrics...")

    eligible = [
        mk
        for mk in metric_config
        if (metric_config[mk] or {}).get("metric_family") != "categorical"
        and (metric_config[mk] or {}).get("higher_is_better") is not None
    ]

    logger.info(f"Processing {len(eligible)} metrics...")

    results_list = [build_reference_curve(data, metric_key, metric_config) for metric_key in eligible]

    if results_list:
        registry = pd.concat([r["curve_row"] for r in results_list], ignore_index=True)
    else:
        registry = pd.DataFrame()

    bar_chart_plots = {
        mk: r["bar_chart_plot"]
        for mk, r in zip(eligible, results_list)
        if r["bar_chart_plot"] is not None
    }
    curve_plots = {
        mk: r["curve_plot"] for mk, r in zip(eligible, results_list) if r["curve_plot"] is not None
    }

    logger.info(
        f"Reference curves complete: {len(registry)} curves built, "
        f"{len(bar_chart_plots)} bar charts, {len(curve_plots)} curve plots"
    )

    return {
        "registry": registry,
        "bar_chart_plots": bar_chart_plots,
        "curve_plots": curve_plots,
    }


# --------------------------------------------------------------------------- #
# Curve scoring — byte-compatible with DEEP (deep/curves.py::interp_curve).
# No R equivalent; this is the scoring contract shared across STAF tiers
# (see R/20_deep_export.R:12-15).
# --------------------------------------------------------------------------- #


def _clamp01(y: float) -> float:
    return 0.0 if y < 0.0 else 1.0 if y > 1.0 else y


def _interp_point_list(points: Any) -> list[dict]:
    """Extract [{'x': float, 'y': float}] in input order, dropping points with
    missing coordinates. Accepts the module's point tables (metric_value/
    index_score), x/y frames or mappings, and DEEP-style record lists."""
    if points is None:
        return []

    if isinstance(points, pd.DataFrame):
        if {"metric_value", "index_score"}.issubset(points.columns):
            pairs = zip(points["metric_value"].tolist(), points["index_score"].tolist())
        elif {"x", "y"}.issubset(points.columns):
            pairs = zip(points["x"].tolist(), points["y"].tolist())
        else:
            return []
    elif isinstance(points, Mapping):
        xs = points.get("metric_value", points.get("x"))
        ys = points.get("index_score", points.get("y"))
        if xs is None or ys is None:
            return []
        pairs = zip(list(xs), list(ys))
    else:
        collected = []
        for p in points:
            if isinstance(p, Mapping):
                collected.append(
                    (p.get("x", p.get("metric_value")), p.get("y", p.get("index_score")))
                )
            else:
                collected.append((p[0], p[1]))
        pairs = iter(collected)

    out: list[dict] = []
    for px, py in pairs:
        if px is None or py is None:
            continue
        fx = float(px)
        fy = float(py)
        if math.isnan(fx) or math.isnan(fy):
            continue
        out.append({"x": fx, "y": fy})
    return out


def interp_curve(points: Any, x: float) -> Optional[float]:
    """Interpolate a reference curve at ``x`` -> index in [0, 1].

    Byte-compatible with ``deep/curves.py::interp_curve``: stable sort
    ascending in x, values beyond the domain clamp to the nearest endpoint's
    index, a zero-width (coincident-x) segment takes the later point's y, and
    the result clamps into [0, 1]. Returns None when there are no usable points.
    """
    pts = sorted(_interp_point_list(points), key=lambda p: p["x"])
    if not pts:
        return None
    x = float(x)
    if len(pts) == 1 or x <= pts[0]["x"]:
        return _clamp01(pts[0]["y"])
    if x >= pts[-1]["x"]:
        return _clamp01(pts[-1]["y"])
    for a, b in zip(pts, pts[1:]):
        if a["x"] <= x <= b["x"]:
            span = b["x"] - a["x"]
            if span <= 0:  # coincident x (vertical step) -> take the later point
                return _clamp01(b["y"])
            t = (x - a["x"]) / span
            return _clamp01(a["y"] + t * (b["y"] - a["y"]))
    return _clamp01(pts[-1]["y"])


def reference_curve_score_value(curve: Any, metric_value: Any) -> float:
    """Score a metric value on a built curve -> index in [0, 1] (NaN when
    unscorable). ``curve`` may be a build_reference_curve* result dict, a
    registry curve_row, or a raw point table."""
    if curve is None:
        return float("nan")
    if metric_value is None:
        return float("nan")
    try:
        x = float(metric_value)
    except (TypeError, ValueError):
        return float("nan")
    if math.isnan(x):
        return float("nan")

    if isinstance(curve, Mapping):
        if curve.get("curve_points") is not None:
            points: Any = curve["curve_points"]
        elif curve.get("curve_row") is not None:
            points = reference_curve_points_from_row(curve["curve_row"])
        else:
            points = curve
    elif isinstance(curve, pd.DataFrame) and (
        "curve_points" in curve.columns or "curve_point1_x" in curve.columns
    ):
        points = reference_curve_points_from_row(curve)
    else:
        points = curve

    y = interp_curve(points, x)
    return float("nan") if y is None else float(y)
