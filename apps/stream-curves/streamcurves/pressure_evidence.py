"""The evidence pass under the pressure-screen reference method (methodology 0.12).

``regional_agent.run_evidence`` hands over here when ``reference_method`` is
``pressure-screen``. The result has the shape the legacy pass returns, so
``assemble``, the provenance builder, the review packet and the session all read
it unchanged, plus the keys the new rules need.

What differs from the legacy pass:

* Reference membership is read from the committed station table (rule REF-04).
  No EASI run, no screening cache, no live service.
* Every metric gets its own pool (REF-05): the ecoregion's own reference
  stations where they suffice, comparable stations of the Level II and then the
  Level I parent where they do not, and no curve at all where nothing does
  (REF-06). The pools live in ONE data frame, each metric column holding a
  value only inside its own pool, so the curve engine, the diagnostics and a
  reopened session all work as they always have.
* Values are the most recent non-null per metric (DATA-11).
* Landscape pressures are not fitted. They ride as fixed-criteria metrics
  (CURVE-11). Wetland cover and base flow index are the landscape metrics that
  still get reference curves, read from the station table.
* A registry split becomes real curves where the pool supports it (STRAT-10).
* Every curve is checked for discrimination (CURVE-12).
* Methodology 0.15: a metric whose pool is mostly unmeasured for it (DATA-03)
  and a two-sided curve narrower than its measurement-precision floor
  (CURVE-09) are withheld at the build with a statement, under REF-06's record
  shape, the same in every region, with no review item raised.

The curve engine is untouched.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Optional

import re

import pandas as pd

from . import acceptance
from . import basis_ladder
from . import carry_forward
from . import curve_basis
from . import published_benchmark
from . import curve_stability, curves, easi_screening, field_methods, fixed_criteria
from . import metric_map
from . import methodology, nrsa, nrsa_dataset, run_state, staf_library
from .deep_export import deep_slug, pooled_layers
from . import discrimination as dz
from . import reference_pool as rp
from . import reference_screen as rscreen
from . import scale_analysis

METHOD = run_state.REFERENCE_METHOD_PRESSURE
NO_LOCAL_REFERENCE = ("No station of this ecoregion passes the reference screen, so every "
                      "reference curve here borrows its reference stations from a parent "
                      "ecoregion.")


def _emit(on_event: Optional[Callable], *args) -> None:
    from . import regional_agent as ra
    ra._safe_emit(on_event, *args)


def pool_missingness(decisions: dict) -> dict:
    """DATA-01/02/03 per metric, over the metric's OWN pool members.

    The shared frame holds every metric's pool, so a plain column mean would
    count the stations of other metrics' pools as missing. The denominator is
    the comparable reference stations at the level used.
    """
    out: dict[str, dict] = {}
    for mk, d in decisions.items():
        if d.status == rp.STATUS_INSUFFICIENT:
            continue
        frac = 0.0 if not d.n_comparable else max(0.0, 1.0 - d.n_usable / d.n_comparable)
        out[mk] = {"missing_fraction": round(float(frac), 4),
                   "disposition": methodology.missingness_disposition(frac),
                   "n_pool_members": int(d.n_comparable), "n_with_value": int(d.n_usable)}
    return out


def discrimination_records(curve_rows: dict, metric_config: dict, decisions: dict,
                           data: pd.DataFrame, values: pd.DataFrame,
                           frame: pd.DataFrame) -> dict:
    """The CURVE-12 record of every built curve."""
    wide = values.set_index(values["site_id"].astype(str))
    out: dict[str, dict] = {}
    for mk, row in curve_rows.items():
        d = decisions.get(mk)
        if d is None or d.level is None or mk not in wide.columns:
            continue
        members = frame[frame[d.level].astype(str) == str(d.region_code)]
        keys = members["station_key"].astype(str)
        vals = keys.map(pd.to_numeric(wide[mk], errors="coerce"))
        vals.index = members.index
        evaluable = members["screen_evaluable"].astype(bool)
        pressured = evaluable & ~members["pass_relaxed"].astype(bool)
        rt = members["rt_nrsa"].astype(object) if "rt_nrsa" in members.columns else None
        rt_values = None
        if rt is not None:
            rt_values = {"R": vals[rt == "R"], "Im": vals[rt == "Im"]}
        ref_values = data[mk] if mk in data.columns else pd.Series(dtype="float64")
        out[mk] = dz.curve_discrimination(
            mk, metric_config.get(mk) or {}, row.get("curve_points"), ref_values,
            vals[pressured], rt_values=rt_values)
    return out


def stratified_rows(data: pd.DataFrame, metric_config: dict, decisions: dict,
                    registry: dict) -> tuple[dict, dict]:
    """Per-stratum curve rows for metrics whose registry split the pool supports.

    Returns ``(rows_by_metric, applied)``. A split is applied only where the
    metric's own pool holds two or more classes at the DATA-07 floor. A class
    below the floor gets no layer, and DEEP falls back to the pooled curve.
    """
    floor = int(rp.floors()["stratum_min_n"])
    rows_by_metric: dict[str, list[dict]] = {}
    applied: dict[str, dict] = {}
    from . import regional_agent as ra
    for mk in metric_config:
        strat = scale_analysis.stratifier_for(mk, registry)
        d = decisions.get(mk)
        if not strat or d is None or mk not in data.columns:
            continue
        col = strat["column"]
        if col not in data.columns:
            continue
        have = data[data[mk].notna()]
        counts = have[col].value_counts()
        supported = [str(label) for label in strat["labels"]
                     if int(counts.get(label, 0)) >= floor]
        record = {"stratifier": strat["key"], "n_by_class": {str(k): int(v)
                                                              for k, v in counts.items()},
                  "supported": supported, "applied": len(supported) >= 2,
                  # the definition rides with the record so the bundle can state
                  # it without reading the registry again
                  "spec": {k: strat.get(k) for k in ("key", "variable", "units", "source",
                                                     "breaks", "right", "labels", "display",
                                                     "registryVersion", "status")}}
        applied[mk] = record
        if len(supported) < 2:
            continue
        rows = []
        for label in supported:
            res = curves.build_reference_curve(
                have[have[col].astype(str) == label], mk, metric_config,
                stratum_label=label, build_plots=False)
            rows.append(ra._row_to_dict(res, mk))
        rows_by_metric[mk] = rows
    return rows_by_metric, applied


# --------------------------------------------------------------------------- #
# what the bundle says (consumed by regional_agent.assemble and, through the
# session's reference_build field, by an interactive republish)
# --------------------------------------------------------------------------- #
DISCRIMINATION_KEYS = ("aucRefVsPressure", "nRef", "nPressure", "medianRefIndex",
                       "medianPressureIndex", "aucRvsIm", "nR", "nIm", "verdict")
BORROWED_CAVEAT = ("The reference stations for this curve were borrowed from {where} because "
                   "this ecoregion has too few least-disturbed stations of its own. {note}")
INVERTED_CAVEAT = ("In the discrimination check this curve scored pressured stations above "
                   "reference stations (area under the curve {auc:.2f}), so the score may not "
                   "track condition here. Read it with the other metrics of the function.")
ZERO_INFLATED_CAVEAT = ("{n_zero} of the {n} reference stations ({share:.0%}) have a value of "
                        "zero, above the {threshold:.0%} at which the pool counts as "
                        "zero-inflated, so the curve was fitted to the positive values only and "
                        "a value of zero scores 0 (the two-part rule).")


def is_withheld_record(d) -> bool:
    """A reference-support record of a metric a rule withheld although its pool
    exists (the discrimination gate, the zero-inflation rule): counted as
    withheld, never as a curve."""
    return bool((d or {}).get("withheld")) if isinstance(d, dict) else False


def stratifier_block(record: dict) -> Optional[dict]:
    """The ``stratifier`` block of a bundle entry: what DEEP needs to choose a
    curve set from a reach's slope or drainage area. None when no split applied."""
    spec = record.get("spec") or {}
    if not record.get("applied") or not spec.get("labels"):
        return None
    display = spec.get("display") or {}
    supported = {str(s) for s in record.get("supported") or []}
    counts = record.get("n_by_class") or {}
    return {"key": spec.get("key"), "variable": spec.get("variable"),
            "units": spec.get("units"), "source": spec.get("source"),
            "breaks": [float(b) for b in spec.get("breaks") or []],
            "right": bool(spec.get("right")),
            "classes": [{"key": str(label), "label": display.get(label, str(label)),
                         "hasCurve": str(label) in supported,
                         "n": int(counts.get(str(label), 0))}
                        for label in spec["labels"]],
            "registryVersion": spec.get("registryVersion"), "status": spec.get("status")}


def layered_row(row: dict, class_rows: list[dict], record: dict) -> dict:
    """The pooled curve row with its class layers attached (``all_strata``), the
    shape the exporter turns into ``curveLayers``. The pooled curve is the
    unnamed layer, so it stays the default and the fallback for a reach whose
    class the pool could not support.

    A layer is named by its class KEY (``ge_2``), the value the data column
    holds, so a bundle rebuilt from a reopened session names its layers the
    same way. The ``stratifier`` block carries the words a person reads. A
    republish reads the stored rows with the same rule (``deep_export.pooled_layers``)."""
    out = dict(row)
    out["all_strata"] = pooled_layers(row, class_rows)
    return out


def reference_annotations(evidence: dict, metrics) -> dict[str, dict]:
    """Per metric, the reference statement the bundle carries beside a regional
    curve: criteria basis, reference support, local comparison, the class split
    and the discrimination check, plus the caveats those raise."""
    support = evidence.get("reference_support") or {}
    comparisons = evidence.get("local_comparison") or {}
    discrimination = evidence.get("discrimination") or {}
    applied = evidence.get("strata_applied") or {}
    out: dict[str, dict] = {}
    for mk in metrics:
        rec0 = support.get(mk)
        basis = curve_basis.resolve(
            (rec0 or {}).get("basis") if isinstance(rec0, dict) else getattr(rec0, "basis", None))
        ann: dict = {"criteriaBasis": "reference", "basis": basis,
                     "basisLabel": curve_basis.label_for(basis)}
        caveats: list[str] = []
        d = support.get(mk)
        if d:
            rec = rp.reference_support_record(d)
            ann["referenceSupport"] = rec
            if str(rec.get("status") or "").startswith("borrowed"):
                where = f"{rec.get('levelLabel')} ecoregion {rec.get('regionCode')}" + (
                    f" ({rec.get('regionName')})" if rec.get("regionName") else "")
                caveats.append(BORROWED_CAVEAT.format(
                    where=where, note=str(rec.get("transferNote") or "")).strip())
            if str(rec.get("transferRisk") or "") == rp.RISK_UNVALIDATED:
                # methodology 0.16 (REF-16): the four disclosure fields ride at the
                # metric, where DEEP, the calculator and the promotion gate read
                # them, and the limitation rides as a caveat an older DEEP prints
                ann["transferRisk"] = rp.RISK_UNVALIDATED
                ann["transferValidation"] = dict(rec.get("transferValidation") or {})
                ann["transferNote"] = str(rec.get("transferNote") or "")
                ann["confidenceCap"] = rec.get("confidenceCap")
                limitation = rp.flagged_limitation(rec)
                if limitation and limitation not in caveats:
                    caveats.append(limitation)
        kept_rec = (evidence.get("missingness") or {}).get(mk) or {}
        if kept_rec.get("keptForCoverage"):
            # methodology 0.16 (D18): a pool DATA-03 would have withheld, kept as its
            # function's last candidate, carries its missingness as a limitation
            meta = staf_library.staf_function_meta()
            by_id = dict(zip(meta["id"].astype(str), meta["name"].astype(str)))
            names = [by_id.get(str(fid), str(fid)) for fid in kept_rec["keptForCoverage"]]
            caveat = missingness_kept_caveat(
                kept_rec, names, float(methodology.threshold("data_rules.max_missingness_review")))
            if caveat not in caveats:
                caveats.append(caveat)
        if comparisons.get(mk):
            ann["localComparison"] = dict(comparisons[mk])
        block = stratifier_block(applied.get(mk) or {})
        if block:
            ann["stratifier"] = block
        disc = discrimination.get(mk)
        if disc:
            ann["discrimination"] = {k: disc.get(k) for k in DISCRIMINATION_KEYS}
            if disc.get("verdict") == "inverted" and disc.get("aucRefVsPressure") is not None:
                caveats.append(INVERTED_CAVEAT.format(auc=float(disc["aucRefVsPressure"])))
        zi = (evidence.get("zero_inflation") or {}).get(mk)
        if zi and zi.get("handling") == curves.ZERO_INFLATED_TWO_PART \
                and zi.get("two_part_applicable"):
            # campaign Round 2 (C3a): the two-part rule states the zero share it set aside
            ann["zeroInflation"] = {"share": zi.get("share"), "threshold": zi.get("threshold"),
                                    "nZero": zi.get("n_zero"), "n": zi.get("n"),
                                    "handling": zi.get("handling")}
            caveats.append(ZERO_INFLATED_CAVEAT.format(
                n_zero=int(zi.get("n_zero") or 0), n=int(zi.get("n") or 0),
                share=float(zi.get("share") or 0.0), threshold=float(zi.get("threshold") or 0.0)))
        if caveats:
            ann["curveCaveats"] = caveats
        # how the metric is measured (the NRSA protocol the curve's data followed)
        method = field_methods.method_context(mk)
        if method:
            ann["methodContext"] = method
        out[mk] = ann
    return out


def fixed_annotations(fixed_keys) -> dict[str, dict]:
    """Annotations of the fixed-criteria metrics (CURVE-11). No reference
    sample, no sample confidence: the label says what the criteria are."""
    out: dict[str, dict] = {}
    for mk in fixed_keys:
        e = fixed_criteria.entry_for(mk)
        caveats = []
        if e.get("provisional"):
            caveats.append("EASI lists the criteria for this metric as provisional, so the "
                           "breakpoints may be revised.")
        out[mk] = {"criteriaBasis": fixed_criteria.CRITERIA_BASIS,
                   "basis": curve_basis.PUBLISHED,
                   "basisLabel": curve_basis.label_for(curve_basis.PUBLISHED),
                   "basisStatement": curve_basis.statement_for(curve_basis.PUBLISHED),
                   "basisLimit": curve_basis.limit_for(curve_basis.PUBLISHED),
                   "criteriaSource": fixed_criteria.criteria_source(e),
                   "metricRole": fixed_criteria.METRIC_ROLE,
                   "confidenceLabel": fixed_criteria.CONFIDENCE_LABEL,
                   "sourceCitation": fixed_criteria.citation_line(e),
                   "curveCaveats": caveats}
        method = field_methods.method_context(mk)
        if method:
            out[mk]["methodContext"] = method
    return out


def _functions_of(metric: str) -> list[dict]:
    """Every function the metric informs, as the bundle would have placed it."""
    from . import regional_agent as ra
    functions: list[dict] = []
    for f in metric_map.metric_map_functions_for(metric):
        fid = ra._canonical_function_id(f.get("function_name"))
        if fid and fid not in [x["functionId"] for x in functions]:
            functions.append({"functionId": fid, "functionName": f.get("function_name")})
    return functions


#: The reasons a metric is withheld with no curve. REF-06 is the hierarchy
#: running out. The others withhold a metric whose pool exists, and the record
#: on the metric says so: the two campaign Round 2 rules (config
#: ``curve12.gate`` and ``curve10.zero_inflated_handling: withhold``, both off
#: by default) and, since methodology 0.15, the two rules that used to hold a
#: built curve for a reviewer: DATA-03 (the pool is mostly unmeasured for the
#: metric) and CURVE-09 (a two-sided core narrower than the metric's
#: measurement-precision floor).
INSUFFICIENT_REFERENCE_SUPPORT = "insufficient-reference-support"
DISCRIMINATION_GATE = "discrimination-gate"
ZERO_INFLATED_POOL = "zero-inflated-reference-pool"
HIGH_MISSINGNESS = "high-missingness"
MEASUREMENT_PRECISION_FLOOR = "measurement-precision-floor"
WITHHELD_REASON_WORDS = {INSUFFICIENT_REFERENCE_SUPPORT: "insufficient reference support",
                         DISCRIMINATION_GATE: "the discrimination gate",
                         ZERO_INFLATED_POOL: "a zero-inflated reference pool",
                         HIGH_MISSINGNESS: "high missingness over its reference pool",
                         MEASUREMENT_PRECISION_FLOOR: "the measurement-precision floor"}
#: the ``reference_support`` status of a metric a rule withheld although its
#: pool exists (methodology 0.15); the pool's own status rides beside it
STATUS_WITHHELD = "withheld"


def withheld_reason(item: dict) -> str:
    """The reason of one ``insufficient_support`` item (REF-06 unless the item
    names another rule)."""
    return str((item or {}).get("reason") or INSUFFICIENT_REFERENCE_SUPPORT)


def withheld_rule(item: dict) -> str:
    """The rule a withheld entry or ``insufficient_support`` item was withheld
    under: the ``rule`` it names, else the rule its reason implies."""
    rule = (item or {}).get("rule")
    return str(rule) if rule else WITHHELD_RULES.get(withheld_reason(item), "REF-06")


def withheld_support_record(decision: dict, reason: str) -> dict:
    """The ``reference_support`` record of a metric a rule withheld although its
    pool exists (methodology 0.15): status ``withheld``, the pool's own status
    beside it (``pool_status``), the reason and the rule. The pool's numbers,
    the options tried and the station ids stay, so the record still says what
    the pool was; :func:`is_withheld_record` keys on ``withheld``."""
    d = dict(decision or {})
    return {**d, "status": STATUS_WITHHELD, "pool_status": d.get("status"),
            "withheld": str(reason), "rule": WITHHELD_RULES.get(str(reason), "REF-06")}


def withheld_metrics(evidence: dict) -> list[dict]:
    """The bundle's ``insufficientReferenceSupport`` list: every metric withheld
    with no curve, with the function it would have scored and what was tried.
    Most are REF-06 (no defensible reference pool); a metric withheld by the
    discrimination gate or the zero-inflation rule carries that reason, its own
    statement and the numbers behind it (``detail``)."""
    out = []
    for mk, item in sorted((evidence.get("insufficient_support") or {}).items()):
        cfg = item.get("config") or {}
        d = item.get("decision") or {}
        functions = _functions_of(mk)
        reason = withheld_reason(item)
        record = {
            "metricId": "spring-" + deep_slug(mk), "metricKey": mk,
            "metricName": cfg.get("display_name") or mk, "units": cfg.get("units") or "",
            "functions": functions,
            "functionId": functions[0]["functionId"] if functions else None,
            "reason": reason,
            "statement": (item.get("statement") if reason != INSUFFICIENT_REFERENCE_SUPPORT
                          and item.get("statement") else _withheld_statement(evidence, mk)),
            "levelsTried": d.get("levels_tried") or [],
            "rungsTried": [{"rung": a.get("rung"), "why": a.get("why"),
                            **({"condition": a["condition"]} if a.get("condition") else {})}
                           for a in (evidence.get("ladder_attempts") or [])
                           if a.get("metric") == mk and a.get("rung")],
        }
        if reason != INSUFFICIENT_REFERENCE_SUPPORT:
            # the rule that judged the existing pool (DATA-03, CURVE-09, CURVE-12,
            # CURVE-10); a REF-06 entry keeps its shape and implies REF-06
            record["rule"] = withheld_rule(item)
        if item.get("detail"):
            record["detail"] = dict(item["detail"])
        out.append(record)
    return out


def _withheld_statement(evidence: dict, metric: str) -> str:
    """Why this metric is not scored, naming every rung that refused it.

    "Unassessed" with no reason is what the coverage gate exists to stop, so a
    withheld metric states the hierarchy AND the three rungs above it.
    """
    base = ("Insufficient reference support. No station pool, from this ecoregion's own "
            "least-disturbed streams or from a wider region under the regional screen, holds "
            "enough comparable stations with a value for this metric and passes acceptance.")
    d = ((evidence.get("insufficient_support") or {}).get(metric) or {}).get("decision") or {}
    pools = [x for x in d.get("options_tried") or [] if x.get("why")]
    if pools:
        base += " " + " ".join(_pool_sentence(x) for x in pools)
    tried = [a for a in (evidence.get("ladder_attempts") or [])
             if a.get("metric") == metric and a.get("rung")]
    if not tried:
        return base + " No curve was built and the metric is not scored."
    # Reader-facing: each refusal as its own sentence under the basis name a DEEP
    # reader sees on a card, never the rule code (REF-08) or the fitness code (PB-1).
    parts = []
    for a in tried:
        why = re.sub(r"^PB-\d+\s+", "", str(a.get("why") or "").strip())
        if why and not why.endswith("."):
            why += "."
        parts.append(f"{RUNG_LABELS.get(a['rung'], a['rung'])}: {why}")
    return (base + " No other basis supports it either. " + " ".join(parts)
            + " No curve was built and the metric is not scored.")


#: The reason a built curve is held for a reviewer, in words a DEEP reader can
#: act on. DATA-03 is worded from the measured fraction instead (see
#: :func:`held_for_review`), and no entry names a rule code.
HELD_REASONS = {
    run_state.CURVE_STATUS_DEGENERATE: ("its lower quartile sits at or below zero, so the "
                                        "curve falls back to a default shape"),
    run_state.CURVE_STATUS_SHAPE_CONFLICT: ("its shape conflicts with the response expected "
                                            "of the metric"),
    run_state.CURVE_STATUS_STRAT_REVIEW: "its stratification needs review",
    run_state.CURVE_STATUS_MULTI_CROSSING: "it crosses a scoring threshold more than twice",
    run_state.CURVE_STATUS_INSUFFICIENT: "a stratum holds fewer than 5 reference observations",
    run_state.CURVE_STATUS_UNMAPPED: "it is not assigned to a STAF function",
    run_state.CURVE_STATUS_ERROR: "the build raised an error",
}
HELD_FOR_REVIEW = "held-for-review"
#: reason -> the rule a withheld entry is recorded under (the ledger row, the
#: register's decision and the REF-15 override name read it)
WITHHELD_RULES = {INSUFFICIENT_REFERENCE_SUPPORT: "REF-06",
                  DISCRIMINATION_GATE: "CURVE-12",
                  ZERO_INFLATED_POOL: "CURVE-10",
                  HIGH_MISSINGNESS: "DATA-03",
                  MEASUREMENT_PRECISION_FLOOR: "CURVE-09",
                  HELD_FOR_REVIEW: "CURVE-07"}


def held_for_review(evidence: dict, curve_review: dict, *, scored) -> list[dict]:
    """A withheld record for every curve that was built and is still held for a
    reviewer, so a metric the region measures is never missing from the bundle
    without a reason.

    Only a curve still pending is recorded. A finalized curve is scored, a removed
    one carries its reviewer's decision in the provenance, and a metric with no
    defensible pool already has its own record. This states the review as it
    stands and decides nothing: the owner can still finalize or remove the curve.
    """
    scored = set(scored or ())
    skip = scored | set(evidence.get("insufficient_support") or {}) | set(
        evidence.get("ladder_metrics") or {}) | set(evidence.get("carried") or {})
    config = evidence.get("metric_config") or {}
    missingness = evidence.get("missingness") or {}
    rows = evidence.get("curve_rows") or {}
    review_at = float(methodology.threshold("data_rules.max_missingness_review"))
    out = []
    for mk in sorted(curve_review or {}):
        entry = curve_review.get(mk) or {}
        if mk in skip or mk not in rows or not run_state.needs_review(entry):
            continue
        status = str(entry.get("status") or "")
        frac = (missingness.get(mk) or {}).get("missing_fraction")
        if status == run_state.CURVE_STATUS_DATA_REVIEW and isinstance(frac, (int, float)):
            n = int(evidence.get("n_retained") or 0)
            pool = f"this ecoregion's {n} least-disturbed stations" if n else "the reference pool"
            why = (f"{frac:.0%} of {pool} have no value for this metric, above the "
                   f"{review_at:.0%} at which a curve needs a reviewer before it is used, and "
                   f"no reviewer has cleared it yet.")
        else:
            reason = HELD_REASONS.get(status, "it needs a reviewer's decision")
            why = (f"The curve built for this metric needs a reviewer before it is used, "
                   f"because {reason}, and no reviewer has cleared it yet.")
        cfg = config.get(mk) or {}
        functions = _functions_of(mk)
        out.append({
            "metricId": "spring-" + deep_slug(mk), "metricKey": mk,
            "metricName": cfg.get("display_name") or mk, "units": cfg.get("units") or "",
            "functions": functions,
            "functionId": functions[0]["functionId"] if functions else None,
            "reason": HELD_FOR_REVIEW,
            "statement": ("Held for review. " + why + " The curve is not published and the "
                          "metric is not scored."),
            "curveStatus": status or None,
        })
    return out


#: the basis label a reader sees for each source after the station pools. The
#: 0.13 rule codes stay so a published 0.13 record still reads.
RUNG_LABELS = {"REF-08": curve_basis.label_for(curve_basis.NATIONAL),
               "REF-09": curve_basis.label_for(curve_basis.MODELED),
               "REF-10": curve_basis.label_for(curve_basis.PUBLISHED),
               "REF-12": curve_basis.label_for(curve_basis.NATIONAL),
               "REF-13": curve_basis.label_for(curve_basis.MODELED),
               "REF-14": curve_basis.label_for(curve_basis.PUBLISHED),
               # methodology 0.16: the flagged-transfer rung, after every validated source
               rp.RULE_FLAGGED: "Flagged transfer",
               # methodology 0.16: the last resort, EASI's screening method (D19)
               fixed_criteria.LAST_RESORT_RULE: curve_basis.label_for(curve_basis.EASI_SCREENING)}


def _pool_sentence(x: dict) -> str:
    """One pool option's refusal as a reader-facing sentence."""
    label = acceptance.option_label(str(x.get("option") or ""))
    code, level = x.get("region_code"), str(x.get("level") or "")
    # the region a wider pool was drawn from; the ecoregion's own options need none
    where = "" if not code or level == "l3" else f" ({code})"
    why = str(x.get("why") or "").strip()
    if why and not why.endswith("."):
        why += "."
    return f"{label}{where}: {why[:1].upper() + why[1:] if why else ''}".strip()


def reference_method_block(evidence: dict) -> dict:
    """The bundle's top-level ``referenceMethod`` block."""
    screen = evidence.get("reference_screen") or {}
    support = evidence.get("reference_support") or {}
    # a metric a rule withheld keeps its pool record and counts as withheld
    statuses = [rp.STATUS_INSUFFICIENT if is_withheld_record(d) else str(d.get("status"))
                for d in support.values()]
    pool = evidence.get("reference_pool_summary") or {}
    return {"method": METHOD, "methodVersion": screen.get("methodVersion"),
            "screenId": screen.get("id"), "screenTier": screen.get("tier"),
            "screenLabel": screen.get("label"),
            "valuePolicy": (evidence.get("value_selection") or {}).get("policy"),
            "nLocalReference": evidence.get("n_retained"),
            # the candidate panel (rule DATA-10) and, of it, the stations the
            # screen can judge (canal reaches are outside the frame)
            "nCandidates": evidence.get("n_candidates"),
            "nInFrame": pool.get("target_n_frame"),
            "nCurvesLocal": statuses.count(rp.STATUS_LOCAL),
            "nCurvesBorrowed": sum(1 for s in statuses if s.startswith("borrowed")),
            "nCurvesRegionalScreen": statuses.count(rp.STATUS_LOCAL_RELAXED),
            "nCurvesCarried": sum(1 for d in support.values() if d.get("carried_from")),
            # methodology 0.16 (REF-16): curves on a flagged transfer
            "nCurvesFlagged": sum(1 for d in support.values()
                                  if str(d.get("transfer_risk") or "") == rp.RISK_UNVALIDATED
                                  and not is_withheld_record(d)),
            # methodology 0.16 (REF-17): curves on an adopted EASI screening method
            "nCurvesEasiScreening": sum(1 for d in support.values()
                                        if curve_basis.resolve(d.get("basis"))
                                        == curve_basis.EASI_SCREENING
                                        and not is_withheld_record(d)),
            # the evidence-time count: metrics with no defensible pool. The bundle's
            # own nWithheld and nHeldForReview are set at assembly from the list it
            # exports (deep_export.build_deep_assessment_bundle), which differs once
            # owner sources and finalizations move metrics back into scoring blocks.
            "nWithheld": statuses.count(rp.STATUS_INSUFFICIENT),
            "curvesByBasis": _basis_counts(support),
            "statement": _method_statement(support)}


def _basis_counts(support: dict) -> dict:
    """How many curves rest on each rung, so the bundle states its own mix."""
    out: dict = {}
    for d in support.values():
        if str(d.get("status")) == rp.STATUS_INSUFFICIENT or is_withheld_record(d):
            continue
        basis = curve_basis.resolve(d.get("basis"))
        out[basis] = out.get(basis, 0) + 1
    return out


def _method_statement(support: dict) -> str:
    """What this assessment actually did, rather than what the method allows.

    Interior Plateau holds three least-disturbed stations and the Eastern Corn
    Belt Plains none, so a sentence that says their curves come from
    least-disturbed stations of this ecoregion is untrue for them. The statement
    is assembled from the curves that were built.
    """
    counts = _basis_counts(support)
    support = {mk: d for mk, d in support.items() if not is_withheld_record(d)}
    local = sum(1 for d in support.values() if str(d.get("status")) == rp.STATUS_LOCAL)
    borrowed = sum(1 for d in support.values()
                   if str(d.get("status") or "").startswith("borrowed"))
    relaxed = sum(1 for d in support.values()
                  if str(d.get("status")) == rp.STATUS_LOCAL_RELAXED)
    carried = sorted({int(d["carried_from"]) for d in support.values() if d.get("carried_from")})
    n_carried = sum(1 for d in support.values() if d.get("carried_from"))
    parts = ["Reference condition is set by a fixed landscape-pressure screen, never by a score."]
    if local:
        parts.append(f"{local} {_curves(local)} fitted to least-disturbed stations of this "
                     f"ecoregion.")
    if relaxed:
        parts.append(f"{relaxed} {_curves(relaxed)} fitted to this ecoregion's own streams "
                     f"under the documented regional screen, which relaxes agriculture only.")
    if borrowed:
        parts.append(f"{borrowed} {'borrows' if borrowed == 1 else 'borrow'} comparable "
                     f"least-disturbed stations from a wider region, stating the region and "
                     f"the transfer risk.")
    for basis in (curve_basis.NATIONAL, curve_basis.MODELED, curve_basis.PUBLISHED):
        n = counts.get(basis, 0)
        if not n:
            continue
        said = curve_basis.statement_for(basis)
        parts.append(f"{n} {'carries' if n == 1 else 'carry'} the "
                     f"{curve_basis.label_for(basis).lower()} label: "
                     + said[0].lower() + said[1:])
    if n_carried:
        parts.append(f"{n_carried} of these {_curves(n_carried).split()[0]} carried forward "
                     f"unchanged from version {', '.join(str(v) for v in carried)}.")
    flagged = sum(1 for d in support.values()
                  if str(d.get("transfer_risk") or "") == rp.RISK_UNVALIDATED)
    if flagged:
        parts.append(f"{flagged} {'rests' if flagged == 1 else 'rest'} on a flagged transfer: a "
                     "comparable least-disturbed pool the recovery test did not confirm for this "
                     "ecoregion, used because no validated source supports the metric here, "
                     "with transfer risk unvalidated and confidence capped.")
    adopted = counts.get(curve_basis.EASI_SCREENING, 0)
    if adopted:
        parts.append(f"{adopted} {'function rests' if adopted == 1 else 'functions rest'} on "
                     "a method adopted from EASI's national screening method (provisional), "
                     "because no reference pool, national donor, model, published benchmark or "
                     "flagged transfer supported the function in this ecoregion.")
    parts.append("A metric that no basis supports is not scored, and the function it would "
                 "have scored is reported as unassessed.")
    return " ".join(parts)


def _curves(n: int) -> str:
    return "curve is" if n == 1 else "curves are"


def revision_note(evidence: dict) -> str:
    block = reference_method_block(evidence)
    carried = evidence.get("carried_from") or {}
    note = (f"Reference condition is the fixed landscape-pressure screen {block['screenLabel']}. "
            f"{block['nLocalReference']} of {block['nInFrame'] or block['nCandidates']} stations "
            f"of this ecoregion pass. {block['nCurvesLocal']} reference curves use local stations, "
            f"{block['nCurvesBorrowed']} borrow from a wider region, "
            f"{len(evidence.get('fixed_metrics') or {})} metrics use fixed criteria, and "
            f"{block['nWithheld']} metrics are withheld for insufficient reference support.")
    if carried.get("fromVersion"):
        note += (f" {len(evidence.get('carried') or {})} published curves are carried forward "
                 f"unchanged from version {carried['fromVersion']}, and "
                 f"{len(evidence.get('carry_rebuilt') or {})} were rebuilt because the data "
                 f"verification corrected values they rested on.")
    return note


# --------------------------------------------------------------------------- #
# run-folder tables (both CLIs and the review packet read these)
# --------------------------------------------------------------------------- #
def support_frame(result: dict) -> pd.DataFrame:
    """``reference_support.csv``: one row per metric, the curves that were
    built, the metrics withheld, and the fixed-criteria metrics, so the table
    accounts for everything the assessment could have scored."""
    confidence = result.get("confidence") or {}
    discrimination = result.get("discrimination") or {}
    applied = result.get("strata_applied") or {}
    config = result.get("metric_config") or {}
    withheld = result.get("insufficient_support") or {}
    intended = set(result.get("intended_metrics") or [])
    bundle = result.get("bundle") or {}
    if bundle.get("metricsByFunction"):
        # what the bundle actually scores: carried curves are never on the review
        # list, and SELECT-04 leaves some reviewed curves out
        ids = {m.get("metricId") for fn in bundle["metricsByFunction"]
               for m in fn.get("metrics") or []}
        intended = {mk for mk in (result.get("reference_support") or {})
                    if "spring-" + deep_slug(mk) in ids}
    rows = []
    for mk, d in (result.get("reference_support") or {}).items():
        cfg = config.get(mk) or (withheld.get(mk) or {}).get("config") or {}
        disc = discrimination.get(mk) or {}
        rows.append({
            "metric": mk, "display_name": cfg.get("display_name") or mk,
            # a published criterion is scored like the fixed criteria of
            # CURVE-11, so the reviewer tables group it with them rather than
            # with the curves fitted to a population
            "criteria_basis": (fixed_criteria.CRITERIA_BASIS
                               if curve_basis.resolve(d.get("basis")) == curve_basis.PUBLISHED
                               else "reference"),
            "basis": curve_basis.resolve(d.get("basis")),
            "basis_label": curve_basis.label_for(curve_basis.resolve(d.get("basis"))),
            "status": d.get("status"),
            "carried_from": d.get("carried_from"),
            "in_bundle": mk in intended,
            "level": rp.LEVEL_LABELS.get(d.get("level") or "", ""),
            "region_code": d.get("region_code"), "region_name": d.get("region_name"),
            "family": d.get("family"), "n_pool": d.get("n_pool"),
            "n_comparable": d.get("n_comparable"), "n_usable": d.get("n_usable"),
            "n_local": d.get("n_local"), "n_huc12": d.get("n_huc12"),
            "disposition": d.get("disposition"),
            "covariates": ", ".join(d.get("covariates") or []),
            "lithology": ", ".join(d.get("lith_groups") or []),
            "self_coverage": d.get("self_coverage"),
            "supported_level": rp.LEVEL_LABELS.get(d.get("supported_level") or "", ""),
            "transfer_risk": d.get("transfer_risk"),
            # methodology 0.16 (REF-16): the flag, its recovery verdict and its cap
            "flagged": str(d.get("transfer_risk") or "") == rp.RISK_UNVALIDATED,
            "transfer_validation": rp.validation_words(d.get("transfer_validation")),
            "confidence_cap": d.get("confidence_cap"),
            "split_applied": bool((applied.get(mk) or {}).get("applied")),
            "auc_ref_vs_pressure": disc.get("aucRefVsPressure"),
            "discrimination": disc.get("verdict"),
            "confidence": (confidence.get(mk) or {}).get("label"),
            "confidence_total": (confidence.get(mk) or {}).get("total"),
            "transfer_note": d.get("transfer_note"),
        })
    for mk in sorted(result.get("fixed_metrics") or {}):
        e = fixed_criteria.entry_for(mk)
        rows.append({"metric": mk, "display_name": e.get("display_name"),
                     "criteria_basis": fixed_criteria.CRITERIA_BASIS, "status": "fixed",
                     "in_bundle": True, "confidence": fixed_criteria.CONFIDENCE_LABEL,
                     "transfer_note": fixed_criteria.criteria_sentence(e)})
    return pd.DataFrame(rows)


def coverage_exceptions_draft(result: dict, *, recorded_by: str = "") -> list[dict]:
    """A prefilled ``coverage_exceptions`` entry for every function the bundle
    leaves uncovered because its candidate metrics were withheld (REF-06).

    A DRAFT: ``recordedBy`` is blank unless the caller names someone, and the
    publish gate refuses a blank. The owner reads it, edits it, and passes it
    back with --coverage-exceptions. Functions uncovered for any other reason
    are not drafted here; they need their own reason.
    """
    missing = set((result.get("coverage") or {}).get("missingFunctionIds") or [])
    if not missing:
        return []
    by_fn: dict[str, list[dict]] = {}
    for w in withheld_metrics(result):
        for f in w.get("functions") or []:
            if f["functionId"] in missing:
                by_fn.setdefault(f["functionId"], []).append(w)
    # a refused source the owner accepted that nothing could be built from (REF-15)
    unbuilt = {str(d.get("metric")): d.get("source") or {}
               for d in result.get("curve_decisions") or []
               if (d.get("source") or {}).get("failedAtBuild")}
    out = []
    for fid, items in sorted(by_fn.items()):
        names = ", ".join(sorted(str(w.get("metricName")) for w in items))
        owner = " ".join(
            f"The owner accepted {unbuilt[str(w.get('metricKey'))].get('title')} for "
            f"{w.get('metricName')}, and nothing could be built from it: "
            f"{unbuilt[str(w.get('metricKey'))].get('failedAtBuild')}"
            for w in items if str(w.get("metricKey")) in unbuilt)
        pools = [w for w in items if withheld_reason(w) == INSUFFICIENT_REFERENCE_SUPPORT]
        ruled = [w for w in items if withheld_reason(w) != INSUFFICIENT_REFERENCE_SUPPORT]
        text = f"Every candidate metric for this function ({names}) was withheld."
        if pools:
            text += (" No station pool from this ecoregion or a wider region under the "
                     "regional screen passed acceptance, no comparable national reference "
                     "passed, no approved modeled reference applies here, and the verified "
                     "catalog holds no applicable published criterion."
                     if not ruled else
                     " For " + ", ".join(sorted(str(w.get("metricName")) for w in pools))
                     + " no station pool, national reference, modeled reference or published "
                       "criterion passed acceptance.")
        for w in ruled:
            # a metric withheld by a rule that judged an existing pool says which
            # rule, in the record's own words
            text += (f" {w.get('metricName')} was withheld by "
                     f"{WITHHELD_REASON_WORDS.get(withheld_reason(w), withheld_reason(w))}: "
                     f"{str(w.get('statement') or '').strip()}")
        text += " No curve was forced." + _blocker_detail(result, pools)
        out.append({
            "functionId": fid, "reason": "insufficient-reference-support",
            "justification": text + (" " + owner if owner else ""),
            "recordedBy": recorded_by, "recordedAt": None})
    return out


def _blocker_detail(result: dict, items: list[dict]) -> str:
    """The specific refusal for the published rung, where there is one.

    A function that is unassessed has to name its blocker. The published rung is
    the last one tried, so its reason is the one a reader most needs.
    """
    keys = {str(w.get("metricKey")) for w in items}
    for a in (result.get("ladder_attempts") or []):
        if a.get("rung") in ("REF-10", "REF-14") and str(a.get("metric")) in keys and a.get("why"):
            return " " + str(a["why"])
    return ""


def _with_fixed_mapping(mapping, fixed_keys: list[str]) -> pd.DataFrame:
    """The workbench mapping with the fixed metrics' own assignments appended.
    Their functions are part of the criterion, so an earlier row for one of
    them is replaced, never merged."""
    base = mapping if isinstance(mapping, pd.DataFrame) else pd.DataFrame(
        columns=["metric_key", "discipline", "function_label", "sort_order"])
    if len(base) and "metric_key" in base.columns:
        base = base[~base["metric_key"].astype(str).isin(fixed_keys)]
    order = (pd.to_numeric(base["sort_order"], errors="coerce")
             if "sort_order" in base.columns else pd.Series(dtype="float64"))
    start = int(order.max()) if order.notna().any() else 0
    extra = fixed_criteria.mapping_rows(fixed_keys)
    extra["sort_order"] = range(start + 1, start + 1 + len(extra))
    return pd.concat([base, extra], ignore_index=True)


def _with_last_resort_mapping(mapping, keys: list[str]) -> pd.DataFrame:
    """The mapping with each last-resort metric's own function appended (REF-17):
    the function it completes is part of the method, so an earlier row for the
    metric is replaced, never merged."""
    base = mapping if isinstance(mapping, pd.DataFrame) else pd.DataFrame(
        columns=["metric_key", "discipline", "function_label", "sort_order"])
    if len(base) and "metric_key" in base.columns:
        base = base[~base["metric_key"].astype(str).isin(keys)]
    order = (pd.to_numeric(base["sort_order"], errors="coerce")
             if "sort_order" in base.columns else pd.Series(dtype="float64"))
    start = int(order.max()) if order.notna().any() else 0
    extra = fixed_criteria.last_resort_mapping_rows(keys)
    extra["sort_order"] = range(start + 1, start + 1 + len(extra))
    return pd.concat([base, extra], ignore_index=True)


SESSION_ANNOTATION_KEYS = ("criteriaBasis", "basis", "basisLabel", "basisStatement",
                           "basisLimit", "referenceSupport", "localComparison",
                           "stratifier", "discrimination", "methodContext",
                           # methodology 0.16 (REF-16): a flagged transfer's disclosure
                           "transferRisk", "transferValidation", "transferNote", "confidenceCap")

#: What the headless build writes on a curve it fitted and an interactive
#: republish cannot recompute (``regional_agent.metric_annotations``): the
#: reference sample, its disposition and range, the metric's role, the caveats
#: and the confidence. Kept with the curve they describe, so a republish of an
#: untouched curve states them again and a curve someone changed does not.
FITTED_ANNOTATION_KEYS = ("referenceN", "sampleDisposition", "metricRole", "curveCaveats",
                          "confidenceLabel", "confidenceTotal", "referenceRange")


def _curve_signature(entry: dict) -> dict:
    """What identifies a bundle entry's curve: its points and its class layers."""
    return {"points": (entry.get("curve") or {}).get("points"),
            "layers": entry.get("curveLayers")}


def fitted_annotations_of(bundle: Optional[dict], metrics) -> dict:
    """``{metric: {"curve": signature, <FITTED_ANNOTATION_KEYS>}}`` for ``metrics``
    (the curves the build fitted) from a built bundle."""
    from .deep_export import deep_slug
    ids = {"spring-" + deep_slug(str(mk)): str(mk) for mk in metrics or ()}
    out: dict = {}
    for blk in (bundle or {}).get("metricsByFunction") or []:
        for m in blk.get("metrics") or []:
            mk = ids.get(str(m.get("metricId")))
            if mk is None or mk in out:
                continue
            kept = {k: m[k] for k in FITTED_ANNOTATION_KEYS if m.get(k) is not None}
            if kept:
                out[mk] = {"curve": _curve_signature(m), **kept}
    return out


def carry_fitted_annotations(bundle: dict, stored: Optional[dict], *, metrics=None) -> list[str]:
    """Put the annotations of :data:`FITTED_ANNOTATION_KEYS` back on every entry
    of ``bundle`` whose curve is exactly the one ``stored`` describes
    (:func:`fitted_annotations_of` of the build that fitted it). ``metrics``, when
    given, limits it to those curves (the ones whose review is unchanged). A key
    the entry already states is kept. Returns the metrics carried.

    A curve the build left in no function (SELECT-04) had no entry to take a
    signature from, so it is stored without one: its unchanged review, which
    only ``metrics`` can vouch for, is what lets an owner's include restore it."""
    from .deep_export import deep_slug
    stored = stored or {}
    ids = {"spring-" + deep_slug(str(mk)): str(mk) for mk in stored
           if metrics is None or str(mk) in {str(x) for x in metrics}}
    carried: list[str] = []
    for blk in (bundle or {}).get("metricsByFunction") or []:
        for m in blk.get("metrics") or []:
            mk = ids.get(str(m.get("metricId")))
            if mk is None:
                continue
            saved = stored[mk] or {}
            if saved.get("curve") is None:
                if metrics is None:
                    continue
            elif _curve_signature(m) != saved.get("curve"):
                continue
            for k in FITTED_ANNOTATION_KEYS:
                if saved.get(k) is not None and m.get(k) is None:
                    m[k] = saved[k]
            if mk not in carried:
                carried.append(mk)
    return carried


def session_reference_build(result: dict) -> Optional[dict]:
    """The session's ``reference_build`` field: what an interactive republish
    needs to keep the reference statement of a pressure-method build (the
    fixed-criteria metrics, the reference support of every curve, the withheld
    list). None on a legacy run, so an older session reads as legacy."""
    if result.get("reference_method") != METHOD:
        return None
    meta = result.get("meta") or {}
    fixed = sorted(result.get("fixed_metrics") or {})
    ladder = set(result.get("ladder_metrics") or {})
    # what the build itself stated, before the owner's decisions (REF-15) changed
    # the bundle: the session keeps the decisions beside it, so they can be undone
    stated = (result["base_metric_annotations"]
              if result.get("base_metric_annotations") is not None
              else meta.get("metricAnnotations") or {})
    withheld = (result["base_withheld"] if result.get("base_withheld") is not None
                else meta.get("insufficientReferenceSupport") or [])
    annotations = {
        mk: ({k: v for k, v in ann.items() if v is not None} if mk in ladder else
             {k: ann[k] for k in SESSION_ANNOTATION_KEYS if ann.get(k) is not None})
        for mk, ann in stated.items() if mk not in fixed}
    carried = result.get("carried") or {}
    for mk in carried:
        annotations.pop(mk, None)
    # what only the build can say about the curves it fitted, kept with the
    # curve it describes (FITTED_ANNOTATION_KEYS)
    fitted = [mk for mk in (result.get("curve_review") or {})
              if mk not in ladder and mk not in carried and mk not in fixed]
    fitted_annotations = fitted_annotations_of(result.get("bundle"), fitted)
    # a fitted curve SELECT-04 left in no function has no bundle entry; what the
    # build stated for it is kept too, so an owner's include (REF-15) published
    # from the workspace states what the build would
    for mk in fitted:
        ann = stated.get(mk) or {}
        kept = {k: ann[k] for k in FITTED_ANNOTATION_KEYS if ann.get(k) is not None}
        if mk not in fitted_annotations and kept:
            fitted_annotations[mk] = {"curve": None, **kept}
    out = {"method": METHOD,
           "referenceMethod": meta.get("referenceMethod"),
           "insufficientReferenceSupport": list(withheld),
           "metricAnnotations": annotations,
           "fixedMetrics": fixed,
           "ladderMetrics": ladder_session_rows(result),
           "referenceTier": result.get("reference_tier")}
    if carried:
        # the published points themselves, so a republish restores them exactly
        out["carriedMetrics"] = carry_forward.session_rows(carried)
        out["carriedFrom"] = dict(result.get("carried_from") or {})
    # the build's own portfolio: the owner's decisions ride beside it
    # (owner_curve_decisions), so the session can undo them
    selection = result.get("base_portfolio_selection", meta.get("portfolioSelection"))
    if selection:
        out["portfolioSelection"] = selection
    if fitted_annotations:
        out["fittedAnnotations"] = fitted_annotations
    held = result.get("held_by_owner") or {}
    if held:
        # the metrics the owner's decisions held out of the fit whose station pool
        # would have supported a curve (REF-15): what an undo says it gives back
        out["ownerHeld"] = {mk: held_summary((h or {}).get("decision") or {})
                            for mk, h in sorted(held.items())}
    return out


def held_summary(decision: dict) -> dict:
    """What a held metric's station pool was, in the session's shape."""
    return {"status": decision.get("status"), "level": decision.get("level"),
            "regionCode": decision.get("region_code"), "regionName": decision.get("region_name"),
            "nUsable": decision.get("n_usable")}


def held_words(held: Optional[dict]) -> str:
    """A held metric's pool in words: "this ecoregion's own reference, 12 stations"
    or "the Level II 8.3 pool (Ozark, Ouachita-Appalachian Forests), 34 stations"."""
    h = held or {}
    n = h.get("nUsable")
    count = f", {int(n)} stations" if isinstance(n, (int, float)) else ""
    if str(h.get("status") or "").startswith("local"):
        return f"this ecoregion's own reference{count}"
    level = {"l2": "Level II", "l1": "Level I", "l3": "Level III",
             "nars9": "NARS-9"}.get(str(h.get("level") or ""), "")
    where = " ".join(x for x in (level, str(h.get("regionCode") or "")) if x)
    name = f" ({h['regionName']})" if h.get("regionName") else ""
    return (f"the {where} pool" if where else "a regional pool") + f"{name}{count}"


def ladder_session_rows(result: dict) -> dict:
    """The curves a rung above the hierarchy produced, as the session stores them.

    A fixed-criteria curve is rebuilt on republish from the vendored catalog,
    which is deterministic. A modelled curve is not: rebuilding it would mean
    refitting, and a refit is a new estimate rather than the one that was
    published. So the session carries the points themselves, and a republish
    restores exactly the curve that was built.
    """
    out: dict = {}
    for mk, row in (result.get("ladder_metrics") or {}).items():
        points = curves.normalize_reference_curve_points(row.get("curve_points"))
        if not len(points):
            continue
        cfg = (result.get("ladder_config") or {}).get(mk) or {}
        out[mk] = {
            "displayName": row.get("display_name") or cfg.get("display_name") or mk,
            "curveStatus": row.get("curve_status") or "complete",
            "curveSource": row.get("curve_source") or "auto",
            "nReference": row.get("n_reference"),
            "stratum": row.get("stratum") or "",
            "config": cfg,
            "points": [{"x": float(r.metric_value), "y": float(r.index_score)}
                       for r in points.itertuples(index=False)],
        }
    return out


def fixed_function_ids(build: Optional[dict]) -> list[str]:
    """Function ids the fixed-criteria metrics of a session's build cover. They
    join the bundle outside the editable mapping, so the coverage views need
    them named. Empty for a legacy session."""
    if not build or build.get("method") != METHOD:
        return []
    fixed = tuple(mk for mk in (build.get("fixedMetrics") or []) if fixed_criteria.is_fixed(mk))
    return list(_function_ids_of(fixed))


def fixed_metric_counts(build: Optional[dict]) -> dict:
    """How many fixed-criteria metrics each function carries, ``{function id: n}``.

    The counting form of :func:`fixed_function_ids`, for the SELECT-01 count the
    Publish page makes before any bundle exists (a function can reach the portfolio
    maximum on fixed metrics alone). Empty for a legacy session.
    """
    if not build or build.get("method") != METHOD:
        return {}
    fixed = tuple(mk for mk in (build.get("fixedMetrics") or []) if fixed_criteria.is_fixed(mk))
    return dict(_function_counts_of(fixed))


@lru_cache(maxsize=8)
def _function_counts_of(fixed: tuple) -> tuple:
    # cached like _function_ids_of, and a tuple of pairs so the cache can hold it
    from . import regional_agent as ra
    counts: dict[str, int] = {}
    for r in fixed_criteria.mapping_rows(list(fixed)).to_dict("records"):
        fid = ra._canonical_function_id(r["function_label"])
        if fid:
            counts[fid] = counts.get(fid, 0) + 1
    return tuple(counts.items())


@lru_cache(maxsize=8)
def _function_ids_of(fixed: tuple) -> tuple:
    # cached: the workflow strip asks on every render
    from . import regional_agent as ra
    out: list[str] = []
    for r in fixed_criteria.mapping_rows(list(fixed)).to_dict("records"):
        fid = ra._canonical_function_id(r["function_label"])
        if fid and fid not in out:
            out.append(fid)
    return tuple(out)


# --------------------------------------------------------------------------- #
# The curves a session scores that its own build did not fit
# --------------------------------------------------------------------------- #
#: How each kind of curve reads in the workspace, which shows them read-only.
REFERENCE_KIND_LABELS = {
    "national": curve_basis.label_for(curve_basis.NATIONAL),
    "modeled": curve_basis.label_for(curve_basis.MODELED),
    "published_benchmark": curve_basis.label_for(curve_basis.PUBLISHED),
    "fixed": "Fixed criterion",
}


def reference_keys(build: Optional[dict]) -> list[str]:
    """Every metric a pressure-screen session scores that its own build did not
    fit: the curves a rung above the hierarchy produced, the curves carried
    forward from the published version, the curves the owner chose (REF-15,
    ``ownerMetrics`` of an effective build) and the fixed criteria. None of them
    sits in ``metric_config``, so nothing that walks the workbook sees them.
    Empty for a legacy session."""
    if not build or build.get("method") != METHOD:
        return []
    keys: list[str] = []
    for mk in (list(build.get("ladderMetrics") or {}) + list(build.get("carriedMetrics") or {})
               + list(build.get("ownerMetrics") or {})):
        if str(mk) not in keys:
            keys.append(str(mk))
    for mk in build.get("fixedMetrics") or []:
        if fixed_criteria.is_fixed(mk) and str(mk) not in keys:
            keys.append(str(mk))
    return keys


def fixed_in_order(keys) -> tuple:
    """The fixed-criteria metrics of ``keys`` in the criteria's own order, the order
    a build places them in (the session stores the keys sorted)."""
    wanted = {str(k) for k in keys or ()}
    return tuple(mk for mk in fixed_criteria.metric_keys() if mk in wanted)


def not_selected_pairs(build: Optional[dict]) -> set:
    """``{(metric, function id)}`` SELECT-04 recorded as supported, not selected:
    the pairs the bundle leaves out (:func:`_apply_selection`)."""
    selection = (build or {}).get("portfolioSelection") or {}
    return {(str(x.get("metric")), str(fid)) for fid, sel in selection.items()
            for x in (sel or {}).get("notSelected") or []}


@lru_cache(maxsize=512)
def _function_id_for(label: str) -> Optional[str]:
    # cached: the workflow strip places every reference curve on each render
    from . import regional_agent as ra
    return ra._canonical_function_id(label)


def _row_function_id(row) -> Optional[str]:
    label = (row or {}).get("function_label")
    return _function_id_for(str(label)) if isinstance(label, str) and label.strip() else None


def _ids_of(rows) -> list[str]:
    out: list[str] = []
    for r in rows or []:
        fid = _row_function_id(r)
        if fid and fid not in out:
            out.append(fid)
    return out


def _session_mapping_rows(mapping, keys) -> dict[str, list[dict]]:
    """``{metric: [mapping row, ...]}`` for ``keys`` from the session's mapping, in
    ``sort_order``, so a metric's primary function comes first."""
    out: dict[str, list[dict]] = {}
    if not keys or not isinstance(mapping, pd.DataFrame) or not len(mapping) \
            or "metric_key" not in mapping.columns or "function_label" not in mapping.columns:
        return out
    m = mapping
    if "sort_order" in m.columns:
        m = m.assign(_order=pd.to_numeric(m["sort_order"], errors="coerce")).sort_values(
            "_order", kind="stable")
    disc = (m["discipline"] if "discipline" in m.columns
            else pd.Series([None] * len(m), index=m.index))
    for mk, d, label in zip(m["metric_key"], disc, m["function_label"]):
        if not isinstance(mk, str) or mk not in keys:
            continue
        if not isinstance(label, str) or not label.strip():
            continue
        out.setdefault(mk, []).append({"metric_key": mk, "discipline": d,
                                       "function_label": label})
    return out


@lru_cache(maxsize=8)
def _fixed_mapping_of(fixed: tuple) -> tuple:
    # cached like _function_ids_of: the criteria's own assignments, as row tuples
    rows = fixed_criteria.mapping_rows(list(fixed)).to_dict("records")
    return tuple((str(r.get("metric_key")), r.get("discipline"), r.get("function_label"))
                 for r in rows)


def _ladder_kind(saved: dict, annotation: Optional[dict]) -> str:
    basis = curve_basis.resolve((annotation or {}).get("basis"),
                                criteria_basis=(annotation or {}).get("criteriaBasis"))
    if basis == curve_basis.PUBLISHED or saved.get("curveSource") == "published_benchmark":
        return "published_benchmark"
    return "modeled" if basis == curve_basis.MODELED else "national"


def reference_mapping_rows(build: Optional[dict], mapping=None, *, built=()) -> dict[str, dict]:
    """``{metric: {kind, mapping, functions}}`` for every curve the session scores
    without having fitted it, placed exactly as :func:`apply_reference_build`
    places it in the bundle: a curve a rung above the hierarchy by the
    session's own mapping rows, a carried curve by its published placement, a
    fixed criterion by the criterion. SELECT-04's not-selected pairs come out.

    Cheap, with no curve row built, so the quick coverage and counts can ask on
    every render. ``built``: metrics the session fitted itself, which win over a
    ladder or carried curve of the same key, as they do in the republish."""
    if not build or build.get("method") != METHOD:
        return {}
    built = {str(k) for k in built or ()}
    dropped = not_selected_pairs(build)
    ladder = {str(mk): s for mk, s in (build.get("ladderMetrics") or {}).items()
              if (s or {}).get("points") and str(mk) not in built}
    carried_keys = {str(mk) for mk in (build.get("carriedMetrics") or {})}
    session_rows = _session_mapping_rows(mapping, set(ladder) | carried_keys)
    annotations = build.get("metricAnnotations") or {}
    placed: dict[str, dict] = {}
    for mk, saved in ladder.items():
        placed[mk] = {"kind": _ladder_kind(saved, annotations.get(mk)),
                      "mapping": list(session_rows.get(mk) or [])}
    for mk, saved in (build.get("carriedMetrics") or {}).items():
        if str(mk) in placed or str(mk) in built:
            continue
        # the session's rows when it has them, as the republish places it
        rows_here = session_rows.get(str(mk))
        placed[str(mk)] = {"kind": "carried",
                           "mapping": (list(rows_here) if rows_here else
                                       [dict(r) for r in (saved or {}).get("mapping") or []])}
    # REF-15: a curve the owner chose, placed in the functions its decision names
    owner = build.get("ownerMetrics") or {}
    if owner:
        from . import owner_sources
        for mk, saved in owner.items():
            if str(mk) in placed or str(mk) in built:
                continue
            src = ((saved or {}).get("decision") or {}).get("source") or {}
            placed[str(mk)] = {
                "kind": owner_sources.DISPLAY_KIND.get(str(src.get("kind")), "owner_entered"),
                "owner": True,
                "mapping": owner_sources.mapping_rows_for(mk, (saved or {}).get("functions"))}
    fixed = fixed_in_order(build.get("fixedMetrics"))
    if fixed:
        rows = _fixed_mapping_of(fixed)
        for mk in fixed:
            placed[mk] = {"kind": "fixed",
                          "mapping": [{"metric_key": k, "discipline": d, "function_label": f}
                                      for k, d, f in rows if k == mk]}
    for mk, entry in placed.items():
        entry["mapping"] = [r for r in entry["mapping"]
                            if (mk, str(_row_function_id(r))) not in dropped]
        entry["functions"] = _ids_of(entry["mapping"])
    return placed


def canonical_function_id(label) -> Optional[str]:
    """The canonical STAF function id of a function label, or None."""
    text = "" if label is None else str(label).strip()
    return _function_id_for(text) if text else None


def reference_summary(build: Optional[dict], *, built=()) -> dict:
    """``{kind: n}`` of the curves a session scores without having fitted them,
    plus ``fromVersion`` for the carried ones. The curves the owner chose count
    under ``owner``. Empty for a legacy session."""
    out: dict = {}
    for entry in reference_mapping_rows(build, None, built=built).values():
        kind = "owner" if entry.get("owner") else entry["kind"]
        out[kind] = out.get(kind, 0) + 1
    if out.get("carried"):
        out["fromVersion"] = ((build or {}).get("carriedFrom") or {}).get("fromVersion")
    return out


def reference_summary_text(summary: Optional[dict]) -> str:
    """"19 carried from v7, 2 modeled and 5 fixed criteria", or empty."""
    s = summary or {}
    parts = []
    if s.get("carried"):
        ver = s.get("fromVersion")
        parts.append(f"{s['carried']} carried from v{ver}" if ver else f"{s['carried']} carried forward")
    for kind, word in (("national", "national"), ("modeled", "modeled"),
                       ("published_benchmark", "published benchmark")):
        if s.get(kind):
            parts.append(f"{s[kind]} {word}")
    if s.get("fixed"):
        parts.append(f"{s['fixed']} fixed criteri{'on' if s['fixed'] == 1 else 'a'}")
    if s.get("owner"):
        parts.append(f"{s['owner']} chosen by the owner")
    if len(parts) > 1:
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    return parts[0] if parts else ""


def reference_function_ids(build: Optional[dict], mapping=None, *, built=()) -> list[str]:
    """Function ids the curves of :func:`reference_mapping_rows` cover: the
    ``always_covered`` of every quick coverage view, so an opened session
    reports the functions its bundle covers."""
    out: list[str] = []
    for entry in reference_mapping_rows(build, mapping, built=built).values():
        for fid in entry["functions"]:
            if fid not in out:
                out.append(fid)
    return out


def reference_metric_counts(build: Optional[dict], mapping=None, *, built=()) -> dict:
    """How many of those curves each function carries, ``{function id: n}``: the
    ``extra`` of the quick SELECT-01 count."""
    counts: dict[str, int] = {}
    for entry in reference_mapping_rows(build, mapping, built=built).values():
        for fid in entry["functions"]:
            counts[fid] = counts.get(fid, 0) + 1
    return counts


def reference_rows(build: Optional[dict], mapping=None, *, built=()) -> dict[str, dict]:
    """Every curve of :func:`reference_mapping_rows`, rehydrated with the
    constructors the republish uses: :func:`_ladder_row`,
    ``carry_forward.restore_rows`` and ``fixed_criteria.curve_row``.

    Each entry: ``row`` (a curve row with ``curve_points``), ``config``,
    ``annotations``, ``mapping`` (the rows that place it), ``functions``,
    ``kind`` (carried, national, modeled, published_benchmark, fixed, and for a
    curve the owner chose also owner_entered, borrowed and owner_exception),
    ``label`` ("Carried from v7", "National reference", ...), ``basis`` and
    ``in_bundle`` (false for a curve SELECT-04 left in no function). A curve the
    owner chose also carries ``owner``, the decision's summary. The workspace
    draws these read-only."""
    placed = reference_mapping_rows(build, mapping, built=built)
    if not placed:
        return {}
    ladder = build.get("ladderMetrics") or {}
    annotations = build.get("metricAnnotations") or {}
    carried = carry_forward.restore_rows(
        {mk: s for mk, s in (build.get("carriedMetrics") or {}).items()
         if (placed.get(str(mk)) or {}).get("kind") == "carried"})
    from_version = (build.get("carriedFrom") or {}).get("fromVersion")
    fixed = [mk for mk, p in placed.items() if p["kind"] == "fixed"]
    fixed_config = fixed_criteria.metric_config_entries() if fixed else {}
    fixed_ann = fixed_annotations(fixed) if fixed else {}
    out: dict[str, dict] = {}
    owner = build.get("ownerMetrics") or {}
    for mk, p in placed.items():
        kind = p["kind"]
        decision = None
        if p.get("owner"):
            from . import owner_curves, owner_sources
            saved = owner.get(mk) or {}
            decision = dict(saved.get("decision") or {})
            c = carry_forward.restore_rows({mk: saved.get("curve") or {}})[mk]
            row, config, ann = dict(c["row"]), dict(c["config"]), dict(c["annotations"])
            row["curve_source"] = owner_sources.CURVE_SOURCE
            ann["ownerDecision"] = owner_curves.decision_annotation(decision)
            label = owner_sources.label_for(decision.get("source"))
        elif kind == "carried":
            c = carried[mk]
            row, config, ann = c["row"], dict(c["config"]), dict(c["annotations"])
            label = f"Carried from v{from_version}" if from_version else "Carried forward"
        elif kind == "fixed":
            row = fixed_criteria.curve_row(mk)
            config, ann = dict(fixed_config.get(mk) or {}), dict(fixed_ann.get(mk) or {})
            label = REFERENCE_KIND_LABELS["fixed"]
        else:
            saved = ladder[mk]
            row, config = _ladder_row(mk, saved), dict(saved.get("config") or {})
            ann = dict(annotations.get(mk) or {})
            label = REFERENCE_KIND_LABELS[kind]
        out[mk] = {"row": row, "config": config, "annotations": ann,
                   "mapping": list(p["mapping"]), "functions": list(p["functions"]),
                   "kind": kind, "label": label,
                   "basis": curve_basis.resolve(ann.get("basis"),
                                                criteria_basis=ann.get("criteriaBasis")),
                   "in_bundle": bool(p["functions"])}
        if decision is not None:
            out[mk]["owner"] = decision
    return out


def apply_reference_build(build: Optional[dict], curve_rows: dict, mapping,
                          metric_config: dict, meta: dict, *,
                          apply_selection: bool = True) -> tuple[dict, object, dict]:
    """Fold a session's ``reference_build`` into an interactive bundle build.

    Returns ``(curve_rows, mapping, metric_config)`` with the fixed-criteria
    metrics added, and sets the reference keys of ``meta``. A session without
    the field (every legacy session) passes through untouched.
    """
    if not build or build.get("method") != METHOD:
        return curve_rows, mapping, metric_config
    rows = dict(curve_rows)
    config = dict(metric_config or {})
    annotations = dict(meta.get("metricAnnotations") or {})
    # Ladder curves first: they are not in the pooled frame, so the interactive
    # build never produced a row for them, and the annotation loop below matches
    # on rows. Restored second, their annotations were silently dropped.
    _restore_ladder_rows(build, rows, config)
    for mk, ann in (build.get("metricAnnotations") or {}).items():
        if mk in rows:
            annotations[mk] = {**(annotations.get(mk) or {}), **ann}
    # methodology 0.14: carried-forward curves, restored from their published points.
    # They are placed by the session's own mapping rows, which are the rows the
    # build published them under, in the build's order; the placement stored with
    # the curve stands in only for one the session has no row for (an open before
    # 2026-09-21 deleted them).
    carried = carry_forward.restore_rows(build.get("carriedMetrics") or {})
    if carried:
        _add_carried(carried, rows, config, annotations)
        placed = _session_mapping_rows(mapping, set(carried))
        extra = carry_forward.mapping_rows(
            {mk: c for mk, c in carried.items() if mk not in placed})
        if len(extra):
            base = mapping if isinstance(mapping, pd.DataFrame) else pd.DataFrame(
                columns=["metric_key", "discipline", "function_label", "sort_order"])
            if len(base) and "metric_key" in base.columns:
                base = base[~base["metric_key"].astype(str).isin(
                    [mk for mk in carried if mk not in placed])]
            order = (pd.to_numeric(base["sort_order"], errors="coerce")
                     if "sort_order" in base.columns else pd.Series(dtype="float64"))
            start = int(order.max()) if order.notna().any() else 0
            extra = extra.assign(sort_order=range(start + 1, start + 1 + len(extra)))
            mapping = pd.concat([base, extra], ignore_index=True)
    fixed = list(fixed_in_order(build.get("fixedMetrics")))
    if fixed:
        fixed_config = fixed_criteria.metric_config_entries()
        for mk in fixed:
            rows[mk] = fixed_criteria.curve_row(mk)
            config[mk] = fixed_config[mk]
        annotations.update(fixed_annotations(fixed))
        mapping = _with_fixed_mapping(mapping, fixed)
    # SELECT-04: the curves the build recorded as supported, not selected stay
    # out of the functions it left them out of, so a republish scores what the
    # build published
    # (apply_selection=False leaves the drop to owner_curves.apply_to_inputs, which
    # applies it under the owner's decisions exactly as a build does)
    if apply_selection:
        rows, mapping = _apply_selection(build.get("portfolioSelection") or {}, rows, mapping,
                                         keep=set(fixed))
    if build.get("portfolioSelection"):
        meta["portfolioSelection"] = build["portfolioSelection"]
    meta["metricAnnotations"] = annotations
    if build.get("referenceMethod"):
        meta["referenceMethod"] = build["referenceMethod"]
    withheld = [w for w in (build.get("insufficientReferenceSupport") or [])
                if w.get("metricKey") not in rows]
    if withheld:
        meta["insufficientReferenceSupport"] = withheld
    if build.get("referenceTier") and not meta.get("referenceTier"):
        meta["referenceTier"] = build["referenceTier"]
    return rows, mapping, config


def _apply_selection(selection: dict, rows: dict, mapping, *, keep: set) -> tuple[dict, object]:
    """Drop each (metric, function) pair SELECT-04 recorded as not selected, and a
    curve left in no function at all."""
    if not selection or not isinstance(mapping, pd.DataFrame) or not len(mapping) \
            or "metric_key" not in mapping.columns:
        return rows, mapping
    from . import regional_agent as ra
    drop = {(str(x.get("metric")), str(fid)) for fid, sel in selection.items()
            for x in (sel or {}).get("notSelected") or []}
    if not drop:
        return rows, mapping
    fid_of = mapping["function_label"].map(
        lambda x: ra._canonical_function_id(x) if isinstance(x, str) and x.strip() else None)
    gone = [(str(m), str(f)) in drop for m, f in zip(mapping["metric_key"], fid_of)]
    mapping = mapping[[not g for g in gone]]
    still = set(mapping["metric_key"].astype(str))
    rows = {mk: r for mk, r in rows.items() if mk in still or mk in keep}
    return rows, mapping


def _restore_ladder_rows(build: dict, rows: dict, config: dict) -> None:
    """Put back the curves a rung above the hierarchy produced, from the points
    the session recorded. Mutates ``rows`` and ``config``."""
    for mk, saved in (build.get("ladderMetrics") or {}).items():
        if mk in rows or not (saved.get("points") or []):
            continue
        rows[mk] = _ladder_row(mk, saved)
        if saved.get("config"):
            config[mk] = saved["config"]


def _ladder_row(mk: str, saved: dict) -> dict:
    """A curve a rung above the hierarchy produced, as a curve row from the points
    the session recorded: the one constructor the republish
    (:func:`_restore_ladder_rows`) and the workspace (:func:`reference_rows`) share."""
    pts = saved.get("points") or []
    return {
        "metric": mk, "display_name": saved.get("displayName") or mk,
        "stratum": saved.get("stratum") or "", "n_reference": saved.get("nReference"),
        "curve_status": saved.get("curveStatus") or "complete",
        "curve_source": saved.get("curveSource") or "auto",
        "curve_points": pd.DataFrame(
            [{"point_order": i + 1, "metric_value": float(q["x"]),
              "index_score": float(q["y"])} for i, q in enumerate(pts)]),
    }


def portfolio_with_fixed(portfolio: list[dict], fixed_keys) -> list[dict]:
    """The SELECT-01 portfolio with the fixed-criteria metrics placed in their
    functions. They count toward a function's metric total, because the
    function score averages them with the rest, and the publish gate counts the
    bundle's blocks the same way."""
    from . import regional_agent as ra
    add: dict[str, list[str]] = {}
    for r in fixed_criteria.mapping_rows(list(fixed_keys or [])).to_dict("records"):
        fid = ra._canonical_function_id(r["function_label"])
        if fid:
            add.setdefault(fid, []).append(str(r["metric_key"]))
    max_per_fn = int(methodology.threshold(
        "metric_portfolio.default_maximum_metrics_per_function"))
    out = []
    for row in portfolio or []:
        extra = [m for m in add.get(str(row.get("function_id")), [])
                 if m not in (row.get("metrics") or [])]
        if not extra:
            out.append(row)
            continue
        metrics = list(row.get("metrics") or []) + extra
        out.append({**row, "coverage": "covered", "metrics": metrics,
                    "n_metrics": len(metrics),
                    "primary_metric": row.get("primary_metric") or metrics[0],
                    "select01_flag": len(metrics) > max_per_fn,
                    "fixed_metrics": extra})
    return out


def ladder_confidence(evidence: dict, ladder_config: dict) -> dict[str, dict]:
    """CONF-01 and CONF-03 for the curves that came from a rung above the
    ecoregion hierarchy.

    ``regional_agent.assemble`` scores confidence by walking ``metric_config``,
    and a ladder metric is deliberately not in it: it carries no station pool and
    so cannot join the pooled frame, the redundancy matrix or the stratifier
    screen. Without this it would publish with no confidence at all, and the
    basis cap would never reach a reader.

    A published criterion gets a label and no number, the convention the fixed
    criteria already use: the six CONF-01 components all describe a curve fitted
    to a sample, and someone else's threshold has none of them. A modelled curve
    is fitted, so it is scored, and it is honest about what it lacks: a synthetic
    population has no leave-one-site-out, so CURVE-02's cap applies.
    """
    from . import confidence as conf
    support = evidence.get("reference_support") or {}
    out: dict[str, dict] = {}
    for mk in ladder_config:
        d = support.get(mk) or {}
        basis = curve_basis.resolve(d.get("basis"))
        if basis == curve_basis.PUBLISHED:
            out[mk] = {"label": curve_basis.label_for(basis), "total": None,
                       "caps_applied": []}
            continue
        if basis == curve_basis.EASI_SCREENING:
            # REF-17: a national screening method, no sample: a label, no number,
            # and the basis cap named so a reader sees why the label is low
            out[mk] = {"label": "EASI screening method (provisional)", "total": None,
                       "caps_applied": [curve_basis.CAP_REASONS[curve_basis.EASI_SCREENING]]}
            continue
        cfg = ladder_config.get(mk) or {}
        got = conf.curve_confidence({
            "basis": basis,
            "transfer_risk": d.get("transfer_risk") or "none",
            "sample_disposition": d.get("disposition"),
            # a modelled population is drawn, not observed, so there is no
            # leave-one-site-out to run and no stability credit to give
            "loo": {"evaluable": False},
            "direction_confidence": cfg.get("direction_confidence"),
            "shape_ok": True, "mapped": True,
            "units_present": bool(cfg.get("units")),
            "reference_tier": evidence.get("tier", {}).get("reference_tier"),
        })
        out[mk] = {"label": got.get("label"), "total": got.get("total"),
                   "caps_applied": got.get("caps_applied") or []}
    return out


def bundle_inputs(evidence: dict, meta: dict, intended_rows: dict,
                  metric_config: dict) -> tuple[dict, dict, pd.DataFrame]:
    """Fold what the pressure method adds into the exporter's inputs.

    Mutates ``meta`` (annotations, ``referenceMethod``,
    ``insufficientReferenceSupport``) and returns the curve rows, the metric
    config and the mapping the exporter should read: the regional curves with
    their class layers, then the fixed-criteria metrics.
    """
    rows = dict(intended_rows)
    applied = evidence.get("strata_applied") or {}
    for mk, class_rows in (evidence.get("stratum_rows") or {}).items():
        if mk in rows and (applied.get(mk) or {}).get("applied"):
            rows[mk] = layered_row(rows[mk], class_rows, applied[mk])

    annotations = meta.setdefault("metricAnnotations", {})
    for mk, extra in reference_annotations(evidence, list(rows)).items():
        merged = dict(annotations.get(mk) or {})
        caveats = list(merged.get("curveCaveats") or []) + list(extra.pop("curveCaveats", []))
        merged.update(extra)
        merged["curveCaveats"] = caveats
        annotations[mk] = merged

    # a ladder curve is a real NRSA metric with a real display name and units,
    # so it joins the rows and the config, and its annotations come from its own
    # pool decision like any other reference curve
    ladder_rows = dict(evidence.get("ladder_metrics") or {})
    ladder_config = dict(evidence.get("ladder_config") or {})
    fixed_rows = dict(evidence.get("fixed_metrics") or {})
    config = dict(metric_config)
    mapping = evidence["mapping_df"]
    if ladder_rows:
        rows.update(ladder_rows)
        config.update({mk: ladder_config[mk] for mk in ladder_rows if mk in ladder_config})
        ladder_conf = ladder_confidence(evidence, ladder_config)
        for mk, extra in reference_annotations(evidence, list(ladder_rows)).items():
            merged = dict(annotations.get(mk) or {})
            caveats = list(merged.get("curveCaveats") or []) + list(
                extra.pop("curveCaveats", []))
            merged.update(extra)
            basis = merged.get("basis")
            limit = curve_basis.limit_for(basis)
            if limit and limit not in caveats:
                caveats.append(limit)
            merged["curveCaveats"] = caveats
            # the sentence rides at the metric, where a reader looks for it, as it
            # does for the fixed criteria, and not only inside referenceSupport
            merged["basisStatement"] = curve_basis.statement_for(basis)
            merged["basisLimit"] = limit or ""

            if basis == curve_basis.NATIONAL and merged.get("referenceN") is None:
                # R5-12: the donors behind a national-reference curve, as a pool
                # curve states its stations (referenceSupport keeps nPool beside it)
                sup = (evidence.get("reference_support") or {}).get(mk) or {}
                n_ref = (ladder_rows.get(mk) or {}).get("n_reference") or sup.get("n_usable")
                if n_ref:
                    merged["referenceN"] = int(n_ref)
            got = ladder_conf.get(mk) or {}
            if got.get("label"):
                merged["confidenceLabel"] = got["label"]
            if got.get("total") is not None:
                merged["confidenceTotal"] = got["total"]
            if got.get("caps_applied"):
                merged["confidenceCaps"] = got["caps_applied"]
            if basis == curve_basis.PUBLISHED:
                # A published criterion behaves like the fixed criteria of
                # CURVE-11 everywhere downstream: no training population, so no
                # train/serve pairing check, no domain-clamp warning and no
                # reference tier. It differs only in carrying its own
                # provenance and its own region.
                merged["criteriaBasis"] = fixed_criteria.CRITERIA_BASIS
                prov = published_benchmark.provenance(mk)
                region = (ladder_rows.get(mk) or {}).get("benchmark_region")
                if region:
                    # PB-5: the criterion's bands, citations and provisional flag
                    # travel with the curve, and the metric cites the criterion
                    # rather than the regional analysis it did not come from
                    merged["criteriaSource"] = published_benchmark.criteria_source(mk, region)
                    merged["sourceCitation"] = published_benchmark.citation_line(mk, region)
                    prov = {**prov, "region": str(region)}
                else:
                    merged["criteriaSource"] = (prov.get("benchmark")
                                                or merged.get("criteriaSource"))
                merged["publishedBenchmark"] = prov
            elif basis == curve_basis.EASI_SCREENING:
                # REF-17 (owner decision D19): an adopted EASI screening method
                # behaves like the fixed criteria downstream (no training
                # population, no pairing check) and carries the method's own
                # evidentiary status, citations and limitations, the input the
                # user supplies and the limitation of this use
                entry = fixed_criteria.last_resort_entry(mk)
                merged["criteriaBasis"] = fixed_criteria.CRITERIA_BASIS
                merged["criteriaSource"] = fixed_criteria.criteria_source(entry)
                merged["sourceCitation"] = fixed_criteria.last_resort_citation_line(entry)
                merged["metricRole"] = fixed_criteria.LAST_RESORT_ROLE
                merged["adoptedMethod"] = fixed_criteria.last_resort_provenance(entry)
                if entry.get("required_input"):
                    merged["methodContext"] = entry["required_input"]
                for text in [entry.get("limitation")] + list(entry.get("limitations") or []):
                    if text and text not in caveats:
                        caveats.append(text)
                merged["curveCaveats"] = caveats
            annotations[mk] = merged
    carried = dict(evidence.get("carried") or {})
    if carried:
        _add_carried(carried, rows, config, annotations)
    if fixed_rows:
        rows.update(fixed_rows)
        fixed_config = fixed_criteria.metric_config_entries()
        config.update({mk: fixed_config[mk] for mk in fixed_rows})
        annotations.update(fixed_annotations(list(fixed_rows)))
        mapping = _with_fixed_mapping(mapping, list(fixed_rows))

    meta["referenceMethod"] = reference_method_block(evidence)
    withheld = withheld_metrics(evidence)
    if withheld:
        meta["insufficientReferenceSupport"] = withheld
    return rows, config, mapping


def zero_inflation_records(data: pd.DataFrame, metric_config: dict,
                           geometry: Optional[dict] = None) -> dict[str, dict]:
    """The zero-inflation record (``curves.zero_inflation_of``) of every metric
    whose pool is zero-inflated under the configured rule (campaign Round 2
    candidate C3a); empty when the rule is off, the default. The same function
    the curve engine applies, on the same masked pool values, so the record and
    the curve agree by construction."""
    geo = dict(geometry if geometry is not None else curves.seed_geometry())
    if geo.get("zero_inflated_share") is None:
        return {}
    out: dict[str, dict] = {}
    for mk, cfg in metric_config.items():
        if mk not in data.columns:
            continue
        rec = curves.zero_inflation_of(data[mk], cfg or {}, geo)
        if rec:
            out[mk] = rec
    return out


def zero_inflated_statement(rec: dict) -> str:
    """Why a zero-inflated pool withholds its metric (``zero_inflated_handling: withhold``)."""
    return (f"Zero-inflated reference pool. {int(rec.get('n_zero') or 0)} of the "
            f"{int(rec.get('n') or 0)} reference stations ({float(rec.get('share') or 0):.0%}) "
            f"have a value of zero, above the {float(rec.get('threshold') or 0):.0%} at which "
            "a curve seeded on the pool's quartiles would misread the median reference station, "
            "so the metric is withheld under the zero-inflation rule. No curve was built and "
            "the metric is not scored.")


def discrimination_gate(discrimination: dict, settings: Optional[dict] = None) -> dict[str, dict]:
    """The curves the CURVE-12 gate withholds (campaign Round 2 candidate C2,
    ``curve12.gate``; empty when the gate is off, the default): a new curve whose
    verdict is inverted or whose AUC is below ``curve12.min_auc``. A curve the
    check could not evaluate passes. Carried curves never have a record here,
    so the gate never touches them."""
    st = dict(settings if settings is not None else methodology.curve12_gate())
    if not st.get("gate"):
        return {}
    floor = float(st["min_auc"])
    out: dict[str, dict] = {}
    for mk, rec in (discrimination or {}).items():
        rec = rec or {}
        verdict = str(rec.get("verdict") or "")
        auc = rec.get("aucRefVsPressure")
        inverted = verdict == dz.VERDICT_INVERTED
        below = auc is not None and float(auc) < floor
        if not (inverted or below):
            continue
        out[mk] = {"verdict": verdict, "auc": None if auc is None else float(auc),
                   "minAuc": floor, "nRef": rec.get("nRef"), "nPressure": rec.get("nPressure"),
                   "why": "inverted" if inverted else "below_min_auc"}
    return out


def discrimination_gate_statement(rec: dict) -> str:
    """Why the discrimination gate withholds a curve, on the metric's record."""
    auc = rec.get("auc")
    counts = f"{rec.get('nRef')} reference and {rec.get('nPressure')} pressured stations"
    if rec.get("why") == "inverted":
        how = (f"ranked pressured stations above reference stations (area under the curve "
               f"{auc:.2f}; {counts})")
    else:
        how = (f"separated reference from pressured stations too weakly (area under the curve "
               f"{auc:.2f}, below the gate's floor of {float(rec.get('minAuc') or 0):.2f}; "
               f"{counts})")
    return (f"Discrimination gate. In the discrimination check this curve {how}, so it is "
            "withheld under the discrimination gate instead of being held for review. No "
            "curve is published and the metric is not scored.")


# --------------------------------------------------------------------------- #
# Methodology 0.15: DATA-03 and CURVE-09 withhold at the build
# --------------------------------------------------------------------------- #
def missingness_withheld(missingness: dict) -> dict[str, dict]:
    """The metrics DATA-03 withholds at the build (methodology 0.15): those
    whose missing-data fraction over their own pool (:func:`pool_missingness`)
    has the disposition ``review``, each with the numbers the statement names.
    Judged before any curve is built, so the same fraction gives the same
    outcome in every region; until 0.14 the built curve was held for a
    reviewer instead."""
    limit = float(methodology.threshold("data_rules.max_missingness_review"))
    out: dict[str, dict] = {}
    for mk, rec in sorted((missingness or {}).items()):
        rec = rec or {}
        if rec.get("disposition") != "review":
            continue
        out[mk] = {"missing_fraction": rec.get("missing_fraction"), "threshold": limit,
                   "n_pool_members": rec.get("n_pool_members"),
                   "n_with_value": rec.get("n_with_value"), "disposition": "review"}
    return out


def _pool_words(decision) -> str:
    """A pool in words for a statement: the ecoregion's own, or the wider pool
    with its level, code and name."""
    d = decision.to_dict() if isinstance(decision, rp.PoolDecision) else dict(decision or {})
    status = str(d.get("status") or "")
    if status.startswith("local") or str(d.get("level") or "") == "l3":
        return "this ecoregion's own reference pool"
    level = rp.LEVEL_LABELS.get(str(d.get("level") or ""), "")
    where = " ".join(x for x in (level, str(d.get("region_code") or "")) if x)
    name = f" ({d['region_name']})" if d.get("region_name") else ""
    return f"the {where} pool{name}" if where else "the reference pool"


def missingness_statement(rec: dict, decision=None) -> str:
    """Why DATA-03 withholds a metric, with the numbers: the fraction and the
    pool it is measured over, the limit, and that nothing is scored."""
    frac = float(rec.get("missing_fraction") or 0.0)
    n = int(rec.get("n_pool_members") or 0)
    have = int(rec.get("n_with_value") or 0)
    limit = float(rec.get("threshold") or 0.0)
    return (f"High missingness. {frac:.0%} of the {n} comparable reference stations of "
            f"{_pool_words(decision)} have no value for this metric ({have} carry one), above "
            f"the {limit:.0%} limit of rule DATA-03, so the metric is withheld at the build. "
            "No curve was built and the metric is not scored.")


def _finite(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def precision_floor_records(curve_rows: dict, metric_config: dict, *,
                            floors: Optional[dict] = None,
                            core_multiple: Optional[float] = None) -> dict[str, dict]:
    """The curves CURVE-09 withholds at the build (methodology 0.15): a
    two-sided curve of a metric with a declared measurement-precision floor
    (``curve_rules.measurement_precision_floors``) whose fitted Functioning core
    is narrower than ``core_multiple`` times the floor. The test provenance
    applied as an advisory record from v0.9, moved in front of the build; the
    numbers the statement names ride in the record."""
    floors = dict(floors if floors is not None
                  else methodology.threshold("curve_rules.measurement_precision_floors", {}) or {})
    mult = float(core_multiple if core_multiple is not None
                 else methodology.threshold("curve_rules.measurement_precision_core_multiple", 2.0))
    out: dict[str, dict] = {}
    for mk, row in sorted((curve_rows or {}).items()):
        precision = floors.get(mk)
        if precision is None:
            continue
        mc = (metric_config or {}).get(mk) or {}
        if curves.curve_form_of(mc) != curves.CURVE_FORM_OPTIMUM:
            continue
        lo, hi = _finite((row or {}).get("functioning_min")), _finite((row or {}).get("functioning_max"))
        if lo is None or hi is None:
            continue
        core = hi - lo
        floor_width = mult * float(precision)
        if core >= floor_width:
            continue
        n = _finite((row or {}).get("n_reference"))
        out[mk] = {"precision_sd": float(precision), "core_multiple": mult,
                   "floor_width": floor_width, "functioning_core_width": core,
                   "functioning_min": lo, "functioning_max": hi,
                   "units": str(mc.get("units") or ""),
                   "n_reference": None if n is None else int(n)}
    return out


def precision_floor_statement(rec: dict) -> str:
    """Why CURVE-09 withholds a curve, with the numbers: the core, the floor it
    is narrower than and the precision behind it."""
    units = f" {rec['units']}" if rec.get("units") else ""
    n = rec.get("n_reference")
    at_n = f" at n = {int(n)}" if isinstance(n, (int, float)) else ""
    return (f"Measurement-precision floor. The Functioning core of this two-sided curve spans "
            f"{float(rec['functioning_core_width']):.3g}{units} ({float(rec['functioning_min']):.3g} "
            f"to {float(rec['functioning_max']):.3g}{units}{at_n}), narrower than "
            f"{float(rec['floor_width']):.3g}{units}, {float(rec['core_multiple']):g} times the "
            f"metric's documented measurement precision of {float(rec['precision_sd']):.3g}{units}, "
            "so band assignments inside it would present measurement noise as condition classes "
            "and the metric is withheld at the build under rule CURVE-09. No curve was built and "
            "the metric is not scored.")


def source_of(decision: Optional[dict]) -> str:
    """Which source of the hierarchy a curve came from, for SELECT-04's ranking:
    local, regional, national, modeled, published, or flagged for a curve on a
    flagged transfer (REF-16), which ranks last whatever pool it rests on, so a
    validated source of any kind wins a function's place over it."""
    d = decision or {}
    if str(d.get("transfer_risk") or "") == rp.RISK_UNVALIDATED:
        return "flagged"
    basis = curve_basis.resolve(d.get("basis"))
    if basis == curve_basis.EASI_SCREENING:
        # methodology 0.16 (REF-17): the last resort ranks after everything,
        # flagged transfers included; it only ever completes an empty function
        return "pathway"
    if basis == curve_basis.NATIONAL:
        return "national"
    if basis == curve_basis.MODELED:
        return "modeled"
    if basis == curve_basis.PUBLISHED:
        return "published"
    return "local" if str(d.get("status")) == rp.STATUS_LOCAL else "regional"


def _abs_spearman(evidence: dict, a: str, b: str, *, min_n: int = 5) -> Optional[float]:
    """The absolute Spearman correlation of two metrics over the stations that
    carry both, from the pooled frame (``evidence["data"]``), or None when
    either is not a column of it (a carried curve has no station values here)
    or fewer than ``min_n`` stations carry both. Computed here rather than read
    from the redundancy view, which lists only pairs above its report floor."""
    data = evidence.get("data")
    if not isinstance(data, pd.DataFrame) or a not in data.columns or b not in data.columns:
        return None
    pair = pd.DataFrame({"a": pd.to_numeric(data[a], errors="coerce"),
                         "b": pd.to_numeric(data[b], errors="coerce")}).dropna()
    if len(pair) < int(min_n):
        return None
    rho = pair["a"].corr(pair["b"], method="spearman")
    return None if rho is None or rho != rho else abs(float(rho))


def second_metric_check(evidence: dict, candidate: str, scored: list[str], ps: dict) -> dict:
    """The independent-and-discriminating test of one candidate for a function's
    next place (SELECT-04, ``metric_portfolio.second_metric_rule``): its absolute
    Spearman with every metric the function already scores must be below
    ``max_abs_spearman`` and its CURVE-12 AUC at least ``min_auc``. An unknown
    correlation (no shared station values) or an unevaluable AUC fails the test
    and the record says which."""
    rhos = {m: _abs_spearman(evidence, candidate, m) for m in scored}
    worst = max((r for r in rhos.values() if r is not None), default=None)
    unknown = sorted(m for m, r in rhos.items() if r is None)
    independent = bool(scored) and not unknown and (worst is None or worst < ps["max_abs_spearman"])
    auc = ((evidence.get("discrimination") or {}).get(candidate) or {}).get("aucRefVsPressure")
    discriminates = auc is not None and float(auc) >= ps["min_auc"]
    why = []
    if unknown:
        why.append("no station values shared with " + ", ".join(unknown))
    elif worst is not None and worst >= ps["max_abs_spearman"]:
        why.append(f"absolute Spearman {worst:.2f} with a scored metric, not below "
                   f"{ps['max_abs_spearman']:.2f}")
    if auc is None:
        why.append("no discrimination check could be run")
    elif not discriminates:
        why.append(f"discrimination AUC {float(auc):.2f}, below {ps['min_auc']:.2f}")
    return {"metric": candidate, "against": list(scored),
            "absSpearman": {m: (None if r is None else round(r, 4)) for m, r in rhos.items()},
            "maxAbsSpearman": None if worst is None else round(worst, 4),
            "auc": None if auc is None else float(auc),
            "independent": bool(independent), "discriminates": bool(discriminates),
            "joins": bool(independent and discriminates),
            "why": "; ".join(why) if why else ""}


def select_portfolio(evidence: dict, rows: dict, mapping: pd.DataFrame, config: dict,
                     scores: dict, meta: dict) -> tuple[dict, pd.DataFrame]:
    """SELECT-04 (methodology 0.14): fill every function to two metrics, and no
    further without a person.

    Carried-forward curves, curves rebuilt because their data were corrected,
    and the fixed criteria keep their places. A new curve joins a function only
    while the function holds fewer than ``fill_to`` scored metrics, the higher
    source first (local, regional, national, modeled, published), then the
    higher SELECT-02 metric score, then the metric key. A reserve candidate joins
    only a function that would otherwise be unassessed. Everything else is
    supported, not selected: its curve exists and its record says why it is not
    scored. Returns the rows and the mapping the exporter should read, and
    records the choice in ``meta["portfolioSelection"]``.

    ``metric_portfolio.second_metric_rule`` (campaign Round 2 candidate C1)
    governs the places after the first. ``rank`` is the rule above. Under
    ``independent_and_discriminating`` the first new metric joins by rank and one
    further place (at least two places in all, ``fill_to`` when larger) goes to
    the highest-ranked candidate that passes :func:`second_metric_check` against
    every metric already scored; each candidate's numbers are recorded per
    function (``secondMetric``), as is the rule (``rule``).
    """
    from . import regional_agent as ra
    ps = methodology.portfolio_settings()
    fill_to, rule = ps["fill_to"], ps["second_metric_rule"]
    rank = dict(methodology.threshold("metric_portfolio.source_rank", {}) or
                {"local": 0, "regional": 1, "national": 2, "modeled": 3, "published": 4,
                 "flagged": 5, "pathway": 6})
    support = evidence.get("reference_support") or {}
    kept_keys = (set(evidence.get("carried") or {}) | set(evidence.get("carry_rebuilt") or {})
                 | set(evidence.get("fixed_metrics") or {}))
    if not isinstance(mapping, pd.DataFrame) or not len(mapping) \
            or "metric_key" not in mapping.columns:
        return rows, mapping
    fid_of = mapping["function_label"].map(lambda x: ra._canonical_function_id(x)
                                           if isinstance(x, str) and x.strip() else None)
    drop_index: list = []
    selection: dict[str, dict] = {}
    for fid, idx in fid_of.dropna().groupby(fid_of.dropna()).groups.items():
        sub = mapping.loc[idx]
        members = [str(m) for m in dict.fromkeys(sub["metric_key"].astype(str)) if str(m) in rows]
        if not members:
            continue
        kept = [m for m in members if m in kept_keys]
        cands = [m for m in members if m not in kept_keys]

        def key(m):
            src = source_of(support.get(m))
            score = float(((scores.get(m) or {}).get("total")) or 0.0)
            return (int(rank.get(src, 9)), -score, m)

        # methodology 0.16 (REF-16): a curve on a flagged transfer is the last
        # resort of a function's portfolio, as it is of a metric's ladder. A
        # validated reserve candidate (one that joins only a function that would
        # otherwise be unassessed by a validated source) still wins the place over
        # a flagged regular candidate; a flagged curve fills only what is left.
        is_reserve = {m: bool((config.get(m) or {}).get("reserve")) for m in cands}
        is_flagged = {m: source_of(support.get(m)) == "flagged" for m in cands}
        regular_ok = sorted([m for m in cands if not is_reserve[m] and not is_flagged[m]], key=key)
        regular_flagged = sorted([m for m in cands if not is_reserve[m] and is_flagged[m]], key=key)
        reserves_ok = sorted([m for m in cands if is_reserve[m] and not is_flagged[m]], key=key)
        reserves_flagged = sorted([m for m in cands if is_reserve[m] and is_flagged[m]], key=key)
        regular = regular_ok + regular_flagged
        reserves = reserves_ok + reserves_flagged
        second: Optional[dict] = None
        if rule == methodology.SECOND_METRIC_RANK:
            need = max(0, fill_to - len(kept))
            chosen = regular_ok[:need]
            if not kept and not chosen:
                chosen = reserves_ok[:fill_to]
            left_places = max(0, fill_to - len(kept) - len(chosen))
            if left_places:
                chosen = chosen + regular_flagged[:left_places]
            if not kept and not chosen:
                chosen = reserves_flagged[:fill_to]
        else:
            places = max(int(fill_to), 2)
            chosen = regular_ok[:max(0, 1 - len(kept))]
            if not kept and not chosen:
                chosen = reserves_ok[:1]
            if not kept and not chosen:
                chosen = regular_flagged[:1]
            if not kept and not chosen:
                chosen = reserves_flagged[:1]
            scored = kept + chosen
            checks: list[dict] = []
            while scored and len(scored) < places:
                pick = None
                for m in regular:
                    if m in chosen:
                        continue
                    rec = second_metric_check(evidence, m, scored, ps)
                    checks.append(rec)
                    if rec["joins"]:
                        pick = m
                        break
                if pick is None:
                    break
                chosen.append(pick)
                scored.append(pick)
            second = {"maxAbsSpearman": ps["max_abs_spearman"], "minAuc": ps["min_auc"],
                      "places": places, "checks": checks}
        left = [m for m in regular + reserves if m not in chosen]
        record = {"kept": kept, "selected": chosen, "rule": rule}
        if second is not None:
            record["secondMetric"] = second
        if not left and not reserves:
            if len(kept) + len(chosen) > 0:
                selection[fid] = {**record, "notSelected": []}
            continue
        for m in left:
            drop_index += list(sub.index[sub["metric_key"].astype(str) == m])
        selection[fid] = {
            **record,
            "notSelected": [{"metric": m, "source": source_of(support.get(m)),
                             "score": (scores.get(m) or {}).get("total"),
                             "reserve": bool((config.get(m) or {}).get("reserve"))}
                            for m in left]}
    out_mapping = mapping.drop(index=drop_index)
    still = set(out_mapping["metric_key"].astype(str))
    out_rows = {mk: r for mk, r in rows.items()
                if mk in still or mk in (evidence.get("fixed_metrics") or {})}
    meta["portfolioSelection"] = selection
    # A reserve candidate is tried only for a function that might go unassessed.
    # Where its function is scored anyway, its refusal is not a gap anyone has
    # to read about, so it leaves the withheld list; where the function is a gap,
    # the refusal is part of the evidence and stays.
    fid_left = out_mapping["function_label"].map(
        lambda x: ra._canonical_function_id(x) if isinstance(x, str) and x.strip() else None)
    covered = {f for f, m in zip(fid_left, out_mapping["metric_key"].astype(str))
               if f and m in out_rows}
    listed = meta.get("insufficientReferenceSupport")
    if listed:
        cfgs = {**{mk: (v.get("config") or {}) for mk, v in
                   (evidence.get("insufficient_support") or {}).items()},
                **(evidence.get("metric_config") or {})}
        meta["insufficientReferenceSupport"] = [
            w for w in listed
            if not ((cfgs.get(str(w.get("metricKey"))) or {}).get("reserve")
                    and {f.get("functionId") for f in w.get("functions") or []} <= covered)]
    return out_rows, out_mapping


def moot_reserves(metric_config: dict, rows: dict, mapping) -> list[str]:
    """Reserve candidates the portfolio cannot use: not published, and every
    function they could serve is scored by other metrics (SELECT-04)."""
    from . import regional_agent as ra
    if not isinstance(mapping, pd.DataFrame) or not len(mapping) \
            or "metric_key" not in mapping.columns:
        return []
    covered = set()
    for mk, label in zip(mapping["metric_key"].astype(str), mapping["function_label"]):
        if mk in rows and isinstance(label, str) and label.strip():
            fid = ra._canonical_function_id(label)
            if fid:
                covered.add(fid)
    out = []
    for mk, cfg in (metric_config or {}).items():
        if not (cfg or {}).get("reserve") or mk in rows:
            continue
        fids = {f["functionId"] for f in _functions_of(mk)}
        if fids and fids <= covered:
            out.append(mk)
    return sorted(out)


def portfolio_from_mapping(portfolio: list[dict], rows: dict, mapping) -> list[dict]:
    """The compact portfolio restated from what the bundle publishes: every
    function's metrics after SELECT-04, carried and fixed metrics included, so
    the SELECT-01 record and the coverage list count the published set."""
    from . import regional_agent as ra
    if not isinstance(mapping, pd.DataFrame) or not len(mapping) \
            or "metric_key" not in mapping.columns:
        return portfolio
    by_fid: dict[str, list[str]] = {}
    for mk, label in zip(mapping["metric_key"].astype(str), mapping["function_label"]):
        if mk not in rows or not isinstance(label, str) or not label.strip():
            continue
        fid = ra._canonical_function_id(label)
        if fid and mk not in by_fid.setdefault(fid, []):
            by_fid[fid].append(mk)
    max_per_fn = int(methodology.threshold(
        "metric_portfolio.default_maximum_metrics_per_function"))
    out = []
    for row in portfolio or []:
        ms = by_fid.get(str(row.get("function_id")), [])
        out.append({**row, "metrics": ms, "n_metrics": len(ms),
                    "coverage": "covered" if ms else "GAP",
                    "primary_metric": ms[0] if ms else None,
                    "select01_flag": len(ms) > max_per_fn})
    return out


def _add_carried(carried: dict, rows: dict, config: dict, annotations: dict) -> None:
    """Put the carried-forward curves into the exporter's inputs exactly as they
    were published: their points and class layers, their config, and their
    annotations, which already say what they rest on. Mutates the three dicts."""
    for mk, c in carried.items():
        if mk in rows:
            continue
        rows[mk] = dict(c["row"])
        if c.get("config"):
            config[mk] = dict(c["config"])
        annotations[mk] = dict(c.get("annotations") or {})


# --------------------------------------------------------------------------- #
# inputs shared by the build and the census
# --------------------------------------------------------------------------- #
def national_inputs(*, dataset_id: Optional[str] = None, cycles=None,
                    max_stream_order: Optional[int] = None, protocols=None,
                    keep_stations: Optional[dict] = None,
                    value_policy: Optional[str] = None) -> dict:
    """The national frame, the DATA-11 values of every metric a build could
    fit, and the metric configs. One function, so the census a reviewer reads
    before a build and the build itself can never see different inputs.

    ``value_policy`` is the id the archive is read under
    (``nrsa_dataset.resolve_value_policy``; ``None`` is the new-build default);
    the id actually used rides back as ``value_policy`` so every record of the
    run names it."""
    from . import regional_agent as ra
    from . import stratifiers
    dataset_id = dataset_id or nrsa_dataset.MULTI_CYCLE_DATASET_ID
    policy = nrsa_dataset.resolve_value_policy(value_policy)
    directions = ra.load_directions()
    landscape_directions = ra.load_landscape_directions()
    frame, frame_ledger = rp.national_frame(
        dataset_id=dataset_id, cycles=cycles, max_stream_order=max_stream_order,
        protocols=protocols, keep_stations=keep_stations or None)
    dataset = nrsa_dataset.load_dataset(dataset_id)
    metric_config, flagged_direction = ra.build_metric_config(
        sorted(dataset.metric_columns()), directions, include_reserve=True)
    registry_cols = [c for c in stratifiers.source_columns(stratifiers.load_national_registry())
                     if c in dataset.values.columns]
    wanted = list(dict.fromkeys(list(metric_config) + registry_cols))
    values, value_ledger = nrsa_dataset.latest_values(
        frame["station_key"], dataset=dataset, metrics=wanted,
        cycles=cycles or nrsa_dataset.CYCLES_NEWEST_FIRST, policy=policy)
    landscape_config, landscape_missing = ra.build_landscape_metric_config(
        list(frame.columns), landscape_directions, expectation_only=True)
    by_key = frame.set_index(frame["station_key"].astype(str))
    for col in landscape_config:
        values[col] = values["site_id"].astype(str).map(
            pd.to_numeric(by_key[col], errors="coerce"))
    metric_config.update(landscape_config)
    return {"frame": frame, "frame_ledger": frame_ledger, "values": values,
            "value_ledger": value_ledger, "metric_config": metric_config,
            "landscape_config": landscape_config,
            "flagged_direction": flagged_direction + landscape_missing,
            "predictor_config": ra.build_predictor_config(list(frame.columns),
                                                          landscape_directions),
            "value_policy": policy}


CENSUS_COLUMNS = ["l3", "region", "metric", "display_name", "family", "status", "level",
                  "region_code", "region_name", "n_pool", "n_comparable", "n_usable", "n_local",
                  "n_huc12", "disposition", "covariates", "lithology", "self_coverage",
                  "supported_level", "transfer_risk", "zero_inflated", "q25"]


def _metric_function_ids(keys, configs: dict) -> dict:
    """``{metric: {canonical function ids}}`` by the build's own mapping (every
    assignment, scaffold rows aside)."""
    keys = [str(k) for k in keys]
    if not keys:
        return {}
    cfg = {k: configs.get(k) or {} for k in keys}
    frame = staf_library.default_discipline_function_mapping(keys, cfg)
    got: dict = {}
    if isinstance(frame, pd.DataFrame) and len(frame) and "metric_key" in frame.columns:
        wanted = set(keys)
        for mk, label in zip(frame["metric_key"], frame["function_label"]):
            # the frame's scaffold rows (one per function, no metric) cover nothing
            if not isinstance(mk, str) or mk not in wanted:
                continue
            canon = staf_library.staf_canonical_function(label)
            if canon is not None:
                got.setdefault(mk, set()).add(str(canon["id"]))
    return got


def _fixed_functions() -> set:
    """The functions the fixed criteria score in every region."""
    out: set = set()
    fixed_map = fixed_criteria.mapping_rows()
    for label in (list(fixed_map["function_label"]) if len(fixed_map) else []):
        canon = staf_library.staf_canonical_function(label)
        if canon is not None:
            out.add(str(canon["id"]))
    return out


def missingness_kept_for_coverage(to_withhold: dict, missingness: dict, *,
                                  covered_metrics: Iterable[str], configs: dict) -> dict:
    """Methodology 0.16 (owner decision D18): the metrics DATA-03 would withhold
    that are the last candidate of a function, kept with their missingness stated.

    DATA-03 withholds a pool most of whose comparable stations carry no value.
    Where that would leave a function with no other metric holding a curve (the
    build's own, ladder, carried, fixed, owner-accepted or owner-held), the
    candidate with the smallest missing fraction (then the most stations with a
    value, then the key) keeps its validated pool, as REF-16 keeps a pool the
    recovery test refused: the failed criterion becomes a stated limitation, never
    a gap. Returns ``{metric: [the function ids it keeps covered]}``."""
    covered: set = set(_fixed_functions())
    for fids in _metric_function_ids(covered_metrics, configs).values():
        covered |= fids
    cand_functions = _metric_function_ids(to_withhold, configs)

    def order(mk):
        rec = missingness.get(mk) or {}
        return (float(rec.get("missing_fraction") if rec.get("missing_fraction") is not None else 1.0),
                -int(rec.get("n_with_value") or 0), mk)

    kept: dict = {}
    for mk in sorted(to_withhold, key=order):
        empty = sorted(f for f in cand_functions.get(mk, set()) if f not in covered)
        if empty:
            kept[mk] = empty
            covered |= cand_functions.get(mk, set())
    return kept


def missingness_kept_caveat(rec: dict, function_names: list, limit: float) -> str:
    """The limitation a pool kept for coverage carries (DEEP prints it with the metric)."""
    frac = float(rec.get("missing_fraction") or 0.0)
    n_val, n_pool = int(rec.get("n_with_value") or 0), int(rec.get("n_pool_members") or 0)
    names = ", ".join(function_names) or "its function"
    return (f"Kept although {n_pool - n_val} of the pool's {n_pool} comparable stations carry "
            f"no value for this metric (missing fraction {frac:.2f}, above the {limit:.2f} "
            f"review level): no other source scores {names} in this ecoregion, so the "
            f"curve rests on the {n_val} stations that do.")


def _tried_of(decision) -> list:
    """A decision's ``options_tried`` (a PoolDecision or its dict), copied."""
    if decision is None:
        return []
    got = (decision.get("options_tried") if isinstance(decision, dict)
           else getattr(decision, "options_tried", None))
    return [dict(x) for x in (got or []) if isinstance(x, dict)]


def last_resort_fills(l3_code: str, name: str, *, covered_metrics: Iterable[str],
                      candidate_metrics: dict, configs: dict, carried: Optional[dict] = None,
                      withheld_statements: Optional[dict] = None,
                      nars9: Optional[str] = None) -> dict:
    """REF-17 (methodology 0.16, owner decision D19): the functions no source
    supports, each completed by EASI's national screening method for the same
    quantity where ``config/fixed_criteria.yaml`` names one under ``last_resort``.

    ``covered_metrics`` are the metrics that hold a curve or a standing owner
    decision (built, ladder, carried, fixed, owner-accepted, owner-held): any
    function one of them maps to is covered and never reaches the last resort.
    ``candidate_metrics`` maps each refused or withheld metric to its decision,
    ``configs`` every metric config known to the build (for the function
    mapping) and ``withheld_statements`` a withheld metric's statement. Returns,
    per metric added, ``decision``, ``row``, ``config``, ``attempt``,
    ``function_id``, ``function_label`` and ``candidates``; empty when the rung
    is off.
    """
    if not methodology.last_resort().get("enabled") or not fixed_criteria.last_resort_keys():
        return {}

    covered: set = set(_fixed_functions())
    for fids in _metric_function_ids(covered_metrics, configs).values():
        covered |= fids
    for c in (carried or {}).values():
        covered |= {str(f) for f in (c.get("functions") or []) if f}
    cand_functions = _metric_function_ids(candidate_metrics, configs)
    statements = withheld_statements or {}
    out: dict = {}
    for mk in fixed_criteria.last_resort_keys():
        entry = fixed_criteria.last_resort_entry(mk)
        labels = list(entry.get("functions") or [])
        canon = staf_library.staf_canonical_function(labels[0]) if labels else None
        if canon is None or str(canon["id"]) in covered:
            continue
        fid, label = str(canon["id"]), str(canon["name"])
        refused = sorted(m for m, fids in cand_functions.items() if fid in fids)
        words = []
        for m in refused:
            said = str(statements.get(m) or "").strip().rstrip(".")
            if not said:
                # each station pool the metric tried, with the reason it was refused
                said = "; ".join(_pool_sentence(x).rstrip(".") for x in _tried_of(
                    candidate_metrics.get(m)) if not x.get("accepted"))
            words.append(f"{m} ({said})" if said else
                         f"{m} (no source in the hierarchy passed acceptance)")
        layer = fixed_criteria.last_resort_stratum(entry, nars9)
        method_words = (f"EASI's {entry.get('easi_title')} reference curve for NARS-9 region {layer}"
                        if layer else f"EASI's national screening method, {entry.get('easi_title')}")
        why = (f"No source supports {label} in this ecoregion: "
               + ("; ".join(words) if words else "the function has no candidate metric here")
               + f". Completed by {method_words}, "
               f"adopted under {fixed_criteria.LAST_RESORT_RULE} (owner decision D19).")
        decision = rp.PoolDecision(
            metric=mk, status=rp.STATUS_PUBLISHED, level=None, region_code=str(l3_code),
            region_name=name, family=rp.family_of(mk), n_pool=0, n_comparable=0, n_usable=0,
            n_local=0, n_huc12=0, disposition="exploratory", supported_level=None,
            transfer_risk=rp.RISK_NONE,
            transfer_note=f"{curve_basis.statement_for(curve_basis.EASI_SCREENING)} {why}",
            station_ids=(),
            # the replaced metric's own refusals stay on its record (traceable on reopen)
            levels_tried=list((candidate_metrics.get(mk).get("levels_tried")
                               if isinstance(candidate_metrics.get(mk), dict)
                               else getattr(candidate_metrics.get(mk), "levels_tried", None)) or []),
            options_tried=_tried_of(candidate_metrics.get(mk)),
            basis=curve_basis.EASI_SCREENING,
            screen_detail={"rule": fixed_criteria.LAST_RESORT_RULE,
                           "easiMethod": entry.get("easi_method"), "functionId": fid,
                           "refusedCandidates": refused, "stratum": layer})
        out[mk] = {"decision": decision, "row": fixed_criteria.last_resort_curve_row(mk, nars9),
                   "config": fixed_criteria.last_resort_config(mk),
                   "attempt": {"metric": mk, "rung": fixed_criteria.LAST_RESORT_RULE,
                               "admitted": True, "basis": curve_basis.EASI_SCREENING,
                               "why": why},
                   "function_id": fid, "function_label": label, "candidates": refused}
    return out


def forced_sources(force: Optional[dict], *, metric_config: dict, carried: dict,
                   insufficient: dict, ladder_rows: dict, frame: pd.DataFrame,
                   values: pd.DataFrame, l3_code: str, name: str,
                   validation: Optional[dict], registry: Optional[dict],
                   excluded: Optional[dict]) -> dict:
    """``{metric: basis_ladder.force_source(...)}`` for each refused source the
    owner accepted. A metric this build fits keeps its own curve: a choice never
    replaces one, so it is not computed and says why."""
    out: dict = {}
    for mk, ref in sorted((force or {}).items()):
        fitted = mk in metric_config and mk not in insufficient and mk not in ladder_rows
        if fitted:
            out[mk] = {"rule": (ref or {}).get("rule"), "option": (ref or {}).get("option"),
                       "row": None, "decision": None, "failed": [],
                       "why": "This build fits the metric itself, so its own curve scores."}
            continue
        config = dict(metric_config.get(mk) or (carried.get(mk) or {}).get("config") or {})
        try:
            if not config:
                from . import owner_sources
                config = owner_sources.agent_config(mk)
            out[mk] = basis_ladder.force_source(
                mk, ref, frame=frame, values_wide=values, target_l3=l3_code, config=config,
                validation=validation, region_name=name, scale_registry=registry,
                excluded=excluded)
        except Exception as exc:  # noqa: BLE001 - one owner request never stops a build
            import logging
            logging.getLogger("streamcurves").exception(
                "REF-15: the refused source the owner accepted for %s could not be computed", mk)
            out[mk] = {"rule": (ref or {}).get("rule"), "option": (ref or {}).get("option"),
                       "row": None, "decision": None, "failed": [],
                       "why": f"The build could not compute the source ({exc})."}
        out[mk]["config"] = config
    return out


def census(l3_codes, *, max_stream_order: Optional[int] = None, protocols=None,
           keep_stations: Optional[dict] = None, excluded: Optional[dict] = None,
           scale_registry: Optional[dict] = None,
           value_policy: Optional[str] = None) -> dict:
    """Reference support per region and metric, before any curve is fitted.

    Returns ``{"table": DataFrame, "regions": [...]}``. ``zero_inflated`` marks
    a higher-is-better metric whose pool's lower quartile is at or below zero:
    the curve engine can only draw a degenerate seed from such a pool, so the
    reviewer sees it here and not first in a flagged curve. ``value_policy`` is
    the build's (``national_inputs``), so the census counts what the build reads.
    """
    from . import regional_agent as ra
    inputs = national_inputs(max_stream_order=max_stream_order, protocols=protocols,
                             keep_stations=keep_stations, value_policy=value_policy)
    frame, values, metric_config = inputs["frame"], inputs["values"], inputs["metric_config"]
    registry = scale_registry if scale_registry is not None else scale_analysis.load_registry()
    wide = values.set_index(values["site_id"].astype(str))
    validation = basis_ladder.load_validation()
    flagged_settings = acceptance.flagged_settings()

    def accept_for(mk):
        return acceptance.pool_acceptor(mk, metric_config.get(mk) or {}, validation,
                                        family=rp.family_of(mk))

    def curve_check_for(mk):
        return acceptance.curve_checker(mk, metric_config.get(mk) or {})

    rows: list[dict] = []
    regions: list[dict] = []
    for code in l3_codes:
        code = str(code).strip()
        name = ra.region_name_for(code) or rp._level_name(frame, "l3", code) or f"L3 {code}"
        # the same hierarchy and acceptance the build walks, fits aside: the census
        # must not say a metric is withheld that the build will go on to score
        pools = rp.build_pools(list(metric_config), values, frame, code,
                               scale_registry=registry, excluded=excluded,
                               accept_for=accept_for, curve_check_for=curve_check_for,
                               flagged=flagged_settings)
        short = [mk for mk, d in pools["decisions"].items()
                 if d.status == rp.STATUS_INSUFFICIENT]
        flagged_pools = [mk for mk, d in pools["decisions"].items() if d.flagged]
        if short or flagged_pools:
            # methodology 0.16: a validated source of any kind wins over a flagged
            # pool, and a flagged national pool stands last, as the build decides
            walked = basis_ladder.resolve(
                sorted(set(short) | set(flagged_pools)), frame=frame, values_wide=values,
                target_l3=code, metric_config=metric_config, validation=validation,
                region_name=name, fit=False, flagged=flagged_settings,
                flag_national=sorted(short))
            pools["decisions"].update(walked["decisions"])
            for mk, got in (walked.get("flagged_national") or {}).items():
                if mk in short and mk not in walked["decisions"]:
                    pools["decisions"][mk] = got["decision"]
        # methodology 0.16 (REF-17): the last resort completes a function no source
        # supports, as the build decides (the build-time withholds aside)
        fills = last_resort_fills(
            code, name,
            covered_metrics=[mk for mk, d in pools["decisions"].items()
                             if d.status != rp.STATUS_INSUFFICIENT],
            candidate_metrics={mk: d for mk, d in pools["decisions"].items()
                               if d.status == rp.STATUS_INSUFFICIENT},
            configs=metric_config,
            nars9=published_benchmark.majority_region(
                frame[frame["l3"].astype(str) == str(code)], "nars9")[0])
        for mk, fill in fills.items():
            pools["decisions"][mk] = fill["decision"]
        statuses = [d.status for d in pools["decisions"].values()]
        regions.append({"l3": code, "region": name,
                        "n_frame": pools["target_n_frame"], "n_strict": pools["target_n_strict"],
                        "n_relaxed": pools["target_n_relaxed"],
                        "n_local": statuses.count(rp.STATUS_LOCAL),
                        "n_local_relaxed": statuses.count(rp.STATUS_LOCAL_RELAXED),
                        "n_borrowed_l2": statuses.count("borrowed_l2"),
                        "n_borrowed_nars9": statuses.count("borrowed_nars9"),
                        "n_borrowed_l1": statuses.count("borrowed_l1"),
                        "n_national": statuses.count(rp.STATUS_NATIONAL),
                        "n_modeled": statuses.count(rp.STATUS_MODELED),
                        "n_published": statuses.count(rp.STATUS_PUBLISHED),
                        "n_insufficient": statuses.count(rp.STATUS_INSUFFICIENT)})
        for mk, d in pools["decisions"].items():
            cfg = metric_config.get(mk) or {}
            q25, zero = None, False
            if d.station_ids and mk in wide.columns:
                v = pd.to_numeric(wide.loc[wide.index.isin(d.station_ids), mk],
                                  errors="coerce").dropna()
                if len(v):
                    q25 = float(v.quantile(0.25))
                    # the curve engine's own degenerate-seed rule: a monotone
                    # higher-is-better metric on a scale that cannot go negative
                    declared = cfg.get("signed_scale")
                    signed = bool(declared) if declared is not None else bool(v.min() < 0)
                    zero = (bool(cfg.get("higher_is_better")) and q25 <= 0 and not signed
                            and curves.curve_form_of(cfg) == curves.CURVE_FORM_MONOTONE)
            rows.append({
                "l3": code, "region": name, "metric": mk,
                "display_name": cfg.get("display_name") or mk, "family": d.family,
                "status": d.status, "level": rp.LEVEL_LABELS.get(d.level or "", ""),
                "region_code": d.region_code, "region_name": d.region_name,
                "n_pool": d.n_pool, "n_comparable": d.n_comparable, "n_usable": d.n_usable,
                "n_local": d.n_local, "n_huc12": d.n_huc12, "disposition": d.disposition,
                "covariates": ", ".join(d.covariates), "lithology": ", ".join(d.lith_groups),
                "self_coverage": d.self_coverage,
                "supported_level": rp.LEVEL_LABELS.get(d.supported_level or "", ""),
                "transfer_risk": d.transfer_risk, "zero_inflated": zero, "q25": q25})
    return {"table": pd.DataFrame(rows, columns=CENSUS_COLUMNS), "regions": regions,
            "registry_present": bool((registry or {}).get("metrics"))}


def census_markdown(result: dict) -> str:
    """The census as a page a reviewer can read top to bottom."""
    table, regions = result["table"], result["regions"]
    lines = ["# Reference support census", "",
             "Reference stations pass the fixed landscape-pressure screen "
             f"({rscreen.screen_label('strict')}). A metric uses the narrowest pool with enough "
             "comparable stations: this ecoregion, then its Level II, then its Level I. "
             "A metric with no such pool is withheld.", ""]
    if not result.get("registry_present"):
        lines += ["No national scale registry is present, so the transfer risk of every "
                  "borrowed pool reads unassessed.", ""]
    lines += ["| L3 | Region | In frame | Pass strict | Pass relaxed | Local | Level II | "
              "Level I | Withheld |", "|---|---|---|---|---|---|---|---|---|"]
    for r in regions:
        lines.append(f"| {r['l3']} | {r['region']} | {r['n_frame']} | {r['n_strict']} | "
                     f"{r['n_relaxed']} | {r['n_local']} | {r['n_borrowed_l2']} | "
                     f"{r['n_borrowed_l1']} | {r['n_insufficient']} |")
    for r in regions:
        sub = table[table["l3"] == r["l3"]]
        lines += ["", f"## {r['region']} (L3 {r['l3']})", "",
                  "| Metric | Status | Pool | Usable n | Local n | Disposition | Self-coverage | "
                  "Transfer risk | Note |", "|---|---|---|---|---|---|---|---|---|"]
        for row in sub.to_dict("records"):
            pool = (f"{row['level']} {row['region_code']} ({row['region_name']})"
                    if row["level"] else "none")
            note = "lower quartile at zero" if row["zero_inflated"] else ""
            cover = "" if row["self_coverage"] is None or pd.isna(row["self_coverage"]) \
                else f"{float(row['self_coverage']):.2f}"
            lines.append(f"| {row['display_name']} (`{row['metric']}`) | {row['status']} | "
                         f"{pool} | {row['n_usable']} | {row['n_local']} | "
                         f"{row['disposition']} | {cover} | {row['transfer_risk']} | {note} |")
    return "\n".join(lines) + "\n"


def run_evidence(l3_code: str, name: str, *,
                 on_event: Optional[Callable] = None,
                 cache_dir: Optional[Path] = None,
                 diagnostics_n_boot: int = 200,
                 diagnostics_enabled: bool = True,
                 nrsa_dataset_id: Optional[str] = None,
                 nrsa_cycles=None,
                 exclude_sites: Optional[dict] = None,
                 nrsa_max_stream_order: Optional[int] = None,
                 nrsa_protocols=None,
                 nrsa_keep_sites: Optional[dict] = None,
                 scale_registry: Optional[dict] = None,
                 carry: Any = True,
                 force: Optional[dict] = None,
                 hold: Optional[list] = None,
                 value_policy: Optional[str] = None) -> dict:
    """The decision-free half of a regional run under the pressure screen.

    ``value_policy`` (rule DATA-11): the id the archive's values are read under
    (``nrsa_dataset.resolve_value_policy``). ``None`` is the new-build default;
    a replay of a published version passes the id that version's manifest
    records (``inputs.nrsa_dataset.policy``), and the evidence records the id
    actually used (``nrsa_policy``, ``value_selection.policy``), which the
    inputs digest carries.

    ``carry`` (methodology 0.14): True carries forward every curve the
    ecoregion's latest published version scores unless its data were found
    defective (``carry_forward.prepare``); a prepared dict is used as given;
    False or None builds every curve afresh.

    ``force`` (REF-15): ``{metric: {"rule", "option"}}``, the sources the build
    refuses that the owner accepted (``owner_curves.forced_sources``). Each is
    computed beside the build's own choices, which it never changes, with every
    check it fails recorded (``basis_ladder.force_source``); the owner's
    decision puts it in the bundle at assembly.

    ``hold`` (REF-15, owner decision 2026-09-22, "your choice stands"): the
    metrics the owner removed or chose a source for (``owner_curves.held_metrics``).
    The build keeps each out of its own fit until the decision is withdrawn: its
    station pools are still judged, apart, so a pool that would support a curve is
    recorded (``held_by_owner``) and one that would not walks the ladder as any
    withheld metric does, but no held station joins the pooled frame and no
    curve is fitted for it."""
    from . import regional_agent as ra

    # campaign Round 2: every knob is read once here, so a misconfigured one
    # fails before any work; the non-default ones ride to the manifest's
    # reference block and the inputs digest
    methodology_knobs = methodology.round2_knobs()
    geometry = curves.seed_geometry()

    dataset_id = nrsa_dataset_id or nrsa_dataset.MULTI_CYCLE_DATASET_ID
    if dataset_id != nrsa_dataset.MULTI_CYCLE_DATASET_ID:
        raise ValueError(
            "the pressure-screen reference method reads the pooled NRSA archive "
            f"({nrsa_dataset.MULTI_CYCLE_DATASET_ID}): the station screen table is keyed "
            "by its stations. Use --reference-method easi-eci with the legacy dataset.")
    if not rscreen.station_screen_available():
        raise FileNotFoundError(
            "data/nrsa/station_screen.parquet is not built; run "
            "scripts/nrsa/build_station_screen.py")
    protocols = tuple(nrsa_protocols) if nrsa_protocols else None
    cycles = tuple(nrsa_cycles) if nrsa_cycles else None

    # --- the target panel, as the legacy pass builds it (rule DATA-10) ---
    candidates, panel_ledger = ra.select_candidates_detailed(
        l3_code, dataset=dataset_id, cycles=nrsa_cycles,
        max_stream_order=nrsa_max_stream_order, protocols=protocols,
        keep_stations=nrsa_keep_sites or None)
    frame_overrides = list(candidates.attrs.get("frame_overrides") or [])
    n_out_of_frame = int(len(panel_ledger[panel_ledger["reason"].astype(str)
                                          .str.contains("reference frame", na=False)])
                         if len(panel_ledger) else 0)
    if nrsa_max_stream_order is not None:
        _emit(on_event, "reference_frame",
              {"max_stream_order": int(nrsa_max_stream_order),
               "n_candidates": int(len(candidates)), "n_out_of_frame": n_out_of_frame,
               "n_overrides": len(frame_overrides)})
    n_candidates = len(candidates)
    if n_candidates == 0:
        raise ValueError(f"no NRSA candidate sites for L3 ecoregion {l3_code}")

    # --- the reference screen (REF-04), read from the station table ---
    tables = rscreen.to_screening_tables(candidates)
    rows = tables["easi_screening_sites"]
    screening = {"tables": tables, "preset": rscreen.screen_label("strict"),
                 "retained_ids": easi_screening.retained_site_ids(tables),
                 "counts": easi_screening.summarize_screening_rows(rows),
                 "from_cache": False, "station_screen": rscreen.station_screen_identity()}
    retained_ids = set(screening["retained_ids"])
    owner_site_exclusions = ra.apply_owner_site_exclusions(screening, retained_ids,
                                                           exclude_sites)
    counts = dict(screening["counts"])
    counts["n_retained"] = len(retained_ids)
    if owner_site_exclusions:
        counts["n_owner_excluded"] = sum(1 for e in owner_site_exclusions if e["was_retained"])
    review_flags = [] if retained_ids else [NO_LOCAL_REFERENCE]
    tier = {"reference_tier": ra.TIER_LEAST_DISTURBED, "ref02_triggered": False,
            "review_flags": review_flags, "screening": screening}
    _emit(on_event, "reference_screen",
          {"screen": rscreen.SCREEN_ID, "n_candidates": n_candidates,
           "n_reference": len(retained_ids)})

    # --- the national frame the pools draw from, and the values (DATA-11) ---
    inputs = national_inputs(dataset_id=dataset_id, cycles=cycles,
                             max_stream_order=nrsa_max_stream_order, protocols=protocols,
                             keep_stations=nrsa_keep_sites or None,
                             value_policy=value_policy)
    frame, values, value_ledger = inputs["frame"], inputs["values"], inputs["value_ledger"]
    value_policy = inputs["value_policy"]      # the id actually read under
    metric_config = inputs["metric_config"]
    landscape_config = inputs["landscape_config"]
    flagged_direction = inputs["flagged_direction"]
    predictor_config = inputs["predictor_config"]

    # --- published curves are carried forward (methodology 0.14) ---
    # Only a missing or newly developed curve walks the hierarchy; a published
    # one keeps its points, layers and annotations unless its data were found
    # defective, and then it is rebuilt like any missing curve.
    prior = (carry_forward.prepare(l3_code) if carry is True
             else (carry if isinstance(carry, dict) else {})) or {}
    carried = dict(prior.get("carried") or {})
    for mk in carried:
        metric_config.pop(mk, None)
    if carried:
        _emit(on_event, "carried_forward",
              {"from_version": prior.get("fromVersion"), "n_carried": len(carried),
               "n_rebuilt": len(prior.get("rebuilt") or {})})

    # --- one hierarchy per metric: the station pools first (REF-04, REF-11) ---
    # Every pool option passes the acceptance criteria before it is used
    # (ACC-01, ACC-04, and ACC-05/06 from the committed recovery evidence).
    validation = basis_ladder.load_validation()
    registry = scale_registry if scale_registry is not None else scale_analysis.load_registry()
    # methodology 0.16: the flagged-transfer rung's settings (REF-16), read once
    flagged_settings = acceptance.flagged_settings()

    def accept_for(mk):
        return acceptance.pool_acceptor(mk, metric_config.get(mk) or {}, validation,
                                        family=rp.family_of(mk))

    def curve_check_for(mk):
        # D13: a pool option whose curve would be a fallback ramp, or would read
        # as inverted against its own pressured stations, is refused
        return acceptance.curve_checker(mk, metric_config.get(mk) or {})

    # REF-15, "your choice stands": a metric the owner removed or chose a source
    # for stays out of the build's own fit while the decision stands
    held = sorted(str(mk) for mk in (hold or ()) if str(mk) in metric_config)
    pools = rp.build_pools([mk for mk in metric_config if mk not in held], values, frame,
                           l3_code, scale_registry=registry, excluded=exclude_sites,
                           accept_for=accept_for, curve_check_for=curve_check_for,
                           flagged=flagged_settings)
    held_by_owner: dict = {}
    if held:
        # judged apart: the pooled frame, and so the run seed, never sees them
        side = rp.build_pools(held, values, frame, l3_code, scale_registry=registry,
                              excluded=exclude_sites, accept_for=accept_for,
                              curve_check_for=curve_check_for, flagged=flagged_settings)
        pools["ledger"] = pd.concat([pools["ledger"], side["ledger"]], ignore_index=True)
        pools["local_comparison"].update(side["local_comparison"])
        for mk, d in side["decisions"].items():
            if d.status == rp.STATUS_INSUFFICIENT:
                pools["decisions"][mk] = d          # walks the ladder as withheld does
            else:
                held_by_owner[mk] = {"decision": d.to_dict(),
                                     "config": dict(metric_config.get(mk) or {})}
        for mk in held_by_owner:
            metric_config.pop(mk, None)            # never fitted, never mapped by the build
        _emit(on_event, "owner_hold", {"n_held": len(held), "n_supported": len(held_by_owner)})
    decisions = pools["decisions"]
    insufficient = {mk: d for mk, d in decisions.items()
                    if d.status == rp.STATUS_INSUFFICIENT}
    # REF-16: the station pools taken under the flagged rung. A validated source
    # of any kind wins over a flagged one, so these metrics walk the national,
    # modeled and published rungs first and keep the flagged pool only when
    # every one of them refuses.
    flagged_pools = {mk: d for mk, d in decisions.items() if d.flagged}

    # --- then national, modeled and published (REF-12, REF-13, REF-14) ---
    # Judged by the same criteria, on evidence fixed before the build ran
    # (config/basis_validation.yaml, config/model_registry.yaml, the verified
    # catalog), so nothing here is admitted by the build's own judgement.
    ladder = basis_ladder.resolve(
        sorted(set(insufficient) | set(flagged_pools)), frame=frame, values_wide=values,
        target_l3=l3_code, metric_config=metric_config, validation=validation,
        region_name=name, flagged=flagged_settings,
        # a flagged national pool is taken only where no flagged station pool exists
        flag_national=sorted(insufficient))
    ladder_rows = ladder["curve_rows"]
    replaced_flagged: list[str] = []
    for mk, d in ladder["decisions"].items():
        decisions[mk] = d
        insufficient.pop(mk, None)
        if flagged_pools.pop(mk, None) is not None:
            replaced_flagged.append(mk)
    # REF-16, last: the flagged national pools of the metrics with neither a
    # validated source nor a flagged station pool
    flagged_national: dict[str, dict] = {}
    for mk, got in (ladder.get("flagged_national") or {}).items():
        if mk in insufficient and got.get("row") is not None:
            decisions[mk] = got["decision"]
            ladder_rows[mk] = got["row"]
            insufficient.pop(mk, None)
            flagged_national[mk] = {"option": got.get("option")}
    if replaced_flagged:
        # the pooled frame, and so the run seed, hold only the pools that stand
        pools["data"] = rp.pool_data(decisions, values, frame)
    flagged_metrics = {**{mk: "pool" for mk in sorted(flagged_pools)},
                       **{mk: "national" for mk in sorted(flagged_national)}}
    for mk in sorted(flagged_pools):
        ladder["attempts"].append({
            "metric": mk, "rung": rp.RULE_FLAGGED, "admitted": True,
            "basis": curve_basis.REGIONAL,
            "why": (f"{flagged_pools[mk].n_usable} usable stations of the "
                    f"{rp.LEVEL_LABELS.get(flagged_pools[mk].level or '', '')} pool "
                    f"{flagged_pools[mk].region_code}, taken under the flagged-transfer rung "
                    "after every validated source refused: the recovery test did not confirm "
                    "the source.")})
    if flagged_metrics:
        _emit(on_event, "flagged_transfer",
              {"n_pools": len(flagged_pools), "n_national": len(flagged_national),
               "n_replaced_by_validated": len(replaced_flagged)})

    # REF-15: the refused sources the owner accepted, each computed on its own
    # stations or donors, never through the pooled frame, so the run seed and
    # every curve the build fits stay as they are
    forced = forced_sources(force, metric_config=metric_config, carried=carried,
                            insufficient=insufficient, ladder_rows=ladder_rows, frame=frame,
                            values=values, l3_code=l3_code, name=name, validation=validation,
                            registry=registry, excluded=exclude_sites)
    insufficient_config = {mk: metric_config[mk] for mk in insufficient}
    for mk in insufficient:                      # no curve, so it never reaches the engine
        metric_config.pop(mk, None)
    # a ladder metric rests on no station of this ecoregion, so it cannot ride in
    # the pooled station frame; it follows the fixed-criteria path instead
    ladder_config = {mk: metric_config[mk] for mk in ladder_rows if mk in metric_config}
    for mk in ladder_rows:
        metric_config.pop(mk, None)
    data = pools["data"]

    # --- a rule that withholds a metric whose pool exists (REF-06's record shape) ---
    # The metric leaves the fit here, as REF-06 takes a metric with no pool out;
    # its pool decision is kept and marked so the support record still says
    # what the pool was, and the withheld entry carries the rule, a statement
    # naming the numbers, and the detail behind it. No review item is raised.
    withheld_by_rule: dict[str, str] = {}
    rule_withheld_items: dict[str, dict] = {}

    def _withhold(mk: str, reason: str, statement: str, detail: dict) -> None:
        nonlocal data
        insufficient_config[mk] = metric_config.pop(mk)
        data = data.drop(columns=[mk], errors="ignore")
        withheld_by_rule[mk] = reason
        rule_withheld_items[mk] = {"decision": decisions[mk].to_dict(),
                                   "config": insufficient_config[mk],
                                   "reason": reason, "rule": WITHHELD_RULES[reason],
                                   "statement": statement, "detail": dict(detail)}

    # --- campaign Round 2 (C3a): a zero-inflated pool under the configured rule ---
    # Off by default. The two-part handling is the curve engine's; the withhold
    # handling takes the metric out here.
    zero_inflation = zero_inflation_records(data, metric_config, geometry)
    for mk, rec in zero_inflation.items():
        if rec.get("handling") == curves.ZERO_INFLATED_WITHHOLD:
            _withhold(mk, ZERO_INFLATED_POOL, zero_inflated_statement(rec), rec)

    # --- methodology 0.15 (DATA-03): a pool mostly unmeasured for the metric ---
    # The missing-data fraction over the metric's own pool is judged before any
    # curve is built; above data_rules.max_missingness_review the metric is
    # withheld with its statement, the same in every region, where 0.14 held a
    # built curve for a reviewer. The fraction stays in the record, flagged, so
    # the DATA-03 record of the build states it.
    missingness = pool_missingness({mk: decisions[mk] for mk in metric_config})
    to_withhold = missingness_withheld(missingness)
    # methodology 0.16 (owner decision D18): DATA-03 never empties a function by
    # itself; the last candidate keeps its pool with the missingness stated
    kept_for_coverage = missingness_kept_for_coverage(
        to_withhold, missingness,
        covered_metrics=([mk for mk in metric_config if mk not in to_withhold] + list(ladder_rows)
                         + list(carried) + list(held)
                         + [mk for mk, f in (forced or {}).items()
                            if isinstance(f, dict) and f.get("row") is not None]),
        configs={**metric_config, **ladder_config})
    for mk, rec in to_withhold.items():
        if mk in kept_for_coverage:
            missingness[mk] = {**missingness[mk], "withheld": False, "rule": "DATA-03",
                               "keptForCoverage": kept_for_coverage[mk]}
            continue
        _withhold(mk, HIGH_MISSINGNESS, missingness_statement(rec, decisions[mk]), rec)
        missingness[mk] = {**missingness[mk], "withheld": True, "rule": "DATA-03"}
    if not len(metric_config) or not len(data):
        raise RuntimeError(
            f"no metric of L3 ecoregion {l3_code} has reference support at any level of "
            "the ecoregion hierarchy; nothing to build.")
    _emit(on_event, "reference_pools",
          {"n_metrics": len(decisions), "n_insufficient": len(insufficient),
           "n_borrowed": sum(1 for d in decisions.values()
                             if d.status.startswith("borrowed")),
           "n_ladder": len(ladder_rows), "n_flagged": len(flagged_metrics),
           "n_pool_stations": int(len(data))})
    metric_cols = list(metric_config)

    # --- classification, redundancy, the advisory stratifier screen ---
    # A ladder metric is an ordinary NRSA metric that happens to get its curve
    # from somewhere other than a station pool, so it belongs in the function
    # mapping even though it is out of the pooled frame. Leaving it out made the
    # exporter skip it for having no canonical function.
    carried_config = {mk: c.get("config") or {} for mk, c in carried.items()}
    mapped_cols = metric_cols + [mk for mk in ladder_rows if mk not in metric_cols] + [
        mk for mk in carried if mk not in metric_cols and mk not in ladder_rows]
    column_functions = {c: metric_map.metric_map_function_label(c) for c in mapped_cols}
    mapping_df = staf_library.default_discipline_function_mapping(
        mapped_cols, {**metric_config, **ladder_config, **carried_config})
    redundancy = ra.redundancy_matrix(
        data, metric_config, {c: column_functions[c] for c in metric_cols})
    data = ra.attach_stratifier_sources(data, values=values)
    strat = ra.run_stratifier_analysis(data, metric_config, predictor_config, on_event=on_event)
    data = strat["data"]

    # --- curves (the engine is unchanged) and the review classification ---
    curve_rows = ra.build_curves(data, metric_config)
    curve_review = ra.review_curves(curve_rows, column_functions,
                                    missingness=missingness, metric_config=metric_config)

    # --- methodology 0.15 (CURVE-09): the measurement-precision floor ---
    # A two-sided curve whose fitted Functioning core is narrower than the
    # metric's floor is withheld here with the core width and the floor in its
    # statement, the same in every region, where 0.9 to 0.14 raised an advisory
    # review item. The curve leaves the rows, the review map, the pooled frame
    # and the redundancy table before any diagnostic runs on it.
    narrow = precision_floor_records(curve_rows, metric_config)
    for mk, rec in narrow.items():
        _withhold(mk, MEASUREMENT_PRECISION_FLOOR, precision_floor_statement(rec), rec)
        curve_rows.pop(mk, None)
        curve_review.pop(mk, None)
    if narrow and redundancy is not None and len(redundancy) and "metric_a" in redundancy.columns:
        gone = set(narrow)
        keep = ~(redundancy["metric_a"].astype(str).isin(gone)
                 | redundancy["metric_b"].astype(str).isin(gone))
        redundancy = redundancy[keep].reset_index(drop=True)
    stratum_rows, strata_applied = stratified_rows(data, metric_config, decisions, registry)

    sample_sizes = {mk: {"n": row.get("n_reference"),
                         "disposition": ra.sample_size_disposition(row.get("n_reference"))}
                    for mk, row in curve_rows.items()}
    sample_size_flags = [{"metric": mk, "n": v["n"], "disposition": v["disposition"]}
                         for mk, v in sample_sizes.items()
                         if v["disposition"] in ("exploratory", "insufficient", "too_few")]
    union_ids = sorted(data["site_id"].astype(str))
    base_seed = ra.run_seed(l3_code, union_ids, methodology.methodology_version())
    diagnostics = (ra.curve_diagnostics_for(data, metric_config, seed=base_seed,
                                            n_boot=diagnostics_n_boot)
                   if diagnostics_enabled else {})

    red06_stability: dict[str, dict] = {}
    if diagnostics_enabled and redundancy is not None and len(redundancy) \
            and "metric_a" in redundancy.columns:
        for row in redundancy.itertuples(index=False):
            r = row._asdict()
            if not r.get("red01_spearman_flag"):
                continue
            a, b = str(r.get("metric_a")), str(r.get("metric_b"))
            if a in data.columns and b in data.columns:
                red06_stability[f"{a}|{b}"] = (
                    curve_stability.bootstrap_pair_category_stability(
                        data[a], data[b], n_boot=diagnostics_n_boot,
                        seed=ra._metric_seed(base_seed, f"{a}|{b}")))
    strat_evidence_df = (ra.stratifier_evidence(data, metric_config, strat, seed=base_seed,
                                                n_boot=min(100, diagnostics_n_boot))
                         if diagnostics_enabled else pd.DataFrame())

    domain_checks: dict[str, dict] = {}
    for mk, row in curve_rows.items():
        dom = curves.metric_domain_of(metric_config.get(mk))
        domain_checks[mk] = {"domain_min": dom[0], "domain_max": dom[1],
                             "violations": curves.count_domain_violations(
                                 row.get("curve_points"), dom[0], dom[1])}
    deferred_gradients = ra.deferred_gradient_candidates(strat_evidence_df)
    # a gradient the registry split already models is not deferred
    for mk in [m for m, rec in strata_applied.items() if rec.get("applied")]:
        deferred_gradients.pop(mk, None)

    discrimination = (discrimination_records(curve_rows, metric_config, decisions, data,
                                             values, frame)
                      if diagnostics_enabled else {})

    # --- campaign Round 2 (C2): the CURVE-12 gate, off by default ---
    # A new curve the check finds inverted or below the floor is withheld here,
    # as REF-06 withholds: it leaves the curves, the review map and the metric
    # config, its pool decision stays marked, and its CURVE-12 record stays for
    # information. Carried curves have no record here and are never gated.
    gated = discrimination_gate(discrimination) if discrimination else {}
    for mk, rec in gated.items():
        insufficient_config[mk] = metric_config.pop(mk)
        curve_rows.pop(mk, None)
        curve_review.pop(mk, None)
        stratum_rows.pop(mk, None)
        strata_applied.pop(mk, None)
        diagnostics.pop(mk, None)
        domain_checks.pop(mk, None)
        deferred_gradients.pop(mk, None)
        data = data.drop(columns=[mk], errors="ignore")
        withheld_by_rule[mk] = DISCRIMINATION_GATE
        rule_withheld_items[mk] = {"decision": decisions[mk].to_dict(),
                                   "config": insufficient_config[mk],
                                   "reason": DISCRIMINATION_GATE,
                                   "rule": WITHHELD_RULES[DISCRIMINATION_GATE],
                                   "statement": discrimination_gate_statement(rec),
                                   "detail": dict(rec)}

    # --- methodology 0.16 (REF-17, owner decision D19): the last resort ---
    # A function every source above left without a curve (each candidate metric
    # refused or withheld) is scored on EASI's national screening method for the
    # same quantity, where config/fixed_criteria.yaml names one. It never
    # replaces a curve and never joins a function that holds any other curve or
    # an owner's standing decision.
    covered_metrics = (set(curve_rows) | set(ladder_rows) | set(carried) | set(held)
                       | {mk for mk, f in (forced or {}).items()
                          if isinstance(f, dict) and f.get("row") is not None})
    last_resort = last_resort_fills(
        l3_code, name, covered_metrics=covered_metrics,
        candidate_metrics={**insufficient,
                           **{mk: rec.get("decision") for mk, rec in rule_withheld_items.items()}},
        configs={**insufficient_config, **metric_config, **ladder_config},
        carried=carried,
        withheld_statements={mk: rec.get("statement") for mk, rec in rule_withheld_items.items()},
        nars9=published_benchmark.majority_region(
            frame[frame["l3"].astype(str) == str(l3_code)], "nars9")[0])
    for mk, fill in last_resort.items():
        decisions[mk] = fill["decision"]
        ladder_rows[mk] = fill["row"]
        ladder_config[mk] = fill["config"]
        ladder["attempts"].append(fill["attempt"])
        insufficient.pop(mk, None)
        insufficient_config.pop(mk, None)
        withheld_by_rule.pop(mk, None)
        rule_withheld_items.pop(mk, None)
        if mk in missingness:
            missingness[mk] = {**missingness[mk], "withheld": False,
                               "replacedBy": fixed_criteria.LAST_RESORT_RULE}
        column_functions[mk] = fill["function_label"]
    if last_resort:
        mapping_df = _with_last_resort_mapping(mapping_df, sorted(last_resort))
        _emit(on_event, "last_resort", {"n_functions": len(last_resort),
                                        "metrics": sorted(last_resort)})

    fixed_metrics = {mk: fixed_criteria.curve_row(mk) for mk in fixed_criteria.metric_keys()}
    n_local_reference = len(retained_ids)

    return {
        "l3_code": str(l3_code), "name": name,
        "reference_method": METHOD,
        "reference_screen": {"id": rscreen.SCREEN_ID, "tier": "strict",
                             "label": rscreen.screen_label("strict"),
                             "methodVersion": run_state.REFERENCE_SCREEN_METHOD_VERSION,
                             "stationScreen": rscreen.station_screen_identity()},
        "screen_preset": rscreen.screen_label("strict"),
        "n_candidates": n_candidates,
        "screening_method": rscreen.METHOD,
        "screening_counts": counts,
        "screening_watershed_engine": None, "screening_comid_mode": None,
        "screening_comids": None, "screening_cache": None, "easi_vendor": None,
        "tier": tier, "screening": screening, "retained_ids": retained_ids,
        "nrsa_dataset": dataset_id,
        "nrsa_cycles": list(nrsa_cycles) if nrsa_cycles else None,
        "nrsa_policy": value_policy,
        "nrsa_max_stream_order": nrsa_max_stream_order,
        "nrsa_protocols": list(protocols) if protocols else None,
        "nrsa_frame_overrides": frame_overrides,
        "nrsa_n_out_of_frame": n_out_of_frame,
        "nrsa_panel_summary": ra._panel_summary(candidates, panel_ledger),
        "nrsa_panel_ledger": panel_ledger.to_dict("records") if len(panel_ledger) else [],
        "n_retained": n_local_reference,
        "metric_config": metric_config, "predictor_config": predictor_config,
        "column_functions": column_functions, "mapping_df": mapping_df,
        "flagged_direction": flagged_direction,
        "source_reports": [{"source": "station_screen", "status": "ok",
                            "n_columns": len(landscape_config),
                            "reason": "landscape expectation values and predictors read from "
                                      "the committed station table"}],
        "predictor_source_flag": "streamcat", "predictor_source": "streamcat",
        "resourced_metrics": [],
        "owner_site_exclusions": owner_site_exclusions,
        "data": data, "curve_rows": curve_rows, "curve_review": curve_review,
        "sample_sizes": sample_sizes, "sample_size_flags": sample_size_flags,
        "missingness": missingness,
        "reference_pool_disposition": ra.sample_size_disposition(n_local_reference),
        "run_seed": base_seed, "diagnostics_n_boot": diagnostics_n_boot,
        "diagnostics": diagnostics, "red06_stability": red06_stability,
        "strat_evidence": strat_evidence_df,
        "tier_evaluation": [],          # REF-02's table; the ladder has no tiers to compare
        "domain_checks": domain_checks, "deferred_gradients": deferred_gradients,
        "redundancy": redundancy, "stratifiers": strat,
        # --- what the pressure method adds ---
        # (a metric a rule withheld although its pool exists keeps its pool
        # decision under status ``withheld``, the pool's own status beside it,
        # with the reason and the rule, so the record says what the pool was)
        "reference_support": {**{mk: (withheld_support_record(d.to_dict(), withheld_by_rule[mk])
                                      if mk in withheld_by_rule else d.to_dict())
                                 for mk, d in decisions.items()},
                              **{mk: dict(c.get("decision") or {}) for mk, c in carried.items()}},
        "reference_pool_ledger": pools["ledger"],
        "reference_pool_summary": {k: pools[k] for k in ("target_n_frame", "target_n_strict",
                                                         "target_n_relaxed")},
        "local_comparison": pools["local_comparison"],
        # REF-08/09/10: curves that rest on something other than a station pool
        # of this ecoregion or a parent. They follow the fixed-criteria path,
        # because like a fixed criterion they cannot ride in the station frame.
        "ladder_metrics": ladder_rows,
        "ladder_config": ladder_config,
        "ladder_attempts": ladder["attempts"],
        "ladder_populations": ladder["populations"],
        # methodology 0.16 (REF-16): the metrics scored on a flagged transfer, by
        # source (a station pool or a national pool), the flagged pools a
        # validated source replaced, and the rung's settings as read
        "flagged_metrics": flagged_metrics,
        "flagged_replaced": sorted(replaced_flagged),
        "flagged_transfer_settings": dict(flagged_settings),
        # methodology 0.16 (REF-17): the functions completed by an adopted EASI
        # screening method, the metric that completes each and the candidates refused
        "last_resort_metrics": {mk: {"functionId": f["function_id"],
                                     "function": f["function_label"],
                                     "candidates": f["candidates"]}
                                for mk, f in last_resort.items()},
        # methodology 0.14: the published curves carried forward unchanged, the
        # version they come from, and the published curves rebuilt and why
        "carried": carried,
        "carried_from": {k: prior.get(k) for k in ("assessmentId", "fromVersion",
                                                   "contentDigest") if prior.get(k)},
        "carry_rebuilt": dict(prior.get("rebuilt") or {}),
        # the published version's SELECT-01 approvals, which carry when their
        # function's metric set is carried unchanged (carry_forward.carried_approvals)
        "carried_approvals": list(prior.get("approvals") or []),
        "insufficient_support": {**{mk: {"decision": d.to_dict(),
                                         "config": insufficient_config.get(mk) or {}}
                                    for mk, d in insufficient.items()},
                                 **rule_withheld_items},
        # campaign Round 2: the knobs set away from their defaults (the manifest's
        # reference.knobs and the inputs digest), the zero-inflation records and
        # the curves the discrimination gate withheld (empty on the defaults)
        "methodology_knobs": methodology_knobs,
        "zero_inflation": zero_inflation,
        "discrimination_gate": gated,
        # REF-15: the refused sources the owner accepted, computed; the requests
        # ride in the manifest so the inputs digest names them
        "forced_metrics": forced,
        "forced_sources": {mk: dict(ref) for mk, ref in sorted((force or {}).items())},
        # REF-15, "your choice stands": the metrics the owner's decisions held out
        # of the fit (the request, which the inputs digest names), and the ones a
        # station pool would have supported
        "owner_hold": sorted(str(mk) for mk in (hold or ())),
        "held_by_owner": held_by_owner,
        "fixed_metrics": fixed_metrics,
        "discrimination": discrimination,
        "stratum_rows": stratum_rows, "strata_applied": strata_applied,
        "scale_registry": {"sha256": scale_analysis.registry_sha256(),
                           "version": (registry or {}).get("version"),
                           "analysis_version": (registry or {}).get("analysis_version"),
                           "present": bool((registry or {}).get("metrics"))},
        "value_selection": {"policy": value_policy,
                            "byMetricCycle": nrsa_dataset.latest_values_summary(
                                value_ledger[value_ledger["metric"].isin(
                                    [c for c in metric_cols if c in metric_config])])},
    }
