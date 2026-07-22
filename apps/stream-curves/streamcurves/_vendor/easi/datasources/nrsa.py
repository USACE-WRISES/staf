"""Bundled NRSA 2018-19 reach evidence with explicit network matching.

An exact COMID is preferred.  Otherwise a site must be both within five miles
and on the NLDI upstream/downstream mainstem returned for the selected COMID.
Spatial proximity, stream name, or shared HUC alone never establish a match.
"""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
import gzip
import json
import math
from pathlib import Path
from typing import Any


DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "nrsa-2018-19-evidence.json.gz"
EARTH_RADIUS_MI = 3958.7613


@lru_cache(maxsize=1)
def _records() -> tuple[dict[str, Any], ...]:
    try:
        with gzip.open(DATA_PATH, "rt", encoding="utf-8") as handle:
            return tuple(json.load(handle).get("records") or [])
    except (OSError, ValueError, TypeError):
        return tuple()


def _distance_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(a))


def _sample_date(record: dict) -> date | None:
    try:
        return datetime.strptime(str(record.get("date")), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _within_age(record: dict, as_of: date, max_age_years: int) -> bool:
    sampled = _sample_date(record)
    if sampled is None or sampled > as_of:
        return False
    cutoff = date(as_of.year - max_age_years, as_of.month,
                  min(as_of.day, 28) if as_of.month == 2 else as_of.day)
    return sampled >= cutoff


def _connected_comids(comid: int, distance_km: float) -> set[int] | None:
    """Return verified upstream/downstream-main COMIDs; ``None`` means NLDI failed."""
    try:
        from pynhd import NLDI
        connected = {int(comid)}
        nldi = NLDI()
        for navigation in ("upstreamMain", "downstreamMain"):
            frame = nldi.navigate_byid(
                fsource="comid", fid=str(comid), navigation=navigation,
                source="flowlines", distance=max(1.0, float(distance_km)))
            column = "nhdplus_comid" if "nhdplus_comid" in frame.columns else "comid"
            if column in frame.columns:
                connected.update(int(value) for value in frame[column].dropna())
        return connected
    except Exception:  # noqa: BLE001 - connectivity failure must degrade explicitly
        return None


def _evidence_count(record: dict) -> int:
    return sum(record.get(key) is not None for key in
               ("wettedPct", "embeddednessPct", "benthicClass", "fishClass"))


def evidence_for_reach(comid: int | None, lat: float, lon: float, *,
                       max_distance_mi: float = 5.0, max_age_years: int = 10,
                       as_of: date | None = None) -> dict | None:
    """Return the strongest eligible NRSA record for a selected reach.

    The returned record includes ``matchType`` (``exact`` or
    ``connected_nearby``), geodesic distance, source confidence, and a warning
    for non-exact evidence.  A connectivity-service failure never falls back to
    a proximity-only match.
    """
    if comid is None:
        return None
    current = as_of or date.today()
    eligible = [record for record in _records()
                if _within_age(record, current, max_age_years)]
    exact = [record for record in eligible if int(record.get("comid") or -1) == int(comid)]
    if exact:
        chosen = max(exact, key=lambda item: (_evidence_count(item), item.get("date", "")))
        return {
            **chosen, "matchType": "exact", "distanceMi": 0.0,
            "confidence": "M", "warning": "",
        }

    nearby = []
    for record in eligible:
        try:
            distance = _distance_mi(lat, lon, float(record["lat"]), float(record["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if distance <= max_distance_mi:
            nearby.append((distance, record))
    if not nearby:
        return None
    connected = _connected_comids(int(comid), max_distance_mi * 1.609344)
    if connected is None:
        return None
    candidates = [(distance, record) for distance, record in nearby
                  if int(record.get("comid") or -1) in connected]
    if not candidates:
        return None
    distance, chosen = min(
        candidates,
        key=lambda item: (item[0], -_evidence_count(item[1]), item[1].get("date", "")))
    return {
        **chosen,
        "matchType": "connected_nearby",
        "distanceMi": round(distance, 3),
        "confidence": "M/L",
        "warning": "Connected nearby NRSA site; not necessarily this reach.",
    }


def clear_cache() -> None:
    """Test/development hook after rebuilding the bundled asset."""
    _records.cache_clear()
