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
    from .model import parsed
    cwa = {r["id"]: r for r in parsed(project.files["cwa-mapping.json"])}
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


def function_basis(project, function_id: str) -> str:
    """The current analytical content of one function's method."""
    curves = project.curves()
    for fid, m, meta, cwa in _function_rows(project):
        if fid == function_id:
            return basis_digest(m, curves, meta, cwa)
    raise ValueError(f"no function {function_id!r} in this method")


def _status_of(decision: Optional[dict], candidate: dict) -> str:
    """A candidate's status for a function from its latest decision there."""
    if candidate.get("supersededBy"):
        return "superseded"
    if candidate.get("buildStatus") == "failed":
        return "failed"
    if (candidate.get("eligibility") or {}).get("status") == "excluded":
        return "excluded"
    if decision is None:
        return "not_evaluated"
    return "selected" if decision.get("decision") == "selected" else "eligible_not_selected"


def status_rows(project) -> list[dict]:
    """One row per function: its selected candidate, every candidate considered with its
    status (the shared vocabulary of ``streamcurves.candidates``) and review flags."""
    curves = project.curves()
    current = {fid: basis_digest(m, curves, meta, cwa) for fid, m, meta, cwa in _function_rows(project)}
    cands = {c["candidateKey"]: c for c in (project.register or {}).get("candidates", [])}
    by_fn: dict[str, list] = {}
    for d in (project.register or {}).get("decisions", []):
        by_fn.setdefault(d["functionId"], []).append(d)
    out = []
    for fid, method, meta, _ in _function_rows(project):
        decs = by_fn.get(fid, [])
        latest: dict[str, dict] = {}
        for d in decs:
            latest[d["candidateKey"]] = d
        keys = list(dict.fromkeys([d["candidateKey"] for d in decs] +
                                  [k for k, c in cands.items()
                                   if (c.get("identity") or {}).get("functionId") == fid]))
        rows = []
        for k in keys:
            c = cands.get(k)
            if c is None:
                continue
            d = latest.get(k)
            rows.append({"candidateKey": k, "candidate": c, "decision": d,
                         "status": _status_of(d, c)})
        chosen = [r for r in rows if r["status"] == "selected"]
        # the most recent selection wins if two ever read as selected
        chosen.sort(key=lambda r: decs.index(r["decision"]))
        sel = chosen[-1] if chosen else None
        dec = sel["decision"] if sel else None
        needs_review = bool(dec and dec.get("basisDigest") != current[fid])
        out.append({"functionId": fid, "functionName": meta.get("functionName") or fid,
                    "method": method.get("title") or method["methodKey"],
                    "methodKey": method["methodKey"],
                    "selectedCandidate": sel and sel["candidateKey"],
                    "decisionId": dec and dec.get("decisionId"),
                    "basis": current[fid],
                    "decidedBy": dec and dec.get("decidedBy"),
                    "who": dec and dec.get("who"),
                    "needsReview": needs_review,
                    "alternatives": sum(1 for r in rows if r["status"] != "selected"),
                    "rows": rows,
                    "candidates": [r["candidate"] for r in rows]})
    return out


def confirm_selection(project, function_id: str, *, by: str, reason: str, at: str):
    """A person confirms, after an analytical change, that the function keeps its selected
    method as it now stands. Returns a new project whose register records the decision
    (``decidedBy: person``, the current ``basisDigest``, the decision it supersedes) and
    whose selected candidate carries the current content; earlier decisions stay."""
    reason = str(reason or "").strip()
    who = str(by or "").strip()
    if not reason:
        raise ValueError("say why the selection stands (a short reason is recorded)")
    if not who:
        raise ValueError("a confirmation needs the name of the person making it")
    rows = {r["functionId"]: r for r in status_rows(project)}
    row = rows.get(function_id)
    if row is None:
        raise ValueError(f"no function {function_id!r} in this method")
    if not row["selectedCandidate"]:
        raise ValueError(f"{row['functionName']} has no selected method to confirm")
    curves = project.curves()
    current = next(basis_digest(m, curves, meta, cwa) for fid, m, meta, cwa in _function_rows(project)
                   if fid == function_id)
    new = project.copy()
    reg = new.register
    prior = [d for d in reg.get("decisions", []) if d.get("functionId") == function_id
             and d.get("decision") == "selected"]
    ident = project.identity()
    cid = row["selectedCandidate"]
    for c in reg.get("candidates", []):
        if c.get("candidateKey") == cid:
            c["basisDigest"] = current
            c["methodVersion"] = ident["methodVersion"]
            c["dataFingerprint"] = ident["packageDigest"]
    n = sum(1 for d in reg.get("decisions", []) if d.get("candidateKey") == cid)
    reg.setdefault("decisions", []).append({
        "decisionId": f"dec-{cid[5:]}-{n + 1}", "candidateKey": cid, "functionId": function_id,
        "decision": "selected", "rule": "confirmed-after-change", "reason": reason,
        "decidedBy": "person", "who": who, "when": at, "basisDigest": current,
        "supersedes": prior[-1]["decisionId"] if prior else None})
    new.history.append({"action": "confirm_selection", "at": at, "by": who, "kind": "decision",
                        "reason": reason, "target": {"functionId": function_id,
                                                     "candidateKey": cid}})
    return new


def needs_review(project) -> list[dict]:
    """The functions whose selected method changed after it was decided."""
    return [r for r in status_rows(project) if r["needsReview"]]


# --------------------------------------------------------------------------- #
# a published state SQT curve considered for an EASI function
# --------------------------------------------------------------------------- #
FIELD_VS_DESKTOP = ("Field protocol against desktop estimate: the SQT curve scores a value measured "
                    "in the field by the SQT's protocol, and EASI estimates this function from desktop "
                    "data. It is kept for comparison and is never substituted without a reviewed "
                    "method change.")


def add_sqt_candidate(project, record: dict, function_id: str, *, by: str, at: str,
                      reason: str = FIELD_VS_DESKTOP):
    """A new project whose register holds a state SQT curve considered for an EASI function,
    with its disposition: an SQT curve is a field measurement, an EASI proxy a desktop
    estimate, so the candidate is excluded for that reason and the person who added it is
    recorded. The method files never change."""
    from .. import sqt_registry as sqt
    from ..candidates import curve_basis_digest
    who = str(by or "").strip()
    if not who:
        raise ValueError("adding a candidate needs the name of the person adding it")
    fids = {r["functionId"] for r in status_rows(project)}
    if function_id not in fids:
        raise ValueError(f"no function {function_id!r} in this method")
    frozen = sqt.frozen_copy(record)
    identity = {"assessmentType": "easi", "subject": {"kind": "sqt-metric", "id": frozen.get("key")},
                "functionId": function_id, "sourceKind": "sqt",
                "sourceRef": {"registryKey": frozen.get("key"), "state": frozen.get("state"),
                              "edition": frozen.get("edition"),
                              "fingerprint": frozen["frozen"]["contentFingerprint"]},
                "applicability": {"geography": {"kind": "state", "code": frozen.get("state")}}}
    pts = [(float(p["x"]), float(p["y"])) for p in frozen.get("normalizedPoints") or []]
    key = candidate_key(identity)
    new = project.copy()
    reg = new.register
    if any(c.get("candidateKey") == key for c in reg.get("candidates", [])):
        raise ValueError("that SQT curve is already considered for this function")
    reg.setdefault("candidates", []).append({
        "candidateKey": key, "identity": identity,
        "basisDigest": curve_basis_digest([{"label": frozen.get("stratumName"), "points": pts}]),
        "methodVersion": None, "dataFingerprint": None, "purpose": "operational", "campaign": None,
        "buildStatus": "built", "supersededBy": None,
        "label": f"{frozen.get('originalMetricName')} ({frozen.get('state')} SQT, {frozen.get('stratumName')})",
        "eligibility": {"status": "excluded", "reasons": [reason], "checks": []},
        "limitations": [str((frozen.get("verification") or {}).get("status") or "")],
        "definition": {"points": [{"x": x, "y": y} for x, y in pts], "units": frozen.get("units"),
                       "direction": frozen.get("direction")},
        "record": frozen, "addedBy": who, "addedAt": at})
    reg.setdefault("decisions", []).append({
        "decisionId": f"dec-{key[5:]}-1", "candidateKey": key, "functionId": function_id,
        "decision": "not_selected", "rule": "field-vs-desktop", "reason": reason,
        "decidedBy": "person", "who": who, "when": at, "basisDigest": None, "supersedes": None})
    new.history.append({"action": "add_sqt_candidate", "at": at, "by": who, "kind": "register",
                        "reason": reason, "target": {"functionId": function_id, "candidateKey": key}})
    return new


def export_rows(project) -> list[dict]:
    """One flat record per candidate and function (the shape of
    ``streamcurves.candidates.export_rows``): what it is, its status, the decision, who and
    why. No method content."""
    out = []
    for r in status_rows(project):
        for x in r["rows"]:
            c, d = x["candidate"], x["decision"] or {}
            ident = c.get("identity") or {}
            out.append({"functionId": r["functionId"], "function": r["functionName"],
                        "candidateKey": x["candidateKey"], "candidate": c.get("label"),
                        "subject": (ident.get("subject") or {}).get("id"),
                        "sourceKind": ident.get("sourceKind"), "status": x["status"],
                        "rule": d.get("rule"), "decidedBy": d.get("decidedBy"), "who": d.get("who"),
                        "when": d.get("when"), "reason": d.get("reason"),
                        "basisDigest": c.get("basisDigest"),
                        "needsReview": bool(x["status"] == "selected" and r["needsReview"])})
    return sorted(out, key=lambda x: (x["functionId"], x["candidateKey"]))
