"""The candidate register: every alternative considered for a function, and what was decided.

One vocabulary for DEEP and EASI (AUTHORING.md, "Candidates and final selection"). A
candidate is one definition (a curve, a method entry) whose ``candidateKey`` hashes what it
is and whose ``basisDigest`` hashes what it holds. A decision selects it for a function or
leaves it out, with the rule, the reason, who decided (automated, imported, person), when,
and the ``basisDigest`` decided on; a decision whose basis no longer matches is flagged for
re-review and stays in the history.

DEEP (:func:`deep_register`) is a projection over the records a session already keeps. It
reads the gallery's tiles (so the register and the gallery never disagree), SELECT-04's
``portfolioSelection``, the withheld list (REF-06), the review (CURVE-07) and the owner's
decisions (REF-15). It writes and decides nothing: REF-15 stays the only writer of what a
version scores. Curves added for comparison (a published state SQT curve, an exploration
fit) ride in the session's ``candidate_register`` field until the owner selects one through
REF-15; a person may also record why one was not selected.

EASI keeps its register in the project (``easi_method.register``), with the alternatives of
the 2026-09-15 controlled study imported by ``easi_method.alternatives``.

Nothing here enters a DEEP bundle or an EASI method package. Every string is user-visible,
so none carries an em dash.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

SELECTED, ELIGIBLE, EXCLUDED, NOT_EVALUATED, FAILED, SUPERSEDED = (
    "selected", "eligible_not_selected", "excluded", "not_evaluated", "failed", "superseded")
STATUSES = (SELECTED, ELIGIBLE, EXCLUDED, NOT_EVALUATED, FAILED, SUPERSEDED)
STATUS_LABELS = {SELECTED: "Selected", ELIGIBLE: "Eligible, not selected",
                 EXCLUDED: "Excluded or unsupported", NOT_EVALUATED: "Not evaluated",
                 FAILED: "Failed to build", SUPERSEDED: "Superseded"}
AUTOMATED, IMPORTED, PERSON = "automated", "imported", "person"
DECIDED_BY = (AUTOMATED, IMPORTED, PERSON)
DECIDED_BY_LABELS = {AUTOMATED: "Rule", IMPORTED: "Imported record", PERSON: "Person"}

#: the register's source kinds (AUTHORING.md)
SOURCE_KINDS = ("fitted", "carried", "national", "modeled", "published_benchmark", "fixed",
                "sqt", "owner_entered", "borrowed", "earlier_version", "imported_alternative")
SOURCE_KIND_LABELS = {"fitted": "Built here", "carried": "Carried forward",
                      "national": "National reference", "modeled": "Modeled expectation",
                      "published_benchmark": "Published criterion", "fixed": "Fixed criterion",
                      "sqt": "State SQT", "owner_entered": "Entered by the owner",
                      "borrowed": "From another assessment",
                      "earlier_version": "Earlier version",
                      "imported_alternative": "Study alternative"}
#: a gallery tile's ``source_kind`` (``curve_sources.KINDS``) as a register source kind
_TILE_KINDS = {"owner_exception": "fitted"}
#: the session field that holds what the register adds: considered candidates and the
#: dispositions a person recorded for them
SESSION_FIELD = "candidate_register"
REGISTER_SCHEMA = 1
#: at most this many candidates are compared side by side
MAX_COMPARE = 3


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def candidate_key(identity: Mapping) -> str:
    """What the candidate is, never its content: a refit keeps the key and its history."""
    return "cand-" + hashlib.sha256(canonical(dict(identity))).hexdigest()[:12]


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _r(v: Any) -> Optional[float]:
    """A coordinate as the digest sees it: 12 significant digits, so a save and reopen
    or a restore from the bundle never reads as a change."""
    f = _num(v)
    if f is None:
        return None
    return 0.0 if abs(f) < 1e-12 else float(f"{f:.12g}")


def curve_basis_digest(strata: Iterable[Mapping], *, higher_is_better: Any = None,
                       extra: Optional[Mapping] = None) -> Optional[str]:
    """The analytical content of a curve: every stratum's label and points, and its
    direction. Captions, badges and caveat text never enter it. None for no points."""
    body = []
    for s in strata or []:
        pts = [[_r(x), _r(y)] for x, y in (s.get("points") or [])
               if _r(x) is not None and _r(y) is not None]
        if pts:
            body.append({"stratum": s.get("label"), "points": pts})
    if not body:
        return None
    doc = {"strata": sorted(body, key=lambda b: str(b["stratum"] or "")),
           "higherIsBetter": higher_is_better, "extra": dict(extra or {})}
    return "sha256:" + hashlib.sha256(canonical(doc)).hexdigest()


# --------------------------------------------------------------------------- #
# the register a session keeps (considered candidates and dispositions)
# --------------------------------------------------------------------------- #
def empty_register() -> dict:
    return {"schema": REGISTER_SCHEMA, "considered": [], "decisions": []}


def load_register(value: Any) -> dict:
    """The session's ``candidate_register`` field, or an empty one. A newer schema is
    refused whole, never read in part."""
    if not isinstance(value, Mapping):
        return empty_register()
    if int(value.get("schema") or 0) > REGISTER_SCHEMA:
        raise ValueError("This project's candidate register was written by a newer StreamCurves. "
                         "Update StreamCurves to open it.")
    out = empty_register()
    out["considered"] = [dict(c) for c in value.get("considered") or [] if isinstance(c, Mapping)]
    out["decisions"] = [dict(d) for d in value.get("decisions") or [] if isinstance(d, Mapping)]
    return out


def decision_id(decisions: Iterable[Mapping], key: str, *parts: Any) -> str:
    """An id for a new decision on ``key``: a digest of what it records, so the id of an
    undone decision is not handed out again, and never one ``decisions`` already holds."""
    taken = {str(d.get("decisionId")) for d in decisions or []}
    base = (f"dec-{str(key)[5:]}-"
            + hashlib.sha256(canonical([str(key), *[str(p) for p in parts]])).hexdigest()[:10])
    out, n = base, 2
    while out in taken:
        out, n = f"{base}-{n}", n + 1
    return out


def _considered_functions(c: Mapping) -> list[str]:
    ident = c.get("identity") or {}
    fns = c.get("functions") or ident.get("functions") or (
        [ident["functionId"]] if ident.get("functionId") else [])
    return [str(f) for f in fns]


def add_considered(register: Any, candidate: Mapping, *, by: str, at: Optional[str] = None) -> dict:
    """The register with ``candidate`` added for comparison; nothing is selected by adding.
    Adding a candidate with the same key again (the same record, function and region)
    replaces its definition and keeps its dispositions. A refreshed SQT record has another
    content fingerprint, so it is another candidate, beside the one added before."""
    who = str(by or "").strip()
    if not who:
        raise ValueError("Adding a candidate needs the name of the person adding it.")
    reg = load_register(register)
    c = copy.deepcopy(dict(candidate))
    if not str(c.get("candidateKey") or "").startswith("cand-"):
        raise ValueError("A candidate needs a candidate key.")
    c.setdefault("addedBy", who)
    c.setdefault("addedAt", at or _now())
    reg["considered"] = [x for x in reg["considered"] if x.get("candidateKey") != c["candidateKey"]]
    reg["considered"].append(c)
    return reg


def remove_considered(register: Any, key: str, *, decisions: Iterable[Mapping] = ()) -> dict:
    """The register without a considered candidate. Its dispositions go with it; one that a
    REF-15 decision (in ``decisions``, the session's) selects cannot be removed until that
    decision is undone."""
    for d in decisions or []:
        if str(((d.get("source") or {}).get("ref") or {}).get("candidateKey") or "") == str(key):
            raise ValueError("An owner decision (REF-15) selects this curve. Undo that decision "
                             "before removing it.")
    reg = load_register(register)
    reg["considered"] = [c for c in reg["considered"] if c.get("candidateKey") != key]
    reg["decisions"] = [d for d in reg["decisions"] if d.get("candidateKey") != key]
    return reg


def record_disposition(register: Any, key: str, function_id: str, *, by: str, reason: str,
                       basis_digest: Optional[str] = None, at: Optional[str] = None,
                       min_reason: int = 20) -> dict:
    """A person records why a considered candidate is not selected for a function. It
    changes nothing a version scores (REF-15 decides that); it is the reason the register
    shows. A later disposition for the same pair supersedes the earlier one, which stays."""
    who, why = str(by or "").strip(), " ".join(str(reason or "").split())
    if not who:
        raise ValueError("A decision needs the name of the person making it.")
    if len(why) < min_reason:
        raise ValueError(f"Give a reason of at least {min_reason} characters.")
    reg = load_register(register)
    cand = next((c for c in reg["considered"] if c.get("candidateKey") == key), None)
    if cand is None:
        raise ValueError("That candidate is not in this register.")
    if str(function_id) not in _considered_functions(cand):
        raise ValueError("That candidate is not considered for this function.")
    prior = [d for d in reg["decisions"]
             if d.get("candidateKey") == key and d.get("functionId") == str(function_id)]
    when = at or _now()
    reg["decisions"].append({
        "decisionId": decision_id(reg["decisions"], key, function_id, who, when, why),
        "candidateKey": key,
        "functionId": str(function_id), "decision": "not_selected", "rule": "person",
        "reason": why, "decidedBy": PERSON, "who": who, "when": when,
        "basisDigest": basis_digest, "supersedes": prior[-1]["decisionId"] if prior else None})
    return reg


def withdraw_disposition(register: Any, decision_id: str) -> dict:
    """Undo a disposition: it leaves the register (the one it superseded applies again)."""
    reg = load_register(register)
    reg["decisions"] = [d for d in reg["decisions"] if d.get("decisionId") != decision_id]
    return reg


def _live_dispositions(reg: Mapping) -> dict:
    """``{(key, function id): decision}``: the latest disposition of each pair."""
    out: dict = {}
    for d in reg.get("decisions") or []:
        out[(str(d.get("candidateKey")), str(d.get("functionId")))] = d
    return out


# --------------------------------------------------------------------------- #
# DEEP: the projection
# --------------------------------------------------------------------------- #
def _functions() -> list[tuple[str, str, str]]:
    """``[(id, name, discipline)]`` of the 20 STAF functions, in framework order."""
    from .staf_library import staf_function_meta
    meta = staf_function_meta()
    return [(str(i), str(n), str(d)) for i, n, d in zip(meta["id"], meta["name"], meta["discipline"])]


def _tile_functions(tile: Mapping) -> list[str]:
    out = []
    for fid in [tile.get("function_id")] + [f.get("id") for f in tile.get("also_function_refs") or []]:
        if fid and str(fid) not in out:
            out.append(str(fid))
    return out


def _tile_digest(tile: Mapping, config: Optional[Mapping] = None) -> Optional[str]:
    hib = (config or {}).get("higher_is_better")
    return curve_basis_digest(tile.get("strata") or [], higher_is_better=hib)


def tile_basis_digest(tile: Optional[Mapping], config: Optional[Mapping] = None) -> Optional[str]:
    """A gallery tile's basis digest, as the register computes it (what a REF-15 decision
    records, so a later change to the curve asks for the decision to be looked at again)."""
    return _tile_digest(tile, config) if tile else None


def _deep_identity(metric: str, kind: str, source_ref: Mapping, region: Mapping) -> dict:
    return {"assessmentType": "deep", "subject": {"kind": "metric", "id": str(metric)},
            "sourceKind": kind, "sourceRef": dict(source_ref),
            "applicability": {"geography": {"kind": "ecoregion", "code": region.get("code")}}}


def _decision(key: str, fid: str, decision: str, *, rule: str, reason: str, by: str,
              who: Any = None, when: Any = None, basis: Optional[str] = None,
              ref: Optional[str] = None) -> dict:
    return {"candidateKey": key, "functionId": fid, "decision": decision, "rule": rule,
            "reason": reason, "decidedBy": by, "who": who, "when": when, "basisDigest": basis,
            "decisionRef": ref}


def _owner_source_kind(tile: Mapping, decision: Optional[Mapping]) -> str:
    src = ((decision or {}).get("source") or {})
    kind = str(src.get("kind") or "")
    if kind == "sqt":
        return "sqt"
    if kind == "catalog":
        return "published_benchmark"
    if kind == "earlier_version":
        return "earlier_version"
    if kind == "other_assessment":
        return "borrowed"
    if kind == "entered":
        return "owner_entered"
    return _TILE_KINDS.get(str(tile.get("source_kind")), str(tile.get("source_kind") or "fitted"))


def _select04_reason(entry: Mapping) -> str:
    src = entry.get("source")
    score = _num(entry.get("score"))
    parts = [f"source {src}" if src else "", f"metric score {score:g}" if score is not None else ""]
    detail = ", ".join(p for p in parts if p)
    reserve = " It is a reserve metric." if entry.get("reserve") else ""
    return ("Supported, not selected: the function already has two metrics from a higher source "
            f"or with a higher score (SELECT-04){' (' + detail + ')' if detail else ''}.{reserve}")


def deep_register(*, tiles: Iterable[Mapping], build: Optional[Mapping],
                  decisions: Iterable[Mapping] = (), metric_config: Optional[Mapping] = None,
                  register: Any = None, coverage_exceptions: Iterable[Mapping] = (),
                  region: Optional[Mapping] = None) -> dict:
    """The register of a DEEP session: every candidate, one row per candidate and function.

    ``tiles``: the gallery's tiles with reference tiles included
    (``views.curve_gallery.gallery_rows(state, include_reference=True)``); ``build``: the
    session's ``reference_build`` as the build wrote it; ``decisions``: the owner's REF-15
    decisions; ``register``: the session's ``candidate_register`` field. Returns
    ``{"candidates": [...], "rows": [...], "functions": [...]}``; ``functions`` holds the 20
    STAF functions in framework order with their selected rows, alternatives, gap and
    unresolved items. A decision this methodology does not apply (the REF-15 extension while
    it is off) selects nothing here, as it scores nothing in the version."""
    from . import curve_sources as cs_
    from . import owner_curves as oc
    region = dict(region or {})
    config = dict(metric_config or {})
    decisions = [dict(d) for d in decisions or [] if isinstance(d, Mapping)]
    applied = [d for d in decisions if not (oc.needs_extension(d) and not oc.alternatives_enabled())]
    built = [str(t.get("metric")) for t in tiles if not t.get("read_only")]
    effective = oc.effective_build(build, applied, built=built) or {}
    portfolio_recorded = "portfolioSelection" in (build or {})
    selection = effective.get("portfolioSelection") or {}
    not_sel: dict[tuple[str, str], dict] = {}
    for fid, sel in selection.items():
        for x in (sel or {}).get("notSelected") or []:
            not_sel[(str(x.get("metric")), str(fid))] = dict(x)
    included = {(str(d.get("metric")), str(f)): d for d in applied
                if d.get("action") == oc.INCLUDE for f in d.get("functions") or []}
    by_id = {str(d.get("id")): d for d in decisions}
    reg = load_register(register)
    considered = {str(c.get("candidateKey")): c for c in reg["considered"]}
    candidates: dict[str, dict] = {}
    rows: list[dict] = []
    done: set = set()                 # (candidate, function) pairs already stated

    def add(identity: dict, *, label: str, basis: Optional[str], build_status: str,
            tile: Optional[Mapping] = None, eligibility: Optional[dict] = None,
            key: Optional[str] = None) -> str:
        key = key or candidate_key(identity)
        if key not in candidates:
            c = (copy.deepcopy(dict(considered[key])) if key in considered else
                 {"candidateKey": key, "identity": identity, "basisDigest": basis,
                  "purpose": "operational", "campaign": None, "buildStatus": build_status,
                  "supersededBy": None, "label": label,
                  "eligibility": eligibility or {"status": "eligible", "reasons": [], "checks": []}})
            if tile is not None and not c.get("tile"):
                c["tile"] = {k: tile.get(k) for k in ("metric", "display_name", "units", "strata",
                                                      "reference_range", "domain", "badge",
                                                      "reference_n", "status_text")}
            candidates[key] = c
        return key

    def row(key: str, fid: str, status: str, decision: dict, *, unresolved: str = "") -> None:
        if (key, fid) in done:
            return
        done.add((key, fid))
        rows.append({"candidateKey": key, "functionId": fid, "status": status,
                     "decision": decision, "unresolved": unresolved,
                     "needsReview": bool(decision.get("basisDigest") and candidates[key].get("basisDigest")
                                         and decision["basisDigest"] != candidates[key]["basisDigest"])})

    def owner_row(key: str, fid: str, status: str, d: Mapping, verb: str) -> None:
        row(key, fid, status, _decision(
            key, fid, verb, rule="REF-15", reason=str(d.get("rationale") or ""), by=PERSON,
            who=d.get("recordedBy"), when=d.get("recordedAt"), basis=d.get("basisDigest"),
            ref=d.get("id")))

    for t in tiles:
        mk = str(t.get("metric") or "")
        if not mk:
            continue
        fns = _tile_functions(t)
        cfg = config.get(mk) or {}
        digest = _tile_digest(t, cfg)
        name = str(t.get("display_name") or mk)
        if t.get("read_only"):
            owner_d = by_id.get(str(t.get("owner_decision"))) if t.get("owner_decision") else None
            kind = _owner_source_kind(t, owner_d) if owner_d else \
                _TILE_KINDS.get(str(t.get("source_kind")), str(t.get("source_kind") or "carried"))
            ref = {"curve": str(t.get("badge") or "")}
            same_as = None
            if owner_d:
                src = owner_d.get("source") or {}
                ref = {"decision": owner_d.get("id"), "kind": src.get("kind"),
                       **{k: v for k, v in (src.get("ref") or {}).items()
                          if k in ("candidateKey", "recordId", "version", "assessmentId", "rule",
                                   "option", "state", "edition")}}
                # a considered candidate the owner selected is that candidate, not a second one
                ck = str((src.get("ref") or {}).get("candidateKey") or "")
                same_as = ck if ck in considered else None
            key = add(_deep_identity(mk, kind, ref, region), label=name, basis=digest,
                      build_status="built", tile=t, key=same_as)
            ns_fids = {f for (m, f) in not_sel if m == mk}
            for fid in dict.fromkeys(fns + sorted(ns_fids)):
                if t.get("removed_decision"):
                    owner_row(key, fid, ELIGIBLE, by_id.get(str(t["removed_decision"])) or {}, "not_selected")
                elif (mk, fid) in not_sel:
                    x = not_sel[(mk, fid)]
                    if x.get("owner"):
                        owner_row(key, fid, ELIGIBLE, by_id.get(str(x.get("decision"))) or {}, "not_selected")
                    else:
                        row(key, fid, ELIGIBLE, _decision(key, fid, "not_selected", rule="SELECT-04",
                                                          reason=_select04_reason(x), by=AUTOMATED))
                elif owner_d:
                    owner_row(key, fid, SELECTED, owner_d, "selected")
                else:
                    rule = cs_.KIND_RULES.get(kind, "REF-12")
                    why = {"carried": "Carried forward from the published version, which keeps its "
                                      "place in the function.",
                           "fixed": "A fixed criterion, scored the same way in every region.",
                           "national": "The build's national reference, after the station pools.",
                           "modeled": "The build's modeled reference, after the station pools.",
                           "published_benchmark": "The build's published criterion, after the "
                                                  "station pools and the modeled reference."}.get(
                        kind, "The build's source after the station pools.")
                    row(key, fid, SELECTED, _decision(key, fid, "selected", rule=rule, reason=why,
                                                      by=AUTOMATED))
            continue
        # a curve fitted in this build
        has_points = digest is not None
        key = add(_deep_identity(mk, "fitted", {"build": "this session"}, region), label=name,
                  basis=digest, build_status="built" if has_points else "failed", tile=t)
        decision_code = str(t.get("decision") or "")
        for fid in fns:
            if decision_code == "removed_from_scope":
                row(key, fid, EXCLUDED, _decision(
                    key, fid, "not_selected", rule="review",
                    reason="Taken out of scope in the curve review.", by=PERSON))
            elif not has_points:
                row(key, fid, FAILED, _decision(key, fid, "not_selected", rule="build",
                                                reason="The build produced no usable curve.",
                                                by=AUTOMATED))
            elif t.get("needs_review"):
                row(key, fid, NOT_EVALUATED, _decision(
                    key, fid, "pending", rule="CURVE-07",
                    reason="Held for review: a reviewer has not cleared this curve yet.",
                    by=AUTOMATED), unresolved="review")
            elif (mk, fid) in not_sel:
                x = not_sel[(mk, fid)]
                if x.get("owner"):
                    owner_row(key, fid, ELIGIBLE, by_id.get(str(x.get("decision"))) or {}, "not_selected")
                else:
                    row(key, fid, ELIGIBLE, _decision(key, fid, "not_selected", rule="SELECT-04",
                                                      reason=_select04_reason(x), by=AUTOMATED))
            elif (mk, fid) in included:
                owner_row(key, fid, SELECTED, included[(mk, fid)], "selected")
            elif portfolio_recorded:
                row(key, fid, SELECTED, _decision(
                    key, fid, "selected", rule="SELECT-04",
                    reason="Fitted here and chosen for the function by the portfolio rule.",
                    by=AUTOMATED))
            else:
                row(key, fid, SELECTED, _decision(
                    key, fid, "selected", rule="mapping",
                    reason="Fitted here and scored in the function by the version's function "
                           "mapping; the version was built before the portfolio rule (SELECT-04).",
                    by=AUTOMATED))
    # a metric the build could not support: no curve, one record per function
    have = {str(t.get("metric")) for t in tiles}
    for w in effective.get("insufficientReferenceSupport") or []:
        mk = str(w.get("metricKey") or "")
        if not mk or mk in have:
            continue
        held = w.get("reason") == "held-for-review"
        key = add(_deep_identity(mk, "fitted", {"build": "this session"}, region),
                  label=str(w.get("metricName") or mk), basis=None, build_status="not_run",
                  eligibility={"status": "excluded", "reasons": [str(w.get("statement") or "")],
                               "checks": []})
        for f in w.get("functions") or [{"functionId": w.get("functionId")}]:
            fid = str((f or {}).get("functionId") or "")
            if fid:
                row(key, fid, EXCLUDED, _decision(key, fid, "not_selected",
                                                  rule="CURVE-07" if held else "REF-06",
                                                  reason=str(w.get("statement") or ""), by=AUTOMATED))
    # the candidates added for comparison
    chosen = {}
    for d in applied:
        if d.get("action") == oc.SOURCE:
            ck = ((d.get("source") or {}).get("ref") or {}).get("candidateKey")
            if ck:
                for f in d.get("functions") or []:
                    chosen[(str(ck), str(f))] = d
    dispositions = _live_dispositions(reg)
    for key, c in considered.items():
        if key not in candidates:
            candidates[key] = copy.deepcopy(dict(c))
        cand = candidates[key]
        fns = [str(f) for f in cand.get("functions") or (cand.get("identity") or {}).get("functions") or []]
        eligible = (cand.get("eligibility") or {}).get("status", "eligible") == "eligible"
        for fid in fns:
            if (key, fid) in chosen:
                owner_row(key, fid, SELECTED, chosen[(key, fid)], "selected")
            elif (key, fid) in dispositions:
                d = dispositions[(key, fid)]
                row(key, fid, ELIGIBLE if eligible else EXCLUDED, _decision(
                    key, fid, "not_selected", rule="person", reason=str(d.get("reason") or ""),
                    by=PERSON, who=d.get("who"), when=d.get("when"), basis=d.get("basisDigest"),
                    ref=d.get("decisionId")))
            elif not eligible:
                reasons = (cand.get("eligibility") or {}).get("reasons") or []
                row(key, fid, EXCLUDED, _decision(key, fid, "not_selected", rule="applicability",
                                                  reason=" ".join(str(r) for r in reasons),
                                                  by=AUTOMATED))
            else:
                added = " ".join(x for x in (f"by {cand['addedBy']}" if cand.get("addedBy") else "",
                                             f"on {str(cand.get('addedAt'))[:10]}" if cand.get("addedAt") else "")
                                 if x)
                row(key, fid, NOT_EVALUATED, _decision(
                    key, fid, "pending", rule="considered",
                    reason=f"Added for comparison{' ' + added if added else ''}. No decision yet.",
                    by=None), unresolved="undecided")
    return {"candidates": list(candidates.values()), "rows": rows,
            "functions": function_rows(rows, candidates, decisions=decisions, build=build,
                                       built=built, coverage_exceptions=coverage_exceptions)}


def function_rows(rows: list[dict], candidates: Mapping, *, decisions=(), build=None, built=(),
                  coverage_exceptions: Iterable[Mapping] = ()) -> list[dict]:
    """The 20 STAF functions in framework order: each with its selected rows, the other
    rows (alternatives considered), a documented gap when nothing is selected, and the
    unresolved items (a curve held for review, a candidate with no decision, a decision to
    look at again, a request waiting for a build, a decision the version cannot apply)."""
    from . import owner_curves as oc
    gaps = {str(g.get("functionId")): g for g in coverage_exceptions or [] if isinstance(g, Mapping)}
    for g in oc.coverage_exceptions(decisions or []):
        gaps[str(g.get("functionId"))] = g
    pending = oc.pending(decisions or [])
    stale = oc.stale(decisions or [], build, built=built) if build else []
    order = {s: i for i, s in enumerate(STATUSES)}
    out = []
    for fid, name, disc in _functions():
        mine = sorted([r for r in rows if r["functionId"] == fid],
                      key=lambda r: (order.get(r["status"], 9),
                                     str((candidates.get(r["candidateKey"]) or {}).get("label") or "")))
        selected = [r for r in mine if r["status"] == SELECTED]
        unresolved = [r for r in mine if r.get("unresolved")]
        review = [r for r in mine if r.get("needsReview")]
        waiting = [d for mk, d in pending.items() if fid in [str(f) for f in d.get("functions") or []]]
        cannot = [(d, why) for d, why in stale if fid in [str(f) for f in d.get("functions") or []]]
        out.append({"functionId": fid, "functionName": name, "discipline": disc,
                    "selected": selected, "alternatives": [r for r in mine if r["status"] != SELECTED],
                    "gap": None if selected else gaps.get(fid),
                    "unassessed": not selected,
                    "unresolved": len(unresolved) + len(review) + len(waiting) + len(cannot),
                    "waiting": [oc.summary(d) for d in waiting],
                    "stale": [{"decision": oc.summary(d), "why": why} for d, why in cannot]})
    return out


def register_counts(register: Mapping) -> dict:
    """``{status: n}`` over the rows, plus functions selected, unassessed and unresolved."""
    counts = {s: 0 for s in STATUSES}
    for r in register.get("rows") or []:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    fns = register.get("functions") or []
    counts["functionsSelected"] = sum(1 for f in fns if f["selected"])
    counts["functionsUnassessed"] = sum(1 for f in fns if f["unassessed"])
    counts["unresolved"] = sum(f["unresolved"] for f in fns)
    return counts


def export_rows(register: Mapping) -> list[dict]:
    """One flat record per candidate and function, for a table export or a version diff:
    what it is, its status, the decision, who and why. No curve data."""
    cands = {c["candidateKey"]: c for c in register.get("candidates") or []}
    names = {fid: name for fid, name, _ in _functions()}
    out = []
    for r in register.get("rows") or []:
        c = cands.get(r["candidateKey"]) or {}
        ident = c.get("identity") or {}
        d = r.get("decision") or {}
        out.append({"functionId": r["functionId"], "function": names.get(r["functionId"], r["functionId"]),
                    "candidateKey": r["candidateKey"], "candidate": c.get("label"),
                    "subject": (ident.get("subject") or {}).get("id"),
                    "sourceKind": ident.get("sourceKind"), "status": r["status"],
                    "rule": d.get("rule"), "decidedBy": d.get("decidedBy"), "who": d.get("who"),
                    "when": d.get("when"), "reason": d.get("reason"),
                    "basisDigest": c.get("basisDigest"), "needsReview": bool(r.get("needsReview"))})
    return sorted(out, key=lambda x: (x["functionId"], x["candidateKey"]))


def diff(before: Iterable[Mapping], after: Iterable[Mapping]) -> list[dict]:
    """What changed between two exports (:func:`export_rows`), per candidate and function:
    added, removed, or a changed status, decision or basis."""
    key = lambda r: (r["functionId"], r["candidateKey"])  # noqa: E731
    a = {key(r): r for r in before or []}
    b = {key(r): r for r in after or []}
    out = []
    for k in sorted(set(a) | set(b)):
        x, y = a.get(k), b.get(k)
        if x is None:
            out.append({"change": "added", **y})
        elif y is None:
            out.append({"change": "removed", **x})
        else:
            fields = [f for f in ("status", "rule", "decidedBy", "who", "reason", "basisDigest")
                      if x.get(f) != y.get(f)]
            if fields:
                out.append({"change": "changed", "fields": fields, "before": x, "after": y})
    return out


# --------------------------------------------------------------------------- #
# a published state SQT curve as a candidate (streamcurves.sqt_registry)
# --------------------------------------------------------------------------- #
def sqt_metric_key(record: Mapping) -> str:
    """The metric key an SQT curve takes in a DEEP session: its own, never an NRSA key
    (the SQT measures by its own protocol)."""
    import re
    key = str(record.get("key") or "").split(":", 1)[-1]
    return "sqt_" + re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


#: registry issues that describe the older adapted assessments or the registry's own work,
#: not a limit of the adopted curve (they stay in the registry and the candidate record)
_AUTHORING_ISSUES = {"adapted-held-flat", "adapted-threshold-as-point", "adapted-drops-breakpoint",
                     "adapted-placeholder", "adapted-clamped", "does-not-reach-0", "does-not-reach-1",
                     "missing-breakpoints", "extra-breakpoint"}


def _interp(points: list, x: float) -> float:
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def sqt_adoption(record: Mapping) -> dict:
    """The published rule written as a DEEP curve, or why it cannot be.

    DEEP interpolates between points and holds the end value past the last one. The rule
    is taken from the original's own points where the registry verified them, else from the
    metric library's bins; an end the source extends linearly is extended to the index limit
    it reaches; an end threshold bin (">3 scores 0") becomes a step just past its value. What
    DEEP cannot write (a logarithmic original, a two-sided or tabulated form, a threshold
    inside the curve) is a blocker. Returns ``{points, notes, blockers, openEnds}``: an open
    end is one that stops inside the index range where the source does not say how it
    scores beyond it."""
    ver = record.get("verification") or {}
    issues = {str(i.get("code")) for i in record.get("issues") or []}
    against = ver.get("against") or {}
    notes: list = []
    blockers: list = []
    if ver.get("status") in ("verified", "partially-verified") and ver.get("originalPoints"):
        base = ver["originalPoints"]
        notes.append(f"Points from the original ({against.get('citation') or 'the source file'}).")
    else:
        base = record.get("normalizedPoints") or []
    pts = sorted((float(p["x"]), float(p["y"])) for p in base)
    form = record.get("form")
    if form != "piecewise":
        blockers.append(f"DEEP adopts a curve of points; this SQT rule is a {str(form).replace('-', ' ')}.")
    if "original-log-form" in issues:
        blockers.append("The original scores with a logarithmic curve; straight segments between its "
                        "points do not reproduce it.")
    if len(pts) < 2:
        blockers.append("The source gives fewer than two points.")
        return {"points": pts, "notes": notes, "blockers": blockers, "openEnds": []}
    for th in record.get("thresholds") or []:
        v, idx, op = float(th.get("value")), float(th.get("index")), str(th.get("op"))
        eps = max(abs(v), 1.0) * 1e-6
        if op == ">" and v == pts[-1][0]:
            pts.append((v + eps, idx))
            notes.append(f"The bin \"> {v:g} scores {idx:g}\" is written as a step just past {v:g}.")
        elif op == "<" and v == pts[0][0]:
            pts.insert(0, (v - eps, idx))
            notes.append(f"The bin \"< {v:g} scores {idx:g}\" is written as a step just before {v:g}.")
        else:
            blockers.append(f"The bin \"{op} {v:g} scores {idx:g}\" cannot be written as a curve.")
    open_ends = []
    ext = record.get("extrapolation")
    for side in ("low", "high"):
        (x0, y0), (x1, y1) = (pts[0], pts[1]) if side == "low" else (pts[-1], pts[-2])
        if y0 in (0.0, 1.0):
            continue                    # the index holds at its limit, as DEEP holds it
        if ext == "linear" and x1 != x0 and y1 != y0:
            m = (y1 - y0) / (x1 - x0)
            target = (0.0 if m > 0 else 1.0) if side == "low" else (1.0 if m > 0 else 0.0)
            xt = x0 + (target - y0) / m
            if side == "low":
                pts.insert(0, (xt, target))
            else:
                pts.append((xt, target))
            notes.append(f"The {side} end is extended along its last segment to {target:g} at "
                         f"{xt:.4g}, as the source's segment equations do.")
        else:
            open_ends.append({"side": side, "x": x0, "y": y0})
    return {"points": pts, "notes": notes, "blockers": blockers, "openEnds": open_ends}


def _sqt_siblings(frozen: Mapping) -> list[str]:
    """The other strata the same state's SQT scores this metric in."""
    from . import sqt_registry as reg
    parts = str(frozen.get("key") or "").split(":")
    if len(parts) < 3:
        return []
    return sorted({str(r.get("stratumName")) for r in reg.records(state=frozen.get("state"))
                   if str(r.get("key") or "").split(":")[2:3] == parts[2:3]
                   and r.get("key") != frozen.get("key")})


def sqt_candidate(record: Mapping, *, function_id: str, context: Optional[Mapping] = None,
                  region: Optional[Mapping] = None) -> dict:
    """A considered candidate from one SQT registry record for one function, frozen: the
    copy carries everything the curve needs, so a later registry build never changes it.
    It is eligible only when DEEP can reproduce the published rule (``sqt_adoption``), the
    registry marks the record eligible, no applicability check fails, and the record is not
    one stratum of a stratified metric (DEEP would apply that stratum to every site)."""
    from . import sqt_registry as reg
    ctx = dict(context or {})
    frozen = reg.frozen_copy(record)
    checks = reg.applicability(frozen, ctx)
    adoption = sqt_adoption(frozen)
    rng = ctx.get("xRange")
    xs = [x for x, _ in adoption["points"]]
    inside = (isinstance(rng, (list, tuple)) and len(rng) == 2 and xs
              and float(rng[0]) >= xs[0] and float(rng[1]) <= xs[-1])
    if adoption["openEnds"] and not inside:
        ends = " and ".join(f"{e['side']} end ({e['x']:g} scores {e['y']:g})" for e in adoption["openEnds"])
        checks.append({"id": "past-ends", "status": "fail",
                       "detail": f"The source does not say how it scores past its {ends}; DEEP would hold "
                                 "the end value there. Only a target whose values all fall inside the "
                                 "curve can use it."})
    for b in adoption["blockers"]:
        checks.append({"id": "form", "status": "fail", "detail": b})
    siblings = _sqt_siblings(frozen)
    if siblings and not ctx.get("stratumCoversTarget"):
        checks.append({"id": "strata", "status": "fail",
                       "detail": f"The SQT scores this metric by stratum ({frozen.get('stratumName')}; also "
                                 f"{', '.join(siblings)}). DEEP would apply this one stratum to every site."})
    elif siblings:
        checks.append({"id": "strata", "status": "pass",
                       "detail": f"Recorded as covering the whole target: {frozen.get('stratumName')}."})
    worst = reg.overall(checks)
    region = dict(region or {})
    identity = {"assessmentType": "deep", "subject": {"kind": "metric", "id": sqt_metric_key(frozen)},
                "functionId": str(function_id), "sourceKind": "sqt",
                "sourceRef": {"registryKey": frozen.get("key"), "state": frozen.get("state"),
                              "tool": frozen.get("tool"), "edition": frozen.get("edition"),
                              "fingerprint": frozen["frozen"]["contentFingerprint"]},
                "applicability": {"geography": {"kind": "ecoregion", "code": region.get("code")}}}
    direction = frozen.get("direction")
    ver = frozen.get("verification") or {}
    reasons = []
    if not frozen.get("eligible"):
        reasons.append("The registry marks this curve ineligible: "
                       + "; ".join(str(i.get("detail")) for i in frozen.get("issues") or []
                                   if i.get("severity") == "defect"))
    reasons += [c["detail"] for c in checks if c.get("status") == "fail"]
    eligible = bool(frozen.get("eligible")) and worst != "fail"
    # what limits the adopted curve (never the registry's own authoring notes)
    limitations = [] if ver.get("status") == "verified" else [
        "Checked only in part against the original." if ver.get("status") == "partially-verified" else
        "A STAF adaptation of the SQT, not checked against the original."]
    limitations += [str(i.get("detail")) for i in frozen.get("issues") or []
                    if i.get("severity") in ("warning", "defect") and i.get("code") not in _AUTHORING_ISSUES]
    limitations += adoption["notes"]
    label = " ".join(x for x in (str(frozen.get("originalMetricName") or ""),
                                 f"({frozen.get('state')} SQT"
                                 + (f", {frozen.get('stratumName')})" if frozen.get("stratumName")
                                    and frozen.get("stratumName") != "Default" else ")")) if x)
    pts = adoption["points"]
    return {"candidateKey": candidate_key(identity), "identity": identity,
            "functions": [str(function_id)],
            "basisDigest": curve_basis_digest([{"label": frozen.get("stratumName"), "points": pts}],
                                              higher_is_better=direction == "increasing"
                                              if direction in ("increasing", "decreasing") else None),
            "purpose": "operational", "campaign": None, "buildStatus": "built", "supersededBy": None,
            "label": label, "verification": ver.get("status"),
            "verificationNotes": list(ver.get("reasons") or []),
            "eligibility": {"status": "eligible" if eligible else "excluded", "reasons": reasons,
                            "checks": checks},
            "limitations": list(dict.fromkeys(limitations)),
            "definition": {"points": [{"x": x, "y": y} for x, y in pts], "units": frozen.get("units"),
                           "stratum": frozen.get("stratumName"), "direction": direction,
                           "extrapolation": frozen.get("extrapolation"),
                           "conversion": adoption["notes"], "openEnds": adoption["openEnds"]},
            "record": frozen}


# --------------------------------------------------------------------------- #
# what an SQT curve is checked against in a session
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def _crosswalk() -> frozenset:
    """``(STAF metric library id, app metric key)`` pairs from the metric library's own
    crosswalk (``config/staf_metric_library.json``), the one the metric workbench uses."""
    from .staf_library import staf_metric_library_entries
    ent = staf_metric_library_entries()
    return frozenset((str(a), str(b)) for a, b in zip(ent["library_id"], ent["app_metric_key"])
                     if isinstance(b, str) and b.strip())


def same_metric(record: Mapping, metric: Any) -> bool:
    """True when the STAF metric library names ``metric`` as this app's key for the metric
    the SQT record scores."""
    return (str(record.get("stafMetricId") or ""), str(metric or "")) in _crosswalk()


def sqt_target(metric: str, config: Optional[Mapping] = None,
               x_range: Optional[Iterable] = None) -> dict:
    """One of the session's curves, as an SQT curve is checked against it: its units, the
    direction its index runs (``higher_is_better``, or two-sided for an optimum) and
    ``[min, max]`` of the values the session scores on it."""
    cfg = dict(config or {})
    hib = cfg.get("higher_is_better")
    shape = str(cfg.get("curve_form") or cfg.get("expected_shape") or "").lower()
    direction = ("increasing" if hib is True else "decreasing" if hib is False else
                 "two-sided" if shape in ("optimum", "trapezoidal", "two-sided") else None)
    rng = [float(v) for v in x_range] if x_range is not None else []
    return {"metric": str(metric), "name": str(cfg.get("display_name") or metric),
            "units": cfg.get("units") or None, "direction": direction,
            "xRange": rng if len(rng) == 2 else None}


def sqt_context(record: Mapping, *, function_id: str, states: Iterable = (),
                targets: Iterable[Mapping] = ()) -> dict:
    """The applicability context of an SQT record for one function of a DEEP session: the
    function, the region's states and the STAF score scale; and, when one of ``targets``
    (:func:`sqt_target`) scores the same metric (:func:`same_metric`), that curve's units,
    direction and value range. When none does, the construct check names the curves."""
    ctx: dict = {"function": str(function_id), "states": [str(s) for s in states or []],
                 "scoreScale": "staf"}
    targets = [dict(t) for t in targets or []]
    same = next((t for t in targets if same_metric(record, t.get("metric"))), None)
    if same is not None:
        ctx.update({"stafMetricId": record.get("stafMetricId"), "targetMetric": same["metric"],
                    "targetName": same.get("name")})
        for k in ("units", "direction", "xRange"):
            if same.get(k) is not None:
                ctx[k] = same[k]
    elif targets:
        ctx["comparedMetrics"] = [str(t.get("name") or t.get("metric")) for t in targets]
    return ctx


__all__ = ["STATUSES", "STATUS_LABELS", "DECIDED_BY", "SOURCE_KINDS", "SOURCE_KIND_LABELS",
           "SESSION_FIELD", "candidate_key", "curve_basis_digest", "load_register",
           "empty_register", "add_considered", "remove_considered", "record_disposition", "decision_id",
           "withdraw_disposition", "deep_register", "function_rows", "register_counts",
           "export_rows", "diff", "MAX_COMPARE", "sqt_candidate", "sqt_metric_key", "sqt_adoption",
           "tile_basis_digest", "same_metric", "sqt_target", "sqt_context"]
