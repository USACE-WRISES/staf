"""USGS NWIS daily streamflow (keyless waterservices.usgs.gov).

Finds a nearby streamgage of comparable drainage area, pulls ~10 years of daily
discharge (parameter 00060, statistic 00003 = mean), and derives screening flow
statistics: zero-flow-day fraction (permanence), flow-duration percentiles
(Q10 / Q50 / Q90, where Qp = flow exceeded p% of the time), and a baseflow-ish
stability ratio (Q90/Q50). Never raises — returns None when no relevant gage/data.

Uses the classic ``waterservices.usgs.gov`` endpoints, which need no API key (the
newer ``api.waterdata.usgs.gov`` requires one). Gage relevance is gated on the
drainage-area ratio so an unrelated gage isn't attributed to the reach.
"""
from __future__ import annotations

from statistics import median
from typing import Optional

import requests

SITE_URL = "https://waterservices.usgs.gov/nwis/site/"
DV_URL = "https://waterservices.usgs.gov/nwis/dv/"
SQMI_PER_SQKM = 0.386102


def _nearby_gages(lat: float, lon: float, deg: float = 0.25, timeout: float = 12.0) -> list[dict]:
    """Discharge streamgages with daily data within a bbox of the point (RDB)."""
    bbox = f"{lon-deg:.5f},{lat-deg:.5f},{lon+deg:.5f},{lat+deg:.5f}"  # W,S,E,N
    params = {"format": "rdb", "bBox": bbox, "parameterCd": "00060", "siteType": "ST",
              "hasDataTypeCd": "dv", "siteStatus": "all", "siteOutput": "expanded"}
    try:
        r = requests.get(SITE_URL, params=params, timeout=timeout)
        if r.status_code != 200 or not r.text:
            return []
    except Exception:  # noqa: BLE001
        return []
    lines = [ln for ln in r.text.splitlines() if ln and not ln.startswith("#")]
    if len(lines) < 3:  # header, format line, then data
        return []
    header = lines[0].split("\t")

    def idx(name):
        return header.index(name) if name in header else None
    i_site, i_lat, i_lon = idx("site_no"), idx("dec_lat_va"), idx("dec_long_va")
    i_da, i_name = idx("drain_area_va"), idx("station_nm")
    if i_site is None or i_lat is None or i_lon is None:
        return []
    gages = []
    for ln in lines[2:]:                                  # skip the RDB format line
        c = ln.split("\t")
        if len(c) <= i_lon:
            continue
        try:
            g = {"site": c[i_site], "lat": float(c[i_lat]), "lon": float(c[i_lon]),
                 "da_sqmi": float(c[i_da]) if (i_da is not None and c[i_da].strip()) else None,
                 "name": (c[i_name].strip() if i_name is not None and len(c) > i_name else "")}
            gages.append(g)
        except (ValueError, IndexError):
            continue
    return gages


def _rank_gages(gages: list[dict], lat: float, lon: float,
                da_sqkm: Optional[float]) -> list[dict]:
    """Gages ranked by drainage-area comparability (ratio 0.3-3.5x), then distance."""
    da_sqmi = da_sqkm * SQMI_PER_SQKM if da_sqkm else None

    def key(g):
        dist = ((g["lat"] - lat) ** 2 + (g["lon"] - lon) ** 2) ** 0.5
        if da_sqmi and g.get("da_sqmi"):
            ratio = g["da_sqmi"] / da_sqmi
            if ratio < 0.3 or ratio > 3.5:
                return (2, dist)                          # implausible DA — deprioritize
            return (0, abs(1.0 - ratio) + dist * 5)
        return (1, dist)
    return sorted(gages, key=key)


def _daily_flow(site: str, start: str = "2014-01-01", timeout: float = 18.0) -> Optional[list[float]]:
    params = {"format": "json", "sites": site, "parameterCd": "00060",
              "statCd": "00003", "startDT": start}
    try:
        r = requests.get(DV_URL, params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        ts = r.json().get("value", {}).get("timeSeries", [])
        if not ts:
            return None
        pts = ts[0].get("values", [{}])[0].get("value", [])
        vals = []
        for p in pts:
            try:
                v = float(p.get("value"))
            except (TypeError, ValueError):
                continue
            if v >= 0:                                   # -999999 = no-data flag
                vals.append(v)
        return vals or None
    except Exception:  # noqa: BLE001
        return None


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Value at the p-th percentile of an ascending-sorted list (0..100)."""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def flow_stats(lat: float, lon: float, da_sqkm: Optional[float] = None) -> Optional[dict]:
    """Screening flow statistics from the most relevant nearby gage, or None.

    Returns ``{site, name, da_sqmi, n_days, zero_frac, q10, q50, q90, baseflow_ratio}``.
    Qp = discharge (cfs) exceeded p% of the time (so Q90 is a low-flow index).
    """
    ranked = _rank_gages(_nearby_gages(lat, lon), lat, lon, da_sqkm)
    for g in ranked[:5]:                                  # first ranked gage with a usable record
        vals = _daily_flow(g["site"])
        if not vals or len(vals) < 60:
            continue
        asc = sorted(vals)
        n = len(asc)
        zeros = sum(1 for v in asc if v <= 0.0)
        # Qp exceeded p% of the time -> value at the (100-p)-th ascending percentile.
        q10 = _percentile(asc, 90.0)
        q50 = median(asc)
        q90 = _percentile(asc, 10.0)
        return {
            "site": g["site"], "name": g["name"], "da_sqmi": g.get("da_sqmi"),
            "n_days": n, "zero_frac": round(zeros / n, 4),
            "q10": round(q10, 1), "q50": round(q50, 1), "q90": round(q90, 2),
            "baseflow_ratio": round(q90 / q50, 3) if q50 > 0 else None,
        }
    return None
