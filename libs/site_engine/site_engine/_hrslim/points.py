"""Point lookups from the bundled national tables (bundle v2, Phase 3).

Each function repeats one app's live rule over a table built by ``tools/hr-slim``
(``hrbuild/tables.py``, ``hrbuild/nwis.py``), so a local answer can replace the service call:

- ``wqp_easi``: EASI's station-balanced TN, TP or water temperature summary
  (``easi.datasources.wqp.sample_summary``), the last ten years within 5 miles, EASI's exclusions and
  unit rules; ``details=True`` adds the results behind it (``wqp_results``), each with its WQP result
  identifier;
- ``wqp_sfari``: SFARI's median of every numeric TN or TP result since 2015 within 5 miles
  (``sfari.datasources.wqp.median_value``);
- ``nid_radius``: mapped NID dams within a geodesic radius (EASI M20 and DEEP's one-mile count);
- ``nid_box``: NID dams in SFARI's one-mile box (``sfari.datasources.nid_barriers``);
- ``nwis_flow_stats``: SFARI's gage pick and daily-flow statistics (``sfari.datasources.nwis``);
- ``nas_established``: EASI's established nonindigenous taxa by HUC12, else HUC8
  (``easi.datasources.nas``; the live API stops at 500 records, the table does not).

The tables folder holds ``wqp_results.parquet``, ``wqp_temperature.parquet``, ``wqp_stations.parquet``,
``nid_points.parquet``, ``nwis_gages.parquet`` and ``nas_taxa.parquet``.
"""
from __future__ import annotations

import math
import threading
from datetime import date
from pathlib import Path
from statistics import median
from typing import Optional

import numpy as np

#: EASI's exclusion reasons by code (``hrbuild.tables.WQP_REASONS``); temperature adds results
#: reported in Fahrenheit, kept converted, and Celsius results set apart as not realistic (outside
#: -1 to 40 C, or out of season for the area), whose values are kept (``hrbuild.wqp_temperature``)
WQP_REASONS = ("ok", "blank", "rejected", "censored", "non_total_fraction", "unsupported_unit", "nonnumeric")
TEMP_REASONS = WQP_REASONS + ("fahrenheit", "fahrenheit_implausible", "celsius_implausible", "celsius_seasonal")
FAHRENHEIT = TEMP_REASONS.index("fahrenheit")
FAHRENHEIT_IMPLAUSIBLE = TEMP_REASONS.index("fahrenheit_implausible")
SCREENED = (TEMP_REASONS.index("celsius_implausible"), TEMP_REASONS.index("celsius_seasonal"))
WQP_PARAMS = ("tn", "tp")
EASI_PARAMS = WQP_PARAMS + ("temp",)
EARTH_MI = 3958.7613            # EASI's WQP distance
EARTH_M = 6371008.8             # EASI's and DEEP's NID distance
SQMI_PER_SQKM = 0.386102


class PointTables:
    """The national tables, each read on first use. ``ensure(name)``, when given, makes a table
    present before it is read (a delivered bundle fetches each table the first time it is used)."""

    def __init__(self, folder, *, ensure=None):
        self.folder = Path(folder)
        self._lock = threading.RLock()       # a table's loader may load the station table
        self._cache: dict = {}
        self._ensure = ensure

    def available(self) -> bool:
        return self._ensure is not None or (self.folder / "wqp_results.parquet").exists()

    def _path(self, name: str) -> Path:
        if self._ensure is not None:
            self._ensure(name)
        return self.folder / name

    def _get(self, name, build):
        with self._lock:
            if name not in self._cache:
                self._cache[name] = build()
            return self._cache[name]

    def wqp_stations(self) -> dict:
        def build():
            import pyarrow.parquet as pq
            s = pq.read_table(self._path("wqp_stations.parquet"))
            return {"ids": s.column("station").to_pylist(), "names": s.column("name").to_pylist(),
                    "lat": s.column("lat").to_numpy(), "lon": s.column("lon").to_numpy(), "n": s.num_rows}
        return self._get("wqp_stations", build)

    def _wqp_table(self, name: str, value_column: str) -> dict:
        import pyarrow.parquet as pq
        t = pq.read_table(self._path(name))
        st = self.wqp_stations()
        station = t.column("station").to_numpy().astype(np.int64)
        out = {"station": station,
               "date": t.column("date").to_numpy().astype("datetime64[D]"),
               "reason": t.column("reason").to_numpy(),
               "starts": np.searchsorted(station, np.arange(st["n"] + 1)),
               "ids": st["ids"], "names": st["names"], "lat": st["lat"], "lon": st["lon"]}
        if value_column in t.column_names:
            out["value"] = t.column(value_column).to_numpy(zero_copy_only=False).astype(np.float64)
        else:                                            # temperature in hundredths of a degree
            c100 = t.column("value_c100")
            out["value"] = c100.cast("float64").fill_null(float("nan")).to_numpy() / 100.0
        for c in ("param", "milli"):
            if c in t.column_names:
                out[c] = t.column(c).to_numpy(zero_copy_only=False)
        for c in ("rid_num", "rid_uuid", "rid_text"):
            out[c] = t.column(c) if c in t.column_names else None
        return out

    def wqp(self) -> dict:
        """TN and TP: ``value`` is the raw number (SFARI's), ``milli`` marks ug/L for EASI."""
        return self._get("wqp", lambda: self._wqp_table("wqp_results.parquet", "raw"))

    def wqp_temp(self) -> dict:
        """Stream temperature: ``value`` in degrees C (hundredths), converted for results reported
        in Fahrenheit, kept for Celsius results the screen sets apart, NaN when excluded."""
        return self._get("wqp_temp", lambda: self._wqp_table("wqp_temperature.parquet", "value"))

    def nid(self) -> dict:
        def build():
            import pyarrow.parquet as pq
            t = pq.read_table(self._path("nid_points.parquet"))
            return dict((c, t.column(c).to_numpy(zero_copy_only=False)) for c in t.column_names)
        return self._get("nid", build)

    def nas(self) -> dict:
        def build():
            import pyarrow.parquet as pq
            t = pq.read_table(self._path("nas_taxa.parquet"), columns=["scientificName", "huc8", "huc12", "status"])
            return dict((c, t.column(c).to_numpy(zero_copy_only=False)) for c in t.column_names)
        return self._get("nas", build)

    def nwis(self) -> dict:
        def build():
            import pyarrow.parquet as pq
            t = pq.read_table(self._path("nwis_gages.parquet"))
            return dict((c, t.column(c).to_numpy(zero_copy_only=False)) for c in t.column_names)
        return self._get("nwis", build)


def _haversine(lat, lon, lats, lons, radius):
    p1, p2 = math.radians(lat), np.radians(lats)
    dp = np.radians(lats - lat)
    dl = np.radians(lons - lon)
    a = np.sin(dp / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * radius * np.arcsin(np.minimum(1.0, np.sqrt(a)))


def _distance(lat1, lon1, lat2, lon2, radius) -> float:
    """The apps' scalar haversine, for the distances they report."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def _ten_years_before(day: date) -> date:
    try:
        return day.replace(year=day.year - 10)
    except ValueError:                                   # leap day
        return day.replace(year=day.year - 10, day=28)


def result_id(w: dict, i: int) -> Optional[str]:
    """The WQP result identifier of row ``i`` (``STORET-<number>``, a USGS UUID, or the text kept)."""
    import uuid
    if w.get("rid_num") is None:
        return None
    n = w["rid_num"][i].as_py()
    if n is not None:
        return f"STORET-{n}"
    u = w["rid_uuid"][i].as_py()
    if u is not None:
        return str(uuid.UUID(bytes=u))
    return w["rid_text"][i].as_py()


def _wqp_rows(tables: PointTables, param: str, lat: float, lon: float, within_mi: float, start: date):
    w = tables.wqp_temp() if param == "temp" else tables.wqp()
    near = np.nonzero(_haversine(lat, lon, w["lat"], w["lon"], EARTH_MI) <= within_mi)[0]
    if not len(near):
        return w, np.zeros(0, dtype=np.int64)
    rows = np.concatenate([np.arange(w["starts"][s], w["starts"][s + 1]) for s in near])
    keep = w["date"][rows] >= np.datetime64(start, "D")
    if param != "temp":
        keep &= w["param"][rows] == WQP_PARAMS.index(param)
    return w, rows[keep]


def _easi_value(w: dict, i: int, param: str) -> float:
    if param == "temp":
        return float(w["value"][i])
    return float(w["value"][i]) * (0.001 if w["milli"][i] else 1.0)


def _celsius(code: int, screen: bool) -> bool:
    """A Celsius result EASI's rule keeps; with ``screen``, not one set apart as unrealistic."""
    return code == 0 or (not screen and code in SCREENED)


def _station_choice(w: dict, rows: np.ndarray, param: str, fahrenheit: bool, screen: bool = True) -> dict:
    """Per station, which results count: Celsius (or TN, TP) results kept by EASI's rule (and, with
    ``screen``, realistic), else, for temperature with ``fahrenheit``, the results converted from
    Fahrenheit at a station that has no such Celsius result in the window."""
    use = {}
    if param != "temp":
        return use
    stations = w["station"][rows]
    reasons = w["reason"][rows]
    for s in np.unique(stations):
        mine = reasons[stations == s]
        celsius = (mine == 0).any() or (not screen and np.isin(mine, SCREENED).any())
        use[int(s)] = "celsius" if celsius else ("fahrenheit" if fahrenheit and (mine == FAHRENHEIT).any() else "none")
    return use


def _used(w: dict, i: int, param: str, choice: dict, screen: bool = True) -> bool:
    code = int(w["reason"][i])
    if param != "temp":
        return code == 0
    pick = choice.get(int(w["station"][i]))
    return (_celsius(code, screen) and pick == "celsius") or (code == FAHRENHEIT and pick == "fahrenheit")


def wqp_results(tables: PointTables, param: str, lat: float, lon: float, within_mi: float = 5.0,
                start: Optional[date] = None, fahrenheit: bool = True, screen: bool = True) -> list:
    """The results behind an answer: every ``param`` result at stations within ``within_mi`` since
    ``start`` (EASI's ten-year window by default), as ``{station, station_name, distance_mi, date,
    value, reason, used, result_id}``; ``value`` is in mg/L or degrees C for results EASI keeps, for
    results reported in Fahrenheit (converted) and for Celsius results set apart as unrealistic, None
    otherwise, ``used`` whether ``wqp_easi`` counts it, and ``result_id`` the WQP result identifier."""
    if param not in EASI_PARAMS:
        return []
    w, rows = _wqp_rows(tables, param, lat, lon, within_mi, start or _ten_years_before(date.today()))
    choice = _station_choice(w, rows, param, fahrenheit, screen)
    names = TEMP_REASONS if param == "temp" else WQP_REASONS
    out = []
    dist: dict = {}
    for i in rows:
        s = int(w["station"][i])
        if s not in dist:
            dist[s] = round(_distance(lat, lon, float(w["lat"][s]), float(w["lon"][s]), EARTH_MI), 3)
        code = int(w["reason"][i])
        has_value = code == 0 or (param == "temp" and (code == FAHRENHEIT or code in SCREENED))
        value = _easi_value(w, int(i), param) if has_value else None
        out.append({"station": w["ids"][s], "station_name": w["names"][s], "distance_mi": dist[s],
                    "date": str(w["date"][i]), "value": value, "reason": names[code],
                    "used": _used(w, int(i), param, choice, screen), "result_id": result_id(w, int(i))})
    return out


def wqp_easi(tables: PointTables, param: str, lat: float, lon: float, within_mi: float = 5.0,
             start: Optional[date] = None, details: bool = False, fahrenheit: bool = True,
             screen: bool = True) -> Optional[dict]:
    """EASI's ``sample_summary`` for ``tn``, ``tp`` or ``temp`` from the bundled results. For
    temperature, ``fahrenheit`` (the default) also counts results reported in Fahrenheit, converted,
    at stations with no Celsius result in the window (``fahrenheit_converted`` says how many), and
    ``screen`` (the default) leaves out Celsius results that are not realistic stream temperatures
    (outside -1 to 40 C, or out of season for the area; counted as ``excluded["implausible"]``, with
    Fahrenheit conversions outside -1 to 40 C). ``fahrenheit=False, screen=False`` is EASI's own
    rule, which counts every Celsius number and puts Fahrenheit results under unsupported units.
    ``details`` adds ``results`` (``wqp_results``), so the answer carries the results it was computed
    from."""
    if param not in EASI_PARAMS:
        return None
    start = start or _ten_years_before(date.today())
    w, rows = _wqp_rows(tables, param, lat, lon, within_mi, start)
    choice = _station_choice(w, rows, param, fahrenheit, screen)
    excluded = dict((r, 0) for r in ("blank", "nonnumeric", "unsupported_unit", "non_total_fraction",
                                     "rejected", "censored"))
    if param == "temp":
        excluded["implausible"] = 0
    by_station: dict = {}
    distances: dict = {}
    dates = []
    converted = 0
    for i in rows:                                       # sorted by station, then date
        code = int(w["reason"][i])
        if not _used(w, int(i), param, choice, screen):
            name = (TEMP_REASONS if param == "temp" else WQP_REASONS)[code]
            if param == "temp" and (code in SCREENED or (code == FAHRENHEIT_IMPLAUSIBLE and fahrenheit)):
                name = "implausible"
            excluded[name if name in excluded else "unsupported_unit"] += 1
            continue
        converted += int(code == FAHRENHEIT) if param == "temp" else 0
        value = _easi_value(w, int(i), param)
        s = int(w["station"][i])
        key = w["ids"][s]
        by_station.setdefault(key, []).append(value)
        dates.append(w["date"][i])
        if key not in distances:
            distances[key] = _distance(lat, lon, float(w["lat"][s]), float(w["lon"][s]), EARTH_MI)
    station_medians = dict((k, median(v)) for k, v in by_station.items())
    value = median(station_medians.values()) if station_medians else None
    out = {
        "parameter": param,
        "value": None if value is None else round(float(value), 4),
        "units": "\u00b0C" if param == "temp" else "mg/L",
        "observation_count": sum(len(v) for v in by_station.values()),
        "station_count": len(by_station),
        "date_start": str(min(dates)) if dates else None,
        "date_end": str(max(dates)) if dates else None,
        "nearest_distance_mi": round(min(distances.values()), 3) if distances else None,
        "excluded_count": sum(excluded.values()),
        "excluded": excluded,
        "station_medians": dict((k, round(float(v), 4)) for k, v in station_medians.items()),
        "query_ok": True,
    }
    if param == "temp":
        out["fahrenheit_converted"] = converted
    if details:
        out["results"] = wqp_results(tables, param, lat, lon, within_mi, start, fahrenheit, screen)
    return out


def wqp_sfari(tables: PointTables, param: str, lat: float, lon: float, within_mi: float = 5.0,
              start: date = date(2015, 1, 1)) -> Optional[float]:
    """SFARI's ``median_value`` for ``tn`` or ``tp``: every numeric result, any status, unit or fraction."""
    if param not in WQP_PARAMS:
        return None
    w, rows = _wqp_rows(tables, param, lat, lon, within_mi, start)
    vals = w["value"][rows]
    vals = vals[~np.isnan(vals)]
    return round(median(vals.tolist()), 3) if len(vals) else None


def _dam(n: dict, i: int, distance=None) -> dict:
    def num(v):
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
    out = {"name": n["name"][i], "storage": num(n["nid_storage_acft"][i]), "height": num(n["dam_height_ft"][i])}
    if distance is not None:
        out["distance_m"] = round(distance, 1)
    return out


def nid_radius(tables: PointTables, lat: float, lon: float, miles: float = 1.0) -> list[dict]:
    """Mapped NID dams within ``miles`` (geodesic), nearest first: EASI's and DEEP's ``barriers_near``."""
    n = tables.nid()
    radius_m = float(miles) * 1609.344
    cand = np.nonzero(_haversine(lat, lon, n["lat"], n["lon"], EARTH_M) <= radius_m + 1.0)[0]
    out = []
    for i in cand:
        d = _distance(lat, lon, float(n["lat"][i]), float(n["lon"][i]), EARTH_M)
        if d <= radius_m:
            out.append(_dam(n, int(i), d))
    return sorted(out, key=lambda x: (x["distance_m"], str(x.get("name") or "")))


def nid_box(tables: PointTables, lat: float, lon: float, miles: float = 1.0) -> list[dict]:
    """NID dams in SFARI's box of ``miles / 69`` degrees around the point."""
    n = tables.nid()
    dx = miles / 69.0
    inside = (n["lon"] >= lon - dx) & (n["lon"] <= lon + dx) & (n["lat"] >= lat - dx) & (n["lat"] <= lat + dx)
    return [_dam(n, int(i)) for i in np.nonzero(inside)[0]]


def nwis_flow_stats(tables: PointTables, lat: float, lon: float, da_sqkm: Optional[float] = None) -> Optional[dict]:
    """SFARI's ``flow_stats``: gages in the 0.25 degree box ranked by drainage-area ratio and distance,
    the first of the top five with 60 or more daily values."""
    g = tables.nwis()
    w, s, e, n_ = (float(f"{v:.5f}") for v in (lon - 0.25, lat - 0.25, lon + 0.25, lat + 0.25))
    inside = np.nonzero((g["lon"] >= w) & (g["lon"] <= e) & (g["lat"] >= s) & (g["lat"] <= n_))[0]
    da_sqmi = da_sqkm * SQMI_PER_SQKM if da_sqkm else None

    def key(i):
        dist = ((g["lat"][i] - lat) ** 2 + (g["lon"][i] - lon) ** 2) ** 0.5
        gda = g["da_sqmi"][i]
        if da_sqmi and gda and not math.isnan(gda):
            ratio = gda / da_sqmi
            if ratio < 0.3 or ratio > 3.5:
                return (2, dist)
            return (0, abs(1.0 - ratio) + dist * 5)
        return (1, dist)
    for i in sorted(inside.tolist(), key=key)[:5]:
        if (g["n_days"][i] or 0) < 60:
            continue
        gda = g["da_sqmi"][i]
        ratio = g["baseflow_ratio"][i]
        return {"site": g["site"][i], "name": g["name"][i], "da_sqmi": None if math.isnan(gda) else float(gda),
                "n_days": int(g["n_days"][i]), "zero_frac": float(g["zero_frac"][i]), "q10": float(g["q10"][i]),
                "q50": float(g["q50"][i]), "q90": float(g["q90"][i]),
                "baseflow_ratio": None if ratio is None or math.isnan(ratio) else float(ratio),
                "as_of": g["as_of"][i]}
    return None


def nas_established(tables: PointTables, huc12: Optional[str] = None, huc8: Optional[str] = None) -> Optional[list]:
    """EASI's ``established_taxa``: sorted scientific names of established records in the HUC12, or in
    the HUC8 when there is no HUC12; None without either."""
    t = tables.nas()
    if huc12:
        sel = t["huc12"] == str(huc12)
    elif huc8:
        sel = t["huc8"] == str(huc8)
    else:
        return None
    sel &= t["status"] == "established"
    return sorted(set(n for n in t["scientificName"][sel].tolist() if n))
