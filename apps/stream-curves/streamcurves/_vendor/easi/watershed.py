"""Watershed evidence: one canonical key set behind the two STAF engines.

EASI's eight watershed metrics (land-cover pressure, wetland extent, flow
alteration, reach inflow, sediment supply, organic-matter supply, thermal
vulnerability, habitat support) read their inputs from this layer instead of
a StreamCat row directly, so the two engines plug in behind one interface:

  * ``from_streamcat(row)``: the StreamCat lookup engine (token ``streamcat``).
    Today's values and today's source strings, byte for byte.
  * ``from_engine(record)``: the STAF site engine (token ``site-engine``). The
    exact-watershed values of a ``compute_site`` record, labeled with the
    engine's own vocabulary and version.
  * ``unavailable(reason)``: no watershed evidence at all (the engine failed
    or refused on a stream outside the StreamCat lookup network). Every value
    reads None with the reason, never a silent proxy.

Sums require every member: a missing class is unknown, never zero (the rule
in ``metrics/base.py``). The EPA modeled integrity components stay on the
StreamCat row: they are COMID-keyed and permanently excluded from the engine.
``compute_exact_watershed`` runs the vendored engine for a routed site and is
the only place EASI calls it.
"""
from __future__ import annotations

import importlib.util
from typing import Any, Callable, Optional

STREAMCAT = "streamcat"
SITE_ENGINE = "site-engine"
ACRE_FT_PER_KM2_TO_M3_PER_KM2 = 1233.48184
ENGINE_FAMILIES = ["dams", "landcover", "roads", "runoff", "soils"]

VALUE_KEYS = (
    "imperviousPct", "cropPct", "hayPct", "woodyWetlandPct", "herbWetlandPct",
    "roadDensity", "soilKFactor", "damStorageM3PerSqkm", "runoffMm",
    "ripConifPct", "ripDecidPct", "ripMixedPct", "ripForestPct", "ripShrubPct",
    "ripGrasslandPct", "ripWoodyWetlandPct", "ripHerbWetlandPct",
)

# canonical key -> StreamCat column (the lookup engine's raw values)
_STREAMCAT_COLUMNS = {
    "imperviousPct": "pctimp2019ws", "cropPct": "pctcrop2019ws",
    "hayPct": "pcthay2019ws", "woodyWetlandPct": "pctwdwet2019ws",
    "herbWetlandPct": "pcthbwet2019ws", "roadDensity": "rddensws",
    "soilKFactor": "kffactws", "damStorageM3PerSqkm": "damnrmstorws",
    "runoffMm": "runoffws",
    "ripConifPct": "pctconif2019wsrp100", "ripDecidPct": "pctdecid2019wsrp100",
    "ripMixedPct": "pctmxfst2019wsrp100", "ripShrubPct": "pctshrb2019wsrp100",
    "ripGrasslandPct": "pctgrs2019wsrp100",
    "ripWoodyWetlandPct": "pctwdwet2019wsrp100",
    "ripHerbWetlandPct": "pcthbwet2019wsrp100",
}

# canonical key -> site engine metric key (the engine's riparian forest is one
# merged class, so the three StreamCat forest components stay None)
_ENGINE_KEYS = {
    "imperviousPct": "imperviousPctWatershed", "cropPct": "cropPctWatershed",
    "hayPct": "hayPasturePctWatershed",
    "woodyWetlandPct": "woodyWetlandPctWatershed",
    "herbWetlandPct": "herbWetlandPctWatershed",
    "roadDensity": "roadDensity", "soilKFactor": "soilKFactor",
    "damStorageM3PerSqkm": "damStoragePerSqkm",      # acre-ft/km2, converted
    "runoffMm": "runoffDepthMm",
    "ripForestPct": "forestPctRiparian", "ripShrubPct": "shrubPctRiparian",
    "ripGrasslandPct": "grasslandPctRiparian",
    "ripWoodyWetlandPct": "woodyWetlandPctRiparian",
    "ripHerbWetlandPct": "herbWetlandPctRiparian",
}

# Per-input source labels the adapters pass into the scoring trace, keyed
# "<adapter>.<input>". The StreamCat strings are the historical ones.
STREAMCAT_INPUT_SOURCES = {
    "impervious.impervious": "EPA StreamCat pctimp2019 (watershed)",
    "impervious.agriculture": "EPA StreamCat crop + hay (watershed)",
    "wetlands.woodyWetland": "EPA StreamCat pctwdwet2019 (watershed)",
    "wetlands.herbaceousWetland": "EPA StreamCat pcthbwet2019 (watershed)",
    "flowAlteration.storage": "EPA StreamCat DamNrmStorWs",
    "flowAlteration.runoff": "EPA StreamCat RunoffWs",
    "reachInflow.roadDensity": "EPA StreamCat RddensWs",
    "sediment.agriculture": "EPA StreamCat crop + hay",
    "sediment.kFactor": "EPA StreamCat KffactWs",
    "sediment.roadDensity": "EPA StreamCat RddensWs",
    "cpom.forest": "EPA StreamCat rp100",
    "cpom.shrub": "EPA StreamCat rp100",
    "cpom.grassland": "EPA StreamCat rp100",
    "cpom.wetland": "EPA StreamCat rp100",
    "temperature.woodyRiparian": "EPA StreamCat forest + shrub + woody wetland (rp100)",
    "temperature.impervious": "EPA StreamCat pctimp2019ws",
    "habitat.woodyRiparian": "EPA StreamCat woody riparian cover (rp100)",
}
# MetricResult.source strings per adapter (the historical StreamCat ones).
STREAMCAT_RESULT_SOURCES = {
    "wetlands": "EPA StreamCat wetlands (watershed)",
    "flowAlteration": "EPA StreamCat normalized storage + annual runoff",
    "reachInflow": "EPA StreamCat road density",
    "sediment": "EPA StreamCat agriculture + K-factor + road density",
    "cpom": "EPA StreamCat riparian land cover (rp100)",
    "temperature": "EPA StreamCat woody riparian cover + watershed impervious cover",
    "habitat": "EPA StreamCat woody riparian cover (rp100)",
}
_ENGINE_INPUT_DETAILS = {
    "impervious.impervious": "NLCD 2021 impervious cover",
    "impervious.agriculture": "NLCD 2021 crop + hay",
    "wetlands.woodyWetland": "NLCD 2021 woody wetland",
    "wetlands.herbaceousWetland": "NLCD 2021 herbaceous wetland",
    "flowAlteration.storage": "NID normal storage",
    "flowAlteration.runoff": "EROM-derived runoff depth",
    "reachInflow.roadDensity": "TIGERweb road density",
    "sediment.agriculture": "NLCD 2021 crop + hay",
    "sediment.kFactor": "SSURGO K, area-weighted",
    "sediment.roadDensity": "TIGERweb road density",
    "cpom.forest": "NLCD 2021 riparian classes (100 m)",
    "cpom.shrub": "NLCD 2021 riparian classes (100 m)",
    "cpom.grassland": "NLCD 2021 riparian classes (100 m)",
    "cpom.wetland": "NLCD 2021 riparian classes (100 m)",
    "temperature.woodyRiparian": "NLCD 2021 forest + shrub + woody wetland (100 m)",
    "temperature.impervious": "NLCD 2021 impervious cover",
    "habitat.woodyRiparian": "NLCD 2021 woody riparian cover (100 m)",
}
_ENGINE_RESULT_DETAILS = {
    "wetlands": "NLCD 2021 wetlands",
    "flowAlteration": "NID normal storage + EROM-derived runoff",
    "reachInflow": "TIGERweb road density",
    "sediment": "agriculture + K-factor + road density",
    "cpom": "NLCD 2021 riparian land cover (100 m)",
    "temperature": "woody riparian cover + watershed impervious cover",
    "habitat": "NLCD 2021 woody riparian cover (100 m)",
}
_ENGINE_NOTES = {
    "flowAlteration": ("Runoff is the STAF site engine EROM-derived depth over "
                       "the exact watershed, not StreamCat RunoffWs."),
}

GUIDANCE_UNAVAILABLE = (
    "Watershed evidence is unavailable for this stream. The exact watershed "
    "could not be calculated ({reason}). Use SFARI or DEEP for this site, or "
    "enter a rating override.")


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def engine_label(version: Optional[str]) -> str:
    """``STAF site engine v0.2.0`` from the vendored vocabulary when present."""
    try:
        from ._vendor.site_engine import naming  # noqa: PLC0415
        return naming.engine_label(version)
    except Exception:  # noqa: BLE001 - vocabulary fallback, never a failure
        return f"STAF site engine v{version or 'unknown'}"


def display_name(token: str) -> str:
    try:
        from ._vendor.site_engine import naming  # noqa: PLC0415
        return naming.display_name(token)
    except Exception:  # noqa: BLE001
        return {STREAMCAT: "StreamCat lookup engine",
                SITE_ENGINE: "STAF site engine"}.get(token, token)


# --------------------------------------------------------------------------- #
# providers
# --------------------------------------------------------------------------- #
def from_streamcat(row: Optional[dict]) -> dict:
    """The StreamCat lookup engine: today's values, today's labels."""
    s = row or {}
    values = {key: None for key in VALUE_KEYS}
    for key, col in _STREAMCAT_COLUMNS.items():
        values[key] = _num(s.get(col))
    return {"provider": STREAMCAT, "label": display_name(STREAMCAT),
            "values": values,
            "inputSources": dict(STREAMCAT_INPUT_SOURCES),
            "resultSources": dict(STREAMCAT_RESULT_SOURCES),
            "notes": {}, "meta": {}, "unavailableReason": None}


def from_engine(record: dict, *, version: Optional[str] = None) -> dict:
    """The STAF site engine: exact-watershed values from a compute_site record."""
    metrics = (record or {}).get("metrics") or {}
    ver = version or (record or {}).get("engineVersion")
    values = {key: None for key in VALUE_KEYS}
    for key, engine_key in _ENGINE_KEYS.items():
        v = _num((metrics.get(engine_key) or {}).get("value"))
        if key == "damStorageM3PerSqkm" and v is not None:
            v = round(v * ACRE_FT_PER_KM2_TO_M3_PER_KM2, 3)
        values[key] = v
    label = engine_label(ver)
    ws = (record or {}).get("watershed") or {}
    return {
        "provider": SITE_ENGINE, "label": label, "values": values,
        "inputSources": {k: f"{label}, exact watershed: {d}"
                         for k, d in _ENGINE_INPUT_DETAILS.items()},
        "resultSources": {k: f"{label}, exact watershed: {d}"
                          for k, d in _ENGINE_RESULT_DETAILS.items()},
        "notes": dict(_ENGINE_NOTES),
        "meta": {"engineVersion": ver, "areaSqkm": ws.get("areaSqkm"),
                 "vaaAreaSqkm": ws.get("vaaAreaSqkm"),
                 "areaAgreement": ws.get("areaAgreement"),
                 "nReaches": ws.get("nReaches"), "nHops": ws.get("nHops")},
        "unavailableReason": None,
    }


def unavailable(reason: Optional[str]) -> dict:
    """No watershed evidence: every value None, the reason kept."""
    reason = reason or "the exact watershed could not be calculated"
    return {"provider": None, "label": "watershed evidence unavailable",
            "values": {key: None for key in VALUE_KEYS},
            "inputSources": {k: "" for k in STREAMCAT_INPUT_SOURCES},
            "resultSources": {k: "" for k in STREAMCAT_RESULT_SOURCES},
            "notes": {}, "meta": {}, "unavailableReason": reason}


def build(ctx, streamcat_row: Optional[dict]) -> dict:
    """The layer for one run: the engine block on ``ctx.extras`` decides."""
    eng = ctx.extras.get("watershedEngine") or {}
    status = eng.get("status")
    if status == "ok" and eng.get("record"):
        return from_engine(eng["record"], version=eng.get("engineVersion"))
    if status in ("failed", "refused", "unavailable"):
        return unavailable(eng.get("reason") or status)
    return from_streamcat(streamcat_row)


def wants_nlcd_fallback(ctx) -> bool:
    """The NLCD outage fallback belongs to the StreamCat lookup engine only."""
    status = (ctx.extras.get("watershedEngine") or {}).get("status")
    return status not in ("ok", "failed", "refused", "unavailable")


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #
def layer(ctx) -> dict:
    """The run's layer; a bare StreamCat row (tests, scripts) is the lookup
    engine."""
    return ctx.extras.get("watershed") or from_streamcat(
        ctx.extras.get("streamcat") or {})


def provider(ctx) -> Optional[str]:
    return layer(ctx)["provider"]


def value(ctx, key: str) -> Optional[float]:
    return layer(ctx)["values"].get(key)


def input_source(ctx, key: str) -> str:
    return layer(ctx)["inputSources"].get(key, "")


def result_source(ctx, key: str) -> str:
    return layer(ctx)["resultSources"].get(key, "")


def result_note(ctx, key: str, default: str) -> str:
    return layer(ctx)["notes"].get(key) or default


def guidance(ctx, default: str) -> str:
    """The adapter note for a missing input: the plain guidance when the
    watershed evidence itself is unavailable, else the adapter's own note."""
    lyr = layer(ctx)
    if lyr["provider"] is None:
        return GUIDANCE_UNAVAILABLE.format(reason=lyr["unavailableReason"])
    return default


# composite accessors: the exact math of the historical base.py accessors
def ag_pct(lyr: dict) -> Optional[float]:
    vals = [lyr["values"].get("cropPct"), lyr["values"].get("hayPct")]
    if any(v is None for v in vals):
        return None
    return round(sum(float(v) for v in vals), 2)


def forest_pct(lyr: dict, ndigits: int) -> Optional[float]:
    """Riparian forest: the sum of the three StreamCat classes when they are
    all present, else the engine's single merged class."""
    v = lyr["values"]
    comps = [v.get("ripConifPct"), v.get("ripDecidPct"), v.get("ripMixedPct")]
    if all(c is not None for c in comps):
        return round(sum(float(c) for c in comps), ndigits)
    merged = v.get("ripForestPct")
    return None if merged is None else round(float(merged), ndigits)


def riparian_forest_pct(lyr: dict) -> Optional[float]:
    return forest_pct(lyr, 2)


def riparian_veg_breakdown(lyr: dict) -> Optional[dict]:
    v = lyr["values"]
    groups: dict[str, float] = {}
    forest = forest_pct(lyr, 1)
    if forest is None:
        return None
    groups["forest"] = forest
    for grp, keys in (("shrub", ("ripShrubPct",)),
                      ("grassland", ("ripGrasslandPct",)),
                      ("wetland", ("ripWoodyWetlandPct", "ripHerbWetlandPct"))):
        vals = [v.get(k) for k in keys]
        if any(x is None for x in vals):
            return None
        groups[grp] = round(sum(float(x) for x in vals), 1)
    groups["total"] = round(sum(groups[g] for g in
                                ("forest", "shrub", "grassland", "wetland")), 1)
    return groups


def riparian_woody_breakdown(lyr: dict) -> Optional[dict]:
    v = lyr["values"]
    forest = forest_pct(lyr, 1)
    if forest is None:
        return None
    out: dict[str, float] = {"forest": forest}
    for grp, key in (("shrub", "ripShrubPct"), ("woodyWetland", "ripWoodyWetlandPct")):
        x = v.get(key)
        if x is None:
            return None
        out[grp] = round(float(x), 1)
    out["total"] = round(sum(out.values()), 1)
    return out


# --------------------------------------------------------------------------- #
# the engine call
# --------------------------------------------------------------------------- #
_GEO_REQUIREMENTS = ("requests", "shapely", "geopandas", "pygeohydro")


def engine_available() -> bool:
    """True when the vendored engine and its geospatial stack import."""
    try:
        if importlib.util.find_spec("easi._vendor.site_engine") is None:
            return False
        return all(importlib.util.find_spec(m) is not None for m in _GEO_REQUIREMENTS)
    except (ImportError, ValueError):
        return False


def compute_exact_watershed(anchor: Optional[dict], *,
                            progress: Optional[Callable[[dict], Any]] = None
                            ) -> dict:
    """Run the STAF site engine at a routed site's clicked HR stream.

    Returns a summary block ``{engine, engineVersion, status, reason,
    nReaches, nHops, areaSqkm, vaaAreaSqkm, areaAgreement, record, polygon}``.
    ``record`` is the engine record with its geometry stripped (it rides the
    ctx inputs and the session); ``polygon`` is the watershed FeatureCollection
    for the map and the exports. Status ``refused`` is the engine's budget
    refusal, ``unavailable`` means the engine is not deployable here. Never
    raises.
    """
    out: dict[str, Any] = {"engine": SITE_ENGINE, "engineVersion": None,
                           "status": "failed", "reason": None,
                           "nReaches": None, "nHops": None, "areaSqkm": None,
                           "vaaAreaSqkm": None, "areaAgreement": None,
                           "record": None, "polygon": None}
    clicked = (anchor or {}).get("clickedStream") or {}
    lat, lon = clicked.get("snapLat"), clicked.get("snapLon")
    if lat is None or lon is None:
        out["reason"] = "the clicked stream's snap point is missing"
        return out
    if not engine_available():
        out["status"] = "unavailable"
        out["reason"] = "the STAF site engine is not available in this deployment"
        return out
    try:
        from ._vendor.site_engine import ENGINE_VERSION  # noqa: PLC0415
        from ._vendor.site_engine.engine import compute_site  # noqa: PLC0415
        from ._vendor.site_engine.provenance import INTERACTIVE_CONFIG  # noqa: PLC0415

        cfg = {**INTERACTIVE_CONFIG, "includeGeometry": True,
               "metricFamilies": list(ENGINE_FAMILIES)}
        rec = compute_site(float(lat), float(lon), cfg, progress=progress)
    except Exception as exc:  # noqa: BLE001 - never raise into the pipeline
        out["reason"] = f"engine error: {exc}"
        return out
    out["engineVersion"] = rec.get("engineVersion") or ENGINE_VERSION
    ws = rec.get("watershed") or {}
    out.update(status=rec.get("status") or "failed", reason=rec.get("reason"),
               nReaches=ws.get("nReaches"), nHops=ws.get("nHops"),
               areaSqkm=ws.get("areaSqkm"), vaaAreaSqkm=ws.get("vaaAreaSqkm"),
               areaAgreement=ws.get("areaAgreement"))
    if out["status"] != "ok":
        return out
    nid = (rec.get("site") or {}).get("nhdplusId")
    if clicked.get("nhdplusId") and nid != clicked.get("nhdplusId"):
        rec.setdefault("warnings", []).append(
            f"engine anchored reach {nid} differs from the clicked stream "
            f"{clicked.get('nhdplusId')}")
    out["polygon"] = ws.get("polygon")
    stripped = dict(rec)
    stripped["watershed"] = {**ws, "polygon": None}
    stripped["reach"] = {**(rec.get("reach") or {}), "geometry": None}
    out["record"] = stripped
    return out
