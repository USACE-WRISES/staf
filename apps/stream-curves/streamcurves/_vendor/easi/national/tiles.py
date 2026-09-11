"""Serve map tiles out of the dataset's per-region PMTiles archives.

The browser cannot read a PMTiles archive on a GitHub release directly (the
asset host answers Range requests but sends no CORS header), so the app reads
the archive by Range request and hands single tiles to the map over a
same-origin route. ``pmtiles.reader.Reader`` re-reads the header and the
directories on every lookup, so the byte source is wrapped in a small LRU that
keeps those (and recent tiles) in memory; the cache key includes the asset's
sha256, so a rebuilt archive never serves stale offsets.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable, Optional

from .client import Dataset, tiles_asset

_MAX_CACHED_RANGE = 4 << 20        # never cache a single range above 4 MB
_DEFAULT_ENTRIES = 2048

# pmtiles.tile.Compression values -> the HTTP Content-Encoding a browser accepts
_ENCODINGS = {1: None, 2: "gzip", 3: "br", 4: "zstd"}
_MEDIA_TYPES = {1: "application/x-protobuf", 2: "image/png", 3: "image/jpeg",
                4: "image/webp", 5: "image/avif"}


class TileSource:
    """One PMTiles archive behind a caching ``get_bytes``."""

    def __init__(self, get_bytes: Callable[[int, int], bytes], key: str, *,
                 max_entries: int = _DEFAULT_ENTRIES):
        from pmtiles.reader import Reader
        self.key = key
        self._raw = get_bytes
        self._cache: "OrderedDict[tuple[int, int], bytes]" = OrderedDict()
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self.reader = Reader(self._get)
        self.header = self.reader.header()

    def _get(self, offset: int, length: int) -> bytes:
        key = (int(offset), int(length))
        with self._lock:
            data = self._cache.get(key)
            if data is not None:
                self._cache.move_to_end(key)
                return data
        data = self._raw(offset, length)
        if len(data) <= _MAX_CACHED_RANGE:
            with self._lock:
                self._cache[key] = data
                while len(self._cache) > self._max_entries:
                    self._cache.popitem(last=False)
        return data

    @property
    def encoding(self) -> Optional[str]:
        raw = self.header.get("tile_compression")
        return _ENCODINGS.get(int(getattr(raw, "value", raw) or 0))

    @property
    def media_type(self) -> str:
        raw = self.header.get("tile_type")
        return _MEDIA_TYPES.get(int(getattr(raw, "value", raw) or 0),
                                "application/octet-stream")

    @property
    def zoom_range(self) -> tuple[int, int]:
        return int(self.header.get("min_zoom") or 0), int(self.header.get("max_zoom") or 0)

    def get(self, z: int, x: int, y: int) -> Optional[bytes]:
        lo, hi = self.zoom_range
        if z < lo or z > hi:
            return None
        return self.reader.get(int(z), int(x), int(y))


class TileStore:
    """Tile sources for every region the dataset has published."""

    def __init__(self, dataset: Optional[Dataset] = None, *,
                 max_entries: int = _DEFAULT_ENTRIES):
        from .client import default_dataset
        self.dataset = dataset or default_dataset()
        self._sources: dict[str, TileSource] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def source(self, vpu: str) -> Optional[TileSource]:
        name = tiles_asset(vpu)
        sha = self.dataset.asset_sha(name) or ""
        key = f"{name}@{sha}"
        with self._lock:
            src = self._sources.get(vpu)
            if src is not None and src.key == key:
                return src
        if self.dataset.local is not None and self.dataset.asset_path(name) is None:
            return None
        if self.dataset.local is None and not sha and not self.dataset.manifest():
            return None
        try:
            src = TileSource(self.dataset.range_reader(name), key,
                             max_entries=self._max_entries)
        except Exception:  # noqa: BLE001 - a missing or unreadable archive is "no tiles"
            return None
        with self._lock:
            self._sources[vpu] = src
        return src

    def tile(self, vpu: str, z: int, x: int, y: int
             ) -> tuple[Optional[bytes], Optional[str], str]:
        """``(data, content_encoding, media_type)``; ``data`` None when absent."""
        src = self.source(vpu)
        if src is None:
            return None, None, "application/octet-stream"
        try:
            data = src.get(z, x, y)
        except Exception:  # noqa: BLE001 - a failed range read is an empty tile
            return None, None, src.media_type
        return data, src.encoding, src.media_type

    def clear(self) -> None:
        with self._lock:
            self._sources.clear()


_STORE: dict[str, TileStore] = {}
_STORE_LOCK = threading.Lock()


def default_store() -> TileStore:
    with _STORE_LOCK:
        store = _STORE.get("store")
        if store is None:
            store = _STORE["store"] = TileStore()
        return store
