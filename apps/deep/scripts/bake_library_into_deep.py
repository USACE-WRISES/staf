"""Bake the shared assessment library's latest bundles into DEEP's data/ registry.

The 8 state-SQT assessments come from ``build_deep_data.py`` (resolved out of the STAF
metric library). This script folds in the completed assessments published to
``apps/library/`` so the *cloud* DEEP — which does not ship ``apps/library`` — still lists
them. Only each assessment's latest version is baked (older versions stay in the library
for reference). Idempotent: re-running with the same library yields the same output.

Run it after publishing a library version, then commit ``apps/deep/data/``. StreamCurves'
publish action runs it automatically on local/desktop.

Usage:
    py scripts/bake_library_into_deep.py [--out data] [--library-root PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEEP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = DEEP_ROOT / "data"

if str(DEEP_ROOT) not in sys.path:
    sys.path.insert(0, str(DEEP_ROOT))
from deep import library as deep_library  # noqa: E402


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def bake(out: Path | None = None, library_root: Path | None = None) -> dict:
    """Rewrite ``deep-assessments.json`` (v2) authoritatively from the shared library.

    The registry becomes ``{schemaVersion: 2, tier, libraryCatalog, assessments}`` where
    ``assessments`` carries **one record per eligible (id, version)** (preliminary or
    certified), each stamped ``assessmentRef``/``version``/``lifecycle``. Per-version
    bundle files ``bundles/<id>@v<N>.deep.json`` plus a default ``bundles/<id>.deep.json``
    (the catalog's defaultVersion) are written; stale bundle files are removed. Ordering is
    deterministic (id, then version). Idempotent: same library -> same output.

    Returns a summary dict: ``assessments`` (id list), ``records`` (count), ``libraryCount``.
    """
    out = Path(out) if out else DEFAULT_OUT
    if library_root is not None:
        os.environ["STAF_LIBRARY_ROOT"] = str(library_root)

    bundles = deep_library.all_eligible_bundles()
    pointers = deep_library.catalog_pointers()

    # Deterministic ordering: by assessmentId, then version.
    records = sorted(bundles, key=lambda b: (b.get("assessmentId") or "", int(b.get("version") or 0)))
    library_catalog = {aid: pointers[aid] for aid in sorted(pointers)}

    doc = {
        "schemaVersion": 2,
        "tier": "detailed",
        "generatedFrom": "apps/library",
        "libraryCatalog": library_catalog,
        "assessments": records,
    }
    reg_path = out / "deep-assessments.json"
    _write(reg_path, doc)

    # Rewrite bundles/: clear stale .deep.json, then write per-version + default copies.
    bundles_dir = out / "bundles"
    if bundles_dir.is_dir():
        for old in bundles_dir.glob("*.deep.json"):
            old.unlink()
    default_version = {aid: p.get("defaultVersion") for aid, p in pointers.items()}
    for bundle in records:
        aid = bundle.get("assessmentId")
        ver = int(bundle.get("version") or 0)
        if not aid or ver < 1:
            continue
        payload = {"schemaVersion": 1, "tier": "detailed", **bundle}
        _write(bundles_dir / f"{aid}@v{ver}.deep.json", payload)
        if ver == default_version.get(aid):
            _write(bundles_dir / f"{aid}.deep.json", payload)

    assessment_ids = sorted({b.get("assessmentId") for b in records if b.get("assessmentId")})
    return {
        "assessments": assessment_ids,
        "records": len(records),
        "libraryCount": len(bundles),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Bake apps/library/ latest bundles into DEEP's data/ registry."
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="DEEP data/ directory.")
    parser.add_argument(
        "--library-root",
        type=Path,
        default=None,
        help="Override the library root (default: the apps/library sibling).",
    )
    args = parser.parse_args(argv)
    result = bake(out=args.out, library_root=args.library_root)
    print(
        f"Baked {result['records']} version record(s) from "
        f"{len(result['assessments'])} assessment(s) into "
        f"{args.out / 'deep-assessments.json'}"
    )
    if result["assessments"]:
        print(f"  assessments: {', '.join(result['assessments'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
