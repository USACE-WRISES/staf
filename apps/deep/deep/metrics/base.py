"""Shared analysis context for DEEP's desktop metric adapters (Phase 3).

Mirrors EASI's ``AnalysisContext`` — the delineation-derived inputs every adapter
needs, plus an ``extras`` cache for data pulled once per run (StreamCat row, NLCD
land cover, the 3DEP reach geomorphology).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AnalysisContext:
    lat: Optional[float] = None
    lon: Optional[float] = None
    comid: Optional[int] = None
    huc8: Optional[str] = None
    huc12: Optional[str] = None
    watershed_geojson: Optional[dict] = None
    reach_geojson: Optional[dict] = None
    drainage_area_sqkm: Optional[float] = None
    slope: Optional[float] = None
    fcode: Optional[int] = None
    stream_order: Optional[int] = None
    sinuosity: Optional[float] = None
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_inputs(cls, ci: Optional[dict]) -> "AnalysisContext":
        ci = ci or {}
        return cls(
            lat=ci.get("lat"), lon=ci.get("lon"), comid=ci.get("comid"),
            huc8=ci.get("huc8"), huc12=ci.get("huc12"),
            watershed_geojson=ci.get("watershed_geojson"),
            reach_geojson=ci.get("reach_geojson"),
            drainage_area_sqkm=ci.get("drainage_area_sqkm"),
            slope=ci.get("slope"), fcode=ci.get("fcode"),
            stream_order=ci.get("stream_order"), sinuosity=ci.get("sinuosity"),
        )
