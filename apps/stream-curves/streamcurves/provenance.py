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
import platform
import subprocess
from pathlib import Path

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
def _git_commit(repo_root: Path) -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True,
            text=True, timeout=10, check=True).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root, capture_output=True,
            text=True, timeout=10, check=True).stdout.strip())
        return commit, dirty
    except Exception:  # noqa: BLE001 — provenance must never break a run
        return None, None


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
    commit, dirty = _git_commit(repo_root)
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
            "python": platform.python_version(),
            "packages": _package_versions(),
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
        "determinism": {
            "randomSeeds": {},
            "seedPolicy": (
                "No stochastic step: IQR quantiles, Spearman and Pearson correlation, "
                "Kruskal-Wallis and Benjamini-Hochberg are all deterministic. Any future "
                "bootstrap (RED-06, STRAT-06 and STRAT-07) must record its seed here."
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
        # CONF-01/02 is not_yet_implemented; record the basis and the caps that
        # are genuinely derivable rather than inventing a number.
        "confidence": {"score": None, "label": None, "basis": "categorical_proxy"},
        "review_required": bool(review_required),
        "review_triggers": list(review_triggers or []),
        "timestamp": timestamp,
        "reviewer": None, "reviewer_action": None,
        "reviewer_rationale": None, "reviewed_at": None,
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
            "data_rules.min_n_unstratified")},
        computed={"reference_tier": tier,
                  "n_retained": (result.get("screening_counts") or {}).get("n_retained")},
        verdict=VERDICT_REVIEW if ref02 else VERDICT_PASS)
    if ref02:
        add("REF-02", "run", "reference_screen",
            computed={"reference_tier": tier,
                      "review_flags": result.get("review_flags") or []},
            verdict=VERDICT_REVIEW, review_required=True,
            recommendation="Accept best-available reference for this region, or acquire "
                           "more least-disturbed sites.",
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
        add("CURVE-07", "metric", metric,
            computed={"curve_status": status, "reasons": (review or {}).get("reasons")},
            verdict=VERDICT_REVIEW if flagged else VERDICT_PASS,
            review_required=flagged,
            review_triggers=["curve_needs_review"] if flagged else [])
    for metric in (result.get("curve_rows") or {}):
        add("CURVE-01", "metric", metric,
            computed={"family": "iqr-seed piecewise-linear",
                      "method_version": run_state.CURVE_METHOD_VERSION})
    for entry in (result.get("flagged_direction") or []):
        add("CURVE-05", "metric", entry.get("metric"),
            computed={"reason": entry.get("reason")},
            verdict=VERDICT_REVIEW, review_required=True,
            recommendation="No curated ecological direction; no curve was built.",
            review_triggers=["direction_unresolved"])

    # --- SELECT-01: compact portfolio ---
    for entry in (result.get("portfolio") or []):
        n_metrics = len(entry.get("metrics") or [])
        add("SELECT-01", "function", entry.get("function_id") or entry.get("function"),
            computed={"n_metrics": n_metrics},
            verdict=VERDICT_REVIEW if n_metrics > 2 else VERDICT_PASS,
            review_required=n_metrics > 2,
            review_triggers=["more_than_two_metrics"] if n_metrics > 2 else [])

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
        "Accept best-available reference for this ecoregion, or stop and acquire more "
        "least-disturbed sites?"),
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
}


def build_review_queue(records, manifest: dict, *, generated_at=None) -> dict:
    """The records that need a human, ordered by tier."""
    items = []
    for record in records:
        if not record["review_required"]:
            continue
        trigger = (record["review_triggers"] or ["unspecified"])[0]
        tier, blocking, question = _TRIGGER_TIERS.get(
            trigger, (4, False, "Review this decision."))
        items.append({
            "item_id": f"{record['rule_id']}:{record['subject']}",
            "priority": tier,
            "priority_basis": {
                "note": (
                    "Review Priority (impact x uncertainty x novelty) is CONF / "
                    "not_yet_implemented. Ordering is by hard stop then impact tier, "
                    "not an invented score."
                ),
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
    queue = build_review_queue(records, manifest, generated_at=timestamp)
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
