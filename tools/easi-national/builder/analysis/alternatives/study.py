"""Snapshot, checkpoints and CLI orchestration for isolated alternatives."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from builder import REPO_ROOT
from . import ALTERNATIVES, BASE_COMMIT, BASE_METHOD, BASE_REFERENCE, STUDY_VERSION
from .io import fingerprint, info, now, output_files, read_json, safe_study, sha, write_json

STEPS = ("snapshot", "candidates", "observations", "evidence", "scores", "acquisition", "field", "spatial", "stability", "report")
DEPENDENCIES = {"snapshot": [], "candidates": ["snapshot"], "observations": ["snapshot"],
                "evidence": ["observations"], "scores": ["candidates", "evidence"],
                "acquisition": ["observations"], "field": ["scores", "acquisition"],
                "spatial": ["scores", "acquisition"], "stability": ["candidates"], "report": ["field", "spatial", "stability"]}


def protocol():
    return {"version": STUDY_VERSION, "sample_n": 100000, "sample_seed": 17,
            "sample_strata": ["state", "l2"], "spatial_folds": 5,
            "fold_assignment": "SHA256('easi-field-fold-v1:' + smallest connected HUC8), first eight bytes as big-endian integer modulo five",
            "paired_bootstrap_draws": 1000, "reference_bootstrap_draws": 200,
            "reference_bootstrap_seed": 7, "auc_noninferiority_margin": 0.01,
            "interval": 0.95, "reference": "alternative-1", "scoring": "banded",
            "rating_index": {"Good": 0.85, "Fair": 0.545, "Poor": 0.195},
            "field_definition": "same-visit-v2", "temporal_evaluation": "retrospective 2023-24",
            "national_rebuild": False, "publication": False,
            "selection": "Both paired AUC lower bounds >= -0.01, unchanged rating availability, regional/function review; fewer curves then benthic AUC; otherwise Alternative 1"}


def source_inputs(root):
    names = ["analysis/values_meta.json", "analysis/values.parquet", "analysis/nrsa/nrsa_desktop.parquet",
             "analysis/nrsa/nrsa_frame.parquet", "analysis/nrsa/nrsa_targets.parquet", "analysis/strata.parquet",
             "analysis/local-review/completion.json", "staging/manifest.json",
             "review/2026-09-15-regional/baseline/analysis/curves/curve_registry.parquet",
             "review/2026-09-15-regional/baseline/analysis/panels/panel_members.parquet",
             "review/2026-09-15-regional/baseline/analysis/panels/reference_panels.parquet"]
    return [root / name for name in names if (root / name).is_file()]


def snapshot(root: Path, study: Path):
    from easi import config
    from easi.national import method_version
    if config.criteria_set() != "regional" or method_version() != BASE_METHOD:
        raise RuntimeError("Alternative 1 must match the preserved regional scoring method")
    artifact = REPO_ROOT / "apps/easi/data/reference-curves.json"
    if sha(artifact) != BASE_REFERENCE:
        raise RuntimeError("Alternative 1 frozen artifact has changed")
    completion = read_json(root / "analysis/local-review/completion.json")
    if completion.get("status") != "complete" or completion.get("method_version") != BASE_METHOD:
        raise RuntimeError("Current Alternative 1 completion is unavailable")
    if read_json(root / "state/queue.json").get("items"):
        raise RuntimeError("Publication queue must remain empty")
    study.mkdir(parents=True, exist_ok=True)
    marker = study / "snapshot/manifest.json"
    if marker.is_file():
        saved = read_json(marker)
        for row in saved["files"]:
            target = study / row["relative_path"]
            if not target.is_file() or sha(target) != row["sha256"]:
                raise RuntimeError(f"Preserved snapshot changed: {target}")
        current = read_json(study / "manifest.json")
        if current.get("protocol") != protocol():
            current["protocol"] = protocol()
            current["status"] = "pending"
            current["input_digest"] = hashlib.sha256(json.dumps(
                {"inputs": current["input_files"], "protocol": protocol()}, sort_keys=True).encode()).hexdigest()
            write_json(study / "protocol.json", protocol())
            write_json(study / "manifest.json", current)
        return {"files": len(saved["files"]), "reused": True}
    copies = [(root / "analysis", "snapshot/analysis"), (root / "staging", "snapshot/staging"),
              (REPO_ROOT / "apps/easi/data", "snapshot/app-data"),
              (REPO_ROOT / "apps/easi/easi", "snapshot/engine")]
    entries = []
    for source, destination in copies:
        for src in sorted(source.rglob("*")):
            if not src.is_file() or "__pycache__" in src.parts or ".pytest_cache" in src.parts or src.name.endswith(".tmp"):
                continue
            if destination.endswith("engine") and src.suffix != ".py":
                continue
            target = study / destination / src.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            original = info(src)
            if not target.is_file() or sha(target) != original["sha256"]:
                temp = target.with_name(target.name + ".tmp")
                shutil.copy2(src, temp)
                if sha(temp) != original["sha256"]:
                    raise RuntimeError(f"Snapshot copy mismatch: {src}")
                os.replace(temp, target)
            if src.stat().st_mtime_ns != original["mtime_ns"]:
                raise RuntimeError(f"Source changed while preserving: {src}")
            entries.append({**original, "relative_path": target.relative_to(study).as_posix()})
        print(f"Preserved {destination}: {len(entries):,} cumulative files", flush=True)
    upstream = []
    for path in source_inputs(root):
        row = info(path)
        upstream.append({"path": path.relative_to(root).as_posix(), "size": row["bytes"],
                         "mtime_ns": row["mtime_ns"], "sha256": row["sha256"]})
    binding = {"completion_sha256": sha(root / "analysis/local-review/completion.json"),
               "method_version": BASE_METHOD, "frozen_sha256": BASE_REFERENCE, "source_commit": BASE_COMMIT}
    data = {"schema_version": 1, "study_id": study.name, "status": "pending", "created_at": now(),
            "alternative_1": {"method_version": BASE_METHOD, "source_commit": BASE_COMMIT, "frozen_sha": BASE_REFERENCE},
            "parent_binding": binding, "alternatives": ALTERNATIVES, "protocol": protocol(),
            "input_files": upstream, "study_only": True}
    data["input_digest"] = hashlib.sha256(json.dumps({"inputs": upstream, "protocol": protocol()}, sort_keys=True).encode()).hexdigest()
    write_json(study / "manifest.json", data)
    write_json(study / "protocol.json", protocol())
    write_json(marker, {"status": "preserved", "created_at": now(), "source_commit": BASE_COMMIT,
                        "files": entries, "bytes": sum(row["bytes"] for row in entries)})
    return {"files": len(entries), "bytes": sum(row["bytes"] for row in entries)}


def check_inputs(root, study):
    manifest = read_json(study / "manifest.json")
    for row in manifest["input_files"]:
        path = root / row["path"]
        stat = path.stat()
        if stat.st_size != row["size"] or stat.st_mtime_ns != row["mtime_ns"]:
            raise RuntimeError(f"Study input changed; create a new study: {path}")
    return manifest


def stage_sources(step):
    package = Path(__file__).parent
    files = {"snapshot": ["study.py", "io.py", "__init__.py"], "candidates": ["candidates.py"],
             "observations": ["field_evaluation.py"], "evidence": ["evidence.py"], "scores": ["scorer.py"],
             "acquisition": ["acquisition.py", "gage_diagnostics.py"], "field": ["field_evaluation.py", "bootstrap_metrics.py"],
             "spatial": ["spatial.py", "bootstrap_metrics.py"], "stability": ["woody_stability.py"], "report": ["report.py"]}
    paths = [package / name for name in sorted(set([*files[step], "io.py", "__init__.py"]))]
    analysis = package.parent
    shared = {"candidates": ["artifact.py", "curves.py"], "observations": ["nrsa.py", "validation.py", "stats.py"],
              "evidence": ["nrsa.py", "values.py"], "field": ["validation.py", "stats.py"],
              "spatial": ["artifact.py", "curves.py", "panels.py", "screens.py", "stats.py"],
              "stability": ["artifact.py", "curves.py", "panels.py", "screens.py", "stats.py"],
              "acquisition": ["stats.py"]}
    paths += [analysis / name for name in shared.get(step, [])]
    if step in ("snapshot", "evidence", "scores", "field", "spatial", "stability"):
        paths += sorted((REPO_ROOT / "apps/easi/easi").rglob("*.py"))
    if step in ("candidates", "spatial", "stability"):
        paths += [REPO_ROOT / "apps/stream-curves/streamcurves/curves.py"]
    if step == "evidence":
        paths += sorted((analysis.parent / "stages").glob("*.py"))
        paths += [analysis.parent / name for name in ("config.py", "units.py", "paths.py")]
    if step == "spatial":
        paths += [package / name for name in ("scorer.py", "candidates.py", "field_evaluation.py")]
    if step == "observations":
        from builder.analysis.nrsa import NRSA_DIR, NRSA_RAW, RAW_FILES, SITE_FILE_1314
        paths += [NRSA_DIR / name for name in ("site_visits.parquet", "values.parquet", "stations.parquet")]
        paths += [NRSA_RAW / name for relatives in RAW_FILES.values() for name in relatives]
        paths += [NRSA_RAW / SITE_FILE_1314]
    return paths


def _digest(study, step, manifest):
    markers = [study / "stages" / f"{name}.json" for name in DEPENDENCIES[step]]
    return fingerprint([*stage_sources(step), *markers], {"input_digest": manifest.get("input_digest"), "step": step})


def _source_receipts(study, step):
    if step == "observations":
        return read_json(study / "cohorts/cohort_summary.json")["inputs"]
    if step == "evidence":
        saved = read_json(study / "cohorts/sampling.json")
        return saved["sources"] + saved["synthetic_sources"]
    if step == "stability":
        saved = read_json(study / "woody-stability/result.json")["provenance"]
        return list(saved["protected_inputs"].values()) + list(saved["landscape_stamps"].values())
    if step == "spatial":
        saved = read_json(study / "spatial/result.json")["inputs"]
        return (list(saved["protected"].values()) + saved["original_reference"]
                + saved["candidate_assets"] + saved["evidence_stamps"])
    return []


def _receipt_current(study, step, manifest, checked):
    if step in checked:
        return
    for parent in DEPENDENCIES[step]:
        _receipt_current(study, parent, manifest, checked)
    marker = study / "stages" / f"{step}.json"
    if not marker.is_file():
        raise RuntimeError(f"Complete dependency first: {step}")
    receipt = read_json(marker)
    expected, _ = _digest(study, step, manifest)
    if receipt.get("status") != "complete" or receipt.get("input_digest") != expected:
        raise RuntimeError(f"Stale dependency {step}; rerun it and its dependencies before continuing")
    for row in receipt.get("source_inputs", []):
        path = Path(row["path"])
        stat = path.stat()
        if stat.st_size != row.get("bytes", row.get("size")) or stat.st_mtime_ns != row["mtime_ns"]:
            raise RuntimeError(f"Source changed for {step}; use a new study: {path}")
    if not receipt.get("outputs") or any(not Path(r["path"]).is_file() or sha(Path(r["path"])) != r["sha256"] for r in receipt["outputs"]):
        raise RuntimeError(f"Changed or missing output in dependency {step}")
    if step == "snapshot":
        for row in read_json(study / "snapshot/manifest.json")["files"]:
            path = study / row["relative_path"]
            if not path.is_file() or sha(path) != row["sha256"]:
                raise RuntimeError(f"Preserved snapshot changed: {path}")
    checked.add(step)


def run_stage(root, study, step, *, workers=4):
    manifest = check_inputs(root, study) if (study / "manifest.json").exists() else {}
    dependencies = DEPENDENCIES[step]
    markers = [study / "stages" / f"{name}.json" for name in dependencies]
    if any(not path.exists() for path in markers):
        raise RuntimeError(f"Complete dependencies first: {dependencies}")
    checked = set()
    for dependency in dependencies:
        _receipt_current(study, dependency, manifest, checked)
    digest, inputs = _digest(study, step, manifest)
    marker = study / "stages" / f"{step}.json"
    if marker.exists():
        previous = read_json(marker)
        if previous.get("input_digest") == digest and previous.get("status") == "complete":
            good = all(Path(row["path"]).is_file() and sha(Path(row["path"])) == row["sha256"] for row in previous.get("outputs", []))
            if good:
                _receipt_current(study, step, manifest, checked)
                print(f"Reused completed {step}", flush=True)
                return previous
    (study / "completion.json").unlink(missing_ok=True)
    write_json(marker, {"status": "running", "step": step, "started_at": now(), "input_digest": digest})
    start = time.monotonic()
    print(f"Starting {step} at {now()}", flush=True)
    if step == "snapshot":
        result = snapshot(root, study)
        outputs = [study / "snapshot/manifest.json"]
    elif step == "candidates":
        from .candidates import build
        result, outputs = build(root, study), [study / "candidates"]
    elif step == "observations":
        from .field_evaluation import build_observations
        result, outputs = build_observations(root, study), [study / "cohorts/observations.parquet", study / "cohorts/cohort_summary.json"]
    elif step == "evidence":
        from .evidence import prepare
        result = prepare(root, study)
        outputs = [study / "evidence", study / "cohorts/reaches.parquet", study / "cohorts/sampling.json"]
    elif step == "scores":
        from .scorer import run
        result, outputs = run(root, study, workers=workers), [study / "scores", study / "traces"]
    elif step == "acquisition":
        from .acquisition import acquire
        from .gage_diagnostics import run
        result, outputs = acquire(root, study), [study / "acquisition"]
        result["gage_diagnostics"] = run(root, study)
    elif step == "field":
        from .field_evaluation import evaluate
        result, outputs = evaluate(root, study, boot=1000), [study / "results"]
    elif step == "spatial":
        from .spatial import run
        from .field_evaluation import evaluate
        result, outputs = run(root, study), [study / "spatial"]
        result["field_evaluation"] = evaluate(root, study, boot=1000, scores_dir=study / "spatial/scores",
            results_dir=study / "spatial/results", cohort_prefix="spatial_refit:")
    elif step == "stability":
        from .woody_stability import run
        result, outputs = run(root, study, n_boot=200), [study / "woody-stability"]
    else:
        from .report import build
        result, outputs = build(root, study), [study / "summary.json", study / "completion.json"]
    check_inputs(root, study)
    # Snapshot establishes the study input digest during its first execution.
    if step == "snapshot":
        digest, inputs = _digest(study, step, read_json(study / "manifest.json"))
    receipt = {"status": "complete", "step": step, "input_digest": digest, "inputs": inputs,
               "source_inputs": _source_receipts(study, step),
               "outputs": output_files(study, outputs), "completed_at": now(),
               "elapsed_seconds": round(time.monotonic() - start, 2), "result": result}
    write_json(marker, receipt)
    print(f"Completed {step} in {receipt['elapsed_seconds']:.2f}s", flush=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("D:/Data/easi-national"))
    parser.add_argument("--study-id", required=True)
    parser.add_argument("--steps", nargs="+", choices=STEPS, default=list(STEPS))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if Path(sys.executable).resolve() != (REPO_ROOT / ".venv/Scripts/python.exe").resolve():
        raise RuntimeError("Use the workspace .venv/Scripts/python.exe")
    root = args.root.resolve()
    study = safe_study(root, root / "review/alternative-studies" / args.study_id)
    study.mkdir(parents=True, exist_ok=True)
    lock = study / "run.lock"
    try:
        with lock.open("x", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "started_at": now()}, stream)
    except FileExistsError:
        raise RuntimeError(f"Study runner already has a lock: {lock}")
    try:
        for step in args.steps:
            run_stage(root, study, step, workers=max(1, min(args.workers, 6)))
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
