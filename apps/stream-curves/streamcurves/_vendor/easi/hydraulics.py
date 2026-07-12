"""Minimal 1-D open-channel hydraulics (Manning's) — pure Python, no I/O.

Ported from the xs-calc library (``app.core.js`` ``integrateOnPolyline`` +
binary-search ``Q_of_stage``). Station-elevation profiles come from 3DEP transects
in EPSG:5070 (metres), so we use **SI Manning** (k = 1.0):

    Q = (1/n) * A * R^(2/3) * S^(1/2),   R = A / P

The floodplain-engagement metric uses only *ratios* of discharge (Q_floodplain /
Q_bankfull), so the absolute unit convention cancels — but keeping everything in
metres/m³·s⁻¹ avoids the US-unit 1.49 foot-trap.
"""
from __future__ import annotations

from math import hypot, sqrt
from typing import Optional

MIN_SLOPE = 1e-4      # floor so a flat/zero/negative NHD slope can't break Manning
DEFAULT_N = 0.035     # generic natural-channel Manning's n (screening default)


def section_props(stations: list[float], elevs: list[float], stage: float) -> dict:
    """Wetted area A, wetted perimeter P, and top width T at a water-surface ``stage``.

    One pass over the station-elevation polyline (extends xs-calc
    ``integrateOnPolyline`` to also accumulate area and wetted perimeter). Callers
    compute ``R = A / P`` (guarding ``P > 0``). Assumes ``stations`` increasing.
    """
    A = P = 0.0
    intervals: list[list[float]] = []
    for i in range(len(stations) - 1):
        x1, z1 = stations[i], elevs[i]
        x2, z2 = stations[i + 1], elevs[i + 1]
        dx = x2 - x1
        if dx <= 0:
            continue
        d1, d2 = stage - z1, stage - z2
        if d1 <= 0 and d2 <= 0:
            continue                                   # dry segment
        if d1 > 0 and d2 > 0:                          # fully wet
            A += 0.5 * (d1 + d2) * dx
            P += hypot(dx, z2 - z1)
            intervals.append([x1, x2])
        else:                                          # partially wet -> waterline
            t = d1 / (d1 - d2)
            xi = x1 + t * dx
            if d1 > 0:                                 # left wet, right dry
                A += 0.5 * d1 * (xi - x1)
                P += hypot(xi - x1, d1)
                intervals.append([x1, xi])
            else:                                      # left dry, right wet
                A += 0.5 * d2 * (x2 - xi)
                P += hypot(x2 - xi, d2)
                intervals.append([xi, x2])
    intervals.sort()
    merged: list[list[float]] = []
    for a, b in intervals:
        if merged and a <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return {"A": A, "P": P, "T": sum(b - a for a, b in merged), "stage": stage}


def discharge(area: float, perimeter: float, slope_s: float,
              n: float = DEFAULT_N) -> float:
    """Manning's discharge (SI, k=1.0). 0 if the section is dry/degenerate."""
    if area <= 0 or perimeter <= 0:
        return 0.0
    s = max(float(slope_s or 0.0), MIN_SLOPE)
    r = area / perimeter
    return (1.0 / n) * area * (r ** (2.0 / 3.0)) * sqrt(s)


def stage_for_discharge(stations: list[float], elevs: list[float], q_target: float,
                        slope_s: float, n: float = DEFAULT_N,
                        max_iter: int = 60) -> Optional[float]:
    """Invert discharge -> stage by bisection (Q is monotonic in stage).

    Returns None if the profile is unusable or cannot convey ``q_target`` within
    its vertical extent.
    """
    if len(stations) < 2 or len(stations) != len(elevs) or q_target <= 0:
        return None
    lo, hi = min(elevs), max(elevs)
    if not hi > lo:
        return None

    def q_at(stage: float) -> float:
        sp = section_props(stations, elevs, stage)
        return discharge(sp["A"], sp["P"], slope_s, n)

    if q_at(hi) < q_target:
        return None
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if q_at(mid) < q_target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)
