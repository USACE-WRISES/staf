"""EPA/USGS Water Quality Portal (WQP) — observed water chemistry near a point.

Opportunistic source: most random reaches have no nearby samples, so callers
must handle None gracefully. Returns the median of recent results for a set of
characteristic-name synonyms. Never raises — returns None on failure/no data.
"""
from __future__ import annotations

import csv
import io
from statistics import median
from typing import Optional

import requests

_RESULT = "https://www.waterqualitydata.us/data/Result/search"
_VALUE_COL = "ResultMeasureValue"

# characteristicName synonyms per parameter (WQP names are fragmented)
SYNONYMS = {
    "tn": ["Total Nitrogen, mixed forms", "Nitrogen",
           "Total Nitrogen, mixed forms (NH3), (NH4), organic, (NO2) and (NO3)"],
    "tp": ["Total Phosphorus, mixed forms", "Phosphorus"],
    "temp": ["Temperature, water"],
}


def _fetch(characteristics: list[str], lat: float, lon: float, within_mi: float,
           start: str, timeout: float) -> Optional[list[float]]:
    params = [("lat", f"{lat}"), ("long", f"{lon}"), ("within", f"{within_mi}"),
              ("startDateLo", start), ("mimeType", "csv"), ("dataProfile", "resultPhysChem"),
              ("siteType", "Stream")]
    for c in characteristics:
        params.append(("characteristicName", c))
    try:
        r = requests.get(_RESULT, params=params, timeout=timeout)
        if r.status_code != 200 or not r.text:
            return None
        # Parse with the csv module, NOT a naive split: WQP rows carry quoted
        # free-text fields containing commas (station names, comments). A bare
        # line.split(",") shifts every column past the first embedded comma, so
        # ResultMeasureValue reads as blank — this silently dropped all
        # temperature data while sparser nutrient rows happened to align.
        reader = csv.DictReader(io.StringIO(r.text))
        field = _value_field(reader.fieldnames)
        if field is None:
            return None
        vals: list[float] = []
        for row in reader:
            raw = (row.get(field) or "").strip()  # blank when not measured/censored
            if not raw:
                continue
            try:
                vals.append(float(raw))
            except ValueError:
                continue
        return vals or None
    except Exception:  # noqa: BLE001
        return None


def _value_field(fieldnames: Optional[list[str]]) -> Optional[str]:
    """Resolve the ResultMeasureValue column from the CSV header (exact, else substring)."""
    if not fieldnames:
        return None
    if _VALUE_COL in fieldnames:
        return _VALUE_COL
    return next((f for f in fieldnames if f and _VALUE_COL in f), None)


def median_value(param: str, lat: float, lon: float, within_mi: float = 5.0,
                 start: str = "01-01-2015", timeout: float = 10.0) -> Optional[float]:
    """Median observed value for 'tn'|'tp'|'temp' near the point, or None.

    ``timeout`` is deliberately short (fail-fast): WQP is an opportunistic source and
    this runs in an interactive screening flow, so a slow/overloaded portal should fall
    back to the metric's surrogate quickly rather than stall the report for tens of
    seconds. Raise it if you need to catch data in dense-monitoring regions.
    """
    chars = SYNONYMS.get(param)
    if not chars:
        return None
    vals = _fetch(chars, lat, lon, within_mi, start, timeout)
    if not vals:
        return None
    return round(median(vals), 3)
