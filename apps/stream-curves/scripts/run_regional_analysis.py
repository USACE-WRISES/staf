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

from streamcurves import methodology  # noqa: E402
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
            "curve_status": entry.get("status"),
            "review_decision": entry.get("decision"),
            "review_reasons": "; ".join(entry.get("reasons") or []),
            "final_status": "in_scope" if mk in result["intended_metrics"] else "flagged",
        })
    for fd in result["flagged_direction"]:
        rows.append({
            "metric": fd["metric"], "display_name": fd.get("display_name"),
            "function": "", "metric_role": "insufficient_evidence",
            "higher_is_better": None, "direction_source": None,
            "direction_confidence": None, "n_reference": None,
            "curve_status": "not_built", "review_decision": "pending",
            "review_reasons": f"direction {fd['reason']}", "final_status": "flagged",
        })
    return pd.DataFrame(rows)


def curve_registry(result: dict) -> pd.DataFrame:
    rows = []
    cr = result["curve_review"]
    for mk, row in result["curve_rows"].items():
        entry = cr.get(mk, {})
        rows.append({
            "metric": mk,
            "function": result["column_functions"].get(mk) or "(unmapped)",
            "curve_family": "iqr_seed_piecewise_linear",
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
            "review_status": entry.get("status"),
            "reviewer_decision": entry.get("decision"),
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
    lines += ["", "## Sample-size flags (DATA-04/05/06, calibrated v0.3)", "",
              f"Reference pool: {len(result['retained_site_ids'])} sites "
              f"({result['reference_pool_disposition']}). Auto floor n>=20; exploratory 10-20; "
              "insufficient <10. Flagged curves stay in the preliminary bundle but need review "
              "before certification, and their confidence is capped.", ""]
    if result["sample_size_flags"]:
        for f in result["sample_size_flags"]:
            lines.append(f"- **{f['metric']}** n={f['n']} ({f['disposition']}). "
                         "Reviewer question: is the reference sample adequate for this curve?")
    else:
        lines.append("- None. Every curve meets the calibrated auto floor (n>=20).")
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
        "Produced by the StreamCurves Regional Analysis Agent (methodology 0.2-provisional). "
        "Preliminary and for review; not certified.", "",
        "## Reference screening", "",
        f"- Candidates: {result['n_candidates']} NRSA sites in the ecoregion.",
        f"- Screening method: **{result['screening_method']}** (a real EASI screen, not a "
        "represented one).",
        f"- Counts: {json.dumps(c, default=_json_default)}",
        f"- Reference tier applied: **{result['reference_tier']}**"
        + ("  (REF-02 fallback triggered)" if result["ref02_triggered"] else ""),
        f"- Retained reference sites: {len(result['retained_site_ids'])} "
        f"(**{result['reference_pool_disposition']}** under the calibrated DATA floors: "
        "auto n>=20, exploratory 10-20, insufficient <10)", "",
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
        f"- Curves in scope (auto-finalized): {len(result['intended_metrics'])}",
        f"- Flagged curves (need review): {len(result['flagged_metrics'])}",
        f"- Sample-size flags (exploratory/insufficient n): {len(result['sample_size_flags'])}",
        f"- Metrics with unresolved direction (not built): {len(result['flagged_direction'])}", "",
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
        lines.append(f"- Published preliminary version {publish_info['version']} to "
                     f"`{publish_info['path']}` (staging library).")
    else:
        lines.append(f"- Not published: {result.get('bundle_error') or 'no bundle'}")
    lines += ["", "## Honesty notes", "",
              "- Cross-validation, bootstrap stability, and the 0-100 confidence score are "
              "not implemented; their absence routes decisions to human review (methodology).",
              "- Compare against pilot_validation/NH58_benchmark.json: this run uses a REAL "
              "reference screen, unlike the represented pilot."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stratifier_evaluation_table(result: dict) -> pd.DataFrame:
    """Output Schema 3, the Stratifier Evaluation Table.

    One row per metric x candidate stratification, plus a row for every candidate
    the region excluded, so the table answers "what was considered here" and not
    only "what was tested". Columns the methodology lists but no rule computes yet
    (cross-validated improvement, AICc, resample stability) are present and
    explicitly not_evaluated rather than blank, because blank reads as zero.
    """
    strat = result.get("stratifiers") or {}
    ledger = strat.get("eligibility")
    if ledger is None or len(ledger) == 0:
        return pd.DataFrame()
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
                # Declared and null, never blank: the methodology lists them and
                # they are not_yet_implemented.
                "cv_error_improvement": None,
                "delta_aicc": None,
                "resample_support": None,
                "rule_ids": "STRAT-00;STRAT-08;STRAT-09",
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
    args = ap.parse_args(argv)

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
    result = ra.run(args.l3, args.name, screen_preset=args.screen,
                    source_citation=args.source_citation, do_screen=not args.no_screen,
                    coverage_exceptions=coverage_exceptions, cache_dir=out_dir,
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
    print(f"[agent] provenance: {provenance_doc['counts']['total']} rule record(s), "
          f"{provenance_doc['counts']['review_required']} need review; "
          f"inputs {manifest['inputsDigest'][:19]}")

    publish_info = None
    if result.get("bundle") is not None:
        try:
            publish_info = ra.publish(result, publish_root, maintainer=args.maintainer,
                                      provenance=provenance_doc)
            print(f"[agent] published preliminary v{publish_info['version']} -> {publish_info['path']}")
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
