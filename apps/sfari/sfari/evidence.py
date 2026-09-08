"""Desktop evidence pull: the STAF site engine's HR reach watershed values, the
StreamCat lookup engine by NHDPlus V2 COMID, and the direct services.

Prefetches once per pull (NID dams near the reach, WQP nutrient medians, the
nearest comparable NWIS gage, NWI wetlands, and the StreamCat row of the
site's COMID), then runs the desktop-metric adapters (pure functions reading
``ctx.extras``). Each returns an :class:`EvidenceResult` with a value, a
suggested Likert where a national break table exists, a confidence, and a
source, shown in the worksheet to SUPPORT the user's scoring; it never
auto-scores. Adapters never raise; on failure they degrade to
``status='unavailable'`` with a deep-link.

Two watershed engines (2026-09-07):

* The STAF site engine answers every watershed metric from the HR reach
  watershed (``origin="engine"``), and the reach cross-section ratios from
  nine 3DEP sections on the assessment reach. While it runs the entry is
  ``pending``.
* The StreamCat lookup engine answers by COMID (``origin="streamcat"``): the
  EPA modeled integrity indices that exist only per NHDPlus V2 reach (HYD,
  CONN, CHEM) and, when the engine has no value for it (it failed, refused,
  never ran, or left the value out of an ok record), the StreamCat analog of a
  watershed value, labeled with the reach it describes (``anchor_label``) and
  why it stands in (``fallback_reason``). A value built from several keys
  (both impervious years, the wetland classes, the riparian classes) takes
  every key from one basin, never an engine year beside a StreamCat year. On a stream
  outside V2 the COMID is the nearest StreamCat reach downstream
  (``comid_anchor``), named with the routed distance and the drainage-area
  ratio on every such value. Without a COMID there is no StreamCat value.
* Direct services stay ``origin="pull"``.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import anyio

from . import comid_anchor, engine_prefill, likert
from .datasources import nid_barriers, nwi, nwis, streamcat, wqp
from .metrics.base import AnalysisContext
from .models import EvidenceResult

FCODE_LABEL = {46006: "perennial", 46003: "intermittent", 46007: "ephemeral",
               55800: "artificial path", 33600: "canal/ditch"}

ACRE_FT_PER_KM2_TO_M3_PER_KM2 = 1233.48184
STREAMCAT_URL = comid_anchor.STREAMCAT_URL

# StreamCat base names fetched once per COMID (the API answers every area of
# interest for each: ws, cat, wsrp100, catrp100).
STREAMCAT_NAMES = [
    "pctimp2019", "pctimp2001", "pctcrop2019", "pcthay2019",
    "pctwdwet2019", "pcthbwet2019",
    "pctconif2019", "pctdecid2019", "pctmxfst2019", "pctshrb2019", "pctgrs2019",
    "kffact", "rddens", "rdcrs", "damdens", "damnrmstor",
    # the EPA modeled integrity indices (0-1), per V2 reach only
    "hyd", "sed", "chem", "conn", "temp", "habt",
]

# engine metric key -> the StreamCat columns whose sum is its analog
_SC_COLUMNS = {
    "imperviousPctWatershed": ("pctimp2019ws",),
    "imperviousPct2001Watershed": ("pctimp2001ws",),
    "cropPctWatershed": ("pctcrop2019ws",),
    "hayPasturePctWatershed": ("pcthay2019ws",),
    "roadDensity": ("rddensws",),
    "roadCrossingDensity": ("rdcrsws",),
    "woodyWetlandPctWatershed": ("pctwdwet2019ws",),
    "herbWetlandPctWatershed": ("pcthbwet2019ws",),
    "forestPctRiparian": ("pctconif2019wsrp100", "pctdecid2019wsrp100", "pctmxfst2019wsrp100"),
    "shrubPctRiparian": ("pctshrb2019wsrp100",),
    "grasslandPctRiparian": ("pctgrs2019wsrp100",),
    "woodyWetlandPctRiparian": ("pctwdwet2019wsrp100",),
    "herbWetlandPctRiparian": ("pcthbwet2019wsrp100",),
    "soilKFactor": ("kffactws",),
    "damDensityPerSqkm": ("damdensws",),
}
_INTEGRITY_TIERS = ("Below 0.40 reads poor and above 0.70 good, the EASI integration "
                    "tiers.")


async def _thread(fn, *a):
    return await anyio.to_thread.run_sync(fn, *a)


async def _none():
    return None


def _ctx_from_inputs(ci: dict) -> AnalysisContext:
    return AnalysisContext(
        lat=ci["lat"], lon=ci["lon"], comid=ci.get("comid"), huc8=ci.get("huc8"),
        watershed_geojson=ci.get("watershed_geojson"), reach_geojson=ci.get("reach_geojson"),
        drainage_area_sqkm=ci.get("drainage_area_sqkm"), slope=ci.get("slope"),
        fcode=ci.get("fcode"), stream_order=ci.get("stream_order"), sinuosity=ci.get("sinuosity"))


# --------------------------------------------------------------------------- #
# the two engines' state on this run
# --------------------------------------------------------------------------- #
def _engine_state(ctx) -> dict:
    return ctx.extras.get("engine") or {"status": "idle"}


def _eng(ctx, key: str):
    return (ctx.extras.get("engine_metrics") or {}).get(key)


def _anchor(ctx) -> Optional[dict]:
    return ctx.extras.get("site_anchor")


def _sc_row(ctx) -> dict:
    return ctx.extras.get("streamcat") or {}


def _sc(ctx, *cols) -> Optional[float]:
    """The sum of StreamCat columns, None when any is missing or not numeric."""
    row = _sc_row(ctx)
    total = 0.0
    for col in cols:
        try:
            total += float(row.get(col))
        except (TypeError, ValueError):
            return None
    return total if cols else None


def _integrity(ctx, key: str) -> tuple[Optional[float], Optional[float]]:
    """``(catchment, watershed)`` of an EPA modeled integrity index (0-1)."""
    return _sc(ctx, f"{key}cat"), _sc(ctx, f"{key}ws")


def _fallback_allowed(ctx) -> bool:
    """StreamCat may stand in for a watershed value the engine has no value
    for, whatever the engine's state (failed, refused, never ran, or an ok
    record that left the value out), as long as the site's COMID has a
    StreamCat row. The engine is still asked first (``_wv``)."""
    return (_engine_state(ctx).get("status") != "running") and bool(_sc_row(ctx))


def _sc_for(ctx, key: str) -> Optional[float]:
    cols = _SC_COLUMNS.get(key)
    return _sc(ctx, *cols) if cols else None


def _wv(ctx, key: str) -> tuple[Optional[float], Optional[str]]:
    """A watershed value and its origin: the engine's, else the StreamCat
    analog when the engine has none, else ``(None, None)``."""
    v = _eng(ctx, key)
    if v is not None:
        return float(v), "engine"
    if _fallback_allowed(ctx):
        s = _sc_for(ctx, key)
        if s is not None:
            return s, "streamcat"
    return None, None


def _wv_all(ctx, keys) -> tuple[Optional[dict], Optional[str]]:
    """Several keys from ONE basin: the engine's values when it has every
    key, else the StreamCat analogs when the row has every column, else
    ``(None, None)``. Keeps a two-year change or a class sum on one basis."""
    eng = {k: _eng(ctx, k) for k in keys}
    if all(v is not None for v in eng.values()):
        return {k: float(v) for k, v in eng.items()}, "engine"
    if _fallback_allowed(ctx):
        sc = {k: _sc_for(ctx, k) for k in keys}
        if all(v is not None for v in sc.values()):
            return sc, "streamcat"
    return None, None


def _sfx(ctx, origin: str) -> str:
    """The value-text suffix naming whose watershed a value describes."""
    if origin == "streamcat":
        return comid_anchor.basin_suffix(_anchor(ctx))
    return " (HR reach watershed)"


def _fallback_reason(ctx) -> str:
    st = _engine_state(ctx)
    status = st.get("status") or "idle"
    if status == "idle":
        return "The STAF site engine has not run for this site."
    if status == "ok":
        return ("The STAF site engine ran but did not return this value for the HR "
                "reach watershed.")
    return f"STAF site engine {status}: {st.get('reason') or 'no detail'}."


_VERSION_HINT: dict = {}


def _engine_version_hint() -> Optional[str]:
    return _VERSION_HINT.get("version") or engine_prefill.engine_version()


def _pending(mid: str) -> EvidenceResult:
    return EvidenceResult(
        mid, status="pending", origin="engine",
        source=engine_prefill.engine_label(_engine_version_hint()),
        source_url=engine_prefill.ENGINE_URL,
        note="STAF site engine running; the HR reach watershed value replaces this "
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


def _streamcat_entry(ctx, mid: str, value, value_text: str, field_text: str,
                     note: str = "", *, fallback: bool = True,
                     suggest: bool = True) -> EvidenceResult:
    """A value from the StreamCat lookup engine, labeled with the reach it
    describes; ``fallback`` records why it stands in for the engine."""
    anchor = _anchor(ctx)
    return EvidenceResult(
        mid, value=value, value_text=value_text, field_value_text=field_text,
        suggested_likert=(likert.suggest(mid, value) if suggest and value is not None
                          else None),
        confidence="M", source=comid_anchor.source_label(anchor),
        source_url=STREAMCAT_URL,
        note=(note + " " if note else "") + comid_anchor.basin_note(anchor),
        origin="streamcat", anchor_label=comid_anchor.describes(anchor),
        fallback_reason=_fallback_reason(ctx) if fallback else "")


def _entry(ctx, mid: str, origin: str, value, value_text: str, field_text: str,
           note: str = "") -> EvidenceResult:
    if origin == "streamcat":
        return _streamcat_entry(ctx, mid, value, value_text, field_text, note)
    return _engine_entry(ctx, mid, value, value_text, field_text, note)


def _missing(ctx, mid: str, what: str) -> EvidenceResult:
    """Neither engine answered this metric: pending while the site engine runs,
    unavailable with the reason otherwise."""
    st = _engine_state(ctx)
    status = st.get("status") or "idle"
    if status == "running":
        return _pending(mid)
    if status == "ok":
        note = f"The STAF site engine did not return {what} for this watershed."
    else:
        note = _fallback_reason(ctx)
        c = comid_anchor.comid(_anchor(ctx))
        if c is None:
            note += " No StreamCat reach is known for this site."
        elif not _sc_row(ctx):
            note += f" The StreamCat lookup engine did not answer for COMID {c}."
        else:
            note += f" The StreamCat lookup engine has no {what} for COMID {c} either."
    return EvidenceResult(mid, status="unavailable", origin="engine",
                          source=engine_prefill.engine_label(_engine_version_hint()),
                          source_url=engine_prefill.ENGINE_URL, note=note)


_engine_missing = _missing      # the name the 2026-09-05 tests used


# Natural riparian vegetation (100 m buffer): forest + shrub + grassland + wetland. Broader than
# forest alone so grassland/arid streams are not falsely scored low for a non-forest but intact
# natural buffer. Backs the buffer/corridor metrics; canopy shade stays forest-only (ev_canopy).
_RIPARIAN_KEYS = ("forestPctRiparian", "shrubPctRiparian", "grasslandPctRiparian",
                  "woodyWetlandPctRiparian", "herbWetlandPctRiparian")


def _riparian_veg_pct(ctx) -> tuple[Optional[float], Optional[str]]:
    vals, origin = _wv_all(ctx, _RIPARIAN_KEYS)
    if vals is None:
        return None, None
    return round(sum(vals.values()), 1), origin


# Alternate catchment-hydrology land-cover indicator: watershed agriculture (crop + hay and
# pasture over the watershed). ``ev_impervious`` compares it against impervious and,
# when agriculture is the more limiting land-cover pressure, advises scoring the function on
# agriculture instead (mirrors the EASI selectable indicator). Likert order: lower rank = worse.
_AG_COVER_KEY = "catchment-hydrology-agricultural-cover"
_LIKERT_RANK = {"Strongly Disagree": 0, "Disagree": 1, "Neutral": 2,
                "Agree": 3, "Strongly Agree": 4}


def _ag_pct(ctx, origin: Optional[str] = None) -> Optional[float]:
    """Crop + hay and pasture from one basin; with ``origin`` only when that
    basin is the one the companion value came from."""
    vals, o = _wv_all(ctx, ("cropPctWatershed", "hayPasturePctWatershed"))
    if vals is None or (origin is not None and o != origin):
        return None
    return round(vals["cropPctWatershed"] + vals["hayPasturePctWatershed"], 1)


def _land_cover_entry(ctx, mid, v, ag, suffix, make):
    imp_likert = likert.suggest(mid, v)
    suggested = imp_likert
    value_text = f"{v:.1f}% impervious{suffix}"
    note = ""
    if ag is not None:
        value_text = f"{v:.1f}% impervious, {ag:.1f}% agricultural land{suffix}"
        ag_likert = likert.suggest(_AG_COVER_KEY, ag)
        if ag_likert and _LIKERT_RANK.get(ag_likert, 9) < _LIKERT_RANK.get(imp_likert, 9):
            suggested = ag_likert
        note = (f"Land-cover indicators: impervious {v:.1f}% ({imp_likert}), "
                f"agricultural {ag:.1f}% ({ag_likert}). The more limiting one is suggested.")
    return make(value_text, f"Impervious {v:.1f}%{suffix}", suggested, note)


# --------------------------------------------------------------------------- #
# adapters: AnalysisContext -> EvidenceResult (watershed metrics)
# --------------------------------------------------------------------------- #
def ev_impervious(ctx):
    mid = "catchment-hydrology-impervious-surface-area"
    imp, origin = _wv(ctx, "imperviousPctWatershed")
    if imp is None:
        return _missing(ctx, mid, "impervious cover")

    def make(value_text, field_text, suggested, note):
        e = _entry(ctx, mid, origin, round(imp, 1), value_text, field_text, note)
        e.suggested_likert = suggested
        e.confidence = "H" if origin == "engine" else "M"
        return e
    sfx = _sfx(ctx, origin)
    return _land_cover_entry(ctx, mid, imp, _ag_pct(ctx, origin), sfx, make)


def ev_road_density(ctx):
    mid = "catchment-hydrology-road-density"
    rd, origin = _wv(ctx, "roadDensity")
    if rd is None:
        return _missing(ctx, mid, "road density")
    sfx = _sfx(ctx, origin)
    what = ("TIGERweb primary + secondary + local roads clipped to the watershed."
            if origin == "engine" else "StreamCat road density (rddens).")
    return _entry(ctx, mid, origin, round(rd, 2),
                  f"{rd:.2f} km/km2 road density{sfx}", f"Roads {rd:.2f} km/km2{sfx}", what)


def ev_impoundments(ctx):
    mid = "catchment-hydrology-impoundments"
    dams = _eng(ctx, "damCount")
    if dams is not None:
        storage = _eng(ctx, "damStorageAcreFt")
        n = int(dams)
        txt = (f"{n} NID dam(s) in the watershed"
               + (f", {storage:,.0f} acre-ft normal storage" if storage is not None else ""))
        e = _engine_entry(ctx, mid, n, txt, f"Dams {n} in watershed (HR reach watershed)")
        e.suggested_likert = ("Strongly Agree" if n == 0 else "Agree" if n <= 2
                              else "Disagree" if n <= 5 else "Strongly Disagree")
        return e
    dens, origin = _wv(ctx, "damDensityPerSqkm")
    if dens is None:
        return _missing(ctx, mid, "the dam count")
    sfx = _sfx(ctx, origin)
    if origin == "engine":
        # one basin per value: an engine density never carries StreamCat storage
        e = _engine_entry(ctx, mid, round(dens, 3), f"{dens:.3f} dams/km2{sfx}",
                          f"Dams {dens:.3f}/km2{sfx}",
                          "NID dams in the HR reach watershed per km2; the count was not reported.")
        e.suggested_likert = None
        return e
    stor = _sc(ctx, "damnrmstorws")
    txt = f"{dens:.3f} dams/km2" + (f", {stor:,.0f} m3/km2 normal storage" if stor is not None
                                    else "") + sfx
    return _streamcat_entry(ctx, mid, round(dens, 3), txt, f"Dams {dens:.3f}/km2{sfx}",
                            "StreamCat dam density and normal storage per km2; the count "
                            "itself is not published.", suggest=False)


def ev_wetland(ctx):
    mid = "surface-water-storage-wetland-coverage"
    vals, origin = _wv_all(ctx, ("woodyWetlandPctWatershed", "herbWetlandPctWatershed"))
    if vals is None:
        return _missing(ctx, mid, "wetland cover")
    wet = round(vals["woodyWetlandPctWatershed"] + vals["herbWetlandPctWatershed"], 1)
    sfx = _sfx(ctx, origin)
    return _entry(ctx, mid, origin, wet, f"{wet:.1f}% wetland{sfx}", f"Wetlands {wet:.1f}%{sfx}")


def _riparian(ctx, mid, extra=""):
    fp, origin = _wv(ctx, "forestPctRiparian")
    if fp is None:
        return _missing(ctx, mid, "riparian forest cover")
    fp = round(fp, 1)
    sfx = _sfx(ctx, origin)
    return _entry(ctx, mid, origin, fp,
                  f"{fp:.1f}% forest in the 100 m riparian buffer{extra}",
                  f"Riparian forest {fp:.1f}%{sfx}",
                  "100 m buffer of the upstream network." if origin == "engine"
                  else "StreamCat riparian forest (100 m buffer, wsrp100).")


def _riparian_veg(ctx, mid):
    """Buffer/corridor evidence from natural riparian vegetation (forest+shrub+grassland+wetland)."""
    veg, origin = _riparian_veg_pct(ctx)
    if veg is None:
        return _missing(ctx, mid, "riparian vegetation")
    sfx = _sfx(ctx, origin)
    return _entry(ctx, mid, origin, veg,
                  f"{veg:.1f}% natural vegetation in the 100 m riparian buffer "
                  "(forest + shrub + grassland + wetland)",
                  f"Riparian natural veg {veg:.1f}%{sfx}",
                  ("100 m buffer of the upstream network. " if origin == "engine"
                   else "StreamCat riparian classes (100 m buffer, wsrp100). ")
                  + "In grassland or arid ecoregions the natural buffer is non-forest; "
                  "verify on the aerial basemap.")


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
    k, k_origin = _wv(ctx, "soilKFactor")
    # agriculture rides beside K only from K's basin; alone it sets the basis
    ag = _ag_pct(ctx, k_origin) if k is not None else _ag_pct(ctx)
    if ag is None and k is None:
        return _missing(ctx, mid, "soil erodibility and agriculture")
    origin = k_origin or _wv_all(ctx, ("cropPctWatershed", "hayPasturePctWatershed"))[1] or "engine"
    sfx = _sfx(ctx, origin)
    parts = []
    if slope is not None:
        parts.append(f"channel slope {slope:.4f} m/m")
    if ag is not None:
        parts.append(f"watershed agriculture {ag:.0f}%{sfx}")
    if k is not None:
        parts.append(f"soil K {k:.2f}{sfx}")
    fvt = (f"Slope {slope:.4f} m/m" if slope is not None
           else f"Agriculture {ag:.0f}%{sfx}" if ag is not None
           else f"Soil K {k:.2f}{sfx}")
    e = _entry(ctx, mid, origin, slope, ", ".join(parts), fvt,
               "Screening context; run the cross-section tool for a Shields "
               "transport-capacity analysis.")
    e.suggested_likert = None
    e.confidence = "L"
    return e


def _land_use_change_suggestion(d: float) -> str:
    return ("Strongly Agree" if d < 1 else "Agree" if d < 3
            else "Disagree" if d < 6 else "Strongly Disagree")


def ev_land_use_change(ctx):
    mid = "catchment-hydrology-land-use-change"
    # both years from one basin: the engine's 2001 baseline and 2021, else
    # StreamCat's 2001 and 2019 by COMID, never one year from each
    vals, origin = _wv_all(ctx, ("imperviousPct2001Watershed", "imperviousPctWatershed"))
    if vals is None:
        return _missing(ctx, mid, "impervious cover for 2001 and 2021")
    a, b = vals["imperviousPct2001Watershed"], vals["imperviousPctWatershed"]
    later = "2021" if origin == "engine" else "2019"
    d = round(b - a, 1)
    sfx = _sfx(ctx, origin)
    e = _entry(ctx, mid, origin, d,
               f"impervious {a:.1f}% (2001) → {b:.1f}% ({later}), Δ {d:+.1f} pts{sfx}",
               f"Impervious change {d:+.1f} pts{sfx}",
               f"NLCD 2001 to {later} impervious-cover change as a proxy for land "
               "conversion.")
    e.suggested_likert = _land_use_change_suggestion(d)
    return e


def ev_natural_flow_regime(ctx):
    mid = "streamflow-regime-channel-natural-flow-regime"
    f = ctx.extras.get("flow")
    ratio = f.get("baseflow_ratio") if f else None
    storage = _eng(ctx, "damStoragePerSqkm")
    origin = "engine"
    dam = None
    if storage is not None:
        dam = round(float(storage) * ACRE_FT_PER_KM2_TO_M3_PER_KM2, 0)
    elif _fallback_allowed(ctx):
        sc_stor = _sc(ctx, "damnrmstorws")
        if sc_stor is not None:
            dam, origin = round(sc_stor, 0), "streamcat"
    if dam is not None:
        sfx = _sfx(ctx, origin)
        parts = []
        if ratio is not None:
            parts.append(f"baseflow ratio Q90/Q50 = {ratio} (gage {f['site']})")
        parts.append(f"upstream normal storage {dam:.0f} m3/km2{sfx}")
        fvt = (f"Baseflow ratio {ratio}" if ratio is not None
               else f"Dam storage {dam:.0f} m3/km2{sfx}")
        e = _entry(ctx, mid, origin, dam, ", ".join(parts), fvt,
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
    return _missing(ctx, mid, "upstream dam storage")


def ev_concentrated_inputs(ctx):
    """Road-stream crossings: where roads meet the stream network in the
    watershed (the STAF site engine counts them on the NHDPlus HR network;
    StreamCat rdcrs stands in by COMID)."""
    mid = "reach-inflow-concentrated-flow-inputs"
    dens, origin = _wv(ctx, "roadCrossingDensity")
    if dens is None:
        return _missing(ctx, mid, "road-stream crossings")
    sfx = _sfx(ctx, origin)
    n = _eng(ctx, "roadCrossings") if origin == "engine" else None
    txt = (f"{int(n)} road-stream crossing(s), " if n is not None else "") + \
        f"{dens:.2f} crossings/km2{sfx}"
    fvt = f"Road crossings {dens:.2f}/km2{sfx}"
    e = _entry(ctx, mid, origin, round(dens, 2), txt, fvt,
               ("TIGERweb roads crossing the NHDPlus HR network in the watershed, a proxy "
                "for concentrated runoff inputs; outfalls and ditches need local layers."
                if origin == "engine" else
                "StreamCat road-stream crossings (rdcrs), a proxy for concentrated runoff "
                "inputs; outfalls and ditches need local layers."))
    e.suggested_likert = None
    e.confidence = "L"
    return e


# --------------------------------------------------------------------------- #
# reach cross-sections (the engine's xsection family, nine 3DEP sections)
# --------------------------------------------------------------------------- #
def _xs_note(ctx) -> str:
    return ("Reach median of nine USGS 3DEP cross-sections on the assessment reach, "
            "bankfull from the Bieger 2015 regional regression. Screening geometry; "
            "the cross-section tool refines it.")


def ev_overbank_frequency(ctx):
    mid = "high-flow-dynamics-overbank-flow-frequency"
    bhr = _eng(ctx, "bankHeightRatio")
    if bhr is None:
        return _missing(ctx, mid, "the bank-height ratio")
    bhr = float(bhr)
    e = _engine_entry(ctx, mid, round(bhr, 2),
                      f"bank height ratio {bhr:.2f} (reach median, 3DEP sections)",
                      f"BHR {bhr:.2f} (reach)", _xs_note(ctx))
    e.suggested_likert = None
    e.confidence = "L"
    return e


def ev_peak_capacity(ctx):
    mid = "high-flow-dynamics-peak-flow-capacity-morphological-check"
    er = _eng(ctx, "entrenchmentRatio")
    w = _eng(ctx, "bankfullWidthM")
    if er is None and w is None:
        return _missing(ctx, mid, "the reach cross-section geometry")
    parts = []
    if w is not None:
        parts.append(f"regional bankfull width {float(w):.1f} m")
    if er is not None:
        parts.append(f"entrenchment ratio {float(er):.2f} (reach median, 3DEP sections)")
    fvt = (f"ER {float(er):.2f} (reach)" if er is not None
           else f"Bankfull width {float(w):.1f} m")
    e = _engine_entry(ctx, mid, round(float(er), 2) if er is not None else round(float(w), 1),
                      ", ".join(parts), fvt, _xs_note(ctx))
    e.suggested_likert = None
    e.confidence = "L"
    return e


# --------------------------------------------------------------------------- #
# direct services, with the COMID-only indices beside them
# --------------------------------------------------------------------------- #
def _integrity_entry(ctx, mid: str, key: str, label: str, what: str) -> Optional[EvidenceResult]:
    """An EPA modeled integrity index as the evidence (origin ``streamcat``),
    None when the COMID has none."""
    cat, ws = _integrity(ctx, key)
    if ws is None and cat is None:
        return None
    lead = ws if ws is not None else cat
    txt = f"{label} integrity {lead:.2f}"
    if ws is not None and cat is not None:
        txt = f"{label} integrity {ws:.2f} watershed, {cat:.2f} catchment"
    txt += " (EPA modeled index" + comid_anchor.basin_suffix(_anchor(ctx)).replace(" (", ", ", 1)
    return _streamcat_entry(
        ctx, mid, round(lead, 2), txt, f"{label} integrity {lead:.2f}",
        f"Modeled landscape-integrity index for the NHDPlus V2 reach, not {what}. "
        + _INTEGRITY_TIERS, fallback=False, suggest=False)


def _integrity_context(ctx, key: str, label: str) -> str:
    """`` · HYD integrity 0.62 (StreamCat, COMID 9327042)`` or empty."""
    _cat, ws = _integrity(ctx, key)
    c = comid_anchor.comid(_anchor(ctx))
    if ws is None or c is None:
        return ""
    return f" · {label} integrity {ws:.2f} (StreamCat, COMID {c})"


def ev_np(ctx):
    mid = "nutrient-cycling-n-p-concentrations"
    tn, tp = ctx.extras.get("tn"), ctx.extras.get("tp")
    if tn is None and tp is None:
        chem = _integrity_entry(ctx, mid, "chem", "CHEM", "an observed concentration")
        if chem is not None:
            return chem
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
        hyd = _integrity_entry(ctx, mid, "hyd", "HYD", "the flow permanence code")
        if hyd is not None:
            return hyd
        return EvidenceResult(mid, status="unavailable", source="NHDPlus FCODE")
    lab = FCODE_LABEL.get(fc, f"FCODE {fc}")
    hyd_ctx = _integrity_context(ctx, "hyd", "HYD")
    txt = f"NHD flow permanence: {lab}" + hyd_ctx
    bfi = _eng(ctx, "baseflowIndexPct")
    sources = ["NHDPlus FCODE"]
    if hyd_ctx:
        sources.append("StreamCat HYD")
    if bfi is not None:
        txt += f" · base-flow index {float(bfi):.0f}% (HR reach watershed)"
        sources.append("STAF site engine base-flow index")
    return EvidenceResult(mid, value=fc, value_text=txt,
                          field_value_text=f"Flow class {lab}",
                          confidence="M", source=" + ".join(sources),
                          note="Natural intermittency is not artificial dewatering. Check NWIS "
                               "zero-flow days and imagery. The HYD index is the EPA modeled "
                               "hydrologic integrity of the NHDPlus V2 reach; the base-flow "
                               "index is the share of streamflow that is base flow.")


def ev_flow_permanence(ctx):
    mid = "streamflow-regime-flow-permanence"
    f = ctx.extras.get("flow")
    if not f:
        hyd = _integrity_entry(ctx, mid, "hyd", "HYD", "gaged flow")
        if hyd is not None:
            hyd.note = "No comparable nearby gage with daily flow. " + hyd.note
            return hyd
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
        conn = _integrity_entry(ctx, mid, "conn", "CONN", "a barrier inventory")
        if conn is not None:
            conn.note = "The dam inventory did not answer near this reach. " + conn.note
            return conn
        return EvidenceResult(mid, status="unavailable", source="USACE NID / Aquatic Barrier Inventory",
                              source_url="https://connectivity.sarpdata.com/")
    n = len(nid)
    sug = ("Strongly Agree" if n == 0 else "Agree" if n <= 1 else "Disagree" if n <= 3 else "Strongly Disagree")
    ctx_txt = _integrity_context(ctx, "conn", "CONN")
    return EvidenceResult(mid, value=n, value_text=f"{n} dam/barrier(s) within ~1 mi" + ctx_txt,
                          field_value_text=f"Barriers {n} dam(s)",
                          confidence="M",
                          source="USACE NID (+ National Aquatic Barrier Inventory)"
                                 + (" + StreamCat CONN" if ctx_txt else ""),
                          source_url="https://connectivity.sarpdata.com/",
                          note="Dam count near the reach; add road-crossing / aquatic barriers for "
                               "passability." + (" The CONN index is the EPA modeled connectivity "
                                                 "integrity of the NHDPlus V2 reach." if ctx_txt
                                                 else ""),
                          suggested_likert=sug)


def ev_lateral_inundation(ctx):
    mid = "floodplain-connectivity-lateral-floodplain-inundation"
    w = ctx.extras.get("nwi")
    er = _eng(ctx, "entrenchmentRatio")
    if not w or not w.get("count"):
        if er is not None:
            e = _engine_entry(ctx, mid, round(float(er), 2),
                              f"entrenchment ratio {float(er):.2f} (reach median, 3DEP "
                              "sections); no NWI wetland feature near the reach",
                              f"ER {float(er):.2f} (reach)",
                              _xs_note(ctx) + " Inspect NWI and the 3DEP hillshade for "
                              "floodplain features.")
            e.suggested_likert = None
            e.confidence = "L"
            return e
        return EvidenceResult(mid, status="unavailable", source="USFWS NWI / 3DEP",
                              source_url="https://www.fws.gov/program/national-wetlands-inventory/wetlands-mapper",
                              note="Inspect NWI + 3DEP hillshade for floodplain features.")
    txt = f"{w['count']} NWI wetland feature(s), {w['acres']} ac near the reach"
    if er is not None:
        txt += f" · entrenchment ratio {float(er):.2f} (reach median, 3DEP sections)"
    return EvidenceResult(mid, value=w["acres"], value_text=txt,
                          field_value_text=f"Floodplain {w['count']} NWI feature(s)",
                          confidence="L",
                          source="USFWS National Wetlands Inventory"
                                 + (" + STAF site engine reach cross-sections" if er is not None else ""),
                          source_url="https://www.fws.gov/program/national-wetlands-inventory/wetlands-mapper",
                          note="Adjacent wetland/floodplain features (screening; confirm inundation with 3DEP).")


REGISTRY = {
    "catchment-hydrology-impervious-surface-area": ev_impervious,
    "catchment-hydrology-road-density": ev_road_density,
    "catchment-hydrology-impoundments": ev_impoundments,
    "surface-water-storage-wetland-coverage": ev_wetland,
    "reach-inflow-concentrated-flow-inputs": ev_concentrated_inputs,
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
    "high-flow-dynamics-overbank-flow-frequency": ev_overbank_frequency,
    "high-flow-dynamics-peak-flow-capacity-morphological-check": ev_peak_capacity,
}

# The watershed metrics the site engine answers first (``origin="engine"``;
# the StreamCat analog stands in, labeled, when the engine could not answer).
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
# Engine metrics that read the reach cross-sections or the road crossings
# (the 0.3.0 and 0.4.0 families); the StreamCat rdcrs analog backs the last.
ENGINE_REACH_METRICS = (
    "reach-inflow-concentrated-flow-inputs",
    "high-flow-dynamics-overbank-flow-frequency",
    "high-flow-dynamics-peak-flow-capacity-morphological-check",
)
# Metrics the StreamCat lookup engine alone can answer when the direct service
# has nothing (the EPA modeled indices exist only per V2 reach).
STREAMCAT_ONLY_METRICS = (
    "streamflow-regime-flow-permanence",
    "watershed-connectivity-dewatered-or-intermittent-segments",
    "watershed-connectivity-upstream-and-downstream-barriers",
    "nutrient-cycling-n-p-concentrations",
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


def _resolve_anchor(ctx_inputs: dict) -> Optional[dict]:
    """The StreamCat reach: the classification the app attached, else a
    covered-reach anchor from a bare COMID (older sessions)."""
    anchor = ctx_inputs.get("siteAnchor")
    if isinstance(anchor, dict) and comid_anchor.comid(anchor) is not None:
        return anchor
    return comid_anchor.synthetic(ctx_inputs.get("comid"))


async def pull(ctx_inputs: dict, *, progress: Optional[dict] = None,
               engine: Optional[dict] = None) -> dict:
    """Pull desktop evidence for the supportable metrics. Returns {metricId: evidence-dict}.

    ``engine`` is the app's site-engine state (``{"status": idle | running |
    ok | failed | refused | unavailable, "record", "reason"}``); None runs the
    engine inline. The engine answers every watershed metric first; the
    StreamCat lookup engine answers by ``ctx_inputs["siteAnchor"]`` (or a bare
    ``comid``) for the COMID-only indices and as the labeled stand-in; the
    direct services answer the rest.
    """
    ctx = _ctx_from_inputs(ctx_inputs)
    state = _resolve_engine(ctx_inputs, engine)
    ctx.extras["engine"] = state
    ctx.extras["engine_metrics"] = engine_prefill.engine_metrics(state.get("record"))
    if (state.get("record") or {}).get("engineVersion"):
        _VERSION_HINT["version"] = state["record"]["engineVersion"]
    anchor = _resolve_anchor(ctx_inputs)
    ctx.extras["site_anchor"] = anchor
    comid = comid_anchor.comid(anchor)

    # concurrent network prefetch (off the event loop); each never raises
    nid, tn, tp, flow, wet, sc_row = await asyncio.gather(
        _thread(nid_barriers.barriers_near, ctx.lat, ctx.lon, 1.0),
        _thread(wqp.median_value, "tn", ctx.lat, ctx.lon),
        _thread(wqp.median_value, "tp", ctx.lat, ctx.lon),
        _thread(nwis.flow_stats, ctx.lat, ctx.lon, ctx.drainage_area_sqkm),
        _thread(nwi.wetlands_near, ctx.lat, ctx.lon),
        (_thread(streamcat.metrics_by_comid, comid, STREAMCAT_NAMES)
         if comid is not None else _none()),
    )
    ctx.extras.update(nid=nid, tn=tn, tp=tp, flow=flow, nwi=wet, streamcat=sc_row or {})

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
