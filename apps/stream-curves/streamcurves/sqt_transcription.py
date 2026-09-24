"""A published state SQT assessment as a complete StreamCurves session.

The eight ``*-sqt-adapted`` library assessments were migrated on 2026-07-12 from the STAF metric
library's detailed adapted assessments (``scripts/migrate_sqts_to_library.py``) with a DEEP
bundle and a stub session, so StreamCurves opened each as an empty project. Their bundles already
hold what the owner asked for on 2026-09-24: every metric of the state's SQT under the function
the metric library gives it, with every curve and stratum as the SQT publishes it. This module
writes the session such a version should have had:

- the state as the region of applicability;
- the function mapping the bundle holds, confirmed;
- every curve and stratum carried from v1 with its State SQT lineage: a published criterion on
  the SQT's own bands (never rescaled), the SQT registry record of each layer with its
  verification, and each known defect or open end as a caveat;
- the documented gaps and the portfolio approvals today's publish gates ask for.

The owner's decisions of 2026-09-24: a completed version scores exactly as v1 (its bundle is
v1's scored content, so DEEP's digest does not change); defects are flagged here and never
fixed; the SQT's own metric set per function is approved as published; DEEP keeps hiding the
state SQT assessments. Pure: nothing here reads or writes the assessment library.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable, Optional

from . import curve_basis
from . import deep_export as dx
from . import library as lib
from . import pressure_evidence as pe

APP_ROOT = Path(__file__).resolve().parents[1]
REPO = APP_ROOT.parent.parent
METRIC_LIBRARY = REPO / "docs" / "assets" / "data" / "metric-library"
ADAPTED = METRIC_LIBRARY / "detailed-adapted-assessments.json"
REGISTRY = APP_ROOT / "data" / "sqt" / "registry.json"
FUNCTIONS = APP_ROOT / "config" / "staf_functions.json"

#: the id suffix of a transcribed state SQT assessment (DEEP hides them by it)
SUFFIX = "-sqt-adapted"
#: who made v1 and when (the stub's own record)
MIGRATION = {"by": "STAF SQT migration", "on": "2026-07-12",
             "source": "docs/assets/data/metric-library/detailed-adapted-assessments.json"}
SOURCE_LABEL = "State SQT"
BASIS_LIMIT = ("A state SQT criterion transcribed as the tool publishes it: not a reference expectation "
               "for this state's streams. The SQT's bands (0.30, 0.70) differ from DEEP's (0.39, 0.69), "
               "and the index is not rescaled.")


def is_transcribed(assessment_id: str) -> bool:
    return str(assessment_id or "").endswith(SUFFIX)


def sha256_of(path: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# the curves
# --------------------------------------------------------------------------- #
def _points(raw) -> list[dict]:
    return [{"x": float(p["x"]), "y": float(p["y"])} for p in raw or []]


def _layers(entry: dict) -> list[dict]:
    """``[{stratum, points}]``: a stratified entry's layers, else its one curve."""
    if entry.get("curveLayers"):
        return [{"stratum": str(L.get("stratum") or ""), "points": _points(L.get("points"))}
                for L in entry["curveLayers"]]
    curve = entry.get("curve") or {}
    return [{"stratum": str(curve.get("stratification") or ""), "points": _points(curve.get("points"))}]


def _higher_is_better(points: list[dict]) -> Optional[bool]:
    ys = [p["y"] for p in sorted(points, key=lambda p: p["x"])]
    if len(ys) < 2 or ys[-1] == ys[0]:
        return None
    return ys[-1] > ys[0]


def _fmt(v: float) -> str:
    return f"{v:g}"


def open_ends(points: list[dict]) -> list[str]:
    """A sentence per end where the published curve stops inside the index range; DEEP holds
    that end's index past it."""
    pts = sorted(points, key=lambda p: p["x"])
    out = []
    if len(pts) < 2:
        return out
    for side, p in (("low", pts[0]), ("high", pts[-1])):
        if 0.0 < p["y"] < 1.0:
            out.append(f"The published curve stops at its {side} end, {_fmt(p['x'])} (index "
                       f"{_fmt(p['y'])}); past it DEEP holds that index.")
    return out


def registry_by_adaptation(records: Iterable[dict]) -> dict:
    """``{(assessment id, metric id, stratum): (registry record, its adaptedIn entry)}``: the
    layer of a v1 bundle each record was compared with."""
    out = {}
    for r in records or []:
        for a in r.get("adaptedIn") or []:
            if int(a.get("version") or 1) == 1:
                key = (str(a.get("assessmentId")), str(a.get("metricId")), str(a.get("stratum") or ""))
                out[key] = (r, a)
    return out


def _defects(record: Optional[dict]) -> list[str]:
    return [str(i.get("detail") or i.get("code")) for i in (record or {}).get("issues") or []
            if isinstance(i, dict) and str(i.get("severity") or "") == "defect"]


def layer_record(assessment_id: str, metric_id: str, layer: dict, by_adaptation: dict) -> dict:
    rec, adaptation = by_adaptation.get((assessment_id, metric_id, layer["stratum"])) or (None, None)
    return {"stratum": layer["stratum"],
            "registryKey": (rec or {}).get("key"),
            "verification": ((rec or {}).get("verification") or {}).get("status"),
            "defects": _defects(rec),
            "differences": list((adaptation or {}).get("differences") or []),
            "openEnds": open_ends(layer["points"])}


def _caveats(layers: list[dict]) -> list[str]:
    out: list[str] = []
    for L in layers:
        where = f"{L['stratum']}: " if L["stratum"] else ""
        for d in L["defects"]:
            text = (f"The metric library row this curve was transcribed from is defective: {d.rstrip('.')}. "
                    "It scores as v1 scores it; the assessment audit decides the fix.")
            text = where + text[:1].lower() + text[1:] if where else text
            if text not in out:
                out.append(text)
        for e in L["openEnds"]:
            text = where + e[:1].lower() + e[1:] if where else e
            if text not in out:
                out.append(text)
    return out


def tool_name(state_code: str, state_name: str, records: Iterable[dict]) -> str:
    for r in records or []:
        if str(r.get("state") or "") == str(state_code) and r.get("tool"):
            return str(r["tool"])
    return f"{state_name} Stream Quantification Tool"


def carried_metrics(assessment_id: str, bundle: dict, records: Iterable[dict], *,
                    tool: str) -> tuple[dict, list[str]]:
    """``(carriedMetrics, order)``: every v1 metric as a curve carried from v1 (the session
    form ``carry_forward.session_rows`` writes), in the bundle's order, each with its State SQT
    lineage and the verbatim v1 entry it was carried from (``transcribed``)."""
    fns = {str(f.get("id")): f for f in dx.deep_read_staf_crosswalk()}
    by_adaptation = registry_by_adaptation(records)
    digest = bundle.get("contentDigest")
    out: dict[str, dict] = {}
    order: list[str] = []
    for block in bundle.get("metricsByFunction") or []:
        fid = str(block.get("functionId"))
        f = fns.get(fid) or {}
        for m in block.get("metrics") or []:
            mid = str(m["metricId"])
            row = {"metric_key": mid, "discipline": f.get("category"), "function_label": f.get("name")}
            if mid in out:
                out[mid]["mapping"].append(row)
                out[mid]["transcribed"]["functions"].append(fid)
                continue
            order.append(mid)
            curve = m.get("curve") or {}
            layers = _layers(m)
            records_here = [layer_record(assessment_id, mid, L, by_adaptation) for L in layers]
            config = {"display_name": m.get("metricName") or mid, "units": "",
                      "metric_family": m.get("inputType") or "", "notes": m.get("howToMeasure") or "",
                      "column_name": mid}
            hib = _higher_is_better(_points(curve.get("points")))
            if hib is not None:
                config["higher_is_better"] = hib
            annotations = {
                "basis": curve_basis.PUBLISHED, "basisLabel": SOURCE_LABEL,
                "basisStatement": curve_basis.statement_for(curve_basis.PUBLISHED),
                "basisLimit": BASIS_LIMIT,
                "criteriaSource": {"title": tool,
                                   "citations": [{"text": str(m.get("sourceCitation") or tool)}]},
                "sourceCitation": m.get("sourceCitation"),
                "methodContext": m.get("methodContext"),
                "curveCaveats": _caveats(records_here),
                "carriedForward": {"assessmentId": assessment_id, "fromVersion": 1,
                                   "contentDigest": digest},
                "sqtTranscription": {"tool": tool, "fromVersion": 1, "migratedBy": MIGRATION["by"],
                                     "migratedOn": MIGRATION["on"], "layers": records_here},
            }
            out[mid] = {
                "displayName": m.get("metricName") or mid, "curveStatus": "complete",
                "nReference": None, "stratum": str(curve.get("stratification") or ""),
                "points": _points(curve.get("points")),
                "layers": layers if m.get("curveLayers") else [],
                "config": config, "annotations": annotations, "mapping": [row],
                "decision": {"metric": mid, "carried_from": 1},
                "transcribed": {"bundleEntry": copy.deepcopy(m), "functions": [fid]},
            }
    return out, order


# --------------------------------------------------------------------------- #
# gaps, approvals, the bundle and the record
# --------------------------------------------------------------------------- #
def _norm(s) -> str:
    s = str(s or "").strip().lower().replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def function_ids_by_name() -> dict:
    cfg = json.loads(FUNCTIONS.read_text(encoding="utf-8"))
    out = {}
    for f in cfg.get("functions") or []:
        for k in [f.get("name")] + list(f.get("aliases") or []):
            if k:
                out[_norm(k)] = str(f["id"])
    return out


def screening_functions(citation: str, bundle: dict, *, library_dir: Path = METRIC_LIBRARY) -> dict:
    """``{function id: [metric name, ...]}``: the metric library's metrics that cite the state's
    SQT (``citation``, e.g. "Minnesota SQT") and are not in the bundle (screening tier:
    categorical, no curve), by the function the metric library gives them."""
    index = json.loads((library_dir / "index.json").read_text(encoding="utf-8"))["metrics"]
    in_bundle = {str(m["metricId"]) for b in bundle.get("metricsByFunction") or []
                 for m in b.get("metrics") or []}
    curve_cites: dict = {}
    for cp in sorted((library_dir / "curves").glob("*.json")):
        doc = json.loads(cp.read_text(encoding="utf-8"))
        cites = {str((L.get("sourceMetadata") or {}).get("sourceCitation") or "")
                 for c in doc.get("curves") or [] for L in c.get("layers") or []}
        curve_cites.setdefault(str(doc.get("metricId")), set()).update(cites)
    names = function_ids_by_name()
    out: dict = {}
    for m in index:
        mid = str(m["metricId"])
        if mid in in_bundle:
            continue
        det_path = library_dir / "metrics" / f"{mid}.json"
        det = json.loads(det_path.read_text(encoding="utf-8")) if det_path.is_file() else {}
        cites = (set((det.get("sourceMetadata") or {}).get("sourceCitations") or [])
                 | set(det.get("references") or []) | curve_cites.get(mid, set()))
        if citation not in cites:
            continue
        fid = names.get(_norm(m.get("function")))
        if fid:
            out.setdefault(fid, []).append(str(m.get("name") or mid))
    return {k: sorted(set(v)) for k, v in out.items()}


def gap_exceptions(bundle: dict, *, tool: str, screening: dict, by: str, at: str) -> list[dict]:
    """A documented reason for every STAF function the SQT publishes no curve for: deferred to
    the screening tier where the SQT has a screening-tier metric for it, else no suitable metric."""
    covered = {str(b.get("functionId")) for b in bundle.get("metricsByFunction") or [] if b.get("metrics")}
    out = []
    for f in dx.deep_read_staf_crosswalk():
        fid = str(f.get("id"))
        if fid in covered:
            continue
        names = screening.get(fid) or []
        if names:
            reason = "deferred-to-other-tier"
            why = (f"The {tool} addresses this function only with a screening-tier metric "
                   f"({'; '.join(names)}), which has no curve. This assessment transcribes the "
                   "tool's curves, so the function is left to the screening tier.")
        else:
            reason = "no-suitable-metric"
            why = (f"The {tool} publishes no curve for this function, and this assessment "
                   "transcribes that tool's curves as published.")
        out.append({"functionId": fid, "reason": reason, "justification": why,
                    "recordedBy": by, "recordedAt": at})
    return out


def portfolio_approvals(bundle: dict, *, tool: str, by: str) -> list[dict]:
    """SELECT-01: the SQT's own metric set for each function over the portfolio limit,
    approved as published (the owner's decision of 2026-09-24)."""
    return [{"functionId": fid, "approvedBy": by,
             "note": (f"The {tool}'s own published metric set for this function ({n} metrics), "
                      "transcribed unchanged.")}
            for fid, n in lib.functions_over_metric_limit(bundle)]


def v2_bundle(bundle: dict, exceptions: list[dict]) -> dict:
    """v1's scored content verbatim, with the coverage block today's gate reads (the content
    digest covers ``metricsByFunction`` and ``regionCode`` only, so it does not move)."""
    out = copy.deepcopy(bundle)
    out.pop("library", None)            # publish_version stamps the new version's own
    out["functionCoverage"] = dx.function_coverage(out.get("metricsByFunction"),
                                                   dx.deep_read_staf_crosswalk(), exceptions)
    return out


def session_fields(assessment_id: str, bundle: dict, *, carried: dict, order: list[str],
                   tool: str, exceptions: list[dict]) -> dict:
    """The session fields of the completed version (``session_io`` names): the state, the
    confirmed mapping, the carried curves and the documented gaps."""
    import pandas as pd
    rows = [r for mk in order for r in carried[mk]["mapping"]]
    mapping = pd.DataFrame(rows, columns=["metric_key", "discipline", "function_label"])
    mapping["sort_order"] = range(1, len(mapping) + 1)
    build = {"method": pe.METHOD, "referenceMethod": None, "insufficientReferenceSupport": [],
             "metricAnnotations": {}, "fixedMetrics": [], "ladderMetrics": {}, "referenceTier": None,
             "carriedMetrics": carried,
             "carriedFrom": {"assessmentId": assessment_id, "fromVersion": 1,
                             "contentDigest": bundle.get("contentDigest")},
             "transcription": {"kind": "state-sqt", "tool": tool, **MIGRATION}}
    return {"region_of_applicability": {"kind": "state", "code": bundle.get("stateCode"),
                                        "name": bundle.get("stateName")},
            "reference_build": build,
            "discipline_function_mapping": mapping,
            "discipline_function_mapping_confirmed": True,
            "function_coverage_exceptions": exceptions,
            "metric_config": {}}


def provenance(assessment_id: str, bundle: dict, *, carried: dict, order: list[str], tool: str,
               exceptions: list[dict], approvals: list[dict], by: str, at: str) -> dict:
    curves = [{"metricId": mk, "functions": list(carried[mk]["transcribed"]["functions"]),
               "layers": carried[mk]["annotations"]["sqtTranscription"]["layers"]} for mk in order]
    return {
        "kind": "sqt-transcription", "schemaVersion": 1, "assessmentId": assessment_id,
        "tool": tool, "publishedBy": by, "publishedAt": at,
        "transcribedFrom": {"version": 1, "contentDigest": bundle.get("contentDigest"), **MIGRATION},
        "ownerDecisions": [
            "2026-09-24: complete the state SQT assessments as StreamCurves sessions, the metrics "
            "under the functions the metric library gives them (approach a).",
            "2026-09-24: score exactly as v1; defective and open-ended layers are flagged as caveats, "
            "not fixed.",
            "2026-09-24: approve each function's SQT metric set as published (SELECT-01).",
            "2026-09-24: Preliminary; DEEP keeps hiding the state SQT assessments.",
        ],
        "sources": {"metricLibrary": {"path": MIGRATION["source"], "sha256": sha256_of(ADAPTED)},
                    "sqtRegistry": {"path": "apps/stream-curves/data/sqt/registry.json",
                                    "sha256": sha256_of(REGISTRY)}},
        "curves": curves,
        "defects": [{"metricId": c["metricId"], "stratum": L["stratum"], "registryKey": L["registryKey"],
                     "defects": L["defects"]} for c in curves for L in c["layers"] if L["defects"]],
        "openEnds": [{"metricId": c["metricId"], "stratum": L["stratum"], "sentences": L["openEnds"]}
                     for c in curves for L in c["layers"] if L["openEnds"]],
        "coverageExceptions": exceptions,
        "portfolioApprovals": approvals,
    }


def complete(assessment_id: str, bundle: dict, *, records: list[dict], citation: str, by: str,
             at: str, library_dir: Path = METRIC_LIBRARY) -> dict:
    """Everything a completed version needs: ``{fields, bundle, provenance, approvals, tool}``."""
    tool = tool_name(bundle.get("stateCode"), bundle.get("stateName"), records)
    carried, order = carried_metrics(assessment_id, bundle, records, tool=tool)
    exceptions = gap_exceptions(bundle, tool=tool,
                                screening=screening_functions(citation, bundle, library_dir=library_dir),
                                by=by, at=at)
    approvals = portfolio_approvals(bundle, tool=tool, by=by)
    return {"tool": tool,
            "fields": session_fields(assessment_id, bundle, carried=carried, order=order, tool=tool,
                                     exceptions=exceptions),
            "bundle": v2_bundle(bundle, exceptions),
            "provenance": provenance(assessment_id, bundle, carried=carried, order=order, tool=tool,
                                     exceptions=exceptions, approvals=approvals, by=by, at=at),
            "approvals": approvals}


__all__ = ["SUFFIX", "MIGRATION", "SOURCE_LABEL", "BASIS_LIMIT", "is_transcribed", "open_ends",
           "registry_by_adaptation", "layer_record", "tool_name", "carried_metrics",
           "screening_functions", "gap_exceptions", "portfolio_approvals", "v2_bundle",
           "session_fields", "provenance", "complete"]
