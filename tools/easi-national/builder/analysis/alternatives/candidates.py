"""Controlled catalog substitutions, using original reference fits only."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import shutil
import stat

from . import ALTERNATIVES, REGIONAL_SETS
from .io import read_json, sha, write_json


def copy_candidate_file(source, target):
    """Copy bytes atomically without inheriting the snapshot's read-only flag."""
    if target.exists():
        target.chmod(target.stat().st_mode | stat.S_IWRITE)
        if sha(source) == sha(target):
            return
    temp = target.with_name(target.name + ".copy.tmp")
    shutil.copyfile(source, temp)
    os.replace(temp, target)


def replace_stratifiers(value, stratifier):
    if isinstance(value, dict):
        spec = value.get("curve")
        if isinstance(spec, dict) and spec.get("set") in REGIONAL_SETS:
            spec["stratifier"] = stratifier
        for child in value.values():
            replace_stratifiers(child, stratifier)
    elif isinstance(value, list):
        for child in value:
            replace_stratifiers(child, stratifier)


def candidate_assets(artifact, catalog, registry, alternative):
    from ..artifact import _curve
    artifact, catalog = deepcopy(artifact), deepcopy(catalog)
    if alternative == "alternative-2":
        for set_id in REGIONAL_SETS:
            definition = artifact["sets"][set_id]
            selected = {"national": deepcopy(definition["curves"]["national"])}
            rows = [r for r in registry if r["quantity"] == definition["quantity"] and r["level"] == "nars9"
                    and not r.get("split") and r.get("usable")]
            for row in rows:
                key = row["stratum"].split(":", 1)[1]
                if key in selected:
                    raise ValueError(f"Duplicate NARS-9 reference: {set_id}/{key}")
                selected[key] = _curve(row)
            if len(selected) != 10:
                raise ValueError(f"Expected nine usable NARS-9 fits: {set_id}")
            definition.update(stratifier="nars9", curves=selected)
        replace_stratifiers(catalog, "nars9")
    elif alternative == "alternative-3":
        for set_id in REGIONAL_SETS:
            definition = artifact["sets"][set_id]
            definition.update(stratifier="national", curves={"national": deepcopy(definition["curves"]["national"])})
        replace_stratifiers(catalog, "national")
    elif alternative == "alternative-4":
        del artifact["sets"]["corridor-woody"]["curves"]["8.2"]
    elif alternative != "alternative-1":
        raise ValueError("Unknown alternative")
    return artifact, catalog


def build(root: Path, study: Path):
    import pyarrow.parquet as pq
    source = study / "snapshot/app-data"
    artifact = read_json(source / "reference-curves.json")
    catalog = read_json(source / "screening-methods.json")
    original = root / "review/2026-09-15-regional/baseline/analysis/curves/curve_registry.parquet"
    if sha(original) != artifact["provenance"]["registry"]["sha256"]:
        raise RuntimeError("Original registry does not match frozen artifact provenance")
    registry = pq.read_table(original).to_pylist()
    rows = []
    for definition in ALTERNATIVES:
        target = study / "candidates" / definition["id"] / "app-data"
        target.mkdir(parents=True, exist_ok=True)
        substituted = definition["id"] != "alternative-1"
        for path in source.iterdir():
            if path.is_file() and not (substituted and path.name in ("reference-curves.json", "screening-methods.json")):
                copy_candidate_file(path, target / path.name)
        if substituted:
            curves, methods = candidate_assets(artifact, catalog, registry, definition["id"])
            write_json(target / "reference-curves.json", curves)
            write_json(target / "screening-methods.json", methods)
        count = sum(len(v["curves"]) for v in read_json(target / "reference-curves.json")["sets"].values())
        if count != definition["curve_count"]:
            raise RuntimeError(f"Curve count differs for {definition['id']}")
        rows.append({**definition, "artifact_sha256": sha(target / "reference-curves.json"),
                     "catalog_sha256": sha(target / "screening-methods.json")})
    write_json(study / "candidates/manifest.json", {"alternatives": rows, "original_registry_sha256": sha(original)})
    return {"alternatives": rows}
