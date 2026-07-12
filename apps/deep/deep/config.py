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


# --- Predefined assessment registry (v2: one record per (id, version)) ---
def assessments_doc() -> dict:
    return _load("deep-assessments.json")


def _normalize_record(r: dict) -> dict:
    """Ensure a registry record carries ``version`` / ``lifecycle`` / ``assessmentRef``
    (v1 records lack them: version from the library block or 1, lifecycle preliminary)."""
    r = dict(r)
    lib = r.get("library") or {}
    ver = r.get("version") or lib.get("version") or 1
    try:
        ver = int(ver)
    except (TypeError, ValueError):
        ver = 1
    r["version"] = ver
    if not r.get("lifecycle"):
        r["lifecycle"] = str(lib.get("lifecycle") or lib.get("status") or "preliminary")
    if not r.get("assessmentRef"):
        r["assessmentRef"] = f"{r.get('assessmentId')}@v{ver}"
    return r


def _registry_records() -> list[dict]:
    """All (id, version) records, live-library-merged by ref (local/desktop only)."""
    baked = [_normalize_record(r) for r in assessments_doc().get("assessments", [])]
    try:
        from . import library as _library  # local import avoids an import cycle

        extra = _library.all_eligible_bundles()
    except Exception:  # noqa: BLE001
        extra = []
    if not extra:
        return baked
    order = [r["assessmentRef"] for r in baked]
    by_ref = {r["assessmentRef"]: r for r in baked}
    for bundle in extra:
        rec = _normalize_record(bundle)
        ref = rec["assessmentRef"]
        if ref not in by_ref:
            order.append(ref)
        by_ref[ref] = rec
    return [by_ref[ref] for ref in order if ref in by_ref]


def library_catalog() -> dict[str, dict]:
    """``{id: {defaultVersion, latestCertified, latestPreliminary}}`` derived from the
    live registry records (certified wins the default, else latest preliminary)."""
    by_id: dict[str, dict] = {}
    for r in _registry_records():
        aid = r.get("assessmentId")
        if not aid:
            continue
        ver = int(r.get("version") or 1)
        life = r.get("lifecycle", "preliminary")
        d = by_id.setdefault(aid, {"versions": [], "certified": [], "preliminary": []})
        d["versions"].append(ver)
        (d["certified"] if life == "certified" else d["preliminary"]).append(ver)
    out: dict[str, dict] = {}
    for aid, d in by_id.items():
        lc = max(d["certified"]) if d["certified"] else 0
        lp = max(d["preliminary"]) if d["preliminary"] else 0
        out[aid] = {
            "defaultVersion": lc or lp or (max(d["versions"]) if d["versions"] else 1),
            "latestCertified": lc,
            "latestPreliminary": lp,
        }
    return out


def assessments_by_ref() -> dict[str, dict]:
    return {r["assessmentRef"]: r for r in _registry_records()}


def default_ref_for(assessment_id: str) -> str | None:
    cat = library_catalog().get(assessment_id)
    if not cat:
        return None
    return f"{assessment_id}@v{cat['defaultVersion']}"


def load_ref(ref: str) -> dict | None:
    return assessments_by_ref().get(ref)


def assessments() -> list[dict]:
    """One record per assessment at its default version (back-compat surface for the
    picker, coverage, and region features). Ordered by first appearance in the registry."""
    by_ref = assessments_by_ref()
    cat = library_catalog()
    seen: list[str] = []
    for r in _registry_records():
        aid = r.get("assessmentId")
        if aid and aid not in seen:
            seen.append(aid)
    out: list[dict] = []
    for aid in seen:
        ref = default_ref_for(aid)
        if ref and ref in by_ref:
            out.append(by_ref[ref])
    return out


def assessments_by_id() -> dict[str, dict]:
    """id -> the default-version record (back-compat: resolves to the default version)."""
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
