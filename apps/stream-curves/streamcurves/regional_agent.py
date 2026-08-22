"""Headless Regional Analysis Agent for StreamCurves.

Implements the methodology's Prompt 2
(``notes/2026-07-23_StreamCurves_Methodology/regional_analysis_prompt.md``) by driving
the existing pure ``streamcurves`` analysis functions, parameterized by one EPA Level III
ecoregion, with no Shiny and no reactive state. It goes from an ecoregion code to a
published preliminary reference-curve assessment plus the methodology's standard output
tables.

Design: the analysis math already lives in pure functions (``curves``, ``easi_screening``,
``nrsa``, ``deep_export``, ``library``, ``run_state`` ...). This module is the orchestration
layer that the reactive ``views/`` package normally provides, plus the methodology's rule
application (the reference-tier ladder REF-01/02/03, the Spearman-primary redundancy flag
RED-01, the six-status review, and the SELECT-01 portfolio rule).

Since the Wave 3 implementation the formerly missing machinery runs here too:
leave-one-site-out stability (CURVE-02), drop-one influence (CURVE-04), bootstrap
intervals (CURVE-06), the stratifier CV and information-criterion evidence
(STRAT-01..06), and the numeric 0-100 confidence with its caps (CONF-01/02).
Every resample is seeded from the run's identity, so the determinism contract
covers the diagnostics as well.
"""
from __future__ import annotations

import copy
import logging
import math
import re
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd
import yaml

from . import curves, deep_export, library, metric_map, nrsa, run_state, session_io, staf_library
from . import confidence as conf
from . import curve_stability
from . import easi_screening, overlap, sites, workbook
# `screening` is already a local name inside run() (the EASI screen); alias the
# stratification-screening module so the two never look like the same thing.
from . import screening as strat_screening
from . import consistency, decision, effects, feasibility, methodology, stability, stratifiers
from .datasources.streamcat import streamcat_metrics

logger = logging.getLogger("streamcurves")

_PKG_ROOT = Path(__file__).resolve().parent
_APP_ROOT = _PKG_ROOT.parent
_DATA_DIR = _APP_ROOT / "data"
_CONFIG_DIR = _APP_ROOT / "config"
DIRECTIONS_PATH = _CONFIG_DIR / "nrsa_response_directions.yaml"
LANDSCAPE_DIRECTIONS_PATH = _CONFIG_DIR / "landscape_response_directions.yaml"
# The shared assessment library (apps/library). Publishing here is gated; a staging root is not.
CANONICAL_LIBRARY = (_APP_ROOT.parent / "library").resolve()

# Sample-size rules (methodology DATA family). Resolved from
# config/methodology/methodology_config.yaml rather than retyped here, so
# changing a threshold is a config edit that the run record fingerprints.
# threshold_status: provisional (v0.6). The v0.3 move from the 50/30 floors, which were
# unreachable at the L3 scale with NRSA 2018-19, was mechanism-verified on the two
# pilots' real reference sizes (NH-58=33, ECBP-55=16); the values are not calibrated.
MIN_N_AUTO = methodology.threshold("data_rules.min_n_unstratified")            # DATA-04
MIN_N_EXPLORATORY = methodology.threshold("data_rules.exploratory_n_unstratified")  # DATA-05
MIN_N_FLOOR = methodology.threshold("data_rules.insufficient_n_unstratified")  # DATA-06
# REF-02 tie-break (2026-08-21, review STAT-8, owner decision): the fallback
# auto-fires only when the least-disturbed pool cannot support curves AT ALL,
# i.e. below the DATA-05 exploratory floor. A pool of 10 to 19 stays at the
# stricter tier as exploratory (DATA-05 exists for exactly that band); the owner
# can still choose fallback for such a pool, but the machinery never does it for
# them. Before this the trigger fired anywhere under the DATA-04 auto floor,
# which contradicted DATA-05's whole reason to exist.
REF_FALLBACK_FLOOR = MIN_N_EXPLORATORY


def sample_size_disposition(n: Any) -> str:
    """Map a curve's reference n to its DATA-04/05/06 band (calibrated v0.3)."""
    if n is None:
        return "unknown"
    n = int(n)
    if n >= MIN_N_AUTO:
        return "adequate"          # DATA-04
    if n >= MIN_N_EXPLORATORY:
        return "exploratory"       # DATA-05: 10 <= n < 20
    if n >= 5:
        return "insufficient"      # DATA-06: 5 <= n < 10
    return "too_few"               # engine's insufficient_data (n < 5)


def _metric_seed(base_seed: int, metric: str) -> int:
    """A per-metric resampling seed that is stable across runs (hash() is salted
    per process, so it must never be used here)."""
    return (int(base_seed) ^ zlib.crc32(str(metric).encode("utf-8"))) & 0x7FFFFFFF


def run_seed(l3_code: str, retained_ids, methodology_version: str | None) -> int:
    """Deterministic base seed for a region's resampling diagnostics: identical
    runs resample identically, so the determinism contract extends to every
    bootstrap interval."""
    payload = f"{l3_code}|{','.join(sorted(str(s) for s in retained_ids))}|{methodology_version}"
    return zlib.crc32(payload.encode("utf-8")) & 0x7FFFFFFF


def _metric_values(data: pd.DataFrame, entry: dict, metric: str) -> Optional[pd.Series]:
    col = (entry or {}).get("column_name") or metric
    if col not in data.columns:
        return None
    if "site_id" in data.columns:
        return pd.Series(data[col].to_numpy(), index=data["site_id"].astype(str))
    return data[col]


def curve_diagnostics_for(data: pd.DataFrame, metric_config: dict, *,
                          seed: int, n_boot: int = 200) -> dict:
    """CURVE-02/04/06 diagnostics per metric through the real engine (see
    curve_stability). The DATA-09 guard runs first: these are fold-using
    procedures and must refuse repeated site observations."""
    curve_stability.assert_one_row_per_site(data)
    out: dict[str, dict] = {}
    for mk, entry in metric_config.items():
        if (entry or {}).get("metric_family") == "categorical":
            continue
        values = _metric_values(data, entry, mk)
        if values is None:
            continue
        mseed = _metric_seed(seed, mk)
        out[mk] = {
            "loo": curve_stability.loo_curve_stability(values, entry),
            "influence": curve_stability.influence_check(values, entry),
            "bootstrap": curve_stability.bootstrap_curve(
                values, entry, n_boot=n_boot, seed=mseed),
        }
    return out


def tier_evaluation_table(data: pd.DataFrame, metric_config: dict, tier: dict,
                          screen_preset: str) -> list[dict]:
    """REF-02's trigger evaluated per metric, as the rule specifies.

    For every metric: the least-disturbed pool's usable sample, the applied
    pool's usable sample, and whether the per-metric trigger (functional n
    below the DATA-04 floor) fires. The pool switch itself stays an
    assessment-level, human-authorized decision; this table is the recorded
    evaluation the reviewer decides from.
    """
    primary = tier.get("primary") or {}
    # An EMPTY functional pool is a real screen result (the ECBP case: zero
    # Functioning sites), not missing information, so the counts must report 0
    # rather than borrowing the applied pool's numbers.
    has_primary = "retained_ids" in primary
    functional_ids = {str(s) for s in (primary.get("retained_ids") or [])}
    rows: list[dict] = []
    for mk, entry in metric_config.items():
        col = (entry or {}).get("column_name") or mk
        if col not in data.columns:
            continue
        n_applied = int(data[col].notna().sum())
        if has_primary and "site_id" in data.columns:
            mask = data["site_id"].astype(str).isin(functional_ids)
            n_functional = int(data.loc[mask, col].notna().sum())
        elif screen_preset == "functional":
            n_functional = n_applied
        else:
            n_functional = None
        trigger = (n_functional is not None and n_functional < MIN_N_EXPLORATORY)
        exploratory_band = (n_functional is not None and not trigger
                            and n_functional < MIN_N_AUTO)
        if trigger:
            note = ("Least-disturbed pool below the DATA-05 exploratory floor "
                    "for this metric; fallback justified (REF-02 tie-break).")
        elif exploratory_band:
            note = ("Least-disturbed pool exploratory for this metric (DATA-05 "
                    "band); stays at tier unless the owner opts into fallback.")
        else:
            note = "Least-disturbed pool adequate for this metric."
        rows.append({
            "metric": mk,
            "n_functional_pool": n_functional,
            "n_applied_pool": n_applied,
            "tier_applied": tier.get("reference_tier"),
            "ref02_metric_trigger": bool(trigger),
            "note": note,
        })
    return rows


def stratifier_evidence(data: pd.DataFrame, metric_config: dict, strat: dict, *,
                        seed: int, n_boot: int = 100) -> pd.DataFrame:
    """STRAT-01/02/03/04/05/06 evidence per metric x eligible stratifier.

    Grouped LOO improvement and AICc support are computed for every pair; the
    bootstrap recurrence (STRAT-06) only where the improvement reaches the
    STRAT-01 floor, since recurrence of a non-improvement answers nothing.
    """
    floor = float(methodology.threshold("stratifier_rules.min_cv_error_improvement"))
    strong = float(methodology.threshold("stratifier_rules.strong_cv_error_improvement"))
    min_r2 = float(methodology.threshold("stratifier_rules.min_delta_cv_r2"))
    sc = strat.get("strat_config") or {}
    rows: list[dict] = []
    for key in (strat.get("eligible") or []):
        col = (sc.get(key) or {}).get("column_name")
        if not col or col not in data.columns:
            continue
        for mk, entry in metric_config.items():
            vcol = (entry or {}).get("column_name") or mk
            if vcol not in data.columns or (entry or {}).get("metric_family") == "categorical":
                continue
            frame = data[[vcol, col]]
            imp = curve_stability.stratified_loo_improvement(frame, vcol, col)
            ic = curve_stability.stratifier_ic_support(frame, vcol, col)
            rec = None
            if imp.get("evaluable") and (imp.get("rmse_improvement_frac") or 0.0) >= floor:
                rec = curve_stability.bootstrap_improvement_recurrence(
                    frame, vcol, col, n_boot=n_boot,
                    seed=_metric_seed(seed, f"{mk}|{key}"))
            rows.append({
                "metric": mk,
                "stratification": key,
                "n": imp.get("n"),
                "cv_rmse_improvement": imp.get("rmse_improvement_frac"),
                "cv_mae_improvement": imp.get("mae_improvement_frac"),
                "delta_cv_r2": imp.get("delta_cv_r2"),
                "strat01_supports": (imp.get("evaluable") or False)
                and (imp.get("rmse_improvement_frac") or 0.0) >= floor,
                "strat02_strong": (imp.get("evaluable") or False)
                and (imp.get("rmse_improvement_frac") or 0.0) >= strong,
                "strat03_supports": (imp.get("evaluable") or False)
                and (imp.get("delta_cv_r2") or 0.0) >= min_r2,
                "delta_aicc": ic.get("delta_aicc"),
                "strat04_supports": ic.get("supports_min"),
                "strat05_strong": ic.get("supports_strong"),
                "strat06_recurrence": (rec or {}).get("recurrence_above_floor"),
                "evaluable": bool(imp.get("evaluable")),
            })
    return pd.DataFrame(rows)


def metric_missingness(data: pd.DataFrame, metric_cols: list) -> dict:
    """Per-metric missing-data fraction over the retained reference pool, with its
    DATA-01/02/03 disposition (auto / caution / review) from the methodology config.

    A metric column absent from the frame counts as fully missing: the data never
    arrived, which is exactly the case DATA-03 exists to keep out of automation.
    """
    out: dict[str, dict] = {}
    n_rows = len(data)
    for mk in metric_cols:
        if mk in data.columns and n_rows:
            frac = float(data[mk].isna().mean())
        else:
            frac = 1.0
        out[mk] = {
            "missing_fraction": round(frac, 4),
            "disposition": methodology.missingness_disposition(frac),
        }
    return out

# The methodology's reference-tier ladder mapped onto easi_screening presets.
TIER_LEAST_DISTURBED = "least_disturbed"      # functional preset, ECI > 0.69
TIER_BEST_AVAILABLE = "best_available"        # at_risk_or_better preset, ECI > 0.39


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def load_directions(path: Path | str = DIRECTIONS_PATH) -> dict:
    """The curated NRSA response-metric direction map (see the YAML header)."""
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    return doc.get("metrics") or {}


def load_landscape_directions(path: Path | str = LANDSCAPE_DIRECTIONS_PATH) -> dict:
    """The curated StreamCat/StreamStats/MMW direction + role map.

    Same shape as the NRSA map, plus an optional ``role: predictor`` override marking a
    variable as scaling/climate context that must never be scored (see the YAML header).
    """
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    return doc.get("metrics") or {}


def _nrsa_catalog() -> pd.DataFrame:
    return pd.read_csv(_DATA_DIR / "nrsa_metric_catalog.csv")


@lru_cache(maxsize=1)
def _streamcat_catalog() -> pd.DataFrame:
    return pd.read_csv(_DATA_DIR / "streamcat_metrics.csv")


def _streamcat_label(code: str) -> tuple[str, str]:
    """(display_name, units) for a StreamCat base code, split off its catalog label."""
    cat = _streamcat_catalog()
    hit = cat[cat["name"].astype(str) == str(code)]
    if not len(hit):
        return code, ""
    label = str(hit["label"].iloc[0])
    m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", label.strip())
    return (m.group(1).strip(), m.group(2).strip()) if m else (label.strip(), "")


def select_landscape_codes(directions: dict) -> tuple[list[str], list[str]]:
    """Split the default-selected StreamCat codes into (scored metrics, predictors).

    A code is scored only when it is default-selected in metric_map.yaml AND carries a
    curated ``higher_is_better``. ``role: predictor`` in the landscape map, or a code
    with no curated direction at all, lands in the predictor list instead of being
    silently dropped -- the omission that produced the 12-of-20 coverage defect.
    """
    entries = metric_map.metric_map_entries()
    sel = entries[(entries["source"] == "streamcat")
                  & (entries["role"].isin(["metric", "both", "predictor"]))
                  & (entries["default_selected"] == True)]  # noqa: E712
    metrics: list[str] = []
    predictors: list[str] = []
    for code in sorted(dict.fromkeys(sel["code"].tolist())):
        d = directions.get(code) or {}
        if str(d.get("role") or "").strip().lower() == "predictor":
            predictors.append(code)
        elif d.get("higher_is_better") is None:
            predictors.append(code)
        else:
            metrics.append(code)
    return metrics, predictors


def select_candidates(l3_code: str, sites_path: Path | str | None = None) -> pd.DataFrame:
    """Candidate NRSA sites for an EPA L3 ecoregion (reimplements the view-only
    ``nrsa_in_region`` filter). Returns a frame with at least site_id, lat, lon."""
    path = Path(sites_path) if sites_path else _DATA_DIR / "nrsa_sites.csv"
    df = pd.read_csv(path, dtype={"us_l3code": str})
    sel = df[df["us_l3code"].astype(str) == str(l3_code)].copy()
    sel["site_id"] = sel["site_id"].astype(str)
    return sel.reset_index(drop=True)


def build_metric_config(columns: list[str], directions: dict) -> tuple[dict, list[dict]]:
    """Construct ``metric_config`` for the NRSA response metrics that (a) are present
    in the data, (b) are default-selected response metrics in the metric_map crosswalk,
    and (c) have a curated monotone direction. Metrics whose direction is flagged for
    review are returned separately (never guessed)."""
    entries = metric_map.metric_map_entries()
    default_nrsa = set(
        entries[(entries["source"] == "nrsa")
                & (entries["role"].isin(["metric", "both"]))
                & (entries["default_selected"] == True)]["code"].tolist()  # noqa: E712
    )
    cat = _nrsa_catalog().set_index("name")  # noqa: F841 (kept below)
    metric_config: dict = {}
    flagged_direction: list[dict] = []
    for code in sorted(default_nrsa):
        if code not in columns:
            continue
        d = directions.get(code)
        label = str(cat.loc[code, "label"]) if code in cat.index else code
        units = str(cat.loc[code, "units"]) if code in cat.index else ""
        if units in ("nan", "None"):
            units = ""
        if d is None:
            flagged_direction.append({"metric": code, "display_name": label,
                                      "reason": "no curated direction available"})
            continue
        if d.get("excluded_from_scoring"):
            # A resolved, human-decided exclusion: recorded, never re-asked.
            flagged_direction.append({
                "metric": code, "display_name": label,
                "reason": d.get("exclusion_reason") or "excluded from scoring",
                "documented": True,
                "decided_by": d.get("decided_by"),
                "note": d.get("note"),
            })
            continue
        hib = d.get("higher_is_better")
        form = str(d.get("curve_form") or curves.CURVE_FORM_MONOTONE).strip().lower()
        # A two-sided metric legitimately has no "better" direction, so hib is null by
        # design; only a metric with neither a direction NOR a declared form is unbuildable.
        if hib is None and form != curves.CURVE_FORM_OPTIMUM:
            flagged_direction.append({"metric": code, "display_name": label,
                                      "reason": d.get("review") or "direction under review",
                                      "note": d.get("note")})
            continue
        metric_config[code] = {
            "column_name": code,
            "display_name": label,
            "units": units,
            "higher_is_better": None if hib is None else bool(hib),
            "curve_form": form,
            "metric_family": d.get("metric_family", "continuous"),
            "include_in_summary": True,
            "direction_source": d.get("source"),
            "direction_confidence": d.get("confidence"),
            # CURVE-05 inputs (Unresolved Inputs 3 and 4, now stored per metric):
            # an explicit registry expected_shape wins, else it derives from the
            # curated declaration. Transformation is a curated record, default none.
            "expected_shape": run_state.expected_shape_from_entry(d),
            "transformation": d.get("transformation") or "none",
            # Seed geometry declarations (2026-08-21): the physical domain the
            # seed clamps into, the declared signed scale, and the optimum
            # low-tail treatment all travel with the metric so the engine and
            # every diagnostic rebuild see identical inputs.
            "domain_min": d.get("domain_min"),
            "domain_max": d.get("domain_max"),
            "signed_scale": d.get("signed_scale"),
            "low_tail": d.get("low_tail"),
            # A site-scale measurement of the response (chemistry, habitat,
            # biology), as opposed to a landscape stressor surrogate.
            "metric_role": "response",
            "caveat": d.get("caveat"),
            "notes": d.get("note", ""),
        }
    return metric_config, flagged_direction


def build_landscape_metric_config(
    columns: list[str], directions: dict
) -> tuple[dict, list[dict]]:
    """``metric_config`` entries for the curated StreamCat response metrics present in
    the compiled data.

    Data columns carry the area-of-interest suffix (``pctimp2019`` -> ``pctimp2019ws``);
    the config is keyed by the real column name so every downstream consumer
    (curve build, mapping, bundle export) works on the column it can actually find.
    """
    scored, _ = select_landscape_codes(directions)
    by_base = _columns_by_base_code(columns)
    metric_config: dict = {}
    missing: list[dict] = []
    for code in scored:
        column = by_base.get(code)
        d = directions.get(code) or {}
        label, units = _streamcat_label(code)
        if column is None:
            missing.append({"metric": code, "display_name": label,
                            "reason": "StreamCat column absent from the compiled data"})
            continue
        metric_config[column] = {
            "column_name": column,
            "display_name": label,
            "units": units,
            "higher_is_better": bool(d.get("higher_is_better")),
            "metric_family": d.get("metric_family", "continuous"),
            "include_in_summary": True,
            "direction_source": d.get("source"),
            "direction_confidence": d.get("confidence"),
            "expected_shape": run_state.expected_shape_from_entry(d),
            "transformation": d.get("transformation") or "none",
            "domain_min": d.get("domain_min"),
            "domain_max": d.get("domain_max"),
            "signed_scale": d.get("signed_scale"),
            "low_tail": d.get("low_tail"),
            # Landscape metrics score a watershed's stressor footprint against
            # the reference pool's footprint, and the reference pool was itself
            # selected by a screening index that reads the same variables, so
            # they are stressor surrogates, not measured function (2026-08-21,
            # review ECO-10 and STAT-1).
            "metric_role": "stressor_surrogate",
            "caveat": d.get("caveat"),
            "notes": d.get("note", ""),
        }
    return metric_config, missing


def build_predictor_config(columns: list[str], directions: dict) -> dict:
    """``predictor_config`` for the scaling/context variables in the compiled data.

    Shape mirrors ``workbook.build_predictor_config_from_workbook`` so the wizard, the
    workbook round-trip, and this agent all produce the same structure. Without this the
    agent published sessions with no predictors at all, which left the metric-predictor
    overlap check with nothing to check against.
    """
    _, predictor_codes = select_landscape_codes(directions)
    by_base = _columns_by_base_code(columns)
    out: dict = {}
    for code in predictor_codes:
        column = by_base.get(code)
        if column is None:
            continue
        d = directions.get(code) or {}
        label, units = _streamcat_label(code)
        out[column] = {
            "display_name": label,
            "column_name": column,
            "type": "continuous",
            "derived": False,
            "derivation_method": "",
            "source_columns": "",
            "constant": None,
            "expected_range": "",
            "missing_data_rule": "omit",
            "notes": d.get("note", "") or f"{label} ({units})" if units else d.get("note", ""),
        }
    return out


def _columns_by_base_code(columns) -> dict[str, str]:
    """Map a StreamCat base code to the real data column that carries it.

    StreamCat returns an area-of-interest suffix (ws / cat / wsrp100 / catrp100); this
    resolves ``pctimp2019`` -> ``pctimp2019ws`` without guessing which suffix was used.
    """
    out: dict[str, str] = {}
    for col in columns:
        c = str(col)
        out.setdefault(c.lower(), c)
        base = re.sub(r"(wsrp100|catrp100|ws|cat)$", "", c.lower(), count=1)
        if base and base != c.lower():
            out.setdefault(base, c)
    return out


def attach_comids(data: pd.DataFrame, screening_tables: dict | None = None) -> pd.DataFrame:
    """Add the NHDPlus ``comid`` StreamCat is keyed on.

    Preferred source is the EASI screen, which already snapped every retained site to a
    reach. Falls back to the bundled NRSA evidence file (``comid_by_site_id``) for sites
    the screen did not resolve, so an offline run still enriches.
    """
    if "comid" in data.columns:
        return data
    out = data.copy()
    by_site: dict[str, Any] = {}
    rows = (screening_tables or {}).get("easi_screening_sites") or []
    frame = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    if len(frame) and {"site_id", "comid"} <= set(frame.columns):
        for sid, cid in zip(frame["site_id"], frame["comid"]):
            if pd.notna(cid):
                by_site[str(sid)] = cid
    try:
        from ._vendor.easi.datasources.nrsa import comid_by_site_id

        for sid, cid in (comid_by_site_id() or {}).items():
            by_site.setdefault(str(sid), cid)
    except Exception:  # noqa: BLE001 - the evidence file is a convenience, not a requirement
        pass
    out["comid"] = [by_site.get(str(s)) for s in out["site_id"]]
    return out


def enrich_streamcat(data: pd.DataFrame, directions: dict, *, enabled: bool = True,
                     on_event: Optional[Callable] = None,
                     fetch: Optional[Callable] = None) -> tuple[pd.DataFrame, dict]:
    """Join StreamCat watershed metrics onto the retained sites by COMID.

    Returns ``(data, report)``. ``report`` records the outcome honestly -- ``ok`` with a
    column count, ``skipped``, or ``failed`` with the reason -- so the run report can
    never present a missing landscape source as an ordinary NA column. ``fetch`` is
    injectable for tests; it defaults to the live StreamCat client.
    """
    codes = sorted(set(select_landscape_codes(directions)[0])
                   | set(select_landscape_codes(directions)[1]))
    report = {"source": "streamcat", "requested": codes, "status": "skipped",
              "n_columns": 0, "reason": None}
    if not enabled:
        report["reason"] = "disabled by caller"
        return data, report
    if not codes:
        report["reason"] = "no landscape codes selected"
        return data, report
    if "comid" not in data.columns:
        report["status"] = "failed"
        report["reason"] = "no comid column on the retained sites"
        logger.warning("StreamCat enrichment skipped: %s", report["reason"])
        return data, report
    if callable(on_event):
        on_event({"event": "streamcat_start", "n_codes": len(codes)})
    try:
        wide = (fetch or streamcat_metrics)(
            data["comid"].tolist(), codes, area="watershed")
    except Exception as exc:  # noqa: BLE001 - a source failure must not kill the run
        report["status"] = "failed"
        report["reason"] = str(exc)
        logger.warning("StreamCat fetch failed: %s", exc)
        return data, report
    if wide is None or not len(wide) or wide.shape[1] <= 1:
        report["status"] = "failed"
        report["reason"] = "StreamCat returned no rows (service unreachable?)"
        logger.warning("StreamCat fetch returned nothing for %d comids", len(data))
        return data, report
    out = sites.attach_by_comid(data, "comid", wide)
    added = [c for c in out.columns if c not in data.columns]
    report.update(status="ok", n_columns=len(added), columns=added)
    if callable(on_event):
        on_event({"event": "streamcat_done", "n_columns": len(added)})
    return out, report


# --------------------------------------------------------------------------- #
# Reference screening (REF ladder)
# --------------------------------------------------------------------------- #
def screen_pool(candidate_rows: list[dict], preset: str,
                on_event: Optional[Callable] = None,
                cache_path: Optional[Path] = None) -> dict:
    """Run one real EASI screen at ``preset`` and return honest results.

    Returns {tables, sites (rows), retained_ids, counts, preset, from_cache}. Never
    fabricates a 'representative' record; the method is the real engine. If ``cache_path``
    exists, the prior live-screen batch is reused (reproducible, no re-hitting services).
    """
    import json
    from_cache = False
    if cache_path is not None and Path(cache_path).exists():
        batch = json.loads(Path(cache_path).read_text(encoding="utf-8"))
        from_cache = True
    else:
        batch = easi_screening.screen_sites_direct(candidate_rows, preset, on_event=on_event)
        if cache_path is not None:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            Path(cache_path).write_text(json.dumps(batch, default=str), encoding="utf-8")
    tables = easi_screening.to_screening_tables(batch)
    rows = tables.get("easi_screening_sites", [])
    return {
        "tables": tables,
        "sites": rows,
        "retained_ids": easi_screening.retained_site_ids(tables),
        "counts": easi_screening.summarize_screening_rows(rows),
        "preset": preset,
        "from_cache": from_cache,
    }


def choose_reference_tier(candidate_rows: list[dict], primary_preset: str,
                          on_event: Optional[Callable] = None,
                          cache_dir: Optional[Path] = None) -> dict:
    """Apply REF-01/02/03. Screen at the primary tier (functional by default). If the
    least-disturbed pool is too small to fit curves, fall back to best-available
    (at_risk_or_better), flag it, and stamp the tier. Never silently relaxes; never
    drops below the best-available floor."""
    if primary_preset not in ("functional", "at_risk_or_better"):
        raise ValueError(f"unsupported reference preset {primary_preset!r}; "
                         "reference curves never draw below at_risk_or_better (REF-03)")

    def _cache(preset):
        return (Path(cache_dir) / f"screening_cache_{preset}.json") if cache_dir else None

    primary = screen_pool(candidate_rows, primary_preset, on_event=on_event,
                          cache_path=_cache(primary_preset))
    n_primary = len(primary["retained_ids"])
    result = {
        "reference_tier": TIER_LEAST_DISTURBED if primary_preset == "functional"
        else TIER_BEST_AVAILABLE,
        "primary": primary,
        "fallback": None,
        "ref02_triggered": False,
        "review_flags": [],
        "screening": primary,
    }

    if primary_preset == "functional" and n_primary < REF_FALLBACK_FLOOR:
        # REF-02: the Functioning-only pool cannot support curves even as
        # exploratory. Fall back explicitly. (A pool of 10 to 19 does NOT land
        # here: it stays least-disturbed at DATA-05 exploratory status.)
        fallback = screen_pool(candidate_rows, "at_risk_or_better", on_event=on_event,
                               cache_path=_cache("at_risk_or_better"))
        result.update({
            "reference_tier": TIER_BEST_AVAILABLE,
            "fallback": fallback,
            "ref02_triggered": True,
            "screening": fallback,
            "review_flags": [
                f"REF-02 fallback: only {n_primary} Functioning sites, below the "
                f"exploratory floor of {REF_FALLBACK_FLOOR}; "
                f"included Functioning-at-Risk to reach {len(fallback['retained_ids'])}. "
                "Mandatory review; confidence capped; reference tier = best_available."
            ],
        })
    elif primary_preset == "functional" and n_primary < MIN_N_AUTO:
        result["review_flags"] = [
            f"Least-disturbed pool is exploratory ({n_primary} sites, DATA-05 band "
            f"{REF_FALLBACK_FLOOR} to {MIN_N_AUTO - 1}). Stays at tier; the owner may "
            "choose the REF-02 fallback for this pool, the machinery never does."
        ]
    return result


# --------------------------------------------------------------------------- #
# Curves + review
# --------------------------------------------------------------------------- #
def _row_to_dict(res: dict, metric_key: str) -> dict:
    from . import curves as _c  # local import keeps module import light
    cr = res["curve_row"]
    row = {c: cr.iloc[0][c] for c in cr.columns}
    row["metric"] = metric_key
    row["curve_points"] = res.get("curve_points")
    return row


def build_curves(data: pd.DataFrame, metric_config: dict) -> dict:
    """Build one IQR-seed reference curve per eligible metric. Returns {metric: row dict}
    where each row carries the full ``curve_points`` table (so the bundle keeps all points)."""
    from . import curves as _c
    out: dict[str, dict] = {}
    for mk in metric_config:
        res = _c.build_reference_curve(data, mk, metric_config, build_plots=False)
        out[mk] = _row_to_dict(res, mk)
    return out


def review_curves(curve_rows_by_metric: dict, column_functions: dict,
                  missingness: Optional[dict] = None,
                  metric_config: Optional[dict] = None) -> dict:
    """Classify every curve proposal into the status review map (REF/CURVE gates).

    This mirrors ``curve_automation.reconcile_review_map`` but calls the pure
    ``run_state`` primitives directly, so the agent never imports the reactive
    ``curve_automation`` (which pulls Shiny + views). mapping_ok is whether the metric
    has a STAF function. ``missingness`` (from :func:`metric_missingness`) routes a
    metric whose missing-data fraction exceeds the DATA-03 review threshold to the
    flagged queue instead of letting it auto-finalize, and ``metric_config`` lets the
    CURVE-05 shape check compare each built curve against its approved expectation."""
    review: dict = {}
    missingness = missingness or {}
    metric_config = metric_config or {}
    for mk, row in curve_rows_by_metric.items():
        mapping = column_functions.get(mk) or ""
        miss = missingness.get(mk) or {}
        data_ok = miss.get("disposition") != "review"
        data_reason = None
        if not data_ok:
            frac = miss.get("missing_fraction")
            data_reason = (
                f"Missing-data fraction {frac:.0%} exceeds the DATA-03 review "
                "threshold, so the curve must not be auto-recommended."
                if isinstance(frac, (int, float)) else None)
        shape_ok, shape_reason = run_state.shape_conflict_check(
            [row], metric_config.get(mk))
        status, reasons = run_state.classify_curve_proposal(
            [row], mapping_ok=bool(mapping), strat_ok=True,
            data_ok=data_ok, data_reason=data_reason,
            shape_ok=shape_ok, shape_reason=shape_reason)
        fingerprint = run_state.proposal_fingerprint(
            [row], mapping=mapping or None, strat=None)
        summary = {
            "metric": mk,
            "function": mapping or None,
            "strat": None,
            "n_strata": 1,
            "curve_statuses": [row.get("curve_status")],
            "min_n_reference": row.get("n_reference"),
        }
        review[mk] = run_state.reconcile_curve_review_entry(
            review.get(mk), status=status, reasons=reasons,
            fingerprint=fingerprint, proposal_summary=summary)
    return review


# --------------------------------------------------------------------------- #
# Redundancy (RED-01 Spearman-primary)
# --------------------------------------------------------------------------- #
def redundancy_matrix(data: pd.DataFrame, metric_config: dict,
                      column_functions: dict) -> pd.DataFrame:
    """Pairwise Spearman + Pearson among the metric columns; RED-01 flags on
    |Spearman| >= 0.80 (the methodology's primary flag).

    Delegates to ``overlap`` so there is exactly one Spearman implementation in
    the app, as that module's docstring has always claimed. The agent's own copy
    lacked overlap's minimum-pair-n and constant-column guards, so a pair with
    three complete observations could be reported as strongly correlated, and the
    app's redundancy view and this table disagreed on the same data.
    """
    metrics = [m for m in metric_config if m in data.columns]
    analysis = overlap.analyze_overlap(
        data,
        metric_columns=metrics,
        partner_columns=metrics,
        partner_role=overlap.PARTNER_METRIC,
        column_functions=column_functions,
    )
    return overlap.redundancy_view(analysis, column_functions)


# --------------------------------------------------------------------------- #
# Portfolio (SELECT-01)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _function_lookup() -> dict:
    return deep_export.deep_function_lookup(deep_export.deep_read_staf_crosswalk())


@lru_cache(maxsize=256)
def _canonical_function_id(label) -> Optional[str]:
    """Function id for either label shape used in this app.

    ``column_functions`` values are ``"Discipline: Function"`` (from
    ``metric_map_function_label``) while the crosswalk is keyed on the bare name,
    so the prefix has to come off or every lookup silently misses.
    """
    if label is None:
        return None
    text = str(label).strip()
    if not text:
        return None
    lookup = _function_lookup()
    for cand in (text, text.split(":", 1)[-1].strip()):
        fn = deep_export.deep_map_function(cand, lookup)
        if fn is not None:
            return str(fn.get("id"))
    return None


def compact_portfolio(intended: list[str], column_functions: dict,
                      metric_config: dict) -> list[dict]:
    """One row per STAF function -- covered or not -- with the in-scope metrics.

    Every one of the 20 is emitted, because a function with no metric was
    previously invisible here: a document titled "Compact Metric Portfolio" listed
    only what was covered and so read as complete when it was partial. A reviewer
    checking coverage should not have to diff this against the framework by hand.
    ``select01_flag`` still marks a function carrying more than two metrics
    (SELECT-01 requires recorded human approval).
    """
    by_fid: dict[str, list[str]] = {}
    unmapped: list[str] = []
    for mk in intended:
        # EVERY function the metric informs, not just the first. column_functions
        # holds metric_map_function_label's single first match, while the bundle is
        # built from metric_map_functions_for's full set -- reading the label here
        # reported High flow dynamics as a gap in the same run whose bundle covered
        # it, because impervious cover happens to list under Catchment hydrology
        # first.
        fids = [fid for fid in
                (_canonical_function_id(f.get("function_name"))
                 for f in metric_map.metric_map_functions_for(mk))
                if fid]
        if not fids:
            fid = _canonical_function_id(column_functions.get(mk))
            if fid is None:
                unmapped.append(mk)
                continue
            fids = [fid]
        for fid in dict.fromkeys(fids):
            by_fid.setdefault(fid, []).append(mk)

    out = []
    for f in deep_export.deep_read_staf_crosswalk():
        fid = str(f.get("id"))
        ms = by_fid.get(fid, [])
        out.append({
            "function": f.get("name"),
            "function_id": fid,
            "discipline": f.get("category"),
            "coverage": "covered" if ms else "GAP",
            "n_metrics": len(ms),
            "metrics": ms,
            "primary_metric": ms[0] if ms else None,
            "select01_flag": len(ms) > 2,
        })
    if unmapped:
        out.append({
            "function": "(unmapped)",
            "function_id": None,
            "discipline": "",
            "coverage": "unmapped",
            "n_metrics": len(unmapped),
            "metrics": unmapped,
            "primary_metric": unmapped[0],
            "select01_flag": len(unmapped) > 2,
        })
    return out


def uncovered_functions(portfolio: list[dict]) -> list[dict]:
    """The GAP rows, with the metrics metric_map says would close each one.

    Pairing every gap with its candidates is what turns "8 functions uncovered"
    into an actionable review item: either pull one of these, or record why not.
    """
    gaps = [r for r in (portfolio or []) if r.get("coverage") == "GAP"]
    if not gaps:
        return []
    by_fid: dict[str, set[str]] = {}
    for e in metric_map.metric_map_entries().to_dict("records"):
        fid = _canonical_function_id(f"{e['discipline']}: {e['function_name']}")
        if fid:
            by_fid.setdefault(fid, set()).add(f"{e['code']} ({e['source']})")
    return [{**row, "candidate_metrics": sorted(by_fid.get(row["function_id"], []))}
            for row in gaps]


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
# Rule ids whose per-metric review records are mandatory-review triggers for the
# CONF-02 mandatory_review_open cap (2026-08-21). CONF-02's own "confidence_capped"
# item is a consequence, not a cause, and stays out; pair and function records
# are not per-curve.
MANDATORY_REVIEW_RULES = ("CURVE-07", "CURVE-04", "CURVE-02", "CURVE-06",
                          "DATA-03", "DATA-05", "DATA-06")
ADJUDICATING_ACTIONS = ("accept", "accept_with_conditions", "modify")


def t_in(keys: set, rule_id: str, subject: str) -> bool:
    return (str(rule_id), str(subject)) in keys


def adjudicated_keys(reviewer_decisions) -> set[tuple[str, str]]:
    """(rule_id, subject) pairs carrying a recorded adjudication that closes the
    item (reject and request_additional_analysis leave it open)."""
    out: set[tuple[str, str]] = set()
    for d in reviewer_decisions or []:
        if str(d.get("action") or "").strip() in ADJUDICATING_ACTIONS:
            out.add((str(d.get("rule_id")), str(d.get("subject"))))
    return out


def mandatory_review_triggers(mk: str, *, review_entry: dict, sample_disposition,
                              missingness_disposition, diag: dict,
                              ref02_triggered: bool) -> list[tuple[str, str]]:
    """The (rule_id, subject) review items the run raises on one curve, derived
    from the same evidence the provenance records are built from."""
    triggers: list[tuple[str, str]] = []
    status = (review_entry or {}).get("status")
    if status not in (run_state.CURVE_STATUS_AUTO_OK, None):
        triggers.append(("CURVE-07", mk))
    if str(sample_disposition) == "exploratory":
        triggers.append(("DATA-05", mk))
    elif str(sample_disposition) in ("insufficient", "too_few"):
        triggers.append(("DATA-06", mk))
    if str(missingness_disposition) == "review":
        triggers.append(("DATA-03", mk))
    loo = (diag or {}).get("loo") or {}
    infl = (diag or {}).get("influence") or {}
    boot = (diag or {}).get("bootstrap") or {}
    if diag:
        if not loo.get("evaluable"):
            triggers.append(("CURVE-02", mk))
        if infl.get("flagged"):
            triggers.append(("CURVE-04", mk))
        if not boot.get("evaluable"):
            triggers.append(("CURVE-06", mk))
    if ref02_triggered:
        triggers.append(("REF-02", "reference_screen"))
    return triggers


def deferred_gradient_candidates(strat_evidence) -> dict[str, dict]:
    """Metrics whose best stratifier candidate clears STRAT-01 (cross-validated
    improvement floor) and STRAT-06 (resample recurrence floor) while the run
    builds unstratified curves. In advisory mode every such candidate is
    deferred by construction (the stratum floors or a recorded STRAT-09 decision
    keep the split unapplied), so the gradient is known and unmodeled: CONF-02
    deducts for it and the bundle carries the caveat (2026-08-21, review ECO-5).
    Mechanical by the owner's decision at the Phase 8 gate: no hand-picked list.
    """
    if strat_evidence is None or len(strat_evidence) == 0:
        return {}
    floor_imp = float(methodology.threshold("stratifier_rules.min_cv_error_improvement"))
    floor_rec = float(methodology.threshold("stratifier_rules.min_resample_support"))
    out: dict[str, dict] = {}
    for row in strat_evidence.itertuples(index=False):
        r = row._asdict()
        if not r.get("evaluable"):
            continue
        imp = r.get("cv_rmse_improvement")
        rec = r.get("strat06_recurrence")
        if imp is None or rec is None:
            continue
        try:
            imp_f, rec_f = float(imp), float(rec)
        except (TypeError, ValueError):
            continue
        if not (imp_f >= floor_imp and rec_f >= floor_rec):
            continue
        mk = str(r.get("metric"))
        cand = {"stratification": str(r.get("stratification")),
                "cv_error_improvement": imp_f, "resample_support": rec_f,
                "rule_ids": ["STRAT-01", "STRAT-06"],
                "reason": "stratification not applied (advisory mode; stratum floors "
                          "or a recorded STRAT-09 decision)"}
        if mk not in out or imp_f > out[mk]["cv_error_improvement"]:
            out[mk] = cand
    return out


def metric_annotations(*, intended, curve_rows, metric_config, sample_sizes,
                       confidence_map, deferred_gradients) -> dict[str, dict]:
    """The per-metric annotations the DEEP bundle carries beside each curve
    (2026-08-21): reference n, sample disposition, metric role, caveats, and the
    confidence band. The scorer sees what the registry and the report see."""
    floor_auto = int(methodology.threshold("data_rules.min_n_unstratified"))
    out: dict[str, dict] = {}
    for mk in intended:
        cfg = metric_config.get(mk) or {}
        row = curve_rows.get(mk) or {}
        n = row.get("n_reference")
        disp = (sample_sizes.get(mk) or {}).get("disposition")
        caveats: list[str] = []
        if disp in ("insufficient", "too_few"):
            caveats.append(
                f"Built from {n} reference sites, below the exploratory floor: read the "
                "condition band, not the point value.")
        elif disp == "exploratory":
            caveats.append(
                f"Built from {n} reference sites (exploratory band, below the automated "
                f"floor of {floor_auto}): the point value carries wide sampling error.")
        g = deferred_gradients.get(mk)
        if g:
            caveats.append(
                f"The reference expectation likely varies with {g['stratification']} "
                f"(cross-validated improvement {g['cv_error_improvement']:.0%}, resample "
                f"recurrence {g['resample_support']:.0%}); the curve is unstratified "
                "pending a larger reference pool, so sites at either end of that "
                "gradient may read as departures that are natural.")
        if cfg.get("metric_role") == "stressor_surrogate":
            caveats.append(
                "Landscape stressor surrogate: the score compares the watershed's "
                "footprint with the reference pool's footprint, and the pool was "
                "screened on the same kind of variable, so read it as a footprint "
                "comparison rather than measured function.")
        if cfg.get("caveat"):
            caveats.append(str(cfg["caveat"]))
        conf = confidence_map.get(mk) or {}
        lo, hi = row.get("min_val"), row.get("max_val")
        ref_range = None
        try:
            if lo is not None and hi is not None and math.isfinite(float(lo)) \
                    and math.isfinite(float(hi)):
                ref_range = [float(lo), float(hi)]
        except (TypeError, ValueError):
            ref_range = None
        out[mk] = {
            "referenceN": None if n is None else int(n),
            "sampleDisposition": disp,
            "metricRole": cfg.get("metric_role") or "response",
            "curveCaveats": caveats,
            "confidenceLabel": conf.get("label"),
            "confidenceTotal": conf.get("total"),
            # The reference pool's observed span (STAT-10): scores outside it
            # come from the seed's sub-reference convention, not from data.
            "referenceRange": ref_range,
        }
    return out


def run_evidence(l3_code: str, name: str, *,
                 screen_preset: str = "functional",
                 on_event: Optional[Callable] = None,
                 do_screen: bool = True,
                 use_streamcat: bool = True,
                 cache_dir: Optional[Path] = None,
                 diagnostics_n_boot: int = 200,
                 diagnostics_enabled: bool = True) -> dict:
    """The expensive, decision-free half of a regional run.

    Screening, data assembly, the registries, redundancy, the stratifier
    analysis, the curves with their review classification, sample sizes, the
    seeded diagnostics, RED-06, the STRAT evidence, the per-metric tier table,
    the domain checks, and the deferred gradients. Nothing here reads a reviewer
    decision, so the batch runner can compute it once and assemble the
    decision-dependent tail (:func:`assemble`) as many times as the standing
    decisions need (2026-08-22).
    """
    # A headless run must not proceed under a config that misdescribes the engine.
    methodology.verify_mirrors(strict=True)
    directions = load_directions()
    candidates = select_candidates(l3_code)
    n_candidates = len(candidates)
    if n_candidates == 0:
        raise ValueError(f"no NRSA candidate sites for L3 ecoregion {l3_code}")

    candidate_rows = [{"site_id": str(r.site_id), "lat": float(r.lat), "lon": float(r.lon)}
                      for r in candidates.itertuples()]

    # --- Reference screening + tier ladder (REF) ---
    if do_screen:
        tier = choose_reference_tier(candidate_rows, screen_preset, on_event=on_event,
                                     cache_dir=cache_dir)
        screening = tier["screening"]
        retained_ids = set(screening["retained_ids"])
        counts = screening["counts"]
        method = "direct_engine"
        if not retained_ids:
            raise RuntimeError(
                "Real EASI screening retained zero sites (external services may be "
                f"unreachable: {counts}). Not falling back to a represented screen; "
                "resolve connectivity or import a finalized EASI batch ZIP.")
    else:
        # Explicit no-screen mode is only for offline tests; provenance stays honest.
        tier = {"reference_tier": TIER_LEAST_DISTURBED, "ref02_triggered": False,
                "review_flags": ["Screening skipped (no_screen mode) - not a real screen."],
                "screening": {"tables": {"easi_screening_sites": [], "easi_screening_metrics": [],
                                         "easi_screening_criteria": {}}, "counts": {}}}
        retained_ids = set(str(s) for s in candidates["site_id"])
        counts = {"n_screened": n_candidates, "n_retained": len(retained_ids)}
        method = "unscreened_test"
        screening = tier["screening"]

    retained = candidates[candidates["site_id"].isin(retained_ids)].reset_index(drop=True)

    # --- Metric config (curated directions) + enrichment ---
    # NRSA response metrics come from the offline bundle; the landscape metrics that
    # carry Hydrology / Watershed connectivity / High flow dynamics come from StreamCat
    # by COMID. Skipping StreamCat is what limited every published assessment to 12 of
    # the 20 STAF functions, so a fetch failure is recorded loudly, never silently NA'd.
    all_cols = set(nrsa.load_nrsa_values().columns)
    metric_config, flagged_direction = build_metric_config(sorted(all_cols), directions)
    nrsa_metric_cols = list(metric_config.keys())
    data = nrsa.attach_nrsa_metrics(retained, nrsa_metric_cols, nrsa.load_nrsa_values())

    landscape_directions = load_landscape_directions()
    data = attach_comids(data, screening.get("tables"))
    data, source_report = enrich_streamcat(
        data, landscape_directions, enabled=use_streamcat, on_event=on_event)
    landscape_config, landscape_missing = build_landscape_metric_config(
        list(data.columns), landscape_directions)
    predictor_config = build_predictor_config(list(data.columns), landscape_directions)
    metric_config.update(landscape_config)
    metric_cols = list(metric_config.keys())
    flagged_direction = flagged_direction + landscape_missing

    # --- Classification / mapping ---
    column_functions = {c: metric_map.metric_map_function_label(c) for c in metric_cols}
    mapping_df = staf_library.default_discipline_function_mapping(metric_cols, metric_config)

    # --- Redundancy (RED-01) ---
    redundancy = redundancy_matrix(data, metric_config, column_functions)

    # --- Stratifier candidates + STRAT-00 screening (advisory) ---
    # Upstream of build_curves so the session carries one data frame, and safe
    # there because build_curves reads only metric_config columns: the class
    # columns added here cannot change a curve.
    data = attach_stratifier_sources(data)
    strat = run_stratifier_analysis(
        data, metric_config, predictor_config, on_event=on_event)
    data = strat["data"]

    # --- Missingness dispositions (DATA-01/02/03), over the retained pool ---
    missingness = metric_missingness(data, list(metric_config))

    # --- Curves + the six-status review classification (no decisions yet) ---
    curve_rows = build_curves(data, metric_config)
    curve_review = review_curves(curve_rows, column_functions,
                                 missingness=missingness, metric_config=metric_config)

    # --- Sample-size disposition (DATA-04/05/06, calibrated v0.3). Flag-and-publish:
    #     curves below the auto floor stay in the preliminary bundle but carry an
    #     exploratory/insufficient flag and a confidence cap; certification needs review.
    sample_sizes = {mk: {"n": row.get("n_reference"),
                         "disposition": sample_size_disposition(row.get("n_reference"))}
                    for mk, row in curve_rows.items()}
    sample_size_flags = [{"metric": mk, "n": v["n"], "disposition": v["disposition"]}
                         for mk, v in sample_sizes.items()
                         if v["disposition"] in ("exploratory", "insufficient", "too_few")]
    reference_pool_disposition = sample_size_disposition(len(retained))

    # --- Resampling diagnostics (CURVE-02/04/06), seeded by run identity ---
    base_seed = run_seed(l3_code, retained_ids, methodology.methodology_version())
    diagnostics = (curve_diagnostics_for(
        data, metric_config, seed=base_seed, n_boot=diagnostics_n_boot)
        if diagnostics_enabled else {})

    # --- RED-06: category stability for the flagged redundant pairs ---
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
                        seed=_metric_seed(base_seed, f"{a}|{b}")))

    # --- STRAT-01..06 evidence for the eligible candidates ---
    strat_evidence_df = (stratifier_evidence(
        data, metric_config, strat, seed=base_seed,
        n_boot=min(100, diagnostics_n_boot))
        if diagnostics_enabled else pd.DataFrame())

    # --- REF-02 evaluated per metric (the rule's own trigger granularity) ---
    tier_eval = tier_evaluation_table(data, metric_config, tier, screen_preset)

    # --- Domain check (CURVE-07a, 2026-08-21): no anchor outside the declared
    #     physical domain. Zero after clamping; recorded so a bundle can be audited.
    domain_checks: dict[str, dict] = {}
    for mk, row in curve_rows.items():
        dom = curves.metric_domain_of(metric_config.get(mk))
        domain_checks[mk] = {
            "domain_min": dom[0], "domain_max": dom[1],
            "violations": curves.count_domain_violations(
                row.get("curve_points"), dom[0], dom[1]),
        }

    # --- Deferred gradients (ECO-5): known, unmodeled stratification evidence ---
    deferred_gradients = deferred_gradient_candidates(strat_evidence_df)

    return {
        "l3_code": str(l3_code),
        "name": name,
        "screen_preset": screen_preset,
        "n_candidates": n_candidates,
        "screening_method": method,
        "screening_counts": counts,
        "tier": tier,
        "screening": screening,
        "retained_ids": retained_ids,
        "n_retained": len(retained),
        "metric_config": metric_config,
        "predictor_config": predictor_config,
        "column_functions": column_functions,
        "mapping_df": mapping_df,
        "flagged_direction": flagged_direction,
        "source_reports": [source_report],
        "data": data,
        "curve_rows": curve_rows,
        "curve_review": curve_review,
        "sample_sizes": sample_sizes,
        "sample_size_flags": sample_size_flags,
        "missingness": missingness,
        "reference_pool_disposition": reference_pool_disposition,
        "run_seed": base_seed,
        "diagnostics_n_boot": diagnostics_n_boot,
        "diagnostics": diagnostics,
        "red06_stability": red06_stability,
        "strat_evidence": strat_evidence_df,
        "tier_evaluation": tier_eval,
        "domain_checks": domain_checks,
        "deferred_gradients": deferred_gradients,
        "redundancy": redundancy,
        "stratifiers": strat,
    }


def assemble(evidence: dict, *,
             source_citation: str = "",
             assessment_id: Optional[str] = None,
             assessment_name: Optional[str] = None,
             author: str = "StreamCurves Regional Analysis Agent",
             coverage_exceptions: Optional[list[dict]] = None,
             finalize_metrics: Optional[dict] = None,
             finalize_actor: str = "",
             remove_metrics: Optional[dict] = None,
             reviewer_decisions: Optional[list] = None) -> dict:
    """The decision-dependent tail of a run, from one evidence dict (seconds).

    Reviewer finalizations and removals are stamped on a COPY of the evidence's
    curve review, so the same evidence can be assembled again with a different
    decision set: scope, the mandatory-review triggers against the recorded
    adjudications, confidence with its caps, the portfolio, the review
    priorities, the bundle, and coverage. Returns the result dict :func:`run`
    returns.
    """
    l3_code, name = evidence["l3_code"], evidence["name"]
    tier, screening = evidence["tier"], evidence["screening"]
    method, screen_preset = evidence["screening_method"], evidence["screen_preset"]
    n_candidates, retained_ids = evidence["n_candidates"], evidence["retained_ids"]
    metric_config = evidence["metric_config"]
    column_functions = evidence["column_functions"]
    curve_rows, sample_sizes = evidence["curve_rows"], evidence["sample_sizes"]
    missingness, diagnostics = evidence["missingness"], evidence["diagnostics"]
    redundancy, deferred_gradients = evidence["redundancy"], evidence["deferred_gradients"]

    curve_review = copy.deepcopy(evidence["curve_review"])
    # Recorded reviewer finalizations (``finalize_metrics``: metric -> note).
    # A flagged curve publishes only through exactly this: a named human
    # decision with a rationale, stamped on the review entry. The agent never
    # finalizes a flagged curve on its own.
    for mk, note in (finalize_metrics or {}).items():
        entry = curve_review.get(mk)
        if entry is None:
            raise ValueError(f"--finalize-metric names unknown metric {mk!r}")
        if not finalize_actor:
            raise ValueError("finalize_metrics requires a named finalize_actor")
        curve_review[mk] = run_state.apply_review_decision(
            entry, run_state.DECISION_FINALIZED, note=note, actor=finalize_actor)
    for mk, note in (remove_metrics or {}).items():
        entry = curve_review.get(mk)
        if entry is None:
            raise ValueError(f"--remove-metric names unknown metric {mk!r}")
        if not finalize_actor:
            raise ValueError("remove_metrics requires a named finalize_actor")
        curve_review[mk] = run_state.apply_review_decision(
            entry, run_state.DECISION_REMOVED, note=note, actor=finalize_actor)
    intended = run_state.intended_metrics_for_publish(curve_review)
    flagged = run_state.flagged_metrics(curve_review)

    # --- Mandatory-review triggers per curve and the recorded adjudications ---
    adjudicated = adjudicated_keys(reviewer_decisions)
    # A recorded reviewer finalization or removal closes the curve's own
    # CURVE-07 item by definition (it IS the recorded decision).
    for mk in list((finalize_metrics or {}).keys()) + list((remove_metrics or {}).keys()):
        adjudicated.add(("CURVE-07", mk))
    mandatory_review: dict[str, dict] = {}
    for mk in metric_config:
        triggers = mandatory_review_triggers(
            mk, review_entry=curve_review.get(mk) or {},
            sample_disposition=sample_sizes.get(mk, {}).get("disposition"),
            missingness_disposition=missingness.get(mk, {}).get("disposition"),
            diag=diagnostics.get(mk) or {},
            ref02_triggered=bool(tier.get("ref02_triggered")))
        open_items = [t for t in triggers if t not in adjudicated]
        mandatory_review[mk] = {
            "triggers": [f"{r}:{sub}" for r, sub in triggers],
            "adjudicated": [f"{r}:{sub}" for r, sub in triggers if t_in(adjudicated, r, sub)],
            "open": [f"{r}:{sub}" for r, sub in open_items],
        }

    # --- CONF-01/02 numeric confidence and the SELECT-02 metric score ---
    flagged_pair_counts: dict[str, int] = {}
    if redundancy is not None and len(redundancy) and "metric_a" in redundancy.columns:
        for row in redundancy.itertuples(index=False):
            r = row._asdict()
            if r.get("red01_spearman_flag"):
                for side in ("metric_a", "metric_b"):
                    key = str(r.get(side))
                    flagged_pair_counts[key] = flagged_pair_counts.get(key, 0) + 1
    confidence_map: dict[str, dict] = {}
    metric_scores: dict[str, dict] = {}
    for mk in metric_config:
        entry = metric_config[mk]
        review_entry = curve_review.get(mk) or {}
        ev = {
            "sample_disposition": sample_sizes.get(mk, {}).get("disposition"),
            "missingness_disposition": missingness.get(mk, {}).get("disposition"),
            "curve_status": review_entry.get("status"),
            "loo": (diagnostics.get(mk) or {}).get("loo"),
            "bootstrap": (diagnostics.get(mk) or {}).get("bootstrap"),
            "influence": (diagnostics.get(mk) or {}).get("influence"),
            "direction_confidence": entry.get("direction_confidence"),
            "shape_ok": review_entry.get("status") != run_state.CURVE_STATUS_SHAPE_CONFLICT,
            "mapped": bool(column_functions.get(mk)),
            "units_present": bool(entry.get("units")),
            "reference_tier": tier["reference_tier"],
            "redundant_pairs": flagged_pair_counts.get(mk, 0),
            # 2026-08-21 honesty inputs (CONF-02 v0.6).
            "mandatory_review_open": bool((mandatory_review.get(mk) or {}).get("open")),
            "deferred_gradient": deferred_gradients.get(mk),
        }
        confidence_map[mk] = conf.curve_confidence(ev)
        metric_scores[mk] = conf.metric_score(ev)

    # --- Portfolio (SELECT-01) ---
    portfolio = compact_portfolio(intended, column_functions, metric_config)

    # --- Review Priority (impact x uncertainty x novelty) for flagged metrics ---
    single_cover = {r.get("function") for r in portfolio
                    if len(r.get("metrics") or []) == 1}
    portfolio_metrics = {m for r in portfolio for m in (r.get("metrics") or [])}
    review_priorities: dict[str, dict] = {}
    for mk in flagged:
        fn = column_functions.get(mk)
        review_priorities[mk] = conf.review_priority(
            confidence_label=(confidence_map.get(mk) or {}).get("label", "Low"),
            ref02=bool(tier.get("ref02_triggered")),
            coverage_critical=bool(fn and fn in single_cover),
            in_portfolio=mk in portfolio_metrics,
            optimum_form=str(metric_config.get(mk, {}).get("curve_form")) == "optimum",
        )

    # --- Bundle (in-scope, complete curves only) ---
    aid = assessment_id or library.slugify(name)
    a_name = assessment_name or f"{name} reference assessment"
    region = {"kind": "ecoregion", "code": str(l3_code), "name": name}
    ref_note = (f"Reference tier: {tier['reference_tier']} "
                f"(screening preset {screening.get('preset', screen_preset)}, method {method}). "
                f"Retained {evidence['n_retained']} of {n_candidates} candidates.")
    meta = {
        "assessmentId": aid,
        "assessmentName": a_name,
        "region": region,
        "stateCode": "",
        "stateName": "",
        "sourceCitation": source_citation or f"USEPA NRSA (L3 ecoregion {l3_code}), StreamCurves Regional Analysis Agent",
        "applicability": name,
        "author": author,
        "revisionNotes": ref_note + ("  " + " ".join(tier.get("review_flags") or []) if tier.get("review_flags") else ""),
        # An unattended run must not be able to mint a version with silent gaps, so
        # anything left uncovered has to arrive here as an explicit justification.
        "functionCoverageExceptions": coverage_exceptions or [],
        # REF stamp for the bundle and every metric entry (REF-02 provenance).
        "referenceTier": tier["reference_tier"],
    }
    meta["metricAnnotations"] = metric_annotations(
        intended=intended, curve_rows=curve_rows, metric_config=metric_config,
        sample_sizes=sample_sizes, confidence_map=confidence_map,
        deferred_gradients=deferred_gradients)
    intended_rows = {mk: curve_rows[mk] for mk in intended if mk in curve_rows}
    bundle = None
    bundle_error = None
    try:
        bundle = deep_export.build_deep_assessment_bundle(
            intended_rows, evidence["mapping_df"], metric_config, meta)
    except ValueError as exc:  # no complete mappable curve
        bundle_error = str(exc)

    # Coverage from the bundle when there is one (authoritative -- it reflects what
    # actually mapped), else from the portfolio so a failed build still reports it.
    if bundle is not None:
        coverage = bundle.get("functionCoverage")
    else:
        coverage = deep_export.function_coverage(
            [{"functionId": r["function_id"], "metrics": r["metrics"]}
             for r in portfolio if r.get("function_id")],
            deep_export.deep_read_staf_crosswalk(), coverage_exceptions)

    return {
        "l3_code": str(l3_code),
        "name": name,
        "assessment_id": aid,
        "meta": meta,
        "region": region,
        "n_candidates": n_candidates,
        "screening_method": method,
        "screening_counts": evidence["screening_counts"],
        "reference_tier": tier["reference_tier"],
        "ref02_triggered": tier.get("ref02_triggered", False),
        "review_flags": tier.get("review_flags", []),
        "retained_site_ids": sorted(retained_ids),
        "metric_config": metric_config,
        "predictor_config": evidence["predictor_config"],
        "column_functions": column_functions,
        "discipline_function_mapping": evidence["mapping_df"],
        "flagged_direction": evidence["flagged_direction"],
        "source_reports": evidence["source_reports"],
        "data": evidence["data"],
        "curve_rows": curve_rows,
        "curve_review": curve_review,
        "intended_metrics": intended,
        "flagged_metrics": flagged,
        "sample_sizes": sample_sizes,
        "sample_size_flags": evidence["sample_size_flags"],
        "missingness": missingness,
        "reference_pool_disposition": evidence["reference_pool_disposition"],
        "run_seed": evidence["run_seed"],
        "diagnostics_n_boot": evidence["diagnostics_n_boot"],
        "diagnostics": diagnostics,
        "red06_stability": evidence["red06_stability"],
        "strat_evidence": evidence["strat_evidence"],
        "tier_evaluation": evidence["tier_evaluation"],
        "confidence": confidence_map,
        "metric_scores": metric_scores,
        "review_priorities": review_priorities,
        "domain_checks": evidence["domain_checks"],
        "deferred_gradients": deferred_gradients,
        "mandatory_review": mandatory_review,
        "removed_metrics": dict(remove_metrics or {}),
        "finalized_metrics": dict(finalize_metrics or {}),
        "redundancy": redundancy,
        "stratifiers": evidence["stratifiers"],
        "portfolio": portfolio,
        "coverage": coverage,
        "uncovered_functions": uncovered_functions(portfolio),
        "coverage_exceptions": coverage_exceptions or [],
        "bundle": bundle,
        "bundle_error": bundle_error,
        "screening_tables": screening.get("tables", {}),
    }


def run(l3_code: str, name: str, *,
        screen_preset: str = "functional",
        source_citation: str = "",
        assessment_id: Optional[str] = None,
        assessment_name: Optional[str] = None,
        author: str = "StreamCurves Regional Analysis Agent",
        on_event: Optional[Callable] = None,
        do_screen: bool = True,
        use_streamcat: bool = True,
        coverage_exceptions: Optional[list[dict]] = None,
        cache_dir: Optional[Path] = None,
        diagnostics_n_boot: int = 200,
        diagnostics_enabled: bool = True,
        finalize_metrics: Optional[dict] = None,
        finalize_actor: str = "",
        remove_metrics: Optional[dict] = None,
        reviewer_decisions: Optional[list] = None) -> dict:
    """Run the full regional analysis for one L3 ecoregion. Returns a structured result
    (no files written here; the CLI writes outputs and publishes).

    Since 2026-08-22 this is :func:`run_evidence` followed by :func:`assemble`,
    with an unchanged signature and result.

    ``remove_metrics`` (metric -> rationale) records a named reviewer decision
    that takes a built curve out of scope for this run only (the curve is still
    built and diagnosed, so its evidence is on the record, and the registry row
    reads removed_from_scope). It is the per-region door the national direction
    registries do not have (2026-08-21, the Eastern Corn Belt Plains pH decision).
    ``reviewer_decisions`` (the same list the CLI merges into provenance) lets
    the confidence heuristic lift the mandatory_review_open cap for adjudicated
    items and the registry distinguish reviewed_then_finalized from
    auto_finalized.

    ``coverage_exceptions``: documented reasons a STAF function carries no metric
    (see ``deep_export.validate_coverage_exceptions``). Without one for each gap the
    publish step refuses the version, which is deliberate -- an unattended run should
    not be able to mint an assessment with an unexplained hole in the framework.
    """
    evidence = run_evidence(
        l3_code, name, screen_preset=screen_preset, on_event=on_event,
        do_screen=do_screen, use_streamcat=use_streamcat, cache_dir=cache_dir,
        diagnostics_n_boot=diagnostics_n_boot, diagnostics_enabled=diagnostics_enabled)
    return assemble(
        evidence, source_citation=source_citation, assessment_id=assessment_id,
        assessment_name=assessment_name, author=author,
        coverage_exceptions=coverage_exceptions, finalize_metrics=finalize_metrics,
        finalize_actor=finalize_actor, remove_metrics=remove_metrics,
        reviewer_decisions=reviewer_decisions)


# --------------------------------------------------------------------------- #
# Stratifier evaluation (STRAT-00, advisory)
#
# The agent used to publish no stratification analysis at all, so every reopened
# assessment reported that exploratory screening, cross-metric analysis and
# verification had never been run. They had not: no candidate stratification was
# ever defined, so there was nothing to screen.
#
# Advisory by design. This decides nothing about the curves -- it runs after the
# metric config is final and before build_curves, and build_curves reads only
# metric_config columns, so the class columns added here cannot perturb a curve.
# A candidate that passes STRAT-00 becomes a review item, not a decision.
# --------------------------------------------------------------------------- #
PHASE2_SIG_THRESHOLD = 0.05
PHASE2_SUPPORT_THRESHOLD = 0.5


def attach_stratifier_sources(data: pd.DataFrame, registry: dict | None = None) -> pd.DataFrame:
    """Attach the raw NRSA columns the stratifier candidates are built from.

    None of them is a response metric, so build_metric_config never selects them
    and attach_nrsa_metrics never pulls them in. Offline: they all live in the
    bundled parquet.
    """
    registry = registry or stratifiers.load_national_registry()
    wanted = [c for c in stratifiers.source_columns(registry) if c not in data.columns]
    if not wanted:
        return data
    return nrsa.attach_nrsa_metrics(data, wanted, nrsa.load_nrsa_values())


def _phase3_finalists(candidates: pd.DataFrame, ranking, allowed: list[str]) -> list[str]:
    """The stratifications verification looks at: this metric's own promising or
    possible candidates, plus any the cross-metric ranking calls broad-use."""
    out: list[str] = []
    if candidates is not None and len(candidates) > 0:
        promising = candidates[candidates["candidate_status"].isin(("promising", "possible"))]
        out.extend(str(sk) for sk in promising["stratification"])
    if ranking is not None and len(ranking) > 0 and "tier" in ranking.columns:
        broad = ranking.loc[ranking["tier"] == "Broad-Use Candidate", "stratification"]
        out.extend(str(sk) for sk in broad)
    seen = set()
    return [sk for sk in out if sk in allowed and not (sk in seen or seen.add(sk))]


def _unstratified_decision(metric: str) -> pd.DataFrame:
    """The advisory-mode decision frame: this metric's curve is unstratified.

    Written into both completed_metrics and metric_phase_cache so that on reopen
    get_metric_curve_stratification never falls back to the phase-1
    recommendation, which would recompute a "single" phase4 signature, stop
    matching the stored one, and blank every curve in the assessment.
    """
    return pd.DataFrame([{
        "metric": metric, "decision_type": "none", "selected_strat": None,
        "p_value": None, "effect_size": None, "effect_size_label": None,
        "runner_up_strat": None, "runner_up_p_value": None,
        "needs_review": False, "review_reason": None, "notes": None,
    }])


def run_stratifier_analysis(
    data: pd.DataFrame,
    metric_config: dict,
    predictor_config: dict,
    *,
    registry: dict | None = None,
    on_event: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Screen every eligible stratifier candidate against every metric.

    Sets ``allowed_stratifications`` / ``allowed_predictors`` on ``metric_config``
    in place, then runs phase 1 (Kruskal-Wallis screening plus effect sizes),
    phase 2 (cross-metric consistency and ranking) and phase 3 (pattern stability
    and feasibility). Returns the session fields the app's three analysis tabs
    read, plus the eligibility ledger for the run record.
    """
    registry = registry or stratifiers.load_national_registry()
    data, skipped = stratifiers.materialize_candidates(data, registry)
    ledger = stratifiers.assess_eligibility(data, registry, skipped=skipped)
    eligible = stratifiers.eligible_keys(ledger)
    strat_config = stratifiers.strat_config_for(registry, eligible, data)

    if on_event:
        on_event({
            "event": "stratifier_screen",
            "registered": int(len(ledger)),
            "eligible": eligible,
            "excluded": {
                str(r["stratification"]): r["exclusion_reason"]
                for _, r in ledger.iterrows() if not r["eligible"]
            },
        })

    predictor_keys = list(predictor_config or {})
    for mk, mc in metric_config.items():
        # A categorical metric has no distribution to compare across strata;
        # run_all_stratification_screening skips it for the same reason.
        mc["allowed_stratifications"] = (
            [] if mc.get("metric_family") == "categorical" else list(eligible)
        )
        mc["allowed_predictors"] = list(predictor_keys)

    result = {
        "data": data,
        "registry": registry,
        "registry_version": registry.get("version"),
        "eligibility": ledger,
        "eligible": eligible,
        "strat_config": strat_config,
        "all_layer1_results": {},
        "all_layer2_results": {},
        "phase1_candidates": {},
        "cross_metric_consistency": None,
        "phase2_ranking": None,
        "phase2_settings": None,
        "phase3_verification": {},
        "metric_phase_cache": {},
        "advisory_decisions": pd.DataFrame(),
    }
    if not eligible:
        logger.info("No stratifier candidate is eligible for this region; skipping STRAT-00.")
        return result

    # --- Phase 1: screening + effect sizes ---
    screened = strat_screening.run_all_stratification_screening(
        data, metric_config, strat_config)
    all_results = screened["results"]
    all_pairwise = screened["pairwise"]

    l1: dict[str, pd.DataFrame] = {}
    l2: dict[str, pd.DataFrame] = {}
    cands: dict[str, pd.DataFrame] = {}
    for mk, mc in metric_config.items():
        allowed = mc.get("allowed_stratifications") or []
        if not allowed:
            continue
        rows = (
            all_results[all_results["metric"] == mk].reset_index(drop=True)
            if len(all_results) else pd.DataFrame()
        )
        if len(rows) == 0:
            continue
        tested = rows["stratification"].astype(str).unique().tolist()
        try:
            effect_sizes = effects.compute_effect_sizes(
                data, mk, tested, metric_config, strat_config)
        except Exception as exc:  # noqa: BLE001 — one bad column must not end the run
            logger.warning("Effect sizes failed for %s: %s", mk, exc)
            effect_sizes = pd.DataFrame()
        l1[mk] = rows
        if len(effect_sizes) > 0:
            l2[mk] = effect_sizes
        cands[mk] = strat_screening.build_metric_phase1_candidate_table_from_sources(
            metric=mk, allowed=allowed, l1=rows, l2=effect_sizes, include_all_allowed=True,
        )

    # --- Phase 2: cross-metric consistency + ranking ---
    consistency_result = None
    ranking = None
    if len(l1) >= 2:
        consistency_result = consistency.compute_strat_consistency(
            l1, l2, metric_config, strat_config, sig_threshold=PHASE2_SIG_THRESHOLD)
        ranking = consistency.build_phase2_ranking(
            consistency_result, cands, PHASE2_SUPPORT_THRESHOLD)

    # --- Phase 3: pattern stability + feasibility (advisory) ---
    verification: dict[str, dict] = {}
    cache: dict[str, dict] = {}
    for mk in metric_config:
        allowed = metric_config[mk].get("allowed_stratifications") or []
        finalists = _phase3_finalists(cands.get(mk), ranking, allowed)
        patterns, feas = pd.DataFrame(), pd.DataFrame()
        if finalists:
            frames = []
            for sk in ["none"] + finalists:
                try:
                    res = stability.assess_pattern_stability(
                        data, mk, None if sk == "none" else sk, predictor_keys,
                        metric_config, strat_config, predictor_config,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Pattern stability failed for %s / %s: %s", mk, sk, exc)
                    res = pd.DataFrame()
                if res is not None and len(res) > 0:
                    frames.append(res)
            patterns = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            try:
                feas = feasibility.assess_feasibility(data, finalists, strat_config)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Feasibility failed for %s: %s", mk, exc)
                feas = pd.DataFrame()

        if mk in l1 or finalists:
            verification[mk] = {
                "finalists": finalists,
                "pattern_results": {"results": patterns, "plots": {}},
                "feasibility_results": feas,
                "verification_status": {sk: "verified" for sk in finalists},
                # Advisory: the curve stays unstratified until a human says otherwise.
                "selected_strat": "none",
                "justification": "Screened by the regional agent (STRAT-00, advisory).",
            }
            cache[mk] = {
                "phase1_screening": {
                    "results": l1.get(mk, pd.DataFrame()),
                    "pairwise": (
                        all_pairwise[all_pairwise["metric"] == mk].reset_index(drop=True)
                        if len(all_pairwise) and "metric" in all_pairwise.columns
                        else pd.DataFrame()
                    ),
                    "plots": {},
                    # run_all_stratification_screening keys plot specs
                    # "{metric}_{strat}" while the views key them by bare strat, so
                    # persisting them wholesale renders nothing. Summary mode makes
                    # the app rebuild them correctly on first open instead.
                    "plot_specs": {},
                },
                "phase1_effect_sizes": l2.get(mk, pd.DataFrame()),
                "phase1_artifact_mode": "summary",
                "phase3_patterns": {"results": patterns, "plots": {}},
                "phase3_feasibility": feas,
                "phase3_artifact_mode": "summary",
                "strat_decision_user": _unstratified_decision(mk),
            }

    # Recorded, never applied: the composite score and needs_review reasons are
    # the tracked-decision artifact for the run, not an instruction to the curves.
    try:
        advisory = decision.make_stratification_decisions(
            all_results, all_pairwise, metric_config, strat_config,
            effect_sizes=pd.concat(l2.values(), ignore_index=True) if l2 else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Advisory stratification decisions failed: %s", exc)
        advisory = pd.DataFrame()

    result.update({
        "all_layer1_results": l1,
        "all_layer2_results": l2,
        "phase1_candidates": cands,
        "cross_metric_consistency": consistency_result,
        "phase2_ranking": ranking,
        "phase2_settings": {
            "metric_filter": sorted(l1),
            "strat_filter": list(eligible),
            "sig_threshold": PHASE2_SIG_THRESHOLD,
            "support_threshold": PHASE2_SUPPORT_THRESHOLD,
        },
        "phase3_verification": verification,
        "metric_phase_cache": cache,
        "advisory_decisions": advisory,
    })
    logger.info(
        "STRAT-00: %d eligible candidate(s), %d metric(s) screened, %d verified.",
        len(eligible), len(l1), len(verification),
    )
    return result


# --------------------------------------------------------------------------- #
# Session assembly + publish (kept JSON-safe for a clean round-trip)
# --------------------------------------------------------------------------- #
def _completed_metric_entry(mk: str, row: dict) -> dict:
    """One ``completed_metrics`` entry in the shape the workspace actually accepts.

    Mirrors the published pilot session exactly. ``views/summary_state.py``
    ``metric_phase4_entry_is_current`` requires a ``phase4_signature`` matching what
    ``build_metric_phase4_signature`` recomputes on restore AND non-empty
    ``phase4_curve_rows``; without both, every metric reopens as "not computed" and no
    curve renders. The signature below pairs with ``data_fingerprint: None`` and
    ``config_version: 0`` in the session fields, and ``decision_type "none"`` is the
    unstratified case.
    """
    base = {k: v for k, v in row.items() if k != "curve_points"}
    curve_row_df = pd.DataFrame([base])
    points = row.get("curve_points")
    phase4_rows = curve_row_df.copy()
    phase4_rows["curve_points"] = [points]      # nested frame in an object cell (pilot parity)
    strat_decision = pd.DataFrame([{
        "metric": mk, "decision_type": "none", "selected_strat": None,
        "selected_p_value": None, "selected_n_groups": None, "selected_min_n": None,
        "runner_up_strat": None, "runner_up_p_value": None,
        "needs_review": False, "review_reason": None, "notes": None,
    }])
    return {
        "strat_decision": strat_decision,
        "reference_curve": {"curve_row": curve_row_df, "curve_points": points,
                            "curve_source": "auto"},
        "phase4_curve_rows": phase4_rows,
        "phase4_signature": {"data_fingerprint": None, "config_version": 0,
                             "decision_type": "none", "selected_strat": None},
        "phase4_artifact_mode": "summary",
    }


def stage_status_for(result: dict) -> dict:
    """Derive the workflow-stage status from what the run actually did.

    The agent used to stamp every stage "done" unconditionally, so the strip read
    complete over an assessment whose analysis tabs correctly reported that
    nothing had run. Deriving it through the app's own pure deriver means agent
    and app cannot disagree. ``publish`` is stamped by publish(), not here: at
    this point nothing has been published.
    """
    screening = result.get("screening_tables") or {}
    n_retained = len(result.get("retained_site_ids") or [])
    status = run_state.derive_stage_status({
        "has_region": True,
        "region_kind": "ecoregion",
        "region_label": result.get("name"),
        "has_data_source": True,
        "n_candidates": result.get("n_candidates"),
        "has_screening": bool(screening.get("easi_screening_sites")),
        "n_retained": n_retained,
        "enriched": True,
        "n_enriched": n_retained,
        "curve_review": result.get("curve_review") or {},
        "coverage": result.get("coverage"),
        "mapping_confirmed": True,
        "n_unmapped_functions": len(result.get("uncovered_functions") or []),
        "published": False,
    })
    strat = result.get("stratifiers") or {}
    ledger = strat.get("eligibility")
    status["enrichment_build"]["stratifier_screen"] = {
        "mode": "advisory",
        "registry_version": strat.get("registry_version"),
        "candidates_registered": int(len(ledger)) if ledger is not None else 0,
        "candidates_eligible": len(strat.get("eligible") or []),
        "metrics_screened": len(strat.get("all_layer1_results") or {}),
    }
    return status


def session_fields(result: dict) -> dict:
    """The SESSION_FIELDS a reopenable session needs, including the full phase-4 curve
    artifacts so the workspace renders curves on Open (not a bare 'recompute' state)."""
    screening = result.get("screening_tables") or {}
    strat = result.get("stratifiers") or {}
    # Every built curve, not just the in-scope ones, so a reviewer can see the flagged
    # (degenerate / exploratory) curves too.
    completed = {mk: _completed_metric_entry(mk, row)
                 for mk, row in (result.get("curve_rows") or {}).items()}
    return {
        "app_data_loaded": True,
        # These two pair with every phase4_signature above; the restore-side
        # build_metric_phase4_signature reads them, so they must match or no curve renders.
        "data_fingerprint": None,
        "config_version": 0,
        "region_of_applicability": result["region"],
        "run_meta": {**run_state.new_run_meta(region=result["region"]),
                     "n_candidates": result["n_candidates"]},
        "run_stage_status": stage_status_for(result),
        # --- STRAT-00 diagnostics (advisory) ---
        # Without these the Exploratory, Cross-Metric and Verification tabs of a
        # reopened assessment truthfully report that nothing was ever run.
        "strat_config": strat.get("strat_config") or {},
        "all_layer1_results": strat.get("all_layer1_results") or {},
        "all_layer2_results": strat.get("all_layer2_results") or {},
        "phase1_candidates": strat.get("phase1_candidates") or {},
        "phase2_ranking": strat.get("phase2_ranking"),
        "cross_metric_consistency": strat.get("cross_metric_consistency"),
        "phase2_settings": strat.get("phase2_settings"),
        "phase3_verification": strat.get("phase3_verification") or {},
        "metric_phase_cache": strat.get("metric_phase_cache") or {},
        # Advisory mode, pinned. get_metric_curve_stratification falls back to the
        # phase-1 recommendation when a metric has no stored choice, which would
        # recompute a "single" phase4 signature, stop matching the stored "none"
        # one, and blank every curve in the assessment. The curves were built
        # unstratified; say so explicitly.
        "curve_stratification": {mk: "none" for mk in completed},
        # RED-01 evidence, computed on every run and previously discarded from the
        # session so only the run folder CSV kept it.
        "metric_redundancy": result.get("redundancy"),
        "screening_run": {
            "n_screened": result["screening_counts"].get("n_screened"),
            "n_retained": result["screening_counts"].get("n_retained"),
            "method": result["screening_method"],
            "method_version": run_state.SCREENING_METHOD_VERSION,
            "reference_tier": result["reference_tier"],
            "note": "; ".join(result.get("review_flags") or []) or "Real EASI reference screen.",
        },
        "easi_screening_sites": screening.get("easi_screening_sites", []),
        "easi_screening_metrics": screening.get("easi_screening_metrics", []),
        "easi_screening_criteria": screening.get("easi_screening_criteria", {}),
        "metric_config": result["metric_config"],
        "predictor_config": result.get("predictor_config") or {},
        # So a revision does not have to re-argue every documented gap.
        "function_coverage_exceptions": result.get("coverage_exceptions") or [],
        # This agent builds its configs directly and never has a workbook to
        # save, so published sessions used to carry input_metadata: null -- and
        # the app's whole Workbook panel reads from it, so a reopened assessment
        # showed "No data loaded." over a complete dataset. Derive the sheets
        # from the configs we just built (settings preserved, not defaulted).
        "input_metadata": workbook.tables_from_configs(
            result["data"],
            result["metric_config"],
            result.get("predictor_config") or {},
            strat.get("strat_config") or {},
        ),
        "column_functions": result["column_functions"],
        "discipline_function_mapping": result["discipline_function_mapping"],
        "discipline_function_mapping_confirmed": True,
        "data": result["data"],
        "curve_review": result["curve_review"],
        "completed_metrics": completed,
    }


def publish(result: dict, publish_root: Path | str, *, maintainer: str = "regional-agent",
            provenance: dict | None = None,
            portfolio_approvals: list[dict] | None = None) -> dict:
    """Publish the result's bundle as a preliminary version into a (staging) library root.

    ``publish_root`` is set as STAF_LIBRARY_ROOT so this never touches canonical apps/library
    unless the caller points it there. ``portfolio_approvals`` carries the recorded
    human approvals SELECT-01 requires for any function with more than two metrics
    (the agent can never self-approve one). Returns {version, path} or raises."""
    import os
    if result.get("bundle") is None:
        raise RuntimeError(f"nothing to publish: {result.get('bundle_error')}")
    root = Path(publish_root)
    root.mkdir(parents=True, exist_ok=True)
    os.environ["STAF_LIBRARY_ROOT"] = str(root)
    # Writing the canonical apps/library tree is gated (STAF_LIBRARY_PUBLISH=1 + writable +
    # a maintainer audit name). A staging root keeps publish_version's writable-only check.
    if root.resolve() == CANONICAL_LIBRARY:
        reason = library.publish_gate_reason(maintainer)
        if reason:
            raise RuntimeError(f"canonical publish blocked: {reason}")
        os.environ.setdefault("STAF_LIBRARY_MAINTAINER", maintainer)
    # session_fields leaves publish un-done because at that point it is; stamp it
    # here, where a version is actually about to be minted.
    fields = session_fields(result)
    fields["run_stage_status"] = {
        **fields["run_stage_status"],
        "publish": {"status": run_state.STAGE_DONE,
                    "detail": f"Published {result['meta']['assessmentName']}."},
    }
    payload = session_io.dump_session_fields(
        fields, session_name=result["meta"]["assessmentName"])
    meta = dict(result["meta"])
    if portfolio_approvals:
        meta["portfolioApprovals"] = list(portfolio_approvals)
    version = library.publish_version(result["assessment_id"], meta, payload,
                                      result["bundle"], provenance=provenance)
    return {"version": version, "root": str(root),
            "path": str(root / "assessments" / library.slugify(result["assessment_id"])
                        / f"v{version}")}
