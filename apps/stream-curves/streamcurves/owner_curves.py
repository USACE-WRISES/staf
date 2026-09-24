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
- ``source``: the metric's curve comes from a source the owner chose
  (``owner_sources``). The source is resolved when it is chosen, so the
  decision holds the curve itself, and the curve scores the functions it names.

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
#: the id prefix of a removal a build makes from ``--remove-metric``: it lives in
#: that build's session and never in the region's file
FLAG_PREFIX = "cd-flag-"
#: The REF-15 extension of the authoring foundation (2026-09-23): a published state SQT curve
#: as a source (``owner_sources.SQT``), and a chosen curve taking the place of curves this
#: build fitted in the functions it names (``replaces``); each replaced curve stays built
#: and is recorded as supported, not selected. False in the canonical configuration until
#: the owner adopts it, and a decision that needs it applies nothing while it is false.
EXTENSION_FLAG = "owner_decisions.alternatives_over_fitted"
EXTENSION_OFF = ("Choosing a state SQT curve, or a curve in place of one built here, is not "
                 "enabled in this methodology.")


def alternatives_enabled() -> bool:
    """The REF-15 extension is on (``owner_decisions.alternatives_over_fitted``)."""
    try:
        return bool(methodology.threshold(EXTENSION_FLAG, False))
    except (KeyError, TypeError, ValueError):
        return False


def needs_extension(d: Optional[Mapping]) -> bool:
    """The decision chooses a state SQT curve or replaces a curve built here."""
    src = (d or {}).get("source") or {}
    return src.get("kind") == "sqt" or bool((d or {}).get("replaces"))


#: The version of the SQT adoption checks a state SQT choice records
#: (``owner_sources.sqt_source``). A choice made before them (round 2 of the authoring
#: foundation, 2026-09-24) applies nothing: its curve was never rebuilt from the frozen
#: record, and an open end was never completed.
SQT_ADOPTION_VERSION = 2
PRE_ADOPTION = "Made before the SQT adoption checks; select the curve again."


def _pre_adoption_sqt(d: Optional[Mapping]) -> bool:
    src = (d or {}).get("source") or {}
    if src.get("kind") != "sqt":
        return False                      # every other source keeps applying as it did
    try:
        version = int((src.get("ref") or {}).get("adoptionVersion") or 0)
    except (TypeError, ValueError):
        version = 0
    return version < SQT_ADOPTION_VERSION


def unusable_reason(d: Mapping) -> Optional[str]:
    """Why this session applies nothing of the decision, or None when it applies: the REF-15
    extension is off, or it chooses a state SQT curve under an older adoption."""
    if needs_extension(d) and not alternatives_enabled():
        return EXTENSION_OFF
    if _pre_adoption_sqt(d):
        return PRE_ADOPTION
    return None


def usable(d: Mapping) -> bool:
    """The decision applies in this session (:func:`unusable_reason` says why not)."""
    return unusable_reason(d) is None


_usable = usable


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
                 recorded_at: Optional[str] = None, decision_id: Optional[str] = None,
                 replaces: Iterable[Mapping] = (), basis_digest: Optional[str] = None) -> dict:
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
         # an explicit value, even an empty one, is kept: a --remove-metric removal
         # carries none, so two builds given the same flags record the same decision
         "recordedAt": _now() if recorded_at is None else str(recorded_at)}
    replaced = [{"metric": str(r.get("metric")), "functionId": str(r.get("functionId"))}
                for r in replaces or () if r]
    if replaced:
        # only a decision that replaces a fitted curve carries the key, so every
        # other decision keeps the shape it always had
        d["replaces"] = replaced
    if basis_digest:
        # the curve the decision was made on: a later change to it asks for another look
        d["basisDigest"] = str(basis_digest)
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
    if d["action"] in (UNMAP, INCLUDE, SOURCE) and not d.get("functions"):
        raise ValueError("Name the function.")
    if needs_extension(d) and not alternatives_enabled():
        raise ValueError(EXTENSION_OFF)
    if _pre_adoption_sqt(d):
        raise ValueError(PRE_ADOPTION)
    if d.get("replaces"):
        if d["action"] != SOURCE:
            raise ValueError("Only a chosen source can take the place of a curve built here.")
        named = {str(f) for f in d.get("functions") or []}
        for r in d["replaces"]:
            if not str(r.get("metric") or "").strip() or str(r.get("functionId")) not in named:
                raise ValueError("A replaced curve names its metric and one of the decision's "
                                 "functions.")
            if str(r.get("metric")) == str(d.get("metric")):
                raise ValueError("A curve cannot take its own place.")
    if d["action"] == SOURCE:
        from . import owner_sources
        src = d.get("source") or {}
        if src.get("kind") not in owner_sources.SOURCE_KINDS:
            raise ValueError("Choose where the curve comes from.")
        if src.get("kind") != owner_sources.REFUSED and \
                len((src.get("curve") or {}).get("points") or []) < 2:
            raise ValueError("The chosen source has no curve.")
    for gap in d.get("coverageExceptions") or []:
        if len(_clean(gap.get("justification"))) < need:
            raise ValueError(f"Say why the function is left unassessed, in at least {need} "
                             "characters.")


def _left_out(build: Optional[Mapping]) -> set:
    """The (metric, function) pairs SELECT-04 itself left out."""
    return {(str(x.get("metric")), str(fid))
            for fid, sel in ((build or {}).get("portfolioSelection") or {}).items()
            for x in (sel or {}).get("notSelected") or [] if not x.get("owner")}


def validate(d: Mapping, *, build: Optional[Mapping], built: Iterable[str] = (),
             decisions: Iterable[Mapping] = ()) -> None:
    """Raise ValueError when the open session cannot take the decision. ``build``
    is the session's reference build as the build wrote it; ``built``, the metrics
    the session fitted; ``decisions``, the ones it already holds (a curve the
    owner chose can be removed or taken out of a function too)."""
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
    for r in d.get("replaces") or []:
        if str(r.get("metric")) not in built:
            raise ValueError(f"{r.get('metric')} is not a curve built here, so nothing is "
                             "replaced; take it out with its own decision instead.")
    if action in (REMOVE, UNMAP):
        if mk in built:
            raise ValueError("This curve was built here. Take it out of a function in Function "
                             "mapping, or out of scope in its review.")
        if mk not in set(pe.reference_keys(build)) | set(sourced(decisions)):
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
    """Write the region's decisions. A file the owner emptied stays, holding none,
    so a build never seeds it again from a published version (:func:`seed`)."""
    folder = Path(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / DECISIONS_FILE).write_text(
        json.dumps({"schema": 1, "decisions": items}, indent=1) + "\n", encoding="utf-8")


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
    items = load(run_dir)                  # folds a legacy removals file in first
    return Path(run_dir) / DECISIONS_FILE if items else None


def standing(run_dir) -> Optional[list[dict]]:
    """The region's standing record: its decisions when it keeps a file, even one
    the owner emptied, and None when it has never recorded any here."""
    if run_dir is None:
        return None
    folder = Path(run_dir)
    if not ((folder / DECISIONS_FILE).exists() or (folder / LEGACY_REMOVALS_FILE).exists()):
        return None
    return load(folder)


def load_file(path) -> list[dict]:
    """The decisions of one decisions file (``stage --curve-decisions``), or ``[]``."""
    doc = _read_json(Path(path)) if path is not None else None
    items = doc.get("decisions") if isinstance(doc, dict) else None
    return [dict(d) for d in items or [] if isinstance(d, dict) and d.get("id")]


def seed(run_dir, decisions: Iterable[Mapping]) -> list[dict]:
    """The region's decisions, first written from ``decisions`` (the ones its latest
    published version recorded) when the region has never recorded any: a
    checkout without the region's run folder keeps the owner's choices."""
    if run_dir is None:
        return []
    folder = Path(run_dir)
    if (folder / DECISIONS_FILE).exists() or (folder / LEGACY_REMOVALS_FILE).exists():
        return load(folder)
    items = [dict(d) for d in decisions or [] if isinstance(d, Mapping) and d.get("id")]
    if items:
        _write(folder, items)
    return items


def supersedes(new: Mapping, old: Mapping) -> bool:
    """A new decision replaces an older one about the same curve: removing it, or
    choosing its source, replaces everything else about it; taking it out of a
    function, or putting it in, replaces an older decision on that function and
    keeps a chosen source."""
    if str(new.get("metric")) != str(old.get("metric")):
        return False
    if new.get("action") in (REMOVE, SOURCE) or old.get("action") == REMOVE:
        return True
    if old.get("action") == SOURCE:
        return False
    return bool(set(new.get("functions") or []) & set(old.get("functions") or []))


def merge(items: Iterable[dict], decision: Mapping) -> list[dict]:
    """``items`` with ``decision`` added last, replacing what it supersedes. A gap
    the owner documented with a replaced decision stays documented unless the new
    decision names that function: removing a curve after taking it out of a
    function keeps the reason given for the function that was emptied."""
    items = [dict(d) for d in items or []]
    others = [d for d in items if d.get("id") != decision.get("id")]
    replaced = [d for d in others if supersedes(decision, d)]
    new = dict(decision)
    named = ({str(f) for f in new.get("functions") or []}
             | {str(g.get("functionId")) for g in new.get("coverageExceptions") or []})
    inherited = []
    for d in replaced:
        for g in d.get("coverageExceptions") or []:
            if str(g.get("functionId")) not in named:
                named.add(str(g.get("functionId")))
                inherited.append(dict(g))
    if inherited:
        new["coverageExceptions"] = list(new.get("coverageExceptions") or []) + inherited
    return [d for d in others if not supersedes(decision, d)] + [new]


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


def _computed(d: Mapping) -> bool:
    """A refused source a build has computed, or found it could not compute."""
    src = (d or {}).get("source") or {}
    return bool(source_curve(d).get("points") or src.get("failedAtBuild"))


def combine(session: Iterable[dict], region: Iterable[dict]) -> list[dict]:
    """The session's decisions with the region's standing ones applied on top. A
    refused source the region's record holds as a request keeps what the
    session's build made of it: the curve it computed, or why it could not."""
    out: list[dict] = []
    for d in list(session or []) + list(region or []):
        if not (isinstance(d, dict) and d.get("id")):
            continue
        prior = next((x for x in out if x.get("id") == d.get("id")), None)
        if prior is not None and not _computed(d) and _computed(prior):
            d = {**d, "source": dict(prior.get("source") or {})}
        out = merge(out, d)
    return out


def restore(session: Iterable[dict],
            region: Optional[Iterable[dict]]) -> tuple[list[dict], list[dict]]:
    """``(decisions, withdrawn)`` of a session reopened in the workspace. The
    region's record, when it keeps one (:func:`standing`), is the standing record:
    a decision the session holds and the record no longer does was withdrawn
    since, so it does not apply. A removal a build made from ``--remove-metric``
    never lives in the record and stays. The session's copy still supplies what
    its build computed for a refused source. With no record at all (a checkout
    without the region's run folder), the session's decisions stand."""
    session = [dict(d) for d in session or [] if isinstance(d, Mapping) and d.get("id")]
    if region is None:
        return session, []
    region = [dict(d) for d in region if isinstance(d, Mapping) and d.get("id")]
    ids = {d["id"] for d in region}
    withdrawn = [d for d in session
                 if d["id"] not in ids and not str(d["id"]).startswith(FLAG_PREFIX)]
    gone = {d["id"] for d in withdrawn}
    return combine([d for d in session if d["id"] not in gone], region), withdrawn


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
                                recorded_at="", decision_id=f"{FLAG_PREFIX}{digest}"))
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
    adds its pair to ``notSelected``, marked as the owner's; a ``source`` takes the
    pairs of the functions it names out, since the owner placed the curve there.
    Pass a source before the unmaps that narrow it."""
    out = copy.deepcopy(dict(selection or {}))
    for d in decisions or []:
        mk = str(d.get("metric"))
        for fid in d.get("functions") or []:
            if d.get("action") == SOURCE:
                sel = out.get(str(fid))
                if sel and sel.get("notSelected"):
                    sel["notSelected"] = [x for x in sel["notSelected"]
                                          if str(x.get("metric")) != mk]
                for r in (d.get("replaces") or []) if _usable(d) else []:
                    if str(r.get("functionId")) != str(fid):
                        continue
                    sel = out.setdefault(str(fid), {"kept": [], "selected": [], "notSelected": []})
                    sel["notSelected"] = [x for x in sel.get("notSelected") or []
                                          if str(x.get("metric")) != str(r.get("metric"))]
                    sel["notSelected"].append({"metric": str(r.get("metric")), "owner": True,
                                               "decision": d.get("id"), "replacedBy": mk})
                continue
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


def applies(metric: str, *, built=()) -> bool:
    """A chosen source applies to the metric: the build did not fit it, and it is
    not a fixed criterion. A curve built here always wins over a choice."""
    mk = str(metric)
    return mk not in {str(k) for k in built or ()} and not fixed_criteria.is_fixed(mk)


def sourced(decisions: Iterable[Mapping]) -> dict:
    """``{metric: decision}`` of the curves whose source the owner chose."""
    return dict(_by_action(decisions)[SOURCE])


def _chosen(acts: dict, built=()) -> dict:
    """The source decisions that put a curve in: a refused source the owner
    accepted has one only once a build has computed it."""
    return {mk: d for mk, d in acts[SOURCE].items()
            if mk not in acts[REMOVE] and applies(mk, built=built) and _usable(d)
            and len(source_curve(d).get("points") or []) >= 2}


def chosen(decisions: Iterable[Mapping], *, built=()) -> dict:
    """``{metric: decision}`` of the source decisions that put a curve in this
    version: not removed, not on a metric the build fitted, holding a curve."""
    return _chosen(_by_action(decisions), built)


def held_metrics(decisions: Iterable[Mapping]) -> list[str]:
    """The metrics a build keeps out of its own fit (owner decision 2026-09-22,
    "your choice stands"): every metric the owner removed or chose a source for,
    until the decision is withdrawn (``pressure_evidence.run_evidence(hold=)``)."""
    acts = _by_action([d for d in decisions or [] if _usable(d)])
    return sorted(set(acts[REMOVE]) | set(acts[SOURCE]))


def forced_sources(decisions: Iterable[Mapping]) -> dict:
    """``{metric: {"rule", "option"}}`` of the refused sources the owner accepted:
    what a build computes for them (``basis_ladder.force_source``)."""
    from . import owner_sources
    out = {}
    for mk, d in _by_action([d for d in decisions or [] if _usable(d)])[SOURCE].items():
        src = d.get("source") or {}
        if src.get("kind") == owner_sources.REFUSED:
            ref = src.get("ref") or {}
            out[mk] = {"rule": ref.get("rule"), "option": ref.get("option")}
    return out


def pending(decisions: Iterable[Mapping]) -> dict:
    """``{metric: decision}`` of the refused sources the owner accepted that no
    build has computed yet: they wait for the next build of the region."""
    from . import owner_sources
    return {mk: d for mk, d in _by_action([d for d in decisions or [] if _usable(d)])[SOURCE].items()
            if (d.get("source") or {}).get("kind") == owner_sources.REFUSED
            and not source_curve(d).get("points")
            and not (d.get("source") or {}).get("failedAtBuild")}


def with_forced(decisions: Iterable[Mapping], forced: Mapping) -> list[dict]:
    """The decisions with each accepted refusal holding what its build computed:
    the curve and the checks it failed, or why nothing could be built."""
    from . import owner_sources
    out = []
    for d in decisions or []:
        src = (d or {}).get("source") or {}
        got = forced.get(str(d.get("metric"))) if src.get("kind") == owner_sources.REFUSED else None
        if got is None:
            out.append(dict(d))
            continue
        filled = {k: v for k, v in src.items() if k not in ("curve", "failed", "failedAtBuild")}
        if got.get("row") is not None:
            filled["curve"] = owner_sources.forced_curve(str(d["metric"]), got)
            filled["failed"] = [dict(f) for f in got.get("failed") or []]
        else:
            filled["failedAtBuild"] = str(got.get("why") or "Nothing could be built.")
        out.append({**dict(d), "source": filled})
    return out


def source_curve(d: Mapping) -> dict:
    """The curve a SOURCE decision holds, in the shape a carried curve rides in."""
    return dict(((d or {}).get("source") or {}).get("curve") or {})


def decision_annotation(d: Mapping) -> dict:
    """The ``ownerDecision`` block a chosen curve carries in the bundle: the choice,
    who made it, when and why (a decision or its :func:`summary`)."""
    src = (d or {}).get("source") or {}
    out = {"id": d.get("id"), "kind": src.get("kind"), "title": src.get("title"),
           "citation": src.get("citation"), "rationale": d.get("rationale"),
           "recordedBy": d.get("recordedBy"), "recordedAt": d.get("recordedAt")}
    return {k: v for k, v in out.items() if v not in (None, "")}


def effective_build(build: Optional[Mapping], decisions: Iterable[Mapping], *,
                    built=()) -> Optional[dict]:
    """The session's reference build with the owner's decisions applied, in the
    shape every reader of ``reference_build`` already understands: a removed curve
    is gone, an unmapped pair and an included one are in the portfolio record, and
    a chosen curve sits in ``ownerMetrics`` in place of the build's. ``built``:
    the metrics the session fitted, which no choice replaces."""
    if not build:
        return build
    decisions = list(decisions or [])
    if not decisions:
        return build
    out = copy.deepcopy(dict(build))
    acts = _by_action(decisions)
    gone = set(acts[REMOVE])
    chosen = _chosen(acts, built)
    for key in ("ladderMetrics", "carriedMetrics"):
        if out.get(key):
            out[key] = {mk: v for mk, v in out[key].items()
                        if str(mk) not in gone and str(mk) not in chosen}
    if out.get("fixedMetrics"):
        out["fixedMetrics"] = [mk for mk in out["fixedMetrics"] if str(mk) not in gone]
    if chosen:
        out["ownerMetrics"] = {mk: {"decision": summary(d), "curve": source_curve(d),
                                    "functions": list(d.get("functions") or [])}
                               for mk, d in chosen.items()}
        out["insufficientReferenceSupport"] = [
            w for w in out.get("insufficientReferenceSupport") or []
            if str(w.get("metricKey")) not in chosen]
    out["portfolioSelection"] = effective_selection(
        out.get("portfolioSelection"), list(chosen.values()) + acts[UNMAP] + acts[INCLUDE])
    out["ownerDecisions"] = [summary(d) for d in decisions]
    return out


def _placed(mapping, chosen: dict):
    """The mapping with each chosen curve placed in exactly the functions its
    decision names, after every other row."""
    from . import owner_sources
    base = mapping if isinstance(mapping, pd.DataFrame) else pd.DataFrame(
        columns=["metric_key", "discipline", "function_label", "sort_order"])
    if len(base) and "metric_key" in base.columns:
        base = base[~base["metric_key"].astype(str).isin(set(chosen))]
    extra = pd.DataFrame(
        [r for mk, d in chosen.items()
         for r in owner_sources.mapping_rows_for(mk, d.get("functions") or [])],
        columns=["metric_key", "discipline", "function_label"])
    if not len(extra):
        return base
    order = (pd.to_numeric(base["sort_order"], errors="coerce")
             if "sort_order" in base.columns else pd.Series(dtype="float64"))
    start = int(order.max()) if order.notna().any() else 0
    extra = extra.assign(sort_order=range(start + 1, start + 1 + len(extra)))
    return pd.concat([base, extra], ignore_index=True)


def _placed_pairs(mapping, selection: Optional[Mapping]) -> set:
    """``{(metric, function id)}`` the mapping places and the portfolio keeps."""
    from . import pressure_evidence as pe
    if not isinstance(mapping, pd.DataFrame) or not len(mapping) \
            or "metric_key" not in mapping.columns or "function_label" not in mapping.columns:
        return set()
    dropped = pe.not_selected_pairs({"portfolioSelection": dict(selection or {})})
    out = set()
    for mk, label in zip(mapping["metric_key"].astype(str), mapping["function_label"]):
        fid = pe.canonical_function_id(label)
        if fid and (mk, fid) not in dropped:
            out.add((mk, fid))
    return out


def _with_chosen(rows: dict, mapping, config: dict, meta: dict, chosen: dict):
    """Put each chosen curve in the exporter's inputs, in place of whatever the
    build gave the metric: its points and layers, config and annotations, placed
    by its decision, and out of the withheld list."""
    from . import carry_forward, owner_sources
    rows, config = dict(rows), dict(config or {})
    restored = carry_forward.restore_rows({mk: source_curve(d) for mk, d in chosen.items()})
    annotations = meta.setdefault("metricAnnotations", {})
    for mk, d in chosen.items():
        c = restored[mk]
        row = dict(c["row"])
        row["curve_source"] = owner_sources.CURVE_SOURCE
        rows[mk] = row
        if c.get("config"):
            config[mk] = dict(c["config"])
        ann = dict(c.get("annotations") or {})
        ann["ownerDecision"] = decision_annotation(d)
        annotations[mk] = ann
    withheld = meta.get("insufficientReferenceSupport")
    if withheld:
        meta["insufficientReferenceSupport"] = [w for w in withheld
                                                if str(w.get("metricKey")) not in chosen]
    return rows, _placed(mapping, chosen), config


def apply_to_inputs(rows: dict, mapping, config: dict, meta: dict,
                    decisions: Iterable[Mapping], *, keep=(),
                    built=()) -> tuple[dict, Any, dict]:
    """The one transform both paths run on the exporter's inputs BEFORE SELECT-04's
    drop (``pressure_evidence.bundle_inputs`` in a build, ``apply_reference_build``
    with ``apply_selection=False`` in the workspace), with the build's portfolio
    record in ``meta["portfolioSelection"]``: the owner's chosen curves in place
    of the build's, SELECT-04's drop under the owner's decisions, then the
    removals. ``keep`` are the fixed criteria, kept by the drop as SELECT-04 keeps
    them; ``built``, the metrics the build fitted, which no choice replaces.
    Returns ``(rows, mapping, config)`` and records the effective portfolio and
    the decisions in ``meta``."""
    from . import pressure_evidence as pe
    decisions = list(decisions or [])
    acts = _by_action(decisions)
    # a removal never takes out a curve the build fitted: that curve is the
    # review's to take out of scope, and stale() names the removal
    gone = set(acts[REMOVE]) - {str(k) for k in built or ()}
    picked = _chosen(acts, built)
    if picked:
        rows, mapping, config = _with_chosen(rows, mapping, config, meta, picked)
    base_selection = meta.get("portfolioSelection")
    # the pairs an include or an unmap can change: SELECT-04's own left-out pairs,
    # and the pairs the mapping places once the chosen curves are in
    left_out = _left_out({"portfolioSelection": base_selection})
    placed = _placed_pairs(mapping, effective_selection(base_selection, list(picked.values())))
    selection = effective_selection(base_selection,
                                    list(picked.values()) + acts[UNMAP] + acts[INCLUDE])
    rows = {mk: r for mk, r in rows.items() if str(mk) not in gone}
    if isinstance(mapping, pd.DataFrame) and len(mapping) and "metric_key" in mapping.columns:
        mapping = mapping[~mapping["metric_key"].astype(str).isin(gone)]
    rows, mapping = pe._apply_selection(selection, rows, mapping,
                                        keep={str(k) for k in keep or ()} - gone)
    if selection:
        meta["portfolioSelection"] = selection
    applied = [d for d in decisions if _usable(d)]
    if applied:
        meta["ownerCurveDecisions"] = [bundle_summary(d) for d in applied]
        # a curve still scoring states the owner's decisions on where it scores,
        # each one that changed a pair here (a stale one is named by stale())
        annotations = meta.setdefault("metricAnnotations", {})
        for d in acts[UNMAP] + acts[INCLUDE]:
            mk = str(d.get("metric"))
            if mk not in rows:
                continue
            pairs = left_out if d.get("action") == INCLUDE else placed
            if not any((mk, str(f)) in pairs for f in d.get("functions") or []):
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
        out["source"] = {k: src.get(k) for k in ("kind", "ref", "title", "citation", "failed",
                                                  "failedAtBuild")
                         if src.get(k) is not None}
        from . import owner_sources
        if src.get("kind") == owner_sources.REFUSED and not _computed(d):
            # a request no build has computed yet applies nothing, and says so
            out["source"]["waitsForBuild"] = True
    if d.get("coverageExceptions"):
        out["coverageExceptions"] = [dict(g) for g in d["coverageExceptions"]]
    return out


#: the authoring register's own keys a source reference may hold: they identify the
#: candidate in the author's register and never ride in a bundle
REGISTER_REF_KEYS = ("candidateKey", "basisDigest", "fingerprint")


def bundle_summary(d: Mapping) -> dict:
    """:func:`summary` as a bundle carries it: the same, without the register's keys."""
    out = summary(d)
    src = out.get("source")
    if src and isinstance(src.get("ref"), dict):
        src["ref"] = {k: v for k, v in src["ref"].items() if k not in REGISTER_REF_KEYS}
    return out


def requests(decisions: Iterable[Mapping]) -> list[str]:
    """What the owner recorded in each decision, one canonical string each,
    sorted: the id, metric, action, functions, source (kind, ref, title,
    citation), documented gaps, rationale and owner, never what a build computed.
    A removal a build made from ``--remove-metric`` is the build's, not the
    region's, and is left out."""
    out = []
    for d in decisions or []:
        if not isinstance(d, Mapping) or not d.get("id") \
                or str(d["id"]).startswith(FLAG_PREFIX):
            continue
        src = {k: v for k, v in (d.get("source") or {}).items()
               if k in ("kind", "ref", "title", "citation")}
        out.append(json.dumps({
            "id": d.get("id"), "metric": d.get("metric"), "action": d.get("action"),
            "functions": [str(f) for f in d.get("functions") or []], "source": src or None,
            "coverageExceptions": [{"functionId": str(g.get("functionId")),
                                    "justification": _clean(g.get("justification"))}
                                   for g in d.get("coverageExceptions") or []],
            "rationale": _clean(d.get("rationale")), "recordedBy": d.get("recordedBy"),
            **({"replaces": [dict(r) for r in d["replaces"]]} if d.get("replaces") else {})},
            sort_keys=True, default=str))
    return sorted(out)


def decisions_changed(staged: Iterable[Mapping], now: Iterable[Mapping]) -> bool:
    """The region's decisions (``now``) are no longer the ones a staged run was
    built with (``staged``): one was recorded or withdrawn after the stage, so
    the staged version would publish without it, or with it. The build's own
    ``--remove-metric`` removals are merged into ``now`` first, as the build
    merged them (``regional_agent.owner_decisions_for``)."""
    staged = [dict(d) for d in staged or [] if isinstance(d, Mapping)]
    expected = [dict(d) for d in now or [] if isinstance(d, Mapping)]
    for d in staged:
        if str(d.get("id") or "").startswith(FLAG_PREFIX):
            expected = merge(expected, d)
    return requests(staged) != requests(expected)


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


def live_exceptions(base: Iterable[dict], decisions: Iterable[Mapping]) -> list[dict]:
    """``base`` without the gaps of an owner's decision that no longer stands: a gap
    recorded with a decision (its ``decision`` id) goes when the decision goes,
    however it reached the session (a build writes them into its documented
    gaps)."""
    live = {str(d.get("id")) for d in decisions or [] if isinstance(d, Mapping)}
    return [dict(e) for e in base or []
            if not (str(e.get("decision") or "").startswith("cd-")
                    and str(e.get("decision")) not in live)]


def with_exceptions(base: Iterable[dict], decisions: Iterable[Mapping]) -> list[dict]:
    """The session's documented gaps with the gaps of the owner's decisions on top
    (one per function), less the gaps of decisions that no longer stand."""
    decisions = list(decisions or [])
    owner = coverage_exceptions(decisions)
    mine = {str(e.get("functionId")) for e in owner}
    return [e for e in live_exceptions(base, decisions)
            if str(e.get("functionId")) not in mine] + owner


def stale(decisions: Iterable[Mapping], build: Optional[Mapping], *,
          built=()) -> list[tuple[dict, str]]:
    """``[(decision, why)]`` for decisions the session cannot apply: the curve is not
    in this version, an included curve is no longer one the build left out, or
    the build now fits a curve the owner chose a source for."""
    from . import pressure_evidence as pe
    built = {str(k) for k in built or ()}
    decisions = list(decisions or [])
    known = set(pe.reference_keys(build)) | built | set(sourced(decisions))
    left_out = _left_out(build)
    out = []
    for d in decisions:
        mk = str(d.get("metric"))
        why = unusable_reason(d)
        if why == EXTENSION_OFF:
            out.append((dict(d), EXTENSION_OFF + " This decision applies nothing here."))
        elif why:
            out.append((dict(d), why))
        elif d.get("action") == SOURCE and mk in built:
            out.append((dict(d), "This build fitted the metric before your choice could hold it "
                                 "out of the fit, so its own curve scores. Build the region "
                                 "again to apply the choice."))
        elif d.get("action") == REMOVE and mk in built:
            out.append((dict(d), "This build fitted the metric before your removal could hold "
                                 "it out of the fit, so the removal does not apply. Build the "
                                 "region again to apply it."))
        elif d.get("action") == SOURCE and (d.get("source") or {}).get("failedAtBuild"):
            out.append((dict(d), "Nothing could be built from the source: "
                        + str((d.get("source") or {}).get("failedAtBuild"))))
        elif d.get("action") == SOURCE and fixed_criteria.is_fixed(mk):
            out.append((dict(d), "A fixed criterion cannot take another source."))
        elif d.get("action") in (REMOVE, UNMAP) and mk not in known:
            out.append((dict(d), "This version has no curve for the metric."))
        elif d.get("action") == INCLUDE and not all(
                (mk, str(f)) in left_out for f in d.get("functions") or []):
            out.append((dict(d), "The build no longer leaves this curve out of the function."))
    return out


__all__ = [
    "RULE", "DECISIONS_FILE", "LEGACY_REMOVALS_FILE", "REMOVE", "UNMAP", "INCLUDE", "SOURCE",
    "EXTENSION_FLAG", "EXTENSION_OFF", "alternatives_enabled", "needs_extension", "bundle_summary",
    "usable", "unusable_reason", "SQT_ADOPTION_VERSION", "PRE_ADOPTION",
    "ACTIONS", "GAP_REASON", "ACTION_LABELS", "FLAG_PREFIX", "min_rationale", "new_decision",
    "check", "validate", "load", "path_of", "standing", "load_file", "seed", "supersedes",
    "merge", "save", "undo", "combine", "restore", "from_removals", "effective_selection",
    "applies", "sourced", "chosen", "source_curve", "decision_annotation", "effective_build",
    "apply_to_inputs", "summary", "requests", "decisions_changed", "removed",
    "decisions_for", "coverage_exceptions", "live_exceptions", "with_exceptions", "stale",
    "held_metrics", "forced_sources", "pending", "with_forced",
]
