"""Compares the files the Shiny tools and the static app wrote, by name.

``compare_files.py A B``: two files, or two folders (their files paired by name). An assessment file
(``*-assessment.json``) is compared with its ``savedAt`` left out; a CSV, GeoJSON or other text file
byte for byte; an xlsx part by part (each member's bytes); a PDF by its decoded page streams
(``pdf_compare.py``). Prints what differs, first difference first, and exits 1 when anything does.

    .venv\\Scripts\\python apps/staf-web/parity/compare_files.py SHINY_DIR STATIC_DIR
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path


def _first_diff(a: str, b: str, label: str) -> str:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return (f"{label}: first difference at {i}\n  A {a[max(0, i - 100):i + 100]!r}\n"
                    f"  B {b[max(0, i - 100):i + 100]!r}")
    return f"{label}: lengths {len(a)} and {len(b)}"


def _json_paths(a, b, path="", out=None, limit=40):
    """Where two JSON values differ (value, type: an int against a float), as paths."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in list(dict.fromkeys(list(a) + list(b))):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'A' if k in a else 'B'}")
            else:
                _json_paths(a[k], b[k], f"{path}/{k}", out, limit)
        if list(a) != list(b) and set(a) == set(b):
            out.append(f"{path}: key order differs")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} items against {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            _json_paths(x, y, f"{path}[{i}]", out, limit)
    elif type(a) is not type(b) or a != b:
        if not (isinstance(a, float) and isinstance(b, float) and a != a and b != b):
            out.append(f"{path}: {a!r} ({type(a).__name__}) against {b!r} ({type(b).__name__})")
    return out


def compare(a: Path, b: Path) -> list[str]:
    name = a.name
    if name.endswith("-assessment.json"):
        ta = re.sub(r'"savedAt": "[^"]*"', '"savedAt": ""', a.read_text(encoding="utf-8"))
        tb = re.sub(r'"savedAt": "[^"]*"', '"savedAt": ""', b.read_text(encoding="utf-8"))
        if ta == tb:
            return []
        paths = _json_paths(json.loads(ta), json.loads(tb))
        return [_first_diff(ta, tb, name)] + [f"  {p}" for p in paths]
    if name.endswith(".xlsx"):
        za, zb = zipfile.ZipFile(a), zipfile.ZipFile(b)
        out = []
        na, nb = [i.filename for i in za.infolist()], [i.filename for i in zb.infolist()]
        if na != nb:
            out.append(f"{name}: members differ: {na} against {nb}")
        for m in [n for n in na if n in nb]:
            x, y = za.read(m), zb.read(m)
            if x != y:
                out.append(_first_diff(x.decode("utf-8", "replace"), y.decode("utf-8", "replace"), f"{name}:{m}"))
        return out
    if name.endswith(".pdf"):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from pdf_compare import compare_pdfs
        return compare_pdfs(a, b)
    x, y = a.read_bytes(), b.read_bytes()
    if x == y:
        return []
    out = [_first_diff(x.decode("utf-8", "replace"), y.decode("utf-8", "replace"), name)]
    if name.endswith((".geojson", ".json")):
        try:
            out += [f"  {p}" for p in _json_paths(json.loads(x), json.loads(y))]
        except ValueError:
            pass
    return out


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    a, b = Path(argv[0]), Path(argv[1])
    pairs = [(a, b)] if a.is_file() else [(f, b / f.name) for f in sorted(a.iterdir()) if f.is_file() and (b / f.name).is_file()]
    if a.is_dir():
        for f in sorted(set(p.name for p in a.iterdir()) ^ set(p.name for p in b.iterdir())):
            print(f"{f}: only in {'A' if (a / f).exists() else 'B'}")
    bad = 0
    for x, y in pairs:
        diffs = compare(x, y)
        if diffs:
            bad += 1
            print("\n".join(diffs))
        else:
            print(f"{x.name}: the same")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
