"""Write the STAF site's list of DEEP calculators (docs/_data/deep_calculators.json).

One entry per regional assessment DEEP shows: its default version's workbook
(``www/calculators/<id>.xlsx``, which the bake rewrites for every new default), named by its
ecoregion and labeled with its version and status. The site's Apply STAF page renders the list as
a picker whose downloads come raw from main, so a download is always the current calculator even
if the list lags behind a publish.

DEEP's bake (``bake_library_into_deep.py``) runs this after every bake of DEEP's own folders, so a
library publish refreshes it; ``tests/test_site_calculators.py`` fails when it is stale.

Usage:
    py scripts/build_site_calculators.py           # rewrite docs/_data/deep_calculators.json
    py scripts/build_site_calculators.py --check   # exit 1 when it is stale, write nothing
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
REGISTRY = APP / "data" / "deep-assessments.json"
CALCULATORS = APP / "www" / "calculators"
OUT = REPO / "docs" / "_data" / "deep_calculators.json"

if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from deep import config, session  # noqa: E402


def rows() -> list:
    """The calculators the site lists, read fresh from DEEP's baked files (never through
    config's cached registry: the bake has just rewritten them)."""
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    index = CALCULATORS / "index.json"
    defaults = (json.loads(index.read_text(encoding="utf-8")).get("default") or {}) if index.is_file() else {}
    records = dict(((r.get("assessmentId"), int(r.get("version") or 0)), r) for r in doc.get("assessments") or [])
    out = []
    for aid, pointer in (doc.get("libraryCatalog") or {}).items():
        rec = records.get((aid, int(pointer.get("defaultVersion") or 0)))
        name = defaults.get(aid)
        if rec is None or not name or config._is_hidden(rec) or not (CALCULATORS / name).is_file():
            continue
        region = rec.get("region") or {}
        out.append({"id": aid,
                    "region": region.get("name") or rec.get("assessmentName") or aid,
                    "code": str(region.get("code") or ""),
                    "kind": region.get("kind") or "",
                    "version": int(rec.get("version")),
                    "status": session.status_label(session.lifecycle_status(rec)),
                    "file": f"apps/deep/www/calculators/{name}"})
    return sorted(out, key=lambda r: (r["region"].casefold(), r["id"]))


def render(items=None) -> str:
    items = rows() if items is None else items
    return json.dumps({"generated_by": "apps/deep/scripts/build_site_calculators.py", "calculators": items},
                      indent=2, ensure_ascii=False) + "\n"


def write() -> str | None:
    """Rewrite the site's list; None when this checkout has no site (no docs/_data)."""
    if not OUT.parent.is_dir():
        return None
    text = render()
    OUT.write_text(text, encoding="utf-8", newline="\n")
    count = len(json.loads(text)["calculators"])
    return f"site calculators: {count} listed in {OUT.relative_to(REPO).as_posix()}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Write the STAF site's list of DEEP calculators.")
    parser.add_argument("--check", action="store_true", help="Exit 1 when the list is stale; write nothing.")
    args = parser.parse_args(argv)
    if args.check:
        current = OUT.read_text(encoding="utf-8").replace("\r\n", "\n") if OUT.is_file() else ""
        if current != render():
            print(f"{OUT.relative_to(REPO).as_posix()} is stale: run py apps/deep/scripts/build_site_calculators.py")
            return 1
        print("site calculators: current")
        return 0
    print(write() or "no docs/_data in this checkout: nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
