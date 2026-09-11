"""Water Quality Portal nutrient results for a chunk, by station-id batches.

Why not by bounding box: the portal materializes a whole result before it
sends its first byte, and its gateway drops any request that has sent no
headers after about three minutes; a dense one-degree cell trickles for an
hour. A station list, on the other hand, is an indexed query that answers in
seconds, and results asked for by explicit station ids stream at tens of
thousands of rows a minute once materialized. So, per chunk:

1. list every stream station per grid cell over the chunk bbox (which
   already carries the 10-mile buffer), one part per cell, resumable; the
   list carries no date or characteristic filter because those make the
   portal scan results (minutes per cell) while the bare box answers in
   seconds;
2. pull results in batches of station ids sized to answer well inside the
   limit: the batch grows while answers are quick, shrinks when they are
   slow, and a batch that is cut is halved (a single station that is cut is
   fetched in date windows), resumable by index range in the station list.

The output files are the ones the bbox stage writes (``wqp_tn.parquet``,
``wqp_tp.parquet``, normalized with the app's rules) plus
``wqp_stations.parquet``; the joins never know which stage ran.
"""
from __future__ import annotations

import csv
import io
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from .. import config, http
from ..paths import DataRoot
from ..state import (CancelRequested, Control, Ledger, PauseRequested, Progress,
                     UnitStates)
from ..units import Chunk
from . import common
from .wqp import PARAMS, STAGE, _csv_text, _quarters, _Timeout, normalize_rows, start_date

CELL_DEG = 1.0
CONTROL_POLL_S = 5.0
#: pauses before retrying a transient error (a 5xx, a reset); a cut request is
#: never retried whole, it is split at once
RETRY_DELAYS_S = (20.0, 60.0)
MAX_CELL_SPLIT_DEPTH = 3
#: stations per results request: the first one of a run, the bounds, and the
#: answer times that grow or shrink the next batch
BATCH_START = 25
BATCH_MIN, BATCH_MAX = 4, 400
QUICK_S, SLOW_S = 30.0, 100.0
GROW, SHRINK = 1.5, 0.5
#: a single station that is cut is asked for in this many date windows, up to
#: this many times over
DATE_WINDOWS = 4
MAX_WINDOW_DEPTH = 3
_DATE = "%m-%d-%Y"
#: the portal throttles bursts (HTTP 429): requests are paced per host, and a
#: throttle answer parks every connection for an escalating cooldown
WQP_HOST = "www.waterqualitydata.us"
MIN_INTERVAL_S = 0.5
THROTTLE_DELAYS_S = (30.0, 60.0, 120.0, 240.0, 300.0, 300.0)
_cooldown = {"until": 0.0}
_cooldown_lock = threading.Lock()


def all_names() -> list[str]:
    from easi.datasources import wqp as app_wqp
    return [name for param in PARAMS for name in app_wqp.SYNONYMS[param]]


def fetch_stations(bbox: list[float], start: str) -> bytes:
    """Station/search for the cell: every stream station in it, with no date
    or characteristic filter. Those filters make the portal scan results to
    answer (minutes, and cut at three); the bare box answers in seconds, and
    the result batches apply the date and the names, so a station with no
    nutrient results costs one near-empty answer."""
    import requests
    del start                                                  # kept in the signature for the tests' fakes
    params = [("bBox", ",".join(f"{v:.5f}" for v in bbox)), ("mimeType", "csv"), ("zip", "yes"),
              ("siteType", "Stream"), ("sorted", "no")]
    http.pace(WQP_HOST, MIN_INTERVAL_S)
    response = requests.get(config.WQP_STATION_URL, params=params, timeout=config.WQP_BATCH_TIMEOUT_S,
                            headers={"User-Agent": http.USER_AGENT})
    response.raise_for_status()
    return response.content


#: characters of station ids per GET request: the POST form of the search
#: answers with an older profile (no ResultIdentifier, station name or
#: coordinates), so results go through GET, whose URL must stay short
MAX_IDS_CHARS = 5000


def fetch_results(siteids: list[str], start: str, end: Optional[str] = None) -> bytes:
    """Result/search (GET, one ``siteid`` parameter per station) for explicit
    station ids, all nutrient names at once; ``sorted=no`` because the default
    sort adds minutes."""
    import requests
    params = [("mimeType", "csv"), ("zip", "yes"), ("dataProfile", "resultPhysChem"), ("sorted", "no"),
              ("startDateLo", start), ("siteType", "Stream")]
    if end:
        params.append(("startDateHi", end))
    params += [("characteristicName", name) for name in all_names()]
    params += [("siteid", station) for station in siteids]
    http.pace(WQP_HOST, MIN_INTERVAL_S)
    response = requests.get(config.WQP_RESULT_URL, params=params, timeout=config.WQP_BATCH_TIMEOUT_S,
                            headers={"User-Agent": http.USER_AGENT})
    response.raise_for_status()
    return response.content


def fit(stations: list[dict], a: int, size: int) -> int:
    """End index of the batch starting at ``a``: at most ``size`` stations and
    at most ``MAX_IDS_CHARS`` characters of ids (never fewer than one)."""
    j, chars = a, 0
    while j < len(stations) and j - a < size:
        chars += len(stations[j]["station"]) + 8            # "&siteid=" plus the id
        if chars > MAX_IDS_CHARS and j > a:
            break
        j += 1
    return max(j, a + 1)


def _watch(fn: Callable[[], bytes], control: Optional[Control], timeout_s: float) -> bytes:
    """Run ``fn`` on a daemon thread and poll the operator's control file while
    it runs, so Pause and Cancel take effect within seconds; give up past
    ``timeout_s`` (the abandoned request is simply repeated on Resume)."""
    import threading
    result: dict = {}
    done = threading.Event()

    def _run():
        try:
            result["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc
        finally:
            done.set()

    threading.Thread(target=_run, daemon=True).start()
    waited = 0.0
    while not done.wait(CONTROL_POLL_S):
        waited += CONTROL_POLL_S
        if control is not None:
            control.check()                    # raises PauseRequested / CancelRequested
        if waited >= timeout_s:
            raise _Timeout(f"no answer in {int(waited)} s")
    if "error" in result:
        raise result["error"]
    return result["value"]


def is_throttle(exc: Optional[BaseException]) -> bool:
    """HTTP 429: the portal asks for a pause, not a smaller request."""
    text = str(exc or "").lower()
    return "429" in text or "too many requests" in text


def _throttled(delay_s: float) -> None:
    with _cooldown_lock:
        _cooldown["until"] = max(_cooldown["until"], time.monotonic() + delay_s)


def _wait_cooldown(sleep) -> None:
    with _cooldown_lock:
        remaining = _cooldown["until"] - time.monotonic()
    if remaining > 0:
        sleep(remaining)


def _fetch_with_backoff(fn: Callable[[], bytes], control, sleep) -> tuple:
    """``(content, None, requests)`` on success; ``(None, last error, requests)``
    when the request was cut, stayed throttled, or kept failing. A throttle
    parks every connection (the shared cooldown) and retries with escalating
    waits; a transient error retries after a pause; a cut is returned at once
    for the caller to split."""
    attempt = throttles = made = 0
    while True:
        _wait_cooldown(sleep)
        made += 1
        try:
            return _watch(fn, control, config.WQP_BATCH_TIMEOUT_S), None, made
        except (PauseRequested, CancelRequested):
            raise
        except Exception as exc:  # noqa: BLE001
            if is_throttle(exc):
                if throttles >= len(THROTTLE_DELAYS_S):
                    return None, exc, made
                delay = THROTTLE_DELAYS_S[throttles]
                throttles += 1
                _throttled(delay)
                sleep(delay)
                continue
            if is_cut(exc):
                return None, exc, made
            if attempt < len(RETRY_DELAYS_S):
                sleep(RETRY_DELAYS_S[attempt])
                attempt += 1
                continue
            return None, exc, made


def is_cut(exc: Optional[BaseException]) -> bool:
    """The gateway closed the connection, or the watchdog gave up: the request
    was too big, so it is split rather than retried."""
    if exc is None:
        return False
    if isinstance(exc, (_Timeout, TimeoutError, ConnectionError)):
        return True
    text = str(exc).lower()
    return any(s in text for s in ("closed connection", "connection aborted", "remotedisconnected",
                                   "timed out", "connection reset"))


# ------------------------------------------------------------ station lists
STATION_FIELDS = ("station", "station_name", "org", "lat", "lon")


def parse_stations(text: str) -> list[dict]:
    from easi.datasources import wqp as app_wqp
    reader = csv.DictReader(io.StringIO(text))
    names = reader.fieldnames or []
    f = app_wqp._field
    id_col = f(names, "MonitoringLocationIdentifier")
    name_col = f(names, "MonitoringLocationName")
    org_col = f(names, "OrganizationIdentifier")
    lat_col = f(names, "LatitudeMeasure")
    lon_col = f(names, "LongitudeMeasure")
    out = []
    for row in reader:
        station = (row.get(id_col) or "").strip() if id_col else ""
        if not station:
            continue
        try:
            lat = float(row.get(lat_col)) if lat_col and row.get(lat_col) else None
            lon = float(row.get(lon_col)) if lon_col and row.get(lon_col) else None
        except (TypeError, ValueError):
            lat = lon = None
        out.append({"station": station,
                    "station_name": (row.get(name_col) or "").strip() if name_col else "",
                    "org": (row.get(org_col) or "").strip() if org_col else "",
                    "lat": lat, "lon": lon})
    return out


def cell_stations(bbox: list[float], start: str, fetch=fetch_stations, *, control=None, sleep=None,
                  depth: int = 0) -> list[dict]:
    """Stations of one cell. A transient error is retried after a pause; a cut
    cell is split into quarters (three times at most)."""
    sleep = sleep or time.sleep
    content, last, _made = _fetch_with_backoff(lambda: fetch(bbox, start), control, sleep)
    if content is not None:
        return parse_stations(_csv_text(content))
    if depth >= MAX_CELL_SPLIT_DEPTH or not is_cut(last):
        raise RuntimeError(f"WQP station list for cell {[round(v, 3) for v in bbox]} keeps failing: {last}")
    out: list[dict] = []
    for quarter in _quarters(bbox):
        if control is not None:
            control.check()
        out.extend(cell_stations(quarter, start, fetch, control=control, sleep=sleep, depth=depth + 1))
    return out


def write_stations(root: DataRoot, chunk: Chunk, stations: list[dict]):
    import pyarrow as pa
    table = pa.Table.from_pylist(stations, schema=pa.schema([
        ("station", pa.string()), ("station_name", pa.string()), ("org", pa.string()),
        ("lat", pa.float64()), ("lon", pa.float64())]))
    return common.write_parquet(table, root.chunk_raw(chunk.id, "wqp_stations"))


def read_stations(root: DataRoot, chunk: Chunk) -> Optional[list[dict]]:
    import pyarrow.parquet as pq
    path = root.chunk_raw(chunk.id, "wqp_stations")
    if not path.exists():
        return None
    return pq.read_table(path).to_pylist()


# ----------------------------------------------------------- result batches
@dataclass
class BatchResult:
    rows: list = field(default_factory=list)
    seconds: Optional[float] = None     # answer time when the batch came back in one request
    split: bool = False                 # the batch had to be halved or windowed
    requests: int = 0


class Cut(Exception):
    """A multi-station batch was cut and the caller asked not to halve it
    in place: the pool re-queues the halves as batches of their own."""

    def __init__(self, last: Optional[BaseException], requests: int):
        super().__init__(str(last))
        self.last = last
        self.requests = requests


def date_windows(start: str, end: Optional[str] = None, n: int = DATE_WINDOWS) -> list[tuple[str, Optional[str]]]:
    """``[start, end]`` (``end`` None = today) as ``n`` consecutive inclusive
    windows; the last keeps the open end when there was one."""
    lo = datetime.strptime(start, _DATE).date()
    hi = datetime.strptime(end, _DATE).date() if end else date.today()
    span = max(1, (hi - lo).days)
    edges = [lo + timedelta(days=round(span * k / n)) for k in range(n + 1)]
    windows: list[tuple[str, Optional[str]]] = []
    for k in range(n):
        a = edges[k]
        if k < n - 1:
            b: Optional[date] = edges[k + 1] - timedelta(days=1)
        else:
            b = hi if end else None
        if b is not None and b < a:
            continue
        windows.append((a.strftime(_DATE), b.strftime(_DATE) if b else None))
    return windows


def batch_rows(siteids: list[str], start: str, fetch=fetch_results, *, control=None, sleep=None,
               end: Optional[str] = None, depth: int = 0, halve: bool = True) -> BatchResult:
    """Normalized rows for a batch of stations. A transient error is retried
    after a pause; a cut batch is halved and both halves fetched (or, with
    ``halve=False``, raised as ``Cut`` for the pool to re-queue the halves as
    batches of their own); a single station that is cut is fetched in date
    windows."""
    sleep = sleep or time.sleep
    ids = list(siteids)
    t0 = time.monotonic()
    content, last, made = _fetch_with_backoff(lambda: fetch(ids, start, end), control, sleep)
    if content is not None:
        return BatchResult(normalize_rows(None, _csv_text(content)), time.monotonic() - t0, False, made)
    if is_throttle(last):
        raise RuntimeError(f"WQP results for {len(ids)} stations: still throttled after "
                           f"{len(THROTTLE_DELAYS_S)} waits: {last}")
    if not is_cut(last):
        raise RuntimeError(f"WQP results for {len(ids)} stations keep failing: {last}")
    if len(ids) > 1 and not halve:
        raise Cut(last, made)
    if len(ids) > 1:
        half = len(ids) // 2
        parts = [batch_rows(ids[:half], start, fetch, control=control, sleep=sleep, end=end, depth=depth),
                 batch_rows(ids[half:], start, fetch, control=control, sleep=sleep, end=end, depth=depth)]
    elif depth < MAX_WINDOW_DEPTH:
        parts = [batch_rows(ids, lo, fetch, control=control, sleep=sleep, end=hi, depth=depth + 1)
                 for lo, hi in date_windows(start, end)]
    else:
        raise RuntimeError(f"WQP results for station {ids[0]} keep being cut even in "
                           f"{DATE_WINDOWS ** MAX_WINDOW_DEPTH}-way date windows: {last}")
    rows = [row for part in parts for row in part.rows]
    return BatchResult(rows, None, True, made + sum(part.requests for part in parts))


def adapt(size: int, result: BatchResult) -> int:
    """The next batch size after ``result``: grow on a quick answer, shrink on
    a slow one or a split."""
    if result.split or (result.seconds is not None and result.seconds > SLOW_S):
        return max(BATCH_MIN, int(size * SHRINK))
    if result.seconds is not None and result.seconds < QUICK_S:
        return min(BATCH_MAX, int(size * GROW) + 1)
    return size


_KEY = re.compile(r"s(\d+)-(\d+)")


def batch_key(a: int, b: int) -> str:
    return f"s{a:07d}-{b:07d}"


def uncovered(n: int, keys) -> list[list[int]]:
    """``[a, b)`` index ranges of the station list not covered by the ledger's
    batch keys."""
    ranges = sorted((int(m.group(1)), int(m.group(2)))
                    for m in (_KEY.fullmatch(str(k)) for k in keys) if m)
    out: list[list[int]] = []
    cursor = 0
    for a, b in ranges:
        if a > cursor:
            out.append([cursor, a])
        cursor = max(cursor, b)
    if cursor < n:
        out.append([cursor, n])
    return out


# -------------------------------------------------------------- the stage
def _station_list(root: DataRoot, chunk: Chunk, start: str, progress: Progress, control: Control,
                  fetch) -> list[dict]:
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    stations = read_stations(root, chunk)
    if stations is not None:
        return stations
    cells = common.bbox_grid(chunk.bbox, CELL_DEG)
    ledger = Ledger(root, f"wqp-stations-{chunk.id}")
    parts = common.parts_dir(root, chunk.id, "wqp_stations")
    pending = [(f"c{i:03d}", cell) for i, cell in enumerate(cells) if f"c{i:03d}" not in ledger]
    done_n = len(cells) - len(pending)
    workers = max(1, config.WQP_CONCURRENCY)
    progress.begin(chunk.id, STAGE, total=len(cells),
                   message=f"WQP stations: {len(cells)} cells since {start}, {len(pending)} to list, "
                           f"{workers} at a time")
    progress.tick(done=done_n)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        queue, running = list(pending), {}
        try:
            while queue or running:
                while queue and len(running) < workers:
                    control.check()
                    key, cell = queue.pop(0)
                    running[pool.submit(cell_stations, cell, start, fetch, control=control)] = key
                finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for future in finished:
                    key = running.pop(future)
                    found = future.result()                 # re-raises Pause/Cancel/failures
                    common.write_part(parts, key, {"stations": found})
                    ledger.add(key, n=len(found))
                    done_n += 1
                    progress.tick(done=done_n, message=f"WQP stations: {done_n} of {len(cells)} cells listed")
        except (PauseRequested, CancelRequested):
            pool.shutdown(wait=True, cancel_futures=True)   # in-flight requests notice within a poll
            raise
    by_id: dict[str, dict] = {}
    for _key, payload in common.read_parts(parts):
        for station in payload.get("stations") or []:
            by_id.setdefault(station["station"], station)
    stations = [by_id[key] for key in sorted(by_id)]
    write_stations(root, chunk, stations)
    progress.say(f"wqp_stations.parquet: {len(stations):,} stations")
    common.drop_parts(parts)
    ledger.clear()
    return stations


def _results(root: DataRoot, chunk: Chunk, start: str, stations: list[dict], progress: Progress,
             control: Control, fetch):
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    ledger = Ledger(root, f"wqp-results-{chunk.id}")
    parts = common.parts_dir(root, chunk.id, "wqp_results")
    pending = uncovered(len(stations), ledger.keys())
    covered = len(stations) - sum(b - a for a, b in pending)
    rows_n = 0
    size = BATCH_START
    workers = max(1, config.WQP_CONCURRENCY)
    progress.begin(chunk.id, STAGE, total=len(stations),
                   message=f"WQP results: {len(stations):,} stations, {len(stations) - covered:,} to fetch "
                           f"in batches from {size}, {workers} at a time")
    progress.tick(done=covered)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        running: dict = {}
        try:
            while pending or running:
                while pending and len(running) < workers:
                    control.check()
                    a, b = pending[0]
                    j = min(b, fit(stations, a, size))
                    if j >= b:
                        pending.pop(0)
                    else:
                        pending[0] = [j, b]
                    key = batch_key(a, j)
                    ids = [s["station"] for s in stations[a:j]]
                    running[pool.submit(batch_rows, ids, start, fetch, control=control,
                                        halve=False)] = (key, a, j)
                    progress.tick(done=covered, message=f"WQP results: {covered:,} of {len(stations):,} stations, "
                                                        f"{rows_n:,} rows, batches of {size}, fetching "
                                                        + ", ".join(sorted(k for k, _, _ in running.values())))
                finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for future in finished:
                    key, a, j = running.pop(future)
                    try:
                        result = future.result()            # re-raises Pause/Cancel/failures
                    except Cut as cut:
                        # too many rows for one answer: the halves go back to the
                        # front of the queue as batches of their own, so they run
                        # in parallel and every finished piece is saved at once
                        middle = a + (j - a) // 2
                        pending.insert(0, [middle, j])
                        pending.insert(0, [a, middle])
                        size = adapt(size, BatchResult(split=True, requests=cut.requests))
                        progress.tick(done=covered, message=f"WQP {key}: cut after {cut.requests} request(s), "
                                                            f"re-queued as {batch_key(a, middle)} and "
                                                            f"{batch_key(middle, j)}; batches of {size}")
                        continue
                    common.write_part(parts, key, {"rows": result.rows})
                    ledger.add(key, n=len(result.rows), stations=j - a,
                               seconds=round(result.seconds or 0.0, 1), split=result.split,
                               requests=result.requests)
                    covered += j - a
                    rows_n += len(result.rows)
                    size = adapt(size, result)
                    took = f"{result.seconds:.0f} s" if result.seconds is not None else "split"
                    progress.tick(done=covered, message=f"WQP {key}: {len(result.rows):,} rows, "
                                                        f"{result.requests} request(s), {took}")
        except (PauseRequested, CancelRequested):
            pool.shutdown(wait=True, cancel_futures=True)
            raise
    return ledger, parts


def fill_from_stations(rows: list[dict], stations: list[dict]) -> list[dict]:
    """Rows missing coordinates or a station name take them from the station
    list (the joins index a station by the coordinates on its rows)."""
    by_id = {s["station"]: s for s in stations}
    for row in rows:
        station = by_id.get(row.get("station"))
        if not station:
            continue
        if row.get("lat") is None or row.get("lon") is None:
            row["lat"], row["lon"] = station.get("lat"), station.get("lon")
        if not row.get("station_name"):
            row["station_name"] = station.get("station_name") or ""
    return rows


def _assemble(root: DataRoot, chunk: Chunk, parts, progress: Progress, stations: list[dict]) -> None:
    import pyarrow as pa
    rows = [row for _key, payload in common.read_parts(parts) for row in (payload.get("rows") or [])]
    fill_from_stations(rows, stations)
    schema = pa.schema([
        ("param", pa.string()), ("result_id", pa.string()), ("station", pa.string()),
        ("station_name", pa.string()), ("org", pa.string()), ("value", pa.float64()),
        ("reason", pa.string()), ("date", pa.string()), ("lat", pa.float64()), ("lon", pa.float64())])
    for param in PARAMS:
        seen: set = set()
        unique: list[dict] = []
        for row in rows:
            if row.get("param") != param:
                continue
            ident = row.get("result_id") or (row["station"], row.get("date"), row.get("value"), row["reason"])
            if ident in seen:
                continue
            seen.add(ident)
            unique.append(row)
        common.write_parquet(pa.Table.from_pylist(unique, schema=schema), root.chunk_raw(chunk.id, f"wqp_{param}"))
        progress.say(f"wqp_{param}.parquet: {len(unique):,} results")


def run_wqp(root: DataRoot, chunk: Chunk, states: UnitStates, progress: Progress, control: Control, *,
            force: bool = False, fetch=fetch_results, fetch_stations_fn=fetch_stations,
            as_of: date = config.NRSA_AS_OF) -> None:
    """The WQP stage by station batches; same inputs digest and output files as
    the bounding-box stage, so a chunk finished either way stays done."""
    if not chunk.bbox:
        raise RuntimeError("the geometry stage must run first (no chunk bbox)")
    start = start_date(as_of)
    inputs = common.chunk_inputs(STAGE, chunk, [round(v, 3) for v in chunk.bbox], start, PARAMS, 1)

    def work():
        stations = _station_list(root, chunk, start, progress, control, fetch_stations_fn)
        ledger, parts = _results(root, chunk, start, stations, progress, control, fetch)
        _assemble(root, chunk, parts, progress, stations)
        common.drop_parts(parts)
        ledger.clear()

    common.run_stage(states, chunk.id, STAGE, inputs, work, progress, force=force)
