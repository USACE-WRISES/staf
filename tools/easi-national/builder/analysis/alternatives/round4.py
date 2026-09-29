"""The EASI addendum (Round 4 of the national campaign) as the study runner reads it.

The yaml ``evaluation_protocol_v1_easi_addendum.yaml`` is frozen (its sha256 is checked
before anything is read from it) and names the eight candidate families, the outcomes P1 to
P6 and their margins. This module gives the runner the family record of a built candidate
package, the deciding targets and functions of a family, the margins as numbers (checked
against the yaml's own text by ``margins_in_addendum``), and the reader of a candidate
package folder (``candidate.json`` beside ``<family>.easi-method.zip``) or a bare zip.

The addendum's "Finalist" step composes the accepted families into one package (candidate
``FINALIST``, ``COMPOSITION``): ``composition_spec`` gives the runner its record from the
components the package names (their order the addendum's, the union of their functions,
decision ``composition``, primary outcome P1, each component's own spec under
``components``); ``arm_spec`` resolves either kind of arm from a manifest entry.

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
#: the id of a composed package (the addendum's "Finalist" step) and its family name; the
#: components are the accepted families the package's candidate.json names (``composition``)
COMPOSITION = "FINALIST"
COMPOSITION_FAMILY_NAME = "finalist_composition"
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


#: The owner's adoption refinements (Addendum 2 of the prose addendum, 2026-09-28): each rides
#: its parent family's mechanism with a stated change, exists after the families' results were
#: read, and is evaluated by the protocol's outcomes under the parent's decision rule. The
#: ids and parents are pinned here; the record's text is StreamCurves' (``round4.REFINEMENTS``),
#: the builder that writes the package, so the two never drift (a test keeps them equal).
REFINEMENT_PARENTS = {"E5b": "E5"}
REFINEMENT_IDS = tuple(REFINEMENT_PARENTS)
#: the keys a refinement takes from its own record; every other key is the parent's
REFINEMENT_TEXT_KEYS = ("family", "change", "mechanism", "hypothesis", "coverage_effect")


def refinements() -> dict:
    """The refinement records StreamCurves' builder writes into a package (``round4.REFINEMENTS``);
    empty when StreamCurves is not importable, and a refinement's spec is then refused."""
    try:
        from streamcurves.easi_method import round4 as sc  # noqa: WPS433 - the builder's peer app
    except Exception:  # noqa: BLE001
        return {}
    return {k: dict(v) for k, v in getattr(sc, "REFINEMENTS", {}).items()}


def refinement_spec(family: str, path: Path = ADDENDUM_PATH) -> dict:
    """The record of an owner's adoption refinement: its parent's addendum record with the
    refinement's own family name, change, mechanism, hypothesis and coverage effect, the
    parent's function, primary outcome and decision rule, and a ``refinement`` block naming
    the parent, the date, the addendum and that it came after the results."""
    if family not in REFINEMENT_PARENTS:
        raise AddendumError(f"unknown refinement {family!r}; the refinements are {', '.join(REFINEMENT_IDS)}")
    ref = refinements().get(family)
    if not ref:
        raise AddendumError(f"{family}: the refinement's record is StreamCurves' (streamcurves.easi_method.round4"
                            ".REFINEMENTS), which is not importable here")
    if ref.get("parent") != REFINEMENT_PARENTS[family]:
        raise AddendumError(f"{family}: StreamCurves records parent {ref.get('parent')!r}, the runner "
                            f"{REFINEMENT_PARENTS[family]!r}")
    parent = family_spec(ref["parent"], path)
    out = dict(parent, id=family)
    for key in REFINEMENT_TEXT_KEYS:
        out[key] = ref[key]
    out.pop("supporting", None)
    out["refinement"] = {"parent": ref["parent"], "date": ref["date"], "addendum": ref["addendum"],
                         "decisionRecord": ref.get("decision_record"), "curveSet": ref.get("curveSet"),
                         "curves": ref.get("curves"), "summary": ref.get("summary"), "afterResults": True}
    return out


def family_spec(family: str, path: Path = ADDENDUM_PATH, *, composition=None) -> dict:
    """The addendum's record of one family plus its dated re-specification when one exists;
    for the composition (``COMPOSITION``) the record ``composition_spec`` builds from the
    components given (a composition without its components is refused); for an owner's
    adoption refinement (``REFINEMENT_IDS``) the record ``refinement_spec`` builds from its
    parent's."""
    if family == COMPOSITION:
        if not composition:
            raise AddendumError(f"{COMPOSITION} is a composition: name its components (a built package "
                                "records them in candidate.json under 'composition')")
        return composition_spec(composition, path)
    if composition:
        raise AddendumError(f"{family} is one family of the addendum; a composition is candidate {COMPOSITION}")
    if family in REFINEMENT_PARENTS:
        return refinement_spec(family, path)
    if family not in FAMILIES:
        raise AddendumError(f"unknown family {family!r}; the addendum names {', '.join(FAMILIES)}"
                            f" and the refinements are {', '.join(REFINEMENT_IDS)}")
    spec = (addendum(path).get("candidates") or {}).get(family)
    if not isinstance(spec, dict):
        raise AddendumError(f"the addendum has no candidate {family}")
    out = dict(spec, id=family)
    respec = respecifications().get(family)
    if respec:
        out["respecified"] = respec
    return out


def composition_members(families) -> list[str]:
    """The components of a composition in the addendum's order: at least two distinct
    families of the addendum, however they were given."""
    given = [str(f) for f in (families or ())]
    if not given:
        raise AddendumError("a composition names its component families; none was given")
    unknown = [f for f in given if f not in FAMILIES]
    if unknown:
        raise AddendumError(f"a composition is made of the addendum's families {', '.join(FAMILIES)}; "
                            f"got {', '.join(unknown)}")
    if len(set(given)) != len(given):
        raise AddendumError(f"a family enters a composition once; got {', '.join(given)}")
    if len(given) < 2:
        raise AddendumError(f"a composition names at least two families; {given[0]} alone is its own study")
    return [f for f in FAMILIES if f in given]


def composition_spec(families, path: Path = ADDENDUM_PATH) -> dict:
    """The runner's record of a composition (the addendum's "Finalist" step): the components
    in the addendum's order, the union of their functions, their texts joined, decision
    ``composition`` (every component's own rule, read on the composed arm), primary outcome
    P1, and each component's own spec under ``components``. The same record StreamCurves'
    builder writes into the package (``streamcurves.easi_method.round4.composition_spec``)."""
    members = composition_members(families)
    components = {f: family_spec(f, path) for f in members}
    functions: list = []
    for spec in components.values():
        raw = spec.get("function")
        for f in (raw if isinstance(raw, list) else [raw]):
            if f not in functions:
                functions.append(f)
    joined = lambda key: "; ".join(f"{f}: {components[f].get(key)}" for f in members)  # noqa: E731
    out = {
        "id": COMPOSITION, "family": COMPOSITION_FAMILY_NAME, "composition": members,
        "function": "all" if "all" in functions else functions,
        "change": ("The finalist composition of the accepted Round 4 families, each applied as its own "
                   "edit to the base method in the addendum's order. " + joined("change")),
        "mechanism": joined("mechanism"),
        "hypothesis": ("H-FINALIST the accepted changes compose without interaction: the composed "
                       "method keeps the association each single-family study found and its rating "
                       "availability changes by their documented gaps only"),
        "primary_outcome": "P1",
        "decision": "composition",
        "coverage_effect": joined("coverage_effect"),
        "components": components,
    }
    respecified = {f: dict(s["respecified"]) for f, s in components.items() if s.get("respecified")}
    if respecified:
        out["respecified"] = respecified
    return out


def arm_spec(entry: dict, path: Path = ADDENDUM_PATH) -> dict:
    """The spec of a study arm from its manifest entry: a family's, or the composition's from
    the components the entry (or its candidate.json record) names."""
    composition = entry.get("composition") or ((entry.get("record") or {}).get("composition"))
    return family_spec(str(entry["id"]), path, composition=composition)


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
    (the 2013-14 designations) is taken as the primary, recorded as such in the summary. A
    composition's deciding targets are the union of its components' (each component's own
    rule reads its own targets on the composed arm)."""
    if spec.get("components"):
        union = [t for comp in spec["components"].values() for t in deciding_targets(comp)[1]]
        return "T1", [t for t in ("T1", "T2") if t in union]
    text = str(spec.get("primary_outcome") or "")
    named = re.findall(r"\bT([12])\b", text.split("(")[0])
    if named:
        primary = "T" + named[0]
        return primary, [primary]
    return "T1", ["T1", "T2"]


def comparison_scope(spec: dict) -> Optional[dict]:
    """The rows a candidate's P1 and P2 comparisons are paired on, when a re-specification
    narrows them (E2, Addendum 1: reaches the candidate rates the family function on):
    ``{"function": <snake_case function>, "rule": "candidate-rated"}`` or None. A composition
    takes its one re-specified component's scope; two components each narrowing the
    comparison would need a rule the addendum does not give, and are refused."""
    if spec.get("components"):
        scopes = {fid: comparison_scope(comp) for fid, comp in spec["components"].items()}
        scopes = {fid: s for fid, s in scopes.items() if s}
        if not scopes:
            return None
        if len(scopes) > 1:
            raise AddendumError("the runner pairs a candidate's comparisons on one scope; components "
                                f"{', '.join(sorted(scopes))} each narrow it")
        (fid, scope), = scopes.items()
        return {**scope, "component": fid}
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
        # a composed package names its components (the addendum's "Finalist" step)
        "composition": (record or {}).get("composition"),
        "components": (record or {}).get("components"),
    }


__all__ = ["ADDENDUM_FILE", "ADDENDUM_PATH", "PROSE_PATH", "ADDENDUM_SHA256", "FAMILIES", "COMPOSITION",
           "COMPOSITION_FAMILY_NAME", "REFINEMENT_PARENTS", "REFINEMENT_IDS", "T1", "T2", "T3", "TARGET_NAMES",
           "DECIDING_COHORT", "REPORTED_COHORTS",
           "RETROSPECTIVE_COHORT", "NEVER_BLOCK_TARGETS", "EXPLORATORY_FIELD_TARGETS", "MARGINS", "AddendumError",
           "addendum_sha256", "addendum", "respecifications", "refinements", "refinement_spec", "family_spec",
           "composition_members",
           "composition_spec", "arm_spec", "function_ids", "deciding_targets", "comparison_scope",
           "margins_in_addendum", "read_candidate_source"]
