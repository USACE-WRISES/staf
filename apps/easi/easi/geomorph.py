"""Cross-section geomorphology math (pure Python, no I/O — fully unit-testable).

The top-width-at-stage algorithm is ported from the xs-calc library
(``app.core.js`` ``integrateOnPolyline``): it integrates a station-elevation
profile against a water-surface elevation and returns the total top width,
merging wetted intervals so islands / non-monotonic beds are handled correctly.

From DEM-sampled perpendicular transects we derive a Rosgen-style entrenchment
ratio and a bank-height ratio. Bankfull dimensions are estimated from a national
hydraulic-geometry curve (drainage-area regression) — a documented interim that
per-region curves and user overrides (e.g. xs-calc) refine.
"""
from __future__ import annotations

from statistics import median
from typing import Optional

# National bankfull regional curve (generalized; DA in km^2 -> metres).
# Width/depth = a * DA^b. Coefficients are a national-average interim
# (Bieger et al. 2015-style); per-physiographic-region curves refine later.
BF_WIDTH_A, BF_WIDTH_B = 2.70, 0.352
BF_DEPTH_A, BF_DEPTH_B = 0.30, 0.315


def bankfull_geometry(da_sqkm: float) -> tuple[float, float]:
    """Estimate (bankfull_width_m, bankfull_depth_m) from drainage area."""
    da = max(float(da_sqkm or 0.0), 0.01)
    width = BF_WIDTH_A * da ** BF_WIDTH_B
    depth = BF_DEPTH_A * da ** BF_DEPTH_B
    return width, depth


def top_width(stations: list[float], elevs: list[float], stage: float):
    """Top width of a station-elevation profile at a water-surface ``stage``.

    Ported from xs-calc ``integrateOnPolyline``. Returns (T, merged_intervals)
    where merged_intervals is a list of (x_left, x_right) wetted spans.
    Assumes ``stations`` strictly increasing.
    """
    intervals: list[list[float]] = []
    for i in range(len(stations) - 1):
        x1, z1 = stations[i], elevs[i]
        x2, z2 = stations[i + 1], elevs[i + 1]
        dx = x2 - x1
        if dx <= 0:
            continue
        rel1, rel2 = z1 - stage, z2 - stage
        if rel1 <= 0 and rel2 <= 0:
            intervals.append([x1, x2])
        elif (rel1 <= 0) != (rel2 <= 0):
            t = (stage - z1) / (z2 - z1)
            xi = x1 + t * dx
            intervals.append([x1, xi] if rel1 <= 0 else [xi, x2])
    intervals.sort()
    merged: list[list[float]] = []
    for a, b in intervals:
        if merged and a <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    T = sum(b - a for a, b in merged)
    return T, merged


def flow_width(stations: list[float], elevs: list[float], stage: float,
               *, thalweg_index: Optional[int] = None) -> tuple[float, bool]:
    """Contiguous top width spanning the thalweg at a water-surface ``stage``.

    Rosgen edge-of-water to edge-of-water *across the channel*: from the thalweg,
    walk outward each way to the first point that rises to/above ``stage``
    (interpolating the exact crossing) and return ``right_edge - left_edge``.
    Unlike :func:`top_width` (which sums every wetted segment across the whole
    transect), this is the single water body containing the channel, so a
    disconnected low pocket elsewhere is *not* counted and a dry bar *between* the
    banks *is* (it is a width, not a wetted area). Returns ``(width_m,
    edge_limited)``; ``edge_limited`` is True when a side never reaches the stage
    within the sampled profile (DEM buffer too small). Width is 0 if ``stage`` is
    at or below the thalweg.
    """
    n = len(stations)
    if n < 2:
        return 0.0, False
    ti = thalweg_index if thalweg_index is not None else min(range(n), key=lambda i: elevs[i])
    if elevs[ti] >= stage:
        return 0.0, False

    def edge(direction: int) -> tuple[float, bool]:
        i = ti
        while 0 <= i + direction < n:
            j = i + direction
            if elevs[j] >= stage:  # crossing between i (below) and j (>= stage)
                t = (stage - elevs[i]) / (elevs[j] - elevs[i])
                return stations[i] + t * (stations[j] - stations[i]), False
            i = j
        return stations[i], True  # reached the profile end without rising to stage

    left, l_edge = edge(-1)
    right, r_edge = edge(+1)
    return max(right - left, 0.0), (l_edge or r_edge)


def flow_area(stations: list[float], elevs: list[float], stage: float,
              *, thalweg_index: Optional[int] = None) -> tuple[float, bool]:
    """Contiguous channel wetted cross-sectional area (m^2) below ``stage``.

    The companion to :func:`flow_width`: bound the single water body spanning the
    thalweg (walk outward each way to the first bank that rises to/above ``stage``,
    interpolating the crossing) and integrate depth ``stage - z`` across that span
    with the trapezoidal rule. Every node between the banks is below the stage (the
    walk stops at the first that isn't), so the integrand is non-negative. Returns
    ``(area_m2, edge_limited)``; area is 0 when ``stage`` is at or below the thalweg,
    and ``edge_limited`` is True when a bank is not reached within the sampled
    profile (DEM buffer too small).
    """
    n = len(stations)
    if n < 2:
        return 0.0, False
    ti = thalweg_index if thalweg_index is not None else min(range(n), key=lambda i: elevs[i])
    if elevs[ti] >= stage:
        return 0.0, False

    def edge(direction: int) -> tuple[int, float, bool]:
        # (last node inside the channel, interpolated bank station, edge_limited)
        i = ti
        while 0 <= i + direction < n:
            j = i + direction
            if elevs[j] >= stage:  # crossing between i (below) and j (>= stage)
                t = (stage - elevs[i]) / (elevs[j] - elevs[i])
                return i, stations[i] + t * (stations[j] - stations[i]), False
            i = j
        return i, stations[i], True  # reached the profile end without rising to stage

    li, left, l_edge = edge(-1)
    ri, right, r_edge = edge(+1)
    # (x, depth) polyline from the left bank (depth 0) across the inside nodes to
    # the right bank (depth 0); a truncated side coincides with its edge node, so
    # its zero-width segment adds nothing and the node's real depth is kept.
    xs_pts = [left] + [stations[k] for k in range(li, ri + 1)] + [right]
    depths = [0.0] + [max(stage - elevs[k], 0.0) for k in range(li, ri + 1)] + [0.0]
    area = 0.0
    for k in range(len(xs_pts) - 1):
        dx = xs_pts[k + 1] - xs_pts[k]
        if dx > 0:
            area += 0.5 * (depths[k] + depths[k + 1]) * dx
    return max(area, 0.0), (l_edge or r_edge)


def stage_for_area(stations: list[float], elevs: list[float], target_area: float,
                   *, thalweg_index: Optional[int] = None,
                   iters: int = 48) -> tuple[float, bool]:
    """Water-surface stage whose contiguous channel area equals ``target_area`` (m^2).

    Bisection — :func:`flow_area` increases monotonically with stage. Returns
    ``(stage, edge_limited)``. If the sampled profile cannot hold the target area even
    at its highest point, returns that top stage with ``edge_limited=True``; a
    non-positive target returns the thalweg elevation.
    """
    n = len(elevs)
    if n < 2:
        return (elevs[0] if elevs else 0.0), False
    ti = thalweg_index if thalweg_index is not None else min(range(n), key=lambda i: elevs[i])
    lo = elevs[ti]
    if target_area is None or target_area <= 0:
        return lo, False
    hi = max(elevs)
    if hi <= lo:
        return lo, False
    a_hi, _ = flow_area(stations, elevs, hi, thalweg_index=ti)
    if a_hi < target_area:
        return hi, True  # the sampled window cannot hold the regional area
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        a, _ = flow_area(stations, elevs, mid, thalweg_index=ti)
        if a < target_area:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi), False


def balanced_profile(stations: list[float], elevs: list[float], *,
                     min_half: float = 30.0
                     ) -> Optional[tuple[list[float], list[float]]]:
    """Recentre a transect on its thalweg and trim to a *height-balanced* band.

    A DEM transect is sampled about the (coarse) NHD flowline vertex, but the
    channel low point is usually offset from it — so a plot that re-datums to the
    thalweg ends up lopsided. Shift stations so the thalweg (lowest elevation) sits
    at 0, then extend **both** sides out to the farther of the two banks' crests:
    ``reach = max(dist-to-left-peak, dist-to-right-peak)``, clamped to ``[min_half,
    lim]`` where ``lim = min(-min(rel), max(rel))`` is the symmetric data limit. This
    shows the low/short side out to where the terrain has actually risen (capturing
    comparable height), while trimming the flat terrace on incised reaches where both
    crests sit near the channel.

    Returns ``(rel_stations, elevs)`` — order-preserving, so strictly increasing in,
    strictly increasing out — or ``None`` when there is too little usable data
    (< 7 points, mismatched lengths, or a symmetric data limit below ``min_half``).
    """
    if len(stations) != len(elevs) or len(stations) < 7:
        return None
    ti = min(range(len(elevs)), key=lambda i: elevs[i])
    t0 = stations[ti]
    rel = [s - t0 for s in stations]
    lim = min(-min(rel), max(rel))
    if lim < min_half:
        return None

    def crest_dist(side):  # (|station|, elev) pairs -> nearest distance ~at the bank crest
        if not side:
            return 0.0
        top = max(e for _, e in side)
        return min(d for d, e in side if e >= top - 0.3)  # within 0.3 m of the crest

    # extend both sides to the farther bank crest (so the low/short side is shown out to
    # where terrain has risen), but no farther than the symmetric data limit
    d_left = crest_dist([(-r, e) for r, e in zip(rel, elevs) if -lim <= r < 0])
    d_right = crest_dist([(r, e) for r, e in zip(rel, elevs) if 0 < r <= lim])
    reach = min(max(d_left, d_right, min_half), lim)
    out_s, out_e = [], []
    for r, e in zip(rel, elevs):
        if -reach - 1e-6 <= r <= reach + 1e-6:
            out_s.append(r)
            out_e.append(e)
    if len(out_s) < 7:
        return None
    return out_s, out_e


def simplify_profile(stations: list[float], elevs: list[float], *,
                     tol_x: float = 0.15, tol_z: float = 0.03,
                     col_tol: float = 0.03, min_slope: float = 0.001,
                     max_points: int = 250) -> tuple[list[float], list[float]]:
    """Thin a dense station/elevation profile (metres), ported from xs-calc's filters.

    Fine (1 m) DEM sampling produces very dense transects; this drops redundant points
    while preserving channel shape. Mirrors ``filter_logic.js`` in the xs-calc repo:
      1. **near** — drop a point within (``tol_x``, ``tol_z``) of the previous kept point;
      2. **collinear** — drop an interior point whose perpendicular distance to the line
         through its neighbours is <= ``col_tol`` and whose slope barely changes
         (<= ``min_slope``);
      3. **Visvalingam trim** — if still over ``max_points``, repeatedly remove the point
         forming the smallest triangle with its neighbours until the cap is met.
    The first point, last point, and the thalweg (min-elevation point) are never removed.
    """
    n = len(stations)
    if n != len(elevs) or n <= 3:
        return list(stations), list(elevs)
    thal = min(range(n), key=lambda i: elevs[i])
    pts = [{"x": float(s), "z": float(e), "g": (i in (0, n - 1, thal))}
           for i, (s, e) in enumerate(zip(stations, elevs))]

    def slope(a, b):
        dx = b["x"] - a["x"]
        return float("inf") if dx == 0 else (b["z"] - a["z"]) / dx

    def perp(p, a, b):  # perpendicular distance from p to the line a-b
        dx, dz = b["x"] - a["x"], b["z"] - a["z"]
        den = (dx * dx + dz * dz) ** 0.5
        if den == 0:
            return ((p["x"] - a["x"]) ** 2 + (p["z"] - a["z"]) ** 2) ** 0.5
        return abs(dz * p["x"] - dx * p["z"] + b["x"] * a["z"] - b["z"] * a["x"]) / den

    def tri(a, b, c):  # triangle area (shoelace)
        return abs(a["x"] * (b["z"] - c["z"]) + b["x"] * (c["z"] - a["z"])
                   + c["x"] * (a["z"] - b["z"])) / 2.0

    kept = [pts[0]]                                   # 1) near filter
    for b in pts[1:]:
        a = kept[-1]
        if (not b["g"]) and abs(b["x"] - a["x"]) <= tol_x and abs(b["z"] - a["z"]) <= tol_z:
            continue
        kept.append(b)
    pts = kept

    changed = True                                   # 2) collinear filter (until stable)
    while changed:
        changed = False
        i = 1
        while i < len(pts) - 1:
            a, b, c = pts[i - 1], pts[i], pts[i + 1]
            if not b["g"] and perp(b, a, c) <= col_tol:
                ma, mc = slope(a, b), slope(a, c)
                if (ma == float("inf") and mc == float("inf")) or abs(ma - mc) <= min_slope:
                    del pts[i]
                    changed = True
                    continue
            i += 1

    while len(pts) > max_points:                     # 3) Visvalingam trim to the cap
        best_i, best_area = None, float("inf")
        for i in range(1, len(pts) - 1):
            if pts[i]["g"]:
                continue
            area = tri(pts[i - 1], pts[i], pts[i + 1])
            if area < best_area:
                best_area, best_i = area, i
        if best_i is None:
            break
        del pts[best_i]

    return [p["x"] for p in pts], [p["z"] for p in pts]


def transect_entrenchment(stations: list[float], elevs: list[float],
                          d_bf: float, w_bf: float) -> Optional[dict]:
    """Rosgen entrenchment for one transect.

    flood-prone stage = thalweg + 2*bankfull_depth; ER = flood-prone width /
    bankfull width (regional-curve width as the denominator avoids the
    floodplain-inclusion error of measuring bankfull width on a flat DEM).
    Returns None if the transect is unusable.
    """
    if len(stations) < 5 or w_bf <= 0 or d_bf <= 0:
        return None
    thalweg = min(elevs)
    fp_stage = thalweg + 2.0 * d_bf
    t_fp, merged = top_width(stations, elevs, fp_stage)
    if t_fp <= 0:
        return None
    er = t_fp / w_bf
    # edge-limited if flood-prone water reaches a transect end (buffer too small)
    span = (stations[-1] - stations[0]) or 1.0
    edge = any(a <= stations[0] + 0.05 * span or b >= stations[-1] - 0.05 * span
               for a, b in merged)
    return {"er": round(er, 2), "floodprone_width_m": round(t_fp, 1),
            "thalweg": thalweg, "edge_limited": edge}


def top_of_bank_elev(stations: list[float], elevs: list[float]) -> Optional[float]:
    """Lowest top-of-bank elevation, scanning outward from the thalweg.

    Scans each direction from the thalweg until the profile descends notably past
    a local crest (the floodplain/terrace beyond the bank), and returns the lower
    of the two bank crests. Returns None if the profile is unusable.
    """
    if len(stations) < 5:
        return None
    ti = elevs.index(min(elevs))

    def crest(direction: int) -> float:
        best = elevs[ti]
        i = ti
        while 0 <= i + direction < len(elevs):
            i += direction
            if elevs[i] > best:
                best = elevs[i]
            elif best - elevs[i] > 0.5:  # descended past the bank crest
                break
        return best

    return min(crest(-1), crest(+1))


def bank_height_ratio(stations: list[float], elevs: list[float],
                      d_bf: float) -> Optional[float]:
    """Bank-height ratio = (lowest top-of-bank - thalweg) / bankfull depth.

    BHR ~1 = floodplain-connected; >1.5 = incised.
    """
    if len(stations) < 5 or d_bf <= 0:
        return None
    top_of_bank = top_of_bank_elev(stations, elevs)
    if top_of_bank is None:
        return None
    return round((top_of_bank - min(elevs)) / d_bf, 2)


def _representative(per: list[dict], ers: list[float]) -> Optional[dict]:
    """Pick the transect whose ER is closest to the reach median ER.

    Falls back to the transect with the greatest relief (most channel-like) so a
    profile is retained even when no transect yields a usable entrenchment ratio.
    """
    usable = [p for p in per if p["er"] is not None]
    if usable and ers:
        med = median(ers)
        return min(usable, key=lambda p: abs(p["er"] - med))
    candidates = [p for p in per if len(p["stations"]) >= 5]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (max(p["elevs"]) - min(p["elevs"])))


def derive_from_stages(stations: list[float], elevs: list[float], *,
                       thalweg: Optional[float] = None,
                       bankfull_stage: float, floodplain_stage: float) -> dict:
    """Recompute Rosgen ER/BHR + a *measured* bankfull width from chosen stages.

    Pure (no I/O) — drives the editable cross-section. ``bankfull_stage`` and
    ``floodplain_stage`` are absolute water-surface elevations on the profile (in
    its metres datum); ``thalweg`` defaults to the profile minimum.

    - max bankfull depth ``d = bankfull_stage - thalweg``;
    - bankfull width = contiguous top width at ``bankfull_stage`` (measured across
      the channel on the profile, so ER is self-consistent);
    - flood-prone stage = ``thalweg + 2*d`` (Rosgen); flood-prone width measured
      there (edge-of-water to edge-of-water across the channel via
      :func:`flow_width`); **ER = flood-prone width / bankfull width**;
    - **BHR = (floodplain_stage - thalweg) / d** — ``floodplain_stage`` here is the
      *low-bank* stage (the lower top-of-bank); 1 = floodplain-connected, >1 incised.
    """
    if thalweg is None:
        thalweg = min(elevs)
    ti = min(range(len(elevs)), key=lambda i: elevs[i]) if elevs else 0
    d = float(bankfull_stage) - float(thalweg)
    out: dict = {"thalweg": thalweg, "bankfull_stage": bankfull_stage,
                 "floodplain_stage": floodplain_stage,
                 "bankfull_depth_max_m": round(d, 3) if d > 0 else None,
                 "entrenchment_ratio": None, "bank_height_ratio": None,
                 "bankfull_width_m": None, "flood_prone_width_m": None,
                 "fp_stage_m": None, "edge_limited": False}
    if d <= 0:
        return out
    w_bf, _ = flow_width(stations, elevs, bankfull_stage, thalweg_index=ti)
    fp_stage = thalweg + 2.0 * d
    w_fp, fp_edge = flow_width(stations, elevs, fp_stage, thalweg_index=ti)
    out["bankfull_width_m"] = round(w_bf, 1) if w_bf > 0 else None
    out["flood_prone_width_m"] = round(w_fp, 1) if w_fp > 0 else None
    out["fp_stage_m"] = round(fp_stage, 3)
    out["edge_limited"] = bool(fp_edge)
    if w_bf > 0:
        out["entrenchment_ratio"] = round(w_fp / w_bf, 2)
    out["bank_height_ratio"] = round((float(floodplain_stage) - thalweg) / d, 2)
    return out


def summarize_profile(stations: list[float], elevs: list[float], da_sqkm: float, *,
                      bankfull: Optional[tuple[float, float]] = None,
                      bankfull_area_m2: Optional[float] = None,
                      division: Optional[str] = None) -> dict:
    """Rosgen summary for one station-elevation profile at its default stages.

    Bankfull stage is the depth at which the channel cross-sectional area equals the
    Bieger regional bankfull area (``bankfull_area_m2``, solved on this profile via
    :func:`stage_for_area`); without that target it falls back to thalweg + the Bieger
    regional depth (``bankfull`` (width, depth) or the national ``bankfull_geometry``).
    Low-bank stage = the lower top-of-bank, clamped to bankfull. ER/BHR/widths are
    measured on this profile (the editable cross-section + metrics share these).
    Returns the profile plus the keys the editable geometry block consumes.
    """
    w_bf, d_bf = bankfull if bankfull is not None else bankfull_geometry(da_sqkm)
    thalweg = min(elevs)
    ti = min(range(len(elevs)), key=lambda i: elevs[i]) if elevs else 0
    area_edge = False
    if bankfull_area_m2 is not None and bankfull_area_m2 > 0:
        bankfull_stage, area_edge = stage_for_area(stations, elevs, bankfull_area_m2,
                                                   thalweg_index=ti)
        d_area = bankfull_stage - thalweg
        if d_area > 0.01:            # area-derived bankfull depth
            d_bf = d_area
        else:                        # degenerate (flat/edge) -> regional-depth fallback
            bankfull_stage = thalweg + d_bf
    else:
        bankfull_stage = thalweg + d_bf
    tob = top_of_bank_elev(stations, elevs)
    low_bank_stage = max(tob if tob is not None else bankfull_stage, bankfull_stage)
    d = derive_from_stages(stations, elevs, thalweg=thalweg,
                           bankfull_stage=bankfull_stage, floodplain_stage=low_bank_stage)
    out: dict = {"profile": {"stations": list(stations), "elevs": list(elevs)},
                 "thalweg": thalweg, "fp_stage_m": thalweg + 2.0 * d_bf,
                 "bankfull_width_m": d.get("bankfull_width_m") or round(w_bf, 1),
                 "bankfull_depth_m": round(d_bf, 2),
                 "flood_prone_width_m": d.get("flood_prone_width_m"),
                 "entrenchment_ratio": d.get("entrenchment_ratio"),
                 "bank_height_ratio": d.get("bank_height_ratio"),
                 "edge_limited": bool(d.get("edge_limited"))}
    if bankfull_area_m2 is not None:
        out["bankfull_area_m2"] = round(float(bankfull_area_m2), 2)
        out["bankfull_area_edge_limited"] = bool(area_edge)
    if tob is not None:
        out["top_of_bank_m"] = tob
    if division:
        out["bankfull_division"] = division
    return out


def reach_summary(transects: list[tuple[list[float], list[float]]],
                  da_sqkm: float, *,
                  bankfull: Optional[tuple[float, float]] = None,
                  division: Optional[str] = None) -> dict:
    """Per-reach summary on the *representative* transect (median entrenchment ratio).

    ``bankfull`` is an optional precomputed ``(width_m, depth_m)`` — the caller injects
    the regional (Bieger) estimate; when None the national curve is used. ER/BHR/widths
    and the retained profile come from :func:`summarize_profile` on the representative
    transect, so the editable cross-section and the metrics share the same values.
    """
    w_bf, d_bf = bankfull if bankfull is not None else bankfull_geometry(da_sqkm)
    per = [{"stations": s, "elevs": e,
            "er": (te["er"] if (te := transect_entrenchment(s, e, d_bf, w_bf)) else None)}
           for s, e in transects]
    ers = [p["er"] for p in per if p["er"] is not None]
    out: dict = {"bankfull_width_m": round(w_bf, 1), "bankfull_depth_m": round(d_bf, 2),
                 "n_transects": len(transects)}
    if division:
        out["bankfull_division"] = division
    rep = _representative(per, ers)
    if rep is not None:
        out.update(summarize_profile(rep["stations"], rep["elevs"], da_sqkm,
                                     bankfull=bankfull, division=division))
    return out
