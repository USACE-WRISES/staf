"""The data root layout.

D:\\Data\\easi-national\\
  national\\   vaa_slim.parquet, enhd.parquet, nid.parquet, comid_huc4.parquet,
              huc4_vpu.json, huc8_index.json, huc4.geojson, dem\\1m\\tiles.parquet
  chunks\\<chunk-id>\\  chunk.json, raw\\<source>.parquet
  huc8\\<huc8>\\        derived.parquet, joins.parquet, evidence.parquet, scores.parquet,
                      reaches.parquet, xs_profiles.parquet (the archive), xs_sample.parquet,
                      xsections.parquet
  tiles\\<vpu>\\        lines.parquet, lines.fgb, tiles_<vpu>.pmtiles
  staging\\            the asset set of the next publish
  state\\              units.json, progress.json, control.json, queue.json,
                      rates.json, publish_log.jsonl, ledgers\\
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass(frozen=True)
class DataRoot:
    root: Path

    @classmethod
    def default(cls) -> "DataRoot":
        return cls(config.data_root())

    # top-level folders
    @property
    def national(self) -> Path:
        return self.root / "national"

    @property
    def chunks(self) -> Path:
        return self.root / "chunks"

    @property
    def huc8(self) -> Path:
        return self.root / "huc8"

    @property
    def tiles(self) -> Path:
        return self.root / "tiles"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def ledgers(self) -> Path:
        return self.state / "ledgers"

    # national one-offs
    @property
    def vaa(self) -> Path:
        return self.national / "vaa_slim.parquet"

    @property
    def vaa_raw(self) -> Path:
        return self.national / "nhdplus_vaa.parquet"

    @property
    def enhd_raw(self) -> Path:
        return self.national / "enhd_attrs.parquet"

    @property
    def nid(self) -> Path:
        return self.national / "nid.parquet"

    @property
    def index(self) -> Path:
        return self.national / "comid_huc4.parquet"

    @property
    def huc4_vpu(self) -> Path:
        return self.national / "huc4_vpu.json"

    @property
    def huc8_index(self) -> Path:
        return self.national / "huc8_index.json"

    @property
    def huc4_geojson(self) -> Path:
        return self.national / "huc4.geojson"

    # the 3DEP 1 m tile catalog (cross-section archive)
    @property
    def dem1m(self) -> Path:
        return self.national / "dem" / "1m"

    @property
    def dem1m_catalog(self) -> Path:
        return self.dem1m / "tiles.parquet"

    @property
    def dem1m_meta(self) -> Path:
        return self.dem1m / "catalog.json"

    @property
    def dem19(self) -> Path:
        return self.national / "dem" / "19"

    @property
    def dem19_catalog(self) -> Path:
        return self.dem19 / "quads.parquet"

    @property
    def dem19_meta(self) -> Path:
        return self.dem19 / "catalog.json"

    @property
    def bandwidth(self) -> Path:
        return self.state / "bandwidth.json"

    # per unit
    def chunk_dir(self, chunk_id: str) -> Path:
        return self.chunks / chunk_id

    def chunk_raw(self, chunk_id: str, source: str) -> Path:
        return self.chunks / chunk_id / "raw" / f"{source}.parquet"

    def huc8_dir(self, huc8: str) -> Path:
        return self.huc8 / huc8

    def huc8_file(self, huc8: str, name: str) -> Path:
        return self.huc8 / huc8 / f"{name}.parquet"

    def huc8_parts(self, huc8: str, name: str) -> Path:
        return self.huc8 / huc8 / f"{name}_parts"

    def tiles_dir(self, vpu: str) -> Path:
        return self.tiles / vpu

    def ensure(self) -> "DataRoot":
        for folder in (self.national, self.chunks, self.huc8, self.tiles,
                       self.staging, self.state, self.ledgers):
            folder.mkdir(parents=True, exist_ok=True)
        return self


def replace_with_retry(tmp: Path, path: Path, *, attempts: int = 150, delay_s: float = 0.02) -> None:
    """``tmp.replace(path)`` that outlives a concurrent reader: on Windows a
    file open for reading (another pool process, the panel) refuses the
    rename with "Access is denied" for the milliseconds it is open."""
    import time
    for attempt in range(attempts):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_s)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write to a sibling temp file, then replace (a crash never leaves a torn file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    replace_with_retry(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))
