"""NHDPlus HR data: a map for checking the slim copy, and the REST API the STAF
apps call instead of the USGS NHDPlus HR service.

The home page is a Leaflet map (``assets/viewer.js``) drawn from the same API the
apps use. The data folder is ``data/`` next to this file, or ``HR_DATA_DIR``.
The map also compares three sources for a clicked point (``api/pick``): GDAL
streaming the USGS package on S3, the USGS package downloaded once, and the slim
copy (``hrslim.jobs``; ``hrslim.direct`` reads the USGS packages in worker
processes, ``hrslim.worker``).

API (JSON, gzip when asked):
  GET  api/health                                   dataset summary
  GET  api/lines?bbox=w,s,e,n                       network flowlines (USGS layer 3 fields)
  GET  api/catchments?bbox=w,s,e,n&tol=20           catchments in a box
  POST api/catchments  {"ids": [...], "tol": 20}    catchments by NHDPlusID
  GET  api/reach?nhdplusid=N | hydroseq=N           one flowline
  POST api/flowlines   {"ids": [...]}               flowline geometry by NHDPlusID
  GET  api/tree?nhdplusid=N&max_reaches=&max_hops=  the upstream walk in one call
  GET  api/watershed?nhdplusid=N&tol=20             walk + catchment union
  GET  api/qa?bbox=w,s,e,n                          original geometry in the QA boxes
  GET  api/where?lon=&lat=                          USGS package and slim region at a point
  POST api/pick {"lon", "lat", "method", "scope"}   start a fetch job (remote|download|slim,
                                                    catchment|watershed); answers {"job"}
  GET  api/job/<id>                                 that job's progress, timings and result
  GET  api/values?nhdplusid=N                       precomputed watershed values (bundle v2): land
                                                    cover and riparian, roads, crossings, dams, soil K
  GET  api/extras?nhdplusid=N&fraction=0.5          per-flowline lookups: sinuosity, HUC12, ATTAINS, NWI
"""
from __future__ import annotations

import atexit
import functools
import gzip
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import dash
from dash import html
from flask import Response, request

from hrslim import Dataset, catchment_features, direct, jobs, line_features

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("HR_DATA_DIR") or (APP_DIR / "data"))
#: Largest boxes answered (square degrees); the viewer draws lines from zoom 12
#: and catchments from zoom 13, which stay under these on a large screen.
MAX_LINES_BOX = float(os.environ.get("HR_DATA_MAX_LINES_BOX") or 0.25)
MAX_CATCHMENTS_BOX = float(os.environ.get("HR_DATA_MAX_CATCHMENTS_BOX") or 0.06)
DEFAULT_MAX_REACHES = 5000
DEFAULT_MAX_HOPS = 200


def _load() -> tuple:
    if not (DATA_DIR / "manifest.json").exists():
        return None, f"No data in {DATA_DIR}."
    try:
        return Dataset(DATA_DIR), None
    except Exception as exc:  # a broken data folder must not stop the page
        return None, f"The data failed to load: {exc}"


DATASET, LOAD_ERROR = _load()


def _values_dir():
    """The precomputed tables (bundle v2): ``HR_DATA_VALUES``, else ``values/`` in or beside the data."""
    for cand in (os.environ.get("HR_DATA_VALUES"), DATA_DIR / "values", DATA_DIR.parent / "values"):
        if cand and Path(cand).is_dir():
            return Path(cand)
    return None


VALUES_DIR = _values_dir()
VALUES = None
if VALUES_DIR is not None and DATASET is not None and DATASET.manifest.get("format") == 2:
    from hrslim.values import ValueTables
    VALUES = ValueTables(VALUES_DIR)
STARTED = time.time()

app = dash.Dash(__name__, title="NHDPlus HR data", update_title=None)
server = app.server

app.layout = html.Div(className="hr-root", children=[
    html.Header(className="hr-header", children=[
        html.Div(className="hr-brand", children=[
            html.Span("NHDPlus HR data", className="hr-title"),
            html.Span("STAF", className="hr-staf"),
        ]),
        html.Nav(className="hr-nav", children=[
            html.A("API", href="api/health", target="_blank", rel="noopener"),
        ]),
    ]),
    html.Div(className="hr-body", children=[
        html.Aside(id="hr-panel", className="hr-panel"),
        html.Div(id="hr-map", className="hr-map"),
    ]),
])


# --------------------------------------------------------------------------- #
# API helpers
# --------------------------------------------------------------------------- #
def _json(obj, status: int = 200, *, cache_s: int = 600) -> Response:
    """JSON answer; data answers may be cached briefly (a redeploy can change
    the data, so never for long), errors and the summary never."""
    resp = Response(json.dumps(obj, separators=(",", ":")), status=status,
                    mimetype="application/json")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = (f"public, max-age={cache_s}" if status == 200 and cache_s > 0
                                     else "no-store")
    return resp


def _error(message: str, http_status: int, status: str | None = None) -> Response:
    word = status or ("error" if http_status >= 500 else "bad-request")
    return _json({"status": word, "message": message}, http_status)


def _collection(features: list, **extra) -> Response:
    body = {"type": "FeatureCollection", "features": features,
            "status": "ok" if features else "empty"}
    body.update(extra)
    return _json(body)


def _bbox() -> tuple[float, float, float, float]:
    raw = request.args.get("bbox", "")
    try:
        w, s, e, n = (float(v) for v in raw.split(","))
    except ValueError:
        raise ValueError("bbox must be west,south,east,north in degrees")
    w, e = min(w, e), max(w, e)
    s, n = min(s, n), max(s, n)
    if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= 90 and -90 <= n <= 90):
        raise ValueError("bbox outside longitude/latitude range")
    return w, s, e, n


def _int_arg(name: str, default=None):
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    return int(float(raw))


def _need_data():
    if DATASET is None:
        return _error(LOAD_ERROR or "No data.", 503, status="unavailable")
    return None


@server.after_request
def _compress(resp: Response) -> Response:
    if (not request.path.startswith("/api/") or resp.direct_passthrough
            or resp.status_code != 200 or "Content-Encoding" in resp.headers
            or "gzip" not in request.headers.get("Accept-Encoding", "").lower()):
        return resp
    data = resp.get_data()
    if len(data) < 1024:
        return resp
    resp.set_data(gzip.compress(data, compresslevel=5))
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Content-Length"] = str(len(resp.get_data()))
    resp.headers["Vary"] = "Accept-Encoding"
    return resp


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
@server.route("/api/health")
def api_health():
    body = {"ok": DATASET is not None, "message": LOAD_ERROR,
            "uptimeSeconds": round(time.time() - STARTED)}
    if DATASET is not None:
        body["dataset"] = DATASET.summary()
    return _json(body, cache_s=0)


@server.route("/api/lines")
def api_lines():
    if (bad := _need_data()) is not None:
        return bad
    try:
        w, s, e, n = _bbox()
    except ValueError as exc:
        return _error(str(exc), 400)
    if (e - w) * (n - s) > MAX_LINES_BOX:
        return _json({"type": "FeatureCollection", "features": [], "status": "too-large",
                      "message": f"box over {MAX_LINES_BOX} square degrees"})
    t0 = time.perf_counter()
    table = DATASET.lines_in_bbox(w, s, e, n, exact=request.args.get("exact", "1") != "0")
    return _collection(line_features(table), ms=round(1000 * (time.perf_counter() - t0), 1))


@server.route("/api/catchments", methods=["GET", "POST"])
def api_catchments():
    if (bad := _need_data()) is not None:
        return bad
    t0 = time.perf_counter()
    try:
        if request.method == "POST":
            body = request.get_json(force=True, silent=True) or {}
            ids = [int(v) for v in body.get("ids") or []]
            table = DATASET.catchments_by_ids(ids, body.get("tol"))
        else:
            w, s, e, n = _bbox()
            if (e - w) * (n - s) > MAX_CATCHMENTS_BOX:
                return _json({"type": "FeatureCollection", "features": [], "status": "too-large",
                              "message": f"box over {MAX_CATCHMENTS_BOX} square degrees"})
            table = DATASET.catchments_in_bbox(w, s, e, n, request.args.get("tol"))
    except ValueError as exc:
        return _error(str(exc), 400)
    return _collection(catchment_features(table), ms=round(1000 * (time.perf_counter() - t0), 1))


@server.route("/api/reach")
def api_reach():
    if (bad := _need_data()) is not None:
        return bad
    try:
        nid = _int_arg("nhdplusid")
        hs = _int_arg("hydroseq")
    except ValueError:
        return _error("nhdplusid and hydroseq must be integers", 400)
    if nid is None and hs is None:
        return _error("give nhdplusid or hydroseq", 400)
    return _collection(line_features(DATASET.reach(nhdplusid=nid, hydroseq=hs)))


@server.route("/api/flowlines", methods=["POST"])
def api_flowlines():
    if (bad := _need_data()) is not None:
        return bad
    body = request.get_json(force=True, silent=True) or {}
    try:
        ids = [int(v) for v in body.get("ids") or []]
    except (TypeError, ValueError):
        return _error("ids must be integers", 400)
    attributes = bool(body.get("attributes"))
    table = DATASET.flowlines_by_ids(ids, attributes=attributes)
    return _collection(line_features(table, attributes=attributes))


@server.route("/api/tree")
def api_tree():
    if (bad := _need_data()) is not None:
        return bad
    try:
        nid = _int_arg("nhdplusid")
        max_reaches = _int_arg("max_reaches", DEFAULT_MAX_REACHES)
        max_hops = _int_arg("max_hops", DEFAULT_MAX_HOPS)
    except ValueError:
        return _error("nhdplusid, max_reaches and max_hops must be integers", 400)
    if nid is None:
        return _error("give nhdplusid", 400)
    t0 = time.perf_counter()
    out = DATASET.upstream_tree(nid, max_reaches=max_reaches, max_hops=max_hops)
    out["ms"] = round(1000 * (time.perf_counter() - t0), 1)
    return _json(out)


@server.route("/api/watershed")
def api_watershed():
    if (bad := _need_data()) is not None:
        return bad
    try:
        nid = _int_arg("nhdplusid")
        max_reaches = _int_arg("max_reaches", DEFAULT_MAX_REACHES)
        max_hops = _int_arg("max_hops", DEFAULT_MAX_HOPS)
        if nid is None:
            raise ValueError("give nhdplusid")
        out = DATASET.watershed(nid, request.args.get("tol"), max_reaches=max_reaches,
                                max_hops=max_hops)
    except ValueError as exc:
        return _error(str(exc), 400)
    return _json(out)


# --------------------------------------------------------------------------- #
# three ways to fetch a catchment or watershed for a clicked point (hrslim.jobs)
# The slim copy answers on a thread here. The two USGS modes run in worker
# processes (hrslim.worker), one per mode: GDAL reads hold Python's GIL, and a
# streamed read takes minutes, so on a thread it would stall the whole app. Job
# state lives in small files; the map polls api/job.
# --------------------------------------------------------------------------- #
_JOB_ID = re.compile(r"^[0-9a-f]{12}$")
_SLIM_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pick-slim")


class _Worker:
    """One long-lived ``hrslim.worker`` process, started on first use and again
    if it stops; jobs it held when it stopped are marked failed."""

    def __init__(self, mode: str):
        self.mode = mode
        self.proc = None
        self.lock = threading.Lock()

    def submit(self, job: dict) -> None:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            self.proc.pending.add(job["id"])
            self.proc.stdin.write(json.dumps(job) + "\n")
            self.proc.stdin.flush()

    def _start(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(APP_DIR), env.get("PYTHONPATH")) if p)
        env["HR_DATA_JOBS"] = str(jobs.job_dir())
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen([sys.executable, "-u", "-m", "hrslim.worker"], cwd=str(APP_DIR), env=env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                encoding="utf-8", bufsize=1)
        proc.pending = set()
        self.proc = proc
        threading.Thread(target=self._watch, args=(proc,), daemon=True, name=f"usgs-{self.mode}").start()

    def _watch(self, proc) -> None:
        for line in proc.stdout:
            try:
                done = json.loads(line)
            except ValueError:
                continue
            with self.lock:
                proc.pending.discard(done.get("id"))
        proc.wait()
        with self.lock:
            lost, proc.pending = set(proc.pending), set()
        for job_id in lost:
            job = jobs.load(job_id) or {"id": job_id, "started": time.time(), "params": {}}
            job.update(status="error", step=None, finished=time.time(),
                       error=f"The {self.mode} worker stopped (exit code {proc.returncode}). Run it again.")
            jobs.save(job, tries=40)

    def stop(self) -> None:
        with self.lock:
            proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:  # a worker deep in a streamed read: end it
            proc.kill()


_WORKERS = dict((m, _Worker(m)) for m in ("remote", "download"))


def _stop_workers() -> None:
    for worker in _WORKERS.values():
        worker.stop()


atexit.register(_stop_workers)


@server.route("/api/where")
def api_where():
    try:
        lon, lat = float(request.args["lon"]), float(request.args["lat"])
    except (KeyError, TypeError, ValueError):
        return _error("give lon and lat", 400)
    vpus = direct.vpus_at(lon, lat)
    body = {"lon": lon, "lat": lat, "vpus": vpus,
            "slim": [v for v in vpus if DATASET is not None and v in DATASET.manifest["vpus"]]}
    if vpus:
        body["downloaded"] = direct.downloaded(vpus[0])
        try:
            pkg = direct.packages().get(vpus[0])
        except Exception as exc:  # S3 listing unavailable: say so, keep the rest
            body["listingError"] = f"{type(exc).__name__}: {exc}"
        else:
            if pkg:
                body["package"] = {"name": pkg["name"], "mb": round(pkg["bytes"] / 1e6, 1)}
    return _json(body, cache_s=0)


@server.route("/api/pick", methods=["POST"])
def api_pick():
    body = request.get_json(force=True, silent=True) or {}
    try:
        params = {"lon": float(body["lon"]), "lat": float(body["lat"]),
                  "method": str(body.get("method") or "slim"), "scope": str(body.get("scope") or "catchment"),
                  "tol": body.get("tol")}
    except (KeyError, TypeError, ValueError):
        return _error("give lon, lat, method and scope", 400)
    if params["method"] not in jobs.METHODS or params["scope"] not in jobs.SCOPES:
        return _error(f"method is one of {', '.join(jobs.METHODS)}; scope is one of {', '.join(jobs.SCOPES)}", 400)
    if not (-180 <= params["lon"] <= 180 and -90 <= params["lat"] <= 90):
        return _error("lon/lat outside the valid range", 400)
    if params["method"] == "slim" and DATASET is not None:
        try:
            params["tol"] = DATASET.tolerance(params["tol"])
        except (TypeError, ValueError) as exc:
            return _error(str(exc), 400)
    jobs.prune()
    job = {"id": uuid.uuid4().hex[:12], "status": "running", "started": time.time(), "params": params,
           "step": "queued"}
    if not jobs.save(job, tries=5):
        return _error("The job could not be recorded.", 500)
    if params["method"] == "slim":
        _SLIM_POOL.submit(jobs.run, job, functools.partial(jobs.pick_slim, DATASET))
    else:
        try:
            _WORKERS[params["method"]].submit(job)
        except OSError as exc:
            job.update(status="error", step=None, finished=time.time(),
                       error=f"The {params['method']} worker did not start: {exc}")
            jobs.save(job, tries=5)
    return _json({"job": job["id"]}, cache_s=0)


@server.route("/api/job/<job_id>")
def api_job(job_id: str):
    job = jobs.load(job_id) if _JOB_ID.match(job_id) else None
    if job is None:
        return _error("This job is unknown here; the app may have restarted. Run it again.", 404, status="unknown")
    job["elapsed_s"] = round((job.get("finished") or time.time()) - job["started"], 1)
    return _json(job, cache_s=0)


@server.route("/api/qa")
def api_qa():
    if (bad := _need_data()) is not None:
        return bad
    try:
        w, s, e, n = _bbox()
    except ValueError as exc:
        return _error(str(exc), 400)
    if (e - w) * (n - s) > MAX_CATCHMENTS_BOX:
        return _json({"type": "FeatureCollection", "features": [], "status": "too-large"})
    return _collection(DATASET.qa_in_bbox(w, s, e, n))


@server.route("/api/values")
def api_values():
    if (bad := _need_data()) is not None:
        return bad
    if VALUES is None:
        return _error("No precomputed values here (bundle v2 values folder).", 503, status="unavailable")
    try:
        nid = _int_arg("nhdplusid")
        max_reaches = _int_arg("max_reaches", 3000)
        max_hops = _int_arg("max_hops", 190)
    except ValueError:
        return _error("nhdplusid, max_reaches and max_hops must be integers", 400)
    if nid is None:
        return _error("give nhdplusid", 400)
    from hrslim.values import watershed_values
    t0 = time.perf_counter()
    try:
        out = watershed_values(DATASET, VALUES, nid, max_reaches=max_reaches, max_hops=max_hops)
    except FileNotFoundError as exc:
        return _error(f"No precomputed values for this region ({Path(str(exc.filename)).name}).", 404,
                      status="unavailable")
    out["ms"] = round(1000 * (time.perf_counter() - t0), 1)
    return _json(out)


@server.route("/api/extras")
def api_extras():
    if (bad := _need_data()) is not None:
        return bad
    if VALUES is None:
        return _error("No precomputed values here (bundle v2 values folder).", 503, status="unavailable")
    try:
        nid = _int_arg("nhdplusid")
        fraction = float(request.args.get("fraction", 0.5))
    except ValueError:
        return _error("nhdplusid must be an integer and fraction a number", 400)
    if nid is None:
        return _error("give nhdplusid", 400)
    from hrslim.values import flowline_extras
    try:
        out = flowline_extras(DATASET, VALUES, nid, fraction)
    except FileNotFoundError as exc:
        return _error(f"No precomputed extras for this region ({Path(str(exc.filename)).name}).", 404,
                      status="unavailable")
    if out is None:
        return _error("reach not found", 404, status="unknown")
    return _json(dict(out, status="ok"))


if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("PORT") or 8030))
