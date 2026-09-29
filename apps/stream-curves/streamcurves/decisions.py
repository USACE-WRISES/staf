"""Standing decisions: the owner's class decisions applied per item by policy.

The two pilot regions were adjudicated through class decisions the owner made
once per class of review item and a notes-side tool expanded per item. That
tool now lives here, and the classes live in
``config/methodology/standing_decisions.yaml`` as a versioned, fingerprinted
policy so a batch run can apply them unattended and a replay can prove they
reproduce the pilots' recorded decisions.

Three honesty rules hold throughout.

- A policy decision is stamped ``rationale_origin = "standing_policy:<version>"``
  and ``reviewer = "standing-policy:<id> (pending owner confirmation)"``. The
  marker is what keeps a staged version out of the canonical library until
  :func:`confirm_decisions` replaces it with the confirming owner's name.
- Every rationale quotes the item's own computed evidence and carries
  ``asserts``, so :func:`streamcurves.provenance.apply_reviewer_decisions`
  refuses a contradiction exactly as it does for a human decision.
- An item no enabled entry covers is reported, never dropped. The batch runner
  lists it in the review packet as an open item for the owner.

Pure: dicts in, dicts out. The CLI (``expand``, ``replay``, ``validate``) is the
only file I/O.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import methodology
from .config import read_yaml
from .paths import CONFIG_DIR

POLICY_PATH = CONFIG_DIR / "methodology" / "standing_decisions.yaml"
PENDING_SUFFIX = "(pending owner confirmation)"
ALLOWED_ACTIONS = ("accept", "accept_with_conditions", "modify", "reject",
                   "request_additional_analysis")
#: policy 1.5 adds remove_metric: a reject that takes the curve out of scoring
#: (the batch runner feeds it to assembly as a removal)
SIDE_EFFECTS = ("portfolio_approval", "finalize_metric", "coverage_exception", "remove_metric")
_OPS = ("eq", "ne", "lt", "lte", "gt", "gte", "in")
#: the policy era of a document that recorded none (the pilots): every entry a
#: later policy retired still applies to it, nothing introduced later does
ERA_UNRECORDED = "0"


# --------------------------------------------------------------------------- #
# Policy file
# --------------------------------------------------------------------------- #
def load_policy(path: Path | str | None = None) -> dict:
    """The policy document, with the file's sha256 and path attached under
    ``meta`` so a manifest can fingerprint what was applied."""
    p = Path(path) if path else POLICY_PATH
    doc = read_yaml(p) or {}
    meta = dict(doc.get("meta") or {})
    meta["path"] = str(p)
    meta["sha256"] = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
    doc["meta"] = meta
    doc["entries"] = list(doc.get("entries") or [])
    return doc


def policy_version(policy: dict) -> str:
    return str((policy.get("meta") or {}).get("policy_version") or "")


def entries_by_id(policy: dict) -> dict[str, dict]:
    return {str(e.get("id")): e for e in policy.get("entries") or [] if e.get("id")}


def _version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])


def entry_applies(entry: dict, era: Optional[str], current: str) -> bool:
    """Whether an entry belongs to the policy era a document was decided under.

    Policy 1.5 (methodology 0.16): an entry may carry ``introduced_in_policy``
    and ``retired_in_policy``. It applies to a document recorded under policy
    ``era`` when the era is at or after its introduction and before its
    retirement. ``era`` None is a new build under ``current``; a document that
    recorded no policy (the pilots) is :data:`ERA_UNRECORDED`, so every entry a
    later policy retired still replays it and nothing introduced later touches it.
    """
    at = _version_tuple(current if era is None else era)
    intro = entry.get("introduced_in_policy")
    retired = entry.get("retired_in_policy")
    if intro and at < _version_tuple(intro):
        return False
    if retired and at >= _version_tuple(retired):
        return False
    return True


def recorded_policy_version(doc: Optional[dict]) -> str:
    """The policy version a provenance document recorded
    (``manifest.standingDecisions.policyVersion``), else :data:`ERA_UNRECORDED`."""
    sd = ((doc or {}).get("manifest") or {}).get("standingDecisions") or {}
    return str(sd.get("policyVersion") or ERA_UNRECORDED)


def validate_policy(policy: dict) -> list[str]:
    """Every way the policy file could be wrong, as plain sentences. Empty means
    the file is usable."""
    problems: list[str] = []
    meta = policy.get("meta") or {}
    if not meta.get("policy_version"):
        problems.append("meta.policy_version is missing")
    current = methodology.methodology_version() or ""
    declared = str(meta.get("methodology_version") or "")
    if declared and _version_tuple(declared) != _version_tuple(current):
        problems.append(f"meta.methodology_version {declared!r} is not the current "
                        f"methodology {current!r}")
    seen: set[str] = set()
    known_rules = set(methodology.rule_ids())
    sample = _sample_evidence()
    for i, e in enumerate(policy.get("entries") or []):
        eid = str(e.get("id") or "")
        where = f"entry {i} ({eid or 'no id'})"
        if not eid:
            problems.append(f"{where}: id is missing")
        elif eid in seen:
            problems.append(f"{where}: duplicate id")
        seen.add(eid)
        if str(e.get("rule_id")) not in known_rules:
            problems.append(f"{where}: rule_id {e.get('rule_id')!r} is not in the catalog")
        if str(e.get("action")) not in ALLOWED_ACTIONS:
            problems.append(f"{where}: action {e.get('action')!r} is not allowed")
        if e.get("side_effect") and e["side_effect"] not in SIDE_EFFECTS:
            problems.append(f"{where}: unknown side_effect {e['side_effect']!r}")
        if not isinstance(e.get("match"), dict) or not e.get("match"):
            problems.append(f"{where}: match conditions are missing")
        else:
            for fld, cond in e["match"].items():
                if not isinstance(cond, dict) or not cond:
                    problems.append(f"{where}: match.{fld} must be an operator map")
                    continue
                for op, val in cond.items():
                    if op not in _OPS:
                        problems.append(f"{where}: match.{fld} uses unknown operator {op!r}")
                    try:
                        _resolve(val)
                    except KeyError as exc:
                        problems.append(f"{where}: match.{fld}: {exc}")
        approved = str(e.get("approved_under") or "")
        if approved and _version_tuple(approved) > _version_tuple(current):
            problems.append(f"{where}: approved_under {approved!r} is newer than the "
                            f"current methodology {current!r}")
        # policy 1.5: the era keys are policy versions no newer than this file's
        this_policy = str(meta.get("policy_version") or "")
        for key in ("introduced_in_policy", "retired_in_policy"):
            value = e.get(key)
            if value is None:
                continue
            if not _version_tuple(str(value)):
                problems.append(f"{where}: {key} {value!r} is not a policy version")
            elif this_policy and _version_tuple(str(value)) > _version_tuple(this_policy):
                problems.append(f"{where}: {key} {value!r} is newer than this policy "
                                f"{this_policy!r}")
        if e.get("retired_in_policy") and not e.get("enabled", False):
            problems.append(f"{where}: a retired entry stays enabled, so the replay of the "
                            "versions decided under it still applies it")
        # No fabricated approvals (campaign Round 2 close): an entry with no approval
        # date must say in approved_under that the owner's confirmation is pending,
        # and a legacy entry is never enabled by default.
        if not e.get("approved_on") and "pending" not in approved.lower():
            problems.append(f"{where}: approved_on is empty, so approved_under must state "
                            "that owner confirmation is pending")
        if e.get("legacy") and e.get("enabled", False):
            problems.append(f"{where}: a legacy entry cannot be enabled by default")
        aliases = e.get("aliases")
        if aliases is not None and (not isinstance(aliases, list)
                                    or not all(isinstance(a, str) and a for a in aliases)):
            problems.append(f"{where}: aliases must be a list of class names")
        if not e.get("rationale"):
            problems.append(f"{where}: rationale template is missing")
        else:
            try:
                str(e["rationale"]).format_map(_Evidence({**sample, "policy_id": eid,
                                                          "policy_version": "x"}))
            except (KeyError, ValueError, IndexError) as exc:
                problems.append(f"{where}: rationale template does not format: {exc}")
    return problems


def _sample_evidence() -> dict:
    """Placeholder values for every field a template may cite, so validation can
    format each template without a run."""
    keys = ("decision_flip", "driver", "max_param_change_frac", "max_param_change_iqr",
            "disposition", "n_reference", "missing_fraction", "stability", "category",
            "min_level_n", "level_counts", "tier", "n_metrics", "bundle_n_metrics",
            "metrics", "max_within_function_abs_spearman", "reference_tier", "n_retained",
            "curve_status", "domain_violations", "structure_stability", "shape_stability",
            "spearman", "equals_default_portfolio",
            # COV-01 (v0.14): a function no source supports
            "function_id", "candidates", "candidates_text", "n_candidates",
            "blockers_documented",
            # REF-05 (v0.12): a borrowed reference pool
            "transfer_risk", "n_usable", "n_local", "self_coverage", "level", "level_label",
            "region_code", "region_name",
            # policy 1.3 (campaign Round 2 close): CURVE-07 fallback curves, CURVE-12
            # thin inversions, RED-01 pairs, CURVE-06 intervals, STRAT-09 candidates
            "reasons", "domain_min", "domain_max", "curve12_verdict", "curve12_auc",
            "curve12_inverted", "curve07_status", "verdict", "auc_ref_vs_pressure", "n_ref",
            "n_pressure", "min_group_n", "group_floor_n", "median_ref_index",
            "median_pressure_index", "same_function", "pearson", "evaluable", "n_boot",
            "n_matched", "n_metrics_tested", "n_significant", "consistency_score")
    return _derived({k: 0 for k in keys})


# --------------------------------------------------------------------------- #
# Evidence formatting (from the notes tool, unchanged in spirit)
# --------------------------------------------------------------------------- #
class _Evidence(dict):
    def __missing__(self, key):
        return f"<{key} not computed>"


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if math.isnan(value):
            return "not computed"
        return f"{value:.3g}" if abs(value) < 1000 else f"{value:,.0f}"
    if isinstance(value, (list, tuple)):
        return ", ".join(_fmt(v) for v in value)
    if value is None:
        return "not computed"
    return str(value)


def _derived(evidence: dict) -> dict:
    """Human-readable derivations the rationale templates can cite."""
    out = {k: _fmt(v) for k, v in evidence.items()}
    if "decision_flip" in evidence:
        out["decision_flip_text"] = (
            "the drop changes the build (decision flip: yes)"
            if evidence.get("decision_flip") else
            "no decision flip (the build validates with or without the site)")
    if isinstance(evidence.get("max_param_change_frac"), (int, float)):
        out["max_param_change_pct"] = f"{100 * float(evidence['max_param_change_frac']):.0f} percent"
    if isinstance(evidence.get("max_param_change_iqr"), (int, float)):
        out["max_param_change_iqr_text"] = f"{float(evidence['max_param_change_iqr']):.2f} IQR"
    if isinstance(evidence.get("structure_stability"), (int, float)):
        out["structure_pct"] = f"{100 * float(evidence['structure_stability']):.0f} percent"
    if isinstance(evidence.get("shape_stability"), (int, float)):
        out["shape_pct"] = f"{100 * float(evidence['shape_stability']):.0f} percent"
    if isinstance(evidence.get("missing_fraction"), (int, float)):
        out["missing_pct"] = f"{100 * float(evidence['missing_fraction']):.0f} percent"
    if isinstance(evidence.get("stability"), (int, float)):
        out["stability_pct"] = f"{100 * float(evidence['stability']):.0f} percent"
    if isinstance(evidence.get("spearman"), (int, float)):
        out["spearman_text"] = f"{float(evidence['spearman']):.3f}"
    if isinstance(evidence.get("max_within_function_abs_spearman"), (int, float)):
        out["max_within_function_abs_spearman_text"] = (
            f"{float(evidence['max_within_function_abs_spearman']):.3f}")
    elif "max_within_function_abs_spearman" in evidence:
        out["max_within_function_abs_spearman_text"] = "none computed"
    if isinstance(evidence.get("metrics"), (list, tuple)):
        out["metrics_text"] = ", ".join(str(m) for m in evidence["metrics"]) or "none"
    return out


# --------------------------------------------------------------------------- #
# Evidence enrichment: the derived fields the match conditions read
# --------------------------------------------------------------------------- #
def _records_by_key(doc: dict) -> dict[tuple[str, str], dict]:
    return {(str(r.get("rule_id")), str(r.get("subject"))): r
            for r in (doc.get("records") or [])}


def _parse_level_counts(value) -> list[int]:
    """The populated class sizes of a stratifier candidate. The manifest writes
    them as ``label=count|label=count`` (``Headwater (<= 10 km2)=72|...``), so a
    labelled string is split on ``|`` and read after each ``=``; a digit scan of
    the whole string would read the labels' own numbers (10, 100, 0.5) as
    counts, which is what policy 1.2 did (see ``enrich_evidence``)."""
    if value is None:
        return []
    if isinstance(value, dict):
        vals = value.values()
    elif isinstance(value, (list, tuple)):
        vals = value
    elif "=" in str(value):
        vals = [part.rsplit("=", 1)[1] for part in str(value).split("|") if "=" in part]
    else:
        vals = re.findall(r"\d+", str(value))
    out = []
    for v in vals:
        try:
            out.append(int(str(v).strip()))
        except (TypeError, ValueError):
            continue
    return out


def _bundle_function_metrics(bundle: Optional[dict]) -> dict[str, list[str]]:
    """functionId -> the metric codes in the bundle block (the published
    ``metricId`` is ``spring-<slug>``; the code is what the records use)."""
    out: dict[str, list[str]] = {}
    for block in ((bundle or {}).get("metricsByFunction") or []):
        fid = str(block.get("functionId") or "")
        out[fid] = [str(m.get("metricId") or "") for m in (block.get("metrics") or [])]
    return out


def _metric_matches_id(metric_code: str, metric_id: str) -> bool:
    """``spring-phab-pct-safn`` is the bundle id of ``phab_PCT_SAFN``."""
    code = re.sub(r"[^a-z0-9]+", "-", str(metric_code).lower()).strip("-")
    mid = str(metric_id).lower()
    return mid == f"spring-{code}" or mid == code


def _default_function_metrics(function_id: str, metric_codes: list[str]) -> Optional[bool]:
    """Whether every bundle metric on the function is one the agent's default
    mapping assigns to it. The default mapping is the union the bundle is built
    from (``staf_library.default_discipline_function_mapping``: the STAF metric
    library plus ``metric_map.yaml``), so a metric that informs a second
    function through the library (width-to-depth ratio on low-flow dynamics)
    counts as default, and only a reviewer-edited mapping reads False. None
    when the crosswalk is unavailable."""
    try:
        from . import regional_agent, staf_library  # local: keeps import light
        mapping = staf_library.default_discipline_function_mapping(list(metric_codes))
    except Exception:  # noqa: BLE001
        return None
    assigned: dict[str, set[str]] = {}
    for row in mapping.itertuples(index=False):
        r = row._asdict()
        label = r.get("function_label")
        mk = r.get("metric_key")
        if label is None or mk is None or str(label).strip() == "" or str(mk).startswith("lib:"):
            continue
        fid = regional_agent._canonical_function_id(str(label))
        if fid:
            assigned.setdefault(str(mk), set()).add(str(fid))
    for code in metric_codes:
        if str(function_id) not in assigned.get(str(code), set()):
            return False
    return True


def enrich_evidence(item: dict, doc: dict, *, bundle: Optional[dict] = None,
                    result: Optional[dict] = None) -> dict:
    """The item's computed evidence plus the derived fields the policy reads.

    Works from a provenance document alone (replay on a published version), and
    reads the bundle for SELECT-01 where one is supplied.
    """
    ev = dict(item.get("evidence") or {})
    rule_id = str((item.get("rule_ids") or [""])[0])
    subject = str(item.get("subject") or "")
    records = _records_by_key(doc)
    manifest = doc.get("manifest") or {}

    if rule_id in ("DATA-03", "DATA-05", "DATA-06", "CURVE-07"):
        for rid in ("DATA-04", "DATA-05", "DATA-06"):
            rec = records.get((rid, subject))
            if rec and (rec.get("inputs") or {}).get("n_reference") is not None:
                ev.setdefault("n_reference", rec["inputs"]["n_reference"])
                break
        rec = records.get(("DATA-03", subject)) or records.get(("DATA-02", subject)) \
            or records.get(("DATA-01", subject))
        if rec and (rec.get("inputs") or {}).get("missing_fraction") is not None:
            ev.setdefault("missing_fraction", rec["inputs"]["missing_fraction"])
    if rule_id == "REF-02":
        rec = records.get(("REF-01", "reference_screen"))
        if rec:
            ev.setdefault("n_retained", (rec.get("computed") or {}).get("n_retained"))
    if rule_id == "STRAT-09":
        # The candidate's smallest populated class: the manifest's own
        # ``min_populated_n`` where the build recorded it, else the counts parsed
        # from ``level_counts``. Policy 1.2 scanned the digits of the labelled
        # string, so its records name the labels' numbers as class sizes; the
        # replay reads those records under strat09-advisory-not-applied's alias.
        for cand in ((manifest.get("stratifiers") or {}).get("candidates") or []):
            if str(cand.get("stratification")) == subject:
                counts = _parse_level_counts(cand.get("level_counts"))
                ev["level_counts"] = "/".join(str(c) for c in counts) if counts else None
                recorded_min = cand.get("min_populated_n")
                if isinstance(recorded_min, (int, float)) and not isinstance(recorded_min, bool):
                    ev["min_level_n"] = int(recorded_min)
                else:
                    ev["min_level_n"] = min(counts) if counts else None
                break
    if rule_id in ("CURVE-06", "CURVE-07"):
        # Policy 1.3: a fallback curve is accepted only when the discrimination
        # check does not contradict it. A build before CURVE-12 (methodology 0.12)
        # has no record for the metric: ``not_recorded`` and not inverted.
        rec = records.get(("CURVE-12", subject))
        comp = (rec.get("computed") or {}) if rec else {}
        verdict = comp.get("verdict") if rec else None
        ev.setdefault("curve12_verdict", str(verdict) if verdict is not None else "not_recorded")
        ev.setdefault("curve12_auc", comp.get("auc_ref_vs_pressure") if rec else None)
        ev["curve12_inverted"] = str(ev.get("curve12_verdict")) == "inverted"
    if rule_id == "CURVE-06":
        # the curve's own status, so a missing interval is read on a fallback ramp
        # (CURVE-07 degenerate) and on nothing else
        rec = records.get(("CURVE-07", subject))
        status = (rec.get("computed") or {}).get("curve_status") if rec else None
        if status is not None:
            ev.setdefault("curve07_status", str(status))
    if rule_id == "CURVE-12":
        # the smaller of the two groups the AUC rests on, against the DATA-04
        # automated floor (the value the rationale cites)
        sizes = [float(ev[k]) for k in ("n_ref", "n_pressure")
                 if isinstance(ev.get(k), (int, float)) and not isinstance(ev.get(k), bool)]
        if sizes:
            ev["min_group_n"] = int(min(sizes))
        ev["group_floor_n"] = methodology.threshold("data_rules.min_n_unstratified")
    if rule_id == "SELECT-01":
        by_fid = _bundle_function_metrics(bundle if bundle is not None
                                          else (result or {}).get("bundle"))
        ids = by_fid.get(subject)
        if ids is not None:
            ev["bundle_n_metrics"] = len(ids)
            # the metric codes as the records name them
            codes = []
            for mid in ids:
                code = next((s for (rid, s) in records if rid == "CURVE-01"
                             and _metric_matches_id(s, mid)), None)
                codes.append(code or mid)
            ev["metrics"] = codes
            ev["equals_default_portfolio"] = _default_function_metrics(subject, codes)
            worst = None
            for (rid, pair), rec in records.items():
                if rid != "RED-01" or "|" not in pair:
                    continue
                a, b = pair.split("|", 1)
                if a in codes and b in codes:
                    sp = (rec.get("computed") or {}).get("spearman")
                    if isinstance(sp, (int, float)):
                        worst = max(worst or 0.0, abs(float(sp)))
            ev["max_within_function_abs_spearman"] = worst if worst is not None else 0.0
    return ev


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def _resolve(value):
    """A literal, or a methodology threshold named as ``$methodology:<path>``."""
    if isinstance(value, str) and value.startswith("$methodology:"):
        return methodology.threshold(value.split(":", 1)[1])
    return value


def _compare(op: str, actual, expected) -> bool:
    if op == "eq":
        return _norm(actual) == _norm(expected)
    if op == "ne":
        return _norm(actual) != _norm(expected)
    if op == "in":
        return _norm(actual) in [_norm(v) for v in (expected or [])]
    try:
        a, e = float(actual), float(expected)
    except (TypeError, ValueError):
        return False
    return {"lt": a < e, "lte": a <= e, "gt": a > e, "gte": a >= e}[op]


def _norm(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None if v is None else str(v)


def match(entry: dict, item: dict, evidence: dict) -> bool:
    """True when the entry governs this item: same rule (and trigger when the
    entry names one) and every match condition holds on the evidence. A
    condition on a field the evidence lacks is False, never a pass."""
    if str(entry.get("rule_id")) not in [str(r) for r in (item.get("rule_ids") or [])]:
        return False
    if entry.get("trigger") and str(entry["trigger"]) != str(item.get("trigger")):
        return False
    for fld, cond in (entry.get("match") or {}).items():
        if fld not in evidence or evidence.get(fld) is None:
            return False
        for op, val in (cond or {}).items():
            if not _compare(op, evidence.get(fld), _resolve(val)):
                return False
    return True


# --------------------------------------------------------------------------- #
# Applying the policy
# --------------------------------------------------------------------------- #
@dataclass
class PolicyResult:
    decisions: list[dict] = field(default_factory=list)
    uncovered: list[dict] = field(default_factory=list)
    hard_stops: list[dict] = field(default_factory=list)
    finalize_metrics: dict[str, str] = field(default_factory=dict)
    remove_metrics: dict[str, str] = field(default_factory=dict)
    portfolio_approvals: list[dict] = field(default_factory=list)
    applied_ids: list[str] = field(default_factory=list)
    #: COV-01 (v0.14): documented-gap exceptions, recorded pending owner confirmation
    coverage_exceptions: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"decisions": self.decisions, "uncovered": self.uncovered,
                "hard_stops": self.hard_stops, "finalize_metrics": self.finalize_metrics,
                "remove_metrics": self.remove_metrics,
                "portfolio_approvals": self.portfolio_approvals,
                "coverage_exceptions": self.coverage_exceptions,
                "applied_ids": self.applied_ids}


def pending_reviewer(entry_id: str, policy: dict) -> str:
    suffix = str((policy.get("meta") or {}).get("pending_reviewer_suffix") or PENDING_SUFFIX)
    return f"standing-policy:{entry_id} {suffix}"


def enabled_entries(policy: dict, enabled: Optional[list[str]] = None, *,
                    era: Optional[str] = None) -> list[dict]:
    """The entries that apply: the enabled ones plus ``enabled`` (the per-run
    opt-ins), kept to the policy era ``era`` (:func:`entry_applies`; None is a
    new build under this policy's version)."""
    extra = {str(e) for e in (enabled or [])}
    known = entries_by_id(policy)
    unknown = extra - set(known)
    if unknown:
        raise ValueError(f"--enable-policy names unknown entries: {', '.join(sorted(unknown))}")
    current = policy_version(policy)
    return [e for e in policy.get("entries") or []
            if (e.get("enabled", False) or str(e.get("id")) in extra)
            and entry_applies(e, era, current)]


def apply_policy(doc: dict, policy: dict, *, bundle: Optional[dict] = None,
                 result: Optional[dict] = None, enabled: Optional[list[str]] = None,
                 date: Optional[str] = None, include_resolved: bool = False,
                 era: Optional[str] = None) -> PolicyResult:
    """Expand the enabled entries over the document's open queue items.

    Returns every decision made, every open item no entry covered (the owner's
    work), the hard stops among them (blocking items and the statuses the policy
    never finalizes), and the side effects the batch runner must feed back into
    assembly (finalizations, removals, portfolio approvals). ``era`` is the
    policy version the document was decided under (:func:`replay` passes the
    recorded one); None is a new build under this policy.
    """
    from . import provenance as pv  # local, matching the lint import in _confirm
    active = enabled_entries(policy, enabled, era=era)
    version = policy_version(policy)
    origin = f"{(policy.get('meta') or {}).get('rationale_origin') or 'standing_policy'}:{version}"
    out = PolicyResult()
    queue = (doc.get("reviewQueue") or {}).get("items") or []
    for item in queue:
        if item.get("status") != "open" and not include_resolved:
            continue
        ev = enrich_evidence(item, doc, bundle=bundle, result=result)
        entry = next((e for e in active if match(e, item, ev)), None)
        if entry is None:
            rec = {"item_id": item.get("item_id"), "rule_id": (item.get("rule_ids") or [None])[0],
                   "subject": item.get("subject"), "trigger": item.get("trigger"),
                   "question": item.get("question"), "blocking": bool(item.get("blocking")),
                   "evidence": ev}
            out.uncovered.append(rec)
            if item.get("blocking") or item.get("trigger") in pv.UNCOVERED_HARD_STOP_TRIGGERS:
                out.hard_stops.append(rec)
            continue
        eid = str(entry["id"])
        fields = _Evidence({**_derived(ev), "policy_id": eid, "policy_version": version})
        rationale = str(entry["rationale"]).format_map(fields)
        rationale = re.sub(r"\s+", " ", rationale).strip()
        raw = item.get("evidence") or {}
        asserts = {k: raw.get(k) for k in (entry.get("asserts") or []) if k in raw}
        decision = {
            "rule_id": (item.get("rule_ids") or [None])[0],
            "subject": item.get("subject"),
            "reviewer": pending_reviewer(eid, policy),
            "date": date,
            "action": entry["action"],
            "rationale": rationale,
            "decision_class": eid,
            "rationale_origin": origin,
            "asserts": asserts,
            "policy_entry": eid,
        }
        out.decisions.append(decision)
        if eid not in out.applied_ids:
            out.applied_ids.append(eid)
        effect = entry.get("side_effect")
        if effect == "finalize_metric":
            out.finalize_metrics[str(item.get("subject"))] = rationale
        elif effect == "remove_metric":
            out.remove_metrics[str(item.get("subject"))] = rationale
        elif effect == "portfolio_approval":
            out.portfolio_approvals.append({
                "functionId": str(item.get("subject")),
                "approvedBy": pending_reviewer(eid, policy),
                "note": f"standing decision {eid} (policy {version}): {rationale}",
            })
        elif effect == "coverage_exception":
            out.coverage_exceptions.append(
                _coverage_exception(str(item.get("subject")), result,
                                    recorded_by=pending_reviewer(eid, policy),
                                    fallback=rationale))
    return out


def _coverage_exception(function_id: str, result: Optional[dict], *, recorded_by: str,
                        fallback: str) -> dict:
    """The documented-gap exception COV-01 records for one function: the
    workflow's own justification, naming each source's blocker, under the
    pending reviewer the owner confirms at promote."""
    from . import methodology as _m
    from . import pressure_evidence as pe
    drafts = {d["functionId"]: d for d in
              (pe.coverage_exceptions_draft(result, recorded_by=recorded_by) if result else [])}
    got = drafts.get(function_id) or {
        "functionId": function_id,
        "reason": _m.threshold("coverage.documented_gap_reason",
                               "insufficient-reference-support"),
        "justification": fallback, "recordedBy": recorded_by, "recordedAt": None}
    return {**got, "recordedBy": recorded_by, "policyEntry": "cov01-documented-gap"}


def confirm_decisions(decisions: list[dict], *, reviewer: str, date: str) -> list[dict]:
    """The owner's confirmation at the end review: the pending reviewer marker
    is replaced by the confirming owner's name, the origin stays
    ``standing_policy:<version>`` so the record never claims a per-item human
    rationale it does not have."""
    reviewer = str(reviewer or "").strip()
    if not reviewer:
        raise ValueError("a confirming reviewer name is required")
    out = []
    for d in decisions:
        c = dict(d)
        c["reviewer"] = reviewer
        c["date"] = date
        c["confirmed_by"] = reviewer
        c["confirmed_at"] = date
        out.append(c)
    return out


# Paths inside a provenance document that quote the marker as an *input*, never
# as a decision: the recorded command line carries the owner's own
# --approve-portfolio string (``FUNCTIONID=owner-draft (pending owner
# confirmation):NOTE``) verbatim.
PENDING_EXEMPT_PATHS = (("manifest", "agent", "argv"),)


def pending_locations(doc) -> list[str]:
    """Dotted paths of every string in a provenance document that carries the
    pending-confirmation marker, the recorded command line excepted."""
    found: list[str] = []

    def walk(node, path: tuple):
        if path in PENDING_EXEMPT_PATHS:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + (str(k),))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, path + (str(i),))
        elif isinstance(node, str) and PENDING_SUFFIX in node:
            found.append(".".join(path))
        elif node is not None and not isinstance(node, (int, float, bool, str)):
            if PENDING_SUFFIX in json.dumps(node, default=str):
                found.append(".".join(path))

    walk(doc, ())
    return found


def pending_approvals(approvals) -> list[dict]:
    """The SELECT-01 approvals a standing decision recorded for the owner to confirm."""
    return [a for a in approvals or [] if PENDING_SUFFIX in str((a or {}).get("approvedBy") or "")]


def pending_exceptions(entries) -> list[dict]:
    """The COV-01 documented gaps a standing decision recorded for the owner to confirm."""
    return [e for e in entries or []
            if isinstance(e, dict) and PENDING_SUFFIX in str(e.get("recordedBy") or "")]


def confirm_approvals(approvals, *, maintainer: str, date: str) -> int:
    """Rewrite every pending approver in ``approvals`` (in place) to the confirming
    owner. Returns how many were confirmed. Promote and the workspace publish
    both confirm this way; promote also refuses any approval by someone else."""
    n = 0
    for ap in approvals or []:
        if PENDING_SUFFIX in str((ap or {}).get("approvedBy") or ""):
            ap["approvedBy"] = maintainer
            ap["confirmedAt"] = date
            n += 1
    return n


def confirm_exceptions(entries, *, maintainer: str, date: str) -> int:
    """Put the confirming owner's name on every pending documented gap (in place).
    Returns how many were confirmed."""
    n = 0
    for e in entries or []:
        if isinstance(e, dict) and PENDING_SUFFIX in str(e.get("recordedBy") or ""):
            e["recordedBy"] = maintainer
            e["recordedAt"] = date
            e["confirmedBy"] = maintainer
            n += 1
    return n


def is_pending(text_or_doc) -> bool:
    """Whether a provenance document (or any JSON text) still carries a
    pending-confirmation reviewer. A document is walked so the recorded
    command line, which quotes the marker as an input, does not count."""
    if isinstance(text_or_doc, str):
        return PENDING_SUFFIX in text_or_doc
    return bool(pending_locations(text_or_doc))


# --------------------------------------------------------------------------- #
# Confirming a staged document under the owner's name
# --------------------------------------------------------------------------- #
#: Actions that would change what the staged version contains (a finalization,
#: removal, or approval would move), so they can never ride in as a confirmation
#: override; the owner re-stages with the decision as an input instead.
SCOPE_CHANGING = ("reject", "request_additional_analysis")

#: A rationale origin ending in this was drafted by the owner ahead of the end
#: review; confirmation turns it into "owner approved".
PENDING_ORIGIN_SUFFIX = "_pending_owner_approval"


def confirm_pending_decisions(doc: dict, *, reviewer: str, date: str,
                              overrides: Optional[dict] = None) -> tuple[dict, list[str]]:
    """Rewrite the pending reviewer on every policy decision to the confirming
    owner, apply rationale-level overrides, and re-run the consistency lint.

    The one implementation both confirmation paths share: ``promote`` calls it
    with the end review's ``--override`` map, and an in-app publish of an
    edited agent build calls it with none, because the human publishing after
    the full in-app review is the owner confirming. Mutates ``doc`` in place
    and returns ``(doc, applied_override_keys)``. Raises ``ValueError`` on a
    scope-changing override, a rationale that contradicts its record, or an
    override naming an unknown item; the caller owns the exit."""
    from . import provenance as pv  # local: the document lint lives there

    overrides = dict(overrides or {})
    applied: list[str] = []
    for rec in doc.get("records") or []:
        key = f"{rec.get('rule_id')}:{rec.get('subject')}"
        ov = overrides.get(key)
        pending = PENDING_SUFFIX in str(rec.get("reviewer") or "")
        if ov:
            action, rationale = ov
            if action in SCOPE_CHANGING or (rec.get("reviewer_action") in SCOPE_CHANGING
                                            and action not in SCOPE_CHANGING):
                raise ValueError(
                    f"override {key}={action} changes the scope of the staged version "
                    "(a finalization, removal, or approval would change). Re-stage with the "
                    "decision as an input instead of overriding at promote.")
            rec["reviewer_action"] = action
            rec["reviewer_rationale"] = rationale
            rec["reviewer_decision_class"] = "owner-override"
            rec["reviewer_rationale_origin"] = "owner_written"
            applied.append(key)
            pending = True
        if pending or ov:
            problems = pv.decision_consistency_problems(
                rec, {"rationale": rec.get("reviewer_rationale"),
                      "asserts": rec.get("reviewer_asserts") or {}})
            if problems:
                raise ValueError(f"{key}: " + "; ".join(problems))
            rec["reviewer"] = reviewer
            rec["reviewed_at"] = date
            origin = str(rec.get("reviewer_rationale_origin") or "")
            if origin.endswith(PENDING_ORIGIN_SUFFIX):
                # an owner-drafted entry staged ahead of the end review: the
                # confirmation turns "pending owner approval" into "owner approved"
                rec["reviewer_rationale_origin"] = (origin[: -len(PENDING_ORIGIN_SUFFIX)]
                                                    + "_owner_approved")
    for item in (doc.get("reviewQueue") or {}).get("items") or []:
        key = item.get("item_id")
        ov = overrides.get(key)
        if ov:
            item["reviewer_action"], item["reviewer_rationale"] = ov
        if PENDING_SUFFIX in str(item.get("reviewer") or "") or ov:
            item["reviewer"] = reviewer
            item["reviewed_at"] = date
    unknown = sorted(set(overrides) - set(applied)
                     - {i.get("item_id") for i in (doc.get("reviewQueue") or {}).get("items") or []})
    if unknown:
        raise ValueError(f"overrides name items that are not on the record: {', '.join(unknown)}")
    sd = (doc.get("manifest") or {}).get("standingDecisions")
    if isinstance(sd, dict):
        sd["confirmedBy"] = reviewer
        sd["confirmedAt"] = date
        sd["overrides"] = applied
    return doc, applied


# --------------------------------------------------------------------------- #
# Replay against a published version
# --------------------------------------------------------------------------- #
MATCH = "match"
ALIAS_MATCH = "alias_match"
STRICTER_OPEN = "stricter_open"
MISMATCH = "mismatch"
#: Policy 1.3 (campaign Round 2 close): the policy decides an item the published
#: record left open. Not a mismatch (the record contradicts nothing), counted on
#: its own so the acceptance tests pin every such item with the entry that
#: decided it.
POLICY_DECIDES_OPEN = "policy_decides_open"
#: The record carries an owner-written decision (a per-item rationale, no class)
#: with the same action the policy takes. The class comparison has nothing to
#: compare, so the agreement in action is reported under its own name.
OWNER_ACTION_MATCH = "owner_action_match"
OWNER_WRITTEN_ORIGIN = "owner_written"


@dataclass
class ReplayReport:
    version_dir: str
    rows: list[dict] = field(default_factory=list)
    owner_only_records: list[dict] = field(default_factory=list)
    portfolio_approvals_not_derived: list[str] = field(default_factory=list)

    def counts(self) -> dict:
        out: dict[str, int] = {}
        for r in self.rows:
            out[r["outcome"]] = out.get(r["outcome"], 0) + 1
        return out

    def rows_with(self, outcome: str) -> list[dict]:
        return [r for r in self.rows if r["outcome"] == outcome]

    def mismatches(self) -> list[dict]:
        return self.rows_with(MISMATCH)

    def policy_decides_open(self) -> dict[str, str]:
        """``{item_id: policy entry}`` of every item the record left open and the
        policy decides, so a test can state each one."""
        return {r["item_id"]: r["policy_class"] for r in self.rows_with(POLICY_DECIDES_OPEN)}

    def as_dict(self) -> dict:
        return {"version_dir": self.version_dir, "counts": self.counts(), "rows": self.rows,
                "owner_only_records": self.owner_only_records,
                "portfolio_approvals_not_derived": self.portfolio_approvals_not_derived}


def replay(version_dir: Path | str, policy: dict, *,
           enabled: Optional[list[str]] = None) -> ReplayReport:
    """Apply the policy to a published version's own review queue (offline, no
    recomputation) and compare each decision with what was recorded."""
    vdir = Path(version_dir)
    doc = json.loads((vdir / "provenance.json").read_text(encoding="utf-8"))
    bundle = json.loads((vdir / "assessment.deep.json").read_text(encoding="utf-8"))
    meta = json.loads((vdir / "meta.json").read_text(encoding="utf-8")) \
        if (vdir / "meta.json").exists() else {}
    # policy 1.5: a version is replayed under the policy era it recorded, so an
    # entry retired since still decides the items it decided then
    res = apply_policy(doc, policy, bundle=bundle, enabled=enabled, include_resolved=True,
                       era=recorded_policy_version(doc))
    by_key = {(str(d["rule_id"]), str(d["subject"])): d for d in res.decisions}
    aliases = {str(e.get("id")): set(e.get("aliases") or []) for e in policy.get("entries") or []}
    records = _records_by_key(doc)
    report = ReplayReport(version_dir=str(vdir))
    for item in (doc.get("reviewQueue") or {}).get("items") or []:
        key = (str((item.get("rule_ids") or [None])[0]), str(item.get("subject")))
        rec = records.get(key) or {}
        published_class = rec.get("reviewer_decision_class")
        published_action = rec.get("reviewer_action")
        published_origin = rec.get("reviewer_rationale_origin")
        d = by_key.get(key)
        if d is None:
            outcome = STRICTER_OPEN
            policy_class = policy_action = None
        else:
            policy_class, policy_action = d["decision_class"], d["action"]
            if not published_action:
                outcome = POLICY_DECIDES_OPEN
            elif published_action != policy_action:
                outcome = MISMATCH
            elif published_class == policy_class:
                outcome = MATCH
            elif published_class in aliases.get(policy_class, set()):
                outcome = ALIAS_MATCH
            elif not published_class and str(published_origin) == OWNER_WRITTEN_ORIGIN:
                outcome = OWNER_ACTION_MATCH
            else:
                outcome = MISMATCH
        report.rows.append({
            "item_id": item.get("item_id"), "trigger": item.get("trigger"),
            "published_class": published_class, "published_action": published_action,
            "published_origin": published_origin,
            "policy_class": policy_class, "policy_action": policy_action,
            "outcome": outcome,
        })
    in_queue = {(str((i.get("rule_ids") or [None])[0]), str(i.get("subject")))
                for i in (doc.get("reviewQueue") or {}).get("items") or []}
    for key, rec in records.items():
        if rec.get("reviewer_action") and key not in in_queue:
            report.owner_only_records.append({
                "rule_id": key[0], "subject": key[1],
                "decision_class": rec.get("reviewer_decision_class"),
                "rationale_origin": rec.get("reviewer_rationale_origin")})
    derived = {a["functionId"] for a in res.portfolio_approvals}
    for a in meta.get("portfolioApprovals") or []:
        if str(a.get("functionId")) not in derived:
            report.portfolio_approvals_not_derived.append(str(a.get("functionId")))
    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Standing decisions: expand, replay, validate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("expand", help="expand the policy over a run's review_queue.json")
    p.add_argument("--queue", required=True)
    p.add_argument("--provenance", default=None,
                   help="the run's decision_provenance_log.json (records), for derived fields")
    p.add_argument("--bundle", default=None)
    p.add_argument("--out", required=True)
    p.add_argument("--policy", default=None)
    p.add_argument("--enable-policy", action="append", default=[])
    p.add_argument("--date", default=None)
    p = sub.add_parser("replay", help="apply the policy to published versions offline")
    p.add_argument("--version-dir", action="append", required=True)
    p.add_argument("--policy", default=None)
    p.add_argument("--enable-policy", action="append", default=[])
    p.add_argument("--json", default=None, help="write the report here as JSON")
    p = sub.add_parser("validate")
    p.add_argument("--policy", default=None)
    a = ap.parse_args(argv)

    policy = load_policy(a.policy)
    if a.cmd == "validate":
        problems = validate_policy(policy)
        for pr in problems:
            print(pr)
        print(f"{len(problems)} problem(s) in {policy['meta']['path']} "
              f"(policy {policy_version(policy)}, {policy['meta']['sha256'][:19]})")
        return 1 if problems else 0
    if a.cmd == "expand":
        queue = json.loads(Path(a.queue).read_text(encoding="utf-8"))
        doc = {"reviewQueue": queue, "records": [], "manifest": {}}
        if a.provenance:
            prov = json.loads(Path(a.provenance).read_text(encoding="utf-8"))
            doc["records"] = prov.get("records") or []
            doc["manifest"] = prov.get("manifest") or {}
        bundle = json.loads(Path(a.bundle).read_text(encoding="utf-8")) if a.bundle else None
        res = apply_policy(doc, policy, bundle=bundle, enabled=a.enable_policy, date=a.date)
        Path(a.out).write_text(json.dumps(res.decisions, indent=1, ensure_ascii=False) + "\n",
                               encoding="utf-8")
        print(f"wrote {len(res.decisions)} decision(s) -> {a.out}; "
              f"{len(res.uncovered)} open item(s) left for the owner")
        for u in res.uncovered:
            print(f"  open: {u['item_id']} ({u['trigger']})")
        return 2 if res.uncovered else 0
    reports = [replay(v, policy, enabled=a.enable_policy) for v in a.version_dir]
    for rep in reports:
        print(f"{rep.version_dir}: {rep.counts()}")
        for r in rep.rows:
            if r["outcome"] != MATCH:
                print(f"  {r['outcome']:19s} {r['item_id']} published={r['published_class']}"
                      f"/{r['published_action']} ({r['published_origin']}) "
                      f"policy={r['policy_class']}/{r['policy_action']}")
        if rep.owner_only_records:
            print(f"  owner-only records: {[o['rule_id'] + ':' + o['subject'] for o in rep.owner_only_records]}")
        if rep.portfolio_approvals_not_derived:
            print(f"  portfolio approvals the policy does not derive: "
                  f"{rep.portfolio_approvals_not_derived}")
    if a.json:
        Path(a.json).write_text(json.dumps([r.as_dict() for r in reports], indent=1),
                                encoding="utf-8")
    return 1 if any(rep.mismatches() for rep in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
