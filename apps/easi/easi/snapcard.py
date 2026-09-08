"""The Identify-step card for a click on a stream outside the StreamCat network.

Pure copy, no Shiny: the app renders the lines and puts the tip behind its
info icon. Three short lines say what happened, what EASI will do, and which
reach supplies the reach evidence (one line, the legend's vocabulary); the
COMID and the drainage-area ratio live in the tip, two sentences, so the card
never reads as an error (2026-09-07: the tip lost its paragraph about what
scores at the point). The routing payload is the ``siteAnchor`` from
``easi.routing.route_from_hr``. Since 2026-09-06 the covered reach supplies
the three metrics whatever it drains, so the card has no warning line.
"""
from __future__ import annotations

from html import escape
from typing import Any

from . import basin

REACH_METRICS = "low flow, substrate, and biological integrity"


def _ft(value: Any) -> str | None:
    try:
        return f"{float(value):,.0f} ft"
    except (TypeError, ValueError):
        return None


def hr_snap_card(anchor: dict) -> dict:
    """``{"lines": [(class, text), ...], "tip_html": str}``.

    ``class`` is the ``easi-snap-note`` modifier (``ok`` or ``""``). Every
    line is a plain sentence with no em dash and no semicolon.
    """
    anchor = anchor or {}
    clicked = anchor.get("clickedStream") or {}
    scored = anchor.get("scoredReach") or {}
    routing = anchor.get("routing") or {}

    name = clicked.get("gnisName") or "an unnamed stream"
    snap_ft = _ft(clicked.get("snapDistFt"))
    where = f"Snapped to {name} ({snap_ft} away)." if snap_ft else f"Snapped to {name}."
    line1 = ("ok", f"\u2713 {where}")
    line2 = ("", "The STAF site engine calculates the HR reach watershed, usually in "
                 "under a minute.")

    reach_name = scored.get("gnisName") or "an unnamed reach"
    comid = scored.get("comid")
    reach_id = f"{reach_name} (COMID {comid})" if comid is not None else reach_name
    routed = _ft(routing.get("routedDistanceFt"))
    ratio = basin.fmt_ratio(routing.get("daRatio"))

    # One line at the pane's width, in the legend's words ("Reach evidence").
    line3 = ("", f"Reach evidence: {reach_name}, {routed + ' ' if routed else ''}downstream.")
    metrics = REACH_METRICS[0].upper() + REACH_METRICS[1:]
    why = (f"{metrics} come from {reach_id}, the nearest StreamCat reach"
           + (f", {routed} downstream" if routed else "") + "."
           + (f" It drains {ratio} times this stream." if ratio else ""))

    tip_html = ('<div class="easi-tip-title">Reach evidence</div>'
                f'<div class="easi-tip-sec">{escape(why)}</div>')
    return {"lines": [line1, line2, line3], "tip_html": tip_html}
