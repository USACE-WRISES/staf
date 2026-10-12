"""Opens the static app's saved assessment files with each Shiny tool's own reader, and writes them again.

For each file (``easi-assessment.json``, ``sfari-assessment.json``, ``deep-assessment.json``, or any file
named on the command line): the tool's ``session.load`` must read it (the Shiny tool's Open would), and
``assessment_file.dump`` of what the file holds, with its own ``savedAt``, must give the file's bytes back:
the static app wrote exactly what Python writes for those values (every number as int or float as Python
holds it after reading the file). Run with the repo's .venv:

    .venv\\Scripts\\python apps/staf-web/parity/roundtrip_open.py FILE [FILE ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TOOLS = {"EASI": "easi", "SFARI": "sfari", "DEEP": "deep"}


def check(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    raw = json.loads(text)
    tool = raw.get("tool")
    key = TOOLS.get(tool)
    if key is None:
        print(f"{path.name}: no tool ({tool!r})")
        return False
    app = REPO / "apps" / key
    sys.path.insert(0, str(app))
    try:
        if key == "easi":
            from easi import session as mod
            from easi._vendor.staf_workbook import assessment_file
        elif key == "sfari":
            from sfari import session as mod
            from sfari._vendor.staf_workbook import assessment_file
        else:
            from deep import session as mod
            from deep._vendor.staf_workbook import assessment_file
        loaded = mod.load(text)
        again = assessment_file.dump(tool, raw["delineation"], raw["toolData"], raw["scenarios"], saved_at=raw["savedAt"])
    finally:
        sys.path.remove(str(app))
        for name in list(sys.modules):
            if name == key or name.startswith(key + "."):
                del sys.modules[name]
    same = again == text
    print(f"{path.name}: {tool} reads it ({', '.join(sorted(loaded))}); written again "
          + ("byte for byte" if same else "DIFFERENTLY"))
    if not same:
        for i, (a, b) in enumerate(zip(text, again)):
            if a != b:
                print(f"  first difference at {i}: file {text[max(0, i - 80):i + 80]!r}")
                print(f"  {'':>27} python {again[max(0, i - 80):i + 80]!r}")
                break
        else:
            print(f"  lengths {len(text)} and {len(again)}")
    return same


def main(argv) -> int:
    files = [Path(a) for a in argv] or sorted(Path.cwd().glob("*-assessment.json"))
    ok = [check(f) for f in files]
    return 0 if ok and all(ok) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
