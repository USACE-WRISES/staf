"""Per-section metrics with EASI's cross-section code, from the stored float32 samples.

The chain is the app's for one transect (``threedep.reach_geomorphology``): drop missing samples,
``geomorph.balanced_profile``, ``geomorph.simplify_profile``, ``geomorph.summarize_profile`` at the
section's regional bankfull. The metrics are derived from the float32 samples the archive keeps,
so recomputing from the archive reproduces them bit for bit.

``simplify_profile`` spends almost all of its time in the last step (a Visvalingam trim that
rescans every point for each one it drops). :func:`simplify` runs ``geomorph.simplify_profile``
itself for the first two steps and does the trim with a heap: same areas by the same arithmetic,
same order of removal (smallest area first, the leftmost on a tie), so the same points survive.
One section in ``config.VERIFY_EVERY`` also runs the reference and must match.
"""
from __future__ import annotations

import heapq
import math

import numpy as np

from . import config, fastgeo
from .engine import geomorph

#: the scalar outputs kept per section (``geomorph.summarize_profile``)
FLOAT_KEYS = ("entrenchment_ratio", "bank_height_ratio", "bankfull_width_m", "flood_prone_width_m",
              "bankfull_depth_m", "thalweg", "bankfull_stage_m", "low_bank_stage_m", "fp_stage_m",
              "top_of_bank_m")
BOOL_KEYS = ("low_bank_capped", "edge_limited", "bankfull_area_edge_limited")
MAX_POINTS = 250


def _tri(a, b, c) -> float:
    """``geomorph.simplify_profile``'s triangle area, operand for operand."""
    return abs(a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1])) / 2.0


def simplify(stations: list, elevs: list, max_points: int = MAX_POINTS) -> tuple:
    """``geomorph.simplify_profile(stations, elevs)`` with the trim done by a heap."""
    st, el = geomorph.simplify_profile(stations, elevs, max_points=math.inf)
    n = len(st)
    if n <= max_points or n <= 3:
        return st, el
    thal = min(range(n), key=lambda i: el[i])           # the guarded thalweg (first lowest)
    pts = list(zip(st, el))
    prev = list(range(-1, n - 1))
    nxt = list(range(1, n + 1))
    alive = [True] * n
    guard = [False] * n
    guard[0] = guard[n - 1] = guard[thal] = True
    version = [0] * n
    heap = []
    for i in range(1, n - 1):
        if not guard[i]:
            heap.append((_tri(pts[i - 1], pts[i], pts[i + 1]), i, 0))
    heapq.heapify(heap)
    left = n
    while left > max_points and heap:
        area, i, ver = heapq.heappop(heap)
        if not alive[i] or ver != version[i]:
            continue
        alive[i] = False
        left -= 1
        a, b = prev[i], nxt[i]
        nxt[a], prev[b] = b, a
        for j in (a, b):
            if 0 < j < n - 1 and not guard[j]:
                version[j] += 1
                heapq.heappush(heap, (_tri(pts[prev[j]], pts[j], pts[nxt[j]]), j, version[j]))
    keep = [i for i in range(n) if alive[i]]
    return [st[i] for i in keep], [el[i] for i in keep]


def _summarize(st, el, da_sqkm, bf_width, bf_depth, bf_area, division, res, fast: bool) -> dict:
    if not fast:
        return geomorph.summarize_profile(list(st), list(el), da_sqkm or 1.0, bankfull=(bf_width, bf_depth),
                                          bankfull_area_m2=bf_area, division=division, dem_res_m=res)
    ref = geomorph.flow_area
    geomorph.flow_area = fastgeo.flow_area          # stage_for_area's 49 calls a section
    try:
        return geomorph.summarize_profile(list(st), list(el), da_sqkm or 1.0, bankfull=(bf_width, bf_depth),
                                          bankfull_area_m2=bf_area, division=division, dem_res_m=res)
    finally:
        geomorph.flow_area = ref


def _chain(ts_ok: list, z_ok: list, args: tuple, fast: bool):
    if fast:
        bal = fastgeo.balanced_profile(ts_ok, z_ok)
        if bal is None:
            return None
        st, el = fastgeo.simplify_profile(bal[0], bal[1])
    else:
        bal = geomorph.balanced_profile(ts_ok, z_ok)
        if bal is None:
            return None
        st, el = geomorph.simplify_profile(bal[0], bal[1])
    return _summarize(st, el, *args, fast=fast), len(st)


def metrics(z32: np.ndarray, wide: float, n_pts: int, res: int, da_sqkm: float, bf_width: float,
            bf_depth: float, bf_area: float, division: str, *, verify: bool = False) -> dict:
    """One section's metrics, or ``{"status": ...}`` when the transect cannot be scored. With
    ``verify`` the untouched reference chain runs too and must agree (else the reference wins)."""
    ts = np.linspace(-wide, wide, n_pts)
    z = z32.astype(np.float64)
    ok = np.isfinite(z)
    if ok.sum() < 7:
        return {"status": "too_few_points"}
    ts_ok, z_ok = ts[ok].tolist(), z[ok].tolist()
    args = (da_sqkm, bf_width, bf_depth, bf_area, division, res)
    fast = fastgeo.ACTIVE
    got = _chain(ts_ok, z_ok, args, fast)
    out = {"status": "ok", "verified": False}
    if verify or not fast:
        ref = got if not fast else _chain(ts_ok, z_ok, args, False)
        if fast and ref != got:
            out["status"] = "ok_reference"                # never expected; the reference wins
            got = ref
        out["verified"] = True
    if got is None:
        return {"status": "unbalanced", "verified": out["verified"]}
    m, n_thin = got
    for k in FLOAT_KEYS:
        v = m.get(k)
        out[k] = None if v is None else float(v)
    for k in BOOL_KEYS:
        out[k] = bool(m.get(k)) if k in m else None
    out["bank_detection"] = m.get("bank_detection")
    zi = int(np.argmin(z[ok]))
    out["thalweg_station_m"] = float(ts[ok][zi])          # where the low point sits on the transect
    out["n_points_thinned"] = n_thin
    return out
