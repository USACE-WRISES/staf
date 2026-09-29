"""P4 stability for a refitted candidate family (a 1.2.0 study).

For each candidate set that replaces a base set (E2: ``flow-min-ratio`` for
``flow-variability``; E4: ``width-variability`` for ``corridor-woody`` as habitat provision
reads it), the class-flip share of the family's ratings over 200 HUC12 reference-panel
resamples (seed 7) on the stored sample's actual values: the base set and the candidate set
each on its own population of finite values (the candidate's on the reaches it rates, the
perennial ones for E2), curve by curve (each NARS-9 stratum's panel resampled and refitted,
the national fallback on the reaches without a regional curve), pooled per draw over the set,
and reported side by side with the addendum's margin: the candidate's flip share may not
exceed the base's by more than 0.02. ``woody_stability.bootstrap`` and ``transitions`` do
the work; ``woody_stability.run`` stays the legacy studies' stage. A 1.2.0 study with no
refitted family records ``not applicable``.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .. import curves as curve_module
from . import REFERENCE_ID, study_candidates
from . import round4 as r4
from . import spatial, woody_stability
from .io import info, now, read_json, safe_study, write_json

SEED = woody_stability.SEED
#: candidate set -> (the base set it replaces, the function that reads it)
REPLACEMENTS = {"flow-min-ratio": ("flow-variability", "low_flow_baseflow_dynamics"),
                "width-variability": ("corridor-woody", "habitat_provision")}
ORIGINAL = spatial.ORIGINAL
NOT_APPLICABLE = "not applicable"


def _stamp(path):
    stat = Path(path).stat()
    return {"path": str(Path(path).resolve()), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def replaced_sets(base_artifact: dict, candidate_artifact: dict) -> list[dict]:
    """The candidate's sets that replace a base set (``REPLACEMENTS``), with the base set."""
    out = []
    for set_id, (base_set, function) in REPLACEMENTS.items():
        if set_id in (candidate_artifact.get("sets") or {}) and set_id not in (base_artifact.get("sets") or {}):
            if base_set not in (base_artifact.get("sets") or {}):
                raise ValueError(f"the base has no {base_set} set for {set_id} to replace")
            out.append({"candidate_set": set_id, "base_set": base_set, "function": function})
    return out


def curve_for(definition: dict, region) -> tuple[str, dict] | tuple[None, None]:
    """The curve a reach of ``region`` is rated by: its stratum's, else the national fallback."""
    curves = definition.get("curves") or {}
    key = str(region) if region is not None else None
    if definition.get("stratifier") == "nars9" and key in curves:
        return key, curves[key]
    if "national" in curves:
        return "national", curves["national"]
    return None, None


def rate(definition: dict, values: np.ndarray, regions) -> np.ndarray:
    """The frozen classes (-1 not rated, 0 Poor, 1 Fair, 2 Good) of a population under a set."""
    values = np.asarray(values, dtype=float)
    out = np.full(len(values), -1, dtype=np.int8)
    regions = np.asarray([None if r is None else str(r) for r in regions], dtype=object)
    for region in set(regions.tolist()):
        key, curve = curve_for(definition, region)
        if curve is None:
            continue
        mask = regions == region
        out[mask] = woody_stability.runtime_classes(curve["points"], values[mask])
    return out


def pool_draws(per_curve: list[dict]) -> dict:
    """The set's flip share per draw: the curves' flip shares weighted by their rated
    population, over the draw ids every curve rated (a draw invalid for one curve is left
    out of the pooled distribution and counted)."""
    if not per_curve:
        return {"mean_flip": None, "p90_flip": None, "n_valid": 0, "n_population": 0, "draws_dropped": 0}
    total = sum(c["n_population"] for c in per_curve)
    common = None
    for c in per_curve:
        ids = {d["draw"] for d in c["draws"]}
        common = ids if common is None else common & ids
    common = sorted(common or [])
    pooled = []
    for draw in common:
        flips = 0.0
        for c in per_curve:
            flip = next(d["flip"] for d in c["draws"] if d["draw"] == draw)
            flips += flip * c["n_population"]
        pooled.append(flips / total if total else np.nan)
    pooled = np.asarray(pooled, dtype=float)
    pooled = pooled[np.isfinite(pooled)]
    requested = max((c["n_requested"] for c in per_curve), default=0)
    return {"mean_flip": float(pooled.mean()) if pooled.size else None,
            "p90_flip": float(np.quantile(pooled, .9)) if pooled.size else None,
            "n_valid": int(pooled.size), "n_requested": requested,
            "draws_dropped": int(requested - pooled.size), "n_population": int(total)}


def stability_of_set(definition: dict, quantity: str, panel: pd.DataFrame, population: pd.DataFrame,
                     *, n_boot: int, split=None) -> dict:
    """One set's stability: per curve the panel (the stratum's original members, restricted
    to the split value when the set records one) resampled by HUC12 and the population (the
    stored sample's reaches rated by that curve, finite values only) re-rated; pooled over the
    set. ``panel`` carries level, stratum, huc12, the quantity column and the split column;
    ``population`` carries comid, nars9, the quantity column and the split column."""
    per_curve, linkage = [], {}
    curves = definition.get("curves") or {}
    regions = population["nars9"].astype(object).where(population["nars9"].notna(), None)
    served = np.asarray([curve_for(definition, r)[0] for r in regions], dtype=object)
    for key in sorted(curves):
        level, stratum = ("national", "national:national") if key == "national" else ("nars9", f"nars9:{key}")
        members = panel[(panel["level"] == level) & (panel["stratum"] == stratum)]
        if split:
            column, value = split
            members = members[members[column].astype(str) == str(value)]
        members = members.sort_values("comid")
        if members.empty:
            per_curve.append({"key": key, "status": "no panel members", "n_population": 0})
            continue
        values = members[quantity].to_numpy(dtype=float)
        clusters = members["huc12"].fillna("").astype(str).to_numpy()
        finite = values[np.isfinite(values)]
        linked = woody_stability.fit_linkage(finite, curves[key], quantity) if finite.size >= 5 else {"status": "too few values"}
        linkage[key] = {"status": linked["status"], "mismatches": linked.get("mismatches"),
                        "n_members": int(len(members)), "n_finite": int(finite.size),
                        "frozen_n_members": curves[key].get("nMembers"), "frozen_n": curves[key].get("n")}
        pop = population[served == key]
        pop_values = pop[quantity].to_numpy(dtype=float)
        if not np.isfinite(pop_values).any():
            per_curve.append({"key": key, "status": "no rated population", "n_population": 0,
                              "fit_linkage": linked["status"]})
            continue
        result = woody_stability.bootstrap(values, clusters, pop_values, curves[key], n_boot=n_boot, seed=SEED,
                                           quantity=quantity)
        per_curve.append({"key": key, "status": "complete", "reference_level": level, "reference_stratum": stratum,
                          "fit_linkage": linked["status"], **result})
    rated = [c for c in per_curve if c.get("status") == "complete"]
    pooled = pool_draws(rated)
    classes = rate(definition, population[quantity].to_numpy(dtype=float), regions)
    return {"quantity": quantity, "split": dict([split]) if split else None,
            "curves": [{k: v for k, v in c.items() if k != "draws"} for c in per_curve],
            "linkage": linkage, "pooled": pooled,
            "population": {"n": int(len(population)), "n_finite": int(np.isfinite(population[quantity].to_numpy(dtype=float)).sum()),
                           "n_rated": int((classes >= 0).sum()),
                           "classes": dict(Counter({-1: "Not rated", 0: "Poor", 1: "Fair", 2: "Good"}[int(c)] for c in classes))},
            "classes": classes}


def run(root: Path, study: Path, n_boot=200) -> dict:
    """Write only study/set-stability; source inputs are checked unchanged."""
    from ..artifact import ENGINE_PATH
    from easi import screening_methods
    root, study = Path(root).resolve(), safe_study(root, study)
    folder = (study / "set-stability").resolve()
    if not folder.is_relative_to(study):
        raise ValueError("Set stability output escapes study")
    manifest = read_json(study / "manifest.json")
    arms = study_candidates(manifest)
    base_path = study / "candidates" / REFERENCE_ID / "app-data" / "reference-curves.json"
    base_artifact = read_json(base_path)
    original = root / ORIGINAL
    registry_path = original / "curves/curve_registry.parquet"
    members_path = original / "panels/panel_members.parquet"
    reaches_path = study / "cohorts/reaches.parquet"
    protected_paths = {"base_artifact": base_path, "registry": registry_path, "members": members_path,
                       "bootstrap_source": Path(__file__), "woody_source": Path(woody_stability.__file__),
                       "curve_engine": ENGINE_PATH, "easi_evaluator": Path(screening_methods.__file__)}
    for arm in arms:
        if arm["id"] != REFERENCE_ID:
            protected_paths[f"artifact:{arm['id']}"] = study / "candidates" / arm["id"] / "app-data" / "reference-curves.json"
    protected = {key: info(path) for key, path in protected_paths.items()}
    if protected["registry"]["sha256"] != base_artifact["provenance"]["registry"]["sha256"]:
        raise ValueError("Original registry does not match the base artifact's provenance")
    population_sources = {"landscape": root / "analysis/landscape.parquet", "values": root / "analysis/values.parquet",
                          "reaches": reaches_path}
    stamps = {name: _stamp(path) for name, path in population_sources.items() if path.is_file()}
    settings = {"n_boot": n_boot, "seed": SEED, "population": "stored sample (cohorts/reaches.parquet, sample)",
                "classification": "live_interp_six_decimal_points_inclusive_.39_.69", "margin_flip_rise": r4.MARGINS["p4_flip_rise"],
                "version": 1}
    signature = hashlib.sha256(json.dumps({"inputs": protected, "population": stamps, "settings": settings},
                                         sort_keys=True).encode()).hexdigest()
    result_path = folder / "result.json"
    if result_path.is_file():
        saved = read_json(result_path)
        if saved.get("input_digest") != signature:
            raise RuntimeError("Existing set stability belongs to different inputs; use a new study")
        return saved
    families = []
    not_applicable = []
    plan = []
    for arm in arms:
        if arm["id"] == REFERENCE_ID:
            continue
        candidate_artifact = read_json(study / "candidates" / arm["id"] / "app-data" / "reference-curves.json")
        pairs = replaced_sets(base_artifact, candidate_artifact)
        if not pairs:
            not_applicable.append(arm["id"])
        for pair in pairs:
            plan.append((arm, candidate_artifact, pair))
    if plan:
        quantities = sorted({base_artifact["sets"][p["base_set"]]["quantity"] for _, _, p in plan}
                            | {art["sets"][p["candidate_set"]]["quantity"] for _, art, p in plan})
        members = pq.read_table(members_path, filters=[("level", "in", ["nars9", "national"])]).to_pandas()
        panel, reference_inputs = spatial._read_reference(original, members, quantities)
        reaches = pq.read_table(reaches_path, columns=["comid", "nars9", "sample", "sample_weight"]).to_pandas()
        sample = reaches[reaches["sample"].astype(bool)].copy()
        ids = sorted(int(c) for c in sample.comid)
        population = sample[["comid", "nars9", "sample_weight"]].copy().set_index("comid")
        for source_name, path in (("landscape", population_sources["landscape"]), ("values", population_sources["values"])):
            wanted = [q for q in quantities if curve_module.QUANTITIES[q].source == source_name]
            if not wanted and source_name == "values":
                continue
            names = pq.read_schema(path).names
            columns = ["comid"] + [curve_module.QUANTITIES[q].column for q in wanted if curve_module.QUANTITIES[q].column in names]
            if source_name == "landscape" and "fcode_class" in names:
                columns.append("fcode_class")
            table = pq.read_table(path, columns=columns, filters=[("comid", "in", ids)]).to_pandas().set_index("comid")
            table = table[~table.index.duplicated(keep="first")]
            for q in wanted:
                column = curve_module.QUANTITIES[q].column
                population[q] = table[column].reindex(population.index).to_numpy(dtype=float) if column in table else np.nan
            if "fcode_class" in table:
                population["fcode_class"] = table["fcode_class"].reindex(population.index)
        population = population.reset_index()
        for arm, candidate_artifact, pair in plan:
            base_def = base_artifact["sets"][pair["base_set"]]
            cand_def = candidate_artifact["sets"][pair["candidate_set"]]
            split = None
            if cand_def.get("split"):
                (column, value), = cand_def["split"].items()
                split = (column, value)
            cand_population = population
            if split:
                if split[0] not in population.columns:
                    raise ValueError(f"the population carries no {split[0]!r} column for {pair['candidate_set']}")
                cand_population = population[population[split[0]].astype(str) == str(split[1])]
            base = stability_of_set(base_def, base_def["quantity"], panel, population, n_boot=n_boot)
            cand = stability_of_set(cand_def, cand_def["quantity"], panel, cand_population, n_boot=n_boot, split=split)
            base_flip, cand_flip = base["pooled"]["mean_flip"], cand["pooled"]["mean_flip"]
            difference = (cand_flip - base_flip) if base_flip is not None and cand_flip is not None else None
            base_classes = base.pop("classes")
            cand_classes = cand.pop("classes")
            # the transition table pairs the base's classes with the candidate's on the reaches
            # the candidate rates (its own population)
            lookup = dict(zip(population.comid.tolist(), base_classes.tolist()))
            paired = np.asarray([lookup[int(c)] for c in cand_population.comid], dtype=np.int8)
            comparison = woody_stability.transitions(paired, cand_classes)
            comparison["label"] = (f"{pair['base_set']} (base) versus {pair['candidate_set']} ({arm['id']}) on the "
                                   f"stored sample's reaches the candidate rates")
            comparison["design"] = "stored-sample"
            families.append({"alternative": arm["id"], "family": arm.get("family"), "function": pair["function"],
                             "base_set": pair["base_set"], "candidate_set": pair["candidate_set"],
                             "base": base, "candidate": cand,
                             "base_flip": base_flip, "candidate_flip": cand_flip, "difference": difference,
                             "margin": r4.MARGINS["p4_flip_rise"],
                             "within_margin": (difference is not None and difference <= r4.MARGINS["p4_flip_rise"]),
                             "comparison": comparison})
        stamps_after = {name: _stamp(path) for name, path in population_sources.items() if path.is_file()}
        if stamps_after != stamps:
            raise RuntimeError("Population source changed during the run")
    else:
        reference_inputs = []
    if protected != {key: info(path) for key, path in protected_paths.items()}:
        raise RuntimeError("Protected stability inputs changed during the run")
    result = {"schema_version": 1, "status": "complete", "created_at": now(), "input_digest": signature,
              "applicable": bool(plan), "not_applicable": not_applicable,
              "note": None if plan else f"{NOT_APPLICABLE}: no candidate replaces a base set",
              "settings": settings, "provenance": {"protected_inputs": protected, "population_stamps": stamps,
                                                   "reference_inputs": reference_inputs},
              "families": families, "limits": [
                  "Reference-sampling uncertainty of a refitted family, not independent ecological validation.",
                  "The base set and the candidate set are each rated on their own population of finite stored-sample values; the candidate's population is the reaches it rates (the perennial ones under Addendum 1).",
                  "Each curve's panel is resampled by HUC12 with 200 requested draws (seed 7); the set's flip share is the curves' shares pooled per draw by rated population.",
                  "Curve classes use actual rounded-point interpolation and inclusive index edges.",
                  "The margin is the addendum's operating choice: the candidate's flip share may not exceed the base's by more than 0.02.",
                  "This study does not change frozen criteria, source evidence, current scores, analysis or staging."]}
    write_json(result_path, result)
    return result
