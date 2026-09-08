"""Desktop auto-compute registry for DEEP.

Maps the desktop-derivable detailed metricIds (as used in the predefined
state-SQT assessments) to adapters that compute the RAW measured value, the
value DEEP then runs through the metric's reference curve, exactly like a
field-entered value. Adapters reuse EASI's datasource + geomorphology code:

- watershed land cover / impervious  <- the STAF site engine (HR reach watershed)
  when the bundle allows it, else the StreamCat lookup engine (NLCD fallback)
- regional landscape metrics (the ``spring-*ws`` ids: impervious, crops,
  wetland (combined, 2026-09-07) and the two wetland classes older bundles
  score, road and dam density, base flow index, road-stream crossings)  <- the
  same two engines, chosen per metric from the curve's own predictorSource
- reach geomorphic ratios ER / BHR / W:D  <- 3DEP DEM cross-section + Bieger bankfull

Every value carries a ``basis`` (``site-engine`` | ``streamcat`` | ``nlcd`` |
``3dep``) and a source label naming the engine. On a stream outside NHDPlus V2
the StreamCat label says which covered reach the value describes, with the
routed distance and the drainage-area ratio (reported, never enforced,
2026-09-07). NLCD over the HR reach watershed polygon answers a land-cover
id only when neither engine has a value for it.

Only a curated, reliably-computable subset is registered; every other metric
stays field entry. Adapters never raise: a failure or missing datum yields
``None`` and the metric is left blank/manual. Heavy datasource imports are lazy
so this module (and the framework tests) load without the geospatial stack.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .base import AnalysisContext

BASIS_SITE_ENGINE = "site-engine"
BASIS_STREAMCAT = "streamcat"
BASIS_NLCD = "nlcd"
BASIS_3DEP = "3dep"


@dataclass
class ComputedValue:
    value: float
    source: str
    confidence: str = "M"
    # True when the value came from the vendored site engine (HR reach watershed).
    # Rides into the measured-value state so the scoring layer can enforce the
    # train/serve pairing rule (engine values never score against curves
    # fitted on StreamCat predictors while the pairing mode is "refuse").
    engine: bool = False
    # Which engine or layer produced the value (the worksheet badge).
    basis: str = ""


_ADAPTERS: dict[str, Callable[[AnalysisContext], Optional[ComputedValue]]] = {}


def adapter(*metric_ids: str):
    def deco(fn):
        for mid in metric_ids:
            _ADAPTERS[mid] = fn
        return fn
    return deco


def computable_ids() -> set[str]:
    """The set of detailed metricIds DEEP can desktop-compute."""
    return set(_ADAPTERS)


# --------------------------------------------------------------------------- #
# Shared prefetch (cached on ctx.extras; lazy imports keep the module light)
# --------------------------------------------------------------------------- #
def site_engine_available() -> bool:
    """The vendored site engine and its geospatial stack are importable."""
    import importlib.util
    for mod in ("deep._vendor.site_engine", "requests", "shapely", "geopandas"):
        if importlib.util.find_spec(mod) is None:
            return False
    return True


def _engine_record(ctx: AnalysisContext) -> dict:
    """The HR reach watershed engine record for the site, cached on ctx.

    The adapters never run the engine: the app runs it once per site at
    Delineate and offers the StreamCat continuation when it fails, so the
    record is the one the app hands over (``ctx.extras["site_engine_prefetched"]``,
    ok status) or ``{}``, and a missing record means the StreamCat and NLCD
    layers answer, labeled (until 2026-09-07 a missing record re-ran the
    engine inline on the worker thread with the full default budget, exactly
    after the app's own run had failed). Never raises.
    """
    if "site_engine_record" not in ctx.extras:
        rec: dict = {}
        try:
            pre = ctx.extras.get("site_engine_prefetched")
            if isinstance(pre, dict) and pre.get("status") == "ok":
                rec = pre
        except Exception:  # noqa: BLE001
            rec = {}
        ctx.extras["site_engine_record"] = rec
    return ctx.extras["site_engine_record"]


def _engine_metric(ctx: AnalysisContext, key: str):
    """An engine value ONLY when the caller allowed it (``ctx.extras["allow_engine"]``,
    set per metric by :func:`compute_for` from the curve's own ``predictorSource``
    stamp, the bundle-level default for ids the bundle does not describe, and
    the pairing mode): against a StreamCat-fitted curve in ``refuse`` mode the
    engine must not supply scoring inputs, so the adapters fall back to the
    StreamCat/NLCD paths that curve was trained on."""
    if not ctx.extras.get("allow_engine"):
        return None
    rec = _engine_record(ctx)
    return ((rec.get("metrics") or {}).get(key) or {}).get("value")


def _engine_version(ctx: AnalysisContext) -> str:
    return str(_engine_record(ctx).get("engineVersion") or "")


def _engine_label(ctx: AnalysisContext) -> str:
    """``STAF site engine v0.2.0`` from the vendored vocabulary when present."""
    ver = _engine_version(ctx)
    try:
        from deep._vendor.site_engine import naming
        return naming.engine_label(ver or None)
    except Exception:  # noqa: BLE001 - vocabulary fallback
        return f"STAF site engine v{ver or 'unknown'}"


def _anchor(ctx: AnalysisContext) -> dict:
    return ctx.extras.get("site_anchor") or {}


def _describes(ctx: AnalysisContext) -> str:
    """``, describes the nearest StreamCat reach Mink Brook (COMID 9327042), 446
    ft downstream, which drains 32 times this stream`` on a stream outside
    NHDPlus V2 (the shared ``comid_anchor`` wording, the ratio reported and
    never enforced); empty on a covered site."""
    try:
        from .. import comid_anchor
        label = comid_anchor.describes(_anchor(ctx))
    except Exception:  # noqa: BLE001
        comid = (_anchor(ctx).get("scoredReach") or {}).get("comid")
        label = f"nearest StreamCat reach (COMID {comid})" if comid else ""
    return f", describes the {label}" if label else ""


def _streamcat_source(ctx: AnalysisContext, what: str) -> str:
    return f"StreamCat lookup engine {what} (watershed){_describes(ctx)}"


def _nlcd_source(ctx: AnalysisContext, what: str) -> str:
    if ctx.extras.get("watershed_basis") == BASIS_SITE_ENGINE:
        return f"NLCD 2021 {what} (HR reach watershed polygon, STAF site engine)"
    return f"NLCD 2021 {what} (watershed)"


# Every StreamCat column an adapter reads, fetched in ONE batched request per
# site (the keys come back aoi-suffixed, e.g. ``pctimp2019ws``).
_STREAMCAT_NAMES = ["pctimp2019", "pctcrop2019", "pcthay2019", "pctwdwet2019",
                    "pcthbwet2019", "rddens", "damdens", "bfi", "rdcrs"]


def _streamcat(ctx: AnalysisContext) -> dict:
    if "streamcat" not in ctx.extras:
        if not ctx.comid:
            ctx.extras["streamcat"] = {}
        else:
            from ..datasources import streamcat
            ctx.extras["streamcat"] = streamcat.metrics_by_comid(
                ctx.comid, list(_STREAMCAT_NAMES)) or {}
    return ctx.extras["streamcat"]


def _landcover(ctx: AnalysisContext) -> dict:
    if "landcover" not in ctx.extras:
        from ..datasources import nlcd
        ctx.extras["landcover"] = nlcd.watershed_landcover(ctx.watershed_geojson) or {}
    return ctx.extras["landcover"]


def _reach_geom(ctx: AnalysisContext) -> dict:
    if "reach_geomorph" not in ctx.extras:
        geom = {}
        try:
            if ctx.reach_geojson and ctx.drainage_area_sqkm and site_engine_available():
                from .. import bieger
                # The transect machinery is EASI's, carried by the vendored site
                # engine's extracts (nine sections along the reach, reach-median
                # ER and BHR, 2026-09-06); DEEP keeps no copy of its own. Not
                # gated by the pairing rule: these are 3DEP values, not engine
                # watershed values.
                from .._vendor.site_engine._extracted import threedep
                bf = bieger.bankfull_geometry(ctx.drainage_area_sqkm, ctx.lat, ctx.lon)
                geom = threedep.reach_geomorphology(
                    ctx.reach_geojson, ctx.drainage_area_sqkm,
                    bankfull=(bf["width_m"], bf["depth_m"]),
                    bankfull_area_m2=bf.get("area_m2"),
                    division=bf.get("division_name"),
                ) or {}
        except Exception:  # noqa: BLE001
            geom = {}
        ctx.extras["reach_geomorph"] = geom
    return ctx.extras["reach_geomorph"]


def _as_float(v) -> Optional[float]:
    """``float(v)``, or ``None`` for a missing or non-numeric datum. Zero is a
    real value at every layer, so only ``None`` means missing."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _layered(ctx: AnalysisContext, *, engine_key: Optional[str], engine_what: str = "",
             sc_col: str, sc_what: str, nlcd_key: Optional[str] = None,
             nlcd_what: str = "", ndigits: int = 2, confidence: str = "H",
             sc_note: str = "") -> Optional[ComputedValue]:
    """The source order every watershed adapter follows: the STAF site engine
    (HR reach watershed, only when ``engine_key`` names an engine metric and the
    ``allow_engine`` gate in :func:`_engine_record` lets it run), then the
    StreamCat lookup engine column ``sc_col``, then NLCD over the HR reach watershed polygon
    (``nlcd_key``, land cover only). ``sc_note`` rides after the StreamCat
    label. Looks the layer helpers up at call time so tests can stand in for
    them on the module."""
    if engine_key:
        ev = _as_float(_engine_metric(ctx, engine_key))
        if ev is not None:
            return ComputedValue(round(ev, ndigits),
                                 f"{_engine_label(ctx)} {engine_what}",
                                 confidence, engine=True, basis=BASIS_SITE_ENGINE)
    v = _as_float(_streamcat(ctx).get(sc_col))
    if v is not None:
        src = _streamcat_source(ctx, sc_what)
        if sc_note:
            src = f"{src}, {sc_note}"
        return ComputedValue(round(v, ndigits), src, confidence, basis=BASIS_STREAMCAT)
    if nlcd_key:
        v = _as_float(_landcover(ctx).get(nlcd_key))
        if v is not None:
            return ComputedValue(round(v, ndigits), _nlcd_source(ctx, nlcd_what),
                                 confidence, basis=BASIS_NLCD)
    return None


# --------------------------------------------------------------------------- #
# Adapters: watershed land cover (site engine, StreamCat, NLCD fallback)
# --------------------------------------------------------------------------- #
@adapter("catchment-hydrology-impervious-cover",
         "catchment-hydrology-percent-impervious-cover",
         "catchment-hydrology-effective-impervious-cover",
         "spring-pctimp2019ws")
def _impervious(ctx):
    ev = _engine_metric(ctx, "imperviousPctWatershed")
    if ev is not None:
        return ComputedValue(round(float(ev), 2),
                             f"{_engine_label(ctx)} impervious (HR reach watershed, NLCD 2021)",
                             "H", engine=True, basis=BASIS_SITE_ENGINE)
    v = _streamcat(ctx).get("pctimp2019ws")
    src, basis = _streamcat_source(ctx, "pctimp2019"), BASIS_STREAMCAT
    if v is None:
        v = _landcover(ctx).get("impervious_pct")
        src, basis = _nlcd_source(ctx, "impervious"), BASIS_NLCD
    return (ComputedValue(round(float(v), 2), src, "H", basis=basis)
            if v is not None else None)


@adapter("catchment-hydrology-anthropogenic-land-cover")
def _anthropogenic(ctx):
    e_imp = _engine_metric(ctx, "imperviousPctWatershed")
    e_crop = _engine_metric(ctx, "cropPctWatershed")
    e_hay = _engine_metric(ctx, "hayPasturePctWatershed")
    if any(v is not None for v in (e_imp, e_crop, e_hay)):
        total = (e_imp or 0.0) + (e_crop or 0.0) + (e_hay or 0.0)
        return ComputedValue(
            round(float(total), 2),
            f"{_engine_label(ctx)} crop+hay+impervious (HR reach watershed, NLCD 2021)",
            "M", engine=True, basis=BASIS_SITE_ENGINE)
    sc = _streamcat(ctx)
    crop, hay, imp = sc.get("pctcrop2019ws"), sc.get("pcthay2019ws"), sc.get("pctimp2019ws")
    if crop is not None or hay is not None or imp is not None:
        total = (crop or 0.0) + (hay or 0.0) + (imp or 0.0)
        return ComputedValue(round(float(total), 2),
                             _streamcat_source(ctx, "crop+hay+impervious"), "M",
                             basis=BASIS_STREAMCAT)
    lc = _landcover(ctx)
    ag, imp = lc.get("ag_pct"), lc.get("impervious_pct")
    if ag is not None or imp is not None:
        return ComputedValue(round(float((ag or 0.0) + (imp or 0.0)), 2),
                             _nlcd_source(ctx, "agriculture + developed"), "M",
                             basis=BASIS_NLCD)
    return None


# --------------------------------------------------------------------------- #
# Adapters: regional landscape metrics (the StreamCat-coded ``spring-*ws`` ids
# the regional bundles score). Land cover and densities follow the site engine
# -> StreamCat -> NLCD order above; base flow index and road-stream crossings
# follow the site engine -> StreamCat order (engine 0.3.0 computes both).
# --------------------------------------------------------------------------- #
@adapter("spring-pctcrop2019ws")
def _crop(ctx):
    return _layered(ctx, engine_key="cropPctWatershed",
                    engine_what="cultivated crops (HR reach watershed, NLCD 2021)",
                    sc_col="pctcrop2019ws", sc_what="pctcrop2019",
                    nlcd_key="crop_pct", nlcd_what="cultivated crops")


def _both(ctx, getter, keys, *, cap: float = 100.0):
    """The sum of two class values from one layer, or None when either is
    missing: an unpublished class leaves the total unknown, never smaller
    (EASI's contract for the same quantity, and StreamCurves' ``_sum_required``).
    """
    vals = [_as_float(getter(ctx, k)) for k in keys]
    if any(v is None for v in vals):
        return None
    return min(cap, sum(vals))


@adapter("spring-pctwet2019ws")
def _wetland(ctx):
    """Wetland cover, woody plus herbaceous: the combined Surface water storage
    metric StreamCurves fits one curve on since 2026-09-07 (review item
    ECO-15). StreamCat publishes no combined column and the engine no combined
    key, so both layers sum their two classes here; NLCD's own total answers
    when no StreamCat reach is known. The split ids keep their adapters: every
    bundle published before this scores them."""
    ev = _both(ctx, _engine_metric,
               ("woodyWetlandPctWatershed", "herbWetlandPctWatershed"))
    if ev is not None:
        return ComputedValue(round(ev, 2),
                             f"{_engine_label(ctx)} wetland, woody plus herbaceous "
                             "(HR reach watershed, NLCD 2021)",
                             "H", engine=True, basis=BASIS_SITE_ENGINE)
    sv = _both(ctx, lambda c, k: _streamcat(c).get(k),
               ("pctwdwet2019ws", "pcthbwet2019ws"))
    if sv is not None:
        return ComputedValue(round(sv, 2),
                             _streamcat_source(ctx, "pctwdwet2019 plus pcthbwet2019"),
                             "H", basis=BASIS_STREAMCAT)
    nv = _as_float(_landcover(ctx).get("wetland_pct"))
    if nv is not None:
        return ComputedValue(round(nv, 2), _nlcd_source(ctx, "wetland"),
                             "H", basis=BASIS_NLCD)
    return None


@adapter("spring-pctwdwet2019ws")
def _woody_wetland(ctx):
    return _layered(ctx, engine_key="woodyWetlandPctWatershed",
                    engine_what="woody wetland (HR reach watershed, NLCD 2021)",
                    sc_col="pctwdwet2019ws", sc_what="pctwdwet2019",
                    nlcd_key="woody_wetland_pct", nlcd_what="woody wetland")


@adapter("spring-pcthbwet2019ws")
def _herb_wetland(ctx):
    return _layered(ctx, engine_key="herbWetlandPctWatershed",
                    engine_what="herbaceous wetland (HR reach watershed, NLCD 2021)",
                    sc_col="pcthbwet2019ws", sc_what="pcthbwet2019",
                    nlcd_key="herb_wetland_pct", nlcd_what="herbaceous wetland")


# Densities keep four decimals: two would zero rdcrsws and most damdensws.
@adapter("spring-rddensws")
def _road_density(ctx):
    return _layered(ctx, engine_key="roadDensity",
                    engine_what="road density (HR reach watershed, TIGERweb roads)",
                    sc_col="rddensws", sc_what="rddens", ndigits=4, confidence="M")


@adapter("spring-damdensws")
def _dam_density(ctx):
    return _layered(ctx, engine_key="damDensityPerSqkm",
                    engine_what="dam density (HR reach watershed, USACE NID)",
                    sc_col="damdensws", sc_what="damdens", ndigits=4)


@adapter("spring-bfiws")
def _baseflow_index(ctx):
    return _layered(ctx, engine_key="baseflowIndexPct",
                    engine_what="base flow index (HR reach watershed, USGS base-flow index grid)",
                    sc_col="bfiws", sc_what="bfi", confidence="M")


_RDCRS_NOTE = ("API-served units, enter values as served, a crossings per km2 "
               "value computed by hand lands about 100 times above this curve")


@adapter("spring-rdcrsws")
def _road_crossings(ctx):
    # The engine counts crossings on the NHDPlus HR network per km2 (its own
    # scale, 2026-09-05); the StreamCat path keeps the served-units caution.
    return _layered(ctx, engine_key="roadCrossingDensity",
                    engine_what="road-stream crossings per km2 (HR reach watershed, TIGERweb "
                                "roads on the NHDPlus HR network)",
                    sc_col="rdcrsws", sc_what="rdcrs", ndigits=4, confidence="M",
                    sc_note=_RDCRS_NOTE)


# --------------------------------------------------------------------------- #
# Adapters: reach geomorphic ratios (3DEP DEM cross-section)
# --------------------------------------------------------------------------- #
@adapter("floodplain-connectivity-entrenchment-ratio-er")
def _entrenchment(ctx):
    er = _reach_geom(ctx).get("entrenchment_ratio")
    return (ComputedValue(round(float(er), 2),
                          "3DEP DEM cross-sections, reach median ER of the sampled sections "
                          "(modeled)", "M", basis=BASIS_3DEP)
            if er is not None else None)


@adapter("channel-and-floodplain-dynamics-bank-height-ratio-bhr")
def _bank_height(ctx):
    bhr = _reach_geom(ctx).get("bank_height_ratio")
    return (ComputedValue(round(float(bhr), 2),
                          "3DEP DEM cross-sections, reach median bank-height ratio of the "
                          "sampled sections (modeled)", "M",
                          basis=BASIS_3DEP)
            if bhr is not None else None)


@adapter("channel-evolution-width-depth-ratio")
def _width_depth(ctx):
    g = _reach_geom(ctx)
    w, d = g.get("bankfull_width_m"), g.get("bankfull_depth_m")
    if w and d and d > 0:
        return ComputedValue(round(float(w) / float(d), 1),
                             "3DEP DEM cross-section, width/depth of the default section "
                             "(modeled)", "M",
                             basis=BASIS_3DEP)
    return None


def compute_for(metric_ids, ctx: AnalysisContext) -> dict[str, ComputedValue]:
    """Run the registered adapters for the requested metricIds. Never raises.

    The engine gate is decided per metric (2026-09-07): ``ctx.extras
    ["allow_engine_by_id"]`` (from each curve's own ``predictorSource`` stamp,
    the reading the scoring layer's pairing rule makes) overrides the
    bundle-level ``allow_engine`` default for the ids it names, so on a mixed
    bundle a re-sourced curve takes the engine value and an unstamped curve
    keeps the StreamCat value it was fitted on, and nothing is withheld.
    """
    out: dict[str, ComputedValue] = {}
    default = bool(ctx.extras.get("allow_engine"))
    by_id = ctx.extras.get("allow_engine_by_id") or {}
    for mid in set(metric_ids):
        fn = _ADAPTERS.get(mid)
        if fn is None:
            continue
        ctx.extras["allow_engine"] = bool(by_id.get(mid, default))
        try:
            cv = fn(ctx)
        except Exception:  # noqa: BLE001
            cv = None
        if cv is not None:
            out[mid] = cv
    ctx.extras["allow_engine"] = default
    return out
