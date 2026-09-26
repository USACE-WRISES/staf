"""The Round 4 candidate families of the EASI addendum as method-file edits.

``config/methodology/evaluation_protocol_v1_easi_addendum.yaml`` (frozen; its sha256 is
``ADDENDUM_SHA256`` and is checked before anything is built) names eight one-change
families against the base method. ``build_candidate`` applies one family to a base file set
(the eight method files, byte for byte) and returns the variant files with the identity file
restamped, the edits it made, and the family's text from the addendum; the CLI
``scripts/build_easi_candidate_package.py`` writes the folder, the ``EASI_METHOD_PACKAGE``
zip and ``candidate.json``. Nothing here reads or writes ``apps/easi/data``.

Each family rides one engine knob of the vendored EASI evaluator (WP-R4k):

- E1 and E5: an ``applicability`` rule on a method (K1), E5 on the cross-section quality
  flags the 3DEP provider exposes (K2);
- E2 and E4: a method variant reading a refit registry quantity (``flowMinRatio`` against
  the ``flow-min-ratio`` set, ``widthCv`` against ``width-variability``) that the refit
  campaign writes (K3, ``refit.candidate_curves``); both refuse to build without the set;
- E3 and E6: the existing ``unscored`` operator with a ``statement`` (a documented gap);
- E7: the ``mean_index`` operator on the five composite methods (K4);
- E8: the catalog's ``rollupReporting`` flag (K5).

Every string a person reads is plain ASCII with no em dash. The label of a built package is
the rehearsal label: no approval and no reviewer is recorded here.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Optional

from .._vendor.easi import method_package as mp
from .edit import dump_like

ADDENDUM_SHA256 = "8a48de98a4205c5eab1e423e69fdce2f238a76df8cbafca860bb032696625e6a"
ADDENDUM_FILE = "evaluation_protocol_v1_easi_addendum.yaml"
ADDENDUM_PATH = Path(__file__).resolve().parents[2] / "config" / "methodology" / ADDENDUM_FILE
REFIT_COMMAND = ("python apps/stream-curves/scripts/refit_easi_candidate_sets.py "
                 "--members <easi-dev-members folder> --campaign <folder> --out <curve-sets.json>")
FAMILIES = ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8")
CATALOG, CURVES, IDENTITY = "screening-methods.json", "reference-curves.json", "scoring-identity.json"
#: the curve set each refit family needs, and the record's input that reads it
FAMILY_SETS = {"E2": "flow-min-ratio", "E4": "width-variability"}
COMPOSITES = ("catchment-land-cover-pressure", "sediment-supply-potential",
              "thermal-regulation-vulnerability", "channel-adjustment-susceptibility",
              "hyporheic-exchange-potential")
CROSS_SECTION_METHODS = ("bank-height-ratio", "entrenchment-ratio",
                         "bhr-bank-instability-susceptibility", "channel-adjustment-susceptibility")

E1_STATEMENT = ("Naturally intermittent or ephemeral reach (NHDPlus FCODE {value}); low-flow "
                "condition is not rated from flow variability.")
E3_STATEMENT = ("Population support is not rated where the benthic model has no value: the ICI and "
                "IWI landscape fallback reuses landscape evidence rated by other functions and is "
                "withheld (candidate E3).")
E5_STATEMENT = ("3DEP cross-section geometry withheld ({value}); the ratio is not rated from "
                "uncertain DEM geometry (candidate E5).")
E6_STATEMENT = ("Bed composition is not rated: watershed agricultural cover enters the assessment "
                "once, through sediment supply and catchment hydrology (candidate E6).")
E7_LIMITATION = ("Candidate E7: the composite index is the mean of the inputs' rating indices, "
                 "banded at 0.39 and 0.69; no single input decides the rating.")
REFERENCE_BREAKPOINTS = [
    {"description": "Fair begins at an interpolated reference index of 0.39.",
     "label": "Reference index 0.39"},
    {"description": "Good begins at an interpolated reference index of 0.69. Physical crossings "
                    "depend on the reference stratum.",
     "label": "Reference index 0.69"}]


class CandidateError(ValueError):
    """A family that cannot be built from what it was given; nothing is written."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def addendum_sha256(path: Path = ADDENDUM_PATH) -> str:
    return _sha(Path(path).read_bytes())


def addendum(path: Path = ADDENDUM_PATH) -> dict:
    """The frozen addendum, parsed, after its bytes are checked against the recorded sha."""
    import yaml
    got = addendum_sha256(path)
    if got != ADDENDUM_SHA256:
        raise CandidateError(f"{Path(path).name} is not the frozen addendum (sha256 {got[:12]}, "
                             f"expected {ADDENDUM_SHA256[:12]}); nothing is built from an "
                             "edited protocol")
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def family_spec(family: str, path: Path = ADDENDUM_PATH) -> dict:
    """The addendum's record of one family (its name, function, change, mechanism)."""
    if family not in FAMILIES:
        raise CandidateError(f"unknown family {family!r}; the addendum names {', '.join(FAMILIES)}")
    doc = addendum(path)
    spec = (doc.get("candidates") or {}).get(family)
    if not isinstance(spec, dict):
        raise CandidateError(f"the addendum has no candidate {family}")
    return dict(spec, id=family)


# --------------------------------------------------------------------------- #
# the edits, on parsed copies of the two files
# --------------------------------------------------------------------------- #
def _method(cat: dict, key: str) -> dict:
    for m in cat.get("methods", []):
        if m.get("methodKey") == key:
            return m
        for v in m.get("variants") or []:
            if v.get("methodKey") == key:
                return v
    raise CandidateError(f"the base catalog has no method {key!r}")


def _input(method: dict, key: str) -> dict:
    for i in method.get("inputs") or []:
        if i.get("key") == key:
            return i
    raise CandidateError(f"method {method.get('methodKey')!r} has no input {key!r}")


def _drop_limitations(method: dict, *needles: str) -> list[str]:
    kept, dropped = [], []
    for text in method.get("limitations") or []:
        if any(n.lower() in str(text).lower() for n in needles):
            dropped.append(text)
        else:
            kept.append(text)
    method["limitations"] = kept
    return dropped


def _e1(cat: dict, curves: dict, sets: Optional[dict]) -> list[dict]:
    m = _method(cat, "erom-flow-variability")
    rule = {"input": "fcodeContext", "exclude": ["46003", "46007"], "statement": E1_STATEMENT}
    m["applicability"] = rule
    _input(m, "fcodeContext")["rationale"] = (
        "Flow classification provides context for natural intermittency. Under candidate E1 an "
        "intermittent (46003) or ephemeral (46007) classification withholds the rating as a "
        "documented gap; a perennial reach keeps the flow-variability rating.")
    limitation = ("Candidate E1: naturally intermittent (FCODE 46003) and ephemeral (FCODE 46007) "
                  "reaches are not rated; low-flow condition is a documented gap there.")
    m.setdefault("limitations", []).append(limitation)
    return [{"methodKey": m["methodKey"], "field": "applicability", "after": rule},
            {"methodKey": m["methodKey"], "field": "limitations", "appended": limitation}]


def _require_set(family: str, sets: Optional[dict]) -> dict:
    set_id = FAMILY_SETS[family]
    definition = (sets or {}).get(set_id)
    if not isinstance(definition, dict) or "national" not in (definition.get("curves") or {}):
        raise CandidateError(
            f"{family} needs the {set_id} curve set (a national curve and the NARS-9 fits of "
            f"{'q_min_ratio' if family == 'E2' else 'bankfull_width_cv'} refit on the strict "
            f"panels); none was given. Run the refit campaign first, then pass its output with "
            f"--curve-sets:\n  {REFIT_COMMAND}")
    return copy.deepcopy(definition)


def _e2(cat: dict, curves: dict, sets: Optional[dict]) -> list[dict]:
    definition = _require_set("E2", sets)
    m = _method(cat, "erom-flow-variability")
    before = copy.deepcopy(m)
    context = [i for i in m.get("inputs") or [] if i.get("contextOnly")]
    m.update({
        "methodKey": "erom-flow-min-ratio",
        "title": "Low-flow condition (minimum-month flow ratio)",
        "curve": {"fallback": ["national"], "mode": "banded", "set": "flow-min-ratio",
                  "stratifier": "nars9"},
        "breakpoints": copy.deepcopy(REFERENCE_BREAKPOINTS),
        "inputs": [{"key": "flowMinRatio", "label": "Minimum-month flow ratio",
                    "rationale": "The lowest of the twelve EROM mean monthly flows over the mean "
                                 "annual flow, a low-flow fraction. All twelve months must be "
                                 "finite and the mean annual flow positive.",
                    "required": True, "slider": {"max": 1, "min": 0, "step": 0.01},
                    "sourceField": "NHDPlus V2 EROM QE_01 through QE_12 over QE_MA",
                    "symbol": "Qmin/Qma", "units": "ratio"}, *context],
        "limitations": [
            "Candidate low-flow proxy (E2): the minimum-month over annual-mean EROM ratio, higher "
            "is better, replaces monthly flow variability.",
            "Modeled monthly flows are not a measurement of daily low flow, baseflow contribution "
            "or wetted connectivity.",
            "NARS-9 curves compare the ratio with regional reference expectations; a national "
            "curve is the fallback. Natural intermittent and ephemeral streams require "
            "interpretation.",
            "All twelve months and a positive mean annual flow are required. Missing months are "
            "unknown, not zero."],
        "plot": {"direction": "higher_better", "domain": [0, 1], "mode": "scalar"},
        "sourceHierarchy": [{
            "description": "Use the lowest of the twelve modeled monthly flows over the mean "
                           "annual flow; a missing month or a nonpositive mean annual flow "
                           "remains unscored.",
            "label": "EROM minimum-month flow ratio", "methodKey": "erom-flow-min-ratio"}],
    })
    curves.setdefault("sets", {})["flow-min-ratio"] = definition
    return [{"methodKey": "erom-flow-min-ratio", "field": "method", "replaces": before["methodKey"],
             "reads": "flowMinRatio", "curveSet": "flow-min-ratio"},
            {"file": CURVES, "set": "flow-min-ratio", "curves": sorted(definition["curves"])}]


def _e3(cat: dict, curves: dict, sets: Optional[dict]) -> list[dict]:
    parent = _method(cat, "streamcat-prg-bmmi")
    v = _method(cat, "streamcat-integrity-products")
    v["operator"] = "unscored"
    v["statement"] = E3_STATEMENT
    v.pop("formula", None)        # the variant keeps its own bands: a resolved variant never
    v["breakpoints"] = []         # inherits the parent's rule, and unscored rates nothing
    v["plot"] = {"mode": "unscored"}
    v["limitations"] = [
        "Candidate E3: the ICI and IWI landscape fallback is withheld; population support is a "
        "documented gap where the benthic model has no value.",
        "ICI/IWI are landscape integrity products that reuse landscape evidence scored elsewhere "
        "in EASI; they are not measured biological assemblage condition."]
    for tier in parent.get("sourceHierarchy") or []:
        if tier.get("methodKey") == "streamcat-integrity-products":
            tier["description"] = ("When the BMMI model has no value the function is not rated "
                                   "(candidate E3): the landscape fallback is withheld.")
            tier["label"] = "Landscape fallback withheld"
    _drop_limitations(parent, "ICI/IWI fallback remains", "integrity fallback reuses")
    parent.setdefault("limitations", []).append(
        "Candidate E3: the model is unavailable for about 59 percent of analyzed reaches, which "
        "are not rated; the ICI/IWI landscape fallback is withheld.")
    return [{"methodKey": "streamcat-integrity-products", "field": "operator", "after": "unscored",
             "statement": E3_STATEMENT},
            {"methodKey": "streamcat-prg-bmmi", "field": "sourceHierarchy", "tier": "streamcat-integrity-products",
             "after": "withheld"}]


def _e4(cat: dict, curves: dict, sets: Optional[dict]) -> list[dict]:
    definition = _require_set("E4", sets)
    m = _method(cat, "habitat-support-potential")
    before = copy.deepcopy(m)
    context = [i for i in m.get("inputs") or [] if i.get("contextOnly")]
    m.update({
        "methodKey": "habitat-width-variability",
        "title": "Habitat-support potential (bankfull width variability)",
        "curve": {"fallback": ["national"], "mode": "banded", "set": "width-variability",
                  "stratifier": "nars9"},
        "breakpoints": copy.deepcopy(REFERENCE_BREAKPOINTS),
        "inputs": [{"key": "widthCv", "label": "Bankfull width variability",
                    "rationale": "The variability of the DEM-derived bankfull width across the "
                                 "sampled cross-sections (coefficient of variation): a channel-form "
                                 "construct of in-stream habitat complexity, higher is better.",
                    "required": True, "slider": {"max": 1.5, "min": 0, "step": 0.01},
                    "sourceField": "USGS 3DEP reach cross-sections, bankfull width per section",
                    "symbol": "CVw", "units": "ratio"}, *context],
        "limitations": [
            "Candidate E4: bankfull width variability from the 3DEP cross-sections replaces "
            "corridor woody cover; reaches without a cross-section set lose the rating.",
            "A channel-form proxy for habitat complexity, not a field inventory of pools, wood, "
            "cover or bedforms; DEM resolution and the regional bankfull estimate set its precision.",
            "NARS-9 reference curves account for regional expectations; a national curve is used "
            "when the regional curve is unavailable. Sinuosity is context only."],
        "plot": {"direction": "higher_better", "domain": [0, 1.5], "mode": "scalar"},
    })
    curves.setdefault("sets", {})["width-variability"] = definition
    return [{"methodKey": "habitat-width-variability", "field": "method", "replaces": before["methodKey"],
             "reads": "widthCv", "curveSet": "width-variability"},
            {"file": CURVES, "set": "width-variability", "curves": sorted(definition["curves"])}]


def _e5(cat: dict, curves: dict, sets: Optional[dict]) -> list[dict]:
    rule = {"evidence": "crossSectionQuality", "withhold_when": ["low_quality", "out_of_range"],
            "statement": E5_STATEMENT}
    limitation = ("Candidate E5: the rating is withheld where the 3DEP cross-section record is "
                  "flagged low quality (bank detection, valid sections) or the ratio is outside its "
                  "physical range (0 < BHR <= 2, ER >= 1); geometry is then a documented gap.")
    edits = []
    for key in CROSS_SECTION_METHODS:
        m = _method(cat, key)
        m["applicability"] = copy.deepcopy(rule)
        m.setdefault("limitations", []).append(limitation)
        edits.append({"methodKey": key, "field": "applicability", "after": rule})
    return edits


def _e6(cat: dict, curves: dict, sets: Optional[dict]) -> list[dict]:
    sediment = _method(cat, "sediment-supply-potential")
    sediment["inputs"] = [i for i in sediment.get("inputs") or [] if i.get("key") != "agriculture"]
    sediment["breakpoints"] = [b for b in sediment.get("breakpoints") or []
                               if b.get("input") != "agriculture"]
    sediment["citations"] = [c for c in sediment.get("citations") or []
                             if c not in ("allan-2004", "wang-1997")]
    _drop_limitations(sediment, "Agricultural cover and road density", "All three inputs")
    sediment.setdefault("limitations", []).extend([
        "Candidate E6: agricultural cover enters the assessment once (catchment hydrology); "
        "sediment supply rates the soil K-factor and road density only.",
        "Road density is also rated by reach inflow and is therefore correlated evidence.",
        "Both inputs are required. Missing inputs are not converted to zero."])
    substrate = _method(cat, "watershed-agriculture-share")
    substrate["operator"] = "unscored"
    substrate["statement"] = E6_STATEMENT
    for key in ("bands", "curve"):
        substrate.pop(key, None)
    substrate["breakpoints"] = []
    substrate["plot"] = {"mode": "unscored"}
    ag = _input(substrate, "agriculture")
    ag["contextOnly"] = True
    ag["required"] = False
    ag["rationale"] = ("Watershed agricultural cover, shown for context. Under candidate E6 it is "
                       "rated once, by catchment hydrology, and bed composition is not rated.")
    substrate["limitations"] = [
        "Candidate E6: bed composition is a documented gap everywhere; the agriculture-share proxy "
        "is withheld so that watershed agriculture enters the assessment once.",
        "Watershed agriculture never measured bed composition, embeddedness or large wood."]
    for tier in substrate.get("sourceHierarchy") or []:
        if tier.get("methodKey") == "watershed-agriculture-share":
            tier["description"] = "Withheld (candidate E6): bed composition is not rated."
            tier["label"] = "Agriculture-share proxy withheld"
    return [{"methodKey": "sediment-supply-potential", "field": "inputs", "removed": "agriculture"},
            {"methodKey": "watershed-agriculture-share", "field": "operator", "after": "unscored",
             "statement": E6_STATEMENT}]


def _e7(cat: dict, curves: dict, sets: Optional[dict]) -> list[dict]:
    edits = []
    for key in COMPOSITES:
        m = _method(cat, key)
        before = m.get("operator")
        m["operator"] = "mean_index"
        plot = dict(m.get("plot") or {})
        plot["mode"] = "mean_index"
        m["plot"] = plot
        _drop_limitations(m, "governs", "better-pathway", "better pathway")
        m.setdefault("limitations", []).append(E7_LIMITATION)
        for tier in m.get("sourceHierarchy") or []:
            if tier.get("methodKey") == key and "governs" in str(tier.get("description") or ""):
                tier["description"] = ("For other reaches, the mean of the BHR and ER indices rates "
                                       "the function (candidate E7).")
        if key == "hyporheic-exchange-potential":
            # the base rationale says an understated sinuosity can never lower the rating,
            # which held under the better-pathway rule only
            _input(m, "sinuosity")["rationale"] = (
                "Sinuosity indicates lateral exchange through meander necks and point bars, the "
                "dominant hyporheic pathway in low-gradient meandering channels. Under candidate "
                "E7 its index enters the mean with the slope index, so sinuosity understated by "
                "generalized reach geometry can lower the rating.")
        edits.append({"methodKey": key, "field": "operator", "before": before, "after": "mean_index"})
    return edits


def _e8(cat: dict, curves: dict, sets: Optional[dict]) -> list[dict]:
    cat["rollupReporting"] = True
    return [{"file": CATALOG, "field": "rollupReporting", "after": True,
             "reports": ["functionsRated", "ecosystemConditionIndexInterval"]}]


EDITS = {"E1": _e1, "E2": _e2, "E3": _e3, "E4": _e4, "E5": _e5, "E6": _e6, "E7": _e7, "E8": _e8}


# --------------------------------------------------------------------------- #
# a family applied to a base file set
# --------------------------------------------------------------------------- #
def base_files(source: Path) -> dict[str, bytes]:
    """The eight method files of a base: a folder holding them, a published library version
    folder (its ``method/`` subfolder), or a method package zip (read and verified)."""
    source = Path(source)
    if source.is_file():
        return dict(mp.read_package(source).files)
    if not source.is_dir():
        raise CandidateError(f"base not found: {source}")
    folder = source / mp.METHOD_DIR if (source / mp.METHOD_DIR).is_dir() else source
    missing = [n for n in mp.METHOD_FILES if not (folder / n).is_file()]
    if missing:
        raise CandidateError(f"{folder} lacks method files: {missing}")
    return {n: (folder / n).read_bytes() for n in mp.METHOD_FILES}


def recorded_base_identity(source: Path) -> dict:
    """What the base records about itself: the envelope (``method.json``) of a version folder
    or a zip names the method version validated under its evaluator; a bare folder names
    nothing, and the version is then only computed under this evaluator."""
    source = Path(source)
    envelope = None
    if source.is_file():
        envelope = mp.read_package(source).envelope
    elif (source / mp.ENVELOPE).is_file():
        envelope = json.loads((source / mp.ENVELOPE).read_text(encoding="utf-8"))
    if not envelope:
        return {}
    ident = envelope.get("identity") or {}
    return {"methodVersion": ident.get("methodVersion"), "evaluatorDigest": ident.get("evaluatorDigest"),
            "packageDigest": ident.get("packageDigest"), "label": envelope.get("label"),
            "version": envelope.get("version")}


def _restamp(files: dict[str, bytes], family: str, spec: dict) -> dict:
    raw = files[IDENTITY]
    ident = json.loads(raw.decode("utf-8"))
    base = {k: ident.get(k) for k in ("alternative_id", "catalog_sha256", "curves_sha256")
            if ident.get(k)}
    curves = json.loads(files[CURVES].decode("utf-8"))
    ident["catalog_sha256"] = _sha(files[CATALOG])
    ident["curves_sha256"] = _sha(files[CURVES])
    ident["nars_geography_sha256"] = _sha(files["nars-ecoregions-9.geojson.gz"])
    ident["curve_count"] = sum(len(s.get("curves") or {}) for s in (curves.get("sets") or {}).values())
    ident["alternative_id"] = f"round4-{family}"
    ident["alternative_name"] = f"Round 4 candidate {family} ({spec.get('family')}), rehearsal"
    ident["derived_from"] = base
    files[IDENTITY] = dump_like(raw, ident)
    return ident


def build_candidate(files: dict[str, bytes], family: str, *, curve_sets: Optional[dict] = None,
                    addendum_path: Path = ADDENDUM_PATH) -> dict:
    """``{"files", "edits", "spec", "identity", "curveSets"}``: the family's variant method
    files (the edited catalog and curves written back in the base file's own style, the
    identity file restamped, the other five byte for byte), the edits applied, the family's
    addendum text and the identity of the result under this evaluator. ``curve_sets`` is the
    refit campaign's ``sets`` (``refit.candidate_curves``), needed by E2 and E4 only."""
    spec = family_spec(family, addendum_path)
    if set(files) != set(mp.METHOD_FILES):
        raise CandidateError("a base needs exactly the eight method files")
    out = dict(files)
    cat = json.loads(files[CATALOG].decode("utf-8"))
    curves = json.loads(files[CURVES].decode("utf-8"))
    edits = EDITS[family](cat, curves, curve_sets)
    out[CATALOG] = dump_like(files[CATALOG], cat)
    out[CURVES] = dump_like(files[CURVES], curves)
    if out[CATALOG] == files[CATALOG] and out[CURVES] == files[CURVES]:
        raise CandidateError(f"{family} changed nothing in the base files")
    identity = _restamp(out, family, spec)
    problems = mp.validate_files(out)
    if problems:
        raise CandidateError(f"{family}: the variant files are not consistent: " + "; ".join(problems[:6]))
    used = {}
    if family in FAMILY_SETS:
        set_id = FAMILY_SETS[family]
        used[set_id] = {"curves": sorted(curves["sets"][set_id]["curves"]),
                        "quantity": curves["sets"][set_id].get("quantity")}
    return {"files": out, "edits": edits, "spec": spec, "scoringIdentity": identity,
            "identity": {"methodVersion": mp.method_version_for("regional", out),
                         "packageDigest": mp.package_digest({n: _sha(b) for n, b in out.items()}),
                         "evaluatorDigest": mp.evaluator_digest()},
            "curveSets": used,
            "unchangedFiles": sorted(n for n in files if out[n] == files[n])}


def package(files: dict[str, bytes], family: str, spec: dict, *, version: int = 1) -> mp.MethodPackage:
    """The ``EASI_METHOD_PACKAGE`` of a built family: the envelope names the family with the
    rehearsal label; no calculator (none was generated from these files)."""
    label = f"Round 4 candidate {family}: {spec.get('family')} (rehearsal)"
    env = mp.build_envelope(files, method_id="easi-screening", version=int(version), label=label)
    return mp.MethodPackage(envelope=env, files=dict(files))


__all__ = ["ADDENDUM_SHA256", "ADDENDUM_PATH", "FAMILIES", "FAMILY_SETS", "COMPOSITES",
           "CROSS_SECTION_METHODS", "REFIT_COMMAND", "CandidateError", "addendum", "addendum_sha256",
           "family_spec", "base_files", "recorded_base_identity", "build_candidate", "package"]
