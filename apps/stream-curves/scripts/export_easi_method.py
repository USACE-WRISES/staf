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

``<project>`` is a project file (``.streamcurves``) or a published library version: its
folder (``apps/library/assessments/easi-screening/v1``) or the ``project.easi.json`` in it.

``--write-easi-data <folder>`` makes this exporter the writer of EASI's shipped method
files (the authority flip: ``apps/easi/easi/method_authority.py``, AUTHORING.md
"Authority"): the eight method files (``METHOD_FILES``) are written into the folder byte
for byte and nothing else is touched. The folder must already be an EASI data folder (it
holds ``physio_divisions.geojson``, an evaluator asset no method carries); a file whose
bytes are already right is left alone. ``--check`` compares instead of writing: every
difference is listed and the exit status is 1. In both forms the printed JSON is the
package identity (method version, package digest, evaluator digest, the zip's SHA-256)
with the per-file outcome.
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
_VERSION_DIR = re.compile(r"^v\d+$")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_project(source: Path):
    """The EasiProject of a project file, a library version folder or its project.easi.json."""
    source = Path(source)
    if source.is_file() and source.name == PROJECT_RECORD:
        source = source.parent
    if source.is_dir():
        return open_version_dir(source)
    if not source.is_file():
        raise SystemExit(f"not found: {source}")
    _, project = eio.read_project(source)
    return project


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
                                    + PROJECT_RECORD)
    ap.add_argument("--out", help="the method package to write (.zip)")
    ap.add_argument("--write-easi-data", metavar="FOLDER",
                    help="write the eight method files into this EASI data folder, byte for byte")
    ap.add_argument("--check", action="store_true",
                    help="with --write-easi-data: compare and write nothing; exit 1 on any "
                         "difference")
    a = ap.parse_args(argv)
    if a.check and not a.write_easi_data:
        ap.error("--check needs --write-easi-data <folder>")
    if a.check and a.out:
        ap.error("--check writes nothing; leave out --out")
    if not a.out and not a.write_easi_data:
        ap.error("give --out <method.zip>, --write-easi-data <folder>, or both")
    if a.write_easi_data:
        check_data_folder(Path(a.write_easi_data))    # refused before anything is written
    project = load_project(Path(a.project))
    _, ident = eio.export_zip(project, Path(a.out) if a.out else None)
    out = {"out": a.out, **ident} if a.out else dict(ident)
    if a.write_easi_data:
        out["easiData"] = write_easi_data(project, Path(a.write_easi_data), check=a.check)
    print(json.dumps(out, indent=1))
    if a.check and out["easiData"]["differences"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
