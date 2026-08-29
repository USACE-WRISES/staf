"""CLI entry for the StreamCurves Regional Analysis Agent.

Runs the methodology's Prompt 2 for one EPA Level III ecoregion end to end: real EASI
reference screening, curve building, review classification, the seven standard output
tables, and a preliminary publish into a (staging) library root.

Usage (from repo root, using the shared venv):
    .venv/Scripts/python apps/stream-curves/scripts/run_regional_analysis.py \
        --l3 58 --name "Northeastern Highlands" \
        --out notes/2026-07-23_StreamCurves_Methodology/runs/nh-58 \
        --screen functional

Add --no-screen to skip the live EASI screen (offline smoke only; provenance is stamped
as unscreened, never 'representative').
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

import pandas as pd  # noqa: E402

from streamcurves import curves  # noqa: E402
from streamcurves import methodology  # noqa: E402
from streamcurves import nrsa_dataset  # noqa: E402
from streamcurves import provenance as pv  # noqa: E402
from streamcurves import regional_agent as ra  # noqa: E402
from streamcurves import run_state  # noqa: E402


def _json_default(o):
    for attr in ("item", "tolist"):
        fn = getattr(o, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                pass
    return str(o)


def variable_evaluation_matrix(result: dict) -> pd.DataFrame:
    rows = []
    cr = result["curve_review"]
    miss = result.get("missingness") or {}
    for mk, cfg in result["metric_config"].items():
        entry = cr.get(mk, {})
        rows.append({
            "metric": mk,
            "display_name": cfg.get("display_name"),
            "function": result["column_functions"].get(mk) or "(unmapped)",
            "metric_role": "yes",
            "higher_is_better": cfg.get("higher_is_better"),
            "direction_source": cfg.get("direction_source"),
            "direction_confidence": cfg.get("direction_confidence"),
            "n_reference": result["curve_rows"].get(mk, {}).get("n_reference"),
            "sample_size_disposition": result["sample_sizes"].get(mk, {}).get("disposition"),
            "missing_fraction": miss.get(mk, {}).get("missing_fraction"),
            "missingness_disposition": miss.get(mk, {}).get("disposition"),
            "curve_status": entry.get("status"),
            "review_decision": entry.get("decision"),
            "review_reasons": "; ".join(entry.get("reasons") or []),
            "final_status": "in_scope" if mk in result["intended_metrics"] else "flagged",
        })
    for fd in result["flagged_direction"]:
        documented = bool(fd.get("documented"))
        rows.append({
            "metric": fd["metric"], "display_name": fd.get("display_name"),
            "function": "", "metric_role": "no" if documented else "insufficient_evidence",
            "higher_is_better": None, "direction_source": None,
            "direction_confidence": None, "n_reference": None,
            "curve_status": "not_built",
            "review_decision": "excluded_by_decision" if documented else "pending",
            "review_reasons": (f"excluded by recorded decision: {fd['reason']}"
                               if documented else f"direction {fd['reason']}"),
            "final_status": "excluded_by_decision" if documented else "flagged",
        })
    return pd.DataFrame(rows)


def _reviewer_decision_label(entry: dict, review: dict) -> str:
    """auto_finalized | reviewed_then_finalized | reviewer_finalized |
    removed_from_scope | pending. A curve that auto-finalized but whose review
    items carry recorded adjudications is reviewed_then_finalized, so the
    registry no longer misstates the review path (2026-08-21, review STAT-14)."""
    decision = entry.get("decision") or (
        run_state.DECISION_AUTO if entry.get("status") == run_state.CURVE_STATUS_AUTO_OK
        else run_state.DECISION_PENDING)
    if decision == run_state.DECISION_AUTO and (review or {}).get("adjudicated"):
        return "reviewed_then_finalized"
    return decision


def curve_registry(result: dict) -> pd.DataFrame:
    rows = []
    cr = result["curve_review"]
    diags = result.get("diagnostics") or {}
    confs = result.get("confidence") or {}
    scores = result.get("metric_scores") or {}
    domain_checks = result.get("domain_checks") or {}
    gradients = result.get("deferred_gradients") or {}
    mandatory = result.get("mandatory_review") or {}
    for mk, row in result["curve_rows"].items():
        entry = cr.get(mk, {})
        d = diags.get(mk) or {}
        loo = d.get("loo") or {}
        boot = d.get("bootstrap") or {}
        infl = d.get("influence") or {}
        conf = confs.get(mk) or {}
        mc = result["metric_config"][mk]
        bands = curves.deep_contract_bands(
            row.get("curve_points"), curve_form=curves.curve_form_of(mc),
            higher_is_better=mc.get("higher_is_better") is True,
            domain=curves.metric_domain_of(mc))
        review = mandatory.get(mk) or {}
        components = conf.get("components") or {}
        rows.append({
            "metric": mk,
            "function": result["column_functions"].get(mk) or "(unmapped)",
            "curve_family": curves.CURVE_FAMILY,
            "curve_status": row.get("curve_status"),
            "n_reference": row.get("n_reference"),
            "higher_is_better": result["metric_config"][mk].get("higher_is_better"),
            "functioning_min": row.get("functioning_min"),
            "functioning_max": row.get("functioning_max"),
            "at_risk_min": row.get("at_risk_min"),
            "at_risk_max": row.get("at_risk_max"),
            "not_functioning_min": row.get("not_functioning_min"),
            "not_functioning_max": row.get("not_functioning_max"),
            "reference_tier": result["reference_tier"],
            "sample_size_disposition": result["sample_sizes"].get(mk, {}).get("disposition"),
            # DEEP-contract bands (0.39 / 0.69 breaks), one-sided for monotone
            # curves. The six scalars above keep the R-parity seed-segment
            # semantics; these say what a DEEP score actually means (ECO-8).
            "band_semantics": bands.get("band_semantics"),
            "functioning_text": bands.get("functioning_text"),
            "deep_functioning_min": bands.get("deep_functioning_min"),
            "deep_functioning_max": bands.get("deep_functioning_max"),
            "deep_at_risk_min": bands.get("deep_at_risk_min"),
            "deep_at_risk_max": bands.get("deep_at_risk_max"),
            "deep_at_risk_high_min": bands.get("deep_at_risk_high_min"),
            "deep_at_risk_high_max": bands.get("deep_at_risk_high_max"),
            "deep_not_functioning_min": bands.get("deep_not_functioning_min"),
            "deep_not_functioning_max": bands.get("deep_not_functioning_max"),
            "deep_not_functioning_high_min": bands.get("deep_not_functioning_high_min"),
            "deep_not_functioning_high_max": bands.get("deep_not_functioning_high_max"),
            "domain_min": (domain_checks.get(mk) or {}).get("domain_min"),
            "domain_max": (domain_checks.get(mk) or {}).get("domain_max"),
            "domain_violations": (domain_checks.get(mk) or {}).get("violations"),
            "loo_mean_abs_delta": loo.get("held_out_mean_abs_delta"),
            "loo_max_abs_delta": loo.get("held_out_max_abs_delta"),
            "loo_n_folds": loo.get("n_folds"),
            "loo_structural_change": loo.get("structural_change"),
            "loo_seed_max_shift_frac": loo.get("seed_max_shift_frac"),
            "bootstrap_structure_stability": boot.get("structure_stability"),
            "bootstrap_shape_stability": boot.get("shape_stability"),
            "bootstrap_n_boot": boot.get("n_boot"),
            "bootstrap_n_matched": boot.get("n_matched"),
            "influence_max_param_change": infl.get("max_param_change_frac"),
            "influence_max_param_change_iqr": infl.get("max_param_change_iqr"),
            "influence_flagged": infl.get("flagged"),
            "influence_driver": infl.get("driver"),
            "influence_decision_flip": infl.get("decision_flip"),
            "deferred_gradient": (gradients.get(mk) or {}).get("stratification"),
            "deferred_gradient_improvement": (gradients.get(mk) or {}).get("cv_error_improvement"),
            "confidence_subtotal": conf.get("subtotal"),
            "confidence_deductions": "; ".join(
                f"{k}={v:g}" for k, v in (conf.get("deductions_applied") or {}).items()),
            "confidence_total": conf.get("total"),
            "confidence_label": conf.get("label"),
            "confidence_caps": "; ".join(conf.get("caps_applied") or []),
            **{f"conf_{name}": pts for name, pts in components.items()},
            "metric_score": (scores.get(mk) or {}).get("total"),
            "review_status": entry.get("status"),
            "reviewer_decision": _reviewer_decision_label(entry, review),
            "review_triggers": "; ".join(review.get("triggers") or []),
            "adjudication_ids": "; ".join(review.get("adjudicated") or []),
            "review_open": "; ".join(review.get("open") or []),
        })
    return pd.DataFrame(rows)


def write_review_queue(result: dict, path: Path) -> None:
    lines = ["# Human Review Queue", "",
             f"Ecoregion L3-{result['l3_code']} ({result['name']}). Reference tier: "
             f"**{result['reference_tier']}**.", ""]
    if result["review_flags"]:
        lines += ["## Reference-tier flags (REF)", ""]
        for f in result["review_flags"]:
            lines.append(f"- {f}")
        lines.append("")
    lines += ["## Flagged curves (six-status classifier)", ""]
    cr = result["curve_review"]
    any_flag = False
    for mk in result["flagged_metrics"]:
        e = cr.get(mk, {})
        any_flag = True
        lines.append(f"- **{mk}** ({e.get('status')}): {'; '.join(e.get('reasons') or [])} "
                     f"Reviewer question: accept, modify, or remove this curve from scope?")
    if not any_flag:
        lines.append("- None. Every built curve auto-finalized.")
    lines += ["", "## Metrics with unresolved direction (not built)", ""]
    if result["flagged_direction"]:
        for fd in result["flagged_direction"]:
            lines.append(f"- **{fd['metric']}** ({fd.get('display_name')}): direction "
                         f"{fd['reason']}. Reviewer question: supply the ecological direction "
                         "(or confirm the metric is unsuitable for a monotone curve)?")
    else:
        lines.append("- None.")
    lines += ["", "## Sample-size flags (DATA-04/05/06, provisional floors)", "",
              f"Reference pool: {len(result['retained_site_ids'])} sites "
              f"({result['reference_pool_disposition']}). Auto floor n>=20; exploratory 10-20; "
              "insufficient <10. Flagged curves stay in the preliminary bundle but need review "
              "before certification, and their confidence is capped.", ""]
    if result["sample_size_flags"]:
        for f in result["sample_size_flags"]:
            lines.append(f"- **{f['metric']}** n={f['n']} ({f['disposition']}). "
                         "Reviewer question: is the reference sample adequate for this curve?")
    else:
        lines.append("- None. Every curve meets the auto floor (n>=20).")
    lines += ["", "## Portfolio (SELECT-01)", ""]
    for p in result["portfolio"]:
        if p["select01_flag"]:
            lines.append(f"- **{p['function']}** carries {p['n_metrics']} metrics "
                         f"({', '.join(p['metrics'])}). Reviewer question: approve keeping "
                         "more than two metrics for this function?")
    if not any(p["select01_flag"] for p in result["portfolio"]):
        lines.append("- No function exceeds two metrics.")

    # Every gap paired with the metrics that would close it, so the reviewer has both
    # legitimate resolutions in hand: pull one of these, or record why not. A publish
    # is refused while any of these is still unresolved.
    cov = result.get("coverage") or {}
    lines += ["", "## Uncovered STAF functions", "",
              f"Coverage: **{cov.get('covered', 0)} of {cov.get('total', 20)}** functions"
              + (f"; {cov['excluded']} documented as out of scope" if cov.get("excluded") else "")
              + ".", ""]
    gaps = result.get("uncovered_functions") or []
    if gaps:
        for g in gaps:
            cands = ", ".join(g.get("candidate_metrics") or []) or "none in metric_map.yaml"
            lines.append(f"- **{g['function']}** ({g.get('discipline', '')}) has no metric. "
                         f"Candidates: {cands}. Reviewer question: pull one of these, or "
                         "record a documented exception (reason + justification)?")
    else:
        lines.append("- None. Every STAF function is covered or documented as excluded.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_portfolio(result: dict, path: Path) -> None:
    cov = result.get("coverage") or {}
    lines = ["# Compact Metric Portfolio", "",
             "One primary metric per function; a secondary only when complementary. More "
             "than two per function requires recorded human approval (SELECT-01).", "",
             f"Covers **{cov.get('covered', 0)} of {cov.get('total', 20)}** STAF functions"
             + (f" ({cov['excluded']} documented as out of scope)" if cov.get("excluded") else "")
             + ". Every function is listed below, covered or not.", "",
             "| Discipline | Function | Coverage | Metrics | Primary | SELECT-01 flag |",
             "|---|---|---|---|---|---|"]
    for p in result["portfolio"]:
        lines.append(f"| {p.get('discipline', '')} | {p['function']} | {p.get('coverage', '')} | "
                     f"{', '.join(p['metrics']) or '—'} | {p['primary_metric'] or '—'} | "
                     f"{'REVIEW' if p['select01_flag'] else 'ok'} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_decision_log(result: dict, publish_info: dict | None, path: Path,
                       provenance_doc: dict | None = None) -> None:
    provenance_doc = provenance_doc or {}
    log = {
        "schemaVersion": pv.PROVENANCE_SCHEMA_VERSION,
        "inputsDigest": provenance_doc.get("inputsDigest"),
        "manifestRef": "run_manifest.json",
        "analysis_run": {
            "ecoregion": result["region"],
            "methodology_version": methodology.methodology_version(),
            "curve_method_version": run_state.CURVE_METHOD_VERSION,
            "screening_method": result["screening_method"],
            "screening_counts": result["screening_counts"],
            "reference_tier": result["reference_tier"],
            "ref02_triggered": result["ref02_triggered"],
            "n_candidates": result["n_candidates"],
            "n_retained": len(result["retained_site_ids"]),
            "reference_pool_disposition": result["reference_pool_disposition"],
        },
        # Derived from the records, not a hardcoded list. The old literal named
        # REF-01 twice, claimed DATA-05/06 on runs where every curve was adequate,
        # and named no STRAT rule at all, which is what let a whole missing
        # analysis stage go unnoticed.
        "rules_applied": provenance_doc.get("rules_applied") or [],
        "rules_not_evaluated": provenance_doc.get("rules_not_evaluated") or [],
        "records": provenance_doc.get("records") or [],
        "counts": provenance_doc.get("counts") or {},
        "source_reports": result.get("source_reports") or [],
        "function_coverage": result.get("coverage") or {},
        "uncovered_functions": [
            {"function": g["function"], "function_id": g.get("function_id"),
             "candidate_metrics": g.get("candidate_metrics") or []}
            for g in (result.get("uncovered_functions") or [])
        ],
        "review_flags": result["review_flags"],
        "sample_size_flags": result["sample_size_flags"],
        "decisions": [
            {"metric": mk, "decision_type": "curve",
             "recommendation": e.get("decision"),
             "status": e.get("status"), "reasons": e.get("reasons"),
             "reference_tier": result["reference_tier"]}
            for mk, e in result["curve_review"].items()
        ],
        "publish": publish_info,
    }
    path.write_text(json.dumps(log, indent=2, default=_json_default) + "\n", encoding="utf-8")


def write_report(result: dict, publish_info: dict | None, path: Path) -> None:
    c = result["screening_counts"]
    lines = [
        f"# Regional Analysis: {result['name']} (EPA L3-{result['l3_code']})", "",
        f"Produced by the StreamCurves Regional Analysis Agent "
        f"(methodology {methodology.methodology_version()}). "
        "Draft build for review; not certified.", "",
        "## Reference screening", "",
        f"- Candidates: {result['n_candidates']} NRSA sites in the ecoregion.",
        f"- Screening method: **{result['screening_method']}**"
        + (" (a real EASI screen, not a represented one)."
           if result["screening_method"] == "direct_engine"
           else " (offline test mode, not a real screen)."),
        f"- Counts: {json.dumps(c, default=_json_default)}",
        f"- Reference tier applied: **{result['reference_tier']}**"
        + ("  (REF-02 fallback triggered)" if result["ref02_triggered"] else ""),
        f"- Retained reference sites: {len(result['retained_site_ids'])} "
        f"(**{result['reference_pool_disposition']}** under the provisional DATA floors: "
        f"auto n>={methodology.threshold('data_rules.min_n_unstratified')}, "
        f"exploratory {methodology.threshold('data_rules.exploratory_n_unstratified')}-"
        f"{methodology.threshold('data_rules.min_n_unstratified')}, "
        f"insufficient <{methodology.threshold('data_rules.insufficient_n_unstratified')})", "",
        "## Data sources", "",
    ]
    for rep in (result.get("source_reports") or []):
        status = str(rep.get("status") or "unknown")
        mark = "OK" if status == "ok" else status.upper()
        detail = (f"{rep.get('n_columns', 0)} columns" if status == "ok"
                  else str(rep.get("reason") or "no reason recorded"))
        lines.append(f"- **{rep.get('source', '?')}**: {mark} - {detail}")
        if status != "ok":
            lines.append(
                f"  - Requested but not joined: {', '.join(rep.get('requested') or []) or 'none'}."
                " The functions these carry are UNCOVERED in this run, not merely NA."
            )
    lines += [
        "",
        "## Curves", "",
        f"- Metrics with a curated direction and data: {len(result['metric_config'])}",
        f"- Curves in scope: {len(result['intended_metrics'])} "
        f"({len(result['intended_metrics']) - len(result.get('finalized_metrics') or {})} "
        f"auto-finalized, {len(result.get('finalized_metrics') or {})} by recorded reviewer "
        "finalization)",
        f"- Curves removed from scope by recorded reviewer decision: "
        f"{len(result.get('removed_metrics') or {})}",
        f"- Flagged curves still needing review: {len(result['flagged_metrics'])}",
        f"- Sample-size flags (exploratory/insufficient n): {len(result['sample_size_flags'])}",
        f"- Missingness above the DATA-03 review threshold (data_review): "
        f"{sum(1 for m in (result.get('missingness') or {}).values() if m.get('disposition') == 'review')}",
        f"- Metrics with unresolved direction (not built): "
        f"{sum(1 for fd in result['flagged_direction'] if not fd.get('documented'))}",
        f"- Metrics excluded by recorded decision: "
        f"{sum(1 for fd in result['flagged_direction'] if fd.get('documented'))}", "",
        "## STAF function coverage", "",
    ]
    _cov = result.get("coverage") or {}
    _gaps = result.get("uncovered_functions") or []
    lines.append(f"- Functions covered: **{_cov.get('covered', 0)} / {_cov.get('total', 20)}**"
                 + (f"; {_cov['excluded']} documented as out of scope"
                    if _cov.get("excluded") else ""))
    if _gaps:
        lines.append(f"- Uncovered ({len(_gaps)}): "
                     + ", ".join(g["function"] for g in _gaps)
                     + ". See review_queue.md for the metrics that would close each.")
    else:
        lines.append("- No uncovered functions.")
    lines += [
        "",
        "## Redundancy (RED-01, Spearman-primary)", "",
        f"- Pairs at or above the moderate band: {len(result['redundancy'])}", "",
        "## Publish", "",
    ]
    if publish_info:
        lines.append(f"- Published draft version {publish_info['version']} to "
                     f"`{publish_info['path']}` (staging library).")
    elif result.get("bundle") is not None:
        lines.append("- Not published: the publish gate refused the version "
                     "(coverage, portfolio approval, or writability; see the console log).")
    else:
        lines.append(f"- Not published: {result.get('bundle_error') or 'no bundle'}")
    confs = result.get("confidence") or {}
    if confs:
        labels = [c.get("label") for c in confs.values()]
        lines += [
            "## Confidence (CONF-01/02)", "",
            f"- Curves by band: High {labels.count('High')}, "
            f"Moderate {labels.count('Moderate')}, Low {labels.count('Low')} "
            f"(seeded diagnostics, run seed {result.get('run_seed')}, "
            f"n_boot {result.get('diagnostics_n_boot')}).",
            "- Deductions and caps applied where the rules require them (best-available "
            "reference, sample below minimum, no stability evidence, an unadjudicated "
            "mandatory-review trigger, a deferred stratification gradient). The score is a "
            "reviewer-priority heuristic on development data, never a probability.", "",
        ]
    lines += ["## Honesty notes", "",
              "- Leave-one-site-out stability, bootstrap intervals, influence checks, "
              "and the 0-100 confidence heuristic are within-pool diagnostics on "
              "development data. They are not out-of-sample evidence and not independent "
              "field validation, and nothing here claims validation.",
              "- Compare against pilot_validation/NH58_benchmark.json: this run uses a REAL "
              "reference screen, unlike the represented pilot."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stratifier_evaluation_table(result: dict) -> pd.DataFrame:
    """Output Schema 3, the Stratifier Evaluation Table.

    One row per metric x candidate stratification, plus a row for every candidate
    the region excluded, so the table answers "what was considered here" and not
    only "what was tested". The cross-validated improvement, AICc, and resample
    columns fill from the run's strat_evidence (STRAT-01..06, Wave 3); a pair
    without evidence stays null, never blank-as-zero.
    """
    strat = result.get("stratifiers") or {}
    ledger = strat.get("eligibility")
    if ledger is None or len(ledger) == 0:
        return pd.DataFrame()
    evidence: dict[tuple, dict] = {}
    ev_df = result.get("strat_evidence")
    if ev_df is not None and len(ev_df):
        for erow in ev_df.itertuples(index=False):
            e = erow._asdict()
            evidence[(str(e["metric"]), str(e["stratification"]))] = e
    ranking_tier = {}
    ranking = strat.get("phase2_ranking")
    if ranking is not None and len(ranking) and "tier" in ranking.columns:
        ranking_tier = dict(zip(ranking["stratification"], ranking["tier"]))

    feasibility_flag = {}
    for verification in (strat.get("phase3_verification") or {}).values():
        feas = verification.get("feasibility_results")
        if feas is not None and len(feas):
            feasibility_flag.update(dict(zip(feas["stratification"], feas["feasibility_flag"])))

    rows = []
    excluded = {str(r["stratification"]): r for _, r in ledger.iterrows() if not r["eligible"]}
    for strat_key, row in excluded.items():
        rows.append({
            "metric": None, "stratification": strat_key,
            "candidate_status": "not_evaluated",
            "exclusion_reason": row["exclusion_reason"],
            "level_counts": row["level_counts"],
            "rule_ids": "STRAT-00;STRAT-08", "review_status": "not_applicable",
        })

    candidates = strat.get("phase1_candidates") or {}
    layer1 = strat.get("all_layer1_results") or {}
    for metric in sorted(candidates):
        cand = candidates[metric]
        screened = layer1.get(metric)
        for crow in cand.itertuples(index=False):
            c = crow._asdict()
            sk = str(c["stratification"])
            match = (
                screened[screened["stratification"] == sk].head(1)
                if screened is not None and len(screened) else pd.DataFrame()
            )
            e = evidence.get((metric, sk)) or {}
            rows.append({
                "metric": metric, "stratification": sk,
                "test": match["test"].iloc[0] if len(match) else None,
                "p_value": c.get("p_value"),
                "epsilon_squared": c.get("epsilon_squared"),
                "effect_size_label": c.get("effect_size_label"),
                "min_group_n": c.get("min_group_n"),
                "n_groups": match["n_groups"].iloc[0] if len(match) else None,
                "candidate_status": c.get("candidate_status"),
                "cross_metric_tier": ranking_tier.get(sk),
                "feasibility_flag": feasibility_flag.get(sk),
                "cv_error_improvement": e.get("cv_rmse_improvement"),
                "delta_cv_r2": e.get("delta_cv_r2"),
                "delta_aicc": e.get("delta_aicc"),
                "resample_support": e.get("strat06_recurrence"),
                "rule_ids": "STRAT-00;STRAT-01;STRAT-02;STRAT-03;STRAT-04;"
                            "STRAT-05;STRAT-06;STRAT-08;STRAT-09"
                            if e else "STRAT-00;STRAT-08;STRAT-09",
                "applied_to_curves": False,
                "review_status": "advisory",
            })
    return pd.DataFrame(rows)


def write_outputs(result: dict, out_dir: Path, publish_info: dict | None,
                  provenance_doc: dict | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    variable_evaluation_matrix(result).to_csv(out_dir / "variable_evaluation_matrix.csv", index=False)
    (result["redundancy"] if len(result["redundancy"]) else pd.DataFrame(
        columns=["metric_a", "metric_b"])).to_csv(out_dir / "redundancy_matrix.csv", index=False)
    curve_registry(result).to_csv(out_dir / "curve_registry.csv", index=False)
    # Output Schema 3, and the ledger behind it: every candidate accounted for.
    stratifier_evaluation_table(result).to_csv(
        out_dir / "stratifier_evaluation.csv", index=False)
    strat = result.get("stratifiers") or {}
    if strat.get("eligibility") is not None:
        strat["eligibility"].to_csv(out_dir / "stratifier_eligibility.csv", index=False)
    tier_eval = result.get("tier_evaluation") or []
    if tier_eval:
        pd.DataFrame(tier_eval).to_csv(out_dir / "tier_evaluation.csv", index=False)
    write_portfolio(result, out_dir / "compact_portfolio.md")
    if provenance_doc:
        (out_dir / "run_manifest.json").write_text(
            json.dumps(provenance_doc["manifest"], indent=2, default=_json_default) + "\n",
            encoding="utf-8")
        (out_dir / "review_queue.json").write_text(
            json.dumps(provenance_doc["reviewQueue"], indent=2, default=_json_default) + "\n",
            encoding="utf-8")
        # Rendered from the JSON so the two cannot drift apart.
        (out_dir / "review_queue.md").write_text(
            pv.review_queue_markdown(provenance_doc["reviewQueue"]), encoding="utf-8")
        pv.to_frame(provenance_doc["records"]).to_csv(
            out_dir / "decision_records.csv", index=False)
    else:
        write_review_queue(result, out_dir / "review_queue.md")
    write_decision_log(result, publish_info, out_dir / "decision_provenance_log.json",
                       provenance_doc)
    write_report(result, publish_info, out_dir / "run_report.md")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="StreamCurves Regional Analysis Agent")
    ap.add_argument("--l3", required=True, help="EPA Level III ecoregion code, e.g. 58")
    ap.add_argument("--name", required=True, help="Ecoregion name, e.g. 'Northeastern Highlands'")
    ap.add_argument("--out", required=True, help="Output directory for the run artifacts")
    ap.add_argument("--screen", default="functional",
                    choices=["functional", "at_risk_or_better"],
                    help="Primary reference-screening preset (REF-01)")
    ap.add_argument("--publish-root", default=None,
                    help="Staging library root to publish into (default <out>/library)")
    ap.add_argument("--source-citation", default="")
    ap.add_argument("--no-screen", action="store_true",
                    help="Skip the live EASI screen (offline smoke only)")
    ap.add_argument("--maintainer", default="gtmenichino",
                    help="Maintainer audit name, required by the canonical publish gate")
    ap.add_argument("--rebake-deep", action="store_true",
                    help="After publishing, fold the library's bundles into DEEP's baked registry")
    ap.add_argument("--coverage-exceptions", default=None, metavar="PATH",
                    help="JSON file: a list of documented reasons a STAF function carries "
                         "no metric. Each entry needs functionId, reason, justification, "
                         "recordedBy. Publishing is refused while any of the 20 functions "
                         "is neither covered nor listed here")
    ap.add_argument("--allow-missing-landscape", action="store_true",
                    help="Exit 0 even when a landscape source (StreamCat) failed to join. "
                         "Without this the run exits non-zero, because a silent StreamCat "
                         "outage drops the Hydrology functions from the published bundle")
    ap.add_argument("--n-boot", type=int, default=1000,
                    help="Bootstrap resamples for the CURVE-06/RED-06/STRAT-06 "
                         "diagnostics (default 1000, the same depth as the batch "
                         "runner, so both entry points produce the same evidence; "
                         "seeded from the run identity)")
    ap.add_argument("--approve-portfolio", action="append", default=[],
                    metavar="FUNCTIONID=APPROVER[:NOTE]",
                    help="Recorded human approval for a function carrying more than "
                         "two metrics (SELECT-01). Repeatable. Without one for each "
                         "such function the publish is refused")
    ap.add_argument("--reviewer-decisions", default=None, metavar="PATH",
                    help="JSON list of recorded human adjudications "
                         "({rule_id, subject, action, rationale, reviewer, date}). "
                         "Merged into the provenance records and review queue, so the "
                         "published document carries the human record, not empty slots")
    ap.add_argument("--finalize-metric", action="append", default=[],
                    metavar="METRIC=NOTE",
                    help="Recorded reviewer finalization for a flagged curve (the only "
                         "way a flagged curve publishes). Repeatable; the maintainer "
                         "name is stamped as the actor")
    ap.add_argument("--nrsa-dataset", default=nrsa_dataset.default_build_dataset_id(),
                    choices=nrsa_dataset.available_datasets(),
                    help="which NRSA data to read; the default is the pooled multi-cycle "
                         "archive when this checkout has built it; pass legacy-1819 to "
                         "reproduce the published assessments' inputs")
    ap.add_argument("--nrsa-cycle", action="append", dest="nrsa_cycles",
                    choices=list(nrsa_dataset.CYCLES_NEWEST_FIRST),
                    help="repeatable; limit a pooled run to these survey cycles")
    ap.add_argument("--remove-metric", action="append", default=[],
                    metavar="METRIC=RATIONALE",
                    help="Recorded reviewer decision that takes a built curve out of scope "
                         "for this run only (the per-region door the national registries "
                         "lack). The curve is still built and diagnosed so its evidence is "
                         "on the record. Repeatable; the maintainer name is the actor")
    args = ap.parse_args(argv)

    remove_metrics = {}
    for spec in args.remove_metric:
        mk, _, note = str(spec).partition("=")
        if not mk or not note:
            ap.error(f"--remove-metric needs METRIC=RATIONALE, got {spec!r}")
        remove_metrics[mk.strip()] = note.strip()

    finalize_metrics = {}
    for spec in args.finalize_metric:
        mk, _, note = str(spec).partition("=")
        if not mk or not note:
            ap.error(f"--finalize-metric needs METRIC=NOTE, got {spec!r}")
        finalize_metrics[mk.strip()] = note.strip()

    portfolio_approvals = []
    for spec in args.approve_portfolio:
        fid, _, rest = str(spec).partition("=")
        approver, _, note = rest.partition(":")
        if not fid or not approver:
            ap.error(f"--approve-portfolio needs FUNCTIONID=APPROVER[:NOTE], got {spec!r}")
        portfolio_approvals.append(
            {"functionId": fid.strip(), "approvedBy": approver.strip(),
             "note": note.strip() or None})

    out_dir = Path(args.out)
    publish_root = Path(args.publish_root) if args.publish_root else out_dir / "library"

    started_at = datetime.now(timezone.utc).isoformat()
    print(f"[agent] L3-{args.l3} ({args.name}); screen={args.screen} "
          f"no_screen={args.no_screen}")
    coverage_exceptions = None
    if args.coverage_exceptions:
        coverage_exceptions = json.loads(
            Path(args.coverage_exceptions).read_text(encoding="utf-8"))
        print(f"[agent] loaded {len(coverage_exceptions)} coverage exception(s)")
    decisions = None
    if args.reviewer_decisions:
        decisions = json.loads(Path(args.reviewer_decisions).read_text(encoding="utf-8"))
    result = ra.run(args.l3, args.name, screen_preset=args.screen,
                    nrsa_dataset_id=args.nrsa_dataset, nrsa_cycles=args.nrsa_cycles,
                    source_citation=args.source_citation, do_screen=not args.no_screen,
                    coverage_exceptions=coverage_exceptions, cache_dir=out_dir,
                    diagnostics_n_boot=args.n_boot,
                    finalize_metrics=finalize_metrics or None,
                    finalize_actor=args.maintainer,
                    remove_metrics=remove_metrics or None,
                    reviewer_decisions=decisions,
                    on_event=lambda ev: print(f"[screen] {ev}") if isinstance(ev, str) else None)
    print(f"[agent] retained {len(result['retained_site_ids'])} / {result['n_candidates']} "
          f"(tier {result['reference_tier']}, pool {result['reference_pool_disposition']}); "
          f"curves in scope {len(result['intended_metrics'])}, flagged {len(result['flagged_metrics'])}, "
          f"sample-size flags {len(result['sample_size_flags'])}")

    # A landscape source that fails to join takes the Hydrology functions with it, and
    # streamcat_metrics() returns an empty frame rather than raising -- so without this
    # the run looks identical to a healthy one and quietly publishes a short bundle.
    bad_sources = [r for r in (result.get("source_reports") or [])
                   if str(r.get("status")) not in ("ok", "skipped")]
    for rep in (result.get("source_reports") or []):
        print(f"[agent] source {rep.get('source')}: {rep.get('status')} "
              f"({rep.get('n_columns', 0)} columns)"
              + (f" - {rep['reason']}" if rep.get("reason") else ""))

    strat = result.get("stratifiers") or {}
    ledger = strat.get("eligibility")
    if ledger is not None and len(ledger):
        print(f"[agent] stratifiers: {len(strat.get('eligible') or [])} of {len(ledger)} "
              f"eligible ({', '.join(strat.get('eligible') or []) or 'none'})")
        for _, row in ledger.iterrows():
            if not row["eligible"]:
                print(f"[agent]   excluded {row['stratification']}: {row['exclusion_reason']}")

    # Built before publishing so the published copy carries the same record.
    manifest = pv.build_run_manifest(
        result, argv=list(argv or sys.argv[1:]), started_at=started_at,
        finished_at=datetime.now(timezone.utc).isoformat())
    provenance_doc = pv.build_provenance(result, manifest, timestamp=started_at)
    if decisions is not None:
        provenance_doc = pv.apply_reviewer_decisions(
            provenance_doc, decisions, default_reviewer=args.maintainer,
            default_date=started_at)
        unmatched = provenance_doc.get("reviewerDecisionsUnmatched") or []
        print(f"[agent] reviewer decisions merged: {len(decisions)} supplied, "
              f"{len(unmatched)} unmatched"
              + (f" ({', '.join(u['rule_id'] + ':' + u['subject'] for u in unmatched)})"
                 if unmatched else ""))
    print(f"[agent] provenance: {provenance_doc['counts']['total']} rule record(s), "
          f"{provenance_doc['counts']['review_required']} need review; "
          f"queue open {provenance_doc['reviewQueue']['counts']['open']}; "
          f"inputs {manifest['inputsDigest'][:19]}")

    publish_info = None
    if result.get("bundle") is not None:
        try:
            publish_info = ra.publish(result, publish_root, maintainer=args.maintainer,
                                      provenance=provenance_doc,
                                      portfolio_approvals=portfolio_approvals,
                                      status="draft")
            print(f"[agent] published draft v{publish_info['version']} -> {publish_info['path']}")
        except Exception as exc:  # noqa: BLE001
            print(f"[agent] publish failed: {exc}")
    else:
        print(f"[agent] no bundle to publish: {result.get('bundle_error')}")

    if args.rebake_deep and publish_info is not None:
        from streamcurves import library as lib
        ok, msg = lib.rebake_deep()
        print(f"[agent] rebake DEEP: {'ok' if ok else 'FAILED'} - {msg}")

    write_outputs(result, out_dir, publish_info, provenance_doc)
    print(f"[agent] wrote outputs -> {out_dir}")

    if bad_sources and not args.allow_missing_landscape:
        for rep in bad_sources:
            print(f"[agent] FAILED source {rep.get('source')}: {rep.get('reason')}")
        print("[agent] exiting non-zero: a landscape source did not join, so the "
              "functions it carries are uncovered. Re-run when the service is up, or "
              "pass --allow-missing-landscape to accept the gap.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
