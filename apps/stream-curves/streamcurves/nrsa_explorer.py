"""Pure logic for the NRSA explorer: filtering stations and describing one.

Kept out of the view module so the suite can reach it. The view cannot be tested
directly, because it lives behind ``@module.server``.

The explorer is read-only. It never writes to the session and never influences a
run, so browsing it cannot perturb a result or its provenance.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

import pandas as pd

from . import metric_names
from .nrsa_dataset import CYCLES_NEWEST_FIRST, CYCLE_LABELS

# one colour per set of cycles a station was sampled in, so the map shows at a
# glance which places are new, which repeat, and which only exist in the archive
CYCLE_SET_COLORS = {
    "1314": "#b07aa1",              # 2013-14 only
    "1819": "#4e79a7",              # 2018-19 only, what the app has today
    "2324": "#59a14f",              # new in 2023-24
    "1314,1819": "#7c6ea8",
    "1819,2324": "#4f9d8a",
    "1314,2324": "#8a9a5b",
    "1314,1819,2324": "#e15759",    # the eleven sampled in all three
}
UNKNOWN_COLOR = "#9aa5b1"


def cycle_set_color(cycles_sampled: object) -> str:
    key = ",".join(sorted(str(cycles_sampled or "").split(","))) if cycles_sampled else ""
    return CYCLE_SET_COLORS.get(key, UNKNOWN_COLOR)


def cycle_set_label(cycles_sampled: object) -> str:
    parts = [p for p in str(cycles_sampled or "").split(",") if p]
    if not parts:
        return "unknown"
    return ", ".join(CYCLE_LABELS.get(p, p) for p in sorted(parts))


def ecoregion_choices(stations: pd.DataFrame) -> dict[str, str]:
    """``{us_l3code: "71 Interior Plateau (64)"}``, in Level III code order.

    Sorted on the code parsed as a number, not on the string: the column is
    free text and every code in the archive is a numeric string from 1 to 85,
    so a plain string sort reads 1, 10, 11, 12 ... 2, 20. A code that is not a
    number sorts to the end rather than to the front.
    """
    if stations is None or stations.empty:
        return {}
    counts = stations.groupby(["us_l3code", "us_l3name"]).size().reset_index(name="n")
    counts["_code_num"] = pd.to_numeric(counts["us_l3code"], errors="coerce")
    counts = counts.sort_values(["_code_num", "us_l3code"], na_position="last")
    out: dict[str, str] = {}
    for row in counts.itertuples(index=False):
        code = str(row.us_l3code).strip()
        if not code or code.lower() == "nan":
            continue
        out[code] = f"{code} {row.us_l3name} ({row.n})"
    return out


def filter_stations(
    stations: pd.DataFrame,
    *,
    ecoregion: Optional[str] = None,
    cycles: Optional[Sequence[str]] = None,
    match: str = "any",
    state: Optional[str] = None,
) -> pd.DataFrame:
    """Stations matching the filters.

    ``match="any"`` keeps a station sampled in at least one of ``cycles``;
    ``match="all"`` keeps only stations sampled in every one of them, which is
    how you find the places with a real time series.
    """
    if stations is None or stations.empty:
        return stations if stations is not None else pd.DataFrame()
    out = stations
    if ecoregion:
        out = out[out["us_l3code"].astype(str).str.strip() == str(ecoregion).strip()]
    if state:
        out = out[out["state"].astype(str).str.strip().str.lower() == str(state).strip().lower()]
    wanted = {str(c) for c in (cycles or []) if c}
    # selecting every cycle is a no-op for "any", but for "all" it is the whole
    # point of the filter: keep only the stations with a real time series
    redundant = match == "any" and wanted == set(CYCLES_NEWEST_FIRST)
    if wanted and not redundant:
        sampled = out["cycles_sampled"].astype(str).str.split(",")
        if match == "all":
            keep = sampled.map(lambda parts: wanted <= set(parts))
        else:
            keep = sampled.map(lambda parts: bool(wanted & set(parts)))
        out = out[keep]
    return out.reset_index(drop=True)


def station_geojson(stations: pd.DataFrame) -> dict:
    """A FeatureCollection the map can draw in one layer.

    One layer rather than 4,378 markers: ipyleaflet renders per-marker updates
    one at a time and a marker-per-station map is unusably slow.
    """
    features = []
    if stations is None or stations.empty:
        return {"type": "FeatureCollection", "features": features}
    for row in stations.itertuples(index=False):
        lat, lon = row.lat, row.lon
        if pd.isna(lat) or pd.isna(lon):
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
            "properties": {
                "station_key": str(row.station_key),
                "cycles": str(row.cycles_sampled),
                "cycle_label": cycle_set_label(row.cycles_sampled),
                "n_cycles": int(row.n_cycles),
                "n_visits": int(row.n_visits),
                "us_l3code": str(row.us_l3code),
                "us_l3name": str(row.us_l3name),
                "state": str(row.state),
                "style": {"color": cycle_set_color(row.cycles_sampled),
                          "fillColor": cycle_set_color(row.cycles_sampled)},
            },
        })
    return {"type": "FeatureCollection", "features": features}


def legend_rows(stations: pd.DataFrame) -> list[dict]:
    """One row per cycle set present, with its colour and count."""
    if stations is None or stations.empty:
        return []
    counts = stations["cycles_sampled"].astype(str).value_counts()
    rows = []
    for key, n in counts.items():
        rows.append({"cycles": key, "label": cycle_set_label(key),
                     "color": cycle_set_color(key), "n": int(n)})
    return sorted(rows, key=lambda r: (-r["n"], r["cycles"]))


# the panel's category order: what a crew measured on the visit first, the
# watershed attributes that describe the place last
CATEGORY_ORDER = [
    "Water chemistry", "Physical habitat",
    "Benthic macroinvertebrates", "Fish", "Landscape",
]
OTHER_CATEGORY = "Other"

# Landscape values are watershed attributes keyed to the flowline, not something
# a crew measured on a visit, so they are identical in every cycle a station was
# sampled in (checked across the archive: 100% identical, against 27% for
# physical habitat and 12% for chemistry). Showing them per cycle would repeat
# one number across every column and read as a time series that does not exist.
STATIC_CATEGORIES = {"Landscape"}

_NON_METRIC_COLUMNS = ("station_key", "cycle", "site_id", "visit_no")


def _station_values(values: Optional[pd.DataFrame], station_key: str) -> pd.DataFrame:
    """The value rows for one station, one per cycle, or an empty frame."""
    if values is None or values.empty:
        return pd.DataFrame()
    return values[values["station_key"].astype(str) == str(station_key)]


def _values_by_metric(mine: pd.DataFrame) -> dict[str, dict[str, float]]:
    """``{metric: {cycle: value}}`` for one station, non-null values only.

    One pass over the frame, because the shape that reads naturally is a trap:
    calling ``mine.itertuples()`` inside a loop over metric columns rebuilds a
    namedtuple carrying one field per column on every single column. On the
    792-column values table that is ~47 ms per call paid 788 times, which took
    **40 seconds** for one station and blocked the whole event loop with it.

    ``GroupBy.first()`` takes the first non-null value per cycle in row order,
    which is exactly what the per-row ``setdefault`` it replaced did, so a
    station with several visits in one cycle reports the same value as before.
    """
    if mine is None or mine.empty:
        return {}
    metrics = [c for c in mine.columns if c not in _NON_METRIC_COLUMNS]
    if not metrics:
        return {}
    per_cycle = mine[metrics].groupby(
        mine["cycle"].astype(str).to_numpy(), sort=False).first()
    return {
        metric: {cycle: float(value) for cycle, value in row.items()
                 if value is not None and not pd.isna(value)}
        for metric, row in per_cycle.T.to_dict("index").items()
    }


def _category_rank(category: str) -> tuple[int, str]:
    try:
        return (CATEGORY_ORDER.index(category), "")
    except ValueError:
        return (len(CATEGORY_ORDER), category)


def station_metric_groups(
    station_key: str,
    *,
    values: Optional[pd.DataFrame] = None,
    search: Optional[str] = None,
) -> list[dict]:
    """Every metric that has a value for this station, grouped by category.

    A station carries a median of about 525 of the 788 metric columns, so the
    panel needs them grouped and countable rather than in one flat table. A
    metric with no value in any cycle is dropped, and ``n`` counts what is
    actually returned, so a section header reports what opening it will show.

    ``search`` matches the display name or the metric code, case-insensitively,
    and is applied before the counts so a filtered header stays honest.
    """
    mine = _station_values(values, station_key)
    if mine.empty:
        return []

    needle = str(search or "").strip().lower()
    by_metric = _values_by_metric(mine)
    grouped: dict[str, list[dict]] = {}
    for metric in mine.columns:
        if metric in _NON_METRIC_COLUMNS:
            continue
        # the full name, not short_name_for: that one truncates at ~34 chars for
        # tile headers, which turns rows here into "Log10[Erodible Substr..."
        name = metric_names.display_name_for(metric, metric)
        if needle and needle not in str(name).lower() and needle not in metric.lower():
            continue
        category = metric_names.category_for(metric, OTHER_CATEGORY) or OTHER_CATEGORY
        static = category in STATIC_CATEGORIES

        per_cycle = by_metric.get(metric) or {}
        if not per_cycle:
            continue

        grouped.setdefault(category, []).append({
            "metric": metric,
            "name": name,
            "units": metric_names.units_for(metric, "") or "",
            "description": metric_names.description_for(metric, "") or "",
            "by_cycle": {} if static else per_cycle,
            "value": next(iter(per_cycle.values())) if static else None,
        })

    out = []
    for category in sorted(grouped, key=_category_rank):
        rows = sorted(grouped[category], key=lambda r: str(r["name"]).lower())
        out.append({
            "category": category,
            "n": len(rows),
            "varies_by_cycle": category not in STATIC_CATEGORIES,
            "metrics": rows,
        })
    return out


def station_detail(
    station_key: str,
    *,
    stations: pd.DataFrame,
    visits: pd.DataFrame,
    values: Optional[pd.DataFrame] = None,
    metrics: Iterable[str] = (),
) -> dict:
    """Everything the panel shows for one station: its identity, its visits, and
    the value of a few metrics in each cycle."""
    station_key = str(station_key)
    hit = stations[stations["station_key"].astype(str) == station_key]
    if hit.empty:
        return {}
    row = hit.iloc[0]
    my_visits = visits[visits["station_key"].astype(str) == station_key].copy()
    my_visits = my_visits.sort_values(["cycle", "visit_no"])

    visit_rows = [{
        "cycle": str(v.cycle),
        "cycle_label": CYCLE_LABELS.get(str(v.cycle), str(v.cycle)),
        "site_id": str(v.site_id),
        "visit_no": str(v.visit_no),
        "year": "" if pd.isna(v.year) else str(int(float(v.year))),
        "date": "" if pd.isna(v.date_col) else str(v.date_col),
    } for v in my_visits.itertuples(index=False)]

    metric_rows = []
    wanted = [m for m in metrics if m]
    mine = _station_values(values, station_key)
    if wanted and not mine.empty:
        by_metric = _values_by_metric(mine)
        for metric in wanted:
            if metric not in mine.columns:
                continue
            # unlike the groups path, a requested metric with no values still
            # gets a row, with an empty by_cycle
            per_cycle = by_metric.get(metric) or {}
            metric_rows.append({
                "metric": metric,
                "name": metric_names.short_name_for(metric, metric),
                "units": metric_names.units_for(metric, "") or "",
                "by_cycle": per_cycle,
            })

    return {
        "station_key": station_key,
        "cycles": str(row.cycles_sampled),
        "cycle_label": cycle_set_label(row.cycles_sampled),
        "n_cycles": int(row.n_cycles),
        "n_visits": int(row.n_visits),
        "lat": None if pd.isna(row.lat) else float(row.lat),
        "lon": None if pd.isna(row.lon) else float(row.lon),
        "comid": None if pd.isna(row.comid) else int(row.comid),
        "us_l3code": str(row.us_l3code),
        "us_l3name": str(row.us_l3name),
        "state": str(row.state),
        "visits": visit_rows,
        "metrics": metric_rows,
    }


def coverage_summary(stations: pd.DataFrame) -> dict:
    """The counts the header line reports."""
    if stations is None or stations.empty:
        return {"stations": 0, "with_comid": 0, "multi_cycle": 0, "ecoregions": 0}
    return {
        "stations": int(len(stations)),
        "with_comid": int(stations["comid"].notna().sum()),
        "multi_cycle": int((stations["n_cycles"] > 1).sum()),
        "ecoregions": int(stations["us_l3code"].astype(str).nunique()),
    }
