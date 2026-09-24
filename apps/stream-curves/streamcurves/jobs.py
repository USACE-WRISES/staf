"""A bounded job runner for campaigns: many independent jobs, a few at a time, resumable.

A job is a spec (anything JSON can hold) of a kind; its id is the SHA-256 of the canonical
``{kind, spec, argv, target, env, cwd}``, so the same work always has the same id and a job
whose completion record names that id is never run again. A caller puts into the spec the
digest of every input the work reads (code, data, packages), so a changed input is another
job. Every job runs in its own process, with the thread caps of ``THREAD_CAPS`` and the
environment it names, and gets its own output folder; a job that writes elsewhere (a stage job
writes its region's run folder) binds that to its inputs itself. One coordinator (the process
calling ``run``, holding the campaign's lock) is the only writer of the campaign's index and
summary; a caller that merges or summarizes after the run holds the lock around all of it
(``with lock(campaign) as held: run(..., lock_held=held)``). A failure is recorded and never
stops the other jobs; an interrupted campaign resumes by running ``run`` again: completed jobs
are skipped, anything else starts over in a clean output folder.

A lock is written whole (a temporary file linked into place, never a half-written file) and
names its process by pid, host, start and a nonce. It is taken over only when that process has
certainly ended; a lock that cannot be read is busy, never stale; the takeover moves aside only
the record it judged stale, so two processes never both hold one lock; and a process removes
only a lock that still carries its own nonce.

Layout under a campaign folder::

    campaign.json                    what the campaign is (written once)
    lock.json                        the running coordinator (pid, host, start, nonce); removed at the end
    index.jsonl                      one line per job event, append-only
    summary.json                     every job's last state, rewritten as jobs finish
    jobs/<id>/job.json               the job's kind and spec
    jobs/<id>/out/                   what the job writes (cleaned before every start)
    jobs/<id>/log.txt                the job's output
    jobs/<id>/complete.json          written atomically when it succeeded: result and the
                                     SHA-256 of every file under out/
    jobs/<id>/failed.json            the last failure

Two kinds: ``command`` (an argv; ``{out}`` and ``{job}`` are replaced by the job's output and
job folders) and ``python`` (``module:function`` called in a fresh interpreter as
``fn(spec, out_dir) -> dict``; the dict is the job's result).
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

THREAD_CAPS = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
               "NUMEXPR_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1"}
APP_ROOT = Path(__file__).resolve().parents[1]


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=str).encode("utf-8")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tree_digest(folder: Path) -> dict:
    """SHA-256 of every file under ``folder``, by relative path."""
    folder = Path(folder)
    if not folder.is_dir():
        return {}
    return {str(p.relative_to(folder)).replace("\\", "/"): sha_file(p)
            for p in sorted(folder.rglob("*")) if p.is_file()}


def tree_fingerprint(root: Path, folders) -> str:
    """SHA-256 over every file under ``folders`` of ``root`` (relative path and bytes, caches
    skipped): a job's code and data inputs in one digest, for its spec."""
    h = hashlib.sha256()
    for sub in folders:
        for p in sorted((Path(root) / sub).rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            h.update(p.relative_to(root).as_posix().encode("utf-8"))
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def peak_memory_mb() -> Optional[float]:
    """This process's peak working set (resident set) in MB, or None where it cannot be read.
    It counts native buffers (Arrow's pool) that ``tracemalloc`` does not see."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class _Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            c = _Counters()
            c.cb = ctypes.sizeof(_Counters)
            k32 = ctypes.WinDLL("kernel32")
            k32.GetCurrentProcess.restype = wintypes.HANDLE      # a 64-bit pseudo handle
            psapi = ctypes.WinDLL("psapi")
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return None
            return round(c.PeakWorkingSetSize / 1e6, 1)
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e3, 1)
    except Exception:  # noqa: BLE001 - a measurement, never a failure
        return None


def seed_for(job_id: str, stream: str = "default") -> int:
    """A seed that belongs to a job (and a named stream within it), never to its position in
    a queue or to the worker that runs it."""
    return int(hashlib.sha256(f"{job_id}|{stream}".encode("utf-8")).hexdigest()[:8], 16)


@dataclass
class Job:
    kind: str                                   # "command" | "python"
    spec: dict
    argv: Optional[list] = None                 # command jobs
    target: Optional[str] = None                # python jobs: "module:function"
    env: dict = field(default_factory=dict)     # added to the worker's environment
    cwd: Optional[str] = None
    label: str = ""
    timeout: Optional[float] = None

    @property
    def id(self) -> str:
        return hashlib.sha256(canonical({"kind": self.kind, "spec": self.spec, "argv": self.argv,
                                         "target": self.target, "env": self.env,
                                         "cwd": self.cwd})).hexdigest()[:16]

    def describe(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label, "spec": self.spec,
                "argv": self.argv, "target": self.target, "env": self.env}


def _write_atomic(path: Path, obj) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.part")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class CampaignBusy(RuntimeError):
    """Another coordinator holds the campaign."""


def _pid_alive(pid: int) -> bool:
    """True while process ``pid`` runs (never signals it: on Windows ``os.kill`` would end it)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32")
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.OpenProcess(0x1000, False, int(pid))     # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_started(pid: int) -> Optional[int]:
    """When process ``pid`` started, as an opaque number that stays the same for the whole life
    of the process (None where it cannot be read). A lock records it for its own process, so a
    later process that happens to get the same pid is never taken for the lock's holder."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32")
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            k32.GetProcessTimes.restype = wintypes.BOOL
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            k32.CloseHandle.restype = wintypes.BOOL
            handle = k32.OpenProcess(0x1000, False, int(pid))     # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return None
            try:
                times = [wintypes.FILETIME() for _ in range(4)]
                if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                    return None
                return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            finally:
                k32.CloseHandle(handle)
        import psutil
        return int(psutil.Process(int(pid)).create_time() * 1_000_000)
    except Exception:  # noqa: BLE001 - a missing start time only weakens the check to the pid
        return None


LOCK = "lock.json"


@dataclass(frozen=True)
class Held:
    """A lock this process holds: its file and the nonce that proves the file is still ours."""
    path: Path
    nonce: str


def _retrying(fn, *, tries: int = 5, wait: float = 0.05):
    """``fn()``, tried again while Windows refuses it because another process has the file open
    for a moment (a PermissionError); the last refusal is raised."""
    for attempt in range(tries):
        try:
            return fn()
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(wait)


def _busy(lock: Path, held: dict) -> CampaignBusy:
    return CampaignBusy(f"Another run is using {lock.parent} (process {held.get('pid')} on "
                        f"{held.get('host')}, since {held.get('startedAt')}). Wait for it to finish. "
                        f"If no run is using the folder, delete {lock}.")


def _read_lock(lock: Path) -> Optional[dict]:
    """The lock's record, or None when there is no lock. A lock that cannot be read now (another
    process has it open, or a crash left it empty) is never judged stale: it reads as busy."""
    try:
        text = _retrying(lambda: lock.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CampaignBusy(f"The lock {lock} cannot be read ({exc}). If no run is using "
                           f"{lock.parent}, delete it.") from exc
    try:
        rec = json.loads(text)
    except ValueError:
        rec = None
    if not isinstance(rec, dict) or not isinstance(rec.get("pid"), int) or isinstance(rec.get("pid"), bool):
        raise CampaignBusy(f"The lock {lock} cannot be read. If no run is using {lock.parent}, "
                           "delete it.")
    return rec


def _holder_gone(held: dict) -> bool:
    """True only when the lock's process has certainly ended: same host, and its pid is not
    running or now belongs to a process that started at another time."""
    import socket
    if held.get("host") not in (None, socket.gethostname()):
        return False
    pid = int(held["pid"])
    if not _pid_alive(pid):
        return True
    recorded = held.get("processStarted")
    if recorded is None:
        return False
    now = _process_started(pid)
    return now is not None and now != recorded


def _place(lock: Path, text: str) -> bool:
    """Put ``text`` at ``lock`` whole, only when no lock is there: written to a temporary file
    and linked into place, so a lock is never seen half written. False when a lock exists."""
    tmp = lock.with_name(f"{lock.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        try:
            os.link(tmp, lock)
            return True
        except FileExistsError:
            return False
        except PermissionError:
            return False                  # a lock that is being deleted still holds the name
        except OSError:
            pass                          # a file system without hard links
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        return True
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _same_record(a: Optional[dict], b: dict) -> bool:
    return isinstance(a, dict) and all(a.get(k) == b.get(k) for k in ("pid", "host", "startedAt", "nonce"))


def _take_over(lock: Path, judged: dict) -> bool:
    """Move aside the lock judged stale, and only that one. True when it is gone. When another
    process took it over first (its fresh lock now holds the name), that lock is put back and
    False says so: two processes never both hold one lock."""
    aside = lock.with_name(f"lock.stale.{os.getpid()}.{time.time_ns()}")
    try:
        _retrying(lambda: os.replace(lock, aside))
    except FileNotFoundError:
        return True                       # already gone: whoever comes next creates the lock
    except PermissionError:
        return False
    try:
        moved = json.loads(aside.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        moved = None
    if _same_record(moved, judged):
        try:
            aside.unlink()
        except OSError:
            pass
        return True
    try:
        os.link(aside, lock)              # a live lock was moved: give it back
    except OSError:
        pass
    try:
        aside.unlink()
    except OSError:
        pass
    return False


def acquire(campaign: Path) -> Held:
    """Take the folder's lock (``lock.json``, written whole and only when absent). A lock whose
    process has ended is taken over; a live or unreadable one raises :class:`CampaignBusy`."""
    import socket
    campaign = Path(campaign)
    campaign.mkdir(parents=True, exist_ok=True)
    lock = campaign / LOCK
    body = {"pid": os.getpid(), "host": socket.gethostname(), "startedAt": _now(),
            "nonce": uuid.uuid4().hex, "processStarted": _process_started(os.getpid())}
    text = json.dumps(body)
    for _ in range(5):
        if _place(lock, text):
            try:
                mine = _read_lock(lock)
            except CampaignBusy:
                mine = None
            if not _same_record(mine, body):
                raise _busy(lock, mine or {})
            return Held(lock, body["nonce"])
        held = _read_lock(lock)
        if held is None:
            time.sleep(0.05)              # released between the two steps: try again
            continue
        if not _holder_gone(held):
            raise _busy(lock, held)
        if not _take_over(lock, held):
            raise _busy(lock, _read_lock(lock) or held)
    raise CampaignBusy(f"The lock {lock} could not be taken. If no run is using {campaign}, delete it.")


def holds(held: Held) -> bool:
    """True while ``held``'s lock file is still this process's."""
    try:
        rec = json.loads(_retrying(lambda: Path(held.path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return False
    return isinstance(rec, dict) and rec.get("nonce") == held.nonce


def release(held: Held) -> None:
    """Remove the lock if it is still this process's; never a lock another process holds."""
    if not holds(held):
        return
    try:
        _retrying(lambda: Path(held.path).unlink())
    except FileNotFoundError:
        pass


@contextlib.contextmanager
def lock(campaign: Path):
    """``with jobs.lock(folder) as held:`` hold the folder's lock for the whole block, and pass
    ``lock_held=held`` to :func:`run` so the run, the merge and the summary are one campaign."""
    held = acquire(campaign)
    try:
        yield held
    finally:
        release(held)


def completed(campaign: Path, job: Job) -> Optional[dict]:
    """The job's completion record when it names this job and its outputs are intact."""
    d = Path(campaign) / "jobs" / job.id
    p = d / "complete.json"
    if not p.is_file():
        return None
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if rec.get("id") != job.id or rec.get("spec") != json.loads(canonical(job.spec)):
        return None
    if tree_digest(d / "out") != rec.get("outputs"):
        return None
    return rec


def _worker_env(job: Job, base: Optional[dict]) -> dict:
    env = dict(os.environ if base is None else base)
    env.update(THREAD_CAPS)
    env.update({k: str(v) for k, v in (job.env or {}).items()})
    env["STREAMCURVES_JOB_ID"] = job.id
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _run_one(campaign: Path, job: Job, base_env: Optional[dict]) -> dict:
    d = Path(campaign) / "jobs" / job.id
    out = d / "out"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for stale in ("complete.json", "failed.json", "result.json"):
        (d / stale).unlink(missing_ok=True)
    _write_atomic(d / "job.json", job.describe())
    env = _worker_env(job, base_env)
    if job.kind == "command":
        argv = [str(a).replace("{out}", str(out)).replace("{job}", str(d)) for a in job.argv or []]
    elif job.kind == "python":
        argv = [sys.executable, "-B", "-m", "streamcurves.jobs", "run-python", str(d)]
    else:
        raise ValueError(f"unknown job kind {job.kind!r}")
    t0 = time.perf_counter()
    with (d / "log.txt").open("w", encoding="utf-8") as log:
        proc = subprocess.run(argv, cwd=job.cwd or (str(APP_ROOT) if job.kind == "python" else None),
                              env=env, stdout=log, stderr=subprocess.STDOUT, timeout=job.timeout)
    seconds = round(time.perf_counter() - t0, 2)
    if proc.returncode != 0:
        tail = (d / "log.txt").read_text(encoding="utf-8", errors="replace")[-2000:]
        rec = {"id": job.id, "label": job.label, "exit": proc.returncode, "seconds": seconds,
               "failedAt": _now(), "tail": tail}
        _write_atomic(d / "failed.json", rec)
        return {"state": "failed", **rec}
    result = None
    if (d / "result.json").is_file():
        result = json.loads((d / "result.json").read_text(encoding="utf-8"))
    resources = None
    if (d / "resources.json").is_file():
        resources = json.loads((d / "resources.json").read_text(encoding="utf-8"))
    rec = {"id": job.id, "label": job.label, "spec": json.loads(canonical(job.spec)),
           "outputs": tree_digest(out), "result": result, "seconds": seconds, "finishedAt": _now(),
           "resources": resources}
    _write_atomic(d / "complete.json", rec)
    return {"state": "completed", **rec}


def run(jobs: list[Job], campaign: Path, *, workers: int = 2, meta: Optional[dict] = None,
        base_env: Optional[dict] = None, stop: Optional[threading.Event] = None,
        on_event: Optional[Callable[[dict], None]] = None, max_starts: Optional[int] = None,
        retries: int = 1, lock_held: Optional[Held] = None) -> dict:
    """Run every job not already completed, ``workers`` at a time. Returns the summary.
    ``stop`` (or ``max_starts``) ends the campaign early: no new job starts, running jobs
    finish; a later ``run`` resumes where this one stopped. A job that fails is started
    again up to ``retries`` times (a worker process can die of a transient native fault);
    every attempt is in the index. The campaign's lock is taken for the run, or ``lock_held``
    is the caller's (:func:`lock`) when it also merges or summarizes afterwards."""
    campaign = Path(campaign)
    (campaign / "jobs").mkdir(parents=True, exist_ok=True)
    ids = [j.id for j in jobs]
    if len(set(ids)) != len(ids):
        raise ValueError("two jobs have the same spec")
    kw = dict(workers=workers, meta=meta, base_env=base_env, stop=stop, on_event=on_event,
              max_starts=max_starts, retries=retries)
    if lock_held is not None:
        if Path(lock_held.path).resolve() != (campaign / LOCK).resolve():
            raise ValueError(f"the lock held is {lock_held.path}, not this campaign's")
        if not holds(lock_held):
            raise CampaignBusy(f"The lock {lock_held.path} is no longer this run's. Start the run again.")
        return _run_locked(jobs, campaign, **kw)
    with lock(campaign):
        return _run_locked(jobs, campaign, **kw)


def _run_locked(jobs: list[Job], campaign: Path, *, workers, meta, base_env, stop, on_event,
                max_starts, retries) -> dict:
    if not (campaign / "campaign.json").is_file():
        _write_atomic(campaign / "campaign.json", {"created": _now(), "meta": meta or {},
                                                   "jobs": len(jobs)})
    index = campaign / "index.jsonl"
    lock = threading.Lock()
    states: dict[str, dict] = {}

    def event(kind: str, job: Job, **extra) -> None:
        rec = {"at": _now(), "event": kind, "id": job.id, "label": job.label, **extra}
        with lock:
            with index.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
            states[job.id] = {"label": job.label, "state": kind, **{k: v for k, v in extra.items()
                                                                    if k in ("seconds", "exit", "peakMemoryMB")}}
        if on_event:
            on_event(rec)

    pending = []
    for job in jobs:
        if completed(campaign, job) is not None:
            event("skipped", job, reason="completed with the same spec and outputs")
        else:
            pending.append(job)
    started = 0
    attempts: dict[str, int] = {}
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        running = {}
        queue = list(pending)
        while queue or running:
            while queue and len(running) < max(1, int(workers)):
                if (stop is not None and stop.is_set()) or (max_starts is not None and started >= max_starts):
                    queue = []
                    break
                job = queue.pop(0)
                event("started", job)
                running[pool.submit(_run_one, campaign, job, base_env)] = job
                started += 1
            if not running:
                break
            done, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for fut in done:
                job = running.pop(fut)
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001 - the runner itself failed for this job
                    res = {"state": "failed", "exit": None, "tail": f"{type(exc).__name__}: {exc}"}
                    _write_atomic(Path(campaign) / "jobs" / job.id / "failed.json",
                                  {"id": job.id, "error": res["tail"], "failedAt": _now()})
                attempts[job.id] = attempts.get(job.id, 0) + 1
                if res["state"] == "failed" and attempts[job.id] <= int(retries):
                    event("retrying", job, seconds=res.get("seconds"), exit=res.get("exit"))
                    queue.insert(0, job)
                    continue
                event(res["state"], job, seconds=res.get("seconds"), exit=res.get("exit"),
                      peakMemoryMB=(res.get("resources") or {}).get("peakMemoryMB"))
    for job in jobs:
        states.setdefault(job.id, {"label": job.label, "state": "not-started"})
    summary = {"updated": _now(), "workers": int(workers), "seconds": round(time.perf_counter() - t0, 2),
               "counts": {s: sum(1 for v in states.values() if v["state"] == s)
                          for s in ("completed", "skipped", "failed", "not-started")},
               "jobs": states}
    _write_atomic(campaign / "summary.json", summary)
    return summary


# --------------------------------------------------------------------------- #
# the worker side of a python job
# --------------------------------------------------------------------------- #
def _run_python(job_dir: Path) -> int:
    doc = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    module, _, name = str(doc["target"]).partition(":")
    fn = getattr(importlib.import_module(module), name)
    result = fn(doc["spec"], job_dir / "out")
    _write_atomic(job_dir / "result.json", result if result is not None else {})
    _write_atomic(job_dir / "resources.json", {"peakMemoryMB": peak_memory_mb()})
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "run-python":
        return _run_python(Path(argv[2]))
    print("usage: python -m streamcurves.jobs run-python <job folder>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))


__all__ = ["THREAD_CAPS", "Job", "run", "completed", "seed_for", "tree_digest", "canonical",
           "acquire", "release", "holds", "lock", "Held", "LOCK", "CampaignBusy", "tree_fingerprint",
           "peak_memory_mb"]
