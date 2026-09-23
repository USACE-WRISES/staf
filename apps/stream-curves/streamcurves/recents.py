"""Recent projects for the start page (ported from HYPE's hype_app/recents.py).

Persisted per user at <data root>/recent_projects.json (desktop_env.data_root). Every helper is
deliberately non-fatal: a broken or read-only data root must never fail opening a project or
the start page; worst case the list is empty.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .desktop_env import data_root

_FILE = "recent_projects.json"
MAX_RECENTS = 15


def _path(file: str = _FILE) -> Path:
    return data_root() / file


def load(file: str = _FILE) -> list[dict]:
    """Recents newest first, without entries whose file no longer exists.

    Each entry: {"path": str, "name": str, "last_opened": iso-utc str}. Pruning is in memory
    only (the file is rewritten on the next touch), so a drive that is briefly unavailable
    does not evict its projects for good.
    """
    try:
        raw = json.loads(_path(file).read_text(encoding="utf-8"))
        items = raw.get("projects", [])
    except (OSError, ValueError, AttributeError):
        return []
    out: list[dict] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        p = str(it.get("path") or "")
        if not p:
            continue
        try:
            if not Path(p).is_file():
                continue
        except OSError:
            continue
        out.append({"path": p, "name": str(it.get("name") or Path(p).stem),
                    "last_opened": str(it.get("last_opened") or "")})
    return out[:MAX_RECENTS]


def _write(items: list[dict], file: str = _FILE) -> None:
    """Atomic same-folder temp file + os.replace, so a crash mid-write cannot corrupt it."""
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".recents-", suffix=".tmp", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"projects": items}, fh, indent=2)
        os.replace(tmp, _path(file))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def touch(path: str | os.PathLike[str], file: str = _FILE, *, name: str | None = None) -> None:
    """Record an open or a create: dedupe by normalized path, put it first, cap, write.

    `name` is the project's display name, which need not match the file. The dedupe key stays
    the PATH, so renaming a project rewrites its row rather than adding a second one. Silently
    a no-op on any IO failure.
    """
    try:
        p = Path(path).resolve()
        key = os.path.normcase(str(p))
        entry = {"path": str(p), "name": str(name or "").strip() or p.stem,
                 "last_opened": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        kept = [it for it in load(file) if os.path.normcase(it["path"]) != key]
        _write([entry, *kept][:MAX_RECENTS], file)
    except Exception:  # noqa: BLE001 - never fatal
        pass


def forget(path: str | os.PathLike[str], file: str = _FILE) -> None:
    """Drop *path* from the list. Only edits the list; never touches the project."""
    try:
        key = os.path.normcase(str(Path(path).resolve()))
        _write([it for it in load(file) if os.path.normcase(it["path"]) != key], file)
    except Exception:  # noqa: BLE001 - never fatal
        pass


__all__ = ["MAX_RECENTS", "load", "touch", "forget"]
