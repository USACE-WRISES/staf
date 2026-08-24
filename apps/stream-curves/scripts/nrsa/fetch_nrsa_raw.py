"""Download the public NRSA data and metadata files, and pin what was fetched.

EPA publishes NRSA as one CSV per indicator per survey cycle, with a companion
metadata file. There is no API: the NARS Data Download Tool is a web UI with no
documented endpoint, so the direct file URLs are the mechanism. Those URLs live
in ``data/nrsa/reference/dataset_index_<cycle>.csv``, which is tracked, so this
script needs no separate manifest.

Raw CSVs are never committed. They land in a working directory (default
``notes/DEEP_Working/nrsa_raw/``, which is gitignored) and what gets tracked is
``data/nrsa/sources.lock.json``: per file, the sha256, the byte count, EPA's
``Last-Modified``, and when we fetched it. That is what makes a later EPA
republication visible instead of silently absorbed.

    py -3.12 scripts/nrsa/fetch_nrsa_raw.py                 # fetch everything missing
    py -3.12 scripts/nrsa/fetch_nrsa_raw.py --cycle 2324    # one survey cycle
    py -3.12 scripts/nrsa/fetch_nrsa_raw.py --verify        # no download, check for drift
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parents[1]
REFERENCE_DIR = APP_ROOT / "data" / "nrsa" / "reference"
LOCK_PATH = APP_ROOT / "data" / "nrsa" / "sources.lock.json"
DEFAULT_RAW = REPO_ROOT / "notes" / "DEEP_Working" / "nrsa_raw"

CYCLES = ("1314", "1819", "2324")
USER_AGENT = "STAF-StreamCurves/NRSA-archive-builder (+https://github.com/gtmenichino/staf)"
TIMEOUT = 180
MAX_WORKERS = 4


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def load_index(cycles) -> pd.DataFrame:
    frames = []
    for cycle in cycles:
        path = REFERENCE_DIR / f"dataset_index_{cycle}.csv"
        if not path.exists():
            raise SystemExit(
                f"missing {path}. Run scripts/nrsa/import_reference_workbooks.py first."
            )
        frames.append(pd.read_csv(path))
    index = pd.concat(frames, ignore_index=True)
    index["cycle"] = index["cycle"].astype(str)
    return index


def planned_files(index: pd.DataFrame, raw_dir: Path) -> list[dict]:
    """One entry per file to hold: both the data and the metadata of each dataset."""
    out: list[dict] = []
    for _, row in index.iterrows():
        for kind, url_col, name_col in (
            ("data", "epa_data_url", "data_file"),
            ("metadata", "epa_metadata_url", "metadata_file"),
        ):
            url = row.get(url_col)
            if not isinstance(url, str) or not url.startswith("http"):
                continue
            filename = str(row.get(name_col) or url.rsplit("/", 1)[-1])
            out.append({
                "cycle": str(row["cycle"]),
                "dataset_id": str(row["dataset_id"]),
                "indicator": str(row.get("indicator") or ""),
                "prefix": str(row.get("convenience_prefix") or ""),
                "kind": kind,
                "url": url,
                "path": raw_dir / str(row["cycle"]) / filename,
            })
    return out


def head(url: str) -> dict:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return {
                "status": response.status,
                "bytes": int(response.headers.get("Content-Length") or 0),
                "last_modified": response.headers.get("Last-Modified", ""),
            }
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "bytes": 0, "last_modified": ""}
    except Exception as exc:  # noqa: BLE001
        return {"status": f"ERROR {type(exc).__name__}", "bytes": 0, "last_modified": ""}


def download(entry: dict) -> dict:
    """Fetch one file to a temporary name, then move it into place."""
    target: Path = entry["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(entry["url"], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            last_modified = response.headers.get("Last-Modified", "")
            with partial.open("wb") as handle:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    handle.write(chunk)
        partial.replace(target)
    except Exception as exc:  # noqa: BLE001
        partial.unlink(missing_ok=True)
        return {**entry, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        **entry,
        "ok": True,
        "bytes": target.stat().st_size,
        "sha256": sha256_of(target),
        "last_modified": last_modified,
        "fetched_at": _now(),
    }


def load_lock() -> dict:
    if not LOCK_PATH.exists():
        return {"schemaVersion": 1, "files": {}}
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def lock_key(entry: dict) -> str:
    return f"{entry['cycle']}/{entry['dataset_id']}/{entry['kind']}"


def write_lock(lock: dict) -> None:
    lock["files"] = dict(sorted(lock["files"].items()))
    lock["updatedAt"] = _now()
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(json.dumps(lock, indent=2, sort_keys=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_verify(entries: list[dict]) -> int:
    """HEAD every URL and compare with the lock. No downloads, no writes."""
    lock = load_lock()
    if not lock["files"]:
        print("no lock file yet: nothing to verify. Run a fetch first.")
        return 1
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        live = list(pool.map(lambda e: (e, head(e["url"])), entries))

    drift, unreachable = [], []
    for entry, info in live:
        recorded = lock["files"].get(lock_key(entry))
        if info["status"] != 200:
            unreachable.append((entry, info["status"]))
            continue
        if recorded is None:
            drift.append((entry, "not in the lock"))
            continue
        if info["bytes"] and int(recorded.get("bytes", 0)) != info["bytes"]:
            drift.append((entry, f"size {recorded.get('bytes')} -> {info['bytes']}"))
        elif info["last_modified"] and recorded.get("last_modified") \
                and recorded["last_modified"] != info["last_modified"]:
            drift.append((entry, f"modified {recorded['last_modified']} -> {info['last_modified']}"))

    print(f"checked {len(entries)} URLs against {len(lock['files'])} locked files")
    for entry, why in unreachable:
        print(f"  UNREACHABLE {why}  {entry['cycle']} {entry['dataset_id']} {entry['kind']}")
    for entry, why in drift:
        print(f"  CHANGED     {entry['cycle']} {entry['dataset_id']} {entry['kind']}: {why}")
    if not drift and not unreachable:
        print("  every file matches the lock")
        return 0
    return 2


def cmd_fetch(entries: list[dict], *, force: bool) -> int:
    lock = load_lock()
    todo, skipped = [], 0
    for entry in entries:
        recorded = lock["files"].get(lock_key(entry))
        if not force and entry["path"].exists() and recorded:
            if sha256_of(entry["path"]) == recorded.get("sha256"):
                skipped += 1
                continue
        todo.append(entry)

    print(f"{len(entries)} files, {skipped} already local and matching the lock, "
          f"{len(todo)} to fetch")
    if not todo:
        return 0

    failures = []
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for result in pool.map(download, todo):
            done += 1
            tag = f"{result['cycle']} {result['dataset_id']:<22} {result['kind']:<8}"
            if not result.get("ok"):
                failures.append(result)
                print(f"  [{done}/{len(todo)}] FAILED {tag} {result['error']}")
                continue
            print(f"  [{done}/{len(todo)}] {tag} {result['bytes'] / 1e6:8.2f} MB")
            lock["files"][lock_key(result)] = {
                "cycle": result["cycle"],
                "dataset_id": result["dataset_id"],
                "indicator": result["indicator"],
                "kind": result["kind"],
                "url": result["url"],
                "file": result["path"].name,
                "bytes": result["bytes"],
                "sha256": result["sha256"],
                "last_modified": result["last_modified"],
                "fetched_at": result["fetched_at"],
            }

    write_lock(lock)
    total = sum(f["bytes"] for f in lock["files"].values())
    print(f"\nlock now holds {len(lock['files'])} files, {total / 1e6:.1f} MB on disk")
    print(f"wrote {LOCK_PATH.relative_to(APP_ROOT).as_posix()}")
    if failures:
        print(f"\n{len(failures)} downloads failed; rerun to retry just those.")
        return 2
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycle", action="append", choices=CYCLES,
                    help="limit to one survey cycle (repeatable)")
    ap.add_argument("--dataset", action="append", help="limit to one dataset id (repeatable)")
    ap.add_argument("--kind", choices=("data", "metadata"), help="limit to data or metadata files")
    ap.add_argument("--out", type=Path, default=DEFAULT_RAW, help="working directory for raw CSVs")
    ap.add_argument("--verify", action="store_true",
                    help="HEAD every URL and report drift against the lock; downloads nothing")
    ap.add_argument("--force", action="store_true", help="refetch even when the sha256 matches")
    args = ap.parse_args(argv)

    index = load_index(args.cycle or CYCLES)
    if args.dataset:
        index = index[index["dataset_id"].astype(str).isin(args.dataset)]
    entries = planned_files(index, args.out)
    if args.kind:
        entries = [e for e in entries if e["kind"] == args.kind]
    if not entries:
        print("nothing selected")
        return 1

    if args.verify:
        return cmd_verify(entries)
    return cmd_fetch(entries, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
