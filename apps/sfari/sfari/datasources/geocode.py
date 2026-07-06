"""Place / address / stream-name -> lat/lon for the map search box.

Uses OSM-based geocoders that resolve **place names, street addresses, and
natural features (streams/rivers)** — unlike the US Census street-address
geocoder used previously, which returned nothing for "Atlanta, GA" or
"Utoy Creek". Photon (Komoot) is tried first (also powers the client-side
type-ahead in ``www/geocode-autocomplete.js``); Nominatim is the fallback.
CONUS only. Never raises — returns None on failure / no match.

Data © OpenStreetMap contributors (via Photon / Nominatim).
"""
from __future__ import annotations

from typing import Optional

import requests

_UA = {"User-Agent": "EASI-stream-screening/1.0 (https://github.com/; CONUS screening tool)"}
_PHOTON = "https://photon.komoot.io/api/"
_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_CONUS = (39.5, -98.35)  # bias results toward the lower-48


def _photon(q: str, timeout: float) -> Optional[tuple[float, float]]:
    r = requests.get(_PHOTON, headers=_UA, timeout=timeout, params={
        "q": q, "limit": 5, "lang": "en", "lat": _CONUS[0], "lon": _CONUS[1]})
    if r.status_code != 200:
        return None
    for feat in r.json().get("features", []):
        props = feat.get("properties", {})
        if props.get("countrycode") == "US":          # keep results in CONUS scope
            lon, lat = feat["geometry"]["coordinates"][:2]
            return float(lat), float(lon)
    return None


def _nominatim(q: str, timeout: float) -> Optional[tuple[float, float]]:
    r = requests.get(_NOMINATIM, headers=_UA, timeout=timeout, params={
        "q": q, "format": "jsonv2", "limit": 1, "countrycodes": "us"})
    if r.status_code != 200:
        return None
    matches = r.json()
    if not matches:
        return None
    return float(matches[0]["lat"]), float(matches[0]["lon"])


def geocode_address(address: str, timeout: float = 15.0) -> Optional[tuple[float, float]]:
    """Return (lat, lon) for a US place / address / stream name, or None.

    Tries Photon, then Nominatim; both OSM-based so place names and waterways
    resolve. Used by the "Find on map" button (the as-you-type dropdown queries
    Photon directly from the browser).
    """
    if not address or not address.strip():
        return None
    q = address.strip()
    for fn in (_photon, _nominatim):
        try:
            hit = fn(q, timeout)
        except Exception:  # noqa: BLE001 - try the next provider / give up
            hit = None
        if hit:
            return hit
    return None
