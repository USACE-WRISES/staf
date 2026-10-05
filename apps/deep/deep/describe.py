"""The one-line description under a metric's name on the Assessment page (owner, 2026-10-04).

EASI and SFARI show a description under every metric; DEEP shows this line, "what the value is;
which way is better", for example "Percent of the watershed in impervious surface; higher is
worse." What the value is comes from :data:`WHAT`, keyed by the metric's name (the mnemonic names
of the early versions, ``XEMBED`` and the like, are aliases). Which way is better is read from the
curve that scores the metric, so the line can never contradict the score. A metric the map does
not know (an uploaded bundle) falls back to the first clause of its ``howToMeasure``, or to no
line at all. The owner reviews the wording in
``notes/2026-10-04_DEEP_Assessment_Page/description_review.md``.
"""
from __future__ import annotations

import re

from . import curves

_PCT_WS = "Percent of the watershed in "
_NRSA_BENTHIC = "benthic macroinvertebrate"

#: what each metric's value is, by casefolded metric name
WHAT: dict[str, str] = {
    "agriculture, crop and hay": _PCT_WS + "crops, hay and pasture",
    "bank angle": "Mean bank angle",
    "bank height ratio": "Bank height ratio (top of bank height over bankfull depth)",
    "bankfull height above channel": "Mean bankfull height above the water surface",
    "bankfull width:depth ratio": "Mean bankfull width over mean bankfull depth",
    "base flow index": "Percent of streamflow that comes from base flow",
    "benthic macroinvertebrate mmi": f"EPA's {_NRSA_BENTHIC} multimetric index (0 to 100)",
    "benthic shannon diversity": f"Shannon diversity of the {_NRSA_BENTHIC} sample",
    "benthic total taxa richness": f"Number of {_NRSA_BENTHIC} taxa",
    "canopy density, mid-channel": "Canopy density over the middle of the channel",
    "chlorophyll a": "Chlorophyll a in the stream water, a measure of algae",
    "cultivated crops": _PCT_WS + "cultivated crops",
    "dam density": "Dams per square kilometer of watershed",
    "degree of regulation": "Upstream dam storage as a percent of mean annual runoff",
    "dissolved nitrogen": "Dissolved nitrogen in the stream water",
    "embeddedness": "Percent of the coarse bed buried in fine sediment",
    "ept taxa richness": "Number of mayfly, stonefly and caddisfly taxa",
    "fast-water / riffle habitat": "Percent of the reach in riffles and other fast water",
    "fish mmi": "EPA's fish multimetric index (0 to 100)",
    "herbaceous wetland": _PCT_WS + "herbaceous wetland",
    "impervious surface": _PCT_WS + "impervious surface",
    "large wood volume": "Volume of large wood per 100 m of channel",
    "mapped dams within one mile": "Mapped dams within one mile of the site",
    "native fish taxa richness": "Number of native fish taxa",
    "native non-tolerant fish taxa": "Percent of native fish taxa that are not pollution tolerant",
    "native non-tolerant fish taxa richness": "Number of native fish taxa that are not pollution tolerant",
    "natural cover of the riparian corridor": "Percent natural cover in the 100 m riparian corridor",
    "natural fish cover": "Areal cover of natural fish cover in the channel",
    "ph": "pH of the stream water",
    "relative bed stability": "Bed particle size against what bankfull flow can move (log)",
    "residual pool depth": "Mean residual pool depth",
    "riparian veg cover, woody": "Woody vegetation cover in the riparian canopy, understory and ground layer",
    "road density": "Road length per square kilometer of watershed",
    "road-stream crossings": "Road and stream crossings per square kilometer of watershed",
    "sand + fines": "Percent of the streambed that is sand or finer",
    "simple lithophil fish individuals": "Percent of native fish that spawn on clean gravel without building nests",
    "sinuosity": "Channel length over straight line length (sinuosity)",
    "specific conductivity": "Specific conductance of the stream water",
    "substrate size": "Mean bed particle size (log mm)",
    "tolerant individuals": f"Percent of {_NRSA_BENTHIC} individuals in pollution tolerant taxa",
    "tolerant native fish individuals": "Percent of native fish individuals that are pollution tolerant",
    "total nitrogen": "Total nitrogen in the stream water",
    "total phosphorus": "Total phosphorus in the stream water",
    "turbidity": "Turbidity of the stream water",
    "wetland": _PCT_WS + "woody and herbaceous wetland",
    "woody wetland": _PCT_WS + "woody wetland",
}

#: other names for the same metrics (the early versions' mnemonics, retitled labels)
ALIASES: dict[str, str] = {
    "xbka": "bank angle",
    "xbkf_h": "bankfull height above channel",
    "bfwd_rat": "bankfull width:depth ratio",
    "hprime": "benthic shannon diversity",
    "shannon diversity (h')": "benthic shannon diversity",
    "totlntax": "benthic total taxa richness",
    "total benthic taxa": "benthic total taxa richness",
    "xcdenmid": "canopy density, mid-channel",
    "chla": "chlorophyll a",
    "cond": "specific conductivity",
    "ntl_diss": "dissolved nitrogen",
    "xembed": "embeddedness",
    "ept_ntax": "ept taxa richness",
    "pct_fast": "fast-water / riffle habitat",
    "large woody debris volume": "large wood volume",
    "lwdeqvolm100": "large wood volume",
    "nat_totlntax": "native fish taxa richness",
    "xfc_nat": "natural fish cover",
    "lrbs_use": "relative bed stability",
    "rp100_cm": "residual pool depth",
    "riparian canopy + ground cover": "riparian veg cover, woody",
    "xcmgw": "riparian veg cover, woody",
    "sand and fines": "sand + fines",
    "pct_safn": "sand + fines",
    "sinu": "sinuosity",
    "lsub_dmm": "substrate size",
    "tolerant individuals (%) ~ hbi": "tolerant individuals",
    "tolrpind": "tolerant individuals",
    "ptl": "total phosphorus",
    "turb": "turbidity",
    "wetland cover of the watershed": "wetland",
}

#: the change in index that counts as a slope, so a flat shoulder never reads as a direction
_EPS = 0.05


def what_of(m) -> str:
    """What the metric's value is: the curated phrase, else the first clause of ``howToMeasure``
    (never a fixed-criteria sentence, never a long one), else ''."""
    key = " ".join(str((m or {}).get("metricName") or "").split()).casefold()
    key = ALIASES.get(key, key)
    if key in WHAT:
        return WHAT[key]
    how = " ".join(str((m or {}).get("howToMeasure") or "").split())
    if not how or " is scored on " in how:
        return ""
    first = re.split(r";|\.(?:\s|$)", how, maxsplit=1)[0].strip()
    return first if 0 < len(first) <= 80 else ""


def direction(points) -> str:
    """Which way the curve rewards: 'higher is better', 'higher is worse', or 'both high and low
    values score lower' for a curve that peaks inside its range; '' for a flat or missing curve."""
    pts = sorted(((float(p["x"]), float(p["y"])) for p in (points or [])
                  if p.get("x") is not None and p.get("y") is not None))
    if len(pts) < 2:
        return ""
    ys = [y for _x, y in pts]
    lo, hi, top = ys[0], ys[-1], max(ys)
    if top - lo > _EPS and top - hi > _EPS:
        return "both high and low values score lower"
    if hi - lo > _EPS:
        return "higher is better"
    if lo - hi > _EPS:
        return "higher is worse"
    return ""


def describe(m, stratum: str | None = None) -> str:
    """The line under the metric's name, or '' when there is nothing safe to say."""
    what = what_of(m)
    if not what:
        return ""
    way = direction(curves.active_points(m, stratum))
    return f"{what}; {way}." if way else f"{what}."
