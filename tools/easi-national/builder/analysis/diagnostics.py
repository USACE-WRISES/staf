"""Step ``stats``: the statistics the report and the decision sheet read.

Under ``analysis/stats/``:
- ``diag_metric_stratum.csv``: per quantity, level and stratum the
  distribution, the saturation shares and the C1 (constant), C2 (censored)
  and C3 (zero-inflated) flags of ``03_stress_test_protocol``.
- ``variance_decomp.csv``: eta-squared of each quantity's percentile rank
  by state, Level III, II, I and NARS-9 (a diagnostic, never the level rule).
- ``level_T_L1.csv``: per curved quantity and level the reference-median
  shift H against the parent level (reach-weighted), the share of reaches
  materially shifted, the pooled NRSA agreement, the fallback and relaxed
  shares, the flip-rate pass share, the border excess and the criteria sets.
- ``level_T_L2.csv``: per state and run the banded-view ECI spread, class
  shares, pinned cells and fallback share.
- ``paradigm_T_P1.csv``, ``paradigm_T_P2.csv``, ``paradigm_T_P3.csv``: the
  three views compared per state (spread and shares), the band confusion
  between views, and the ECI-level NRSA AUCs per view.
- ``border_excess.csv``: per run the mean ECI jump and class-flip share
  between adjacent reaches across an ecoregion border minus within a region.
- the NRSA validation (``validation/``) and the stability bootstraps
  (``stability/``) run here too.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional

import numpy as np

from ..paths import DataRoot
from ..state import Control, Progress, digest
from . import ANALYSIS_VERSION, stability, validation
from .curves import QUANTITIES, registry_path, registry_rows
from .panels import panels_path
from .schemes import CURVE_INPUTS, RUNS, VIEWS, run_path, schemes_dir
from .strata import crosswalk_path, read_crosswalk
from .values import target_states, values_path

LEVELS_DIAG = ("state", "l3", "l2", "l1", "nars9", "national")
#: Legacy quantities: key -> (values column, resolution floor, cap).
DIAG_QUANTITIES = {
    "impervious": ("v__catchment_hydrology__impervious", 1.0, None),
    "agriculture": ("v__catchment_hydrology__agriculture", 1.0, None),
    "wetland": ("c__surface_water_storage", 1.0, None),
    "road_density": ("v__reach_inflow__roadDensity", 0.2, None),
    "dor": ("c__streamflow_regime", 1.0, None),
    "hyd_integrity": ("c__low_flow_baseflow_dynamics", 0.05, 1.0),
    "sed_integrity": ("c__bed_composition_bedform_dynamics", 0.05, 1.0),
    "bhr": ("v__high_flow_dynamics__bhr", 0.1, 2.0),
    "er": ("v__floodplain_connectivity__er", 0.2, None),
    "slope": ("v__hyporheic_connectivity__slope", 0.001, None),
    "sinuosity": ("v__hyporheic_connectivity__sinuosity", 0.03, None),
    "k_factor": ("v__sediment_continuity__kFactor", 0.03, None),
    "woody_corridor": ("v__habitat_provision__woodyRiparian", 5.0, 100.0),
    "natural_corridor": ("c__carbon_processing", 5.0, 100.0),
    "tn": ("v__nutrient_cycling__tn", None, None),
    "tp": ("v__nutrient_cycling__tp", None, None),
    "dams": ("v__watershed_connectivity__damCount", 1.0, None),
    "taxa": ("v__community_dynamics__taxaCount", 1.0, None),
}
MIN_GROUP = 200
H_MATERIAL = 0.5


def diagnostic_quantities() -> dict:
    """Physical inputs of the selected criteria, without mixing source routes."""
    from easi.config import criteria_set
    quantities = dict(DIAG_QUANTITIES)
    if criteria_set() == "regional":
        quantities.pop("hyd_integrity")
        quantities.pop("sed_integrity")
        quantities.update({
            "flow_variability_cv": ("v__low_flow_baseflow_dynamics__flowCv", None, None),
            "bed_agriculture": ("v__bed_composition_bedform_dynamics__agriculture", 1.0, 100.0),
            "biological_model_probability": ("v__population_support__prGBmmi", None, 1.0),
            "biological_integrity_fallback": ("c__population_support", None, 1.0),
        })
    return quantities


def quantity_methods() -> dict:
    """Restrict the two biological quantities to their actual source method."""
    from easi.config import criteria_set
    if criteria_set() == "legacy":
        return {}
    return {
        "biological_model_probability": ("method_population_support", "streamcat-prg-bmmi"),
        "biological_integrity_fallback": ("method_population_support", "streamcat-integrity-products"),
    }


def stats_dir(root: DataRoot) -> Path:
    return root.analysis / "stats"


def _write(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ["empty"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _num(value) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if np.isfinite(out) else None


# ------------------------------------------------------------ distributions
def distribution(values: np.ndarray, floor: Optional[float], cap: Optional[float]) -> dict:
    ok = values[np.isfinite(values)]
    n = int(ok.size)
    out = {"n": n, "share_missing": float(1 - n / max(len(values), 1))}
    if n == 0:
        return out
    q = np.quantile(ok, [0.05, 0.25, 0.50, 0.75, 0.95])
    iqr = float(q[3] - q[1])
    out.update({"p05": float(q[0]), "p25": float(q[1]), "p50": float(q[2]), "p75": float(q[3]), "p95": float(q[4]),
                "iqr": iqr, "robust_cv": float(iqr / (abs(q[2]) + 1e-9)),
                "share_zero": float((ok == 0).mean()),
                "share_cap": float((ok >= cap - 1e-9).mean()) if cap is not None else None})
    out["c1_constant"] = bool(out["robust_cv"] < 0.05 or (floor is not None and iqr < floor))
    out["c2_censored"] = bool(out["share_cap"] is not None and out["share_cap"] >= 0.5)
    out["c3_zero_inflated"] = bool(out["share_zero"] >= 0.6)
    return out


def eta_squared(values: np.ndarray, groups: np.ndarray) -> Optional[float]:
    """Share of the percentile rank's variance between groups (None under 2 groups)."""
    ok = np.isfinite(values) & np.array([g is not None for g in groups])
    if ok.sum() < MIN_GROUP:
        return None
    x = values[ok]
    ranks = x.argsort(kind="stable").argsort() / max(len(x) - 1, 1)
    g = groups[ok]
    labels, inverse = np.unique(g.astype(str), return_inverse=True)
    if len(labels) < 2:
        return None
    total = ranks.var()
    if total == 0:
        return 0.0
    means = np.bincount(inverse, weights=ranks) / np.bincount(inverse)
    between = ((means[inverse] - ranks.mean()) ** 2).mean()
    return float(between / total)


def distributions(table, progress: Progress) -> tuple[list[dict], list[dict]]:
    n = table.num_rows

    def col(name, kind="float"):
        if name not in table.column_names:
            return np.full(n, np.nan) if kind == "float" else np.full(n, None, dtype=object)
        return np.asarray(table.column(name).to_pandas(), dtype=float) if kind == "float" else np.asarray(table.column(name).to_pylist(), dtype=object)

    groups = {"state": col("state", "object"), "l3": col("l3_code", "object"), "l2": col("l2", "object"),
              "l1": col("l1", "object"), "nars9": col("nars9", "object"), "national": np.full(n, "national", dtype=object)}
    diag_rows, var_rows = [], []
    methods = quantity_methods()
    for key, (column, floor, cap) in diagnostic_quantities().items():
        values = col(column)
        if key in methods:
            method_column, method_key = methods[key]
            values = np.where(col(method_column, "object") == method_key, values, np.nan)
        if not np.isfinite(values).any():
            continue
        var_row = {"quantity": key, "column": column}
        for level, labels in groups.items():
            if level != "national":
                var_row[f"eta2_{level}"] = eta_squared(values, labels)
            for code in sorted({g for g in labels.tolist() if isinstance(g, str)}):
                mask = labels == code
                if mask.sum() < MIN_GROUP:
                    continue
                diag_rows.append({"quantity": key, "level": level, "stratum": f"{level}:{code}",
                                  **distribution(values[mask], floor, cap)})
        var_rows.append(var_row)
        progress.say(f"stats: {key} distributions over {len(groups)} levels")
    return diag_rows, var_rows


# ------------------------------------------------------------ level tables
CANDIDATE_LEVELS = ("national", "nars9", "l2", "l1")


def median_shift_rows(root: DataRoot) -> list[dict]:
    """C15's H per (quantity, candidate level, Level III stratum, split): the
    Level III panel median against the candidate level's panel median in
    units of that panel's IQR, with the scored reaches each pair carries for
    the reach weighting (Level III against itself is zero and has no row)."""
    import pyarrow.parquet as pq
    registry = [r for r in registry_rows(root)
                if _num(r.get("q50")) is not None and (_num(r.get("iqr")) or 0.0) > 0]
    if not registry or not values_path(root).exists():
        return []
    by_key = {(r["quantity"], r["level"], r["stratum"], r.get("split") or ""): r for r in registry}
    schema = pq.read_schema(values_path(root)).names
    columns = [c for c in ("l3_code", "l2", "l1", "nars9", "slope_class", "fcode_class") if c in schema]
    if "l3_code" not in columns:
        return []
    frame = pq.read_table(values_path(root), columns=columns).to_pandas()
    for c in columns:
        frame[c] = frame[c].astype("object").where(frame[c].notna(), "")
    counts = frame.groupby(columns, observed=True, dropna=False).size()
    rows = []
    for quantity in sorted({r["quantity"] for r in registry}):
        q = QUANTITIES.get(quantity)
        has_split = any(r.get("split") for r in registry if r["quantity"] == quantity)
        split_column = ((q.split if q is not None and q.split else "slope_class") if has_split else None)
        groups: dict[tuple, int] = {}
        for key, n in counts.items():
            values = dict(zip(columns, key))
            split = str(values.get(split_column, "")) if split_column else ""
            group = (values.get("l3_code", ""), values.get("l2", ""), values.get("l1", ""), values.get("nars9", ""), split)
            groups[group] = groups.get(group, 0) + int(n)
        for (l3c, l2c, l1c, n9, split), n in groups.items():
            child = by_key.get((quantity, "l3", f"l3:{l3c}", split))
            if child is None or not l3c:
                continue
            for level, code in (("national", "national"), ("nars9", n9), ("l2", l2c), ("l1", l1c)):
                if not code:
                    continue
                parent = by_key.get((quantity, level, f"{level}:{code}", split))
                if parent is None:
                    continue
                h = abs(float(child["q50"]) - float(parent["q50"])) / float(parent["iqr"])
                rows.append({"quantity": quantity, "level": level, "stratum": child["stratum"], "split": split,
                             "parent": f"{level}:{code}", "median": child["q50"], "parent_median": parent["q50"],
                             "parent_iqr": parent["iqr"], "h": h, "material": h >= H_MATERIAL,
                             "n_total": n, "panel_tier": child.get("panel_tier")})
    return rows


def level_table(root: DataRoot, shift_rows: list[dict], validation_rows: list[dict], stability_rows: list[dict],
                pinned_rows: list[dict], border_rows: list[dict]) -> list[dict]:
    registry = [r for r in registry_rows(root) if r.get("usable")]
    run_of_level = {level: run for run, level in RUNS.items() if level}
    curved = sorted({q for fk_inputs in CURVE_INPUTS.values() for q, _c, _m in fk_inputs})
    rows = []
    for quantity in curved:
        for level in ("national", "nars9", "l2", "l3"):
            usable = [r for r in registry if r["quantity"] == quantity and r["level"] == level]
            shifts = [r for r in shift_rows if r["quantity"] == quantity and r["level"] == level]
            weights = np.array([float(r["n_total"] or 0) for r in shifts]) if shifts else np.zeros(0)
            hs = np.array([float(r["h"]) for r in shifts]) if shifts else np.zeros(0)
            if level == "l3" and not shifts:                     # Level III against itself
                weights, hs = np.ones(1), np.zeros(1)
            run = run_of_level.get(level)
            functions = [fk for fk, inputs in CURVE_INPUTS.items() if any(q == quantity for q, _c, _m in inputs)]
            val = [r for r in validation_rows if r["run"] == run and r["subject"] in functions and r["region"] == "US"]
            stab = [r for r in stability_rows if r["quantity"] == quantity and r["level"] == level]
            pinned = [r for r in pinned_rows if r["run"] == run and r["function"] in functions]
            border = [r for r in border_rows if r["run"] == run]
            rows.append({
                "quantity": quantity, "level": level, "run": run, "functions": ";".join(functions),
                "criteria_sets": len(usable),
                "relaxed_share": float(np.mean([r.get("panel_tier") == "best_available" for r in usable])) if usable else None,
                "h_mean_weighted": float(np.average(hs, weights=weights)) if hs.size and weights.sum() > 0 else (float(hs.mean()) if hs.size else None),
                "share_reaches_material": float(weights[hs >= H_MATERIAL].sum() / weights.sum()) if weights.sum() > 0 else None,
                "nrsa_rho_pooled": float(np.mean([float(r["rho"]) for r in val if _num(r.get("rho")) is not None])) if any(_num(r.get("rho")) is not None for r in val) else None,
                "nrsa_auc_poor_pooled": float(np.mean([float(r["auc_poor"]) for r in val if _num(r.get("auc_poor")) is not None])) if any(_num(r.get("auc_poor")) is not None for r in val) else None,
                "flip_pass_share": float(np.mean([r["verdict"] == "accept" for r in stab])) if stab else None,
                "pinned_cells": int(sum(1 for r in pinned if str(r.get("pinned")).lower() == "true")) if pinned else None,
                "border_eci_excess": float(border[0]["eci_excess_banded"]) if border and _num(border[0].get("eci_excess_banded")) is not None else None,
            })
    return rows


# ------------------------------------------------------------ border excess
def border_excess(root: DataRoot, values_table) -> list[dict]:
    """Adjacent reaches (comid -> tocomid) across a Level III border vs within
    one region: the mean absolute ECI jump and the share of ECI class flips,
    per run, as cross-border minus within-region."""
    import pyarrow.parquet as pq
    n = values_table.num_rows
    if "tocomid" not in values_table.column_names:
        return []
    comid = np.asarray(values_table.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
    tocomid = np.asarray(values_table.column("tocomid").to_pandas(), dtype=float)
    l3 = np.asarray(values_table.column("l3_code").to_pylist(), dtype=object) if "l3_code" in values_table.column_names else np.full(n, None, dtype=object)
    order = np.argsort(comid, kind="stable")
    sorted_comid = comid[order]
    valid = np.isfinite(tocomid)
    pos = np.searchsorted(sorted_comid, tocomid[valid].astype(np.int64))
    pos = np.minimum(pos, max(len(sorted_comid) - 1, 0))
    found = sorted_comid[pos] == tocomid[valid].astype(np.int64)
    src = np.flatnonzero(valid)[found]
    dst = order[pos[found]]
    cross = np.array([l3[a] is not None and l3[b] is not None and l3[a] != l3[b] for a, b in zip(src, dst)])
    within = np.array([l3[a] is not None and l3[a] == l3[b] for a, b in zip(src, dst)])
    rows = []
    for run in RUNS:
        path = run_path(root, run)
        if not path.exists():
            continue
        table = pq.read_table(path, columns=["comid", "eci_banded", "eci_continuous"])
        run_comid = np.asarray(table.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
        aligned = np.searchsorted(run_comid, comid)
        aligned = np.minimum(aligned, max(len(run_comid) - 1, 0))
        ok = run_comid[aligned] == comid
        row = {"run": run, "pairs_cross": int(cross.sum()), "pairs_within": int(within.sum())}
        for view in ("banded", "continuous"):
            eci = np.full(n, np.nan)
            eci[ok] = np.asarray(table.column(f"eci_{view}").to_pandas(), dtype=float)[aligned[ok]]
            a, b = eci[src], eci[dst]
            both = np.isfinite(a) & np.isfinite(b)
            jump = np.abs(a - b)
            band = lambda x: np.where(x <= 0.39, 0, np.where(x <= 0.69, 1, 2))  # noqa: E731
            flip = band(a) != band(b)
            for label, mask in (("cross", cross & both), ("within", within & both)):
                row[f"eci_jump_{label}_{view}"] = float(jump[mask].mean()) if mask.any() else None
                row[f"flip_{label}_{view}"] = float(flip[mask].mean()) if mask.any() else None
            if row.get(f"eci_jump_cross_{view}") is not None and row.get(f"eci_jump_within_{view}") is not None:
                row[f"eci_excess_{view}"] = row[f"eci_jump_cross_{view}"] - row[f"eci_jump_within_{view}"]
                row[f"flip_excess_{view}"] = row[f"flip_cross_{view}"] - row[f"flip_within_{view}"]
        rows.append(row)
    return rows


# ------------------------------------------------------------ paradigm tables
def paradigm_tables(root: DataRoot, comparison_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """T-P1 from the comparison rows (all views per state and run); T-P2 the
    ECI band confusion between the banded view and the other two per run."""
    import pyarrow.parquet as pq
    t_p1 = [r for r in comparison_rows]
    t_p2 = []
    for run in RUNS:
        path = run_path(root, run)
        if not path.exists():
            continue
        table = pq.read_table(path, columns=["state", "eci_banded", "eci_continuous", "eci_mix"])
        states = np.asarray(table.column("state").to_pylist(), dtype=object)
        banded = np.asarray(table.column("eci_banded").to_pandas(), dtype=float)
        band = lambda x: np.where(x <= 0.39, 0, np.where(x <= 0.69, 1, 2))  # noqa: E731
        for view in ("continuous", "mix"):
            other = np.asarray(table.column(f"eci_{view}").to_pandas(), dtype=float)
            for group in ["US"] + sorted({s for s in states.tolist() if isinstance(s, str)}):
                sel = (np.ones(len(banded), dtype=bool) if group == "US" else states == group) & np.isfinite(banded) & np.isfinite(other)
                if not sel.any():
                    continue
                a, b = band(banded[sel]), band(other[sel])
                confusion = {f"banded_{i}_to_{j}": int(((a == i) & (b == j)).sum()) for i in range(3) for j in range(3)}
                t_p2.append({"run": run, "view": view, "group": group, "n": int(sel.sum()),
                             "share_band_changes": float((a != b).mean()), **confusion})
    return t_p1, t_p2


# ------------------------------------------------------------ the step
def inputs(root: DataRoot, options: Optional[dict] = None) -> str:
    stamps = [(p.name, p.stat().st_size, int(p.stat().st_mtime)) if p.exists() else None
              for p in (values_path(root), registry_path(root), schemes_dir(root) / "scheme_comparison.csv")]
    return digest("stats", ANALYSIS_VERSION, stamps, diagnostic_quantities(), quantity_methods(), 4)


def run(root: DataRoot, progress: Progress, control: Control, options: Optional[dict] = None) -> Path:
    import pyarrow.parquet as pq
    options = dict(options or {})
    reuse = set(options.get("reuse") or ())      # parts whose existing outputs are read instead of recomputed
    stats_dir(root).mkdir(parents=True, exist_ok=True)
    progress.begin("analysis", "stats", total=6, message="stats: distributions")
    columns = ["comid", "state", "l3_code", "l2", "l1", "nars9", "tocomid"] + [c for c, _f, _cap in diagnostic_quantities().values()]
    columns += [c for c, _m in quantity_methods().values()]
    columns = list(dict.fromkeys(columns))
    schema = pq.read_schema(values_path(root)).names
    table = pq.read_table(values_path(root), columns=[c for c in columns if c in schema])
    if "distributions" in reuse and (stats_dir(root) / "diag_metric_stratum.csv").exists():
        progress.say("stats: distributions reused")
        diag_rows = _read_csv(stats_dir(root) / "diag_metric_stratum.csv")
    else:
        diag_rows, var_rows = distributions(table, progress)
        _write(stats_dir(root) / "diag_metric_stratum.csv", diag_rows)
        _write(stats_dir(root) / "variance_decomp.csv", var_rows)
    progress.tick(done=1, message="stats: NRSA validation")
    control.check()
    validation_rows: list[dict] = []
    if "validation" in reuse and validation.validation_path(root).exists():
        progress.say("stats: validation reused")
        validation_rows = _read_csv(validation.validation_path(root))
    elif validation.frame_path(root).exists():
        validation.run_validation(root, progress, boot=int(options.get("boot") or validation.BOOT))
        validation_rows = _read_csv(validation.validation_path(root))
    progress.tick(done=2, message="stats: stability bootstraps")
    control.check()
    stability_rows: list[dict] = []
    from .panels import members_path
    if "stability" in reuse and stability.stability_path(root).exists():
        progress.say("stats: stability reused")
        stability_rows = _read_csv(stability.stability_path(root))
    elif registry_path(root).exists() and members_path(root).exists():
        stability.run_stability(root, progress, control, n_boot=int(options.get("n_boot") or stability.N_BOOT))
        stability_rows = _read_csv(stability.stability_path(root))
    progress.tick(done=3, message="stats: border excess")
    border_rows = border_excess(root, table)
    _write(stats_dir(root) / "border_excess.csv", border_rows)
    progress.tick(done=4, message="stats: level and paradigm tables")
    comparison_rows = _read_csv(schemes_dir(root) / "scheme_comparison.csv")
    pinned_rows = _read_csv(schemes_dir(root) / "pinned_cells.csv")
    targets = set(target_states(root))
    if targets:                       # per-state statistics cover the fully scored states; the national rows pool everything
        comparison_rows = [r for r in comparison_rows if r.get("group") == "US" or r.get("group") in targets]
        pinned_rows = [r for r in pinned_rows if r.get("state") in targets]
    shift_rows = median_shift_rows(root)
    _write(stats_dir(root) / "median_shift.csv", shift_rows)
    _write(stats_dir(root) / "level_T_L1.csv",
           level_table(root, shift_rows, validation_rows, stability_rows, pinned_rows, border_rows))
    t_l2 = []
    for r in comparison_rows:
        if r["view"] != "banded" or r["group"] == "US":
            continue
        pinned = sum(1 for p in pinned_rows if p["run"] == r["run"] and p["state"] == r["group"] and str(p.get("pinned")).lower() == "true")
        t_l2.append({**r, "pinned_cells_state": pinned})
    _write(stats_dir(root) / "level_T_L2.csv", t_l2)
    t_p1, t_p2 = paradigm_tables(root, comparison_rows)
    _write(stats_dir(root) / "paradigm_T_P1.csv", t_p1)
    _write(stats_dir(root) / "paradigm_T_P2.csv", t_p2)
    _write(stats_dir(root) / "paradigm_T_P3.csv", _read_csv(validation.eci_auc_path(root)))
    progress.tick(done=6)
    progress.say(f"stats: {len(diag_rows)} distribution rows, {len(shift_rows)} median shifts, "
                 f"{len(validation_rows)} validation rows, {len(stability_rows)} stability rows")
    return stats_dir(root) / "level_T_L1.csv"
