"""Export an EASI method project as the method package EASI loads.

The one explicit way an authored EASI method leaves StreamCurves: the eight method files
byte for byte, the envelope naming the method, its version and identities, and the
calculator only when it was generated from exactly these files. Pointing EASI at the
package (``EASI_METHOD_PACKAGE``) is a separate, explicit activation.

    python apps/stream-curves/scripts/export_easi_method.py <project> --out <method.zip>
    python apps/stream-curves/scripts/export_easi_method.py <project> --out <method.zip> \\
        --write-easi-data apps/easi/data
    python apps/stream-curves/scripts/export_easi_method.py <project> \\
        --write-easi-data apps/easi/data --check

``<project>`` is a project file (``.streamcurves``), a published library version: its
folder (``apps/library/assessments/easi-screening/v1``) or the ``project.easi.json`` in it,
or a Round 4 candidate package folder (``candidate.json`` beside ``method/``, as
``build_easi_candidate_package.py`` writes it; ``round4.candidate_project``).

``--write-easi-data <folder>`` makes this exporter the writer of EASI's shipped method
files (the authority flip: ``apps/easi/easi/method_authority.py``, AUTHORING.md
"Authority"): the eight method files (``METHOD_FILES``) are written into the folder byte
for byte and nothing else is touched. The folder must already be an EASI data folder (it
holds ``physio_divisions.geojson``, an evaluator asset no method carries); a file whose
bytes are already right is left alone. ``--check`` compares instead of writing: every
difference is listed and the exit status is 1. In both forms the printed JSON is the
package identity (method version, package digest, evaluator digest, the zip's SHA-256)
with the per-file outcome.

    python apps/stream-curves/scripts/export_easi_method.py <candidate folder> \\
        --version-dir D:/Data/staf-campaign-2026-09/easi/finalist-version [--version 2]

``--version-dir <folder>`` writes a library version candidate: the eight method files under
``method/``, the ``method.json`` envelope (the rehearsal label, the identities under this
evaluator), the package zip with its ``.sha256`` beside it, and ``candidate.json`` (the
candidate's record with a ``versionCandidate`` block). It is never written under
``apps/library`` or ``apps/easi/data`` and registers nothing: publishing, activation and
redeploy stay the owner's.

    python apps/stream-curves/scripts/export_easi_method.py <candidate folder> \\
        --version-dir <folder> --calculator <EASI_Calculator_<template>.xlsx>

``--calculator`` carries the workbook generated from exactly the candidate's files (EASI's
``scripts/build_calculator.py --out <xlsx>`` run with ``EASI_METHOD_PACKAGE`` set to the
candidate's zip, so the generator reads the candidate's method files and stamps their method
version): its recorded scoring method digest must be the candidate's method version under
this evaluator or it is refused; it then rides in the package zip, the envelope's
``calculator`` block and the version candidate's ``calculator/`` folder, as a published
library version holds its own.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from streamcurves import easi_env  # noqa: E402

easi_env.sanitize()

from streamcurves._vendor.easi import method_package as mp  # noqa: E402
from streamcurves.easi_method import io as eio  # noqa: E402

#: An evaluator asset every EASI data folder holds and no method package carries: its
#: presence is what makes a folder an EASI data folder.
EVALUATOR_ASSET = "physio_divisions.geojson"
PROJECT_RECORD = "project.easi.json"
CANDIDATE_RECORD = "candidate.json"
_VERSION_DIR = re.compile(r"^v\d+$")
REPO = APP.parent.parent
#: a version candidate is never written into the library or EASI's data folder
FORBIDDEN_VERSION_DIRS = (REPO / "apps" / "library", REPO / "apps" / "easi" / "data")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_candidate_folder(source: Path) -> bool:
    """A Round 4 candidate package folder: candidate.json beside method/."""
    source = Path(source)
    return source.is_dir() and (source / CANDIDATE_RECORD).is_file() and (source / mp.METHOD_DIR).is_dir()


def load_project(source: Path, *, version: int | None = None, calculator: Path | None = None):
    """The EasiProject of a project file, a library version folder or its project.easi.json,
    or a Round 4 candidate package folder (``round4.candidate_project``; ``calculator`` a
    workbook generated from exactly the candidate's files, carried with them)."""
    source = Path(source)
    if source.is_file() and source.name == PROJECT_RECORD:
        source = source.parent
    if is_candidate_folder(source):
        from streamcurves.easi_method import round4
        try:
            project, _ = round4.candidate_project(source, version=version, calculator=calculator)
        except round4.CandidateError as exc:
            raise SystemExit(str(exc))
        return project
    if source.is_dir():
        return open_version_dir(source)
    if not source.is_file():
        raise SystemExit(f"not found: {source}")
    _, project = eio.read_project(source)
    return project


def check_version_dir(folder: Path) -> Path:
    """The folder a version candidate is written to: never under the library or EASI's data,
    never a library layout (``.../assessments/<id>/v<N>``), so nothing is registered."""
    folder = Path(folder).resolve()
    for forbidden in FORBIDDEN_VERSION_DIRS:
        try:
            folder.relative_to(forbidden.resolve())
        except ValueError:
            continue
        raise SystemExit(f"{folder} is under {forbidden}; a version candidate is never written there")
    if _VERSION_DIR.match(folder.name) and folder.parent.parent.name == "assessments":
        raise SystemExit(f"{folder} is a library version layout (assessments/<id>/v<N>); a version candidate "
                         "is written outside every library and registered nowhere")
    return folder


def write_version_dir(project, folder: Path, *, source: Path | None = None,
                      calculator_note: str | None = None) -> dict:
    """Write ``project`` as a library version candidate into ``folder``: ``method/<the eight
    files>``, ``method.json`` (the envelope the exporter computes), the package zip (through
    ``export_zip``, the exporter's own writer) with its ``.sha256`` beside it, and
    ``candidate.json`` (the source candidate's record when the project came from one, with a
    ``versionCandidate`` block). Nothing is registered anywhere."""
    folder = check_version_dir(folder)
    pkg = eio.consumer_package(project)
    method_id = project.meta.get("methodId") or eio.METHOD_ID
    version = int(project.meta.get("version") or 1)
    zip_name = f"{method_id}-v{version}.easi-method.zip"
    folder.mkdir(parents=True, exist_ok=True)
    blob, ident = eio.export_zip(project, folder / zip_name)
    method_dir = folder / mp.METHOD_DIR
    method_dir.mkdir(parents=True, exist_ok=True)
    for name in mp.METHOD_FILES:
        (method_dir / name).write_bytes(pkg.files[name])
    calculator = None
    if pkg.calculator is not None:
        # as a published library version holds it: calculator/<name> beside method/, the
        # envelope's calculator block naming its bytes and sha256
        (folder / mp.CALCULATOR_DIR).mkdir(parents=True, exist_ok=True)
        (folder / mp.CALCULATOR_DIR / pkg.calculator[0]).write_bytes(pkg.calculator[1])
        calculator = {**(pkg.envelope.get("calculator") or {}), "path": f"{mp.CALCULATOR_DIR}/{pkg.calculator[0]}",
                      "generatedFor": (project.meta.get("calculator") or {}).get("generatedFor")}
        if calculator_note:
            calculator["note"] = str(calculator_note)
    envelope_text = json.dumps(pkg.envelope, indent=1, sort_keys=True) + "\n"
    (folder / mp.ENVELOPE).write_text(envelope_text, encoding="utf-8", newline="\n")
    (folder / (zip_name + ".sha256")).write_text(f"{ident['zipSha256']}  {zip_name}\n", encoding="utf-8", newline="\n")
    record = {}
    if source is not None and is_candidate_folder(source):
        record = json.loads((Path(source) / CANDIDATE_RECORD).read_text(encoding="utf-8"))
    record["versionCandidate"] = {
        "methodId": method_id, "proposedVersion": version, "label": pkg.envelope.get("label"),
        "identity": pkg.envelope.get("identity"), "requires": (pkg.envelope.get("evaluator") or {}).get("requires"),
        "files": pkg.envelope.get("files"), "zip": zip_name, "zipSha256": ident["zipSha256"], "zipBytes": ident["zipBytes"],
        "calculator": calculator,
        "registered": False, "folder": str(folder), "lineage": project.meta.get("lineage"),
        "writtenBy": "apps/stream-curves/scripts/export_easi_method.py --version-dir", "writtenAt": _now(),
        "note": ("a library version candidate written by the exporter (the sole writer of EASI method packages): "
                 "not under apps/library, registered in no manifest, activated nowhere; publishing it as a library "
                 "version, activating it in EASI, redeploying and any national rescoring are the owner's")}
    (folder / CANDIDATE_RECORD).write_text(json.dumps(record, indent=1, sort_keys=True, ensure_ascii=True) + "\n",
                                           encoding="utf-8", newline="\n")
    return {"folder": str(folder), "zip": zip_name, **ident, "label": pkg.envelope.get("label"), "version": version,
            "files": sorted(mp.METHOD_FILES) + [mp.ENVELOPE, zip_name, zip_name + ".sha256", CANDIDATE_RECORD]
            + ([calculator["path"]] if calculator else []),
            "calculator": calculator, "registered": False}


def open_version_dir(vdir: Path):
    """A published version folder ``<library>/assessments/<id>/v<N>`` as the project it
    holds (its method files byte for byte, its authoring record), read the way the app
    opens a library version; the folder's own library is the one read."""
    vdir = Path(vdir).resolve()
    ok = (bool(_VERSION_DIR.match(vdir.name)) and len(vdir.parents) >= 3
          and vdir.parents[1].name == "assessments" and (vdir / mp.ENVELOPE).is_file())
    if not ok:
        raise SystemExit(f"{vdir} is not a published EASI version folder "
                         f"(<library>/assessments/<id>/v<N> holding {mp.ENVELOPE})")
    os.environ["STAF_LIBRARY_ROOT"] = str(vdir.parents[2])   # this process only
    return eio.open_version(vdir.parent.name, int(vdir.name[1:]))


def check_data_folder(folder: Path) -> Path:
    folder = Path(folder)
    if not folder.is_dir() or not (folder / EVALUATOR_ASSET).is_file():
        raise SystemExit(f"{folder} is not an EASI data folder (no {EVALUATOR_ASSET}); "
                         "nothing written")
    return folder


def write_easi_data(project, folder: Path, *, check: bool = False) -> dict:
    """Write (or with ``check`` only compare) the eight method files into an EASI data
    folder. Only those files are ever written, each atomically, and only when its bytes
    differ; every difference is reported, with the bytes found."""
    folder = check_data_folder(folder)
    written, unchanged, differences = [], [], []
    for name in mp.METHOD_FILES:
        blob = project.files[name]
        target = folder / name
        current = target.read_bytes() if target.is_file() else None
        if current == blob:
            unchanged.append(name)
            continue
        differences.append({"file": name,
                            "expected": {"bytes": len(blob), "sha256": _sha(blob)},
                            "found": ({"bytes": len(current), "sha256": _sha(current)}
                                      if current is not None else None)})
        if not check:
            tmp = target.with_name(target.name + ".export.tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, target)
            written.append(name)
    return {"folder": str(folder), "check": bool(check), "written": written,
            "unchanged": unchanged, "differences": differences}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", help="an EASI method project (.streamcurves), a published library "
                                    "version folder (.../assessments/<id>/v<N>) or its "
                                    + PROJECT_RECORD + ", or a Round 4 candidate package folder "
                                    "(" + CANDIDATE_RECORD + " beside method/)")
    ap.add_argument("--out", help="the method package to write (.zip)")
    ap.add_argument("--write-easi-data", metavar="FOLDER",
                    help="write the eight method files into this EASI data folder, byte for byte")
    ap.add_argument("--check", action="store_true",
                    help="with --write-easi-data: compare and write nothing; exit 1 on any "
                         "difference")
    ap.add_argument("--version-dir", metavar="FOLDER",
                    help="write a library version candidate folder (method/, method.json, the zip and its "
                         ".sha256, candidate.json); never under apps/library, registered nowhere")
    ap.add_argument("--version", type=int, default=None,
                    help="with a candidate folder: the proposed version number (default: the base library "
                         "version's successor)")
    ap.add_argument("--calculator", metavar="XLSX", default=None,
                    help="with a candidate folder: the calculator workbook generated from exactly its files "
                         "(apps/easi/scripts/build_calculator.py under EASI_METHOD_PACKAGE), carried in the "
                         "package, the envelope and the version candidate's calculator/ folder; refused when its "
                         "recorded method digest is not the candidate's")
    ap.add_argument("--calculator-note", metavar="TEXT", default=None,
                    help="with --calculator and --version-dir: a note recorded beside the calculator in the "
                         "version candidate's record (for example the template version the workbook takes at "
                         "adoption); never inside the workbook or the package")
    a = ap.parse_args(argv)
    if a.calculator_note and not (a.calculator and a.version_dir):
        ap.error("--calculator-note needs --calculator and --version-dir")
    if a.check and not a.write_easi_data:
        ap.error("--check needs --write-easi-data <folder>")
    if a.check and (a.out or a.version_dir):
        ap.error("--check writes nothing; leave out --out and --version-dir")
    if not a.out and not a.write_easi_data and not a.version_dir:
        ap.error("give --out <method.zip>, --write-easi-data <folder>, --version-dir <folder>, or several")
    if (a.version is not None or a.calculator is not None) and not is_candidate_folder(Path(a.project)):
        ap.error("--version and --calculator apply to a candidate package folder")
    if a.write_easi_data:
        check_data_folder(Path(a.write_easi_data))    # refused before anything is written
    if a.version_dir:
        check_version_dir(Path(a.version_dir))        # refused before anything is written
    project = load_project(Path(a.project), version=a.version,
                           calculator=Path(a.calculator) if a.calculator else None)
    _, ident = eio.export_zip(project, Path(a.out) if a.out else None)
    out = {"out": a.out, **ident} if a.out else dict(ident)
    if a.write_easi_data:
        out["easiData"] = write_easi_data(project, Path(a.write_easi_data), check=a.check)
    if a.version_dir:
        out["versionDir"] = write_version_dir(project, Path(a.version_dir), source=Path(a.project),
                                              calculator_note=a.calculator_note)
    print(json.dumps(out, indent=1))
    if a.check and out["easiData"]["differences"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
