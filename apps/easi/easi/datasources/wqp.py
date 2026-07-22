"""EPA/USGS Water Quality Portal observations near an EASI reach.

The nutrient path applies a reproducible normalization contract: total-fraction
results only, mg/L preservation, µg/L conversion, explicit exclusion counts,
station medians followed by the median of station medians, and spatial/temporal
coverage metadata.  Query failure remains distinguishable from a successful
query with no qualifying observations.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
import io
import math
from statistics import median
from typing import Optional

import requests

_RESULT = "https://www.waterqualitydata.us/data/Result/search"

SYNONYMS = {
    "tn": [
        "Total Nitrogen, mixed forms",
        "Nitrogen",
        "Total Nitrogen, mixed forms (NH3), (NH4), organic, (NO2) and (NO3)",
    ],
    "tp": ["Total Phosphorus, mixed forms", "Phosphorus"],
    "temp": ["Temperature, water"],
}


def _field(fieldnames: list[str] | None, *candidates: str) -> str | None:
    if not fieldnames:
        return None
    lower = {str(name).lower(): name for name in fieldnames if name}
    for candidate in candidates:
        if candidate.lower() in lower:
            return lower[candidate.lower()]
    for candidate in candidates:
        needle = candidate.lower()
        hit = next((name for name in fieldnames if name and needle in name.lower()), None)
        if hit:
            return hit
    return None


def _start_10_years() -> str:
    today = date.today()
    try:
        start = today.replace(year=today.year - 10)
    except ValueError:  # leap day
        start = today.replace(year=today.year - 10, day=28)
    return start.strftime("%m-%d-%Y")


def _distance_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_mi = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius_mi * math.asin(min(1.0, math.sqrt(a)))


def _date(raw: str) -> date | None:
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _unit_factor(raw: str) -> float | None:
    unit = (raw or "").strip().lower().replace("μ", "µ").replace(" ", "")
    if unit in {"mg/l", "mgperl", "milligrams/liter", "milligram/liter"}:
        return 1.0
    if unit in {"µg/l", "ug/l", "mcg/l", "micrograms/liter", "microgram/liter"}:
        return 0.001
    return None


def _valid_status(raw: str) -> bool:
    status = (raw or "").strip().lower()
    return not any(word in status for word in ("reject", "invalid", "delete", "cancel"))


def _fetch_csv(characteristics: list[str], lat: float, lon: float, within_mi: float,
               start: str, timeout: float) -> str | None:
    params = [
        ("lat", f"{lat}"), ("long", f"{lon}"), ("within", f"{within_mi}"),
        ("startDateLo", start), ("mimeType", "csv"),
        ("dataProfile", "resultPhysChem"), ("siteType", "Stream"),
    ]
    for characteristic in characteristics:
        params.append(("characteristicName", characteristic))
    try:
        response = requests.get(_RESULT, params=params, timeout=timeout)
        if response.status_code != 200:
            return None
        return response.text
    except Exception:  # noqa: BLE001 - external service degrades gracefully
        return None


def sample_summary(param: str, lat: float, lon: float, within_mi: float = 5.0,
                   start: str | None = None, timeout: float = 10.0) -> Optional[dict]:
    """Normalized station-balanced WQP summary for TN, TP, or temperature.

    Nutrients require ``ResultSampleFractionText`` containing ``total`` and a
    supported mass-concentration unit. Temperature is retained as optional
    context and accepts Celsius units without applying a rating.
    """
    characteristics = SYNONYMS.get(param)
    if not characteristics:
        return None
    text = _fetch_csv(characteristics, lat, lon, within_mi, start or _start_10_years(),
                      timeout)
    if text is None:
        return None

    reader = csv.DictReader(io.StringIO(text))
    names = reader.fieldnames or []
    value_col = _field(names, "ResultMeasureValue")
    unit_col = _field(names, "ResultMeasure/MeasureUnitCode", "MeasureUnitCode")
    fraction_col = _field(names, "ResultSampleFractionText", "SampleFractionText")
    status_col = _field(names, "ResultStatusIdentifier", "ResultStatus")
    censor_col = _field(names, "ResultDetectionConditionText", "DetectionCondition")
    station_col = _field(names, "MonitoringLocationIdentifier")
    station_name_col = _field(names, "MonitoringLocationName")
    org_col = _field(names, "OrganizationIdentifier")
    date_col = _field(names, "ActivityStartDate")
    lat_col = _field(names, "LatitudeMeasure")
    lon_col = _field(names, "LongitudeMeasure")

    excluded = {
        "blank": 0, "nonnumeric": 0, "unsupported_unit": 0,
        "non_total_fraction": 0, "rejected": 0, "censored": 0,
    }
    by_station: dict[str, list[float]] = {}
    station_distances: dict[str, float] = {}
    dates: list[date] = []

    for row in reader:
        raw = (row.get(value_col) or "").strip() if value_col else ""
        if not raw:
            excluded["blank"] += 1
            continue
        if status_col and not _valid_status(row.get(status_col) or ""):
            excluded["rejected"] += 1
            continue
        if censor_col and (row.get(censor_col) or "").strip():
            excluded["censored"] += 1
            continue
        if param in {"tn", "tp"}:
            fraction = (row.get(fraction_col) or "").strip().lower() if fraction_col else ""
            if "total" not in fraction:
                excluded["non_total_fraction"] += 1
                continue
            factor = _unit_factor(row.get(unit_col) or "") if unit_col else None
        else:
            unit = (row.get(unit_col) or "").strip().lower() if unit_col else ""
            factor = 1.0 if unit in {"deg c", "c", "°c", "degrees celsius"} else None
        if factor is None:
            excluded["unsupported_unit"] += 1
            continue
        try:
            value = float(raw) * factor
        except ValueError:
            excluded["nonnumeric"] += 1
            continue
        if not math.isfinite(value):
            excluded["nonnumeric"] += 1
            continue

        station = (row.get(station_col) or "").strip() if station_col else ""
        if not station:
            station = "|".join(filter(None, [
                (row.get(org_col) or "").strip() if org_col else "",
                (row.get(station_name_col) or "").strip() if station_name_col else "",
            ])) or "unknown-station"
        by_station.setdefault(station, []).append(value)
        observed_date = _date(row.get(date_col) or "") if date_col else None
        if observed_date:
            dates.append(observed_date)
        try:
            sample_lat = float(row.get(lat_col)) if lat_col and row.get(lat_col) else None
            sample_lon = float(row.get(lon_col)) if lon_col and row.get(lon_col) else None
        except (TypeError, ValueError):
            sample_lat = sample_lon = None
        if sample_lat is not None and sample_lon is not None:
            distance = _distance_mi(lat, lon, sample_lat, sample_lon)
            station_distances[station] = min(distance, station_distances.get(station, distance))

    station_medians = {station: median(values) for station, values in by_station.items()}
    value = median(station_medians.values()) if station_medians else None
    total_excluded = sum(excluded.values())
    return {
        "parameter": param,
        "value": None if value is None else round(float(value), 4),
        "units": "mg/L" if param in {"tn", "tp"} else "°C",
        "observation_count": sum(len(values) for values in by_station.values()),
        "station_count": len(by_station),
        "date_start": min(dates).isoformat() if dates else None,
        "date_end": max(dates).isoformat() if dates else None,
        "nearest_distance_mi": (round(min(station_distances.values()), 3)
                                if station_distances else None),
        "excluded_count": total_excluded,
        "excluded": excluded,
        "station_medians": {key: round(float(val), 4)
                            for key, val in station_medians.items()},
        "query_ok": True,
    }


def median_value(param: str, lat: float, lon: float, within_mi: float = 5.0,
                 start: str | None = None, timeout: float = 10.0) -> Optional[float]:
    """Compatibility wrapper returning only the normalized summary value."""
    summary = sample_summary(param, lat, lon, within_mi, start, timeout)
    return None if summary is None else summary.get("value")
