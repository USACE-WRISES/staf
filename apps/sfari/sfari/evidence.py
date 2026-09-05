"""Desktop evidence pull: the STAF site engine's exact-watershed values plus
direct services.

Prefetches the direct services once (NID dams near the reach, WQP nutrient
medians, the nearest comparable NWIS gage, NWI wetlands), then runs the
desktop-metric adapters (pure functions reading ``ctx.extras``). Each returns
an :class:`EvidenceResult` with a value + a suggested Likert (from
``likert.suggest`` or an in-adapter rule) + confidence + source, shown in the
worksheet to SUPPORT the user's scoring; it never auto-scores. Adapters never
raise; on failure they degrade to ``status='unavailable'`` with a deep-link.

One watershed engine (2026-09-05). Every watershed metric reads the STAF site
engine's exact-watershed value (``origin="engine"``). While the engine is
still running the entry is ``status="pending"``; when it failed or refused,
the entry is unavailable and says why. Nothing is substituted from a
neighboring NHDPlus V2 reach. Direct services stay ``origin="pull"``.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import anyio

from . import engine_prefill, likert
from .datasources import nid_barriers, nwi, nwis, wqp
from .metrics.base import AnalysisContext
from .models import EvidenceResult

FCODE_LABEL = {46006: "perennial", 46003: "intermittent", 46007: "ephemeral",
               55800: "artificial path", 33600: "canal/ditch"}

ACRE_FT_PER_KM2_TO_M3_PER_KM2 = 1233.48184


async def _thread(fn, *a):
    return await anyio.to_thread.run_sync(fn, *a)


def _ctx_from_inputs(ci: dict) -> AnalysisContext:
    return AnalysisContext(
        lat=ci["lat"], lon=ci["lon"], comid=ci.get("comid"), huc8=ci.get("huc8"),
        watershed_geojson=ci.get("watershed_geojson"), reach_geojson=ci.get("reach_geojson"),
        drainage_area_sqkm=ci.get("drainage_area_sqkm"), slope=ci.get("slope"),
        fcode=ci.get("fcode"), stream_order=ci.get("stream_order"), sinuosity=ci.get("sinuosity"))


# --------------------------------------------------------------------------- #
# the site engine's state on this run
# --------------------------------------------------------------------------- #
def _engine_state(ctx) -> dict:
    return ctx.extras.get("engine") or {"status": "idle"}


def _eng(ctx, key: str):
    return (ctx.extras.get("engine_metrics") or {}).get(key)


def _engine_running(ctx) -> bool:
    return _engine_state(ctx).get("status") == "running"


_VERSION_HINT: dict = {}


def _engine_version_hint() -> Optional[str]:
    return _VERSION_HINT.get("version") or engine_prefill.engine_version()


def _pending(mid: str) -> EvidenceResult:
    return EvidenceResult(
        mid, status="pending", origin="engine",
        source=engine_prefill.engine_label(_engine_version_hint()),
        source_url=engine_prefill.ENGINE_URL,
        note="STAF site engine running; the exact-watershed value replaces this "
             "entry when it finishes.")


def _engine_entry(ctx, mid: str, value, value_text: str, field_text: str,
                  note: str = "") -> EvidenceResult:
    rec = _engine_state(ctx).get("record") or {}
    ver = rec.get("engineVersion") or _engine_version_hint()
    return EvidenceResult(
        mid, value=value, value_text=value_text, field_value_text=field_text,
        suggested_likert=likert.suggest(mid, value) if value is not None else None,
        confidence="M", source=engine_prefill.engine_source(rec),
        source_url=engine_prefill.ENGINE_URL,
        note=(note + " " if note else "") + engine_prefill.engine_note(rec),
        origin="engine", engine_version=ver)


def _engine_missing(ctx, mid: str, what: str) -> EvidenceResult:
    """The engine has not answered this metric: pending while it runs,
    unavailable with the reason otherwise. Never a value from another reach."""
    st = _engine_state(ctx)
    status = st.get("status") or "idle"
    if status == "running":
        return _pending(mid)
    if status == "ok":
        note = f"The STAF site engine did not return {what} for this watershed."
    elif status == "idle":
        note = "The STAF site engine has not run for this site."
    else:
        note = f"STAF site engine {status}: {st.get('reason') or 'no detail'}."
    return EvidenceResult(mid, status="unavailable", origin="engine",
                          source=engine_prefill.engine_label(_engine_version_hint()),
                          source_url=engine_prefill.ENGINE_URL, note=note)


# Natural riparian vegetation (100 m buffer): forest + shrub + grassland + wetland. Broader than
# forest alone so grassland/arid streams are not falsely scored low for a non-forest but intact
# natural buffer. Backs the buffer/corridor metrics; canopy shade stays forest-only (ev_canopy).
_ENGINE_RIPARIAN_KEYS = ("forestPctRiparian", "shrubPctRiparian", "grasslandPctRiparian",
                         "woodyWetlandPctRiparian", "herbWetlandPctRiparian")


def _engine_riparian_veg_pct(ctx) -> Optional[float]:
    vals = [_eng(ctx, k) for k in _ENGINE_RIPARIAN_KEYS]
    if any(v is None for v in vals):
        return None
    return round(sum(float(v) for v in vals), 1)


# Alternate catchment-hydrology land-cover indicator: watershed agriculture (crop + hay and
# pasture over the exact watershed). ``ev_impervious`` compares it against impervious and,
# when agriculture is the more limiting land-cover pressure, advises scoring the function on
# agriculture instead (mirrors the EASI selectable indicator). Likert order: lower rank = worse.
_AG_COVER_KEY = "catchment-hydrology-agricultural-cover"
_LIKERT_RANK = {"Strongly Disagree": 0, "Disagree": 1, "Neutral": 2,
                "Agree": 3, "Strongly Agree": 4}


def _engine_ag_pct(ctx) -> Optional[float]:
    crop, hay = _eng(ctx, "cropPctWatershed"), _eng(ctx, "hayPasturePctWatershed")
    if crop is None or hay is None:
        return None
    return round(float(crop) + float(hay), 1)


def _land_cover_entry(ctx, mid, v, ag, value_suffix, field_suffix, make):
    imp_likert = likert.suggest(mid, v)
    suggested = imp_likert
    value_text = f"{v:.1f}% impervious{value_suffix}"
    note = ""
    if ag is not None:
        value_text = f"{v:.1f}% impervious, {ag:.1f}% agricultural land{value_suffix}"
        ag_likert = likert.suggest(_AG_COVER_KEY, ag)
        if ag_likert and _LIKERT_RANK.get(ag_likert, 9) < _LIKERT_RANK.get(imp_likert, 9):
            suggested = ag_likert
        note = (f"Land-cover indicators: impervious {v:.1f}% ({imp_likert}), "
                f"agricultural {ag:.1f}% ({ag_likert}). The more limiting one is suggested.")
    return make(value_text, f"Impervious {v:.1f}%{field_suffix}", suggested, note)


# --------------------------------------------------------------------------- #
# adapters: AnalysisContext -> EvidenceResult
# --------------------------------------------------------------------------- #
def ev_impervious(ctx):
    mid = "catchment-hydrology-impervious-surface-area"
    imp = _eng(ctx, "imperviousPctWatershed")
    if imp is None:
        return _engine_missing(ctx, mid, "impervious cover")

    def make(value_text, field_text, suggested, note):
        e = _engine_entry(ctx, mid, round(float(imp), 1), value_text, field_text, note)
        e.suggested_likert = suggested
        e.confidence = "H"
        return e
    return _land_cover_entry(ctx, mid, float(imp), _engine_ag_pct(ctx),
                             " (exact watershed)", " (exact watershed)", make)


def ev_road_density(ctx):
    mid = "catchment-hydrology-road-density"
    rd = _eng(ctx, "roadDensity")
    if rd is None:
        return _engine_missing(ctx, mid, "road density")
    return _engine_entry(ctx, mid, round(float(rd), 2),
                         f"{rd:.2f} km/km2 road density (exact watershed)",
                         f"Roads {rd:.2f} km/km2 (exact watershed)",
                         "TIGERweb primary + secondary + local roads clipped to the "
                         "watershed.")


def ev_impoundments(ctx):
    mid = "catchment-hydrology-impoundments"
    dams = _eng(ctx, "damCount")
    if dams is None:
        return _engine_missing(ctx, mid, "the dam count")
    storage = _eng(ctx, "damStorageAcreFt")
    n = int(dams)
    txt = (f"{n} NID dam(s) in the watershed"
           + (f", {storage:,.0f} acre-ft normal storage" if storage is not None else ""))
    e = _engine_entry(ctx, mid, n, txt, f"Dams {n} in watershed (exact watershed)")
    e.suggested_likert = ("Strongly Agree" if n == 0 else "Agree" if n <= 2
                          else "Disagree" if n <= 5 else "Strongly Disagree")
    return e


def ev_wetland(ctx):
    mid = "surface-water-storage-wetland-coverage"
    woody, herb = _eng(ctx, "woodyWetlandPctWatershed"), _eng(ctx, "herbWetlandPctWatershed")
    if woody is None or herb is None:
        return _engine_missing(ctx, mid, "wetland cover")
    wet = round(float(woody) + float(herb), 1)
    return _engine_entry(ctx, mid, wet, f"{wet:.1f}% wetland (exact watershed)",
                         f"Wetlands {wet:.1f}% (exact watershed)")


def _riparian(ctx, mid, extra=""):
    fp_eng = _eng(ctx, "forestPctRiparian")
    if fp_eng is None:
        return _engine_missing(ctx, mid, "riparian forest cover")
    fp = round(float(fp_eng), 1)
    return _engine_entry(ctx, mid, fp,
                         f"{fp:.1f}% forest in the 100 m riparian buffer{extra}",
                         f"Riparian forest {fp:.1f}% (exact watershed)",
                         "100 m buffer of the upstream network.")


def _riparian_veg(ctx, mid):
    """Buffer/corridor evidence from natural riparian vegetation (forest+shrub+grassland+wetland)."""
    veg = _engine_riparian_veg_pct(ctx)
    if veg is None:
        return _engine_missing(ctx, mid, "riparian vegetation")
    return _engine_entry(ctx, mid, veg,
                         f"{veg:.1f}% natural vegetation in the 100 m riparian buffer "
                         "(forest + shrub + grassland + wetland)",
                         f"Riparian natural veg {veg:.1f}% (exact watershed)",
                         "100 m buffer of the upstream network. In grassland or arid "
                         "ecoregions the natural buffer is non-forest; verify on the aerial "
                         "basemap.")


def ev_canopy(ctx):
    # canopy shade is forest-specific (grass does not shade), so keep this one forest-only
    return _riparian(ctx, "light-thermal-regime-riparian-canopy-cover")


def ev_corridor(ctx):
    return _riparian_veg(ctx, "carbon-processing-riparian-corridor-width-and-quality")


def ev_veg_corridor(ctx):
    return _riparian_veg(ctx, "nutrient-cycling-vegetated-riparian-corridor-width")


def ev_riparian_communities(ctx):
    return _riparian_veg(ctx, "community-dynamics-riparian-communities")


def ev_transport_capacity(ctx):
    mid = "sediment-continuity-transport-capacity"
    slope = ctx.slope
    ag_eng = _engine_ag_pct(ctx)
    k_eng = _eng(ctx, "soilKFactor")
    if ag_eng is None and k_eng is None:
        return _engine_missing(ctx, mid, "soil erodibility and agriculture")
    parts = []
    if slope is not None:
        parts.append(f"channel slope {slope:.4f} m/m")
    if ag_eng is not None:
        parts.append(f"watershed agriculture {ag_eng:.0f}% (exact watershed)")
    if k_eng is not None:
        parts.append(f"soil K {float(k_eng):.2f} (exact watershed)")
    fvt = (f"Slope {slope:.4f} m/m" if slope is not None
           else f"Agriculture {ag_eng:.0f}% (exact watershed)" if ag_eng is not None
           else f"Soil K {float(k_eng):.2f} (exact watershed)")
    e = _engine_entry(ctx, mid, slope, ", ".join(parts), fvt,
                      "Screening context; run the cross-section tool for a Shields "
                      "transport-capacity analysis.")
    e.suggested_likert = None
    e.confidence = "L"
    return e


def ev_np(ctx):
    mid = "nutrient-cycling-n-p-concentrations"
    tn, tp = ctx.extras.get("tn"), ctx.extras.get("tp")
    if tn is None and tp is None:
        return EvidenceResult(mid, status="unavailable", source="Water Quality Portal",
                              source_url="https://www.waterqualitydata.us/",
                              note="No nearby WQP nutrient samples (2015+, within 5 mi).")
    parts = []
    if tn is not None:
        parts.append(f"TN {tn} mg/L")
    if tp is not None:
        parts.append(f"TP {tp} mg/L")
    short = " / ".join(s for s in [f"TN {tn}" if tn is not None else "",
                                   f"TP {tp}" if tp is not None else ""] if s)
    return EvidenceResult(mid, value_text="observed median: " + ", ".join(parts),
                          field_value_text=f"{short} mg/L",
                          confidence="M", source="EPA/USGS Water Quality Portal",
                          source_url="https://www.waterqualitydata.us/",
                          note="Observed medians; compare to ecoregion reference criteria to score "
                               "(reference added in Phase 4).")


def ev_channel_pattern(ctx):
    mid = "channel-floodplain-dynamics-channel-pattern"
    sin = ctx.sinuosity
    if sin is None:
        return EvidenceResult(mid, status="unavailable", source="NHDPlus flowline geometry")
    return EvidenceResult(mid, value=sin, value_text=f"sinuosity {sin}",
                          field_value_text=f"Sinuosity {sin}",
                          confidence="L", source="NHDPlus flowline geometry",
                          note="Compare planform to local reference (multi-date imagery / TopoView) to score.")


def ev_dewatered(ctx):
    mid = "watershed-connectivity-dewatered-or-intermittent-segments"
    fc = ctx.fcode
    if fc is None:
        return EvidenceResult(mid, status="unavailable", source="NHDPlus FCODE")
    lab = FCODE_LABEL.get(fc, f"FCODE {fc}")
    return EvidenceResult(mid, value=fc, value_text=f"NHD flow permanence: {lab}",
                          field_value_text=f"Flow class {lab}",
                          confidence="M", source="NHDPlus FCODE",
                          note="Natural intermittency is not artificial dewatering. Check NWIS "
                               "zero-flow days and imagery.")


def _land_use_change_suggestion(d: float) -> str:
    return ("Strongly Agree" if d < 1 else "Agree" if d < 3
            else "Disagree" if d < 6 else "Strongly Disagree")


def ev_land_use_change(ctx):
    mid = "catchment-hydrology-land-use-change"
    a_eng, b_eng = _eng(ctx, "imperviousPct2001Watershed"), _eng(ctx, "imperviousPctWatershed")
    if a_eng is None or b_eng is None:
        return _engine_missing(ctx, mid, "impervious cover for 2001 and 2021")
    d = round(float(b_eng) - float(a_eng), 1)
    e = _engine_entry(ctx, mid, d,
                      f"impervious {float(a_eng):.1f}% (2001) → {float(b_eng):.1f}% (2021), "
                      f"Δ {d:+.1f} pts (exact watershed)",
                      f"Impervious change {d:+.1f} pts (exact watershed)",
                      "NLCD 2001 to 2021 impervious-cover change as a proxy for land "
                      "conversion.")
    e.suggested_likert = _land_use_change_suggestion(d)
    return e


def ev_flow_permanence(ctx):
    mid = "streamflow-regime-flow-permanence"
    f = ctx.extras.get("flow")
    if not f:
        return EvidenceResult(mid, status="unavailable", source="USGS NWIS",
                              source_url="https://waterdata.usgs.gov/nwis",
                              note="No comparable nearby gage with daily flow.")
    z = f["zero_frac"]
    sug = ("Strongly Agree" if z <= 0.0 else "Agree" if z < 0.02
           else "Disagree" if z < 0.1 else "Strongly Disagree")
    return EvidenceResult(mid, value=z,
                          value_text=f"{z*100:.1f}% zero-flow days at gage {f['site']} ({f['n_days']} d)",
                          field_value_text=f"Zero-flow {z*100:.1f}%",
                          confidence="M", source=f"USGS NWIS {f['site']} {f['name']}".strip(),
                          source_url=f"https://waterdata.usgs.gov/monitoring-location/{f['site']}/",
                          note="Zero-flow-day fraction from the nearest comparable gage.",
                          suggested_likert=sug)


def ev_flow_statistics(ctx):
    mid = "streamflow-regime-flow-permanence-statistics"
    f = ctx.extras.get("flow")
    if not f:
        return EvidenceResult(mid, status="unavailable", source="USGS NWIS",
                              source_url="https://waterdata.usgs.gov/nwis")
    return EvidenceResult(mid, value=f["q50"],
                          value_text=f"Q10 {f['q10']} / Q50 {f['q50']} / Q90 {f['q90']} cfs (gage {f['site']})",
                          field_value_text=f"Q50 {f['q50']} cfs",
                          confidence="M", source=f"USGS NWIS {f['site']}",
                          source_url=f"https://waterdata.usgs.gov/monitoring-location/{f['site']}/",
                          note="Flow-duration percentiles (Qp = discharge exceeded p% of days).")


def ev_natural_flow_regime(ctx):
    mid = "streamflow-regime-channel-natural-flow-regime"
    f = ctx.extras.get("flow")
    ratio = f.get("baseflow_ratio") if f else None
    storage = _eng(ctx, "damStoragePerSqkm")
    if storage is not None:
        dam_eng = round(float(storage) * ACRE_FT_PER_KM2_TO_M3_PER_KM2, 0)
        parts = []
        if ratio is not None:
            parts.append(f"baseflow ratio Q90/Q50 = {ratio} (gage {f['site']})")
        parts.append(f"upstream normal storage {dam_eng:.0f} m3/km2 (exact watershed)")
        fvt = (f"Baseflow ratio {ratio}" if ratio is not None
               else f"Dam storage {dam_eng:.0f} m3/km2 (exact watershed)")
        e = _engine_entry(ctx, mid, dam_eng, ", ".join(parts), fvt,
                          "Compare to a reference or unregulated regime (TNC IHA) to judge "
                          "alteration.")
        e.suggested_likert = None
        e.confidence = "L"
        return e
    if ratio is not None:
        # the gage alone: dam storage waits for the engine or is unavailable
        return EvidenceResult(mid, value_text=f"baseflow ratio Q90/Q50 = {ratio} (gage {f['site']})",
                              field_value_text=f"Baseflow ratio {ratio}", confidence="L",
                              source=f"USGS NWIS {f['site']}",
                              source_url=f"https://waterdata.usgs.gov/monitoring-location/{f['site']}/",
                              note="Compare to a reference or unregulated regime (TNC IHA) to "
                                   "judge alteration.")
    return _engine_missing(ctx, mid, "upstream dam storage")


def ev_artificial_structures(ctx):
    mid = "streamflow-regime-artificial-structures-and-inputs"
    nid = ctx.extras.get("nid")
    if nid is None:
        return EvidenceResult(mid, status="unavailable", source="USACE NID / NLD",
                              source_url="https://levees.sec.usace.army.mil/")
    n = len(nid)
    sug = ("Strongly Agree" if n == 0 else "Agree" if n <= 1 else "Disagree" if n <= 3 else "Strongly Disagree")
    return EvidenceResult(mid, value=n, value_text=f"{n} dam(s) within ~1 mi (also check NLD levees)",
                          field_value_text=f"Structures {n} dam(s)",
                          confidence="M", source="USACE NID (dams) + National Levee Database",
                          source_url="https://levees.sec.usace.army.mil/",
                          note="Dams from NID; add levees from the National Levee Database (deep-link).",
                          suggested_likert=sug)


def ev_barriers(ctx):
    mid = "watershed-connectivity-upstream-and-downstream-barriers"
    nid = ctx.extras.get("nid")
    if nid is None:
        return EvidenceResult(mid, status="unavailable", source="USACE NID / Aquatic Barrier Inventory",
                              source_url="https://connectivity.sarpdata.com/")
    n = len(nid)
    sug = ("Strongly Agree" if n == 0 else "Agree" if n <= 1 else "Disagree" if n <= 3 else "Strongly Disagree")
    return EvidenceResult(mid, value=n, value_text=f"{n} dam/barrier(s) within ~1 mi",
                          field_value_text=f"Barriers {n} dam(s)",
                          confidence="M", source="USACE NID (+ National Aquatic Barrier Inventory)",
                          source_url="https://connectivity.sarpdata.com/",
                          note="Dam count near the reach; add road-crossing / aquatic barriers for passability.",
                          suggested_likert=sug)


def ev_lateral_inundation(ctx):
    mid = "floodplain-connectivity-lateral-floodplain-inundation"
    w = ctx.extras.get("nwi")
    if not w or not w.get("count"):
        return EvidenceResult(mid, status="unavailable", source="USFWS NWI / 3DEP",
                              source_url="https://www.fws.gov/program/national-wetlands-inventory/wetlands-mapper",
                              note="Inspect NWI + 3DEP hillshade for floodplain features.")
    return EvidenceResult(mid, value=w["acres"],
                          value_text=f"{w['count']} NWI wetland feature(s), {w['acres']} ac near the reach",
                          field_value_text=f"Floodplain {w['count']} NWI feature(s)",
                          confidence="L", source="USFWS National Wetlands Inventory",
                          source_url="https://www.fws.gov/program/national-wetlands-inventory/wetlands-mapper",
                          note="Adjacent wetland/floodplain features (screening; confirm inundation with 3DEP).")


REGISTRY = {
    "catchment-hydrology-impervious-surface-area": ev_impervious,
    "catchment-hydrology-road-density": ev_road_density,
    "catchment-hydrology-impoundments": ev_impoundments,
    "surface-water-storage-wetland-coverage": ev_wetland,
    "light-thermal-regime-riparian-canopy-cover": ev_canopy,
    "carbon-processing-riparian-corridor-width-and-quality": ev_corridor,
    "nutrient-cycling-vegetated-riparian-corridor-width": ev_veg_corridor,
    "community-dynamics-riparian-communities": ev_riparian_communities,
    "sediment-continuity-transport-capacity": ev_transport_capacity,
    "nutrient-cycling-n-p-concentrations": ev_np,
    "channel-floodplain-dynamics-channel-pattern": ev_channel_pattern,
    "watershed-connectivity-dewatered-or-intermittent-segments": ev_dewatered,
    "catchment-hydrology-land-use-change": ev_land_use_change,
    "streamflow-regime-flow-permanence": ev_flow_permanence,
    "streamflow-regime-flow-permanence-statistics": ev_flow_statistics,
    "streamflow-regime-channel-natural-flow-regime": ev_natural_flow_regime,
    "streamflow-regime-artificial-structures-and-inputs": ev_artificial_structures,
    "watershed-connectivity-upstream-and-downstream-barriers": ev_barriers,
    "floodplain-connectivity-lateral-floodplain-inundation": ev_lateral_inundation,
}

# The metrics the site engine answers (every entry carries ``origin="engine"``).
ENGINE_METRICS = (
    "catchment-hydrology-impervious-surface-area",
    "catchment-hydrology-road-density",
    "catchment-hydrology-impoundments",
    "surface-water-storage-wetland-coverage",
    "light-thermal-regime-riparian-canopy-cover",
    "carbon-processing-riparian-corridor-width-and-quality",
    "nutrient-cycling-vegetated-riparian-corridor-width",
    "community-dynamics-riparian-communities",
    "sediment-continuity-transport-capacity",
    "catchment-hydrology-land-use-change",
    "streamflow-regime-channel-natural-flow-regime",
)


def _resolve_engine(ctx_inputs: dict, engine: Optional[dict]) -> dict:
    """The site-engine state for this pull. ``None`` runs the engine inline
    (scripts, tests); the app passes its own state so the pull never blocks
    on the engine."""
    if engine is not None:
        return dict(engine)
    if not engine_prefill.site_engine_available():
        return {"status": "unavailable",
                "reason": "the STAF site engine is not available in this deployment"}
    rec = engine_prefill.run_engine(ctx_inputs["lat"], ctx_inputs["lon"],
                                    include_geometry=False)
    return {"status": rec.get("status") or "failed", "record": rec,
            "reason": rec.get("reason")}


async def pull(ctx_inputs: dict, *, progress: Optional[dict] = None,
               engine: Optional[dict] = None) -> dict:
    """Pull desktop evidence for the Phase-3 metrics. Returns {metricId: evidence-dict}.

    ``engine`` is the app's site-engine state (``{"status": idle | running |
    ok | failed | refused | unavailable, "record", "reason"}``); None runs the
    engine inline. The engine is the only source for every watershed metric;
    the direct services answer the rest.
    """
    ctx = _ctx_from_inputs(ctx_inputs)
    state = _resolve_engine(ctx_inputs, engine)
    ctx.extras["engine"] = state
    ctx.extras["engine_metrics"] = engine_prefill.engine_metrics(state.get("record"))
    if (state.get("record") or {}).get("engineVersion"):
        _VERSION_HINT["version"] = state["record"]["engineVersion"]

    # concurrent network prefetch (off the event loop); each never raises
    nid, tn, tp, flow, wet = await asyncio.gather(
        _thread(nid_barriers.barriers_near, ctx.lat, ctx.lon, 1.0),
        _thread(wqp.median_value, "tn", ctx.lat, ctx.lon),
        _thread(wqp.median_value, "tp", ctx.lat, ctx.lon),
        _thread(nwis.flow_stats, ctx.lat, ctx.lon, ctx.drainage_area_sqkm),
        _thread(nwi.wetlands_near, ctx.lat, ctx.lon),
    )
    ctx.extras.update(nid=nid, tn=tn, tp=tp, flow=flow, nwi=wet)

    if progress is not None:
        progress["total"] = len(REGISTRY)
        progress["done"] = 0
    out: dict[str, dict] = {}
    for mid, fn in REGISTRY.items():
        try:
            res = fn(ctx)
        except Exception as exc:  # noqa: BLE001 - graceful degradation
            res = EvidenceResult(mid, status="unavailable", note=f"adapter error: {exc}")
        out[mid] = res.to_dict()
        if progress is not None:
            progress["done"] = len(out)
    return out
