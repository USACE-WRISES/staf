"""The EASI addendum's outcomes P1 to P6 for a candidate arm, and the decision by its margins.

Everything here reads what the study's steps wrote: the paired field agreement tables
(frozen and watershed-held-out designs), the arms' score tables on the weighted stored
sample, the set-stability result and the receipts. Nothing is refitted or rescored, and the
margins are ``round4.MARGINS`` (checked against the frozen yaml's text). The multiplicity
step (BH q-values across the eight primaries) belongs to ``round4_finalist.py``.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math

import numpy as np
import pandas as pd

from . import round4 as r4

SEED = 20260915
AGGREGATES = ("eci", "physical", "chemical", "biological")
DESIGNS = (("frozen", ""), ("watershed-held-out", "spatial_refit:"))
COHORTS = ("latest_visit1", "development_1314_1819", "retrospective_2324")
P1_TARGETS = {"T1": r4.T1, "T2": r4.T2, "T3_fish_mmi": r4.T3[0], "T3_oe": r4.T3[1]}


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value) if value is not None else False


def _match(rows, **wanted):
    out = []
    for row in rows:
        if all(str(row.get(key)) == str(value) for key, value in wanted.items()):
            out.append(row)
    return out


def _summary(row) -> dict:
    """One paired row, the fields the outcomes read; every value None when the row is absent."""
    keys = ("reference", "alternative", "delta", "delta_median", "ci_low", "ci_high", "p_two_sided",
            "reference_ci_low", "reference_ci_high", "alternative_ci_low", "alternative_ci_high",
            "n", "n_huc8", "n_positive", "n_negative", "boot_requested", "boot_valid")
    out = {key: _num((row or {}).get(key)) for key in keys}
    out["support_floor_met"] = _truthy((row or {}).get("support_floor_met")) if row else None
    out["noninferiority_status"] = (row or {}).get("noninferiority_status")
    out["interpretation"] = (row or {}).get("interpretation")
    out["present"] = row is not None
    out["supported"] = bool(row) and out["support_floor_met"] and (out["boot_valid"] or 0) >= r4.MARGINS["support_draws"] \
        and out["ci_low"] is not None and out["ci_high"] is not None
    return out


def p1(field, spatial, candidate, *, cohort=r4.DECIDING_COHORT) -> dict:
    """P1 independent association: the paired AUC deltas (candidate minus base; a base-only
    study pairs the base with itself and the value intervals carry its absolute AUCs) on T1
    and T2 in both designs, T3 reported, for the deciding cohort (the development cohort) in
    ``designs``, which is all the decision reads; every cohort, the retrospective one
    included, beside it in ``cohorts`` for reporting only."""
    out = {"deciding_cohort": cohort, "decision_reads": "designs (the deciding cohort only)",
           "designs": {}, "cohorts": {}}
    for design, prefix in DESIGNS:
        rows = field if design == "frozen" else spatial
        block = {}
        for name, (target, statistic) in P1_TARGETS.items():
            hit = _match(rows, alternative_id=candidate, function="eci", region="US",
                         cohort=prefix + cohort, target=target, statistic=statistic)
            block[name] = {"target": target, "statistic": statistic, **_summary(hit[0] if hit else None),
                           "role": "exploratory" if name.startswith("T3") else "deciding"}
        out["designs"][design] = block
    for other in COHORTS:
        out["cohorts"][other] = {}
        for design, prefix in DESIGNS:
            rows = field if design == "frozen" else spatial
            block = {}
            for name, (target, statistic) in P1_TARGETS.items():
                hit = _match(rows, alternative_id=candidate, function="eci", region="US",
                             cohort=prefix + other, target=target, statistic=statistic)
                block[name] = {k: v for k, v in _summary(hit[0] if hit else None).items()
                               if k in ("reference", "alternative", "delta", "delta_median", "ci_low", "ci_high", "n",
                                        "boot_valid", "supported", "present")}
            out["cohorts"][other][design] = block
    return out


def p2(field, spatial, candidate, functions, *, cohort=r4.DECIDING_COHORT) -> dict:
    """P2 function and regional review: per function (the family's, and the ECI) and per
    NARS-9 region, the paired AUC, signed Spearman and weighted-kappa deltas; a finding is a
    supported AUC interval wholly below -0.01, or a supported Spearman or kappa interval wholly
    below zero. Exploratory rows are counted and never block: population support against the
    benthic MMI (independence rule), the T3 targets and the exploratory field targets
    (``round4.NEVER_BLOCK_TARGETS``, the width to depth ratio)."""
    wanted = set(functions or []) | {"eci"}
    findings, reviewed, supported_n, exploratory, regions = [], 0, 0, 0, set()
    for design, prefix in DESIGNS:
        rows = field if design == "frozen" else spatial
        for row in rows:
            if str(row.get("alternative_id")) != str(candidate) or str(row.get("cohort")) != prefix + cohort:
                continue
            function, target = str(row.get("function")), str(row.get("target"))
            if functions is not None and function not in wanted:
                continue
            if str(row.get("region")).startswith("woody_L2"):
                continue
            statistic = str(row.get("statistic", ""))
            if not (statistic.startswith("auc") or statistic in ("spearman", "weighted_kappa")):
                continue
            if (function == "population_support" and target == "t__bent_mmi") or target in r4.NEVER_BLOCK_TARGETS:
                exploratory += 1
                continue
            reviewed += 1
            regions.add(str(row.get("region")))
            summary = _summary(row)
            if not summary["supported"] or _num(row.get("boot_requested")) != r4.MARGINS["bootstrap_draws"]:
                continue
            supported_n += 1
            finding = None
            if statistic.startswith("auc") and summary["ci_high"] < r4.MARGINS["p2_auc_block"]:
                finding = "supported paired AUC interval wholly below -0.01"
            elif statistic in ("spearman", "weighted_kappa") and summary["ci_high"] < r4.MARGINS["p2_correlation_block"]:
                finding = "supported signed correlation or class agreement interval wholly below zero"
            if finding:
                findings.append({"design": design, "function": function, "target": target, "region": row.get("region"),
                                 "statistic": statistic, "delta": summary["delta"], "ci_low": summary["ci_low"],
                                 "ci_high": summary["ci_high"], "n": summary["n"], "boot_valid": summary["boot_valid"],
                                 "reason": finding})
    return {"functions": sorted(wanted), "cohort": cohort, "rows_reviewed": reviewed, "rows_supported": supported_n,
            "rows_exploratory": exploratory,
            "regions": sorted(regions), "findings": findings, "blocks": bool(findings),
            "support_rule": f"sample floor and at least {r4.MARGINS['support_draws']} of {r4.MARGINS['bootstrap_draws']} valid draws",
            "independence": ("population_support against t__bent_mmi, the T3 targets and the exploratory field targets "
                             f"({', '.join(r4.EXPLORATORY_FIELD_TARGETS)}) are exploratory and never block")}


def _labels(series):
    return series.astype(object).where(series.notna(), "Not rated")


def _wshare(mask, weights):
    total = float(weights.sum())
    return float(weights[np.asarray(mask, bool)].sum() / total) if total else None


def p3(base, candidate, reaches) -> dict:
    """P3 coverage and footprint on the weighted stored sample: rating availability per
    function under each arm, the withheld share (a documented gap, counted, never favorable),
    what was lost, lost as a documented gap, and gained, the changed share, and the paired mean
    ECI delta on reaches with a finite ECI under both arms."""
    sample = reaches.index[reaches["sample"].astype(bool)]
    sample = sample.intersection(base.index).intersection(candidate.index)
    weights = reaches.loc[sample, "sample_weight"].to_numpy(dtype=float)
    b, c = base.loc[sample], candidate.loc[sample]
    functions = sorted(col[8:] for col in base.columns if col.startswith("rating__"))
    per_function, gaps = {}, {}
    any_changed = np.zeros(len(sample), bool)
    all_documented, unchanged = True, True
    for fn in functions:
        rb, rc = b[f"rating__{fn}"], c[f"rating__{fn}"] if f"rating__{fn}" in c else pd.Series(index=sample, dtype=object)
        rated_b, rated_c = rb.notna().to_numpy(), rc.notna().to_numpy()
        comp = c[f"completeness__{fn}"] if f"completeness__{fn}" in c else None
        withheld = (comp.astype(object) == "withheld").to_numpy() if comp is not None else np.zeros(len(sample), bool)
        lost, gained = rated_b & ~rated_c, ~rated_b & rated_c
        changed = _labels(rb).ne(_labels(rc)).to_numpy()
        changed_rated = rated_b & rated_c & rb.astype(object).ne(rc.astype(object)).to_numpy()
        any_changed |= changed
        documented = bool(np.array_equal(lost, lost & withheld)) and not gained.any()
        all_documented &= documented
        unchanged &= not lost.any() and not gained.any()
        per_function[fn] = {"availability_base": _wshare(rated_b, weights), "availability_candidate": _wshare(rated_c, weights),
                            "withheld_share": _wshare(withheld, weights), "lost_share": _wshare(lost, weights),
                            "lost_withheld_share": _wshare(lost & withheld, weights), "gained_share": _wshare(gained, weights),
                            "changed_share": _wshare(changed, weights), "changed_rated_share": _wshare(changed_rated, weights),
                            "n_lost": int(lost.sum()), "n_lost_withheld": int((lost & withheld).sum()),
                            "n_gained": int(gained.sum()), "n_withheld": int(withheld.sum()),
                            "documented_gaps_only": documented}
        if f"gap__{fn}" in c and withheld.any():
            statements = Counter()
            for raw, w in zip(c[f"gap__{fn}"].to_numpy(), weights):
                if isinstance(raw, str) and raw:
                    try:
                        statements[str(json.loads(raw).get("statement"))] += w
                    except ValueError:
                        statements["unreadable gap record"] += w
            gaps[fn] = {k: float(v) for k, v in statements.most_common(12)}
    finite_b, finite_c = b["eci"].notna().to_numpy(), c["eci"].notna().to_numpy()
    both = finite_b & finite_c
    delta = float(np.average(c.loc[both, "eci"].to_numpy(dtype=float) - b.loc[both, "eci"].to_numpy(dtype=float),
                             weights=weights[both])) if both.any() else None
    return {"cohort": "stored sample", "weighted": True, "n_sample": int(len(sample)),
            "population_n": float(weights.sum()), "functions": per_function, "gaps": gaps,
            "changed_ratings_share": _wshare(any_changed, weights),
            "eci_finite_share_base": _wshare(finite_b, weights), "eci_finite_share_candidate": _wshare(finite_c, weights),
            "eci_mean_base": float(np.average(b.loc[finite_b, "eci"].to_numpy(dtype=float), weights=weights[finite_b])) if finite_b.any() else None,
            "eci_mean_candidate": float(np.average(c.loc[finite_c, "eci"].to_numpy(dtype=float), weights=weights[finite_c])) if finite_c.any() else None,
            "eci_mean_delta_paired": delta, "n_paired_eci": int(both.sum()),
            "availability_unchanged": bool(unchanged), "documented_gaps_only": bool(all_documented),
            "rule": "reported; never traded against P1 or P2; a withheld rating is a documented gap, never a favorable rating"}


def p4(stability, candidate) -> dict:
    """P4 stability: the set-stability rows of a refitted family, or not applicable."""
    rows = [f for f in (stability or {}).get("families") or [] if f.get("alternative") == candidate]
    if not rows:
        return {"status": "not applicable", "families": [],
                "note": "no refitted family; the threshold sensitivity of fixed bands is not computed by this runner"}
    return {"status": "computed", "families": [
        {k: v for k, v in f.items() if k in ("family", "function", "base_set", "candidate_set", "base_flip", "candidate_flip",
                                             "difference", "margin", "within_margin")}
        | {"base_pooled": f["base"]["pooled"], "candidate_pooled": f["candidate"]["pooled"],
           "base_linkage": {k: v["status"] for k, v in f["base"]["linkage"].items()},
           "candidate_linkage": {k: v["status"] for k, v in f["candidate"]["linkage"].items()},
           "comparison": {k: v for k, v in (f.get("comparison") or {}).items() if k != "rows"}}
        for f in rows],
        "within_margin": all(f.get("within_margin") for f in rows)}


def p5(candidate, reaches, *, boot=1000, seed=SEED) -> dict:
    """P5 completeness optimism (E8): the weighted mean ECI of reaches with fewer than 15
    rated functions minus complete reaches within the same Level II stratum, pooled over the
    strata by the incomplete reaches' weight, on the stored sample; a HUC8 cluster bootstrap
    gives the interval and a two-sided p."""
    if "functions_rated" not in candidate.columns or candidate["functions_rated"].notna().sum() == 0:
        return {"status": "not applicable", "note": "the arm reports no functionsRated (not E8)"}
    sample = reaches.index[reaches["sample"].astype(bool)].intersection(candidate.index)
    frame = pd.DataFrame({"eci": candidate.loc[sample, "eci"].to_numpy(dtype=float),
                          "rated": pd.to_numeric(candidate.loc[sample, "functions_rated"], errors="coerce").to_numpy(dtype=float),
                          "w": reaches.loc[sample, "sample_weight"].to_numpy(dtype=float),
                          "l2": reaches.loc[sample, "l2"].astype(str).to_numpy(),
                          "huc8": reaches.loc[sample, "huc8"].astype(str).to_numpy()})
    frame = frame[np.isfinite(frame.eci) & np.isfinite(frame.rated)]
    few, complete = r4.MARGINS["few_functions"], r4.MARGINS["complete_functions"]
    frame["group"] = np.where(frame.rated < few, "incomplete", np.where(frame.rated >= complete, "complete", "other"))

    def statistic(rows):
        strata, num, den = [], 0.0, 0.0
        for l2, group in rows.groupby("l2", sort=True):
            inc, comp = group[group.group == "incomplete"], group[group.group == "complete"]
            if inc.empty or comp.empty:
                continue
            d = float(np.average(inc.eci, weights=inc.w)) - float(np.average(comp.eci, weights=comp.w))
            weight = float(inc.w.sum())
            strata.append({"l2": l2, "difference": d, "n_incomplete": int(len(inc)), "n_complete": int(len(comp)),
                           "weight_incomplete": weight})
            num += weight * d
            den += weight
        return (num / den if den else None), strata

    difference, strata = statistic(frame)
    labels, inverse = np.unique(frame.huc8.to_numpy(), return_inverse=True)
    members = [np.flatnonzero(inverse == i) for i in range(len(labels))]
    rng = np.random.default_rng(seed)
    draws = []
    if difference is not None and len(labels) >= 2:
        for _ in range(boot):
            picked = rng.integers(0, len(members), size=len(members))
            rows = frame.iloc[np.concatenate([members[i] for i in picked])]
            value, _ = statistic(rows)
            if value is not None:
                draws.append(value)
    arr = np.asarray(draws, dtype=float)
    enough = arr.size >= max(30, math.ceil(boot * .8))
    lo, hi = (float(np.quantile(arr, .025)), float(np.quantile(arr, .975))) if enough else (None, None)
    p = min(1.0, 2.0 * (min(int((arr <= 0).sum()), int((arr >= 0).sum())) + 1) / (arr.size + 1)) if enough else None
    return {"status": "computed", "difference": difference, "ci_low": lo, "ci_high": hi, "p_two_sided": p,
            "boot_requested": boot, "boot_valid": int(arr.size), "seed": seed, "cluster": "huc8",
            "n_incomplete": int((frame.group == "incomplete").sum()), "n_complete": int((frame.group == "complete").sum()),
            "n_other": int((frame.group == "other").sum()), "strata": strata, "weighted": True,
            "definition": f"mean ECI of reaches with fewer than {few} rated functions minus reaches with all {complete} rated, within Level II strata, pooled by the incomplete reaches' weight",
            "margin": r4.MARGINS["p5_optimism"],
            "exceeds_margin": difference is not None and difference > r4.MARGINS["p5_optimism"]}


def p6(catalog, metrics, score_record, elapsed_seconds) -> dict:
    """P6 usability: inputs per function and their distinct sources from the arm's catalog,
    the runtime from the receipts, failures and unavailable services from the score record."""
    function_of = {m.get("metricId"): m.get("functionId") for m in (metrics.get("metrics") or [])}
    inputs, sources = {}, set()
    for method in catalog.get("methods") or []:
        function = str(function_of.get(method.get("metricId")) or method.get("metricId")).replace("-", "_")
        rated = [i for i in method.get("inputs") or [] if not i.get("contextOnly")]
        inputs[function] = len(rated)
        for i in rated:
            if i.get("sourceField"):
                sources.add(str(i["sourceField"]))
    return {"inputs_per_function": dict(sorted(inputs.items())), "inputs_total": int(sum(inputs.values())),
            "distinct_sources": sorted(sources), "distinct_sources_n": len(sources),
            "runtime_seconds_arm": (score_record or {}).get("seconds"), "runtime_seconds_scores_step": elapsed_seconds,
            "rows_scored": (score_record or {}).get("rows"), "unrated_reaches": (score_record or {}).get("unrated_reaches"),
            "failures": 0, "failure_note": "a scoring failure aborts the arm's worker; a completed score table has none",
            "unavailable_services": "none: every arm scores stored evidence offline",
            "route": (score_record or {}).get("route"), "asset_fallbacks": (score_record or {}).get("asset_fallbacks")}


def _interval_excludes_zero(row) -> bool:
    return row["ci_low"] is not None and row["ci_high"] is not None and (row["ci_low"] > 0 or row["ci_high"] < 0)


def _accuracy_reading(p1_block, primary, other):
    """The accuracy rule read with ``primary`` as the primary target, in both designs."""
    reasons = []
    for design, block in p1_block["designs"].items():
        pr, ot = block[primary], block[other]
        if not pr["supported"]:
            reasons.append(f"{design}: no supported paired interval on {primary}")
        else:
            if not _interval_excludes_zero(pr):
                reasons.append(f"{design}: the {primary} interval includes 0")
            if pr["delta_median"] is None or pr["delta_median"] < r4.MARGINS["accuracy_median_delta"]:
                reasons.append(f"{design}: the {primary} median delta is below {r4.MARGINS['accuracy_median_delta']}")
        if not ot["supported"]:
            reasons.append(f"{design}: no supported paired interval on {other}")
        elif ot["ci_low"] < r4.MARGINS["noninferiority_lower"]:
            reasons.append(f"{design}: the {other} lower bound is below {r4.MARGINS['noninferiority_lower']}")
    return {"primary": primary, "other": other, "passes": not reasons, "reasons": reasons}


def _primary_reading(spec, p1_block, p3_block, p5_block) -> tuple:
    """``(passes, reasons, readings)`` of one family's own primary rule (an accuracy change, a
    simplification or E8's presentation rule) on the P1, P3 and P5 blocks given: the family's
    own when it is studied alone, the composed arm's when it is a composition's component."""
    decision = str(spec.get("decision") or "")
    primary, deciding = r4.deciding_targets(spec)
    reasons, readings = [], {}
    if decision == "accuracy_change":
        other = "T2" if primary == "T1" else "T1"
        readings = {f"primary_{primary}": _accuracy_reading(p1_block, primary, other),
                    f"primary_{other}": _accuracy_reading(p1_block, other, primary)}
        chosen = readings[f"primary_{primary}"]
        passes = chosen["passes"]
        reasons.extend(chosen["reasons"])
    elif decision == "simplification":
        passes = True
        for design, block in p1_block["designs"].items():
            for name in deciding:
                row = block[name]
                if not row["supported"]:
                    reasons.append(f"{design}: no supported paired interval on {name}")
                    passes = False
                elif row["ci_low"] < r4.MARGINS["noninferiority_lower"]:
                    reasons.append(f"{design}: the {name} lower bound is below {r4.MARGINS['noninferiority_lower']}")
                    passes = False
        if not (p3_block.get("availability_unchanged") or p3_block.get("documented_gaps_only")):
            reasons.append("rating availability changed other than by documented gaps")
            passes = False
    elif decision == "presentation":
        if p5_block.get("status") != "computed":
            reasons.append("P5 could not be computed (the arm reports no functionsRated)")
            passes = False
        elif not p5_block.get("exceeds_margin"):
            reasons.append(f"the completeness optimism {p5_block.get('difference')} does not exceed {r4.MARGINS['p5_optimism']}")
            passes = False
        else:
            passes = True
    else:
        reasons.append(f"unknown decision rule {decision!r}")
        passes = False
    return bool(passes), reasons, readings


def decide(spec, outcomes) -> dict:
    """The decision by the yaml's margins exactly as written: an accuracy change, a
    simplification or E8's presentation rule on P1 or P5, the P2 block, the P4 margin for a
    refitted family, with every reason listed. A composition (the finalist) holds when every
    component's own rule holds on the composed arm's outcomes, with P2 read on the union of
    the components' functions and P4 for any refitted component; each component's reading
    is listed under ``components``."""
    decision = str(spec.get("decision") or "")
    primary, deciding = r4.deciding_targets(spec)
    p1_block, p2_block, p3_block, p4_block, p5_block = (outcomes[k] for k in ("P1", "P2", "P3", "P4", "P5"))
    components = {}
    if decision == "composition":
        passes, reasons, readings = True, [], {}
        for fid, comp in (spec.get("components") or {}).items():
            c_passes, c_reasons, c_readings = _primary_reading(comp, p1_block, p3_block, p5_block)
            c_primary, c_deciding = r4.deciding_targets(comp)
            components[fid] = {"decision_rule": comp.get("decision"), "primary_target": c_primary,
                               "deciding_targets": c_deciding, "passes": c_passes, "reasons": c_reasons,
                               "readings": c_readings}
            passes &= c_passes
            reasons.extend(f"{fid}: {r}" for r in c_reasons)
        if not components:
            reasons.append("the composition names no components")
            passes = False
        basis = "each component's own rule and targets, read on the composed arm"
    else:
        passes, reasons, readings = _primary_reading(spec, p1_block, p3_block, p5_block)
        basis = ("named by the addendum" if "T1" in str(spec.get("primary_outcome", "")) else
                 "the addendum names none for this family; T1 (the 2013-14 designations) taken, both readings reported")
    if p2_block.get("blocks"):
        reasons.append(f"P2 retains the base: {len(p2_block['findings'])} supported finding(s) in the function and regional review")
    p4_ok = p4_block.get("status") != "computed" or p4_block.get("within_margin")
    if not p4_ok:
        reasons.append(f"P4: the refitted family's flip share rises by more than {r4.MARGINS['p4_flip_rise']}")
    adopted = bool(passes and not p2_block.get("blocks") and p4_ok)
    out = {"decision_rule": decision, "primary_target": primary, "deciding_targets": deciding,
           "primary_target_basis": basis,
           "adopted": adopted, "primary_rule_passes": bool(passes), "p2_blocks": bool(p2_block.get("blocks")),
           "p4_within_margin": bool(p4_ok), "reasons": reasons, "readings": readings,
           "margins": dict(r4.MARGINS), "deciding_cohort": p1_block.get("deciding_cohort"),
           "reads": "P1 designs of the deciding cohort, P2 of the same cohort, P3, P4, P5; never the retrospective rows"}
    if decision == "composition":
        out["composition"] = list(spec.get("composition") or [])
        out["components"] = components
        out["rule"] = ("the composition holds when every component's own rule holds on the composed arm "
                       "(P1 on its own targets in both designs, P3 documented gaps only for a simplification), "
                       "with no P2 block on the union of the components' functions and P4 within margin for a "
                       "refitted component; its interaction with the single-family studies is reported, not judged")
    return out


def _sum(values):
    finite = [v for v in values if v is not None]
    return float(sum(finite)) if finite else None


def interaction(p1_block, p3_block, component_studies) -> dict:
    """The finalist's interaction check, reported and never judged (the addendum names no
    margin for it): the composed arm's P1 delta against the sum of its components' single-
    family deltas on the same targets and designs (the deciding cohort), and the composed P3
    availability per function against the single-family figures. ``component_studies`` is
    the block the snapshot recorded from the components' own summaries."""
    p1 = {}
    for design, block in (p1_block.get("designs") or {}).items():
        p1[design] = {}
        for target, row in block.items():
            singles = {fid: ((cs.get("P1") or {}).get(design) or {}).get(target) or {}
                       for fid, cs in component_studies.items()}
            deltas = {fid: s.get("delta") for fid, s in singles.items()}
            total = _sum(deltas.values())
            composed = row.get("delta")
            p1[design][target] = {
                "composed_delta": composed, "composed_ci": [row.get("ci_low"), row.get("ci_high")],
                "composed_n": row.get("n"), "role": row.get("role"),
                "single_family_deltas": deltas,
                "single_family_ci": {fid: [s.get("ci_low"), s.get("ci_high")] for fid, s in singles.items()},
                "sum_of_single_deltas": total,
                "difference": (composed - total) if composed is not None and total is not None else None}
    p3 = {}
    for fn, row in (p3_block.get("functions") or {}).items():
        singles = {fid: ((cs.get("P3") or {}).get("functions") or {}).get(fn) or {}
                   for fid, cs in component_studies.items()}
        declared = [fid for fid, cs in component_studies.items() if fn in (cs.get("functions") or [])]
        changed_by = [fid for fid, s in singles.items()
                      if s.get("availability_base") is not None and s.get("availability_candidate") is not None
                      and abs(s["availability_candidate"] - s["availability_base"]) > 1e-12]
        composed_changed = (row.get("availability_base") is not None and row.get("availability_candidate") is not None
                            and abs(row["availability_candidate"] - row["availability_base"]) > 1e-12)
        if not declared and not changed_by and not composed_changed:
            continue
        expected = singles[changed_by[0]].get("availability_candidate") if len(changed_by) == 1 else None
        p3[fn] = {"availability_base": row.get("availability_base"),
                  "composed_availability": row.get("availability_candidate"),
                  "composed_withheld_share": row.get("withheld_share"),
                  "composed_documented_gaps_only": row.get("documented_gaps_only"),
                  "single_family_availability": {fid: s.get("availability_candidate") for fid, s in singles.items()},
                  "single_family_withheld_share": {fid: s.get("withheld_share") for fid, s in singles.items()},
                  "declared_by": declared, "changed_by_single_family": changed_by,
                  "expected_without_interaction": expected,
                  "difference": ((row.get("availability_candidate") - expected)
                                 if expected is not None and row.get("availability_candidate") is not None else None)}
    eci_singles = {fid: (cs.get("P3") or {}).get("eci_mean_delta_paired") for fid, cs in component_studies.items()}
    eci_total = _sum(eci_singles.values())
    eci = p3_block.get("eci_mean_delta_paired")
    changed_singles = {fid: (cs.get("P3") or {}).get("changed_ratings_share") for fid, cs in component_studies.items()}
    return {"cohort": p1_block.get("deciding_cohort"), "components": sorted(component_studies),
            "P1": p1, "P3": p3,
            "eci_mean_delta_paired": {"composed": eci, "single_family": eci_singles, "sum_of_single": eci_total,
                                      "difference": (eci - eci_total) if eci is not None and eci_total is not None else None},
            "changed_ratings_share": {"composed": p3_block.get("changed_ratings_share"), "single_family": changed_singles},
            "rule": ("reported, not judged: the addendum names no margin for the interaction of accepted changes; "
                     "a paired AUC delta is not additive and a function's availability is set by the components that "
                     "withhold it, so each difference describes the composed arm and decides nothing")}


__all__ = ["p1", "p2", "p3", "p4", "p5", "p6", "decide", "interaction", "SEED", "DESIGNS", "COHORTS", "P1_TARGETS"]
