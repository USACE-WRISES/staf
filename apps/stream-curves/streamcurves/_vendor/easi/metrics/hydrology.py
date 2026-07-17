"""Hydrology-discipline EASI metric adapters."""
from __future__ import annotations

from . import base
from .base import AnalysisContext, MetricResult, unavailable

IMPERVIOUS_ID = "catchment-hydrology-impervious-surface-cover"
WETLANDS_ID = "surface-water-storage-percent-wetlands-in-watershed"
FLOW_ALTERATION_ID = "streamflow-regime-flow-alteration-regulation-water-use"
REACH_INFLOW_ID = "reach-inflow-concentrated-runoff-stormwater-inputs"


# Catchment-hydrology land-cover pressure. Two indicators are derived from the same watershed
# land cover: impervious cover (Center for Watershed Protection Impervious Cover Model, Good <10 /
# Fair 10-25 / Poor >25) and agricultural cover (Good <25 / Fair 25-50 / Poor >50, provisional).
# The metric is scored AUTOMATICALLY on whichever is more limiting (worse rating) for the
# watershed -- catchment hydrology is impaired if either land-cover pressure is high -- so the
# user never has to choose a-priori. The report names the governing driver and shows both.
_RANK = {"Poor": 0, "Fair": 1, "Good": 2, None: 3}  # lower rank = more limiting


def _impervious_pct(ctx: AnalysisContext):
    v = base.sc(ctx).get("pctimp2019ws")
    if v is not None:
        return v, "EPA StreamCat pctimp2019 (watershed)"
    v = (ctx.extras.get("landcover") or {}).get("impervious_pct")
    return v, "NLCD 2021 impervious (watershed)"


def _agriculture_pct(ctx: AnalysisContext):
    v = base.ag_pct(ctx)  # StreamCat pctcrop2019ws + pcthay2019ws
    if v is not None:
        return v, "EPA StreamCat crop+hay (watershed)"
    v = (ctx.extras.get("landcover") or {}).get("ag_pct")
    return v, "NLCD 2021 agriculture (watershed)"


def _comparison_note(imp, imp_rating, ag, ag_rating) -> str:
    """One-line explanation naming both land-cover indicators; blank if only one has data."""
    if imp is None or ag is None:
        return ""
    return (f"Land-cover indicators: impervious {round(float(imp), 1)}% ({imp_rating}); "
            f"agricultural {round(float(ag), 1)}% ({ag_rating}). The more limiting one governs.")


def impervious(ctx: AnalysisContext) -> MetricResult:
    """Catchment-hydrology land-cover pressure, scored on the more limiting of two indicators.

    Impervious cover (Center for Watershed Protection Impervious Cover Model, Good <10, Fair
    10-25, Poor >25) and agricultural cover (Good <25, Fair 25-50, Poor >50, provisional) are
    both derived from watershed land cover; the worse rating governs (a tie, or a missing
    agricultural value, defaults to impervious, the anchor). The result carries both indicators
    in ``detail`` so the report can show them and mark the governing one.
    """
    imp, imp_src = _impervious_pct(ctx)
    ag, ag_src = _agriculture_pct(ctx)
    imp_rating = ("Good" if imp < 10 else "Fair" if imp <= 25 else "Poor") if imp is not None else None
    ag_rating = ("Good" if ag < 25 else "Fair" if ag <= 50 else "Poor") if ag is not None else None
    if imp_rating is None and ag_rating is None:
        return unavailable(IMPERVIOUS_ID, "no land-cover data available", "H")
    # governing = the more limiting driver; a tie or a missing agricultural value defaults to impervious
    if imp_rating is None:
        governing = "agriculture"
    elif ag_rating is None or _RANK[ag_rating] >= _RANK[imp_rating]:
        governing = "impervious"
    else:
        governing = "agriculture"
    if governing == "agriculture":
        val, rating, source = ag, ag_rating, ag_src
        value_text = f"{round(float(ag), 1)}% agricultural land (watershed)"
    else:
        val, rating, source = imp, imp_rating, imp_src
        value_text = f"{round(float(imp), 1)}% impervious"
    detail = {
        "governing": governing,
        "impervious": None if imp is None else {"pct": round(float(imp), 1), "rating": imp_rating},
        "agriculture": None if ag is None else {"pct": round(float(ag), 1), "rating": ag_rating},
    }
    return MetricResult(IMPERVIOUS_ID, value=round(float(val), 2), value_text=value_text,
                        rating=rating, confidence="H", source=source,
                        note=_comparison_note(imp, imp_rating, ag, ag_rating), detail=detail,
                        scoring={"inputs": {"impervious": imp, "agriculture": ag},
                                 "value": round(float(val), 2), "model": "worst"})


def wetlands(ctx: AnalysisContext) -> MetricResult:
    """% wetlands in the watershed. STAF: Good >5, Fair 1-5, Poor <1.

    Source is user-selectable (config.SOURCE_OPTIONS): EPA StreamCat (default) or
    NLCD 2021. Absent a choice, prefer StreamCat and fall back to NLCD. Both values
    come from data already prefetched for the run, so when
    ctx.extras["prefetch_variants"] is set the result carries BOTH variants and the
    UI can swap sources without recomputing (assessment.apply_source_choices).
    """
    src = (ctx.extras.get("source_choices") or {}).get(WETLANDS_ID)
    s = base.sc(ctx)
    wd, hb = s.get("pctwdwet2019ws"), s.get("pcthbwet2019ws")
    sc_val = (wd or 0.0) + (hb or 0.0) if (wd is not None or hb is not None) else None
    nlcd_val = (ctx.extras.get("landcover") or {}).get("wetland_pct")

    def _variant(val, source):
        if val is None:
            return unavailable(WETLANDS_ID, "no wetland data available", "H")
        rating = "Good" if val > 5 else ("Fair" if val >= 1 else "Poor")
        return MetricResult(WETLANDS_ID, value=round(float(val), 2),
                            value_text=f"{round(float(val), 1)}% wetland cover",
                            rating=rating, confidence="H", source=source,
                            scoring={"inputs": {"wetland": val},
                                     "value": round(float(val), 2), "model": "scalar"})

    values = {"streamcat": (sc_val, "EPA StreamCat wetlands (watershed)"),
              "nlcd": (nlcd_val, "NLCD 2021 wetlands (watershed)")}
    if src in values:
        key = src
    else:  # auto: StreamCat preferred, NLCD fallback
        key = "streamcat" if sc_val is not None else "nlcd"
    if not ctx.extras.get("prefetch_variants"):
        return _variant(*values[key])
    variants = {k: _variant(*v) for k, v in values.items()}
    primary = variants[key]
    primary.variants = variants
    primary.source_key = key
    return primary


def flow_alteration(ctx: AnalysisContext) -> MetricResult:
    """Flow regulation proxy from upstream dam storage per unit drainage area.

    Ungaged proxy (StreamCat normal storage). A gaged NWIS current-vs-baseline
    comparison is a later refinement.
    """
    stor = base.sc(ctx).get("damnrmstorws")
    if stor is None:
        return unavailable(FLOW_ALTERATION_ID, "no dam-storage data", "M")
    da = max(ctx.drainage_area_sqkm or 1.0, 1.0)
    ratio = stor / da  # acre-ft normal storage per km^2
    rating = base.band(ratio, good_below=5.0, fair_below=100.0, higher_is_worse=True)
    return MetricResult(FLOW_ALTERATION_ID, value=round(ratio, 2),
                        value_text=f"{stor:.0f} ac-ft upstream storage ({ratio:.1f} ac-ft/km²)",
                        rating=rating, confidence="M",
                        source="EPA StreamCat dam storage (ungaged proxy)",
                        note="regulation proxy; gaged NWIS comparison refines",
                        scoring={"inputs": {"storage": stor,
                                            "drainage_area": ctx.drainage_area_sqkm},
                                 "value": round(ratio, 2), "model": "combined"})


def reach_inflow(ctx: AnalysisContext) -> MetricResult:
    """Concentrated runoff/stormwater proxy via watershed road density.

    Road-stream crossing counts (TIGER) are a later refinement.
    """
    rd = base.sc(ctx).get("rddensws")
    if rd is None:
        return unavailable(REACH_INFLOW_ID, "no road-density data", "L")
    rating = base.band(rd, good_below=1.0, fair_below=3.0, higher_is_worse=True)
    return MetricResult(REACH_INFLOW_ID, value=round(rd, 2),
                        value_text=f"{rd:.2f} km/km² road density",
                        rating=rating, confidence="L",
                        source="EPA StreamCat road density (proxy)",
                        note="stormwater-input proxy; road-stream crossings refine",
                        scoring={"inputs": {"road_density": rd},
                                 "value": round(rd, 2), "model": "scalar"})
