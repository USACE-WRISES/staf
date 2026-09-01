"""Desktop evidence pull — SFARI analog of EASI's assessment orchestrator.

Prefetches shared national data once (StreamCat watershed + riparian rows, NID
dams near the reach, WQP nutrient medians), then runs the desktop-metric adapters
(pure functions reading ``ctx.extras``). Each returns an :class:`EvidenceResult`
with a value + a suggested Likert (from ``likert.suggest`` or an in-adapter rule)
+ confidence + source, shown in the worksheet to SUPPORT the user's scoring — it
never auto-scores. Adapters never raise; on failure they degrade to
``status='unavailable'`` with a resource deep-link.

Two watershed engines, one order. Every metric the STAF site engine covers
reads the engine's exact-watershed value FIRST (``origin="engine"``); the
StreamCat lookup engine's COMID-keyed value is the labeled fallback
(``origin="streamcat"``, with ``anchor_label`` naming the reach it describes on
a site outside NHDPlus V2 and ``fallback_reason`` when the engine failed or
refused); direct services stay ``origin="pull"``. While the engine is still
running, a covered site shows the StreamCat value flagged ``upgrade_pending``
and a site outside NHDPlus V2 shows ``status="pending"``.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import anyio

from . import engine_prefill, likert
from .datasources import nid_barriers, nwi, nwis, streamcat, tiger_roads, wqp
from .metrics.base import AnalysisContext
from .models import EvidenceResult

# StreamCat base names, watershed (ws) + riparian_watershed (wsrp100) AOIs.
# pctimp2001 pairs with pctimp2019 for the land-use-change proxy; kffact feeds
# transport capacity when the engine's area-weighted K is absent.
STREAMCAT_WS = ["pctimp2019", "pctimp2001", "rddens", "damnrmstor",
                "pctwdwet2019", "pcthbwet2019", "pctcrop2019", "pcthay2019", "kffact"]
STREAMCAT_RP = ["pctmxfst2019", "pctdecid2019", "pctconif2019",
                "pctgrs2019", "pctshrb2019", "pctwdwet2019", "pcthbwet2019"]  # natural-veg buffer classes

FCODE_LABEL = {46006: "perennial", 46003: "intermittent", 46007: "ephemeral",
               55800: "artificial path", 33600: "canal/ditch"}

_SC_URL = "https://www.epa.gov/national-aquatic-resource-surveys/streamcat-dataset"
ACRE_FT_PER_KM2_TO_M3_PER_KM2 = 1233.48184

# Metrics whose StreamCat value is COMID-keyed and therefore describes the
# nearest covered reach on a site outside NHDPlus V2.
ANCHOR_LABELED_METRICS = (
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


async def _thread(fn, *a):
    return await anyio.to_thread.run_sync(fn, *a)


def _ctx_from_inputs(ci: dict) -> AnalysisContext:
    return AnalysisContext(
        lat=ci["lat"], lon=ci["lon"], comid=ci.get("comid"), huc8=ci.get("huc8"),
        watershed_geojson=ci.get("watershed_geojson"), reach_geojson=ci.get("reach_geojson"),
        drainage_area_sqkm=ci.get("drainage_area_sqkm"), slope=ci.get("slope"),
        fcode=ci.get("fcode"), stream_order=ci.get("stream_order"), sinuosity=ci.get("sinuosity"))


def _sc(ctx):
    return ctx.extras.get("streamcat") or {}


# --------------------------------------------------------------------------- #
# the site engine's state on this run
# --------------------------------------------------------------------------- #
def _engine_state(ctx) -> dict:
    return ctx.extras.get("engine") or {"status": "idle"}


def _eng(ctx, key: str):
    return (ctx.extras.get("engine_metrics") or {}).get(key)


def _engine_running(ctx) -> bool:
    return _engine_state(ctx).get("status") == "running"


def _hr_only(ctx) -> bool:
    return (ctx.extras.get("site_anchor") or {}).get("anchorKind") == "hrSurrogate"


def _fallback_reason(ctx) -> str:
    st = _engine_state(ctx)
    if st.get("status") in ("failed", "refused", "unavailable"):
        return (f"STAF site engine {st.get('status')}: {st.get('reason') or 'no detail'}. "
                "Showing the StreamCat lookup engine value for the reach's basin.")
    return ""


def _pending(mid: str) -> EvidenceResult:
    return EvidenceResult(
        mid, status="pending", origin="engine",
        source=engine_prefill.engine_label(_engine_version_hint()),
        source_url=engine_prefill.ENGINE_URL,
        note="STAF site engine running; the exact-watershed value replaces this "
             "entry when it finishes.")


_VERSION_HINT: dict = {}


def _engine_version_hint() -> Optional[str]:
    return _VERSION_HINT.get("version") or engine_prefill.engine_version()


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


def _streamcat_entry(ctx, mid: str, **kw) -> EvidenceResult:
    """A StreamCat lookup engine entry, flagged for the engine's state."""
    kw.setdefault("source_url", _SC_URL)
    kw.setdefault("confidence", "M")
    return EvidenceResult(mid, origin="streamcat",
                          fallback_reason=_fallback_reason(ctx),
                          upgrade_pending=_engine_running(ctx) and not _hr_only(ctx),
                          **kw)


def _engine_first(ctx, mid: str):
    """The pending entry when the engine still owns this row, else None."""
    if _engine_running(ctx) and _hr_only(ctx):
        return _pending(mid)
    return None


def _riparian_forest_pct(ctx) -> Optional[float]:
    s = ctx.extras.get("streamcat_rp") or {}
    vals = [s.get("pctconif2019wsrp100"), s.get("pctdecid2019wsrp100"), s.get("pctmxfst2019wsrp100")]
    if all(v is None for v in vals):
        return None
    return round(sum(v or 0.0 for v in vals), 1)


# Natural riparian vegetation (100 m buffer): forest + shrub + grassland + wetland. Broader than
# forest alone so grassland/arid streams are not falsely scored low for a non-forest but intact
# natural buffer. Backs the buffer/corridor metrics; canopy shade stays forest-only (ev_canopy).
_RIPARIAN_VEG_KEYS = ["pctconif2019wsrp100", "pctdecid2019wsrp100", "pctmxfst2019wsrp100",
                      "pctgrs2019wsrp100", "pctshrb2019wsrp100",
                      "pctwdwet2019wsrp100", "pcthbwet2019wsrp100"]
_ENGINE_RIPARIAN_KEYS = ("forestPctRiparian", "shrubPctRiparian", "grasslandPctRiparian",
                         "woodyWetlandPctRiparian", "herbWetlandPctRiparian")


def _riparian_natural_veg_pct(ctx) -> Optional[float]:
    s = ctx.extras.get("streamcat_rp") or {}
    vals = [s.get(k) for k in _RIPARIAN_VEG_KEYS]
    if all(v is None for v in vals):
        return None
    return round(sum(v or 0.0 for v in vals), 1)


def _engine_riparian_veg_pct(ctx) -> Optional[float]:
    vals = [_eng(ctx, k) for k in _ENGINE_RIPARIAN_KEYS]
    if any(v is None for v in vals):
        return None
    return round(sum(float(v) for v in vals), 1)


# Alternate catchment-hydrology land-cover indicator: watershed agriculture (StreamCat
# crop+hay). ``ev_impervious`` compares it against impervious and, when agriculture is the
# more limiting land-cover pressure, advises scoring the function on agriculture instead
# (mirrors the EASI selectable indicator). Likert order: lower rank = worse condition.
_AG_COVER_KEY = "catchment-hydrology-agricultural-cover"
_LIKERT_RANK = {"Strongly Disagree": 0, "Disagree": 1, "Neutral": 2,
                "Agree": 3, "Strongly Agree": 4}


def _watershed_ag_pct(ctx) -> Optional[float]:
    s = _sc(ctx)
    crop, hay = s.get("pctcrop2019ws"), s.get("pcthay2019ws")
    if crop is None and hay is None:
        return None
    return round((crop or 0.0) + (hay or 0.0), 1)


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
        value_text = f"{v:.1f}% impervious; {ag:.1f}% agricultural land{value_suffix}"
        ag_likert = likert.suggest(_AG_COVER_KEY, ag)
        if ag_likert and _LIKERT_RANK.get(ag_likert, 9) < _LIKERT_RANK.get(imp_likert, 9):
            suggested = ag_likert
        note = (f"Land-cover indicators: impervious {v:.1f}% ({imp_likert}); "
                f"agricultural {ag:.1f}% ({ag_likert}). The more limiting one is suggested.")
    return make(value_text, f"Impervious {v:.1f}%{field_suffix}", suggested, note)


# --------------------------------------------------------------------------- #
# adapters: AnalysisContext -> EvidenceResult
# --------------------------------------------------------------------------- #
def ev_impervious(ctx):
    mid = "catchment-hydrology-impervious-surface-area"
    imp = _eng(ctx, "imperviousPctWatershed")
    if imp is not None:
        def make(value_text, field_text, suggested, note):
            e = _engine_entry(ctx, mid, round(float(imp), 1), value_text, field_text, note)
            e.suggested_likert = suggested
            e.confidence = "H"
            return e
        return _land_cover_entry(ctx, mid, float(imp), _engine_ag_pct(ctx),
                                 " (exact watershed)", " (exact watershed)", make)
    pending = _engine_first(ctx, mid)
    if pending is not None:
        return pending
    v = _sc(ctx).get("pctimp2019ws")
    if v is None:
        return _streamcat_entry(ctx, mid, status="unavailable", source="EPA StreamCat")

    def make_sc(value_text, field_text, suggested, note):
        return _streamcat_entry(ctx, mid, value=round(v, 1), value_text=value_text,
                                field_value_text=field_text, confidence="H",
                                source="EPA StreamCat pctimp2019ws",
                                suggested_likert=suggested, note=note)
    return _land_cover_entry(ctx, mid, v, _watershed_ag_pct(ctx),
                             " (watershed)", "", make_sc)


def ev_road_density(ctx):
    mid = "catchment-hydrology-road-density"
    rd = _eng(ctx, "roadDensity")
    if rd is not None:
        return _engine_entry(ctx, mid, round(float(rd), 2),
                             f"{rd:.2f} km/km2 road density (exact watershed)",
                             f"Roads {rd:.2f} km/km2 (exact watershed)",
                             "TIGERweb primary + secondary + local roads clipped to the "
                             "watershed.")
    pending = _engine_first(ctx, mid)
    if pending is not None:
        return pending
    # StreamCat columns carry the AOI suffix (rddens -> rddensws); the bare
    # name never matched, which left this tier dead and every site on the
    # TIGER fallback.
    v = _sc(ctx).get("rddensws")
    if v is not None:
        return _streamcat_entry(ctx, mid, value=round(v, 2),
                                value_text=f"{v:.2f} km/km² road density (watershed)",
                                field_value_text=f"Roads {v:.2f} km/km2",
                                source="EPA StreamCat rddens",
                                suggested_likert=likert.suggest(mid, v))
    roads = ctx.extras.get("tiger")
    if roads is not None:
        return EvidenceResult(mid, value=roads,
                              value_text=f"{roads} TIGER road feature(s) near the reach (crossing proxy)",
                              field_value_text=f"Roads {roads} TIGER feature(s)",
                              confidence="L", source="Census TIGER roads (fallback)",
                              source_url="https://tigerweb.geo.census.gov/",
                              note="StreamCat road density unavailable; TIGER road count near the reach as a proxy.")
    return EvidenceResult(mid, status="unavailable", source="EPA StreamCat / TIGER", source_url=_SC_URL)


def ev_impoundments(ctx):
    mid = "catchment-hydrology-impoundments"
    dams = _eng(ctx, "damCount")
    if dams is not None:
        storage = _eng(ctx, "damStorageAcreFt")
        n = int(dams)
        txt = (f"{n} NID dam(s) in the watershed"
               + (f", {storage:,.0f} acre-ft normal storage" if storage is not None else ""))
        e = _engine_entry(ctx, mid, n, txt, f"Dams {n} in watershed (exact watershed)")
        e.suggested_likert = ("Strongly Agree" if n == 0 else "Agree" if n <= 2
                              else "Disagree" if n <= 5 else "Strongly Disagree")
        return e
    pending = _engine_first(ctx, mid)
    if pending is not None:
        return pending
    nid = ctx.extras.get("nid")
    stor = _sc(ctx).get("damnrmstorws")   # ws-suffixed column (see ev_road_density)
    if nid is None and stor is None:
        return _streamcat_entry(ctx, mid, status="unavailable", source="USACE NID / StreamCat",
                                source_url="https://nid.sec.usace.army.mil/")
    n = len(nid) if nid is not None else None
    parts = []
    if n is not None:
        parts.append(f"{n} dam(s) within ~1 mi")
    if stor:
        parts.append(f"upstream normal storage {stor:.0f}")
    sug = None
    if n is not None:
        sug = ("Strongly Agree" if n == 0 else "Agree" if n <= 2
               else "Disagree" if n <= 5 else "Strongly Disagree")
    fvt = (f"Impoundments {n} dam(s)" if n is not None
           else f"Impoundments storage {stor:.0f}" if stor else "Impoundments none")
    return _streamcat_entry(ctx, mid, value=n, value_text="; ".join(parts) or "no dams found",
                            field_value_text=fvt,
                            source="USACE NID + StreamCat damnrmstor",
                            source_url="https://nid.sec.usace.army.mil/", suggested_likert=sug)


def ev_wetland(ctx):
    mid = "surface-water-storage-wetland-coverage"
    woody, herb = _eng(ctx, "woodyWetlandPctWatershed"), _eng(ctx, "herbWetlandPctWatershed")
    if woody is not None and herb is not None:
        wet = round(float(woody) + float(herb), 1)
        return _engine_entry(ctx, mid, wet, f"{wet:.1f}% wetland (exact watershed)",
                             f"Wetlands {wet:.1f}% (exact watershed)")
    pending = _engine_first(ctx, mid)
    if pending is not None:
        return pending
    s = _sc(ctx)
    w1, w2 = s.get("pctwdwet2019ws"), s.get("pcthbwet2019ws")
    if w1 is None and w2 is None:
        return _streamcat_entry(ctx, mid, status="unavailable", source="EPA StreamCat")
    v = round((w1 or 0.0) + (w2 or 0.0), 1)
    return _streamcat_entry(ctx, mid, value=v,
                            value_text=f"{v:.1f}% wetland (watershed; woody+herbaceous)",
                            field_value_text=f"Wetland {v:.1f}%",
                            source="EPA StreamCat pctwdwet+pcthbwet",
                            note="Watershed wetland % — national-default proxy for the doc's "
                                 "floodplain-area criterion (calibrate regionally).",
                            suggested_likert=likert.suggest(mid, v))


def _riparian(ctx, mid, extra=""):
    fp_eng = _eng(ctx, "forestPctRiparian")
    if fp_eng is not None:
        fp = round(float(fp_eng), 1)
        return _engine_entry(ctx, mid, fp,
                             f"{fp:.1f}% forest in the 100 m riparian buffer{extra}",
                             f"Riparian forest {fp:.1f}% (exact watershed)",
                             "100 m buffer of the upstream network.")
    pending = _engine_first(ctx, mid)
    if pending is not None:
        return pending
    fp = _riparian_forest_pct(ctx)
    if fp is None:
        return _streamcat_entry(ctx, mid, status="unavailable", source="EPA StreamCat riparian")
    return _streamcat_entry(ctx, mid, value=fp,
                            value_text=f"{fp:.0f}% riparian forest (100 m buffer){extra}",
                            field_value_text=f"Riparian forest {fp:.0f}%",
                            source="EPA StreamCat *wsrp100 forest",
                            note="Riparian forest % — proxy for canopy/corridor (EnviroAtlas in Phase 4).",
                            suggested_likert=likert.suggest(mid, fp))


def _riparian_veg(ctx, mid):
    """Buffer/corridor evidence from natural riparian vegetation (forest+shrub+grassland+wetland)."""
    veg = _engine_riparian_veg_pct(ctx)
    if veg is not None:
        return _engine_entry(ctx, mid, veg,
                             f"{veg:.1f}% natural vegetation in the 100 m riparian buffer "
                             "(forest + shrub + grassland + wetland)",
                             f"Riparian natural veg {veg:.1f}% (exact watershed)",
                             "100 m buffer of the upstream network.")
    pending = _engine_first(ctx, mid)
    if pending is not None:
        return pending
    v = _riparian_natural_veg_pct(ctx)
    if v is None:
        return _streamcat_entry(ctx, mid, status="unavailable", source="EPA StreamCat riparian")
    return _streamcat_entry(ctx, mid, value=v,
                            value_text=f"{v:.0f}% natural riparian vegetation (100 m buffer)",
                            field_value_text=f"Riparian vegetation {v:.0f}%",
                            source="EPA StreamCat *wsrp100 vegetation",
                            note="Natural riparian vegetation (forest, shrub, grassland, wetland) in the "
                                 "100 m buffer. In grassland or arid ecoregions the natural buffer is "
                                 "non-forest; verify on the aerial basemap.",
                            suggested_likert=likert.suggest(mid, v))


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
    if ag_eng is not None or k_eng is not None:
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
        e = _engine_entry(ctx, mid, slope, "; ".join(parts), fvt,
                          "Screening context; run the cross-section tool for a Shields "
                          "transport-capacity analysis.")
        e.suggested_likert = None
        e.confidence = "L"
        return e
    pending = _engine_first(ctx, mid)
    if pending is not None:
        return pending
    s = _sc(ctx)
    c, h = s.get("pctcrop2019ws"), s.get("pcthay2019ws")
    ag = round((c or 0.0) + (h or 0.0), 1) if (c is not None or h is not None) else None
    k = s.get("kffactws")
    if slope is None and ag is None and k is None:
        return EvidenceResult(mid, status="unavailable", source="NHDPlus VAA / StreamCat")
    parts = []
    if slope is not None:
        parts.append(f"channel slope {slope:.4f} m/m")
    if ag is not None:
        parts.append(f"watershed agriculture {ag:.0f}%")
    if k is not None:
        parts.append(f"soil K {k:.2f}")
    fvt = (f"Slope {slope:.4f} m/m" if slope is not None
           else f"Agriculture {ag:.0f}%" if ag is not None else f"Soil K {k:.2f}")
    return _streamcat_entry(ctx, mid, value=slope, value_text="; ".join(parts),
                            field_value_text=fvt, confidence="L",
                            source="NHDPlus VAA slope + StreamCat %ag + K",
                            note="Screening context; run the cross-section tool for a Shields "
                                 "transport-capacity analysis (Phase 5).")


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
    return EvidenceResult(mid, value_text="observed median — " + "; ".join(parts),
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
                          note="Natural intermittency is not artificial dewatering — check NWIS "
                               "zero-flow days and imagery.")


def _land_use_change_suggestion(d: float) -> str:
    return ("Strongly Agree" if d < 1 else "Agree" if d < 3
            else "Disagree" if d < 6 else "Strongly Disagree")


def ev_land_use_change(ctx):
    mid = "catchment-hydrology-land-use-change"
    a_eng, b_eng = _eng(ctx, "imperviousPct2001Watershed"), _eng(ctx, "imperviousPctWatershed")
    if a_eng is not None and b_eng is not None:
        d = round(float(b_eng) - float(a_eng), 1)
        e = _engine_entry(ctx, mid, d,
                          f"impervious {float(a_eng):.1f}% (2001) → {float(b_eng):.1f}% (2021), "
                          f"Δ {d:+.1f} pts (exact watershed)",
                          f"Impervious change {d:+.1f} pts (exact watershed)",
                          "NLCD 2001 to 2021 impervious-cover change as a proxy for land "
                          "conversion.")
        e.suggested_likert = _land_use_change_suggestion(d)
        return e
    pending = _engine_first(ctx, mid)
    if pending is not None:
        return pending
    s = _sc(ctx)
    a, b = s.get("pctimp2001ws"), s.get("pctimp2019ws")
    if a is None or b is None:
        return _streamcat_entry(ctx, mid, status="unavailable",
                                source="EPA StreamCat (NLCD 2001/2019)",
                                source_url="https://www.mrlc.gov/viewer/",
                                note="Compare NLCD 2001 vs 2019 in the MRLC viewer.")
    d = round(b - a, 1)
    return _streamcat_entry(ctx, mid, value=d,
                            value_text=f"impervious {a:.1f}% (2001) → {b:.1f}% (2019), Δ {d:+.1f} pts",
                            field_value_text=f"Impervious change {d:+.1f} pts",
                            source="EPA StreamCat pctimp2001/2019 (NLCD)",
                            note="Impervious-cover change 2001→2019 as a proxy for land conversion.",
                            suggested_likert=_land_use_change_suggestion(d))


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
    storage = _eng(ctx, "damStoragePerSqkm")
    dam_eng = (round(float(storage) * ACRE_FT_PER_KM2_TO_M3_PER_KM2, 0)
               if storage is not None else None)
    if dam_eng is not None:
        parts = []
        if f and f.get("baseflow_ratio") is not None:
            parts.append(f"baseflow ratio Q90/Q50 = {f['baseflow_ratio']} (gage {f['site']})")
        parts.append(f"upstream normal storage {dam_eng:.0f} m3/km2 (exact watershed)")
        fvt = (f"Baseflow ratio {f['baseflow_ratio']}" if f and f.get("baseflow_ratio") is not None
               else f"Dam storage {dam_eng:.0f} m3/km2 (exact watershed)")
        e = _engine_entry(ctx, mid, dam_eng, "; ".join(parts), fvt,
                          "Compare to a reference or unregulated regime (TNC IHA) to judge "
                          "alteration.")
        e.suggested_likert = None
        e.confidence = "L"
        return e
    pending = _engine_first(ctx, mid)
    if pending is not None:
        return pending
    dam = _sc(ctx).get("damnrmstorws")    # ws-suffixed column (see ev_road_density)
    if not f and dam is None:
        return EvidenceResult(mid, status="unavailable", source="USGS NWIS / StreamCat")
    parts = []
    if f and f.get("baseflow_ratio") is not None:
        parts.append(f"baseflow ratio Q90/Q50 = {f['baseflow_ratio']} (gage {f['site']})")
    if dam:
        parts.append(f"upstream dam storage {dam:.0f}")
    if f and f.get("baseflow_ratio") is not None:
        fvt = f"Baseflow ratio {f['baseflow_ratio']}"
    elif dam:
        fvt = f"Dam storage {dam:.0f}"
    else:
        fvt = "Flow regime limited context"
    return _streamcat_entry(ctx, mid, value_text="; ".join(parts) or "limited flow context",
                            field_value_text=fvt, confidence="L",
                            source="USGS NWIS + StreamCat dam storage", source_url="",
                            note="Compare to a reference/unregulated regime (TNC IHA) to judge alteration.")


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


def _stamp_anchor_labels(out: dict[str, dict], anchor: Optional[dict]) -> None:
    """COMID-keyed StreamCat entries on a site outside NHDPlus V2 say which
    reach they describe."""
    label = engine_prefill.anchor_label(anchor)
    if not label:
        return
    for mid in ANCHOR_LABELED_METRICS:
        ev = out.get(mid)
        if ev and ev.get("origin") == "streamcat":
            ev["anchor_label"] = label


async def pull(ctx_inputs: dict, *, progress: Optional[dict] = None,
               engine: Optional[dict] = None) -> dict:
    """Pull desktop evidence for the Phase-3 metrics. Returns {metricId: evidence-dict}.

    ``engine`` is the app's site-engine state (``{"status": idle | running |
    ok | failed | refused | unavailable, "record", "reason"}``); None runs the
    engine inline. The engine's values are the FIRST source for every metric it
    covers; StreamCat values are the labeled fallback.
    """
    ctx = _ctx_from_inputs(ctx_inputs)
    ctx.extras["site_anchor"] = ctx_inputs.get("siteAnchor") or {}
    state = _resolve_engine(ctx_inputs, engine)
    ctx.extras["engine"] = state
    ctx.extras["engine_metrics"] = engine_prefill.engine_metrics(state.get("record"))
    if (state.get("record") or {}).get("engineVersion"):
        _VERSION_HINT["version"] = state["record"]["engineVersion"]

    # concurrent network prefetch (off the event loop); each never raises
    sc_ws, sc_rp, nid, tn, tp, flow, wet, roads = await asyncio.gather(
        _thread(streamcat.metrics_by_comid, ctx.comid, STREAMCAT_WS),
        _thread(streamcat.metrics_by_comid, ctx.comid, STREAMCAT_RP, "riparian_watershed"),
        _thread(nid_barriers.barriers_near, ctx.lat, ctx.lon, 1.0),
        _thread(wqp.median_value, "tn", ctx.lat, ctx.lon),
        _thread(wqp.median_value, "tp", ctx.lat, ctx.lon),
        _thread(nwis.flow_stats, ctx.lat, ctx.lon, ctx.drainage_area_sqkm),
        _thread(nwi.wetlands_near, ctx.lat, ctx.lon),
        _thread(tiger_roads.roads_near, ctx.lat, ctx.lon),
    )
    ctx.extras.update(streamcat=sc_ws, streamcat_rp=sc_rp, nid=nid, tn=tn, tp=tp,
                      flow=flow, nwi=wet, tiger=roads)

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
    _stamp_anchor_labels(out, ctx.extras.get("site_anchor"))
    return out
