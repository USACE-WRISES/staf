"""The owner's decisions on a region's curves (REF-15, owner decision 2026-09-22).

The build chooses every curve it did not fit (a carried curve, a curve from a
source after the station pools, a fixed criterion), and its portfolio rule
(SELECT-04) picks which fitted curves score each function. The owner can overrule
it, per metric and function, and every decision is recorded with who, when and
why:

- ``remove``: the metric's curve leaves the assessment;
- ``unmap``: the curve stops scoring the functions named and keeps the rest;
- ``include``: a curve the build fitted, but the two-per-function rule left out
  of a function, scores that function;
- ``source``: the metric's curve comes from a source the owner chose.

A decision is a standing decision of its region. It applies at once in the
workspace, rides in the session, and every later build of the region applies it
too, the same way: :func:`apply_to_inputs` is the one transform both paths run,
after SELECT-04, and :func:`effective_build` states the same decisions in the
shape the workspace displays. Nothing refills a function after a decision; the
owner chooses what scores in a curve's place. A decision that leaves a function
with no curve carries the documented gap the owner gave for it
(:func:`coverage_exceptions`), and undoing the decision undoes the gap.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

from . import fixed_criteria, methodology

RULE = "REF-15"
DECISIONS_FILE = "curve_decisions.json"
#: the removals of carried curves recorded on 2026-09-21, read once and folded in
LEGACY_REMOVALS_FILE = "owner_removals.json"
REMOVE, UNMAP, INCLUDE, SOURCE = "remove", "unmap", "include", "source"
ACTIONS = (REMOVE, UNMAP, INCLUDE, SOURCE)
#: the reason a documented gap an owner's decision leaves is recorded under (COV-01)
GAP_REASON = "insufficient-reference-support"
ACTION_LABELS = {REMOVE: "Removed from the assessment", UNMAP: "Removed from a function",
                 INCLUDE: "Used in a function", SOURCE: "Source chosen by the owner"}


def min_rationale() -> int:
    """The shortest rationale a decision takes (``owner_decisions.min_rationale``)."""
    try:
        return int(methodology.threshold("owner_decisions.min_rationale", 20) or 20)
    except (KeyError, TypeError, ValueError):
        return 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def new_decision(metric: str, action: str, *, rationale: str, recorded_by: str,
                 functions: Iterable[str] = (), source: Optional[dict] = None,
                 coverage_exceptions: Iterable[dict] = (),
                 recorded_at: Optional[str] = None, decision_id: Optional[str] = None) -> dict:
    """A decision record, checked for what every decision needs. What depends on
    the open session is :func:`validate`'s."""
    d = {"id": decision_id or ("cd-" + uuid.uuid4().hex[:10]),
         "metric": str(metric), "action": str(action),
         "functions": [str(f) for f in functions or () if f],
         "source": source,
         "coverageExceptions": [{"functionId": str(g.get("functionId")),
                                 "reason": str(g.get("reason") or GAP_REASON),
                                 "justification": _clean(g.get("justification"))}
                                for g in coverage_exceptions or ()],
         "rationale": _clean(rationale), "recordedBy": str(recorded_by or "").strip(),
         "recordedAt": recorded_at or _now()}
    check(d)
    return d


def check(d: Mapping) -> None:
    """Raise ValueError on a decision no session could accept."""
    if d.get("action") not in ACTIONS:
        raise ValueError(f"Unknown curve decision {d.get('action')!r}.")
    if not str(d.get("metric") or "").strip():
        raise ValueError("A curve decision names a metric.")
    need = min_rationale()
    if len(_clean(d.get("rationale"))) < need:
        raise ValueError(f"Give a rationale of at least {need} characters.")
    if not str(d.get("recordedBy") or "").strip():
        raise ValueError("A curve decision needs a named owner. Set STAF_LIBRARY_MAINTAINER.")
    if d["action"] in (UNMAP, INCLUDE) and not d.get("functions"):
        raise ValueError("Name the function.")
    for gap in d.get("coverageExceptions") or []:
        if len(_clean(gap.get("justification"))) < need:
            raise ValueError(f"Say why the function is left unassessed, in at least {need} "
                             "characters.")


def _left_out(build: Optional[Mapping]) -> set:
    """The (metric, function) pairs SELECT-04 itself left out."""
    return {(str(x.get("metric")), str(fid))
            for fid, sel in ((build or {}).get("portfolioSelection") or {}).items()
            for x in (sel or {}).get("notSelected") or [] if not x.get("owner")}


def validate(d: Mapping, *, build: Optional[Mapping], built: Iterable[str] = ()) -> None:
    """Raise ValueError when the open session cannot take the decision. ``build``
    is the session's reference build as the build wrote it; ``built``, the metrics
    the session fitted."""
    from . import pressure_evidence as pe
    check(d)
    mk, action = str(d["metric"]), d["action"]
    built = {str(k) for k in built or ()}
    if action == SOURCE and fixed_criteria.is_fixed(mk):
        raise ValueError("A fixed criterion is scored the same way in every region (CURVE-11). "
                         "It can be removed or taken out of a function, not given another "
                         "source.")
    if action == SOURCE and mk in built:
        raise ValueError("This curve was built here. Edit it in its analysis instead.")
    if action in (REMOVE, UNMAP):
        if mk in built:
            raise ValueError("This curve was built here. Take it out of a function in Function "
                             "mapping, or out of scope in its review.")
        if mk not in set(pe.reference_keys(build)):
            raise ValueError(f"{mk} is not a curve from another source in this version.")
    if action == INCLUDE:
        missing = [f for f in d.get("functions") or [] if (mk, str(f)) not in _left_out(build)]
        if missing:
            raise ValueError(f"{mk} is not a curve this build left out of {', '.join(missing)}.")


# --------------------------------------------------------------------------- #
# the region's file
# --------------------------------------------------------------------------- #
def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(run_dir, items: list[dict]) -> None:
    folder = Path(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / DECISIONS_FILE
    if items:
        path.write_text(json.dumps({"schema": 1, "decisions": items}, indent=1) + "\n",
                        encoding="utf-8")
    elif path.exists():
        path.unlink()


def _migrate(folder: Path) -> None:
    """Fold the removals of carried curves (2026-09-21) into the decisions file."""
    legacy = folder / LEGACY_REMOVALS_FILE
    items = _read_json(legacy)
    current = [d for d in ((_read_json(folder / DECISIONS_FILE) or {}).get("decisions") or [])
               if isinstance(d, dict)]
    for r in items if isinstance(items, list) else []:
        if isinstance(r, dict) and r.get("metric"):
            current = merge(current, {
                "id": "cd-" + uuid.uuid4().hex[:10], "metric": str(r["metric"]),
                "action": REMOVE, "functions": [], "source": None, "coverageExceptions": [],
                "rationale": _clean(r.get("rationale")),
                "recordedBy": str(r.get("recordedBy") or ""),
                "recordedAt": str(r.get("recordedAt") or "")})
    _write(folder, current)
    legacy.unlink()


def load(run_dir) -> list[dict]:
    """The region's standing curve decisions, oldest first."""
    if run_dir is None:
        return []
    folder = Path(run_dir)
    if (folder / LEGACY_REMOVALS_FILE).exists():
        _migrate(folder)
    doc = _read_json(folder / DECISIONS_FILE)
    items = doc.get("decisions") if isinstance(doc, dict) else None
    return [dict(d) for d in items or [] if isinstance(d, dict) and d.get("id")]


def path_of(run_dir) -> Optional[Path]:
    """The region's decisions file when it holds any decision, else None."""
    if run_dir is None:
        return None
    load(run_dir)                          # folds a legacy removals file in first
    path = Path(run_dir) / DECISIONS_FILE
    return path if path.exists() else None


def supersedes(new: Mapping, old: Mapping) -> bool:
    """A new decision replaces an older one about the same curve: removing it, or
    choosing its source, replaces everything else about it; taking it out of a
    function, or putting it in, replaces an older decision on that function."""
    if str(new.get("metric")) != str(old.get("metric")):
        return False
    if new.get("action") in (REMOVE, SOURCE) or old.get("action") == REMOVE:
        return True
    return bool(set(new.get("functions") or []) & set(old.get("functions") or []))


def merge(items: Iterable[dict], decision: Mapping) -> list[dict]:
    """``items`` with ``decision`` added last, replacing what it supersedes."""
    kept = [dict(d) for d in items or [] if d.get("id") != decision.get("id")
            and not supersedes(decision, d)]
    return kept + [dict(decision)]


def save(run_dir, decision: Mapping) -> list[dict]:
    """Record a decision for the region; returns the region's decisions."""
    items = merge(load(run_dir), decision)
    _write(run_dir, items)
    return items


def undo(run_dir, decision_id: str) -> list[dict]:
    """Withdraw a decision from the region; returns the region's decisions."""
    items = [d for d in load(run_dir) if d.get("id") != decision_id]
    if run_dir is not None:
        _write(run_dir, items)
    return items


def combine(session: Iterable[dict], region: Iterable[dict]) -> list[dict]:
    """The session's decisions with the region's standing ones applied on top."""
    out: list[dict] = []
    for d in list(session or []) + list(region or []):
        if isinstance(d, dict) and d.get("id"):
            out = merge(out, d)
    return out


def from_removals(remove_metrics: Optional[Mapping], *, recorded_by: str,
                  keys: Iterable[str]) -> list[dict]:
    """``--remove-metric`` inputs naming a curve the build did not fit, as remove
    decisions with ids that depend only on what they say, so two builds given the
    same flags record the same decision."""
    keys = {str(k) for k in keys or ()}
    out = []
    for mk, why in sorted((remove_metrics or {}).items()):
        if str(mk) not in keys:
            continue
        digest = hashlib.sha1(f"{mk}|{_clean(why)}".encode("utf-8")).hexdigest()[:10]
        out.append(new_decision(mk, REMOVE, rationale=why, recorded_by=recorded_by,
                                recorded_at="", decision_id=f"cd-flag-{digest}"))
    return out


# --------------------------------------------------------------------------- #
# what the decisions do
# --------------------------------------------------------------------------- #
def _by_action(decisions: Iterable[Mapping]) -> dict:
    out: dict = {REMOVE: {}, UNMAP: [], INCLUDE: [], SOURCE: {}}
    for d in decisions or []:
        action = d.get("action")
        if action == REMOVE:
            out[REMOVE][str(d["metric"])] = d
        elif action == SOURCE:
            out[SOURCE][str(d["metric"])] = d
        elif action in (UNMAP, INCLUDE):
            out[action].append(d)
    return out


def effective_selection(selection: Optional[Mapping], decisions: Iterable[Mapping]) -> dict:
    """SELECT-04's record with the owner's decisions on it: an ``include`` takes its
    pair out of ``notSelected`` (and names it in ``ownerIncluded``); an ``unmap``
    adds its pair to ``notSelected``, marked as the owner's."""
    out = copy.deepcopy(dict(selection or {}))
    for d in decisions or []:
        mk = str(d.get("metric"))
        for fid in d.get("functions") or []:
            sel = out.setdefault(str(fid), {"kept": [], "selected": [], "notSelected": []})
            sel["notSelected"] = list(sel.get("notSelected") or [])
            if d.get("action") == INCLUDE:
                sel["notSelected"] = [x for x in sel["notSelected"] if str(x.get("metric")) != mk]
                included = list(sel.get("ownerIncluded") or [])
                if mk not in included:
                    included.append(mk)
                sel["ownerIncluded"] = included
            elif d.get("action") == UNMAP:
                if not any(str(x.get("metric")) == mk for x in sel["notSelected"]):
                    sel["notSelected"].append({"metric": mk, "owner": True,
                                               "decision": d.get("id")})
    return out


def effective_build(build: Optional[Mapping], decisions: Iterable[Mapping]) -> Optional[dict]:
    """The session's reference build with the owner's decisions applied, in the
    shape every reader of ``reference_build`` already understands: a removed curve
    is gone, an unmapped pair and an included one are in the portfolio record."""
    if not build:
        return build
    decisions = list(decisions or [])
    if not decisions:
        return build
    out = copy.deepcopy(dict(build))
    acts = _by_action(decisions)
    gone = set(acts[REMOVE])
    for key in ("ladderMetrics", "carriedMetrics"):
        if out.get(key):
            out[key] = {mk: v for mk, v in out[key].items() if str(mk) not in gone}
    if out.get("fixedMetrics"):
        out["fixedMetrics"] = [mk for mk in out["fixedMetrics"] if str(mk) not in gone]
    out["portfolioSelection"] = effective_selection(
        out.get("portfolioSelection"), acts[UNMAP] + acts[INCLUDE])
    out["ownerDecisions"] = [summary(d) for d in decisions]
    return out


def apply_to_inputs(rows: dict, mapping, config: dict, meta: dict,
                    decisions: Iterable[Mapping], *, keep=()) -> tuple[dict, Any, dict]:
    """The one transform both paths run on the exporter's inputs BEFORE SELECT-04's
    drop (``pressure_evidence.bundle_inputs`` in a build, ``apply_reference_build``
    with ``apply_selection=False`` in the workspace), with the build's portfolio
    record in ``meta["portfolioSelection"]``: SELECT-04's drop under the owner's
    decisions, then the removals. ``keep`` are the fixed criteria, kept by the
    drop as SELECT-04 keeps them. Returns ``(rows, mapping, config)`` and records
    the effective portfolio and the decisions in ``meta``."""
    from . import pressure_evidence as pe
    decisions = list(decisions or [])
    acts = _by_action(decisions)
    gone = set(acts[REMOVE])
    selection = effective_selection(meta.get("portfolioSelection"), acts[UNMAP] + acts[INCLUDE])
    rows = {mk: r for mk, r in rows.items() if str(mk) not in gone}
    if isinstance(mapping, pd.DataFrame) and len(mapping) and "metric_key" in mapping.columns:
        mapping = mapping[~mapping["metric_key"].astype(str).isin(gone)]
    rows, mapping = pe._apply_selection(selection, rows, mapping,
                                        keep={str(k) for k in keep or ()} - gone)
    if selection:
        meta["portfolioSelection"] = selection
    if decisions:
        meta["ownerCurveDecisions"] = [summary(d) for d in decisions]
        # a curve still scoring states the owner's decisions on where it scores
        annotations = meta.setdefault("metricAnnotations", {})
        for d in acts[UNMAP] + acts[INCLUDE]:
            mk = str(d.get("metric"))
            if mk not in rows:
                continue
            ann = dict(annotations.get(mk) or {})
            ann["ownerDecisions"] = [x for x in ann.get("ownerDecisions") or []
                                     if x.get("id") != d.get("id")] + [summary(d)]
            annotations[mk] = ann
    return rows, mapping, config


def summary(d: Mapping) -> dict:
    """What a decision records beside the bundle: the choice, never curve data."""
    out = {"id": d.get("id"), "metric": d.get("metric"), "action": d.get("action"),
           "functions": list(d.get("functions") or []), "rationale": d.get("rationale"),
           "recordedBy": d.get("recordedBy"), "recordedAt": d.get("recordedAt")}
    src = d.get("source") or {}
    if src:
        out["source"] = {k: src.get(k) for k in ("kind", "ref", "title", "citation")
                         if src.get(k) is not None}
    if d.get("coverageExceptions"):
        out["coverageExceptions"] = [dict(g) for g in d["coverageExceptions"]]
    return out


def removed(decisions: Iterable[Mapping]) -> dict:
    """``{metric: decision}`` of the curves the owner removed."""
    return dict(_by_action(decisions)[REMOVE])


def decisions_for(metric: str, decisions: Iterable[Mapping]) -> list[dict]:
    return [dict(d) for d in decisions or [] if str(d.get("metric")) == str(metric)]


def coverage_exceptions(decisions: Iterable[Mapping]) -> list[dict]:
    """The documented gaps the owner gave with decisions that emptied a function."""
    out = []
    for d in decisions or []:
        for gap in d.get("coverageExceptions") or []:
            out.append({"functionId": gap.get("functionId"),
                        "reason": gap.get("reason") or GAP_REASON,
                        "justification": _clean(gap.get("justification")),
                        "recordedBy": d.get("recordedBy"), "recordedAt": d.get("recordedAt"),
                        "decision": d.get("id")})
    return out


def with_exceptions(base: Iterable[dict], decisions: Iterable[Mapping]) -> list[dict]:
    """The session's documented gaps with the gaps of the owner's decisions on top
    (one per function)."""
    owner = coverage_exceptions(decisions)
    mine = {str(e.get("functionId")) for e in owner}
    return [dict(e) for e in base or [] if str(e.get("functionId")) not in mine] + owner


def stale(decisions: Iterable[Mapping], build: Optional[Mapping], *,
          built=()) -> list[tuple[dict, str]]:
    """``[(decision, why)]`` for decisions the session cannot apply: the curve is not
    in this version, or an included curve is no longer one the build left out."""
    from . import pressure_evidence as pe
    known = set(pe.reference_keys(build)) | {str(k) for k in built or ()}
    left_out = _left_out(build)
    out = []
    for d in decisions or []:
        mk = str(d.get("metric"))
        if d.get("action") in (REMOVE, UNMAP) and mk not in known:
            out.append((dict(d), "This version has no curve for the metric."))
        elif d.get("action") == INCLUDE and not all(
                (mk, str(f)) in left_out for f in d.get("functions") or []):
            out.append((dict(d), "The build no longer leaves this curve out of the function."))
    return out


__all__ = [
    "RULE", "DECISIONS_FILE", "LEGACY_REMOVALS_FILE", "REMOVE", "UNMAP", "INCLUDE", "SOURCE",
    "ACTIONS", "GAP_REASON", "ACTION_LABELS", "min_rationale", "new_decision", "check",
    "validate", "load", "path_of", "supersedes", "merge", "save", "undo", "combine",
    "from_removals", "effective_selection", "effective_build", "apply_to_inputs", "summary",
    "removed", "decisions_for", "coverage_exceptions", "with_exceptions", "stale",
]
