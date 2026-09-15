"""Panel stability (rule C11): for every usable curve, 200 HUC12-cluster
bootstraps of its panel, the closed-form seed refitted each time, and the
share of the stratum's reaches whose class (from the 0.69 / 0.39 crossings)
changes between the full-panel curve and the resampled one. The mean flip
rate is the verdict: accept at or under 0.10, exploratory to 0.20 (the
national fallback is recorded), reject above.

Output: ``analysis/stability/bootstrap_stability.csv``.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

import numpy as np

from ..paths import DataRoot
from ..state import Control, Progress
from . import stats
from .curves import INDEX_BANDS, QUANTITIES, crossings, registry_rows, seed_points
from .panels import load_landscape, members_path
from .values import values_path

N_BOOT = 200
SEED = 7
ACCEPT = 0.10
EXPLORATORY = 0.20


def stability_path(root: DataRoot) -> Path:
    return root.analysis / "stability" / "bootstrap_stability.csv"


def classes_from_crossings(values: np.ndarray, x39: float, x69: float, higher_is_better: bool) -> np.ndarray:
    """0 Poor, 1 Fair, 2 Good from the two crossings (-1 where missing)."""
    out = np.full(values.shape, -1, dtype=np.int8)
    ok = np.isfinite(values)
    v = values[ok]
    if higher_is_better:
        cls = np.where(v > x69, 2, np.where(v > x39, 1, 0))
    else:
        cls = np.where(v < x69, 2, np.where(v < x39, 1, 0))
    out[ok] = cls
    return out


def seed_crossings(q25: float, q75: float, higher_is_better: bool, domain: tuple) -> tuple[Optional[float], Optional[float]]:
    points = [{"x": x, "y": y} for x, y in seed_points(q25, q75, higher_is_better, domain)]
    lo = crossings(points, INDEX_BANDS[0])
    hi = crossings(points, INDEX_BANDS[1])
    return (lo[0] if len(lo) == 1 else None), (hi[0] if len(hi) == 1 else None)


def flip_rate(member_values: np.ndarray, member_clusters: np.ndarray, population: np.ndarray,
              higher_is_better: bool, domain: tuple, x39: float, x69: float, *,
              n_boot: int = N_BOOT, seed: int = SEED) -> dict:
    """Bootstrap the panel by cluster, refit the seed, and measure the class
    flips over the population against the full-panel crossings."""
    base = classes_from_crossings(population, x39, x69, higher_is_better)
    rated = base >= 0
    if not rated.any():
        return {"flip_mean": None, "flip_p90": None, "x39_lo": None, "x39_hi": None, "x69_lo": None, "x69_hi": None,
                "n_boot_valid": 0}
    labels, inverse = np.unique(member_clusters, return_inverse=True)
    groups = [np.flatnonzero(inverse == i) for i in range(len(labels))]
    rng = np.random.default_rng(seed)
    flips, x39s, x69s = [], [], []
    for _ in range(n_boot):
        picked = rng.integers(0, len(groups), size=len(groups))
        rows = np.concatenate([groups[i] for i in picked])
        sample = member_values[rows]
        sample = sample[np.isfinite(sample)]
        if sample.size < 5:
            continue
        q25, q75 = float(np.quantile(sample, 0.25)), float(np.quantile(sample, 0.75))
        if q75 - q25 <= 0 or (higher_is_better and q25 <= 0 and (domain[0] is None or domain[0] >= 0)):
            continue
        b39, b69 = seed_crossings(q25, q75, higher_is_better, domain)
        if b39 is None or b69 is None:
            continue
        boot = classes_from_crossings(population, b39, b69, higher_is_better)
        flips.append(float((boot[rated] != base[rated]).mean()))
        x39s.append(b39)
        x69s.append(b69)
    if not flips:
        return {"flip_mean": None, "flip_p90": None, "x39_lo": None, "x39_hi": None, "x69_lo": None, "x69_hi": None,
                "n_boot_valid": 0}
    lo39, hi39 = stats.interval(x39s)
    lo69, hi69 = stats.interval(x69s)
    return {"flip_mean": float(np.mean(flips)), "flip_p90": float(np.quantile(flips, 0.90)),
            "x39_lo": lo39, "x39_hi": hi39, "x69_lo": lo69, "x69_hi": hi69, "n_boot_valid": len(flips)}


def verdict(flip_mean: Optional[float]) -> str:
    if flip_mean is None:
        return "not evaluable"
    if flip_mean <= ACCEPT:
        return "accept"
    if flip_mean <= EXPLORATORY:
        return "exploratory"
    return "reject"


def _population_values(quantity, level: str, code: str, split: str, landscape, values) -> np.ndarray:
    """The values of the stratum's reaches (the population the curve scores)."""
    q = QUANTITIES[quantity]
    source = landscape if q.source == "landscape" else values
    if source is None or q.column not in source.columns:
        return np.zeros(0)
    if level == "national":
        mask = np.ones(len(source), dtype=bool)
    else:
        column = "l3_code" if (level == "l3" and "l3_code" in source.columns and "l3" not in source.columns) else level
        mask = (source[column].astype("object") == code).to_numpy()
    if split:
        split_column = q.split if q.split else "slope_class"
        if split_column in source.columns:
            mask = mask & (source[split_column].astype("object") == split).to_numpy()   # pandas 3 hands out read-only arrays
    return source.loc[mask, q.column].to_numpy(dtype=float)


def run_stability(root: DataRoot, progress: Progress, control: Control, *, n_boot: int = N_BOOT) -> Path:
    import pandas as pd
    import pyarrow.parquet as pq
    rows = [r for r in registry_rows(root) if r.get("usable")]
    members = pq.read_table(members_path(root)).to_pandas()
    quantities = sorted({r["quantity"] for r in rows})
    landscape_cols = sorted({"comid", "huc12", "l3", "l2", "l1", "nars9", "slope_class", "fcode_class"}
                            | {QUANTITIES[q].column for q in quantities if QUANTITIES[q].source == "landscape"})
    landscape = load_landscape(root, landscape_cols)
    values_cols = sorted({"comid", "huc12", "l3_code", "l2", "l1", "nars9", "slope_class", "fcode_class"}
                         | {QUANTITIES[q].column for q in quantities if QUANTITIES[q].source == "values"})
    values = None
    if values_path(root).exists():
        schema = pq.read_schema(values_path(root)).names
        values = pq.read_table(values_path(root), columns=[c for c in values_cols if c in schema]).to_pandas()
    landscape_by_comid = landscape.set_index("comid")
    values_by_comid = values.set_index("comid") if values is not None else None
    out = []
    progress.begin("analysis", "stability", total=len(rows), message=f"stability: {len(rows)} usable curves")
    for i, row in enumerate(rows):
        if i % 20 == 0:
            control.check()
        q = QUANTITIES[row["quantity"]]
        sub = members[(members["level"] == row["level"]) & (members["stratum"] == row["stratum"])]
        split = row.get("split") or ""
        if split:
            split_column = q.split if q.split else "slope_class"
            sub = sub[sub[split_column].astype("object") == split]
        source = landscape_by_comid if q.source == "landscape" else values_by_comid
        if source is None or q.column not in source.columns:
            continue
        member_values = source[q.column].reindex(sub["comid"].to_numpy()).to_numpy(dtype=float)
        member_clusters = sub["huc12"].astype("object").fillna("").to_numpy()
        code = row["stratum"].split(":", 1)[1]
        population = _population_values(row["quantity"], row["level"], code, split, landscape, values)
        result = flip_rate(member_values, member_clusters, population, q.higher_is_better, q.domain,
                           float(row["x39"]), float(row["x69"]), n_boot=n_boot)
        out.append({"quantity": row["quantity"], "level": row["level"], "stratum": row["stratum"], "split": split,
                    "n_members": int(np.isfinite(member_values).sum()), "n_clusters": int(len(set(member_clusters.tolist()))),
                    "n_population": int(np.isfinite(population).sum()), "x39": row["x39"], "x69": row["x69"],
                    **result, "verdict": verdict(result["flip_mean"])})
        progress.tick(done=i + 1)
    path = stability_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(out[0].keys()) if out else ["quantity"])
        writer.writeheader()
        writer.writerows(out)
    verdicts = {v: sum(1 for r in out if r["verdict"] == v) for v in ("accept", "exploratory", "reject", "not evaluable")}
    progress.say(f"bootstrap_stability.csv: {len(out)} curves, {verdicts}")
    return path
