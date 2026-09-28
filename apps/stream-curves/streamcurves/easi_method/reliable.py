"""The geometry curves evaluated and refit on reliable reference members (WP-R6c, E5b).

Owner decision D15 (2026-09-28): the reliable cross sections of the qualifying least-disturbed
reaches evaluate the shipped geometry curves and refit them where warranted. ``xs_quality``
is the per-reach K2b quality table the national builder writes from the stored evidence
(``tools/easi-national/builder/analysis/xs_quality.py``: the same ``cross_section_quality``
record the evaluator reads); a member is reliable for a quantity when its cross sections carry
the ratio, no ``low_quality`` flag is set and the ratio is inside its physical range, exactly
the population an E5b package rates that quantity on. The fits are the shipped fit rules
(``refit.fit_registry``: the geometry rule, national by slope class; ``refit.national_entrenchment``:
the unsplit national fallback), under the members package's own tail endpoints, on the
reliable members only.

The materiality rule (stated before the fits were read): a shipped curve is refit when a
quartile of the reliable-member fit moves by more than the ACC-04 shift (0.20 of the shipped
curve's interquartile range, ``methodology_config.yaml`` ``acceptance.scoring_stability``) or a
class boundary (the 0.39 or 0.69 crossing) moves by at least the rounding the ratios are
measured and displayed at (0.01). A quantity whose reliable-member fit is not usable under
the shipped usability rules (the bank-height ratio, censored at its 2.0 cap) keeps its shipped
rule and the finding is recorded. Nothing here reads a developer path: the quality table and
the members package are files the caller names.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from . import fit_recipe as fr
from . import refit

#: the ACC-04 shift (methodology_config.yaml acceptance.scoring_stability.max_quartile_shift_iqr)
ACC04_SHIFT_IQR = 0.20
#: the rounding the two ratios are measured and displayed at (two decimals)
RATIO_ROUNDING = 0.01
GEOMETRY_QUANTITIES = ("er_median", "bhr_median")
#: the quantity each shipped geometry rule reads and the set (or fixed bands) it rates by
SHIPPED = {"er_median": {"set": "entrenchment", "rule": "curve by slope class, national fallback"},
           "bhr_median": {"set": None, "rule": "fixed bands 1.3 and 1.5 (published thresholds)"}}
CLASS_COLUMNS = ("slope_class", "da_class")
#: the E5b applicability rule's flag per quantity (``screening_methods._withhold_flags``)
RANGE_FLAG = {"er_median": "out_of_range_er", "bhr_median": "out_of_range_bhr"}
RATIO_COLUMN = {"er_median": "er", "bhr_median": "bhr"}


def load_quality(path: Path):
    """The K2b quality table (``xs_quality.parquet``) as a pandas frame indexed by comid."""
    import pyarrow.parquet as pq
    frame = pq.read_table(Path(path)).to_pandas()
    if "comid" not in frame.columns:
        raise ValueError(f"{path} has no comid column")
    return frame.set_index("comid", drop=False)


def _flag_mask(frame, flag: str):
    flags = frame["flags"].fillna("").astype(str)
    return flags.str.split(",").apply(lambda items: flag in items).to_numpy(dtype=bool)


def reliability(quality, quantity: str) -> dict:
    """Per comid of the quality table, for one quantity: ``has`` (the sections carry the ratio),
    ``three_rules`` (at least three sections carry it, bankfull not extrapolated, not every
    section by the crest scan) and ``k2b`` (no low_quality flag and the ratio inside its
    physical range: what an E5b package rates), as boolean numpy arrays aligned to the table."""
    import pandas as pd
    ratio = RATIO_COLUMN[quantity]
    ok = (quality["xs_status"].astype(str) == "ok").to_numpy() & pd.to_numeric(quality[ratio], errors="coerce").notna().to_numpy()
    sections = pd.to_numeric(quality[f"{ratio}_sections"], errors="coerce").fillna(0).to_numpy()
    extrapolated = quality["bankfull_extrapolated"].fillna(False).astype(bool).to_numpy()
    crest = (quality["detection"].fillna("").astype(str) == "crest_scan").to_numpy()
    three = ok & (sections >= 3) & ~extrapolated & ~crest
    k2b = ok & ~_flag_mask(quality, "low_quality") & ~_flag_mask(quality, RANGE_FLAG[quantity])
    return {"has": ok, "three_rules": three, "k2b": k2b}


def select_members(members, quality, quantity: str, *, level: str = "national"):
    """``(reliable members, counts)``: the members of ``level`` whose cross sections are
    reliable for ``quantity`` under K2b, and the counts per stratum and per slope and
    drainage-area class (members, members with the ratio, the brief's three-rule reliability,
    K2b reliability). Members the quality table does not know (no stored evidence) carry no
    ratio and are counted as such."""
    import pandas as pd
    sub = members[members["level"] == level].copy()
    masks = reliability(quality, quantity)
    lookup = pd.DataFrame({k: v for k, v in masks.items()}, index=quality.index)
    joined = lookup.reindex(sub["comid"].to_numpy())
    for key in masks:
        sub[f"_{key}"] = joined[key].fillna(False).astype(bool).to_numpy()
    counts = {"quantity": quantity, "level": level, "total": _count_block(sub)}
    counts["by_stratum"] = {str(k): _count_block(g) for k, g in sub.groupby("stratum", dropna=False, observed=True)}
    for column in CLASS_COLUMNS:
        if column in sub.columns:
            counts[f"by_{column}"] = {str(k) if k is not None and k == k else "unknown": _count_block(g)
                                      for k, g in sub.groupby(column, dropna=False, observed=True)}
    if all(c in sub.columns for c in CLASS_COLUMNS):
        counts["by_slope_class_and_da_class"] = {
            f"{s}|{d}": _count_block(g) for (s, d), g in sub.groupby(list(CLASS_COLUMNS), dropna=False, observed=True)}
    reliable = sub[sub["_k2b"]].drop(columns=[c for c in sub.columns if c.startswith("_")])
    return reliable, counts


def _count_block(frame) -> dict:
    return {"members": int(len(frame)), "with_ratio": int(frame["_has"].sum()),
            "three_rules": int(frame["_three_rules"].sum()), "k2b": int(frame["_k2b"].sum())}


def fit_reliable(members_reliable, values, panels, *, quantities: Iterable[str] = GEOMETRY_QUANTITIES,
                 tail_offsets_iqr=None) -> dict:
    """The shipped fit rules on the reliable members: the registry rows of the geometry
    quantities at the national level by slope class (``refit.fit_registry``) and the unsplit
    national entrenchment fallback (``refit.national_entrenchment``)."""
    rows = refit.fit_registry(members_reliable, values, panels, quantities=list(quantities), levels=("national",),
                              tail_offsets_iqr=tail_offsets_iqr)
    fallback = refit.national_entrenchment(members_reliable, values, panels, tail_offsets_iqr=tail_offsets_iqr)
    return {"rows": rows, "national_entrenchment": fallback}


def assemble_entrenchment(fit: dict) -> dict:
    """The refit ``entrenchment`` set in the method file's shape: the usable slope-class fits
    and the national fallback (``refit.operational_curves`` for one set). A slope class whose
    reliable-member fit is not usable is left out (the method falls back to national there)."""
    curves = {}
    for r in fit["rows"]:
        if (r["quantity"] == "er_median" and r["level"] == "national" and r["stratum"] == "national:national"
                and r.get("split") in refit.SLOPE_CLASSES and r.get("usable")):
            curves[r["split"]] = fr._curve(r)
    fallback = fit["national_entrenchment"]
    if fallback.get("usable"):
        curves["national"] = fr._curve(fallback)
    q = fr.QUANTITIES["er_median"]
    return {"higherIsBetter": bool(q.higher_is_better), "quantity": "er_median", "stratifier": "slope_class",
            "curves": curves}


def _delta(a, b) -> Optional[float]:
    if a is None or b is None:
        return None
    return float(a) - float(b)


def compare_curve(shipped: dict, refit_row: Optional[dict], *, shift: float = ACC04_SHIFT_IQR,
                  rounding: float = RATIO_ROUNDING) -> dict:
    """One curve's comparison: the shipped and refit quartiles and class boundaries, their
    deltas, the ACC-04 allowance (``shift`` of the shipped interquartile range) and the
    materiality verdict under the stated rule."""
    out = {"shipped": {k: shipped.get(k) for k in ("n", "nMembers", "q25", "q50", "q75", "x39", "x69", "status", "panelTier")},
           "refit": None, "deltas": {}, "iqr_shipped": None, "allowance_iqr": None, "material": False, "reasons": []}
    if shipped.get("q25") is not None and shipped.get("q75") is not None:
        out["iqr_shipped"] = float(shipped["q75"]) - float(shipped["q25"])
        out["allowance_iqr"] = shift * out["iqr_shipped"]
    if refit_row is None:
        out["reasons"].append("no reliable-member fit for this curve")
        return out
    out["refit"] = {k: refit_row.get(k) for k in ("n", "n_members", "q25", "q50", "q75", "x39", "x69", "status", "usable", "reason",
                                                   "panel_tier", "rho_pressure")}
    for key in ("q25", "q50", "q75", "x39", "x69"):
        out["deltas"][key] = _delta(refit_row.get(key), shipped.get(key))
    if not refit_row.get("usable"):
        out["reasons"].append(f"the reliable-member fit is not usable ({refit_row.get('reason')}); the shipped curve stands")
        return out
    allowance = out["allowance_iqr"]
    for key in ("q25", "q75"):
        d = out["deltas"].get(key)
        if d is not None and allowance is not None and abs(d) > allowance + 1e-12:
            out["reasons"].append(f"{key} moves by {d:+.4f}, more than the ACC-04 allowance {allowance:.4f} "
                                  f"({shift:g} of the shipped IQR {out['iqr_shipped']:.4f})")
    for key in ("x39", "x69"):
        d = out["deltas"].get(key)
        if d is not None and abs(d) >= rounding - 1e-12:
            out["reasons"].append(f"the class boundary {key} moves by {d:+.4f}, at least the rounding {rounding:g}")
    out["material"] = bool(out["reasons"])
    return out


def compare_entrenchment(shipped_set: dict, fit: dict, **kw) -> dict:
    """Every shipped entrenchment curve (the slope classes and the national fallback) against
    the reliable-member fit of the same stratum; ``material`` when any curve is."""
    by_split = {r["split"]: r for r in fit["rows"]
                if r["quantity"] == "er_median" and r["level"] == "national" and r["stratum"] == "national:national"}
    curves = {}
    for key, shipped in sorted((shipped_set.get("curves") or {}).items()):
        row = fit["national_entrenchment"] if key == "national" else by_split.get(key)
        curves[key] = compare_curve(shipped, row, **kw)
    extra = sorted(k for k in by_split if k not in (shipped_set.get("curves") or {}) and by_split[k].get("usable"))
    return {"set": "entrenchment", "quantity": "er_median", "curves": curves,
            "material": any(c["material"] for c in curves.values()),
            "material_curves": sorted(k for k, c in curves.items() if c["material"]),
            "refit_only_strata": extra,
            "rule": (f"a quartile moving by more than {kw.get('shift', ACC04_SHIFT_IQR):g} of the shipped curve's "
                     f"interquartile range (the ACC-04 shift), or a class boundary (x39, x69) moving by at least "
                     f"{kw.get('rounding', RATIO_ROUNDING):g} (the rounding the ratios are measured at)")}


def evaluate_bhr(fit: dict, bands: Iterable[float] = (1.3, 1.5)) -> dict:
    """The bank-height ratio's reliable-member fits beside the shipped fixed bands: whether
    any slope-class fit is usable under the shipped rules (a right-censored panel is refused
    at the 2.0 cap) and where its class boundaries would fall against the bands."""
    bands = list(bands)
    out = {"quantity": "bhr_median", "shipped_rule": SHIPPED["bhr_median"]["rule"], "bands": bands, "fits": {},
           "any_usable": False}
    for r in fit["rows"]:
        if r["quantity"] != "bhr_median" or r["level"] != "national":
            continue
        key = r.get("split") or "national"
        entry = {k: r.get(k) for k in ("n", "n_members", "q25", "q50", "q75", "x39", "x69", "status", "usable", "reason",
                                        "rho_pressure")}
        # the bank-height ratio is lower-is-better: x69 is where Good ends, x39 where Poor begins
        entry["boundary_vs_bands"] = ({"good_below": r.get("x69"), "poor_above": r.get("x39"),
                                       "delta_good": _delta(r.get("x69"), bands[0]), "delta_poor": _delta(r.get("x39"), bands[1])}
                                      if r.get("usable") else None)
        out["fits"][key] = entry
        out["any_usable"] |= bool(r.get("usable"))
    out["finding"] = ("a usable reliable-member curve exists; compare its boundaries with the bands"
                      if out["any_usable"] else
                      "no reliable-member fit is usable under the shipped rules (the panel's upper quartile sits "
                      "at the 2.0 cap: censored); the fixed bands 1.3 and 1.5 stand")
    return out


def write_curve_sets(out: Path, *, entrenchment: Optional[dict], provenance: dict) -> Path:
    """The ``curve-sets.json`` the candidate builder reads (``--curve-sets``): the refit
    entrenchment set when material, else an empty ``sets`` with the reason in the provenance."""
    doc = {"schema": "staf-easi-candidate-curves", "schemaVersion": 1,
           "sets": {"entrenchment": entrenchment} if entrenchment else {}, "provenance": provenance}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True, default=float) + "\n", encoding="utf-8", newline="\n")
    return out


__all__ = ["ACC04_SHIFT_IQR", "RATIO_ROUNDING", "GEOMETRY_QUANTITIES", "SHIPPED", "load_quality", "reliability",
           "select_members", "fit_reliable", "assemble_entrenchment", "compare_curve", "compare_entrenchment",
           "evaluate_bhr", "write_curve_sets"]
