"""National WQP nutrient results, one calendar month at a time, through the
Water Quality Portal's WQX3 service (the same request the operator ran by
hand: every stream site in the country, the five TN and TP characteristic
names the app queries, the ``fullPhysChem`` profile, NWIS and STORET).

Each month lands in ``national/wqp/monthly/wqx3_<YYYY-MM>.csv`` with a
ledger beside it (``ledger.json``: status, rows, bytes, attempts, the last
error). A month counts as done only when the transfer ended normally and the
file does not end with the portal's trailer ("ERROR: INCOMPLETE DATA ...
PLEASE RETRY THE REQUEST."); a cut connection, an overloaded answer (HTTP
500 "Unable to get the headers") or the trailer put the month back in the
queue with a growing wait; a stream still running at the one-hour cap is
abandoned for that pass and the window retried whole later, never split.
When every month is done the files are combined
into ``national/wqp/wqp_results.parquet`` (every column kept as text, rows
deduplicated on the result identifier) and the CSVs removed.
"""
from __future__ import annotations

import calendar
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from .. import config, http
from ..paths import DataRoot, atomic_write_text
from ..state import CancelRequested, Control, PauseRequested, Progress, now_iso

STAGE = "wqp_monthly"
URL = "https://waterqualitydata.us/wqx3/Result/search"
CHARACTERISTICS = (
    "Phosphorus",
    "Nitrogen",
    "Total Nitrogen, mixed forms",
    "Total Phosphorus, mixed forms",
    "Total Nitrogen, mixed forms (NH3), (NH4), organic, (NO2) and (NO3)",
)
PROVIDERS = ("NWIS", "STORET")
TRAILER = "ERROR: INCOMPLETE DATA"
#: waits before a month is retried, by attempt (seconds); the last repeats
BACKOFF_S = (60, 120, 300, 600, 1200, 1800)
ID_COLUMN = "Result_MeasureIdentifier"


# --------------------------------------------------------------- windows
def months(start: str = config.WQP_MONTHLY_START, end: Optional[str] = None) -> list[str]:
    """``YYYY-MM`` from ``start`` through ``end`` (default: the current month)."""
    end = end or datetime.now().strftime("%Y-%m")
    y, m = (int(v) for v in start.split("-"))
    ey, em = (int(v) for v in end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def month_bounds(month: str) -> tuple[date, date]:
    """First and last day of a calendar month."""
    y, m = (int(v) for v in month.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def window(month: str) -> tuple[str, str]:
    """The portal's ``MM-DD-YYYY`` bounds of a calendar month, both inclusive."""
    lo, hi = month_bounds(month)
    return _portal_date(lo), _portal_date(hi)


def _portal_date(day: date) -> str:
    return f"{day.month:02d}-{day.day:02d}-{day.year:04d}"


def params_for_window(lo: date, hi: date) -> list[tuple[str, str]]:
    out = [("countrycode", "US"), ("siteType", "Stream")]
    out += [("characteristicName", c) for c in CHARACTERISTICS]
    out += [("startDateLo", _portal_date(lo)), ("startDateHi", _portal_date(hi)), ("mimeType", "csv"),
            ("dataProfile", "fullPhysChem")]
    out += [("providers", p) for p in PROVIDERS]
    return out


def params_for(month: str) -> list[tuple[str, str]]:
    return params_for_window(*month_bounds(month))


# ---------------------------------------------------------------- files
def folder(root: DataRoot) -> Path:
    return root.national / "wqp" / "monthly"


def month_path(root: DataRoot, month: str) -> Path:
    return folder(root) / f"wqx3_{month}.csv"


def combined_path(root: DataRoot) -> Path:
    return root.national / "wqp" / "wqp_results.parquet"


def ledger_path(root: DataRoot) -> Path:
    return folder(root) / "ledger.json"


def read_ledger(root: DataRoot) -> dict:
    try:
        return json.loads(ledger_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"months": {}}


_LEDGER_LOCK = threading.Lock()


def record(root: DataRoot, month: str, **fields) -> dict:
    """Merge ``fields`` into the month's ledger entry (thread-safe) and return the ledger."""
    with _LEDGER_LOCK:
        ledger = read_ledger(root)
        entry = ledger.setdefault("months", {}).setdefault(month, {"attempts": 0})
        entry.update(fields)
        entry["at"] = now_iso()
        ledger["updated"] = now_iso()
        atomic_write_text(ledger_path(root), json.dumps(ledger, indent=1, sort_keys=True))
        return ledger


def summary(root: DataRoot, wanted: Optional[list[str]] = None) -> dict:
    """Done / pending counts, rows and bytes for the panel."""
    wanted = wanted or months()
    ledger = read_ledger(root).get("months", {})
    done = [m for m in wanted if ledger.get(m, {}).get("status") == "done"]
    rows = sum(int(ledger[m].get("rows") or 0) for m in done)
    size = sum(int(ledger[m].get("bytes") or 0) for m in done)
    failing = [m for m in wanted if m not in done and ledger.get(m, {}).get("attempts")]
    return {"total": len(wanted), "done": len(done), "failing": len(failing), "rows": rows, "bytes": size,
            "combined": combined_path(root).exists(), "last_error": next(
                (ledger[m].get("last_error") for m in reversed(wanted) if ledger.get(m, {}).get("last_error")), None)}


# ------------------------------------------------------------- download
def plain_session():
    """A session without urllib3's own retries: the shared one would retry an
    overloaded 500 four times behind our back and report a RetryError instead
    of the portal's message; this stage schedules its own retries."""
    import requests
    from requests.adapters import HTTPAdapter
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=0, pool_maxsize=4)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers["User-Agent"] = http.USER_AGENT
    return s


def _last_line(path: Path, tail_bytes: int = 4096) -> str:
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - tail_bytes))
        tail = fh.read().decode("utf-8", "replace")
    lines = [line for line in tail.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _count_rows(path: Path) -> int:
    n = 0
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            n += block.count(b"\n")
    return max(0, n - 1)                                  # the header


def piece_path(dest: Path, lo: date, hi: date) -> Path:
    """Where a finished window of the month waits until the month is stitched."""
    return dest.with_name(f"{dest.stem}.piece_{lo:%Y%m%d}_{hi:%Y%m%d}.csv")


def download_window(lo: date, hi: date, dest: Path, *, session=None, stall_s: float = 300.0,
                    max_s: float = 3600.0, should_stop: Optional[Callable[[], bool]] = None) -> dict:
    """One date window to ``dest``: ``{"status": done | cut | incomplete | slow | failed,
    "bytes", "elapsed_s", "error"}``. ``cut`` is a transfer that broke after
    its first bytes (the portal's connection limit), ``incomplete`` the
    portal's own trailer, ``slow`` a stream still running at ``max_s`` (kept
    whole for a later pass, never split); nothing but a complete file ever
    lands at ``dest``."""
    session = session or plain_session()
    tmp = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    nbytes = 0
    try:
        with session.get(URL, params=params_for_window(lo, hi), stream=True, timeout=(30, stall_s),
                         headers={"User-Agent": http.USER_AGENT}) as resp:
            if resp.status_code != 200:
                text = resp.text[:300].strip()
                return {"status": "failed", "bytes": 0, "elapsed_s": time.monotonic() - started,
                        "error": f"HTTP {resp.status_code}: {text}"}
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    nbytes += len(chunk)
                    if time.monotonic() - started > max_s:
                        raise TimeoutError(f"still streaming after {max_s:.0f} s")
                    if should_stop is not None and should_stop():
                        raise PauseRequested("stop requested")
    except (PauseRequested, CancelRequested):
        tmp.unlink(missing_ok=True)
        raise
    except TimeoutError as exc:          # our own cap: the portal was merely slow, retry whole later
        tmp.unlink(missing_ok=True)
        return {"status": "slow", "bytes": nbytes, "elapsed_s": time.monotonic() - started,
                "error": f"{exc} after {nbytes:,} bytes; retried whole in a later pass"}
    except Exception as exc:  # noqa: BLE001 - one window, recorded, retried or split
        tmp.unlink(missing_ok=True)
        return {"status": "cut" if nbytes else "failed", "bytes": nbytes, "elapsed_s": time.monotonic() - started,
                "error": f"{type(exc).__name__}: {str(exc)[:160]} after {nbytes:,} bytes"}
    elapsed = time.monotonic() - started
    if nbytes == 0:
        tmp.unlink(missing_ok=True)
        return {"status": "failed", "bytes": 0, "elapsed_s": elapsed, "error": "empty answer"}
    if TRAILER in _last_line(tmp).upper():
        tmp.unlink(missing_ok=True)
        return {"status": "incomplete", "bytes": nbytes, "elapsed_s": elapsed,
                "error": "the portal's incomplete-data trailer"}
    tmp.replace(dest)
    return {"status": "done", "bytes": nbytes, "elapsed_s": elapsed, "error": None}


def _split_marker(dest: Path, lo: date, hi: date) -> Path:
    """Records that the window was cut and split, so a retry resumes below it."""
    return dest.with_name(f"{dest.stem}.split_{lo:%Y%m%d}_{hi:%Y%m%d}")


def _wait(seconds: float, sleep, should_stop) -> None:
    remaining = float(seconds)
    while remaining > 0:
        if should_stop is not None and should_stop():
            raise PauseRequested("stop requested")
        step = min(5.0, remaining)
        sleep(step)
        remaining -= step


def download_month(month: str, dest: Path, *, session=None, stall_s: float = 300.0, max_s: float = 3600.0,
                   should_stop: Optional[Callable[[], bool]] = None, min_days: int = 1,
                   piece_tries: int = 3, sleep=time.sleep) -> dict:
    """The month as one request when the portal finishes it, else as the
    pieces it can finish: a cut or incomplete window is halved, down to
    ``min_days``; finished pieces stay on disk across retries; the month file
    is stitched from them (one header) and the pieces removed. Returns
    ``{"status": done | failed | cut | incomplete, "rows", "bytes", "elapsed_s",
    "error", "pieces"}``; anything but done leaves the finished pieces behind."""
    session = session or plain_session()
    dest.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    first, last = month_bounds(month)
    pending: list[tuple[date, date]] = [(first, last)]
    done: list[tuple[date, Path]] = []
    nbytes = 0
    while pending:
        lo, hi = pending.pop(0)
        path = piece_path(dest, lo, hi)
        days = (hi - lo).days + 1
        if path.exists():
            done.append((lo, path))
            continue
        if days > min_days and (_split_marker(dest, lo, hi).exists() or _piece_inside(dest, lo, hi)):
            pending[:0] = _halves(lo, hi)              # an earlier attempt already split this window
            continue
        for attempt in range(max(1, piece_tries)):
            result = download_window(lo, hi, path, session=session, stall_s=stall_s, max_s=max_s,
                                     should_stop=should_stop)
            nbytes += int(result["bytes"])
            overloaded = result["status"] == "failed" and "HTTP 5" in str(result["error"])
            if not overloaded or attempt == piece_tries - 1:
                break
            _wait(20 * (attempt + 1), sleep, should_stop)  # the portal asks to wait before trying again
        if result["status"] == "done":
            done.append((lo, path))
            continue
        if result["status"] in ("cut", "incomplete") and days > min_days:
            _split_marker(dest, lo, hi).touch()
            pending[:0] = _halves(lo, hi)
            continue
        return {"status": result["status"], "rows": 0, "bytes": nbytes, "elapsed_s": time.monotonic() - started,
                "error": f"{lo} to {hi}: {result['error']}", "pieces": len(done)}
    done.sort()
    tmp = dest.with_name(dest.name + ".part")
    rows = _stitch([p for _lo, p in done], tmp)
    tmp.replace(dest)
    for _lo, p in done:
        p.unlink(missing_ok=True)
    for marker in dest.parent.glob(f"{dest.stem}.split_*"):
        marker.unlink(missing_ok=True)
    return {"status": "done", "rows": rows, "bytes": nbytes, "elapsed_s": time.monotonic() - started,
            "error": None, "pieces": len(done)}


def _halves(lo: date, hi: date) -> list[tuple[date, date]]:
    """The window split in two; the first half holds ``days // 2`` days."""
    mid = lo + timedelta(days=((hi - lo).days + 1) // 2 - 1)
    return [(lo, mid), (mid + timedelta(days=1), hi)]


def _piece_inside(dest: Path, lo: date, hi: date) -> bool:
    """A finished piece of an earlier attempt lies strictly inside the window."""
    prefix = f"{dest.stem}.piece_"
    for path in dest.parent.glob(f"{dest.stem}.piece_*_*.csv"):
        a, b = path.stem[len(prefix):].split("_")
        plo = date(int(a[:4]), int(a[4:6]), int(a[6:8]))
        phi = date(int(b[:4]), int(b[4:6]), int(b[6:8]))
        if lo <= plo and phi <= hi and (plo, phi) != (lo, hi):
            return True
    return False


def _stitch(paths: list[Path], out: Path) -> int:
    """Concatenate the pieces (the header once) and count the data lines."""
    rows = 0
    with open(out, "wb") as fh:
        for i, path in enumerate(paths):
            with open(path, "rb") as src:
                header = src.readline()
                if i == 0:
                    fh.write(header if header.endswith(b"\n") else header + b"\n")
                tail = b""
                for line in src:
                    if not line.strip():                # blank lines are not rows
                        continue
                    fh.write(line)
                    tail = line
                    rows += 1
                if tail and not tail.endswith(b"\n"):
                    fh.write(b"\n")
    return rows


def _attempt(root: DataRoot, month: str, session, control: Control, sleep=time.sleep) -> dict:
    """One pool job: mark the month running when its download starts, then download it."""
    entry = read_ledger(root).get("months", {}).get(month, {})
    record(root, month, status="running", attempts=int(entry.get("attempts") or 0) + 1)
    return download_month(month, month_path(root, month), session=session, sleep=sleep,
                          should_stop=lambda: control.read().get("action") in ("pause", "cancel"))


def run_wqp_monthly(root: DataRoot, progress: Progress, control: Control, *, workers: int = 3,
                    wanted: Optional[list[str]] = None, session_factory=None, sleep=time.sleep,
                    max_rounds: Optional[int] = None, combine_when_done: bool = True) -> dict:
    """Download every month not yet done, ``workers`` at a time, retrying with
    a growing wait until all are done (or ``max_rounds``); then combine unless
    ``combine_when_done`` is off (a partial campaign keeps its CSVs)."""
    wanted = wanted or months()
    ledger = read_ledger(root).get("months", {})
    pending = [m for m in wanted if ledger.get(m, {}).get("status") != "done"
               or not month_path(root, m).exists()]
    rounds = 0
    progress.begin("national", STAGE, total=len(wanted),
                   message=f"WQP monthly: {len(wanted) - len(pending)} of {len(wanted)} months done")
    progress.tick(done=len(wanted) - len(pending))
    while pending:
        control.check()
        rounds += 1
        if max_rounds is not None and rounds > max_rounds:
            break
        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {}
            for month in pending:
                session = session_factory() if session_factory else plain_session()
                futures[pool.submit(_attempt, root, month, session, control, sleep)] = month
            for future in as_completed(futures):
                month = futures[future]
                try:
                    result = future.result()
                except (PauseRequested, CancelRequested):
                    for other in futures.values():
                        record(root, other, status="pending")
                    raise
                results[month] = result
                record(root, month, status=result["status"] if result["status"] == "done" else "pending",
                       rows=result["rows"], bytes=result["bytes"], elapsed_s=round(result["elapsed_s"], 1),
                       last_error=result["error"], pieces=result.get("pieces", 0))
                if result["status"] == "done":
                    progress.say(f"WQP {month}: {result['rows']:,} rows, {result['bytes'] / 1e6:.1f} MB "
                                 f"in {result['elapsed_s']:.0f} s, {result.get('pieces', 1)} pieces")
                else:
                    progress.say(f"WQP {month}: {result['status']} ({result['error']})")
        pending = [m for m in pending if results.get(m, {}).get("status") != "done"]
        done = len(wanted) - len(pending)
        progress.tick(done=done, message=f"WQP monthly: {done} of {len(wanted)} months done, "
                                         f"{len(pending)} to retry")
        if pending:
            attempts = max(int(read_ledger(root).get("months", {}).get(m, {}).get("attempts") or 1)
                           for m in pending)
            wait = BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1]
            progress.say(f"WQP monthly: waiting {wait} s before retrying {len(pending)} months")
            remaining = float(wait)
            while remaining > 0:                    # in short steps so a pause lands within seconds
                control.check()
                step = min(5.0, remaining)
                sleep(step)
                remaining -= step
    result = summary(root, wanted)
    if combine_when_done and result["done"] == result["total"] and not result["combined"]:
        combine(root, wanted, progress)
        result = summary(root, wanted)
    return result


# -------------------------------------------------------------- combine
def combine(root: DataRoot, wanted: Optional[list[str]] = None, progress: Optional[Progress] = None) -> Path:
    """Every month's CSV into one parquet (all columns text, zstd, dictionary
    encoded; duplicates on the result identifier dropped), then the CSVs go."""
    import pyarrow as pa
    import pyarrow.csv as pcsv
    import pyarrow.parquet as pq
    wanted = wanted or months()
    out = combined_path(root)
    tmp = out.with_name(out.name + ".part")
    writer = None
    seen: set[str] = set()
    total = kept = 0
    for month in wanted:
        path = month_path(root, month)
        if not path.exists():
            raise RuntimeError(f"WQP month {month} is missing: {path}")
        # the file handles are ours, closed by the with blocks: a reader left to
        # the garbage collector keeps the CSV open and Windows refuses the unlink
        with pa.OSFile(str(path), "rb") as probe_file:
            header = list(pcsv.open_csv(probe_file, parse_options=pcsv.ParseOptions(newlines_in_values=True)).schema.names)
        types = {name: pa.string() for name in header}
        with pa.OSFile(str(path), "rb") as source:
            reader = pcsv.open_csv(source, read_options=pcsv.ReadOptions(block_size=1 << 24),
                                   parse_options=pcsv.ParseOptions(newlines_in_values=True),
                                   convert_options=pcsv.ConvertOptions(column_types=types, strings_can_be_null=True))
            for batch in _batches(reader):
                table = pa.Table.from_batches([batch])
                table = table.append_column("source_month", pa.array([month] * table.num_rows, pa.string()))
                total += table.num_rows
                if ID_COLUMN in table.column_names:
                    ids = table.column(ID_COLUMN).to_pylist()
                    mask = []
                    for value in ids:
                        key = value or ""
                        fresh = key not in seen
                        if fresh and key:
                            seen.add(key)
                        mask.append(fresh)
                    table = table.filter(pa.array(mask, pa.bool_()))
                if writer is None:
                    writer = pq.ParquetWriter(tmp, table.schema, compression="zstd", use_dictionary=True)
                elif not table.schema.equals(writer.schema):
                    table = table.select(writer.schema.names) if set(writer.schema.names) <= set(table.column_names) \
                        else _conform(table, writer.schema)
                writer.write_table(table)
                kept += table.num_rows
        if progress is not None:
            progress.say(f"WQP combine: {month} folded in, {kept:,} rows so far")
    if writer is None:
        raise RuntimeError("no WQP months to combine")
    writer.close()
    tmp.replace(out)
    check = pq.read_metadata(out).num_rows
    if check != kept:
        raise RuntimeError(f"combined parquet holds {check:,} rows, expected {kept:,}")
    for month in wanted:
        month_path(root, month).unlink(missing_ok=True)
    record_path = folder(root) / "combined.json"
    atomic_write_text(record_path, json.dumps({"rows_read": total, "rows_kept": kept, "months": len(wanted),
                                               "at": now_iso(), "parquet": str(out)}, indent=1))
    if progress is not None:
        progress.say(f"WQP combined: {kept:,} of {total:,} rows kept ({total - kept:,} duplicates), "
                     f"{out.stat().st_size / 1e6:.0f} MB; monthly CSVs removed")
    return out


def _batches(reader):
    """Iterate a streaming CSV reader and close it afterwards."""
    try:
        for batch in reader:
            yield batch
    finally:
        reader.close()


def _conform(table, schema):
    """A month whose header differs: missing columns become null, extras are dropped."""
    import pyarrow as pa
    columns = []
    for field in schema:
        if field.name in table.column_names:
            columns.append(table.column(field.name))
        else:
            columns.append(pa.nulls(table.num_rows, field.type))
    return pa.Table.from_arrays(columns, schema=schema)
