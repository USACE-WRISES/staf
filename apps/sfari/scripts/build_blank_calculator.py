"""Write SFARI's blank workbook, ``data/calculator/<version>_SFARI Calculator.xlsx``.

The blank is the completed workbook's tabs (Instructions, Summary, Existing Conditions,
ReferenceCurves) with one empty Existing Conditions, made from the template by
``sfari.workbook.build_blank`` (owner, 2026-10-19). Get Forms serves it as it is, the static web
app keeps a copy (``apps/staf-web/parity/copy_page.py``), and the Apply STAF page links it raw
from main. Re-run it after the template, the shared workbook toolkit or the metrics' criteria
change; ``tests/test_calculator_blank.py`` fails until you do.

    .venv\\Scripts\\python apps/sfari/scripts/build_blank_calculator.py [--check]
"""
from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from sfari import calculator, workbook  # noqa: E402


def parts(data: bytes) -> list:
    """Each member's name and bytes, in order: the same workbook whichever zlib compressed it."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return [(i.filename, z.read(i.filename)) for i in z.infolist()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when the file is not what the template makes")
    a = ap.parse_args(argv)
    data = workbook.build_blank()
    path = calculator.BLANK_PATH
    if a.check:
        ok = path.is_file() and parts(path.read_bytes()) == parts(data)
        print(f"{path.name}: {'up to date' if ok else 'stale; run this script without --check'}")
        return 0 if ok else 1
    path.write_bytes(data)
    print(f"wrote {path} ({len(data):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
