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
                      "strata": ["NARS-9 region", "slope class"],
                      "note": "One national method; NARS-9 and slope-class strata and their national "
                              "fallbacks are inside the method."},
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
    project.register = reg.imported_register(project, decided_at=now)
    project.history = [{"action": "import", "at": now, "by": imported_by, "kind": "import",
                        "detail": f"method {identity['methodVersion']} imported unchanged from "
                                  f"apps/easi/data"}]
    return project


def consumer_package(project: EasiProject, *, status: Optional[str] = None) -> mp.MethodPackage:
    """The method package EASI loads: the method files plus the envelope; a calculator
    only when it was generated from exactly these files."""
    calc = project.calculator if (project.calculator and
                                  project.meta.get("calculatorFor") == project.package_digest) else None
    env = mp.build_envelope(project.files, method_id=project.meta.get("methodId") or METHOD_ID,
                            version=int(project.meta.get("version") or 1),
                            label=project.meta.get("label") or "",
                            status=status or project.meta.get("status") or "draft",
                            criteria_set=project.meta.get("criteriaSet") or "regional",
                            calculator=calc)
    return mp.MethodPackage(envelope=env, files=dict(project.files), calculator=calc)


def export_zip(project: EasiProject, out: Optional[Path] = None, *,
               status: Optional[str] = None) -> tuple[bytes, dict]:
    pkg = consumer_package(project, status=status)
    blob = mp.to_zip(pkg)
    if out is not None:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(blob)
    return blob, {**pkg.envelope["identity"], "zipSha256": sha(blob), "zipBytes": len(blob)}


def fork(project: EasiProject, *, by: str, label: Optional[str] = None) -> EasiProject:
    """A draft revision of ``project``: the next version number, the origin recorded (its
    version, package and method identities), the register and notes carried, a fork record
    at the head of its history. The origin is never modified."""
    new = project.copy()
    ident = project.identity()
    now = _now()
    base_version = int(project.meta.get("version") or 1)
    new.meta["version"] = base_version + 1
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
        "detail": f"draft revision v{base_version + 1} of v{base_version} "
                  f"(method {ident['methodVersion']})"}]
    return new


REGION = {"kind": "national", "code": "CONUS", "name": "Contiguous United States"}


def write_project(project: EasiProject, path: Path, *, name: str, prepared_by: Optional[str] = None,
                  desktop_project: bool = True, origin: Optional[dict] = None,
                  meta: Optional[dict] = None) -> Path:
    """Write ``project`` as a StreamCurves project file (format 2, assessment type easi).
    ``meta`` carries an existing project's identity (id, created) on a save."""
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
                            assessment_type="easi", desktop_project=desktop_project)


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
