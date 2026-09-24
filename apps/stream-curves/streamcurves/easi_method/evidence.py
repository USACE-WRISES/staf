"""The development data an EASI project names: references to evidence packages.

A project records which packages its curves were developed from (``project.evidence``),
never their bytes: a reference names the package, its version, its data digest (the
identity), its roles and reproducibility, what it covers and, when known, the archive a copy
can be fetched as. The packages themselves live in the evidence store
(``streamcurves.evidence_store``). Attaching or removing a reference changes the authoring
record only: the method files, and so the method EASI scores with, never change.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from .model import EasiProject

ROLE_LABELS = {"development": "Development", "evaluation": "Evaluation", "operational": "Operational"}
REPRODUCIBILITY_LABELS = {"reviewable": "Reviewable", "refittable": "Refittable",
                          "regenerable": "Regenerable"}
REPRODUCIBILITY_HELP = {
    "reviewable": "the values behind the curves can be inspected",
    "refittable": "the curves can be refit from this package",
    "regenerable": "the package itself can be rebuilt from its original sources",
}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def reference(manifest: dict, *, archive: Optional[dict] = None, package_digest: Optional[str] = None) -> dict:
    """A project's reference to the package ``manifest`` describes."""
    return {"packageId": manifest["packageId"], "version": manifest["version"],
            "dataDigest": manifest["dataDigest"],
            **({"packageDigest": package_digest} if package_digest else {}),
            "title": manifest.get("title") or manifest["packageId"],
            "roles": list(manifest.get("roles") or []),
            "reproducibility": manifest.get("reproducibility"),
            "bytes": sum(int(r.get("bytes") or 0) for r in (manifest.get("files") or {}).values()),
            "coverage": manifest.get("coverage") or {},
            "dependsOn": manifest.get("dependsOn") or [],
            **({"archive": archive} if archive else {})}


def attach(project: EasiProject, ref: dict, *, by: str, reason: str = "") -> EasiProject:
    """A new project naming ``ref`` (replacing an earlier reference to the same package)."""
    new = project.copy()
    kept = [e for e in new.evidence if e.get("packageId") != ref["packageId"]]
    before = next((e for e in new.evidence if e.get("packageId") == ref["packageId"]), None)
    if before == ref:
        return project
    new.evidence = kept + [dict(ref)]
    new.evidence.sort(key=lambda e: e.get("packageId") or "")
    new.history.append({"action": "attach_evidence", "at": _now(), "by": by, "kind": "evidence",
                        "reason": reason, "target": {"packageId": ref["packageId"]},
                        "before": before and before.get("dataDigest"), "after": ref["dataDigest"]})
    return new


def detach(project: EasiProject, package_id: str, *, by: str, reason: str = "") -> EasiProject:
    before = next((e for e in project.evidence if e.get("packageId") == package_id), None)
    if before is None:
        return project
    new = project.copy()
    new.evidence = [e for e in new.evidence if e.get("packageId") != package_id]
    new.history.append({"action": "detach_evidence", "at": _now(), "by": by, "kind": "evidence",
                        "reason": reason, "target": {"packageId": package_id},
                        "before": before.get("dataDigest"), "after": None})
    return new


def status(ref: dict, installed: list[dict]) -> str:
    """``installed`` (a verified copy of the package the reference names: its package digest
    when it records one, else its data digest), ``damaged`` (that copy failed its check),
    ``other`` (the package is here in another version) or ``missing``."""
    from ..evidence_store import matches
    same = [r for r in installed if r["packageId"] == ref.get("packageId")]
    mine = [r for r in same if matches(r, ref)]
    if any(r.get("verified", True) for r in mine):
        return "installed"
    if mine:
        return "damaged"
    return "other" if same else "missing"


__all__ = ["ROLE_LABELS", "REPRODUCIBILITY_LABELS", "REPRODUCIBILITY_HELP", "reference", "attach",
           "detach", "status"]
