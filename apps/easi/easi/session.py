"""The EASI assessment file: the header's Save and Open (presentation code: outside the method
digest, ``method_package._PRESENTATION``).

EASI writes the STAF assessment file, the structure SFARI and DEEP write too (owner, 2026-10-05;
``_vendor/staf_workbook/assessment_file.py``):

* ``delineation``: the ``delineate_only`` result as the page holds it, ``ctx_inputs`` included, so
  a site saved before its screening finished can still be screened after Open.
* ``toolData``: what every scenario shares. ``screening`` is the finished screening's own part,
  the HUC12 and the ``report`` every scenario scores from (None until one has run); ``notes`` are
  the worksheet's notes, which every scenario shares; ``method`` names the scoring method the
  screening ran under (``report.scoring_method``).
* ``scenarios``: each scenario's rating state (``scenario_state``: the overrides, the
  observations and the cross-section).

Open restores the screening as it was saved, with no network call: each scenario rescores from
the stored report (``scenario_state.scored_for``), as a scenario switch does.
"""
from __future__ import annotations

import math

from . import calculator, scenario_state
from ._vendor.staf_workbook import assessment_file

TOOL = "EASI"


def dump(delin, base, notes, method, scenarios) -> str:
    """The file. ``base`` is the page's screening result (``base_result``, None before one ran);
    ``scenarios`` is ``assessment_file.scenarios_block``'s."""
    screening = None
    if base is not None:
        screening = {"huc12": (base.get("delineation") or {}).get("huc12"), "report": base.get("report")}
    return assessment_file.dump(TOOL, delin or {},
                                {"screening": screening, "notes": notes or {}, "method": method or {}}, scenarios)


def load(text: str) -> dict:
    """``{"delineation", "screening", "notes", "method", "scenarios"}`` of a saved file, checked
    (a screening's report must rescore). Raises ``assessment_file.AssessmentFileError``."""
    st = assessment_file.parse(text, TOOL)
    data = st["toolData"]
    screening = data.get("screening")
    if screening is not None:
        report = screening.get("report") if isinstance(screening, dict) else None
        if not isinstance(report, dict) or not isinstance(report.get("metricRows"), list):
            raise assessment_file.AssessmentFileError("the saved screening has no report.")
        if not st["delineation"]:
            raise assessment_file.AssessmentFileError("the saved screening has no delineation.")
        try:
            scenario_state.scored_for(report, None)
        except Exception:  # noqa: BLE001 - a report this EASI cannot score never opens half-way
            raise assessment_file.AssessmentFileError("the saved screening cannot be scored.") from None
    notes = data.get("notes")
    method = data.get("method")
    return {"delineation": st["delineation"], "screening": screening,
            "notes": dict((str(k), v) for k, v in notes.items() if isinstance(v, str) and v.strip())
            if isinstance(notes, dict) else {},
            "method": method if isinstance(method, dict) else {},
            "scenarios": st["scenarios"]}


def screened(delin: dict, huc12, report: dict) -> dict:
    """The screening result the Assessment page scores (``base_result``): the delineation without
    the inputs the run read, with the HUC12 and the report."""
    merged = {k: v for k, v in delin.items() if k != "ctx_inputs"}
    merged["delineation"] = {**(delin.get("delineation") or {}), "huc12": huc12}
    merged["report"] = report
    # the twelve EROM monthly flows behind the low-flow variability, kept for the
    # completed calculator's monthly flow helper (ctx_inputs itself is dropped above)
    merged["eromMonthly"] = calculator.monthly_flows((delin.get("ctx_inputs") or {}).get("erom"))
    return merged


def saved_point(delin: dict):
    """The saved site as the page's ``snapped_point``, ``(lat, lon, dist_ft, comid)``: the pin on
    the stream that was picked (the HR site, else a routed site's clicked stream, else the StreamCat
    reach's own snap) and the StreamCat COMID that sources it. None without a usable pair."""
    anchor = delin.get("siteAnchor") or {}
    scored = anchor.get("scoredReach") or {}
    site = next((s for s in (anchor.get("selectedSite"), anchor.get("clickedStream"), scored)
                 if isinstance(s, dict) and s.get("snapLat") is not None and s.get("snapLon") is not None), None)
    if site is None:
        dd = delin.get("delineation") or {}
        site = {"snapLat": dd.get("snapped_lat"), "snapLon": dd.get("snapped_lon")}
    comid = scored.get("comid")
    try:
        if isinstance(comid, bool):
            return None
        point = (float(site["snapLat"]), float(site["snapLon"]), float(site.get("snapDistFt") or 0.0), int(comid))
    except (TypeError, ValueError):
        return None
    lat, lon, dist, cid = point
    if not all(math.isfinite(v) for v in (lat, lon, dist)) or cid <= 0 or abs(lat) > 90 or abs(lon) > 180:
        return None
    return point


def route_segment(anchor):
    """The dashed connector from a routed site's stream to the StreamCat reach that supplies its
    reach metrics, or None for a site on a StreamCat reach."""
    clicked = (anchor or {}).get("clickedStream") or {}
    scored = (anchor or {}).get("scoredReach") or {}
    if clicked.get("snapLat") is None or scored.get("snapLat") is None:
        return None
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "LineString", "coordinates": [
            [clicked["snapLon"], clicked["snapLat"]],
            [scored["snapLon"], scored["snapLat"]]]}}]}


def method_changed(saved: dict, current: dict):
    """A line for Open when the file's screening ran under another scoring method, else None."""
    old, new = (saved or {}).get("method_version"), (current or {}).get("method_version")
    if not old or not new or old == new:
        return None
    from . import report
    return (f"This screening ran under {report.scoring_method_text(saved)}. This EASI scores with "
            f"{report.scoring_method_text(current)}: screen the site again for current results.")


def fingerprint_state(state) -> dict:
    """A scenario's rating state for the unsaved-work check: its ratings, without the height boxes'
    display values (their effect is in the ratings already)."""
    return dict((k, v) for k, v in scenario_state.normalized(state).items() if k != "xsHeights")
