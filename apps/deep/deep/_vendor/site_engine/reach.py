"""Assessment reach on the HR mainstem, trimmed upstream of the site point.

Walks ``uphydroseq`` (the VAA's deterministic upstream-mainstem pointer) from
the anchor reach until enough mainstem is collected, then trims with the
shared merge/orient/trim math. Same derivation the EASI Phase 2 re-anchor
uses, so the two produce the same reach for the same anchor. Never raises.
"""
from __future__ import annotations

from typing import Optional

from . import hr
from .geometry import FT_PER_M, reach_from_lines

_HOP_CAP = 25  # deterministic upper bound on the mainstem walk


def derive_reach(anchor: dict, lat: float, lon: float, length_ft: float
                 ) -> tuple[Optional[dict], Optional[float], list[str]]:
    """``(reach_geojson, actual_ft, warnings)`` for one parsed HR reach."""
    from shapely.geometry import shape

    warnings: list[str] = []
    try:
        if not anchor or not anchor.get("geometry"):
            return None, None, ["no HR flowline geometry for the reach"]
        length_km = (length_ft / FT_PER_M) / 1000.0
        own_len_km = anchor.get("lengthkm") or 0.0
        needed_km = round(max(length_km * 4, own_len_km + length_km) + 0.3, 1)
        geoms = [shape(anchor["geometry"])]
        total_km = own_len_km
        up = anchor.get("uphydroseq")
        for _ in range(_HOP_CAP):
            if total_km >= needed_km or not up:
                break
            nxt = hr.feature_by_hydroseq(up)
            if nxt is None or not nxt.get("geometry"):
                break
            geoms.append(shape(nxt["geometry"]))
            total_km += nxt.get("lengthkm") or 0.0
            up = nxt.get("uphydroseq")
        return reach_from_lines(geoms, [geoms[0]], lat, lon, length_ft,
                                warnings)
    except Exception as exc:  # noqa: BLE001 - resilience by design
        return None, None, [f"reach derivation failed: {exc}"]
