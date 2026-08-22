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

import logging
import os
import platform
import hashlib
import re
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import methodology, run_state

logger = logging.getLogger("streamcurves")

MANIFEST_SCHEMA_VERSION = 1
PROVENANCE_SCHEMA_VERSION = 2
REVIEW_QUEUE_SCHEMA_VERSION = 1

AGENT_VERSION = "regional-agent-1"

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


def build_run_manifest(result: dict, *, argv=None, started_at=None, finished_at=None,
                       app_root: Path | None = None) -> dict:
    """The reproducibility record for one regional run."""
    app_root = app_root or Path(__file__).resolve().parent.parent
    repo_root = app_root.parent.parent
    git_state = _git_state(repo_root)
    commit, dirty = git_state["commit"], git_state["dirty"]
    region = result.get("region") or {}

    configs = methodology.file_fingerprints([
        app_root / "config" / "nrsa_response_directions.yaml",
        app_root / "config" / "landscape_response_directions.yaml",
        app_root / "config" / "metric_map.yaml",
        app_root / "config" / "staf_functions.json",
        app_root / "config" / "national_stratifier_registry.yaml",
    ])
    inputs = {
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
        },
        "streamcat": (result.get("source_reports") or [None])[0],
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
            "mode": "advisory",
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
    manifest["inputsDigest"] = methodology.inputs_digest({
        "region": region,
        "methodology": manifest["methodology"],
        "configs": configs,
        "nrsa_values": inputs["nrsa_values"],
        "nrsa_sites": inputs["nrsa_sites"],
        "easi_preset": inputs["easi"]["preset"],
        "registry_version": manifest["stratifiers"]["registryVersion"],
    })
    return manifest


# --------------------------------------------------------------------------- #
# Decision records
# --------------------------------------------------------------------------- #
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


def build_records(result: dict, manifest: dict, *, timestamp=None) -> list[dict]:
    """Every rule the run actually applied, derived from its own output."""
    run_id = manifest.get("inputsDigest", "")[:23]
    region_code = (result.get("region") or {}).get("code")
    records: list[dict] = []

    def add(*args, **kwargs):
        records.append(_record(run_id, region_code, *args, timestamp=timestamp, **kwargs))

    # --- REF: reference tier ladder ---
    tier = result.get("reference_tier")
    ref02 = bool(result.get("ref02_triggered"))
    add("REF-01", "run", "reference_screen",
        inputs={"preset": result.get("screening_method")},
        thresholds={"ref_fallback_floor": methodology.threshold(
            "data_rules.exploratory_n_unstratified"),
                    "ref_fallback_floor_rule": "DATA-05"},
        computed={"reference_tier": tier,
                  "n_retained": (result.get("screening_counts") or {}).get("n_retained")},
        verdict=VERDICT_REVIEW if ref02 else VERDICT_PASS)
    if ref02:
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
                        "fdr_q": methodology.threshold(
                            "stratifier_rules.group_difference_fdr_q")},
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
                thresholds={"fdr_q": methodology.threshold(
                    "stratifier_rules.group_difference_fdr_q")},
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
            computed={"family": "iqr-seed piecewise-linear",
                      "method_version": run_state.CURVE_METHOD_VERSION})
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
    for entry in (result.get("portfolio") or []):
        n_metrics = len(entry.get("metrics") or [])
        fid = entry.get("function_id") or entry.get("function")
        in_bundle = bundle_blocks.get(str(fid))
        n_bundle = len(in_bundle) if in_bundle is not None else None
        n_review = max(n_metrics, n_bundle or 0)
        add("SELECT-01", "function", fid,
            computed={"n_metrics": n_metrics, "bundle_n_metrics": n_bundle,
                      "bundle_metrics": in_bundle},
            verdict=VERDICT_REVIEW if n_review > 2 else VERDICT_PASS,
            review_required=n_review > 2,
            review_triggers=["more_than_two_metrics"] if n_review > 2 else [])

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
    "unstable_redundancy_category": (
        3, False,
        "This pair's redundancy category is unstable across resamples. Treat the "
        "pair as redundant, or keep both metrics?"),
    "confidence_capped": (
        4, False,
        "Confidence is capped by rule. Accept the capped score, or address the "
        "capping condition first?"),
}


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


def build_provenance(result: dict, manifest: dict, *, timestamp=None) -> dict:
    """Manifest, decision log and review queue as one auditable document."""
    records = build_records(result, manifest, timestamp=timestamp)
    queue = build_review_queue(records, manifest, generated_at=timestamp,
                               priorities=result.get("review_priorities"))
    counts_by_family: dict[str, int] = {}
    counts_by_verdict: dict[str, int] = {}
    for record in records:
        counts_by_family[record["rule_family"]] = (
            counts_by_family.get(record["rule_family"], 0) + 1)
        counts_by_verdict[record["verdict"]] = counts_by_verdict.get(record["verdict"], 0) + 1
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
    })


def build_interactive_provenance(bundle: dict, curve_review: Optional[dict], *,
                                 region: Optional[dict] = None,
                                 screening_preset: Optional[str] = None,
                                 publisher: str = "",
                                 session_name: Optional[str] = None,
                                 timestamp=None) -> dict:
    """A real, leaner provenance document for an interactive (non-agent) publish.

    Records only what the interactive path genuinely applied: the curve-review
    classifications and decisions (CURVE-07), the approved family (CURVE-01),
    the SELECT-01 portfolio counts from the bundle itself, and the screening
    preset when one was run (REF-01). Everything the interactive session did
    not evaluate lands in rules_not_evaluated, so an interactive version never
    fakes an agent-grade audit chain, and never publishes without any chain.
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
            computed={"family": "iqr-seed piecewise-linear",
                      "method_version": run_state.CURVE_METHOD_VERSION})
    for block in bundle.get("metricsByFunction") or []:
        n_metrics = len(block.get("metrics") or [])
        add("SELECT-01", "function", block.get("functionId"),
            computed={"n_metrics": n_metrics},
            verdict=VERDICT_REVIEW if n_metrics > 2 else VERDICT_PASS,
            review_required=False,
            review_triggers=["more_than_two_metrics"] if n_metrics > 2 else [])

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
    return jsonable({
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
    })


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
