"""Static configuration and data loaders for DEEP.

Holds (a) the scoring constants shared across STAF tiers (outcome weights, color
bands, function-score max) — kept identical to EASI/SFARI so the three tiers are
numerically comparable — and (b) loaders for the generated data files in
``data/`` (functions, outcome mapping, predefined assessments) produced by
``scripts/build_deep_data.py`` from the STAF metric library.

NOTE on the scoring convention: DEEP uses indirect weight **0.10** and a **0-15**
function scale (matching the SFARI report and SFARI/EASI ``config.py``), NOT the
0.25 / 0-10 variant that appears in ``staf/docs/tiered-approach.md``.
:func:`validate` guards this so it can't drift.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# --- Scoring constants (shared with EASI/SFARI/STAF) ---
# Outcome contribution weights: Direct, indirect, none.
WEIGHTS: dict[str, float] = {"D": 1.0, "i": 0.1, "-": 0.0}
OUTCOMES = ("physical", "chemical", "biological")
FUNCTION_SCORE_MAX = 15

# Color bands. Index bands (0-1) and function-score bands (0-15) mirror STAF/EASI/SFARI.
INDEX_BANDS = [(0.39, "#f5b5b5"), (0.69, "#f5e7a6"), (1.01, "#c8d9f2")]
INDEX_BAND_LABELS = ("Non-Functioning", "Functioning-at-Risk", "Functioning")
FUNCTION_SCORE_BANDS = [(5, "#f5b5b5"), (10, "#f5e7a6"), (FUNCTION_SCORE_MAX, "#c8d9f2")]
FUNCTION_SCORE_BAND_SHORT = ("NF", "AR", "F")

CATEGORY_ORDER = ("Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology")


@functools.lru_cache(maxsize=None)
def _load(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


# --- Framework constants: the 20 functions + outcome mapping ---
def functions() -> list[dict]:
    return _load("deep-functions.json")


def functions_by_id() -> dict[str, dict]:
    return {f["id"]: f for f in functions()}


def functions_by_category() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in sorted(functions(), key=lambda f: f.get("order", 0)):
        out.setdefault(f["category"], []).append(f)
    return out


def outcome_mapping() -> dict[str, dict[str, str]]:
    """function id -> {physical, chemical, biological} contribution codes (D/i/-)."""
    rows = _load("deep-outcome-mapping.json")
    return {r["id"]: {k: r[k] for k in OUTCOMES} for r in rows}


# --- Predefined assessment registry ---
def assessments_doc() -> dict:
    return _load("deep-assessments.json")


def assessments() -> list[dict]:
    return assessments_doc()["assessments"]


def assessments_by_id() -> dict[str, dict]:
    return {a["assessmentId"]: a for a in assessments()}


def validate() -> list[str]:
    """Consistency + convention checks. Empty list == OK."""
    problems: list[str] = []
    # Guard the scoring convention (reject the 0.25 / 0-10 variant outright).
    if WEIGHTS.get("i") != 0.1:
        problems.append(f"indirect weight must be 0.10, got {WEIGHTS.get('i')!r}")
    if FUNCTION_SCORE_MAX != 15:
        problems.append(f"FUNCTION_SCORE_MAX must be 15, got {FUNCTION_SCORE_MAX!r}")
    fids = set(functions_by_id())
    if len(fids) != 20:
        problems.append(f"expected 20 functions, got {len(fids)}")
    mapping = outcome_mapping()
    for fid in sorted(fids):
        if fid not in mapping:
            problems.append(f"function {fid} missing from outcome mapping")
    return problems
