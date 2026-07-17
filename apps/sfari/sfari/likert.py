"""Suggested-Likert matcher.

Compares a pulled numeric value to per-metric break tables encoded from the SFARI
doc's Likert criteria (Table 22 metric ladders). ``suggest()`` returns the
suggested Likert or ``None`` — it is shown to the user as a chip, NEVER auto-applied
(decision: show-don't-autofill). The doc's thresholds are "example screening"
values meant to be regionally calibrated, so several suggestions are flagged
``proxy`` (approximate national default) in the evidence note.
"""
from __future__ import annotations

from typing import Optional

LOW = "low"    # lower value is better (breaks ascending; first `value < bound` wins)
HIGH = "high"  # higher value is better (breaks descending; first `value >= bound` wins)

# metricId -> {"dir", "breaks": [(bound|None, likert), ...], "proxy"?}
BREAKS: dict[str, dict] = {
    "catchment-hydrology-impervious-surface-area": {
        "dir": LOW, "breaks": [(5, "Strongly Agree"), (10, "Agree"),
                               (20, "Disagree"), (None, "Strongly Disagree")]},
    # Watershed agricultural cover (StreamCat crop+hay) as an alternate catchment-hydrology
    # indicator for agriculture-dominated watersheds. Higher breakpoints than impervious
    # (agriculture is a weaker per-unit hydrologic stressor); national default, calibrate
    # regionally. Used by evidence.ev_impervious to flag when agriculture is the more limiting
    # land-cover pressure. Mirrors the EASI selectable indicator (easi/metrics/hydrology.py).
    "catchment-hydrology-agricultural-cover": {
        "dir": LOW, "breaks": [(15, "Strongly Agree"), (25, "Agree"),
                               (50, "Disagree"), (None, "Strongly Disagree")], "proxy": True},
    "catchment-hydrology-road-density": {
        "dir": LOW, "breaks": [(0.5, "Strongly Agree"), (1.0, "Agree"),
                               (2.0, "Disagree"), (None, "Strongly Disagree")]},
    # Watershed wetland % (StreamCat) as a national-default proxy for the doc's
    # "% of floodplain area" criterion (>5 SA / 2-5 A / 1-2 D / <1 SD).
    "surface-water-storage-wetland-coverage": {
        "dir": HIGH, "breaks": [(5, "Strongly Agree"), (2, "Agree"),
                                (1, "Disagree"), (None, "Strongly Disagree")], "proxy": True},
    # Riparian forest % (StreamCat *wsrp100) as a proxy for canopy shade / buffer.
    "light-thermal-regime-riparian-canopy-cover": {
        "dir": HIGH, "breaks": [(75, "Strongly Agree"), (50, "Agree"),
                                (25, "Disagree"), (None, "Strongly Disagree")], "proxy": True},
    "carbon-processing-riparian-corridor-width-and-quality": {
        "dir": HIGH, "breaks": [(60, "Strongly Agree"), (40, "Agree"),
                                (20, "Disagree"), (None, "Strongly Disagree")], "proxy": True},
    "nutrient-cycling-vegetated-riparian-corridor-width": {
        "dir": HIGH, "breaks": [(60, "Strongly Agree"), (40, "Agree"),
                                (20, "Disagree"), (None, "Strongly Disagree")], "proxy": True},
    "community-dynamics-riparian-communities": {
        "dir": HIGH, "breaks": [(60, "Strongly Agree"), (40, "Agree"),
                                (20, "Disagree"), (None, "Strongly Disagree")], "proxy": True},
}


# Per-ecoregion break overrides — populated after SME calibration; falls back to
# the national BREAKS. Shape: {ecoregion_key: {metricId: spec}} (see suggest()).
REGIONAL_BREAKS: dict[str, dict[str, dict]] = {}


def _breaks_for(metric_id: str, ecoregion: Optional[str]):
    if ecoregion:
        regional = REGIONAL_BREAKS.get(ecoregion, {}).get(metric_id)
        if regional:
            return regional
    return BREAKS.get(metric_id)


def suggest(metric_id: str, value, ecoregion: Optional[str] = None) -> Optional[str]:
    """Suggested Likert for ``value`` per the metric's break table, or None.

    ``ecoregion`` (optional) selects a regionally-calibrated break table when one
    has been registered in ``REGIONAL_BREAKS``; otherwise the national default
    applies (the doc's thresholds are explicitly "calibrate regionally").
    """
    spec = _breaks_for(metric_id, ecoregion)
    if spec is None or value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if spec["dir"] == LOW:
        for bound, likert in spec["breaks"]:
            if bound is None or v < bound:
                return likert
    else:  # HIGH
        for bound, likert in spec["breaks"]:
            if bound is None or v >= bound:
                return likert
    return None


def is_proxy(metric_id: str) -> bool:
    return bool((BREAKS.get(metric_id) or {}).get("proxy"))
