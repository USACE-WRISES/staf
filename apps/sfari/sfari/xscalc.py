"""Open-channel cross-section hydraulics — a Python port of xs-calc's core.

Normal-depth Manning's-equation solver for an irregular station/elevation
cross-section, with optional left/right overbank zones (LOB / channel / ROB) each
carrying its own Manning's n (conveyance summed across zones, as in HEC-RAS /
xs-calc). Given a water-surface stage it integrates the wetted area (A), perimeter
(P), top width (T) and returns discharge Q, mean velocity V, hydraulic radius R,
bed shear stress tau = gamma*R*S, unit stream power tau*V, and the Froude number.
``solve_stage`` inverts for the stage that conveys a target Q; ``rating`` sweeps a
stage/discharge curve. Pure Python (no I/O) so it is unit-tested against an
analytical trapezoid. Not a cross-section *designer* — geometry comes in from a
3DEP transect or user entry.
"""
from __future__ import annotations

from math import hypot, sqrt
from typing import Optional

# unit-system constants: Manning k, specific weight gamma, gravity g
UNITS = {
    "US": {"k": 1.49, "gamma": 62.4, "g": 32.174},   # ft, lb/ft^3, ft/s^2
    "SI": {"k": 1.0, "gamma": 9810.0, "g": 9.81},     # m, N/m^3, m/s^2
}


FT_PER_M = 3.28083989501312


def synthetic_section(width_m, depth_m, *, side_slope=2.0, floodplain_factor=1.0):
    """Trapezoidal channel + floodplain benches from bankfull width/depth (metres).

    Returns ``(points, lb, rb, bankfull_stage)`` in US feet — a screening default
    when no measured/3DEP transect is supplied. ``side_slope`` is bank H:V.
    """
    w = max(float(width_m), 0.3) * FT_PER_M
    d = max(float(depth_m), 0.1) * FT_PER_M
    b = max(w - 2.0 * side_slope * d, 0.3 * w)          # channel bottom width
    fp = w * floodplain_factor                           # floodplain half-extent beyond bank
    pts = [
        (-(w / 2 + fp), d * 1.15),
        (-w / 2, d),
        (-b / 2, 0.0),
        (b / 2, 0.0),
        (w / 2, d),
        (w / 2 + fp, d * 1.15),
    ]
    return pts, -w / 2, w / 2, d


def _interp_z(xa, za, xb, zb, x):
    if xb == xa:
        return za
    return za + (zb - za) * (x - xa) / (xb - xa)


def _wetted(points, stage, x_left=None, x_right=None):
    """Integrate wetted (area, perimeter, top_width, max_depth) below ``stage``.

    ``points`` are (station, elevation) sorted by station. The section is clipped
    to [x_left, x_right] when given (overbank zone bounds); vertical clip walls do
    NOT add to the wetted perimeter (they are interfaces between zones, not bed).
    """
    xl = -float("inf") if x_left is None else x_left
    xr = float("inf") if x_right is None else x_right
    area = perim = top = 0.0
    dmax = 0.0
    for (xa, za), (xb, zb) in zip(points, points[1:]):
        if xb <= xa:
            continue
        # clip the segment's x-range to the zone, interpolating elevations
        cxa, cxb = max(xa, xl), min(xb, xr)
        if cxb <= cxa:
            continue
        cza = _interp_z(xa, za, xb, zb, cxa)
        czb = _interp_z(xa, za, xb, zb, cxb)
        da, db = stage - cza, stage - czb
        if da <= 0 and db <= 0:
            continue                                   # dry
        if da > 0 and db > 0:                          # fully submerged
            area += (da + db) / 2.0 * (cxb - cxa)
            perim += hypot(cxb - cxa, czb - cza)
            top += (cxb - cxa)
            dmax = max(dmax, da, db)
        else:                                          # partially submerged
            xc = cxa + da / (da - db) * (cxb - cxa)    # station where bed meets stage
            if da > 0:                                 # left part wet
                area += da / 2.0 * (xc - cxa)
                perim += hypot(xc - cxa, da)
                top += (xc - cxa)
                dmax = max(dmax, da)
            else:                                      # right part wet
                area += db / 2.0 * (cxb - xc)
                perim += hypot(cxb - xc, db)
                top += (cxb - xc)
                dmax = max(dmax, db)
    return area, perim, top, dmax


def _zone(points, stage, n, x_left, x_right, k):
    a, p, t, dmax = _wetted(points, stage, x_left, x_right)
    if a <= 0 or p <= 0 or not n:
        return {"A": a, "P": p, "T": t, "dmax": dmax, "R": 0.0, "K": 0.0}
    r = a / p
    kcap = (k / n) * a * (r ** (2.0 / 3.0))            # conveyance
    return {"A": a, "P": p, "T": t, "dmax": dmax, "R": r, "K": kcap}


def compute(points, stage, slope, n_chan, *, n_lob=None, n_rob=None,
            lb=None, rb=None, units="US"):
    """Full hydraulics at ``stage``. Returns totals + per-zone breakdown.

    ``lb``/``rb`` are the channel bank stations; without them the whole section is
    one channel. ``n_lob``/``n_rob`` default to ``n_chan``.
    """
    u = UNITS.get(units, UNITS["US"])
    k, gamma, g = u["k"], u["gamma"], u["g"]
    slope = max(float(slope), 0.0)
    pts = sorted((float(x), float(z)) for x, z in points)
    if len(pts) < 2:
        return None

    if lb is not None and rb is not None and rb > lb:
        zones = {
            "LOB": _zone(pts, stage, n_lob or n_chan, None, lb, k),
            "channel": _zone(pts, stage, n_chan, lb, rb, k),
            "ROB": _zone(pts, stage, n_rob or n_chan, rb, None, k),
        }
    else:
        zones = {"channel": _zone(pts, stage, n_chan, None, None, k)}

    A = sum(z["A"] for z in zones.values())
    P = sum(z["P"] for z in zones.values())
    T = sum(z["T"] for z in zones.values())
    K = sum(z["K"] for z in zones.values())
    dmax = max((z["dmax"] for z in zones.values()), default=0.0)
    if A <= 0 or P <= 0:
        return {"stage": stage, "Q": 0.0, "A": 0.0, "P": 0.0, "T": 0.0, "R": 0.0,
                "V": 0.0, "tau": 0.0, "power": 0.0, "froude": 0.0,
                "depth_max": 0.0, "depth_avg": 0.0, "zones": zones, "units": units}
    sq = sqrt(slope)
    Q = K * sq
    R = A / P
    V = Q / A if A else 0.0
    d_hyd = A / T if T else 0.0
    tau = gamma * R * slope
    power = tau * V
    froude = V / sqrt(g * d_hyd) if d_hyd > 0 else 0.0
    for z in zones.values():
        z["Q"] = z["K"] * sq
        z["V"] = (z["Q"] / z["A"]) if z["A"] else 0.0
    return {"stage": stage, "Q": Q, "A": A, "P": P, "T": T, "R": R, "V": V,
            "tau": tau, "power": power, "froude": froude,
            "depth_max": dmax, "depth_avg": d_hyd, "zones": zones, "units": units}


def _bed_min(points):
    return min(z for _x, z in points)


def _bed_max(points):
    return max(z for _x, z in points)


def solve_stage(points, target_q, slope, n_chan, *, n_lob=None, n_rob=None,
                lb=None, rb=None, units="US", tol=1e-3, iters=60):
    """Stage (elevation) that conveys ``target_q`` by bisection, or None."""
    pts = sorted((float(x), float(z)) for x, z in points)
    if target_q <= 0 or len(pts) < 2 or slope <= 0:
        return None
    lo, hi = _bed_min(pts), _bed_max(pts)
    # ensure the top of section conveys at least target_q; else extrapolate a wall
    top = compute(pts, hi, slope, n_chan, n_lob=n_lob, n_rob=n_rob, lb=lb, rb=rb, units=units)
    if not top or top["Q"] < target_q:
        span = hi - lo or 1.0
        hi = hi + span * 3.0                          # allow out-of-bank extrapolation
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        res = compute(pts, mid, slope, n_chan, n_lob=n_lob, n_rob=n_rob, lb=lb, rb=rb, units=units)
        q = res["Q"] if res else 0.0
        if abs(q - target_q) <= tol * max(target_q, 1.0):
            return mid
        if q < target_q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def rating(points, slope, n_chan, *, n_lob=None, n_rob=None, lb=None, rb=None,
           units="US", steps=20):
    """Stage/discharge rating from thalweg to top of section (list of compute dicts)."""
    pts = sorted((float(x), float(z)) for x, z in points)
    if len(pts) < 2:
        return []
    lo, hi = _bed_min(pts), _bed_max(pts)
    out = []
    for i in range(1, steps + 1):
        stage = lo + (hi - lo) * i / steps
        res = compute(pts, stage, slope, n_chan, n_lob=n_lob, n_rob=n_rob, lb=lb, rb=rb, units=units)
        if res:
            out.append(res)
    return out
