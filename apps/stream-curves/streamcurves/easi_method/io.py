"""Importer and exporter for EASI method projects.

The two directions are explicit and one-way:

- ``import_from_easi`` reads EASI's method files once (a maintainer checkout's
  ``apps/easi/data``) and creates the authored project, byte for byte;
- ``consumer_package`` / ``export_zip`` turn a project into the method package
  EASI loads.

Neither runs implicitly, and neither writes into ``apps/easi``: making the
exporter the writer of EASI's shipped files is the owner's adoption step.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Optional

from .._vendor.easi import method_package as mp
from . import register as reg
from . import stages
from .model import EasiProject, sha

METHOD_ID = "easi-screening"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def import_from_easi(data_dir: Path, *, imported_by: str, version: int = 1,
                     cases: Optional[dict] = None, calculator: Optional[Path] = None,
                     promotion_receipt: Optional[Path] = None, release: Optional[dict] = None,
                     label: Optional[str] = None) -> EasiProject:
    """The authored project of the method in ``data_dir``, unchanged.

    ``cases`` is the preview case set (``apps/easi/scripts/export_preview_cases.py``);
    ``calculator`` the workbook generated from these files; ``promotion_receipt`` the
    record of how the files became operational (``data/source/alternative-2-promotion.json``);
    ``release`` what the owner released them as (tag, date).
    """
    data_dir = Path(data_dir)
    files = {name: (data_dir / name).read_bytes() for name in mp.METHOD_FILES}
    ident = json.loads(files["scoring-identity.json"].decode("utf-8"))
    calc = (calculator.name, calculator.read_bytes()) if calculator else None
    project = EasiProject(meta={}, files=files, calculator=calc, cases=cases)
    identity = project.identity()
    receipt = None
    if promotion_receipt is None and (data_dir / "source" / "alternative-2-promotion.json").is_file():
        # the promotion record EASI keeps beside its method files
        promotion_receipt = data_dir / "source" / "alternative-2-promotion.json"
    if promotion_receipt and Path(promotion_receipt).is_file():
        receipt = json.loads(Path(promotion_receipt).read_text(encoding="utf-8"))
    now = _now()
    project.meta = {
        "methodId": METHOD_ID,
        "version": int(version),
        "status": "draft",
        "label": label or ident.get("alternative_name") or "EASI screening method",
        "criteriaSet": "regional",
        "geography": {"kind": "national", "code": "CONUS",
                      "name": "Contiguous United States",
                      # what the curve sets are stratified by (an adopted alternative updates it)
                      "strata": stages.strata_names(project.curves()),
                      "note": "One national method; its strata and their national fallbacks are "
                              "inside the method."},
        "created": now,
        "updated": now,
        "calculatorFor": identity["packageDigest"] if calc else None,
        "lineage": {
            "importedFrom": {
                "source": "apps/easi/data",
                "importedAt": now,
                "importedBy": imported_by,
                **identity,
                "scoringIdentity": ident,
                "promotionReceipt": receipt,
                "release": release,
            },
            "origin": {"kind": "import", **identity},
        },
    }
    project.register = reg.imported_register(project, decided_at=now, receipt=receipt)
    project.history = [{"action": "import", "at": now, "by": imported_by, "kind": "import",
                        "detail": f"method {identity['methodVersion']} imported unchanged from "
                                  f"apps/easi/data"}]
    return project


def easi_source(repo_root: Path) -> Optional[Path]:
    """``apps/easi`` in a checkout, or None when this copy has no EASI source."""
    easi = Path(repo_root) / "apps" / "easi"
    return easi if (easi / "data").is_dir() and (easi / "scripts" / "export_preview_cases.py").is_file()         else None


def export_cases(easi_app: Path, *, timeout: float = 900.0) -> dict:
    """The preview case set, written by EASI's own exporter in its own process."""
    import subprocess
    import sys
    import tempfile
    with tempfile.TemporaryDirectory(prefix="sc-easi-cases-") as tmp:
        out = Path(tmp) / "cases.json"
        proc = subprocess.run([sys.executable, "-B", "scripts/export_preview_cases.py", str(out)],
                              cwd=str(easi_app), capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0 or not out.is_file():
            raise RuntimeError(f"EASI's preview case export failed: {(proc.stderr or '')[-1500:]}")
        return json.loads(out.read_text(encoding="utf-8"))


def import_from_checkout(repo_root: Path, *, imported_by: str, version: int = 1,
                         release: Optional[dict] = None,
                         evidence_dir: Optional[Path] = None) -> EasiProject:
    """EASI's current method from a checkout's ``apps/easi``: the eight method files byte for
    byte, the preview cases EASI exports, the committed calculator (generated from exactly
    these files) and the promotion receipt. Nothing is written to ``apps/easi``.
    ``evidence_dir`` is an evidence export (``index.json`` and one folder per package): the
    project then names those packages as its development data."""
    easi = easi_source(repo_root)
    if easi is None:
        raise RuntimeError("apps/easi is not in this checkout; importing needs the EASI source")
    calc = easi / "www" / "calculator" / "EASI_Calculator_1.0.xlsx"
    project = import_from_easi(easi / "data", imported_by=imported_by, version=version,
                               cases=export_cases(easi),
                               calculator=calc if calc.is_file() else None,
                               promotion_receipt=easi / "data" / "source" / "alternative-2-promotion.json",
                               release=release)
    if evidence_dir is not None:
        project.evidence = evidence_references(Path(evidence_dir))
    return project


def evidence_references(evidence_dir: Path) -> list[dict]:
    """References to the packages of an evidence export, each with the archive it ships as."""
    from .. import evidence_store as evs
    from .evidence import reference
    index = json.loads((evidence_dir / "index.json").read_text(encoding="utf-8"))
    refs = []
    for package_id, rec in sorted(index.items()):
        doc = evs.read_manifest(evidence_dir / package_id)
        if doc["dataDigest"] != rec.get("dataDigest"):
            raise ValueError(f"{package_id}: the export's index and its manifest disagree")
        archive = {"name": rec["zip"], "sha256": rec["zipSha256"], "bytes": int(rec["zipBytes"])}
        refs.append(reference(doc, archive=archive, package_digest=evs.package_digest(doc)))
    return refs


def consumer_package(project: EasiProject) -> mp.MethodPackage:
    """The method package EASI loads: the method files plus the envelope; a calculator
    only when it was generated from exactly these files."""
    calc = project.calculator if (project.calculator and
                                  project.meta.get("calculatorFor") == project.package_digest) else None
    env = mp.build_envelope(project.files, method_id=project.meta.get("methodId") or METHOD_ID,
                            version=int(project.meta.get("version") or 1),
                            label=project.meta.get("label") or "",
                            criteria_set=project.meta.get("criteriaSet") or "regional",
                            calculator=calc)
    return mp.MethodPackage(envelope=env, files=dict(project.files), calculator=calc)


def export_zip(project: EasiProject, out: Optional[Path] = None) -> tuple[bytes, dict]:
    pkg = consumer_package(project)
    blob = mp.to_zip(pkg)
    if out is not None:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(blob)
    return blob, {**pkg.envelope["identity"], "zipSha256": sha(blob), "zipBytes": len(blob)}


def fork(project: EasiProject, *, by: str, label: Optional[str] = None,
         version: Optional[int] = None) -> EasiProject:
    """A draft revision of ``project``: the next version number (``version`` when the
    library's next number is higher, as for a revision of an older version), the origin
    recorded (its version, package and method identities), the register and notes carried,
    a fork record at the head of its history. The origin is never modified."""
    new = project.copy()
    new.base = {}                       # the fork's origin is exactly the files it starts from
    ident = project.identity()
    now = _now()
    base_version = int(project.meta.get("version") or 1)
    new_version = max(base_version + 1, int(version or 0))
    new.meta["version"] = new_version
    new.meta["status"] = "draft"
    new.meta["created"] = now
    new.meta["updated"] = now
    if label:
        new.meta["label"] = label
        new.meta["labelSetByAuthor"] = True
    lineage = dict(project.meta.get("lineage") or {})
    lineage["origin"] = {"kind": "revision", "methodId": project.meta.get("methodId"),
                         "version": base_version, "status": project.meta.get("status"),
                         "scoringIdentity": json.loads(project.files["scoring-identity.json"]
                                                       .decode("utf-8")),
                         **ident}
    new.meta["lineage"] = lineage
    new.history = list(project.history) + [{
        "action": "fork", "at": now, "by": by, "kind": "lifecycle",
        "detail": f"draft revision v{new_version} of v{base_version} "
                  f"(method {ident['methodVersion']})"}]
    return new


REGION = {"kind": "national", "code": "CONUS", "name": "Contiguous United States"}


def write_project(project: EasiProject, path: Path, *, name: str, prepared_by: Optional[str] = None,
                  desktop_project: bool = True, origin: Optional[dict] = None,
                  meta: Optional[dict] = None, origin_files: Optional[dict] = None) -> Path:
    """Write ``project`` as a StreamCurves project file (format 2, assessment type easi).
    ``meta`` carries an existing project's identity (id, created) on a save;
    ``origin_files`` the ``origin/`` files it was opened with (a library copy's records)."""
    from .. import project_file as pf
    from .. import project_meta as pm
    m = dict(meta or {})
    m.setdefault("project_id", pm.new_identity())
    m.setdefault("project_created", pm.now_iso())
    m["project_name"] = name
    if prepared_by is not None:
        m["prepared_by"] = prepared_by
    if origin is not None:
        m["origin"] = origin
    m["region"] = dict(REGION)
    text = pf.session_text_from_fields({}, session_name=name)
    return pf.write_project(path, meta=m, session_text=text, parts=project.to_parts(),
                            assessment_type="easi", desktop_project=desktop_project,
                            origin=origin_files)


def read_project(source):
    """``(ProjectFile, EasiProject)`` from a project file path or bytes."""
    from .. import project_file as pf
    proj = pf.read_project(source)
    if proj.assessment_type != "easi":
        raise pf.ProjectFileError("This project is not an EASI method project.")
    try:
        return proj, EasiProject.from_parts(proj.parts)
    except ValueError as exc:
        raise pf.ProjectFileError(f"This EASI project is damaged ({exc}).") from exc


ASSESSMENT_ID = "easi-screening"
ASSESSMENT_NAME = "EASI screening method"


def publish(project: EasiProject, *, author: str, revision_notes: str = "", status: str = "draft",
            assessment_id: str = ASSESSMENT_ID, consequences: Optional[dict] = None) -> int:
    """Publish ``project`` as the next version of the library's EASI method (maintainer).

    The library receives the method package exactly as EASI would load it, the authoring
    record beside it (register, decisions, lineage, notes, history) and the preview cases.
    ``consequences`` is the reviewed preview summary (``evaluate.preview``), kept in the
    version's provenance. Returns the new version number. The project's own version must
    be that number, and no changed function may still wait for its confirmation."""
    from .. import library as lib
    if lib.is_canonical_root():
        reason = lib.publish_gate_reason(author)
        if reason:
            raise RuntimeError(reason)
    pending = reg.needs_review(project)
    if pending:
        names = ", ".join(r["functionName"] for r in pending)
        raise ValueError(f"confirm the changed selections before publishing: {names}")
    pkg = consumer_package(project)
    doc = json.loads(project.to_parts()["easi/package.json"].decode("utf-8"))
    ident = project.identity()
    provenance = {"kind": "easi-method", "methodId": project.meta.get("methodId"),
                  "identity": ident, "lineage": project.meta.get("lineage"),
                  "history": project.history, "publishedBy": author,
                  "consequences": consequences,
                  # what was considered for each function and why: beside the package, never in it
                  "candidateRegister": {"schema": 1, "rows": reg.export_rows(project),
                                        "studies": (project.register or {}).get("studies") or []}}
    meta = {"assessmentName": ASSESSMENT_NAME, "region": dict(REGION), "author": author,
            "revisionNotes": revision_notes}
    return lib.publish_easi_version(assessment_id, meta, envelope=pkg.envelope, files=pkg.files,
                                    calculator=pkg.calculator, project=doc, cases=project.cases,
                                    provenance=provenance, status=status)


def open_version(assessment_id: str, version: int) -> EasiProject:
    """A published EASI method version as a project: its files byte for byte, its
    authoring record, and the version itself recorded as the origin."""
    from .. import library as lib
    got = lib.easi_version_files(assessment_id, version)
    doc = got["project"] or {}
    status = lib.version_status(assessment_id, version)
    project = EasiProject(meta=dict(doc.get("meta") or {}), files=dict(got["files"]),
                          calculator=got["calculator"],
                          register=doc.get("register") or {"candidates": [], "decisions": []},
                          evidence=doc.get("evidence") or [], recipes=doc.get("recipes") or {},
                          notes=doc.get("notes") or {}, history=doc.get("history") or [],
                          cases=got["cases"])
    ident = project.identity()
    project.meta["version"] = int(version)
    project.meta["status"] = status
    if got["calculator"] is not None:
        project.meta["calculatorFor"] = ident["packageDigest"]
    lineage = dict(project.meta.get("lineage") or {})
    lineage["origin"] = {"kind": "library", "assessmentId": assessment_id, "version": int(version),
                         "status": status, "scoringIdentity": json.loads(
                             got["files"]["scoring-identity.json"].decode("utf-8")), **ident}
    project.meta["lineage"] = lineage
    return project
