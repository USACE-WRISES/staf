"""NHDPlus HR service client for the site engine.

Flowline attributes/geometry, upstream-tree parent queries (``dnhydroseq``),
tree flowline geometries by id, and catchment polygons, all against the
NHDPlus_HR MapServer. Self-contained (the engine is vendored into apps that
must not import ``libs/`` at runtime); ``parse_feature`` keeps identical
semantics to EASI's ``easi/datasources/nhd_hr.py`` and a parity test guards
that when the EASI source tree is present.

Style contract shared with the STAF datasources: helpers never raise; they
return ``None`` (or empty lists) on failure, and callers degrade with recorded
reasons. Results are cached in-process where re-use is likely; a request the
service never answered is not a result, so it is asked again next time.

Query shapes (0.2.1): the upstream walk asks the spatial index for the
flowlines touching the frontier's endpoints (``parents_by_node``, one POST per
BFS level, about a second) and keeps those whose ``dnhydroseq`` is in the
frontier, which is the same membership test the attribute walk makes.
Attribute walks on ``dnhydroseq`` (``parents_by_dnhydroseq``, kept as the
reference) cost 35 to 55 seconds per query regardless of size because the
service scans that field (``nhdplusid`` answers in half a second), so the
0.2.0 geometry-free POST walk was bounded at about 36 seconds per hop and the
0.1.0 geometry-bearing GET walk at the same number spread over its reaches.
Geometry-bearing fetches (tree flowlines, catchments) use POST chunks of
``_GEOM_CHUNK``.

Resilience (2026-09-30; the service's latency is erratic, 1.5 to 75 seconds
for the same query within minutes). Same queries, same answers, so the
records do not change:

- Every answer is kept on disk (``httpcache``), so a view, a pick or a
  delineation asked before costs no request.
- Request policies (``POLICIES``) bound how hard one request tries. The
  process default is ``patient`` (the rules this client always had, for
  batch runs and scripts); the apps select ``interactive`` at startup
  (``set_policy``): fewer, shorter attempts, a failing batch split in halves
  before the call fails, and an eight-minute deadline on a delineation's walk
  and catchments. The map's tiles and a pick's snap name their own policies.
- The map reads the network in tiles of a fixed global grid
  (``flowlines_in_tiles``): each tile is well under the service's 2,000-record
  cap, tiles are reused across views and sessions, and a view draws the tiles
  that answered. A pick reads the tiles under its probe box (``snap_records``).
- At most ``_MAX_IN_FLIGHT`` requests to the service run at once per process.
"""
from __future__ import annotations

import contextlib
import dataclasses
import functools
import json
import math
import threading
import time
from collections import OrderedDict
from concurrent import futures
from typing import Any, Callable, Iterable, Optional

import requests

from . import httpcache

_BASE = "https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer"
FLOWLINE_QUERY_URL = f"{_BASE}/3/query"
CATCHMENT_QUERY_URL = f"{_BASE}/10/query"
SLOPE_SENTINEL = -9998.0

_ATTR_FIELDS = ("nhdplusid", "gnis_name", "reachcode", "lengthkm", "totdasqkm",
                "slope", "fcode", "ftype", "streamorde", "hydroseq",
                "uphydroseq", "dnhydroseq", "vpuid", "innetwork", "qama")
# Batched IN-clause sizes. GET chunks stay small (URL length); POST chunks are
# bounded by the layer's 2000-record cap on the answer, not by the request.
_CHUNK = 40
_WALK_CHUNK = 250
_GEOM_CHUNK = 100
# A failing batch splits in halves down to this many ids (100 -> 50 -> 25).
_MIN_SPLIT = 25
# Node walk: endpoints per spatial query (two per frontier reach) and the
# snapping distance, in meters, that joins coincident network nodes. A failing
# point batch splits down to a quarter (400 -> 200 -> 100).
_NODE_CHUNK = 400
_NODE_DISTANCE_M = 5.0
# The map's stream fetch (``fast_fail``): one attempt of this length, and no
# second attempt after a timeout or any failure slower than _FAST_FAIL_S, so a
# struggling service is not asked twice while the user waits. A quick failure
# (a 502, a reset connection) still gets its retry.
_DISPLAY_TIMEOUT_S = 20.0
_FAST_FAIL_S = 5.0
# Requests to the service in flight at once, per process.
_MAX_IN_FLIGHT = 6
# The map's tiles: a global grid of TILE_DEG squares; a tile over the record
# cap is answered by its quarters, down to _TILE_MIN_DEG. A view waits at most
# _TILE_WAIT_S for its tiles (the rest finish into the cache).
TILE_DEG = 0.05
_TILE_MIN_DEG = 0.0125
_TILE_WAIT_S = 30.0
_TILE_WORKERS = 4
_TILE_MEMO_MAX = 512


@dataclasses.dataclass(frozen=True)
class Policy:
    """How hard one request to the service tries.

    ``attempts`` counts the first try (None: the caller's ``retries`` under
    the patient rules). ``timeout_s`` replaces the caller's per-attempt
    timeout and ``cap_s`` bounds it. ``backoff_s`` are the pauses between
    attempts (the last repeats). ``retry_slow`` False ends the call after a
    timeout or any failure slower than ``_FAST_FAIL_S``. ``split`` lets a
    failing batch be asked again in halves. ``deadline_s`` bounds a
    delineation's walk and catchments (``deadline``)."""
    name: str
    attempts: Optional[int]
    timeout_s: Optional[float] = None
    cap_s: Optional[float] = None
    backoff_s: tuple = ()
    retry_slow: bool = True
    split: bool = False
    deadline_s: Optional[float] = None


POLICIES = {
    # The rules this client always had: the caller's retries and timeouts,
    # a pause of 0.5 s times the attempt, a chunk's second pass at the
    # escalated timeout. Batch runs (StreamCurves) and scripts.
    "patient": Policy("patient", attempts=None),
    # The app's own work: engine delineations, routing, reach derivation.
    "interactive": Policy("interactive", attempts=2, cap_s=60.0, backoff_s=(2.0, 5.0),
                          split=True, deadline_s=480.0),
    # One map tile: one 20 s attempt, a quick failure retried once. The
    # app's own capped retries ask for missing tiles again later.
    "display": Policy("display", attempts=2, timeout_s=_DISPLAY_TIMEOUT_S,
                      backoff_s=(0.5,), retry_slow=False),
    # A pick's probe tiles: about a 30 s budget.
    "pick": Policy("pick", attempts=2, timeout_s=15.0, backoff_s=(2.0,)),
}
_active = {"name": "patient"}
_local = threading.local()
_SLOTS = threading.BoundedSemaphore(_MAX_IN_FLIGHT)
_answered = {"at": None}


def set_policy(name: str) -> None:
    """Make ``name`` the process default (the apps choose ``interactive``)."""
    if name not in POLICIES:
        raise ValueError(f"unknown HR request policy {name!r}")
    _active["name"] = name


def active_policy() -> Policy:
    return POLICIES[_active["name"]]


def _policy(policy) -> Policy:
    if policy is None:
        return active_policy()
    if isinstance(policy, Policy):
        return policy
    return POLICIES[policy]


@contextlib.contextmanager
def deadline(seconds: Optional[float]):
    """Bound the requests this thread makes inside the block: past the
    deadline a request is not sent and answers None, and an attempt never
    outlives it. None or 0: no deadline. Nested deadlines keep the earlier."""
    if not seconds:
        yield
        return
    before = getattr(_local, "deadline", None)
    mine = time.monotonic() + float(seconds)
    _local.deadline = mine if before is None else min(before, mine)
    try:
        yield
    finally:
        _local.deadline = before


def _remaining() -> Optional[float]:
    until = getattr(_local, "deadline", None)
    return None if until is None else until - time.monotonic()


def out_of_time() -> bool:
    """True inside a ``deadline`` block whose time has run out."""
    left = _remaining()
    return left is not None and left <= 0


def last_answer_at() -> Optional[float]:
    """``time.monotonic()`` of the last answer the service itself gave this
    process (a cache hit is not one), or None."""
    return _answered["at"]


class _Unanswered(Exception):
    """The service did not answer: never a cached result."""


def _tell(on_attempt: Optional[Callable[[Any], Any]], what: Any) -> None:
    if on_attempt is None:
        return
    try:
        on_attempt(what)
    except Exception:  # noqa: BLE001 - an observer never breaks a request
        pass


def _attempt(url: str, params: dict, timeout: float, post: bool,
             on_attempt: Optional[Callable[[Any], Any]]) -> tuple[str, Optional[dict]]:
    """One request: ``("answer", data)``, ``("timeout", None)``, ``("error",
    None)`` (a connection error, a 5xx or 429, or an error payload: worth
    asking again) or ``("final", None)`` (another 4xx). ``on_attempt`` sees
    each failure: the status code, 500 for an error payload, or the
    exception."""
    try:
        with _SLOTS:
            r = (requests.post(url, data=params, timeout=timeout) if post
                 else requests.get(url, params=params, timeout=timeout))
    except requests.exceptions.Timeout as exc:
        _tell(on_attempt, exc)
        return "timeout", None
    except Exception as exc:  # noqa: BLE001 - resilience by design
        _tell(on_attempt, exc)
        return "error", None
    if r.status_code != 200:
        _tell(on_attempt, r.status_code)
        transient = r.status_code == 429 or r.status_code >= 500
        return ("error" if transient else "final"), None
    try:
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        _tell(on_attempt, exc)
        return "error", None
    # ArcGIS reports errors inside a 200 payload.
    if isinstance(data, dict) and "error" not in data:
        _answered["at"] = time.monotonic()
        return "answer", data
    _tell(on_attempt, 500)
    return "error", None


def _patient(url, params, timeout, retries, post, fast_fail, on_attempt) -> Optional[dict]:
    """The rules this client always had (and the map's old ``fast_fail``)."""
    for attempt in range(retries + 1):
        left = _remaining()
        if left is not None and left <= 0:
            return None
        started = time.monotonic()
        outcome, data = _attempt(url, params, timeout if left is None else min(timeout, left),
                                 post, on_attempt)
        if outcome == "answer":
            return data
        if outcome == "timeout" and fast_fail:
            return None
        if fast_fail and time.monotonic() - started > _FAST_FAIL_S:
            return None
        time.sleep(0.5 * (attempt + 1))
    return None


def _with_policy(url, params, timeout, post, pol: Policy, on_attempt) -> Optional[dict]:
    per = pol.timeout_s if pol.timeout_s is not None else timeout
    if pol.cap_s is not None:
        per = min(per, pol.cap_s)
    for attempt in range(pol.attempts):
        left = _remaining()
        if left is not None and left <= 0:
            return None
        started = time.monotonic()
        outcome, data = _attempt(url, params, per if left is None else min(per, left),
                                 post, on_attempt)
        if outcome == "answer":
            return data
        if outcome == "final" or attempt + 1 >= pol.attempts:
            return None
        if not pol.retry_slow and (outcome == "timeout"
                                   or time.monotonic() - started > _FAST_FAIL_S):
            return None
        pause = pol.backoff_s[min(attempt, len(pol.backoff_s) - 1)] if pol.backoff_s else 0.0
        if pause > 0:
            time.sleep(pause)
    return None


def _query(url: str, params: dict, *, timeout: float, retries: int = 1,
           post: bool = False, fast_fail: bool = False, policy=None,
           cache: bool = True, offline: bool = False,
           on_attempt: Optional[Callable[[Any], Any]] = None) -> Optional[dict]:
    """The answer to one request, or None. A stored answer is used first
    (``httpcache``); ``offline`` asks nothing when there is none. Only
    answers are stored."""
    key = None
    if cache:
        key = httpcache.key_for(url, params, "POST" if post else "GET")
        stored = httpcache.get(key)
        if stored is not None:
            return stored
    if offline:
        return None
    pol = _policy(policy)
    if fast_fail or pol.attempts is None:
        data = _patient(url, params, timeout, retries, post, fast_fail, on_attempt)
    else:
        data = _with_policy(url, params, timeout, post, pol, on_attempt)
    if data is not None and key is not None:
        httpcache.put(key, data)
    return data


def _request(url: str, params: dict, timeout: float, retries: int = 1,
             *, fast_fail: bool = False, policy=None, offline: bool = False,
             on_attempt: Optional[Callable[[Any], Any]] = None) -> Optional[dict]:
    return _query(url, params, timeout=timeout, retries=retries, fast_fail=fast_fail,
                  policy=policy, offline=offline, on_attempt=on_attempt)


def _request_post(url: str, params: dict, timeout: float, retries: int = 1,
                  *, policy=None, on_attempt: Optional[Callable[[Any], Any]] = None
                  ) -> Optional[dict]:
    """POST form of ``_request`` (long IN clauses never hit URL limits)."""
    return _query(url, params, timeout=timeout, retries=retries, post=True,
                  policy=policy, on_attempt=on_attempt)


def _exceeded(payload: Optional[dict]) -> bool:
    if not payload:
        return False
    if payload.get("exceededTransferLimit"):
        return True
    props = payload.get("properties")
    return bool(isinstance(props, dict) and props.get("exceededTransferLimit"))


def _int_id(value: Any) -> Optional[int]:
    try:
        iv = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return iv if iv > 0 else None


def parse_feature(feature: Optional[dict]) -> Optional[dict]:
    """Typed attribute dict from one HR GeoJSON feature, or None.

    Sentinel guards: slope None when negative (covers -9998); drainage area
    None when non-positive. Semantics parity-tested against EASI's copy.
    """
    if not feature:
        return None
    props = feature.get("properties") or {}
    nid = _int_id(props.get("nhdplusid"))
    if nid is None:
        return None

    def _f(key):
        v = props.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _i(key):
        v = props.get(key)
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    slope = _f("slope")
    da = _f("totdasqkm")
    name = props.get("gnis_name")
    return {
        "nhdplusid": nid,
        "gnis_name": (str(name).strip() or None) if name else None,
        "reachcode": str(props["reachcode"]) if props.get("reachcode") else None,
        "lengthkm": _f("lengthkm"),
        "totdasqkm": da if da is not None and da > 0 else None,
        "slope": slope if slope is not None and slope >= 0 else None,
        "fcode": _i("fcode"),
        "ftype": _i("ftype"),
        "stream_order": _i("streamorde"),
        "hydroseq": _int_id(props.get("hydroseq")),
        "uphydroseq": _int_id(props.get("uphydroseq")),
        "dnhydroseq": _int_id(props.get("dnhydroseq")),
        "vpuid": str(props["vpuid"]) if props.get("vpuid") else None,
        # Engine extra (not in the EASI copy): EROM mean-annual flow, cfs.
        "qama": (_f("qama") if _f("qama") is not None and _f("qama") >= 0
                 else None),
        "geometry": feature.get("geometry"),
    }


def _round_bbox(west, south, east, north, ndigits=3):
    return (round(west, ndigits), round(south, ndigits),
            round(east, ndigits), round(north, ndigits))


def _bbox_params(west: float, south: float, east: float, north: float) -> dict:
    """The network flowlines in an envelope, every attribute, full geometry."""
    return {"geometry": f"{west},{south},{east},{north}",
            "geometryType": "esriGeometryEnvelope", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects", "where": "innetwork=1",
            "outFields": ",".join(_ATTR_FIELDS), "returnGeometry": "true",
            "outSR": "4326", "f": "geojson"}


def _parse_records(data: dict) -> tuple:
    recs = [parse_feature(f) for f in data.get("features") or []]
    return tuple(r for r in recs if r and r.get("geometry"))


@functools.lru_cache(maxsize=32)
def _fetch_bbox(west: float, south: float, east: float, north: float,
                fast_fail: bool = False) -> tuple[str, tuple]:
    """``(status, records)`` for a rounded bbox. Always called positionally
    (the cache key). An unanswered request raises ``_Unanswered``, which the
    cache never stores, so only answers (ok, empty, truncated) are kept."""
    data = _request(FLOWLINE_QUERY_URL, _bbox_params(west, south, east, north),
                    timeout=_DISPLAY_TIMEOUT_S if fast_fail else 30.0, fast_fail=fast_fail)
    if data is None:
        raise _Unanswered
    if _exceeded(data):
        return "truncated", ()
    recs = _parse_records(data)
    return ("ok", recs) if recs else ("empty", ())


def flowlines_in_bbox_status(west: float, south: float, east: float, north: float,
                             *, max_area_deg2: float = 0.02, fast_fail: bool = False
                             ) -> tuple[str, list[dict]]:
    """``(status, records)`` for a bbox. Status is ``ok``, ``empty``,
    ``truncated`` (the service hit its record cap), ``too-large`` (over
    ``max_area_deg2``, never asked) or ``failed`` (no answer; asked again on
    the next call). ``fast_fail`` is the map's policy (see _DISPLAY_TIMEOUT_S)."""
    west, east = min(west, east), max(west, east)
    south, north = min(south, north), max(south, north)
    if west == east or south == north:
        return "empty", []
    if (east - west) * (north - south) > max_area_deg2:
        return "too-large", []
    try:
        status, recs = _fetch_bbox(*_round_bbox(west, south, east, north), fast_fail)
    except _Unanswered:
        return "failed", []
    return status, list(recs)


def flowlines_in_bbox(west: float, south: float, east: float, north: float,
                      *, max_area_deg2: float = 0.02) -> list[dict]:
    """Parsed HR flowline records (attrs + geometry) for a bbox; [] on failure."""
    return flowlines_in_bbox_status(west, south, east, north,
                                    max_area_deg2=max_area_deg2)[1]


# --------------------------------------------------------------------------- #
# the map's tiles and the pick's probe
# --------------------------------------------------------------------------- #
_tile_lock = threading.Lock()
_tile_memo: "OrderedDict[tuple, tuple]" = OrderedDict()
_tile_jobs: dict = {}
_TILE_POOL = futures.ThreadPoolExecutor(max_workers=_TILE_WORKERS,
                                        thread_name_prefix="hr-tile")


def _tile_size(level: int) -> float:
    return TILE_DEG / (2 ** level)


def tile_bbox(tile: tuple) -> tuple[float, float, float, float]:
    """``(west, south, east, north)`` of a ``(level, ix, iy)`` tile."""
    level, ix, iy = tile
    size = _tile_size(level)
    return (round(ix * size, 6), round(iy * size, 6),
            round((ix + 1) * size, 6), round((iy + 1) * size, 6))


def tiles_over(west: float, south: float, east: float, north: float) -> list[tuple]:
    """The top-level tiles covering a bbox, west to east, then south to north.
    An edge on a grid line (within float noise) does not pull in the next tile."""
    eps = 1e-9
    x0 = math.floor(west / TILE_DEG + eps)
    y0 = math.floor(south / TILE_DEG + eps)
    x1 = max(x0, math.ceil(east / TILE_DEG - eps) - 1)
    y1 = max(y0, math.ceil(north / TILE_DEG - eps) - 1)
    return [(0, ix, iy) for iy in range(y0, y1 + 1) for ix in range(x0, x1 + 1)]


def _remembered(tile: tuple) -> Optional[tuple]:
    with _tile_lock:
        hit = _tile_memo.get(tile)
        if hit is not None:
            _tile_memo.move_to_end(tile)
        return hit


def _remember(tile: tuple, answer: tuple) -> None:
    with _tile_lock:
        _tile_memo[tile] = answer
        _tile_memo.move_to_end(tile)
        while len(_tile_memo) > _TILE_MEMO_MAX:
            _tile_memo.popitem(last=False)


def _dedupe(records: Iterable[dict]) -> list[dict]:
    seen: set = set()
    out = []
    for rec in records:
        nid = rec.get("nhdplusid")
        if nid in seen:
            continue
        seen.add(nid)
        out.append(rec)
    return out


def _tile_answer(tile: tuple, policy="display", offline: bool = False) -> tuple[str, tuple]:
    """``(status, records)`` for one tile: ``ok``, ``empty`` or ``truncated``
    (over the cap even at the smallest size). A tile over the cap is answered
    by its four quarters. Raises ``_Unanswered`` when the service (or, with
    ``offline``, the cache) has no answer; nothing is remembered then."""
    hit = _remembered(tile)
    if hit is not None:
        return hit
    data = _query(FLOWLINE_QUERY_URL, _bbox_params(*tile_bbox(tile)),
                  timeout=_DISPLAY_TIMEOUT_S, policy=policy, offline=offline)
    if data is None:
        raise _Unanswered
    if not _exceeded(data):
        recs = _parse_records(data)
        answer = ("ok", recs) if recs else ("empty", ())
    elif _tile_size(tile[0] + 1) < _TILE_MIN_DEG - 1e-12:
        answer = ("truncated", ())
    else:
        level, ix, iy = tile
        quarters = [(level + 1, 2 * ix + dx, 2 * iy + dy) for dy in (0, 1) for dx in (0, 1)]
        recs = []
        answer = None
        for quarter in quarters:
            status, got = _tile_answer(quarter, policy, offline)
            if status == "truncated":
                answer = ("truncated", ())
                break
            recs.extend(got)
        if answer is None:
            recs = tuple(_dedupe(recs))
            answer = ("ok", recs) if recs else ("empty", ())
    _remember(tile, answer)
    return answer


def _tile_job(tile: tuple, policy) -> futures.Future:
    """The running fetch of a tile, shared by every view that wants it."""
    with _tile_lock:
        job = _tile_jobs.get(tile)
        if job is not None and not job.done():
            return job
        job = _TILE_POOL.submit(_tile_answer, tile, policy)
        _tile_jobs[tile] = job

    def _done(finished, key=tile):
        with _tile_lock:
            if _tile_jobs.get(key) is finished:
                del _tile_jobs[key]
    job.add_done_callback(_done)
    return job


def flowlines_in_tiles(west: float, south: float, east: float, north: float, *,
                       max_area_deg2: float = 0.02, wait_s: float = _TILE_WAIT_S,
                       offline: bool = False, policy="display"
                       ) -> tuple[str, list[dict], list[tuple]]:
    """The map's network for a view: ``(status, records, missing)``.

    The records of every tile over the view that answered, de-duplicated by
    ``nhdplusid``; ``missing`` holds the ``(west, south, east, north)`` of the
    tiles that did not. Status: ``ok``, ``empty``, ``partial`` (some tiles
    missing), ``failed`` (all missing), ``truncated`` (a tile over the cap at
    the smallest size; nothing drawn), ``too-large`` (over ``max_area_deg2``,
    never asked). Waits at most ``wait_s``: a tile still running then counts
    as missing and finishes into the cache. ``offline`` reads remembered and
    stored tiles only and asks nothing."""
    west, east = min(west, east), max(west, east)
    south, north = min(south, north), max(south, north)
    if west == east or south == north:
        return "empty", [], []
    if (east - west) * (north - south) > max_area_deg2:
        return "too-large", [], []
    tiles = tiles_over(west, south, east, north)
    answers: dict = {}
    jobs: dict = {}
    for tile in tiles:
        hit = _remembered(tile)
        if hit is not None:
            answers[tile] = hit
        elif offline:
            try:
                answers[tile] = _tile_answer(tile, policy, offline=True)
            except _Unanswered:
                pass
        else:
            jobs[tile] = _tile_job(tile, policy)
    if jobs:
        done, _running = futures.wait(list(jobs.values()), timeout=wait_s)
        for tile, job in jobs.items():
            if job in done:
                try:
                    answers[tile] = job.result()
                except Exception:  # noqa: BLE001 - _Unanswered or worse: missing
                    pass
    if any(a[0] == "truncated" for a in answers.values()):
        return "truncated", [], []
    missing = [tile_bbox(t) for t in tiles if t not in answers]
    records = _dedupe(r for t in tiles if t in answers for r in answers[t][1])
    if len(missing) == len(tiles):
        return "failed", [], missing
    if missing:
        return "partial", records, missing
    return ("ok", records, []) if records else ("empty", [], [])


def records_in_box(records: Iterable[dict], west: float, south: float, east: float,
                   north: float) -> list[dict]:
    """The records whose geometry intersects the box (the service's envelope
    test), in order."""
    from shapely.geometry import box, shape

    envelope = box(west, south, east, north)
    out = []
    for rec in records:
        try:
            if rec.get("geometry") and envelope.intersects(shape(rec["geometry"])):
                out.append(rec)
        except Exception:  # noqa: BLE001 - an unreadable geometry is skipped
            continue
    return out


def snap_records(lat: float, lon: float, half_deg: float = 0.012, *, policy="pick",
                 offline: bool = False) -> tuple[str, list[dict]]:
    """``(status, records)`` for a pick: the flowlines intersecting the probe
    box around the point, read from the map's tiles under it (remembered,
    stored, else fetched under ``policy``). The same lines the probe box's
    own query returns. Status: ``ok``, ``empty``, ``truncated`` or ``failed``
    (a tile did not answer). The engine's own anchoring keeps its exact
    envelope query (``flowlines_in_bbox``)."""
    west, south, east, north = lon - half_deg, lat - half_deg, lon + half_deg, lat + half_deg
    tiles = tiles_over(west, south, east, north)
    answers: dict = {}
    todo = []
    for tile in tiles:
        hit = _remembered(tile)
        if hit is not None:
            answers[tile] = hit
        else:
            todo.append(tile)
    if todo:
        with futures.ThreadPoolExecutor(max_workers=min(4, len(todo))) as pool:
            jobs = {t: pool.submit(_tile_answer, t, policy, offline) for t in todo}
            for tile, job in jobs.items():
                try:
                    answers[tile] = job.result()
                except Exception:  # noqa: BLE001
                    pass
    if len(answers) < len(tiles):
        return "failed", []
    if any(a[0] == "truncated" for a in answers.values()):
        return "truncated", []
    records = records_in_box(_dedupe(r for t in tiles for r in answers[t][1]),
                             west, south, east, north)
    return ("ok", records) if records else ("empty", [])


def clear_caches() -> None:
    """Forget the in-process answers (tests). The disk cache is ``httpcache``'s."""
    _fetch_bbox.cache_clear()
    with _tile_lock:
        _tile_memo.clear()
        _tile_jobs.clear()


# --------------------------------------------------------------------------- #
# ids, the upstream walk and catchments
# --------------------------------------------------------------------------- #
def flowline_by_id(nhdplusid: int, timeout: float = 30.0) -> Optional[dict]:
    nid = _int_id(nhdplusid)
    if nid is None:
        return None
    data = _request(FLOWLINE_QUERY_URL, {
        "where": f"nhdplusid = {nid}", "outFields": ",".join(_ATTR_FIELDS),
        "returnGeometry": "true", "outSR": "4326", "f": "geojson"},
        timeout=timeout, retries=2)
    feats = (data or {}).get("features") or []
    return parse_feature(feats[0]) if feats else None


def feature_by_hydroseq(hydroseq: int, timeout: float = 30.0) -> Optional[dict]:
    hs = _int_id(hydroseq)
    if hs is None:
        return None
    data = _request(FLOWLINE_QUERY_URL, {
        "where": f"hydroseq = {hs}", "outFields": ",".join(_ATTR_FIELDS),
        "returnGeometry": "true", "outSR": "4326", "f": "geojson"},
        timeout=timeout)
    feats = (data or {}).get("features") or []
    if len(feats) != 1:
        return None
    return parse_feature(feats[0])


def _chunks(values: Iterable[int], size: int = _CHUNK):
    buf: list[int] = []
    for v in values:
        buf.append(int(v))
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def _chunk_query(url: str, params: dict, timeout: float,
                 escalated_timeout: float, *, post: bool = False
                 ) -> Optional[dict]:
    """One batched chunk with a second, longer-timeout pass before giving up.

    The 2026-08-29 acceptance panel lost 5 of 14 sites to single chunk
    timeouts on multi-hop walks while every completed union agreed exactly
    with the published area, so failures here are worth real patience. A
    chunk that exhausts both passes still fails the whole call: the caller's
    invariant is a complete tree or None, never a silently partial one.

    Under a policy with its own attempts (``interactive``) the chunk gets
    those attempts and no escalated pass: the caller splits a failing chunk
    instead (``_split_query``).
    """
    fetch = _request_post if post else _request
    if active_policy().attempts is not None:
        data = fetch(url, params, timeout=timeout)
    else:
        data = fetch(url, params, timeout=timeout, retries=2)
        if data is None:
            data = fetch(url, params, timeout=escalated_timeout, retries=1)
    if data is None or _exceeded(data):
        return None
    return data


def _ask_piece(piece: list, ask: Callable[[list], Optional[dict]],
               min_size: Optional[int]) -> Optional[list[dict]]:
    data = ask(piece)
    if data is not None:
        return [data]
    if min_size is None or len(piece) <= min_size or out_of_time():
        return None
    mid = (len(piece) + 1) // 2
    left = _ask_piece(piece[:mid], ask, min_size)
    if left is None:
        return None
    right = _ask_piece(piece[mid:], ask, min_size)
    if right is None:
        return None
    return left + right


def _split_query(items: list, size: int, ask: Callable[[list], Optional[dict]], *,
                 min_size: int, progress: Optional[Callable[[int, int], Any]] = None
                 ) -> Optional[list[dict]]:
    """The answers for ``items`` asked ``size`` at a time through ``ask``
    (an answer or None), in order, or None as soon as a batch cannot be
    answered. Under a splitting policy a batch with no answer is asked again
    in halves, down to ``min_size``; the union is the same set of rows (only
    a batch that would otherwise fail the whole call is ever split).
    ``progress(done, total)`` follows the top-level batches."""
    min_split = min_size if active_policy().split else None
    batches = [items[i:i + size] for i in range(0, len(items), size)]
    answers: list[dict] = []
    for n, batch in enumerate(batches, start=1):
        got = _ask_piece(batch, ask, min_split)
        if got is None:
            return None
        answers.extend(got)
        if progress is not None:
            try:
                progress(n, len(batches))
            except Exception:  # noqa: BLE001 - the UI never breaks a query
                pass
    return answers


def parents_by_dnhydroseq(hydroseqs: list[int], *, with_geometry: bool = False,
                          timeout: float = 60.0,
                          escalated_timeout: float = 120.0
                          ) -> Optional[list[dict]]:
    """All reaches whose downstream hydroseq is in ``hydroseqs`` (one BFS level).

    Includes tributaries and divergences, which is what the upstream TREE walk
    needs. Geometry-free by default (the walk only needs ids and hydroseqs);
    pass ``with_geometry=True`` for a geometry-bearing level. Returns None on
    any chunk failure (the caller must treat the tree as incomplete, never
    silently partial).
    """
    size = _GEOM_CHUNK if with_geometry else _WALK_CHUNK

    def ask(chunk):
        where = "dnhydroseq IN (" + ",".join(str(x) for x in chunk) + ")"
        return _chunk_query(FLOWLINE_QUERY_URL, {
            "where": where, "outFields": ",".join(_ATTR_FIELDS),
            "returnGeometry": str(with_geometry).lower(), "outSR": "4326",
            "f": "geojson"}, timeout, escalated_timeout, post=True)
    answers = _split_query([int(v) for v in hydroseqs], size, ask, min_size=size // 4)
    if answers is None:
        return None
    out: list[dict] = []
    for data in answers:
        for f in data.get("features") or []:
            rec = parse_feature(f)
            if rec:
                out.append(rec)
    return out


def _endpoints(geometry: Optional[dict]) -> list[list[float]]:
    """Both ends of every part of a (Multi)LineString, lon/lat pairs."""
    if not geometry:
        return []
    coords = geometry.get("coordinates") or []
    parts = coords if geometry.get("type") == "MultiLineString" else [coords]
    pts: list[list[float]] = []
    for part in parts:
        if part:
            pts.append([float(part[0][0]), float(part[0][1])])
            pts.append([float(part[-1][0]), float(part[-1][1])])
    return pts


def parents_by_node(frontier: list[dict], *, timeout: float = 60.0,
                    escalated_timeout: float = 120.0,
                    distance_m: float = _NODE_DISTANCE_M
                    ) -> Optional[list[dict]]:
    """All reaches whose downstream hydroseq is one of the frontier's (one BFS
    level), found through the spatial index instead of a ``dnhydroseq`` scan.

    ``frontier`` is a list of parsed records carrying ``hydroseq`` and
    ``geometry``. A parent's downstream end coincides with its child's
    upstream end in the HR network, so the flowlines within ``distance_m`` of
    the frontier's endpoints contain every parent; the ``dnhydroseq``
    membership test then keeps exactly the parents (the child itself, its own
    child, and unrelated lines that merely touch a node are dropped). Records
    come back with geometry, which the next level and the riparian buffer
    both use. A frontier record without geometry cannot be walked from, so
    the call fails (None) rather than silently skipping it. Returns None on
    any chunk failure: the tree is complete or the call fails.
    """
    wanted = {int(r["hydroseq"]) for r in frontier if r.get("hydroseq")}
    if not wanted:
        return []
    points: list[list[float]] = []
    for rec in frontier:
        pts = _endpoints(rec.get("geometry"))
        if not pts:
            return None
        points.extend(pts)

    def ask(chunk):
        return _chunk_query(FLOWLINE_QUERY_URL, {
            "geometry": json.dumps({"points": chunk,
                                    "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryMultipoint", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "distance": str(distance_m), "units": "esriSRUnit_Meter",
            "outFields": ",".join(_ATTR_FIELDS), "returnGeometry": "true",
            "outSR": "4326", "f": "geojson"}, timeout, escalated_timeout,
            post=True)
    answers = _split_query(points, _NODE_CHUNK, ask, min_size=_NODE_CHUNK // 4)
    if answers is None:
        return None
    out: list[dict] = []
    seen: set[int] = set()
    for data in answers:
        for f in data.get("features") or []:
            rec = parse_feature(f)
            if not rec or rec.get("dnhydroseq") not in wanted:
                continue
            nid = rec.get("nhdplusid")
            if nid is None or nid in seen:
                continue
            seen.add(nid)
            out.append(rec)
    return out


def flowlines_by_ids(nhdplusids: list[int], timeout: float = 60.0,
                     escalated_timeout: float = 120.0
                     ) -> Optional[list[dict]]:
    """Flowline geometries for the given reach ids (one fetch for a whole
    tree). Returns ``[{"nhdplusid", "geometry"}]`` for the features that
    carry geometry, or None on any chunk failure."""
    def ask(chunk):
        where = "nhdplusid IN (" + ",".join(str(x) for x in chunk) + ")"
        return _chunk_query(FLOWLINE_QUERY_URL, {
            "where": where, "outFields": "nhdplusid",
            "returnGeometry": "true", "outSR": "4326", "f": "geojson"},
            timeout, escalated_timeout, post=True)
    answers = _split_query([int(v) for v in nhdplusids], _GEOM_CHUNK, ask,
                           min_size=_MIN_SPLIT)
    if answers is None:
        return None
    out: list[dict] = []
    for data in answers:
        for f in data.get("features") or []:
            nid = _int_id((f.get("properties") or {}).get("nhdplusid"))
            if nid is None or not f.get("geometry"):
                continue
            out.append({"nhdplusid": nid, "geometry": f["geometry"]})
    return out


def catchments_by_ids(nhdplusids: list[int], timeout: float = 90.0,
                      escalated_timeout: float = 180.0, *,
                      progress: Optional[Callable[[int, int], Any]] = None
                      ) -> Optional[list[dict]]:
    """Catchment polygons for the given reach ids.

    Returns ``[{"nhdplusid", "areasqkm", "geometry"}]`` or None on any chunk
    failure. A reach with no catchment simply has no row (zero-area sliver
    reaches exist in the HR fabric). ``progress(done, total)`` follows the
    batches.
    """
    def ask(chunk):
        where = "nhdplusid IN (" + ",".join(str(x) for x in chunk) + ")"
        return _chunk_query(CATCHMENT_QUERY_URL, {
            "where": where, "outFields": "nhdplusid,areasqkm",
            "returnGeometry": "true", "outSR": "4326", "f": "geojson"},
            timeout, escalated_timeout, post=True)
    answers = _split_query([int(v) for v in nhdplusids], _GEOM_CHUNK, ask,
                           min_size=_MIN_SPLIT, progress=progress)
    if answers is None:
        return None
    out: list[dict] = []
    for data in answers:
        for f in data.get("features") or []:
            props = f.get("properties") or {}
            nid = _int_id(props.get("nhdplusid"))
            if nid is None or not f.get("geometry"):
                continue
            try:
                area = float(props.get("areasqkm"))
            except (TypeError, ValueError):
                area = None
            out.append({"nhdplusid": nid, "areasqkm": area,
                        "geometry": f["geometry"]})
    return out
