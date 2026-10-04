"""Fetch jobs for the map's three-way comparison.

A job fetches the catchment or upstream watershed of the stream nearest a point,
one of three ways: ``remote`` (GDAL streams the USGS package on S3), ``download``
(the USGS package downloaded once) or ``slim`` (the slim copy). Job state lives in
small JSON files (``job_dir()``), so the web process, the USGS worker processes
(``hrslim.worker``) and any other web process all see the same state.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
import traceback
from pathlib import Path

import shapely
import shapely.geometry

from . import direct
from .reader import catchment_features, line_features

PICK_BOX_M = 200.0
METHODS = ("remote", "download", "slim")
SCOPES = ("catchment", "watershed")
KEEP_S = 7200


def job_dir() -> Path:
    return Path(os.environ.get("HR_DATA_JOBS") or (Path(tempfile.gettempdir()) / "hr_data_jobs"))


# ---------------------------------------------------------------------- store
def save(job: dict, *, tries: int = 1) -> bool:
    """Write the job's state atomically; retried because Windows refuses to
    replace a file another thread is reading."""
    folder = job_dir()
    for attempt in range(tries):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / f"{job['id']}.{os.getpid()}.{threading.get_ident()}.tmp"
            tmp.write_text(json.dumps(job, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, folder / f"{job['id']}.json")
            return True
        except OSError:
            if attempt + 1 < tries:
                time.sleep(0.05)
    return False


def load(job_id: str, *, tries: int = 3):
    path = job_dir() / f"{job_id}.json"
    for attempt in range(tries):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            if attempt + 1 < tries:
                time.sleep(0.05)
    return None


def prune() -> None:
    cutoff = time.time() - KEEP_S
    try:
        for f in job_dir().glob("*.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except OSError:
        pass


def run(job: dict, pick) -> dict:
    """Run ``pick(params, timings, progress)`` for the job; progress and the
    final state go to the job file."""
    timings: dict = {}
    t0 = time.perf_counter()

    def progress(text: str) -> None:
        job["step"] = text
        save(job)

    try:
        job.update(status="done", result=pick(job["params"], timings, progress))
    except Exception as exc:  # report it; never kill the worker
        job.update(status="error", error=f"{type(exc).__name__}: {exc}",
                   trace=traceback.format_exc()[-1500:])
    timings["total_s"] = round(time.perf_counter() - t0, 2)
    job.update(timings=timings, step=None, finished=time.time())
    save(job, tries=40)
    res = job.get("result") or {}
    print("PICK " + json.dumps({"params": job["params"], "status": job["status"], "result": res.get("status"),
                                "reason": res.get("reason") or job.get("error"), "timings": timings}),
          flush=True, file=_log_stream())
    return job


def _log_stream():
    import sys
    return sys.stderr                     # the worker's stdout carries its protocol


# ---------------------------------------------------------------------- results
def _nearest_feature(features: list, lon: float, lat: float):
    scale = math.cos(math.radians(lat))
    pt = shapely.Point(lon * scale, lat)
    best, best_d = None, None
    for f in features:
        g = shapely.transform(shapely.geometry.shape(f["geometry"]), lambda c: c * [scale, 1.0])
        d = shapely.distance(g, pt)
        if best_d is None or d < best_d:
            best, best_d = f, d
    return best, (None if best_d is None else round(float(best_d) * 111_320.0, 1))


def _polygon_stats(feature: dict) -> dict:
    geom = shapely.geometry.shape(feature["geometry"])
    return {"areaSqkm": direct.area_sqkm(geom), "vertices": int(shapely.get_num_coordinates(geom)),
            "polygonKb": round(len(json.dumps(feature["geometry"], separators=(",", ":"))) / 1024, 1)}


def _with_watershed(out: dict, ws: dict) -> dict:
    if ws["status"] != "ok":
        return dict(out, status=ws["status"], reason=ws.get("reason"),
                    nReaches=ws.get("nReaches"), nHops=ws.get("nHops"))
    out["polygon"] = {"type": "Feature", "properties": {}, "geometry": ws["geometry"]}
    out.update(_polygon_stats(out["polygon"]))
    out.update(nReaches=ws["nReaches"], nHops=ws["nHops"], publishedSqkm=ws.get("vaaAreaSqkm"))
    return out


def _agreement(out: dict) -> dict:
    a, b = out.get("areaSqkm"), out.get("publishedSqkm")
    out["agreement"] = round(a / b, 4) if a and b else None
    return out


# ---------------------------------------------------------------------- picks
def pick_slim(dataset, p: dict, timings: dict, progress) -> dict:
    """The slim copy: nearest network line in the box, then its catchment or the walk."""
    if dataset is None:
        return {"status": "failed", "reason": "This app holds no slim data."}
    lon, lat = p["lon"], p["lat"]
    tol = dataset.tolerance(p.get("tol"))
    progress("finding the stream")
    t0 = time.perf_counter()
    dlat = PICK_BOX_M / 111_320.0
    dlon = dlat / max(math.cos(math.radians(lat)), 0.2)
    lines = dataset.lines_in_bbox(lon - dlon, lat - dlat, lon + dlon, lat + dlat)
    reach, snap = _nearest_feature(line_features(lines), lon, lat)
    timings["find_s"] = round(time.perf_counter() - t0, 3)
    if reach is None:
        inside = [v for v in direct.vpus_at(lon, lat) if v in dataset.manifest["vpus"]]
        note = "" if inside else " The point is outside the slim copy's regions (the dashed boxes)."
        return {"status": "failed", "reason": f"No network stream within {PICK_BOX_M:.0f} m in the slim copy.{note}"}
    reach["properties"]["snap_m"] = snap
    nid = reach["properties"]["nhdplusid"]
    out = {"status": "ok", "reach": reach, "vpu": reach["properties"].get("vpuid"), "tolerance_m": tol}
    if p["scope"] == "catchment":
        progress("reading the catchment")
        t1 = time.perf_counter()
        cats = catchment_features(dataset.catchments_by_ids([nid], tol))
        timings["catchment_s"] = round(time.perf_counter() - t1, 3)
        if not cats:
            return dict(out, status="failed", reason="No catchment for this reach in the slim copy.")
        out["polygon"] = cats[0]
        out.update(_polygon_stats(cats[0]))
        return out
    progress("walking upstream and joining catchments")
    ws = dataset.watershed(nid, tol, max_reaches=direct.MAX_REACHES, max_hops=direct.MAX_HOPS)
    for key, name in (("walkMs", "walk_s"), ("catchmentsMs", "catchments_s"), ("unionMs", "union_s")):
        if ws.get(key) is not None:
            timings[name] = round(ws[key] / 1000, 3)
    return _agreement(_with_watershed(out, ws))


def pick_usgs(p: dict, timings: dict, progress) -> dict:
    """A USGS package, streamed (``remote``) or downloaded once (``download``)."""
    lon, lat = p["lon"], p["lat"]
    vpus = direct.vpus_at(lon, lat)
    if not vpus:
        return {"status": "failed", "reason": "No USGS NHDPlus HR package covers this point."}
    progress("looking up the USGS package")
    reg = direct.region(vpus[0], p["method"])
    out = {"status": "ok", "vpu": vpus[0], "package": reg.pkg["name"],
           "packageMb": round(reg.pkg["bytes"] / 1e6, 1)}
    reg.ensure(timings, progress)
    progress("finding the stream")
    t0 = time.perf_counter()
    reach = reg.nearest(lon, lat, PICK_BOX_M)
    timings["find_s"] = round(time.perf_counter() - t0, 3)
    if reach is None:
        return dict(out, status="failed", reason=f"No network stream within {PICK_BOX_M:.0f} m.")
    out["reach"] = reach
    nid = reach["properties"]["nhdplusid"]
    if p["scope"] == "catchment":
        progress("reading the catchment")
        t1 = time.perf_counter()
        poly = reg.catchment(nid)
        timings["catchment_s"] = round(time.perf_counter() - t1, 3)
        if poly is None:
            return dict(out, status="failed", reason="No catchment for this reach.")
        out["polygon"] = poly
        out.update(_polygon_stats(poly))
        out["publishedSqkm"] = poly["properties"]["areasqkm"]
        return _agreement(out)
    return _agreement(_with_watershed(out, reg.watershed(nid, timings, progress)))
