"""Biology-discipline EASI metric adapters.

Biological integrity (population support) has no reliable national per-reach IBI
source (NRSA is too sparse; a usable national per-COMID predicted-bio column is
unconfirmed), so it is computed as a low-confidence *modeled surrogate* from
already-fetched landscape/stressor signals — clearly labeled not a measured IBI,
and overrideable with state biomonitoring data.
"""
from __future__ import annotations

from . import base
from ..datasources import nas, nid_barriers
from .base import AnalysisContext, MetricResult, unavailable

INVASIVES_ID = "community-dynamics-invasive-non-native-species-presence"
BARRIERS_ID = ("watershed-connectivity-fish-passage-and-barrier-effects-"
               "longitudinal-connectivity")
HABITAT_ID = "habitat-provision-in-stream-habitat-complexity-and-cover"
BIOINTEGRITY_ID = "population-support-biological-integrity-ibi-community-condition"


def invasives(ctx: AnalysisContext) -> MetricResult:
    """Established nonindigenous aquatic taxa in the local HUC12 (USGS NAS)."""
    taxa = nas.established_taxa(huc12=ctx.huc12, huc8=ctx.huc8)
    if taxa is None:
        return unavailable(INVASIVES_ID, "NAS query unavailable", "M")
    n = len(taxa)
    rating = "Good" if n == 0 else ("Fair" if n <= 2 else "Poor")
    sample = ", ".join(taxa[:4]) + ("…" if n > 4 else "")
    scope = "HUC12" if ctx.huc12 else "HUC8"
    txt = f"{n} established non-native taxa ({scope})"
    if sample:
        txt += f": {sample}"
    return MetricResult(INVASIVES_ID, value=n, value_text=txt, rating=rating,
                        confidence="M", source=f"USGS NAS ({scope})",
                        note="presence-based proxy")


def barriers(ctx: AnalysisContext) -> MetricResult:
    """Fish-passage barriers within ~1 mile of the reach (USACE NID)."""
    dams = nid_barriers.barriers_near(ctx.lat, ctx.lon, miles=1.0)
    if dams is None:
        return unavailable(BARRIERS_ID, "NID query unavailable", "M")
    n = len(dams)
    rating = "Good" if n == 0 else ("Fair" if n == 1 else "Poor")
    return MetricResult(BARRIERS_ID, value=n,
                        value_text=f"{n} dam(s) within ~1 mile (NID)", rating=rating,
                        confidence="M", source="USACE NID (proximity)",
                        note="proximity proxy; upstream/downstream network trace refines")


def habitat_complexity(ctx: AnalysisContext) -> MetricResult:
    """In-stream habitat complexity proxy: riparian cover + stream size."""
    rip = base.riparian_forest_pct(ctx)
    so, slope = ctx.stream_order, ctx.slope
    if rip is None and so is None and slope is None:
        return unavailable(HABITAT_ID, "no riparian/order/slope data", "L")
    score = 0.6 * min((rip or 0) / 60, 1) + 0.4 * min((so or 1) / 4, 1)
    rating = base.band(score, good_below=0.55, fair_below=0.30, higher_is_worse=False)
    return MetricResult(HABITAT_ID, value=round(score, 2),
                        value_text=f"complexity {score:.2f} "
                                   f"(riparian {rip}%, order {so})",
                        rating=rating, confidence="L",
                        source="NHDPlus order/slope + riparian (proxy)",
                        note="habitat-complexity proxy; field survey refines")


def biological_integrity(ctx: AnalysisContext) -> MetricResult:
    """Modeled biological-integrity surrogate (low confidence, overrideable).

    No reliable per-reach national IBI exists, so this combines already-fetched
    landscape signals into an "expected biological condition" (0-1): riparian
    cover supports it; impervious cover, agriculture, and road density degrade it.
    Explicitly NOT a measured IBI — override with state biomonitoring where known.
    """
    s = base.sc(ctx)
    rip = base.riparian_forest_pct(ctx)          # higher is better
    imp = s.get("pctimp2019ws")                  # higher is worse
    ag = base.ag_pct(ctx)                        # higher is worse
    rd = s.get("rddensws")                       # higher is worse

    if all(v is None for v in (rip, imp, ag, rd)):
        return MetricResult(
            BIOINTEGRITY_ID, value=None,
            value_text="no landscape data — screening default",
            rating="Fair", confidence="L", source="default (no national IBI source)",
            note="not a measured IBI; no inputs available — overrideable")

    support = min((rip or 0.0) / 60.0, 1.0)
    stress = (0.45 * min((imp or 0.0) / 25.0, 1.0)
              + 0.35 * min((ag or 0.0) / 60.0, 1.0)
              + 0.20 * min((rd or 0.0) / 5.0, 1.0))
    score = max(0.0, min(1.0, 0.5 + 0.5 * support - 0.6 * stress))
    rating = base.band(score, good_below=0.66, fair_below=0.4, higher_is_worse=False)
    return MetricResult(
        BIOINTEGRITY_ID, value=round(score, 2),
        value_text=f"modeled condition {score:.2f} "
                   f"(riparian {rip}%, impervious {imp}%)",
        rating=rating, confidence="L",
        source="Modeled habitat/stressor surrogate (landscape composite)",
        note="not a measured IBI — low-confidence national surrogate; "
             "override with state biomonitoring/IBI where available")
