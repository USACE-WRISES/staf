"""NHDPlus HR straight from the USGS packages, for side-by-side tests with the slim copy.

Two ways to read a USGS regional package (a zipped File Geodatabase on S3):

- ``remote``: GDAL streams it in place (``/vsizip//vsicurl/<zip url>/<name>.gdb``);
- ``download``: the zip is downloaded once into a cache folder and unzipped, then
  read locally.

A ``Region`` caches what later clicks reuse: the path, the field names, the VAA
topology (for the upstream walk) and the catchment id to row map (catchments are
then read by row, which is much faster than ``NHDPlusID IN (...)`` lists). The
walk follows the STAF site engine's rules (``dnhydroseq`` membership, the budget
checked before every level).

USGS packages are HU4 regions in the lower 48 (HU8 in Alaska); the point to package
lookup uses the outlines in ``vpu_index.geojson`` (USGS fabric API, simplified).
A package ``url`` may also be a local zip path (tests, offline use).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import warnings
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import shapely
from shapely.errors import GEOSException

# NHDFlowline is "Measured 3D MultiLineString"; pyogrio drops M with this
# warning on every read, which only floods the log.
warnings.filterwarnings("ignore", message=r"Measured \(M\) geometry types are not supported")

S3_BASE = "https://prd-tnm.s3.amazonaws.com/"
S3_PREFIX = "StagedProducts/Hydrography/NHDPlusHR/VPU/Current/GDB/"
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_NAME = re.compile(r"^NHDPLUS_H_(\d{4}i?|\d{8})_HU(4|8)(?:_(\d{8}))?_GDB\.zip$")
INDEX_PATH = Path(__file__).resolve().parent / "vpu_index.geojson"
CACHE_DIR = Path(os.environ.get("HR_DIRECT_CACHE") or (Path(tempfile.gettempdir()) / "hr_direct"))
#: Downloaded regions are evicted, oldest use first, to stay under this many bytes
#: (Posit Connect Cloud stops an app past 24 GB of disk).
CACHE_CAP_BYTES = int(float(os.environ.get("HR_DIRECT_CACHE_GB") or 12) * 1e9)
MODES = ("remote", "download")
#: The STAF apps' interactive budget (site engine ``INTERACTIVE_CONFIG``).
MAX_REACHES = 3000
MAX_HOPS = 190

# GDAL settings for streaming zips: a cache large enough to hold a region's big
# tables, so repeated reads stop re-downloading (they still re-inflate).
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".zip")
os.environ.setdefault("CPL_VSIL_CURL_CACHE_SIZE", str(512 * 2 ** 20))
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")

Progress = Optional[Callable[[str], None]]

_lock = threading.Lock()
_packages: Optional[dict] = None
_index = None
_regions: dict = {}
_proj_lock = threading.Lock()
_projections: dict = {}


def _say(progress: Progress, text: str) -> None:
    if progress is not None:
        progress(text)


def _is_local(url: str) -> bool:
    return not url.lower().startswith(("http://", "https://"))


def packages() -> dict:
    """``vpu -> {vpu, name, url, bytes}`` from the S3 listing (cached per process),
    or from the JSON file ``HR_DIRECT_PACKAGES`` names (a local mirror, tests)."""
    global _packages
    local = os.environ.get("HR_DIRECT_PACKAGES")
    if local:
        return json.loads(Path(local).read_text(encoding="utf-8"))
    with _lock:
        if _packages is not None:
            return _packages
    import requests
    out, token = {}, None
    while True:
        params = [("list-type", "2"), ("prefix", S3_PREFIX)]
        if token:
            params.append(("continuation-token", token))
        r = requests.get(S3_BASE, params=params, timeout=60)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for c in root.findall(_NS + "Contents"):
            key = c.find(_NS + "Key").text
            name = key.rsplit("/", 1)[-1]
            m = _NAME.match(name)
            if m:
                out[m.group(1)] = {"vpu": m.group(1), "name": name, "url": S3_BASE + key,
                                   "bytes": int(c.find(_NS + "Size").text)}
        nxt = root.find(_NS + "NextContinuationToken")
        if nxt is None:
            break
        token = nxt.text
    with _lock:
        _packages = out
    return out


def vpus_at(lon: float, lat: float) -> list[str]:
    """Package codes whose outline holds the point (Great Lakes "i" units last)."""
    global _index
    with _lock:
        if _index is None:
            data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            codes = [f["properties"]["vpu"] for f in data["features"]]
            geoms = shapely.from_geojson([json.dumps(f["geometry"]) for f in data["features"]])
            _index = (codes, shapely.STRtree(geoms))
        codes, tree = _index
    pt = shapely.Point(lon, lat)
    found = sorted({codes[i] for i in tree.query(pt, predicate="intersects")})
    if not found:
        found = sorted({codes[i] for i in tree.query_nearest(pt, max_distance=0.01)})
    return [c for c in found if not c.endswith("i")] + [c for c in found if c.endswith("i")]


def region(vpu: str, mode: str, pkg: Optional[dict] = None) -> "Region":
    """The shared ``Region`` for a package read one way (``pkg`` defaults to the S3 listing)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if pkg is None:
        listing = packages()
        if vpu not in listing:
            raise KeyError(f"USGS has no NHDPlus HR package named {vpu}")
        pkg = listing[vpu]
    with _lock:
        reg = _regions.get((vpu, mode))
        if reg is None:
            reg = _regions[(vpu, mode)] = Region(pkg, mode)
        return reg


def _cached_gdb(folder: Path) -> Optional[Path]:
    if not folder.is_dir():
        return None
    found = [p for p in folder.glob("*.gdb") if p.is_dir()]
    return found[0] if found else None


def downloaded(vpu: str) -> bool:
    """Whether the package is already unzipped in the download cache."""
    return _cached_gdb(CACHE_DIR / vpu) is not None


def _folder_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _make_room(need: int, keep: str) -> list[str]:
    """Delete downloaded regions, least recently used first, until ``need`` more
    bytes fit under ``CACHE_CAP_BYTES``. Returns the regions removed."""
    if not CACHE_DIR.is_dir():
        return []
    folders = [d for d in CACHE_DIR.iterdir() if d.is_dir() and d.name != keep]
    sizes = dict((d, _folder_bytes(d)) for d in folders)
    total = sum(sizes.values())
    removed = []
    for d in sorted(folders, key=lambda p: p.stat().st_mtime):
        if total + need <= CACHE_CAP_BYTES:
            break
        with _lock:
            _regions.pop((d.name, "download"), None)
        shutil.rmtree(d, ignore_errors=True)
        total -= sizes[d]
        removed.append(d.name)
    return removed


def area_sqkm(geom) -> float:
    """Area of a lon/lat (NAD83) geometry in EPSG:5070, square kilometres."""
    return round(float(_to_albers(geom).area) / 1e6, 4)


def _transformer(src: int, dst: int):
    from pyproj import Transformer
    with _proj_lock:
        if (src, dst) not in _projections:
            _projections[(src, dst)] = Transformer.from_crs(src, dst, always_xy=True)
        return _projections[(src, dst)]


def _to_albers(geoms):
    tr = _transformer(4269, 5070)
    return shapely.transform(geoms, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))


def _from_albers(geoms):
    tr = _transformer(5070, 4269)
    return shapely.transform(geoms, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))


class Region:
    """One USGS package read one way, with the caches later clicks reuse."""

    def __init__(self, pkg: dict, mode: str):
        self.pkg = pkg
        self.mode = mode
        self.path: Optional[str] = None
        self.fields: dict = {}
        self.vaa: Optional[dict] = None
        self.cat_fid: Optional[dict] = None
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ opening
    def ensure(self, timings: dict, progress: Progress = None) -> str:
        """Open the package (downloading it first in download mode). The one-time
        costs land in ``timings`` the first time only."""
        import pyogrio
        with self.lock:
            if self.path is not None:
                if self.mode != "download":
                    return self.path
                if Path(self.path).exists():
                    try:
                        os.utime(Path(self.path).parent)     # recently used
                    except OSError:
                        pass
                    return self.path
                self.path, self.vaa, self.cat_fid = None, None, None   # evicted: fetch again
            if self.mode == "download":
                path = self._download(timings, progress)
            elif _is_local(self.pkg["url"]):
                path = "/vsizip/" + Path(self.pkg["url"]).as_posix() + "/" + self.pkg["name"][:-4] + ".gdb"
            else:
                path = "/vsizip//vsicurl/" + self.pkg["url"] + "/" + self.pkg["name"][:-4] + ".gdb"
            _say(progress, "opening the geodatabase")
            t0 = time.perf_counter()
            fields = {}
            for layer in ("NHDFlowline", "NHDPlusFlowlineVAA", "NHDPlusCatchment"):
                names = pyogrio.read_info(path, layer=layer)["fields"]
                fields[layer] = dict((str(f).lower(), str(f)) for f in names)
            timings["open_s"] = round(time.perf_counter() - t0, 2)
            self.fields, self.path = fields, path
            return path

    def _download(self, timings: dict, progress: Progress) -> str:
        folder = CACHE_DIR / self.pkg["vpu"]
        have = _cached_gdb(folder)
        if have is not None:
            timings["download_note"] = "already on disk"
            os.utime(folder)
            return str(have)
        removed = _make_room(4 * int(self.pkg["bytes"]), keep=self.pkg["vpu"])
        if removed:
            timings["evicted"] = removed
        folder.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="_get_", dir=folder))
        try:
            zpath = work / self.pkg["name"]
            t0 = time.perf_counter()
            self._fetch(zpath, progress)
            t1 = time.perf_counter()
            _say(progress, "unzipping")
            with zipfile.ZipFile(zpath) as z:
                z.extractall(work / "x")
            zpath.unlink()
            src = _cached_gdb(work / "x")
            if src is None:
                raise RuntimeError(f"{self.pkg['name']} holds no .gdb folder")
            try:
                src.rename(folder / src.name)
            except OSError:
                if _cached_gdb(folder) is None:              # else another worker finished first
                    raise
            t2 = time.perf_counter()
        finally:
            shutil.rmtree(work, ignore_errors=True)
        mb = int(self.pkg["bytes"]) / 1e6
        timings.update(download_s=round(t1 - t0, 2), unzip_s=round(t2 - t1, 2),
                       download_mb=round(mb, 1), mb_per_s=round(mb / max(t1 - t0, 1e-6), 1))
        return str(_cached_gdb(folder))

    def _fetch(self, dest: Path, progress: Progress) -> None:
        total = int(self.pkg["bytes"]) / 1e6
        if _is_local(self.pkg["url"]):
            shutil.copyfile(self.pkg["url"], dest)
            return
        import requests
        done, last = 0, 0.0
        with requests.get(self.pkg["url"], stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
                    done += len(chunk)
                    now = time.perf_counter()
                    if now - last >= 1.0:
                        last = now
                        _say(progress, f"downloading {done / 1e6:,.0f} of {total:,.0f} MB")

    def _col(self, layer: str, name: str) -> str:
        return self.fields[layer][name.lower()]

    def _read(self, layer: str, names: list[str], **kw):
        import pyogrio.raw
        meta, fids, geom, data = pyogrio.raw.read(self.path, layer=layer,
                                                  columns=[self._col(layer, n) for n in names], **kw)
        return fids, geom, dict((str(f).lower(), v) for f, v in zip(meta["fields"], data))

    # ------------------------------------------------------------------ queries
    def nearest(self, lon: float, lat: float, box_m: float = 200.0) -> Optional[dict]:
        """The nearest network flowline within the box, as a GeoJSON feature."""
        dlat = box_m / 111_320.0
        dlon = dlat / max(np.cos(np.radians(lat)), 0.2)
        _, geom, data = self._read("NHDFlowline", ["NHDPlusID", "GNIS_Name", "ReachCode", "FCode", "LengthKM", "InNetwork"],
                                   force_2d=True, bbox=(lon - dlon, lat - dlat, lon + dlon, lat + dlat))
        if geom is None or not len(geom):
            return None
        lines = shapely.from_wkb(geom)
        ok = np.nonzero(np.asarray(data["innetwork"]) == 1)[0]
        if not ok.size:
            return None
        scale = np.cos(np.radians(lat))
        d = shapely.distance(shapely.transform(lines[ok], lambda c: c * [scale, 1.0]), shapely.Point(lon * scale, lat))
        j = int(ok[int(np.argmin(d))])
        nid = int(round(float(data["nhdplusid"][j])))
        props = {"nhdplusid": nid, "gnis_name": data["gnis_name"][j] or None,
                 "reachcode": data["reachcode"][j], "fcode": int(data["fcode"][j]),
                 "lengthkm": float(data["lengthkm"][j]), "snap_m": round(float(np.min(d)) * 111_320.0, 1)}
        _, _, vaa = self._read("NHDPlusFlowlineVAA", ["NHDPlusID", "TotDASqKm", "HydroSeq", "StreamOrde"],
                               where=f"{self._col('NHDPlusFlowlineVAA', 'NHDPlusID')} = {nid}", read_geometry=False)
        if len(vaa["nhdplusid"]):
            props.update(totdasqkm=float(vaa["totdasqkm"][0]), hydroseq=int(vaa["hydroseq"][0]),
                         streamorde=int(vaa["streamorde"][0]))
        return {"type": "Feature", "properties": props, "geometry": json.loads(shapely.to_geojson(lines[j]))}

    def catchment(self, nid: int) -> Optional[dict]:
        """The reach's catchment, with the area USGS publishes and the area measured here."""
        _, geom, data = self._read("NHDPlusCatchment", ["NHDPlusID", "AreaSqKm"], force_2d=True,
                                   where=f"{self._col('NHDPlusCatchment', 'NHDPlusID')} = {int(nid)}")
        if geom is None or not len(geom):
            return None
        poly = shapely.from_wkb(geom[0])
        return {"type": "Feature",
                "properties": {"nhdplusid": int(nid), "areasqkm": float(data["areasqkm"][0]),
                               "measuredSqkm": area_sqkm(poly)},
                "geometry": json.loads(shapely.to_geojson(poly))}

    def _topology(self, timings: dict, progress: Progress) -> dict:
        with self.lock:
            if self.vaa is None:
                _say(progress, "reading the VAA table")
                t0 = time.perf_counter()
                _, _, data = self._read("NHDPlusFlowlineVAA", ["NHDPlusID", "HydroSeq", "DnHydroSeq", "TotDASqKm"],
                                        read_geometry=False)
                ids = np.round(np.asarray(data["nhdplusid"], dtype="float64")).astype("int64")
                hs = np.round(np.asarray(data["hydroseq"], dtype="float64")).astype("int64")
                dn = np.round(np.asarray(data["dnhydroseq"], dtype="float64")).astype("int64")
                order = np.argsort(dn, kind="stable")
                self.vaa = {"ids": ids, "hs": hs, "dn_sorted": dn[order], "dn_order": order,
                            "row": dict((int(v), i) for i, v in enumerate(ids)),
                            "da": np.asarray(data["totdasqkm"], dtype="float64")}
                timings["topology_s"] = round(time.perf_counter() - t0, 2)
            if self.cat_fid is None:
                _say(progress, "indexing the catchment table")
                t0 = time.perf_counter()
                fids, _, data = self._read("NHDPlusCatchment", ["NHDPlusID"], read_geometry=False, return_fids=True)
                cid = np.round(np.asarray(data["nhdplusid"], dtype="float64")).astype("int64")
                self.cat_fid = dict(zip(cid.tolist(), np.asarray(fids).tolist()))
                timings["catchment_index_s"] = round(time.perf_counter() - t0, 2)
            return self.vaa

    def watershed(self, nid: int, timings: dict, progress: Progress = None, *,
                  max_reaches: int = MAX_REACHES, max_hops: int = MAX_HOPS) -> dict:
        """The engine's walk on this package, then the union of the tree's catchments."""
        import pyogrio.raw
        v = self._topology(timings, progress)
        if int(nid) not in v["row"]:
            return {"status": "failed", "reason": "reach not in the VAA table"}
        _say(progress, "walking upstream")
        t0 = time.perf_counter()
        tree, frontier, hops = {int(nid)}, [v["row"][int(nid)]], 0
        while frontier:
            if hops >= max_hops or len(tree) >= max_reaches:
                timings["walk_s"] = round(time.perf_counter() - t0, 3)
                return {"status": "refused", "nReaches": len(tree), "nHops": hops,
                        "reason": (f"watershed exceeds the engine budget ({len(tree)} reaches, {hops} hops; "
                                   f"the budget is {max_reaches} reaches and {max_hops} hops)")}
            nxt = []
            for r in frontier:
                h = int(v["hs"][r])
                if not h:
                    continue
                lo = np.searchsorted(v["dn_sorted"], h, "left")
                hi = np.searchsorted(v["dn_sorted"], h, "right")
                for p in v["dn_order"][lo:hi]:
                    pid = int(v["ids"][p])
                    if pid not in tree:
                        tree.add(pid)
                        if int(v["hs"][p]):
                            nxt.append(int(p))
            frontier = nxt
            hops += 1
        t1 = time.perf_counter()
        fids = np.array(sorted(self.cat_fid[i] for i in tree if i in self.cat_fid), dtype=np.int64)
        if not fids.size:
            return {"status": "failed", "reason": "no catchments for the upstream tree",
                    "nReaches": len(tree), "nHops": hops}
        _say(progress, f"reading {len(fids):,} catchments")
        _, _, geom, _ = pyogrio.raw.read(self.path, layer="NHDPlusCatchment", columns=[], fids=fids, force_2d=True)
        t2 = time.perf_counter()
        _say(progress, "joining the catchments")
        polys = _to_albers(shapely.from_wkb(geom))
        try:
            union = shapely.union_all(polys)
        except GEOSException:
            union = shapely.union_all(shapely.make_valid(polys))
        t3 = time.perf_counter()
        timings.update(walk_s=round(t1 - t0, 3), catchments_s=round(t2 - t1, 2), union_s=round(t3 - t2, 2))
        da = float(v["da"][v["row"][int(nid)]])
        area = round(float(union.area) / 1e6, 4)
        return {"status": "ok", "nReaches": len(tree), "nHops": hops, "nCatchments": int(len(fids)),
                "areaSqkm": area, "vaaAreaSqkm": da, "areaAgreement": round(area / da, 4) if da > 0 else None,
                "geometry": json.loads(shapely.to_geojson(shapely.set_precision(_from_albers(union), 1e-6)))}
