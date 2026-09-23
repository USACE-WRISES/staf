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
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
REPO = APP.parent.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from streamcurves import easi_env  # noqa: E402

easi_env.sanitize()

from streamcurves import project_file as pf  # noqa: E402
from streamcurves.easi_method import io as eio  # noqa: E402

EASI = REPO / "apps" / "easi"


def export_cases() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "cases.json"
        proc = subprocess.run([sys.executable, "-B", "scripts/export_preview_cases.py", str(out)],
                              cwd=EASI, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            raise SystemExit(f"EASI's preview case export failed: {proc.stderr[-1500:]}")
        return json.loads(out.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="the project file to write (.streamcurves)")
    ap.add_argument("--by", default="", help="who imports it (recorded)")
    ap.add_argument("--version", type=int, default=1)
    ap.add_argument("--name", default="EASI screening method")
    ap.add_argument("--release-tag", default=None)
    ap.add_argument("--release-date", default=None)
    a = ap.parse_args(argv)
    if not (EASI / "data").is_dir():
        raise SystemExit("apps/easi is not in this checkout; importing needs the EASI source")
    calc = EASI / "www" / "calculator" / "EASI_Calculator_1.0.xlsx"
    project = eio.import_from_easi(
        EASI / "data", imported_by=a.by or "maintainer", version=a.version, cases=export_cases(),
        calculator=calc if calc.is_file() else None,
        promotion_receipt=EASI / "data" / "source" / "alternative-2-promotion.json",
        release=({"tag": a.release_tag, "date": a.release_date} if a.release_tag else None))
    path = eio.write_project(project, Path(a.out), name=a.name)
    ident = project.identity()
    print(f"imported method {ident['methodVersion']} (package {ident['packageDigest'][7:19]}) "
          f"-> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
