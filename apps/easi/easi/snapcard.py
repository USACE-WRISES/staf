"""The Identify-step card for a click on a stream outside the StreamCat network.

Pure copy, no Shiny: the app renders the lines and puts the tip behind its
info icon. Three short lines say what happened, what EASI will do, and the
one caveat; the numbers (drainage-area ratio, COMID, distance) live in the
tip so the card never reads as an error. The routing payload is the
``siteAnchor`` from ``easi.routing.route_from_hr``.
"""
from __future__ import annotations

from html import escape
from typing import Any

REACH_METRICS = "low flow, substrate, and biological integrity"


def _fmt_limit(value: Any) -> Any:
    try:
        f = float(value)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return value


def _ft(value: Any) -> str | None:
    try:
        return f"{float(value):,.0f} ft"
    except (TypeError, ValueError):
        return None


def hr_snap_card(anchor: dict) -> dict:
    """``{"lines": [(class, text), ...], "tip_html": str, "declined": bool}``.

    ``class`` is the ``easi-snap-note`` modifier (``ok``, ``warn``, or ``""``).
    Every line is a plain sentence with no em dash and no semicolon.
    """
    anchor = anchor or {}
    clicked = anchor.get("clickedStream") or {}
    scored = anchor.get("scoredReach") or {}
    routing = anchor.get("routing") or {}
    declined = bool(routing.get("declined"))

    name = clicked.get("gnisName") or "an unnamed stream"
    snap_ft = _ft(clicked.get("snapDistFt"))
    where = f"Snapped to {name} ({snap_ft} away)." if snap_ft else f"Snapped to {name}."
    line1 = ("ok", f"✓ {where} Not in the StreamCat lookup network.")
    line2 = ("", "EASI will compute the exact watershed with the STAF site engine "
                 "(usually under a minute, up to about five on a large basin).")

    reach_name = scored.get("gnisName") or "an unnamed reach"
    comid = scored.get("comid")
    reach_id = f"{reach_name} (COMID {comid})" if comid is not None else reach_name
    routed = _ft(routing.get("routedDistanceFt"))
    ratio = routing.get("daRatio")
    limit = _fmt_limit(routing.get("daRatioLimit") or 10)

    if declined:
        line3 = ("warn", "Three reach metrics are unavailable here.")
        if routing.get("declineCode") == "surrogate_da_unavailable" or ratio is None:
            why = ("Drainage area is unknown for the clicked stream or the nearest "
                   "covered reach, so the substitution limit cannot be checked and "
                   "the three metrics stay unavailable.")
        else:
            why = (f"The nearest covered reach, {reach_id}, drains {ratio} times "
                   f"this stream. The limit is {limit}, so EASI leaves the three "
                   "metrics unavailable rather than borrow them from a much larger "
                   "stream.")
    else:
        tail = f", {routed} downstream." if routed else " downstream."
        line3 = ("", f"Reach evidence from {reach_id}{tail}")
        ratio_txt = (f" It drains {ratio} times this stream (limit {limit})."
                     if ratio is not None else "")
        why = (f"They describe {reach_id}"
               + (f", {routed} downstream" if routed else "")
               + f", the nearest reach on the StreamCat network.{ratio_txt}")

    tip_html = (
        '<div class="easi-tip-title">Reach-keyed evidence</div>'
        f'<div class="easi-tip-sec">Three metrics come from a reach on the StreamCat '
        f'network, not from the clicked stream: {escape(REACH_METRICS)}.</div>'
        f'<div class="easi-tip-sec">{escape(why)}</div>'
        '<div class="easi-tip-sec">Everything else scores here: the eight watershed '
        'metrics from the exact watershed, the reach metrics on the clicked stream, '
        'and the point metrics at the point. SFARI and DEEP can assess this site in '
        'full.</div>'
    )
    return {"lines": [line1, line2, line3], "tip_html": tip_html, "declined": declined}
