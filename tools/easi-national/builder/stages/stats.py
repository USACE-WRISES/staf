"""Staging statistics for the condition dashboard: for everything published
and for each state, the distribution of the ECI and the three sub-indices
and of every function's rating, computed from the per-HUC8 scores and the
COMID to state table. One small JSON asset (``stats.json``) rides the
release; the app draws the dashboard from it and never opens a score file.

Reaches belong to the state containing the midpoint of their flowline
(``stages.states``). Function scores are three-valued (3, 8, 13: the rating
times 15), so a function is described by its rating shares and a 16-bin
score histogram; the indices are continuous, so they get quantiles, a
20-bin histogram and the band counts under the app's own edges."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..paths import DataRoot, atomic_write_text
from ..state import Progress, now_iso
from ..units import STATES_PATH
from . import states as states_stage
from .score import _function_key

STATS_SCHEMA = 1
STATS_ASSET = "stats.json"
NATIONAL = "US"
#: (key in the asset, column in scores.parquet)
INDICES = (("eci", "eci_raw"), ("physical", "phys_raw"), ("chemical", "chem_raw"), ("biological", "bio_raw"))
QUANTILES = (("p5", 5), ("p10", 10), ("p25", 25), ("p50", 50), ("p75", 75), ("p90", 90), ("p95", 95))
INDEX_BINS = 20
SCORE_MAX = 15
SOURCE_TIERS = ("observed", "connected-nearby", "published-model", "screening-proxy", "manual", "unavailable")


class NoStateTable(RuntimeError):
    """The COMID to state table has not been built (the national ``states`` step)."""


def function_catalog() -> list[dict]:
    """``[{id, name, category}]`` in report order, from the app's catalog."""
    from easi import config
    return [{"id": fn["id"], "name": fn["name"], "category": fn["category"]} for fn in config.functions()]


def band_edges() -> tuple[tuple[float, float], tuple[int, int]]:
    """``((index NF edge, index AR edge), (score NF edge, score AR edge))``:
    the app's upper-inclusive band edges (0.39 / 0.69 and 5 / 10)."""
    from easi import config
    idx = (float(config.INDEX_BANDS[0][0]), float(config.INDEX_BANDS[1][0]))
    fs = (int(config.FUNCTION_SCORE_BANDS[0][0]), int(config.FUNCTION_SCORE_BANDS[1][0]))
    return idx, fs


class _Group:
    """The running totals of one scope (a state or everything)."""

    def __init__(self, fids):
        self.n = 0
        self.provisional = 0
        self.complete = 0
        self.tier2 = 0
        self.index_values = {key: [] for key, _ in INDICES}
        self.fn_hist = {fid: [0] * (SCORE_MAX + 1) for fid in fids}
        self.fn_unrated = {fid: 0 for fid in fids}
        self.fn_tiers = {fid: {} for fid in fids}


def _column(table, name, default):
    """A numpy view of ``name`` with nulls filled, or ``None`` when absent."""
    import pyarrow.compute as pc
    if name not in table.column_names:
        return None
    return pc.fill_null(table.column(name), default).to_numpy(zero_copy_only=False)


def _float_column(table, name):
    import numpy as np
    if name not in table.column_names:
        return None
    return np.asarray(table.column(name).to_numpy(zero_copy_only=False), dtype=np.float64)


def _accumulate(group: _Group, rows, fids, columns) -> None:
    """Add the rows at positions ``rows`` (a numpy index array) to ``group``."""
    import numpy as np
    n = int(len(rows))
    if n == 0:
        return
    group.n += n
    if columns["provisional"] is not None:
        group.provisional += int(np.count_nonzero(columns["provisional"][rows]))
    if columns["n_rated"] is not None:
        group.complete += int(np.count_nonzero(columns["n_rated"][rows] >= columns["n_functions"]))
    if columns["tier"] is not None:
        group.tier2 += int(np.count_nonzero(columns["tier"][rows] >= 2))
    for key, _col in INDICES:
        values = columns["index"].get(key)
        if values is not None:
            picked = values[rows]
            picked = picked[~np.isnan(picked)]
            if len(picked):
                group.index_values[key].append(picked.astype(np.float32))
    for fid in fids:
        fs = columns["fs"].get(fid)
        if fs is None:
            group.fn_unrated[fid] += n
        else:
            picked = fs[rows]
            rated = picked[(picked >= 0) & (picked <= SCORE_MAX)]
            group.fn_unrated[fid] += n - int(len(rated))
            if len(rated):
                counts = np.bincount(rated.astype(np.int64), minlength=SCORE_MAX + 1)
                hist = group.fn_hist[fid]
                for i, c in enumerate(counts.tolist()):
                    hist[i] += int(c)
        tiers = columns["tiers"].get(fid)
        if tiers is not None and fs is not None:
            # the sources of the rated reaches only: an unrated row keeps the
            # tier its adapter reported, which would count a proxy it never used
            picked = tiers[rows]
            picked = picked[(fs[rows] >= 0) & (fs[rows] <= SCORE_MAX)]
            labels, counts = np.unique(picked.astype(str), return_counts=True)
            bucket = group.fn_tiers[fid]
            for label, count in zip(labels.tolist(), counts.tolist()):
                bucket[label] = bucket.get(label, 0) + int(count)


def _read_scores(path: Path, fids):
    """The columns the statistics need, as numpy arrays keyed for ``_accumulate``."""
    import numpy as np
    import pyarrow.parquet as pq
    schema = pq.read_schema(path)
    wanted = ["comid", "provisional", "n_rated", "tier"] + [col for _k, col in INDICES]
    for fid in fids:
        key = _function_key(fid)
        wanted += [f"fs_{key}", f"tier_{key}"]
    present = [c for c in wanted if c in schema.names]
    table = pq.read_table(path, columns=present)
    comids = np.asarray(table.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
    columns = {
        "provisional": _column(table, "provisional", False),
        "n_rated": _column(table, "n_rated", 0),
        "tier": _column(table, "tier", 1),
        "n_functions": len(fids),
        "index": {key: _float_column(table, col) for key, col in INDICES},
        "fs": {}, "tiers": {},
    }
    for fid in fids:
        key = _function_key(fid)
        fs = _column(table, f"fs_{key}", -1)
        columns["fs"][fid] = None if fs is None else np.asarray(fs, dtype=np.int64)
        tiers = _column(table, f"tier_{key}", "unavailable")
        columns["tiers"][fid] = None if tiers is None else np.asarray(tiers, dtype=object)
    return comids, columns


def _index_summary(chunks, edges) -> Optional[dict]:
    import numpy as np
    if not chunks:
        return None
    values = np.concatenate(chunks).astype(np.float64)
    if len(values) == 0:
        return None
    nf_edge, ar_edge = edges
    quantiles = np.percentile(values, [q for _k, q in QUANTILES])
    hist, _ = np.histogram(values, bins=INDEX_BINS, range=(0.0, 1.0))
    out = {"n": int(len(values)), "mean": round(float(values.mean()), 4),
           "sd": round(float(values.std()), 4) if len(values) > 1 else 0.0,
           "min": round(float(values.min()), 4), "max": round(float(values.max()), 4)}
    for (key, _q), value in zip(QUANTILES, quantiles.tolist()):
        out[key] = round(float(value), 4)
    out["bands"] = [int(np.count_nonzero(values <= nf_edge)),
                    int(np.count_nonzero((values > nf_edge) & (values <= ar_edge))),
                    int(np.count_nonzero(values > ar_edge))]
    out["hist"] = [int(c) for c in hist.tolist()]
    return out


def _function_summary(hist, unrated, tiers, edges) -> dict:
    nf_edge, ar_edge = edges
    rated = int(sum(hist))
    total = 0
    for score, count in enumerate(hist):
        total += score * count
    bands = [int(sum(hist[:nf_edge + 1])), int(sum(hist[nf_edge + 1:ar_edge + 1])), int(sum(hist[ar_edge + 1:]))]
    return {"rated": rated, "unrated": int(unrated), "bands": bands,
            "mean": round(total / rated, 3) if rated else None, "hist": [int(c) for c in hist],
            "tiers": {k: int(v) for k, v in sorted(tiers.items())}}


def _finish(group: _Group, fids, edges) -> dict:
    idx_edges, fs_edges = edges
    return {"n": group.n, "provisional": group.provisional, "complete": group.complete, "tier2": group.tier2,
            "indices": {key: _index_summary(group.index_values[key], idx_edges) for key, _c in INDICES},
            "functions": {fid: _function_summary(group.fn_hist[fid], group.fn_unrated[fid],
                                                 group.fn_tiers[fid], fs_edges) for fid in fids}}


def _state_totals(root: DataRoot, index) -> dict[str, int]:
    """Reaches of the national index per state (the coverage denominators)."""
    import numpy as np
    import pyarrow.parquet as pq
    if not root.index.exists():
        return {}
    comids = pq.read_table(root.index, columns=["comid"]).column("comid").to_numpy(zero_copy_only=False)
    states = states_stage.states_of(index, np.asarray(comids, dtype=np.int64))
    known = states[states != None]  # noqa: E711 - object array
    labels, counts = np.unique(known.astype(str), return_counts=True)
    totals = {str(k): int(v) for k, v in zip(labels.tolist(), counts.tolist())}
    totals[NATIONAL] = int(len(comids))
    return totals


def build_stats(root: DataRoot, huc8s, progress: Optional[Progress] = None, *,
                vintage: Optional[str] = None, method_version: Optional[str] = None,
                polygons_path: Path = STATES_PATH) -> dict:
    """The statistics asset for the scored ``huc8s``."""
    import numpy as np
    index = states_stage.load_comid_states(root)
    if index is None:
        raise NoStateTable("national/comid_state.parquet is missing: run the national states step")
    catalog = function_catalog()
    fids = [fn["id"] for fn in catalog]
    edges = band_edges()
    groups: dict[str, _Group] = {NATIONAL: _Group(fids)}
    unknown = 0
    huc8s = sorted(huc8s)
    for i, huc8 in enumerate(huc8s):
        path = root.huc8_file(huc8, "scores")
        if not path.exists():
            continue
        comids, columns = _read_scores(path, fids)
        states = states_stage.states_of(index, comids)
        _accumulate(groups[NATIONAL], np.arange(len(comids)), fids, columns)
        known = states != None  # noqa: E711 - object array
        unknown += int(len(comids) - np.count_nonzero(known))
        for abbr in sorted(set(states[known].tolist())):
            rows = np.flatnonzero(states == abbr)
            group = groups.get(abbr)
            if group is None:
                group = groups[abbr] = _Group(fids)
            _accumulate(group, rows, fids, columns)
        if progress is not None:
            progress.tick(message=f"statistics: {huc8} ({i + 1} of {len(huc8s)})")
    totals = _state_totals(root, index)
    names = states_stage.state_names(polygons_path)
    states_block = {}
    for abbr, group in groups.items():
        if abbr == NATIONAL:
            continue
        total = totals.get(abbr)
        states_block[abbr] = {"name": names.get(abbr, abbr), "n_total": total, "n_scored": group.n,
                              "coverage": round(group.n / total, 4) if total else None}
    stats = {"schema_version": STATS_SCHEMA, "generated": now_iso(), "vintage": vintage,
             "method_version": method_version, "huc8s": len(huc8s),
             "reaches": groups[NATIONAL].n, "reaches_total": totals.get(NATIONAL),
             "unassigned": unknown,
             "measures": {"indices": [{"id": key, "name": name} for key, name in
                                      (("eci", "Ecosystem Condition Index"), ("physical", "Physical"),
                                       ("chemical", "Chemical"), ("biological", "Biological"))],
                          "functions": catalog,
                          "index_edges": list(edges[0]), "score_edges": list(edges[1]),
                          "score_max": SCORE_MAX, "source_tiers": list(SOURCE_TIERS)},
             "states": dict(sorted(states_block.items())),
             "groups": {abbr: _finish(group, fids, edges) for abbr, group in sorted(groups.items())}}
    return stats


def write_stats(staging: Path, stats: dict) -> Path:
    path = staging / STATS_ASSET
    atomic_write_text(path, json.dumps(stats, separators=(",", ":"), sort_keys=True))
    return path
