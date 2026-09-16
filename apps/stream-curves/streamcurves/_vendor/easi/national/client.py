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
import copy
import hashlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from .. import config
from . import DATASET_TAG, SCHEMA_VERSION, method_version, providers, records
from .http import session as _http_session

_LOG = logging.getLogger(__name__)

DEFAULT_BASE = f"https://github.com/USACE-WRISES/staf/releases/download/{DATASET_TAG}/"
ENV_BASE = "EASI_NATIONAL_BASE"

MANIFEST = "manifest.json"
INDEX = "comid_huc4.parquet"
COVERAGE = "coverage.geojson"
STATS = "stats.json"

_RESOLVE_TTL_S = 40 * 60.0
_PARQUET_BATCH_SIZE = 512
_PARQUET_BUFFER_SIZE = 256 * 1024
_POSITION_CACHE_COUNT = 16
_POSITION_CACHE_BYTES = 8 * 1024 * 1024
_RECORD_CACHE_COUNT = 256
_RECORD_CACHE_BYTES = 16 * 1024 * 1024
# Readers across all Dataset instances share the same process memory budget.
_PARQUET_READERS = threading.BoundedSemaphore(2)


def _deep_size(value: Any, seen: Optional[set[int]] = None) -> int:
    """Owned size of a decoded JSON record, including its nested values."""
    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_deep_size(k, seen) + _deep_size(v, seen) for k, v in value.items())
    elif isinstance(value, (list, tuple, set, frozenset)):
        size += sum(_deep_size(v, seen) for v in value)
    return size


def criteria_status(manifest: dict) -> dict:
    """Published schema-1 datasets predate the switch and use legacy criteria."""
    if not manifest:
        return {"criteria_set": None, "criteria_current": None}
    published = manifest.get("criteria_set") or "legacy"
    return {"criteria_set": published,
            "criteria_current": published == config.criteria_set()}


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
        # Serialize misses within a dataset so simultaneous clicks decode once.
        # Refresh uses only _lock and can invalidate an in-flight read.
        self._read_lock = threading.RLock()
        self._generation = 0
        self._manifest: Optional[dict] = None
        self._manifest_at = 0.0
        self._manifest_stamp = None
        self._positions: "OrderedDict[str, tuple[Any, Any, int]]" = OrderedDict()
        self._position_bytes = 0
        self._records: "OrderedDict[tuple[str, int], tuple[dict, int]]" = OrderedDict()
        self._record_bytes = 0
        self._index = None
        self._stats: Optional[tuple[Optional[str], dict]] = None
        self._resolved: dict[str, tuple[str, float]] = {}
        self._verified: dict[str, tuple] = {}

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
            with _http_session().head(url, allow_redirects=False, timeout=self.timeout) as head:
                target = head.headers.get("Location") if head.is_redirect else None
        except requests.RequestException:
            return url
        resolved = target or url
        with self._lock:
            self._resolved[name] = (resolved, now + _RESOLVE_TTL_S)
        return resolved

    # -------------------------------------------------------------- manifest
    def manifest(self, *, refresh: bool = False) -> Optional[dict]:
        """The dataset manifest, or None when it cannot be read."""
        stamp = None
        if self.local is not None:
            try:
                stat = (self.local / MANIFEST).stat()
                stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            except OSError:
                return None
        with self._lock:
            fresh = (self._manifest is not None
                     and time.monotonic() - self._manifest_at < self.manifest_ttl_s
                     and (self.local is None or stamp == self._manifest_stamp))
            if fresh and not refresh:
                return self._manifest
        try:
            if self.local is not None:
                text = (self.local / MANIFEST).read_text(encoding="utf-8")
            else:
                with _http_session().get(self.url(MANIFEST), timeout=self.timeout) as response:
                    if response.status_code != 200:
                        return self._manifest
                    text = response.text
            manifest = json.loads(text)
            if not isinstance(manifest, dict):
                raise ValueError("National manifest must be an object")
        except (OSError, ValueError, requests.RequestException):
            # A disappearing local manifest marks an incomplete replacement.
            return None if self.local is not None else self._manifest
        with self._lock:
            if self._manifest != manifest:
                self._clear_read_caches()
            self._manifest = manifest
            self._manifest_at = time.monotonic()
            self._manifest_stamp = stamp
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
            if (Path(name).name != name or not path.is_file()
                    or path.resolve().parent != self.local.resolve()):
                return None
            sha = self.asset_sha(name)
            if sha:
                stat = path.stat()
                stamp = (sha, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                with self._lock:
                    if self._verified.get(name) != stamp:
                        if _sha256(path) != sha:
                            self._verified.pop(name, None)
                            return None
                        self._verified[name] = stamp
            return path
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
                with _http_session().get(url, stream=True, timeout=self.timeout) as response:
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
            path = self.asset_path(name)
            if path is None:
                raise DatasetError(f"{name}: missing or damaged national asset")

            def _local(offset: int, length: int) -> bytes:
                with open(path, "rb") as handle:
                    handle.seek(offset)
                    return handle.read(length)
            return _local

        def _remote(offset: int, length: int) -> bytes:
            started = time.perf_counter()
            for attempt in range(2):
                url = self.resolve(name, force=attempt > 0)
                headers = {"Range": f"bytes={offset}-{offset + length - 1}"}
                with _http_session().get(url, headers=headers, timeout=self.timeout) as response:
                    if response.status_code in (200, 206):
                        _LOG.debug("%s: range %d+%d -> %d in %.0f ms", name, offset, length,
                                   response.status_code, 1000 * (time.perf_counter() - started))
                        if response.status_code == 200:  # the host ignored Range
                            return response.content[offset:offset + length]
                        return response.content
                    if response.status_code in (403, 404) and attempt == 0:
                        continue
                    _LOG.warning("%s: HTTP %s for range %d+%d after %.0f ms", name, response.status_code,
                                 offset, length, 1000 * (time.perf_counter() - started))
                    raise DatasetError(f"{name}: HTTP {response.status_code} for range")
            _LOG.warning("%s: could not resolve the asset for range %d+%d", name, offset, length)
            raise DatasetError(f"{name}: could not resolve the asset")
        return _remote

    # ----------------------------------------------------------- bounded rows
    def _clear_read_caches(self) -> None:
        """Called under _lock; old readers may finish but cannot refill caches."""
        self._generation += 1
        self._positions.clear()
        self._position_bytes = 0
        self._records.clear()
        self._record_bytes = 0
        self._index = None
        self._stats = None

    def _load_index(self):
        import numpy as np
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        with self._read_lock:
            self.manifest()
            with self._lock:
                if self._index is not None:
                    return self._index
                generation = self._generation
            path = self.asset_path(INDEX)
            if path is None:
                return None
            with self._lock:
                if generation != self._generation:
                    raise DatasetError("The national dataset changed. Refresh Nationwide screening.")
            with _PARQUET_READERS, pq.ParquetFile(
                    path, buffer_size=_PARQUET_BUFFER_SIZE, pre_buffer=False) as parquet:
                count = parquet.metadata.num_rows
                comids = np.empty(count, dtype=np.int64)
                huc4s = np.empty(count, dtype="S4")
                batches = parquet.iter_batches(batch_size=_PARQUET_BATCH_SIZE,
                                               columns=["comid", "huc4"], use_threads=False)
                offset = 0
                try:
                    for batch in batches:
                        try:
                            end = offset + batch.num_rows
                            comids[offset:end] = batch.column("comid").to_numpy()
                            column = batch.column("huc4")
                            if column.null_count or not (pa.types.is_string(column.type)
                                                         or pa.types.is_large_string(column.type)):
                                raise DatasetError("The national index contains an invalid HUC4.")
                            try:
                                fixed = pc.cast(column, pa.binary(4))
                            except pa.ArrowInvalid as exc:
                                raise DatasetError("The national index contains an invalid HUC4.") from exc
                            values = np.frombuffer(fixed.buffers()[1], dtype="S4",
                                                   count=len(fixed), offset=fixed.offset * 4)
                            digits = values.view(np.uint8)
                            if not np.all((digits >= 48) & (digits <= 57)):
                                raise DatasetError("The national index contains an invalid HUC4.")
                            # Copy out of Arrow's buffer; the retained index has
                            # no per-code Python objects or borrowed Arrow data.
                            huc4s[offset:end] = values
                            offset = end
                        finally:
                            del batch
                finally:
                    batches.close()
            if np.all(comids[1:] >= comids[:-1]):
                index = (comids, huc4s)
            else:
                order = np.argsort(comids, kind="stable")
                index = (comids[order], huc4s[order])
            with self._lock:
                if generation != self._generation:
                    raise DatasetError("The national dataset changed. Refresh Nationwide screening.")
                self._index = index
            return index

    def _row_positions(self, name: str, path: Path, generation: int):
        """Sorted COMIDs and physical positions, owned by numpy rather than Arrow."""
        import numpy as np
        import pyarrow.parquet as pq

        with self._lock:
            if generation != self._generation:
                raise DatasetError("The national dataset changed. Refresh Nationwide screening.")
            hit = self._positions.get(name)
            if hit is not None:
                self._positions.move_to_end(name)
                return hit[:2]
        with _PARQUET_READERS, pq.ParquetFile(
                path, buffer_size=_PARQUET_BUFFER_SIZE, pre_buffer=False) as parquet:
            comids = np.empty(parquet.metadata.num_rows, dtype=np.int64)
            batches = parquet.iter_batches(batch_size=_PARQUET_BATCH_SIZE,
                                           columns=["comid"], use_threads=False)
            offset = 0
            try:
                for batch in batches:
                    try:
                        end = offset + batch.num_rows
                        comids[offset:end] = batch.column("comid").to_numpy()
                        offset = end
                    finally:
                        del batch
            finally:
                batches.close()
        order = np.argsort(comids, kind="stable")
        sorted_comids = comids[order]
        size = _deep_size((name, sorted_comids, order))
        with self._lock:
            if generation == self._generation and size <= _POSITION_CACHE_BYTES:
                self._positions[name] = (sorted_comids, order, size)
                self._position_bytes += size
                while (len(self._positions) > _POSITION_CACHE_COUNT
                       or self._position_bytes > _POSITION_CACHE_BYTES):
                    self._position_bytes -= self._positions.popitem(last=False)[1][2]
        return sorted_comids, order

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
                out[comid] = huc4s[pos].decode("ascii")
        return out

    def _rows_for(self, name: str, comids: list[int]) -> dict[int, dict]:
        if not comids:
            return {}
        import numpy as np
        import pyarrow.parquet as pq

        with self._read_lock:
            self.manifest()
            with self._lock:
                generation = self._generation
                found: dict[int, dict] = {}
                missing = []
                for comid in dict.fromkeys(comids):
                    key = (name, int(comid))
                    hit = self._records.get(key)
                    if hit is None:
                        missing.append(int(comid))
                    else:
                        self._records.move_to_end(key)
                        found[int(comid)] = hit[0]
            if missing:
                path = self.asset_path(name)
                if path is None:
                    with self._lock:
                        if generation != self._generation:
                            raise DatasetError("The national dataset changed. Refresh Nationwide screening.")
                    return copy.deepcopy(found)
                # asset_path may have refreshed the manifest while verifying it.
                with self._lock:
                    if generation != self._generation:
                        raise DatasetError("The national dataset changed. Refresh Nationwide screening.")
                sorted_comids, positions = self._row_positions(name, path, generation)
                wanted = np.asarray(missing, dtype=np.int64)
                # Stable sorting plus the rightmost match preserves the old
                # dict comprehension's last-physical-duplicate-wins behavior.
                matches = np.searchsorted(sorted_comids, wanted, side="right") - 1
                needed = sorted(int(positions[pos]) for comid, pos in zip(wanted, matches)
                                if pos >= 0 and sorted_comids[pos] == comid)
                decoded: dict[int, dict] = {}
                if needed:
                    with _PARQUET_READERS, pq.ParquetFile(
                            path, buffer_size=_PARQUET_BUFFER_SIZE, pre_buffer=False) as parquet:
                        batches = parquet.iter_batches(batch_size=_PARQUET_BATCH_SIZE,
                                                       use_threads=False)
                        offset = cursor = 0
                        try:
                            for batch in batches:
                                try:
                                    end = offset + batch.num_rows
                                    while cursor < len(needed) and needed[cursor] < end:
                                        row = batch.slice(needed[cursor] - offset, 1).to_pylist()[0]
                                        decoded[int(row["comid"])] = records.from_row(row)
                                        cursor += 1
                                    offset = end
                                    if cursor == len(needed):
                                        break
                                finally:
                                    del batch
                        finally:
                            batches.close()
                with self._lock:
                    if generation == self._generation:
                        for comid, record in decoded.items():
                            key = (name, comid)
                            size = _deep_size((key, record))
                            if size > _RECORD_CACHE_BYTES:
                                continue
                            self._records[key] = (record, size)
                            self._record_bytes += size
                            while (len(self._records) > _RECORD_CACHE_COUNT
                                   or self._record_bytes > _RECORD_CACHE_BYTES):
                                self._record_bytes -= self._records.popitem(last=False)[1][1]
                found.update(decoded)
            # Evidence providers can mutate nested values while building a
            # report. Neither another caller nor our cache may see those edits.
            result = copy.deepcopy(found)
            with self._lock:
                if generation != self._generation:
                    raise DatasetError("The national dataset changed. Refresh Nationwide screening.")
            return result

    def _vpu_of(self, huc4: str) -> str:
        manifest = self.manifest() or {}
        unit = (manifest.get("units") or {}).get(huc4) or {}
        return str(unit.get("vpu") or huc4[:2])

    def records(self, comids) -> dict[int, dict]:
        """Evidence records by COMID (missing COMIDs are absent)."""
        self.manifest()
        with self._lock:
            generation = self._generation
        wanted = [int(c) for c in comids]
        by_huc4: dict[str, list[int]] = {}
        for comid, huc4 in self.huc4_of(wanted).items():
            by_huc4.setdefault(huc4, []).append(comid)
        out: dict[int, dict] = {}
        for huc4, group in by_huc4.items():
            out.update(self._rows_for(evidence_asset(huc4), group))
        with self._lock:
            if generation != self._generation:
                raise DatasetError("The national dataset changed. Refresh Nationwide screening.")
        return out

    def scores(self, comids) -> dict[int, dict]:
        """Baked score rows by COMID (missing COMIDs are absent)."""
        self.manifest()
        with self._lock:
            generation = self._generation
        wanted = [int(c) for c in comids]
        by_vpu: dict[str, list[int]] = {}
        for comid, huc4 in self.huc4_of(wanted).items():
            by_vpu.setdefault(self._vpu_of(huc4), []).append(comid)
        out: dict[int, dict] = {}
        for vpu, group in by_vpu.items():
            out.update(self._rows_for(scores_asset(vpu), group))
        with self._lock:
            if generation != self._generation:
                raise DatasetError("The national dataset changed. Refresh Nationwide screening.")
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

    def stats(self) -> Optional[dict]:
        """The dashboard's statistics asset (distributions by state), or None
        when the manifest lists none or it cannot be read. Cached per sha."""
        sha = self.asset_sha(STATS)
        if sha is None and self.local is None:
            return None
        # Local files can change even when a manifest has not been replaced.
        local_path = self.asset_path(STATS) if self.local is not None else None
        if self.local is not None and local_path is None:
            return None
        with self._lock:
            hit = self._stats
            if hit is not None and hit[0] == sha:
                return hit[1]
        path = local_path or self.asset_path(STATS)
        if path is None:
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        with self._lock:
            self._stats = (sha, data)
        return data

    def require_current(self, dataset_key: Optional[str] = None, verify_all: bool = True) -> dict:
        """Gate interactive use on a completed build matching this application."""
        from . import bundle
        try:
            manifest = bundle.validate(self, verify_all=verify_all)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise DatasetError(str(exc)) from exc
        if dataset_key is not None and bundle.dataset_key(manifest) != dataset_key:
            raise DatasetError("The national dataset changed. Refresh Nationwide screening.")
        return manifest

    def is_current_key(self, dataset_key: str) -> bool:
        """Cheap post-await guard; activation already verified the full bundle."""
        from . import bundle
        manifest = self.manifest() or {}
        return bundle.identity_error(manifest) is None and bundle.dataset_key(manifest) == dataset_key

    def current_stats(self, dataset_key: Optional[str] = None) -> Optional[dict]:
        manifest = self.require_current(dataset_key)
        stats = self.stats()
        if stats is None or any(stats.get(key) != manifest.get(key)
                                for key in ("build_id", "alternative_id", "method_version")):
            raise DatasetError("The dashboard statistics do not match the national build.")
        return stats

    def summary(self) -> dict:
        """What the viewer header says: units, reaches, vintage, freshness."""
        manifest = self.manifest() or {}
        from . import bundle
        try:
            self.require_current()
            unavailable = None
        except DatasetError as exc:
            unavailable = str(exc)
        units = manifest.get("units") or {}
        published = [u for u in units.values() if u.get("status") in ("partial", "complete")]
        return {
            "available": bool(manifest) and unavailable is None,
            "error": unavailable,
            "dataset_key": bundle.dataset_key(manifest) if manifest else None,
            "alternative_id": manifest.get("alternative_id"),
            "alternative_name": (manifest.get("scoring_identity") or {}).get("alternative_name"),
            "build_id": manifest.get("build_id"),
            "vpu_bounds": {key: entry.get("bounds") for key, entry in
                           (manifest.get("tiles") or {}).items() if entry.get("bounds")},
            "vintage": manifest.get("vintage"),
            "tier": manifest.get("tier"),
            "updated": manifest.get("updated"),
            "method_version": manifest.get("method_version"),
            "method_current": manifest.get("method_version") == method_version(),
            **criteria_status(manifest),
            "units_total": int(manifest.get("units_total") or 222),
            "units_published": len(published),
            "units_tier2": sum(1 for u in published if u.get("tier") == 2),
            "tiers": manifest.get("tiers") or {},
            "units_complete": sum(1 for u in published if u.get("status") == "complete"),
            "reaches_scored": sum(int(u.get("n_scored") or 0) for u in published),
            "vpus": sorted((manifest.get("tiles") or {}).keys()) if unavailable is None else [],
        }

    def summary_refreshed(self) -> dict:
        """``summary()`` after re-reading the manifest (the viewer's Refresh)."""
        self.manifest(refresh=True)
        return self.summary()

    def clear(self) -> None:
        with self._lock:
            self._manifest = None
            self._manifest_at = 0.0
            self._manifest_stamp = None
            self._clear_read_caches()
            self._resolved.clear()
            self._verified.clear()


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
        "alternative_id": manifest.get("alternative_id"),
        "build_id": manifest.get("build_id"),
        "vintage": manifest.get("vintage"),
        "tier": unit.get("tier") or manifest.get("tier"),
        "dem_resolution_m": ((record.get("geomorph") or {}).get("dem_resolution_m")
                             if isinstance(record.get("geomorph"), dict) else None),
        "updated": manifest.get("updated"),
        "method_version": manifest.get("method_version"),
        "method_current": (manifest.get("method_version") in (None, method_version())),
        **criteria_status(manifest),
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
                                 dataset_key: Optional[str] = None,
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
    try:
        await asyncio.to_thread(ds.require_current, dataset_key)
    except DatasetError as exc:
        return _error("outdated_dataset", str(exc), comid)
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
