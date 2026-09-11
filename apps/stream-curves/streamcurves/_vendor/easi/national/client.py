"""Read the published national dataset on demand.

The dataset lives on the rolling GitHub prerelease (``DATASET_TAG``) as a
manifest plus per-HUC4 evidence files, per-region score files and per-region
PMTiles. ``EASI_NATIONAL_BASE`` overrides the base with another https base or
a local directory (the builder's staging folder, a desktop copy), so the app
reads either the same way.

Remote assets download once into a cache directory keyed by the manifest's
sha256 and are re-fetched only when the manifest changes. Release-asset URLs
redirect (302) to a signed object URL that expires; the resolved URL is kept
for a while and re-resolved on a 403/404, which is also what a replaced
(``--clobber``) asset looks like.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from . import DATASET_TAG, SCHEMA_VERSION, method_version, providers, records

DEFAULT_BASE = f"https://github.com/USACE-WRISES/staf/releases/download/{DATASET_TAG}/"
ENV_BASE = "EASI_NATIONAL_BASE"

MANIFEST = "manifest.json"
INDEX = "comid_huc4.parquet"
COVERAGE = "coverage.geojson"

_RESOLVE_TTL_S = 40 * 60.0
_TABLE_CACHE = 8


def evidence_asset(huc4: str) -> str:
    return f"evidence_{huc4}.parquet"


def scores_asset(vpu: str) -> str:
    return f"scores_{vpu}.parquet"


def tiles_asset(vpu: str) -> str:
    return f"tiles_{vpu}.pmtiles"


class DatasetError(RuntimeError):
    """The dataset could not be read (no manifest, no asset, bad hash)."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Dataset:
    """One published dataset: a base (https or a local directory) and a cache."""

    def __init__(self, base: Optional[str] = None, *, cache_dir=None,
                 manifest_ttl_s: float = 300.0, timeout: float = 60.0):
        base = base or os.environ.get(ENV_BASE) or DEFAULT_BASE
        self.remote = base.startswith(("http://", "https://"))
        self.base = base if not self.remote or base.endswith("/") else base + "/"
        self.local: Optional[Path] = None if self.remote else Path(base)
        self.timeout = timeout
        self.manifest_ttl_s = manifest_ttl_s
        self.cache_dir = Path(cache_dir or Path(tempfile.gettempdir()) / "easi_national")
        self._lock = threading.RLock()
        self._manifest: Optional[dict] = None
        self._manifest_at = 0.0
        self._tables: "OrderedDict[str, Any]" = OrderedDict()
        self._index = None
        self._resolved: dict[str, tuple[str, float]] = {}

    # ------------------------------------------------------------------ urls
    def url(self, name: str) -> str:
        return self.base + name

    def resolve(self, name: str, *, force: bool = False) -> str:
        """The URL to read ``name`` from: the signed redirect target when the
        host redirects, cached for a while; the plain URL otherwise."""
        url = self.url(name)
        now = time.monotonic()
        with self._lock:
            hit = self._resolved.get(name)
            if hit and not force and hit[1] > now:
                return hit[0]
        try:
            head = requests.head(url, allow_redirects=False, timeout=self.timeout)
        except requests.RequestException:
            return url
        target = head.headers.get("Location") if head.is_redirect else None
        resolved = target or url
        with self._lock:
            self._resolved[name] = (resolved, now + _RESOLVE_TTL_S)
        return resolved

    # -------------------------------------------------------------- manifest
    def manifest(self, *, refresh: bool = False) -> Optional[dict]:
        """The dataset manifest, or None when it cannot be read."""
        with self._lock:
            fresh = (self._manifest is not None
                     and time.monotonic() - self._manifest_at < self.manifest_ttl_s)
            if fresh and not refresh:
                return self._manifest
        try:
            if self.local is not None:
                text = (self.local / MANIFEST).read_text(encoding="utf-8")
            else:
                response = requests.get(self.url(MANIFEST), timeout=self.timeout)
                if response.status_code != 200:
                    return self._manifest
                text = response.text
            manifest = json.loads(text)
        except (OSError, ValueError, requests.RequestException):
            return self._manifest
        with self._lock:
            self._manifest = manifest
            self._manifest_at = time.monotonic()
        return manifest

    def _asset_entry(self, name: str) -> Optional[dict]:
        manifest = self.manifest() or {}
        entry = (manifest.get("assets") or {}).get(name)
        if entry:
            return entry
        for unit in (manifest.get("units") or {}).values():
            ev = unit.get("evidence") or {}
            if ev.get("asset") == name:
                return ev
        for block in (manifest.get("tiles") or {}, manifest.get("scores") or {}):
            for item in block.values():
                if item.get("asset") == name:
                    return item
        return None

    def asset_sha(self, name: str) -> Optional[str]:
        entry = self._asset_entry(name)
        return entry.get("sha256") if entry else None

    # ---------------------------------------------------------------- assets
    def asset_path(self, name: str) -> Optional[Path]:
        """A local path for ``name`` (the directory file, or the cached download)."""
        if self.local is not None:
            path = self.local / name
            return path if path.exists() else None
        sha = self.asset_sha(name)
        target = self.cache_dir / name
        sidecar = self.cache_dir / (name + ".sha256")
        with self._lock:
            if target.exists():
                have = sidecar.read_text(encoding="utf-8").strip() if sidecar.exists() else ""
                if sha is None or have == sha:
                    return target
            return self._download(name, target, sidecar, sha)

    def _download(self, name: str, target: Path, sidecar: Path,
                  sha: Optional[str]) -> Optional[Path]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        for attempt in range(2):
            url = self.resolve(name, force=attempt > 0)
            try:
                with requests.get(url, stream=True, timeout=self.timeout) as response:
                    if response.status_code in (403, 404) and attempt == 0:
                        continue
                    if response.status_code != 200:
                        return None
                    with open(tmp, "wb") as handle:
                        for chunk in response.iter_content(1 << 20):
                            handle.write(chunk)
            except requests.RequestException:
                return None
            break
        else:
            return None
        if sha is not None and _sha256(tmp) != sha:
            tmp.unlink(missing_ok=True)
            return None
        os.replace(tmp, target)
        sidecar.write_text(sha or "", encoding="utf-8")
        return target

    def range_reader(self, name: str) -> Callable[[int, int], bytes]:
        """A ``get_bytes(offset, length)`` over ``name`` for the tile store:
        file reads locally, HTTP Range requests remotely."""
        if self.local is not None:
            path = self.local / name

            def _local(offset: int, length: int) -> bytes:
                with open(path, "rb") as handle:
                    handle.seek(offset)
                    return handle.read(length)
            return _local

        def _remote(offset: int, length: int) -> bytes:
            for attempt in range(2):
                url = self.resolve(name, force=attempt > 0)
                headers = {"Range": f"bytes={offset}-{offset + length - 1}"}
                response = requests.get(url, headers=headers, timeout=self.timeout)
                if response.status_code == 206:
                    return response.content
                if response.status_code == 200:      # the host ignored Range
                    return response.content[offset:offset + length]
                if response.status_code in (403, 404) and attempt == 0:
                    continue
                raise DatasetError(f"{name}: HTTP {response.status_code} for range")
            raise DatasetError(f"{name}: could not resolve the asset")
        return _remote

    # ---------------------------------------------------------------- tables
    def _table(self, name: str):
        with self._lock:
            table = self._tables.get(name)
            if table is not None:
                self._tables.move_to_end(name)
                return table
        path = self.asset_path(name)
        if path is None:
            return None
        import pyarrow.parquet as pq
        table = pq.read_table(path)
        with self._lock:
            self._tables[name] = table
            while len(self._tables) > _TABLE_CACHE:
                self._tables.popitem(last=False)
        return table

    def _load_index(self):
        with self._lock:
            if self._index is not None:
                return self._index
        table = self._table(INDEX)
        if table is None:
            return None
        import numpy as np
        comids = np.asarray(table.column("comid").to_numpy(), dtype=np.int64)
        huc4s = np.asarray(table.column("huc4").to_pylist(), dtype=object)
        order = np.argsort(comids, kind="stable")
        index = (comids[order], huc4s[order])
        with self._lock:
            self._index = index
        return index

    def huc4_of(self, comids) -> dict[int, str]:
        """COMID -> HUC4 for the COMIDs the index knows."""
        index = self._load_index()
        if index is None:
            return {}
        import numpy as np
        sorted_comids, huc4s = index
        wanted = np.asarray([int(c) for c in comids], dtype=np.int64)
        positions = np.searchsorted(sorted_comids, wanted)
        out: dict[int, str] = {}
        for comid, pos in zip(wanted.tolist(), positions.tolist()):
            if pos < len(sorted_comids) and sorted_comids[pos] == comid:
                out[comid] = str(huc4s[pos])
        return out

    def _rows_for(self, name: str, comids: list[int]) -> dict[int, dict]:
        table = self._table(name)
        if table is None or not comids:
            return {}
        import pyarrow as pa
        import pyarrow.compute as pc
        column = pc.cast(table.column("comid"), pa.int64())
        mask = pc.is_in(column, value_set=pa.array(comids, pa.int64()))
        rows = table.filter(mask).to_pylist()
        return {int(row["comid"]): records.from_row(row) for row in rows}

    def _vpu_of(self, huc4: str) -> str:
        manifest = self.manifest() or {}
        unit = (manifest.get("units") or {}).get(huc4) or {}
        return str(unit.get("vpu") or huc4[:2])

    def records(self, comids) -> dict[int, dict]:
        """Evidence records by COMID (missing COMIDs are absent)."""
        wanted = [int(c) for c in comids]
        by_huc4: dict[str, list[int]] = {}
        for comid, huc4 in self.huc4_of(wanted).items():
            by_huc4.setdefault(huc4, []).append(comid)
        out: dict[int, dict] = {}
        for huc4, group in by_huc4.items():
            out.update(self._rows_for(evidence_asset(huc4), group))
        return out

    def scores(self, comids) -> dict[int, dict]:
        """Baked score rows by COMID (missing COMIDs are absent)."""
        wanted = [int(c) for c in comids]
        by_vpu: dict[str, list[int]] = {}
        for comid, huc4 in self.huc4_of(wanted).items():
            by_vpu.setdefault(self._vpu_of(huc4), []).append(comid)
        out: dict[int, dict] = {}
        for vpu, group in by_vpu.items():
            out.update(self._rows_for(scores_asset(vpu), group))
        return out

    # -------------------------------------------------------------- coverage
    def coverage(self) -> Optional[dict]:
        path = self.asset_path(COVERAGE)
        if path is None:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def summary(self) -> dict:
        """What the viewer header says: units, reaches, vintage, freshness."""
        manifest = self.manifest() or {}
        units = manifest.get("units") or {}
        published = [u for u in units.values() if u.get("status") in ("partial", "complete")]
        return {
            "available": bool(manifest),
            "vintage": manifest.get("vintage"),
            "tier": manifest.get("tier"),
            "updated": manifest.get("updated"),
            "method_version": manifest.get("method_version"),
            "method_current": manifest.get("method_version") == method_version(),
            "units_total": int(manifest.get("units_total") or 222),
            "units_published": len(published),
            "units_tier2": sum(1 for u in published if u.get("tier") == 2),
            "tiers": manifest.get("tiers") or {},
            "units_complete": sum(1 for u in published if u.get("status") == "complete"),
            "reaches_scored": sum(int(u.get("n_scored") or 0) for u in published),
            "vpus": sorted((manifest.get("tiles") or {}).keys()),
        }

    def summary_refreshed(self) -> dict:
        """``summary()`` after re-reading the manifest (the viewer's Refresh)."""
        self.manifest(refresh=True)
        return self.summary()

    def clear(self) -> None:
        with self._lock:
            self._manifest = None
            self._manifest_at = 0.0
            self._tables.clear()
            self._index = None
            self._resolved.clear()


_DEFAULT: dict[str, Dataset] = {}
_DEFAULT_LOCK = threading.Lock()


def default_dataset() -> Dataset:
    """The process-wide dataset (base from the environment or the release)."""
    with _DEFAULT_LOCK:
        ds = _DEFAULT.get("ds")
        if ds is None:
            ds = _DEFAULT["ds"] = Dataset()
        return ds


# ------------------------------------------------------------------ reports
def _error(code: str, message: str, comid) -> dict:
    return {"status": "error", "code": code, "message": message,
            "input": {"comid": comid}}


def score_record(record: dict, *, cross_section: bool = True,
                 watershed_geojson: Optional[dict] = None,
                 reach_geojson: Optional[dict] = None,
                 site_anchor: Optional[dict] = None,
                 dataset: Optional[Dataset] = None) -> dict:
    """The full report for one evidence record (pure, offline)."""
    from .. import assessment
    ctx = records.build_context(record, watershed_geojson=watershed_geojson,
                                reach_geojson=reach_geojson)
    if site_anchor:
        ctx.extras["siteAnchor"] = site_anchor
        records.apply_evidence(ctx, record)
    with providers.preloaded(record):
        report = assessment.assess_preloaded(ctx, cross_section=cross_section)
    manifest = (dataset.manifest() if dataset is not None else None) or {}
    unit = (manifest.get("units") or {}).get(str(record.get("huc4") or "")) or {}
    report["precomputed"] = {
        "vintage": manifest.get("vintage"),
        "tier": unit.get("tier") or manifest.get("tier"),
        "dem_resolution_m": ((record.get("geomorph") or {}).get("dem_resolution_m")
                             if isinstance(record.get("geomorph"), dict) else None),
        "updated": manifest.get("updated"),
        "method_version": manifest.get("method_version"),
        "method_current": (manifest.get("method_version") in (None, method_version())),
        "huc4": record.get("huc4"),
        "schema": SCHEMA_VERSION,
    }
    return report


def record_delineation(record: dict, reach_ft: float) -> dict:
    """The report header's delineation block from the record alone, for a
    report opened before the live geometry arrives."""
    return {"comid": int(record["comid"]), "gnis_name": record.get("gnis_name") or None,
            "huc8": record.get("huc8"), "huc12": record.get("huc12"),
            "drainage_area_sqkm": record.get("totdasqkm"),
            "snapped_lat": record.get("lat"), "snapped_lon": record.get("lon"),
            "watershed_area_sqkm": None, "watershed_source": None,
            "reach_length_ft": reach_ft, "warnings": []}


async def precomputed_geometry_async(comid: int, reach_length_ft: Optional[float] = None, *,
                                     dataset: Optional[Dataset] = None) -> dict:
    """The live geometry for a report already opened from its record: the
    NLDI basin polygon, the reach line, the site anchor and the delineation
    block (``{"status": "error", ...}`` when it cannot be delineated)."""
    from .. import pipeline
    ds = dataset or default_dataset()
    reach_ft = float(reach_length_ft or (ds.manifest() or {}).get("reach_length_ft")
                     or pipeline.DEFAULT_REACH_FT)
    record = (await asyncio.to_thread(ds.records, [int(comid)])).get(int(comid))
    if record is None:
        return _error("no_record", "This reach has no precomputed assessment yet.", comid)
    delin = await pipeline.delineate_only(float(record["lat"]), float(record["lon"]),
                                          reach_ft, comid=int(comid))
    if delin.get("status") != "ok":
        return delin
    ctx_inputs = delin.pop("ctx_inputs", None) or {}
    delineation = dict(delin.get("delineation") or {})
    delineation["huc12"] = record.get("huc12")
    if record.get("gnis_name"):
        delineation["gnis_name"] = record["gnis_name"]
    return {"status": "ok", "watershed_geojson": delin.get("watershed_geojson"),
            "reach_geojson": delin.get("reach_geojson"), "siteAnchor": ctx_inputs.get("siteAnchor"),
            "delineation": delineation}


async def open_precomputed_async(comid: int, reach_length_ft: Optional[float] = None, *,
                                 dataset: Optional[Dataset] = None,
                                 cross_section: bool = True, geometry: bool = True) -> dict:
    """The ``base`` dict (delineation, geometry, anchor, report) for a reach
    from the dataset: the record supplies every metric input, NLDI supplies
    the basin polygon and reach line, ``assess_preloaded`` scores. Returns
    ``{"status": "error", ...}`` when the reach has no record or the geometry
    could not be delineated. With ``geometry=False`` nothing is fetched: the
    report scores from the record at once, the geometry fields stay None and
    ``geometry_pending`` is set, for ``precomputed_geometry_async`` to fill."""
    from .. import pipeline
    ds = dataset or default_dataset()
    reach_ft = float(reach_length_ft or (ds.manifest() or {}).get("reach_length_ft")
                     or pipeline.DEFAULT_REACH_FT)
    record = (await asyncio.to_thread(ds.records, [int(comid)])).get(int(comid))
    if record is None:
        return _error("no_record", "This reach has no precomputed assessment yet.", comid)
    if not geometry:
        report = await asyncio.to_thread(score_record, record, cross_section=cross_section, dataset=ds)
        return {
            "status": "ok",
            "input": {"lat": record["lat"], "lon": record["lon"], "comid": int(comid),
                      "reach_length_ft": reach_ft},
            "delineation": record_delineation(record, reach_ft),
            "watershed_geojson": None, "reach_geojson": None, "siteAnchor": None,
            "report": report, "geometry_pending": True,
        }
    delin = await pipeline.delineate_only(float(record["lat"]), float(record["lon"]),
                                          reach_ft, comid=int(comid))
    if delin.get("status") != "ok":
        return delin
    ctx_inputs = delin.pop("ctx_inputs")
    report = await asyncio.to_thread(
        score_record, record, cross_section=cross_section,
        watershed_geojson=ctx_inputs.get("watershed_geojson"),
        reach_geojson=ctx_inputs.get("reach_geojson"),
        site_anchor=ctx_inputs.get("siteAnchor"), dataset=ds)
    delineation = dict(delin.get("delineation") or {})
    delineation["huc12"] = record.get("huc12")
    if record.get("gnis_name"):
        delineation["gnis_name"] = record["gnis_name"]
    return {
        "status": "ok",
        "input": {"lat": record["lat"], "lon": record["lon"], "comid": int(comid),
                  "reach_length_ft": reach_ft},
        "delineation": delineation,
        "watershed_geojson": delin.get("watershed_geojson"),
        "reach_geojson": delin.get("reach_geojson"),
        "siteAnchor": ctx_inputs.get("siteAnchor"),
        "report": report,
    }


def open_precomputed(comid: int, reach_length_ft: Optional[float] = None, *,
                     dataset: Optional[Dataset] = None,
                     cross_section: bool = True, geometry: bool = True) -> dict:
    """Synchronous ``open_precomputed_async`` for worker threads and scripts."""
    return asyncio.run(open_precomputed_async(comid, reach_length_ft, dataset=dataset,
                                              cross_section=cross_section, geometry=geometry))
