"""The routed-site note: one text for the report banner and the PDF
(2026-09-04, a note instead of a warning since 2026-09-06; the Assessment
worksheet's ribbon was dropped 2026-09-07 to match SFARI, which shows none).

A click outside the StreamCat network used to produce three different
paragraphs (modal, ribbon, PDF) with engine version numbers, a ratio, a limit,
and five lines listing every metric by source, and until 2026-09-06 it warned
that three metrics could not be scored past the drainage-area bound. Nothing
is withheld any more.

Since 2026-09-08 the note is split by where it is read. ``routed_notice`` says
where the watershed metrics come from, which the report modal shows only when
the watershed could not be calculated (its Basin characteristics block names
the engine otherwise) and the PDF always shows, having no such block.
``marker_note`` is the footnote beside the metric table, and the rows it
describes carry a marker of their own rather than being listed by name. The
per-metric provenance (the reach, the routed distance, the drainage-area
ratio) rides each of those rows as ``anchorNote`` (``borrowed_note``), the CSV,
the PDF rows, and the report table's "Scored at" column.
"""
from __future__ import annotations

from typing import Any, Optional

from . import basin

TITLE = "Note"

#: Ties a borrowed metric row to the footnote beside the table. Lives here so
#: the report modal and the PDF mark the same rows with the same character.
BORROWED_MARK = "†"
OUTSIDE = "This stream is outside the StreamCat network"


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


def routed_notice(anchor: Optional[dict], delineation: Optional[dict]) -> Optional[dict]:
    """``{"title": "Note", "lines": [...]}`` for a routed site, None on the
    covered network. Plain sentences, no engine versions, no ratio, no limit,
    no em dash, no semicolon; the numbers a reader may want (the reach, the
    routed distance, the drainage-area ratio) sit on the borrowed rows."""
    anchor = anchor or {}
    if anchor.get("anchorKind") != "hrSurrogate":
        return None
    d = delineation or {}
    r = anchor.get("routing") or {}
    source = d.get("watershed_source") or ""
    eng = d.get("watershed_engine") or {}
    dist = _ft(r.get("routedDistanceFt"))
    dist_txt = f"{dist} downstream" if dist else "downstream"

    if source == "site-engine":
        first = (f"{OUTSIDE}. Watershed metrics use the HR reach watershed "
                 f"({basin.fmt_km2(eng.get('areaSqkm'))}) from the STAF site engine.")
    elif source == "not-calculated":
        first = (f"{OUTSIDE}, and the STAF site engine could not calculate its "
                 f"watershed ({eng.get('reason') or 'not calculated'}). Watershed "
                 "metrics are unavailable.")
    else:
        # The streamcat-legacy policy: every metric rides the covered reach.
        reach = d.get("gnis_name") or "the nearest StreamCat reach"
        ratio = basin.fmt_ratio(r.get("daRatio"))
        limit = _limit(r.get("daRatioLimit") or 10)
        tail = f" (drainage area ratio {ratio}, limit {limit})" if ratio else ""
        return {"title": TITLE,
                "lines": [f"{OUTSIDE}. Results describe {reach}, {dist_txt}, not the "
                          f"clicked stream{tail}."]}

    return {"title": TITLE, "lines": [first]}


def marker_note(anchor: Optional[dict]) -> str:
    """The footnote for the marked rows: where the borrowed values come from.

    This used to be the note's second sentence and it named three metrics, but
    the borrowed set is not those three. Two more rows re-anchor to the reach
    downstream whenever their StreamCat fallback fires (nutrients and
    regulatory impairment, in ``assessment._annotate_anchors``), which happens
    exactly when ATTAINS or WQP has nothing at the clicked point, a situation
    correlated with being off-network in the first place. So the sentence named
    three while the table's own "Scored at" column could show five. The rows
    now carry a marker and this says what the marker means, which cannot go
    stale. Empty on the covered network.
    """
    anchor = anchor or {}
    if anchor.get("anchorKind") != "hrSurrogate":
        return ""
    dist = _ft((anchor.get("routing") or {}).get("routedDistanceFt"))
    where = f"{dist} downstream" if dist else "downstream"
    return f"Comes from the nearest StreamCat reach, {where}."


def borrowed_note(anchor: Optional[dict]) -> str:
    """One sentence for a COMID-keyed row on a routed site: which covered reach
    scored it, how far downstream, and how much more it drains than the
    clicked stream. Empty on the covered network. Clauses a payload cannot
    fill (an unnamed reach, an unknown distance or ratio) are left out."""
    anchor = anchor or {}
    if anchor.get("anchorKind") != "hrSurrogate":
        return ""
    scored = anchor.get("scoredReach") or {}
    r = anchor.get("routing") or {}
    name, comid = scored.get("gnisName"), scored.get("comid")
    if name and comid is not None:
        reach = f"the nearest StreamCat reach, {name} (COMID {comid})"
    elif comid is not None:
        reach = f"the nearest StreamCat reach (COMID {comid})"
    elif name:
        reach = f"the nearest StreamCat reach, {name}"
    else:
        reach = "the nearest StreamCat reach"
    parts = [f"Scored from {reach}"]
    dist = _ft(r.get("routedDistanceFt"))
    if dist:
        parts.append(f"{dist} downstream")
    ratio = basin.fmt_ratio(r.get("daRatio"))
    if ratio:
        parts.append(f"which drains {ratio} times this stream")
    return ", ".join(parts) + "."
