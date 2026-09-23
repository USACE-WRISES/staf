"""Check a staged apps payload against the byte records it ships (stdlib only).

StreamCurves fingerprints some files over their raw bytes, and two committed records pin those
bytes: data/nrsa_provenance.json (the legacy NRSA files) and data/nrsa/manifest.json (the
multi-cycle archive). A payload whose bytes differ from the records would hash differently from
the maintainer's checkout, so the build fails instead of shipping it. It also checks that
sentinel config files are LF: a subtree `git archive` ignores the root .gitattributes and
converts text to CRLF on a Windows machine with core.autocrlf=true.

    python desktop/scripts/check_payload_records.py --stage desktop/build/apps-work/stage
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

#: Text files that must be LF in the payload, as in every checkout.
LF_SENTINELS = (
    "stream-curves/config/metric_map.yaml",
    "stream-curves/config/methodology/methodology_config.yaml",
    "stream-curves/app.py",
)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _records(stage: Path):
    """(path, sha256, bytes) for every file the two committed records describe."""
    data = stage / "stream-curves" / "data"
    prov = json.loads((data / "nrsa_provenance.json").read_text(encoding="utf-8"))
    for f in prov.get("files") or []:
        yield data / f["file"], f["sha256"], f.get("bytes")
    manifest_path = data / "nrsa" / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for rel, rec in (manifest.get("files") or {}).items():
            yield data / "nrsa" / rel, rec["sha256"], rec.get("bytes")


def check(stage: Path) -> list[str]:
    problems = []
    n = 0
    for path, sha, size in _records(stage):
        n += 1
        rel = path.relative_to(stage).as_posix()
        if not path.is_file():
            problems.append(f"{rel}: missing")
            continue
        if size is not None and path.stat().st_size != size:
            problems.append(f"{rel}: {path.stat().st_size} bytes, recorded {size}")
        elif _sha(path) != sha:
            problems.append(f"{rel}: sha256 differs from the record")
    for rel in LF_SENTINELS:
        path = stage / rel
        if path.is_file() and b"\r\n" in path.read_bytes():
            problems.append(f"{rel}: CRLF line endings (the archive did not apply .gitattributes)")
    print(f"[records] checked {n} recorded files and {len(LF_SENTINELS)} LF sentinels")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stage", type=Path, required=True,
                    help="the staged payload root (holds stream-curves/ and library/)")
    a = ap.parse_args(argv)
    problems = check(a.stage)
    for p in problems:
        print(f"[records] {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
