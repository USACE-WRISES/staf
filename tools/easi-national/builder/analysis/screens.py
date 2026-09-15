"""The least-disturbed desktop screens (decision 2): fixed pressure caps, a
documented relaxed tier, never a percentile rule. Shared by the reference
panels (``panels``) and the NRSA screen check (``nrsa``), so both read one
definition. Variables are named as the landscape table names them; a
variable a table lacks is skipped and reported, never silently passed."""
from __future__ import annotations

from typing import Iterable

import numpy as np

#: variable -> (operator, cap); the seven pressure variables of the screen
#: The one documented national cutoff adjustment (plan step 12): with the road
#: cap at 1.0 km/km2 the strict screen passed 29 % of EPA's 2013-14 reference
#: stations nationally and under 50 % in every NARS-9 region (road density was
#: the rule those stations failed most: their median is 0.93 and their 90th
#: percentile 1.95 km/km2). At 2.0 the pass rate is 38 % of reference and 8 %
#: of most-disturbed stations (was 5 %); no other cap changed, and the share of
#: the wadeable non-canal frame passing rises from 26 % to 36 %. Recorded in
#: analysis/nrsa/screen_check_before_road_adjustment.csv.
ROAD_DENSITY_CAP = 2.0

STRICT = {
    "pctimp2019ws": ("<=", 1.0),          # impervious cover, % of the watershed
    "agriculture_ws": ("<=", 10.0),       # crop + hay, %
    "rddensws": ("<=", ROAD_DENSITY_CAP), # road density, km/km2 (see the adjustment above)
    "dor": ("<", 2.0),                    # degree of regulation, %
    "sc__nabd_densws": ("==", 0.0),       # network-snapped dams per km2
    "sc__npdesdensws": ("==", 0.0),       # NPDES outfalls per km2
    "mines_ws": ("==", 0.0),              # mines + coal mines per km2
}
RELAXED = {
    "pctimp2019ws": ("<=", 3.0),
    "agriculture_ws": ("<=", 25.0),
    "rddensws": ("<=", 2.0),
    "dor": ("<", 5.0),
    "sc__npdesdensws": ("==", 0.0),
    "mines_ws": ("==", 0.0),
}
SCREENS = {"strict": STRICT, "relaxed": RELAXED}
#: the frame every panel is drawn from: not a canal, order 1 to 5
FRAME_RULES = {"fcode_class": ("!=", "canal"), "wadeable": ("==", True)}
#: the pressure variables the composite rank averages (the caps' variables)
PRESSURE_VARIABLES = ("pctimp2019ws", "agriculture_ws", "rddensws", "dor", "sc__npdesdensws", "mines_ws",
                      "corridor_conversion_wsrp100")


def _compare(values, operator: str, cap) -> np.ndarray:
    if operator == "<=":
        return values <= cap
    if operator == "<":
        return values < cap
    if operator == "==":
        return values == cap
    if operator == "!=":
        return values != cap
    raise ValueError(f"unknown operator {operator!r}")


def evaluate(columns: dict, rules: dict, *, missing: str = "skip") -> tuple[np.ndarray, list[str]]:
    """``(mask, skipped)``: True where every available rule holds (a missing
    numeric value fails its rule); ``skipped`` names the rule variables the
    table lacks. ``missing="fail"`` raises on a missing variable instead."""
    mask = None
    skipped: list[str] = []
    for name, (operator, cap) in rules.items():
        if name not in columns:
            if missing == "fail":
                raise KeyError(f"screen variable {name!r} is not in the table")
            skipped.append(name)
            continue
        values = np.asarray(columns[name])
        if values.dtype.kind in "fiu":
            values = np.asarray(values, dtype=float)
            ok = np.isfinite(values) & _compare(values, operator, cap)
        else:
            ok = _compare(values.astype(object), operator, cap)
        mask = ok if mask is None else (mask & ok)
    if mask is None:
        mask = np.ones(len(next(iter(columns.values()))) if columns else 0, dtype=bool)
    return np.asarray(mask, dtype=bool), skipped


def composite_pressure(columns: dict, variables: Iterable[str] = PRESSURE_VARIABLES) -> tuple[np.ndarray, list[str]]:
    """The mean percentile rank (0 to 1, higher is more pressure) over the
    pressure variables present; NaN where every variable is missing."""
    ranks = []
    used = []
    n = None
    for name in variables:
        if name not in columns:
            continue
        values = np.asarray(columns[name], dtype=float)
        n = len(values)
        finite = np.isfinite(values)
        rank = np.full(n, np.nan)
        if finite.any():
            order = values[finite].argsort(kind="stable")
            r = np.empty(finite.sum())
            r[order] = np.arange(1, finite.sum() + 1)
            # average ties by ranking the sorted values against themselves
            sorted_vals = values[finite][order]
            lo = np.searchsorted(sorted_vals, sorted_vals, side="left") + 1
            hi = np.searchsorted(sorted_vals, sorted_vals, side="right")
            avg = np.empty_like(r)
            avg[order] = (lo + hi) / 2.0
            rank[finite] = (avg - 0.5) / finite.sum()
        ranks.append(rank)
        used.append(name)
    if not ranks:
        return np.full(n or 0, np.nan), used
    stacked = np.vstack(ranks)
    with np.errstate(invalid="ignore"):
        return np.nanmean(stacked, axis=0), used
