"""A persistent cache of answered NHDPlus HR requests (stdlib ``sqlite3``).

The HR client (``hr.py``) keeps every answer the service gives, never a
failure, so a map view, a pick or a delineation asked before is answered from
disk across sessions and restarts. NHDPlus HR is a published, rarely revised
product, so an answer is kept for ``TTL_S`` (90 days). The file stays near
``MAX_BYTES`` (about 500 MB of compressed answers): past it the oldest answers
go first.

Where: ``STAF_HR_CACHE_DIR`` names the folder (``off`` turns the cache off).
Without it, a per-user cache folder (``%LOCALAPPDATA%/STAF/hr-cache`` on
Windows, ``$XDG_CACHE_HOME/staf/hr-cache`` or ``~/.cache/staf/hr-cache``
elsewhere), else the system temp folder (Posit Connect). Under pytest the
cache is off unless ``STAF_HR_CACHE_DIR`` names a folder, so no test reads an
answer another test, or a real run, left behind.

Never raises: a database error reads as a miss, and a few of them turn the
cache off for the process. Several processes may share the file (WAL mode,
a busy timeout). Stdlib only: the engine is vendored into the apps.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import zlib
from pathlib import Path
from typing import Optional

try:
    import sqlite3
except ImportError:  # a Python built without it: no cache, every request asked
    sqlite3 = None

TTL_S = 90 * 86400.0
MAX_BYTES = 500 * 1024 * 1024
FILE_NAME = "hr-answers.sqlite"
_MAX_ERRORS = 3
_EVICT_EVERY = 50           # stores between size checks
_OFF = ("", "off", "0", "none", "false", "no")

_lock = threading.Lock()
_state: dict = {"conn": None, "folder": None, "errors": 0, "stores": 0, "dead": False}
_default: list = []         # the per-user folder, resolved once


def _default_folders() -> list[Path]:
    out = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "STAF" / "hr-cache")
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        out.append(Path(xdg) / "staf" / "hr-cache")
    try:
        out.append(Path.home() / ".cache" / "staf" / "hr-cache")
    except Exception:  # noqa: BLE001 - no home folder (a service account)
        pass
    out.append(Path(tempfile.gettempdir()) / "staf-hr-cache")
    return out


def cache_folder() -> Optional[Path]:
    """The folder the cache would use now, or None when it is off."""
    env = os.environ.get("STAF_HR_CACHE_DIR")
    if env is not None:
        return None if env.strip().lower() in _OFF else Path(env.strip())
    if "PYTEST_CURRENT_TEST" in os.environ:
        return None
    if not _default:
        found = None
        for folder in _default_folders():
            try:
                folder.mkdir(parents=True, exist_ok=True)
                if os.access(folder, os.W_OK):
                    found = folder
                    break
            except OSError:
                continue
        _default.append(found)
    return _default[0]


def _close() -> None:
    conn = _state["conn"]
    _state["conn"] = None
    _state["folder"] = None
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _open(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(folder / FILE_NAME), timeout=5.0,
                           check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, "
                 "created REAL NOT NULL, size INTEGER NOT NULL, body BLOB NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS answers_created ON answers (created)")
    return conn


def _connection():
    """The open connection for the current folder (call with ``_lock`` held)."""
    if _state["dead"] or sqlite3 is None:
        return None
    folder = cache_folder()
    if folder is None:
        _close()
        return None
    if _state["conn"] is not None and _state["folder"] == folder:
        return _state["conn"]
    _close()
    try:
        _state["conn"] = _open(folder)
        _state["folder"] = folder
    except Exception:  # noqa: BLE001
        _failed()
        return None
    return _state["conn"]


def _failed() -> None:
    _state["errors"] += 1
    if _state["errors"] >= _MAX_ERRORS:
        _state["dead"] = True
        _close()


def key_for(url: str, params: dict, method: str = "GET") -> str:
    """The cache key of one request: the method, the URL and the sorted params."""
    blob = json.dumps([method.upper(), url,
                       sorted((str(k), str(v)) for k, v in (params or {}).items())],
                      separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get(key: str) -> Optional[dict]:
    """The stored answer for ``key``, or None (absent, expired, or no cache)."""
    with _lock:
        conn = _connection()
        if conn is None:
            return None
        try:
            row = conn.execute("SELECT created, body FROM answers WHERE key = ?",
                               (key,)).fetchone()
            if row is None:
                return None
            created, body = row
            if time.time() - float(created) > TTL_S:
                conn.execute("DELETE FROM answers WHERE key = ?", (key,))
                return None
        except Exception:  # noqa: BLE001
            _failed()
            return None
    try:
        return json.loads(zlib.decompress(body).decode("utf-8"))
    except Exception:  # noqa: BLE001 - a damaged row reads as a miss
        return None


def put(key: str, answer: dict) -> None:
    """Store an answer (never a failure: the caller decides what answered)."""
    try:
        body = zlib.compress(json.dumps(answer, separators=(",", ":")).encode("utf-8"), 6)
    except Exception:  # noqa: BLE001
        return
    with _lock:
        conn = _connection()
        if conn is None:
            return
        try:
            conn.execute("INSERT OR REPLACE INTO answers (key, created, size, body) "
                         "VALUES (?, ?, ?, ?)", (key, time.time(), len(body), body))
            _state["stores"] += 1
            if (_state["stores"] - 1) % _EVICT_EVERY == 0:     # the first store, then every Nth
                _evict(conn)
        except Exception:  # noqa: BLE001
            _failed()


def _evict(conn) -> None:
    """Drop expired answers, then the oldest until the total is under 90% of
    ``MAX_BYTES``."""
    conn.execute("DELETE FROM answers WHERE created < ?", (time.time() - TTL_S,))
    total = int(conn.execute("SELECT COALESCE(SUM(size), 0) FROM answers").fetchone()[0])
    if total <= MAX_BYTES:
        return
    target = int(MAX_BYTES * 0.9)
    doomed = []
    for key, size in conn.execute("SELECT key, size FROM answers ORDER BY created, rowid"):
        if total <= target:
            break
        doomed.append((key,))
        total -= int(size)
    conn.executemany("DELETE FROM answers WHERE key = ?", doomed)


def stats() -> dict:
    """``{"folder", "answers", "bytes"}`` for diagnostics (zeros when off)."""
    with _lock:
        conn = _connection()
        if conn is None:
            return {"folder": None, "answers": 0, "bytes": 0}
        try:
            n, size = conn.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) "
                                   "FROM answers").fetchone()
        except Exception:  # noqa: BLE001
            return {"folder": str(_state["folder"]), "answers": 0, "bytes": 0}
        return {"folder": str(_state["folder"]), "answers": int(n), "bytes": int(size)}


def reset() -> None:
    """Close the file and forget errors (tests, and a changed folder)."""
    with _lock:
        _close()
        _state.update(errors=0, stores=0, dead=False)
