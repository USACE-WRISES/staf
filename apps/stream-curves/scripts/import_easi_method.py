"""Import EASI's current method into an authored StreamCurves project (maintainer, checkout).

The one explicit way EASI's method files enter StreamCurves: the eight method files are
read byte for byte from ``apps/easi/data``, the preview case set is exported by EASI's own
script, the committed calculator rides along (it was generated from exactly these files),
and the promotion receipt names how the files became operational. Nothing is written to
``apps/easi``.

    python apps/stream-curves/scripts/import_easi_method.py --out <Name>.streamcurves
        [--by NAME] [--version 1] [--release-tag easi-v1.0.0 --release-date 2026-09-16]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
REPO = APP.parent.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from streamcurves import easi_env  # noqa: E402

easi_env.sanitize()

from streamcurves.easi_method import io as eio  # noqa: E402

EASI = REPO / "apps" / "easi"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="the project file to write (.streamcurves)")
    ap.add_argument("--by", default="", help="who imports it (recorded)")
    ap.add_argument("--version", type=int, default=1)
    ap.add_argument("--name", default="EASI screening method")
    ap.add_argument("--release-tag", default=None)
    ap.add_argument("--release-date", default=None)
    ap.add_argument("--evidence", type=Path, default=None,
                    help="an evidence export folder (index.json): the project names its packages")
    a = ap.parse_args(argv)
    if eio.easi_source(REPO) is None:
        raise SystemExit("apps/easi is not in this checkout; importing needs the EASI source")
    project = eio.import_from_checkout(
        REPO, imported_by=a.by or "maintainer", version=a.version,
        release=({"tag": a.release_tag, "date": a.release_date} if a.release_tag else None),
        evidence_dir=a.evidence)
    path = eio.write_project(project, Path(a.out), name=a.name)
    ident = project.identity()
    print(f"imported method {ident['methodVersion']} (package {ident['packageDigest'][7:19]}) "
          f"-> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
