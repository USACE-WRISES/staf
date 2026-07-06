"""Metric adapter contract.

Every EASI metric is implemented as an async adapter that, given the analysis
context (snapped point, watershed polygon, reach geometry, outlet COMID, HUC,
drainage area), returns a ``MetricResult`` carrying the computed value, the
Good/Fair/Poor rating, the data-confidence, the source label, and whether a
user override applied. Adapters must NEVER raise to the orchestrator: on failure
they return a ``MetricResult`` with ``rating=None`` and ``status='unavailable'``
so one failed source degrades gracefully instead of aborting the report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class AnalysisContext:
    """Inputs shared by all metric adapters for one analysis run."""
    lat: float
    lon: float
    comid: Optional[int] = None
    huc8: Optional[str] = None
    huc12: Optional[str] = None
    watershed_geojson: Optional[dict] = None      # delineated basin (EPSG:4326)
    reach_geojson: Optional[dict] = None           # ~1000 ft reach (EPSG:4326)
    drainage_area_sqkm: Optional[float] = None
    # NHDPlus value-added attributes for the snapped flowline
    slope: Optional[float] = None                  # channel slope (m/m)
    fcode: Optional[int] = None                    # 46006 perennial / 46003 interm / 46007 ephem
    stream_order: Optional[int] = None
    sinuosity: Optional[float] = None
    # cached shared pulls fetched once per run, e.g. extras['streamcat'], extras['landcover']
    extras: dict[str, Any] = field(default_factory=dict)


# --- shared StreamCat accessors used by multiple adapters --------------------
def sc(ctx: "AnalysisContext") -> dict:
    return ctx.extras.get("streamcat") or {}


def riparian_forest_pct(ctx: "AnalysisContext") -> Optional[float]:
    s = sc(ctx)
    vals = [s.get("pctconif2019wsrp100"), s.get("pctdecid2019wsrp100"),
            s.get("pctmxfst2019wsrp100")]
    if all(v is None for v in vals):
        return None
    return round(sum(v or 0.0 for v in vals), 2)


def ag_pct(ctx: "AnalysisContext") -> Optional[float]:
    s = sc(ctx)
    vals = [s.get("pctcrop2019ws"), s.get("pcthay2019ws")]
    if all(v is None for v in vals):
        return None
    return round(sum(v or 0.0 for v in vals), 2)


def band(value: float, good_below: float, fair_below: float,
         higher_is_worse: bool = True) -> str:
    """Map a 0..1-ish risk/quality value to Good/Fair/Poor.

    higher_is_worse=True: value<good_below->Good, <fair_below->Fair, else Poor.
    higher_is_worse=False: value>good_below->Good, >fair_below->Fair, else Poor.
    """
    if higher_is_worse:
        return "Good" if value < good_below else ("Fair" if value < fair_below else "Poor")
    return "Good" if value > good_below else ("Fair" if value > fair_below else "Poor")


@dataclass
class MetricResult:
    metric_id: str
    value: Any = None                 # raw computed value (number/str)
    value_text: str = ""              # human-readable value for the report
    rating: Optional[str] = None      # 'Good' | 'Fair' | 'Poor' | None
    confidence: str = "L"             # H / M / M/L / L
    source: str = ""                  # data source label for the report
    status: str = "ok"                # 'ok' | 'unavailable' | 'override'
    note: str = ""
    is_override: bool = False


class MetricAdapter(Protocol):
    metric_id: str

    async def compute(self, ctx: AnalysisContext) -> MetricResult:
        ...


def unavailable(metric_id: str, note: str = "", confidence: str = "L") -> MetricResult:
    """Helper for graceful degradation when a source has no data/errors."""
    return MetricResult(metric_id=metric_id, rating=None, status="unavailable",
                        confidence=confidence, note=note, value_text="not available")
