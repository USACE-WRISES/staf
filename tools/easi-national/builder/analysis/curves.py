"""Step ``curves``: the reference curves per quantity, level and stratum.

Every expectation quantity in ``QUANTITIES`` is fitted per stratum of every
level with StreamCurves' engine (``streamcurves.curves.build_reference_curve``,
the IQR-seeded piecewise-linear family DEEP scores with). The two crossings
at index 0.69 and 0.39 are the only class edges the whole analysis uses:
the banded view rates by them and the continuous view interpolates between
the same points, so paradigm is a view of one run. A curve is usable only
when the engine calls it complete, the panel is not a fallback, the
crossings exist, a censored quantity's 0.39 crossing sits inside its domain,
and the quantity is not pressure-driven inside the panel (|Spearman with the
composite pressure| <= 0.30). Zero-inflated quantities whose panel
quartile is zero get no curve (the engine's degenerate seed would score
every zero as 0.0).

Outputs under ``analysis/curves/``: ``curve_registry.parquet`` (+ ``.csv``),
``curve_points.parquet`` and ``curves_<level>.json`` in DEEP's point shape so
the engine phase lifts the chosen level directly.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from ..paths import DataRoot
from ..state import Control, Progress, digest
from ..stages import common
from . import ANALYSIS_VERSION, screens, stats
from .panels import LEVELS, load_landscape, members_path, panels_path
from .values import values_path

INDEX_BANDS = (0.39, 0.69)
PRESSURE_RHO_MAX = 0.30
SPLIT_FLOOR = 30


@dataclass(frozen=True)
class Quantity:
    key: str
    source: str                      # landscape | values
    column: str
    higher_is_better: bool
    domain: tuple = (None, None)
    functions: tuple = ()
    kind: str = "incumbent"          # incumbent | candidate
    zero_inflated: bool = False
    geometry: bool = False           # fitted at national x slope class (never by ecoregion)
    split: Optional[str] = None      # a secondary split column (slope_class, fcode_class)
    cap: Optional[float] = None      # right-censoring cap (the bank-height ratio's 2.0)
    gate: Optional[tuple] = None     # (column, minimum) rows the quantity is defined for
    label: str = ""


QUANTITIES: dict[str, Quantity] = {q.key: q for q in (
    Quantity("wetland_ws", "landscape", "wetland_ws", True, (0.0, 100.0), ("surface_water_storage",),
             zero_inflated=True, label="Wetland cover in the watershed, %"),
    Quantity("wetland_retention", "landscape", "wetland_retention", True, (0.0, None), ("surface_water_storage",),
             kind="candidate", gate=("sc__pcthydricws", 5.0), label="Wetland cover over hydric soils"),
    Quantity("woody_wsrp100", "landscape", "woody_wsrp100", True, (0.0, 100.0),
             ("light_thermal_regime", "habitat_provision"), label="Woody riparian cover, watershed corridor, %"),
    Quantity("natural_wsrp100", "landscape", "natural_wsrp100", True, (0.0, 100.0), ("carbon_processing",),
             label="Natural riparian vegetation, watershed corridor, %"),
    Quantity("woody_catrp100", "landscape", "woody_catrp100", True, (0.0, 100.0), ("light_thermal_regime",),
             kind="candidate", label="Woody riparian cover, reach corridor, %"),
    Quantity("natural_catrp100", "landscape", "natural_catrp100", True, (0.0, 100.0), ("carbon_processing",),
             kind="candidate", label="Natural riparian vegetation, reach corridor, %"),
    Quantity("bfiws", "landscape", "sc__bfiws", True, (0.0, 100.0), ("low_flow_baseflow_dynamics",),
             kind="candidate", split="fcode_class", label="Base flow index, %"),
    Quantity("q_min_ratio", "landscape", "erom__q_min_ratio", True, (0.0, 1.0), ("low_flow_baseflow_dynamics",),
             kind="candidate", split="fcode_class", label="Minimum monthly over mean annual flow"),
    Quantity("q_cv_monthly", "landscape", "erom__q_cv_monthly", False, (0.0, None), ("streamflow_regime",),
             kind="candidate", label="Monthly flow variability (CV)"),
    Quantity("hyd_min", "landscape", "hyd_min", True, (0.0, 1.0), ("low_flow_baseflow_dynamics",),
             label="HYD integrity, min of catchment and watershed"),
    Quantity("sed_min", "landscape", "sed_min", True, (0.0, 1.0), ("bed_composition_bedform_dynamics",),
             label="SED integrity, min of catchment and watershed"),
    Quantity("chem_min", "landscape", "chem_min", True, (0.0, 1.0), ("nutrient_cycling", "water_soil_quality"),
             label="CHEM integrity, min of catchment and watershed"),
    Quantity("habt_min", "landscape", "habt_min", True, (0.0, 1.0), ("habitat_provision",),
             kind="candidate", label="HABT integrity, min of catchment and watershed"),
    Quantity("prg_bmmi0809", "landscape", "sc__prg_bmmi0809", True, (0.0, 1.0), ("population_support",),
             kind="candidate", label="Predicted probability of good benthic condition"),
    Quantity("er_median", "values", "er_median", True, (0.0, None), ("floodplain_connectivity", "channel_evolution"),
             geometry=True, label="Entrenchment ratio, reach median"),
    Quantity("bhr_median", "values", "bhr_median", False, (0.0, 2.0),
             ("high_flow_dynamics", "channel_floodplain_dynamics", "channel_evolution"),
             geometry=True, cap=2.0, label="Bank-height ratio, reach median"),
    Quantity("bhr_share_ge_1p5", "values", "bhr_share_ge_1p5", False, (0.0, 1.0), ("high_flow_dynamics",),
             kind="candidate", geometry=True, label="Share of transects with bank-height ratio >= 1.5"),
    Quantity("sinuosity_flowline", "landscape", "sinuosity_flowline", True, (1.0, None),
             ("channel_floodplain_dynamics", "hyporheic_connectivity"), kind="candidate", geometry=True,
             label="Flowline sinuosity"),
    Quantity("bankfull_width_cv", "values", "bankfull_width_cv", True, (0.0, None), ("habitat_provision",),
             kind="candidate", geometry=True, label="Bankfull width variability across transects (CV)"),
    Quantity("bankfull_depth_cv", "values", "bankfull_depth_cv", True, (0.0, None), ("habitat_provision",),
             kind="candidate", geometry=True, label="Bankfull depth variability across transects (CV)"),
    Quantity("tn", "values", "v__nutrient_cycling__tn", False, (0.0, None), ("nutrient_cycling",),
             label="Total nitrogen, WQP median, mg/L"),
    Quantity("tp", "values", "v__nutrient_cycling__tp", False, (0.0, None), ("nutrient_cycling",),
             label="Total phosphorus, WQP median, mg/L"),
)}


def curves_dir(root: DataRoot) -> Path:
    return root.analysis / "curves"


def registry_path(root: DataRoot) -> Path:
    return curves_dir(root) / "curve_registry.parquet"


def points_path(root: DataRoot) -> Path:
    return curves_dir(root) / "curve_points.parquet"


def level_json_path(root: DataRoot, level: str) -> Path:
    return curves_dir(root) / f"curves_{level}.json"


def registry_rows(root: DataRoot) -> list[dict]:
    """The curve registry as dicts with the point lists decoded (empty
    before the curves step ran)."""
    import pyarrow.parquet as pq
    if not registry_path(root).exists():
        return []
    rows = pq.read_table(registry_path(root)).to_pylist()
    for row in rows:
        row["points"] = json.loads(row.get("points_json") or "[]")
        row["split"] = row.get("split") or ""
    return rows


# ------------------------------------------------------------ fitting
def metric_config(q: Quantity) -> dict:
    return {q.key: {"column_name": q.column, "higher_is_better": q.higher_is_better, "curve_form": "monotone",
                    "domain_min": q.domain[0], "domain_max": q.domain[1], "signed_scale": False,
                    "display_name": q.label or q.key}}


def crossings(points, target: float) -> list[float]:
    from streamcurves import curves as engine
    return engine.reference_curve_threshold_crossings(points, target)


def fit_curve(values, q: Quantity, stratum: str) -> dict:
    """One fit: the engine's status, stats, points and the two class crossings."""
    import pandas as pd
    from streamcurves import curves as engine
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    result = engine.build_reference_curve(pd.DataFrame({q.column: arr}), q.key, metric_config(q),
                                          stratum_label=stratum, build_plots=False)
    row = result["curve_row"]
    points = result["curve_points"]
    status = str(row["curve_status"].iloc[0])
    out = {
        "status": status,
        "n": int(arr.size),
        "q05": float(np.quantile(arr, 0.05)) if arr.size else None,
        "q25": float(row["q25"].iloc[0]) if arr.size else None,
        "q50": float(row["median_val"].iloc[0]) if arr.size else None,
        "q75": float(row["q75"].iloc[0]) if arr.size else None,
        "q95": float(np.quantile(arr, 0.95)) if arr.size else None,
        "iqr": float(row["iqr"].iloc[0]) if arr.size else None,
        "min": float(row["min_val"].iloc[0]) if arr.size else None,
        "max": float(row["max_val"].iloc[0]) if arr.size else None,
        "points": [[float(x), float(y)] for x, y in zip(points["metric_value"], points["index_score"])],
        "x39": None, "x69": None,
    }
    if len(points) >= 2:
        lo = crossings(points, INDEX_BANDS[0])
        hi = crossings(points, INDEX_BANDS[1])
        out["x39"] = lo[0] if len(lo) == 1 else None
        out["x69"] = hi[0] if len(hi) == 1 else None
    return out


def seed_points(q25: float, q75: float, higher_is_better: bool, domain: tuple = (None, None)) -> list[list[float]]:
    """The engine's monotone seed in closed form (for the bootstraps): the
    same five anchors ``build_reference_curve`` places, clamped to the domain."""
    import pandas as pd
    from streamcurves import curves as engine
    iqr = q75 - q25
    if higher_is_better:
        xs = [0.0, q25 * 3 / 7, q25, q75, q75 + iqr * 0.3]
        ys = [0.0, 0.30, 0.70, 1.0, 1.0]
    else:
        xs = [max(0.0, q25 - iqr * 0.3), q25, q75, q75 + iqr * 4 / 3, q75 + iqr * 7 / 3]
        ys = [1.0, 1.0, 0.70, 0.30, 0.0]
    frame = pd.DataFrame({"point_order": [1, 2, 3, 4, 5], "metric_value": xs, "index_score": ys})
    frame = engine.clamp_points_to_domain(frame, domain[0], domain[1])
    return [[float(x), float(y)] for x, y in zip(frame["metric_value"], frame["index_score"])]


def interp(points, x) -> np.ndarray:
    """Index in [0, 1] for each x from a monotone point list: ``np.interp`` on
    the sorted points, byte-equivalent to ``streamcurves.curves.interp_curve``
    for non-decreasing x (max difference about 6e-17), with the engine's own
    interpolation as the guard for degenerate point lists."""
    from streamcurves import curves as engine
    pts = sorted(points, key=lambda p: p[0])
    px = np.asarray([p[0] for p in pts], dtype=float)
    py = np.asarray([p[1] for p in pts], dtype=float)
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan)
    ok = np.isfinite(x)
    if len(px) == 0:
        return out
    if len(px) >= 2 and (np.diff(px) > 0).all():
        out[ok] = np.clip(np.interp(x[ok], px, py), 0.0, 1.0)
    else:
        out[ok] = [engine.interp_curve(pts, float(v)) for v in x[ok]]
    return out


# ------------------------------------------------------------ the step
def _quantity_values(root: DataRoot, q: Quantity, comids: np.ndarray):
    """The quantity's value for the given COMIDs (NaN where absent), from
    the landscape table or the values table."""
    import pyarrow.parquet as pq
    path = landscape_path_for(root, q)
    schema = pq.read_schema(path).names
    if q.column not in schema:
        return None
    columns = ["comid", q.column] + ([q.gate[0]] if q.gate and q.gate[0] in schema else [])
    table = pq.read_table(path, columns=columns)
    table_comids = np.asarray(table.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
    order = np.argsort(table_comids, kind="stable")
    sorted_comids = table_comids[order]
    values = np.asarray(table.column(q.column).to_pandas(), dtype=float)[order]
    pos = np.searchsorted(sorted_comids, comids)
    pos = np.minimum(pos, max(len(sorted_comids) - 1, 0))
    found = sorted_comids[pos] == comids
    out = np.full(len(comids), np.nan)
    out[found] = values[pos[found]]
    if q.gate and q.gate[0] in schema:
        gate = np.asarray(table.column(q.gate[0]).to_pandas(), dtype=float)[order]
        gated = np.full(len(comids), False)
        gated[found] = gate[pos[found]] >= q.gate[1]
        out[~gated] = np.nan
    return out


def landscape_path_for(root: DataRoot, q: Quantity) -> Path:
    from .values import landscape_path
    return landscape_path(root) if q.source == "landscape" else values_path(root)


def _pressure(root: DataRoot, comids: np.ndarray) -> np.ndarray:
    """The composite pressure of the member reaches (for the circularity check)."""
    import pyarrow.parquet as pq
    from .values import landscape_path
    schema = pq.read_schema(landscape_path(root)).names
    columns = ["comid"] + [c for c in screens.PRESSURE_VARIABLES if c in schema]
    table = pq.read_table(landscape_path(root), columns=columns)
    table_comids = np.asarray(table.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
    pos = np.searchsorted(table_comids, comids)
    pos = np.minimum(pos, max(len(table_comids) - 1, 0))
    found = table_comids[pos] == comids
    data = {c: np.asarray(table.column(c).to_pandas(), dtype=float)[pos] for c in columns[1:]}
    pressure, _ = screens.composite_pressure(data)
    pressure[~found] = np.nan
    return pressure


def fit_all(root: DataRoot, members, panels, *, levels, progress: Progress, control: Control) -> list[dict]:
    """Every quantity x level x stratum (x split) fit; returns the registry rows."""
    import pandas as pd
    tier_of = {(r["level"], r["stratum"]): (r["panel_tier"], r["screen"]) for r in panels.to_dict("records")}
    member_comids = np.asarray(members["comid"].to_numpy(), dtype=np.int64)
    pressure = _pressure(root, member_comids)
    rows: list[dict] = []
    total = len(QUANTITIES) * len(levels)
    done = 0
    for q in QUANTITIES.values():
        values = _quantity_values(root, q, member_comids)
        if values is None:
            progress.say(f"curves: {q.key} has no column {q.column!r} yet, skipped")
            done += len(levels)
            progress.tick(done=done)
            continue
        frame = members.assign(_value=values, _pressure=pressure)
        for level in levels:
            control.check()
            if q.geometry and level != "national":
                done += 1
                progress.tick(done=done)
                continue
            sub = frame[frame["level"] == level]
            if q.split and q.split in sub.columns:
                groups = sub.groupby(["stratum", q.split], dropna=True, observed=True)
            elif q.geometry and "slope_class" in sub.columns:
                groups = sub.groupby(["stratum", "slope_class"], dropna=True, observed=True)
            else:
                groups = sub.groupby(["stratum"], dropna=True, observed=True)
            for key, group in groups:
                stratum = key[0] if isinstance(key, tuple) else key
                split = key[1] if isinstance(key, tuple) and len(key) > 1 else ""
                tier, screen = tier_of.get((level, stratum), ("none", "none"))
                vals = group["_value"].to_numpy(dtype=float)
                vals_ok = vals[np.isfinite(vals)]
                row = {"quantity": q.key, "level": level, "stratum": stratum, "split": str(split or ""),
                       "kind": q.kind, "higher_is_better": q.higher_is_better, "panel_tier": tier,
                       "screen": screen, "n_members": int(len(group)), "n": int(vals_ok.size)}
                if vals_ok.size < SPLIT_FLOOR:
                    row.update(status="insufficient_data", usable=False, reason="fewer than 30 values", points=[])
                    rows.append(row)
                    continue
                fit = fit_curve(vals_ok, q, f"{stratum}|{split}" if split else stratum)
                rho = stats.spearman(group["_value"].to_numpy(dtype=float), group["_pressure"].to_numpy(dtype=float))
                row.update(fit)
                row["rho_pressure"] = rho
                row["bp_good"] = fit["q25"] if q.higher_is_better else fit["q75"]
                row["bp_poor"] = fit["q05"] if q.higher_is_better else fit["q95"]
                row["usable"], row["reason"] = usable(q, row, tier)
                rows.append(row)
            done += 1
            progress.tick(done=done, message=f"curves {q.key} at {level}")
    return rows


def usable(q: Quantity, row: dict, tier: str) -> tuple[bool, str]:
    if tier == "none":
        return False, "no panel at this level"
    if row.get("status") != "complete":
        return False, f"engine status {row.get('status')}"
    if row.get("x39") is None or row.get("x69") is None:
        return False, "missing a class crossing"
    if q.zero_inflated and (row.get("q25") or 0) <= 0:
        return False, "zero-inflated: panel first quartile is zero"
    if q.cap is not None:
        # a right-censored panel: its upper quartile sits at the cap, so the engine's domain
        # clamp squeezes the seed and every crossing is an artefact of the cap
        quartile = row.get("q75") if not q.higher_is_better else row.get("q25")
        edge = row["x39"] if not q.higher_is_better else row["x69"]
        if (quartile is not None and quartile >= q.cap - 1e-9) or edge >= q.cap - 1e-9:
            return False, f"censored at the {q.cap} cap"
    rho = row.get("rho_pressure")
    if rho is not None and abs(rho) > PRESSURE_RHO_MAX:
        if tier == "best_available":
            return False, f"pressure-driven inside a relaxed panel (rho {rho:.2f})"
        return True, f"pressure-driven inside the panel (rho {rho:.2f}), strict tier only"
    return True, ""


PARENT = {"l3": ("l3", "l2", "l1", "national"), "l2": ("l2", "l1", "national"), "l1": ("l1", "national"),
          "nars9": ("nars9", "national"), "national": ("national",)}


def resolve_vector(registry_rows: list[dict], quantity: str, level: str, frame, split: Optional[str] = None):
    """``(stratum_used, depth)`` per row of ``frame`` (which carries the
    level columns and the split column): the first usable curve along the
    parent chain of ``level``; depth 0 is the level itself, -1 no curve."""
    usable_keys = {(r["level"], r["stratum"], r.get("split") or "") for r in registry_rows
                   if r["quantity"] == quantity and r.get("usable")}
    n = len(frame)
    used = np.full(n, None, dtype=object)
    depth = np.full(n, -1, dtype=np.int16)
    split_values = frame[split].astype("object").to_numpy() if split and split in frame.columns else np.full(n, "", dtype=object)
    for d, chain_level in enumerate(PARENT[level]):
        codes = (np.full(n, "national", dtype=object) if chain_level == "national"
                 else frame[chain_level].astype("object").to_numpy())
        for i in np.flatnonzero(depth < 0):
            code = codes[i]
            if code is None:
                continue
            key = (chain_level, f"{chain_level}:{code}", str(split_values[i] or "") if split else "")
            if key in usable_keys:
                used[i] = key[1] + (f"|{key[2]}" if key[2] else "")
                depth[i] = d
    return used, depth


def inputs(root: DataRoot, options: Optional[dict] = None) -> str:
    stamps = [(p.name, p.stat().st_size, int(p.stat().st_mtime)) if p.exists() else None
              for p in (panels_path(root), members_path(root), values_path(root))]
    levels = tuple((options or {}).get("levels") or LEVELS)
    return digest("curves", ANALYSIS_VERSION, stamps, levels, sorted(QUANTITIES), INDEX_BANDS, PRESSURE_RHO_MAX, 1)


def run(root: DataRoot, progress: Progress, control: Control, options: Optional[dict] = None) -> Path:
    import pandas as pd
    import pyarrow.parquet as pq
    if not panels_path(root).exists():
        raise RuntimeError("reference panels not found: run the panels step first")
    levels = list((options or {}).get("levels") or LEVELS)
    curves_dir(root).mkdir(parents=True, exist_ok=True)
    panels = pq.read_table(panels_path(root)).to_pandas()
    members = pq.read_table(members_path(root)).to_pandas()
    progress.begin("analysis", "curves", total=len(QUANTITIES) * len(levels), message="curves: fitting")
    rows = fit_all(root, members, panels, levels=levels, progress=progress, control=control)
    registry = pd.DataFrame([{k: v for k, v in r.items() if k != "points"} for r in rows])
    registry["points_json"] = [json.dumps(r.get("points") or []) for r in rows]
    common.write_parquet(registry, registry_path(root))
    registry.to_csv(registry_path(root).with_suffix(".csv"), index=False)
    point_rows = [{"quantity": r["quantity"], "level": r["level"], "stratum": r["stratum"], "split": r["split"],
                   "point_order": i + 1, "x": x, "y": y}
                  for r in rows for i, (x, y) in enumerate(r.get("points") or [])]
    common.write_parquet(pd.DataFrame(point_rows, columns=["quantity", "level", "stratum", "split", "point_order", "x", "y"]),
                         points_path(root))
    for level in levels:
        payload: dict = {}
        for r in rows:
            if r["level"] != level or not r.get("usable"):
                continue
            key = r["stratum"] + (f"|{r['split']}" if r.get("split") else "")
            payload.setdefault(r["quantity"], {})[key] = {
                "points": [{"x": x, "y": y} for x, y in r["points"]], "n": r["n"], "status": r["status"],
                "panel_tier": r["panel_tier"], "x39": r["x39"], "x69": r["x69"]}
        level_json_path(root, level).write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    usable_n = int(registry["usable"].sum()) if len(registry) else 0
    progress.say(f"curve_registry.parquet: {len(registry):,} fits, {usable_n:,} usable")
    return registry_path(root)
