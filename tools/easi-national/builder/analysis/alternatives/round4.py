"""The EASI addendum (Round 4 of the national campaign) as the study runner reads it.

The yaml ``evaluation_protocol_v1_easi_addendum.yaml`` is frozen (its sha256 is checked
before anything is read from it) and names the eight candidate families, the outcomes P1 to
P6 and their margins. This module gives the runner the family record of a built candidate
package, the deciding targets and functions of a family, the margins as numbers (checked
against the yaml's own text by ``margins_in_addendum``), and the reader of a candidate
package folder (``candidate.json`` beside ``<family>.easi-method.zip``) or a bare zip.

Nothing here scores or decides; ``outcomes.py`` computes P1 to P6 and applies the rule.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from builder import STREAM_CURVES_APP

ADDENDUM_FILE = "evaluation_protocol_v1_easi_addendum.yaml"
PROSE_FILE = "EVALUATION_PROTOCOL_V1_EASI_ADDENDUM.md"
ADDENDUM_PATH = STREAM_CURVES_APP / "config" / "methodology" / ADDENDUM_FILE
PROSE_PATH = STREAM_CURVES_APP / "config" / "methodology" / PROSE_FILE
#: the frozen yaml's sha256 (``streamcurves.easi_method.round4.ADDENDUM_SHA256`` is the same
#: literal; a test keeps them equal)
ADDENDUM_SHA256 = "8a48de98a4205c5eab1e423e69fdce2f238a76df8cbafca860bb032696625e6a"
FAMILIES = ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8")
#: the P1 targets as the field evaluation names them: (target column, statistic)
T1 = ("reference_2013", "auc_reference_vs_impaired")
T2 = ("t__bent_mmi", "auc_good_vs_poor")
T3 = (("t__fish_mmi", "auc_good_vs_poor"), ("t__oe", "auc_good_vs_poor"))
T3_TARGETS_FLAT = tuple(target for target, _ in T3)
TARGET_NAMES = {"T1": T1, "T2": T2}
#: field targets whose P2 rows are exploratory and never block (the coordinator's answer of
#: 2026-09-27: the width to depth ratio keeps its sign but never retains the base)
EXPLORATORY_FIELD_TARGETS = ("a__phab_BFWD_RAT",)
#: every target whose per-function or regional row never blocks: T3 and the exploratory field targets
NEVER_BLOCK_TARGETS = T3_TARGETS_FLAT + EXPLORATORY_FIELD_TARGETS
#: the cohort every family decision reads: the development cohort (2013-19); latest_visit1
#: and the retrospective cohort are reported beside it and never decide (the retrospective
#: cohort is read once, by the finalist step's separate retrospective report)
DECIDING_COHORT = "development_1314_1819"
REPORTED_COHORTS = ("latest_visit1", "retrospective_2324")
RETROSPECTIVE_COHORT = "retrospective_2324"
#: the addendum's margins as numbers (section 6 and 7; the yaml spells them in words, and
#: ``margins_in_addendum`` checks every number appears there)
MARGINS = {
    "accuracy_median_delta": 0.01,     # P1 accuracy change: median delta at least 0.01
    "noninferiority_lower": -0.01,     # P1: the other target's (or both targets') lower bound
    "p2_auc_block": -0.01,             # P2: a supported AUC interval wholly below this blocks
    "p2_correlation_block": 0.0,       # P2: a supported Spearman or kappa interval wholly below zero
    "support_draws": 800,              # P2 support: at least 800 of 1,000 valid draws
    "bootstrap_draws": 1000,
    "p4_flip_rise": 0.02,              # P4: the flip share may not rise by more than 0.02
    "p5_optimism": 0.02,               # P5: E8 adopted when the difference exceeds 0.02
    "few_functions": 15,               # P5: fewer than 15 rated functions
    "complete_functions": 20,
    "bh_q": 0.10,                      # multiplicity: BH q <= 0.10, reported
}
_FALLBACK_FUNCTION = {"all": None}


class AddendumError(ValueError):
    """The frozen addendum is not the frozen addendum, or a family is unknown."""


def addendum_sha256(path: Path = ADDENDUM_PATH) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def addendum(path: Path = ADDENDUM_PATH) -> dict:
    """The frozen addendum, parsed, after its bytes are checked against the recorded sha."""
    import yaml
    got = addendum_sha256(path)
    if got != ADDENDUM_SHA256:
        raise AddendumError(f"{Path(path).name} is not the frozen addendum (sha256 {got[:12]}, "
                            f"expected {ADDENDUM_SHA256[:12]}); nothing is decided from an edited protocol")
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def respecifications() -> dict:
    """The dated re-specifications StreamCurves' builder records (``round4.RESPECIFIED``):
    the prose addendum's "Addenda" section as data. Empty when StreamCurves is not importable."""
    try:
        from streamcurves.easi_method import round4 as sc  # noqa: WPS433 - the builder's peer app
    except Exception:  # noqa: BLE001 - the runner still reads the yaml without it
        return {}
    return {k: dict(v) for k, v in getattr(sc, "RESPECIFIED", {}).items()}


def family_spec(family: str, path: Path = ADDENDUM_PATH) -> dict:
    """The addendum's record of one family plus its dated re-specification when one exists."""
    if family not in FAMILIES:
        raise AddendumError(f"unknown family {family!r}; the addendum names {', '.join(FAMILIES)}")
    spec = (addendum(path).get("candidates") or {}).get(family)
    if not isinstance(spec, dict):
        raise AddendumError(f"the addendum has no candidate {family}")
    out = dict(spec, id=family)
    respec = respecifications().get(family)
    if respec:
        out["respecified"] = respec
    return out


def function_ids(spec: dict) -> Optional[list[str]]:
    """The functions a family declares, as the score tables name them (``snake_case``);
    None when the family names every function (E8)."""
    raw = spec.get("function")
    if raw in (None, "all"):
        return None
    items = raw if isinstance(raw, list) else [raw]
    return [str(f).replace("-", "_") for f in items]


def deciding_targets(spec: dict) -> tuple[str, list[str]]:
    """``(primary, deciding)``: the P1 target the accuracy rule reads first and the targets a
    simplification's lower bounds are read on. The yaml names a target only for E3 ("P1 on T1",
    T2 exploratory for population support); for every other family both targets decide and T1
    (the 2013-14 designations) is taken as the primary, recorded as such in the summary."""
    text = str(spec.get("primary_outcome") or "")
    named = re.findall(r"\bT([12])\b", text.split("(")[0])
    if named:
        primary = "T" + named[0]
        return primary, [primary]
    return "T1", ["T1", "T2"]


def comparison_scope(spec: dict) -> Optional[dict]:
    """The rows a candidate's P1 and P2 comparisons are paired on, when a re-specification
    narrows them (E2, Addendum 1: reaches the candidate rates the family function on):
    ``{"function": <snake_case function>, "rule": "candidate-rated"}`` or None."""
    scope = (spec.get("respecified") or {}).get("comparisonScope")
    if not scope:
        return None
    return {"function": str(scope["function"]).replace("-", "_"), "rule": scope.get("rule", "candidate-rated"),
            "reason": f"Addendum {(spec.get('respecified') or {}).get('addendum')} "
                      f"({(spec.get('respecified') or {}).get('date')})"}


def margins_in_addendum(path: Path = ADDENDUM_PATH) -> dict:
    """Every number of ``MARGINS`` found in the yaml's outcomes and multiplicity text, so the
    code's margins and the frozen protocol never drift apart silently."""
    doc = addendum(path)
    text = json.dumps({"outcomes": doc.get("outcomes"), "multiplicity": doc.get("multiplicity")})
    found = {}
    for key, value in MARGINS.items():
        spelled = ("1,000" if value == 1000 else f"{value:g}".lstrip("-"))
        found[key] = spelled in text or f"{value}" in text
    return found


def read_candidate_source(source: Path) -> dict:
    """A built candidate package as the study records it: ``source`` is the builder's output
    folder (``candidate.json`` beside ``<family>.easi-method.zip``) or a package zip. The zip
    is read and verified by EASI's ``method_package``; the record (when there is one) must
    name the zip's package digest."""
    from easi import method_package as mp
    source = Path(source)
    record = None
    if source.is_dir():
        record_path = source / "candidate.json"
        if not record_path.is_file():
            raise ValueError(f"{source} has no candidate.json (a candidate is a builder output folder or a zip)")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        zips = sorted(source.glob("*.easi-method.zip"))
        wanted = (record.get("candidate") or {}).get("zip")
        zip_path = source / wanted if wanted and (source / wanted).is_file() else (zips[0] if len(zips) == 1 else None)
        if zip_path is None:
            raise ValueError(f"{source} holds no single method package zip")
    elif source.is_file():
        zip_path = source
        beside = source.with_name("candidate.json")
        if beside.is_file():
            record = json.loads(beside.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"candidate not found: {source}")
    blob = zip_path.read_bytes()
    pkg = mp.read_package(blob)
    zip_sha = hashlib.sha256(blob).hexdigest()
    curves = json.loads(pkg.files["reference-curves.json"].decode("utf-8"))
    curve_count = sum(len(s.get("curves") or {}) for s in (curves.get("sets") or {}).values())
    identity = pkg.identity
    if record is not None:
        recorded = (record.get("candidate") or {}).get("packageDigest")
        if recorded and recorded != pkg.digest:
            raise ValueError(f"{source}: candidate.json names package {recorded[:15]} but the zip is {pkg.digest[:15]}")
    family = (record or {}).get("family")
    # the refit's own findings the study reports (E2: XER's degenerate perennial pool, served
    # by the national fallback): every stratum of a candidate set that gave no usable curve
    diagnostics = (((record or {}).get("curveSets") or {}).get("provenance") or {}).get("diagnostics") or {}
    findings = {}
    for set_id, item in diagnostics.items():
        if set_id in (curves.get("sets") or {}) and (item.get("notUsable") or item.get("split")):
            findings[set_id] = {"not_usable": dict(item.get("notUsable") or {}), "split": item.get("split"),
                                "served_by": "the national fallback" if item.get("notUsable") else None}
    return {
        "source": str(source), "zip": str(zip_path), "zip_sha256": zip_sha, "zip_bytes": len(blob),
        "curve_set_findings": findings,
        "record": record, "family": family, "label": pkg.envelope.get("label"),
        "package_digest": pkg.digest, "method_version": identity.get("methodVersion"),
        "evaluator_digest": identity.get("evaluatorDigest"),
        "validated_under": list(identity.get("validatedUnder") or []),
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(pkg.files.items())},
        "curve_count": curve_count,
        "curve_sets": {sid: {"quantity": s.get("quantity"), "stratifier": s.get("stratifier"),
                             "curves": sorted(s.get("curves") or {}),
                             **({"split": s["split"]} if s.get("split") else {})}
                       for sid, s in sorted((curves.get("sets") or {}).items())},
        "requires": (pkg.envelope.get("evaluator") or {}).get("requires"),
        "base": (record or {}).get("base"),
        "respecified": (record or {}).get("respecified"),
    }


__all__ = ["ADDENDUM_FILE", "ADDENDUM_PATH", "PROSE_PATH", "ADDENDUM_SHA256", "FAMILIES", "T1", "T2", "T3",
           "TARGET_NAMES", "DECIDING_COHORT", "REPORTED_COHORTS", "RETROSPECTIVE_COHORT", "NEVER_BLOCK_TARGETS",
           "EXPLORATORY_FIELD_TARGETS", "MARGINS", "AddendumError", "addendum_sha256", "addendum",
           "respecifications", "family_spec", "function_ids", "deciding_targets", "comparison_scope",
           "margins_in_addendum", "read_candidate_source"]
