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

The curve engine is untouched.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

import re

import pandas as pd

from . import basis_ladder
from . import curve_basis
from . import published_benchmark
from . import curve_stability, curves, easi_screening, field_methods, fixed_criteria
from . import metric_map
from . import methodology, nrsa, nrsa_dataset, run_state, staf_library
from .deep_export import deep_slug
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
    same way. The ``stratifier`` block carries the words a person reads."""
    layers = [{"stratum": "", "curve_points": row.get("curve_points")}]
    for srow in class_rows or []:
        if str(srow.get("curve_status") or "complete") != "complete":
            continue
        layers.append({"stratum": str(srow.get("stratum") or ""),
                       "curve_points": srow.get("curve_points")})
    out = dict(row)
    out["all_strata"] = layers
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


def withheld_metrics(evidence: dict) -> list[dict]:
    """The bundle's ``insufficientReferenceSupport`` list (REF-06): every metric
    withheld because no defensible reference pool exists, with the function it
    would have scored and what was tried."""
    out = []
    for mk, item in sorted((evidence.get("insufficient_support") or {}).items()):
        cfg = item.get("config") or {}
        d = item.get("decision") or {}
        functions = _functions_of(mk)
        out.append({
            "metricId": "spring-" + deep_slug(mk), "metricKey": mk,
            "metricName": cfg.get("display_name") or mk, "units": cfg.get("units") or "",
            "functions": functions,
            "functionId": functions[0]["functionId"] if functions else None,
            "reason": "insufficient-reference-support",
            "statement": _withheld_statement(evidence, mk),
            "levelsTried": d.get("levels_tried") or [],
            "rungsTried": [{"rung": a.get("rung"), "why": a.get("why"),
                            **({"condition": a["condition"]} if a.get("condition") else {})}
                           for a in (evidence.get("ladder_attempts") or [])
                           if a.get("metric") == mk and a.get("rung")],
        })
    return out


def _withheld_statement(evidence: dict, metric: str) -> str:
    """Why this metric is not scored, naming every rung that refused it.

    "Unassessed" with no reason is what the coverage gate exists to stop, so a
    withheld metric states the hierarchy AND the three rungs above it.
    """
    base = ("Insufficient reference support. Too few comparable least-disturbed stations carry "
            "this metric in this ecoregion or in its Level II and Level I parents.")
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
        evidence.get("ladder_metrics") or {})
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


#: the basis label a reader sees for each rung above the hierarchy
RUNG_LABELS = {"REF-08": curve_basis.label_for(curve_basis.NATIONAL),
               "REF-09": curve_basis.label_for(curve_basis.MODELED),
               "REF-10": curve_basis.label_for(curve_basis.PUBLISHED)}


def reference_method_block(evidence: dict) -> dict:
    """The bundle's top-level ``referenceMethod`` block."""
    screen = evidence.get("reference_screen") or {}
    support = evidence.get("reference_support") or {}
    statuses = [str(d.get("status")) for d in support.values()]
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
            "nWithheld": statuses.count(rp.STATUS_INSUFFICIENT),
            "curvesByBasis": _basis_counts(support),
            "statement": _method_statement(support)}


def _basis_counts(support: dict) -> dict:
    """How many curves rest on each rung, so the bundle states its own mix."""
    out: dict = {}
    for d in support.values():
        if str(d.get("status")) == rp.STATUS_INSUFFICIENT:
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
    local = sum(1 for d in support.values() if str(d.get("status")) == rp.STATUS_LOCAL)
    borrowed = sum(1 for d in support.values()
                   if str(d.get("status") or "").startswith("borrowed"))
    parts = ["Reference condition is set by a fixed landscape-pressure screen, never by a score."]
    if local:
        parts.append(f"{local} {_curves(local)} fitted to least-disturbed stations of this "
                     f"ecoregion.")
    if borrowed:
        parts.append(f"{borrowed} {'borrows' if borrowed == 1 else 'borrow'} comparable "
                     f"least-disturbed stations from a parent ecoregion, stating the level and "
                     f"the transfer risk.")
    for basis in (curve_basis.NATIONAL, curve_basis.MODELED, curve_basis.PUBLISHED):
        n = counts.get(basis, 0)
        if not n:
            continue
        said = curve_basis.statement_for(basis)
        parts.append(f"{n} {'carries' if n == 1 else 'carry'} the "
                     f"{curve_basis.label_for(basis).lower()} label: "
                     + said[0].lower() + said[1:])
    parts.append("A metric that no basis supports is not scored, and the function it would "
                 "have scored is reported as unassessed.")
    return " ".join(parts)


def _curves(n: int) -> str:
    return "curve is" if n == 1 else "curves are"


def revision_note(evidence: dict) -> str:
    block = reference_method_block(evidence)
    return (f"Reference condition is the fixed landscape-pressure screen {block['screenLabel']}. "
            f"{block['nLocalReference']} of {block['nInFrame'] or block['nCandidates']} stations "
            f"of this ecoregion pass. {block['nCurvesLocal']} reference curves use local stations, "
            f"{block['nCurvesBorrowed']} borrow from a parent ecoregion, "
            f"{len(evidence.get('fixed_metrics') or {})} metrics use fixed criteria, and "
            f"{block['nWithheld']} metrics are withheld for insufficient reference support.")


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
    out = []
    for fid, items in sorted(by_fn.items()):
        names = ", ".join(sorted(str(w.get("metricName")) for w in items))
        out.append({
            "functionId": fid, "reason": "insufficient-reference-support",
            "justification": (
                f"Every candidate metric for this function ({names}) was withheld. Too few "
                "comparable least-disturbed stations carry them in this ecoregion or in its "
                "Level II and Level I parents, no national donor pool was shown to transfer "
                "here, no modeled expectation passed validation, and no published criterion "
                "applies to them. No curve was forced." + _blocker_detail(result, items)),
            "recordedBy": recorded_by, "recordedAt": None})
    return out


def _blocker_detail(result: dict, items: list[dict]) -> str:
    """The specific refusal for the published rung, where there is one.

    A function that is unassessed has to name its blocker. The published rung is
    the last one tried, so its reason is the one a reader most needs.
    """
    keys = {str(w.get("metricKey")) for w in items}
    for a in (result.get("ladder_attempts") or []):
        if a.get("rung") == "REF-10" and str(a.get("metric")) in keys and a.get("why"):
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


SESSION_ANNOTATION_KEYS = ("criteriaBasis", "basis", "basisLabel", "basisStatement",
                           "basisLimit", "referenceSupport", "localComparison",
                           "stratifier", "discrimination", "methodContext")


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
    annotations = {
        mk: ({k: v for k, v in ann.items() if v is not None} if mk in ladder else
             {k: ann[k] for k in SESSION_ANNOTATION_KEYS if ann.get(k) is not None})
        for mk, ann in (meta.get("metricAnnotations") or {}).items() if mk not in fixed}
    return {"method": METHOD,
            "referenceMethod": meta.get("referenceMethod"),
            "insufficientReferenceSupport": list(meta.get("insufficientReferenceSupport") or []),
            "metricAnnotations": annotations,
            "fixedMetrics": fixed,
            "ladderMetrics": ladder_session_rows(result),
            "referenceTier": result.get("reference_tier")}


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


def apply_reference_build(build: Optional[dict], curve_rows: dict, mapping,
                          metric_config: dict, meta: dict) -> tuple[dict, object, dict]:
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
    fixed = [mk for mk in (build.get("fixedMetrics") or []) if fixed_criteria.is_fixed(mk)]
    if fixed:
        fixed_config = fixed_criteria.metric_config_entries()
        for mk in fixed:
            rows[mk] = fixed_criteria.curve_row(mk)
            config[mk] = fixed_config[mk]
        annotations.update(fixed_annotations(fixed))
        mapping = _with_fixed_mapping(mapping, fixed)
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


def _restore_ladder_rows(build: dict, rows: dict, config: dict) -> None:
    """Put back the curves a rung above the hierarchy produced, from the points
    the session recorded. Mutates ``rows`` and ``config``."""
    for mk, saved in (build.get("ladderMetrics") or {}).items():
        pts = saved.get("points") or []
        if mk in rows or not pts:
            continue
        rows[mk] = {
            "metric": mk, "display_name": saved.get("displayName") or mk,
            "stratum": saved.get("stratum") or "", "n_reference": saved.get("nReference"),
            "curve_status": saved.get("curveStatus") or "complete",
            "curve_source": saved.get("curveSource") or "auto",
            "curve_points": pd.DataFrame(
                [{"point_order": i + 1, "metric_value": float(q["x"]),
                  "index_score": float(q["y"])} for i, q in enumerate(pts)]),
        }
        if saved.get("config"):
            config[mk] = saved["config"]


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
            annotations[mk] = merged
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


# --------------------------------------------------------------------------- #
# inputs shared by the build and the census
# --------------------------------------------------------------------------- #
def national_inputs(*, dataset_id: Optional[str] = None, cycles=None,
                    max_stream_order: Optional[int] = None, protocols=None,
                    keep_stations: Optional[dict] = None) -> dict:
    """The national frame, the DATA-11 values of every metric a build could
    fit, and the metric configs. One function, so the census a reviewer reads
    before a build and the build itself can never see different inputs."""
    from . import regional_agent as ra
    from . import stratifiers
    dataset_id = dataset_id or nrsa_dataset.MULTI_CYCLE_DATASET_ID
    directions = ra.load_directions()
    landscape_directions = ra.load_landscape_directions()
    frame, frame_ledger = rp.national_frame(
        dataset_id=dataset_id, cycles=cycles, max_stream_order=max_stream_order,
        protocols=protocols, keep_stations=keep_stations or None)
    dataset = nrsa_dataset.load_dataset(dataset_id)
    metric_config, flagged_direction = ra.build_metric_config(
        sorted(dataset.metric_columns()), directions)
    registry_cols = [c for c in stratifiers.source_columns(stratifiers.load_national_registry())
                     if c in dataset.values.columns]
    wanted = list(dict.fromkeys(list(metric_config) + registry_cols))
    values, value_ledger = nrsa_dataset.latest_values(
        frame["station_key"], dataset=dataset, metrics=wanted,
        cycles=cycles or nrsa_dataset.CYCLES_NEWEST_FIRST)
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
                                                          landscape_directions)}


CENSUS_COLUMNS = ["l3", "region", "metric", "display_name", "family", "status", "level",
                  "region_code", "region_name", "n_pool", "n_comparable", "n_usable", "n_local",
                  "n_huc12", "disposition", "covariates", "lithology", "self_coverage",
                  "supported_level", "transfer_risk", "zero_inflated", "q25"]


def census(l3_codes, *, max_stream_order: Optional[int] = None, protocols=None,
           keep_stations: Optional[dict] = None, excluded: Optional[dict] = None,
           scale_registry: Optional[dict] = None) -> dict:
    """Reference support per region and metric, before any curve is fitted.

    Returns ``{"table": DataFrame, "regions": [...]}``. ``zero_inflated`` marks
    a higher-is-better metric whose pool's lower quartile is at or below zero:
    the curve engine can only draw a degenerate seed from such a pool, so the
    reviewer sees it here and not first in a flagged curve.
    """
    from . import regional_agent as ra
    inputs = national_inputs(max_stream_order=max_stream_order, protocols=protocols,
                             keep_stations=keep_stations)
    frame, values, metric_config = inputs["frame"], inputs["values"], inputs["metric_config"]
    registry = scale_registry if scale_registry is not None else scale_analysis.load_registry()
    wide = values.set_index(values["site_id"].astype(str))
    rows: list[dict] = []
    regions: list[dict] = []
    for code in l3_codes:
        code = str(code).strip()
        name = ra.region_name_for(code) or rp._level_name(frame, "l3", code) or f"L3 {code}"
        pools = rp.build_pools(list(metric_config), values, frame, code,
                               scale_registry=registry, excluded=excluded)
        # the same ladder the build walks, gates only: the census must not say a
        # metric is withheld that the build will go on to score
        short = [mk for mk, d in pools["decisions"].items()
                 if d.status == rp.STATUS_INSUFFICIENT]
        if short:
            walked = basis_ladder.resolve(
                sorted(short), frame=frame, values_wide=values, target_l3=code,
                metric_config=metric_config, validation=basis_ladder.load_validation(),
                region_name=name, fit=False)
            pools["decisions"].update(walked["decisions"])
        statuses = [d.status for d in pools["decisions"].values()]
        regions.append({"l3": code, "region": name,
                        "n_frame": pools["target_n_frame"], "n_strict": pools["target_n_strict"],
                        "n_relaxed": pools["target_n_relaxed"],
                        "n_local": statuses.count(rp.STATUS_LOCAL),
                        "n_borrowed_l2": statuses.count("borrowed_l2"),
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
                 scale_registry: Optional[dict] = None) -> dict:
    """The decision-free half of a regional run under the pressure screen."""
    from . import regional_agent as ra

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
                             keep_stations=nrsa_keep_sites or None)
    frame, values, value_ledger = inputs["frame"], inputs["values"], inputs["value_ledger"]
    metric_config = inputs["metric_config"]
    landscape_config = inputs["landscape_config"]
    flagged_direction = inputs["flagged_direction"]
    predictor_config = inputs["predictor_config"]

    # --- one pool per metric (REF-05, REF-06, REF-07) ---
    registry = scale_registry if scale_registry is not None else scale_analysis.load_registry()
    pools = rp.build_pools(list(metric_config), values, frame, l3_code,
                           scale_registry=registry, excluded=exclude_sites)
    decisions = pools["decisions"]
    insufficient = {mk: d for mk, d in decisions.items()
                    if d.status == rp.STATUS_INSUFFICIENT}

    # --- REF-08/09/10: what to try once the ecoregion hierarchy runs out ---
    # Every rung is gated on evidence fixed before it ran (config/basis_validation.yaml
    # for the fitted and borrowed rungs, the vendored catalog for the published one),
    # so nothing here is admitted by the build's own judgement.
    ladder = basis_ladder.resolve(
        sorted(insufficient), frame=frame, values_wide=values, target_l3=l3_code,
        metric_config=metric_config, validation=basis_ladder.load_validation(),
        region_name=name)
    ladder_rows = ladder["curve_rows"]
    for mk, d in ladder["decisions"].items():
        decisions[mk] = d
        insufficient.pop(mk, None)

    insufficient_config = {mk: metric_config[mk] for mk in insufficient}
    for mk in insufficient:                      # no curve, so it never reaches the engine
        metric_config.pop(mk, None)
    # a ladder metric rests on no station of this ecoregion, so it cannot ride in
    # the pooled station frame; it follows the fixed-criteria path instead
    ladder_config = {mk: metric_config[mk] for mk in ladder_rows if mk in metric_config}
    for mk in ladder_rows:
        metric_config.pop(mk, None)
    data = pools["data"]
    if not len(metric_config) or not len(data):
        raise RuntimeError(
            f"no metric of L3 ecoregion {l3_code} has reference support at any level of "
            "the ecoregion hierarchy; nothing to build.")
    _emit(on_event, "reference_pools",
          {"n_metrics": len(decisions), "n_insufficient": len(insufficient),
           "n_borrowed": sum(1 for d in decisions.values()
                             if d.status.startswith("borrowed")),
           "n_ladder": len(ladder_rows),
           "n_pool_stations": int(len(data))})
    metric_cols = list(metric_config)

    # --- classification, redundancy, the advisory stratifier screen ---
    # A ladder metric is an ordinary NRSA metric that happens to get its curve
    # from somewhere other than a station pool, so it belongs in the function
    # mapping even though it is out of the pooled frame. Leaving it out made the
    # exporter skip it for having no canonical function.
    mapped_cols = metric_cols + [mk for mk in ladder_rows if mk not in metric_cols]
    column_functions = {c: metric_map.metric_map_function_label(c) for c in mapped_cols}
    mapping_df = staf_library.default_discipline_function_mapping(
        mapped_cols, {**metric_config, **ladder_config})
    redundancy = ra.redundancy_matrix(
        data, metric_config, {c: column_functions[c] for c in metric_cols})
    data = ra.attach_stratifier_sources(data, values=values)
    strat = ra.run_stratifier_analysis(data, metric_config, predictor_config, on_event=on_event)
    data = strat["data"]

    missingness = pool_missingness({mk: decisions[mk] for mk in metric_cols})

    # --- curves (the engine is unchanged) and the review classification ---
    curve_rows = ra.build_curves(data, metric_config)
    curve_review = ra.review_curves(curve_rows, column_functions,
                                    missingness=missingness, metric_config=metric_config)
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
        "nrsa_policy": nrsa_dataset.POLICY_LATEST_NON_NULL,
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
        "reference_support": {mk: d.to_dict() for mk, d in decisions.items()},
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
        "insufficient_support": {mk: {"decision": d.to_dict(),
                                      "config": insufficient_config.get(mk) or {}}
                                 for mk, d in insufficient.items()},
        "fixed_metrics": fixed_metrics,
        "discrimination": discrimination,
        "stratum_rows": stratum_rows, "strata_applied": strata_applied,
        "scale_registry": {"sha256": scale_analysis.registry_sha256(),
                           "version": (registry or {}).get("version"),
                           "analysis_version": (registry or {}).get("analysis_version"),
                           "present": bool((registry or {}).get("metrics"))},
        "value_selection": {"policy": nrsa_dataset.POLICY_LATEST_NON_NULL,
                            "byMetricCycle": nrsa_dataset.latest_values_summary(
                                value_ledger[value_ledger["metric"].isin(metric_cols)])},
    }
