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

from .paths import CONFIG_DIR

logger = logging.getLogger("streamcurves")


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
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
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
                "points": points,
            },
        }

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

    return {
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
