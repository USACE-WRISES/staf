"""Declarative scoring-method catalog for the EASI worksheet "Scoring method" panel.

This is a **pure display + what-if layer**. For every screening metric it declares the inputs
used, the equation, and the numeric Good/Fair/Poor breakpoints, so the Assessment worksheet can
show a reference-curve plot, list the inputs, and recompute a rating from user-perturbed inputs
WITHOUT touching the report. The metric *adapters* in ``easi/metrics/*.py`` remain the source of
truth for the actual assessment; this module mirrors their value->rating math so a "what-if" here
matches what the pipeline would compute. A drift-guard test (``tests/test_methods_catalog.py``)
feeds each adapter's own recorded inputs back through :func:`evaluate` and asserts the value and
rating agree, so the two can never silently diverge.

The three cross-section ratings reuse the adapters' own pure helpers
(``rate_entrenchment`` / ``rate_engagement`` / ``rate_channel_evolution``) directly, so those are
consistent by construction.

Criteria *text* is NOT duplicated here — it stays in ``data/easi-metrics.json`` and is read via
``config.criteria_bands``. The numeric ``bands`` below only position the plot's colored regions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from . import config, scoring
from .metrics import base
from .metrics.biology import BARRIERS_ID, BIOINTEGRITY_ID, HABITAT_ID, INVASIVES_ID
from .metrics.geomorphology import (BANK_EROSION_ID, CHANNEL_EVOL_ID, SEDIMENT_ID, SUBSTRATE_ID,
                                    rate_channel_evolution)
from .metrics.hydraulics import (ENTRENCHMENT_ID, FLOODPLAIN_ENGAGEMENT_ID, HYPORHEIC_ID,
                                 LOW_FLOW_ID, rate_engagement, rate_entrenchment)
from .metrics.hydrology import (FLOW_ALTERATION_ID, IMPERVIOUS_ID, REACH_INFLOW_ID, WETLANDS_ID)
from .metrics.physicochemistry import CPOM_ID, IMPAIRMENT_ID, NUTRIENTS_ID, TEMPERATURE_ID

HIGHER_BETTER = "higher_better"
HIGHER_WORSE = "higher_worse"
_RANK = {"Poor": 0, "Fair": 1, "Good": 2}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# --------------------------------------------------------------------------- #
# Catalog data model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MethodInput:
    """One input to a metric's calculation (a slider under 'Explore values', unless
    ``context_only`` — a fixed site property shown but not perturbed, e.g. drainage area)."""
    key: str
    label: str
    unit: str = ""
    symbol: str = ""
    source_label: str = ""
    slider: Optional[tuple] = None          # (min, max, step); None => not a slider
    context_only: bool = False
    integer: bool = False


@dataclass(frozen=True)
class Band:
    """A colored Good/Fair/Poor region on the reference curve, [lo, hi) in value units
    (None = open). Used only to draw the plot; the rating itself comes from ``rate``."""
    rating: str
    lo: Optional[float] = None
    hi: Optional[float] = None


@dataclass(frozen=True)
class Decision:
    """One row of a categorical decision table (category -> rating)."""
    label: str
    rating: str


@dataclass
class ScoringMethod:
    metric_id: str
    mode: str                                # scalar | combined | worst | count | categorical
    inputs: tuple = ()
    equation: Optional[str] = None
    combine: Optional[Callable] = None       # (values) -> value (unrounded, for banding)
    rate: Optional[Callable] = None          # (value) -> "Good"|"Fair"|"Poor"
    value_rating: Optional[Callable] = None  # (values) -> (value, rating) when the two are coupled
    round_ndigits: Optional[int] = 2         # display rounding for the value
    bands: tuple = ()                        # Band[] for the plot regions (scalar/combined/count)
    per_input: tuple = ()                    # worst: ((key, rate_fn, (Band,...)), ...)
    decisions: tuple = ()                    # categorical: (Decision, ...)
    breakpoints: tuple = ()                  # annotation labels for interior boundaries (in order)
    domain: Optional[tuple] = None           # (lo, hi) x-domain for the plot
    direction: str = HIGHER_WORSE
    value_label: str = "Combined value"
    value_unit: str = ""


# --------------------------------------------------------------------------- #
# Small reusable rate / combine builders (mirror easi/metrics/base.band and the adapters)
# --------------------------------------------------------------------------- #
def _band_rate(good_below: float, fair_below: float, higher_is_worse: bool) -> Callable:
    def f(v):
        return base.band(v, good_below, fair_below, higher_is_worse)
    return f


def _pick(key: str) -> Callable:
    def f(vals):
        return vals.get(key)
    return f


def _rate_wetland(v):                         # adapter: >5 Good, >=1 Fair, else Poor
    return "Good" if v > 5 else ("Fair" if v >= 1 else "Poor")


def _rate_impervious(v):                       # <10 Good, <=25 Fair, else Poor
    return "Good" if v < 10 else ("Fair" if v <= 25 else "Poor")


def _rate_agriculture(v):                      # <25 Good, <=50 Fair, else Poor
    return "Good" if v < 25 else ("Fair" if v <= 50 else "Poor")


def _rate_invasives(n):                        # 0 Good, 1-2 Fair, >2 Poor
    return "Good" if n == 0 else ("Fair" if n <= 2 else "Poor")


def _rate_barriers(n):                         # 0 Good, 1 Fair, >=2 Poor
    return "Good" if n == 0 else ("Fair" if n == 1 else "Poor")


def _c_flow_alteration(vals):
    stor, da = vals.get("storage"), vals.get("drainage_area")
    if stor is None:
        return None
    return stor / max(da or 1.0, 1.0)


def _c_hyporheic(vals):
    slope, sin = vals.get("slope"), vals.get("sinuosity")
    if slope is None and sin is None:
        return None
    s = min((slope or 0.0) / 0.01, 1.0)
    sn = max(min(((sin or 1.0) - 1.0) / 0.5, 1.0), 0.0)
    return 0.6 * s + 0.4 * sn


def _c_bank_erosion(vals):
    kf, rip, slope = vals.get("kffact"), vals.get("riparian"), vals.get("slope")
    if kf is None and rip is None and slope is None:
        return None
    return (0.4 * min((kf or 0) / 0.4, 1) + 0.4 * (1 - min((rip or 0) / 100, 1))
            + 0.2 * min((slope or 0) / 0.02, 1))


def _c_sediment(vals):
    ag, kf, rd = vals.get("agriculture"), vals.get("kffact"), vals.get("road_density")
    if ag is None and kf is None and rd is None:
        return None
    return (0.5 * min((ag or 0) / 50, 1) + 0.3 * min((kf or 0) / 0.4, 1)
            + 0.2 * min((rd or 0) / 5, 1))


def _c_substrate(vals):
    slope, ag, kf = vals.get("slope"), vals.get("agriculture"), vals.get("kffact")
    if slope is None and ag is None and kf is None:
        return None
    return (0.4 * (1 - min((slope or 0) / 0.01, 1)) + 0.4 * min((ag or 0) / 50, 1)
            + 0.2 * min((kf or 0) / 0.4, 1))


def _c_habitat(vals):
    rip, so = vals.get("riparian"), vals.get("stream_order")
    if rip is None and so is None:
        return None
    return 0.6 * min((rip or 0) / 60, 1) + 0.4 * min((so or 1) / 4, 1)


def _c_bio_integrity(vals):
    rip, imp, ag, rd = (vals.get("riparian"), vals.get("impervious"),
                        vals.get("agriculture"), vals.get("road_density"))
    if all(v is None for v in (rip, imp, ag, rd)):
        return None
    support = min((rip or 0.0) / 60.0, 1.0)
    stress = (0.45 * min((imp or 0.0) / 25.0, 1.0) + 0.35 * min((ag or 0.0) / 60.0, 1.0)
              + 0.20 * min((rd or 0.0) / 5.0, 1.0))
    return _clamp(0.5 + 0.5 * support - 0.6 * stress)


def _c_impairment_surrogate(vals):
    imp, ag, rd, rip = (vals.get("impervious"), vals.get("agriculture"),
                        vals.get("road_density"), vals.get("riparian"))
    if all(v is None for v in (imp, ag, rd, rip)):
        return None
    stress = (0.45 * min((imp or 0.0) / 25.0, 1.0) + 0.35 * min((ag or 0.0) / 60.0, 1.0)
              + 0.20 * min((rd or 0.0) / 5.0, 1.0))
    return _clamp(stress - 0.15 * min((rip or 0.0) / 60.0, 1.0))


def _c_temp_surrogate(vals):
    tair, rip = vals.get("air_temp"), vals.get("riparian")
    if tair is None:
        return None
    return tair - 2.0 * min((rip or 0.0) / 60.0, 1.0)


def _c_detrital(vals):
    parts = [vals.get(k) for k in ("forest", "shrub", "grassland", "wetland")]
    if all(p is None for p in parts):
        return None
    return round(sum(p or 0.0 for p in parts), 1)     # adapter bands on the 1-dp total


def _vr_engagement(vals):
    """(value, rating) for floodplain engagement — reuse the adapter's pure helper so the
    bank-height-ratio -> recurrence -> rating chain is identical."""
    bhr = vals.get("bhr")
    rating, t_years = rate_engagement(bhr)
    return t_years, rating


# --------------------------------------------------------------------------- #
# The catalog — one entry per metric (thresholds/formulas copied from easi/metrics/*.py)
# --------------------------------------------------------------------------- #
_PCT = "%"

METHODS: dict[str, ScoringMethod] = {
    # ---- Hydrology ----
    WETLANDS_ID: ScoringMethod(
        WETLANDS_ID, "scalar",
        inputs=(MethodInput("wetland", "Wetland cover", _PCT, "wetland",
                            "EPA StreamCat / NLCD wetlands (watershed)", slider=(0, 30, 0.5)),),
        combine=_pick("wetland"), rate=_rate_wetland, direction=HIGHER_BETTER,
        bands=(Band("Poor", 0, 1), Band("Fair", 1, 5), Band("Good", 5, 30)),
        breakpoints=("1%", "5%"), domain=(0, 30),
        value_label="Wetland cover", value_unit=_PCT),
    REACH_INFLOW_ID: ScoringMethod(
        REACH_INFLOW_ID, "scalar",
        inputs=(MethodInput("road_density", "Road density", "km/km²", "roads",
                            "EPA StreamCat road density (watershed)", slider=(0, 8, 0.1)),),
        combine=_pick("road_density"), rate=_band_rate(1.0, 3.0, True),
        bands=(Band("Good", 0, 1), Band("Fair", 1, 3), Band("Poor", 3, 8)),
        breakpoints=("1.0", "3.0"), domain=(0, 8),
        value_label="Road density", value_unit="km/km²"),
    FLOW_ALTERATION_ID: ScoringMethod(
        FLOW_ALTERATION_ID, "combined",
        inputs=(MethodInput("storage", "Upstream dam storage", "ac-ft", "storage",
                            "EPA StreamCat normal storage", slider=(0, 5000, 10)),
                MethodInput("drainage_area", "Drainage area", "km²", "DA",
                            "delineated basin", context_only=True)),
        equation="V = dam storage / drainage area",
        combine=_c_flow_alteration, rate=_band_rate(5.0, 100.0, True),
        bands=(Band("Good", 0, 5), Band("Fair", 5, 100), Band("Poor", 100, 200)),
        breakpoints=("5", "100"), domain=(0, 150),
        value_label="Storage per drainage area", value_unit="ac-ft/km²"),
    # ---- Catchment-hydrology land cover (worst of two indicators) ----
    IMPERVIOUS_ID: ScoringMethod(
        IMPERVIOUS_ID, "worst",
        inputs=(MethodInput("impervious", "Impervious cover", _PCT, "impervious",
                            "EPA StreamCat / NLCD impervious (watershed)", slider=(0, 100, 1)),
                MethodInput("agriculture", "Agricultural cover", _PCT, "agriculture",
                            "EPA StreamCat crop+hay / NLCD (watershed)", slider=(0, 100, 1))),
        equation="rating = more limiting of (impervious, agricultural)",
        per_input=(
            ("impervious", _rate_impervious,
             (Band("Good", 0, 10), Band("Fair", 10, 25), Band("Poor", 25, 100))),
            ("agriculture", _rate_agriculture,
             (Band("Good", 0, 25), Band("Fair", 25, 50), Band("Poor", 50, 100))),
        ),
        direction=HIGHER_WORSE, domain=(0, 100), value_unit=_PCT),
    # ---- Hydraulics ----
    LOW_FLOW_ID: ScoringMethod(
        LOW_FLOW_ID, "categorical",
        decisions=(Decision("Perennial flow", "Good"), Decision("Intermittent flow", "Fair"),
                   Decision("Ephemeral flow", "Poor"))),
    FLOODPLAIN_ENGAGEMENT_ID: ScoringMethod(
        FLOODPLAIN_ENGAGEMENT_ID, "scalar",
        inputs=(MethodInput("bhr", "Bank-height ratio", "ratio", "BHR",
                            "representative cross-section (3DEP)"),),
        value_rating=_vr_engagement, round_ndigits=1, direction=HIGHER_WORSE,
        bands=(Band("Good", 1, 2), Band("Fair", 2, 5), Band("Poor", 5, 25)),
        breakpoints=("2 yr", "5 yr"), domain=(1, 25),
        value_label="Bankfull recurrence", value_unit="yr"),
    ENTRENCHMENT_ID: ScoringMethod(
        ENTRENCHMENT_ID, "scalar",
        inputs=(MethodInput("er", "Entrenchment ratio", "ratio", "ER",
                            "representative cross-section (3DEP)"),),
        combine=_pick("er"), rate=rate_entrenchment, round_ndigits=None,
        direction=HIGHER_BETTER,
        bands=(Band("Poor", 0, 1.4), Band("Fair", 1.4, 2.2), Band("Good", 2.2, 3.5)),
        breakpoints=("1.4", "2.2"), domain=(0, 3.5),
        value_label="Entrenchment ratio", value_unit="ratio"),
    HYPORHEIC_ID: ScoringMethod(
        HYPORHEIC_ID, "combined",
        inputs=(MethodInput("slope", "Channel slope", "m/m", "slope",
                            "NHDPlus slope", slider=(0, 0.02, 0.0001)),
                MethodInput("sinuosity", "Reach sinuosity", "ratio", "sinuosity",
                            "selected reach geometry", slider=(1, 2.5, 0.01))),
        equation="V = 0.6·min(slope/0.01, 1) + 0.4·clamp((sinuosity−1)/0.5, 0, 1)",
        combine=_c_hyporheic, rate=_band_rate(0.6, 0.3, False), direction=HIGHER_BETTER,
        bands=(Band("Poor", 0, 0.3), Band("Fair", 0.3, 0.6), Band("Good", 0.6, 1)),
        breakpoints=("V 0.30", "V 0.60"), domain=(0, 1)),
    # ---- Geomorphology ----
    CHANNEL_EVOL_ID: ScoringMethod(
        CHANNEL_EVOL_ID, "scalar",
        inputs=(MethodInput("bhr", "Bank-height ratio", "ratio", "BHR",
                            "representative cross-section (3DEP)"),),
        combine=_pick("bhr"), rate=rate_channel_evolution, round_ndigits=None,
        direction=HIGHER_WORSE,
        bands=(Band("Good", 0.8, 1.3), Band("Fair", 1.3, 1.7), Band("Poor", 1.7, 2.5)),
        breakpoints=("1.3", "1.7"), domain=(0.8, 2.5),
        value_label="Bank-height ratio", value_unit="ratio"),
    BANK_EROSION_ID: ScoringMethod(
        BANK_EROSION_ID, "combined",
        inputs=(MethodInput("kffact", "Soil erodibility (K)", "", "K",
                            "EPA StreamCat K-factor", slider=(0, 0.7, 0.01)),
                MethodInput("riparian", "Riparian forest", _PCT, "riparian",
                            "EPA StreamCat forest (rp100)", slider=(0, 100, 1)),
                MethodInput("slope", "Channel slope", "m/m", "slope",
                            "NHDPlus slope", slider=(0, 0.05, 0.001))),
        equation="risk = 0.4·min(K/0.4, 1) + 0.4·(1 − min(riparian/100, 1)) + 0.2·min(slope/0.02, 1)",
        combine=_c_bank_erosion, rate=_band_rate(0.4, 0.7, True),
        bands=(Band("Good", 0, 0.4), Band("Fair", 0.4, 0.7), Band("Poor", 0.7, 1)),
        breakpoints=("0.40", "0.70"), domain=(0, 1), value_label="Erosion risk"),
    SEDIMENT_ID: ScoringMethod(
        SEDIMENT_ID, "combined",
        inputs=(MethodInput("agriculture", "Agricultural cover", _PCT, "ag",
                            "EPA StreamCat crop+hay", slider=(0, 100, 1)),
                MethodInput("kffact", "Soil erodibility (K)", "", "K",
                            "EPA StreamCat K-factor", slider=(0, 0.7, 0.01)),
                MethodInput("road_density", "Road density", "km/km²", "roads",
                            "EPA StreamCat road density", slider=(0, 8, 0.1))),
        equation="risk = 0.5·min(ag/50, 1) + 0.3·min(K/0.4, 1) + 0.2·min(roads/5, 1)",
        combine=_c_sediment, rate=_band_rate(0.33, 0.66, True),
        bands=(Band("Good", 0, 0.33), Band("Fair", 0.33, 0.66), Band("Poor", 0.66, 1)),
        breakpoints=("0.33", "0.66"), domain=(0, 1), value_label="Supply risk"),
    SUBSTRATE_ID: ScoringMethod(
        SUBSTRATE_ID, "combined",
        inputs=(MethodInput("slope", "Channel slope", "m/m", "slope",
                            "NHDPlus slope", slider=(0, 0.02, 0.0001)),
                MethodInput("agriculture", "Agricultural cover", _PCT, "ag",
                            "EPA StreamCat crop+hay", slider=(0, 100, 1)),
                MethodInput("kffact", "Soil erodibility (K)", "", "K",
                            "EPA StreamCat K-factor", slider=(0, 0.7, 0.01))),
        equation="fines = 0.4·(1 − min(slope/0.01, 1)) + 0.4·min(ag/50, 1) + 0.2·min(K/0.4, 1)",
        combine=_c_substrate, rate=_band_rate(0.4, 0.7, True),
        bands=(Band("Good", 0, 0.4), Band("Fair", 0.4, 0.7), Band("Poor", 0.7, 1)),
        breakpoints=("0.40", "0.70"), domain=(0, 1), value_label="Fines/embedding risk"),
    # ---- Physicochemistry ----
    TEMPERATURE_ID: ScoringMethod(   # default view = observed (WQP); surrogate variant below
        TEMPERATURE_ID, "scalar",
        inputs=(MethodInput("temp", "Water temperature", "°C", "T",
                            "WQP observed median", slider=(0, 35, 0.5)),),
        combine=_pick("temp"), rate=_band_rate(20, 25, True), round_ndigits=None,
        bands=(Band("Good", 0, 20), Band("Fair", 20, 25), Band("Poor", 25, 35)),
        breakpoints=("20", "25"), domain=(0, 35),
        value_label="Water temperature", value_unit="°C"),
    CPOM_ID: ScoringMethod(
        CPOM_ID, "combined",
        inputs=(MethodInput("forest", "Forest", _PCT, "forest",
                            "EPA StreamCat forest (rp100)", slider=(0, 100, 1)),
                MethodInput("shrub", "Shrub", _PCT, "shrub",
                            "EPA StreamCat shrub (rp100)", slider=(0, 100, 1)),
                MethodInput("grassland", "Grassland", _PCT, "grassland",
                            "EPA StreamCat grassland (rp100)", slider=(0, 100, 1)),
                MethodInput("wetland", "Wetland", _PCT, "wetland",
                            "EPA StreamCat wetland (rp100)", slider=(0, 100, 1))),
        equation="V = forest + shrub + grassland + wetland  (natural riparian vegetation)",
        combine=_c_detrital, rate=_band_rate(50, 20, False), round_ndigits=1,
        direction=HIGHER_BETTER,
        bands=(Band("Poor", 0, 20), Band("Fair", 20, 50), Band("Good", 50, 100)),
        breakpoints=("20%", "50%"), domain=(0, 100),
        value_label="Natural riparian vegetation", value_unit=_PCT),
    NUTRIENTS_ID: ScoringMethod(
        NUTRIENTS_ID, "worst",
        inputs=(MethodInput("tn", "Total nitrogen", "mg/L", "TN",
                            "WQP observed median", slider=(0, 3, 0.05)),
                MethodInput("tp", "Total phosphorus", "mg/L", "TP",
                            "WQP observed median", slider=(0, 0.3, 0.005))),
        equation="rating = worse of (TN, TP)",
        per_input=(
            ("tn", _band_rate(0.5, 1.5, True),
             (Band("Good", 0, 0.5), Band("Fair", 0.5, 1.5), Band("Poor", 1.5, 3))),
            ("tp", _band_rate(0.05, 0.10, True),
             (Band("Good", 0, 0.05), Band("Fair", 0.05, 0.10), Band("Poor", 0.10, 0.3))),
        ),
        direction=HIGHER_WORSE),
    IMPAIRMENT_ID: ScoringMethod(    # default view = ATTAINS (categorical); surrogate variant below
        IMPAIRMENT_ID, "categorical",
        decisions=(Decision("Fully supporting", "Good"),
                   Decision("Assessed — insufficient information", "Fair"),
                   Decision("Listed impaired — IR 4/5 (303(d)/TMDL)", "Poor"))),
    # ---- Biology ----
    HABITAT_ID: ScoringMethod(
        HABITAT_ID, "combined",
        inputs=(MethodInput("riparian", "Riparian forest", _PCT, "riparian",
                            "EPA StreamCat forest (rp100)", slider=(0, 100, 1)),
                MethodInput("stream_order", "Stream order", "", "order",
                            "NHDPlus stream order", slider=(1, 8, 1), integer=True)),
        equation="V = 0.6·min(riparian/60, 1) + 0.4·min(order/4, 1)",
        combine=_c_habitat, rate=_band_rate(0.55, 0.30, False), direction=HIGHER_BETTER,
        bands=(Band("Poor", 0, 0.30), Band("Fair", 0.30, 0.55), Band("Good", 0.55, 1)),
        breakpoints=("0.30", "0.55"), domain=(0, 1), value_label="Complexity score"),
    BIOINTEGRITY_ID: ScoringMethod(
        BIOINTEGRITY_ID, "combined",
        inputs=(MethodInput("riparian", "Riparian forest", _PCT, "riparian",
                            "EPA StreamCat forest (rp100)", slider=(0, 100, 1)),
                MethodInput("impervious", "Impervious cover", _PCT, "impervious",
                            "EPA StreamCat impervious", slider=(0, 100, 1)),
                MethodInput("agriculture", "Agricultural cover", _PCT, "ag",
                            "EPA StreamCat crop+hay", slider=(0, 100, 1)),
                MethodInput("road_density", "Road density", "km/km²", "roads",
                            "EPA StreamCat road density", slider=(0, 8, 0.1))),
        equation=("V = clamp(0.5 + 0.5·min(riparian/60, 1) − 0.6·stress);  "
                  "stress = 0.45·min(imp/25,1) + 0.35·min(ag/60,1) + 0.20·min(roads/5,1)"),
        combine=_c_bio_integrity, rate=_band_rate(0.66, 0.4, False), direction=HIGHER_BETTER,
        bands=(Band("Poor", 0, 0.4), Band("Fair", 0.4, 0.66), Band("Good", 0.66, 1)),
        breakpoints=("0.40", "0.66"), domain=(0, 1), value_label="Expected condition"),
    INVASIVES_ID: ScoringMethod(
        INVASIVES_ID, "count",
        inputs=(MethodInput("count", "Established non-native taxa", "taxa", "n",
                            "USGS NAS", slider=(0, 6, 1), integer=True),),
        combine=_pick("count"), rate=_rate_invasives, round_ndigits=None,
        direction=HIGHER_WORSE,
        bands=(Band("Good", -0.5, 0.5), Band("Fair", 0.5, 2.5), Band("Poor", 2.5, 6.5)),
        breakpoints=("0", "2"), domain=(0, 6),
        value_label="Established non-native taxa", value_unit="taxa"),
    BARRIERS_ID: ScoringMethod(
        BARRIERS_ID, "count",
        inputs=(MethodInput("count", "Dams within ~1 mile", "dams", "n",
                            "USACE NID", slider=(0, 5, 1), integer=True),),
        combine=_pick("count"), rate=_rate_barriers, round_ndigits=None,
        direction=HIGHER_WORSE,
        bands=(Band("Good", -0.5, 0.5), Band("Fair", 0.5, 1.5), Band("Poor", 1.5, 5.5)),
        breakpoints=("0", "1"), domain=(0, 5),
        value_label="Dams within ~1 mile", value_unit="dams"),
}


# Model-dependent variants (source swap or a special branch) — resolved per row via
# the adapter-emitted ``scoring["model"]``. Keys match the ``model`` string the adapter sets.
_VARIANTS: dict[str, dict[str, ScoringMethod]] = {
    TEMPERATURE_ID: {
        "wqp": METHODS[TEMPERATURE_ID],
        "surrogate": ScoringMethod(
            TEMPERATURE_ID, "combined",
            inputs=(MethodInput("air_temp", "Mean-annual air temp", "°C", "Tair",
                                "PRISM normal (StreamCat)", slider=(0, 25, 0.5)),
                    MethodInput("riparian", "Riparian forest", _PCT, "riparian",
                                "EPA StreamCat forest (rp100)", slider=(0, 100, 1))),
            equation="index = air temp − 2.0·min(riparian/60, 1)",
            combine=_c_temp_surrogate, rate=_band_rate(12.0, 17.0, True), round_ndigits=1,
            bands=(Band("Good", 0, 12), Band("Fair", 12, 17), Band("Poor", 17, 30)),
            breakpoints=("12", "17"), domain=(0, 30),
            value_label="Climate thermal index", value_unit="°C"),
    },
    IMPAIRMENT_ID: {
        "attains": METHODS[IMPAIRMENT_ID],
        "surrogate": ScoringMethod(
            IMPAIRMENT_ID, "combined",
            inputs=(MethodInput("impervious", "Impervious cover", _PCT, "imp",
                                "EPA StreamCat impervious", slider=(0, 100, 1)),
                    MethodInput("agriculture", "Agricultural cover", _PCT, "ag",
                                "EPA StreamCat crop+hay", slider=(0, 100, 1)),
                    MethodInput("road_density", "Road density", "km/km²", "roads",
                                "EPA StreamCat road density", slider=(0, 8, 0.1)),
                    MethodInput("riparian", "Riparian forest", _PCT, "riparian",
                                "EPA StreamCat forest (rp100)", slider=(0, 100, 1))),
            equation=("risk = clamp(stress − 0.15·min(riparian/60,1));  "
                      "stress = 0.45·min(imp/25,1) + 0.35·min(ag/60,1) + 0.20·min(roads/5,1)"),
            combine=_c_impairment_surrogate, rate=_band_rate(0.25, 0.5, True),
            bands=(Band("Good", 0, 0.25), Band("Fair", 0.25, 0.5), Band("Poor", 0.5, 1)),
            breakpoints=("0.25", "0.50"), domain=(0, 1), value_label="Modeled impairment risk"),
    },
    CHANNEL_EVOL_ID: {
        "categorical": ScoringMethod(
            CHANNEL_EVOL_ID, "categorical",
            decisions=(Decision("Channelized (canal/ditch)", "Poor"),)),
    },
}


# --------------------------------------------------------------------------- #
# Resolution + evaluation
# --------------------------------------------------------------------------- #
def resolve(mid: str, model: Optional[str] = None) -> Optional[ScoringMethod]:
    """The ScoringMethod to render for a row: a model-specific variant (temperature/impairment
    source, or the channelized branch) when one exists, else the primary catalog entry."""
    variants = _VARIANTS.get(mid)
    if variants and model in variants:
        return variants[model]
    return METHODS.get(mid)


def sliderable(method: ScoringMethod) -> list:
    return [i for i in method.inputs if i.slider is not None and not i.context_only]


def _index_of(rating: Optional[str]) -> Optional[float]:
    if rating not in config.RATINGS:
        return None
    return scoring.rating_to_index(rating)


def evaluate_method(method: ScoringMethod, values: dict) -> dict:
    """Compute {value, rating, index, functionScore} for a method + input values, mirroring the
    adapter math (bands on the unrounded value; the reported value uses ``round_ndigits``)."""
    if method.mode == "worst":
        ratings = []
        for key, rate_fn, _bands in method.per_input:
            v = values.get(key)
            if v is not None:
                ratings.append(rate_fn(v))
        rating = min(ratings, key=lambda r: _RANK[r]) if ratings else None
        return {"value": None, "rating": rating, "index": _index_of(rating),
                "functionScore": scoring.function_score(_index_of(rating))
                if rating in config.RATINGS else None}
    if method.mode == "categorical":
        rating = values.get("rating")     # site rating drives the highlighted decision row
        return {"value": values.get("value"), "rating": rating, "index": _index_of(rating),
                "functionScore": scoring.function_score(_index_of(rating))
                if rating in config.RATINGS else None}
    # scalar | combined | count
    if method.value_rating is not None:
        value, rating = method.value_rating(values)
    else:
        value = method.combine(values) if method.combine else None
        rating = method.rate(value) if (value is not None and method.rate) else None
    if value is None or rating is None:
        return {"value": None if value is None else _round(value, method),
                "rating": rating, "index": _index_of(rating), "functionScore": None}
    idx = _index_of(rating)
    return {"value": _round(value, method), "rating": rating, "index": idx,
            "functionScore": scoring.function_score(idx) if idx is not None else None}


def _round(value, method: ScoringMethod):
    if method.round_ndigits is None:
        return value
    try:
        return round(float(value), method.round_ndigits)
    except (TypeError, ValueError):
        return value


def evaluate(mid: str, values: dict, model: Optional[str] = None) -> dict:
    """Resolve the method for ``mid``/``model`` and evaluate it (see :func:`evaluate_method`)."""
    method = resolve(mid, model)
    if method is None:
        return {"value": None, "rating": None, "index": None, "functionScore": None}
    return evaluate_method(method, values)


def equation_for(method: ScoringMethod) -> Optional[str]:
    return method.equation


def _num(v) -> str:
    """Compact numeric text (no trailing zeros): 0.33, 5, 2.2, 0.05."""
    return f"{float(v):g}"


def _join_unit(rng: str, unit: str) -> str:
    """Attach a unit to a range string: ``%`` binds tight (``< 5%``), others take a space
    (``< 2 yr``, ``0.5-1.5 mg/L``). ``ratio`` is a bare number so it carries no unit."""
    if not unit or unit == "ratio":
        return rng
    if unit == "%":
        return f"{rng}%"
    if unit in ("taxa", "dams") and rng == "1":     # singular for a lone count
        return f"1 {unit[:-1]}"
    return f"{rng} {unit}"


def _range_from_bands(bands, lo_edge, hi_edge, integer=False) -> dict:
    """Map each Good/Fair/Poor band to a bare value-range string. A band touching the domain's low
    edge reads ``< hi``; one touching the high edge reads ``> lo``; an interior band reads
    ``lo-hi``. For integer (count) metrics the half-step edges collapse to whole numbers
    (e.g. [-0.5, 0.5) -> "0", [0.5, 2.5) -> "1-2", [2.5, ...) -> "> 2")."""
    out: dict[str, str] = {}
    for b in bands:
        touch_lo = b.lo is None or float(b.lo) <= lo_edge + 1e-9
        touch_hi = b.hi is None or float(b.hi) >= hi_edge - 1e-9
        if integer:
            ilo = None if b.lo is None else int(round(float(b.lo) + 0.5))
            ihi = None if b.hi is None else int(round(float(b.hi) - 0.5))
            if touch_hi and not touch_lo:
                out[b.rating] = f"> {ilo - 1}"
            elif ilo is not None and ihi is not None and ilo == ihi:
                out[b.rating] = f"{ilo}"
            elif ilo is not None and ihi is not None:
                out[b.rating] = f"{ilo}-{ihi}"
            continue
        if touch_lo and not touch_hi:
            out[b.rating] = f"< {_num(b.hi)}"
        elif touch_hi and not touch_lo:
            out[b.rating] = f"> {_num(b.lo)}"
        elif b.lo is not None and b.hi is not None:
            out[b.rating] = f"{_num(b.lo)}-{_num(b.hi)}"
    return out


def band_range_texts(method: ScoringMethod) -> dict:
    """The numeric value range for each Good/Fair/Poor rating, derived from the same plot bands so
    the criteria list and the reference curve can never disagree. ``{}`` for categorical metrics
    (their criteria are a decision list, not a value range)."""
    if method.mode == "categorical" or not method.mode:
        return {}
    if method.mode == "worst":
        # one chip per rating joining each indicator's own range, tagged by its symbol
        per_rating: dict[str, list] = {"Good": [], "Fair": [], "Poor": []}
        for key, _rate_fn, bands in method.per_input:
            inp = next((mi for mi in method.inputs if mi.key == key), None)
            tag = (inp.symbol or inp.label) if inp else key
            unit = inp.unit if inp else ""
            lo_edge = min(b.lo for b in bands if b.lo is not None)
            hi_edge = max(b.hi for b in bands if b.hi is not None)
            for rating, rng in _range_from_bands(bands, lo_edge, hi_edge).items():
                per_rating[rating].append(f"{tag} {_join_unit(rng, unit)}")
        return {r: " · ".join(parts) for r, parts in per_rating.items() if parts}
    # scalar | combined | value_rating carry a labeled value; count reads as a bare noun-unit
    lo_edge, hi_edge = method.domain or (0.0, 1.0)
    integer = method.mode == "count"
    ranges = _range_from_bands(method.bands, lo_edge, hi_edge, integer=integer)
    prefix = "" if integer else (f"{method.value_label} " if method.value_label else "")
    return {r: f"{prefix}{_join_unit(rng, method.value_unit)}" for r, rng in ranges.items()}


def slider_specs(method: ScoringMethod, site_inputs: dict) -> list:
    """Per-slider ``(MethodInput, site_value, (min, max, step))`` for the active method, with the
    max auto-expanded when the site value exceeds the default domain (matches the alt-repo UX)."""
    out = []
    for inp in sliderable(method):
        lo, hi, step = inp.slider
        sv = (site_inputs or {}).get(inp.key)
        try:
            svf = float(sv)
            if svf > hi:
                hi = _nice_ceiling(svf)
            if svf < lo:
                lo = svf
        except (TypeError, ValueError):
            svf = None
        out.append((inp, sv, (lo, hi, step)))
    return out


def _nice_ceiling(v: float) -> float:
    """Round ``v`` up to a tidy slider maximum (1/2/5 x 10^n)."""
    if v <= 0:
        return 1.0
    import math
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag
