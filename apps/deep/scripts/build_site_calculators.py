"""Write the STAF site's list of DEEP calculators (docs/_data/deep_calculators.json).

One entry per regional assessment DEEP shows: its default version's workbook
(``www/calculators/<id>.xlsx``, which the bake rewrites for every new default), named by its
ecoregion and labeled with its version and status. The site's Apply STAF page renders the list as
a picker whose downloads come raw from main, so a download is always the current calculator even
if the list lags behind a publish.

DEEP's bake (``bake_library_into_deep.py``) runs this after every bake of DEEP's own folders, so a
library publish refreshes it; ``tests/test_site_calculators.py`` fails when it is stale.

``--check`` also holds the list to the library itself (owner, 2026-10-08: the site follows the
library): each entry's version and status must be what ``apps/library/catalog.json`` says DEEP
shows by default, so a push that carries the library without the rebaked list goes red, here and
in the ``deep-site-list`` workflow. It needs only the standard library.

Usage:
    py scripts/build_site_calculators.py           # rewrite docs/_data/deep_calculators.json
    py scripts/build_site_calculators.py --check   # exit 1 when it is stale or disagrees with
                                                   # the library, write nothing
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
LIBRARY_CATALOG = REPO / "apps" / "library" / "catalog.json"
#: Library assessments DEEP never lists, the state SQT transcriptions (StreamCurves' gallery keeps
#: the same list, streamcurves.gallery.DEEP_HIDDEN_SUFFIXES).
HIDDEN_SUFFIXES = ("-sqt-adapted",)

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


def library_defaults() -> dict | None:
    """``{assessment id: (version, status label)}``: every DEEP assessment the library says DEEP
    opens by default (``deepDefaultVersion``, ``deepDefaultStatus``), or None when this checkout
    has no library. An assessment DEEP shows no version of has version 0 and is left out."""
    if not LIBRARY_CATALOG.is_file():
        return None
    doc = json.loads(LIBRARY_CATALOG.read_text(encoding="utf-8"))
    out = {}
    for entry in doc.get("assessments") or []:
        aid = str(entry.get("assessmentId") or "")
        version = int(entry.get("deepDefaultVersion") or 0)
        if (not aid or str(entry.get("assessmentType") or "deep") != "deep"
                or aid.endswith(HIDDEN_SUFFIXES) or version < 1):
            continue
        out[aid] = (version, session.status_label(entry.get("deepDefaultStatus")))
    return out


def library_mismatches(items=None) -> list:
    """How the site's list differs from the library: an assessment it leaves out, one the library
    no longer shows in DEEP, or another version or status. Empty when the two agree or when this
    checkout has no library."""
    expected = library_defaults()
    if expected is None:
        return []
    if items is None:
        items = json.loads(OUT.read_text(encoding="utf-8"))["calculators"] if OUT.is_file() else []
    listed = dict((i["id"], (int(i["version"]), i["status"])) for i in items)
    out = []
    for aid in sorted(set(expected) | set(listed)):
        want, have = expected.get(aid), listed.get(aid)
        if have is None:
            out.append(f"{aid}: the library shows v{want[0]} ({want[1]}); the site list leaves it out")
        elif want is None:
            out.append(f"{aid}: the site list offers v{have[0]} ({have[1]}); the library shows none in DEEP")
        elif want != have:
            out.append(f"{aid}: the site list says v{have[0]} ({have[1]}), the library v{want[0]} ({want[1]})")
    return out


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
        mismatches = library_mismatches()
        if mismatches:
            print(f"{OUT.relative_to(REPO).as_posix()} disagrees with "
                  f"{LIBRARY_CATALOG.relative_to(REPO).as_posix()}:")
            for line in mismatches:
                print("  " + line)
            print("Rebake DEEP (py apps/deep/scripts/bake_library_into_deep.py), then commit "
                  "apps/deep/data, apps/deep/www/calculators and docs/_data/deep_calculators.json "
                  "with the library.")
            return 1
        print("site calculators: current, and they match the library")
        return 0
    print(write() or "no docs/_data in this checkout: nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
