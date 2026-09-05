"""The routed-site warning: one text for the report banner, the worksheet
ribbon, and the PDF (2026-09-04).

A click outside the StreamCat lookup network used to produce three different
paragraphs (modal, ribbon, PDF) with engine version numbers, a ratio, a limit,
and five lines listing every metric by source. This module says the two things
a reader needs, marked as a warning: where the watershed metrics come from, and
which three metrics could not be scored (or which reach scored them). The
provenance detail stays in the CSV, the PDF rows, and the report table's
"Scored at" column.
"""
from __future__ import annotations

from typing import Any, Optional

from . import basin

TITLE = "Warning"
REACH_METRICS = "low flow, substrate, and biological integrity"


def _ratio(value: Any) -> Optional[str]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f"{f:.0f}" if f >= 10 else f"{f:.1f}"


def _limit(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "10"
    return f"{f:.0f}" if f.is_integer() else f"{f:g}"


def _ft(value: Any) -> Optional[str]:
    try:
        return f"{float(value):,.0f} ft"
    except (TypeError, ValueError):
        return None


def routed_warning(anchor: Optional[dict], delineation: Optional[dict]) -> Optional[dict]:
    """``{"title": "Warning", "lines": [...]}`` for a routed site, None on the
    covered network. Plain sentences, no engine versions, no em dash, no
    semicolon; every number a reader acts on is in the line that needs it."""
    anchor = anchor or {}
    if anchor.get("anchorKind") != "hrSurrogate":
        return None
    d = delineation or {}
    r = anchor.get("routing") or {}
    scored = anchor.get("scoredReach") or {}
    source = d.get("watershed_source") or ""
    eng = d.get("watershed_engine") or {}
    dist = _ft(r.get("routedDistanceFt"))
    dist_txt = f"{dist} downstream" if dist else "downstream"
    ratio, limit = _ratio(r.get("daRatio")), _limit(r.get("daRatioLimit") or 10)
    not_in = "This stream is not in the StreamCat lookup network."

    if source == "site-engine":
        first = (f"{not_in} Watershed metrics use the exact watershed from the "
                 f"STAF site engine ({basin.fmt_km2(eng.get('areaSqkm'))}).")
    elif source == "not-calculated":
        first = (f"{not_in[:-1]}, and the STAF site engine could not calculate its "
                 f"watershed ({eng.get('reason') or 'not calculated'}). Watershed "
                 "metrics are unavailable.")
    else:
        reach = d.get("gnis_name") or "the nearest covered reach"
        tail = f" (drainage area ratio {ratio}, limit {limit})" if ratio else ""
        return {"title": TITLE,
                "lines": [f"{not_in} Results describe {reach}, {dist_txt}, not the "
                          f"clicked stream{tail}."]}

    reach_name = scored.get("gnisName") or "an unnamed reach"
    comid = scored.get("comid") if scored.get("comid") is not None else d.get("comid")
    reach_id = f"{reach_name} (COMID {comid})" if comid is not None else reach_name
    if r.get("declined"):
        if ratio is None or r.get("declineCode") == "surrogate_da_unavailable":
            why = "The drainage area needed to check the substitution limit is unknown."
        else:
            why = (f"The nearest StreamCat reach drains {ratio} times this stream, "
                   f"past the limit of {limit}.")
        second = f"Three metrics could not be scored: {REACH_METRICS}. {why}"
    else:
        second = (f"Three metrics come from the nearest StreamCat reach, {reach_id}, "
                  f"{dist_txt}: {REACH_METRICS}.")
    return {"title": TITLE, "lines": [first, second]}
