r"""Port of R/18_geomorph.R — cross-section geomorphology math (pure, no I/O)
plus the Bieger et al. (2015) regional bankfull hydraulic-geometry curves.

``top_width()`` / ``flow_width()`` integrate a station-elevation profile against
a water-surface stage. ``derive_from_stages()`` turns user-chosen bankfull &
low-bank stages into a Rosgen entrenchment ratio (ER), bank-height ratio (BHR),
and bankfull / flood-prone widths::

    flood-prone stage = thalweg + 2 * bankfull_depth
    ER  = flood-prone width / bankfull width
    BHR = (low_bank_stage - thalweg) / bankfull_depth

All station/elevation inputs are 1-D sequences (lists or numpy arrays) with
stations strictly increasing.

R <-> DEEP divergences
----------------------
R/18_geomorph.R was itself ported from the sister app DEEP (deep/geomorph.py and
deep/bieger.py at D:\Code\Work\deep). Where the two agree this module adapts
DEEP's implementations; the **R file is authoritative** where they differ. Full
diff of DEEP vs the R port:

1. Bieger lookup: R's ``bieger_geometry(da_sqkm, division)`` takes the division
   abbreviation directly; DEEP's ``bieger.bankfull_geometry(da_sqkm, lat, lon)``
   resolves the division itself from bundled GeoJSON polygons (point-in-polygon
   with a ~25 km nearest-division fallback). Coefficient table, rounding
   (width 2 dp / depth 3 dp / area 2 dp) and output keys are identical.
2. DA sanitising: R coerces any non-numeric / NA / NaN / +-Inf drainage area to
   0.01 km^2 (``!is.finite``); DEEP only guards None/0 (``da_sqkm or 0.0``), so
   NaN and +Inf propagate through DEEP's curves. Ported: R behaviour.
3. ``deep.geomorph.bankfull_geometry`` (DEEP's national-curve fallback) returns
   an UNROUNDED ``(width, depth)`` tuple and uses depth = 0.30 * DA^0.315 — not
   the Bieger Table 3 USA depth exponent 0.213 used by the R port (and by
   deep/bieger.py). No R counterpart; not ported.
4. ``derive_from_stages``: R names the second stage ``low_bank_stage`` (argument
   and output key); DEEP calls it ``floodplain_stage`` (keyword-only). Math and
   rounding are identical (depth 3 dp, widths 1 dp, fp_stage 3 dp, ER 2 dp,
   BHR 2 dp; edge_limited from the flood-prone width only). Ported: R names.
5. ``summarize_profile`` diverges structurally. DEEP solves the bankfull stage
   from the Bieger regional cross-sectional AREA on the profile
   (``stage_for_area``/``flow_area`` bisection, depth fallback) and returns a
   different key set (``profile``, ``bankfull_depth_m`` 2 dp, curve-width
   fallback for ``bankfull_width_m``, ``top_of_bank_m``, ``bankfull_division``,
   ``fp_stage_m`` = thalweg + 2*curve depth). The R port dropped the area-based
   staging entirely: bankfull stage = thalweg + curve depth, low-bank stage =
   lower top-of-bank clamped to bankfull, and it returns the
   ``derive_from_stages()`` keys plus ``curve_width_m`` / ``curve_depth_m`` /
   ``division`` (unrounded beyond what bieger_geometry already rounded).
   Ported: R.
6. DEEP-only functions with no R counterpart (not ported): ``flow_area``,
   ``stage_for_area``, ``balanced_profile``, ``simplify_profile``,
   ``_representative``, ``reach_summary``, ``division_at``,
   ``area_equations``/``AREA_R2``.
7. ``top_width`` interval ordering: R sorts candidate wetted intervals by their
   left endpoint only (stable); DEEP sorts lexicographically. The merged union —
   the only thing returned — is identical either way.
8. Guard nuance: R's ``top_of_bank_elev`` checks ``length(elevs) < 5``; DEEP
   checks ``len(stations) < 5``. Equivalent for well-formed profiles; this port
   follows R (elevs).
9. Indexing: ``thalweg_index`` is 0-based here (Python) vs 1-based in R — a
   convention change, not a behavioural one.

Identical in both and adapted from DEEP: ``top_width`` (interval merge with the
1e-9 tolerance), ``flow_width`` (thalweg outward walk with interpolated
crossings), ``top_of_bank_elev`` (crest walk, "descended > 0.5 m" stop),
``bank_height_ratio`` (2 dp), ``transect_entrenchment`` (ER 2 dp, flood-prone
width 1 dp, edge test = merged interval within 5% of the profile ends).
"""

from __future__ import annotations

import math

import numpy as np

# --------------------------------------------------------------------------- #
# Bieger (2015) regional bankfull geometry
# y = a * DA^b   (DA in km^2; width & depth in m, area in m^2). Bieger,
# Rathjens, Allen & Arnold 2015, JAWRA 51(4):842-858, Table 3, by the eight
# Fenneman physiographic divisions of CONUS; "USA" is the national fallback
# curve. LUP and IHI are tentative (n < 10).
# --------------------------------------------------------------------------- #

bieger_coef = {
    "LUP": {"width": (4.15, 0.308), "depth": (0.31, 0.202), "area": (1.27, 0.509)},
    "APL": {"width": (2.22, 0.363), "depth": (0.24, 0.323), "area": (0.52, 0.680)},
    "AHI": {"width": (3.12, 0.415), "depth": (0.26, 0.287), "area": (0.82, 0.704)},
    "IPL": {"width": (2.56, 0.351), "depth": (0.38, 0.191), "area": (1.28, 0.472)},
    "IHI": {"width": (23.23, 0.121), "depth": (0.27, 0.267), "area": (6.28, 0.387)},
    "RMS": {"width": (1.24, 0.435), "depth": (0.23, 0.225), "area": (0.20, 0.688)},
    "IMP": {"width": (1.11, 0.415), "depth": (0.07, 0.329), "area": (0.07, 0.751)},
    "PMS": {"width": (2.76, 0.399), "depth": (0.23, 0.294), "area": (0.87, 0.652)},
    "USA": {"width": (2.70, 0.352), "depth": (0.30, 0.213), "area": (0.95, 0.540)},
}

bieger_division_name = {
    "LUP": "Laurentian Upland", "APL": "Atlantic Plain",
    "AHI": "Appalachian Highlands", "IPL": "Interior Plains",
    "IHI": "Interior Highlands", "RMS": "Rocky Mountain System",
    "IMP": "Intermontane Plateaus", "PMS": "Pacific Mountain System",
    "USA": "National curve",
}

# Physiographic-division names (the "DIVISION" property in
# physio_divisions.geojson, upper-cased) -> Bieger abbreviation. Used by the
# cross-section module to pick the regional curve from a site's lat/lon.
bieger_division_abbr = {
    "LAURENTIAN UPLAND": "LUP", "ATLANTIC PLAIN": "APL",
    "APPALACHIAN HIGHLANDS": "AHI", "INTERIOR PLAINS": "IPL",
    "INTERIOR HIGHLANDS": "IHI", "ROCKY MOUNTAIN SYSTEM": "RMS",
    "INTERMONTANE PLATEAUS": "IMP", "PACIFIC MOUNTAIN SYSTEM": "PMS",
}


def bieger_geometry(da_sqkm, division=None) -> dict:
    """Regional bankfull geometry for a drainage area (Bieger 2015 division curve).

    ``da_sqkm``: drainage area in km^2 (non-numeric/non-finite coerced, then
    clamped to >= 0.01). ``division``: Bieger abbreviation (a ``bieger_coef``
    key); None/unknown -> "USA". Returns dict(width_m, depth_m, area_m2,
    division, division_name, regional).
    """
    try:
        arr = np.asarray(da_sqkm, dtype=float).reshape(-1)
        da = float(arr[0]) if arr.size == 1 else float("nan")
    except (TypeError, ValueError):
        da = float("nan")
    if not math.isfinite(da):
        da = 0.01
    da = max(da, 0.01)
    key = division if isinstance(division, str) and division in bieger_coef else "USA"
    co = bieger_coef[key]

    def pw(ab):
        return ab[0] * da ** ab[1]

    return {
        "width_m": round(pw(co["width"]), 2),   # R: round(, 2)
        "depth_m": round(pw(co["depth"]), 3),   # R: round(, 3)
        "area_m2": round(pw(co["area"]), 2),    # R: round(, 2)
        "division": key,
        "division_name": bieger_division_name[key],
        "regional": key != "USA",
    }


# --------------------------------------------------------------------------- #
# Profile geomorphology (pure, no I/O)
# --------------------------------------------------------------------------- #


def top_width(stations, elevs, stage) -> tuple[float, list[list[float]]]:
    """Top width of a station-elevation profile at a water-surface ``stage``.

    Integrates the profile, merging wetted intervals so islands / non-monotonic
    beds are handled. Returns ``(T, merged)`` where ``T`` is the total top width
    and ``merged`` is a list of ``[x_left, x_right]`` wetted spans (R's
    ``list(T =, merged =)``).
    """
    n = len(stations)
    stage = float(stage)
    intervals: list[list[float]] = []
    for i in range(n - 1):
        x1, z1 = float(stations[i]), float(elevs[i])
        x2, z2 = float(stations[i + 1]), float(elevs[i + 1])
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
    # R orders by left endpoint only (stable); lexicographic sort yields the
    # same merged union.
    intervals.sort()
    merged: list[list[float]] = []
    for a, b in intervals:
        if merged and a <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    T = float(sum(b - a for a, b in merged))
    return T, merged


def flow_width(stations, elevs, stage, thalweg_index=None) -> tuple[float, bool]:
    """Contiguous top width spanning the thalweg at a water-surface ``stage``
    (Rosgen edge-of-water to edge-of-water across the channel). From the thalweg,
    walk outward each way to the first point at/above ``stage`` (interpolating
    the crossing). Returns ``(width, edge_limited)``; ``edge_limited`` True if a
    side never reached the stage. ``thalweg_index`` is 0-based (R's is 1-based).
    """
    n = len(stations)
    if n < 2:
        return 0.0, False
    ti = int(thalweg_index) if thalweg_index is not None else int(np.argmin(elevs))
    stage = float(stage)
    if elevs[ti] >= stage:
        return 0.0, False

    def edge(direction: int) -> tuple[float, bool]:
        i = ti
        while 0 <= i + direction < n:
            j = i + direction
            if elevs[j] >= stage:
                t = (stage - elevs[i]) / (elevs[j] - elevs[i])
                return float(stations[i] + t * (stations[j] - stations[i])), False
            i = j
        return float(stations[i]), True

    l_pos, l_edge = edge(-1)
    r_pos, r_edge = edge(+1)
    return max(r_pos - l_pos, 0.0), bool(l_edge or r_edge)


def top_of_bank_elev(stations, elevs):
    """Lowest top-of-bank elevation, scanning outward from the thalweg until the
    profile descends > 0.5 m past a local crest (the floodplain/terrace beyond
    the bank). Returns the lower of the two bank crests (float), or None if the
    profile is unusable.
    """
    if len(elevs) < 5:
        return None
    ti = int(np.argmin(elevs))

    def crest(direction: int) -> float:
        best = elevs[ti]
        i = ti
        while 0 <= i + direction < len(elevs):
            i += direction
            if elevs[i] > best:
                best = elevs[i]
            elif best - elevs[i] > 0.5:
                break
        return best

    return float(min(crest(-1), crest(1)))


def bank_height_ratio(stations, elevs, d_bf):
    """Bank-height ratio = (lowest top-of-bank - thalweg) / bankfull depth.
    ~1 = floodplain-connected; > 1.5 = incised. None if unusable.
    """
    if len(stations) < 5 or d_bf <= 0:
        return None
    tob = top_of_bank_elev(stations, elevs)
    if tob is None:
        return None
    return round((tob - float(np.min(elevs))) / d_bf, 2)   # R: round(, 2)


def transect_entrenchment(stations, elevs, d_bf, w_bf):
    """Rosgen entrenchment for one transect using a regional-curve bankfull width
    as the denominator. flood-prone stage = thalweg + 2*d_bf; ER = flood-prone
    width / w_bf. Returns dict(er, floodprone_width_m, thalweg, edge_limited)
    or None if unusable.
    """
    if len(stations) < 5 or w_bf <= 0 or d_bf <= 0:
        return None
    thalweg = float(np.min(elevs))
    fp_stage = thalweg + 2 * d_bf
    T, merged = top_width(stations, elevs, fp_stage)
    if T <= 0:
        return None
    er = T / w_bf
    span = float(stations[-1]) - float(stations[0])
    if span == 0:
        span = 1
    s_lo, s_hi = float(stations[0]), float(stations[-1])
    edge = any(a <= s_lo + 0.05 * span or b >= s_hi - 0.05 * span
               for a, b in merged)
    return {"er": round(er, 2),                     # R: round(, 2)
            "floodprone_width_m": round(T, 1),      # R: round(, 1)
            "thalweg": thalweg, "edge_limited": bool(edge)}


def derive_from_stages(stations, elevs, bankfull_stage, low_bank_stage,
                       thalweg=None) -> dict:
    """Recompute Rosgen ER/BHR + a measured bankfull width from chosen absolute
    stages. Pure — drives the editable cross-section. ``bankfull_stage`` /
    ``low_bank_stage`` are water-surface elevations in the profile's datum;
    ``thalweg`` defaults to min(elevs)::

        d (max bankfull depth) = bankfull_stage - thalweg
        bankfull width  = contiguous top width at bankfull_stage
        flood-prone stage = thalweg + 2*d ; flood-prone width measured there
        ER  = flood-prone width / bankfull width
        BHR = (low_bank_stage - thalweg) / d
    """
    if thalweg is None:
        # NOTE(parity): R's min() of an empty vector is +Inf (with a warning),
        # which then takes the d <= 0 "no geometry" exit; Python's min raises,
        # so reproduce the Inf explicitly.
        thalweg = float(np.min(elevs)) if len(elevs) else math.inf
    ti = int(np.argmin(elevs)) if len(elevs) else 0
    d = float(bankfull_stage) - float(thalweg)
    out = {
        "thalweg": thalweg,
        "bankfull_stage": bankfull_stage,
        "low_bank_stage": low_bank_stage,
        "bankfull_depth_max_m": round(d, 3) if d > 0 else None,   # R: round(, 3)
        "entrenchment_ratio": None,
        "bank_height_ratio": None,
        "bankfull_width_m": None,
        "flood_prone_width_m": None,
        "fp_stage_m": None,
        "edge_limited": False,
    }
    if d <= 0:
        return out
    w_bf, _ = flow_width(stations, elevs, bankfull_stage, thalweg_index=ti)
    fp_stage = thalweg + 2 * d
    w_fp, fp_edge = flow_width(stations, elevs, fp_stage, thalweg_index=ti)
    out["bankfull_width_m"] = round(w_bf, 1) if w_bf > 0 else None     # R: round(, 1)
    out["flood_prone_width_m"] = round(w_fp, 1) if w_fp > 0 else None  # R: round(, 1)
    out["fp_stage_m"] = round(fp_stage, 3)                             # R: round(, 3)
    out["edge_limited"] = bool(fp_edge)
    if w_bf > 0:
        out["entrenchment_ratio"] = round(w_fp / w_bf, 2)              # R: round(, 2)
    out["bank_height_ratio"] = round((float(low_bank_stage) - thalweg) / d, 2)  # R: round(, 2)
    return out


def summarize_profile(stations, elevs, da_sqkm, bankfull=None, division=None) -> dict:
    """Default Rosgen summary for a profile at curve-derived stages (the editor's
    starting point). Bankfull depth from ``bankfull`` — a positional
    ``(width_m, depth_m)`` pair, matching R's ``bankfull[[1]]``/``[[2]]`` — or
    from :func:`bieger_geometry`; bankfull stage = thalweg + depth; low-bank
    stage = lower top-of-bank clamped to bankfull. Returns
    :func:`derive_from_stages` output plus the seed stages
    (curve_width_m, curve_depth_m, division).
    """
    if bankfull is not None:
        bf = {"width_m": bankfull[0], "depth_m": bankfull[1]}
    else:
        bf = bieger_geometry(da_sqkm, division)
    thalweg = float(np.min(elevs))
    tob = top_of_bank_elev(stations, elevs)
    bankfull_stage = thalweg + bf["depth_m"]
    low_bank_stage = max(tob if tob is not None else bankfull_stage, bankfull_stage)
    res = derive_from_stages(stations, elevs,
                             bankfull_stage=bankfull_stage,
                             low_bank_stage=low_bank_stage,
                             thalweg=thalweg)
    res["curve_width_m"] = bf["width_m"]
    res["curve_depth_m"] = bf["depth_m"]
    bf_division = bf.get("division")
    res["division"] = bf_division if bf_division is not None else division  # R: %||%
    return res
