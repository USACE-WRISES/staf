"""Faster copies of three of EASI's cross-section functions, the same numbers bit for bit.

The national grid scores about 377 million sections, and the reference code spends most of its
time in Python bookkeeping, not arithmetic. Each function here repeats its original's arithmetic
operation for operand in the same order, so every result is the same float:

- :func:`balanced_profile` is ``geomorph.balanced_profile`` on numpy arrays (elementwise IEEE
  subtraction, exact min/max and comparisons);
- :func:`simplify_profile` is ``geomorph.simplify_profile`` with the per-point dictionaries and
  helper calls inlined, and its last step (a Visvalingam trim that rescans every point for each one
  it drops) done with a heap that removes points in the same order (smallest area first, the
  leftmost on a tie);
- :func:`flow_area` is ``geomorph.flow_area`` summing the same trapezoids in the same order without
  building lists (``stage_for_area`` calls it 49 times a section).

They are used only while ``geomorph.py`` is the file they were written against
(:data:`GEOMORPH_SHA256`); otherwise the reference runs. In the build, one section in
``config.VERIFY_EVERY`` is also scored by the untouched reference chain and must match.
"""
from __future__ import annotations

import hashlib
import heapq
from pathlib import Path

import numpy as np

from .engine import geomorph

#: sha256 of the ``geomorph.py`` these copies follow (the site engine's, byte-synced from EASI's
#: ``apps/easi/easi/geomorph.py``; 2026-10-03). Any other file: the reference runs instead.
PINNED = "ef61a98a5e132530722317aebcd91a8331bb9307e6f5e63e20069739ac7d0c94"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


GEOMORPH_SHA256 = _sha(Path(geomorph.__file__))
ACTIVE = GEOMORPH_SHA256 == PINNED
_INF = float("inf")


def balanced_profile(stations, elevs, *, min_half: float = 30.0):
    n = len(stations)
    if n != len(elevs) or n < 7:
        return None
    e = np.asarray(elevs, dtype=np.float64)
    s = np.asarray(stations, dtype=np.float64)
    ti = int(np.argmin(e))                         # first lowest, as min(range(n), key=...)
    t0 = s[ti]
    rel = s - t0
    lim = min(-float(rel.min()), float(rel.max()))
    if lim < min_half:
        return None

    def crest_dist(d, ee):
        if not len(d):
            return 0.0
        top = float(ee.max())
        return float(d[ee >= top - 0.3].min())

    left = (rel >= -lim) & (rel < 0)
    right = (rel > 0) & (rel <= lim)
    d_left = crest_dist(-rel[left], e[left])
    d_right = crest_dist(rel[right], e[right])
    reach = min(max(d_left, d_right, min_half), lim)
    lo_b = -reach - 1e-6
    hi_b = reach + 1e-6
    keep = (rel >= lo_b) & (rel <= hi_b)
    if int(keep.sum()) < 7:
        return None
    return rel[keep].tolist(), e[keep].tolist()


def _tri(ax, az, bx, bz, cx, cz) -> float:
    return abs(ax * (bz - cz) + bx * (cz - az) + cx * (az - bz)) / 2.0


def simplify_profile(stations, elevs, *, tol_x: float = 0.15, tol_z: float = 0.03, col_tol: float = 0.03,
                     min_slope: float = 0.001, max_points: int = 250):
    n = len(stations)
    if n != len(elevs) or n <= 3:
        return list(stations), list(elevs)
    thal = min(range(n), key=lambda i: elevs[i])
    xs = [float(v) for v in stations]
    zs = [float(v) for v in elevs]
    # 1) near filter
    kx, kz, kg = [xs[0]], [zs[0]], [True]
    ax, az = xs[0], zs[0]
    last = n - 1
    for i in range(1, n):
        bx, bz = xs[i], zs[i]
        g = i == last or i == thal
        if (not g) and abs(bx - ax) <= tol_x and abs(bz - az) <= tol_z:
            continue
        kx.append(bx)
        kz.append(bz)
        kg.append(g)
        ax, az = bx, bz
    # 2) collinear filter, passes until stable
    changed = True
    while changed:
        changed = False
        i = 1
        while i < len(kx) - 1:
            if not kg[i]:
                ax, az = kx[i - 1], kz[i - 1]
                bx, bz = kx[i], kz[i]
                cx, cz = kx[i + 1], kz[i + 1]
                dx, dz = cx - ax, cz - az
                den = (dx * dx + dz * dz) ** 0.5
                if den == 0:
                    pd = ((bx - ax) ** 2 + (bz - az) ** 2) ** 0.5
                else:
                    pd = abs(dz * bx - dx * bz + cx * az - cz * ax) / den
                if pd <= col_tol:
                    d1 = bx - ax
                    ma = _INF if d1 == 0 else (bz - az) / d1
                    d2 = cx - ax
                    mc = _INF if d2 == 0 else (cz - az) / d2
                    if (ma == _INF and mc == _INF) or abs(ma - mc) <= min_slope:
                        del kx[i]
                        del kz[i]
                        del kg[i]
                        changed = True
                        continue
            i += 1
    m = len(kx)
    if m <= max_points:
        return kx, kz
    # 3) Visvalingam trim with a heap
    prev = list(range(-1, m - 1))
    nxt = list(range(1, m + 1))
    alive = [True] * m
    version = [0] * m
    heap = [(_tri(kx[i - 1], kz[i - 1], kx[i], kz[i], kx[i + 1], kz[i + 1]), i, 0)
            for i in range(1, m - 1) if not kg[i]]
    heapq.heapify(heap)
    left = m
    while left > max_points and heap:
        area, i, ver = heapq.heappop(heap)
        if not alive[i] or ver != version[i]:
            continue
        alive[i] = False
        left -= 1
        a, b = prev[i], nxt[i]
        nxt[a], prev[b] = b, a
        for j in (a, b):
            if 0 < j < m - 1 and not kg[j]:
                version[j] += 1
                p, q = prev[j], nxt[j]
                heapq.heappush(heap, (_tri(kx[p], kz[p], kx[j], kz[j], kx[q], kz[q]), j, version[j]))
    keep = [i for i in range(m) if alive[i]]
    return [kx[i] for i in keep], [kz[i] for i in keep]


def flow_area(stations, elevs, stage, *, thalweg_index=None):
    n = len(stations)
    if n < 2:
        return 0.0, False
    ti = thalweg_index if thalweg_index is not None else min(range(n), key=lambda i: elevs[i])
    if elevs[ti] >= stage:
        return 0.0, False
    i = ti                                          # left edge
    l_edge = True
    while 0 <= i - 1:
        j = i - 1
        if elevs[j] >= stage:
            t = (stage - elevs[i]) / (elevs[j] - elevs[i])
            left = stations[i] + t * (stations[j] - stations[i])
            l_edge = False
            break
        i = j
    li = i
    if l_edge:
        left = stations[i]
    i = ti                                          # right edge
    r_edge = True
    while i + 1 < n:
        j = i + 1
        if elevs[j] >= stage:
            t = (stage - elevs[i]) / (elevs[j] - elevs[i])
            right = stations[i] + t * (stations[j] - stations[i])
            r_edge = False
            break
        i = j
    ri = i
    if r_edge:
        right = stations[i]
    # the trapezoids of [left] + stations[li..ri] + [right], depths [0] + ... + [0], in order
    area = 0.0
    px, pd = left, 0.0
    for k in range(li, ri + 1):
        x = stations[k]
        d = max(stage - elevs[k], 0.0)
        dx = x - px
        if dx > 0:
            area += 0.5 * (pd + d) * dx
        px, pd = x, d
    dx = right - px
    if dx > 0:
        area += 0.5 * (pd + 0.0) * dx
    return max(area, 0.0), (l_edge or r_edge)
