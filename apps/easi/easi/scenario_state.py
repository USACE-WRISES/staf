"""What one EASI scenario holds, and its scores (presentation code: outside the method digest).

A scenario changes ratings only (owner, 2026-10-03): the Good/Fair/Poor override of any function,
the cross-section (which sampled section is shown and its bankfull and low-bank heights, which
re-rate the three cross-section metrics), and the observed channel class and bank condition. The
screening itself, its evidence and the method are shared by every scenario, and so are the notes.

``scored_for`` composes a state exactly as the Assessment page always has: the override rescore,
then the cross-section relabel, then the observations (``assessment.apply_observed_evidence`` must
run last: a rescore after it would rebuild an observed row from its generated rating).
"""
from __future__ import annotations

import copy

from . import assessment

#: ``geomOwned``: metric ids whose rating comes from the cross-section; ``geomText`` and
#: ``geomScoring``: their value text and recomputed scoring trace; ``geomReason``: edited | scrolled;
#: ``xsSel``: the shown section (None: the screening's own); ``xsHeights``: the edited heights in
#: metres (``{"bankfull_m", "lowbank_m"}``), None while they are the section's defaults.
FIELDS = {"overrides": dict, "observed": dict, "geomOwned": list, "geomText": dict, "geomScoring": dict,
          "geomReason": str, "xsSel": None, "xsHeights": None}


def empty() -> dict:
    return {"overrides": {}, "observed": {}, "geomOwned": [], "geomText": {}, "geomScoring": {},
            "geomReason": "edited", "xsSel": None, "xsHeights": None}


def normalized(state) -> dict:
    """A copy of ``state`` with every field present and of the right kind."""
    out = empty()
    if not isinstance(state, dict):
        return out
    for key, kind in FIELDS.items():
        value = state.get(key)
        if value is None or (kind is not None and not isinstance(value, kind)):
            continue
        out[key] = copy.deepcopy(value)
    if out["geomReason"] not in ("edited", "scrolled"):
        out["geomReason"] = "edited"
    if out["xsSel"] is not None:
        try:
            out["xsSel"] = int(out["xsSel"])
        except (TypeError, ValueError):
            out["xsSel"] = None
    return out


def xs_block(report: dict, sel):
    """The cross-section ``sel`` names (None: the screening's own), as the page shows it."""
    cross = (report or {}).get("crossSection") or {}
    cands = cross.get("candidates") or []
    if cands:
        default = int(cross.get("selected", 0) or 0)
        idx = default if sel is None else int(sel)
        block = cands[min(max(idx, 0), len(cands) - 1)]
    else:
        block = cross.get("geom")
    return block if (block and block.get("thalweg") is not None) else None


def scored_for(report: dict, state) -> dict:
    """The scored report of one scenario: ``report`` is the screening's (``base_result``)."""
    st = normalized(state)
    sc = assessment.rescore(report, dict(st["overrides"]))
    owned = set(st["geomOwned"])
    if owned:  # relabel so an edited or scrolled section doesn't read as a manual override
        texts = st["geomText"]
        traces = st["geomScoring"]
        scrolled = st["geomReason"] == "scrolled"
        station = (xs_block(report, st["xsSel"]) or {}).get("label") or "the shown station"
        where = f"cross-section at {station}" if scrolled else "edited cross-section"
        note = (f"scored from the section at {station}, not the reach median" if scrolled
                else "recomputed from your bankfull/floodplain heights")
        for row in sc["metricRows"]:
            mid = row["metricId"]
            if mid in owned:
                row["status"] = "xs-derived"
                row["source"] = where
                row["valueText"] = texts.get(mid) or f"from {where}: {row['rating']}"
                row["note"] = note
                # carry the recomputed trace so the Scoring method panel shows the
                # edited geometry, not the geometry the run started from
                trace = traces.get(mid)
                if trace:
                    row["scoring"] = trace
                    row["generatedRating"] = trace.get("generatedRating")
                    row["completeness"] = trace.get("completeness", row.get("completeness"))
    observed = st["observed"]
    if observed:
        sc = assessment.apply_observed_evidence(sc, observed)
    return sc
