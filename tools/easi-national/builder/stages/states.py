"""The COMID to state table: every NHDPlus V2 flowline assigned to the
Census state polygon that contains its midpoint, or the nearest polygon for
the few coastal midpoints the 1:500,000 coastline leaves outside every
state. One national pass over ``flowlines.parquet`` (about 2.7 M lines, a
few minutes); the staging statistics read the result, so a reach belongs to
exactly one state everywhere the dashboard counts it."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Optional

from ..paths import DataRoot
from ..state import Progress
from ..units import STATES_PATH
from . import common

#: the Census file's vintage rides the step's input digest
CENSUS_VINTAGE = "cb_2024_us_state_500k"
BATCH = 200_000


def comid_state_path(root: DataRoot) -> Path:
    return root.national / "comid_state.parquet"


def load_state_polygons(path: Path = STATES_PATH):
    """``(geometries, abbreviations, names)`` from the Census GeoJSON DEEP ships
    (properties ``state`` and ``name``), in file order."""
    from shapely.geometry import shape
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    geoms, abbrs, names = [], [], []
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        abbr = str(props.get("state") or "").upper()
        if not abbr or not feature.get("geometry"):
            continue
        geoms.append(shape(feature["geometry"]))
        abbrs.append(abbr)
        names.append(str(props.get("name") or abbr))
    return geoms, abbrs, names


def state_names(path: Path = STATES_PATH) -> dict[str, str]:
    """Abbreviation -> full name."""
    _geoms, abbrs, names = load_state_polygons(path)
    return dict(zip(abbrs, names))


class StateLocator:
    """Vectorised point-in-polygon over the state polygons with a nearest
    fallback for points no polygon contains."""

    def __init__(self, polygons_path: Path = STATES_PATH):
        import shapely
        geoms, self.abbrs, self.names = load_state_polygons(polygons_path)
        self.tree = shapely.STRtree(geoms)

    def assign(self, points):
        """``(codes, by_nearest)``: one abbreviation per point (``None`` for a
        missing or empty point) and a mask of the points no polygon contained,
        which took the nearest polygon."""
        import numpy as np
        import shapely
        points = np.asarray(points, dtype=object)
        n = len(points)
        codes = np.full(n, None, dtype=object)
        by_nearest = np.zeros(n, dtype=bool)
        if n == 0:
            return codes, by_nearest
        valid = ~(shapely.is_missing(points) | shapely.is_empty(points))
        pos = np.flatnonzero(valid)
        if len(pos) == 0:
            return codes, by_nearest
        hits_pt, hits_poly = self.tree.query(points[pos], predicate="within")
        # the first hit per point (states do not overlap, a boundary point hits none)
        first = np.full(len(pos), -1, dtype=np.int64)
        for p, t in zip(hits_pt[::-1].tolist(), hits_poly[::-1].tolist()):
            first[p] = t
        missing = np.flatnonzero(first < 0)
        if len(missing):
            nearest = self.tree.nearest(points[pos[missing]])
            first[missing] = np.asarray(nearest, dtype=np.int64)
            by_nearest[pos[missing]] = True
        abbrs = np.asarray(self.abbrs, dtype=object)
        codes[pos] = abbrs[first]
        return codes, by_nearest


def run_states(root: DataRoot, progress: Progress, *, polygons_path: Path = STATES_PATH,
               flowlines: Optional[Path] = None, batch: int = BATCH) -> Path:
    """``national/comid_state.parquet``: ``comid``, ``state`` and ``by_nearest``
    (True where no polygon contained the midpoint), sorted by comid."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely
    flowlines = flowlines or (root.national / "flowlines.parquet")
    if not flowlines.exists():
        raise RuntimeError(f"{flowlines.name} not found: run the national flowlines step first")
    locator = StateLocator(polygons_path)
    reader = pq.ParquetFile(flowlines)
    total = reader.metadata.num_rows
    progress.begin("national", "states", total=total, message=f"states: {total:,} flowline midpoints")
    comids: list = []
    states: list = []
    flags: list = []
    n_nearest = done = 0
    for record_batch in reader.iter_batches(batch_size=batch, columns=["comid", "geometry"]):
        wkb = record_batch.column("geometry").to_numpy(zero_copy_only=False)
        lines = shapely.from_wkb(wkb)
        points = shapely.line_interpolate_point(lines, 0.5, normalized=True)
        codes, by_nearest = locator.assign(points)
        comids.append(np.asarray(record_batch.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64))
        states.append(codes)
        flags.append(by_nearest)
        n_nearest += int(by_nearest.sum())
        done += len(codes)
        progress.tick(done=done)
    comid = np.concatenate(comids) if comids else np.zeros(0, dtype=np.int64)
    state = np.concatenate(states) if states else np.zeros(0, dtype=object)
    by_nearest = np.concatenate(flags) if flags else np.zeros(0, dtype=bool)
    order = np.argsort(comid, kind="stable")
    table = pa.table({"comid": pa.array(comid[order], pa.int64()),
                      "state": pa.array(state[order].tolist(), pa.string()),
                      "by_nearest": pa.array(by_nearest[order], pa.bool_())})
    path = common.write_parquet(table, comid_state_path(root))
    progress.say(f"comid_state.parquet: {len(comid):,} flowlines, {n_nearest:,} by the nearest polygon, "
                 f"{len(set(state.tolist()) - {None})} states")
    return path


def load_comid_states(root: DataRoot):
    """``(sorted comids, states)`` as numpy arrays, or ``None`` before the step ran."""
    import numpy as np
    import pyarrow.parquet as pq
    path = comid_state_path(root)
    if not path.exists():
        return None
    table = pq.read_table(path, columns=["comid", "state"])
    comids = np.asarray(table.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
    states = np.asarray(table.column("state").to_pylist(), dtype=object)
    order = np.argsort(comids, kind="stable")
    return comids[order], states[order]


def states_of(index, comids) -> "list":
    """The state of each COMID (``None`` when unknown) from ``load_comid_states``."""
    import numpy as np
    sorted_comids, states = index
    wanted = np.asarray(comids, dtype=np.int64)
    positions = np.searchsorted(sorted_comids, wanted)
    positions = np.minimum(positions, max(len(sorted_comids) - 1, 0))
    found = (len(sorted_comids) > 0) & (sorted_comids[positions] == wanted)
    out = np.full(len(wanted), None, dtype=object)
    out[found] = states[positions[found]]
    return out
