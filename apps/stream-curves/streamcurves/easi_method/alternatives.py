"""The alternatives the EASI method was chosen from, as candidates in a project's register.

The 2026-09-15 controlled alternatives study froze four complete method data folders:
Alternative 1 (the regional method in use before, Level II references, 62 curves),
Alternative 2 (NARS-9 references, 34 curves), Alternative 3 (national references, 7) and
Alternative 4 (Alternative 1 with the woody Level II 8.2 curve replaced by the national one,
61). The study's own rule recommended retaining Alternative 1; the owner adopted
Alternative 2 on 2026-09-16 (``apps/easi/data/source/alternative-2-promotion.json``, commit
02f39a8). The method also keeps the historical baseline criteria
(``screening-methods-legacy.json``, ``EASI_CRITERIA_SET=legacy``), which the study treated as
a separate definition.

An alternative is a candidate for a function only where its definition differs from the
project's (``register.basis_digest``); Alternative 2 differs nowhere. Each candidate carries
its definition (the catalog entry and the curve sets it reads), so the register is complete
without the study folder, and the study is named by the SHA-256 of its receipts. Two
functions that read one curve family (the woody curves serve light and thermal regime and
habitat provision) are linked: adopting one adopts both, and the register records both.

Importing reads the study folder (a maintainer's machine); adopting and undoing work on the
project alone. Every string is user-visible, so none carries an em dash.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional

from . import edit as E
from . import register as R
from .model import EasiProject, parsed

STUDY_ID = "2026-09-15-controlled-alternatives"
STUDY_COMPLETION_SHA256 = "97a24b44e2314cbe5c001d99bb67ce71343ac99d432f27bec54948342b0fa7dd"
ADOPTED = "alternative-2"
ADOPTION = {"date": "2026-09-16", "commit": "02f39a8ccbe99de81d0aaa1a2da786b24669b555",
            "record": "apps/easi/data/source/alternative-2-promotion.json"}
#: the alternatives imported as candidates, in the study's order
IMPORTED = ("alternative-1", "alternative-3", "alternative-4")
LEGACY_FILE = "screening-methods-legacy.json"
CATALOG, CURVES = "screening-methods.json", "reference-curves.json"
#: where the study lives on a maintainer's machine (EASI_ALTERNATIVES_STUDY overrides)
STUDY_DIR = Path(os.environ.get("EASI_ALTERNATIVES_STUDY") or
                 r"D:\Data\easi-national\review\alternative-studies\2026-09-15-controlled-alternatives")
#: the legacy criteria are an evaluator asset, not a method file: StreamCurves reads them
#: from its vendored EASI
VENDORED_LEGACY = Path(__file__).resolve().parents[1] / "_vendor" / "easi" / "data" / LEGACY_FILE


class AlternativeError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _study_findings(report: str) -> dict:
    """``{alternative id: finding}`` from the study report's eligibility table."""
    out = {}
    for line in report.splitlines():
        m = re.match(r"^\|\s*(alternative-\d)\s*\|\s*(Yes|No)\s*\|\s*(.+?)\s*\|\s*$", line)
        if m:
            out[m.group(1)] = {"eligible": m.group(2) == "Yes", "finding": m.group(3)}
    return out


def read_study(study_dir: Path, *, completion_sha256: Optional[str] = STUDY_COMPLETION_SHA256) -> dict:
    """The study's receipts, verified: its completion record is the one the adopted method
    names (``completion_sha256``; None only for a test study), and every candidate's
    catalog and curves are the bytes the study recorded."""
    study_dir = Path(study_dir)
    completion = (study_dir / "completion.json").read_bytes()
    if completion_sha256 is not None and _sha(completion) != completion_sha256:
        raise AlternativeError("This is not the completed 2026-09-15 alternatives study the method "
                               "was chosen from (its completion record differs).")
    hashes = json.loads(completion.decode("utf-8")).get("output_hashes") or {}
    manifest = json.loads((study_dir / "candidates" / "manifest.json").read_text(encoding="utf-8"))
    report = (study_dir / "review-report.md").read_bytes()
    alternatives = {}
    for a in manifest.get("alternatives") or []:
        folder = study_dir / "candidates" / a["id"] / "app-data"
        files = {n: (folder / n).read_bytes() for n in (CATALOG, CURVES, "easi-metrics.json",
                                                         "cwa-mapping.json")}
        if _sha(files[CURVES]) != a["artifact_sha256"] or \
                hashes.get(f"candidates/{a['id']}/app-data/{CURVES}") != a["artifact_sha256"]:
            raise AlternativeError(f"{a['id']}: its curves are not the bytes the study recorded.")
        if _sha(files[CATALOG]) != a["catalog_sha256"]:
            raise AlternativeError(f"{a['id']}: its catalog is not the one the study recorded.")
        alternatives[a["id"]] = {"label": a["label"], "curveCount": a.get("curve_count"),
                                 "changes": a.get("changes"), "files": files,
                                 "catalogSha256": a["catalog_sha256"],
                                 "curvesSha256": a["artifact_sha256"]}
    findings = _study_findings(report.decode("utf-8"))
    recommendation = re.search(r"\*\*Recommendation:\s*(alternative-\d)", report.decode("utf-8"))
    return {"id": STUDY_ID, "completionSha256": _sha(completion),
            "manifestSha256": _sha((study_dir / "candidates" / "manifest.json").read_bytes()),
            "reportSha256": _sha(report), "alternatives": alternatives, "findings": findings,
            "recommendation": recommendation.group(1) if recommendation else None}


def _rows(catalog: dict, metrics: dict, cwa: list) -> dict:
    """``{function id: (method entry, metric meta, cwa row)}``."""
    mm = {m["metricId"]: m for m in metrics.get("metrics", [])}
    cw = {r["id"]: r for r in cwa}
    out = {}
    for m in catalog.get("methods", []):
        meta = mm.get(m["metricId"], {})
        fid = meta.get("functionId") or m["metricId"]
        out[fid] = (m, meta, cw.get(fid, {}))
    return out


def _project_rows(project: EasiProject) -> dict:
    return _rows(project.catalog(), project.metrics(), parsed(project.files["cwa-mapping.json"]))


def _definition(entry: dict, curves: dict) -> dict:
    sets = (curves or {}).get("sets") or {}
    return {"method": copy.deepcopy(entry),
            "curveSets": {n: copy.deepcopy(sets[n]) for n in R.curve_sets_used(entry) if n in sets}}


def _candidate(project: EasiProject, fid: str, entry: dict, curves: dict, meta: dict, cwa_row: dict,
               *, source_ref: dict, label: str) -> dict:
    identity = {"assessmentType": "easi", "subject": {"kind": "method", "id": entry["methodKey"]},
                "functionId": fid, "sourceKind": "imported_alternative", "sourceRef": source_ref,
                "applicability": {"geography": {"kind": "national", "code": "CONUS"},
                                  "strata": {"curveSets": R.curve_sets_used(entry)}}}
    return {"candidateKey": R.candidate_key(identity), "identity": identity,
            "basisDigest": R.basis_digest(entry, curves, meta, cwa_row),
            "methodVersion": None, "dataFingerprint": None, "purpose": "operational",
            "campaign": None, "buildStatus": "built", "supersededBy": None, "label": label,
            "eligibility": {"status": "eligible", "reasons": [], "checks": []},
            "limitations": list(entry.get("limitations") or []),
            "definition": _definition(entry, curves)}


def _reason(alt: str, study: dict) -> str:
    if alt == "alternative-1":
        rec = " The study's rule recommended retaining it." if study.get("recommendation") == alt else ""
        return ("The regional method in use before 2026-09-16 (Level II references). The owner "
                "adopted Alternative 2 (NARS-9 references) on 2026-09-16 in its place." + rec)
    found = (study.get("findings") or {}).get(alt) or {}
    finding = f" Study finding: {found['finding']}" if found.get("finding") else ""
    return ("Evaluated in the 2026-09-15 controlled alternatives study and not adopted; the owner "
            "adopted Alternative 2 on 2026-09-16." + finding)


LEGACY_REASON = ("The historical baseline criteria, which EASI still offers as its legacy criteria "
                 "set. The 2026-09-15 study treated them as a separate definition, not a control. "
                 "The decision that replaced them is not recorded here.")
STUDY_RULE = "study-2026-09-15"


def import_alternatives(project: EasiProject, study_dir: Path, *, imported_by: str,
                        at: Optional[str] = None, legacy_file: Optional[Path] = None,
                        completion_sha256: Optional[str] = STUDY_COMPLETION_SHA256) -> EasiProject:
    """The project with the study's alternatives (and the legacy criteria) in its register:
    one candidate per function whose definition differs from the project's, not selected,
    with the study's receipts and the owner's 2026-09-16 decision recorded as imported.
    Running it again changes nothing."""
    if not str(imported_by or "").strip():
        raise AlternativeError("An import needs the name of the person running it.")
    study = read_study(study_dir, completion_sha256=completion_sha256)
    at = at or E._now()
    new = project.copy()
    reg = new.register or {"schema": 1, "candidates": [], "decisions": []}
    have = {c["candidateKey"] for c in reg.get("candidates", [])}
    mine = _project_rows(project)
    current = {fid: R.basis_digest(m, project.curves(), meta, cwa) for fid, (m, meta, cwa) in mine.items()}
    added = []

    def offer(alt_id: str, rows: dict, curves: dict, source_ref: dict, label: str, reason: str,
              evidence: dict) -> None:
        group = []
        for fid, (entry, meta, cwa_row) in rows.items():
            if fid not in current:
                continue
            cand = _candidate(project, fid, entry, curves, meta, cwa_row, source_ref=source_ref,
                              label=f"{label}: {entry.get('title') or entry['methodKey']}")
            if cand["basisDigest"] == current[fid]:
                continue
            cand["evidence"] = evidence
            found = (study.get("findings") or {}).get(alt_id) or {}
            if found and found.get("eligible") is False:
                # the study's own simplification rule found it not eligible; the register says so
                cand["eligibility"] = {
                    "status": "excluded",
                    "reasons": [f"Not eligible under the 2026-09-15 study's rule: {found.get('finding')}"],
                    "checks": [{"id": STUDY_RULE, "status": "fail", "detail": found.get("finding")}]}
            group.append(cand)
        # functions that read one changed curve family move together
        for c in group:
            sets = set(c["definition"]["curveSets"])
            c["links"] = sorted(o["candidateKey"] for o in group
                                if o is not c and sets & set(o["definition"]["curveSets"]))
        for c in group:
            if c["candidateKey"] in have:
                continue
            reg.setdefault("candidates", []).append(c)
            excluded = (c.get("eligibility") or {}).get("status") == "excluded"
            reg.setdefault("decisions", []).append({
                "decisionId": f"dec-{c['candidateKey'][5:]}-import", "candidateKey": c["candidateKey"],
                "functionId": c["identity"]["functionId"], "decision": "not_selected",
                "rule": (STUDY_RULE if excluded else
                         "owner-adoption-2026-09-16" if alt_id != "legacy" else "historical-baseline"),
                "reason": reason, "decidedBy": "imported", "who": None,
                "when": ADOPTION["date"] if alt_id != "legacy" else None,
                "basisDigest": c["basisDigest"], "supersedes": None})
            added.append(c["candidateKey"])

    for alt_id in IMPORTED:
        a = study["alternatives"].get(alt_id)
        if a is None:
            raise AlternativeError(f"The study has no {alt_id}.")
        cat, curves = json.loads(a["files"][CATALOG]), json.loads(a["files"][CURVES])
        rows = _rows(cat, json.loads(a["files"]["easi-metrics.json"]), json.loads(a["files"]["cwa-mapping.json"]))
        offer(alt_id, rows, curves,
              {"study": STUDY_ID, "alternative": alt_id, "catalogSha256": a["catalogSha256"],
               "curvesSha256": a["curvesSha256"]},
              a["label"], _reason(alt_id, study),
              {"study": STUDY_ID, "completionSha256": study["completionSha256"],
               "reportSha256": study["reportSha256"], "curveCount": a["curveCount"],
               "changes": a["changes"]})
    legacy_path = Path(legacy_file) if legacy_file else VENDORED_LEGACY
    if legacy_path.is_file():
        raw = legacy_path.read_bytes()
        rows = _rows(json.loads(raw), project.metrics(), parsed(project.files["cwa-mapping.json"]))
        offer("legacy", rows, project.curves(),
              {"criteriaSet": "legacy", "file": LEGACY_FILE, "sha256": _sha(raw)},
              "Legacy criteria", LEGACY_REASON, {"file": LEGACY_FILE, "sha256": _sha(raw)})
    a2 = (study.get("findings") or {}).get(ADOPTED) or {}
    changed = {c["identity"]["functionId"] for c in reg.get("candidates", [])
               if (c["identity"].get("sourceRef") or {}).get("alternative") == "alternative-1"}
    if a2.get("finding"):
        note = (f"The 2026-09-15 study found Alternative 2 not eligible under its own rule: "
                f"{a2['finding']} The owner adopted it on {ADOPTION['date']}.")
        for c in reg.get("candidates", []):
            if (c["identity"].get("sourceRef") or {}).get("importedFrom") and \
                    c["identity"].get("functionId") in changed and note not in (c.get("limitations") or []):
                c["limitations"] = list(c.get("limitations") or []) + [note]
    studies = [s for s in reg.get("studies") or [] if s.get("id") != STUDY_ID]
    studies.append({"id": STUDY_ID, "completionSha256": study["completionSha256"],
                    "manifestSha256": study["manifestSha256"], "reportSha256": study["reportSha256"],
                    "recommendation": study["recommendation"], "adopted": ADOPTED,
                    "adoption": dict(ADOPTION),
                    "findings": {k: v["finding"] for k, v in study["findings"].items()},
                    "importedBy": str(imported_by).strip(), "importedAt": at})
    reg["studies"] = studies
    new.register = reg
    if added:
        new.history.append({"action": "import_alternatives", "at": at, "by": str(imported_by).strip(),
                            "kind": "register", "reason": f"{len(added)} alternatives from {STUDY_ID}",
                            "target": {"study": STUDY_ID}})
    return new


# --------------------------------------------------------------------------- #
# adopting an alternative, and going back
# --------------------------------------------------------------------------- #
def _cand(project: EasiProject, key: str) -> dict:
    for c in (project.register or {}).get("candidates", []):
        if c.get("candidateKey") == key:
            return c
    raise AlternativeError("That candidate is not in this project's register.")


def definition_of(project: EasiProject, cand: dict) -> dict:
    """The candidate's definition: carried by an alternative, or, for the method this
    project started from, read from the origin's files."""
    if cand.get("definition"):
        return cand["definition"]
    fid = cand["identity"]["functionId"]
    files = project.origin_files() if project.is_revision() else project.files
    cat, curves = parsed(files[CATALOG]), parsed(files[CURVES])
    rows = _rows(cat, parsed(files["easi-metrics.json"]), parsed(files["cwa-mapping.json"]))
    if fid not in rows:
        raise AlternativeError("The origin has no method for this function.")
    return _definition(rows[fid][0], curves)


def _source(cand: dict) -> str:
    ident = cand.get("identity") or {}
    return json.dumps([ident.get("sourceKind"), ident.get("sourceRef")], sort_keys=True)


def affected(project: EasiProject, key: str) -> list[dict]:
    """What adopting the candidate changes: ``[{functionId, candidateKey}]``, the function
    itself and every function that reads a curve family the adoption rewrites. Such a
    function moves to its candidate from the same source (the same alternative, or the
    method this draft started from) when there is one; otherwise it keeps its own entry and
    reads the new curves, and its selection is flagged for review."""
    cand = _cand(project, key)
    src = _source(cand)
    same = {c["identity"]["functionId"]: c["candidateKey"]
            for c in (project.register or {}).get("candidates", []) if _source(c) == src}
    sets_now = project.curves().get("sets") or {}
    reads = {fid: set(R.curve_sets_used(m)) for fid, (m, _, _) in _project_rows(project).items()}
    out = {cand["identity"]["functionId"]: key}
    changed: set = set()
    while True:
        for k in [k for k in out.values() if k]:
            changed |= {n for n, s in definition_of(project, _cand(project, k))["curveSets"].items()
                        if sets_now.get(n) != s}
        more = {fid: same.get(fid) for fid, sets in reads.items()
                if fid not in out and sets & changed}
        if not more:
            break
        out.update(more)
    return [{"functionId": f, "candidateKey": k} for f, k in out.items()]


def adopt(project: EasiProject, key: str, *, by: str, reason: str, at: Optional[str] = None,
          allow_excluded: bool = False) -> EasiProject:
    """Select a candidate for its function in this draft: its catalog entry and curve sets
    replace the current ones (with every linked function), and the register records who
    selected it, when and why, and that the one it replaces is no longer selected. Adopting
    the method the draft started from puts the origin bytes back."""
    who, why = str(by or "").strip(), " ".join(str(reason or "").split())
    if not who:
        raise AlternativeError("A selection needs the name of the person making it.")
    if len(why) < 20:
        raise AlternativeError("Say why, in at least 20 characters.")
    if not project.is_revision():
        raise AlternativeError("Only a draft revision can change its selection; start a revision "
                               "of this version first.")
    target = _cand(project, key)
    if (target.get("eligibility") or {}).get("status") == "excluded" and not allow_excluded:
        raise AlternativeError("This alternative is excluded: "
                               + " ".join((target.get("eligibility") or {}).get("reasons") or []))
    at = at or E._now()
    moves = affected(project, key)
    cat = copy.deepcopy(project.catalog())
    curves = copy.deepcopy(project.curves())
    by_key = {m["methodKey"]: i for i, m in enumerate(cat.get("methods", []))}
    fn_of = {fid: m["methodKey"] for fid, (m, _, _) in _project_rows(project).items()}
    for mv in moves:
        if mv["candidateKey"] is None:
            continue
        d = definition_of(project, _cand(project, mv["candidateKey"]))
        old_key = fn_of.get(mv["functionId"])
        if old_key is None or old_key not in by_key:
            raise AlternativeError("The draft has no method for that function.")
        cat["methods"][by_key[old_key]] = copy.deepcopy(d["method"])
        for n, s in d["curveSets"].items():
            curves.setdefault("sets", {})[n] = copy.deepcopy(s)
    new = project.copy()
    changed = []
    for name, obj in ((CATALOG, cat), (CURVES, curves)):
        blob = E.dump_like(project.files[name], obj)
        if blob != project.files[name]:
            new.set_file(name, blob)
            changed.append(name)
    if not changed:
        raise AlternativeError("That candidate is already what this draft uses.")
    new.calculator = None
    new.meta["calculatorFor"] = None
    E.restamp_identity(new)
    new.meta["updated"] = at
    reg = new.register
    rows = {r["functionId"]: r for r in R.status_rows(project)}
    for mv in moves:
        fid = mv["functionId"]
        prior = rows.get(fid) or {}
        target = mv["candidateKey"]
        if target is None:
            continue
        cand = _cand(new, target)
        basis = cand["basisDigest"] if cand.get("definition") else R.function_basis(new, fid)
        was = prior.get("selectedCandidate")
        from ..candidates import decision_id
        reg.setdefault("decisions", []).append({
            "decisionId": decision_id(reg.get("decisions", []), target, fid, "adopt", who, at, why),
            "candidateKey": target, "functionId": fid,
            "decision": "selected", "rule": "person", "reason": why, "decidedBy": "person",
            "who": who, "when": at, "basisDigest": basis, "supersedes": prior.get("decisionId")})
        if was and was != target:
            reg["decisions"].append({
                "decisionId": decision_id(reg["decisions"], was, fid, "replaced", who, at, why),
                "candidateKey": was, "functionId": fid,
                "decision": "not_selected", "rule": "person", "reason": why, "decidedBy": "person",
                "who": who, "when": at, "basisDigest": prior.get("basis"), "supersedes": None})
    new.history.append({"action": "adopt_candidate", "at": at, "by": who, "kind": "analytical",
                        "reason": why, "files": changed,
                        "target": {"candidateKey": key, "functions": [m["functionId"] for m in moves]}})
    return new


__all__ = ["STUDY_ID", "STUDY_COMPLETION_SHA256", "ADOPTION", "read_study", "import_alternatives",
           "definition_of", "affected", "adopt", "AlternativeError", "LEGACY_REASON"]
