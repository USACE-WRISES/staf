"""The EASI project's workflow: five stages, their status, and plain names for the method.

An EASI project walks five stages in the same strip and Project panel as a DEEP project
(``views/stagebar.py``), with the same status vocabulary (``run_state``):

1. Method: identity, lineage, the 20 functions and what each one computes;
2. Development data: what the curves were developed from;
3. Curves and criteria: the reference curves and fixed criteria (edited in a revision);
4. Final selection: the method selected for each function and what needs review;
5. Review and publish: what changed, its consequences, export and publish.

Everything here is pure (no Shiny), so the strip, the page and the tests share it.
"""
from __future__ import annotations

from typing import Optional

from .. import run_state as rs
from . import register as reg

STAGE_KEYS = ["method", "development", "curves", "selection", "publish"]

STAGE_LABELS = {
    "method": "Method: identity, lineage and functions",
    "development": "Development data",
    "curves": "Curves and criteria",
    "selection": "Final selection",
    "publish": "Review and publish",
}

STAGE_SHORT = {
    "method": "Method",
    "development": "Development data",
    "curves": "Curves and criteria",
    "selection": "Final selection",
    "publish": "Review and publish",
}

TARGET_PREFIX = "easi:"

#: The operators of the catalog, in words.
OPERATOR_LABELS = {
    "threshold": "Fixed bands",
    "worst_index": "Worst of its inputs",
    "best_index": "Best of its inputs",
    "sum_capped": "Capped sum",
    "ratio": "Ratio",
    "categorical_lookup": "Category lookup",
    "unscored": "Not scored",
}

#: NARS-9 regions and slope classes, the strata of the reference curves.
STRATUM_NAMES = {
    "CPL": "Coastal Plains",
    "NAP": "Northern Appalachians",
    "NPL": "Northern Plains",
    "SAP": "Southern Appalachians",
    "SPL": "Southern Plains",
    "TPL": "Temperate Plains",
    "UMW": "Upper Midwest",
    "WMT": "Western Mountains",
    "XER": "Xeric",
    "lt_0.5": "Slope under 0.5%",
    "0.5_to_2": "Slope 0.5 to 2%",
    "ge_2": "Slope 2% or more",
    "national": "National",
}

#: Reference curve families, in words.
FAMILY_NAMES = {
    "corridor-natural": "Natural cover in the riparian corridor",
    "corridor-woody": "Woody cover in the riparian corridor",
    "flow-variability": "Monthly flow variability",
    "entrenchment": "Entrenchment ratio",
}

STRATIFIER_NAMES = {"nars9": "NARS-9 region", "slope_class": "slope class",
                    "l2": "Level II region", "national": "national"}


def curve_strata(curves: dict) -> dict:
    """``{stratifier words: every set of it has a national fallback}`` over the method's curve
    sets, in the order the sets first name them (a national-only set is ``national``)."""
    out: dict = {}
    for s in ((curves or {}).get("sets") or {}).values():
        key = str(s.get("stratifier") or "national")
        name = STRATIFIER_NAMES.get(key, key)
        fallback = key != "national" and "national" in (s.get("curves") or {})
        out[name] = out.get(name, True) and fallback
    return out


def strata_names(curves: dict) -> list:
    """What the method's reference curves are stratified by, as ``meta.geography.strata``."""
    return list(curve_strata(curves))


def geography_sentence(curves: dict) -> str:
    """The Method stage's geography, from the curve sets the method reads (an adopted
    alternative can bring Level II or national-only sets)."""
    strata = curve_strata(curves)
    by = [n for n in strata if n != "national"]
    if not by:
        return "one national reference curve per family"
    with_fallback = [n for n in by if strata[n]]
    text = "reference curves by " + " or ".join(by)
    if with_fallback and len(with_fallback) == len(by):
        text += ", each with a national fallback" if len(by) > 1 else ", with a national fallback"
    elif with_fallback:
        text += f" ({' and '.join(with_fallback)} with a national fallback)"
    if "national" in strata:
        text += "; some families have national curves only"
    return text

#: What each curve family's x axis measures.
QUANTITY_NAMES = {
    "natural_wsrp100": "natural cover in the 100 m riparian corridor (%)",
    "woody_wsrp100": "woody cover in the 100 m riparian corridor (%)",
    "q_cv_monthly": "coefficient of variation of the 12 monthly mean flows",
    "er_median": "entrenchment ratio (reach median)",
}


def stage_target(key: str) -> str:
    return TARGET_PREFIX + key


def stratum_name(code: str) -> str:
    return STRATUM_NAMES.get(str(code), str(code))


def family_name(name: str) -> str:
    return FAMILY_NAMES.get(str(name), str(name).replace("-", " ").capitalize())


def version_line(project) -> str:
    """"v2 draft, a revision of v1" / "v1, preliminary" / "v1 as imported"."""
    meta = project.meta
    ver = int(meta.get("version") or 1)
    status = str(meta.get("status") or "draft")
    origin = project.origin()
    kind = origin.get("kind")
    label = {"certified": "final"}.get(status, status)
    if kind == "revision":
        return f"v{ver} {label}, a revision of v{origin.get('version')}"
    if kind == "library":
        return f"v{ver}, {label} in the library"
    return f"v{ver}, imported from EASI"


def snapshot(project, *, preview: Optional[dict] = None, published: bool = False) -> dict:
    """What the strip and the page need to know about an EASI project, computed once.
    ``published``: the library holds this version number with exactly these files."""
    rows = reg.status_rows(project)
    pending = [r for r in rows if r["needsReview"]]
    changed = not project.is_unchanged_from_origin()
    preview_current = bool(preview and preview.get("packageDigest") == project.package_digest)
    return {
        "revision": project.is_revision(),
        "changed": changed,
        "n_functions": len(rows),
        "n_pending": len(pending),
        "pending": [r["functionName"] for r in pending],
        "preview_current": preview_current,
        "published": bool(published),
        "n_evidence": len(project.evidence or []),
        "version_line": version_line(project),
    }


def stage_status(snap: dict) -> dict[str, dict]:
    """Status and one-line detail per stage, in the strip's vocabulary."""
    out: dict[str, dict] = {}
    out["method"] = {"status": rs.STAGE_DONE, "detail": snap["version_line"]}
    n_ev = snap["n_evidence"]
    out["development"] = (
        {"status": rs.STAGE_DONE, "detail": f"{n_ev} development data package"
                                            + ("" if n_ev == 1 else "s")}
        if n_ev else
        {"status": rs.STAGE_READY, "detail": "No development data packages are attached."})
    if not snap["revision"]:
        out["curves"] = {"status": rs.STAGE_DONE,
                         "detail": "This version stays as it is; start a revision to change it."}
    elif not snap["changed"]:
        out["curves"] = {"status": rs.STAGE_READY, "detail": "No changes yet."}
    elif snap["preview_current"]:
        out["curves"] = {"status": rs.STAGE_DONE, "detail": "Changed; consequences previewed."}
    else:
        out["curves"] = {"status": rs.STAGE_ATTENTION,
                         "detail": "Changed; preview the consequences."}
    if snap["n_pending"]:
        out["selection"] = {"status": rs.STAGE_ATTENTION,
                            "detail": f"{snap['n_pending']} changed "
                                      + ("function needs" if snap["n_pending"] == 1
                                         else "functions need")
                                      + " your confirmation."}
    else:
        out["selection"] = {"status": rs.STAGE_DONE,
                            "detail": f"A method selected for all {snap['n_functions']} functions."}
    if snap["published"]:
        out["publish"] = {"status": rs.STAGE_DONE, "detail": "This version is in the library."}
    elif snap["revision"] and not snap["changed"]:
        out["publish"] = {"status": rs.STAGE_BLOCKED,
                          "detail": "Nothing to publish until the method changes."}
    elif snap["n_pending"]:
        out["publish"] = {"status": rs.STAGE_BLOCKED,
                          "detail": "Confirm the changed selections first."}
    else:
        out["publish"] = {"status": rs.STAGE_READY, "detail": "Ready to review and publish."}
    return out


__all__ = ["STAGE_KEYS", "STAGE_LABELS", "STAGE_SHORT", "TARGET_PREFIX", "OPERATOR_LABELS",
           "STRATUM_NAMES", "FAMILY_NAMES", "STRATIFIER_NAMES", "QUANTITY_NAMES", "stage_target",
           "stratum_name", "curve_strata", "strata_names", "geography_sentence",
           "family_name", "version_line", "snapshot", "stage_status"]
