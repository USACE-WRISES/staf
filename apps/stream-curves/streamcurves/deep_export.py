"""Port of R/20_deep_export.R — export a completed set of reference curves as a
DEEP "detailed assessment bundle" (the STAF metric-library contract that the
DEEP executor app consumes on upload).

The bundle is a self-contained JSON object: assessment metadata + a
``metricsByFunction`` list where every selected metric carries its reference
curve inlined as ``{x = metric value, y = index score}`` points. Function labels
are mapped to canonical STAF function ids via config/staf_functions.json so the
bundle validates against DEEP's 20-function framework.

Contract mirror (DEEP side): ``deep/assessments.py`` validate_bundle/from_bundle
and ``deep/curves.py`` interp_curve. Points are ascending in x with direction
encoded in y (index score), which is exactly how DEEP interpolates — no
transform needed.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from . import curves
from .paths import CONFIG_DIR

logger = logging.getLogger("streamcurves")

# The DEEP scoring contract's numeric constants, stated once. They mirror
# apps/deep/deep/config.py (INDEX_BANDS thresholds, FUNCTION_SCORE_BANDS,
# FUNCTION_SCORE_MAX, indirect weight); the methodology mirror check
# (methodology.mirror_drift) verifies the methodology config against them.
SCORING_CONTRACT_CONSTANTS = {
    "indexBands": [0.39, 0.69],
    "functionScoreBands": [5, 10],
    "functionScoreMax": 15,
    "indirectWeight": 0.10,
}


# ---- small helpers ----------------------------------------------------------
def _is_na(x: Any) -> bool:
    """Scalar NA check (R ``is.na`` on a length-1 value). Containers never NA."""
    if isinstance(x, (pd.DataFrame, pd.Series, np.ndarray, list, tuple, dict, set)):
        return False
    try:
        return bool(pd.isna(x))
    except (TypeError, ValueError):
        return False


def deep_default(x: Any, d: Any) -> Any:
    """R ``deep_default``: return ``d`` when ``x`` is NULL (None) or a scalar NA."""
    if x is None:
        return d
    if _is_na(x):
        return d
    return x


def _as_num(x: Any) -> float:
    """R ``as.numeric`` for a scalar: unparseable -> NA (NaN)."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def deep_slug(x: Any) -> str:
    s = str(x).strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"^-+|-+$", "", s)


def deep_norm_label(x: Any) -> str:
    s = str(x).strip().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---- canonical STAF function crosswalk --------------------------------------
#: path -> (mtime_ns, parsed functions). The crosswalk is read on every
#: snapshot/coverage computation, so an uncached read made each workflow-strip
#: render pay two disk reads + JSON parses. Path RESOLUTION (env vars included)
#: still runs on every call; only the file read is memoized, keyed on mtime so
#: an edited file is picked up. Consumers treat the list as read-only.
_CROSSWALK_CACHE: dict[str, tuple[int, list[dict]]] = {}


def deep_read_staf_crosswalk(path: Optional[str | Path] = None) -> list[dict]:
    if path is None:
        # NOTE(parity): R probes cwd-relative "config/staf_functions.json" first;
        # here the repo CONFIG_DIR is the canonical copy (paths come from
        # streamcurves.paths per PORTING.md), with the R fallbacks retained for
        # odd working directories / an explicit STREAMCURVES_ROOT.
        cand: list[Path] = [
            CONFIG_DIR / "staf_functions.json",
            Path("config") / "staf_functions.json",
            Path("..") / "config" / "staf_functions.json",
        ]
        env_root = os.environ.get("STREAMCURVES_ROOT", "")
        if env_root:
            cand.append(Path(env_root) / "config" / "staf_functions.json")
        hit = [p for p in cand if p.exists()]
        if not hit:
            raise ValueError("config/staf_functions.json not found; pass `crosswalk_path` explicitly")
        path = hit[0]
    resolved = Path(path)
    key = str(resolved)
    mtime = resolved.stat().st_mtime_ns
    cached = _CROSSWALK_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    doc = json.loads(resolved.read_text(encoding="utf-8"))
    _CROSSWALK_CACHE[key] = (mtime, doc["functions"])
    return doc["functions"]


def deep_function_lookup(crosswalk: list[dict]) -> dict[str, dict]:
    """Normalized name/alias -> function record lookup."""
    idx: dict[str, dict] = {}
    for f in crosswalk:
        keys = [f.get("name")] + list(f.get("aliases") or [])
        for k in keys:
            if k is None:
                continue
            idx[deep_norm_label(k)] = f
    return idx


def deep_map_function(label: Any, lookup: dict[str, dict]) -> Optional[dict]:
    return lookup.get(deep_norm_label(label))  # None if no match


# ---- STAF function coverage -------------------------------------------------
# docs/tiered-approach.md: the 20 functions are "a comprehensive starting point.
# Regional tools may consolidate or tailor the list, but changes should be
# documented and traceable back to these functions to preserve comparability."
# So a function may be left uncovered -- but only on the record. Every function
# resolves to covered, excluded (justified), or missing; the library publisher
# refuses to mint a version while anything is still `missing`.

#: Controlled vocabulary for why a function carries no metric, so gaps stay
#: queryable across the library instead of being free prose in each bundle.
FUNCTION_EXCLUSION_REASONS = (
    "not-applicable-to-region",   # the function does not operate in this setting
    "no-suitable-metric",         # nothing in the crosswalk measures it here
    "data-unavailable",           # a metric exists but the source has no coverage
    "direction-unresolved",       # a metric exists but its ecological direction is under review
    "consolidated-into",          # folded into another function (set consolidatedInto)
    "deferred-to-other-tier",     # assessed at screening/rapid instead
)

_MIN_JUSTIFICATION_CHARS = 20


def validate_coverage_exceptions(exceptions, crosswalk: list[dict]) -> list[dict]:
    """Normalize and check documented function exclusions.

    Raises ``ValueError`` naming the offending field: a gap recorded with an
    unknown function, an off-vocabulary reason, or a placeholder justification is
    not documentation, and letting it through would defeat the publish gate.
    """
    if not exceptions:
        return []
    by_id = {str(f.get("id")): f for f in crosswalk}
    out: list[dict] = []
    seen: set[str] = set()
    for i, raw in enumerate(exceptions):
        if not isinstance(raw, dict):
            raise ValueError(f"coverage exception {i}: expected an object, got {type(raw).__name__}")
        fid = str(raw.get("functionId") or "").strip()
        if fid not in by_id:
            raise ValueError(
                f"coverage exception {i}: functionId {fid!r} is not one of the "
                f"{len(by_id)} canonical STAF functions"
            )
        if fid in seen:
            raise ValueError(f"coverage exception {i}: duplicate entry for {fid!r}")
        reason = str(raw.get("reason") or "").strip()
        if reason not in FUNCTION_EXCLUSION_REASONS:
            raise ValueError(
                f"coverage exception {fid!r}: reason {reason!r} is not one of "
                f"{', '.join(FUNCTION_EXCLUSION_REASONS)}"
            )
        justification = str(raw.get("justification") or "").strip()
        if len(justification) < _MIN_JUSTIFICATION_CHARS:
            raise ValueError(
                f"coverage exception {fid!r}: justification must explain the gap "
                f"(at least {_MIN_JUSTIFICATION_CHARS} characters, got {len(justification)})"
            )
        recorded_by = str(raw.get("recordedBy") or "").strip()
        if not recorded_by:
            raise ValueError(f"coverage exception {fid!r}: recordedBy is required")
        fn = by_id[fid]
        entry = {
            "functionId": fid,
            "functionName": fn.get("name"),
            "discipline": fn.get("category"),
            "reason": reason,
            "justification": justification,
            "recordedBy": recorded_by,
            "recordedAt": str(raw.get("recordedAt") or "").strip() or None,
        }
        if reason == "consolidated-into":
            target = str(raw.get("consolidatedInto") or "").strip()
            if target not in by_id:
                raise ValueError(
                    f"coverage exception {fid!r}: consolidated-into requires "
                    "consolidatedInto naming a canonical function"
                )
            entry["consolidatedInto"] = target
        seen.add(fid)
        out.append(entry)
    return out


def function_coverage(metrics_by_function, crosswalk: list[dict],
                      exceptions=None) -> dict:
    """Coverage of the fixed STAF function skeleton by a bundle's metric blocks.

    ``missing`` is what the publish gate reads: functions with neither a metric
    nor a documented exclusion. An exception naming a function that IS covered is
    dropped rather than raising -- coverage wins, and a stale justification left
    behind after a metric was added is not an error worth blocking a publish for.
    """
    ordered_ids = [str(f.get("id")) for f in crosswalk]
    covered = [
        str(b.get("functionId"))
        for b in (metrics_by_function or [])
        if b.get("metrics")
    ]
    covered_set = {c for c in covered if c in set(ordered_ids)}
    validated = validate_coverage_exceptions(exceptions, crosswalk)
    excluded = [e for e in validated if e["functionId"] not in covered_set]
    excluded_ids = {e["functionId"] for e in excluded}
    missing = [f for f in ordered_ids if f not in covered_set and f not in excluded_ids]
    return {
        "framework": "staf-20",
        "total": len(ordered_ids),
        "covered": len(covered_set),
        "excluded": len(excluded),
        "missing": len(missing),
        "coveredFunctionIds": [f for f in ordered_ids if f in covered_set],
        "missingFunctionIds": missing,
        "exclusions": excluded,
    }


def coverage_gap_message(coverage: dict, crosswalk: list[dict]) -> str:
    """Human-readable naming of the uncovered functions, for the publish error."""
    names = {str(f.get("id")): f.get("name") for f in crosswalk}
    gaps = [f"{names.get(fid, fid)} ({fid})" for fid in coverage.get("missingFunctionIds") or []]
    return "; ".join(gaps)


def _mapping_cell_blank(v: Any) -> bool:
    """Blank mapping cell: None/NaN or whitespace-only."""
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    return str(v).strip() == ""


def uncovered_functions_from_mapping(mapping, metric_config,
                                     exceptions=None) -> list[tuple[str, str]]:
    """[(function_id, function_name)] with no assigned metric and no documented
    exception, judged from the discipline-function mapping alone.

    The mapping-level twin of :func:`function_coverage`: that one judges a built
    bundle's metric blocks (and so cannot exist until a curve is finalized);
    this one judges the editable mapping, so the workspace stage and its strip
    status can show the shortfall while there is still time to fix it. Rows
    whose metric_key is not in ``metric_config`` do not count as coverage -- a
    mapping entry for a metric that carries no data covers nothing.
    """
    metric_config = metric_config or {}
    crosswalk = deep_read_staf_crosswalk()
    lookup = deep_function_lookup(crosswalk)
    covered: set[str] = set()
    if mapping is not None and len(mapping) > 0:
        for _, r in mapping.iterrows():
            mk, label = r.get("metric_key"), r.get("function_label")
            if _mapping_cell_blank(mk) or _mapping_cell_blank(label):
                continue
            if str(mk) not in metric_config:
                continue
            fn = deep_map_function(label, lookup)
            if fn is not None:
                covered.add(str(fn.get("id")))
    excused = {str(e.get("functionId")) for e in (exceptions or [])}
    return [(str(f.get("id")), str(f.get("name"))) for f in crosswalk
            if str(f.get("id")) not in covered and str(f.get("id")) not in excused]


def _completed_metric_candidates_status(cm) -> tuple[bool, bool]:
    """(has_candidates, has_complete): does a completed_metrics entry hold any
    curve-row candidate at all, and does any of them count as complete? Mirrors
    deep_collect_curve_rows' candidate gathering (phase4_curve_rows first, the
    stratum_results fallback only when those are empty) and its default of
    "complete" when curve_status is absent/NA."""
    rows = (cm or {}).get("phase4_curve_rows")
    if isinstance(rows, pd.DataFrame) and len(rows) > 0:
        if "curve_status" not in rows.columns:
            return True, True
        return True, bool((rows["curve_status"].fillna("complete") == "complete").any())
    sr = (cm or {}).get("stratum_results")
    has_candidates = False
    if isinstance(sr, dict):
        for res in sr.values():
            try:
                cr = (res or {}).get("reference_curve", {}).get("curve_row")
            except AttributeError:
                continue
            if isinstance(cr, pd.DataFrame) and len(cr) > 0:
                has_candidates = True
                if "curve_status" not in cr.columns:
                    return True, True
                v = cr.iloc[0].get("curve_status")
                if _is_na(v) or str(v) == "complete":
                    return True, True
    return has_candidates, False


def function_coverage_quick(completed_metrics, mapping, exceptions=None) -> Optional[dict]:
    """``functionCoverage`` as the full bundle would report it, judged from
    curve presence and the mapping walk alone; no bundle build, so the workflow
    strip's snapshot can afford it on every render.

    Contract parity with ``build_deep_assessment_bundle``:

    * returns None exactly when ``deep_collect_curve_rows`` would come back
      empty (which makes ``build_bundle_from_state`` raise and the snapshot's
      coverage read None: nothing to judge yet);
    * a metric covers its mapped functions when any of its candidate rows is
      complete (missing ``curve_status`` defaults to complete), matching the
      exporter's pick-then-skip;
    * unmapped or non-canonical function labels add nothing;
    * exceptions run through the same ``function_coverage`` /
      ``validate_coverage_exceptions``, so shape and exclusion semantics are
      identical.

    One documented divergence: curve points are not parsed here, so a complete
    row whose points fail extraction counts covered. Advisory-only drift; the
    publish gate (library.publish_version) still judges the real bundle.
    """
    lookup = deep_function_lookup(deep_read_staf_crosswalk())
    by_metric: dict[str, list[str]] = {}
    if isinstance(mapping, pd.DataFrame) and len(mapping) > 0:
        for mk, lbl in zip(mapping.get("metric_key"), mapping.get("function_label")):
            if not _mapping_cell_blank(mk) and not _mapping_cell_blank(lbl):
                by_metric.setdefault(str(mk), []).append(str(lbl))
    any_candidates = False
    covered: set[str] = set()
    for mk, cm in (completed_metrics or {}).items():
        if cm is None:
            continue
        has_candidates, has_complete = _completed_metric_candidates_status(cm)
        any_candidates = any_candidates or has_candidates
        if not has_complete:
            continue
        for lbl in by_metric.get(str(mk), []):
            fn = deep_map_function(lbl, lookup)
            if fn is not None:
                covered.add(str(fn.get("id")))
    if not any_candidates:
        return None
    return function_coverage(
        [{"functionId": fid, "metrics": [True]} for fid in covered],
        deep_read_staf_crosswalk(), exceptions)


# ---- curve points -----------------------------------------------------------
# Extract a curve row's points as an ascending list of {"x":, "y":}, y in [0,1].
def deep_points_finalize(pts: Optional[pd.DataFrame]) -> Optional[list[dict]]:
    """Finalize a DataFrame(x, y): drop NA, sort ascending x, clamp y -> [{x,y}]."""
    if pts is None or len(pts) == 0:
        return None
    pts = pts.dropna()
    if len(pts) == 0:
        return None
    pts = pts.sort_values("x", kind="stable")
    return [
        {"x": float(x), "y": min(1.0, max(0.0, float(y)))}
        for x, y in zip(pts["x"], pts["y"])
    ]


def deep_points_from_tibble(cp: Any) -> Optional[list[dict]]:
    """Points from a curve_points table (metric_value, index_score) -> [{x,y}].

    Accepts a DataFrame or the R list-column wrapping (a length-1 list holding
    the DataFrame).
    """
    if isinstance(cp, pd.DataFrame):
        tb = cp
    elif isinstance(cp, (list, tuple)):
        # NOTE(parity): R `cp[[1]]` errors on an empty list; we return None.
        tb = cp[0] if len(cp) > 0 else None
    else:
        tb = None
    if not (
        isinstance(tb, pd.DataFrame)
        and len(tb) > 0
        and {"metric_value", "index_score"}.issubset(tb.columns)
    ):
        return None
    return deep_points_finalize(
        pd.DataFrame(
            {
                "x": pd.to_numeric(tb["metric_value"], errors="coerce").to_numpy(),
                "y": pd.to_numeric(tb["index_score"], errors="coerce").to_numpy(),
            }
        )
    )


def deep_points_from_row(row: dict) -> Optional[list[dict]]:
    """Nested curve_points table first, else curve_point{1..7}_{x,y} flat columns."""
    pts = deep_points_from_tibble(row.get("curve_points"))
    if pts is not None:
        return pts
    xs: list[float] = []
    ys: list[float] = []
    for i in range(1, 8):
        xv = deep_default(row.get(f"curve_point{i}_x"), np.nan)
        yv = deep_default(row.get(f"curve_point{i}_y"), np.nan)
        if not _is_na(xv) and not _is_na(yv):
            xs.append(_as_num(xv))
            ys.append(_as_num(yv))
    if len(xs) == 0:
        return None
    return deep_points_finalize(pd.DataFrame({"x": xs, "y": ys}))


# ---- rv -> one finalized curve row per metric -------------------------------
def deep_collect_curve_rows(completed_metrics: dict) -> dict:
    """Flatten completed_metrics into per-metric curve rows (dict of row dicts):
    prefer the unstratified curve, else the first "complete" stratum; gather all
    complete, distinct strata into ``all_strata`` for curveLayers export."""
    out: dict[str, dict] = {}
    for mk, cm in (completed_metrics or {}).items():
        if cm is None:
            continue
        candidates: list[dict] = []

        rows = cm.get("phase4_curve_rows")
        if isinstance(rows, pd.DataFrame) and len(rows) > 0:
            for i in range(len(rows)):
                candidates.append({c: rows.iloc[i][c] for c in rows.columns})
        sr = cm.get("stratum_results")
        if len(candidates) == 0 and isinstance(sr, dict):
            for lvl, res in sr.items():
                try:
                    cr = (res or {}).get("reference_curve", {}).get("curve_row")
                except AttributeError:
                    cr = None
                if isinstance(cr, pd.DataFrame) and len(cr) > 0:
                    rec = {c: cr.iloc[0][c] for c in cr.columns}
                    if rec.get("stratum") is None:
                        rec["stratum"] = lvl
                    # carry the points table if present alongside the row
                    if rec.get("curve_points") is None:
                        try:
                            rec["curve_points"] = (res or {}).get("reference_curve", {}).get(
                                "curve_points"
                            )
                        except AttributeError:
                            rec["curve_points"] = None
                    candidates.append(rec)
        if len(candidates) == 0:
            continue

        # choose: unstratified (stratum NA/"") first, else first complete
        pick = None
        for rec in candidates:
            st = deep_default(rec.get("stratum"), "")
            status = deep_default(rec.get("curve_status"), "complete")
            if (_is_na(st) or str(st) == "") and status == "complete":
                pick = rec
                break
        if pick is None:
            for rec in candidates:
                if deep_default(rec.get("curve_status"), "complete") == "complete":
                    pick = rec
                    break
        if pick is None:
            pick = candidates[0]
        pick["metric"] = deep_default(pick.get("metric"), mk)

        # gather all complete, distinct strata for curveLayers export
        complete: list[dict] = []
        seen: list = []
        for rec in candidates:
            if deep_default(rec.get("curve_status"), "complete") != "complete":
                continue
            stn = deep_default(rec.get("stratum"), "")
            if _is_na(stn):
                stn = ""
            if stn in seen:
                continue
            seen.append(stn)
            complete.append({"stratum": stn, "curve_points": rec.get("curve_points")})
        pick["all_strata"] = complete
        out[mk] = pick
    return out


# ---- the exporter -----------------------------------------------------------
def build_deep_assessment_bundle(
    curve_rows,
    discipline_function_mapping,
    metric_config: Optional[dict] = None,
    meta: Optional[dict] = None,
    crosswalk_path: Optional[str | Path] = None,
) -> dict:
    """Build a DEEP detailed-assessment bundle dict.

    ``curve_rows``: a DataFrame (one row per metric, reference_curve_thresholds
    shape) OR a dict/list of per-metric row dicts (e.g. from
    :func:`deep_collect_curve_rows`).
    ``discipline_function_mapping``: DataFrame (metric_key, discipline,
    function_label, sort_order).
    ``metric_config``: dict keyed by metric_key (display_name, units,
    metric_family, notes...).
    ``meta``: dict (assessmentId, assessmentName, stateCode, stateName,
    sourceCitation, applicability).
    """
    crosswalk = deep_read_staf_crosswalk(crosswalk_path)
    lookup = deep_function_lookup(crosswalk)
    order_of = {f.get("id"): i for i, f in enumerate(crosswalk)}

    meta_full: dict = {
        "assessmentId": "spring-assessment",
        "assessmentName": "SPRING detailed assessment",
        "stateCode": "",
        "stateName": "",
        "sourceCitation": "Developed in SPRING (stream-curves)",
        "applicability": "",
    }
    for k, v in (meta or {}).items():
        if v is None:  # modifyList(): a NULL value removes the entry
            meta_full.pop(k, None)
        else:
            meta_full[k] = v
    meta = meta_full

    if isinstance(curve_rows, pd.DataFrame):
        rows = [
            {c: curve_rows.iloc[i][c] for c in curve_rows.columns}
            for i in range(len(curve_rows))
        ]
    elif isinstance(curve_rows, dict):
        rows = list(curve_rows.values())
    else:
        rows = list(curve_rows or [])

    # metric_key -> list of assignments {discipline, function_label}. A metric may
    # be assigned to several functions (reuse); it is exported under each. Rows are
    # in sort_order, so the first assignment is the primary (canonical) one. Only
    # rows with a function_label are assignments; scaffold rows are ignored.
    map_by_metric: dict[str, list[dict]] = {}
    mp = discipline_function_mapping
    if isinstance(mp, pd.DataFrame) and len(mp) > 0:
        for i in range(len(mp)):
            r = mp.iloc[i]
            mk = r.get("metric_key")
            lbl = r.get("function_label")
            if not _is_na(mk) and str(mk) != "" and not _is_na(lbl) and str(lbl) != "":
                map_by_metric.setdefault(str(mk), []).append(
                    {
                        "discipline": None if _is_na(r.get("discipline")) else str(r.get("discipline")),
                        "function_label": str(lbl),
                    }
                )

    fn_blocks: dict[str, dict] = {}
    unmapped: list[str] = []
    skipped: list[str] = []

    for row in rows:
        if row is None:
            continue
        if isinstance(row, pd.Series):
            row = row.to_dict()
        raw_mk = deep_default(row.get("metric"), deep_default(row.get("metric_key"), None))
        if raw_mk is None or _is_na(raw_mk):
            continue
        mk = str(raw_mk)
        if deep_default(row.get("curve_status"), "complete") != "complete":
            skipped.append(mk)
            continue
        points = deep_points_from_row(row)
        if points is None:
            skipped.append(mk)
            continue

        assigns = map_by_metric.get(mk)
        if not assigns:
            unmapped.append(mk)
            continue

        cfg = deep_default((metric_config or {}).get(mk), {})
        display_name = str(
            deep_default(row.get("display_name"), deep_default(cfg.get("display_name"), mk))
        )
        units = str(deep_default(cfg.get("units"), ""))
        stratum = deep_default(row.get("stratum"), "")
        stratum = "" if _is_na(stratum) else str(stratum)

        # Function-independent metric payload. `discipline` and `assignmentOrigin`
        # are set per assigned function when the entry is pushed into a block below.
        base_entry: dict = {
            "metricId": "spring-" + deep_slug(mk),
            "metricName": display_name,
            "inputType": str(deep_default(cfg.get("metric_family"), "")),
            "sourceCitation": meta.get("sourceCitation"),
            "xLabel": f"{display_name} ({units})" if units != "" else display_name,
            "howToMeasure": str(deep_default(cfg.get("notes"), "")),
            "methodContext": "",
            "curve": {
                "layerName": meta.get("sourceCitation"),
                "stratification": stratum,
                # "form" is additive metadata: DEEP's interp_curve reads only the
                # points and is shape-agnostic, so an older reader ignores this and
                # still scores a two-sided curve correctly. It exists so the bundle
                # states the intended shape rather than leaving it to be inferred.
                "form": curves.curve_form_of(cfg),
                "points": points,
            },
        }

        # Per-metric annotations (2026-08-21, adversarial review): the reference
        # sample behind the curve, its disposition, whether the metric is a
        # response measurement or a landscape stressor surrogate, the caveats a
        # scorer should read beside the number, and the confidence band. Additive
        # fields: DEEP retains what it does not read. They ride inside
        # metricsByFunction, so they are part of the content digest, which is
        # correct because the same curve with a different caveat set is a
        # different published statement.
        annotations = (meta.get("metricAnnotations") or {}).get(mk) or {}
        for key in ("referenceN", "sampleDisposition", "metricRole", "curveCaveats",
                    "confidenceLabel", "confidenceTotal", "referenceRange"):
            if key in annotations and annotations[key] is not None:
                base_entry[key] = annotations[key]

        # multi-stratum metric -> emit curveLayers (DEEP's per-metric stratum chooser)
        # NOTE(parity): in R the data.frame input path wraps a list-column cell in a
        # length-1 list (as.list), so curveLayers only fire on the list-of-rows path;
        # pandas object columns hold the bare list, matching the R list-path behavior.
        all_strata = row.get("all_strata")
        if isinstance(all_strata, (list, tuple)) and len(all_strata) > 1:
            layers: list[dict] = []
            seen_strata: list = []
            for s in all_strata:
                stn = deep_default((s or {}).get("stratum"), "")
                if _is_na(stn):
                    stn = ""
                if stn in seen_strata:
                    continue
                lpts = deep_points_from_tibble((s or {}).get("curve_points"))
                if lpts is not None:
                    seen_strata.append(stn)
                    layers.append({"stratum": stn, "points": lpts})
            if len(layers) > 1:
                active = layers[0]["stratum"]
                for L in layers:
                    if str(deep_default(L["stratum"], "")) == "":
                        active = L["stratum"]
                        break
                base_entry["curveLayers"] = layers
                base_entry["activeStratum"] = active

        # Emit the metric under EVERY function it is assigned to (reuse). The first
        # successfully mapped assignment is "canonical"; the rest are
        # "additional-function". Dedupe within a block so re-adds can't double it.
        matched_any = False
        for a in assigns:
            label = a.get("function_label")
            if label is None or _is_na(label) or str(label) == "":
                continue
            fn = deep_map_function(label, lookup)
            if fn is None:
                unmapped.append(f"{mk} ({label})")
                continue
            fid = fn["id"]
            if fid not in fn_blocks:
                fn_blocks[fid] = {
                    "functionId": fid,
                    "functionName": fn.get("name"),
                    "discipline": fn.get("category"),
                    "metrics": [],
                    "seen": [],
                }
            if base_entry["metricId"] in fn_blocks[fid]["seen"]:
                matched_any = True
                continue
            entry = dict(base_entry)
            entry["discipline"] = fn.get("category")
            entry["assignmentOrigin"] = "additional-function" if matched_any else "canonical"
            fn_blocks[fid]["seen"].append(base_entry["metricId"])
            fn_blocks[fid]["metrics"].append(entry)
            matched_any = True

    if unmapped:
        logger.warning(
            "DEEP export: skipped metrics whose function label is not a canonical "
            "STAF function: %s",
            ", ".join(unmapped),
        )

    if len(fn_blocks) == 0:
        raise ValueError(
            "DEEP export: no complete, mappable curves to export. Finalize at least "
            "one metric and confirm its discipline/function mapping."
        )

    # function order = crosswalk file order
    ordered = sorted(fn_blocks.values(), key=lambda b: order_of.get(b["functionId"], len(crosswalk)))
    metrics_by_function = [
        {
            "functionId": b["functionId"],
            "functionName": b["functionName"],
            "discipline": b["discipline"],
            "metrics": b["metrics"],
        }
        for b in ordered
    ]

    bundle = {
        "schemaVersion": 1,
        "tier": "detailed",
        "assessmentId": meta.get("assessmentId"),
        "assessmentName": meta.get("assessmentName"),
        "stateCode": meta.get("stateCode"),
        "stateName": meta.get("stateName"),
        "sourceCitation": meta.get("sourceCitation"),
        "applicability": meta.get("applicability"),
        "metricsByFunction": metrics_by_function,
    }
    # Coverage of the 20-function skeleton travels WITH the bundle so DEEP knows the
    # real denominator (it otherwise infers one from the blocks present, and reports a
    # 12-function assessment as "12 / 12 functions scored"). Computed always, never
    # raised on -- this path also builds drafts and the "Test in DEEP" handoff. The
    # publish gate in library.publish_version is what refuses an undocumented gap.
    # Excluded from content_digest (which hashes only metricsByFunction + region code),
    # so adding it cannot re-mint an existing version's fingerprint.
    bundle["functionCoverage"] = function_coverage(
        metrics_by_function, crosswalk, meta.get("functionCoverageExceptions"))
    # Scoring contract: the method a DEEP consumer applies to these curves. Mirrors
    # apps/deep/deep/config.py (INDEX_BANDS thresholds, FUNCTION_SCORE_BANDS,
    # FUNCTION_SCORE_MAX, indirect weight). Excluded from content_digest (which hashes
    # only metricsByFunction + region code), so it never perturbs a version fingerprint.
    bundle["scoringContract"] = {
        "method": "STAF detailed reference-curve scoring",
        # v2 adds the two-sided ("optimum") curve form alongside the monotone seed.
        # Scoring itself is unchanged (interp_curve was always shape-agnostic); the
        # bump records that a bundle may now contain curves that fall in both tails.
        "methodVersion": "iqr-seed-v2",
        **SCORING_CONTRACT_CONSTANTS,
        "rounding": {"index": 2, "functionScore": 0},
        "settings": {},
    }
    # Region of applicability + library-version block travel as top-level fields.
    # DEEP retains unknown bundle fields (LoadedAssessment.raw), so these surface in
    # its assessment info panel without any DEEP schema change. `region` is present
    # for drafts too; `library` is stamped by the library publisher (streamcurves/
    # library.py) once a version is assigned.
    region = meta.get("region")
    if region:
        bundle["region"] = region
    library = meta.get("library")
    if library:
        bundle["library"] = library
    # REF ladder provenance: the tier the reference pool was drawn at, stamped
    # on the bundle and on every metric entry (REF-02 requires the stamp so a
    # best-available curve can never read as reference condition). Per-metric
    # stamps ride inside metricsByFunction, so they are part of the analytical
    # content the digest fingerprints, which is correct: the same curve from a
    # different reference tier is a different assessment.
    tier = meta.get("referenceTier")
    if tier:
        bundle["referenceTier"] = tier
        for block in bundle.get("metricsByFunction") or []:
            for m in block.get("metrics") or []:
                m["referenceTier"] = tier
    # Predictor-source provenance (train/serve pairing): which source computed
    # the predictors these curves were fitted against. Derived from the build,
    # never user-chosen; absent means the StreamCat default (DEEP treats a
    # missing field as "streamcat"). Follows the referenceTier pattern: the
    # bundle-level declaration plus per-metric stamps inside metricsByFunction,
    # so the field is part of the content digest — the same curve from a
    # different predictor source is a different assessment.
    predictor_source = meta.get("predictorSource")
    if predictor_source and predictor_source != "streamcat":
        bundle["predictorSource"] = predictor_source
        for block in bundle.get("metricsByFunction") or []:
            for m in block.get("metrics") or []:
                m["predictorSource"] = predictor_source
    return bundle


def _json_default(o: Any):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def write_deep_assessment_bundle(bundle: dict, path: str | Path) -> str:
    """Write a bundle to disk (jsonlite write_json idiom: pretty, unboxed, null)."""
    Path(path).write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return str(path)
