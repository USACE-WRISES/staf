"""The arms a study scores, as app-data folders and method packages.

A 1.0.0 or 1.1.0 study: controlled catalog substitutions of the 2026-09-15 alternatives,
using original reference fits only (``candidate_assets``). A 1.2.0 study (``build_round4``):
the base as ``alternative-1`` (the snapshot's app-data unchanged, and its own method package
zip written from the snapshot's eight method files, whose package digest must be the base's)
and one folder per built candidate package (the snapshot's app-data with the eight method
files replaced by the candidate's), each candidate refused unless its recorded base is the
study's base and its evaluator digest the running evaluator's.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import shutil
import stat

from . import ALTERNATIVES, REFERENCE_ID, REGIONAL_SETS, bases, is_round4, study_candidates
from . import round4 as r4
from .io import info, read_json, sha, write_json


def copy_candidate_file(source, target):
    """Copy bytes atomically without inheriting the snapshot's read-only flag."""
    if target.exists():
        target.chmod(target.stat().st_mode | stat.S_IWRITE)
        if sha(source) == sha(target):
            return
    temp = target.with_name(target.name + ".copy.tmp")
    shutil.copyfile(source, temp)
    os.replace(temp, target)


def write_bytes_once(target: Path, data: bytes) -> None:
    """Write ``data`` atomically, leaving an identical file (and its stamps) untouched."""
    target = Path(target)
    if target.exists():
        target.chmod(target.stat().st_mode | stat.S_IWRITE)
        if target.read_bytes() == data:
            return
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".write.tmp")
    temp.write_bytes(data)
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


def _curve_count(path: Path) -> int:
    return sum(len(v["curves"]) for v in read_json(path)["sets"].values())


def build_round4(root: Path, study: Path, manifest: dict):
    """The 1.2.0 arms: ``candidates/<id>/app-data`` and ``candidates/<id>/<id>.easi-method.zip``
    per arm, ``candidates/manifest.json`` with the arms and the candidate zips' stamps."""
    from easi import method_package as mp
    from .study import CandidateRefused, check_candidate
    base = bases.study_base(manifest)
    source = study / "snapshot/app-data"
    running = mp.evaluator_digest()
    published_zip = ((r4.addendum().get("baseline") or {}).get("method_zip_sha256"))
    rows, sources = [], []
    for entry in study_candidates(manifest):
        aid = entry["id"]
        folder = study / "candidates" / aid
        target = folder / "app-data"
        target.mkdir(parents=True, exist_ok=True)
        zip_path = folder / f"{aid}.easi-method.zip"
        if aid == REFERENCE_ID:
            for path in source.iterdir():
                if path.is_file():
                    copy_candidate_file(path, target / path.name)
            pkg = mp.package_from_dir(source, method_id="easi-screening", version=1, label=base.label)
            if pkg.digest != base.package_digest:
                raise RuntimeError(f"the snapshot's method files are package {pkg.digest[:15]}, not the base's "
                                   f"{str(base.package_digest)[:15]} ({base.id})")
            blob = mp.to_zip(pkg)
            write_bytes_once(zip_path, blob)
            row = {"id": aid, "role": "base", "label": base.label, "family": None,
                   "package_digest": pkg.digest, "method_version": pkg.identity.get("methodVersion"),
                   "evaluator_digest": pkg.identity.get("evaluatorDigest"),
                   "zip_source": "written by the candidates step from the snapshot's method files",
                   "published_zip_sha256": published_zip, "library_version": base.library_version}
        else:
            check_candidate(entry, base, running)
            try:
                found = r4.read_candidate_source(Path(entry["source"]))
            except ValueError as exc:
                raise CandidateRefused(f"{aid}: {entry['source']} changed since the snapshot recorded it ({exc})") from exc
            if found["package_digest"] != entry.get("package_digest") or found["files"] != entry.get("files"):
                raise CandidateRefused(f"{aid}: {entry['source']} changed since the snapshot recorded it")
            pkg = mp.read_package(Path(found["zip"]).read_bytes())
            for path in source.iterdir():
                if path.is_file() and path.name not in mp.METHOD_FILES:
                    copy_candidate_file(path, target / path.name)
            for name in mp.METHOD_FILES:
                write_bytes_once(target / name, pkg.files[name])
            write_bytes_once(zip_path, Path(found["zip"]).read_bytes())
            sources.append(info(Path(found["zip"])))
            row = {"id": aid, "role": "candidate", "label": entry.get("label"), "family": entry.get("family"),
                   "package_digest": pkg.digest, "method_version": entry.get("method_version"),
                   "evaluator_digest": entry.get("evaluator_digest"), "source": entry["source"],
                   "source_zip": found["zip"], "respecified": entry.get("respecified")}
        count = _curve_count(target / "reference-curves.json")
        if count != entry["curve_count"]:
            raise RuntimeError(f"Curve count differs for {aid}: {count} in app-data, {entry['curve_count']} recorded")
        row.update({"curve_count": count, "zip": str(zip_path), "zip_sha256": sha(zip_path),
                    "artifact_sha256": sha(target / "reference-curves.json"),
                    "catalog_sha256": sha(target / "screening-methods.json"),
                    "files": {name: hashlib.sha256((target / name).read_bytes()).hexdigest() for name in mp.METHOD_FILES},
                    "app_data": str(target)})
        rows.append(row)
    write_json(study / "candidates/manifest.json", {
        "candidates": rows, "alternatives": rows, "sources": sources, "base": base.record(),
        "evaluator_digest": running, "scoring_route": "EASI_METHOD_PACKAGE per arm, EASI_DATA_DIR unset",
        "addendum_sha256": r4.ADDENDUM_SHA256})
    return {"candidates": [{k: row.get(k) for k in ("id", "family", "package_digest", "curve_count", "zip_sha256")}
                           for row in rows]}


def build(root: Path, study: Path):
    import pyarrow.parquet as pq
    manifest = read_json(study / "manifest.json") if (study / "manifest.json").is_file() else {}
    if is_round4(manifest):
        return build_round4(root, study, manifest)
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
        count = _curve_count(target / "reference-curves.json")
        if count != definition["curve_count"]:
            raise RuntimeError(f"Curve count differs for {definition['id']}")
        rows.append({**definition, "artifact_sha256": sha(target / "reference-curves.json"),
                     "catalog_sha256": sha(target / "screening-methods.json")})
    write_json(study / "candidates/manifest.json", {"alternatives": rows, "original_registry_sha256": sha(original)})
    return {"alternatives": rows}
