"""Targeted woody 8.2 versus national reference uncertainty on one population.

Both reference panels are resampled by HUC12. Every fitted point is rounded to
the shipped six-place precision and rated by EASI's actual interpolation and
inclusive index edges. Existing broad diagnostic bootstrap outputs are not
modified or substituted for this population-specific experiment.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np

from .io import info, now, read_json, safe_study, write_json

SEED = 7
KEYS = ("8.2", "national")


def runtime_classes(points, values):
    """Use the live scalar evaluator once per unique finite physical value."""
    from easi.screening_methods import interp_curve
    values = np.asarray(values, dtype=float)
    result = np.full(values.shape, -1, dtype=np.int8)
    finite = np.isfinite(values)
    unique, inverse = np.unique(values[finite], return_inverse=True)
    scores = [interp_curve(points, float(value)) for value in unique]
    labels = np.asarray([-1 if score is None else 2 if score >= 0.69 else 1 if score >= 0.39 else 0
                         for score in scores], dtype=np.int8)
    result[finite] = labels[inverse]
    return result


def _rounded_fit(values):
    from ..artifact import _rounded
    from ..curves import QUANTITIES, fit_curve
    return _rounded(fit_curve(values, QUANTITIES["woody_wsrp100"], "targeted-woody-stability"))


def fit_linkage(values, frozen):
    """Prove the supplied original observations reproduce the shipped fit."""
    fit = _rounded_fit(values)
    fields = ("n", "q25", "q50", "q75", "x39", "x69", "points", "status")
    mismatches = {key: {"frozen": frozen.get(key), "refit": fit.get(key)}
                  for key in fields if frozen.get(key) != fit.get(key)}
    return {"status": "canonical_match" if not mismatches else "mismatch",
            "precision": "six_decimal_export", "compared_fields": list(fields), "mismatches": mismatches}


def bootstrap(values, clusters, population, frozen, *, n_boot=200, seed=SEED):
    """Reference uncertainty; both calls receive the identical 8.2 population."""
    from ..curves import crossings
    from ..stability import verdict
    if not isinstance(n_boot, int) or isinstance(n_boot, bool) or n_boot < 1:
        raise ValueError("n_boot must be a positive integer")
    values, population = np.asarray(values, dtype=float), np.asarray(population, dtype=float)
    clusters = np.asarray(clusters, dtype=str)
    if values.ndim != 1 or len(values) != len(clusters) or not len(values):
        raise ValueError("Reference observations and cluster labels must align")
    baseline = runtime_classes(frozen["points"], population)
    rated = baseline >= 0
    if not rated.any():
        raise ValueError("The 8.2 population has no finite rateable woody values")
    labels, inverse = np.unique(clusters, return_inverse=True)
    groups = [np.flatnonzero(inverse == i) for i in range(len(labels))]
    rng = np.random.default_rng(seed)
    draws, invalid = [], Counter()
    for draw in range(n_boot):
        picked = rng.integers(0, len(groups), size=len(groups))
        sample = values[np.concatenate([groups[i] for i in picked])]
        sample = sample[np.isfinite(sample)]
        if len(sample) < 5:
            invalid["fewer_than_five_finite_values"] += 1
            continue
        fit = _rounded_fit(sample)
        if fit.get("status") != "complete":
            invalid[str(fit.get("status"))] += 1
            continue
        points = fit["points"]
        # Crossings describe the actual rounded bootstrap curve, not unrounded seed coordinates.
        records = [{"x": x, "y": y} for x, y in points]
        lo, hi = crossings(records, 0.39), crossings(records, 0.69)
        if len(lo) != 1 or len(hi) != 1:
            invalid["missing_unique_crossing"] += 1
            continue
        current = runtime_classes(points, population)
        if not (current[rated] >= 0).all():
            invalid["unrateable_population"] += 1
            continue
        draws.append({"draw": draw, "flip": float(np.mean(current[rated] != baseline[rated])),
                      "x39": float(lo[0]), "x69": float(hi[0])})
    def interval(key):
        return list(map(float, np.quantile([row[key] for row in draws], [0.025, 0.975]))) if draws else [None, None]
    mean = float(np.mean([r["flip"] for r in draws])) if draws else None
    lo39, hi39 = interval("x39")
    lo69, hi69 = interval("x69")
    return {"n_members": int(np.isfinite(values).sum()), "n_panel_members": len(values),
            "n_clusters": len(labels), "n_population": int(rated.sum()), "n_population_total": len(population),
            "n_valid": len(draws), "n_requested": n_boot, "invalid_draws": dict(invalid), "seed": seed,
            "mean_flip": mean, "p90_flip": float(np.quantile([r["flip"] for r in draws], .90)) if draws else None,
            "x39_lo": lo39, "x39_hi": hi39, "x69_lo": lo69, "x69_hi": hi69,
            "verdict": verdict(mean), "draws": draws}


def transitions(regional, national):
    regional, national = np.asarray(regional), np.asarray(national)
    if regional.shape != national.shape:
        raise ValueError("Paired population arrays differ")
    labels = {-1: "Not rated", 0: "Poor", 1: "Fair", 2: "Good"}
    rows = []
    total = len(regional)
    for before in (-1, 0, 1, 2):
        for after in (-1, 0, 1, 2):
            n = int(np.sum((regional == before) & (national == after)))
            if n:
                rows.append({"from": labels[before], "to": labels[after], "n": n, "share": n / total if total else None})
    return {"label": "Woody 8.2 regional versus national on the full stored 8.2 population",
            "design": "full-l2-8.2", "weighted": False, "sample_n": total, "population_n": total,
            "changed_n": int(np.sum(regional != national)),
            "changed_share": float(np.mean(regional != national)) if total else None, "rows": rows}


def _stamp(path):
    stat = path.stat()
    return {"path": str(path.resolve()), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _observations_digest(comids, values, clusters=None):
    digest = hashlib.sha256()
    for comid, value, cluster in zip(comids, values, clusters if clusters is not None else [None] * len(comids)):
        digest.update(json.dumps([int(comid), float(value) if np.isfinite(value) else None, cluster],
                                 separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()


def run(root: Path, study: Path, n_boot=200) -> dict:
    """Write only study/woody-stability; source inputs are checked unchanged."""
    import pyarrow.parquet as pq
    from ..artifact import ENGINE_PATH, _curve
    from easi import screening_methods
    root, study = Path(root).resolve(), safe_study(root, study)
    folder = (study / "woody-stability").resolve()
    if not folder.is_relative_to(study):
        raise ValueError("Woody stability output escapes study")
    artifact_path = study / "snapshot/app-data/reference-curves.json"
    artifact = read_json(artifact_path)
    original = root / "review/2026-09-15-regional/baseline/analysis"
    registry_path = original / "curves/curve_registry.parquet"
    members_path = original / "panels/panel_members.parquet"
    original_landscape = original / "landscape.parquet"
    # Snapshots may reference large current files; use original evidence when preserved.
    if not original_landscape.is_file():
        original_landscape = root / "analysis/landscape.parquet"
    population_path = root / "analysis/landscape.parquet"
    protected_paths = {"artifact": artifact_path, "registry": registry_path, "members": members_path,
                       "bootstrap_source": Path(__file__), "curve_engine": ENGINE_PATH,
                       "easi_evaluator": Path(screening_methods.__file__)}
    protected = {key: info(path) for key, path in protected_paths.items()}
    if protected["registry"]["sha256"] != artifact["provenance"]["registry"]["sha256"]:
        raise ValueError("Original registry does not match frozen artifact provenance")
    original_members_sha = (artifact["provenance"].get("entrenchmentNationalFallback") or {}).get("panelMembersSha256")
    if original_members_sha and protected["members"]["sha256"] != original_members_sha:
        raise ValueError("Original panel membership does not match frozen artifact provenance")
    expected_engine = artifact["provenance"].get("curveEngineSha256")
    if expected_engine and protected["curve_engine"]["sha256"] != expected_engine:
        raise ValueError("Curve engine differs from the frozen fit engine")
    stamps = {str(p): _stamp(p) for p in {original_landscape, population_path}}
    settings = {"n_boot": n_boot, "seed": SEED, "population": "all_stored_l2_8.2",
                "classification": "live_interp_six_decimal_points_inclusive_.39_.69", "version": 1}
    signature = hashlib.sha256(json.dumps({"inputs": protected, "landscape": stamps, "settings": settings},
                                         sort_keys=True).encode()).hexdigest()
    result_path = folder / "result.json"
    if result_path.is_file():
        saved = read_json(result_path)
        if saved.get("input_digest") != signature:
            raise RuntimeError("Existing woody stability belongs to different inputs; use a new study")
        return saved
    registry = pq.read_table(registry_path, filters=[("quantity", "=", "woody_wsrp100")]).to_pylist()
    frozen = artifact["sets"]["corridor-woody"]["curves"]
    # Read only this quantity and identities. No full values/scores table is required.
    reference = pq.read_table(original_landscape, columns=["comid", "woody_wsrp100"]).to_pandas().set_index("comid")
    if not reference.index.is_unique:
        raise ValueError("Reference landscape has duplicate COMIDs")
    population = pq.read_table(population_path, columns=["comid", "woody_wsrp100"], filters=[("l2", "=", "8.2")]).to_pandas()
    population = population.sort_values("comid")
    if population.empty or not population["comid"].is_unique:
        raise ValueError("Full stored 8.2 population must be nonempty and unique")
    pop_values = population["woody_wsrp100"].to_numpy(dtype=float)
    rows, linkage = [], {}
    for key in KEYS:
        level, stratum = ("l2", "l2:8.2") if key == "8.2" else ("national", "national:national")
        selected = [r for r in registry if r["level"] == level and r["stratum"] == stratum and not r.get("split")]
        if len(selected) != 1 or not selected[0].get("usable") or _curve(selected[0]) != frozen[key]:
            raise ValueError(f"Original registry does not exactly export frozen woody {key}")
        members = pq.read_table(members_path, columns=["comid", "huc12"], filters=[("level", "=", level), ("stratum", "=", stratum)]).to_pandas()
        members = members.sort_values("comid")
        if not members["comid"].is_unique or len(members) != frozen[key]["nMembers"]:
            raise ValueError(f"Original panel membership mismatch for {key}")
        ids = members["comid"].to_numpy()
        if not np.isin(ids, reference.index.to_numpy()).all():
            raise ValueError("Reference panel COMIDs missing from source landscape")
        values = reference["woody_wsrp100"].reindex(ids).to_numpy(dtype=float)
        clusters = members["huc12"].fillna("").astype(str).to_numpy()
        linked = fit_linkage(values, frozen[key])
        if linked["status"] != "canonical_match":
            raise ValueError(f"Original observations do not reproduce the frozen woody {key} fit: {linked['mismatches']}")
        linkage[key] = {**linked, "observations_sha256": _observations_digest(ids, values, clusters)}
        result = bootstrap(values, clusters, pop_values, frozen[key], n_boot=n_boot)
        rows.append({"set_id": "corridor-woody", "key": key, "fit_linkage": "canonical_match",
                     "reference_level": level, "reference_stratum": stratum, **result})
    comparison = transitions(runtime_classes(frozen["8.2"]["points"], pop_values), runtime_classes(frozen["national"]["points"], pop_values))
    if protected != {key: info(path) for key, path in protected_paths.items()}:
        raise RuntimeError("Protected woody reference inputs changed during the run")
    if stamps != {str(p): _stamp(p) for p in {original_landscape, population_path}}:
        raise RuntimeError("Landscape source changed during the run")
    result = {"schema_version": 1, "status": "complete", "created_at": now(), "input_digest": signature,
              "settings": settings, "provenance": {"protected_inputs": protected, "landscape_stamps": stamps,
                  "population_observations_sha256": _observations_digest(population["comid"], pop_values),
                  "population_l2": "8.2", "population_scope": "full_stored_landscape_not_scored_or_sample_subset",
                  "linkage": linkage}, "curves": rows, "comparison": comparison, "limits": [
                  "Reference-sampling uncertainty, not independent ecological validation.",
                  "Both curves are evaluated on the identical full stored 8.2 population, including explicit missing values.",
                  "Each panel uses 200 requested HUC12 draws by default; intervals condition on valid draws only.",
                  "National and regional draws are separate panel resamples, not paired uncertainty in their difference.",
                  "Curve classes use actual rounded-point interpolation and inclusive index edges; old diagnostic crossing-only flip rates differ.",
                  "This study does not change frozen criteria, source evidence, current scores, analysis or staging."]}
    write_json(result_path, result)
    return result
