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
    """Merge the library's latest bundles into ``deep-assessments.json`` (+ bundles/).

    Returns a summary dict: ``added`` / ``updated`` assessment ids, ``libraryCount``,
    ``total`` in the registry after baking.
    """
    out = Path(out) if out else DEFAULT_OUT
    if library_root is not None:
        os.environ["STAF_LIBRARY_ROOT"] = str(library_root)

    reg_path = out / "deep-assessments.json"
    doc = (
        _load(reg_path)
        if reg_path.is_file()
        else {"schemaVersion": 1, "tier": "detailed", "assessments": []}
    )
    baked = list(doc.get("assessments") or [])
    bundles = deep_library.latest_bundles()

    order = [a.get("assessmentId") for a in baked]
    by_id = {a.get("assessmentId"): a for a in baked}
    added: list[str] = []
    updated: list[str] = []
    for bundle in bundles:
        aid = bundle.get("assessmentId")
        if aid is None:
            continue
        if aid in by_id:
            updated.append(aid)
        else:
            added.append(aid)
            order.append(aid)
        by_id[aid] = bundle

    doc["assessments"] = [by_id[i] for i in order if i in by_id]
    _write(reg_path, doc)

    # Standalone upload-shaped copies (parity with build_deep_data.py).
    for bundle in bundles:
        aid = bundle.get("assessmentId")
        if aid:
            _write(
                out / "bundles" / f"{aid}.deep.json",
                {"schemaVersion": 1, "tier": "detailed", **bundle},
            )

    return {
        "added": added,
        "updated": updated,
        "libraryCount": len(bundles),
        "total": len(doc["assessments"]),
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
        f"Baked {result['libraryCount']} library assessment(s) into "
        f"{args.out / 'deep-assessments.json'}"
    )
    if result["added"]:
        print(f"  added:   {', '.join(result['added'])}")
    if result["updated"]:
        print(f"  updated: {', '.join(result['updated'])}")
    print(f"  registry now has {result['total']} assessment(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
