"""The tile store: PMTiles archives read by range, cached, keyed by hash."""
from __future__ import annotations

import gzip
import hashlib
import json
import logging

import pytest
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

from easi.national import client, tiles

TILES = {(0, 0, 0): b"tile-000", (1, 0, 0): b"tile-100", (1, 1, 1): b"tile-111"}


@pytest.mark.parametrize("ranges,expected", [
    ([(4, 12), (4, 12)], (4, 12)),
    ([(4, 12), (6, 10)], (6, 10)),
    ([(4, 4)], (4, 4)),
    ([(8, 12), (7, 10)], (8, 10)),
    ([(7, 7)], (7, 7)),
    ([(0, 16), (4, 31)], (4, 16)),
    ([(0, 12), (2, 10)], (4, 10)),
])
def test_viewer_zoom_range_uses_shared_manifest_bounds(ranges, expected):
    manifest = {"tiles": {str(i): {"minzoom": lo, "maxzoom": hi}
                          for i, (lo, hi) in enumerate(ranges)}}
    assert tiles.viewer_zoom_range(manifest) == expected


@pytest.mark.parametrize("entries", [None, {}, [], {"01": None}, {"01": {}},
    {"01": {"minzoom": 7}}, {"01": {"minzoom": "7", "maxzoom": 12}},
    {"01": {"minzoom": True, "maxzoom": 12}}, {"01": {"minzoom": 7.0, "maxzoom": 12}},
    {"01": {"minzoom": -1, "maxzoom": 12}}, {"01": {"minzoom": 4, "maxzoom": 32}},
    {"01": {"minzoom": 12, "maxzoom": 7}}, {"01": {"minzoom": 0, "maxzoom": 3}},
    {"01": {"minzoom": 17, "maxzoom": 20}},
    {"01": {"minzoom": 8, "maxzoom": 12}, "02": {"minzoom": 4, "maxzoom": 7}},
])
def test_viewer_zoom_range_rejects_invalid_or_incompatible_manifest(entries):
    with pytest.raises(ValueError, match="tile zoom range"):
        tiles.viewer_zoom_range({"tiles": entries})


def _write_pmtiles(path):
    with open(path, "wb") as handle:
        writer = Writer(handle)
        for (z, x, y), payload in sorted(TILES.items(), key=lambda kv: zxy_to_tileid(*kv[0])):
            writer.write_tile(zxy_to_tileid(z, x, y), gzip.compress(payload))
        writer.finalize({"tile_type": TileType.MVT, "tile_compression": Compression.GZIP},
                        {"name": "test"})


def _dataset(root, vpu="02"):
    root.mkdir(parents=True, exist_ok=True)
    asset = client.tiles_asset(vpu)
    _write_pmtiles(root / asset)
    sha = hashlib.sha256((root / asset).read_bytes()).hexdigest()
    manifest = {"schema_version": 1, "vintage": "2026.09", "units": {},
                "tiles": {vpu: {"asset": asset, "sha256": sha, "minzoom": 0, "maxzoom": 1}}}
    (root / client.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return client.Dataset(base=str(root)), manifest


def test_tiles_come_back_with_their_encoding(tmp_path):
    ds, _ = _dataset(tmp_path)
    store = tiles.TileStore(ds)
    data, encoding, media = store.tile("02", 0, 0, 0)
    assert gzip.decompress(data) == b"tile-000"
    assert encoding == "gzip" and media == "application/x-protobuf"
    assert gzip.decompress(store.tile("02", 1, 1, 1)[0]) == b"tile-111"
    assert store.tile("02", 1, 0, 1)[0] is None          # absent tile
    assert store.tile("02", 5, 0, 0)[0] is None          # beyond the archive's zooms
    assert store.tile("99", 0, 0, 0) == (None, None, "application/octet-stream")
    assert store.source("02").zoom_range == (0, 1)


def test_directories_and_tiles_are_read_once(tmp_path, monkeypatch):
    ds, _ = _dataset(tmp_path)
    raw = ds.range_reader(client.tiles_asset("02"))
    calls = []

    def counting(offset, length):
        calls.append((offset, length))
        return raw(offset, length)

    monkeypatch.setattr(ds, "range_reader", lambda name: counting)
    store = tiles.TileStore(ds)
    store.tile("02", 0, 0, 0)
    first = len(calls)
    store.tile("02", 0, 0, 0)
    store.tile("02", 1, 0, 0)
    # the header and root directory were cached: the third lookup adds at most one range
    assert len(calls) - first <= 1


def test_a_rebuilt_archive_replaces_the_source(tmp_path):
    ds, manifest = _dataset(tmp_path)
    store = tiles.TileStore(ds)
    before = store.source("02")
    TILES[(0, 0, 0)] = b"tile-000-v2"
    try:
        _write_pmtiles(tmp_path / client.tiles_asset("02"))
    finally:
        TILES[(0, 0, 0)] = b"tile-000"
    manifest["tiles"]["02"]["sha256"] = hashlib.sha256(
        (tmp_path / client.tiles_asset("02")).read_bytes()).hexdigest()
    (tmp_path / client.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    ds.manifest(refresh=True)
    after = store.source("02")
    assert after is not before
    assert gzip.decompress(store.tile("02", 0, 0, 0)[0]) == b"tile-000-v2"


def test_a_failed_range_read_raises_and_logs(tmp_path, monkeypatch, caplog):
    ds, _ = _dataset(tmp_path)
    store = tiles.TileStore(ds)
    src = store.source("02")

    def failing(offset, length):
        raise OSError("connection reset")

    monkeypatch.setattr(src, "_raw", failing)
    src._cache.clear()                                   # the next lookup has to read the archive again
    with caplog.at_level(logging.WARNING, logger="easi.national.tiles"):
        with pytest.raises(tiles.TileReadError):
            store.tile("02", 0, 0, 0)
    assert any(r.getMessage().startswith("tile 02/0/0/0 failed") for r in caplog.records)


def test_failed_initial_header_read_of_declared_archive_raises(tmp_path, monkeypatch):
    ds, _ = _dataset(tmp_path)
    def failing_reader(name):
        def read(offset, length):
            raise OSError("header connection reset")
        return read
    monkeypatch.setattr(ds, "range_reader", failing_reader)
    with pytest.raises(tiles.TileReadError, match="archive initialization failed"):
        tiles.TileStore(ds).tile("02", 0, 0, 0)


def test_a_slow_tile_is_logged_as_a_warning(tmp_path, monkeypatch, caplog):
    ds, _ = _dataset(tmp_path)
    store = tiles.TileStore(ds)
    with caplog.at_level(logging.DEBUG, logger="easi.national.tiles"):
        store.tile("02", 0, 0, 0)                        # a fast lookup stays at DEBUG
        monkeypatch.setattr(tiles, "_SLOW_TILE_S", 0.0)
        store.tile("02", 1, 0, 0)
    fast = [r for r in caplog.records if r.getMessage().startswith("tile 02/0/0/0:")]
    slow = [r for r in caplog.records if r.getMessage().startswith("tile 02/1/0/0:")]
    assert fast and fast[-1].levelno == logging.DEBUG
    assert slow and slow[-1].levelno == logging.WARNING and "bytes in" in slow[-1].getMessage()
