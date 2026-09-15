"""Atomic study outputs and content-bound checkpoints."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha(path: Path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def info(path: Path):
    path = Path(path)
    before = path.stat()
    value = {"path": str(path.resolve()), "bytes": before.st_size,
             "mtime_ns": before.st_mtime_ns, "sha256": sha(path)}
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"Input changed while reading: {path}")
    return value


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == raw:
        return path
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(raw, encoding="utf-8")
    os.replace(temp, path)
    return path


def write_parquet(path: Path, table):
    import pyarrow as pa
    import pyarrow.parquet as pq
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    if not isinstance(table, pa.Table):
        table = pa.Table.from_pandas(table, preserve_index=False)
    pq.write_table(table, temp, compression="zstd")
    os.replace(temp, path)
    return path


def fingerprint(paths, extra=None):
    inputs = [info(Path(p)) for p in sorted(set(map(str, paths)))]
    raw = json.dumps({"inputs": inputs, "extra": extra}, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest(), inputs


def safe_study(root: Path, study: Path):
    root, study = Path(root).resolve(), Path(study).resolve()
    base = root / "review/alternative-studies"
    if study.parent != base or not study.name or study.is_symlink():
        raise ValueError("Study must be an immediate child of review/alternative-studies")
    return study


def output_files(study: Path, paths):
    result = []
    for path in paths:
        path = Path(path)
        if path.is_file():
            result.append({**info(path), "relative_path": path.relative_to(study).as_posix()})
        elif path.is_dir():
            result.extend(output_files(study, sorted(p for p in path.rglob("*") if p.is_file() and not p.name.endswith(".tmp"))))
    return result
