"""Direct GDAL access to the USGS NHDPlus HR zipped FileGDBs on S3, instrumented.

The question this answers: can an app read one flowline and its catchment straight
from the USGS regional package (a zipped File Geodatabase on S3) quickly enough for
an interactive click, without downloading the whole package first?

Two modes:

- ``remote``: GDAL opens ``/vsizip//vsicurl/<zip url>/<name>.gdb`` and queries it in
  place, with HTTP range requests;
- ``download``: the zip is downloaded once and unzipped to a cache folder, then the
  same queries run against the local FileGDB.

Each run happens in a child process, so GDAL's caches start cold and GDAL's own
debug log (``VSICURL: Downloading a-b`` lines on stderr) can be counted per step:
the child writes a marker line to stderr after every step.

Self-contained (no STAF imports) so it deploys as part of the Posit benchmark app.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Optional

S3_BASE = "https://prd-tnm.s3.amazonaws.com/"
S3_PREFIX = "StagedProducts/Hydrography/NHDPlusHR/VPU/Current/GDB/"
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
#: Same pattern as tools/hr-slim/hrbuild/source.py.
_NAME = re.compile(r"^NHDPLUS_H_(\d{4}i?|\d{8})_HU(4|8)(?:_(\d{8}))?_GDB\.zip$")
STEP_MARK = "@@STEP "
_DOWNLOAD = re.compile(r"Downloading (\d+)-(\d+)")
HERE = Path(__file__).resolve().parent
INDEX_PATH = HERE / "vpu_index.geojson"
#: Click offsets in degrees from the given point: the point, about 300 m away,
#: about 10 km away, and the point again (purely warm).
CLICK_OFFSETS = [(0.0, 0.0), (0.003, 0.002), (0.1, 0.0), (0.0, 0.0)]


# --------------------------------------------------------------------------- #
# packages and the region index
# --------------------------------------------------------------------------- #
def list_packages(timeout: float = 60.0) -> list[dict]:
    import requests
    out, token = [], None
    while True:
        params = [("list-type", "2"), ("prefix", S3_PREFIX)]
        if token:
            params.append(("continuation-token", token))
        r = requests.get(S3_BASE, params=params, timeout=timeout)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for c in root.findall(_NS + "Contents"):
            key = c.find(_NS + "Key").text
            name = key.rsplit("/", 1)[-1]
            m = _NAME.match(name)
            if m:
                out.append({"vpu": m.group(1), "name": name, "url": S3_BASE + key,
                            "bytes": int(c.find(_NS + "Size").text)})
        nxt = root.find(_NS + "NextContinuationToken")
        if nxt is None:
            return sorted(out, key=lambda p: p["vpu"])
        token = nxt.text


def gdb_name(pkg: dict) -> str:
    """The geodatabase folder inside the zip carries the zip's name (dated packages
    keep the date)."""
    return pkg["name"][:-4] + ".gdb"


def remote_path(pkg: dict) -> str:
    return "/vsizip//vsicurl/" + pkg["url"] + "/" + gdb_name(pkg)


def load_index(path: Path = INDEX_PATH):
    """HU4 outlines (and their package codes) for point lookups."""
    import shapely
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    codes = [f["properties"]["vpu"] for f in data["features"]]
    geoms = shapely.from_geojson([json.dumps(f["geometry"]) for f in data["features"]])
    return codes, geoms, shapely.STRtree(geoms)


def vpus_at(lon: float, lat: float, index=None) -> list[str]:
    """Package codes whose outline holds the point (several near a boundary)."""
    import shapely
    codes, geoms, tree = index or load_index()
    hits = tree.query(shapely.Point(lon, lat), predicate="intersects")
    found = sorted({codes[i] for i in hits})
    if not found:                              # just off an outline: nearest within ~1 km
        near = tree.query_nearest(shapely.Point(lon, lat), max_distance=0.01)
        found = sorted({codes[i] for i in near})
    return found


# --------------------------------------------------------------------------- #
# the GDAL log
# --------------------------------------------------------------------------- #
def parse_log(text: str) -> list[dict]:
    """Per-step HTTP range requests and bytes from the child's stderr: every
    ``Downloading a-b`` line counts toward the next ``@@STEP`` marker."""
    steps, n, b, sizes = [], 0, 0, []
    for line in text.splitlines():
        if line.startswith(STEP_MARK):
            payload = json.loads(line[len(STEP_MARK):])
            payload.update(requests=n, bytes=b)
            steps.append(payload)
            n, b = 0, 0
            continue
        if "VSICURL" in line or "Downloading" in line:
            for a, z in _DOWNLOAD.findall(line):
                size = int(z) - int(a) + 1
                n += 1
                b += size
                sizes.append(size)
    return steps


def _peak_rss_mb() -> Optional[float]:
    try:
        import resource                         # Linux (Posit)
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except ImportError:
        pass
    try:
        import psutil                           # Windows
        return round(psutil.Process().memory_info().peak_wset / 2 ** 20, 1)
    except Exception:
        return None


def gdal_env(cache_mb: int) -> dict:
    env = dict(os.environ)
    env.update({"CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".zip", "CPL_DEBUG": "ON",
                "GDAL_HTTP_MAX_RETRY": "3", "GDAL_HTTP_RETRY_DELAY": "2",
                "GDAL_HTTP_TIMEOUT": "60", "PYTHONIOENCODING": "utf-8"})
    for key in ("CPL_VSIL_CURL_CACHE_SIZE", "VSI_CACHE", "VSI_CACHE_SIZE", "CPL_LOG"):
        env.pop(key, None)
    if cache_mb > 0:
        env.update({"CPL_VSIL_CURL_CACHE_SIZE": str(cache_mb * 2 ** 20), "VSI_CACHE": "TRUE",
                    "VSI_CACHE_SIZE": str(cache_mb * 2 ** 20)})
    return env


# --------------------------------------------------------------------------- #
# the child process
# --------------------------------------------------------------------------- #
def _worker(args: dict) -> dict:
    import numpy as np
    import pyogrio
    import pyogrio.raw
    import shapely

    t_last = [time.perf_counter()]

    def step(label: str, **extra) -> float:
        now = time.perf_counter()
        secs = round(now - t_last[0], 2)
        t_last[0] = now
        sys.stderr.write(STEP_MARK + json.dumps(dict(extra, label=label, seconds=secs)) + "\n")
        sys.stderr.flush()
        return secs

    pkg = args["package"]
    out: dict = {"clicks": [], "download": None}
    if args["mode"] == "download":
        import requests
        cache = Path(args["cache_dir"]) / pkg["vpu"]
        zpath = cache / pkg["name"]
        gdb_dir = cache / gdb_name(pkg)
        if args.get("fresh") and cache.exists():
            shutil.rmtree(cache, ignore_errors=True)
        cache.mkdir(parents=True, exist_ok=True)
        info = {"cached": gdb_dir.exists()}
        if not gdb_dir.exists():
            t0 = time.perf_counter()
            with requests.get(pkg["url"], stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(zpath, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
            secs = time.perf_counter() - t0
            info.update(download_s=round(secs, 1), mb_per_s=round(pkg["bytes"] / 1e6 / max(secs, 1e-6), 1))
            step("download the zip")
            with zipfile.ZipFile(zpath) as z:
                z.extractall(cache)
            info["unzipped_mb"] = round(sum(p.stat().st_size for p in gdb_dir.rglob("*") if p.is_file()) / 1e6, 1)
            zpath.unlink(missing_ok=True)
            step("unzip")
        out["download"] = info
        path = str(gdb_dir)
    else:
        path = remote_path(pkg)

    layers = [str(name) for name, _ in pyogrio.list_layers(path)]
    step("open and list layers", layers=len(layers))
    fields = {}
    for layer in ("NHDFlowline", "NHDPlusFlowlineVAA", "NHDPlusCatchment"):
        fields[layer] = dict((str(f).lower(), str(f)) for f in pyogrio.read_info(path, layer=layer)["fields"])
    step("read the three schemas")

    def col(layer, name):
        return fields[layer][name.lower()]

    def read(layer, names, **kw):
        """pyogrio returns columns in the file's order, not the asked order, so map
        them by name (lower case); a ``where`` column must be among ``names``."""
        meta, _, geom, data = pyogrio.raw.read(path, layer=layer, columns=[col(layer, n) for n in names], **kw)
        return geom, dict((str(f).lower(), v) for f, v in zip(meta["fields"], data))

    lon0, lat0 = args["lon"], args["lat"]
    dlat = args["box_m"] / 111_320.0
    for k in range(args["clicks"]):
        dx, dy = CLICK_OFFSETS[k % len(CLICK_OFFSETS)]
        lon, lat = lon0 + dx, lat0 + dy
        dlon = dlat / max(np.cos(np.radians(lat)), 0.2)
        label = f"click {k + 1}"
        geom, data = read("NHDFlowline", ["NHDPlusID", "GNIS_Name", "ReachCode", "FCode", "LengthKM", "InNetwork"],
                          force_2d=True, bbox=(lon - dlon, lat - dlat, lon + dlon, lat + dlat))
        lines = shapely.from_wkb(geom) if geom is not None else np.empty(0, dtype=object)
        n_box = len(lines)
        step(f"{label}: flowlines in the box", features=n_box)
        rec = {"click": k + 1, "lon": round(lon, 6), "lat": round(lat, 6), "flowlines_in_box": n_box}
        if n_box:
            innet = np.asarray(data["innetwork"]) == 1
            if innet.any():
                idx = np.nonzero(innet)[0]
                scale = np.cos(np.radians(lat))
                d = shapely.distance(shapely.transform(lines[idx], lambda c: c * [scale, 1.0]),
                                     shapely.Point(lon * scale, lat))
                j = int(idx[int(np.argmin(d))])
                nid = int(round(float(data["nhdplusid"][j])))
                rec.update(nhdplusid=nid, gnis_name=data["gnis_name"][j], reachcode=data["reachcode"][j],
                           fcode=int(data["fcode"][j]), lengthkm=float(data["lengthkm"][j]),
                           snap_m=round(float(np.min(d)) * 111_320.0, 1),
                           flowline_vertices=int(shapely.get_num_coordinates(lines[j])))
                key = col("NHDPlusFlowlineVAA", "NHDPlusID")
                _, vdata = read("NHDPlusFlowlineVAA",
                                ["NHDPlusID", "TotDASqKm", "HydroSeq", "DnHydroSeq", "StreamOrde", "Slope"],
                                where=f"{key} = {nid}", read_geometry=False)
                step(f"{label}: VAA by NHDPlusID")
                if len(vdata["nhdplusid"]):
                    rec.update(totdasqkm=float(vdata["totdasqkm"][0]), hydroseq=int(vdata["hydroseq"][0]),
                               dnhydroseq=int(vdata["dnhydroseq"][0]), streamorde=int(vdata["streamorde"][0]),
                               slope=float(vdata["slope"][0]))
                key = col("NHDPlusCatchment", "NHDPlusID")
                cgeom, cdata = read("NHDPlusCatchment", ["NHDPlusID", "AreaSqKm"], where=f"{key} = {nid}",
                                    force_2d=True)
                step(f"{label}: catchment by NHDPlusID", features=0 if cgeom is None else len(cgeom))
                if cgeom is not None and len(cgeom):
                    cpoly = shapely.from_wkb(cgeom[0])
                    rec.update(catchment_areasqkm=float(cdata["areasqkm"][0]),
                               catchment_vertices=int(shapely.get_num_coordinates(cpoly)))
        out["clicks"].append(rec)
    out["peak_rss_mb"] = _peak_rss_mb()
    return out


# --------------------------------------------------------------------------- #
# the parent: run one benchmark
# --------------------------------------------------------------------------- #
def environment() -> dict:
    import pyogrio
    drivers = pyogrio.list_drivers()
    info = {"python": platform.python_version(), "platform": platform.platform(),
            "cpus": os.cpu_count(), "pyogrio": pyogrio.__version__,
            "gdal": pyogrio.__gdal_version_string__, "openfilegdb": "OpenFileGDB" in drivers,
            "tempdir": tempfile.gettempdir()}
    try:
        import psutil
        info["memory_gb"] = round(psutil.virtual_memory().total / 2 ** 30, 1)
    except Exception:
        pass
    usage = shutil.disk_usage(tempfile.gettempdir())
    info["temp_free_gb"] = round(usage.free / 2 ** 30, 1)
    return info


def s3_speed(url: str, mb: int = 20, timeout: float = 120.0) -> dict:
    """Throughput of one HTTP range request from the USGS bucket."""
    import requests
    t0 = time.perf_counter()
    r = requests.get(url, headers={"Range": f"bytes=0-{mb * 2 ** 20 - 1}"}, timeout=timeout)
    secs = time.perf_counter() - t0
    return {"status": r.status_code, "mb": round(len(r.content) / 1e6, 1), "seconds": round(secs, 2),
            "mb_per_s": round(len(r.content) / 1e6 / max(secs, 1e-6), 1)}


def vsi_check(pkg: dict) -> dict:
    """``/vsizip//vsicurl/`` plus OpenFileGDB on a (small) package: list its layers."""
    import pyogrio
    os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".zip")
    t0 = time.perf_counter()
    try:
        n = len(pyogrio.list_layers(remote_path(pkg)))
        return {"ok": True, "layers": n, "seconds": round(time.perf_counter() - t0, 1), "package": pkg["name"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "package": pkg["name"]}


def run(*, package: dict, lat: float, lon: float, mode: str = "remote", box_m: float = 200.0,
        cache_mb: int = 0, clicks: int = 3, fresh: bool = True,
        cache_dir: Optional[str] = None, timeout_s: float = 1800.0) -> dict:
    """One benchmark run in a fresh child process; per-step seconds, range requests
    and bytes, the click results, and a behaviour summary."""
    args = {"package": package, "lat": lat, "lon": lon, "mode": mode, "box_m": box_m,
            "clicks": clicks, "fresh": fresh,
            "cache_dir": cache_dir or str(Path(tempfile.gettempdir()) / "hr_gdal_poc")}
    t0 = time.perf_counter()
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", json.dumps(args)],
                          env=gdal_env(cache_mb), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout_s)
    wall = round(time.perf_counter() - t0, 1)
    steps = parse_log(proc.stderr)
    try:
        body = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        body = {"error": (proc.stderr or proc.stdout)[-2000:]}
    total_bytes = sum(s["bytes"] for s in steps)
    total_req = sum(s["requests"] for s in steps)
    first = 0.0
    for s in steps:                          # time to the first click's catchment
        first += s["seconds"]
        if s["label"].startswith("click 1: catchment"):
            break
    result = {
        "settings": {"mode": mode, "cache_mb": cache_mb, "box_m": box_m, "clicks": clicks},
        "package": {k: package[k] for k in ("vpu", "name", "bytes")},
        "wall_seconds": wall,
        "first_click_seconds": round(first, 1),
        "steps": steps,
        "http": {"requests": total_req, "mb": round(total_bytes / 1e6, 1),
                 "share_of_zip": round(total_bytes / package["bytes"], 2) if package["bytes"] else None},
        "returncode": proc.returncode,
    }
    result.update(body)
    return result


def table(result: dict) -> str:
    """Plain-text table of a run."""
    rows = [f"{result['package']['name']} ({result['package']['bytes'] / 1e6:.0f} MB), mode {result['settings']['mode']}, "
            f"GDAL cache {result['settings']['cache_mb'] or 'default'} MB"]
    for s in result["steps"]:
        rows.append(f"  {s['label']:<38} {s['seconds']:8.1f} s  {s['requests']:5d} requests  {s['bytes'] / 1e6:8.1f} MB")
    h = result["http"]
    rows.append(f"  first answer (open to click 1 catchment): {result['first_click_seconds']} s; whole run {result['wall_seconds']} s")
    rows.append(f"  HTTP: {h['requests']} range requests, {h['mb']} MB ({h['share_of_zip']} of the zip)")
    for c in result.get("clicks", []):
        rows.append(f"  click {c['click']}: {c.get('nhdplusid')} {c.get('gnis_name') or ''} "
                    f"(snap {c.get('snap_m')} m, catchment {c.get('catchment_areasqkm')} km2)")
    if result.get("download"):
        rows.append(f"  download: {result['download']}")
    rows.append(f"  peak memory of the run: {result.get('peak_rss_mb')} MB")
    return "\n".join(rows)


if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "--worker":
    print(json.dumps(_worker(json.loads(sys.argv[2])), default=str))
