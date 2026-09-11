"""Checkpoints, ledgers, progress heartbeat, pause/cancel control, the queue.

Every stage is idempotent: it writes to a temp path, renames on completion and
records a done marker keyed by a hash of its inputs, so re-running a finished
(unit, stage) is a no-op and a changed input (a new method version, a new
chunk definition) marks the stage stale. Long loops keep a *ledger* of done
chunk keys (HUC12s for NAS, COMID batches for StreamCat) so a pause in the
middle resumes without repeating finished requests.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config
from .paths import DataRoot, atomic_write_text, replace_with_retry

STAGE_STATUSES = ("pending", "running", "done", "failed", "stale")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def digest(*parts: Any) -> str:
    """A short hash of JSON-serializable parts (stage input identity)."""
    h = hashlib.sha256()
    for part in parts:
        h.update(json.dumps(part, sort_keys=True, default=str).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


class PauseRequested(Exception):
    """Raised by a stage between chunks when the operator asked to pause."""


class CancelRequested(Exception):
    """Raised by a stage between chunks when the operator asked to cancel."""


# ------------------------------------------------------------------ units
class FileLock:
    """A cross-process lock on a sibling ``.lock`` file (create-exclusive),
    for the checkpoint file that several worker processes update."""

    def __init__(self, path: Path, timeout_s: float = 60.0):
        self.path = path
        self.timeout_s = timeout_s
        self._fd: Optional[int] = None

    def __enter__(self):
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    try:                          # a lock older than the timeout is stale
                        if time.time() - self.path.stat().st_mtime > self.timeout_s:
                            self.path.unlink()
                            continue
                    except OSError:
                        pass
                    raise TimeoutError(f"could not lock {self.path}")
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except OSError:
            pass
        return False


class UnitStates:
    """``state/units.json``: ``{unit_key: {stage: {status, inputs, at, note}}}``.

    Several processes may update it (the per-HUC8 stages run in a pool), so
    every write merges the file's current content first, under a file lock."""

    def __init__(self, root: DataRoot):
        self.path = root.state / "units.json"
        self._file_lock = FileLock(self.path.with_suffix(".lock"))
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, dict]] = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        atomic_write_text(self.path, json.dumps(self._data, indent=1, sort_keys=True))

    def reload(self) -> None:
        with self._lock:
            self._data = self._load()

    def get(self, unit: str, stage: str) -> dict:
        with self._lock:
            return dict((self._data.get(unit) or {}).get(stage) or {})

    def unit(self, unit: str) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in (self._data.get(unit) or {}).items()}

    def all(self) -> dict[str, dict[str, dict]]:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def is_done(self, unit: str, stage: str, inputs: Optional[str] = None) -> bool:
        entry = self.get(unit, stage)
        if entry.get("status") != "done":
            return False
        return inputs is None or entry.get("inputs") == inputs

    def set(self, unit: str, stage: str, status: str, *, inputs: Optional[str] = None,
            note: str = "", **extra) -> None:
        if status not in STAGE_STATUSES:
            raise ValueError(status)
        with self._lock, self._file_lock:
            # merge with what is on disk first: the GUI and several worker
            # processes may hold the file at the same time, and a whole-file
            # save from a stale copy would drop the others' checkpoints
            fresh = self._load()
            for other_unit, stages in fresh.items():
                mine = self._data.setdefault(other_unit, {})
                for other_stage, entry in stages.items():
                    if other_stage not in mine or entry.get("at", "") >= mine[other_stage].get("at", ""):
                        mine[other_stage] = entry
            entry = self._data.setdefault(unit, {}).setdefault(stage, {})
            entry.update({"status": status, "at": now_iso(), "note": note, **extra})
            if inputs is not None:
                entry["inputs"] = inputs
            self._save()

    def mark_stale(self, stage: str, *, units: Optional[Iterable[str]] = None) -> int:
        """Flip every done ``stage`` (of ``units`` or all) to stale; returns the count."""
        n = 0
        with self._lock:
            for unit, stages in self._data.items():
                if units is not None and unit not in set(units):
                    continue
                entry = stages.get(stage)
                if entry and entry.get("status") == "done":
                    entry["status"] = "stale"
                    entry["at"] = now_iso()
                    n += 1
            if n:
                self._save()
        return n


# ---------------------------------------------------------------- ledgers
class Ledger:
    """An append-only set of done keys for one long loop (``ledgers/<name>.jsonl``)."""

    def __init__(self, root: DataRoot, name: str):
        self.path = root.ledgers / f"{name}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._done: set[str] = set()
        self._lock = threading.Lock()
        if self.path.exists():
            with open(self.path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        try:
                            self._done.add(str(json.loads(line)["key"]))
                        except (ValueError, KeyError, TypeError):
                            continue

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return str(key) in self._done

    def __len__(self) -> int:
        with self._lock:
            return len(self._done)

    def add(self, key: str, **info) -> None:
        with self._lock:
            if str(key) in self._done:
                return
            self._done.add(str(key))
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"key": str(key), "at": now_iso(), **info}) + "\n")

    def keys(self) -> list[str]:
        with self._lock:
            return sorted(self._done)

    def pending(self, keys: Iterable[str]) -> list[str]:
        with self._lock:
            return [str(k) for k in keys if str(k) not in self._done]

    def clear(self) -> None:
        with self._lock:
            self._done.clear()
            if self.path.exists():
                self.path.unlink()


# -------------------------------------------------------------- bandwidth
class Bandwidth:
    """``state/bandwidth.json``: estimated bytes the cross-section sampling
    pulled per calendar month, shared by the pool children (buffered in
    process, flushed under the file lock)."""

    def __init__(self, root: DataRoot, budget_bytes: Optional[float] = None,
                 flush_every: int = 50, flush_after_s: float = 30.0):
        self.path = root.bandwidth
        self.lock = FileLock(self.path.with_suffix(".lock"))
        self.budget_bytes = (float(config.XS_BYTE_BUDGET_GB) * 1e9 if budget_bytes is None
                             else float(budget_bytes))
        self.flush_every = flush_every
        self.flush_after_s = flush_after_s
        self._pending_bytes = 0
        self._pending_reaches = 0
        self._last_flush = time.monotonic()
        self._month_total: Optional[int] = None

    @staticmethod
    def month(now: Optional[datetime] = None) -> str:
        return (now or datetime.now()).strftime("%Y-%m")

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("months", {})
        return data

    def totals(self) -> dict:
        """``{month: {bytes, reaches}}`` as on disk plus this process's buffer."""
        data = self._read()
        months = {k: dict(v) for k, v in data["months"].items()}
        if self._pending_bytes:
            entry = months.setdefault(self.month(), {"bytes": 0, "reaches": 0})
            entry["bytes"] += self._pending_bytes
            entry["reaches"] += self._pending_reaches
        return months

    def month_bytes(self, month: Optional[str] = None) -> int:
        return int(self.totals().get(month or self.month(), {}).get("bytes", 0))

    def add(self, nbytes: int, reaches: int = 1) -> None:
        self._pending_bytes += int(nbytes)
        self._pending_reaches += int(reaches)
        if (self._pending_reaches >= self.flush_every
                or time.monotonic() - self._last_flush >= self.flush_after_s):
            self.flush()

    def flush(self) -> None:
        if not self._pending_bytes and not self._pending_reaches:
            return
        with self.lock:
            data = self._read()
            entry = data["months"].setdefault(self.month(), {"bytes": 0, "reaches": 0})
            entry["bytes"] = int(entry["bytes"]) + self._pending_bytes
            entry["reaches"] = int(entry["reaches"]) + self._pending_reaches
            data["updated"] = now_iso()
            tmp = self.path.with_name(self.path.name + ".part")
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
            replace_with_retry(tmp, self.path)          # readers never take the lock
            self._month_total = entry["bytes"]
        self._pending_bytes = 0
        self._pending_reaches = 0
        self._last_flush = time.monotonic()

    def check(self) -> None:
        """Raise ``PauseRequested`` once the month's estimate reaches the budget."""
        used = self.month_bytes()
        if used >= self.budget_bytes:
            self.flush()
            raise PauseRequested(
                f"cross-section byte budget for {self.month()} reached "
                f"({used / 1e9:.0f} of {self.budget_bytes / 1e9:.0f} GB); resumes next month, "
                f"or raise EASI_NATIONAL_XS_BYTE_BUDGET_GB")


# --------------------------------------------------------------- progress
@dataclass
class Progress:
    """The worker's heartbeat (``state/progress.json``), rewritten every ~2 s."""
    root: DataRoot
    job: str = ""                # the phase: "Chunk Virginia", "Tiles 02", "Staging", "Publish"
    unit: str = ""
    stage: str = ""
    done: int = 0
    total: int = 0
    message: str = ""
    started_at: float = field(default_factory=time.monotonic)
    stage_started_at: float = field(default_factory=time.monotonic)
    pid: int = field(default_factory=os.getpid)
    log: list[str] = field(default_factory=list)
    quiet: bool = False          # a pool child: never writes the heartbeat file
    _last_write: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def path(self) -> Path:
        return self.root.state / "progress.json"

    def begin(self, unit: str, stage: str, total: int = 0, message: str = "") -> None:
        with self._lock:
            self.unit, self.stage, self.done, self.total = unit, stage, 0, int(total)
            self.message = message
            self.stage_started_at = time.monotonic()
        self.write(force=True)

    def tick(self, done: Optional[int] = None, message: Optional[str] = None,
             total: Optional[int] = None) -> None:
        changed = False
        with self._lock:
            if done is not None:
                self.done = int(done)
            if total is not None:
                self.total = int(total)
            if message is not None and message != self.message:
                self.message = message
                changed = True                 # a new message is worth an immediate write
        self.write(force=changed)

    def say(self, line: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.log.append(f"{stamp} {line}")
            del self.log[:-200]
        print(line, flush=True)
        self.write(force=True)

    def write(self, force: bool = False) -> None:
        if self.quiet:
            return
        now = time.monotonic()
        if not force and now - self._last_write < 2.0:
            return
        with self._lock:
            elapsed = now - self.stage_started_at
            rate = (self.done / elapsed) if elapsed > 0 and self.done else None
            eta = ((self.total - self.done) / rate) if rate and self.total else None
            payload = {
                "pid": self.pid, "job": self.job, "unit": self.unit, "stage": self.stage,
                "done": self.done, "total": self.total, "message": self.message,
                "rate_per_s": rate, "eta_s": eta,
                "stage_elapsed_s": round(elapsed, 1),
                "elapsed_s": round(now - self.started_at, 1),
                "updated": now_iso(), "log": list(self.log[-60:]),
            }
            self._last_write = now
        atomic_write_text(self.path, json.dumps(payload))

    def finish(self, message: str = "idle") -> None:
        with self._lock:
            self.job, self.unit, self.stage, self.message = "", "", "", message
            self.done = self.total = 0
        self.write(force=True)


STAGE_LABELS = {
    "dem1m_index": "3DEP 1 m tile catalog",
    "dem19_index": "3DEP 1/9 arc-second catalog",
    "xs_sample": "cross-section sampling",
    "xs_derive": "cross-section derivation",
    "streamcat": "StreamCat rows", "geometry": "flowline geometry", "huc12": "HUC12 polygons",
    "wqp": "WQP nutrients", "attains": "ATTAINS units", "nas": "NAS taxa", "derive": "derive",
    "joins": "point-service joins", "score": "score", "tiles": "tiles", "stage": "staging",
    "publish": "publish", "vaa": "NHDPlus attributes", "index": "COMID index",
    "nid": "dam inventory", "huc4": "HUC4 polygons",
}


def status_line(progress: dict, *, now: Optional[str] = None) -> str:
    """One line for the panel: ``Phase · Task · Step · heartbeat``."""
    from datetime import datetime
    stamp = now or datetime.now().strftime("%H:%M:%S")
    job = progress.get("job") or ""
    stage = progress.get("stage") or ""
    unit = progress.get("unit") or ""
    if not job and not stage:
        return f"{stamp}  idle: {progress.get('message') or 'nothing running'}"
    task = STAGE_LABELS.get(stage, stage)
    done, total = int(progress.get("done") or 0), int(progress.get("total") or 0)
    step = f"{done:,} of {total:,}" if total else (f"{done:,} done" if done else "starting")
    message = str(progress.get("message") or "").strip()
    eta = progress.get("eta_s")
    eta_txt = f", about {int(eta // 60)} min left" if eta and eta > 90 else ""
    heartbeat = ""
    raw = str(progress.get("updated") or "")
    if raw:
        try:                                   # the heartbeat is UTC; show it in local time
            heartbeat = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
        except ValueError:
            heartbeat = raw[11:19]
    phase = job or unit
    if unit and unit not in phase:
        phase = f"{phase} ({unit})"
    return (f"{stamp}  Phase: {phase} · Task: {task} · Step: {step}{eta_txt}"
            + (f" · {message}" if message else "") + (f" · heartbeat {heartbeat}" if heartbeat else ""))


def read_progress(root: DataRoot) -> dict:
    try:
        return json.loads((root.state / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def pid_alive(pid: Optional[int]) -> bool:
    """Whether a process id is running (Windows: tasklist; elsewhere: signal 0)."""
    if not pid:
        return False
    if os.name == "nt":
        import subprocess
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return f'"{int(pid)}"' in out.stdout
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def worker_alive(root: DataRoot) -> bool:
    """Whether the worker that last wrote the heartbeat is still running,
    whoever started it (the panel, a terminal, a scheduled task)."""
    return pid_alive(read_progress(root).get("pid"))


def worker_state(root: DataRoot, control: Optional[dict] = None) -> dict:
    """What the panel says about the worker: ``phase`` in running / pausing /
    paused / failed / idle, whether it is safe to disconnect, and a sentence."""
    progress = read_progress(root)
    alive = pid_alive(progress.get("pid"))
    action = (control if control is not None else Control(root).read()).get("action")
    message = str(progress.get("message") or "")
    stage = progress.get("stage") or ""
    if alive and action == "pause":
        slow = " The Water Quality Portal request in flight can take up to 15 minutes." if stage == "wqp" else ""
        return {"phase": "pausing", "safe": False,
                "text": "Pausing: finishing the request in flight, then stopping." + slow}
    if alive and action == "cancel":
        return {"phase": "cancelling", "safe": False, "text": "Cancelling after the request in flight."}
    if alive:
        return {"phase": "running", "safe": False,
                "text": "Running. Pause to stop cleanly; a shutdown now only repeats the request in flight."}
    if message == "paused":
        return {"phase": "paused", "safe": True,
                "text": "Paused. Safe to disconnect or shut down; Resume continues from the checkpoints."}
    if message.startswith("failed"):
        return {"phase": "failed", "safe": True,
                "text": "Stopped after a failure. Safe to shut down; Resume retries the job from its checkpoints."}
    if message == "cancelled":
        return {"phase": "cancelled", "safe": True, "text": "Cancelled. Safe to shut down."}
    return {"phase": "idle", "safe": True, "text": "Idle. Safe to disconnect or shut down."}


# ---------------------------------------------------------------- control
class Control:
    """``state/control.json``: the operator's pause/cancel request."""

    def __init__(self, root: DataRoot):
        self.path = root.state / "control.json"

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def request(self, action: str) -> None:
        atomic_write_text(self.path, json.dumps({"action": action, "at": now_iso()}))

    def clear(self) -> None:
        atomic_write_text(self.path, json.dumps({}))

    def check(self) -> None:
        """Raise ``PauseRequested`` / ``CancelRequested`` when the operator asked."""
        action = self.read().get("action")
        if action == "pause":
            raise PauseRequested()
        if action == "cancel":
            raise CancelRequested()


# ------------------------------------------------------------------ queue
class Queue:
    """``state/queue.json``: an ordered list of ``{unit, stage, args}`` items."""

    def __init__(self, root: DataRoot):
        self.path = root.state / "queue.json"

    def read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return list(data.get("items") or [])
        except (OSError, ValueError, AttributeError):
            return []

    def write(self, items: list[dict]) -> None:
        atomic_write_text(self.path, json.dumps({"items": items, "at": now_iso()}, indent=1))

    @staticmethod
    def _key(item: dict) -> str:
        return json.dumps(item, sort_keys=True, default=str)

    def append(self, items: Iterable[dict]) -> None:
        """Append items not already queued (identity = the whole item)."""
        current = self.read()
        seen = {self._key(i) for i in current}
        for item in items:
            key = self._key(item)
            if key not in seen:
                current.append(item)
                seen.add(key)
        self.write(current)

    def pop_front(self) -> Optional[dict]:
        items = self.read()
        if not items:
            return None
        head, rest = items[0], items[1:]
        self.write(rest)
        return head

    def clear(self) -> None:
        self.write([])


# ------------------------------------------------------------------ rates
class Rates:
    """``state/rates.json``: measured seconds per item per stage (ETA model)."""

    def __init__(self, root: DataRoot):
        self.path = root.state / "rates.json"

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def record(self, stage: str, items: int, seconds: float) -> None:
        if items <= 0 or seconds <= 0:
            return
        data = self.read()
        entry = data.setdefault(stage, {"items": 0, "seconds": 0.0, "samples": 0})
        entry["items"] += int(items)
        entry["seconds"] += float(seconds)
        entry["samples"] += 1
        entry["per_item_s"] = entry["seconds"] / entry["items"]
        entry["at"] = now_iso()
        atomic_write_text(self.path, json.dumps(data, indent=1, sort_keys=True))

    def per_item(self, stage: str, default: float) -> float:
        entry = self.read().get(stage) or {}
        return float(entry.get("per_item_s") or default)
