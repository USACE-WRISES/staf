"""Export an EASI method project as the method package EASI loads.

The one explicit way an authored EASI method leaves StreamCurves: the eight method files
byte for byte, the envelope naming the method, its version and identities, and the
calculator only when it was generated from exactly these files. Nothing is written to
``apps/easi``; pointing EASI at the package (``EASI_METHOD_PACKAGE``) is a separate,
explicit activation.

    python apps/stream-curves/scripts/export_easi_method.py <project.streamcurves> --out <method.zip>
        [--status draft|preliminary]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from streamcurves import easi_env  # noqa: E402

easi_env.sanitize()

from streamcurves.easi_method import io as eio  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", help="an EASI method project (.streamcurves)")
    ap.add_argument("--out", required=True, help="the method package to write (.zip)")
    ap.add_argument("--status", default=None, help="the lifecycle status to stamp (default: the project's)")
    a = ap.parse_args(argv)
    _, project = eio.read_project(Path(a.project))
    blob, ident = eio.export_zip(project, Path(a.out), status=a.status)
    print(json.dumps({"out": a.out, **ident}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
