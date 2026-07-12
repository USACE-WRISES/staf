"""Pure helpers for the EASI batch workspace UI (parsing + result shaping).

Kept UI-free and side-effect-free so it is unit-testable; ``app.py`` does the
Shiny wiring. Parsing accepts pasted or uploaded CSV / TSV / whitespace tables.
"""
from __future__ import annotations

import csv
import io
from typing import Optional

from .batch.api import MAX_SITES

_ID_KEYS = {"id", "site_id", "siteid", "site", "name", "label"}
_LAT_KEYS = {"lat", "latitude", "y"}
_LON_KEYS = {"lon", "long", "lng", "longitude", "x"}
_COMID_KEYS = {"comid", "com_id", "nhd_comid"}


def _sniff_delimiter(text: str) -> str:
    head = text.strip().splitlines()[0] if text.strip() else ""
    if "\t" in head:
        return "\t"
    if "," in head:
        return ","
    return None  # whitespace-delimited


def _rows(text: str) -> list[list[str]]:
    delim = _sniff_delimiter(text)
    out: list[list[str]] = []
    if delim is None:
        for line in text.splitlines():
            if line.strip():
                out.append(line.split())
    else:
        for row in csv.reader(io.StringIO(text), delimiter=delim):
            if any(c.strip() for c in row):
                out.append([c.strip() for c in row])
    return out


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _header_map(header: list[str]) -> Optional[dict]:
    """Map a header row to column indices, or None if it is not a header."""
    low = [h.strip().lower() for h in header]
    idx: dict[str, int] = {}
    for i, h in enumerate(low):
        if h in _LAT_KEYS and "lat" not in idx:
            idx["lat"] = i
        elif h in _LON_KEYS and "lon" not in idx:
            idx["lon"] = i
        elif h in _ID_KEYS and "id" not in idx:
            idx["id"] = i
        elif h in _COMID_KEYS and "comid" not in idx:
            idx["comid"] = i
    if "lat" in idx and "lon" in idx:
        return idx
    return None


def parse_sites_text(text: str) -> tuple[list[dict], list[str]]:
    """Parse pasted/loaded site rows into ``[{site_id,lat,lon,comid?,metadata}]``.

    Accepts CSV, TSV, or whitespace tables, with or without a header. Without a
    header, columns are inferred: 3+ cols = id, lat, lon; 2 cols = lat, lon (ids
    auto-generated later). Returns ``(sites, errors)``; bad rows become errors and
    do not abort the parse. Coordinates are validated to CONUS-plausible ranges.
    """
    rows = _rows(text or "")
    if not rows:
        return [], []
    errors: list[str] = []
    sites: list[dict] = []

    hmap = _header_map(rows[0])
    if hmap is not None:
        data_rows, cols = rows[1:], hmap
    else:
        # infer positional columns from the first data row width
        width = len(rows[0])
        if width >= 3:
            cols = {"id": 0, "lat": 1, "lon": 2}
            if width >= 4 and _is_number(rows[0][3]):
                cols["comid"] = 3
        elif width == 2:
            cols = {"lat": 0, "lon": 1}
        else:
            return [], [f"could not parse columns from a {width}-column table"]
        data_rows = rows

    for n, row in enumerate(data_rows, 1):
        try:
            lat = float(row[cols["lat"]])
            lon = float(row[cols["lon"]])
        except (IndexError, ValueError):
            errors.append(f"row {n}: could not read lat/lon")
            continue
        if not (17.0 <= lat <= 72.0) or not (-180.0 <= lon <= -64.0):
            errors.append(f"row {n}: coordinates ({lat}, {lon}) out of CONUS range")
            continue
        site: dict = {"site_id": (row[cols["id"]].strip() if "id" in cols
                                  and cols["id"] < len(row) else ""),
                      "lat": lat, "lon": lon}
        if "comid" in cols and cols["comid"] < len(row) and row[cols["comid"]].strip():
            try:
                site["comid"] = int(float(row[cols["comid"]]))
            except ValueError:
                pass
        sites.append(site)

    if len(sites) > MAX_SITES:
        errors.append(f"{len(sites)} sites exceeds the {MAX_SITES}-site limit; "
                      f"only the first {MAX_SITES} will run")
    return sites, errors


def result_summary(batch: dict) -> dict:
    """Headline counts for the Results header."""
    sites = batch.get("sites", [])
    diag = batch.get("diagnostics", {})
    retained = sum(1 for s in sites
                   if s.get("qualification", {}).get("final") == "retained")
    return {
        "total": len(sites),
        "succeeded": diag.get("succeeded", 0),
        "partial": diag.get("partial", 0),
        "failed": diag.get("failed", 0),
        "cancelled": diag.get("cancelled", 0),
        "qualified": diag.get("qualified", 0),
        "retained": retained,
        "elapsed_s": diag.get("elapsed_s"),
    }
