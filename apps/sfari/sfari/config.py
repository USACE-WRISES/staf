"""Static configuration and data loaders for SFARI.

Holds (a) the scoring constants shared with EASI/STAF (outcome weights, color
bands, function-score max) and (b) loaders for the three generated data files in
``data/`` (functions, metrics, outcome mapping). The SFARI method facts
(function/metric definitions, Likert criteria, function->outcome mapping) live in
those JSON files; this module holds design metadata + loaders.

NOTE on the outcome mapping: ``sfari-outcome-mapping.json`` is the operative
mapping from the doc's worked example / calculator (reproduces the published
sub-indices). ``sfari-outcome-mapping-table1.json`` is the Table 1 reference
mapping, kept for SME review (see scripts/build_sfari_data.py).
"""
from __future__ import annotations

import functools
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# --- Scoring constants (shared with EASI/STAF) ---
# Outcome contribution weights: Direct, indirect, none.
WEIGHTS: dict[str, float] = {"D": 1.0, "i": 0.1, "-": 0.0}
OUTCOMES = ("physical", "chemical", "biological")
FUNCTION_SCORE_MAX = 15

# rating -> index (kept for completeness; SFARI scores functions directly).
RATING_INDEX: dict[str, float] = {"Good": 0.85, "Fair": 0.545, "Poor": 0.195}

# Color bands. Index bands (0-1) and function-score bands (0-15) mirror STAF/EASI.
INDEX_BANDS = [(0.39, "#f5b5b5"), (0.69, "#f5e7a6"), (1.01, "#c8d9f2")]
INDEX_BAND_LABELS = ("Non-Functioning", "Functioning-at-Risk", "Functioning")
FUNCTION_SCORE_BANDS = [(5, "#f5b5b5"), (10, "#f5e7a6"), (FUNCTION_SCORE_MAX, "#c8d9f2")]
FUNCTION_SCORE_BAND_SHORT = ("NF", "AR", "F")

# Data-confidence levels for the evidence badges.
CONFIDENCE = ("H", "M", "L")

# --- Likert scale (SFARI metric scoring) ---
LIKERT_ORDER = ("Strongly Agree", "Agree", "Neutral", "Disagree", "Strongly Disagree")
LIKERT_SHORT = {"Strongly Agree": "SA", "Agree": "A", "Neutral": "N",
                "Disagree": "D", "Strongly Disagree": "SD"}
# Optional Likert -> numeric conversion for the function-score auto-suggest (doc default).
LIKERT_NUMERIC = {"Strongly Agree": 14, "Agree": 11, "Neutral": 8, "Disagree": 5, "Strongly Disagree": 2}
LIKERT_NA = "Not Applicable"


@functools.lru_cache(maxsize=None)
def _load(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def outcome_mapping() -> dict[str, dict[str, str]]:
    """function id -> {physical, chemical, biological} contribution codes (operative)."""
    rows = _load("sfari-outcome-mapping.json")
    return {r["id"]: {k: r[k] for k in OUTCOMES} for r in rows}


def outcome_mapping_table1() -> dict[str, dict[str, str]]:
    """Table 1 reference mapping (for SME review; not the app default)."""
    rows = _load("sfari-outcome-mapping-table1.json")
    return {r["id"]: {k: r[k] for k in OUTCOMES} for r in rows}


def functions() -> list[dict]:
    return _load("sfari-functions.json")


def functions_by_id() -> dict[str, dict]:
    return {f["id"]: f for f in functions()}


def functions_by_category() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in sorted(functions(), key=lambda f: f["order"]):
        out.setdefault(f["category"], []).append(f)
    return out


def metrics_doc() -> dict:
    return _load("sfari-metrics.json")


def metrics() -> list[dict]:
    return metrics_doc()["metrics"]


def metrics_by_id() -> dict[str, dict]:
    return {m["metricId"]: m for m in metrics()}


def metrics_by_function() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for m in metrics():
        out.setdefault(m["functionId"], []).append(m)
    return out


def desktop_metrics() -> list[dict]:
    return [m for m in metrics() if m.get("desktopSupportable")]


def online_resources() -> list[dict]:
    return metrics_doc().get("onlineResources", [])


CATEGORY_ORDER = ("Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology")


def validate() -> list[str]:
    """Consistency checks across the three data files."""
    problems: list[str] = []
    mapping = outcome_mapping()
    fids = set(functions_by_id())
    if len(fids) != 20:
        problems.append(f"expected 20 functions, got {len(fids)}")
    for fid in fids:
        if fid not in mapping:
            problems.append(f"function {fid} missing from outcome mapping")
    for m in metrics():
        if m["functionId"] not in fids:
            problems.append(f"metric {m['metricId']} references unknown function {m['functionId']}")
    return problems
