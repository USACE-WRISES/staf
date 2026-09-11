"""The tile store: PMTiles archives read by range, cached, keyed by hash."""
from __future__ import annotations

import gzip
import hashlib
import json

from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

from easi.national import client, tiles

TILES = {(0, 0, 0): b"tile-000", (1, 0, 0): b"tile-100", (1, 1, 1): b"tile-111"}


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
