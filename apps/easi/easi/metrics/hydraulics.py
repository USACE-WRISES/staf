"""Hydraulics-discipline EASI metric adapters."""
from __future__ import annotations

from . import base
from .base import AnalysisContext, MetricResult, unavailable

LOW_FLOW_ID = "low-flow-and-baseflow-dynamics-low-flow-wetted-connectivity"
HYPORHEIC_ID = "hyporheic-connectivity-hyporheic-exchange-indicators"
ENTRENCHMENT_ID = "floodplain-connectivity-floodplain-access-entrenchment"
FLOODPLAIN_ENGAGEMENT_ID = ("high-flow-dynamics-floodplain-engagement-frequency-"
                            "bankfull-recurrence")

PERENNIAL, INTERMITTENT, EPHEMERAL = 46006, 46003, 46007

# Generic dimensionless flood-frequency growth curve, normalized to bankfull≈Q1.5
# (Q_T / Q_1.5). A documented national screening generalization — a gage analysis
# or regional regression refines it; the metric is overrideable.
_GROWTH = [(1.5, 1.0), (2.0, 1.2), (5.0, 1.9), (10.0, 2.6), (25.0, 3.6), (50.0, 4.4)]


def _recurrence_from_ratio(ratio: float) -> float:
    """Invert a Q_floodplain/Q_bankfull ratio into a recurrence interval (years)."""
    if ratio <= 1.0:
        return 1.5
    for (t0, r0), (t1, r1) in zip(_GROWTH, _GROWTH[1:]):
        if ratio <= r1:
            frac = (ratio - r0) / (r1 - r0) if r1 > r0 else 0.0
            return t0 + frac * (t1 - t0)
    return _GROWTH[-1][0]


def rate_engagement(bhr):
    """Floodplain-engagement recurrence from the bank-height ratio (vertical axis).

    BHR = top-of-bank height / bankfull depth — how far above bankfull flow must rise
    to spill onto the floodplain. Converted to a discharge multiple by a wide-channel
    Manning approximation (Q ~ depth^(5/3)) and then to a recurrence interval via the
    regional growth curve: BHR~1 -> engages near bankfull (~1-2 yr, Good); higher BHR ->
    incised, rarer engagement (Fair/Poor). Pure (no ctx/network) — reused by the metric
    adapter and the editable cross-section recompute. Returns ``(rating, t_years)``, or
    ``(None, None)`` if BHR is unavailable.
    """
    if bhr is None or bhr <= 0:
        return None, None
    t_years = _recurrence_from_ratio(bhr ** (5.0 / 3.0))
    rating = "Good" if t_years <= 2.0 else ("Fair" if t_years <= 5.0 else "Poor")
    return rating, round(t_years, 1)


def floodplain_engagement(ctx: AnalysisContext) -> MetricResult:
    """Floodplain engagement frequency (bankfull recurrence) — the vertical axis.

    How *often* flows top the bank onto the floodplain, distinct from Floodplain
    Access (which measures lateral entrenchment). The bank-height ratio (top-of-bank
    height / bankfull depth) sets how far above bankfull flow must rise; it maps to a
    recurrence interval (~1-2 yr = Good, ~3-5 yr = Fair, rarely = Poor). Conservative
    screening default when no cross-section is available. Overrideable (e.g. with a
    gage/xs-calc analysis).
    """
    g = ctx.extras.get("reach_geomorph") or {}
    bhr = g.get("bank_height_ratio")
    rating, t_years = rate_engagement(bhr)
    if rating:
        return MetricResult(
            FLOODPLAIN_ENGAGEMENT_ID, value=t_years,
            value_text=f"floodplain engaged by ~{t_years:.0f}-yr flow "
                       f"(bank-height ratio {bhr} — how often flow tops the bank)",
            rating=rating, confidence="L" if g.get("edge_limited") else "M",
            source="USGS 3DEP bank-height ratio (vertical incision → recurrence)",
            note="recurrence proxy from bank height above bankfull; "
                 "gage/regional flood-frequency regression refines")

    return MetricResult(
        FLOODPLAIN_ENGAGEMENT_ID, value=None,
        value_text="insufficient terrain data — screening default",
        rating="Fair", confidence="L", source="default (no 3DEP cross-section)",
        note="no usable cross-section; conservative screening default — overrideable")


def rate_entrenchment(er):
    """Lateral floodplain access from the entrenchment ratio (flood-prone width /
    bankfull width): ER >= 2.2 Good, >= 1.4 Fair, else Poor. Pure — reused by the metric
    adapter and the editable cross-section recompute. Returns the rating, or ``None`` if
    ER is unavailable. (Incision / how often it floods is the separate High flow dynamics
    metric — see ``rate_engagement``.)
    """
    if er is None:
        return None
    return "Good" if er >= 2.2 else ("Fair" if er >= 1.4 else "Poor")


def floodplain_access(ctx: AnalysisContext) -> MetricResult:
    """Rosgen entrenchment ratio from 3DEP cross-sections — lateral floodplain access.

    ER = flood-prone width (~2x bankfull depth) / bankfull width: >= 2.2 a broad
    accessible floodplain (Good); 1.4-2.2 moderate (Fair); < 1.4 entrenched (Poor).
    Measures whether a floodplain is laterally *there* to access (a form question);
    *how often* it floods is the separate High flow dynamics metric. DEM-derived with
    curve-estimated bankfull -> approximate; overrideable with a user XS (e.g. xs-calc).
    """
    g = ctx.extras.get("reach_geomorph") or {}
    er = g.get("entrenchment_ratio")
    if er is None:
        return unavailable(ENTRENCHMENT_ID, "3DEP entrenchment unavailable for reach", "M")
    rating = rate_entrenchment(er)
    edge = bool(g.get("edge_limited"))
    res = g.get("dem_resolution_m") or 10
    note = (f"DEM {res} m; bankfull from national curve; override via xs-calc"
            + (" — floodprone reached buffer edge (ER likely under-estimated)"
               if edge else ""))
    return MetricResult(ENTRENCHMENT_ID, value=er,
                        value_text=f"entrenchment ratio {er} — floodprone width / "
                                   f"bankfull width (representative cross-section)",
                        rating=rating, confidence="L" if edge else "M",
                        source="USGS 3DEP cross-sections (Rosgen entrenchment ratio)", note=note)


def low_flow_connectivity(ctx: AnalysisContext) -> MetricResult:
    """Flow-permanence proxy (NHD FCODE) for low-flow wetted connectivity."""
    fc = ctx.fcode
    if fc is None:
        return unavailable(LOW_FLOW_ID, "no flow-permanence (FCODE) data", "L")
    mapping = {PERENNIAL: ("Good", "perennial flow"),
               INTERMITTENT: ("Fair", "intermittent flow"),
               EPHEMERAL: ("Poor", "ephemeral flow")}
    rating, desc = mapping.get(fc, ("Fair", f"FCODE {fc}"))
    return MetricResult(LOW_FLOW_ID, value=fc,
                        value_text=f"{desc} (NHD FCODE {fc})", rating=rating,
                        confidence="L", source="NHDPlus flow permanence (FCODE)",
                        note="permanence proxy for low-flow wetted connectivity")


def hyporheic(ctx: AnalysisContext) -> MetricResult:
    """Hyporheic-exchange potential proxy from channel slope + sinuosity."""
    slope, sin = ctx.slope, ctx.sinuosity
    if slope is None and sin is None:
        return unavailable(HYPORHEIC_ID, "no slope/sinuosity data", "L")
    s = min((slope or 0.0) / 0.01, 1.0)
    sn = max(min(((sin or 1.0) - 1.0) / 0.5, 1.0), 0.0)
    score = 0.6 * s + 0.4 * sn
    rating = base.band(score, good_below=0.6, fair_below=0.3, higher_is_worse=False)
    return MetricResult(HYPORHEIC_ID, value=round(score, 2),
                        value_text=f"exchange potential {score:.2f} "
                                   f"(slope {slope}, sinuosity {sin})",
                        rating=rating, confidence="L",
                        source="NHDPlus slope + sinuosity (proxy)",
                        note="bedform/exchange proxy; field/SDA refinement later")
