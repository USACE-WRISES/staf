"""Snapshot, checkpoints and CLI orchestration for isolated alternatives.

A study starts from a base (``bases.py``): the snapshot checks that the active EASI method
and its frozen curve artifact are that base's, and the manifest names it (``base_id``).
A study never changes its base, and a study created by another runner version is never
rerun by this one.

Runner 1.2.0: a study's candidates are built candidate packages (``--candidate E4=<folder
with candidate.json or the .easi-method.zip>``, one per family of the frozen EASI addendum).
The snapshot records them in the manifest's ``candidates`` block beside the base
(``alternative-1``); the ``candidates`` step verifies and assembles them (``candidates.py``);
every later step enumerates ``study_candidates(manifest)``. A study created without
``--candidate`` is a base-only study (Round EA).
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from builder import REPO_ROOT
from . import (ALTERNATIVES, BASE_COMMIT, BASE_METHOD, BASE_REFERENCE, DEFAULT_BASE_ID, REFERENCE_ID,
               STUDY_VERSION, bases, is_round4, study_candidates)
from . import base_scores as bs
from . import round4 as r4
from .io import fingerprint, info, now, output_files, read_json, safe_study, sha, write_json

STEPS = ("snapshot", "candidates", "observations", "evidence", "scores", "acquisition", "field", "spatial", "stability", "report")
DEPENDENCIES = {"snapshot": [], "candidates": ["snapshot"], "observations": ["snapshot"],
                "evidence": ["observations"], "scores": ["candidates", "evidence"],
                "acquisition": ["observations"], "field": ["scores", "acquisition"],
                "spatial": ["scores", "acquisition"], "stability": ["candidates", "evidence"],
                "report": ["field", "spatial", "stability"]}
#: the seeds every study records (the sample, the reference-panel resamples, the paired bootstrap)
SEEDS = {"sample": 17, "reference_bootstrap": 7, "paired_bootstrap": 20260915}
#: how each scoring step reaches the evaluator (recorded in the manifest, coordinator's answer Q7)
SCORING_ROUTES = {
    "scores": "package: each arm scores its own method package (EASI_METHOD_PACKAGE, EASI_DATA_DIR unset)",
    "spatial": ("data-dir: a fold-local refit artifact is not a method package (its identity file would have to be "
                "restamped per fold), so the fold arms point the same evaluator at their app-data folder "
                "(EASI_DATA_DIR); both routes score identically (the runner's tests prove it on library records)"),
}


def _input_digest(upstream, base_scores=None) -> str:
    """The study's input digest: the upstream inputs, the protocol and, when the base arm is
    verified against base scores, their files' sha256."""
    doc = {"inputs": upstream, "protocol": protocol()}
    if base_scores:
        doc["base_scores"] = {"manifest_sha256": base_scores.get("manifest_sha256"),
                              "files": [f["sha256"] for f in base_scores.get("files", [])]}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


def protocol():
    return {"version": STUDY_VERSION, "sample_n": 100000, "sample_seed": SEEDS["sample"],
            "sample_strata": ["state", "l2"], "spatial_folds": 5,
            "fold_assignment": "SHA256('easi-field-fold-v1:' + smallest connected HUC8), first eight bytes as big-endian integer modulo five",
            "paired_bootstrap_draws": 1000, "paired_bootstrap_seed": SEEDS["paired_bootstrap"],
            "reference_bootstrap_draws": 200,
            "reference_bootstrap_seed": SEEDS["reference_bootstrap"], "auc_noninferiority_margin": 0.01,
            "interval": 0.95, "reference": REFERENCE_ID, "scoring": "banded",
            "rating_index": {"Good": 0.85, "Fair": 0.545, "Poor": 0.195},
            "field_definition": "same-visit-v2", "temporal_evaluation": "retrospective 2023-24",
            "national_rebuild": False, "publication": False,
            "selection": "Both paired AUC lower bounds >= -0.01, unchanged rating availability, regional/function review; fewer curves then benthic AUC; otherwise Alternative 1",
            "addendum": {"file": "apps/stream-curves/config/methodology/" + r4.ADDENDUM_FILE,
                         "sha256": r4.ADDENDUM_SHA256},
            "decision": ("a study with candidate packages is decided per candidate by the EASI addendum's "
                         "outcomes P1 to P6 and margins (outcomes.py); a study without a candidates block "
                         "keeps the 2026-09-15 selection rule"),
            "deciding_cohort": r4.DECIDING_COHORT, "reported_cohorts": list(r4.REPORTED_COHORTS),
            "retrospective_rule": ("no family decision reads the retrospective cohort; the finalist step reads it once, "
                                   "in its separate retrospective report"),
            "candidate_scoring": "each arm scores an isolated method package (EASI_METHOD_PACKAGE)",
            "base_scores": ("when the stored analysis is another registry base's, the base arm is verified against "
                            "--base-scores, a national build of the study base; the stored analysis then supplies "
                            "inputs only")}


def resolve_base(base_id=None):
    """The base a study starts from. The default base reads this module's ``BASE_*``
    names, which tests patch; any other base is its registry entry."""
    if base_id in (None, DEFAULT_BASE_ID):
        return dataclasses.replace(bases.base(DEFAULT_BASE_ID), method_version=BASE_METHOD,
                                   commit=BASE_COMMIT, reference_sha256=BASE_REFERENCE)
    return bases.base(base_id)


def source_inputs(root):
    names = ["analysis/values_meta.json", "analysis/values.parquet", "analysis/nrsa/nrsa_desktop.parquet",
             "analysis/nrsa/nrsa_frame.parquet", "analysis/nrsa/nrsa_targets.parquet", "analysis/strata.parquet",
             "analysis/local-review/completion.json", "staging/manifest.json",
             "review/2026-09-15-regional/baseline/analysis/curves/curve_registry.parquet",
             "review/2026-09-15-regional/baseline/analysis/panels/panel_members.parquet",
             "review/2026-09-15-regional/baseline/analysis/panels/reference_panels.parquet"]
    return [root / name for name in names if (root / name).is_file()]


class CandidateRefused(RuntimeError):
    """A candidate package the study cannot take: the wrong base, another evaluator, a
    record that does not match its zip. Nothing is written."""


def check_candidate(entry: dict, base, running_evaluator: str) -> None:
    """The candidates step's refusals (also applied at the snapshot, before anything is
    copied): the package's recorded base (method version and package digest) must be the
    study's base, and its evaluator digest the running evaluator's."""
    family = entry["id"]
    recorded = entry.get("base") or {}
    if not base.package_digest:
        raise CandidateRefused(f"{family}: base {base.id} has no method package; candidate packages need "
                               "a base the library holds (alternative-2-b2e3033116e3)")
    if recorded.get("packageDigest") != base.package_digest:
        raise CandidateRefused(f"{family}: the candidate records base package {str(recorded.get('packageDigest'))[:15]}, "
                               f"not the study's base {base.package_digest[:15]} ({base.id})")
    accepted = bases.accepted_method_versions(base, REPO_ROOT)
    if recorded.get("methodVersion") not in accepted:
        raise CandidateRefused(f"{family}: the candidate records base method version {recorded.get('methodVersion')!r}, "
                               f"not one of the study's base {base.id} ({', '.join(accepted)})")
    if entry.get("evaluator_digest") != running_evaluator:
        raise CandidateRefused(f"{family}: built under evaluator {str(entry.get('evaluator_digest'))[:15]}, "
                               f"not the running evaluator {running_evaluator[:15]}; rebuild the package")
    record_family = (entry.get("record") or {}).get("family")
    if record_family and record_family != family:
        raise CandidateRefused(f"{family}: the package's candidate.json is family {record_family}")


def candidate_block(base, candidates: dict | None, running_evaluator: str) -> list[dict]:
    """The manifest's ``candidates`` block: the base as ``alternative-1`` (labelled by its
    registry entry) and one entry per built candidate package, each verified."""
    block = [{"id": REFERENCE_ID, "role": "base", "label": base.label, "curve_count": base.curve_count,
              "changes": f"None; the study's base ({base.id})", "family": None,
              "package_digest": base.package_digest, "library_version": base.library_version,
              "method_version": base.method_version}]
    for family, source in sorted((candidates or {}).items()):
        spec = r4.family_spec(family)
        found = r4.read_candidate_source(Path(source))
        if found["family"] and found["family"] != family:
            raise CandidateRefused(f"{family}: {source} holds candidate {found['family']}")
        entry = {"id": family, "role": "candidate", "label": found["label"], "curve_count": found["curve_count"],
                 "changes": spec.get("change"), "family": spec.get("family"), "function": spec.get("function"),
                 "decision": spec.get("decision"), "primary_outcome": spec.get("primary_outcome"),
                 "coverage_effect": spec.get("coverage_effect"), "hypothesis": spec.get("hypothesis"),
                 "source": found["source"], "zip": found["zip"], "zip_sha256": found["zip_sha256"],
                 "zip_bytes": found["zip_bytes"], "record": found["record"], "files": found["files"],
                 "package_digest": found["package_digest"], "method_version": found["method_version"],
                 "evaluator_digest": found["evaluator_digest"], "validated_under": found["validated_under"],
                 "requires": found["requires"], "base": found["base"], "curve_sets": found["curve_sets"],
                 "curve_set_findings": found["curve_set_findings"],
                 "respecified": found["respecified"] or (spec.get("respecified") or {}).get("date"),
                 "comparison_scope": r4.comparison_scope(spec)}
        check_candidate(entry, base, running_evaluator)
        block.append(entry)
    return block


def stored_analysis_record(root: Path, base, completion: dict) -> tuple:
    """``(stored base, record)`` for the stored analysis the study snapshots: the registry base
    whose method scored it (its local-review completion), and what the study uses it for. A
    completion under no known base, or not complete, is refused."""
    if completion.get("status") != "complete":
        raise RuntimeError("Current Alternative 1 completion is unavailable")
    stored = bs.stored_analysis_base(completion.get("method_version"), REPO_ROOT)
    if stored is None:
        raise RuntimeError(f"Current Alternative 1 completion is unavailable: its method version "
                           f"{completion.get('method_version')!r} is no registry base's")
    same = stored.id == base.id
    record = {"method_version": completion.get("method_version"), "base_id": stored.id,
              "is_study_base": same, "completion_sha256": sha(root / "analysis/local-review/completion.json"),
              "used_for": list(bs.STORED_ANALYSIS_USES) + (["the base arm's reference scores (scorer.verify's replay)"] if same else []),
              "never": None if same else bs.STORED_ANALYSIS_NEVER}
    return stored, record


def snapshot(root: Path, study: Path, base_id=None, candidates: dict | None = None, command_line=None,
             base_scores=None):
    from easi import config
    from easi import method_package as mp
    from easi.national import method_version
    base = resolve_base(base_id)
    running = method_version()
    # the running evaluator's method version is the base's own, or one the library validated
    # the base's method files under (bases.accepts); the manifest then records which
    if config.criteria_set() != "regional" or not bases.accepts(base, running, REPO_ROOT):
        raise RuntimeError(f"Alternative 1 must match the preserved regional scoring method "
                           f"of base {base.id} ({base.method_version})")
    artifact = REPO_ROOT / "apps/easi/data/reference-curves.json"
    if sha(artifact) != base.reference_sha256:
        raise RuntimeError(f"Alternative 1 frozen artifact has changed (base {base.id})")
    completion = read_json(root / "analysis/local-review/completion.json")
    # the stored analysis is accepted when its completion is under a known registry base: the
    # study base's (its scores are then the base arm's reference), or another base's (its
    # inputs are used, never its scores, and the base arm is verified against --base-scores)
    stored, stored_analysis = stored_analysis_record(root, base, completion)
    if not stored_analysis["is_study_base"] and base_scores is None:
        raise RuntimeError(f"the stored analysis under {root / 'analysis'} is base {stored.id} "
                           f"(method {completion.get('method_version')}), not the study base {base.id}: pass "
                           f"--base-scores <staging folder of the base's national build> so the base arm is "
                           f"verified against the base's own scores")
    base_scores_record = bs.read_base_scores(base_scores, base, root) if base_scores is not None else None
    base_evaluator = {"method_version": running, "evaluator_digest": mp.evaluator_digest(),
                      "validated": running != base.method_version,
                      "accepted_method_versions": list(bases.accepted_method_versions(base, REPO_ROOT))}
    if read_json(root / "state/queue.json").get("items"):
        raise RuntimeError("Publication queue must remain empty")
    # the candidates are verified before anything is copied (a refused package costs no snapshot)
    block = candidate_block(base, candidates, mp.evaluator_digest())
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
            current["input_digest"] = _input_digest(current["input_files"], current.get("base_scores"))
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
               "method_version": base.method_version, "frozen_sha256": base.reference_sha256,
               "source_commit": base.commit}
    # alternative_1 keeps its 1.0.0 meaning (the study's reference alternative is its base);
    # base_id and base name the registry entry the study was created on, base_evaluator the
    # evaluator the study ran the base under (its method version when validated later);
    # candidates is the 1.2.0 block (the base and the candidate packages), alternatives its
    # short form for readers of the older key
    data = {"schema_version": 1, "study_id": study.name, "status": "pending", "created_at": now(),
            "base_id": base.id, "base": base.record(), "base_evaluator": base_evaluator,
            "alternative_1": {"method_version": base.method_version, "source_commit": base.commit,
                              "frozen_sha": base.reference_sha256},
            "parent_binding": binding,
            "alternatives": [{k: row.get(k) for k in ("id", "label", "curve_count", "changes")} for row in block],
            "candidates": block, "protocol": protocol(),
            "runner_version": STUDY_VERSION, "seeds": dict(SEEDS),
            "addendum": {"file": "apps/stream-curves/config/methodology/" + r4.ADDENDUM_FILE,
                         "sha256": r4.ADDENDUM_SHA256, "prose": "apps/stream-curves/config/methodology/" + r4.PROSE_FILE},
            "candidate_package_digests": {row["id"]: row.get("package_digest") for row in block},
            "command_line": list(command_line) if command_line is not None else list(sys.argv),
            "stored_analysis": stored_analysis, "base_scores": base_scores_record,
            "scoring_routes": dict(SCORING_ROUTES),
            "input_files": upstream, "study_only": True}
    data["input_digest"] = _input_digest(upstream, base_scores_record)
    write_json(study / "manifest.json", data)
    write_json(study / "protocol.json", protocol())
    write_json(marker, {"status": "preserved", "created_at": now(), "source_commit": base.commit,
                        "files": entries, "bytes": sum(row["bytes"] for row in entries)})
    return {"files": len(entries), "bytes": sum(row["bytes"] for row in entries),
            "candidates": [row["id"] for row in block]}


def check_inputs(root, study):
    manifest = read_json(study / "manifest.json")
    for row in manifest["input_files"]:
        path = root / row["path"]
        stat = path.stat()
        if stat.st_size != row["size"] or stat.st_mtime_ns != row["mtime_ns"]:
            raise RuntimeError(f"Study input changed; create a new study: {path}")
    for row in (manifest.get("base_scores") or {}).get("files", []):
        path = Path(row["path"])
        stat = path.stat()
        if stat.st_size != row["bytes"] or stat.st_mtime_ns != row["mtime_ns"]:
            raise RuntimeError(f"Base scores changed; create a new study: {path}")
    return manifest


def stage_sources(step):
    package = Path(__file__).parent
    files = {"snapshot": ["study.py", "io.py", "__init__.py", "round4.py", "base_scores.py"],
             "candidates": ["candidates.py", "round4.py"],
             "observations": ["field_evaluation.py"], "evidence": ["evidence.py"], "scores": ["scorer.py", "base_scores.py"],
             "acquisition": ["acquisition.py", "gage_diagnostics.py"],
             "field": ["field_evaluation.py", "bootstrap_metrics.py", "round4.py"],
             "spatial": ["spatial.py", "bootstrap_metrics.py"],
             "stability": ["woody_stability.py", "set_stability.py"],
             "report": ["report.py", "outcomes.py", "round4.py"]}
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
    if step in ("candidates", "report"):
        paths += [r4.ADDENDUM_PATH]
    if step == "evidence":
        paths += sorted((analysis.parent / "stages").glob("*.py"))
        paths += [analysis.parent / name for name in ("config.py", "units.py", "paths.py")]
    if step == "spatial":
        paths += [package / name for name in ("scorer.py", "candidates.py", "field_evaluation.py", "round4.py")]
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
    if step == "candidates":
        # the candidate package zips the study took (external inputs of a 1.2.0 study)
        return list(read_json(study / "candidates/manifest.json").get("sources") or [])
    if step == "stability":
        if (study / "set-stability/result.json").is_file():
            saved = read_json(study / "set-stability/result.json")["provenance"]
            return list(saved["protected_inputs"].values()) + list(saved.get("population_stamps", {}).values())
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


def run_stage(root, study, step, *, workers=4, base_id=None, candidates=None, command_line=None, base_scores=None):
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
        result = snapshot(root, study, base_id=base_id, candidates=candidates, command_line=command_line,
                          base_scores=base_scores)
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
        if is_round4(manifest):
            from .set_stability import run
            result, outputs = run(root, study, n_boot=200), [study / "set-stability"]
        else:
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


def parse_candidates(items) -> dict:
    """``["E4=<folder or zip>", ...]`` -> ``{"E4": "<folder or zip>"}``; a family named twice
    or unknown to the addendum is refused."""
    out: dict = {}
    for item in items or ():
        family, sep, source = str(item).partition("=")
        if not sep or not family or not source:
            raise RuntimeError(f"--candidate takes <family>=<folder with candidate.json or .easi-method.zip>, got {item!r}")
        if family not in r4.FAMILIES:
            raise RuntimeError(f"--candidate {family}: the addendum names {', '.join(r4.FAMILIES)}")
        if family in out:
            raise RuntimeError(f"--candidate {family} given twice")
        out[family] = source
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("D:/Data/easi-national"))
    parser.add_argument("--study-id", required=True)
    parser.add_argument("--steps", nargs="+", choices=STEPS, default=list(STEPS))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--base", choices=sorted(bases.BASES), default=None,
                        help="the base method a NEW study starts from (bases.py); an existing "
                             "study keeps the base its manifest names")
    parser.add_argument("--candidate", action="append", default=[], metavar="FAMILY=PATH",
                        help="a built candidate package for a NEW study (E1 to E8 = the builder's output "
                             "folder holding candidate.json, or its .easi-method.zip); an existing study "
                             "keeps the candidates its manifest names")
    parser.add_argument("--base-scores", type=Path, default=None, metavar="STAGING",
                        help="the staging folder of a national build of the study base (its manifest names the "
                             "base's method version and alternative id); required for a NEW study when the "
                             "stored analysis is another base's, and the base arm is verified against it")
    args = parser.parse_args()
    if Path(sys.executable).resolve() != (REPO_ROOT / ".venv/Scripts/python.exe").resolve():
        raise RuntimeError("Use the workspace .venv/Scripts/python.exe")
    root = args.root.resolve()
    study = safe_study(root, root / "review/alternative-studies" / args.study_id)
    candidates = parse_candidates(args.candidate)
    manifest_path = study / "manifest.json"
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        created_by = (manifest.get("protocol") or {}).get("version")
        if created_by not in (None, STUDY_VERSION):
            raise RuntimeError(f"{study.name} was created by study runner {created_by}; this runner "
                               f"is {STUDY_VERSION}. A completed study is never rerun: start a new "
                               f"study ({bases.STUDY_ID_PATTERN.pattern})")
        base_id = bases.study_base(manifest).id
        if args.base and args.base != base_id:
            raise RuntimeError(f"{study.name} was created on base {base_id}; a study never changes "
                               f"its base")
        if candidates:
            recorded = {row["id"]: row for row in study_candidates(manifest) if row.get("role") == "candidate"}
            if set(candidates) != set(recorded):
                raise RuntimeError(f"{study.name} was created with candidates {sorted(recorded)}; a study "
                                   f"never changes its candidates (got {sorted(candidates)})")
            for family, source in candidates.items():
                found = r4.read_candidate_source(Path(source))
                if found["package_digest"] != recorded[family].get("package_digest"):
                    raise RuntimeError(f"{study.name}: candidate {family} was recorded as package "
                                       f"{str(recorded[family].get('package_digest'))[:15]}, not {found['package_digest'][:15]}")
        if args.base_scores is not None:
            recorded_folder = (manifest.get("base_scores") or {}).get("folder")
            if recorded_folder is None or Path(recorded_folder).resolve() != args.base_scores.resolve():
                raise RuntimeError(f"{study.name} was created with base scores {recorded_folder}; a study never "
                                   f"changes its base scores (got {args.base_scores})")
    else:
        base_id = args.base or DEFAULT_BASE_ID
        if not bases.study_id_ok(study.name):
            raise RuntimeError(f"a new study is named <date>-<family>-alternatives "
                               f"({bases.STUDY_ID_PATTERN.pattern}), for example "
                               f"2026-10-03-low-flow-alternatives; got {study.name!r}")
    study.mkdir(parents=True, exist_ok=True)
    lock = study / "run.lock"
    try:
        with lock.open("x", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "started_at": now()}, stream)
    except FileExistsError:
        raise RuntimeError(f"Study runner already has a lock: {lock}")
    try:
        for step in args.steps:
            run_stage(root, study, step, workers=max(1, min(args.workers, 6)), base_id=base_id,
                      candidates=candidates or None, command_line=sys.argv, base_scores=args.base_scores)
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
