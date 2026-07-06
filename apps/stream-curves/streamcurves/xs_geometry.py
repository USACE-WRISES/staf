"""Port of R/17_xs_geometry.R — transect geometry for the cross-section tool.

A reach is a polyline of lon/lat vertices (from NLDI, ordered so that increasing
index = upstream). We project to a local equirectangular metric frame at the site
latitude (accurate at the ~300 m reach scale), take the ~1000 ft segment upstream
of the snapped point, place N cross-section stations along it, and build a
perpendicular transect of sample points at each (to send to 3DEP for elevations).

Lineage: the R file itself ports hype-app/delineate.py (``_normal``) into base R;
this module closes the loop back to Python but follows the R version, which is
authoritative for parity.

Shapes (numpy throughout): polylines are (n, 2) float arrays — metric ``xy``
columns are (x, y), geographic arrays are columns (lon, lat); R's cbind column
names are dropped. R named lists become dicts with the same keys; the R named
vector ``center = c(lon=, lat=)`` becomes ``{"lon": ..., "lat": ...}``.
"""

from __future__ import annotations

import math

import numpy as np

# --------------------------------------------------------------------------- #
# Local equirectangular projection (about lon0/lat0)
# --------------------------------------------------------------------------- #


def to_m(lon, lat, lon0, lat0) -> np.ndarray:
    """Project lon/lat (degrees) to local metres about (lon0, lat0).

    Returns an (n, 2) array with columns (x, y); scalars give shape (1, 2),
    matching R's ``cbind`` of scalars.
    """
    kx = 111320 * math.cos(lat0 * math.pi / 180)
    ky = 110540
    lon = np.atleast_1d(np.asarray(lon, dtype=float))
    lat = np.atleast_1d(np.asarray(lat, dtype=float))
    return np.column_stack(((lon - lon0) * kx, (lat - lat0) * ky))


def to_ll(x, y, lon0, lat0) -> np.ndarray:
    """Inverse of :func:`to_m`. Returns an (n, 2) array with columns (lon, lat)."""
    kx = 111320 * math.cos(lat0 * math.pi / 180)
    ky = 110540
    x = np.atleast_1d(np.asarray(x, dtype=float))
    y = np.atleast_1d(np.asarray(y, dtype=float))
    return np.column_stack((lon0 + x / kx, lat0 + y / ky))


# --------------------------------------------------------------------------- #
# Polyline helpers (xy is an (n, 2) array of metric coordinates)
# --------------------------------------------------------------------------- #


def polyline_cumlen(xy) -> np.ndarray:
    """Cumulative arc length at each vertex (first element 0).

    NOTE(parity): R returns scalar 0 for a < 2-row polyline (== a length-1
    vector); here that is ``np.zeros(1)``.
    """
    xy = np.asarray(xy, dtype=float)
    if xy.shape[0] < 2:
        return np.zeros(1)
    seg = np.sqrt(np.diff(xy[:, 0]) ** 2 + np.diff(xy[:, 1]) ** 2)
    return np.concatenate(([0.0], np.cumsum(seg)))


def polyline_length(xy) -> float:
    cl = polyline_cumlen(xy)
    return float(cl[-1])


def interpolate_along(xy, s) -> np.ndarray:
    """Point (unnamed ``[x, y]``) at arc-length ``s`` along polyline ``xy``."""
    xy = np.asarray(xy, dtype=float)
    cl = polyline_cumlen(xy)
    L = float(cl[-1])
    if L <= 0:
        return np.array([xy[0, 0], xy[0, 1]])
    s = max(0.0, min(float(s), L))
    # findInterval(s, cl, rightmost.closed = TRUE), 0-based; the clamp below
    # makes the two definitions agree at s == L exactly like the R code.
    i = int(np.searchsorted(cl, s, side="right")) - 1
    i = max(0, min(i, xy.shape[0] - 2))
    seg_len = cl[i + 1] - cl[i]
    t = (s - cl[i]) / seg_len if seg_len > 0 else 0.0
    return np.array([xy[i, 0] + t * (xy[i + 1, 0] - xy[i, 0]),
                     xy[i, 1] + t * (xy[i + 1, 1] - xy[i, 1])])


def project_point_to_polyline(xy, px, py) -> dict:
    """Closest point on polyline ``xy`` to (px, py); returns dict(station, dist)."""
    xy = np.asarray(xy, dtype=float)
    px = float(px)
    py = float(py)
    if xy.shape[0] < 2:
        return {"station": 0.0,
                "dist": math.sqrt((px - xy[0, 0]) ** 2 + (py - xy[0, 1]) ** 2)}
    cl = polyline_cumlen(xy)
    ax, ay = xy[:-1, 0], xy[:-1, 1]
    dx = np.diff(xy[:, 0])
    dy = np.diff(xy[:, 1])
    seg2 = dx * dx + dy * dy
    t = np.divide((px - ax) * dx + (py - ay) * dy, seg2,
                  out=np.zeros_like(seg2), where=seg2 > 0)
    t = np.clip(t, 0.0, 1.0)
    cx = ax + t * dx
    cy = ay + t * dy
    d2 = (px - cx) ** 2 + (py - cy) ** 2
    # argmin keeps the first minimum — same tie-break as R's strict `<` loop.
    i = int(np.argmin(d2))
    return {"station": float(cl[i] + t[i] * np.sqrt(seg2[i])),
            "dist": float(np.sqrt(d2[i]))}


def unit_normal(xy, s, ds=5) -> dict:
    """Unit normal to polyline ``xy`` at arc-length ``s`` via a centred difference
    (stable at endpoints). Returns dict(point=[x, y], normal=[nx, ny]).
    Port of hype-app ``_normal()`` (via the R port).
    """
    xy = np.asarray(xy, dtype=float)
    L = polyline_length(xy)
    s0 = max(0.0, min(float(s), L))
    a = interpolate_along(xy, max(0.0, s0 - ds))
    b = interpolate_along(xy, min(L, s0 + ds))
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    nrm = math.sqrt(dx * dx + dy * dy)
    if nrm == 0:
        nrm = 1.0
    return {"point": interpolate_along(xy, s0),
            "normal": np.array([-dy / nrm, dx / nrm])}


# --------------------------------------------------------------------------- #
# Reach segments and transects
# --------------------------------------------------------------------------- #


def reach_upstream_segment(coords_lonlat, snap_lonlat, length_ft=1000,
                           lon0=None, lat0=None) -> dict:
    """Sub-polyline of ``length_ft`` starting at the projection of ``snap_lonlat``
    onto ``coords_lonlat``, walking in increasing-index direction (= upstream by
    convention). Returns dict(xy [metric (m, 2)], lon0, lat0, length_m,
    full_length_m).
    """
    coords_lonlat = np.asarray(coords_lonlat, dtype=float)
    if lat0 is None:
        lat0 = float(snap_lonlat[1])
    if lon0 is None:
        lon0 = float(snap_lonlat[0])
    xy = to_m(coords_lonlat[:, 0], coords_lonlat[:, 1], lon0, lat0)
    snap_m = to_m(snap_lonlat[0], snap_lonlat[1], lon0, lat0)
    proj = project_point_to_polyline(xy, snap_m[0, 0], snap_m[0, 1])
    L = polyline_length(xy)
    len_m = length_ft * 0.3048
    s0 = proj["station"]
    s1 = min(s0 + len_m, L)
    cl = polyline_cumlen(xy)
    interior = np.where((cl > s0 + 1e-9) & (cl < s1 - 1e-9))[0]
    rows = [interpolate_along(xy, s0)]
    for k in interior:
        rows.append(np.array([xy[k, 0], xy[k, 1]]))
    rows.append(interpolate_along(xy, s1))
    pts = np.vstack(rows)
    return {"xy": pts, "lon0": lon0, "lat0": lat0,
            "length_m": polyline_length(pts), "full_length_m": L}


def reach_from_bearing(lat, lon, bearing_deg, length_ft=1000) -> np.ndarray:
    """Build a 2-point reach from a point + compass bearing (fallback when a site
    is off the NHD network). ``bearing_deg``: 0 = north, 90 = east. Returns the
    same shape as a ``coords_lonlat`` matrix — (2, 2), columns (lon, lat) —
    ordered start -> upstream.
    """
    len_m = length_ft * 0.3048
    theta = bearing_deg * math.pi / 180
    dx = len_m * math.sin(theta)   # east
    dy = len_m * math.cos(theta)   # north
    end = to_ll(dx, dy, lon, lat)
    return np.array([[lon, lat], [end[0, 0], end[0, 1]]], dtype=float)


def build_transects(segment, n_transects=3, half_m=150, n_samp=200,
                    fracs=None) -> list[dict]:
    """Place N cross-sections along the reach segment and build their perpendicular
    transect sample points. ``half_m`` = half-width of each transect; ``n_samp`` =
    points across (~2 m spacing). Returns a list, one dict per transect, with keys:
    index, frac, station_along, stations (the transect station axis, m; 0 = reach
    line), lonlat ((n_samp, 2) array to send to 3DEP), center ({"lon", "lat"}).

    NOTE(parity): ``index`` is 1-based, matching R's ``seq_along``.
    """
    xy = np.asarray(segment["xy"], dtype=float)
    lon0 = segment["lon0"]
    lat0 = segment["lat0"]
    L = polyline_length(xy)
    if fracs is None:
        fracs = (np.array([0.5]) if n_transects <= 1
                 else np.linspace(0.15, 0.85, n_transects))
    else:
        fracs = np.atleast_1d(np.asarray(fracs, dtype=float))
    ts = np.linspace(-half_m, half_m, n_samp)
    out = []
    for i, frac in enumerate(fracs):
        s = float(frac) * L
        un = unit_normal(xy, s)
        px, py = un["point"][0], un["point"][1]
        nx, ny = un["normal"][0], un["normal"][1]
        sx = px + nx * ts
        sy = py + ny * ts
        ll = to_ll(sx, sy, lon0, lat0)
        center_ll = to_ll(px, py, lon0, lat0)
        out.append({
            "index": i + 1,
            "frac": float(frac),
            "station_along": s,
            "stations": ts.copy(),
            "lonlat": ll,
            "center": {"lon": float(center_ll[0, 0]), "lat": float(center_ll[0, 1])},
        })
    return out


def transect_half_width(bf_width_m) -> float:
    """Half-width for a transect from a regional-curve bankfull width (m), clamped
    to a sane viewing band. Mirrors the hype-app sizing. Non-numeric / non-scalar /
    non-finite / non-positive input falls back to a 10 m bankfull width (-> 80).
    """
    try:
        arr = np.asarray(bf_width_m, dtype=float).reshape(-1)
        bf = float(arr[0]) if arr.size == 1 else float("nan")
    except (TypeError, ValueError):
        bf = float("nan")
    if not math.isfinite(bf) or bf <= 0:
        bf = 10.0
    return float(min(max(8 * bf, 80), 250))
