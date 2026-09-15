"""Step ``runs``: the scoring runs and the three views over the values table.

Five runs, each producing per reach the 20 function indices and classes:
``S0`` (today's catalog, reproduced from the harvested ratings), ``SN``
(national panels), ``S9`` (NARS-9), ``S2`` (Level II) and ``S3`` (Level III).
Across the four regional runs everything is byte-identical except the
panels and curves of the expectation quantities: the pressure metrics keep
their guidance edges, the counts and categories keep their rules, the
published NARS-9 nutrient thresholds keep their native level, and the
geometry quantities are fitted at national x slope class in every run.

Three views per run, all from the same class edges (the 0.69 and 0.39
crossings of each curve, the Good and Poor edges of each guidance line):
``continuous`` (curves interpolated, guidance quantities on a line anchored
at 1.0 / 0.69 / 0.39 / 0.0, counts and categories at their class midpoints),
``banded`` (every class collapsed to its midpoint 0.85 / 0.545 / 0.195) and
``mix`` (continuous only where the criterion is a fitted curve). Function
scores are ``round(index x 15)`` and the rollup is the app's own arithmetic,
vectorised and proven equal to ``easi.scoring.rollup``.

Candidate substitutions (the data-ready ones of ``05_function_alternatives``)
are computed beside the incumbents as ``cand__<function>__<name>`` index and
class columns, so the route step can compare them on the same reaches.

Outputs under ``analysis/schemes/``: ``<run>.parquet`` per run,
``candidates_<run>.parquet``, ``scheme_comparison.csv``, ``pinned_cells.csv``
and ``sanity_gradients.csv``.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from ..paths import DataRoot
from ..state import Control, Progress, digest
from ..stages import common
from . import ANALYSIS_VERSION, stats
from .curves import QUANTITIES, interp, registry_path, registry_rows, resolve_vector  # noqa: F401
from .values import values_path

RUNS = {"S0": None, "SN": "national", "S9": "nars9", "S2": "l2", "S3": "l3"}
VIEWS = ("continuous", "banded", "mix")
MIDPOINT = {"Good": 0.85, "Fair": 0.545, "Poor": 0.195}
CLASSES = ("Poor", "Fair", "Good")
INDEX_EDGES = (0.39, 0.69)
SCORE_MAX = 15
OUTCOMES = ("physical", "chemical", "biological")
#: which incumbent quantity each function scores through a curve in the regional
#: runs: function -> (quantity key, values column of the observed value, method
#: key the reach must be on, or None for every reach)
CURVE_INPUTS: dict[str, list[tuple[str, str, Optional[str]]]] = {
    "surface_water_storage": [("wetland_ws", "c__surface_water_storage", None)],
    "carbon_processing": [("natural_wsrp100", "c__carbon_processing", None)],
    "habitat_provision": [("woody_wsrp100", "v__habitat_provision__woodyRiparian", None)],
    "light_thermal_regime": [("woody_wsrp100", "v__light_thermal_regime__woodyRiparian", None)],
    "floodplain_connectivity": [("er_median", "v__floodplain_connectivity__er", None)],
    "high_flow_dynamics": [("bhr_median", "v__high_flow_dynamics__bhr", None)],
    "channel_floodplain_dynamics": [("bhr_median", "v__channel_floodplain_dynamics__bhr", "bank-height-ratio")],
    "channel_evolution": [("bhr_median", "v__channel_evolution__bhr", None), ("er_median", "v__channel_evolution__er", None)],
    "low_flow_baseflow_dynamics": [("hyd_min", "c__low_flow_baseflow_dynamics", "streamcat-hyd-integrity")],
    "bed_composition_bedform_dynamics": [("sed_min", "c__bed_composition_bedform_dynamics", "streamcat-sed-integrity")],
    "nutrient_cycling": [("chem_min", "c__nutrient_cycling", "streamcat-chem-integrity-nutrient")],
    "water_soil_quality": [("chem_min", "c__water_soil_quality", "streamcat-chem-integrity-regulatory")],
}
#: functions whose criteria are counts or categories in every view
BANDED_ALWAYS = ("community_dynamics", "watershed_connectivity", "population_support")


def schemes_dir(root: DataRoot) -> Path:
    return root.analysis / "schemes"


def run_path(root: DataRoot, run: str) -> Path:
    return schemes_dir(root) / f"{run}.parquet"


def candidates_run_path(root: DataRoot, run: str) -> Path:
    return schemes_dir(root) / f"candidates_{run}.parquet"


# ------------------------------------------------------------ catalog rules
def _edges(bands: list[dict]) -> Optional[tuple[float, float, bool]]:
    """``(good_edge, poor_edge, higher_is_better)`` of a three-band list."""
    if not bands:
        return None
    by = {b["rating"]: b for b in bands}
    if not all(r in by for r in ("Good", "Fair", "Poor")):
        return None
    good, poor = by["Good"], by["Poor"]
    if good.get("max") is not None and good.get("min") is None:          # Good is the low side
        return float(good["max"]), float(poor["min"]), False
    if good.get("min") is not None and good.get("max") is None:          # Good is the high side
        return float(good["min"]), float(poor["max"]), True
    return None


def function_rules() -> dict[str, dict]:
    """Per function: the operator, the inputs with their values column and
    guidance edges (or per-region edges), from the app's catalog."""
    from easi import config
    from easi import screening_methods as sm
    rules: dict[str, dict] = {}
    for mid, meta in config.metrics_by_id().items():
        fk = meta["functionId"].replace("-", "_")
        method = sm.method_for(mid)
        inputs = []
        for inp in method.get("inputs", []):
            if inp.get("contextOnly"):
                continue
            entry = {"key": inp["key"], "column": f"v__{fk}__{inp['key']}", "edges": None, "regional": None}
            if inp.get("regionalBands"):
                entry["regional"] = {str(k).upper(): (float(v[0]), float(v[1])) for k, v in inp["regionalBands"].items()}
                entry["higher"] = False
            elif inp.get("bands"):
                edges = _edges(inp["bands"])
                if edges:
                    entry["edges"], entry["higher"] = (edges[0], edges[1]), edges[2]
            inputs.append(entry)
        method_edges = _edges(method.get("bands") or [])
        rules[fk] = {"operator": method["operator"], "inputs": inputs,
                     "combined_column": f"c__{fk}",
                     "method_edges": (method_edges[0], method_edges[1]) if method_edges else None,
                     "method_higher": method_edges[2] if method_edges else None,
                     "integer": any(i.get("valueType") == "integer" for i in method.get("inputs", []))}
    return rules


# ------------------------------------------------------------ scoring helpers
def line_index(values, good_edge: float, poor_edge: float, higher_is_better: bool,
               domain: tuple = (0.0, None)) -> np.ndarray:
    """The guidance-anchored line: 1.0 at the physical best, 0.69 at the Good
    edge, 0.39 at the Poor edge, 0.0 one Fair-band width beyond it."""
    x = np.asarray(values, dtype=float)
    width = abs(poor_edge - good_edge)
    if higher_is_better:
        lo = poor_edge - width
        hi = good_edge + width
        if domain[0] is not None:
            lo = max(lo, float(domain[0]))
        if domain[1] is not None:
            hi = min(hi, float(domain[1]))
        px, py = [lo, poor_edge, good_edge, hi], [0.0, 0.39, 0.69, 1.0]
    else:
        best = float(domain[0]) if domain[0] is not None else good_edge - width
        px, py = [best, good_edge, poor_edge, poor_edge + width], [1.0, 0.69, 0.39, 0.0]
    px = np.asarray(px, dtype=float)
    py = np.asarray(py, dtype=float)
    keep = np.concatenate([[True], np.diff(px) > 0])
    px, py = px[keep], py[keep]
    out = np.full(x.shape, np.nan)
    ok = np.isfinite(x)
    out[ok] = np.clip(np.interp(x[ok], px, py), 0.0, 1.0) if len(px) >= 2 else np.nan
    return out


def _nanagg(stack: np.ndarray, operator: str) -> np.ndarray:
    """nanmin or nanmax over axis 0 without numpy's all-NaN warning (an all-NaN column is NaN)."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmin(stack, axis=0) if operator == "worst_index" else np.nanmax(stack, axis=0)


def class_of_index(index) -> np.ndarray:
    """Poor at or below 0.39, Fair at or below 0.69, else Good; None where NaN."""
    x = np.asarray(index, dtype=float)
    out = np.full(x.shape, None, dtype=object)
    ok = np.isfinite(x)
    out[ok & (x <= INDEX_EDGES[0])] = "Poor"
    out[ok & (x > INDEX_EDGES[0]) & (x <= INDEX_EDGES[1])] = "Fair"
    out[ok & (x > INDEX_EDGES[1])] = "Good"
    return out


def midpoint_index(classes) -> np.ndarray:
    return np.asarray([MIDPOINT.get(c, np.nan) if c is not None else np.nan for c in classes], dtype=float)


def function_scores(index) -> np.ndarray:
    """``round(index x 15)`` clamped, NaN where unrated (half-to-even, as ``round``)."""
    x = np.asarray(index, dtype=float)
    out = np.full(x.shape, np.nan)
    ok = np.isfinite(x)
    out[ok] = np.clip(np.rint(x[ok] * SCORE_MAX), 0, SCORE_MAX)
    return out


def rollup_frame(scores: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The app's rollup over ``{function_key: score array}``: per outcome
    sum(score x weight) / sum(15 x weight) over the functions with a score,
    the ECI the mean of the available outcomes."""
    from easi import config
    mapping = config.cwa_mapping()
    weights = config.WEIGHTS
    n = len(next(iter(scores.values())))
    out: dict[str, np.ndarray] = {}
    available = []
    for outcome in OUTCOMES:
        weighted = np.zeros(n)
        maximum = np.zeros(n)
        for fk, values in scores.items():
            code = (mapping.get(fk.replace("_", "-")) or {}).get(outcome, "-")
            weight = float(weights.get(code, 0.0))
            if not weight:
                continue
            present = np.isfinite(values)
            weighted[present] += values[present] * weight
            maximum[present] += SCORE_MAX * weight
        sub = np.where(maximum > 0, weighted / np.where(maximum > 0, maximum, 1.0), np.nan)
        out[outcome] = sub
        available.append(sub)
    stacked = np.vstack(available)
    with np.errstate(invalid="ignore"):
        out["eci"] = np.where(np.isfinite(stacked).any(axis=0), np.nanmean(stacked, axis=0), np.nan)
    return out


# ------------------------------------------------------------ the runs
def _col(table, name: str, n: int, kind: str = "float") -> np.ndarray:
    if name not in table.column_names:
        return np.full(n, np.nan) if kind == "float" else np.full(n, None, dtype=object)
    if kind == "float":
        return np.asarray(table.column(name).to_pandas(), dtype=float)
    return np.asarray(table.column(name).to_pylist(), dtype=object)


def curve_index_for(quantity: str, level: Optional[str], values: np.ndarray, frame, registry: list[dict]):
    """``(index, stratum_used, depth)`` for one quantity at one run level;
    NaN and depth -1 where no usable curve resolves."""
    n = len(values)
    if level is None:
        return np.full(n, np.nan), np.full(n, None, dtype=object), np.full(n, -1, dtype=np.int16)
    q = QUANTITIES[quantity]
    split = q.split if q.split else ("slope_class" if q.geometry else None)
    level_used = "national" if q.geometry else level
    used, depth = resolve_vector(registry, quantity, level_used, frame, split=split)
    points_by_key = {}
    for row in registry:
        if row["quantity"] == quantity and row.get("usable"):
            key = row["stratum"] + (f"|{row['split']}" if row.get("split") else "")
            points_by_key[key] = row["points"]
    index = np.full(n, np.nan)
    for key in set(k for k in used.tolist() if k):
        mask = used == key
        index[mask] = interp(points_by_key[key], values[mask])
    return index, used, depth


class Run:
    """One run's per-function indices and classes over the values table."""

    def __init__(self, name: str, level: Optional[str], table, frame, rules: dict, registry: list[dict]):
        self.name, self.level, self.table, self.frame, self.rules, self.registry = name, level, table, frame, rules, registry
        self.n = table.num_rows
        self.index: dict[str, np.ndarray] = {}          # continuous-view index
        self.classes: dict[str, np.ndarray] = {}
        self.mode: dict[str, np.ndarray] = {}
        self.stratum: dict[str, np.ndarray] = {}
        self.depth: dict[str, np.ndarray] = {}
        self.curve_mask: dict[str, np.ndarray] = {}     # True where a fitted curve produced the index

    def score(self) -> None:
        for fk, rule in self.rules.items():
            self._score_function(fk, rule)

    def _s0(self, fk: str):
        classes = _col(self.table, f"rating_{fk}", self.n, "object")
        return midpoint_index(classes), classes

    def _line_inputs(self, fk: str, rule: dict):
        """Per-input line indices for the guidance inputs (None for curve inputs)."""
        per_input = {}
        region = _col(self.table, f"ctx__{fk}__region", self.n, "object")
        for inp in rule["inputs"]:
            values = _col(self.table, inp["column"], self.n)
            if inp.get("regional"):
                index = np.full(self.n, np.nan)
                for code, (good, poor) in inp["regional"].items():
                    mask = region == code
                    if mask.any():
                        index[mask] = line_index(values[mask], good, poor, False)
                per_input[inp["key"]] = index
            elif inp.get("edges"):
                per_input[inp["key"]] = line_index(values, inp["edges"][0], inp["edges"][1], inp["higher"])
        return per_input

    def _score_function(self, fk: str, rule: dict) -> None:
        s0_index, s0_classes = self._s0(fk)
        base_index, base_classes = s0_index.copy(), s0_classes.copy()
        mode = np.full(self.n, "s0", dtype=object)
        stratum = np.full(self.n, None, dtype=object)
        depth = np.full(self.n, -1, dtype=np.int16)
        curve_mask = np.zeros(self.n, dtype=bool)
        operator = rule["operator"]
        method = _col(self.table, f"method_{fk}", self.n, "object")
        rated = np.array([c is not None for c in s0_classes])
        if fk in BANDED_ALWAYS:
            self._store(fk, base_index, base_classes, mode, stratum, depth, curve_mask)
            return
        # 1. guidance lines (pressure and other banded quantities) for the continuous view
        lines = self._line_inputs(fk, rule)
        if operator in ("threshold", "ratio", "sum_capped", "minimum") and rule.get("method_edges"):
            combined = _col(self.table, rule["combined_column"], self.n)
            if operator == "threshold" and not np.isfinite(combined).any():
                combined = _col(self.table, rule["inputs"][0]["column"], self.n) if rule["inputs"] else combined
            line = line_index(combined, rule["method_edges"][0], rule["method_edges"][1], bool(rule["method_higher"]))
            ok = np.isfinite(line) & rated
            base_index[ok] = line[ok]
            mode[ok] = "line"
        elif operator in ("worst_index", "best_index") and lines:
            stack = np.vstack([lines[k] for k in lines])
            agg = _nanagg(stack, operator)
            ok = np.isfinite(agg) & rated & (~np.isnan(stack)).all(axis=0)
            base_index[ok] = agg[ok]
            mode[ok] = "line"
        # 2. fitted curves for the expectation quantities (regional runs only)
        if self.level is not None and fk in CURVE_INPUTS:
            curve_indices = {}
            for quantity, column, method_key in CURVE_INPUTS[fk]:
                values = _col(self.table, column, self.n)
                if method_key is not None:
                    values = np.where(method == method_key, values, np.nan)
                index, used, dep = curve_index_for(quantity, self.level, values, self.frame, self.registry)
                curve_indices[quantity] = index
                has = np.isfinite(index)
                stratum[has] = used[has]
                depth[has] = dep[has]
            if operator in ("worst_index", "best_index"):
                # combine the curve inputs with the remaining line inputs
                curve_keys = {q for q, _c, _m in CURVE_INPUTS[fk]}
                other_lines = [lines[k] for k in lines if k not in ("woodyRiparian", "bhr", "er")]
                parts = list(curve_indices.values()) + other_lines
                stack = np.vstack(parts) if parts else np.full((1, self.n), np.nan)
                agg = _nanagg(stack, operator)
                has_curve = np.isfinite(np.vstack(list(curve_indices.values()))).any(axis=0) if curve_indices else np.zeros(self.n, dtype=bool)
                ok = np.isfinite(agg) & rated & has_curve
            else:
                agg = next(iter(curve_indices.values())) if curve_indices else np.full(self.n, np.nan)
                ok = np.isfinite(agg) & rated
            base_index[ok] = agg[ok]
            mode[ok] = "curve"
            curve_mask[ok] = True
        # classes change only where a fitted curve scored the function: a guidance line
        # re-expresses today's bands for the continuous view and never moves a class
        base_classes = np.where(mode == "curve", class_of_index(base_index), base_classes)
        self._store(fk, base_index, base_classes, mode, stratum, depth, curve_mask)

    def _store(self, fk, index, classes, mode, stratum, depth, curve_mask) -> None:
        self.index[fk] = index
        self.classes[fk] = classes
        self.mode[fk] = mode
        self.stratum[fk] = stratum
        self.depth[fk] = depth
        self.curve_mask[fk] = curve_mask

    def view(self, view: str) -> dict[str, np.ndarray]:
        """Per-function index under a view, then the rollup."""
        scores = {}
        for fk in self.rules:
            if view == "continuous":
                index = self.index[fk]
            elif view == "banded":
                index = midpoint_index(self.classes[fk])
            else:
                index = np.where(self.curve_mask[fk], self.index[fk], midpoint_index(self.classes[fk]))
            scores[fk] = function_scores(index)
        return rollup_frame(scores)


# ------------------------------------------------------------ candidates
def _percentile_edges(values: np.ndarray, lower: float, upper: float) -> tuple[float, float]:
    ok = values[np.isfinite(values)]
    if ok.size == 0:
        return float("nan"), float("nan")
    return float(np.quantile(ok, lower)), float(np.quantile(ok, upper))


def candidate_indices(run: Run) -> dict[str, np.ndarray]:
    """``cand__<function>__<name>`` continuous indices from the data at hand.
    Every candidate records the rule it uses in ``CANDIDATE_NOTES``."""
    t, n, level, frame, registry = run.table, run.n, run.level or "national", run.frame, run.registry
    col = lambda name: _col(t, name, n)  # noqa: E731
    out: dict[str, np.ndarray] = {}

    def curve(quantity: str, column: str):
        index, _used, _depth = curve_index_for(quantity, level, col(column), frame, registry)
        return index

    def worst(*arrays):
        stack = np.vstack(arrays)
        agg = _nanagg(stack, "worst_index")
        agg[np.isnan(stack).any(axis=0)] = np.nan
        return agg

    # 5 Low flow: base flow index, low-flow fraction, gage-adjusted over reference-gage flow
    out["cand__low_flow_baseflow_dynamics__bfi"] = curve("bfiws", "sc__bfiws")
    out["cand__low_flow_baseflow_dynamics__q_min_ratio"] = curve("q_min_ratio", "erom__q_min_ratio")
    out["cand__low_flow_baseflow_dynamics__q_alteration_gate"] = line_index(col("erom__q_alteration_c"), 0.9, 0.7, True, (0.0, 1.0))
    # 12 Bed composition: agriculture on slopes and slope-weighted crossings at the catchment scale
    ag_slopes_cat = col("sc__pctagslpmid2019cat") + col("sc__pctagslphigh2019cat")
    rdcrs = col("sc__rdcrsslpwtdcat")
    p75, p90 = _percentile_edges(rdcrs, 0.75, 0.90)
    out["cand__bed_composition_bedform_dynamics__ag_slopes_crossings"] = worst(
        line_index(ag_slopes_cat, 2.0, 10.0, False), line_index(rdcrs, p75, p90, False))
    # 4 Streamflow regime: regulation plus canal density plus the EROM seasonal alteration
    dor_line = line_index(col("c__streamflow_regime"), 2.0, 15.0, False)
    out["cand__streamflow_regime__dor_canals_erom"] = worst(
        dor_line, line_index(col("sc__canaldensws"), 0.05, 0.5, False),
        line_index(col("erom__q_seasonal_alteration"), 0.1, 0.3, False))
    out["cand__streamflow_regime__dor_canals"] = worst(dor_line, line_index(col("sc__canaldensws"), 0.05, 0.5, False))
    # 20 Watershed connectivity: crossing density and network-snapped dam density
    rdcrsws = col("sc__rdcrsws")
    p50, p90 = _percentile_edges(rdcrsws, 0.50, 0.90)
    nabd = col("sc__nabd_densws")
    nabd_p50 = _percentile_edges(nabd[nabd > 0], 0.5, 0.5)[0] if np.isfinite(nabd).any() and (nabd > 0).any() else float("nan")
    nabd_index = np.where(nabd == 0, 0.85, np.where(nabd <= nabd_p50, 0.545, 0.195)).astype(float)
    nabd_index[~np.isfinite(nabd)] = np.nan
    out["cand__watershed_connectivity__crossings_nabd"] = worst(line_index(rdcrsws, p50, p90, False), nabd_index)
    # 18 Population support: the published model probability
    out["cand__population_support__prg_bmmi"] = line_index(col("sc__prg_bmmi0809"), 0.5, 0.25, True, (0.0, 1.0))
    # 10 Channel and floodplain dynamics: flowline sinuosity by slope class
    out["cand__channel_floodplain_dynamics__sinuosity"] = curve("sinuosity_flowline", "sinuosity_flowline")
    # 3 Reach inflow: the catchment ladder of outfalls, canals and crossings
    npdes, canal, cross = col("sc__npdesdenscat"), col("sc__canaldenscat"), col("sc__rdcrscat")
    c50, c90 = _percentile_edges(cross, 0.50, 0.90)
    kinds = (npdes > 0).astype(int) + (canal > 0).astype(int)
    ladder = np.where((kinds == 0) & (cross <= c50), 0.85, np.where((kinds >= 2) | (cross >= c90), 0.195, 0.545)).astype(float)
    ladder[~(np.isfinite(npdes) & np.isfinite(canal) & np.isfinite(cross))] = np.nan
    out["cand__reach_inflow__catchment_ladder"] = ladder
    # 11 Sediment continuity: agriculture on slopes and the agricultural K factor
    ag_slopes_ws = col("sc__pctagslpmid2019ws") + col("sc__pctagslphigh2019ws")
    out["cand__sediment_continuity__ag_slopes_agk"] = worst(
        line_index(ag_slopes_ws, 2.0, 10.0, False), line_index(col("sc__agkffactws"), 0.25, 0.40, False))
    # 17 Habitat provision: channel heterogeneity, the HABT integrity component
    out["cand__habitat_provision__width_cv"] = curve("bankfull_width_cv", "bankfull_width_cv")
    out["cand__habitat_provision__depth_cv"] = curve("bankfull_depth_cv", "bankfull_depth_cv")
    out["cand__habitat_provision__habt"] = curve("habt_min", "habt_min")
    # 13 Light and thermal: the corridor curve alone, and corridor conversion
    out["cand__light_thermal_regime__woody_only"] = curve("woody_wsrp100", "v__light_thermal_regime__woodyRiparian")
    out["cand__light_thermal_regime__corridor_conversion"] = line_index(col("corridor_conversion_wsrp100"), 10.0, 40.0, False)
    # 14 Carbon processing: the reach corridor
    out["cand__carbon_processing__natural_catrp100"] = curve("natural_catrp100", "natural_catrp100")
    # 2 Surface water storage: wetland cover over hydric soils
    out["cand__surface_water_storage__wetland_retention"] = curve("wetland_retention", "wetland_retention")
    # 6 High flow: the uncensored transect share
    out["cand__high_flow_dynamics__bhr_share"] = curve("bhr_share_ge_1p5", "bhr_share_ge_1p5")
    out["cand__high_flow_dynamics__bhr_share_line"] = line_index(col("bhr_share_ge_1p5"), 1 / 3, 2 / 3, False, (0.0, 1.0))
    # 16 Water and soil quality: aquatic-life use (two variants) with a toxic-source ladder fallback
    for variant in ("strict", "cause_screen"):
        classes = _col(t, f"au__aquatic_life_{variant}", n, "object")
        out[f"cand__water_soil_quality__aquatic_life_{variant}"] = midpoint_index(classes)
    tox = col("sc__tridensws") + col("sc__superfunddensws")
    ladder_parts = []
    for name, values in (("tox", tox), ("mines", col("mines_ws")), ("wwtp", col("sc__wwtpmajordensws")), ("septic", col("sc__septicws"))):
        lo, hi = _percentile_edges(values, 0.75, 0.90)
        ladder_parts.append(line_index(values, lo, hi, False))
    out["cand__water_soil_quality__toxic_ladder"] = worst(*ladder_parts)
    return out


CANDIDATE_NOTES = {
    "cand__low_flow_baseflow_dynamics__bfi": "base flow index (ws) on the regional curve by FCODE class",
    "cand__low_flow_baseflow_dynamics__q_min_ratio": "EROM minimum monthly over mean annual flow on the regional curve",
    "cand__low_flow_baseflow_dynamics__q_alteration_gate": "EROM gage-adjusted over reference-gage flow: 0.9 / 0.7 line",
    "cand__bed_composition_bedform_dynamics__ag_slopes_crossings": "worst of agriculture on mid+high slopes (cat, 2 / 10) and slope-weighted crossings (cat, national P75 / P90)",
    "cand__streamflow_regime__dor_canals_erom": "worst of regulation (2 / 15), canal density (0.05 / 0.5) and EROM seasonal alteration (0.1 / 0.3, provisional)",
    "cand__streamflow_regime__dor_canals": "worst of regulation (2 / 15) and canal density (0.05 / 0.5)",
    "cand__watershed_connectivity__crossings_nabd": "worst of road crossings (ws, national P50 / P90) and network dams (0 Good, at or under the median of dammed reaches Fair)",
    "cand__population_support__prg_bmmi": "predicted probability of good benthic condition: 0.5 / 0.25 line",
    "cand__channel_floodplain_dynamics__sinuosity": "flowline sinuosity on the national curve by slope class",
    "cand__reach_inflow__catchment_ladder": "outfalls, canals and crossings at the catchment scale: none and crossings at or under P50 Good; two kinds or crossings at or above P90 Poor",
    "cand__sediment_continuity__ag_slopes_agk": "worst of agriculture on mid+high slopes (ws, 2 / 10) and the agricultural K factor (0.25 / 0.40)",
    "cand__habitat_provision__width_cv": "bankfull width variability across transects on the national curve by slope class",
    "cand__habitat_provision__depth_cv": "bankfull depth variability across transects on the national curve by slope class",
    "cand__habitat_provision__habt": "HABT integrity component on the regional curve",
    "cand__light_thermal_regime__woody_only": "woody corridor cover on the regional curve, impervious dropped",
    "cand__light_thermal_regime__corridor_conversion": "crop + hay + impervious in the corridor: 10 / 40 line",
    "cand__carbon_processing__natural_catrp100": "natural vegetation in the reach's own corridor on the regional curve",
    "cand__surface_water_storage__wetland_retention": "wetland cover over hydric soils (hydric at least 5 %) on the regional curve",
    "cand__high_flow_dynamics__bhr_share": "share of transects with bank-height ratio at or above 1.5 on the national curve by slope class",
    "cand__high_flow_dynamics__bhr_share_line": "share of transects with bank-height ratio at or above 1.5: 1/3 / 2/3 line",
    "cand__water_soil_quality__aquatic_life_strict": "ATTAINS aquatic-life use, strict rule",
    "cand__water_soil_quality__aquatic_life_cause_screen": "ATTAINS aquatic-life use, cause-screen rule",
    "cand__water_soil_quality__toxic_ladder": "worst of toxic releases plus superfund, mines, major WWTPs, septic (national P75 / P90)",
}


# ------------------------------------------------------------ comparison
def comparison_rows(run: Run, views: dict[str, dict[str, np.ndarray]], states: np.ndarray) -> list[dict]:
    rows = []
    groups = ["US"] + sorted({s for s in states.tolist() if isinstance(s, str)})
    for view, roll in views.items():
        eci = roll["eci"]
        for group in groups:
            sel = np.ones(len(eci), dtype=bool) if group == "US" else (states == group)
            x = eci[sel]
            x = x[np.isfinite(x)]
            if x.size == 0:
                continue
            bands = [float((x <= 0.39).mean()), float(((x > 0.39) & (x <= 0.69)).mean()), float((x > 0.69).mean())]
            pinned = 0
            for fk in run.rules:
                classes = run.classes[fk][sel]
                counts = {c: int((classes == c).sum()) for c in CLASSES}
                total = sum(counts.values())
                if total and max(counts.values()) / total >= 0.90:
                    pinned += 1
            rows.append({"run": run.name, "view": view, "group": group, "n": int(x.size),
                         "eci_p10": float(np.quantile(x, 0.10)), "eci_p50": float(np.quantile(x, 0.50)),
                         "eci_p90": float(np.quantile(x, 0.90)), "eci_sd": float(x.std(ddof=0)),
                         "share_nf": bands[0], "share_ar": bands[1], "share_f": bands[2], "pinned_cells": pinned})
    return rows


def pinned_rows(run: Run, states: np.ndarray) -> list[dict]:
    rows = []
    for group in sorted({s for s in states.tolist() if isinstance(s, str)}):
        sel = states == group
        for fk in run.rules:
            classes = run.classes[fk][sel]
            counts = {c: int((classes == c).sum()) for c in CLASSES}
            total = sum(counts.values())
            if not total:
                continue
            top = max(counts, key=counts.get)
            index = run.index[fk][sel]
            index = index[np.isfinite(index)]
            rows.append({"run": run.name, "state": group, "function": fk, "n": total,
                         "share_poor": counts["Poor"] / total, "share_fair": counts["Fair"] / total,
                         "share_good": counts["Good"] / total, "top_class": top,
                         "pinned": counts[top] / total >= 0.90, "index_sd": float(index.std(ddof=0)) if index.size else None,
                         "curve_share": float(run.curve_mask[fk][sel].mean()),
                         "fallback_depth_mean": float(np.mean(run.depth[fk][sel][run.depth[fk][sel] >= 0])) if (run.depth[fk][sel] >= 0).any() else None})
    return rows


GRADIENTS = (
    ("urban_vs_rural", "v__catchment_hydrology__impervious", ">=", 10.0, "<", 1.0),
    ("agricultural_vs_forested", "v__catchment_hydrology__agriculture", ">=", 50.0, "<", 10.0),
    ("regulated_vs_free", "c__streamflow_regime", ">", 15.0, "==", 0.0),
    ("dammed_vs_free", "nid_dam_count", ">=", 1.0, "==", 0.0),
)


def gradient_rows(run: Run, views: dict[str, dict[str, np.ndarray]]) -> list[dict]:
    """Cliff's delta of the ECI between the degraded and the intact group of
    each gradient (a scheme fails when a delta reverses sign, i.e. the
    degraded group scores higher)."""
    rows = []
    fcode_class = _col(run.table, "fcode_class", run.n, "object")
    for view, roll in views.items():
        eci = roll["eci"]
        for name, column, op_hi, hi, op_lo, lo in GRADIENTS:
            x = _col(run.table, column, run.n)
            degraded = (x >= hi) if op_hi == ">=" else (x > hi)
            intact = (x < lo) if op_lo == "<" else (x == lo)
            both = (degraded | intact) & np.isfinite(eci)
            delta = stats.cliffs_delta(-eci[both], degraded[both]) if both.any() else None
            rows.append({"run": run.name, "view": view, "gradient": name, "n_degraded": int((degraded & np.isfinite(eci)).sum()),
                         "n_intact": int((intact & np.isfinite(eci)).sum()), "cliffs_delta": delta,
                         "reversed": (delta is not None and delta < 0)})
        canal = fcode_class == "canal"
        natural = np.isin(fcode_class, ["perennial", "intermittent"])
        both = (canal | natural) & np.isfinite(eci)
        delta = stats.cliffs_delta(-eci[both], canal[both]) if canal.any() and natural.any() else None
        rows.append({"run": run.name, "view": view, "gradient": "canal_vs_natural", "n_degraded": int((canal & np.isfinite(eci)).sum()),
                     "n_intact": int((natural & np.isfinite(eci)).sum()), "cliffs_delta": delta,
                     "reversed": (delta is not None and delta < 0)})
    return rows


# ------------------------------------------------------------ the step
NEEDED_PREFIXES = ("rating_", "index_", "method_", "v__", "c__", "ctx__", "sc__", "erom__", "au__")
NEEDED_COLUMNS = ("comid", "state", "huc8", "huc12", "l3_code", "l2", "l1", "nars9", "slope_class", "fcode_class",
                  "natural_catrp100", "wetland_retention", "corridor_conversion_wsrp100", "mines_ws",
                  "sinuosity_flowline", "bankfull_width_cv", "bankfull_depth_cv", "bhr_share_ge_1p5", "habt_min",
                  "nid_dam_count")


def load_values(root: DataRoot):
    """The values table restricted to the columns the runs read, plus the
    derived landscape quantities the candidates need."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema = pq.read_schema(values_path(root)).names
    wanted = [c for c in schema if c in NEEDED_COLUMNS or c.startswith(NEEDED_PREFIXES)]
    table = pq.read_table(values_path(root), columns=wanted)
    from .values import derived_landscape
    derived = derived_landscape(table)
    for name, values in derived.items():
        if name not in table.column_names:
            table = table.append_column(name, pa.array(values, pa.float64(), mask=~np.isfinite(values)))
    return table


def frame_for(table):
    """The level and split columns as a pandas frame (``l3`` from the derived key)."""
    import pandas as pd
    n = table.num_rows
    return pd.DataFrame({
        "l3": _col(table, "l3_code", n, "object"), "l2": _col(table, "l2", n, "object"),
        "l1": _col(table, "l1", n, "object"), "nars9": _col(table, "nars9", n, "object"),
        "slope_class": _col(table, "slope_class", n, "object"), "fcode_class": _col(table, "fcode_class", n, "object"),
    })


def inputs(root: DataRoot, options: Optional[dict] = None) -> str:
    stamps = [(p.name, p.stat().st_size, int(p.stat().st_mtime)) if p.exists() else None
              for p in (values_path(root), registry_path(root))]
    return digest("runs", ANALYSIS_VERSION, stamps, sorted(RUNS.items()), VIEWS, sorted(CURVE_INPUTS), 1)


def run(root: DataRoot, progress: Progress, control: Control, options: Optional[dict] = None) -> Path:
    import pandas as pd
    import pyarrow as pa
    if not values_path(root).exists():
        raise RuntimeError("values.parquet not found: run the values step first")
    schemes_dir(root).mkdir(parents=True, exist_ok=True)
    progress.begin("analysis", "runs", total=len(RUNS), message="runs: loading the values table")
    table = load_values(root)
    frame = frame_for(table)
    rules = function_rules()
    registry = registry_rows(root)
    states = _col(table, "state", table.num_rows, "object")
    comparison, pinned, gradients = [], [], []
    for i, (name, level) in enumerate(RUNS.items()):
        control.check()
        run_ = Run(name, level, table, frame, rules, registry)
        run_.score()
        views = {view: run_.view(view) for view in VIEWS}
        columns = {"comid": table.column("comid"), "state": pa.array(states.tolist(), pa.string())}
        for fk in rules:
            columns[f"idx_{fk}"] = pa.array(run_.index[fk], pa.float64(), mask=~np.isfinite(run_.index[fk]))
            columns[f"cls_{fk}"] = pa.array(run_.classes[fk].tolist(), pa.string())
            columns[f"mode_{fk}"] = pa.array(run_.mode[fk].tolist(), pa.string())
            columns[f"stratum_{fk}"] = pa.array(run_.stratum[fk].tolist(), pa.string())
            columns[f"depth_{fk}"] = pa.array(run_.depth[fk], pa.int16())
        for view, roll in views.items():
            for key in ("eci", *OUTCOMES):
                arr = roll[key]
                columns[f"{key}_{view}"] = pa.array(arr, pa.float64(), mask=~np.isfinite(arr))
        common.write_parquet(pa.table(columns), run_path(root, name))
        cands = candidate_indices(run_)
        cand_columns = {"comid": table.column("comid")}
        for key, arr in cands.items():
            cand_columns[f"{key}__idx"] = pa.array(arr, pa.float64(), mask=~np.isfinite(arr))
            cand_columns[f"{key}__cls"] = pa.array(class_of_index(arr).tolist(), pa.string())
        common.write_parquet(pa.table(cand_columns), candidates_run_path(root, name))
        comparison.extend(comparison_rows(run_, views, states))
        pinned.extend(pinned_rows(run_, states))
        gradients.extend(gradient_rows(run_, views))
        progress.tick(done=i + 1, message=f"run {name} scored")
        us = next(r for r in comparison if r["run"] == name and r["view"] == "banded" and r["group"] == "US")
        progress.say(f"run {name}: banded ECI p10/p50/p90 {us['eci_p10']:.3f}/{us['eci_p50']:.3f}/{us['eci_p90']:.3f}, "
                     f"pinned cells {us['pinned_cells']}")
    for name, rows in (("scheme_comparison.csv", comparison), ("pinned_cells.csv", pinned), ("sanity_gradients.csv", gradients)):
        with open(schemes_dir(root) / name, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["run"])
            writer.writeheader()
            writer.writerows(rows)
    (schemes_dir(root) / "candidate_notes.json").write_text(json.dumps(CANDIDATE_NOTES, indent=1), encoding="utf-8")
    return schemes_dir(root) / "scheme_comparison.csv"
