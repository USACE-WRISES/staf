"""NRSA agreement statistics for the stats step: how each function's desktop
class and index, under each run, and each candidate substitution, track the
field condition at the NRSA stations. Pooled nationally with a NARS-9
breakdown (4,378 stations cannot validate 85 Level III regions), unweighted,
with station bootstraps for the national rows.

Per subject (a function, or a candidate ``cand__<function>__<name>``) and
target: AUC of the desktop index for the field class Poor against the rest
(the probability a truly poor station scores lower) and Good against the
rest, Spearman's rho between the index and the continuous field indicator
(oriented so a positive rho is agreement), the linear-weighted kappa between
the desktop class and the field class, the floors (30 stations and 10 per
class) and the C5 verdict. The ECI-level check per view: AUC for EPA's
reference against most-disturbed stations and for benthic Good against Poor.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

import numpy as np

from ..paths import DataRoot
from . import stats
from .nrsa import CYCLES, frame_path
from .schemes import CLASSES, Run, candidate_indices, class_of_index, frame_for, function_rules, registry_rows, RUNS, VIEWS

#: function -> (primary class target key, [(continuous field column, sign)])
TARGETS: dict[str, tuple[Optional[str], list[tuple[str, int]]]] = {
    "habitat_provision": ("instrmcvr", [("a__phab_XFC_NAT", 1), ("a__phab_XFC_LWD", 1), ("a__phab_RP100", 1)]),
    "light_thermal_regime": ("ripveg", [("a__phab_XCDENMID", 1), ("a__phab_XCMGW", 1)]),
    "carbon_processing": ("ripveg", [("a__phab_XCMGW", 1), ("a__phab_XPCMG", 1), ("a__phab_LWDeqVolM100", 1)]),
    "bed_composition_bedform_dynamics": ("bedsed", [("a__phab_LRBS_use", 1), ("a__phab_PCT_SAFN", -1), ("a__phab_XEMBED", -1)]),
    "sediment_continuity": ("bedsed", [("a__phab_LRBS_use", 1), ("a__phab_PCT_SAFN", -1), ("a__chem_TURB", -1)]),
    "low_flow_baseflow_dynamics": (None, [("a__phab_XWD_RAT", 1), ("a__phab_PCT_DR", -1)]),
    "high_flow_dynamics": (None, [("inc_ratio", -1)]),
    "floodplain_connectivity": (None, [("inc_ratio", -1)]),
    "channel_evolution": (None, [("inc_ratio", -1), ("a__phab_LRBS_use", 1)]),
    "channel_floodplain_dynamics": (None, [("a__phab_XBKA", -1), ("a__phab_XUN", 1)]),
    "hyporheic_connectivity": (None, [("a__phab_XSLOPE_use", 1), ("a__phab_SINU", 1)]),
    "nutrient_cycling": ("ntl", [("a__chem_NTL", -1), ("a__chem_PTL", -1)]),
    "water_soil_quality": ("chem_any", [("a__chem_COND", -1), ("a__chem_NTL", -1)]),
    "population_support": ("bent_mmi", [("x__mmi_bent", 1), ("a__bent_EPT_NTAX", 1)]),
    "community_dynamics": (None, [("a__fish_ALIENPTAX", -1), ("a__fish_ALIENPIND", -1)]),
    "watershed_connectivity": (None, [("a__fish_MIGRPTAX", 1), ("a__fish_RHEOPTAX", 1)]),
    "catchment_hydrology": ("bent_mmi", [("a__phab_W1_HALL", -1), ("a__phab_RDIST1", -1)]),
    "reach_inflow": ("bent_mmi", [("a__phab_W1_HALL", -1)]),
    "streamflow_regime": ("bent_mmi", []),
    "surface_water_storage": (None, []),
}
FLOOR_N = 30
FLOOR_CLASS = 10
BOOT = 1000
SEED = 17


def validation_path(root: DataRoot) -> Path:
    return root.analysis / "validation" / "nrsa_validation.csv"


def eci_auc_path(root: DataRoot) -> Path:
    return root.analysis / "validation" / "eci_auc.csv"


# ------------------------------------------------------------ the station table
def station_rows(root: DataRoot):
    """One desktop row per station (the frame repeats it per cycle) and,
    per station, the latest-cycle value of every target and field indicator."""
    import pyarrow.parquet as pq
    table = pq.read_table(frame_path(root))
    frame = table.to_pandas()
    frame = frame[frame["station_key"].notna()].copy()
    if "a__phab_XINC_H" in frame.columns and "a__phab_XBKF_H" in frame.columns:
        with np.errstate(divide="ignore", invalid="ignore"):
            frame["inc_ratio"] = frame["a__phab_XINC_H"] / frame["a__phab_XBKF_H"].where(frame["a__phab_XBKF_H"] > 0)
    # the derived chemistry class: Poor when any of the chemistry classes is Poor, Good when all are Good
    chem = [c for c in ("t__ntl", "t__ptl", "t__anc", "t__sal") if c in frame.columns]
    if chem:
        block = frame[chem]
        any_poor = (block == "Poor").any(axis=1)
        all_good = (block == "Good").all(axis=1) & block.notna().any(axis=1)
        frame["t__chem_any"] = np.where(any_poor, "Poor", np.where(all_good, "Good", np.where(block.notna().any(axis=1), "Fair", None)))
    frame = frame.sort_values(["station_key", "cycle_rank"], kind="stable")
    desktop_columns = [c for c in frame.columns if not (c.startswith(("t__", "x__", "a__", "land_")) or c in ("cycle", "cycle_rank", "site_id", "visit_no", "weight", "strah_cat", "ag_eco9_raw", "unique_id", "rt_nrsa", "inc_ratio"))]
    stations = frame.drop_duplicates("station_key", keep="first")[desktop_columns].reset_index(drop=True)
    target_columns = [c for c in frame.columns if c.startswith(("t__", "x__", "a__")) or c in ("rt_nrsa", "inc_ratio")]
    latest = frame.groupby("station_key", sort=False)[target_columns].first()      # first non-null per column in cycle order
    stations = stations.merge(latest, left_on="station_key", right_index=True, how="left")
    return stations


def _positive_masks(classes: np.ndarray) -> dict[str, np.ndarray]:
    return {c: classes == c for c in CLASSES}


def agreement(index: np.ndarray, desktop_class: np.ndarray, target_class: Optional[np.ndarray],
              continuous: list[tuple[np.ndarray, int]], *, boot: int = 0, seed: int = SEED) -> dict:
    """The statistics for one subject, one target set and one station subset."""
    out: dict = {"n": int(np.isfinite(index).sum())}
    if target_class is not None:
        # Missing and unrecognized labels cannot serve as negative AUC controls.
        known_class = np.array([isinstance(c, str) and c in CLASSES for c in target_class], dtype=bool)
        have = np.isfinite(index) & known_class
        classes = target_class[have]
        idx = index[have]
        counts = {c: int((classes == c).sum()) for c in CLASSES}
        out.update({"n_class": int(have.sum()), "n_poor": counts["Poor"], "n_fair": counts["Fair"], "n_good": counts["Good"]})
        out["auc_poor"] = stats.auc(-idx, classes == "Poor")
        out["auc_good"] = stats.auc(idx, classes == "Good")
        out["kappa"] = stats.weighted_kappa(desktop_class[have].tolist(), classes.tolist())
        if boot and out["auc_poor"] is not None:
            rows = np.arange(len(idx))
            samples = stats.cluster_bootstrap(rows, lambda r: stats.auc(-idx[r], (classes[r] == "Poor")), n_boot=boot, seed=seed)
            out["auc_poor_lo"], out["auc_poor_hi"] = stats.interval(samples)
    rhos = []
    for values, sign in continuous:
        rho = stats.spearman(index, sign * np.asarray(values, dtype=float))
        if rho is not None:
            rhos.append(rho)
    out["rho"] = float(np.mean(rhos)) if rhos else None
    out["rho_targets"] = len(rhos)
    if boot and rhos:
        # the interval of the same statistic: the mean Spearman over the targets, resampling stations
        targets = [sign * np.asarray(values, dtype=float) for values, sign in continuous]
        ok = np.isfinite(index) & np.any([np.isfinite(v) for v in targets], axis=0)
        idx = index[ok]
        targets = [v[ok] for v in targets]
        if ok.sum() >= 3:
            def mean_rho(r):
                found = [x for x in (stats.spearman(idx[r], v[r]) for v in targets) if x is not None]
                return float(np.mean(found)) if found else None
            samples = stats.cluster_bootstrap(np.arange(len(idx)), mean_rho, n_boot=boot, seed=seed + 1)
            out["rho_lo"], out["rho_hi"] = stats.interval(samples)
    return out


REGIONS_REQUIRED = 6


def regional_consistency(us: dict, regional: list[dict]) -> tuple[int, int]:
    """C5's regional clause: the number of NARS-9 regions with at least
    ``FLOOR_N`` stations, and how many of them hold (AUC(Poor) at least 0.55
    where there is a class target, and rho with the national sign)."""
    sign = float(np.sign(us.get("rho") or 0.0))
    ok = 0
    for row in regional:
        auc, rho = row.get("auc_poor"), row.get("rho")
        if auc is None and rho is None:
            continue
        auc_ok = auc is None or auc >= 0.55
        sign_ok = rho is None or sign == 0 or float(np.sign(rho)) == sign
        ok += int(auc_ok and sign_ok)
    return len(regional), ok


def verdict(row: dict) -> str:
    """C5: validated nationally by AUC(Poor) >= 0.65 with a lower bound above
    0.55, or by |rho| >= 0.25 with the interval excluding zero (the regional
    clause is applied afterwards by ``validate``)."""
    auc_ok = (row.get("auc_poor") is not None and row["auc_poor"] >= 0.65
              and row.get("auc_poor_lo") is not None and row["auc_poor_lo"] > 0.55)
    rho_ok = (row.get("rho") is not None and abs(row["rho"]) >= 0.25 and row.get("rho_lo") is not None
              and row.get("rho_hi") is not None and (row["rho_lo"] > 0 or row["rho_hi"] < 0))
    if auc_ok or rho_ok:
        return "validated"
    if row.get("n", 0) < FLOOR_N:
        return "below floor"
    return "not validated"


def validate(root: DataRoot, progress, *, boot: int = BOOT) -> tuple[list[dict], list[dict]]:
    """The validation rows (per run, subject, target set, region) and the
    ECI-level AUC rows (per run, view, region)."""
    import pyarrow as pa
    stations = station_rows(root)
    table = pa.Table.from_pandas(stations, preserve_index=False)
    from .values import concrete
    table = concrete(table)
    rules = function_rules()
    registry = registry_rows(root)
    frame = frame_for(table)
    n = table.num_rows
    regions = np.asarray(stations["station_nars9"].tolist(), dtype=object) if "station_nars9" in stations else np.full(n, None, dtype=object)
    groups = ["US"] + sorted({r for r in regions.tolist() if isinstance(r, str)})   # pandas 3: None reads as NaN
    rt = np.asarray(stations["rt_nrsa"].tolist(), dtype=object) if "rt_nrsa" in stations else np.full(n, None, dtype=object)
    bent = np.asarray(stations["t__bent_mmi"].tolist(), dtype=object) if "t__bent_mmi" in stations else np.full(n, None, dtype=object)

    def column(name: str) -> np.ndarray:
        return np.asarray(stations[name].to_numpy(), dtype=float) if name in stations.columns else np.full(n, np.nan)

    def target_class(key: Optional[str]):
        if key is None or f"t__{key}" not in stations.columns:
            return None
        return np.asarray(stations[f"t__{key}"].tolist(), dtype=object)

    rows: list[dict] = []
    eci_rows: list[dict] = []
    for run_name, level in RUNS.items():
        run_ = Run(run_name, level, table, frame, rules, registry)
        run_.score()
        start = len(rows)
        subjects: dict[str, tuple[np.ndarray, np.ndarray, str]] = {}
        for fk in rules:
            subjects[fk] = (run_.index[fk], run_.classes[fk], fk)
        for key, index in candidate_indices(run_).items():
            fk = key.split("__")[1]
            subjects[key] = (index, class_of_index(index), fk)
        for subject, (index, classes, fk) in subjects.items():
            class_key, continuous = TARGETS.get(fk, (None, []))
            target = target_class(class_key)
            cont = [(column(name), sign) for name, sign in continuous if name in stations.columns]
            for group in groups:
                sel = np.ones(n, dtype=bool) if group == "US" else (regions == group)
                idx = np.where(sel, index, np.nan)
                stat = agreement(idx, classes, target, cont, boot=(boot if group == "US" else 0))
                row = {"run": run_name, "subject": subject, "function": fk, "target": class_key or "",
                       "continuous_targets": ";".join(name for name, _ in continuous if name in stations.columns),
                       "region": group, **stat}
                row["inherited"] = bool(row.get("n", 0) < FLOOR_N or (target is not None and min(row.get("n_poor", 0) + row.get("n_fair", 0), row.get("n_good", 0)) < FLOOR_CLASS))
                row["verdict"] = verdict(row) if group == "US" else ""
                rows.append(row)
        by_subject: dict[str, list[dict]] = {}
        for row in rows[start:]:
            by_subject.setdefault(row["subject"], []).append(row)
        for subject_rows in by_subject.values():
            us = next((r for r in subject_rows if r["region"] == "US"), None)
            if us is None:
                continue
            regional = [r for r in subject_rows if r["region"] != "US" and (r.get("n") or 0) >= FLOOR_N]
            us["regions_n"], us["regions_ok"] = regional_consistency(us, regional)
            if us["verdict"] == "validated" and us["regions_n"] and us["regions_ok"] < min(REGIONS_REQUIRED, us["regions_n"]):
                us["verdict"] = "validated nationally, inconsistent by region"
        for view in VIEWS:
            roll = run_.view(view)
            eci = roll["eci"]
            for group in groups:
                sel = (np.ones(n, dtype=bool) if group == "US" else (regions == group)) & np.isfinite(eci)
                r_vs_im = sel & np.isin(rt, ["R", "Im"])
                good_vs_poor = sel & np.isin(bent, ["Good", "Poor"])
                eci_rows.append({
                    "run": run_name, "view": view, "region": group,
                    "n_rt": int(r_vs_im.sum()), "auc_rt_r_vs_im": stats.auc(eci[r_vs_im], rt[r_vs_im] == "R") if r_vs_im.any() else None,
                    "n_bent": int(good_vs_poor.sum()), "auc_bent_good_vs_poor": stats.auc(eci[good_vs_poor], bent[good_vs_poor] == "Good") if good_vs_poor.any() else None,
                })
        progress.say(f"validation: run {run_name}, {len(subjects)} subjects over {n:,} stations")
    return rows, eci_rows


def write_csv(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames or ["run"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def run_validation(root: DataRoot, progress, *, boot: int = BOOT) -> Path:
    rows, eci_rows = validate(root, progress, boot=boot)
    write_csv(eci_auc_path(root), eci_rows)
    return write_csv(validation_path(root), rows)
