"""Water Quality Portal nutrient results for a chunk, pulled by bounding box
(the chunk bbox already carries the 10-mile buffer) in grid cells, one zipped
CSV per cell and parameter, normalized row by row with the app's own rules
(``easi.datasources.wqp``) so the later 5-mile join reproduces
``wqp.sample_summary`` exactly."""
from __future__ import annotations

import csv
import io
import math
import zipfile
from datetime import date
from typing import Optional

from .. import config, http
from ..paths import DataRoot
from ..state import Control, Ledger, Progress, UnitStates
from ..units import Chunk
from . import common

STAGE = "wqp"
PARAMS = ("tn", "tp")
CELL_DEG = 1.0
COLUMNS = ("param", "result_id", "station", "station_name", "org", "value", "reason",
           "date", "lat", "lon")


def start_date(as_of: date, years: int = config.WQP_YEARS) -> str:
    try:
        start = as_of.replace(year=as_of.year - years)
    except ValueError:
        start = as_of.replace(year=as_of.year - years, day=28)
    return start.strftime("%m-%d-%Y")


#: A cell with more result-bearing stations than this is split before any
#: results are requested: the portal's response time grows with rows, and a
#: dense one-degree cell (a metropolitan area) can take an hour whole.
STATION_SPLIT_THRESHOLD = 250
MAX_STATION_SPLIT_DEPTH = 4


def fetch_station_count(param: str, bbox: list[float], start: str) -> int:
    """How many stations in the cell have results for the parameter (the
    portal's Station/search answers in seconds)."""
    import requests
    from easi.datasources import wqp as app_wqp
    params = [("bBox", ",".join(f"{v:.5f}" for v in bbox)),
              ("startDateLo", start), ("mimeType", "csv"), ("siteType", "Stream")]
    for characteristic in app_wqp.SYNONYMS[param]:
        params.append(("characteristicName", characteristic))
    response = requests.get(config.WQP_STATION_URL, params=params, timeout=300.0,
                            headers={"User-Agent": http.USER_AGENT})
    response.raise_for_status()
    text = _csv_text(response.content)
    return max(0, sum(1 for line in text.splitlines() if line.strip()) - 1)


def fetch_cell(param: str, bbox: list[float], start: str) -> bytes:
    """One attempt per cell (no automatic retry: a slow WQP answer can take
    minutes, and a hung one is better split than retried whole). ``sorted=no`` skips
    the portal default sort, which can hold the first row back for minutes."""
    import requests
    from easi.datasources import wqp as app_wqp
    params = [("bBox", ",".join(f"{v:.5f}" for v in bbox)),
              ("startDateLo", start), ("mimeType", "csv"), ("zip", "yes"),
              ("dataProfile", "resultPhysChem"), ("siteType", "Stream"),
              ("sorted", "no")]
    for characteristic in app_wqp.SYNONYMS[param]:
        params.append(("characteristicName", characteristic))
    response = requests.get(config.WQP_RESULT_URL, params=params, timeout=config.WQP_TIMEOUT_S,
                            headers={"User-Agent": http.USER_AGENT})
    response.raise_for_status()
    return response.content


#: Pauses before retrying a cell that failed transiently (a dropped connection,
#: a 5xx); the service closes connections under sustained load. A timeout is
#: never retried whole: a cell that slow is split at once.
RETRY_DELAYS_S = (20.0, 60.0)
MAX_SPLIT_DEPTH = 2
CONTROL_POLL_S = 5.0


class _Timeout(Exception):
    """The request outlived ``WQP_TIMEOUT_S``."""


def _fetch_watching_control(fetch, param, bbox, start, control):
    """Run ``fetch`` on a daemon thread and poll the operator's control file
    while it runs, so Pause and Cancel take effect within seconds instead of
    after a request that may take a quarter of an hour. The abandoned request
    is simply repeated on Resume."""
    import concurrent.futures as cf
    import threading
    result: dict = {}
    done = threading.Event()

    def _run():
        try:
            result["value"] = fetch(param, bbox, start)
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
        if waited >= config.WQP_TIMEOUT_S:
            raise _Timeout(f"no answer in {int(waited)} s")
    if "error" in result:
        raise result["error"]
    return result["value"]


def _quarters(bbox: list[float]) -> list[list[float]]:
    west, south, east, north = bbox
    mx, my = (west + east) / 2.0, (south + north) / 2.0
    return [[west, south, mx, my], [mx, south, east, my],
            [west, my, mx, north], [mx, my, east, north]]


_DEFAULT = object()


def fetch_cell_rows(param: str, bbox: list[float], start: str, fetch=fetch_cell,
                    depth: int = 0, *, sleep=None, control=None,
                    station_count=_DEFAULT, split_depth: int = 0) -> list[dict]:
    """Rows for a cell. Dense cells are split before any results are requested
    (``station_count`` decides); an empty cell costs one station query. Then a
    transient failure is retried after a pause; a timeout, or a cell still
    failing after the retries, is split into quarters (twice at most). Raises
    only when a quarter keeps failing."""
    import time
    from ..state import CancelRequested, PauseRequested
    sleep = sleep or time.sleep
    if station_count is _DEFAULT:
        station_count = fetch_station_count      # resolved at call time (tests substitute it)
    if station_count is not None and split_depth < MAX_STATION_SPLIT_DEPTH:
        try:
            stations = station_count(param, bbox, start)
        except Exception:  # noqa: BLE001 - the count is an optimization, never a blocker
            stations = None
        if stations == 0:
            return []
        if stations is not None and stations > STATION_SPLIT_THRESHOLD:
            rows: list[dict] = []
            for quarter in _quarters(bbox):
                if control is not None:
                    control.check()
                rows.extend(fetch_cell_rows(param, quarter, start, fetch, depth, sleep=sleep,
                                            control=control, station_count=station_count,
                                            split_depth=split_depth + 1))
            return rows
    last: Exception | None = None
    for attempt in range(len(RETRY_DELAYS_S) + 1):
        try:
            content = _fetch_watching_control(fetch, param, bbox, start, control)
            return normalize_rows(param, _csv_text(content))
        except (PauseRequested, CancelRequested):
            raise
        except _Timeout as exc:
            last = exc
            break                              # too slow: split, never retry whole
        except Exception as exc:  # noqa: BLE001 - resets, 5xx, bad archives
            last = exc
            if attempt < len(RETRY_DELAYS_S):
                sleep(RETRY_DELAYS_S[attempt])
    if depth >= MAX_SPLIT_DEPTH:
        raise RuntimeError(f"WQP {param} cell {[round(v, 3) for v in bbox]} keeps failing: {last}")
    rows = []
    for quarter in _quarters(bbox):
        rows.extend(fetch_cell_rows(param, quarter, start, fetch, depth + 1, sleep=sleep,
                                    control=control, station_count=None))
    return rows


def _csv_text(content: bytes) -> str:
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")] or zf.namelist()
            return zf.read(names[0]).decode("utf-8", "replace") if names else ""
    return content.decode("utf-8", "replace")


_PARAM_BY_NAME: dict = {}


def param_of(characteristic: str) -> Optional[str]:
    """``tn`` or ``tp`` for a characteristic name the app queries, else None."""
    if not _PARAM_BY_NAME:
        from easi.datasources import wqp as app_wqp
        for param in PARAMS:
            for name in app_wqp.SYNONYMS[param]:
                _PARAM_BY_NAME[name.lower()] = param
    return _PARAM_BY_NAME.get((characteristic or "").strip().lower())


def normalize_rows(param: Optional[str], text: str) -> list[dict]:
    """One dict per result row with the app's exclusion ``reason`` (``ok`` when kept).
    ``param`` None derives it per row from the CharacteristicName column and
    drops rows with other names."""
    from easi.datasources import wqp as app_wqp
    reader = csv.DictReader(io.StringIO(text))
    names = reader.fieldnames or []
    f = app_wqp._field
    name_col = f(names, "CharacteristicName")
    value_col = f(names, "ResultMeasureValue")
    unit_col = f(names, "ResultMeasure/MeasureUnitCode", "MeasureUnitCode")
    fraction_col = f(names, "ResultSampleFractionText", "SampleFractionText")
    status_col = f(names, "ResultStatusIdentifier", "ResultStatus")
    censor_col = f(names, "ResultDetectionConditionText", "DetectionCondition")
    station_col = f(names, "MonitoringLocationIdentifier")
    station_name_col = f(names, "MonitoringLocationName")
    org_col = f(names, "OrganizationIdentifier")
    date_col = f(names, "ActivityStartDate")
    lat_col = f(names, "LatitudeMeasure")
    lon_col = f(names, "LongitudeMeasure")
    result_col = f(names, "ResultIdentifier")
    rows = []
    for row in reader:
        row_param = param or (param_of(row.get(name_col) or "") if name_col else None)
        if row_param is None:
            continue
        raw = (row.get(value_col) or "").strip() if value_col else ""
        reason, value = "ok", None
        if not raw:
            reason = "blank"
        elif status_col and not app_wqp._valid_status(row.get(status_col) or ""):
            reason = "rejected"
        elif censor_col and (row.get(censor_col) or "").strip():
            reason = "censored"
        else:
            fraction = (row.get(fraction_col) or "").strip().lower() if fraction_col else ""
            if "total" not in fraction:
                reason = "non_total_fraction"
            else:
                factor = app_wqp._unit_factor(row.get(unit_col) or "") if unit_col else None
                if factor is None:
                    reason = "unsupported_unit"
                else:
                    try:
                        value = float(raw) * factor
                        if not math.isfinite(value):
                            reason, value = "nonnumeric", None
                    except ValueError:
                        reason, value = "nonnumeric", None
        station = (row.get(station_col) or "").strip() if station_col else ""
        org = (row.get(org_col) or "").strip() if org_col else ""
        station_name = (row.get(station_name_col) or "").strip() if station_name_col else ""
        if not station:
            station = "|".join(filter(None, [org, station_name])) or "unknown-station"
        observed = app_wqp._date(row.get(date_col) or "") if date_col else None
        try:
            lat = float(row.get(lat_col)) if lat_col and row.get(lat_col) else None
            lon = float(row.get(lon_col)) if lon_col and row.get(lon_col) else None
        except (TypeError, ValueError):
            lat = lon = None
        rows.append({"param": row_param, "result_id": (row.get(result_col) or "").strip() if result_col else "",
                     "station": station, "station_name": station_name, "org": org,
                     "value": value, "reason": reason,
                     "date": observed.isoformat() if observed else None,
                     "lat": lat, "lon": lon})
    return rows


def run_wqp(root: DataRoot, chunk: Chunk, states: UnitStates, progress: Progress,
            control: Control, *, force: bool = False, fetch=fetch_cell,
            as_of: date = config.NRSA_AS_OF) -> None:
    if not chunk.bbox:
        raise RuntimeError("the geometry stage must run first (no chunk bbox)")
    start = start_date(as_of)
    inputs = common.chunk_inputs(STAGE, chunk, [round(v, 3) for v in chunk.bbox], start, PARAMS, 1)

    def work():
        import pyarrow as pa
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
        from ..state import CancelRequested, PauseRequested
        cells = common.bbox_grid(chunk.bbox, CELL_DEG)
        plan = [(param, ci, cell) for param in PARAMS for ci, cell in enumerate(cells)]
        ledger = Ledger(root, f"wqp-{chunk.id}")
        parts = common.parts_dir(root, chunk.id, "wqp")
        pending = [(f"{p}-c{ci:03d}", p, cell) for p, ci, cell in plan if f"{p}-c{ci:03d}" not in ledger]
        done_n = len(plan) - len(pending)
        progress.begin(chunk.id, STAGE, total=len(plan),
                       message=f"WQP: {len(cells)} cells x {len(PARAMS)} parameters since {start}, "
                               f"{len(pending)} to fetch, {config.WQP_CONCURRENCY} at a time")
        progress.tick(done=done_n)
        # a few cells in flight at once: each request is watched for Pause/Cancel
        # every few seconds, so the pool drains within a poll when asked to stop
        with ThreadPoolExecutor(max_workers=max(1, config.WQP_CONCURRENCY)) as pool:
            queue = list(pending)
            running: dict = {}
            try:
                while queue or running:
                    while queue and len(running) < config.WQP_CONCURRENCY:
                        control.check()
                        key, param, cell = queue.pop(0)
                        running[pool.submit(fetch_cell_rows, param, cell, start, fetch,
                                            control=control)] = key
                        progress.tick(done=done_n, message=f"WQP: {done_n} of {len(plan)} cells done, "
                                                           f"fetching {', '.join(sorted(running.values()))}")
                    finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
                    for future in finished:
                        key = running.pop(future)
                        rows = future.result()               # re-raises Pause/Cancel/failures
                        common.write_part(parts, key, {"rows": rows})
                        ledger.add(key, n=len(rows))
                        done_n += 1
                        progress.tick(done=done_n, message=f"WQP {key}: {len(rows):,} results")
            except (PauseRequested, CancelRequested):
                pool.shutdown(wait=True, cancel_futures=True)   # in-flight cells notice within a poll
                raise
        for param in PARAMS:
            rows: list[dict] = []
            for key, payload in common.read_parts(parts):
                if key.startswith(param + "-"):
                    rows.extend(payload.get("rows") or [])
            seen: set = set()
            unique: list[dict] = []
            for r in rows:
                ident = r.get("result_id") or (r["station"], r.get("date"), r.get("value"), r["reason"])
                if ident in seen:
                    continue
                seen.add(ident)
                unique.append(r)
            table = pa.Table.from_pylist(unique, schema=pa.schema([
                ("param", pa.string()), ("result_id", pa.string()), ("station", pa.string()),
                ("station_name", pa.string()), ("org", pa.string()), ("value", pa.float64()),
                ("reason", pa.string()), ("date", pa.string()), ("lat", pa.float64()),
                ("lon", pa.float64())]))
            common.write_parquet(table, root.chunk_raw(chunk.id, f"wqp_{param}"))
            progress.say(f"wqp_{param}.parquet: {len(unique):,} results")
        common.drop_parts(parts)
        ledger.clear()

    common.run_stage(states, chunk.id, STAGE, inputs, work, progress, force=force)
