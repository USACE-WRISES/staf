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
# Every accessor below returns None when any expected source field is absent. A missing
# class is unknown, not zero: summing an absent class as 0 would silently produce a
# falsely favorable rating (low agriculture, low wetland) from missing data.
def sc(ctx: "AnalysisContext") -> dict:
    return ctx.extras.get("streamcat") or {}


def nrsa_evidence(ctx: "AnalysisContext") -> Optional[dict]:
    """The exact or network-confirmed nearby NRSA record prefetched for this reach."""
    value = ctx.extras.get("nrsa")
    return value if isinstance(value, dict) else None


def _integrity_value(value: Any) -> Optional[float]:
    """A StreamCat integrity component, valid only on its documented 0-1 scale."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0.0 <= number <= 1.0 else None


def integrity_pair(ctx: "AnalysisContext",
                   component: str) -> tuple[Optional[float], Optional[float]]:
    """Validated ``(catchment, watershed)`` StreamCat integrity components, e.g. CHYD/WHYD."""
    key = str(component).strip().lower()
    values = sc(ctx)
    return (_integrity_value(values.get(f"{key}cat")),
            _integrity_value(values.get(f"{key}ws")))


def integrity_products(ctx: "AnalysisContext") -> Optional[dict[str, float]]:
    """The six published integrity components behind the ICI/IWI products.

    Returns None if any component is missing; they are never converted to zero.
    """
    values: dict[str, float] = {}
    for component in ("hyd", "chem", "sed", "conn", "temp", "habt"):
        catchment, watershed = integrity_pair(ctx, component)
        if catchment is None or watershed is None:
            return None
        values[f"{component}Cat"] = catchment
        values[f"{component}Ws"] = watershed
    return values


def riparian_forest_pct(ctx: "AnalysisContext") -> Optional[float]:
    s = sc(ctx)
    vals = [s.get("pctconif2019wsrp100"), s.get("pctdecid2019wsrp100"),
            s.get("pctmxfst2019wsrp100")]
    if any(v is None for v in vals):
        return None
    return round(sum(float(v) for v in vals), 2)


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
    """Per-group natural-vegetation cover in the 100 m riparian buffer, or None if incomplete.

    Returns ``{forest, shrub, grassland, wetland, total}`` (percent). Every expected source
    field is required — an absent class is unknown, not zero.
    """
    s = sc(ctx)
    groups: dict[str, float] = {}
    for grp, keys in _RIPARIAN_VEG_KEYS.items():
        vals = [s.get(k) for k in keys]
        if any(v is None for v in vals):
            return None
        groups[grp] = round(sum(float(v) for v in vals), 1)
    groups["total"] = round(sum(groups[g] for g in _RIPARIAN_VEG_KEYS), 1)
    return groups


def riparian_woody_breakdown(ctx: "AnalysisContext") -> Optional[dict]:
    """Forest + shrub + woody-wetland cover in the 100 m riparian corridor.

    All five expected StreamCat fields are required. Grassland and herbaceous wetland are
    excluded: they do not provide the canopy shade this supports.
    """
    s = sc(ctx)
    groups = {
        "forest": ("pctconif2019wsrp100", "pctdecid2019wsrp100", "pctmxfst2019wsrp100"),
        "shrub": ("pctshrb2019wsrp100",),
        "woodyWetland": ("pctwdwet2019wsrp100",),
    }
    out: dict[str, float] = {}
    for group, keys in groups.items():
        vals = [s.get(k) for k in keys]
        if any(v is None for v in vals):
            return None
        out[group] = round(sum(float(v) for v in vals), 1)
    out["total"] = round(sum(out.values()), 1)
    return out


def riparian_woody_pct(ctx: "AnalysisContext") -> Optional[float]:
    breakdown = riparian_woody_breakdown(ctx)
    return None if breakdown is None else breakdown["total"]


def riparian_natural_veg_pct(ctx: "AnalysisContext") -> Optional[float]:
    """% of the 100 m riparian buffer in natural vegetation (forest + shrub + grassland +
    wetland) as a CPOM / buffer-condition proxy. Unlike forest-only, it credits the natural
    buffer of grassland and arid/xeric ecoregions."""
    b = riparian_veg_breakdown(ctx)
    return None if b is None else b["total"]


def ag_pct(ctx: "AnalysisContext") -> Optional[float]:
    s = sc(ctx)
    vals = [s.get("pctcrop2019ws"), s.get("pcthay2019ws")]
    if any(v is None for v in vals):
        return None
    return round(sum(float(v) for v in vals), 2)


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
    # The canonical evaluation trace from screening_methods.evaluate(): method key/kind, basis,
    # every input with its value+source, combined value, completeness, source tier, evidence
    # family, fallback and observed-supersedes flags, limitations. Never overloaded into detail.
    scoring: Optional[dict] = None
    is_override: bool = False
    # Reserved for supplied alternate evaluations keyed by source value (the engine-side
    # merge in assessment.apply_source_choices). No adapter populates this today; the
    # in-app source picker was removed in the 2026-08 review.
    variants: Optional[dict[str, "MetricResult"]] = None
    source_key: Optional[str] = None


class MetricAdapter(Protocol):
    metric_id: str

    async def compute(self, ctx: AnalysisContext) -> MetricResult:
        ...


def unavailable(metric_id: str, note: str = "", confidence: str = "L",
                *, scoring: Optional[dict] = None,
                value_text: str = "not available") -> MetricResult:
    """Graceful degradation when a source fails or a required input is absent.

    Pass ``scoring`` to keep the evaluation trace (which inputs were missing, and why the
    metric could not be rated) even though no rating was produced.
    """
    return MetricResult(metric_id=metric_id, rating=None, status="unavailable",
                        confidence=confidence, note=note, value_text=value_text,
                        scoring=scoring)
