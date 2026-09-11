"""Chunks: named sets of HUC8s, and the national index that resolves them.

A chunk is built from a HUC8 id, a HUC4, a state abbreviation (every HUC8
intersecting the state polygon), or a VPU. Its COMID list comes from the
national VAA slim table (``reachcode[:8]``); its bounding box is computed from
the flowlines once they are fetched (``chunk.json`` records both).
"""
from __future__ import annotations

import gzip
import json
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from . import REPO_ROOT, config, http
from .paths import DataRoot, atomic_write_text

STATES_PATH = REPO_ROOT / "apps" / "deep" / "data" / "us_states.geojson.gz"

_VPU_ORDER = ("01", "02", "03N", "03S", "03W", "04", "05", "06", "07", "08", "09",
              "10U", "10L", "11", "12", "13", "14", "15", "16", "17", "18")


@dataclass
class Chunk:
    id: str
    kind: str                       # huc8 | huc4 | state | vpu | custom
    label: str
    huc8s: list[str]
    n_comids: int = 0
    bbox: Optional[list[float]] = None      # [west, south, east, north] with the buffer
    states: list[str] = field(default_factory=list)
    vpus: list[str] = field(default_factory=list)
    created: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Chunk":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})

    def save(self, root: DataRoot) -> None:
        path = root.chunk_dir(self.id) / "chunk.json"
        atomic_write_text(path, json.dumps(self.to_dict(), indent=1))

    @classmethod
    def load(cls, root: DataRoot, chunk_id: str) -> Optional["Chunk"]:
        path = root.chunk_dir(chunk_id) / "chunk.json"
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None


def list_chunks(root: DataRoot) -> list[Chunk]:
    out = []
    if root.chunks.exists():
        for folder in sorted(root.chunks.iterdir()):
            chunk = Chunk.load(root, folder.name)
            if chunk is not None:
                out.append(chunk)
    return out


# ---------------------------------------------------------------- HUC8 index
def load_huc8_index(root: DataRoot) -> dict[str, dict]:
    """``{huc8: {"n": comid count, "vpu": majority vpu, "huc4": huc4}}`` (national stage)."""
    try:
        return json.loads(root.huc8_index.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_huc4_vpu(root: DataRoot) -> dict[str, str]:
    try:
        return json.loads(root.huc4_vpu.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def huc8s_for_huc4(root: DataRoot, huc4: str) -> list[str]:
    return sorted(h for h in load_huc8_index(root) if h.startswith(huc4))


def huc8s_for_vpu(root: DataRoot, vpu: str) -> list[str]:
    index = load_huc8_index(root)
    return sorted(h for h, info in index.items() if info.get("vpu") == vpu)


def _state_polygon(abbr: str):
    from shapely.geometry import shape
    with gzip.open(STATES_PATH, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        if str(props.get("state", "")).upper() == abbr.upper():
            return shape(feature["geometry"]), props.get("name")
    raise ValueError(f"unknown state {abbr!r}")


def huc8s_for_state(root: DataRoot, abbr: str, *, fetch=None) -> tuple[list[str], str]:
    """Every HUC8 (from the fabric HUC08 collection) intersecting the state polygon,
    filtered to HUC8s the national index knows (CONUS network). ``fetch`` overrides
    the HTTP call for tests: ``fetch(bbox) -> list of GeoJSON features``."""
    from shapely.geometry import shape
    polygon, _name = _state_polygon(abbr)
    west, south, east, north = polygon.bounds
    if fetch is None:
        fetch = _fabric_huc8_features
    features = fetch([west, south, east, north])
    known = load_huc8_index(root)
    out = []
    for feature in features:
        props = feature.get("properties") or {}
        code = str(props.get("08") or props.get("huc8") or "").strip()
        if not code or (known and code not in known):
            continue
        try:
            if shape(feature["geometry"]).intersects(polygon):
                out.append(code)
        except Exception:  # noqa: BLE001 - a malformed polygon is skipped
            continue
    return sorted(set(out)), _name


def _fabric_huc8_features(bbox: list[float]) -> list[dict]:
    url = f"{config.FABRIC_BASE}/nhdplusv2-huc08/items"
    features: list[dict] = []
    offset, page = 0, 50            # HUC8 polygons are heavy: small pages, long timeout
    while True:
        data = http.get_json(url, {"f": "json", "limit": page, "offset": offset,
                                   "bbox": ",".join(f"{v:.6f}" for v in bbox)},
                             timeout=300.0)
        batch = data.get("features") or []
        features.extend(batch)
        if len(batch) < page:
            break
        offset += len(batch)
    return features


# --------------------------------------------------------------- chunk making
def _vpus_for(root: DataRoot, huc8s: Iterable[str]) -> list[str]:
    index = load_huc8_index(root)
    found = {index.get(h, {}).get("vpu") for h in huc8s}
    return [v for v in _VPU_ORDER if v in found] + sorted(v for v in found if v and v not in _VPU_ORDER)


def make_chunk(root: DataRoot, kind: str, value: str, *, fetch=None) -> Chunk:
    """A chunk of ``kind`` (``huc8``, ``huc4``, ``state``, ``vpu``) for ``value``."""
    from .state import now_iso
    index = load_huc8_index(root)
    kind = kind.lower()
    value = value.strip()
    if kind == "huc8":
        huc8s, label, states = [value], f"HUC8 {value}", []
    elif kind == "huc4":
        huc8s, label, states = huc8s_for_huc4(root, value), f"HUC4 {value}", []
    elif kind == "state":
        huc8s, name = huc8s_for_state(root, value, fetch=fetch)
        label, states = f"{name or value}", [value.upper()]
    elif kind == "vpu":
        huc8s, label, states = huc8s_for_vpu(root, value.upper()), f"Region {value.upper()}", []
    else:
        raise ValueError(f"unknown chunk kind {kind!r}")
    if not huc8s:
        raise ValueError(f"no HUC8s resolved for {kind} {value!r}")
    n = sum(int(index.get(h, {}).get("n") or 0) for h in huc8s)
    chunk_id = f"{kind}-{value.upper().replace(' ', '_')}"
    chunk = Chunk(id=chunk_id, kind=kind, label=label, huc8s=sorted(huc8s), n_comids=n,
                  states=states, vpus=_vpus_for(root, huc8s), created=now_iso())
    chunk.save(root)
    return chunk


def chunk_comids(root: DataRoot, chunk: Chunk):
    """The chunk's COMID table (``comid, huc8, huc4, vpu, ...``) from the VAA slim table."""
    import pyarrow.parquet as pq
    return pq.read_table(root.vaa, filters=[("huc8", "in", list(chunk.huc8s))])
