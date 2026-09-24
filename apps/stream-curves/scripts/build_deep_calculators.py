"""Build the Excel calculator of published assessment versions (backfill).

A calculator is generated when a version is published
(``library.publish_version``). This script builds it for versions published
before that existed, rebuilds one after a generator fix, and checks that every
recorded workbook is still present and current. It writes only
``vN/calculator.xlsx`` and an appended record in the assessment's
``artifacts.json``. A version's bundle, session, provenance and ``meta.json`` are
never touched, so no fingerprint moves.

    py -3.12 scripts/build_deep_calculators.py --all
    py -3.12 scripts/build_deep_calculators.py --assessment northeastern-highlands --version 6
    py -3.12 scripts/build_deep_calculators.py --all --reissue      # after a generator change
    py -3.12 scripts/build_deep_calculators.py --all --check        # exit 1 when any is missing or stale

Writing the canonical ``apps/library`` needs the same gate a publish does
(``STAF_LIBRARY_PUBLISH=1``). ``--library-root`` points at another root.
After a backfill, run ``apps/deep/scripts/bake_library_into_deep.py`` so DEEP
ships the workbooks, and commit ``apps/library/**`` with ``apps/deep/**`` together.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from streamcurves import library as lib  # noqa: E402


def targets(assessment: str | None, version: int | None) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    ids = [lib.slugify(assessment)] if assessment else [
        str(a.get("assessmentId")) for a in lib.list_assessments() if lib.entry_type(a) == "deep"]
    for aid in ids:
        manifest = lib.read_manifest(aid)
        if not manifest:
            raise SystemExit(f"no assessment {aid!r} in {lib.library_root()}")
        versions = [int(v.get("version")) for v in manifest.get("versions") or []]
        if version is not None:
            if int(version) not in versions:
                raise SystemExit(f"{aid} has no version {version}")
            versions = [int(version)]
        out += [(aid, v) for v in versions]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    pick = ap.add_mutually_exclusive_group(required=True)
    pick.add_argument("--all", action="store_true", help="every version of every assessment")
    pick.add_argument("--assessment", help="one assessment id")
    ap.add_argument("--version", type=int, default=None, help="with --assessment: one version")
    ap.add_argument("--reissue", action="store_true",
                    help="rebuild even when the recorded workbook is present and current")
    ap.add_argument("--check", action="store_true", help="report only; write nothing")
    ap.add_argument("--library-root", default=None,
                    help="another library root (default: the canonical apps/library)")
    ap.add_argument("--actor", default=os.environ.get("STAF_LIBRARY_MAINTAINER", ""),
                    help="name recorded with each workbook")
    a = ap.parse_args(argv)
    if a.version is not None and not a.assessment:
        ap.error("--version needs --assessment")
    if a.library_root:
        os.environ["STAF_LIBRARY_ROOT"] = str(Path(a.library_root).resolve())

    todo = targets(a.assessment, a.version)
    if a.check:
        bad = 0
        for aid, v in todo:
            state = lib.calculator_state(aid, v)
            bad += state != "present"
            print(f"  {aid} v{v}: {state}")
        print(f"[calculators] {len(todo) - bad} of {len(todo)} present and current")
        return 1 if bad else 0

    if lib.is_canonical_root():
        reason = lib.publish_gate_reason(a.actor)
        if reason:
            print(f"[calculators] blocked: {reason}")
            return 2
    elif not lib.writable():
        print(f"[calculators] {lib.library_root()} is not writable")
        return 2

    built = skipped = failed = 0
    for aid, v in todo:
        try:
            record = lib.write_calculator(
                aid, v, actor=a.actor,
                note="Reissued." if a.reissue else "Backfilled for a version published "
                                                   "before calculators were generated.",
                reissue=a.reissue)
        except Exception as exc:  # noqa: BLE001 - one bad bundle must not end the run
            failed += 1
            print(f"  {aid} v{v}: FAILED ({exc})")
            continue
        if record is None:
            skipped += 1
            print(f"  {aid} v{v}: present, left alone")
        else:
            built += 1
            print(f"  {aid} v{v}: built {record['bytes']:,} bytes {record['sha256'][:19]}")
    lib._regenerate_catalog()
    print(f"[calculators] built {built}, left alone {skipped}, failed {failed} "
          f"in {lib.library_root()}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
