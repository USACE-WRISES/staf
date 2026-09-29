"""Check a staged apps payload against the byte records it ships (stdlib only).

StreamCurves fingerprints some files over their raw bytes, and two committed records pin those
bytes: data/nrsa_provenance.json (the legacy NRSA files) and data/nrsa/manifest.json (the
multi-cycle archive). A payload whose bytes differ from the records would hash differently from
the maintainer's checkout, so the build fails instead of shipping it. It also checks that
sentinel config files are LF: a subtree `git archive` ignores the root .gitattributes and
converts text to CRLF on a Windows machine with core.autocrlf=true. And it checks that the
library snapshot is catalog-only: the catalog and each assessment's records ship, a version
folder (v1, v2, ...) never does, because an installed copy downloads versions from the
`library` release and the snapshot only lists them offline.

    python desktop/scripts/check_payload_records.py --stage desktop/build/apps-work/stage
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

#: Text files that must be LF in the payload, as in every checkout.
LF_SENTINELS = (
    "stream-curves/config/metric_map.yaml",
    "stream-curves/config/methodology/methodology_config.yaml",
    "stream-curves/app.py",
)
#: The library snapshot: its catalog, and per assessment the record every reader needs.
LIBRARY_DIR = "library"
LIBRARY_CATALOG = "catalog.json"
ASSESSMENT_RECORD = "manifest.json"
_VERSION_DIR = re.compile(r"^v\d+$")


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _records(stage: Path):
    """(path, sha256, bytes) for every file the two committed records describe."""
    data = stage / "stream-curves" / "data"
    prov = json.loads((data / "nrsa_provenance.json").read_text(encoding="utf-8"))
    for f in prov.get("files") or []:
        yield data / f["file"], f["sha256"], f.get("bytes")
    manifest_path = data / "nrsa" / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for rel, rec in (manifest.get("files") or {}).items():
            yield data / "nrsa" / rel, rec["sha256"], rec.get("bytes")


def library_problems(stage: Path) -> tuple[list[str], int]:
    """(problems, assessments seen) for the staged library snapshot: the catalog present,
    every assessment folder carrying its manifest, and no version folder anywhere. A stage
    without a library folder is left to the build script, which requires the catalog."""
    lib = stage / LIBRARY_DIR
    problems: list[str] = []
    if not lib.is_dir():
        return problems, 0
    if not (lib / LIBRARY_CATALOG).is_file():
        problems.append(f"{LIBRARY_DIR}/{LIBRARY_CATALOG}: missing")
    assessments = lib / "assessments"
    folders = sorted(p for p in assessments.iterdir() if p.is_dir()) if assessments.is_dir() else []
    for adir in folders:
        rel = adir.relative_to(stage).as_posix()
        if not (adir / ASSESSMENT_RECORD).is_file():
            problems.append(f"{rel}/{ASSESSMENT_RECORD}: missing")
        for sub in sorted(p for p in adir.iterdir() if p.is_dir()):
            if _VERSION_DIR.match(sub.name):
                problems.append(f"{rel}/{sub.name}: a version folder shipped (the snapshot is "
                                "catalog-only; installed copies download versions from the "
                                "library release)")
    return problems, len(folders)


def check(stage: Path) -> list[str]:
    problems = []
    n = 0
    for path, sha, size in _records(stage):
        n += 1
        rel = path.relative_to(stage).as_posix()
        if not path.is_file():
            problems.append(f"{rel}: missing")
            continue
        if size is not None and path.stat().st_size != size:
            problems.append(f"{rel}: {path.stat().st_size} bytes, recorded {size}")
        elif _sha(path) != sha:
            problems.append(f"{rel}: sha256 differs from the record")
    for rel in LF_SENTINELS:
        path = stage / rel
        if path.is_file() and b"\r\n" in path.read_bytes():
            problems.append(f"{rel}: CRLF line endings (the archive did not apply .gitattributes)")
    lib_problems, n_assessments = library_problems(stage)
    problems += lib_problems
    print(f"[records] checked {n} recorded files, {len(LF_SENTINELS)} LF sentinels and the "
          f"library snapshot ({n_assessments} assessments, catalog only)")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stage", type=Path, required=True,
                    help="the staged payload root (holds stream-curves/ and library/)")
    a = ap.parse_args(argv)
    problems = check(a.stage)
    for p in problems:
        print(f"[records] {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
