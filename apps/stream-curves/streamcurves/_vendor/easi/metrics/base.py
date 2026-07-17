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


# Natural riparian vegetation classes in the 100 m buffer (StreamCat *wsrp100). Forest, shrub,
# grassland, and wetland are the natural vegetative cover that supplies coarse organic matter;
# developed, impervious, barren, water, and agriculture (crop/hay) are excluded (not natural buffer).
_RIPARIAN_VEG_KEYS = {
    "forest": ("pctconif2019wsrp100", "pctdecid2019wsrp100", "pctmxfst2019wsrp100"),
    "shrub": ("pctshrb2019wsrp100",),
    "grassland": ("pctgrs2019wsrp100",),
    "wetland": ("pctwdwet2019wsrp100", "pcthbwet2019wsrp100"),
}


def riparian_veg_breakdown(ctx: "AnalysisContext") -> Optional[dict]:
    """Per-group natural-vegetation cover in the 100 m riparian buffer, or None if no data.

    Returns ``{forest, shrub, grassland, wetland, total}`` (percent). A group with no data
    contributes 0; the result is None only when every class is absent.
    """
    s = sc(ctx)
    groups: dict[str, float] = {}
    any_present = False
    for grp, keys in _RIPARIAN_VEG_KEYS.items():
        vals = [s.get(k) for k in keys]
        if any(v is not None for v in vals):
            any_present = True
        groups[grp] = round(sum(v or 0.0 for v in vals), 1)
    if not any_present:
        return None
    groups["total"] = round(sum(groups[g] for g in _RIPARIAN_VEG_KEYS), 1)
    return groups


def riparian_natural_veg_pct(ctx: "AnalysisContext") -> Optional[float]:
    """% of the 100 m riparian buffer in natural vegetation (forest + shrub + grassland +
    wetland) as a CPOM / buffer-condition proxy. Unlike forest-only, it credits the natural
    buffer of grassland and arid/xeric ecoregions."""
    b = riparian_veg_breakdown(ctx)
    return None if b is None else b["total"]


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
    detail: Optional[dict] = None     # adapter-specific extra render data (e.g. land-cover indicators)
    # Transparency trace for the worksheet "Scoring method" panel: the raw inputs the rating used,
    # the computed value, and the method key (easi.methods mode / source variant). Additive — the
    # rating/value math is unchanged; see easi/methods.py.
    scoring: Optional[dict] = None    # {"inputs": {key: value|None}, "value": .., "model": ..}
    is_override: bool = False
    # Multi-source metrics (config.SOURCE_OPTIONS) with ctx.extras["prefetch_variants"]
    # set: every computed variant keyed by source value, plus which key produced THIS
    # result — lets the UI swap sources instantly (assessment.apply_source_choices).
    variants: Optional[dict[str, "MetricResult"]] = None
    source_key: Optional[str] = None


class MetricAdapter(Protocol):
    metric_id: str

    async def compute(self, ctx: AnalysisContext) -> MetricResult:
        ...


def unavailable(metric_id: str, note: str = "", confidence: str = "L") -> MetricResult:
    """Helper for graceful degradation when a source has no data/errors."""
    return MetricResult(metric_id=metric_id, rating=None, status="unavailable",
                        confidence=confidence, note=note, value_text="not available")
