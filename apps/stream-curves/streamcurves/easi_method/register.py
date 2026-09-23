"""The EASI candidate register: every method definition considered, and what was decided.

The register rides in the authoring project only; it never enters a method package.
A candidate is one definition for one function (a method entry of the catalog with
the curves it reads). A decision selects it, or leaves it eligible but not selected,
for that function; ``basisDigest`` is the analytical content the decision was made on,
so a later analytical edit flags the decision for review without erasing it.

Status vocabulary (shared with DEEP's register):
selected | eligible_not_selected | excluded | not_evaluated | failed | superseded.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

STATUSES = ("selected", "eligible_not_selected", "excluded", "not_evaluated", "failed", "superseded")
DECIDED_BY = ("automated", "imported", "person")

#: Catalog fields that change what a method computes. Everything else (titles, labels,
#: rationale, limitations, citations, plot hints) is display: editing it does not
#: invalidate a decision, although it still changes the method file bytes.
ANALYTICAL_METHOD_KEYS = ("operator", "inputs", "bands", "curve", "formula", "allowPartial",
                          "variants", "status", "lookup", "default", "cap")
ANALYTICAL_INPUT_KEYS = ("key", "bands", "curve", "regionalBands", "required", "contextOnly",
                         "positive", "valueType", "lookup", "default")
ANALYTICAL_BAND_KEYS = ("rating", "min", "max", "minInclusive", "maxInclusive")


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _strip(obj, keep):
    if isinstance(obj, dict):
        return {k: _strip_value(k, v) for k, v in obj.items() if k in keep}
    return obj


def _strip_value(key, value):
    if key == "inputs" and isinstance(value, list):
        return [{k: _strip_band_list(k, v) for k, v in i.items() if k in ANALYTICAL_INPUT_KEYS}
                for i in value if isinstance(i, dict)]
    if key == "variants" and isinstance(value, list):
        return [analytical_method(v) for v in value if isinstance(v, dict)]
    return _strip_band_list(key, value)


def _strip_band_list(key, value):
    if key == "bands" and isinstance(value, list):
        return [{k: b.get(k) for k in ANALYTICAL_BAND_KEYS} for b in value if isinstance(b, dict)]
    return value


def analytical_method(method: dict) -> dict:
    """The part of a catalog method entry that decides ratings."""
    return {k: _strip_value(k, method[k]) for k in ANALYTICAL_METHOD_KEYS if k in method}


def curve_sets_used(method: dict) -> list[str]:
    out = []

    def visit(rule):
        c = (rule or {}).get("curve")
        if isinstance(c, dict) and c.get("set"):
            out.append(c["set"])

    visit(method)
    for i in method.get("inputs") or []:
        visit(i)
    for v in method.get("variants") or []:
        out.extend(curve_sets_used(v))
    return sorted(set(out))


def basis_digest(method: dict, curves: dict, metric_meta: Optional[dict] = None,
                 cwa_row: Optional[dict] = None) -> str:
    """Analytical content of one function's method: the method entry's analytical
    fields, the points of every curve it reads, its index anchors and CWA weights."""
    sets = (curves or {}).get("sets") or {}
    used = {}
    for name in curve_sets_used(method):
        s = sets.get(name) or {}
        used[name] = {"stratifier": s.get("stratifier"), "higherIsBetter": s.get("higherIsBetter"),
                      "curves": {k: (v or {}).get("points") for k, v in (s.get("curves") or {}).items()}}
    doc = {"method": analytical_method(method), "curves": used,
           "anchors": (metric_meta or {}).get("indexMidpoints"),
           "cwa": {k: (cwa_row or {}).get(k) for k in ("physical", "chemical", "biological")}}
    return "sha256:" + hashlib.sha256(canonical(doc)).hexdigest()


def candidate_key(identity: dict) -> str:
    """What the candidate is (never its content): a refit or an edit keeps the key and its
    history, and the ``basisDigest`` beside it records the content."""
    return "cand-" + hashlib.sha256(canonical(identity)).hexdigest()[:12]


def _function_rows(project) -> list[tuple[str, dict, dict, dict]]:
    cat = project.catalog()
    metrics = {m["metricId"]: m for m in project.metrics().get("metrics", [])}
    cwa = {r["id"]: r for r in json.loads(project.files["cwa-mapping.json"].decode("utf-8"))}
    rows = []
    for m in cat.get("methods", []):
        meta = metrics.get(m["metricId"], {})
        fid = meta.get("functionId") or m["metricId"]
        rows.append((fid, m, meta, cwa.get(fid, {})))
    return rows


def imported_register(project, *, decided_at: str) -> dict:
    """The register at import: the operational method of each function, selected, with
    the decision recorded as imported (the history before import is not reconstructed)."""
    curves = project.curves()
    ident = project.identity()
    candidates, decisions = [], []
    for fid, method, meta, cwa_row in _function_rows(project):
        identity = {"assessmentType": "easi", "subject": {"kind": "method", "id": method["methodKey"]},
                    "functionId": fid, "sourceKind": "imported_alternative",
                    "sourceRef": {"importedFrom": "apps/easi/data"},
                    "applicability": {"geography": {"kind": "national", "code": "CONUS"},
                                      "strata": {"curveSets": curve_sets_used(method)}}}
        cid = candidate_key(identity)
        basis = basis_digest(method, curves, meta, cwa_row)
        candidates.append({"candidateKey": cid, "identity": identity, "basisDigest": basis,
                           "methodVersion": ident["methodVersion"],
                           "dataFingerprint": ident["packageDigest"],
                           "purpose": "operational", "campaign": None,
                           "buildStatus": "built", "supersededBy": None,
                           "label": method.get("title") or method["methodKey"],
                           "eligibility": {"status": "eligible", "reasons": [], "checks": []},
                           "limitations": list(method.get("limitations") or [])})
        decisions.append({"decisionId": f"dec-{cid[5:]}-import", "candidateKey": cid, "functionId": fid,
                          "decision": "selected", "rule": "operational-at-import",
                          "reason": "The operational EASI method when it was imported "
                                    "(Alternative 2: NARS-9 references, adopted by the owner "
                                    "2026-09-16). Earlier alternatives and their decisions are "
                                    "recorded only where their study records exist.",
                          "decidedBy": "imported", "who": None, "when": decided_at,
                          "basisDigest": basis, "supersedes": None})
    return {"schema": 1, "candidates": candidates, "decisions": decisions,
            "scope": "the operational method of each function at import"}


def status_rows(project) -> list[dict]:
    """One row per function: its selected candidate, alternatives and review flags."""
    curves = project.curves()
    current = {fid: basis_digest(m, curves, meta, cwa) for fid, m, meta, cwa in _function_rows(project)}
    by_fn: dict[str, list] = {}
    cands = {c["candidateKey"]: c for c in (project.register or {}).get("candidates", [])}
    for d in (project.register or {}).get("decisions", []):
        by_fn.setdefault(d["functionId"], []).append(d)
    out = []
    for fid, method, meta, _ in _function_rows(project):
        decs = by_fn.get(fid, [])
        selected = [d for d in decs if d.get("decision") == "selected"]
        latest = selected[-1] if selected else None
        needs_review = bool(latest and latest.get("basisDigest") != current[fid])
        out.append({"functionId": fid, "functionName": meta.get("functionName") or fid,
                    "method": method.get("title") or method["methodKey"],
                    "methodKey": method["methodKey"],
                    "selectedCandidate": latest and latest["candidateKey"],
                    "decidedBy": latest and latest.get("decidedBy"),
                    "needsReview": needs_review,
                    "alternatives": sum(1 for d in decs if d.get("decision") != "selected"),
                    "candidates": [cands[d["candidateKey"]] for d in decs if d["candidateKey"] in cands]})
    return out
