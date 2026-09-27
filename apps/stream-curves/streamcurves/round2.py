"""Round 2 of the national assessment campaign: the pure helpers behind the harnesses.

Pre-registration V (``evaluation_protocol_v1.yaml``) tests ten candidate changes to
methodology 0.14, one family at a time, against a full-refit baseline (A2). This
module holds everything the orchestrator (``scripts/run_round2.py``), the
association test (``scripts/run_association_test.py``) and the stability test
(``scripts/run_stability_test.py``) share, and nothing that touches the network or
runs a build:

* the protocol: its sha256 (every Round 2 output records it), its candidates, and
  the refusal when a recorded sha differs from the committed one;
* arm config roots: a copy of ``config/`` with one candidate's knob applied, the
  knob routed to the file that carries it, and ``arm.json`` recording what changed;
* candidate C4: the metric_map.yaml variant built from the evidence table's
  ``revise`` dispositions (:func:`apply_revise_dispositions`, every rule documented);
* the statistics: the Mann-Whitney area, Spearman's rho, cluster bootstraps, the
  paired delta between two arms on identical units with its interval, the paired
  AUC delta, a bootstrap p-value and Benjamini-Hochberg;
* the adoption rule per decision type, with the O2, O3 and O5 limits and the NARS-9
  subgroup block, every margin a constant stated before any result is read;
* DEEP's scoring arithmetic on a bundle (metric index by interpolation, the layer a
  station's slope or drainage area selects, function score, the ECI rollup with the
  coverage interval), so the association test scores stations exactly as DEEP does;
* the report tables.

Every number labelled an operating choice in the protocol is repeated here as a
constant and never derived from a result. No timestamps: a harness output is a
function of its inputs alone.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import zlib
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

import numpy as np
import pandas as pd
import yaml

from . import curves

PROTOCOL_SCHEMA = "evaluation-protocol/1"
#: The full-refit baseline every candidate is compared with (protocol sequence.round_a).
BASELINE_ARM = "A2"
FINALIST_ARM = "finalist"
#: What a rehearsal stage records as its maintainer: never an owner decision.
REHEARSAL_MAINTAINER = "Rehearsal (not an owner decision)"
ARM_FILE = "arm.json"
ARMS_DIR = "arms"
CONFIG_DIR_NAME = "config"

VERDICT_ADOPT = "adopt"
VERDICT_REJECT = "reject"
VERDICT_REFERENCE = "reference-only"
#: an arm whose primary outcome or a blocking outcome has no data: never adopted, never
#: rejected, evaluated again (C3b, 2026-09-26: the hierarchy harness crashed and the compare
#: step had written adopt on the stability outcome alone)
VERDICT_INCONCLUSIVE = "inconclusive"
#: ``composition`` is the finalist (``compose_finalist``): the accepted changes together,
#: checked once on the development cells the way a simplification is (noninferior on the
#: primary, every block in force), since each member was adopted on its own margin.
DECISION_TYPES = ("accuracy_change", "simplification", "coverage_only", "reference_arm", "composition")
OUTCOME_IDS = ("O1", "O2", "O3", "O4", "O5", "O6")

#: The margins of Pre-registration V, section 6, as constants. O1 and O3 name the
#: interval level of their paired delta; every number is the protocol's operating
#: choice and none of them moves after gate G2.
MARGINS: dict[str, dict] = {
    "O1": {"level": 0.90, "accuracy_change_min_delta": 0.05, "simplification_lower_bound": -0.05,
           "a1_exceedance": 0.10},
    "O2": {"max_net_optimism": 0.05, "max_worsening": 0.02},
    "O3": {"level": 0.95, "block_lower_bound": -0.01},
    "O5": {"level": 0.90, "max_rise": 0.02},
    "subgroups": {"max_loss": 0.10},
    "multiplicity": {"bh_q": 0.10},
}


class ProtocolMismatch(RuntimeError):
    """A recorded protocol sha differs from the committed protocol's."""


class KnobError(ValueError):
    """A candidate knob names something the generator cannot place."""


# --------------------------------------------------------------------------- #
# the protocol
# --------------------------------------------------------------------------- #
def sha256_of(path: Path | str) -> str:
    """Hex sha256 of a file's bytes (no prefix, as run_hierarchy_test records it)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_protocol(path: Path | str) -> dict:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict) or doc.get("schema") != PROTOCOL_SCHEMA:
        raise ValueError(f"{path} is not an evaluation protocol (schema {PROTOCOL_SCHEMA})")
    if not isinstance(doc.get("candidates"), dict) or not doc["candidates"]:
        raise ValueError(f"{path} names no candidates")
    return doc


def candidate_ids(protocol: dict) -> list[str]:
    return [str(k) for k in protocol["candidates"]]


def candidate(protocol: dict, arm_id: str) -> dict:
    cands = protocol["candidates"]
    if arm_id not in cands:
        raise KeyError(f"the protocol names no candidate {arm_id!r}; it names "
                       f"{', '.join(candidate_ids(protocol))} (and {BASELINE_ARM} is the baseline)")
    return dict(cands[arm_id])


def check_protocol(recorded: Mapping, protocol_sha: str, *, what: str) -> None:
    """Refuse when ``recorded`` (an arm.json or any Round 2 record) names another
    protocol than the committed one: a margin never moves after gate G2, so an arm
    built under one protocol is never read under another."""
    have = str(((recorded or {}).get("protocol") or {}).get("sha256") or "")
    if have != str(protocol_sha):
        raise ProtocolMismatch(
            f"{what} was recorded under protocol sha256 {have[:12] or 'none'}, the committed "
            f"protocol is {str(protocol_sha)[:12]}; refusing. Rebuild the arms under the "
            "committed protocol, or restore the protocol the arms were built under.")


def stamp(protocol_path: Path | str, *, config_root: Optional[Path | str],
          app_root: Optional[Path | str] = None, campaign: Optional[str] = None) -> dict:
    """What every Round 2 output records: the campaign id, the protocol's sha256, the
    config root the process read, and the code fingerprint."""
    from . import code_identity
    proto = load_protocol(protocol_path)
    return {
        "campaign": campaign or str(proto.get("campaign") or ""),
        "protocol": {"path": Path(protocol_path).name, "sha256": sha256_of(protocol_path),
                     "version": str(proto.get("protocol_version") or "")},
        "configRoot": (str(Path(config_root).resolve()).replace("\\", "/")
                       if config_root else None),
        "code": {"fingerprint": code_identity.fingerprint(app_root),
                 "scope": [f"{s}/{','.join(p)}" for s, p in code_identity.SCOPE]},
    }


# --------------------------------------------------------------------------- #
# arm config roots
# --------------------------------------------------------------------------- #
METHODOLOGY_FILE = "methodology/methodology_config.yaml"
TRANSFER_FILE = "reference_transfer.yaml"
METRIC_MAP_FILE = "metric_map.yaml"
EVIDENCE_FILE = "metric_evidence.yaml"
#: Protocol keys that are stage flags, not configuration: recorded in arm.json and
#: passed to ``run_region_batch.py stage-many`` by the orchestrator.
STAGE_KNOBS = ("refit",)
#: A protocol key whose engine key WP-R2a placed elsewhere (the protocol is the
#: authority for the candidate; the engine key is where the value lands).
KEY_ALIASES: dict[str, str] = {
    "regional_screen.enabled": "reference_hierarchy.regional_screen.enabled",
    "reference_pool.regional_screen.enabled": "reference_hierarchy.regional_screen.enabled",
    "portfolio.fill_to": "metric_portfolio.fill_to",
    "portfolio.second_metric_rule": "metric_portfolio.second_metric_rule",
    "portfolio.second_metric_max_abs_spearman": "metric_portfolio.second_metric_max_abs_spearman",
    "portfolio.second_metric_min_auc": "metric_portfolio.second_metric_min_auc",
}
#: A protocol value the engine spells differently, by (engine key, protocol value).
#: Empty today; one line here translates a value without touching a candidate.
VALUE_ALIASES: dict[tuple[str, Any], Any] = {}
#: Engine keys a protocol knob implies. C1 states the second-metric numbers; the
#: engine's rule switch that reads them (WP-R2a) is turned on beside them.
COMPANION_KNOBS: dict[str, dict[str, Any]] = {
    "portfolio.second_metric_max_abs_spearman": {
        "metric_portfolio.second_metric_rule": "independent_and_discriminating"},
    "portfolio.second_metric_min_auc": {
        "metric_portfolio.second_metric_rule": "independent_and_discriminating"},
    "metric_portfolio.second_metric_max_abs_spearman": {
        "metric_portfolio.second_metric_rule": "independent_and_discriminating"},
    "metric_portfolio.second_metric_min_auc": {
        "metric_portfolio.second_metric_rule": "independent_and_discriminating"},
}
METRIC_MAP_VARIANTS = ("evidence_table_revise_set",)
ABSENT = "<absent>"


def route_knob(key: str) -> dict:
    """Where a protocol knob lands: ``{"kind", "file", "path"}``. ``reference_transfer.*``
    keys land in reference_transfer.yaml, ``metric_map`` names a generated map
    variant, ``refit`` is a stage flag, and everything else is a dotted path in
    methodology_config.yaml (created when absent: WP-R2a owns the engine keys)."""
    key = str(key)
    if key in STAGE_KNOBS:
        return {"kind": "stage", "file": None, "path": key}
    if key == "metric_map":
        return {"kind": "metric_map", "file": METRIC_MAP_FILE, "path": key}
    engine = KEY_ALIASES.get(key, key)
    if engine.startswith("reference_transfer."):
        return {"kind": "transfer", "file": TRANSFER_FILE, "path": engine[len("reference_transfer."):]}
    if "." not in engine:
        raise KnobError(f"knob {key!r} is neither a dotted methodology key, a reference_transfer key, "
                        "a metric_map variant nor a stage flag")
    return {"kind": "methodology", "file": METHODOLOGY_FILE, "path": engine}


def get_dotted(doc: Any, path: str, default: Any = ABSENT) -> Any:
    node = doc
    for part in str(path).split("."):
        if not isinstance(node, Mapping) or part not in node:
            return default
        node = node[part]
    return node


def set_dotted(doc: dict, path: str, value: Any) -> Any:
    """Set a dotted path, creating intermediate maps; returns the previous value or
    :data:`ABSENT`."""
    parts = str(path).split(".")
    node = doc
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    previous = node.get(parts[-1], ABSENT)
    node[parts[-1]] = copy.deepcopy(value)
    return previous


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_yaml(path: Path, doc: dict, header: str = "") -> None:
    text = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, allow_unicode=False,
                          width=100)
    path.write_text((header + "\n" if header else "") + text, encoding="utf-8", newline="\n")


def config_tree_fingerprint(folder: Path | str) -> str:
    """Sha256 over every file under a config folder (relative POSIX path and bytes, in
    sorted order), the way code_identity fingerprints the app."""
    root = Path(folder)
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        h.update(p.relative_to(root).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def apply_knobs(config_root: Path | str, knobs: Mapping[str, Any]) -> list[dict]:
    """Apply a candidate's ``config`` block to the config root in place. Returns one
    record per change: the protocol key, the engine key, the file, the value and what
    was there before (``<absent>`` for a key the engine gains with WP-R2a)."""
    root = Path(config_root)
    docs: dict[str, dict] = {}
    applied: list[dict] = []

    def doc_of(rel: str) -> dict:
        if rel not in docs:
            p = root / rel
            if not p.is_file():
                raise KnobError(f"{p} is missing from the config root")
            docs[rel] = _read_yaml(p)
        return docs[rel]

    def place(protocol_key: str, engine_key: str, value: Any, origin: str) -> None:
        route = route_knob(engine_key) if origin == "companion" else route_knob(protocol_key)
        value = VALUE_ALIASES.get((route["path"], _hashable(value)), value)
        if route["kind"] == "stage":
            applied.append({"protocolKey": protocol_key, "engineKey": route["path"], "file": None,
                            "value": value, "previous": ABSENT, "origin": origin,
                            "note": "a stage flag of run_region_batch.py, not a configuration key"})
            return
        if route["kind"] == "metric_map":
            variant = str(value)
            if variant not in METRIC_MAP_VARIANTS:
                raise KnobError(f"unknown metric_map variant {variant!r}; known: "
                                f"{', '.join(METRIC_MAP_VARIANTS)}")
            mm_path, ev_path = root / METRIC_MAP_FILE, root / EVIDENCE_FILE
            before = sha256_of(mm_path)
            new_map, log = apply_revise_dispositions(_read_yaml(mm_path), _read_yaml(ev_path))
            mm_path.write_text(render_metric_map(new_map, log, evidence_sha=sha256_of(ev_path),
                                                 source_sha=before), encoding="utf-8", newline="\n")
            applied.append({"protocolKey": protocol_key, "engineKey": "metric_map", "file": METRIC_MAP_FILE,
                            "value": variant, "previous": f"sha256:{before}", "origin": origin,
                            "rules": [dict(r) for r in log]})
            return
        doc = doc_of(route["file"])
        previous = set_dotted(doc, route["path"], value)
        applied.append({"protocolKey": protocol_key, "engineKey": route["path"], "file": route["file"],
                        "value": value, "previous": previous, "origin": origin})

    for key, value in (knobs or {}).items():
        place(str(key), str(key), value, "protocol")
        for companion_key, companion_value in (COMPANION_KNOBS.get(str(key)) or {}).items():
            if any(r["engineKey"] == companion_key for r in applied):
                continue
            place(str(key), companion_key, companion_value, "companion")
    for rel, doc in docs.items():
        _write_yaml(root / rel, doc, header=_generated_header(rel, applied))
    return applied


def _hashable(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_hashable(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _hashable(v)) for k, v in value.items()))
    return value


def _generated_header(rel: str, applied: list[dict]) -> str:
    keys = [r["engineKey"] for r in applied if r.get("file") == rel]
    return ("# GENERATED by streamcurves.round2 for a Round 2 arm: a copy of the committed file\n"
            "# with these keys set (comments of the source are not carried): " + ", ".join(keys))


def arm_record(protocol_path: Path | str, arm_id: str, *, knobs: Mapping[str, Any],
               applied: list[dict], config_fingerprint: str, source_fingerprint: str,
               members: Optional[list[str]] = None) -> dict:
    """The ``arm.json`` of one arm: the protocol (sha256), the candidate's block, the knob
    and the baseline it changes, what was applied where, and the stage flags."""
    proto = load_protocol(protocol_path)
    cand = candidate(proto, arm_id) if arm_id in proto["candidates"] else {}
    stage_flags = {"refit": "all", "maintainer": REHEARSAL_MAINTAINER,
                   "value_policy": str((proto.get("baseline") or {}).get("value_policy_round2") or "")}
    for r in applied:
        if r.get("file") is None and r.get("engineKey") in STAGE_KNOBS:
            stage_flags[str(r["engineKey"])] = r["value"]
    return {
        "schema": "round2-arm/1",
        "arm": arm_id,
        "campaign": str(proto.get("campaign") or ""),
        "protocol": {"path": Path(protocol_path).name, "sha256": sha256_of(protocol_path),
                     "version": str(proto.get("protocol_version") or "")},
        "isBaseline": arm_id == BASELINE_ARM,
        "candidate": cand,
        "knobs": dict(knobs or {}),
        "applied": [dict(r) for r in applied],
        "baseline": dict(proto.get("baseline") or {}),
        "changes": {"file_keys": sorted({f"{r['file']}:{r['engineKey']}" for r in applied if r.get("file")}),
                    "stage_flags": [r["engineKey"] for r in applied if r.get("file") is None]},
        "stageFlags": stage_flags,
        "config": {"fingerprint": config_fingerprint, "sourceFingerprint": source_fingerprint,
                   "untouched": config_fingerprint == source_fingerprint},
        "members": list(members or []),
    }


def write_arm(arm_dir: Path | str, record: Mapping) -> Path:
    p = Path(arm_dir) / ARM_FILE
    p.write_text(json.dumps(record, indent=1, sort_keys=True, default=str) + "\n",
                 encoding="utf-8", newline="\n")
    return p


def read_arm(arm_dir: Path | str) -> dict:
    p = Path(arm_dir) / ARM_FILE
    if not p.is_file():
        raise FileNotFoundError(f"no {ARM_FILE} under {arm_dir}; run `run_round2.py arms` first")
    return json.loads(p.read_text(encoding="utf-8"))


def arm_dir(out_root: Path | str, arm_id: str) -> Path:
    return Path(out_root) / ARMS_DIR / str(arm_id)


def build_arm_root(protocol_path: Path | str, arm_id: str, *, source_config: Path | str,
                   out_root: Path | str, knobs: Optional[Mapping[str, Any]] = None) -> Path:
    """Write ``<out_root>/arms/<arm_id>/config`` (a copy of ``source_config`` with the
    candidate's knob applied; ``A2`` is the untouched baseline) and its ``arm.json``.
    An arm folder already recorded under another protocol is refused; its campaign and
    evaluation folders are never touched, only ``config`` and ``arm.json`` are replaced."""
    proto = load_protocol(protocol_path)
    sha = sha256_of(protocol_path)
    if knobs is None:
        knobs = {} if arm_id == BASELINE_ARM else dict(candidate(proto, arm_id).get("config") or {})
    target = arm_dir(out_root, arm_id)
    if (target / ARM_FILE).is_file():
        check_protocol(read_arm(target), sha, what=f"arm {arm_id}")
    cfg = target / CONFIG_DIR_NAME
    if cfg.exists():
        shutil.rmtree(cfg)
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path(source_config), cfg,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    source_fp = config_tree_fingerprint(source_config)
    applied = apply_knobs(cfg, knobs)
    (target / "decisions").mkdir(exist_ok=True)
    rec = arm_record(protocol_path, arm_id, knobs=knobs, applied=applied,
                     config_fingerprint=config_tree_fingerprint(cfg), source_fingerprint=source_fp)
    write_arm(target, rec)
    return target


def compose_finalist(protocol_path: Path | str, member_dirs: Iterable[Path | str], *,
                     source_config: Path | str, out_root: Path | str) -> Path:
    """One config root carrying every accepted arm's knob, applied in the order given.
    Two members setting one engine key to different values are refused: a composition
    is checked once on the development cells (protocol sequence.finalist), never guessed."""
    sha = sha256_of(protocol_path)
    knobs: dict[str, Any] = {}
    owners: dict[str, tuple[str, Any]] = {}
    members: list[str] = []
    for d in member_dirs:
        rec = read_arm(d)
        check_protocol(rec, sha, what=f"arm {rec.get('arm')}")
        members.append(str(rec.get("arm")))
        for key, value in (rec.get("knobs") or {}).items():
            route = route_knob(key)
            engine = route["path"] if route["kind"] != "metric_map" else "metric_map"
            if engine in owners and _hashable(owners[engine][1]) != _hashable(value):
                raise KnobError(f"arms {owners[engine][0]} and {rec.get('arm')} both set {engine} "
                                f"({owners[engine][1]!r} vs {value!r}); compose them by hand")
            owners[engine] = (str(rec.get("arm")), value)
            knobs[key] = value
    target = build_arm_root(protocol_path, FINALIST_ARM, source_config=source_config,
                            out_root=out_root, knobs=knobs)
    rec = read_arm(target)
    rec["members"] = members
    rec["candidate"] = {"family": "finalist", "change": "the accepted changes composed",
                        "decision": "composition"}
    write_arm(target, rec)
    return target


# --------------------------------------------------------------------------- #
# candidate C4: the metric map variant from the evidence table's dispositions
# --------------------------------------------------------------------------- #
#: Where dam pressure scores once. Dam density affects both functions it was listed
#: under; the cited evidence (Poff et al. 2007, dams homogenize flow regimes) is the
#: streamflow construct, and Watershed connectivity keeps road-stream crossings, so
#: no function is left without a candidate. One constant flips the choice.
DAM_PRESSURE_FUNCTION = "Streamflow regime"
#: Where chlorophyll a scores once: it is the response to nutrient enrichment in the
#: cited guidance (USEPA 2000), and canopy density alone carries the light signal.
CHLOROPHYLL_FUNCTION = "Nutrient cycling"
#: The agricultural criterion's construct: crops plus hay (EASI's pctag2019ws), the
#: StreamCat code the fixed criteria name (fixed_criteria.SPEC["pctag2019ws"]["streamcat"]).
AGRICULTURE_CODE, AGRICULTURE_LABEL = "pctag2019", "Agriculture, crop and hay (%)"
POPULATION_SUPPORT_FUNCTION = "Population support"
FISH_PROPORTION_SET = ("fish_NAT_NTOLPTAX", "fish_NAT_TOLRPIND", "fish_NAT_LITHPIND")
FISH_RICHNESS_ALTERNATIVE = "fish_NAT_TOTLNTAX"


def dispositions_of(evidence: Mapping) -> dict[str, dict]:
    return {str(k): dict(v or {}) for k, v in ((evidence or {}).get("metrics") or {}).items()}


def _functions(doc: dict) -> list[dict]:
    return [f for f in (doc.get("functions") or []) if isinstance(f, dict)]


def _listings(doc: dict, code: str) -> list[tuple[dict, dict]]:
    return [(f, m) for f in _functions(doc) for m in (f.get("metrics") or [])
            if isinstance(m, dict) and str(m.get("code")) == code]


def _remove_listings(doc: dict, code: str, *, keep_function: Optional[str] = None) -> list[str]:
    removed = []
    for f in _functions(doc):
        if keep_function is not None and str(f.get("function")) == keep_function:
            continue
        before = len(f.get("metrics") or [])
        f["metrics"] = [m for m in (f.get("metrics") or []) if str(m.get("code")) != code]
        if len(f["metrics"]) != before:
            removed.append(str(f.get("function")))
    return removed


def _function_block(doc: dict, name: str) -> Optional[dict]:
    return next((f for f in _functions(doc) if str(f.get("function")) == name), None)


def _rule_omit(doc: dict, disp: dict) -> list[dict]:
    """``omit``: every listing of the metric is removed, whichever function carried it."""
    out = []
    for code, e in sorted(disp.items()):
        if str(e.get("disposition")) == "omit" and _listings(doc, code):
            out.append({"rule": "omit", "code": code, "action": "removed",
                        "functions": _remove_listings(doc, code)})
    return out


def _rule_bfi_covariate(doc: dict, disp: dict) -> list[dict]:
    """``bfi`` revised as a natural-setting covariate: it stays in the map as a predictor
    (``role: predictor``, not default-selected), so the landscape selection never fits a
    reference curve on it; the frame still carries ``bfiws`` as the comparability
    covariate of the water-chemistry family."""
    e = disp.get("bfi") or {}
    if str(e.get("disposition")) != "revise" or str(e.get("construct_class")) != "natural_setting_covariate":
        return []
    fns = []
    for f, m in _listings(doc, "bfi"):
        m["role"] = "predictor"
        m["default_selected"] = False
        fns.append(str(f.get("function")))
    return [{"rule": "bfi_covariate", "code": "bfi", "action": "role predictor, not selected",
             "functions": fns}] if fns else []


def _rule_one_function(doc: dict, disp: dict, *, code: str, function: str, rule: str) -> list[dict]:
    """A metric listed under two functions scores once: the listing under ``function``
    is kept (moved there when it was never listed there) and every other is removed."""
    e = disp.get(code) or {}
    if str(e.get("disposition")) != "revise" or not _listings(doc, code):
        return []
    block = _function_block(doc, function)
    if block is None:
        raise KnobError(f"the metric map has no function {function!r} for {code}")
    have = [m for m in (block.get("metrics") or []) if str(m.get("code")) == code]
    if not have:
        first = _listings(doc, code)[0][1]
        block.setdefault("metrics", []).append(copy.deepcopy(first))
    removed = _remove_listings(doc, code, keep_function=function)
    return [{"rule": rule, "code": code, "action": f"kept under {function} only",
             "functions": removed}]


def _rule_chlorophyll(doc: dict, disp: dict) -> list[dict]:
    """``chem_CHLA`` revised: chlorophyll a scores one function, :data:`CHLOROPHYLL_FUNCTION`."""
    return _rule_one_function(doc, disp, code="chem_CHLA", function=CHLOROPHYLL_FUNCTION,
                              rule="chlorophyll_one_function")


def _rule_dam_pressure(doc: dict, disp: dict) -> list[dict]:
    """``damdens`` revised: dam pressure scores once, under :data:`DAM_PRESSURE_FUNCTION`."""
    return _rule_one_function(doc, disp, code="damdens", function=DAM_PRESSURE_FUNCTION,
                              rule="dam_pressure_once")


def _rule_agriculture(doc: dict, disp: dict) -> list[dict]:
    """``pctcrop2019`` revised: the agricultural criterion is crops plus hay, the construct
    EASI's fixed criterion scores (``pctag2019``), in place of crops alone."""
    e = disp.get("pctcrop2019") or {}
    if str(e.get("disposition")) != "revise":
        return []
    fns = []
    for f, m in _listings(doc, "pctcrop2019"):
        m["code"] = AGRICULTURE_CODE
        m["label"] = AGRICULTURE_LABEL
        fns.append(str(f.get("function")))
    return [{"rule": "agriculture_crops_plus_hay", "code": "pctcrop2019",
             "action": f"replaced by {AGRICULTURE_CODE}", "functions": fns}] if fns else []


def _rule_doc_decided(doc: dict, disp: dict) -> list[dict]:
    """``chem_DOC`` revised: the direction registry declares no scoring direction, so a
    listing that is never scored is the dishonest state the evidence names. It is
    omitted from the map; Carbon processing keeps riparian woody cover."""
    e = disp.get("chem_DOC") or {}
    if str(e.get("disposition")) != "revise" or not _listings(doc, "chem_DOC"):
        return []
    return [{"rule": "doc_decided", "code": "chem_DOC", "action": "omitted (no scoring direction)",
             "functions": _remove_listings(doc, "chem_DOC")}]


def _rule_fish_proportions(doc: dict, disp: dict) -> list[dict]:
    """The fish proportion set (revised ``fish_NAT_NTOLPTAX`` and ``fish_NAT_TOLRPIND``,
    with the lithophil reserve) becomes the default for Population support and native
    richness the documented alternative (a reserve), as the 2026-09-21 reasoning says."""
    if str((disp.get("fish_NAT_NTOLPTAX") or {}).get("disposition")) != "revise":
        return []
    block = _function_block(doc, POPULATION_SUPPORT_FUNCTION)
    if block is None:
        return []
    changed = []
    for m in block.get("metrics") or []:
        code = str(m.get("code"))
        if code in FISH_PROPORTION_SET:
            m["default_selected"] = True
            m["reserve"] = False
            changed.append(code)
        elif code == FISH_RICHNESS_ALTERNATIVE:
            m["default_selected"] = False
            m["reserve"] = True
            changed.append(code)
    return [{"rule": "fish_proportion_set", "code": ",".join(changed),
             "action": "proportions default, native richness a reserve",
             "functions": [POPULATION_SUPPORT_FUNCTION]}] if changed else []


def _rule_left_as_listed(doc: dict, disp: dict, touched: set[str]) -> list[dict]:
    """Every other ``revise`` (and any ``add`` or ``replace``) is left as the map lists it
    and named here, so the variant's header says what it did not decide."""
    out = []
    for code, e in sorted(disp.items()):
        d = str(e.get("disposition"))
        if d in ("revise", "add", "replace") and code not in touched:
            out.append({"rule": "left_as_listed", "code": code, "action": f"{d}: no map rule",
                        "functions": [str(f.get("function")) for f, _ in _listings(doc, code)]})
    return out


def apply_revise_dispositions(metric_map: Mapping, evidence: Mapping) -> tuple[dict, list[dict]]:
    """The C4 variant of metric_map.yaml from metric_evidence.yaml's dispositions.

    Rules, each firing only on the disposition it names (a changed disposition changes
    the variant, never the code):

    1. ``omit``: the metric leaves every function.
    2. ``bfi`` revised as a natural-setting covariate: demoted to a predictor.
    3. ``chem_CHLA`` revised: chlorophyll scores :data:`CHLOROPHYLL_FUNCTION` only.
    4. ``damdens`` revised: dam pressure scores :data:`DAM_PRESSURE_FUNCTION` only.
    5. ``pctcrop2019`` revised: crops plus hay (``pctag2019``) is the agricultural criterion.
    6. ``chem_DOC`` revised: omitted, since it has no scoring direction.
    7. ``fish_NAT_NTOLPTAX`` revised: the fish proportion set is the Population support
       default, native richness the reserve alternative.
    8. every other disposition is left as listed and named in the log.

    Returns ``(variant, log)``; the input is not modified.
    """
    doc = copy.deepcopy(dict(metric_map))
    disp = dispositions_of(evidence)
    log: list[dict] = []
    for rule in (_rule_omit, _rule_bfi_covariate, _rule_chlorophyll, _rule_dam_pressure,
                 _rule_agriculture, _rule_doc_decided, _rule_fish_proportions):
        log.extend(rule(doc, disp))
    touched = {c for r in log for c in str(r.get("code", "")).split(",") if c}
    touched |= {"fish_NAT_TOLRPIND", "fish_NAT_LITHPIND"} if "fish_NAT_NTOLPTAX" in touched else set()
    log.extend(_rule_left_as_listed(doc, disp, touched))
    return doc, log


def render_metric_map(doc: Mapping, log: list[dict], *, evidence_sha: str, source_sha: str) -> str:
    lines = ["# metric_map.yaml, candidate C4 variant (GENERATED by streamcurves.round2)",
             f"# from the committed map sha256:{source_sha[:12]} and metric_evidence.yaml "
             f"sha256:{evidence_sha[:12]}; rules applied:"]
    for r in log:
        lines.append(f"#   {r['rule']:<26} {r['code']:<20} {r['action']}")
    return "\n".join(lines) + "\n" + yaml.safe_dump(dict(doc), sort_keys=False, default_flow_style=None,
                                                    allow_unicode=False, width=120)


# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #
def _ranks(values: np.ndarray) -> np.ndarray:
    order = values.argsort(kind="stable")
    sorted_vals = values[order]
    lo = np.searchsorted(sorted_vals, sorted_vals, side="left") + 1
    hi = np.searchsorted(sorted_vals, sorted_vals, side="right")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = (lo + hi) / 2.0
    return ranks


def auc(scores: Any, positive: Any) -> Optional[float]:
    """The Mann-Whitney area: the chance a random positive outscores a random negative,
    ties counting half; None without one case in each group."""
    x = np.asarray(scores, dtype=float)
    pos = np.asarray(positive, dtype=bool)
    ok = np.isfinite(x)
    x, pos = x[ok], pos[ok]
    n1, n0 = int(pos.sum()), int((~pos).sum())
    if n1 == 0 or n0 == 0:
        return None
    ranks = _ranks(x)
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def spearman(x: Any, y: Any) -> Optional[float]:
    """Spearman's rho over the pairs where both are finite (None under 3 pairs)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3:
        return None
    rx, ry = _ranks(x[ok]), _ranks(y[ok])
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    denominator = np.sqrt((rx * rx).sum() * (ry * ry).sum())
    return float((rx * ry).sum() / denominator) if denominator > 0 else None


def seed_for(*parts: Any, seed: int = 11) -> int:
    """One deterministic seed from what a comparison is (the basis_recovery convention:
    the CRC32 of the parts and the run seed), never from when it runs."""
    key = "|".join(str(p) for p in parts) + f"|{int(seed)}"
    return int(zlib.crc32(key.encode("utf-8")))


def cluster_groups(clusters: Any) -> list[np.ndarray]:
    labels = np.asarray([str(c) for c in clusters], dtype=object)
    uniq, inverse = np.unique(labels, return_inverse=True)
    return [np.flatnonzero(inverse == i) for i in range(len(uniq))]


def cluster_bootstrap(clusters: Any, statistic: Callable[[np.ndarray], Optional[float]], *,
                      n_boot: int = 200, seed: int = 11) -> np.ndarray:
    """Resample whole clusters with replacement and evaluate ``statistic(row_positions)``
    each time; NaN where the statistic has no answer. Deterministic for a seed."""
    members = cluster_groups(clusters)
    rng = np.random.default_rng(int(seed))
    out = np.full(int(n_boot), np.nan)
    if not members:
        return out
    for b in range(int(n_boot)):
        picked = rng.integers(0, len(members), size=len(members))
        rows = np.concatenate([members[i] for i in picked])
        value = statistic(rows)
        out[b] = np.nan if value is None else float(value)
    return out


def interval(samples: Any, level: float) -> tuple[Optional[float], Optional[float]]:
    arr = np.asarray(samples, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None, None
    lo = (1.0 - float(level)) / 2.0
    return float(np.quantile(arr, lo)), float(np.quantile(arr, 1.0 - lo))


def bootstrap_p_value(samples: Any, null: float = 0.0) -> Optional[float]:
    """A two-sided bootstrap p-value: twice the smaller tail share at ``null``."""
    arr = np.asarray(samples, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    left = float(np.mean(arr <= null))
    right = float(np.mean(arr >= null))
    return float(min(1.0, 2.0 * min(left, right)))


def paired_delta(a: Any, b: Any, clusters: Any, *, level: float = 0.90, n_boot: int = 200,
                 seed: int = 11, statistic: str = "median") -> dict:
    """The paired delta ``a - b`` on identical units (a station, or a cell), with a
    cluster bootstrap interval: whole clusters (HUC12, HUC8 or a region, as the caller
    says) are drawn with replacement and the statistic of the resampled deltas taken.

    ``statistic`` is ``median`` (the protocol's O1 reading: "median delta") or ``mean``.
    Units where either side is missing are dropped from both, so the comparison stays
    paired. ``excludes_zero`` is what an accuracy change is adopted on.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    cl = np.asarray(list(clusters), dtype=object)
    if not (len(a) == len(b) == len(cl)):
        raise ValueError("paired_delta needs a, b and clusters of one length")
    ok = np.isfinite(a) & np.isfinite(b)
    d = (a - b)[ok]
    cl = cl[ok]
    out: dict[str, Any] = {"n": int(ok.sum()), "n_clusters": int(len(set(str(c) for c in cl))),
                           "level": float(level), "statistic": statistic, "estimate": None,
                           "mean_delta": None, "median_delta": None, "lo": None, "hi": None,
                           "excludes_zero": None, "p_value": None, "n_boot": int(n_boot),
                           "n_boot_valid": 0}
    if out["n"] == 0:
        return out
    stat = np.median if statistic == "median" else np.mean
    out.update({"estimate": float(stat(d)), "mean_delta": float(np.mean(d)),
                "median_delta": float(np.median(d))})
    samples = cluster_bootstrap(cl, lambda rows: float(stat(d[rows])) if len(rows) else None,
                                n_boot=n_boot, seed=seed)
    lo, hi = interval(samples, level)
    out.update({"lo": lo, "hi": hi, "n_boot_valid": int(np.isfinite(samples).sum()),
                "excludes_zero": None if lo is None else bool(lo > 0 or hi < 0),
                "p_value": bootstrap_p_value(samples)})
    return out


def paired_auc_delta(score_a: Any, score_b: Any, positive: Any, clusters: Any, *,
                     level: float = 0.95, n_boot: int = 200, seed: int = 11) -> dict:
    """``AUC(a) - AUC(b)`` on identical labelled stations with a cluster bootstrap
    interval (HUC8 for O3). ``blocks`` is the protocol's O3 rule: a lower bound below
    -0.01 blocks adoption."""
    sa = np.asarray(score_a, dtype=float)
    sb = np.asarray(score_b, dtype=float)
    pos = np.asarray(positive, dtype=bool)
    cl = np.asarray(list(clusters), dtype=object)
    if not (len(sa) == len(sb) == len(pos) == len(cl)):
        raise ValueError("paired_auc_delta needs scores, labels and clusters of one length")
    ok = np.isfinite(sa) & np.isfinite(sb)
    sa, sb, pos, cl = sa[ok], sb[ok], pos[ok], cl[ok]
    out: dict[str, Any] = {"n": int(ok.sum()), "n_pos": int(pos.sum()), "n_neg": int((~pos).sum()),
                           "n_clusters": int(len(set(str(c) for c in cl))), "level": float(level),
                           "auc_a": None, "auc_b": None, "delta": None, "lo": None, "hi": None,
                           "blocks": None, "p_value": None, "n_boot": int(n_boot), "n_boot_valid": 0}
    aa, ab = auc(sa, pos), auc(sb, pos)
    if aa is None or ab is None:
        return out
    out.update({"auc_a": aa, "auc_b": ab, "delta": float(aa - ab)})

    def delta_of(rows: np.ndarray) -> Optional[float]:
        x, y = auc(sa[rows], pos[rows]), auc(sb[rows], pos[rows])
        return None if x is None or y is None else float(x - y)

    samples = cluster_bootstrap(cl, delta_of, n_boot=n_boot, seed=seed)
    lo, hi = interval(samples, level)
    out.update({"lo": lo, "hi": hi, "n_boot_valid": int(np.isfinite(samples).sum()),
                "blocks": None if lo is None else bool(lo < MARGINS["O3"]["block_lower_bound"]),
                "p_value": bootstrap_p_value(samples)})
    return out


def bh_adjust(pvalues: Mapping[str, Optional[float]]) -> dict[str, Optional[float]]:
    """Benjamini-Hochberg q-values over a family of p-values (None stays None)."""
    items = [(k, float(v)) for k, v in pvalues.items() if v is not None and v == v]
    out: dict[str, Optional[float]] = {k: None for k in pvalues}
    m = len(items)
    if not m:
        return out
    ordered = sorted(items, key=lambda kv: kv[1])
    q = [0.0] * m
    running = 1.0
    for i in range(m - 1, -1, -1):
        running = min(running, ordered[i][1] * m / (i + 1))
        q[i] = running
    for (k, _), value in zip(ordered, q):
        out[k] = float(min(1.0, value))
    return out


# --------------------------------------------------------------------------- #
# the adoption rule per decision type, the limits and the subgroup block
# --------------------------------------------------------------------------- #
def primary_test(decision: str, delta: Optional[Mapping]) -> dict:
    """The decision-type rule on the candidate's primary paired delta (agreement for
    O1, AUC for O3, minus the flip rate for O5, so that up is better everywhere)."""
    d = dict(delta or {})
    m = MARGINS["O1"]
    if decision not in DECISION_TYPES:
        raise ValueError(f"unknown decision type {decision!r}; one of {', '.join(DECISION_TYPES)}")
    if decision in ("reference_arm", "coverage_only"):
        return {"rule": decision, "passes": None, "why": "no accuracy margin applies"}
    if d.get("estimate") is None or d.get("lo") is None:
        return {"rule": decision, "passes": False, "why": "no paired comparison on identical units"}
    if decision == "accuracy_change":
        ok = bool(d.get("excludes_zero")) and float(d["estimate"]) >= m["accuracy_change_min_delta"]
        return {"rule": "accuracy_change", "passes": ok,
                "why": (f"median delta {float(d['estimate']):+.4f} with the {int(d.get('level', 0.9) * 100)} "
                        f"percent interval [{float(d['lo']):+.4f}, {float(d['hi']):+.4f}]; adopted when the "
                        f"interval excludes 0 and the median delta is at least "
                        f"{m['accuracy_change_min_delta']:+.2f}")}
    ok = float(d["lo"]) >= m["simplification_lower_bound"]
    return {"rule": "composition" if decision == "composition" else "simplification", "passes": ok,
            "why": (f"lower bound {float(d['lo']):+.4f} of the {int(d.get('level', 0.9) * 100)} percent "
                    f"interval; noninferior when at least {m['simplification_lower_bound']:+.2f}")}


def o2_limit(net_optimism: Optional[float], baseline_net_optimism: Optional[float]) -> dict:
    """O2: the arm's median net optimism (ACC-06) may not exceed 0.05 and may not worsen
    the baseline's by more than 0.02."""
    m = MARGINS["O2"]
    if net_optimism is None:
        return {"outcome": "O2", "blocks": False, "why": "no net optimism recorded",
                "value": None, "baseline": baseline_net_optimism, "worsening": None}
    worsening = (None if baseline_net_optimism is None
                 else float(net_optimism) - float(baseline_net_optimism))
    over = float(net_optimism) > m["max_net_optimism"]
    worse = worsening is not None and worsening > m["max_worsening"]
    why = []
    if over:
        why.append(f"median net optimism {float(net_optimism):+.4f} is above {m['max_net_optimism']:.2f}")
    if worse:
        why.append(f"net optimism worsened by {worsening:+.4f}, more than {m['max_worsening']:.2f}")
    return {"outcome": "O2", "blocks": bool(over or worse), "why": "; ".join(why) or "within the limit",
            "value": float(net_optimism), "baseline": baseline_net_optimism, "worsening": worsening}


def o3_block(auc_deltas: Iterable[Mapping]) -> dict:
    """O3: any paired AUC delta (the ECI against each independent target) whose lower
    95 percent bound is below -0.01 blocks."""
    rows = [dict(r) for r in auc_deltas]
    lows = [(str(r.get("target")), float(r["lo"])) for r in rows if r.get("lo") is not None]
    worst = min(lows, key=lambda t: t[1]) if lows else None
    blocking = [t for t, lo in lows if lo < MARGINS["O3"]["block_lower_bound"]]
    return {"outcome": "O3", "blocks": bool(blocking), "worst": worst,
            "why": (f"lower bound below {MARGINS['O3']['block_lower_bound']:+.2f} on "
                    + ", ".join(blocking)) if blocking else "no association loss beyond the margin",
            "targets": {t: lo for t, lo in lows}}


def o5_limit(flip_arm: Optional[float], flip_base: Optional[float]) -> dict:
    """O5: the median bootstrap class-flip rate may not rise by more than 0.02."""
    if flip_arm is None or flip_base is None:
        return {"outcome": "O5", "blocks": False, "why": "no stability comparison",
                "value": flip_arm, "baseline": flip_base, "rise": None}
    rise = float(flip_arm) - float(flip_base)
    over = rise > MARGINS["O5"]["max_rise"]
    return {"outcome": "O5", "blocks": bool(over), "value": float(flip_arm), "baseline": float(flip_base),
            "rise": rise, "why": (f"median flip rate rose by {rise:+.4f}, more than "
                                  f"{MARGINS['O5']['max_rise']:.2f}") if over else "within the limit"}


def subgroup_block(deltas_by_group: Mapping[str, Optional[float]], *,
                   max_loss: Optional[float] = None) -> dict:
    """Any NARS-9 group losing more than 0.10 on O1 blocks adoption (uncorrected,
    exploratory, and still a block)."""
    limit = MARGINS["subgroups"]["max_loss"] if max_loss is None else float(max_loss)
    losing = sorted(g for g, d in deltas_by_group.items() if d is not None and float(d) < -limit)
    return {"outcome": "subgroups", "blocks": bool(losing), "groups": dict(deltas_by_group),
            "losing": losing, "why": (f"NARS-9 group(s) losing more than {limit:.2f}: " + ", ".join(losing))
            if losing else "no group loses more than the margin"}


def adoption(decision: str, *, primary: Optional[Mapping], limits: Iterable[Mapping],
             constraint_resolved: Optional[bool] = None,
             coverage_gain: Optional[float] = None,
             missing: Iterable[str] = ()) -> dict:
    """The verdict of one candidate: adopt, reject, reference-only or inconclusive.

    A reference arm is never adopted. A coverage-only candidate is adopted only with
    no block, a recorded resolution of its constraint (D4a) and a coverage gain; it is
    reference-only while the constraint stands. An accuracy change or a simplification
    is adopted on its primary rule with no block, and rejected otherwise. ``missing``
    names the outcomes that have no data (the primary, or one that could block: O1, O2,
    O3, O5): with any of them missing the verdict is inconclusive for every candidate
    but a reference arm, because an absent block is not a passed one.
    """
    limits = [dict(x) for x in limits]
    blocking = [x for x in limits if x.get("blocks")]
    reasons = [f"{x.get('outcome')}: {x.get('why')}" for x in blocking]
    test = primary_test(decision, primary)
    missing = [str(m) for m in missing]
    if missing and decision != "reference_arm":
        return {"verdict": VERDICT_INCONCLUSIVE, "decision": decision, "primaryRule": test,
                "reasons": [f"no data for {', '.join(missing)}: evaluate the arm again before a verdict"]
                + reasons,
                "blocks": [x.get("outcome") for x in blocking], "missing": missing}
    if decision == "reference_arm":
        verdict = VERDICT_REFERENCE
        reasons = ["a reference arm is reported and never adopted"] + reasons
    elif decision == "coverage_only":
        if blocking:
            verdict = VERDICT_REJECT
        elif constraint_resolved is not True:
            verdict = VERDICT_REFERENCE
            reasons.append("the candidate's constraint is not recorded as resolved (D4a)")
        elif coverage_gain is None or float(coverage_gain) <= 0:
            verdict = VERDICT_REJECT
            reasons.append("no coverage gained")
        else:
            verdict = VERDICT_ADOPT
            reasons.append(f"coverage gained ({coverage_gain:+g}) with the constraint resolved")
    else:
        if blocking:
            verdict = VERDICT_REJECT
        elif test["passes"]:
            verdict = VERDICT_ADOPT
        else:
            verdict = VERDICT_REJECT
            reasons.append(f"primary rule not met: {test['why']}")
        if verdict == VERDICT_ADOPT:
            reasons.append(f"primary rule met: {test['why']}")
    return {"verdict": verdict, "decision": decision, "primaryRule": test, "reasons": reasons,
            "blocks": [x.get("outcome") for x in blocking]}


# --------------------------------------------------------------------------- #
# DEEP's scoring arithmetic on a bundle
# --------------------------------------------------------------------------- #
FUNCTION_SCORE_MAX = 15.0
WEIGHTS = {"D": 1.0, "i": 0.1, "-": 0.0}
OUTCOMES = ("physical", "chemical", "biological")
#: apps/deep/data/deep-outcome-mapping.json, function by function (a test pins the copy).
OUTCOME_MAPPING: dict[str, dict[str, str]] = {
    "catchment-hydrology": {"physical": "D", "chemical": "i", "biological": "i"},
    "surface-water-storage": {"physical": "D", "chemical": "i", "biological": "i"},
    "reach-inflow": {"physical": "D", "chemical": "i", "biological": "i"},
    "streamflow-regime": {"physical": "D", "chemical": "i", "biological": "i"},
    "low-flow-baseflow-dynamics": {"physical": "D", "chemical": "i", "biological": "i"},
    "high-flow-dynamics": {"physical": "D", "chemical": "i", "biological": "i"},
    "floodplain-connectivity": {"physical": "D", "chemical": "i", "biological": "i"},
    "hyporheic-connectivity": {"physical": "D", "chemical": "i", "biological": "i"},
    "channel-evolution": {"physical": "D", "chemical": "i", "biological": "i"},
    "channel-floodplain-dynamics": {"physical": "D", "chemical": "i", "biological": "i"},
    "sediment-continuity": {"physical": "D", "chemical": "i", "biological": "i"},
    "bed-composition-bedform-dynamics": {"physical": "D", "chemical": "i", "biological": "i"},
    "light-thermal-regime": {"physical": "-", "chemical": "D", "biological": "i"},
    "carbon-processing": {"physical": "-", "chemical": "D", "biological": "i"},
    "nutrient-cycling": {"physical": "i", "chemical": "D", "biological": "i"},
    "water-soil-quality": {"physical": "i", "chemical": "D", "biological": "i"},
    "habitat-provision": {"physical": "-", "chemical": "-", "biological": "D"},
    "population-support": {"physical": "i", "chemical": "i", "biological": "D"},
    "community-dynamics": {"physical": "i", "chemical": "i", "biological": "D"},
    "watershed-connectivity": {"physical": "i", "chemical": "-", "biological": "D"},
}
FUNCTION_IDS = tuple(OUTCOME_MAPPING)
METRIC_ID_PREFIX = "spring-"
#: Bundle metric ids whose station value is not the slug of a frame column: the
#: fixed agriculture criterion reads crops plus hay (the screen's ``agriculture_ws``),
#: the degree of regulation is the screen's ``dor``, and the dam count within a mile
#: is a site lookup DEEP makes at scoring time, which no station table carries.
STATION_COLUMN_ALIASES: dict[str, Optional[str]] = {
    "spring-pctag2019ws": "agriculture_ws",
    "spring-dorws": "dor",
    "spring-nid-dams-1mi": None,
}
NRSA_FIELD_PREFIXES = ("spring-phab-", "spring-chem-", "spring-bent-", "spring-fish-")


def deep_slug(x: Any) -> str:
    s = str(x).strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"^-+|-+$", "", s)


def metric_id_of(metric_key: str) -> str:
    return METRIC_ID_PREFIX + deep_slug(metric_key)


def bundle_metrics(bundle: Mapping) -> list[tuple[str, dict]]:
    """``[(functionId, metric spec)]`` in bundle order."""
    out = []
    for fn in (bundle or {}).get("metricsByFunction") or []:
        for m in fn.get("metrics") or []:
            if isinstance(m, dict) and m.get("metricId"):
                out.append((str(fn.get("functionId")), m))
    return out


def metric_columns(bundle: Mapping, columns: Iterable[str]) -> dict[str, Optional[str]]:
    """``{metricId: station column}`` for every metric the bundle scores: the alias table
    first, else the column whose slug is the id without its prefix; None where no station
    column carries the value (the metric then scores as missing)."""
    by_slug = {deep_slug(c): str(c) for c in columns}
    out: dict[str, Optional[str]] = {}
    for _fid, m in bundle_metrics(bundle):
        mid = str(m["metricId"])
        if mid in STATION_COLUMN_ALIASES:
            out[mid] = STATION_COLUMN_ALIASES[mid]
        else:
            out[mid] = by_slug.get(mid[len(METRIC_ID_PREFIX):] if mid.startswith(METRIC_ID_PREFIX) else mid)
    return out


def _class_of(value: float, breaks: list, right: bool) -> int:
    idx = 0
    for b in breaks:
        if (value > b) if right else (value >= b):
            idx += 1
    return idx


def station_stratum(spec: Mapping, *, drainage_area_sqkm: Any = None, nhd_slope: Any = None) -> Optional[str]:
    """The curve layer a station belongs to, from the bundle's ``stratifier`` block and
    the station's NHDPlus slope or drainage area, as DEEP's ``reference_support.auto_stratum``
    chooses it for a delineated reach: the class key when the bundle carries that layer,
    ``""`` (the pooled layer) when it does not or the station has no value, None when the
    metric has no stratifier."""
    block = (spec or {}).get("stratifier")
    layers = (spec or {}).get("curveLayers") or []
    if not isinstance(block, dict) or len(layers) < 2:
        return None
    variable = str(block.get("variable"))
    raw = {"nhd_slope": nhd_slope, "drainage_area_sqkm": drainage_area_sqkm}.get(variable)
    try:
        value = float(raw)
        breaks = [float(b) for b in block.get("breaks") or []]
    except (TypeError, ValueError):
        return ""
    if value != value or value < 0 or (bool(block.get("right")) and value <= 0):
        return ""
    classes = block.get("classes") or []
    idx = _class_of(value, breaks, bool(block.get("right")))
    if idx >= len(classes):
        return ""
    cls = classes[idx] or {}
    key, label = str(cls.get("key") or ""), str(cls.get("label") or "")
    have = {str(layer.get("stratum", "")) for layer in layers}
    layer = key if key and key in have else label if label and label in have else None
    return layer if layer is not None else ""


def active_points(spec: Mapping, stratum: Optional[str] = None) -> list:
    """DEEP's ``curves.active_points``: the chosen layer, else ``activeStratum``, else the
    first layer; ``curve.points`` for a single-curve metric."""
    layers = (spec or {}).get("curveLayers")
    if layers:
        if stratum:
            for layer in layers:
                if str(layer.get("stratum", "")) == str(stratum):
                    return layer.get("points") or []
        active = spec.get("activeStratum")
        if active:
            for layer in layers:
                if str(layer.get("stratum", "")) == str(active):
                    return layer.get("points") or []
        return layers[0].get("points") or []
    return ((spec or {}).get("curve") or {}).get("points") or []


def metric_index(spec: Mapping, value: Any, *, drainage_area_sqkm: Any = None,
                 nhd_slope: Any = None) -> Optional[float]:
    """DEEP's ``curves.metric_index`` for one station value: None when the value is
    missing or the curve has no points, else the interpolated, clamped index."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    pts = active_points(spec, station_stratum(spec, drainage_area_sqkm=drainage_area_sqkm,
                                              nhd_slope=nhd_slope))
    if not pts:
        return None
    return curves.interp_curve(pts, x)


def band_of(index: Optional[float]) -> Optional[str]:
    if index is None or index != index:
        return None
    return "NF" if index <= 0.39 else "AR" if index <= 0.69 else "F"


def unassessed_functions(bundle: Mapping) -> list[str]:
    scored = {str(fn.get("functionId")) for fn in (bundle or {}).get("metricsByFunction") or []
              if fn.get("metrics")}
    return [fid for fid in FUNCTION_IDS if fid not in scored]


def rollup(function_scores: Mapping[str, Optional[float]], unassessed: Iterable[str] = ()) -> dict:
    """DEEP's ``scoring.rollup`` arithmetic: outcome sub-indices from the weighted
    function scores, the ECI as their mean when complete, the coverage interval when
    functions are unassessed, and the over-scored ratio the calculator computes."""
    unassessed_ids = tuple(dict.fromkeys(str(u) for u in unassessed or ()))
    outcomes = {k: {"weighted": 0.0, "max": 0.0, "direct": 0, "indirect": 0, "unassessed_max": 0.0}
                for k in OUTCOMES}
    for fid, score in function_scores.items():
        if score is None or fid in unassessed_ids:
            continue
        codes = OUTCOME_MAPPING.get(str(fid))
        if codes is None:
            continue
        for key in OUTCOMES:
            code = codes.get(key, "-")
            weight = WEIGHTS.get(code, 0.0)
            if code == "D":
                outcomes[key]["direct"] += 1
            elif code == "i":
                outcomes[key]["indirect"] += 1
            if weight:
                outcomes[key]["weighted"] += float(score) * weight
                outcomes[key]["max"] += FUNCTION_SCORE_MAX * weight
    for fid in unassessed_ids:
        codes = OUTCOME_MAPPING.get(str(fid))
        if codes is None:
            continue
        for key in OUTCOMES:
            weight = WEIGHTS.get(codes.get(key, "-"), 0.0)
            if weight:
                outcomes[key]["unassessed_max"] += FUNCTION_SCORE_MAX * weight
    sub: dict[str, Optional[float]] = {}
    bounds: dict[str, Optional[tuple[float, float]]] = {}
    for key, o in outcomes.items():
        sub[key] = None if o["max"] <= 0 or o["direct"] == 0 else o["weighted"] / o["max"]
        total = o["max"] + o["unassessed_max"]
        bounds[key] = None if total <= 0 else (o["weighted"] / total, (o["weighted"] + o["unassessed_max"]) / total)
    complete = not unassessed_ids and all(v is not None for v in sub.values())
    eci = (sum(sub.values()) / len(OUTCOMES)) if complete else None
    spans = [bounds[k] for k in OUTCOMES]
    eci_bounds = None
    if all(b is not None for b in spans):
        eci_bounds = (sum(b[0] for b in spans) / len(spans), sum(b[1] for b in spans) / len(spans))
    over_scored = sum((o["weighted"] / o["max"]) if o["max"] > 0 else 0.0 for o in outcomes.values()) / len(OUTCOMES)
    return {"outcomes": outcomes, "sub_indices": sub, "eci": eci, "eci_bounds": eci_bounds,
            "eci_over_scored": over_scored, "unassessed": list(unassessed_ids)}


def score_station(bundle: Mapping, values: Mapping[str, Any], *, columns_of: Mapping[str, Optional[str]],
                  drainage_area_sqkm: Any = None, nhd_slope: Any = None) -> dict:
    """One station scored the way DEEP scores a site: every metric's index on its
    curve, each function's score as the mean index times 15 (None when no metric of
    the function scored), and the rollup. ``values`` maps station columns to values."""
    function_scores: dict[str, Optional[float]] = {}
    indices: dict[str, Optional[float]] = {}
    for fn in (bundle or {}).get("metricsByFunction") or []:
        fid = str(fn.get("functionId"))
        scored: list[float] = []
        for m in fn.get("metrics") or []:
            mid = str(m.get("metricId"))
            col = columns_of.get(mid)
            idx = metric_index(m, values.get(col) if col else None,
                               drainage_area_sqkm=drainage_area_sqkm, nhd_slope=nhd_slope)
            indices[f"{fid}|{mid}"] = idx
            if idx is not None:
                scored.append(idx)
        function_scores[fid] = (sum(scored) / len(scored)) * FUNCTION_SCORE_MAX if scored else None
    roll = rollup({k: v for k, v in function_scores.items() if v is not None},
                  unassessed_functions(bundle))
    return {"functions": function_scores, "indices": indices, **roll}


def station_list_hash(keys: Iterable[Any]) -> str:
    """Sha256 of the sorted station keys, one per line: two arms scored on the same
    stations carry the same hash."""
    text = "\n".join(sorted(str(k) for k in keys))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def coverage_of(bundle: Mapping) -> dict:
    """O4 of one version: functions supported of 20, curves by basis, the withheld count."""
    by_basis: dict[str, int] = {}
    metrics = 0
    for _fid, m in bundle_metrics(bundle):
        metrics += 1
        basis = str(m.get("basis") or ("published-benchmark" if str(m.get("criteriaBasis")) == "fixed"
                                        else "regional-reference"))
        by_basis[basis] = by_basis.get(basis, 0) + 1
    withheld = [w for w in (bundle.get("insufficientReferenceSupport") or []) if isinstance(w, dict)]
    supported = [fid for fid in FUNCTION_IDS if fid not in unassessed_functions(bundle)]
    return {"functionsSupported": len(supported), "functionsTotal": len(FUNCTION_IDS),
            "unassessed": unassessed_functions(bundle), "metricListings": metrics,
            "distinctMetrics": len({m["metricId"] for _f, m in bundle_metrics(bundle)}),
            "curvesByBasis": dict(sorted(by_basis.items())), "withheld": len(withheld),
            "withheldMetrics": sorted(str(w.get("metricId") or w.get("metric") or "") for w in withheld)}


def usability_of(bundle: Mapping) -> dict:
    """O6 of one version: field metrics per assessment (NRSA-sourced) and the distinct
    field procedures they need (the method context, else the how-to-measure text)."""
    field = {}
    for _fid, m in bundle_metrics(bundle):
        mid = str(m.get("metricId"))
        if mid.startswith(NRSA_FIELD_PREFIXES):
            proc = str(m.get("methodContext") or m.get("howToMeasure") or mid).strip()
            field[mid] = proc
    return {"fieldMetrics": len(field), "distinctProcedures": len(set(field.values())),
            "fieldMetricIds": sorted(field)}


# --------------------------------------------------------------------------- #
# the hierarchy harness (O1, O2) read as cells
# --------------------------------------------------------------------------- #
SELECTED_COLUMNS = ("selected", "chosen", "in_ladder")


def hierarchy_cells(table: pd.DataFrame) -> pd.DataFrame:
    """One row per evaluable cell of a hierarchy run (metric x region x basis with a
    class-flip rate): the agreement with the withheld reference (1 - flip), the A1
    exceedance, the net optimism, the NARS-9 group and the family, and ``selected``
    when the harness marks the source the arm's ladder chose (None otherwise)."""
    df = table.copy()
    for col in ("metric", "l3", "basis"):
        if col not in df.columns:
            raise ValueError(f"the hierarchy table has no {col!r} column")
    df["l3"] = df["l3"].astype(str)
    flip = pd.to_numeric(df.get("flip"), errors="coerce")
    df = df[flip.notna()].copy()
    df["agreement"] = 1.0 - pd.to_numeric(df["flip"], errors="coerce")
    df["cell"] = df["metric"].astype(str) + "|" + df["l3"] + "|" + df["basis"].astype(str)
    sel_col = next((c for c in SELECTED_COLUMNS if c in df.columns), None)
    df["selected"] = (df[sel_col].map(lambda v: bool(v) if v == v and v is not None else False)
                      if sel_col else None)
    keep = ["cell", "metric", "l3", "basis", "agreement", "flip", "exceedance", "net_optimism",
            "nars9", "family", "role", "selected"]
    for c in keep:
        if c not in df.columns:
            df[c] = None
    return df[keep].reset_index(drop=True)


def o1_comparison(arm_table: pd.DataFrame, base_table: pd.DataFrame, *, level: Optional[float] = None,
                  n_boot: int = 200, seed: int = 11, selected_only: bool = False) -> dict:
    """O1 paired between an arm and the baseline on identical cells: the delta of
    agreement with the withheld strict reference (median, cluster bootstrap by region),
    the A1 exceedance share of each arm, and the exploratory subgroups (NARS-9 group and
    metric family, each a median delta over its cells)."""
    level = MARGINS["O1"]["level"] if level is None else float(level)
    a, b = hierarchy_cells(arm_table), hierarchy_cells(base_table)
    if selected_only and a["selected"].notna().any() and b["selected"].notna().any():
        a, b = a[a["selected"] == True], b[b["selected"] == True]  # noqa: E712
    both = a.merge(b, on="cell", suffixes=("_arm", "_base"))
    delta = paired_delta(both["agreement_arm"].to_numpy(dtype=float),
                         both["agreement_base"].to_numpy(dtype=float),
                         both["l3_arm"].tolist(), level=level, n_boot=n_boot, seed=seed,
                         statistic="median")
    d = both["agreement_arm"].to_numpy(dtype=float) - both["agreement_base"].to_numpy(dtype=float)

    def by(col: str) -> dict[str, Optional[float]]:
        out: dict[str, Optional[float]] = {}
        groups = both[f"{col}_arm"].astype(object).where(both[f"{col}_arm"].notna(), None)
        for g in sorted({str(x) for x in groups.tolist() if x not in (None, "", "nan")}):
            sel = (groups.astype(str) == g).to_numpy()
            out[g] = float(np.median(d[sel])) if sel.any() else None
        return out

    a1 = MARGINS["O1"]["a1_exceedance"]

    def a1_share(cells: pd.DataFrame) -> Optional[float]:
        e = pd.to_numeric(cells["exceedance"], errors="coerce")
        return float((e >= a1).mean()) if len(e) else None

    return {"unit": "cell (metric x region x basis); clusters: Level III region",
            "n_cells": int(len(both)), "n_cells_arm": int(len(a)), "n_cells_base": int(len(b)),
            "cells_hash": station_list_hash(both["cell"].tolist()), "delta": delta,
            "a1_share_arm": a1_share(a), "a1_share_base": a1_share(b),
            "by_nars9": by("nars9"), "by_family": by("family"), "selected_only": bool(selected_only)}


def o1_from_calls(arm_calls: pd.DataFrame, base_calls: pd.DataFrame, *, level: Optional[float] = None,
                  n_boot: int = 200, seed: int = 11) -> dict:
    """O1 on station-level class calls when a harness writes them (columns ``metric``,
    ``l3``, ``basis``, ``station_key``, ``huc12``, ``agree``): the same paired delta, on
    identical stations with a HUC12-cluster bootstrap."""
    level = MARGINS["O1"]["level"] if level is None else float(level)
    keys = ["metric", "l3", "basis", "station_key"]
    a = arm_calls.copy()
    b = base_calls.copy()
    for df in (a, b):
        for k in keys:
            df[k] = df[k].astype(str)
    both = a.merge(b[keys + ["agree"]], on=keys, suffixes=("_arm", "_base"))
    clusters = both["huc12"].astype(str).tolist() if "huc12" in both.columns else both["l3"].tolist()
    delta = paired_delta(both["agree_arm"].to_numpy(dtype=float), both["agree_base"].to_numpy(dtype=float),
                         clusters, level=level, n_boot=n_boot, seed=seed, statistic="mean")
    return {"unit": "station; clusters: HUC12", "n_units": int(len(both)),
            "units_hash": station_list_hash((both[k].astype(str) for k in keys)), "delta": delta}


def o2_summary(table: pd.DataFrame) -> Optional[float]:
    """The median net optimism (ACC-06) over a hierarchy run's evaluable cells."""
    cells = hierarchy_cells(table)
    net = pd.to_numeric(cells["net_optimism"], errors="coerce").dropna()
    return float(net.median()) if len(net) else None


# --------------------------------------------------------------------------- #
# report tables
# --------------------------------------------------------------------------- #
def fmt(x: Any, digits: int = 4) -> str:
    if x is None:
        return ""
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, (int, np.integer)):
        return str(int(x))
    if isinstance(x, (float, np.floating)):
        return "" if x != x else f"{float(x):.{digits}f}"
    return str(x)


def markdown_table(rows: Iterable[Mapping], columns: Iterable[str]) -> str:
    cols = list(columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(fmt(r.get(c)).replace("|", "/") for c in cols) + " |")
    return "\n".join(lines)


def verdict_markdown(v: Mapping) -> str:
    """The verdict as a page: the decision, every number, the limits and the subgroup block."""
    out = [f"# Round 2 verdict: {v.get('arm')} against {v.get('baseline')}", ""]
    cand = v.get("candidate") or {}
    out.append(f"Family {cand.get('family', '')}: {cand.get('change', '')}. Hypothesis: "
               f"{cand.get('hypothesis', '')}. Primary outcome {cand.get('primary_outcome', '')}, "
               f"decision type {cand.get('decision', '')}.")
    out.append("")
    ad = v.get("adoption") or {}
    out.append(f"**Verdict: {ad.get('verdict', '')}**")
    out.append("")
    for r in ad.get("reasons") or []:
        out.append(f"- {r}")
    out.append("")
    out.append("## Primary comparison")
    out.append("")
    p = v.get("primary") or {}
    out.append(f"Outcome {p.get('outcome', '')} on {p.get('unit', '')}: {fmt(p.get('n'))} units, "
               f"{fmt(p.get('n_clusters'))} clusters.")
    d = p.get("delta") or {}
    out.append(markdown_table([{"statistic": d.get("statistic"), "estimate": d.get("estimate"),
                                "lo": d.get("lo"), "hi": d.get("hi"), "level": d.get("level"),
                                "excludes zero": d.get("excludes_zero"), "p": d.get("p_value")}],
                              ["statistic", "estimate", "lo", "hi", "level", "excludes zero", "p"]))
    out.append("")
    out.append("## Outcomes O1 to O6")
    out.append("")
    rows = []
    for oid in OUTCOME_IDS:
        block = (v.get("outcomes") or {}).get(oid) or {}
        rows.append({"outcome": oid, "arm": block.get("arm_value"), "baseline": block.get("baseline_value"),
                     "delta": block.get("delta_value"), "lo": block.get("lo"), "hi": block.get("hi"),
                     "limit": block.get("limit"), "blocks": block.get("blocks"), "note": block.get("note")})
    out.append(markdown_table(rows, ["outcome", "arm", "baseline", "delta", "lo", "hi", "limit", "blocks", "note"]))
    out.append("")
    sub = v.get("subgroups") or {}
    out.append("## Subgroup block (NARS-9, exploratory)")
    out.append("")
    groups = (sub.get("nars9") or {}).get("groups") or {}
    out.append(markdown_table([{"group": g, "median delta": groups[g]} for g in sorted(groups)],
                              ["group", "median delta"]))
    out.append("")
    out.append((sub.get("nars9") or {}).get("why", ""))
    fam = sub.get("family") or {}
    if fam:
        out.append("")
        out.append("Metric families (exploratory): " + ", ".join(f"{k} {fmt(v_)}" for k, v_ in sorted(fam.items())))
    out.append("")
    cov = v.get("coverage") or {}
    if cov:
        out.append("## Coverage change (O4, reported separately)")
        out.append("")
        out.append(markdown_table(cov.get("rows") or [], cov.get("columns") or []))
        out.append("")
    st = v.get("stamp") or {}
    out.append("## Record")
    out.append("")
    out.append(f"Campaign {st.get('campaign', '')}; protocol {((st.get('protocol') or {}).get('sha256') or '')[:12]}; "
               f"code {((st.get('code') or {}).get('fingerprint') or '')[:12]}; arm config root "
               f"{v.get('armConfigRoot', '')}; baseline config root {v.get('baselineConfigRoot', '')}.")
    return "\n".join(out) + "\n"


__all__ = [
    "PROTOCOL_SCHEMA", "BASELINE_ARM", "FINALIST_ARM", "REHEARSAL_MAINTAINER", "ARM_FILE", "MARGINS",
    "ProtocolMismatch", "KnobError", "sha256_of", "load_protocol", "candidate_ids", "candidate",
    "check_protocol", "stamp", "route_knob", "get_dotted", "set_dotted", "apply_knobs", "arm_record",
    "write_arm", "read_arm", "arm_dir", "build_arm_root", "compose_finalist", "config_tree_fingerprint",
    "apply_revise_dispositions", "render_metric_map", "auc", "spearman", "seed_for", "cluster_bootstrap",
    "interval", "bootstrap_p_value", "paired_delta", "paired_auc_delta", "bh_adjust", "primary_test",
    "o2_limit", "o3_block", "o5_limit", "subgroup_block", "adoption", "OUTCOME_MAPPING", "FUNCTION_IDS",
    "deep_slug", "metric_id_of", "bundle_metrics", "metric_columns", "station_stratum", "active_points",
    "metric_index", "band_of", "unassessed_functions", "rollup", "score_station", "station_list_hash",
    "coverage_of", "usability_of", "hierarchy_cells", "o1_comparison", "o1_from_calls", "o2_summary",
    "markdown_table", "verdict_markdown", "fmt",
]
