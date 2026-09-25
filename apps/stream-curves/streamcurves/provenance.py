"""What a regional run did, recorded so another run can be compared against it.

The contract, in one line: two runs with the same ``inputs_digest`` must produce
the same assessment.

Three artifacts come out of here.

- The **run manifest**: the versions, config hashes and input hashes a run saw,
  plus the stratifier candidate ledger with a verdict for every registered
  candidate, included and excluded. That ledger is what makes "why was slope not
  screened in the Southeastern Plains" answerable without re-running anything.
- The **decision records**: one uniform record per rule application, derived from
  the result dict ``regional_agent.run`` already returns rather than by threading
  a recorder through every analysis function. Derived means they cannot drift
  from the reports, and they are testable from a fixture dict with no pipeline
  run. ``rules_applied`` is computed from the records, replacing a hardcoded
  literal that listed REF-01 twice and no STRAT rule at all.
- The **review queue**: the records that need a human, prioritized into tiers.
  Including the item this whole module exists to surface: a stratifier that
  passed STRAT-00 and was deliberately not applied to the curves.

Pure: dicts in, dicts out. The CLI writes the files.
"""

from __future__ import annotations

import copy
import functools
import logging
import os
import platform
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Mapping, Optional

import numpy as np
import pandas as pd

from . import curves as curve_engine
from . import methodology, run_state
from .nrsa_dataset import DEFAULT_DATASET_ID as LEGACY_DATASET_ID

# The canonical bootstrap depth (the batch runner's default). A run at this
# depth adds no digest key (absence semantics, like the legacy dataset).
DIGEST_DEFAULT_N_BOOT = 1000

logger = logging.getLogger("streamcurves")

MANIFEST_SCHEMA_VERSION = 1
PROVENANCE_SCHEMA_VERSION = 2
REVIEW_QUEUE_SCHEMA_VERSION = 1

#: regional-agent-2 (2026-09-25, campaign Round 1): the provenance document carries the
#: per-metric rebuild ledger (``metricLedger``) and the candidate register; a manifest built
#: with ``digestSchema`` 2 fingerprints the code, the wider config list, the standing-decision
#: policy, every reviewer and owner input and the refit mode into ``inputsDigest``. The
#: agent block is outside the digest payload, so the bump moves no published digest.
AGENT_VERSION = "regional-agent-2"

#: The ``inputsDigest`` rules a manifest declares. Absent: the legacy rules every published
#: version was digested under, kept verbatim. 2: the legacy payload plus the code fingerprint,
#: the policy, the decisions digest, the refit mode, the reviewer input files, the NRSA value
#: policy and the experimental block (:func:`digest_payload_from_manifest`).
DIGEST_SCHEMA = 2

#: The config files a schema-2 manifest fingerprints beside the five the legacy rules name
#: (``build_run_manifest``). ``metric_evidence.yaml`` and ``nrsa_harmonization_audit.csv`` are
#: documentation and stay out.
DIGEST_SCHEMA_2_CONFIGS = ("staf_metric_library.json", "nrsa_cycle_compatibility.yaml",
                           "model_registry.yaml", "published_benchmarks.yaml",
                           "reference_transfer.yaml", "basis_validation.yaml")

#: Every record carries these, so the log is uniformly queryable.
RULE_RECORD_FIELDS = (
    "decision_id", "run_id", "ecoregion_code",
    "rule_id", "rule_family", "threshold_status", "implementation_status",
    "subject_kind", "subject",
    "inputs", "thresholds_used", "computed",
    "verdict", "recommendation", "confidence",
    "review_required", "review_triggers",
    "timestamp",
    # Null at emission; a human pass fills them. This is what makes the log an
    # audit trail rather than a report.
    "reviewer", "reviewer_action", "reviewer_rationale", "reviewed_at",
    # 2026-08-21 (review VAL-6, VAL-12): the class a decision was made as, who
    # drafted the rationale, and the computed fields the rationale asserts.
    "reviewer_decision_class", "reviewer_rationale_origin", "reviewer_asserts",
)

def jsonable(value):
    """Plain Python types throughout.

    Records are built from pandas frames, so they carry numpy scalars that
    json.dump refuses. The published provenance.json goes through a strict writer
    with no default handler, so coerce here rather than at each writer.
    """
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if np.isnan(value) else float(value)
    if isinstance(value, float):
        return None if pd.isna(value) else value
    if value is None or isinstance(value, (str, int)):
        return value
    if value is pd.NA or (not isinstance(value, (pd.DataFrame, pd.Series)) and pd.isna(value)):
        return None
    return str(value)


VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_REVIEW = "review"
VERDICT_NOT_APPLICABLE = "not_applicable"
VERDICT_NOT_EVALUATED = "not_evaluated"


# --------------------------------------------------------------------------- #
# Environment (best effort: a tarball checkout records null, never raises)
# --------------------------------------------------------------------------- #
def _git_state(repo_root: Path) -> dict:
    """Commit, dirty flag, the dirty file list, and a digest of the working-tree
    diff. A boolean alone could not tell output-only dirt from code drift, which
    is what kept the July runs from being reproducible from their manifests
    (2026-08-21, review VAL-7)."""
    out = {"commit": None, "dirty": None, "dirty_files": None, "diff_digest": None}
    try:
        out["commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True,
            text=True, timeout=10, check=True).stdout.strip()
        porcelain = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root, capture_output=True,
            text=True, timeout=10, check=True).stdout
        files = [line[3:].strip() for line in porcelain.splitlines() if line.strip()]
        out["dirty"] = bool(files)
        out["dirty_files"] = files
        if files:
            diff = subprocess.run(
                ["git", "diff", "HEAD"], cwd=repo_root, capture_output=True,
                text=True, timeout=30, check=True).stdout
            out["diff_digest"] = "sha256:" + hashlib.sha256(
                diff.encode("utf-8", errors="replace")).hexdigest()
    except Exception:  # noqa: BLE001 — provenance must never break a run
        pass
    return out


def _git_commit(repo_root: Path) -> tuple[str | None, bool | None]:
    st = _git_state(repo_root)
    return st["commit"], st["dirty"]


def _package_versions() -> dict:
    out = {}
    for name in ("pandas", "numpy", "scipy", "statsmodels", "pyarrow"):
        try:
            out[name] = __import__(name).__version__
        except Exception:  # noqa: BLE001
            out[name] = None
    return out


# --------------------------------------------------------------------------- #
# Run manifest
# --------------------------------------------------------------------------- #
def _stratifier_candidates(result: dict) -> list[dict]:
    """Every registered candidate with a verdict, not only the ones that ran."""
    strat = result.get("stratifiers") or {}
    ledger = strat.get("eligibility")
    if ledger is None or len(ledger) == 0:
        return []
    registry = strat.get("registry") or {}
    candidates = registry.get("candidates") or {}
    out = []
    for _, row in ledger.iterrows():
        key = str(row["stratification"])
        cfg = candidates.get(key) or {}
        out.append({
            "stratification": key,
            "display_name": row["display_name"],
            "source_column": row["source_column"],
            "source_present": bool(row["source_present"]),
            "breakpoint_source": "pre-defined (national_stratifier_registry.yaml)",
            "breakpoints": [
                d.get("rule_expression") for d in (cfg.get("group_definitions") or [])
            ],
            "levels_declared": list(cfg.get("levels") or []),
            "levels_populated": [
                lvl for lvl in str(row["populated_levels"]).split("|") if lvl
            ],
            "level_counts": row["level_counts"],
            "min_group_size_rule": int(row["min_group_size"]),
            "min_populated_n": int(row["min_populated_n"]),
            "eligible": bool(row["eligible"]),
            "reason": row["exclusion_reason"] or "eligible",
        })
    return out


def _nrsa_dataset_record(result: dict, app_root: Path) -> dict:
    """Which NRSA dataset the run read, and enough to pin it.

    For the legacy default the two bundled file fingerprints already pin it, so
    this stays minimal. For the multi-cycle archive it carries the manifest digest,
    which covers every file in it, plus the cycles and the selection policy.
    """
    record = {
        "datasetId": str(result.get("nrsa_dataset") or LEGACY_DATASET_ID),
        "cycles": result.get("nrsa_cycles"),
        "policy": result.get("nrsa_policy"),
    }
    if record["datasetId"] == LEGACY_DATASET_ID:
        return record

    manifest_path = app_root / "data" / "nrsa" / "manifest.json"
    if manifest_path.exists():
        raw = manifest_path.read_bytes()
        record["manifestPath"] = manifest_path.relative_to(app_root).as_posix()
        record["manifestDigest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
        try:
            doc = json.loads(raw.decode("utf-8"))
            record["totalBytes"] = doc.get("totalBytes")
            record["fileCount"] = len(doc.get("files") or {})
            if record["cycles"] is None:
                record["cycles"] = doc.get("cycles")
        except ValueError:
            pass
    else:
        record["manifestDigest"] = None
    panel = result.get("nrsa_panel_summary")
    if panel:
        record["panel"] = panel
    # The reference frame (2026-09-07): wadeable means NHDPlus V2 stream order
    # 1 to 5, with the NRSA sampling protocol deciding only where a station's
    # order cannot be resolved. Absent means every stream, which is what every
    # published version ran, so it adds no digest key.
    record["maxStreamOrder"] = result.get("nrsa_max_stream_order")
    record["protocols"] = list(result.get("nrsa_protocols") or []) or None
    # How many stations the frame kept out, and every one the owner readmitted
    # with a reason. The overrides change the pool, so they join the digest.
    record["nOutOfFrame"] = result.get("nrsa_n_out_of_frame")
    record["frameOverrides"] = list(result.get("nrsa_frame_overrides") or []) or None
    return record


def new_manifest_defaults() -> dict:
    """What a new build's manifest declares beyond the legacy rules: the digest schema
    (``build_run_manifest(result, defaults=new_manifest_defaults())``). The batch package
    merges it into the manifests it builds; every manifest built without it keeps the
    legacy rules verbatim, so every published digest still replays."""
    return {"digestSchema": DIGEST_SCHEMA}


def build_run_manifest(result: dict, *, argv=None, started_at=None, finished_at=None,
                       app_root: Path | None = None, defaults: Optional[dict] = None) -> dict:
    """The reproducibility record for one regional run.

    ``defaults`` (:func:`new_manifest_defaults`): what the caller declares for the manifest
    beyond the legacy rules. With ``digestSchema`` 2 the manifest also records the code
    fingerprint (``agent.codeFingerprint``), the wider config list, the refit mode
    (``inputs.refit``: ``defaults["refit"]``, else ``result["refit_mode"]``, else
    ``missing``), the digest of every reviewer and owner input
    (``reviewerInputs.decisionsDigest``, :func:`decisions_digest`), the sha of each decision
    file read (``reviewerInputs.files``: ``defaults["reviewerInputs"]["files"]``, else
    ``result["reviewer_input_files"]``) and the ``experimental`` block
    (``defaults["experimental"]``, else ``result["experimental"]``, else null). Without it
    the manifest is what it always was.
    """
    app_root = app_root or Path(__file__).resolve().parent.parent
    repo_root = app_root.parent.parent
    git_state = _git_state(repo_root)
    commit, dirty = git_state["commit"], git_state["dirty"]
    region = result.get("region") or {}
    defaults = dict(defaults or {})
    schema = defaults.get("digestSchema")
    if schema is not None and int(schema) != DIGEST_SCHEMA:
        raise ValueError(f"unknown inputs digest schema {schema!r}; this app writes "
                         f"{DIGEST_SCHEMA} or the legacy rules")

    # Spelled out path by path: tests/test_line_endings.py reads the fingerprinted set from
    # this source, so every file a manifest hashes stays LF or pinned in .gitattributes.
    config_paths = [
        app_root / "config" / "nrsa_response_directions.yaml",
        app_root / "config" / "landscape_response_directions.yaml",
        app_root / "config" / "metric_map.yaml",
        app_root / "config" / "staf_functions.json",
        app_root / "config" / "national_stratifier_registry.yaml",
    ]
    if schema is not None:
        # digest schema 2: the configs the legacy rules left out (DIGEST_SCHEMA_2_CONFIGS)
        config_paths += [
            app_root / "config" / "staf_metric_library.json",
            app_root / "config" / "nrsa_cycle_compatibility.yaml",
            app_root / "config" / "model_registry.yaml",
            app_root / "config" / "published_benchmarks.yaml",
            app_root / "config" / "reference_transfer.yaml",
            app_root / "config" / "basis_validation.yaml",
        ]
        assert [p.name for p in config_paths[5:]] == list(DIGEST_SCHEMA_2_CONFIGS)
    configs = methodology.file_fingerprints(config_paths)
    # Which NRSA data the run read. The legacy default keeps fingerprinting the two
    # bundled files exactly as before, so an unchanged run reproduces its digest;
    # a pooled run adds the archive's manifest digest, the cycles it drew on and the
    # selection policy. Without this two runs over the same ecoregion on different
    # data would share an inputsDigest, which is precisely what the digest promises
    # cannot happen.
    dataset = _nrsa_dataset_record(result, app_root)
    inputs = {
        "nrsa_dataset": dataset,
        "nrsa_values": methodology.file_fingerprints(
            [app_root / "data" / "nrsa_metrics.parquet"])[0],
        "nrsa_sites": methodology.file_fingerprints(
            [app_root / "data" / "nrsa_sites.csv"])[0],
        "easi": {
            "preset": result.get("screening_method"),
            "reference_tier": result.get("reference_tier"),
            "ref02_triggered": bool(result.get("ref02_triggered")),
            "n_screened": (result.get("screening_counts") or {}).get("n_screened"),
            "n_retained": (result.get("screening_counts") or {}).get("n_retained"),
            "method_version": run_state.SCREENING_METHOD_VERSION,
            # The watershed-engine policy the screen was pinned to, the vendored
            # engine's own echo of it from the batch config, and what the screen
            # keyed on. The pin and the COMID mode JOIN the digest below (under
            # absence semantics): neither reproduces anything published, since
            # surrogate routing reached EASI on 2026-08-29 and the pin on
            # 2026-09-01, after every published version was screened.
            "watershed_engine": result.get("screening_watershed_engine"),
            "watershed_engine_echo": _screening_engine_echo(result),
            "comid_mode": result.get("screening_comid_mode"),
            "comid_sources": ((result.get("screening_comids") or {}).get("counts")
                              if result.get("screening_comids") else None),
            "routed_sites": ((result.get("screening_comids") or {}).get("routed_sites")
                             if result.get("screening_comids") else None),
            "refused_sites": ((result.get("screening_comids") or {}).get("refused_sites")
                              if result.get("screening_comids") else None),
            "differing_sites": ((result.get("screening_comids") or {}).get("differing_sites")
                                if result.get("screening_comids") else None),
            "vendor_sha": (result.get("easi_vendor") or {}).get("vendorSha"),
            "cache": result.get("screening_cache"),
            # Sites the owner dropped from the retained pool, with reasons
            # (--exclude-site); they change the pool, so they join the digest.
            "owner_site_exclusions": list(result.get("owner_site_exclusions") or []),
        },
        "streamcat": (result.get("source_reports") or [None])[0],
    }
    # The reference method (methodology 0.12). Recorded only for a
    # pressure-screen run, so every manifest built under the legacy method reads
    # as it always did. It names everything that decides pool membership and
    # curve geometry there: the screen, the committed station table, the
    # transfer profiles, the national scale registry, the fixed criteria, and
    # the value policy. It JOINS the digest below.
    if result.get("reference_method") == "pressure-screen":
        from . import fixed_criteria, reference_pool
        screen = result.get("reference_screen") or {}
        registry = result.get("scale_registry") or {}
        try:
            from ._vendor.easi import national as _easi_national
            easi_method = _easi_national.method_version()
        except Exception:  # noqa: BLE001 - context only, never a build input
            easi_method = None
        inputs["reference"] = {
            "method": "pressure-screen",
            "methodVersion": run_state.REFERENCE_SCREEN_METHOD_VERSION,
            "screenId": screen.get("id"), "screenTier": screen.get("tier"),
            "frame": "wadeable (DATA-10), non-canal",
            "stationScreen": {k: (screen.get("stationScreen") or {}).get(k)
                              for k in ("path", "sha256", "rows", "source", "builtAt")},
            "transferConfig": {"sha256": reference_pool.transfer_config_sha256()},
            "scaleRegistry": {"sha256": registry.get("sha256"),
                              "version": registry.get("version"),
                              "analysisVersion": registry.get("analysis_version")},
            "fixedCriteria": {"sha256": fixed_criteria.fixed_criteria_sha256()},
            "valuePolicy": (result.get("value_selection") or {}).get("policy"),
            "easiMethodVersion": easi_method,
            "pool": result.get("reference_pool_summary"),
            # REF-08/09/10: which rungs a curve could reach is decided by the
            # committed verdicts, so the digest carries their identity. A run
            # that reached no rung above the hierarchy adds nothing, which keeps
            # every earlier digest reproducible.
            "basisValidation": _basis_validation_record(),
            "curvesByBasis": _curves_by_basis(result),
        }
        # REF-15: the refused sources the owner accepted change what the build
        # computes, so they join the digest; a run with none adds no key
        if result.get("forced_sources"):
            inputs["reference"]["forcedSources"] = dict(sorted(
                (str(k), dict(v)) for k, v in result["forced_sources"].items()))
        # and so do the metrics the owner's decisions hold out of the fit ("your
        # choice stands"): they change what the build fits
        if result.get("owner_hold"):
            inputs["reference"]["heldByOwner"] = sorted(str(k) for k in result["owner_hold"])

    # Predictor source: recorded whenever the run declares one. The DERIVED
    # value (from the predictor columns actually configured) is authoritative;
    # the requested flag rides beside it for the audit trail.
    predictor_source = result.get("predictor_source")
    if predictor_source and predictor_source != "streamcat":
        se_report = next((r for r in (result.get("source_reports") or [])
                          if (r or {}).get("source") == "site_engine"), None)
        inputs["predictor_source"] = {
            "source": predictor_source,
            "requestedFlag": result.get("predictor_source_flag"),
            "engine": (se_report or {}).get("engine"),
            "report": se_report,
            # The scored landscape columns the engine recomputed (2026-09-02);
            # joins the digest below because those curves change with it.
            "resourced_metrics": sorted(
                str(c) for c in (result.get("resourced_metrics") or [])),
        }

    manifest = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "region": region,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "agent": {
            "module": "streamcurves.regional_agent",
            "agentVersion": AGENT_VERSION,
            "argv": list(argv or []),
            "gitCommit": commit,
            "gitDirty": dirty,
            # The files behind the dirty flag and a digest of the diff, so a
            # third party can tell output-only dirt from code drift (VAL-7).
            "gitDirtyFiles": git_state["dirty_files"],
            "gitDiffDigest": git_state["diff_digest"],
            "python": platform.python_version(),
            "packages": _package_versions(),
            # The AI operator behind the run, when one drove it. Set
            # STAF_AI_MODEL (e.g. "claude-fable-5") and optionally
            # STAF_AI_TOOL; absent means the run was launched by a person
            # directly, and the manifest says so rather than guessing.
            "aiModel": os.environ.get("STAF_AI_MODEL"),
            "aiTool": os.environ.get("STAF_AI_TOOL"),
        },
        "diagnostics": {
            "runSeed": result.get("run_seed"),
            "nBoot": result.get("diagnostics_n_boot"),
        },
        "methodology": {
            **methodology.config_fingerprints(),
            "curveMethodVersion": run_state.CURVE_METHOD_VERSION,
            "screeningMethodVersion": run_state.SCREENING_METHOD_VERSION,
        },
        "configs": configs,
        "inputs": inputs,
        "stratifiers": {
            "registryVersion": (result.get("stratifiers") or {}).get("registry_version"),
            # "registry" once a national scale-registry split became real curves
            # in this run (STRAT-10); the STRAT-00 screen itself stays advisory.
            "mode": ("registry" if any(r.get("applied") for r in
                                       (result.get("strata_applied") or {}).values())
                     else "advisory"),
            "breakpointPolicy": (
                "Pre-defined categories only. No data-derived binning, so STRAT-08 is "
                "satisfied by construction."
            ),
            "candidates": _stratifier_candidates(result),
        },
        "reviewerInputs": {
            # The recorded human inputs the run was given, so the publish is
            # reproducible from the manifest alone (2026-08-21).
            "finalizedMetrics": result.get("finalized_metrics") or {},
            "removedMetrics": result.get("removed_metrics") or {},
            # the owner's curve decisions (REF-15), without curve data
            "curveDecisions": [_owner_summary(d) for d in result.get("curve_decisions") or []],
            "deferredGradients": result.get("deferred_gradients") or {},
        },
        # The standing-decision policy a batch run applied (2026-08-22): its
        # version and digest, the entries enabled for the run, how many
        # decisions it made, and who confirmed them (null while staged).
        "standingDecisions": result.get("standing_decisions"),
        "determinism": {
            "randomSeeds": {"runSeed": result.get("run_seed")},
            "seedPolicy": (
                "Every resampling diagnostic (CURVE-02/04/06, RED-06, STRAT-06) derives "
                "its seed from the run seed, which is a function of the ecoregion, the "
                "retained site ids, and the methodology version. Quantiles, "
                "correlations, Kruskal-Wallis and Benjamini-Hochberg are deterministic."
            ),
            "orderPolicy": (
                "Metrics in metric_config order; stratifier candidates and their levels "
                "in registry-declared order, never data order; redundancy pairs by "
                "descending absolute Spearman."
            ),
        },
        "outputs": {
            "assessmentId": result.get("assessment_id"),
            "nCurves": len(result.get("curve_rows") or {}),
            "nIntendedMetrics": len(result.get("intended_metrics") or []),
        },
    }
    if schema is not None:
        _declare_digest_schema_2(manifest, result, defaults, app_root=app_root)
    manifest["inputsDigest"] = methodology.inputs_digest(
        digest_payload_from_manifest(manifest))
    return manifest


def _declare_digest_schema_2(manifest: dict, result: dict, defaults: dict, *,
                             app_root: Path) -> None:
    """Record on ``manifest`` what the schema-2 digest names beyond the legacy rules."""
    from . import code_identity
    manifest["digestSchema"] = DIGEST_SCHEMA
    manifest["agent"]["codeFingerprint"] = code_identity.fingerprint(app_root)
    carried = result.get("carried_from") or {}
    mode = str(defaults.get("refit") or result.get("refit_mode") or "missing")
    manifest["inputs"]["refit"] = {
        "mode": mode,
        "carriedFrom": ({"assessmentId": carried.get("assessmentId"),
                         "version": carried.get("fromVersion"),
                         "contentDigest": carried.get("contentDigest")}
                        if carried.get("assessmentId") else None),
    }
    files = ((defaults.get("reviewerInputs") or {}).get("files")
             or result.get("reviewer_input_files") or {})
    manifest["reviewerInputs"]["files"] = {str(k): v for k, v in sorted(dict(files).items())}
    manifest["reviewerInputs"]["decisionsDigest"] = decisions_digest(decision_inputs_of(result))
    manifest["experimental"] = defaults.get("experimental") or result.get("experimental") or None


def decision_inputs_of(result: dict) -> dict:
    """Every reviewer and owner input a build was given, read off the result in the shape
    :func:`decisions_digest` hashes: the owner's curve decisions (REF-15), the reviewer
    answers (``result["reviewer_decisions"]``, the batch's owner answers), the finalizations
    and removals, the SELECT-01 approvals (``result["portfolio_approvals"]`` or the meta's),
    the documented gaps, the policy entries enabled for the run and the candidates a person
    added for comparison."""
    meta = result.get("meta") or {}
    register = result.get("candidate_register") or {}
    return {
        "curveDecisions": list(result.get("curve_decisions") or []),
        "answers": list(result.get("reviewer_decisions") or []),
        "finalizations": dict(result.get("finalized_metrics") or {}),
        "removals": dict(result.get("removed_metrics") or {}),
        "approvals": list(result.get("portfolio_approvals") or meta.get("portfolioApprovals") or []),
        "gaps": list(result.get("coverage_exceptions") or []),
        "enabledPolicyIds": list((result.get("standing_decisions") or {}).get("enabledIds") or []),
        "consideredCandidates": [c.get("candidateKey") for c in
                                 (register.get("considered") or []) if isinstance(c, dict)],
    }


def decisions_digest(inputs: dict) -> str:
    """One canonical sha256 over every reviewer and owner input of a build, so two builds
    given the same decisions share it and any changed answer moves the inputs digest.

    ``inputs`` (:func:`decision_inputs_of`): ``curveDecisions`` (REF-15 records, hashed as
    ``owner_curves.requests`` states them: what the owner recorded, never what a build
    computed), ``answers`` (reviewer decisions: rule, subject, action, rationale, class),
    ``finalizations`` and ``removals`` (metric to note), ``approvals`` (function and note;
    who confirmed rides in the manifest, and a standing decision's pending marker is
    confirmed at promote without re-staging), ``gaps`` (function, reason, justification),
    ``enabledPolicyIds`` and ``consideredCandidates`` (candidate keys). Missing keys read as
    empty, so a build with no decisions has a digest too.
    """
    from . import owner_curves
    inputs = dict(inputs or {})

    def clean(text) -> str:
        return " ".join(str(text or "").split())

    answers = sorted(json.dumps(
        {"rule_id": str(a.get("rule_id")), "subject": str(a.get("subject")),
         "action": str(a.get("action") or ""), "rationale": clean(a.get("rationale")),
         "class": a.get("decision_class")}, sort_keys=True)
        for a in inputs.get("answers") or [] if isinstance(a, dict))
    approvals = sorted(json.dumps(
        {"functionId": str(a.get("functionId")), "note": clean(a.get("note"))}, sort_keys=True)
        for a in inputs.get("approvals") or [] if isinstance(a, dict))
    gaps = sorted(json.dumps(
        {"functionId": str(g.get("functionId")), "reason": str(g.get("reason") or ""),
         "justification": clean(g.get("justification"))}, sort_keys=True)
        for g in inputs.get("gaps") or [] if isinstance(g, dict))
    payload = {
        "curveDecisions": owner_curves.requests(inputs.get("curveDecisions") or []),
        "answers": answers,
        "finalizations": {str(k): clean(v) for k, v in
                          sorted((inputs.get("finalizations") or {}).items())},
        "removals": {str(k): clean(v) for k, v in sorted((inputs.get("removals") or {}).items())},
        "approvals": approvals,
        "gaps": gaps,
        "enabledPolicyIds": sorted(str(x) for x in inputs.get("enabledPolicyIds") or []),
        "consideredCandidates": sorted(str(x) for x in inputs.get("consideredCandidates") or [] if x),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _screening_engine_echo(result: dict) -> Optional[str]:
    """The ``watershed_engine`` the vendored EASI batch config actually carried
    (None for screening caches written before the field existed)."""
    try:
        tables = (result.get("screening") or {}).get("tables") or {}
        config = (tables.get("easi_screening_criteria") or {}).get("config") or {}
        value = config.get("watershed_engine")
        return str(value) if value else None
    except AttributeError:
        return None


def digest_payload_from_manifest(manifest: dict) -> dict:
    """The ``inputsDigest`` payload of a run manifest, rebuilt from the
    manifest's own recorded blocks.

    Pure, so a published version's digest can be re-derived from its stored
    manifest (``tests/test_screening_engine_pin.py`` does exactly that). The
    additive-key rules live here: the StreamCat default predictor source, the
    legacy NRSA dataset, the default bootstrap depth and an unrecorded
    screening policy add NO key, so every digest published before each of them
    existed still reproduces byte for byte.
    """
    inputs = manifest.get("inputs") or {}
    dataset = inputs.get("nrsa_dataset") or {}
    digest_payload = {
        "region": manifest.get("region") or {},
        "methodology": manifest.get("methodology"),
        "configs": manifest.get("configs"),
        "nrsa_values": inputs.get("nrsa_values"),
        "nrsa_sites": inputs.get("nrsa_sites"),
        "easi_preset": (inputs.get("easi") or {}).get("preset"),
        "registry_version": (manifest.get("stratifiers") or {}).get("registryVersion"),
    }
    # Added only when the run did not use the default dataset, so every digest
    # published before this existed still reproduces byte for byte.
    if dataset.get("datasetId") not in (None, LEGACY_DATASET_ID):
        digest_payload["nrsa_dataset"] = {
            "datasetId": dataset.get("datasetId"),
            "manifestDigest": dataset.get("manifestDigest"),
            "cycles": dataset.get("cycles"),
            "policy": dataset.get("policy"),
        }
    # Same additive rule for the reference frame: a run that keeps every stream
    # (each published version) adds no key; a wadeable-only panel is a different
    # reference population and must change the digest.
    if dataset.get("maxStreamOrder") is not None:
        digest_payload["nrsa_max_stream_order"] = int(dataset["maxStreamOrder"])
    if dataset.get("protocols"):
        digest_payload["nrsa_protocols"] = sorted(str(p) for p in dataset["protocols"])
    # An owner readmission changes the reference population exactly as an owner
    # exclusion does, so it joins the digest the same way.
    overrides = sorted(str((o or {}).get("station_key"))
                       for o in (dataset.get("frameOverrides") or []))
    if overrides:
        digest_payload["nrsa_frame_overrides"] = overrides
    # Same additive rule for the bootstrap depth: every published version ran at
    # the batch default (1000), which adds no key, so their digests stay stable.
    # Any other depth changes the resampling evidence (CURVE-06, RED-06,
    # STRAT-06) and therefore must change the digest.
    n_boot = (manifest.get("diagnostics") or {}).get("nBoot")
    if n_boot not in (None, DIGEST_DEFAULT_N_BOOT):
        digest_payload["n_boot"] = int(n_boot)
    # Same additive rule for the predictor source: the StreamCat default adds
    # no key (every previously published digest reproduces byte for byte); an
    # engine-sourced build changes the predictors and therefore the digest.
    excluded = sorted(str((e or {}).get("site_id"))
                      for e in ((inputs.get("easi") or {}).get("owner_site_exclusions") or []))
    if excluded:
        digest_payload["owner_site_exclusions"] = excluded
    # Same additive rule for the screening policy (2026-09-07): a run that
    # records no watershed engine or COMID mode (every version published before
    # either existed) adds no key, so its digest still reproduces; a run that
    # records one must change the digest, because both decide pool membership.
    easi = inputs.get("easi") or {}
    if easi.get("watershed_engine"):
        digest_payload["easi_watershed_engine"] = str(easi["watershed_engine"])
    if easi.get("comid_mode"):
        digest_payload["easi_comid_mode"] = str(easi["comid_mode"])
    ps = inputs.get("predictor_source")
    if ps:
        digest_payload["predictor_source"] = {
            "source": ps.get("source"),
            "engine": ps.get("engine"),
        }
        # The scored landscape columns the engine recomputed change the curves
        # themselves, so they join the digest; a predictors-only engine build
        # (an empty list) adds no key.
        resourced = sorted(str(c) for c in (ps.get("resourced_metrics") or []))
        if resourced:
            digest_payload["predictor_source"]["resourced_metrics"] = resourced
    # Same additive rule for the reference method (2026-09-19, methodology
    # 0.12): a legacy run records no ``reference`` block and adds no key. Under
    # the pressure screen the station table, the transfer profiles, the scale
    # registry, the fixed criteria and the value policy all decide what a curve
    # is, so every one of them is in the digest.
    ref = inputs.get("reference")
    if ref:
        digest_payload["reference"] = {
            "method": ref.get("method"), "methodVersion": ref.get("methodVersion"),
            "screenId": ref.get("screenId"), "screenTier": ref.get("screenTier"),
            "stationScreen": (ref.get("stationScreen") or {}).get("sha256"),
            "transferConfig": (ref.get("transferConfig") or {}).get("sha256"),
            "scaleRegistry": (ref.get("scaleRegistry") or {}).get("sha256"),
            "fixedCriteria": (ref.get("fixedCriteria") or {}).get("sha256"),
            "valuePolicy": ref.get("valuePolicy"),
        }
        # Additive, like every key above it: a run whose curves all came from the
        # ecoregion hierarchy records no basis evidence and its digest is
        # unchanged, so the versions published under 0.12 still replay.
        basis = ref.get("basisValidation")
        if basis:
            digest_payload["reference"]["basisValidation"] = basis
        by_basis = ref.get("curvesByBasis") or {}
        above = {k: v for k, v in by_basis.items() if k != "regional-reference"}
        if above:
            digest_payload["reference"]["curvesByBasis"] = dict(sorted(by_basis.items()))
        if ref.get("forcedSources"):
            digest_payload["reference"]["forcedSources"] = ref["forcedSources"]
        if ref.get("heldByOwner"):
            digest_payload["reference"]["heldByOwner"] = ref["heldByOwner"]
    # A manifest without a digest schema is digested under the legacy rules above,
    # verbatim: every published version replays. Schema 2 (2026-09-25) ALWAYS adds
    # what the legacy rules left out, so an unchanged value is a key with a null and
    # a manifest cannot fall back to a legacy digest by omission.
    schema = manifest.get("digestSchema")
    if schema is None:
        return digest_payload
    if int(schema) != DIGEST_SCHEMA:
        raise ValueError(f"unknown inputs digest schema {schema!r}; this app reads "
                         f"{DIGEST_SCHEMA} or the legacy rules")
    reviewer = manifest.get("reviewerInputs") or {}
    policy = manifest.get("standingDecisions") or {}
    digest_payload["digestSchema"] = DIGEST_SCHEMA
    digest_payload["code"] = (manifest.get("agent") or {}).get("codeFingerprint")
    digest_payload["policy"] = {"sha256": policy.get("sha256"),
                                "version": policy.get("policyVersion")}
    digest_payload["decisions"] = reviewer.get("decisionsDigest")
    digest_payload["refit"] = (inputs.get("refit") or {}).get("mode")
    digest_payload["reviewer_files"] = {str(k): v for k, v in
                                        sorted(dict(reviewer.get("files") or {}).items())}
    # the NRSA value policy every manifest records under inputs.nrsa_dataset.policy (the
    # literal every published version carries is latest_non_null_index_visit; a new build
    # records the policy it ran under): the one value-policy field, always present here
    digest_payload["nrsa_value_policy"] = dataset.get("policy")
    digest_payload["experimental"] = manifest.get("experimental")
    return digest_payload



def _curves_by_basis(result: dict) -> dict:
    """How many curves rest on each rung, counted from the pool decisions.

    ``result["reference_method"]`` is the method NAME, not the bundle block, so
    the count is taken from the support records themselves.
    """
    from . import curve_basis
    from . import reference_pool as rp
    out: dict = {}
    for d in (result.get("reference_support") or {}).values():
        if str((d or {}).get("status")) == rp.STATUS_INSUFFICIENT:
            continue
        basis = curve_basis.resolve((d or {}).get("basis"))
        out[basis] = out.get(basis, 0) + 1
    return dict(sorted(out.items()))


def _basis_validation_record() -> dict:
    """What the committed basis evidence says about itself.

    The file's hash alone would change whenever a comment moved, so the digest
    carries the pre-registration it was produced under and the verdicts that can
    actually admit a rung. An absent file records nothing.
    """
    from . import basis_ladder
    prov = basis_ladder.validation_provenance()
    if not prov:
        return {}
    table = basis_ladder.load_validation()
    admits = {mk: sorted(b for b, rec in by_basis.items()
                         if str((rec or {}).get("verdict")) == "validated")
              for mk, by_basis in table.items()}
    return {"preregistration": prov.get("preregistration"),
            "preregistrationSha256": prov.get("preregistration_sha256"),
            "admits": {k: v for k, v in sorted(admits.items()) if v}}


# --------------------------------------------------------------------------- #
# Decision records
# --------------------------------------------------------------------------- #
def _row_value(row, key):
    """A finite float from a one-row registry frame (or a plain mapping), else
    None."""
    try:
        v = row[key].iloc[0] if hasattr(row, "iloc") else row.get(key)
        v = float(v)
        return v if np.isfinite(v) else None
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _owner_summary(d: dict) -> dict:
    from . import owner_curves
    return owner_curves.summary(d)


def _record(run_id, region_code, rule_id, subject_kind, subject, *,
            inputs=None, thresholds=None, computed=None,
            verdict=VERDICT_PASS, recommendation=None,
            review_required=False, review_triggers=None, timestamp=None) -> dict:
    try:
        cat = methodology.rule(rule_id)
    except KeyError:
        cat = {}
    return {
        "decision_id": f"{run_id}:{rule_id}:{subject_kind}:{subject}",
        "run_id": run_id,
        "ecoregion_code": region_code,
        "rule_id": rule_id,
        "rule_family": cat.get("family") or rule_id.split("-")[0],
        "threshold_status": cat.get("threshold_status"),
        "implementation_status": cat.get("implementation_status"),
        "subject_kind": subject_kind,
        "subject": subject,
        "inputs": inputs or {},
        "thresholds_used": thresholds or {},
        "computed": computed or {},
        "verdict": verdict,
        "recommendation": recommendation,
        # The per-record confidence slot keeps the categorical basis: the CONF-01
        # heuristic is a per-curve score and rides in its own CONF-01/02 records,
        # not on every rule record.
        "confidence": {"score": None, "label": None, "basis": "categorical_proxy"},
        "review_required": bool(review_required),
        "review_triggers": list(review_triggers or []),
        "timestamp": timestamp,
        "reviewer": None, "reviewer_action": None,
        "reviewer_rationale": None, "reviewed_at": None,
        "reviewer_decision_class": None, "reviewer_rationale_origin": None,
        "reviewer_asserts": None,
    }


_LEVEL_LABELS = {"l3": "Level III", "l2": "Level II", "l1": "Level I"}


def _acceptance_thresholds() -> dict:
    """The acceptance rule set a source was judged by (ACC-01 to ACC-06)."""
    return dict(methodology.threshold("acceptance", {}) or {})


def coverage_gaps(result: dict) -> list[dict]:
    """COV-01's evidence: every function the bundle leaves unassessed or records as
    a documented gap for insufficient reference support, with its candidate
    metrics and whether each was refused by every source for a stated reason."""
    cov = result.get("coverage") or {}
    reason = methodology.threshold("coverage.documented_gap_reason",
                                   "insufficient-reference-support")
    fids = list(cov.get("missingFunctionIds") or []) + [
        e.get("functionId") for e in cov.get("exclusions") or []
        if e.get("reason") == reason]
    if not fids:
        return []
    listed = list((result.get("meta") or {}).get("insufficientReferenceSupport") or [])
    out = []
    for fid in dict.fromkeys(str(f) for f in fids if f):
        cands = [w for w in listed
                 if fid in {f.get("functionId") for f in w.get("functions") or []}]
        documented = bool(cands) and all(
            w.get("reason") == "insufficient-reference-support" and w.get("statement")
            for w in cands)
        names = [str(w.get("metricName") or w.get("metricKey")) for w in cands]
        out.append({"function_id": fid, "candidates": [w.get("metricKey") for w in cands],
                    "candidates_text": ", ".join(names) or "none",
                    "n_candidates": len(cands), "blockers_documented": documented,
                    "held_for_review": [w.get("metricKey") for w in cands
                                        if w.get("reason") != "insufficient-reference-support"]})
    return out


def _hierarchy_records(result: dict, add) -> None:
    """Methodology 0.14: the source-by-source walk of every metric that reached
    the sources after the station pools (REF-12 to REF-14 refusals), the
    fill-to-two portfolio (SELECT-04) and the documented gaps (COV-01)."""
    for a in result.get("ladder_attempts") or []:
        rule = a.get("rung")
        if rule not in ("REF-12", "REF-13", "REF-14") or a.get("admitted"):
            continue
        computed = {"why": a.get("why"), "options": a.get("options"),
                    "condition": a.get("condition"), "candidate": a.get("candidate")}
        add(rule, "metric", a.get("metric"),
            thresholds=_acceptance_thresholds(),
            computed={k: v for k, v in computed.items() if v is not None},
            verdict=VERDICT_FAIL, recommendation=a.get("why"))
    # SELECT-04's own choice; the owner's decisions on it have their REF-15 records
    selection = (result.get("base_portfolio_selection")
                 if "base_portfolio_selection" in result else result.get("portfolio_selection"))
    for fid, sel in sorted((selection or {}).items()):
        add("SELECT-04", "function", fid,
            thresholds={"fill_to": methodology.threshold("metric_portfolio.fill_to", 2)},
            computed=sel, verdict=VERDICT_PASS,
            recommendation=("Supported, not selected: " + ", ".join(
                x.get("metric") for x in sel.get("notSelected") or [])
                if sel.get("notSelected") else None))
    for rec in coverage_gaps(result):
        add("COV-01", "function", rec["function_id"],
            thresholds={"reason": methodology.threshold("coverage.documented_gap_reason",
                                                        "insufficient-reference-support")},
            computed=rec, verdict=VERDICT_REVIEW, review_required=True,
            review_triggers=["function_unassessed"],
            recommendation=("Every candidate metric was refused by every source, each for a "
                            "stated reason: publish as a documented gap." if
                            rec["blockers_documented"] else
                            "Not every candidate metric has a documented blocker; the owner "
                            "decides."))
    carried = result.get("carried_from") or {}
    if carried:
        add("REF-05", "run", "carry_forward",
            inputs={"assessmentId": carried.get("assessmentId"),
                    "fromVersion": carried.get("fromVersion"),
                    "contentDigest": carried.get("contentDigest")},
            # the curves this version scores: the carried set less the owner's
            # removals and the curves the owner gave another source
            computed={"n_carried": len(set(result.get("carried") or {})
                                       - set(result.get("removed_carried") or {})
                                       - set(result.get("replaced_carried") or {})),
                      **({"replaced": dict(result["replaced_carried"])}
                         if result.get("replaced_carried") else {}),
                      "rebuilt": {k: v.get("why") for k, v in
                                  (result.get("carry_rebuilt") or {}).items()},
                      **({"removed": dict(result["removed_carried"]),
                          "removed_by": result.get("removed_carried_by")}
                         if result.get("removed_carried") else {})},
            verdict=VERDICT_PASS,
            recommendation=("Published curves are carried forward unchanged; a curve whose "
                            "pool held a value the data verification corrected is rebuilt"
                            + ("; the owner removed "
                               + ", ".join(sorted(result.get("removed_carried") or {}))
                               + " from this version." if result.get("removed_carried")
                               else ".")))
    # REF-15: one record per decision of the owner on the curves the build did not fit
    from . import owner_curves, owner_sources
    for d in result.get("curve_decisions") or []:
        action = str(d.get("action") or "")
        fns = ", ".join(str(f) for f in d.get("functions") or [])
        computed = {"metric": d.get("metric"), "action": action,
                    "functions": list(d.get("functions") or []),
                    "coverageExceptions": [g.get("functionId") for g in
                                           d.get("coverageExceptions") or []]}
        chosen = ""
        verdict = VERDICT_PASS
        if action == owner_curves.SOURCE:
            src = d.get("source") or {}
            computed.update({"outcome": owner_sources.outcome_of(src),
                             "source": {k: src.get(k) for k in ("kind", "title", "citation")
                                        if src.get(k)},
                             "overrides": _overridden_rule(result, str(d.get("metric")))})
            chosen = (f" Source: {src.get('title')}, "
                      f"{str(src.get('citation') or '') or owner_sources.JUDGMENT.lower()}.")
            if src.get("kind") == owner_sources.REFUSED:
                computed["failedChecks"] = list(src.get("failed") or [])
                if src.get("failedAtBuild"):
                    # nothing could be built from the source: the metric keeps what
                    # the build gave it, and the record says why
                    computed["failedAtBuild"] = src["failedAtBuild"]
                    verdict = VERDICT_FAIL
                    chosen += f" Not applied: {src['failedAtBuild']}"
        why_not = owner_curves.unusable_reason(d)
        if why_not:
            verdict = VERDICT_FAIL
            chosen += f" Not applied: {why_not}"
        held = (result.get("held_by_owner") or {}).get(str(d.get("metric")))
        if held and action in (owner_curves.SOURCE, owner_curves.REMOVE):
            # "your choice stands": the build kept the metric out of its own fit,
            # though a station pool would have supported a curve
            from . import pressure_evidence as _pe
            summary = _pe.held_summary(held.get("decision") or {})
            computed["heldFromFit"] = summary
            if action == owner_curves.REMOVE:
                computed["overrides"] = _overridden_rule(result, str(d.get("metric")))
            chosen += (f" The station pools could support a fitted curve for it "
                       f"({_pe.held_words(summary)}); the decision holds it out of the build "
                       "until the owner withdraws it.")
        add(owner_curves.RULE, "owner_decision", str(d.get("id")),
            inputs=owner_curves.bundle_summary(d),
            thresholds={"min_rationale": owner_curves.min_rationale()},
            computed={k: v for k, v in computed.items() if v is not None},
            verdict=verdict,
            recommendation=(f"{owner_curves.ACTION_LABELS.get(action, action)}"
                            f"{(' (' + fns + ')') if fns else ''}: {d.get('metric')}, by "
                            f"{d.get('recordedBy')}. {d.get('rationale')}{chosen}"))


def _owner_chosen(result: dict) -> dict:
    """``{metric: source title}`` of the curves the owner's decisions put in this
    version (REF-15): a chosen source that holds a curve."""
    out = {}
    for d in result.get("curve_decisions") or []:
        src = d.get("source") or {}
        if d.get("action") == "source" and len((src.get("curve") or {}).get("points") or []) >= 2:
            out[str(d.get("metric"))] = str(src.get("title") or "a source the owner chose")
    return out


def _overridden_rule(result: dict, metric: str) -> Optional[str]:
    """The rule under which the build gave the metric what the owner replaced: a
    carried curve (REF-05), a withheld metric (REF-06), a curve from a source
    after the station pools (REF-12 to REF-14), or a station pool the decision
    held out of the fit (REF-04 for the local reference, REF-11 otherwise)."""
    if metric in (result.get("carried") or {}):
        return "REF-05"
    held = (result.get("held_by_owner") or {}).get(metric)
    if held:
        status = str(((held or {}).get("decision") or {}).get("status") or "")
        return "REF-04" if status.startswith("local") else "REF-11"
    if metric in (result.get("insufficient_support") or {}):
        return "REF-06"
    status = str(((result.get("reference_support") or {}).get(metric) or {}).get("status") or "")
    return {"national": "REF-12", "modeled": "REF-13", "published": "REF-14"}.get(status)


def _pressure_records(result: dict, add) -> None:
    """The records of the pressure-screen reference method (methodology 0.12):
    REF-04 to REF-07, DATA-11, STRAT-10, CURVE-11 and CURVE-12. Derived from the
    run's own output, like every other record."""
    screen = result.get("reference_screen") or {}
    counts = result.get("screening_counts") or {}
    summary = result.get("reference_pool_summary") or {}
    add("REF-04", "run", "reference_screen",
        inputs={"screen": screen.get("id"), "tier": screen.get("tier"),
                "stationScreenSha256": (screen.get("stationScreen") or {}).get("sha256")},
        thresholds={"strict": methodology.threshold("reference_screen.strict")},
        computed={"n_in_frame": summary.get("target_n_frame"),
                  "n_reference": counts.get("n_retained"),
                  "n_not_evaluable": counts.get("n_unresolved")},
        verdict=VERDICT_PASS,
        recommendation=("Reference membership is the fixed desktop pressure screen, read "
                        "from the committed station table."))

    # --- REF-05 / REF-06 / REF-07: one pool per metric ---
    comparisons = result.get("local_comparison") or {}
    for metric, d in (result.get("reference_support") or {}).items():
        status = d.get("status")
        computed = {
            "status": status, "level": d.get("level"),
            "level_label": _LEVEL_LABELS.get(d.get("level") or "", ""),
            "region_code": d.get("region_code"), "region_name": d.get("region_name"),
            "n_pool": d.get("n_pool"), "n_comparable": d.get("n_comparable"),
            "n_usable": d.get("n_usable"), "n_local": d.get("n_local"),
            "self_coverage": d.get("self_coverage"),
            "supported_level": d.get("supported_level"),
            "transfer_risk": d.get("transfer_risk"),
            "covariates": d.get("covariates"), "lith_groups": d.get("lith_groups"),
            "levels_tried": d.get("levels_tried"),
        }
        thresholds = {"adequate_n": methodology.threshold("data_rules.min_n_unstratified"),
                      "exploratory_n": methodology.threshold(
                          "data_rules.exploratory_n_unstratified"),
                      "envelope_quantiles": methodology.threshold(
                          "reference_pool.envelope_quantiles")}
        # methodology 0.14: the screen a pool was admitted under and every
        # source option tried before it, with why each refused
        if d.get("screen_detail"):
            computed["screen_detail"] = d.get("screen_detail")
        if d.get("options_tried"):
            computed["options_tried"] = d.get("options_tried")
        basis = str(d.get("basis") or "")
        if d.get("carried_from"):
            # a published curve kept unchanged: its pool was decided, and any
            # review of it adjudicated, in the version it comes from
            computed["carried_from"] = d.get("carried_from")
            add("REF-05", "metric", metric, thresholds=thresholds, computed=computed,
                verdict=VERDICT_PASS,
                recommendation=(f"Carried forward unchanged from version "
                                f"{d.get('carried_from')}; its reference support was decided "
                                f"there."))
        elif status == "insufficient":
            chosen = _owner_chosen(result).get(str(metric))
            add("REF-06", "metric", metric, thresholds=thresholds, computed=computed,
                verdict=VERDICT_FAIL,
                recommendation=(
                    "Insufficient reference support: no source in the hierarchy passed "
                    "acceptance, so the build builds no curve. The owner chose a source for "
                    f"it under REF-15 ({chosen}), recorded on its own." if chosen else
                    "Insufficient reference support: no source in the hierarchy "
                    "passed acceptance, so no curve is built and the metric is "
                    "not scored. No curve is forced."))
        elif basis in ("national-reference", "modeled-reference", "published-benchmark"):
            rule = {"national-reference": "REF-12", "modeled-reference": "REF-13",
                    "published-benchmark": "REF-14"}[basis]
            add(rule, "metric", metric, thresholds=_acceptance_thresholds(),
                computed=computed, verdict=VERDICT_PASS,
                recommendation=d.get("transfer_note"))
        elif d.get("options_tried") and status != "local":
            # a regional pool that passed the pre-registered acceptance criteria
            # is the rule's decision, not a review item (REF-11, ACC-01 to ACC-06)
            add("REF-11", "metric", metric, thresholds=_acceptance_thresholds(),
                computed=computed, verdict=VERDICT_PASS,
                recommendation=d.get("transfer_note"))
        elif status != "local":
            add("REF-05", "metric", metric, thresholds=thresholds, computed=computed,
                verdict=VERDICT_REVIEW, review_required=True,
                review_triggers=["borrowed_reference_pool"],
                recommendation=d.get("transfer_note"))
        else:
            add("REF-05", "metric", metric, thresholds=thresholds, computed=computed,
                verdict=VERDICT_PASS,
                recommendation="The ecoregion's own reference stations support this metric.")
        comp = comparisons.get(metric)
        if comp:
            add("REF-07", "metric", metric,
                computed={"n": comp.get("n"), "q25": comp.get("q25"), "q50": comp.get("q50"),
                          "q75": comp.get("q75"), "definition": comp.get("definition")},
                verdict=VERDICT_PASS,
                recommendation="Shown as a labeled comparison. Never a baseline.")

    _hierarchy_records(result, add)

    # --- DATA-11: value selection across cycles ---
    selection = result.get("value_selection") or {}
    add("DATA-11", "run", "value_selection",
        inputs={"policy": selection.get("policy")},
        computed={"by_metric_cycle": selection.get("byMetricCycle")},
        verdict=VERDICT_PASS)

    # --- STRAT-10: the national registry, and what this pool could apply ---
    registry = result.get("scale_registry") or {}
    applied = result.get("strata_applied") or {}
    add("STRAT-10", "run", "scale_registry",
        inputs={"sha256": registry.get("sha256"), "version": registry.get("version"),
                "analysis_version": registry.get("analysis_version")},
        computed={"registry_present": bool(registry.get("present")),
                  "metrics_with_a_registry_split": sorted(applied),
                  "splits_applied": sorted(m for m, r in applied.items() if r.get("applied"))},
        verdict=VERDICT_PASS if registry.get("present") else VERDICT_NOT_EVALUATED,
        recommendation=(None if registry.get("present") else
                        "No national scale registry is present, so every borrowed pool's "
                        "transfer risk is unassessed."))
    for metric, rec in applied.items():
        add("STRAT-10", "metric", metric,
            inputs={"stratifier": rec.get("stratifier")},
            thresholds={"stratum_min_n": methodology.threshold("data_rules.min_n_stratum")},
            computed={"n_by_class": rec.get("n_by_class"), "supported": rec.get("supported"),
                      "applied": bool(rec.get("applied"))},
            verdict=VERDICT_PASS if rec.get("applied") else VERDICT_NOT_APPLICABLE,
            recommendation=(None if rec.get("applied") else
                            "The registry supports a split, but this pool holds fewer than "
                            "two classes at the stratum floor, so the curve stays pooled."))

    # --- CURVE-11: fixed criteria (the ones the owner kept, REF-15) ---
    removed_by_owner = {str(d.get("metric")) for d in result.get("curve_decisions") or []
                        if d.get("action") == "remove"}
    for metric in sorted(result.get("fixed_metrics") or {}):
        if metric in removed_by_owner:
            continue
        add("CURVE-11", "metric", metric,
            inputs={"criteria": "config/fixed_criteria.yaml"},
            computed={"criteria_basis": "fixed"}, verdict=VERDICT_PASS,
            recommendation="Scored on EASI's fixed criteria, identical in every region.")

    # --- CURVE-12: discrimination (advisory) ---
    for metric, rec in (result.get("discrimination") or {}).items():
        verdict_word = rec.get("verdict")
        inverted = verdict_word == "inverted"
        add("CURVE-12", "metric", metric,
            computed={"auc_ref_vs_pressure": rec.get("aucRefVsPressure"),
                      "n_ref": rec.get("nRef"), "n_pressure": rec.get("nPressure"),
                      "median_ref_index": rec.get("medianRefIndex"),
                      "median_pressure_index": rec.get("medianPressureIndex"),
                      "auc_r_vs_im": rec.get("aucRvsIm"), "verdict": verdict_word},
            verdict=(VERDICT_REVIEW if inverted else
                     VERDICT_NOT_EVALUATED if verdict_word == "not_evaluable" else VERDICT_PASS),
            review_required=inverted,
            review_triggers=["inverted_discrimination"] if inverted else [])


def build_records(result: dict, manifest: dict, *, timestamp=None) -> list[dict]:
    """Every rule the run actually applied, derived from its own output."""
    run_id = manifest.get("inputsDigest", "")[:23]
    region_code = (result.get("region") or {}).get("code")
    records: list[dict] = []
    # SELECT-04: a reserve candidate whose functions are all scored by other
    # metrics can never enter the portfolio, so nothing about its curve is a
    # decision anyone has to make; its records stay, its review items do not
    moot = set(result.get("moot_reserves") or [])

    def add(*args, **kwargs):
        subject = str(args[2]) if len(args) > 2 else ""
        if moot and kwargs.get("review_required") and set(subject.split("|")) & moot:
            kwargs["review_required"] = False
            kwargs["review_triggers"] = []
            note = ("A reserve candidate not needed: every function it could serve is scored "
                    "by other metrics, so it cannot enter the portfolio (SELECT-04).")
            kwargs["recommendation"] = (f"{kwargs.get('recommendation')} {note}".strip()
                                        if kwargs.get("recommendation") else note)
        records.append(_record(run_id, region_code, *args, timestamp=timestamp, **kwargs))

    # --- REF: the reference definition ---
    tier = result.get("reference_tier")
    ref02 = bool(result.get("ref02_triggered"))
    pressure = result.get("reference_method") == "pressure-screen"
    if pressure:
        _pressure_records(result, add)
    else:
        add("REF-01", "run", "reference_screen",
            inputs={"preset": result.get("screening_method")},
            thresholds={"ref_fallback_floor": methodology.threshold(
                "data_rules.exploratory_n_unstratified"),
                        "ref_fallback_floor_rule": "DATA-05"},
            computed={"reference_tier": tier,
                      "n_retained": (result.get("screening_counts") or {}).get("n_retained")},
            verdict=VERDICT_REVIEW if ref02 else VERDICT_PASS)
    if ref02 and not pressure:
        add("REF-02", "run", "reference_screen",
            computed={"reference_tier": tier,
                      "review_flags": result.get("review_flags") or []},
            verdict=VERDICT_REVIEW, review_required=True,
            recommendation="The least-disturbed pool is below the DATA-05 exploratory "
                           "floor. Accept the best-available reference for this region "
                           "under mandatory review, or acquire more least-disturbed sites.",
            review_triggers=["reference_tier_fallback"])

    # --- DATA-04/05/06: per-metric reference sample size ---
    for metric, info in (result.get("sample_sizes") or {}).items():
        disposition = info.get("disposition")
        rule_id = {"adequate": "DATA-04", "exploratory": "DATA-05"}.get(
            disposition, "DATA-06")
        add(rule_id, "metric", metric,
            inputs={"n_reference": info.get("n")},
            thresholds={"min_n_auto": methodology.threshold("data_rules.min_n_unstratified"),
                        "exploratory_n": methodology.threshold(
                            "data_rules.exploratory_n_unstratified")},
            computed={"disposition": disposition},
            verdict=VERDICT_PASS if disposition == "adequate" else VERDICT_REVIEW,
            review_required=disposition in ("exploratory", "insufficient", "too_few"),
            review_triggers=[] if disposition == "adequate" else [f"n_{disposition}"])

    # --- RED-01: pairwise metric redundancy ---
    redundancy = result.get("redundancy")
    if redundancy is not None and len(redundancy):
        for row in redundancy.itertuples(index=False):
            r = row._asdict()
            flagged = bool(r.get("red01_spearman_flag"))
            add("RED-01", "metric_pair", f"{r['metric_a']}|{r['metric_b']}",
                thresholds={"strong_abs_spearman": methodology.threshold(
                    "redundancy_rules.strong_abs_spearman")},
                computed={"spearman": r.get("spearman"), "pearson": r.get("pearson"),
                          "same_function": bool(r.get("same_function"))},
                verdict=VERDICT_REVIEW if flagged else VERDICT_PASS,
                review_required=flagged and bool(r.get("same_function")),
                review_triggers=["redundant_pair"] if flagged else [])

    # --- STRAT: stratifier screening ---
    for candidate in (manifest.get("stratifiers") or {}).get("candidates") or []:
        add("STRAT-08", "stratifier", candidate["stratification"],
            inputs={"source_column": candidate["source_column"]},
            thresholds={"max_bins": methodology.threshold(
                "stratifier_rules.max_data_derived_bins")},
            computed={"breakpoint_source": candidate["breakpoint_source"],
                      "n_levels_declared": len(candidate["levels_declared"])},
            verdict=VERDICT_PASS,
            recommendation="Breakpoints are declared constants, not sample-derived.")
        add("STRAT-00", "stratifier", candidate["stratification"],
            inputs={"source_column": candidate["source_column"],
                    "levels_populated": candidate["levels_populated"]},
            thresholds={"min_group_size": candidate["min_group_size_rule"],
                        "screening_alpha": methodology.threshold(
                            "stratifier_rules.screening_significance_alpha")},
            computed={"eligible": candidate["eligible"],
                      "level_counts": candidate["level_counts"],
                      "reason": candidate["reason"]},
            verdict=VERDICT_PASS if candidate["eligible"] else VERDICT_NOT_APPLICABLE)

    # A candidate the screening called broad-use, which the run deliberately did
    # not apply to the curves. Invisible before this record existed.
    ranking = (result.get("stratifiers") or {}).get("phase2_ranking")
    if ranking is not None and len(ranking) and "tier" in ranking.columns:
        for row in ranking.itertuples(index=False):
            r = row._asdict()
            if r.get("tier") != "Broad-Use Candidate":
                continue
            add("STRAT-09", "stratifier", str(r["stratification"]),
                thresholds={"screening_alpha": methodology.threshold(
                    "stratifier_rules.screening_significance_alpha")},
                computed={"n_metrics_tested": r.get("n_metrics_tested"),
                          "n_significant": r.get("n_significant"),
                          "consistency_score": r.get("consistency_score"),
                          "tier": r.get("tier")},
                verdict=VERDICT_REVIEW, review_required=True,
                recommendation="Passed STRAT-00 screening. The curves were built "
                               "unstratified; applying it is a human decision.",
                review_triggers=["advisory_stratifier_not_applied"])

    # --- CURVE: family + geometric review ---
    for metric, review in (result.get("curve_review") or {}).items():
        status = (review or {}).get("status")
        flagged = status not in ("auto_ok", None)
        domain_check = (result.get("domain_checks") or {}).get(metric) or {}
        add("CURVE-07", "metric", metric,
            computed={"curve_status": status, "reasons": (review or {}).get("reasons"),
                      # CURVE-07a domain check (2026-08-21, review ECO-1)
                      "domain_min": domain_check.get("domain_min"),
                      "domain_max": domain_check.get("domain_max"),
                      "domain_violations": domain_check.get("violations"),
                      "reviewer_decision": (review or {}).get("decision")},
            verdict=VERDICT_REVIEW if flagged else VERDICT_PASS,
            review_required=flagged,
            review_triggers=["curve_needs_review"] if flagged else [])
    for metric in (result.get("curve_rows") or {}):
        add("CURVE-01", "metric", metric,
            computed={"family": curve_engine.CURVE_FAMILY,
                      "method_version": run_state.CURVE_METHOD_VERSION})

    # --- CURVE-09: measurement-precision floor for two-sided cores (v0.9).
    # Advisory only: a narrow core flags for review, nothing is auto-removed.
    # Only metrics with a declared precision floor are checked.
    floors = methodology.threshold(
        "curve_rules.measurement_precision_floors", {}) or {}
    core_mult = float(methodology.threshold(
        "curve_rules.measurement_precision_core_multiple", 2.0))
    for metric, row in (result.get("curve_rows") or {}).items():
        precision = floors.get(metric)
        if precision is None:
            continue
        mc = (result.get("metric_config") or {}).get(metric) or {}
        if str(mc.get("curve_form") or "") != "optimum":
            continue
        lo = _row_value(row, "functioning_min")
        hi = _row_value(row, "functioning_max")
        if lo is None or hi is None:
            continue
        core = float(hi) - float(lo)
        narrow = core < core_mult * float(precision)
        add("CURVE-09", "metric", metric,
            thresholds={"precision_sd": float(precision),
                        "core_multiple": core_mult},
            computed={"functioning_core_width": core},
            verdict=VERDICT_REVIEW if narrow else VERDICT_PASS,
            review_required=narrow,
            review_triggers=["measurement_precision_floor"] if narrow else [])

    for entry in (result.get("flagged_direction") or []):
        if entry.get("documented"):
            # A human-decided, recorded exclusion is a resolved expectation,
            # not an open review item.
            add("CURVE-05", "metric", entry.get("metric"),
                computed={"reason": entry.get("reason"),
                          "decided_by": entry.get("decided_by")},
                verdict=VERDICT_PASS,
                recommendation="Excluded from scoring by recorded owner decision; "
                               "kept as regional context.")
        else:
            add("CURVE-05", "metric", entry.get("metric"),
                computed={"reason": entry.get("reason")},
                verdict=VERDICT_REVIEW, review_required=True,
                recommendation="No curated ecological direction; no curve was built.",
                review_triggers=["direction_unresolved"])

    # --- SELECT-01: compact portfolio, counted on the BUNDLE when one exists ---
    # The publish gate counts the bundle's metric blocks, and a metric that
    # informs a second function adds a third entry there that the compact
    # portfolio never shows. Both pilots lost a publish attempt to exactly that
    # gap (2026-08-21), so the record and the queue now carry both counts and
    # review on the larger one (2026-08-22).
    bundle_blocks = {}
    for block in ((result.get("bundle") or {}).get("metricsByFunction") or []):
        bundle_blocks[str(block.get("functionId"))] = [
            str(m.get("metricId")) for m in (block.get("metrics") or [])]
    max_per_fn = int(methodology.threshold(
        "metric_portfolio.default_maximum_metrics_per_function"))
    for entry in (result.get("portfolio") or []):
        n_metrics = len(entry.get("metrics") or [])
        fid = entry.get("function_id") or entry.get("function")
        in_bundle = bundle_blocks.get(str(fid))
        n_bundle = len(in_bundle) if in_bundle is not None else None
        n_review = max(n_metrics, n_bundle or 0)
        add("SELECT-01", "function", fid,
            thresholds={"metric_portfolio.default_maximum_metrics_per_function":
                        max_per_fn},
            computed={"n_metrics": n_metrics, "bundle_n_metrics": n_bundle,
                      "bundle_metrics": in_bundle},
            verdict=VERDICT_REVIEW if n_review > max_per_fn else VERDICT_PASS,
            review_required=n_review > max_per_fn,
            review_triggers=["more_than_two_metrics"] if n_review > max_per_fn else [])

    # --- DATA-01/02/03: missingness dispositions over the reference pool ---
    for metric, info in (result.get("missingness") or {}).items():
        disp = info.get("disposition")
        rule_id = {"auto": "DATA-01", "caution": "DATA-02"}.get(disp, "DATA-03")
        add(rule_id, "metric", metric,
            inputs={"missing_fraction": info.get("missing_fraction")},
            thresholds={"max_missingness_auto": methodology.threshold(
                            "data_rules.max_missingness_auto"),
                        "max_missingness_review": methodology.threshold(
                            "data_rules.max_missingness_review")},
            computed={"disposition": disp},
            verdict=VERDICT_PASS if disp == "auto" else VERDICT_REVIEW,
            review_required=disp == "review",
            review_triggers=(["missingness_review"] if disp == "review"
                             else ["missingness_caution"] if disp == "caution" else []),
            recommendation=(
                "Do not auto-recommend this curve (DATA-03)." if disp == "review"
                else "Analyze with caution; confidence takes a penalty." if disp == "caution"
                else None))

    # --- DATA-09: the leakage guard ran before any fold-using diagnostic ---
    if result.get("diagnostics"):
        add("DATA-09", "run", "resampling_guard",
            computed={"one_row_per_site": True},
            recommendation="Repeated site observations would refuse to resample "
                           "(site-grouped folds are not implemented for repeats).")

    # --- CURVE-02/04/06: resampling diagnostics per metric ---
    for metric, diag in (result.get("diagnostics") or {}).items():
        loo = diag.get("loo") or {}
        infl = diag.get("influence") or {}
        boot = diag.get("bootstrap") or {}
        add("CURVE-02", "metric", metric,
            computed={"evaluable": loo.get("evaluable"),
                      "held_out_mean_abs_delta": loo.get("held_out_mean_abs_delta"),
                      "held_out_max_abs_delta": loo.get("held_out_max_abs_delta"),
                      "seed_max_shift_frac": loo.get("seed_max_shift_frac")},
            verdict=VERDICT_PASS if loo.get("evaluable") else VERDICT_REVIEW,
            review_required=not loo.get("evaluable"),
            review_triggers=[] if loo.get("evaluable") else ["cv_not_evaluable"],
            recommendation=None if loo.get("evaluable") else
            "Leave-one-out could not run (sample too small); confidence capped.")
        add("CURVE-04", "metric", metric,
            thresholds={"influence_param_change_frac": methodology.threshold(
                "curve_rules.influence_param_change_frac")},
            computed={"max_param_change_frac": infl.get("max_param_change_frac"),
                      # Scale-free companion in IQR units (2026-08-21, STAT-15).
                      "max_param_change_iqr": infl.get("max_param_change_iqr"),
                      "decision_flip": infl.get("decision_flip"),
                      "driver": infl.get("driver")},
            verdict=VERDICT_REVIEW if infl.get("flagged") else VERDICT_PASS,
            review_required=bool(infl.get("flagged")),
            review_triggers=["influential_site"] if infl.get("flagged") else [])
        add("CURVE-06", "metric", metric,
            computed={"evaluable": boot.get("evaluable"),
                      "structure_stability": boot.get("structure_stability"),
                      "shape_stability": boot.get("shape_stability"),
                      "n_boot": boot.get("n_boot"), "seed": boot.get("seed"),
                      # The resamples the intervals condition on (STAT-4).
                      "n_matched": boot.get("n_matched"),
                      # S-02: the percentile intervals live in provenance (and
                      # the reports), never in the scoring bundle.
                      "point_intervals": boot.get("point_intervals")},
            verdict=VERDICT_PASS if boot.get("evaluable") else VERDICT_REVIEW,
            review_required=not boot.get("evaluable"),
            review_triggers=[] if boot.get("evaluable") else ["no_interval"])

    # --- RED-06/07: pair stability and multiplicity support ---
    if redundancy is not None and len(redundancy) and "fdr_q" in getattr(
            redundancy, "columns", []):
        for row in redundancy.itertuples(index=False):
            r = row._asdict()
            add("RED-07", "metric_pair", f"{r['metric_a']}|{r['metric_b']}",
                thresholds={"fdr_q": methodology.threshold("redundancy_rules.fdr_q")},
                computed={"p_value": r.get("p_value"), "fdr_q": r.get("fdr_q")},
                verdict=VERDICT_PASS,
                recommendation="Supporting evidence only; the effect size stays primary.")
    for pair_key, stab in (result.get("red06_stability") or {}).items():
        add("RED-06", "metric_pair", pair_key,
            thresholds={"bootstrap_stability": methodology.threshold(
                "redundancy_rules.bootstrap_stability")},
            computed={"category": stab.get("category"),
                      "stability": stab.get("stability")},
            verdict=VERDICT_PASS if (stab.get("stability") or 0) >= float(
                methodology.threshold("redundancy_rules.bootstrap_stability"))
            else VERDICT_REVIEW,
            review_required=(stab.get("stability") or 0) < float(
                methodology.threshold("redundancy_rules.bootstrap_stability")),
            review_triggers=[] if (stab.get("stability") or 0) >= float(
                methodology.threshold("redundancy_rules.bootstrap_stability"))
            else ["unstable_redundancy_category"])

    # --- STRAT-01..06: stratifier CV and information-criterion evidence ---
    strat_ev = result.get("strat_evidence")
    if strat_ev is not None and len(strat_ev):
        for row in strat_ev.itertuples(index=False):
            r = row._asdict()
            subject = f"{r['stratification']}|{r['metric']}"
            evaluable = bool(r.get("evaluable"))
            pairs = [("STRAT-01", "strat01_supports", "cv_rmse_improvement"),
                     ("STRAT-02", "strat02_strong", "cv_rmse_improvement"),
                     ("STRAT-03", "strat03_supports", "delta_cv_r2"),
                     ("STRAT-04", "strat04_supports", "delta_aicc"),
                     ("STRAT-05", "strat05_strong", "delta_aicc")]
            for rule_id, flag_key, value_key in pairs:
                supports = r.get(flag_key)
                add(rule_id, "stratifier_metric", subject,
                    computed={value_key: r.get(value_key), "supports": supports},
                    verdict=(VERDICT_PASS if supports
                             else VERDICT_REVIEW if evaluable
                             else VERDICT_NOT_APPLICABLE))
            if r.get("strat06_recurrence") is not None:
                add("STRAT-06", "stratifier_metric", subject,
                    thresholds={"min_resample_support": methodology.threshold(
                        "stratifier_rules.min_resample_support")},
                    computed={"recurrence_above_floor": r.get("strat06_recurrence")},
                    verdict=VERDICT_PASS if (r.get("strat06_recurrence") or 0) >= float(
                        methodology.threshold("stratifier_rules.min_resample_support"))
                    else VERDICT_REVIEW)

    # --- CONF-01/02 and SELECT-02 per metric ---
    for metric, score in (result.get("confidence") or {}).items():
        add("CONF-01", "metric", metric,
            computed={"components": score.get("components"),
                      "total": score.get("total"), "label": score.get("label"),
                      "subtotal": score.get("subtotal"),
                      "deductions_applied": score.get("deductions_applied") or {},
                      "basis": score.get("basis")},
            verdict=VERDICT_PASS,
            recommendation=(f"Confidence {score.get('total')} ({score.get('label')}): a "
                            "reviewer-priority heuristic on development data, not a "
                            "probability."))
        if score.get("caps_applied") or score.get("deductions_applied"):
            add("CONF-02", "metric", metric,
                computed={"caps_applied": score.get("caps_applied") or [],
                          "deductions_applied": score.get("deductions_applied") or {},
                          "subtotal": score.get("subtotal"),
                          "total": score.get("total")},
                verdict=VERDICT_REVIEW if score.get("caps_applied") else VERDICT_PASS,
                review_triggers=["confidence_capped"] if score.get("caps_applied") else [])
    for metric, score in (result.get("metric_scores") or {}).items():
        add("SELECT-02", "metric", metric,
            computed={"components": score.get("components"),
                      "total": score.get("total")},
            verdict=VERDICT_PASS,
            recommendation="Within-function ranking evidence; never an automatic decision.")

    return records


def rules_applied(records) -> list[str]:
    """Derived, so it cannot disagree with what ran. The old hardcoded literal
    listed REF-01 twice and no STRAT rule at all."""
    return sorted({r["rule_id"] for r in records})


def rules_not_evaluated(records) -> list[dict]:
    """The honest counterpart: every catalog rule this run did not apply, with
    its implementation status. Together with rules_applied this accounts for the
    whole catalog, which makes a silently skipped family impossible to miss."""
    applied = set(rules_applied(records))
    out = []
    for rule_id in methodology.rule_ids():
        if rule_id in applied:
            continue
        cat = methodology.rule(rule_id)
        out.append({
            "rule_id": rule_id,
            "family": cat.get("family"),
            "implementation_status": cat.get("implementation_status"),
            "reason": (
                "not implemented in the analysis pipeline"
                if cat.get("implementation_status") == "not_yet_implemented"
                else f"superseded by {cat.get('superseded_by')}"
                if cat.get("implementation_status") == "superseded"
                else "implemented but not applicable to this run"
            ),
        })
    return out


# --------------------------------------------------------------------------- #
# Human review queue (Output Schema 6)
# --------------------------------------------------------------------------- #
#: Trigger -> (tier, blocking, question). Priority is an ordered tier, not a
#: number: the methodology's Review Priority product is not_yet_implemented and
#: inventing a score would be the one thing that makes a run indefensible.
_TRIGGER_TIERS = {
    "reference_tier_fallback": (
        1, True,
        "The least-disturbed pool is below the exploratory floor. Accept the "
        "best-available reference for this ecoregion under mandatory review, or stop "
        "and acquire more least-disturbed sites?"),
    "curve_needs_review": (
        2, False, "Accept this curve as preliminary, adjust it, or drop the metric?"),
    "advisory_stratifier_not_applied": (
        2, False,
        "This stratification is significant across metrics. Split the reference curves "
        "by it, or keep them unstratified?"),
    "n_exploratory": (3, False, "Publish this curve flagged as exploratory?"),
    "n_insufficient": (3, False, "Publish this curve flagged as insufficient, or drop it?"),
    "n_too_few": (3, False, "Drop this metric, or accept a curve below the floor?"),
    "redundant_pair": (
        3, False,
        "These two metrics carry the same signal for one function. Which one is kept?"),
    "more_than_two_metrics": (
        4, False, "This function carries more than two metrics. Approve or trim?"),
    "direction_unresolved": (
        4, False, "Supply a curated ecological direction, or leave this metric out?"),
    # Wave 3 machinery (DATA-03, CURVE-02/04/06, RED-06, CONF-02):
    "missingness_review": (
        3, False,
        "Missing data exceed the DATA-03 threshold. Keep this curve under review, "
        "or exclude the metric for this region?"),
    "cv_not_evaluable": (
        3, False,
        "Leave-one-out could not run on this sample. Accept the capped confidence, "
        "or drop the metric?"),
    "influential_site": (
        3, False,
        "One site moves this curve past the influence threshold. Keep the site, "
        "investigate it, or accept the curve with the flag?"),
    "no_interval": (
        4, False,
        "Bootstrap intervals could not be derived. Accept the curve without an "
        "interval, or drop the metric?"),
    "measurement_precision_floor": (
        3, False,
        "This two-sided Functioning core is narrower than the metric's "
        "documented measurement precision allows. Keep the curve flagged, or "
        "exclude the metric for this region?"),
    "unstable_redundancy_category": (
        3, False,
        "This pair's redundancy category is unstable across resamples. Treat the "
        "pair as redundant, or keep both metrics?"),
    # Methodology 0.14 (COV-01):
    "function_unassessed": (
        2, False,
        "No source of the reference hierarchy supports any candidate metric of this "
        "function. Publish it as a documented gap with its blockers, or stop and supply "
        "a source?"),
    "confidence_capped": (
        4, False,
        "Confidence is capped by rule. Accept the capped score, or address the "
        "capping condition first?"),
    # Methodology 0.12 (REF-05, CURVE-12):
    "borrowed_reference_pool": (
        2, False,
        "This ecoregion holds too few least-disturbed stations for the metric, so the "
        "reference pool borrows comparable stations from a parent ecoregion. Accept the "
        "borrowed pool with its transfer note, or leave the metric out for this region?"),
    "inverted_discrimination": (
        2, False,
        "Pressured stations score higher than reference stations on this curve. Is the "
        "curated direction wrong for this region, is stream size confounding it, or "
        "should the metric be left out?"),
}

#: Triggers an automated run must never publish through while uncovered: the
#: queue's own blocking trigger plus the two per-curve triggers whose absence of
#: a recorded decision is itself the problem. decisions.apply_policy reads this
#: so the policy layer and the queue cannot drift apart.
UNCOVERED_HARD_STOP_TRIGGERS = ("reference_tier_fallback", "curve_needs_review",
                                "direction_unresolved")
assert all(t in UNCOVERED_HARD_STOP_TRIGGERS
           for t, (_, blocking, _q) in _TRIGGER_TIERS.items() if blocking), \
    "every blocking queue trigger must be an uncovered hard stop"


def build_review_queue(records, manifest: dict, *, generated_at=None,
                       priorities: Optional[dict] = None) -> dict:
    """The records that need a human, ordered by tier.

    ``priorities`` (metric -> review_priority dict from the run) attaches the
    numeric impact x uncertainty x novelty score to metric-subject items. Tier
    ordering stays primary: a hard stop outranks any score.
    """
    priorities = priorities or {}
    items = []
    for record in records:
        if not record["review_required"]:
            continue
        trigger = (record["review_triggers"] or ["unspecified"])[0]
        tier, blocking, question = _TRIGGER_TIERS.get(
            trigger, (4, False, "Review this decision. Accept, modify, or reject?"))
        numeric = priorities.get(record["subject"]) if record.get(
            "subject_kind") == "metric" else None
        items.append({
            "item_id": f"{record['rule_id']}:{record['subject']}",
            "priority": tier,
            "priority_basis": {
                "note": (
                    "Ordering is by hard stop then impact tier. The numeric Review "
                    "Priority (impact x uncertainty x novelty) rides per metric item "
                    "where the run computed it."
                ),
                "review_priority": (numeric or {}).get("priority"),
                "review_priority_parts": numeric,
            },
            "decision_id": record["decision_id"],
            "rule_ids": [record["rule_id"]],
            "subject_kind": record["subject_kind"],
            "subject": record["subject"],
            "trigger": trigger,
            "evidence": record["computed"],
            "question": question,
            "allowed_actions": ["accept", "accept_with_conditions", "modify", "reject",
                                "request_additional_analysis"],
            "blocking": blocking,
            "status": "open",
            "reviewer": None, "reviewer_action": None,
            "reviewer_rationale": None, "reviewed_at": None,
        })
    items.sort(key=lambda i: (i["priority"], i["item_id"]))

    by_priority: dict[str, int] = {}
    for item in items:
        by_priority[str(item["priority"])] = by_priority.get(str(item["priority"]), 0) + 1
    return {
        "schemaVersion": REVIEW_QUEUE_SCHEMA_VERSION,
        "inputsDigest": manifest.get("inputsDigest"),
        "generatedAt": generated_at,
        # Methodology section 8: during the pilot phase humans review 100 percent of
        # role, stratifier, curve and selection decisions.
        "protocol": "pilot",
        "counts": {
            "open": len(items),
            "blocking": sum(1 for i in items if i["blocking"]),
            "byPriority": by_priority,
        },
        "items": items,
    }


# --------------------------------------------------------------------------- #
# The per-metric rebuild ledger (rebuild-ledger/1, campaign Round 1)
# --------------------------------------------------------------------------- #
#: One document, in the run folder (``rebuild_ledger.json``), in the version's provenance
#: (``metricLedger``) and in the session (``reference_build.ledger``), that says for every
#: metric and function what a build did with it and why, so a reopened assessment shows its
#: own history and a "refitted" claim is backed by engine evidence, never by equal points.
LEDGER_SCHEMA = "rebuild-ledger/1"
(REFITTED, CARRIED, FIXED, OWNER_SOURCED, OWNER_HELD, REMOVED, UNSUPPORTED, HELD_FOR_REVIEW,
 NOT_EVALUATED) = ("refitted", "carried", "fixed", "owner_sourced", "owner_held", "removed",
                   "unsupported", "held_for_review", "not_evaluated")
LEDGER_DISPOSITIONS = (REFITTED, CARRIED, FIXED, OWNER_SOURCED, OWNER_HELD, REMOVED, UNSUPPORTED,
                       HELD_FOR_REVIEW, NOT_EVALUATED)
#: the dispositions of a curve the version scores: the ledger's selected set (invariant 5)
LEDGER_SELECTED = (REFITTED, CARRIED, FIXED, OWNER_SOURCED)
#: a reference_pool option as the ledger names it
_LEDGER_OPTIONS = {"local": "l3_local", "regional_l3": "l3_regional", "regional_l2": "l2_regional",
                   "regional_nars9": "nars9_regional", "regional_l1": "l1_regional",
                   "3c_matched": "national_3c", "3a_envelope": "national_3a",
                   "modeled": "modeled", "published": "published"}
#: the option a pool status implies when the support record names none
_STATUS_OPTIONS = {"local": "local", "local_relaxed": "regional_l3", "borrowed_l2": "regional_l2",
                   "borrowed_nars9": "regional_nars9", "borrowed_l1": "regional_l1",
                   "national": "3c_matched", "modeled": "modeled", "published": "published"}
#: the rule a pool status was admitted under
_STATUS_RULES = {"local": "REF-05", "local_relaxed": "REF-11", "borrowed_l2": "REF-11",
                 "borrowed_nars9": "REF-11", "borrowed_l1": "REF-11", "national": "REF-12",
                 "modeled": "REF-13", "published": "REF-14"}
#: an owner-chosen source's register kind as the ledger's option
_OWNER_OPTIONS = {"sqt": "sqt", "owner_entered": "owner_entered", "borrowed": "other_assessment",
                  "earlier_version": "earlier_version", "published_benchmark": "published",
                  "carried": "carried"}
#: register kinds only an owner decision (REF-15) produces
_OWNER_ONLY_KINDS = ("sqt", "owner_entered", "borrowed", "earlier_version")
#: the camelCase keys a session's referenceSupport annotation uses for the pool record
_SUPPORT_KEYS = {"regionCode": "region_code", "regionName": "region_name", "nPool": "n_pool",
                 "nComparable": "n_comparable", "nUsable": "n_usable", "nLocal": "n_local",
                 "nHuc12": "n_huc12", "transferRisk": "transfer_risk",
                 "transferNote": "transfer_note", "selfCoverage": "self_coverage",
                 "supportedLevel": "supported_level", "levelsTried": "levels_tried",
                 "optionsTried": "options_tried", "screenDetail": "screen_detail",
                 "carriedFrom": "carried_from", "stationIds": "station_ids",
                 "lithGroups": "lith_groups"}


def _snake_support(rec: Mapping) -> dict:
    """A pool record with snake_case keys, whether it came from the result
    (``PoolDecision.to_dict``) or from a session annotation (camelCase)."""
    return {_SUPPORT_KEYS.get(str(k), str(k)): v for k, v in dict(rec or {}).items()}


def _support_from_build(build: Optional[Mapping]) -> dict:
    """``{metric: pool record}`` from a session's reference build: the annotations'
    ``referenceSupport`` and the carried curves' decisions."""
    out: dict = {}
    for mk, ann in ((build or {}).get("metricAnnotations") or {}).items():
        rs = (ann or {}).get("referenceSupport")
        if rs:
            out[str(mk)] = _snake_support(rs)
    for mk, c in ((build or {}).get("carriedMetrics") or {}).items():
        d = (c or {}).get("decision") or ((c or {}).get("annotations") or {}).get("referenceSupport")
        if d and str(mk) not in out:
            out[str(mk)] = _snake_support(d)
    return out


def _pool_option(sup: Mapping) -> Optional[str]:
    """The reference_pool option a support record was admitted under: the accepted entry
    of ``options_tried``, else the one its status implies."""
    for x in sup.get("options_tried") or []:
        if isinstance(x, Mapping) and (x.get("why") == "accepted" or x.get("accepted") is True):
            return str(x.get("option") or "") or None
    detail = sup.get("screen_detail") or {}
    if isinstance(detail, Mapping) and detail.get("option"):
        return str(detail["option"])
    return _STATUS_OPTIONS.get(str(sup.get("status") or ""))


def _ledger_frame(ledger) -> Optional[pd.DataFrame]:
    if ledger is None:
        return None
    if isinstance(ledger, pd.DataFrame):
        return ledger if len(ledger) else None
    try:
        df = pd.DataFrame(list(ledger))
    except (TypeError, ValueError):
        return None
    return df if len(df) else None


def _pool_block(mk: str, sup: Mapping, option: Optional[str], ledger: Optional[pd.DataFrame],
                code: Optional[str]) -> dict:
    """The stations behind a pool: the ledger's in-pool rows for the option the metric was
    admitted under when the build kept its ledger, else the record's own station ids
    (the sources after the station pools name their donors), else nothing, said so."""
    ids: Optional[list] = None
    donors: list = []
    cycles: list = []
    evidence = "not recorded"
    status = str(sup.get("status") or "")
    if ledger is not None and option and {"metric", "option", "in_pool", "station_key"} <= set(ledger.columns):
        mine = ledger[(ledger["metric"].astype(str) == mk) & (ledger["option"].astype(str) == option)
                      & ledger["in_pool"].astype(bool)]
        if len(mine):
            ids = sorted(mine["station_key"].astype(str).unique().tolist())
            if "l3" in mine.columns:
                donors = sorted({str(x) for x in mine["l3"].dropna().astype(str).unique()
                                 if code is None or str(x) != str(code)})
            if "source_cycle" in mine.columns:
                cycles = sorted({str(x) for x in mine["source_cycle"].dropna().astype(str).unique()},
                                reverse=True)
            evidence = "reference_pool_ledger"
    if ids is None and sup.get("station_ids"):
        ids = sorted(str(x) for x in sup["station_ids"])
        evidence = "reference_support"
    if status in ("national", "modeled") and not donors:
        donors = ["national"]
    level = sup.get("level") or ("national" if status in ("national", "modeled") else None)
    n = sup.get("n_usable")
    return {"level": level, "nStations": int(n) if isinstance(n, (int, float)) and n == n else None,
            "stationIds": ids, "donorRegions": donors, "cyclesUsed": cycles, "evidence": evidence}


def _metric_seed(run_seed, mk: str) -> Optional[int]:
    if run_seed is None:
        return None
    from . import regional_agent as ra
    try:
        return int(ra._metric_seed(int(run_seed), mk))
    except (TypeError, ValueError):
        return None


@functools.lru_cache(maxsize=16)
def _version_digests_cached(vdir: str, stamp: float) -> dict:
    from . import candidates as C
    from . import curve_tiles as ct
    from . import library as lib
    from . import session_io as sio
    fields = sio.decode_session_fields(sio.load_session_payload(Path(vdir) / lib.SESSION_FILE))
    mc = fields.get("metric_config") or {}
    out: dict = {}
    for tile in ct.tiles_for_fields(fields):
        mk = str(tile.get("metric") or "")
        if mk and mk not in out:
            out[mk] = C.tile_basis_digest(tile, mc.get(mk))
    return out


def version_basis_digests(vdir) -> dict:
    """``{metric: basisDigest}`` of every curve a published version's session draws, computed
    as the register computes it, so a later build can say which curves moved. Empty when the
    folder holds no session."""
    from . import library as lib
    p = Path(vdir) / lib.SESSION_FILE
    if not p.is_file():
        return {}
    return dict(_version_digests_cached(str(Path(vdir)), p.stat().st_mtime))


def _previous_digests(carried_from: Optional[Mapping]) -> Optional[dict]:
    """The basis digests of the version a build carries forward from, read from the
    canonical library; None when the build carries from nothing or the version is not
    on this computer."""
    from . import library as lib
    cf = carried_from or {}
    aid, ver = cf.get("assessmentId"), cf.get("fromVersion", cf.get("version"))
    if not aid or ver in (None, ""):
        return None
    try:
        vdir = lib.canonical_root() / "assessments" / str(aid) / f"v{int(ver)}"
    except (TypeError, ValueError):
        return None
    got = version_basis_digests(vdir)
    return got or None


def build_ledger(result: dict, *, manifest: Optional[dict] = None, register: Optional[dict] = None,
                 previous: Optional[dict] = None) -> dict:
    """The ``rebuild-ledger/1`` document of a build: one row per metric and function.

    ``result`` is a stage result (``regional_agent.assemble``) or a dict of session fields
    (a reopened version, an interactive session: ``curve_tiles.fields_from_result`` tells
    them apart). The rows are derived from the candidate register the same records produce
    (``candidates.register_for_result``), so the ledger's selected set, the register's
    selected pairs and the bundle's ``metricsByFunction`` agree by construction; the engine
    evidence behind every ``refitted`` row comes from the result's ``reference_support``
    records, its ``reference_pool_ledger`` (station ids per pool option) and its run seed.
    Where the build kept no ledger (a published version's session), the row says the
    stations were not recorded; where a metric has no evidence record at all (a legacy
    build, a data defect), its disposition is ``not_evaluated`` with the reason.

    ``manifest``: the run manifest (its digest, dates, refit mode, methodology version);
    ``register``: the register already computed for this result; ``previous``:
    ``{metric: basisDigest}`` of the version carried forward from (read from the canonical
    library when None and the build names one).
    """
    from . import candidates as C
    from . import curve_tiles as ct
    from . import owner_curves as oc
    from . import pressure_evidence as pe
    manifest = manifest or {}
    fields = ct.fields_from_result(result)
    reg = register if register is not None else C.register_for_result(result)
    cands = {c["candidateKey"]: c for c in reg.get("candidates") or []}
    names = {fid: name for fid, name, _ in C._functions()}
    region = result.get("region") or fields.get("region_of_applicability") or {}
    code = None if region.get("code") is None else str(region.get("code"))
    build = fields.get("reference_build") or {}
    mc = fields.get("metric_config") or {}
    pressure = bool(build) or bool(result.get("reference_support"))
    support = {str(k): dict(v or {}) for k, v in (result.get("reference_support") or {}).items()}
    for mk, rec in _support_from_build(build).items():
        support.setdefault(mk, rec)
    ledger_df = _ledger_frame(result.get("reference_pool_ledger"))
    seed = result.get("run_seed")
    if seed is None:
        seed = (manifest.get("diagnostics") or {}).get("runSeed")
    method_version = (result.get("curve_method_version")
                      or (manifest.get("methodology") or {}).get("curveMethodVersion")
                      or run_state.CURVE_METHOD_VERSION)
    carried_from = build.get("carriedFrom") or result.get("carried_from") or {}
    if previous is None:
        previous = _previous_digests(carried_from) or {}
    decisions = [dict(d) for d in fields.get("owner_curve_decisions") or [] if isinstance(d, Mapping)]
    held = dict(build.get("ownerHeld") or {})
    for mk, h in (result.get("held_by_owner") or {}).items():
        held.setdefault(str(mk), pe.held_summary((h or {}).get("decision") or {}))
    removed_by = oc.removed(decisions)
    sourced_by = oc.sourced(decisions)
    withheld = {str(w.get("metricKey")): w for w in build.get("insufficientReferenceSupport") or []}
    review = fields.get("curve_review") or {}
    finished = str(manifest.get("finishedAt") or manifest.get("startedAt") or "")[:10] or None
    legacy = not pressure

    rows: list[dict] = []

    def base(mk: str, fid: Optional[str], *, disposition: str, rule: Optional[str], reason: str,
             candidate: Optional[str], basis: Optional[str], option: Optional[str] = None,
             decided_by: str = "automated", who: Optional[str] = None, when: Optional[str] = None,
             pool: Optional[dict] = None, engine: Optional[dict] = None, **extra) -> dict:
        prev = previous.get(mk) if previous else None
        if disposition == CARRIED and prev is None:
            prev = basis                      # carried unchanged: the previous curve is this one
        row = {"metric": mk, "functionId": fid, "function": names.get(fid, fid),
               "disposition": disposition, "basis": (support.get(mk) or {}).get("basis"),
               "option": option, "pool": pool, "engine": engine,
               "basisDigest": basis, "previousBasisDigest": prev,
               "changed": basis != prev, "rule": rule, "reason": reason,
               "decidedBy": decided_by, "who": who or "n/a",
               "when": (str(when)[:10] if when else None) or (finished if decided_by == "automated" else None),
               "candidateKey": candidate}
        row.update(extra)
        return row

    def engine_block(executed: bool, mk: str) -> dict:
        return {"curveMethodVersion": method_version if executed else None,
                "seed": _metric_seed(seed, mk) if executed else None, "executed": bool(executed)}

    for r in reg.get("rows") or []:
        c = cands.get(r["candidateKey"]) or {}
        ident = c.get("identity") or {}
        mk = str((ident.get("subject") or {}).get("id") or "")
        kind = str(ident.get("sourceKind") or "fitted")
        ref = ident.get("sourceRef") or {}
        fid = str(r.get("functionId") or "") or None
        d = r.get("decision") or {}
        status = r.get("status")
        basis = c.get("basisDigest")
        key = r["candidateKey"]
        person = {"decided_by": d.get("decidedBy") or "automated", "who": d.get("who"),
                  "when": d.get("when")}
        placement = d.get("rule")
        if status == C.SELECTED:
            # a curve an owner decision put in: a read-only tile carrying the decision, or a
            # kind only a decision produces (a state SQT curve, an entered or borrowed one)
            owner_source = bool(ref.get("decision")) or kind in _OWNER_ONLY_KINDS
            if owner_source and kind != "fixed":
                option = _OWNER_OPTIONS.get(kind) or _LEDGER_OPTIONS.get(str(ref.get("option") or ""))
                extra = {"placementRule": placement, "decisionRef": d.get("decisionRef")}
                if mk in held:
                    extra["heldFromFit"] = dict(held[mk])
                rows.append(base(mk, fid, disposition=OWNER_SOURCED, rule="REF-15",
                                 reason=str(d.get("reason") or ""), candidate=key, basis=basis,
                                 option=option, decided_by="person", who=d.get("who"),
                                 when=d.get("when"), pool=None, engine=engine_block(False, mk),
                                 **extra))
                continue
            if kind == "carried":
                sup = support.get(mk) or {}
                rows.append(base(mk, fid, disposition=CARRIED, rule="REF-05",
                                 reason=str(d.get("reason") or ""), candidate=key, basis=basis,
                                 option="carried", pool=_pool_block(mk, sup, None, None, code),
                                 engine=engine_block(False, mk), placementRule=placement,
                                 fromVersion=(carried_from.get("fromVersion") or carried_from.get("version")),
                                 **person))
                continue
            if kind == "fixed":
                rows.append(base(mk, fid, disposition=FIXED, rule="CURVE-11",
                                 reason=str(d.get("reason") or ""), candidate=key, basis=basis,
                                 option="fixed", pool=None, engine=engine_block(False, mk),
                                 placementRule=placement, **person))
                continue
            if kind == "published_benchmark":
                sup = support.get(mk) or {}
                rows.append(base(mk, fid, disposition=FIXED, rule="REF-14",
                                 reason=str(d.get("reason") or ""), candidate=key, basis=basis,
                                 option="published", pool=_pool_block(mk, sup, None, None, code),
                                 engine=engine_block(False, mk), placementRule=placement,
                                 criteriaSource=(sup.get("screen_detail") or None), **person))
                continue
            # a curve the engine fitted in this build: local, regional, national or modeled
            sup = support.get(mk)
            if sup is None:
                why = ("The build used the legacy reference method (easi-eci): no reference-support "
                       "evidence pass ran for this metric." if legacy else
                       "The build kept no reference-support record for this metric.")
                rows.append(base(mk, fid, disposition=NOT_EVALUATED, rule=None, reason=why,
                                 candidate=key, basis=basis, pool=None,
                                 engine=engine_block(False, mk), placementRule=placement, **person))
                continue
            st = str(sup.get("status") or "")
            raw = _pool_option(sup)
            rows.append(base(mk, fid, disposition=REFITTED,
                             rule=_STATUS_RULES.get(st, "REF-11" if st.startswith("borrowed") else "REF-05"),
                             reason=str(d.get("reason") or ""), candidate=key, basis=basis,
                             option=_LEDGER_OPTIONS.get(str(raw or ""), raw),
                             pool=_pool_block(mk, sup, raw, ledger_df, code),
                             engine=engine_block(True, mk), placementRule=placement, **person))
        elif status == C.ELIGIBLE:
            rows.append(base(mk, fid, disposition=REMOVED, rule=placement or "SELECT-04",
                             reason=str(d.get("reason") or ""), candidate=key, basis=basis,
                             pool=None, engine=engine_block(False, mk),
                             decisionRef=d.get("decisionRef"), **person))
        elif status == C.EXCLUDED:
            if placement == "REF-06":
                w = withheld.get(mk) or {}
                rows.append(base(mk, fid, disposition=UNSUPPORTED, rule="REF-06",
                                 reason=str(d.get("reason") or ""), candidate=key, basis=None,
                                 pool=None, engine=engine_block(False, mk),
                                 levelsTried=list(w.get("levelsTried") or
                                                  (support.get(mk) or {}).get("levels_tried") or []),
                                 rungsTried=list(w.get("rungsTried") or []), **person))
            elif placement == "CURVE-07":
                rows.append(base(mk, fid, disposition=HELD_FOR_REVIEW, rule="CURVE-07",
                                 reason=str(d.get("reason") or ""), candidate=key, basis=basis,
                                 pool=None, engine=engine_block(mk in support, mk),
                                 reviewItem=f"CURVE-07:{mk}",
                                 trigger=(review.get(mk) or {}).get("status"), **person))
            elif placement == "review":
                entry = review.get(mk) or {}
                rows.append(base(mk, fid, disposition=REMOVED, rule="CURVE-07",
                                 reason=str(entry.get("decision_note") or d.get("reason") or ""),
                                 candidate=key, basis=basis, pool=None,
                                 engine=engine_block(mk in support, mk),
                                 decided_by="person", who=entry.get("decided_by") or d.get("who"),
                                 when=entry.get("decided_at") or d.get("when")))
            # a considered candidate excluded on applicability is the register's, not a
            # metric of this build: no ledger row
        elif status == C.NOT_EVALUATED:
            if placement == "CURVE-07":
                entry = review.get(mk) or {}
                rows.append(base(mk, fid, disposition=HELD_FOR_REVIEW, rule="CURVE-07",
                                 reason=str(d.get("reason") or ""), candidate=key, basis=basis,
                                 pool=None, engine=engine_block(mk in support, mk),
                                 reviewItem=f"CURVE-07:{mk}", trigger=entry.get("status"),
                                 **person))
        elif status == C.FAILED:
            rows.append(base(mk, fid, disposition=NOT_EVALUATED, rule=None,
                             reason=str(d.get("reason") or "The build produced no usable curve."),
                             candidate=key, basis=basis, pool=None,
                             engine=engine_block(mk in support, mk), **person))

    # REF-15, "your choice stands": a metric the owner removed whose station pool would
    # have supported a curve has no tile and no withheld record, so it is stated here
    stated = {(r["metric"], r["functionId"]) for r in rows}
    for mk, summary in sorted(held.items()):
        d = removed_by.get(mk)
        if d is None or mk in sourced_by:
            continue
        for f in pe._functions_of(mk):
            fid = str(f.get("functionId") or "") or None
            if (mk, fid) in stated:
                continue
            rows.append(base(mk, fid, disposition=OWNER_HELD, rule="REF-15",
                             reason=str(d.get("rationale") or ""), candidate=None, basis=None,
                             pool=None, engine=engine_block(False, mk), decided_by="person",
                             who=d.get("recordedBy"), when=d.get("recordedAt"),
                             heldFromFit=dict(summary or {}), decisionRef=d.get("id")))

    rows.sort(key=lambda r: (str(r.get("functionId") or ""), str(r.get("metric") or "")))
    refit = ((manifest.get("inputs") or {}).get("refit") or {}).get("mode") or result.get("refit_mode")
    return jsonable({
        "schema": LEDGER_SCHEMA,
        "region": {"kind": region.get("kind") or "ecoregion", "code": code},
        "build": {
            "inputsDigest": manifest.get("inputsDigest") or result.get("inputs_digest"),
            "refit": refit or "missing",
            "carriedFrom": ({"assessmentId": carried_from.get("assessmentId"),
                             "version": carried_from.get("fromVersion", carried_from.get("version"))}
                            if carried_from.get("assessmentId") else None),
            "methodologyVersion": ((manifest.get("methodology") or {}).get("methodology_version")
                                   or methodology.methodology_version()),
            "protocolSha256": (manifest.get("protocol") or {}).get("sha256"),
        },
        "rows": rows,
    })


def build_ledger_from_version(vdir, *, previous: Optional[dict] = None) -> dict:
    """The ledger of a published version, from its own three files (``assessment.deep.json``,
    ``session.streamcurves.json``, ``provenance.json``): the session's reference build and
    decisions, the pool records the provenance's REF-05/06/11/12/13/14 records kept, the
    sources the ladder refused, the run seed and the digest. No run folder is read, so a
    version published before the station panel entered provenance says its station ids
    were not recorded."""
    from . import library as lib
    from . import session_io as sio
    vdir = Path(vdir)
    fields = sio.decode_session_fields(sio.load_session_payload(vdir / lib.SESSION_FILE))
    bundle_path = vdir / lib.BUNDLE_FILE
    bundle = json.loads(bundle_path.read_text(encoding="utf-8")) if bundle_path.is_file() else None
    prov_path = vdir / lib.PROVENANCE_FILE
    doc = json.loads(prov_path.read_text(encoding="utf-8")) if prov_path.is_file() else {}
    manifest = doc.get("manifest") or {}
    support: dict = {}
    attempts: list = []
    for rec in doc.get("records") or []:
        rule, subject = str(rec.get("rule_id")), str(rec.get("subject"))
        if rec.get("subject_kind") != "metric":
            continue
        computed = rec.get("computed") or {}
        if rule in ("REF-05", "REF-06", "REF-11", "REF-12", "REF-13", "REF-14") and rec.get("verdict") != VERDICT_FAIL:
            support[subject] = dict(computed)
        elif rule == "REF-06":
            support[subject] = dict(computed)
        elif rule in ("REF-12", "REF-13", "REF-14") and rec.get("verdict") == VERDICT_FAIL:
            attempts.append({"metric": subject, "rung": rule, "admitted": False,
                             "why": computed.get("why"), "options": computed.get("options"),
                             "condition": computed.get("condition")})
    result = dict(fields)
    result.update({
        "region": fields.get("region_of_applicability") or manifest.get("region") or {},
        "reference_support": support,
        "ladder_attempts": attempts,
        "run_seed": (manifest.get("diagnostics") or {}).get("runSeed"),
        "curve_method_version": (manifest.get("methodology") or {}).get("curveMethodVersion"),
        "carried_from": (fields.get("reference_build") or {}).get("carriedFrom") or {},
        "bundle": bundle,
        "inputs_digest": manifest.get("inputsDigest"),
    })
    return build_ledger(result, manifest=manifest, previous=previous)


def ledger_rows_for_bundle(bundle: Optional[dict], ledger: Optional[dict]) -> dict:
    """The ledger's selected set beside the bundle's ``metricsByFunction`` pairs (invariant
    5): ``selected`` and ``bundle`` as sorted ``(metricId, functionId)`` pairs, ``equal``,
    and what each side lacks (``missing`` in the ledger, ``extra`` in the ledger)."""
    from .deep_export import deep_slug
    selected = sorted({("spring-" + deep_slug(r.get("metric")), str(r.get("functionId")))
                       for r in (ledger or {}).get("rows") or []
                       if r.get("disposition") in LEDGER_SELECTED and r.get("functionId")})
    in_bundle = sorted({(str(m.get("metricId")), str(f.get("functionId")))
                        for f in (bundle or {}).get("metricsByFunction") or []
                        for m in f.get("metrics") or []})
    return {"selected": selected, "bundle": in_bundle, "equal": selected == in_bundle,
            "missing": sorted(set(in_bundle) - set(selected)),
            "extra": sorted(set(selected) - set(in_bundle))}


def build_provenance(result: dict, manifest: dict, *, timestamp=None,
                     register: Optional[dict] = None) -> dict:
    """Manifest, decision log, review queue, the candidate register and the per-metric
    rebuild ledger as one auditable document. ``register``: the register already computed
    for this result (``candidates.register_for_result``); computed here when absent."""
    from . import candidates as C
    records = build_records(result, manifest, timestamp=timestamp)
    queue = build_review_queue(records, manifest, generated_at=timestamp,
                               priorities=result.get("review_priorities"))
    counts_by_family: dict[str, int] = {}
    counts_by_verdict: dict[str, int] = {}
    for record in records:
        counts_by_family[record["rule_family"]] = (
            counts_by_family.get(record["rule_family"], 0) + 1)
        counts_by_verdict[record["verdict"]] = counts_by_verdict.get(record["verdict"], 0) + 1
    reg = register if register is not None else C.register_for_result(result)
    ledger = build_ledger(result, manifest=manifest, register=reg)
    return jsonable({
        "schemaVersion": PROVENANCE_SCHEMA_VERSION,
        "inputsDigest": manifest.get("inputsDigest"),
        "manifest": manifest,
        "rules_applied": rules_applied(records),
        "rules_not_evaluated": rules_not_evaluated(records),
        "records": records,
        "counts": {
            "total": len(records),
            "by_family": counts_by_family,
            "by_verdict": counts_by_verdict,
            "review_required": sum(1 for r in records if r["review_required"]),
        },
        "reviewQueue": queue,
        "candidateRegister": C.register_document(reg),
        "metricLedger": ledger,
    })


def build_interactive_provenance(bundle: dict, curve_review: Optional[dict], *,
                                 region: Optional[dict] = None,
                                 screening_preset: Optional[str] = None,
                                 publisher: str = "",
                                 session_name: Optional[str] = None,
                                 timestamp=None,
                                 fields: Optional[dict] = None) -> dict:
    """A real, leaner provenance document for an interactive (non-agent) publish.

    Records only what the interactive path genuinely applied: the curve-review
    classifications and decisions (CURVE-07), the approved family (CURVE-01),
    the SELECT-01 portfolio counts from the bundle itself, and the screening
    preset when one was run (REF-01). Everything the interactive session did
    not evaluate lands in rules_not_evaluated, so an interactive version never
    fakes an agent-grade audit chain, and never publishes without any chain.

    ``fields``: the session's decoded fields (``session_io.SESSION_FIELDS`` names). With
    them the document carries the per-metric rebuild ledger (``metricLedger``,
    :func:`build_ledger`) of the session as it is published; without them it says so.
    """
    run_id = f"interactive:{session_name or 'session'}"
    region_code = (region or {}).get("code")
    records: list[dict] = []

    def add(*args, **kwargs):
        records.append(_record(run_id, region_code, *args, timestamp=timestamp, **kwargs))

    if screening_preset:
        add("REF-01", "run", "reference_screen",
            inputs={"preset": screening_preset},
            computed={"path": "interactive"})
    for metric, review in (curve_review or {}).items():
        status = (review or {}).get("status")
        flagged = status not in ("auto_ok", None)
        add("CURVE-07", "metric", metric,
            computed={"curve_status": status,
                      "decision": (review or {}).get("decision"),
                      "reasons": (review or {}).get("reasons")},
            verdict=VERDICT_REVIEW if flagged else VERDICT_PASS,
            review_required=flagged and (review or {}).get("decision") == "pending",
            review_triggers=["curve_needs_review"] if flagged else [])
        add("CURVE-01", "metric", metric,
            computed={"family": curve_engine.CURVE_FAMILY,
                      "method_version": run_state.CURVE_METHOD_VERSION})
    max_per_fn = int(methodology.threshold(
        "metric_portfolio.default_maximum_metrics_per_function"))
    for block in bundle.get("metricsByFunction") or []:
        n_metrics = len(block.get("metrics") or [])
        add("SELECT-01", "function", block.get("functionId"),
            thresholds={"metric_portfolio.default_maximum_metrics_per_function":
                        max_per_fn},
            computed={"n_metrics": n_metrics},
            verdict=VERDICT_REVIEW if n_metrics > max_per_fn else VERDICT_PASS,
            review_required=False,
            review_triggers=["more_than_two_metrics"] if n_metrics > max_per_fn else [])

    manifest = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "mode": "interactive_session",
        "region": region,
        "publisher": publisher,
        "sessionName": session_name,
        "startedAt": timestamp,
        "finishedAt": timestamp,
        "agent": {
            "module": "views.publish (interactive)",
            "aiModel": os.environ.get("STAF_AI_MODEL"),
            "aiTool": os.environ.get("STAF_AI_TOOL"),
            "python": platform.python_version(),
        },
        "methodology": {
            **methodology.config_fingerprints(),
            "curveMethodVersion": run_state.CURVE_METHOD_VERSION,
            "screeningMethodVersion": run_state.SCREENING_METHOD_VERSION,
        },
        # An interactive session does not version-lock its inputs the way the
        # agent does; saying so beats inventing a digest.
        "inputsDigest": None,
        "inputsDigestNote": "interactive session; inputs not version-locked",
    }
    queue = build_review_queue(records, manifest, generated_at=timestamp)
    doc = {
        "schemaVersion": PROVENANCE_SCHEMA_VERSION,
        "inputsDigest": None,
        "manifest": manifest,
        "rules_applied": rules_applied(records),
        "rules_not_evaluated": rules_not_evaluated(records),
        "records": records,
        "counts": {
            "total": len(records),
            "review_required": sum(1 for r in records if r["review_required"]),
        },
        "reviewQueue": queue,
    }
    if fields is not None:
        session_like = {**dict(fields), "bundle": bundle,
                        "region_of_applicability": (fields.get("region_of_applicability")
                                                    or region)}
        doc["metricLedger"] = build_ledger(session_like, manifest=manifest)
    else:
        doc["metricLedgerNote"] = "no session fields were given, so no rebuild ledger"
    return jsonable(doc)


# --------------------------------------------------------------------------- #
# Carrying an agent build's provenance through an interactive publish
# --------------------------------------------------------------------------- #
INTERACTIVE_REVISION_SCHEMA_VERSION = 1


def revision_changes(origin: Optional[dict], *, content_digest: Optional[str] = None,
                     data_fingerprint=None, mapping_digest: Optional[str] = None,
                     region_code: Optional[str] = None,
                     curve_fingerprints: Optional[dict] = None) -> dict:
    """Coarse what-changed flags for an interactive revision, diffed against the
    baselines captured when the originating build was opened.

    None-safe on both sides: a baseline the open could not compute is treated as
    unknown, never as a change. The flags are disclosure, not verification; the
    session itself is the record of what the human did."""
    base = (origin or {}).get("baselines") or {}

    def moved(key: str, now) -> bool:
        was = base.get(key)
        if was is None or now is None:
            return False
        return was != now

    prior = base.get("curve_fingerprints") or {}
    current = curve_fingerprints or {}
    return {
        "contentDigestMatches": bool(content_digest)
        and content_digest == (origin or {}).get("content_digest"),
        "dataFingerprintChanged": moved("data_fingerprint", data_fingerprint),
        "mappingChanged": moved("mapping_digest", mapping_digest),
        "regionChanged": moved("region_code", region_code),
        "curvesAdded": sorted(set(current) - set(prior)),
        "curvesRemoved": sorted(set(prior) - set(current)),
        "curvesChanged": sorted(k for k in set(prior) & set(current)
                                if prior[k] != current[k]),
    }


def build_carried_provenance(source_doc: dict, *, origin: dict, publisher: str,
                             session_name: Optional[str] = None,
                             changes: Optional[dict] = None,
                             timestamp=None) -> dict:
    """The originating run's provenance, republished with one appended
    interactive-revision entry.

    The manifest, decision records, rules_applied and reviewQueue stay
    byte-identical to the source document: the build's audit chain describes
    the build, and the appended ``interactiveRevisions`` entry is what says a
    human edited the assessment afterwards (who, when, based on which staged
    content, and what moved at coarse granularity). Stale per-version stamps
    are stripped the way promote strips them; publish_version re-stamps."""
    doc = copy.deepcopy(source_doc)
    for k in ("version", "updatedAt", "contentDigest"):
        doc.pop(k, None)
    prior = doc.get("interactiveRevisions")
    entries = list(prior) if isinstance(prior, list) else []
    entries.append(jsonable({
        "schemaVersion": INTERACTIVE_REVISION_SCHEMA_VERSION,
        "editedBy": publisher,
        "editedAt": timestamp,
        "sessionName": session_name,
        "basedOn": {
            "kind": (origin or {}).get("kind"),
            "libraryId": (origin or {}).get("library_id"),
            "version": (origin or {}).get("version"),
            "stagedPath": (origin or {}).get("staged_path"),
            "runDir": (origin or {}).get("run_dir"),
            "contentDigest": (origin or {}).get("content_digest"),
            "inputsDigest": source_doc.get("inputsDigest"),
        },
        "note": ("The manifest, decision records and review queue describe the "
                 "originating agent run; this entry records the interactive "
                 "revision published after it."),
        "changes": changes or {},
    }))
    doc["interactiveRevisions"] = entries
    return doc


#: Phrases a templated rationale uses to assert a computed fact, with the
#: computed field and the value the phrase asserts. A rationale that contradicts
#: its own record's evidence is what an audit trail exists to prevent (the
#: published Eastern Corn Belt Plains v2 fast-water influence record read "no
#: decision flip" over decision_flip: true, review VAL-6, 2026-08-21).
_NEGATIONS = ("no", "not", "never", "without", "none")


def _phrase_claims(text: str) -> dict[str, bool]:
    """The computed facts a templated rationale asserts in prose.

    "no decision flip" / "decision flip: no" assert decision_flip False; a bare
    "decision flip" (or "decision flip: yes") asserts True; "no structural
    change" asserts structural_change False. Negation is looked for in the
    few words BEFORE the phrase and in a trailing ": no", never in the text
    after it, which is what the first version of this lint got wrong.
    """
    claims: dict[str, bool] = {}
    low = text.lower()
    for m in re.finditer(r"\b(?:decision[- ])?flip(?:ped|s)?\b", low):
        before = low[max(0, m.start() - 24):m.start()]
        after = low[m.end():m.end() + 8]
        negated = (any(re.search(r"\b" + n + r"\b", before) for n in _NEGATIONS)
                   or bool(re.match(r"\s*[:=]\s*(no|false)\b", after)))
        affirmed_after = bool(re.match(r"\s*[:=]\s*(yes|true)\b", after))
        value = not (negated and not affirmed_after)
        # Several mentions: any negated mention makes the claim False.
        claims["decision_flip"] = claims.get("decision_flip", True) and value
    for m in re.finditer(r"\bstructural change\b", low):
        before = low[max(0, m.start() - 24):m.start()]
        if any(re.search(r"\b" + n + r"\b", before) for n in _NEGATIONS):
            claims["structural_change"] = False
    return claims


def _values_match(computed, expected) -> bool:
    if isinstance(expected, bool) or isinstance(computed, bool):
        return bool(computed) == bool(expected)
    if isinstance(expected, (int, float)) and isinstance(computed, (int, float)):
        return abs(float(computed) - float(expected)) <= 1e-6 * max(
            1.0, abs(float(expected)))
    if expected is None or computed is None:
        return computed is expected
    return str(computed) == str(expected)


def decision_consistency_problems(record: dict, decision: dict) -> list[str]:
    """Every way a decision's stated facts contradict its record's computed
    evidence: explicit ``asserts`` first, then the templated phrases."""
    problems: list[str] = []
    computed = record.get("computed") or {}
    for field, expected in (decision.get("asserts") or {}).items():
        if field not in computed:
            problems.append(f"asserts '{field}', which the record does not compute")
        elif not _values_match(computed.get(field), expected):
            problems.append(f"asserts {field}={expected!r} but the record computed "
                            f"{computed.get(field)!r}")
    text = str(decision.get("rationale") or "")
    for field, asserted in _phrase_claims(text).items():
        if field in computed and computed.get(field) is not None:
            if bool(computed.get(field)) != asserted:
                problems.append(f"the rationale's wording asserts {field}={asserted} "
                                f"but the record computed {field}={computed.get(field)!r}")
    return problems


def apply_reviewer_decisions(provenance_doc: dict, decisions: list[dict],
                             *, default_reviewer: str = "",
                             default_date: Optional[str] = None) -> dict:
    """Fold recorded human adjudications into a provenance document.

    Each decision: ``{rule_id, subject, action, rationale, reviewer?, date?,
    decision_class?, rationale_origin?, asserts?}`` with action in accept /
    accept_with_conditions / modify / reject / request_additional_analysis.
    Matching records get their reviewer fields filled, matching queue items are
    marked resolved, and the queue counts are recomputed, so the published
    document carries the human record the methodology's section 8 requires
    instead of empty reviewer slots. Returns the same document object, modified
    in place, plus a summary of unmatched decisions under
    ``reviewerDecisionsUnmatched`` (never silently dropped).

    Consistency (2026-08-21, review VAL-6): a decision may carry ``asserts``,
    a mapping of computed fields to the values its rationale relies on, and
    every rationale is linted for the templated phrases. Any contradiction
    between a decision and its record's computed evidence raises, so no
    published rationale can contradict the record it sits on.
    """
    allowed = {"accept", "accept_with_conditions", "modify", "reject",
               "request_additional_analysis"}
    by_key: dict[tuple, dict] = {}
    for d in decisions or []:
        action = str(d.get("action") or "").strip()
        if action not in allowed:
            raise ValueError(f"unknown reviewer action {action!r} for "
                             f"{d.get('rule_id')}:{d.get('subject')}")
        by_key[(str(d.get("rule_id")), str(d.get("subject")))] = d

    matched: set[tuple] = set()
    inconsistent: list[str] = []
    for record in provenance_doc.get("records") or []:
        key = (str(record.get("rule_id")), str(record.get("subject")))
        d = by_key.get(key)
        if not d:
            continue
        matched.add(key)
        for problem in decision_consistency_problems(record, d):
            inconsistent.append(f"{key[0]}:{key[1]}: {problem}")
        record["reviewer"] = d.get("reviewer") or default_reviewer
        record["reviewer_action"] = d.get("action")
        record["reviewer_rationale"] = d.get("rationale")
        record["reviewed_at"] = d.get("date") or default_date
        record["reviewer_decision_class"] = d.get("decision_class")
        record["reviewer_rationale_origin"] = d.get("rationale_origin")
        record["reviewer_asserts"] = d.get("asserts")
    if inconsistent:
        raise ValueError(
            "reviewer decisions contradict their records' computed evidence:\n- "
            + "\n- ".join(inconsistent))

    queue = provenance_doc.get("reviewQueue") or {}
    for item in queue.get("items") or []:
        for rid in item.get("rule_ids") or []:
            d = by_key.get((str(rid), str(item.get("subject"))))
            if d:
                item["status"] = "resolved"
                item["reviewer"] = d.get("reviewer") or default_reviewer
                item["reviewer_action"] = d.get("action")
                item["reviewer_rationale"] = d.get("rationale")
                item["reviewed_at"] = d.get("date") or default_date
                break
    open_items = [i for i in queue.get("items") or [] if i.get("status") == "open"]
    if "counts" in queue:
        queue["counts"] = {
            **queue["counts"],
            "open": len(open_items),
            "blocking": sum(1 for i in open_items if i.get("blocking")),
        }

    provenance_doc["reviewerDecisionsUnmatched"] = [
        {"rule_id": k[0], "subject": k[1], "action": by_key[k].get("action")}
        for k in by_key if k not in matched
    ]
    return provenance_doc


def review_queue_markdown(queue: dict) -> str:
    """The queue rendered from the JSON, so the two cannot diverge."""
    counts = queue.get("counts") or {}
    lines = [
        "# Human review queue",
        "",
        f"{counts.get('open', 0)} open item(s), {counts.get('blocking', 0)} blocking.",
        "",
        "Priority is an ordered tier, not a score. Tier 1 blocks release.",
        "",
    ]
    current = None
    for item in queue.get("items") or []:
        if item["priority"] != current:
            current = item["priority"]
            lines += [f"## Priority {current}", ""]
        blocking = " **(blocking)**" if item["blocking"] else ""
        lines.append(f"- `{item['item_id']}`{blocking} - {item['question']}")
        if item["evidence"]:
            evidence = ", ".join(
                f"{k}={round(v, 3) if isinstance(v, float) else v}"
                for k, v in item["evidence"].items()
            )
            lines.append(f"  - evidence: {evidence}")
    if not (queue.get("items") or []):
        lines.append("No item needs review.")
    return "\n".join(lines) + "\n"


def to_frame(records) -> pd.DataFrame:
    """Records as a flat table for the run folder CSV."""
    if not records:
        return pd.DataFrame(columns=list(RULE_RECORD_FIELDS))
    return pd.DataFrame([
        {field: record.get(field) for field in RULE_RECORD_FIELDS} for record in records
    ])
