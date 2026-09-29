"""Completed, bounded summaries for the local comparison page."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from . import ALTERNATIVES, REFERENCE_ID, STUDY_VERSION, is_round4, study_candidates
from . import outcomes as oc
from . import round4 as r4
from .io import now, read_json, sha, write_json
from builder import REPO_ROOT

TARGETS = ("reference_2013", "t__bent_mmi")
CHANGED = {"light_thermal_regime", "habitat_provision", "carbon_processing", "low_flow_baseflow_dynamics"}


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def csv_rows(path):
    return clean(pd.read_csv(path).to_dict("records")) if path.exists() and path.stat().st_size else []


def source_inventory(root, study):
    """Bind every captured external source and implementation file for local review."""
    preserved = read_json(study / "snapshot/manifest.json")["files"]
    captured = list(preserved)
    captured.extend({**row, "path": str(study / row["relative_path"])} for row in preserved)
    for path in sorted((study / "stages").glob("*.json")):
        if path.stem == "report":
            continue
        receipt = read_json(path)
        captured.extend(receipt.get("inputs", []))
        captured.extend(receipt.get("source_inputs", []))
        if path.stem == "evidence":
            captured.extend(row for row in receipt.get("outputs", []) if Path(row["path"]).suffix == ".parquet")
    for path in (study / "results/field_summary.json", study / "spatial/results/field_summary.json"):
        if path.exists():
            captured.extend(read_json(path).get("inputs", []))
    acquisition = read_json(study / "acquisition/manifest.json")
    captured.extend(row for row in acquisition.get("sources", []) if row.get("path") and row.get("sha256"))
    captured.extend(row for row in acquisition.get("daily", []) if row.get("path") and row.get("sha256"))
    from .io import info
    captured.extend(info(path) for path in sorted(Path(__file__).parent.glob("*.py")))
    captured.extend(info(REPO_ROOT / "apps/easi" / name) for name in ("local_review.py", "alternative_review.py"))
    # a Round 4 study's candidate packages are declared inputs that live outside the data
    # root and the workspace (the manifest's candidates block records each package's folder,
    # zip sha256 and package digest), so each package folder is a declared root of its own
    candidate_roots = []
    manifest_path = study / "manifest.json"
    if manifest_path.is_file():
        for entry in read_json(manifest_path).get("candidates") or []:
            source = entry.get("source")
            if entry.get("role") == "candidate" and source:
                folder = Path(source)
                folder = folder.parent if folder.suffix else folder
                candidate_roots.append((str(entry.get("id")), folder.resolve()))
    by_path = {}
    for row in captured:
        path = Path(row["path"]).resolve()
        stat = path.stat()
        expected = row.get("bytes", row.get("size"))
        if stat.st_size != expected or stat.st_mtime_ns != row["mtime_ns"]:
            raise RuntimeError(f"Captured study source changed: {path}")
        candidate_scope = next(((family, folder) for family, folder in candidate_roots
                                if path.is_relative_to(folder)), None)
        if path.is_relative_to(root.resolve()):
            scope, relative = "data", path.relative_to(root.resolve()).as_posix()
        elif path.is_relative_to(REPO_ROOT.resolve()):
            scope, relative = "workspace", path.relative_to(REPO_ROOT.resolve()).as_posix()
        elif candidate_scope is not None:
            scope, relative = f"candidate:{candidate_scope[0]}", path.relative_to(candidate_scope[1]).as_posix()
        else:
            raise RuntimeError(f"Source is outside the study's declared roots: {path}")
        previous = by_path.get((scope, relative), {})
        by_path[scope, relative] = {"scope": scope, "path": relative, "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "sha256": row.get("sha256") or previous.get("sha256") or sha(path)}
    rows = [by_path[key] for key in sorted(by_path)]
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    write_json(study / "source-inventory.json", {"files": rows, "source_digest": digest, "count": len(rows)})
    return rows, digest


def recommendation(field, spatial, availability_unchanged=True):
    """Apply the declared noninferiority rule without promoting a method."""
    def supported_review(row):
        bounds = (row.get("ci_low"), row.get("ci_high"))
        return (row.get("support_floor_met") and row.get("boot_requested") == 1000
                and 800 <= row.get("boot_valid", 0) <= 1000
                and all(value is not None and np.isfinite(value) for value in bounds)
                and bounds[0] <= bounds[1])

    decisions = []
    for definition in ALTERNATIVES[1:]:
        aid = definition["id"]
        aggregate = [r for r in field if r.get("alternative_id") == aid and r.get("cohort") == "latest_visit1"
                     and r.get("region") == "US" and r.get("function") == "eci" and r.get("target") in TARGETS]
        aggregate = [r for r in aggregate if str(r.get("statistic", "")).startswith("auc")]
        proven = len(aggregate) == 2 and {r["target"] for r in aggregate} == set(TARGETS) and all(
            r.get("noninferiority_status") == "noninferior" for r in aggregate)
        deterioration, review_findings = [], []
        for design, rows in (("frozen", field), ("watershed-held-out", spatial)):
            for row in rows:
                cohort = row.get("cohort", "")
                latest = cohort == "latest_visit1" if design == "frozen" else str(cohort).endswith("latest_visit1")
                if (row.get("alternative_id") != aid or not latest
                        or row.get("function") not in CHANGED | {"eci"} or not supported_review(row)):
                    continue
                statistic = str(row.get("statistic", ""))
                if statistic.startswith("auc") and row["ci_high"] < -.01:
                    deterioration.append({**row, "comparison_design": design,
                        "reason": "The supported paired AUC interval is entirely below the -0.01 margin."})
                elif statistic in ("spearman", "weighted_kappa") and row["ci_high"] < 0:
                    review_findings.append({**row, "comparison_design": design,
                        "reason": "The supported paired interval shows lower signed correlation or class agreement; review is unresolved and no equivalence margin is assumed."})
        heldout = [r for r in spatial if r.get("alternative_id") == aid and str(r.get("cohort", "")).endswith("latest_visit1")
                   and r.get("region") == "US" and r.get("function") == "eci" and r.get("target") in TARGETS]
        conflicting = [r for r in deterioration if r["comparison_design"] == "watershed-held-out"]
        # Missing held-out aggregate evidence prevents a positive recommendation.
        heldout_complete = len(heldout) == 2 and all(r.get("noninferiority_status") == "noninferior"
            and r.get("ci_low") is not None and r.get("ci_high") is not None
            and r.get("alternative") is not None and r.get("boot_valid", 0) >= 800 for r in heldout)
        eligible = proven and availability_unchanged and not deterioration and not review_findings and heldout_complete
        reasons = []
        if not proven:
            reasons.append("Both frozen aggregate endpoints have not demonstrated AUC noninferiority.")
        if not heldout_complete:
            reasons.append("Both watershed-held-out aggregate endpoints have not demonstrated supported AUC noninferiority.")
        if not availability_unchanged:
            reasons.append("Rating availability changed.")
        if deterioration:
            reasons.append("Supported function or regional AUC deterioration requires retaining Alternative 1.")
        if review_findings:
            reasons.append("Supported declines in signed correlation or class agreement require review before simplification.")
        benthic = next((r.get("alternative") for r in aggregate if r["target"] == "t__bent_mmi"), None)
        decisions.append({"alternative_id": aid, "curve_count": definition["curve_count"], "eligible": eligible,
                          "aggregate_noninferiority": proven, "availability_unchanged": availability_unchanged,
                          "deterioration_findings": deterioration, "spatial_conflicts": conflicting,
                          "unresolved_review_findings": review_findings, "reasons": reasons,
                          "spatial_support_available": heldout_complete, "benthic_auc": benthic})
    eligible = [r for r in decisions if r["eligible"]]
    eligible.sort(key=lambda r: (r["curve_count"], -(r["benthic_auc"] or 0)))
    preferred = eligible[0]["alternative_id"] if eligible else "alternative-1"
    return {"recommended": preferred, "decision": "Consider the eligible simplification after local inspection" if eligible else
            "Retain Alternative 1 because the simplification evidence is insufficient or conflicting",
            "criteria": "Both aggregate paired 95% AUC lower bounds >= -0.01 in frozen and watershed-held-out comparisons; unchanged availability; supported latest-visit function and region review in both designs. AUC declines beyond -0.01 block simplification; signed-correlation or class-agreement intervals entirely below zero require unresolved review. Review findings require at least 800 valid draws out of 1,000. Rank eligible alternatives by curve count then benthic AUC",
            "candidates": decisions, "automatic_scoring_change": False}


def labels(series, eci=False):
    if not eci:
        return series.fillna("Not rated")
    return series.map(lambda x: "Not rated" if pd.isna(x) else "Non-Functioning" if x <= .39 else "Functioning-at-Risk" if x <= .69 else "Functioning")


def transition(before, after, weights, label, design, population_n):
    frame = pd.DataFrame({"from": before, "to": after, "n": weights})
    rows = frame.groupby(["from", "to"], dropna=False)["n"].sum().reset_index()
    rows["share"] = rows.n / rows.n.sum()
    return {"label": label, "design": design, "weighted": design == "weighted-sample",
            "sample_n": len(frame), "population_n": population_n, "rows": clean(rows.to_dict("records"))}


def curve_changes(base_artifact, artifact):
    """The curve sets and curves an arm adds, removes or replaces against the base."""
    changes = []
    base_sets, sets = base_artifact.get("sets") or {}, artifact.get("sets") or {}
    for set_id in sorted(base_sets.keys() | sets.keys()):
        before, after = (base_sets.get(set_id) or {}).get("curves") or {}, (sets.get(set_id) or {}).get("curves") or {}
        if set_id not in base_sets:
            changes.append({"set": set_id, "stratum": None, "change": "set added", "curves": sorted(after),
                            "quantity": sets[set_id].get("quantity"), "split": sets[set_id].get("split")})
            continue
        if set_id not in sets:
            changes.append({"set": set_id, "stratum": None, "change": "set removed"})
            continue
        for key in sorted(before.keys() | after.keys()):
            if before.get(key) != after.get(key):
                changes.append({"set": set_id, "stratum": key,
                                "change": "removed" if key not in after else "added" if key not in before else "replaced"})
    return changes


def _scores(study, arm_id):
    return pq.read_table(study / f"scores/{arm_id}.parquet").to_pandas().set_index("comid")


def build_round4(root: Path, study: Path, manifest: dict):
    """A 1.2.0 study's summary: per candidate the addendum's P1 to P6 and the decision by
    its margins; the base's own P1 value intervals, P3 availability and P6 beside them."""
    verification = read_json(study / "scores/verification.json")
    if verification.get("status") != "passed":
        raise RuntimeError("A passing replay and candidate-scope receipt is required")
    arms = study_candidates(manifest)
    cohorts = pq.read_table(study / "cohorts/reaches.parquet").to_pandas().set_index("comid")
    sampling = read_json(study / "cohorts/sampling.json")
    field = csv_rows(study / "results/field_agreement.csv")
    spatial = csv_rows(study / "spatial/results/field_agreement.csv")
    diagnostics = {key: csv_rows(study / f"results/field_{filename}.csv") for key, filename in
                   (("low_flow", "lowflow"), ("agriculture", "agriculture"), ("model", "model"))}
    stability = read_json(study / "set-stability/result.json") if (study / "set-stability/result.json").is_file() else {}
    receipts = {p.stem: read_json(p) for p in sorted((study / "stages").glob("*.json")) if p.stem != "report"}
    scores_elapsed = (receipts.get("scores") or {}).get("elapsed_seconds")
    base_artifact = read_json(study / "candidates" / REFERENCE_ID / "app-data/reference-curves.json")
    metrics = read_json(study / "candidates" / REFERENCE_ID / "app-data/easi-metrics.json")
    base = _scores(study, REFERENCE_ID)
    base_record = read_json(study / f"scores/{REFERENCE_ID}.json") if (study / f"scores/{REFERENCE_ID}.json").is_file() else {}
    base_catalog = read_json(study / "candidates" / REFERENCE_ID / "app-data/screening-methods.json")
    base_summary = {"id": REFERENCE_ID, "role": "base", "label": arms[0].get("label"),
                    "package_digest": arms[0].get("package_digest"), "method_version": base_record.get("method_version"),
                    "curve_count": arms[0].get("curve_count"),
                    "P1_value_ci": oc.p1(field, spatial, REFERENCE_ID) if len(arms) == 1 else None,
                    "P3": oc.p3(base, base, cohorts), "P6": oc.p6(base_catalog, metrics, base_record, scores_elapsed),
                    "route": {k: v for k, v in base_record.items() if k in ("route", "package_digest", "asset_fallbacks", "seconds")}}
    candidates = []
    for arm in arms[1:]:
        aid = arm["id"]
        spec = r4.arm_spec(arm)
        artifact = read_json(study / "candidates" / aid / "app-data/reference-curves.json")
        catalog = read_json(study / "candidates" / aid / "app-data/screening-methods.json")
        scores = _scores(study, aid)
        record = read_json(study / f"scores/{aid}.json") if (study / f"scores/{aid}.json").is_file() else {}
        functions = r4.function_ids(spec)
        result = {"P1": oc.p1(field, spatial, aid), "P2": oc.p2(field, spatial, aid, functions),
                  "P3": oc.p3(base, scores, cohorts), "P4": oc.p4(stability, aid),
                  "P5": oc.p5(scores, cohorts) if aid == "E8" or "functions_rated" in scores.columns and scores["functions_rated"].notna().any()
                  else {"status": "not applicable", "note": "E8 only"},
                  "P6": oc.p6(catalog, metrics, record, scores_elapsed)}
        decision = oc.decide(spec, result)
        entry = {"id": aid, "family": arm.get("family"), "label": arm.get("label"),
                 "change": spec.get("change"), "hypothesis": spec.get("hypothesis"),
                 "mechanism": spec.get("mechanism"), "primary_outcome": spec.get("primary_outcome"),
                 "decision_rule": spec.get("decision"), "coverage_effect": spec.get("coverage_effect"),
                 "functions": functions, "respecified": arm.get("respecified"),
                 "refinement": spec.get("refinement"),
                 "comparison_scope": arm.get("comparison_scope"),
                 "package_digest": arm.get("package_digest"), "method_version": arm.get("method_version"),
                 "evaluator_digest": arm.get("evaluator_digest"), "curve_count": arm.get("curve_count"),
                 "curve_changes": curve_changes(base_artifact, artifact),
                 "curve_set_findings": arm.get("curve_set_findings") or {},
                 "route": {k: v for k, v in record.items() if k in ("route", "package_digest", "asset_fallbacks", "seconds")},
                 **result, "decision": decision}
        if spec.get("composition"):
            # the finalist: the components, each one's rule on the composed arm, the
            # single-family studies the snapshot recorded, and the interaction check
            studies = arm.get("component_studies") or {}
            entry["composition"] = {
                "components": list(spec["composition"]),
                "component_rules": decision.get("components"),
                "component_studies": {fid: {k: cs.get(k) for k in ("study_id", "summary", "summary_sha256", "input_digest",
                                                                   "package_digest", "method_version", "functions",
                                                                   "decision_rule", "decision")}
                                      for fid, cs in studies.items()},
                "interaction": oc.interaction(result["P1"], result["P3"], studies),
                "rule": decision.get("rule")}
        candidates.append(entry)
    summary = clean({"schema_version": 2, "study_id": study.name, "input_digest": manifest["input_digest"],
                     "runner_version": STUDY_VERSION, "protocol_version": (manifest.get("protocol") or {}).get("version"),
                     "addendum": manifest.get("addendum"), "base_id": manifest.get("base_id"),
                     "base_evaluator": manifest.get("base_evaluator"), "seeds": manifest.get("seeds"),
                     "command_line": manifest.get("command_line"), "reference_id": REFERENCE_ID,
                     "deciding_cohort": r4.DECIDING_COHORT, "reported_cohorts": list(r4.REPORTED_COHORTS),
                     "stored_analysis": manifest.get("stored_analysis"),
                     "base_scores": ({k: v for k, v in manifest["base_scores"].items() if k != "files"}
                                     | {"files_n": len(manifest["base_scores"].get("files", []))}
                                     if manifest.get("base_scores") else None),
                     "scoring_routes": manifest.get("scoring_routes"), "margins": r4.MARGINS,
                     "margins_in_addendum": r4.margins_in_addendum(),
                     "base": base_summary, "candidates": candidates,
                     "decisions": {c["id"]: {"adopted": c["decision"]["adopted"], "rule": c["decision_rule"],
                                            "reasons": c["decision"]["reasons"],
                                            **({"composition": c["composition"]["components"],
                                                "components": {fid: r["passes"] for fid, r in
                                                               (c["composition"]["component_rules"] or {}).items()}}
                                               if c.get("composition") else {})} for c in candidates},
                     "verification": verification, "observation_ledger": read_json(study / "cohorts/cohort_summary.json"),
                     "acquisition": read_json(study / "acquisition/summary.json") if (study / "acquisition/summary.json").exists() else {},
                     "diagnostics": {key: [r for r in rows if r.get("region") == "US"] for key, rows in diagnostics.items()},
                     "stability": {k: v for k, v in stability.items() if k in ("applicable", "not_applicable", "note", "settings")},
                     "runtime_seconds": {step: rec.get("elapsed_seconds") for step, rec in receipts.items()},
                     "limitations": [
                         "No scoring method is changed automatically: a decision here is an adoption candidate for the owner (D4c, Round 5).",
                         "P1, P2 and the field agreements support proxy meaning and association, never validation; T3 is exploratory.",
                         "The BH q-values across the eight primaries are the finalist step's (round4_finalist.py), not this study's.",
                         "2023-24 results are retrospective temporal evaluation because the project has already used those observations.",
                         "Frozen alternatives and watershed-excluded refitted reference-method validation are reported separately.",
                         "Stored-footprint totals estimated from the fixed 100,000-reach sample are not observed national counts.",
                         "Reference-sampling uncertainty is not independent biological validation."]})
    write_json(study / "summary.json", summary)
    if (study / "summary.json").stat().st_size > 4 * 1024 * 1024:
        raise RuntimeError("Local review summary exceeds its bounded read contract")
    manifest["status"] = "complete"
    manifest["completed_at"] = now()
    manifest["source_files"], manifest["source_digest"] = source_inventory(root, study)
    write_json(study / "manifest.json", manifest)
    outputs = [study / "summary.json", study / "manifest.json", study / "protocol.json",
               *sorted((study / "results").glob("*")), *sorted((study / "traces").glob("*.json")),
               study / "scores/verification.json", study / "set-stability/result.json", study / "source-inventory.json",
               *sorted((study / "spatial/results").glob("*")), study / "acquisition/summary.json",
               study / "acquisition/gage-comparison.csv", study / "acquisition/annual-record-coverage.csv"]
    for arm in arms:
        outputs += [study / "candidates" / arm["id"] / "app-data/reference-curves.json",
                    study / "candidates" / arm["id"] / "app-data/screening-methods.json",
                    study / "candidates" / arm["id"] / f"{arm['id']}.easi-method.zip",
                    study / f"scores/{arm['id']}.json"]
    write_json(study / "completion.json", {"schema_version": 1, "study_id": study.name, "status": "complete",
               "completed_at": now(), "input_digest": manifest["input_digest"], "parent_binding": manifest["parent_binding"],
               "source_digest": manifest["source_digest"], "runner_version": STUDY_VERSION,
               "addendum_sha256": r4.ADDENDUM_SHA256,
               "candidate_package_digests": manifest.get("candidate_package_digests"),
               "output_hashes": {p.relative_to(study).as_posix(): sha(p) for p in outputs if p.is_file()}})
    return {"status": "complete", "candidates": len(candidates),
            "decisions": {c["id"]: c["decision"]["adopted"] for c in candidates}}


def build(root: Path, study: Path):
    manifest = read_json(study / "manifest.json")
    if is_round4(manifest):
        return build_round4(root, study, manifest)
    verification = read_json(study / "scores/verification.json")
    if verification.get("status") != "passed":
        raise RuntimeError("A passing replay and candidate-scope receipt is required")
    cohorts = pq.read_table(study / "cohorts/reaches.parquet").to_pandas().set_index("comid")
    sampling = read_json(study / "cohorts/sampling.json")
    field = csv_rows(study / "results/field_agreement.csv")
    spatial = csv_rows(study / "spatial/results/field_agreement.csv")
    diagnostics = {key: csv_rows(study / f"results/field_{filename}.csv") for key, filename in
                   (("low_flow", "lowflow"), ("agriculture", "agriculture"), ("model", "model"))}
    stability = read_json(study / "woody-stability/result.json")
    recommendation_result = recommendation(field, spatial)
    base = pq.read_table(study / "scores/alternative-1.parquet").to_pandas().set_index("comid")
    sample = cohorts.index[cohorts["sample"]]
    woody = cohorts.index[cohorts.woody_82]
    weights = cohorts.loc[sample, "sample_weight"]
    rating_columns = [c for c in base.columns if c.startswith("rating__")]
    output = []
    baseline_artifact = read_json(study / "candidates/alternative-1/app-data/reference-curves.json")
    for definition in ALTERNATIVES:
        aid = definition["id"]
        artifact = read_json(study / "candidates" / aid / "app-data/reference-curves.json")
        curve_changes = []
        for set_id, old_set in baseline_artifact["sets"].items():
            before, after = old_set["curves"], artifact["sets"][set_id]["curves"]
            for key in sorted(before.keys() | after.keys()):
                if before.get(key) != after.get(key):
                    curve_changes.append({"set": set_id, "stratum": key, "change": "removed" if key not in after else "added" if key not in before else "replaced"})
        current = pq.read_table(study / f"scores/{aid}.parquet").to_pandas().set_index("comid")
        previous, candidate = base.loc[sample], current.loc[sample]
        changed = np.zeros(len(sample), bool)
        for column in rating_columns:
            changed |= labels(previous[column]).ne(labels(candidate[column])).to_numpy()
        fallback = np.zeros(len(sample), bool)
        for column in (c for c in current.columns if c.startswith("curves__")):
            fallback |= candidate[column].map(lambda x: any(v.get("fallbackDepth", 0) > 0 for v in json.loads(x or "{}").values())).to_numpy()
        finite = candidate.eci.notna()
        base_finite = finite & previous.eci.notna()
        mean = float(np.average(candidate.loc[finite, "eci"], weights=weights[finite])) if finite.any() else None
        delta = float(np.average(candidate.loc[base_finite, "eci"] - previous.loc[base_finite, "eci"], weights=weights[base_finite])) if base_finite.any() else None
        transitions = []
        for name, ids, design, weight in (("Stored footprint estimate", sample, "weighted-sample", weights),
                                         ("Full stored Level II 8.2", woody, "full-l2-8.2", pd.Series(1., index=woody))):
            population_n = sampling["population_n"] if design == "weighted-sample" else len(ids)
            for column in ["eci", *("rating__" + function for function in sorted(CHANGED))]:
                transitions.append(transition(labels(base.loc[ids, column], column == "eci"), labels(current.loc[ids, column], column == "eci"),
                    weight, name + ": " + column.replace("rating__", "").replace("_", " "), design, population_n))
        if aid == "alternative-4":
            transitions.append(stability["comparison"])
        selected_field = [r for r in field + spatial if r.get("alternative_id") == aid and r.get("region") == "US"
                          and (r.get("function") in CHANGED or r.get("function") in ("eci", "physical", "chemical", "biological"))]
        row = {**definition, "coverage": {"cohort": "100,000 sampled stored reaches", "weighted": True,
                "population_n": sampling["population_n"], "rated_n": float(weights[finite].sum()),
                "missing_n": float(weights[~finite].sum()), "fallback_n": float(weights[fallback].sum())},
               "differences": {"cohort": "100,000 sampled stored reaches", "weighted": True,
                "changed_curves": len(curve_changes), "curve_changes": curve_changes,
                "changed_ratings_n": float(weights[changed].sum()), "changed_ratings_share": float(weights[changed].sum() / weights.sum()),
                "eci_mean": mean, "eci_mean_delta": delta}, "field_agreement": selected_field,
               "transitions": transitions, "curve_uncertainty": [{k: v for k, v in r.items() if k != "draws"} for r in stability["curves"]],
               "notes": ["Alternative 1 remains the default. The historical legacy baseline is a separately named method.",
                         "Fallback counts are reaches with at least one selected reference fallback, including unavailable quantities and retained entrenchment fallback; selection does not prove contribution to a rating.",
                         "Detailed target-specific counts and all regional comparisons are in the downloadable CSVs."]}
        for key, rows in diagnostics.items():
            row[key] = {"note": {"low_flow": "Raw predictors only. Natural intermittence is distinct from impairment; no new scoring thresholds.",
                                 "agriculture": "Intentional contribution-removal diagnostics preserve official weights and are distinct from missing evidence.",
                                 "model": "Published model and integrity fallback have different eligibility. Training independence remains unresolved."}[key],
                        "rows": [r for r in rows if r.get("region") == "US"]}
        output.append(row)
    summary = clean({"schema_version": 1, "study_id": study.name, "input_digest": manifest["input_digest"],
                    "reference_id": "alternative-1", "alternatives": output, "recommendation": recommendation_result,
                    "verification": verification, "observation_ledger": read_json(study / "cohorts/cohort_summary.json"),
                    "acquisition": read_json(study / "acquisition/summary.json") if (study / "acquisition/summary.json").exists() else {},
                    "limitations": [recommendation_result["decision"] + ". No scoring method is changed automatically.",
                        "2023-24 results are retrospective temporal evaluation because the project has already used those observations.",
                        "Frozen alternatives and watershed-excluded refitted reference-method validation are reported separately.",
                        "Stored-footprint totals estimated from the fixed 100,000-reach sample are not observed national counts.",
                        "Reference-sampling uncertainty is not independent biological validation."]})
    # The page deliberately reads this bounded producer summary only.
    write_json(study / "summary.json", summary)
    if (study / "summary.json").stat().st_size > 4 * 1024 * 1024:
        raise RuntimeError("Local review summary exceeds its bounded read contract")
    manifest["status"] = "complete"
    manifest["completed_at"] = now()
    manifest["source_files"], manifest["source_digest"] = source_inventory(root, study)
    write_json(study / "manifest.json", manifest)
    outputs = [study / "summary.json", study / "manifest.json", study / "protocol.json",
               *sorted((study / "results").glob("*")), *sorted((study / "traces").glob("*.json")),
               study / "scores/verification.json", study / "woody-stability/result.json", study / "source-inventory.json",
               *sorted((study / "spatial/results").glob("*")), study / "acquisition/summary.json",
               study / "acquisition/gage-comparison.csv", study / "acquisition/annual-record-coverage.csv"]
    outputs += [study / "candidates" / a["id"] / "app-data/reference-curves.json" for a in ALTERNATIVES]
    write_json(study / "completion.json", {"schema_version": 1, "study_id": study.name, "status": "complete",
               "completed_at": now(), "input_digest": manifest["input_digest"], "parent_binding": manifest["parent_binding"],
               "source_digest": manifest["source_digest"],
               "output_hashes": {p.relative_to(study).as_posix(): sha(p) for p in outputs if p.is_file()}})
    return {"status": "complete", "recommended": recommendation_result["recommended"], "alternatives": len(output)}
