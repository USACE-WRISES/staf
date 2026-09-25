"""Select final curves: the candidate register of a DEEP session, one row per STAF function,
and the one place every decision on it is made.

The third section of the Reference Curves page, beside Gallery and Table. Each function
shows the curves that score it and, folded under it, every alternative considered with its
status and the reason: SELECT-04's supported but not selected curves, curves withheld for
want of reference support (REF-06), curves held for review (CURVE-07), the owner's REF-15
decisions, and curves added for comparison (a published state SQT curve). Up to three can be
compared side by side.

One decision authority (2026-09-25). What a person decides about a version is decided here
and nowhere else, each into the record it always had:

- REF-15 curve decisions (remove, take out of a function, use here, choose a source) and
  their undo, through ``views.source_panel``'s one form and undo (``owner_curves``, the
  session's ``owner_curve_decisions`` and the region's ``curve_decisions.json``);
- CURVE-07 answers on a curve held for review: Accept or Remove with a rationale
  (``curve_automation.set_review_decision`` on the session's ``curve_review``) and the same
  answer appended to the region's ``owner_decisions.json`` (``region_build.save_answer``),
  the file the next build reads;
- the other items a build left open (REF-02, DATA-03, RED-01, STRAT-09 and the rest), the
  "Build items left for you" list read from the build's provenance, answered into
  ``owner_decisions.json``;
- documented gaps (COV-01) into ``function_coverage_exceptions`` and the region's
  ``coverage_exceptions.json`` (``region_build.save_gap``);
- SELECT-01 approvals of a function carrying more than the portfolio maximum, into the
  session's ``portfolio_approvals`` (``views.state``), which the publish writes as
  meta.portfolioApprovals.

The Gallery, the Table, the Function mapping page and the Region builder show these
decisions; they no longer take them. Every string is user-visible, so none carries an em dash.
"""
from __future__ import annotations

import asyncio
import csv
import functools
import io
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import parse_qs

from shiny import reactive, render, ui

from streamcurves import candidates as C
from streamcurves import compare as cmpv
from streamcurves import curve_automation as ca
from streamcurves import curve_svg as cs
from streamcurves import owner_curves as oc
from streamcurves import region_build as rb
from streamcurves import run_state as rs
from views import source_dialog as sd
from views import source_panel as sp
from views import state as st
from views.theme import fa
from views.uihelpers import count_text, guard, linkify_rule_ids, rule_chip

SECTION = "final"
STATUS_CLASS = {C.SELECTED: "is-selected", C.ELIGIBLE: "is-eligible", C.EXCLUDED: "is-excluded",
                C.NOT_EVALUATED: "is-pending", C.FAILED: "is-failed", C.SUPERSEDED: "is-superseded"}
RULE_WORDS = {"SELECT-04": "Portfolio rule (SELECT-04)", "REF-15": "Owner decision (REF-15)",
              "REF-06": "No defensible reference (REF-06)", "CURVE-07": "Held for review (CURVE-07)",
              "CURVE-11": "Fixed criterion (CURVE-11)", "REF-12": "Reference hierarchy",
              # the rule each kind of curve the build chose was chosen under (curve_sources.KIND_RULES):
              # a carried curve keeps its place (REF-05 is the rule the register cites for it)
              "REF-05": "Carried forward (REF-05)", "REF-13": "Modeled reference (REF-13)",
              "REF-14": "Published benchmark (REF-14)", "mapping": "Function mapping",
              "review": "Curve review", "build": "Build", "person": "Recorded by a person",
              "applicability": "Applicability checks", "considered": "Added for comparison",
              # EASI's register (easi_method.alternatives, easi_method.register)
              "study-2026-09-15": "Alternatives study (2026-09-15)",
              "study-2026-09-15-override": "Selected against the alternatives study",
              "field-vs-desktop": "Field protocol vs desktop estimate",
              "owner-adoption-2026-09-16": "Owner's adoption of Alternative 2 (2026-09-16)",
              "historical-baseline": "Historical baseline criteria",
              "operational-at-import": "The operational method when imported",
              "confirmed-after-change": "Confirmed after a change"}


def rule_words(rule: Any) -> str:
    """A rule id as a person reads it: the register's own words, else the methodology's name
    for a catalog rule, never a bare id."""
    rid = str(rule or "")
    if not rid:
        return ""
    if rid in RULE_WORDS:
        return RULE_WORDS[rid]
    try:
        from streamcurves import methodology
        name = (methodology.rule(rid) or {}).get("name")
    except Exception:  # noqa: BLE001 - an unknown id reads as a rule, never raw
        name = None
    return f"{name} ({rid})" if name else "Another rule"


TILE_W, TILE_H = 260, 160
COMPLETE_FIRST = ("Complete the curve first: the source leaves an end open, and the curve is used "
                  "only once points past that end are added.")
#: at most this many points are added past each open end of an SQT curve
COMPLETION_ROWS = 3


def dom_id(function_id: Any) -> str:
    """A namespaced-id-safe element id for a function (letters, digits, underscore)."""
    return "fs_" + re.sub(r"[^A-Za-z0-9_]", "_", str(function_id or ""))


def _onclick(input_id: str, payload: Mapping) -> str:
    text = json.dumps(dict(payload)).replace(chr(39), chr(92) + chr(39))
    return f"event.stopPropagation();Shiny.setInputValue('{input_id}',{text},{{priority:'event'}})"


def candidate_tile(cand: Mapping) -> dict:
    """A thumbnail tile for any candidate: its own tile, or one drawn from its definition."""
    t = dict(cand.get("tile") or {})
    if t.get("strata"):
        return t
    d = cand.get("definition") or {}
    pts = [(float(p["x"]), float(p["y"])) for p in d.get("points") or [] if "x" in p and "y" in p]
    ident = cand.get("identity") or {}
    return {"metric": (ident.get("subject") or {}).get("id"), "display_name": cand.get("label"),
            "units": d.get("units"), "strata": [{"label": d.get("stratum"), "points": pts}],
            "reference_range": (None, None), "domain": None, "badge": cand.get("label")}


def crossings(points, breaks=cs.DEEP_INDEX_BANDS) -> list:
    """Where a curve first reaches each band edge, by linear interpolation (None when it
    never does)."""
    pts = [(float(x), float(y)) for x, y in points or []]
    out = []
    for t in breaks:
        hit = None
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if y0 != y1 and (y0 - t) * (y1 - t) <= 0:
                hit = x0 + (t - y0) * (x1 - x0) / (y1 - y0)
                break
        out.append(hit)
    return out if any(v is not None for v in out) else []


def status_pill(status: str, *, review: bool = False):
    pill = ui.tags.span(C.STATUS_LABELS.get(status, status),
                        class_=f"fs-status {STATUS_CLASS.get(status, '')}")
    if review:
        return ui.TagList(pill, ui.tags.span("Look again", class_="fs-status is-review",
                                             title="The curve changed after this decision."))
    return pill


def _who(decision: Mapping) -> str:
    by = decision.get("decidedBy")
    who = decision.get("who")
    when = str(decision.get("when") or "")[:10]
    head = who if (by == C.PERSON and who) else C.DECIDED_BY_LABELS.get(by, by or "")
    return ", ".join(x for x in (head, when) if x)


def _actions(row: Mapping, cand: Mapping, *, ns, compare: list, extension_on: bool):
    """The buttons a row offers: compare, and what REF-15 or the register can do with it."""
    key, fid = row["candidateKey"], row["functionId"]
    d = row.get("decision") or {}
    ident = cand.get("identity") or {}
    metric = str((ident.get("subject") or {}).get("id") or "")
    channel = ns("final_action")
    out = []
    on = key in compare
    if has_curve(cand):
        out.append(ui.tags.button(fa("check" if on else "table-columns"), " Comparing" if on else " Compare",
                                  type="button",
                                  class_="btn btn-sm " + ("btn-primary" if on else "btn-outline-secondary"),
                                  onclick=_onclick(channel, {"action": "compare", "key": key, "fid": fid}),
                                  **{"aria-pressed": "true" if on else "false"}))
    considered = bool(cand.get("addedBy"))
    if d.get("rule") == "SELECT-04" and row["status"] == C.ELIGIBLE and ident.get("sourceKind") == "fitted":
        out.append(ui.tags.button(fa("plus"), " Use in this function", type="button",
                                  class_="btn btn-sm btn-outline-primary",
                                  onclick=sp.act_onclick(metric, oc.INCLUDE, [fid])))
    if d.get("rule") == "REF-15" and d.get("decisionRef"):
        out.append(ui.tags.button(fa("rotate-left"), " Undo", type="button",
                                  class_="btn btn-sm btn-outline-secondary",
                                  onclick=sp.undo_onclick(d["decisionRef"])))
    # a curve held for review (CURVE-07): the reviewer's answer, with a rationale
    if d.get("rule") == "CURVE-07" and row["status"] == C.NOT_EVALUATED and metric:
        out.append(ui.tags.button(fa("circle-check"), " Accept", type="button",
                                  class_="btn btn-sm btn-outline-success",
                                  title="Accept the curve as proposed (rationale required)",
                                  onclick=_onclick(channel, {"action": "review", "key": key, "fid": fid,
                                                             "decision": "accept"})))
        out.append(ui.tags.button(fa("xmark"), " Remove", type="button",
                                  class_="btn btn-sm btn-outline-danger",
                                  title="Take the curve out of the published scope (rationale required)",
                                  onclick=_onclick(channel, {"action": "review", "key": key, "fid": fid,
                                                             "decision": "remove"})))
    # a curve from another source the version scores (carried, national, modeled, published
    # criterion, fixed criterion): the owner can remove it or take it out of this function
    # (REF-15); the source panel's one form records the decision
    kind = ident.get("sourceKind")
    if (row["status"] == C.SELECTED and kind not in (None, "fitted") and not considered
            and d.get("rule") != "REF-15" and metric):
        out.append(ui.tags.button(fa("trash-can"), " Remove", type="button",
                                  class_="btn btn-sm btn-outline-danger",
                                  title="Remove this curve from the assessment (recorded as your decision)",
                                  onclick=sp.act_onclick(metric, oc.REMOVE)))
        out.append(ui.tags.button(fa("minus"), " Take out of this function", type="button",
                                  class_="btn btn-sm btn-outline-secondary",
                                  title="Leave this curve out of this function only (recorded as your decision)",
                                  onclick=sp.act_onclick(metric, oc.UNMAP, [fid])))
    if considered and row["status"] in (C.NOT_EVALUATED, C.ELIGIBLE):
        if row["status"] == C.NOT_EVALUATED:
            out.append(ui.tags.button(fa("pen"), " Record why not", type="button",
                                      class_="btn btn-sm btn-outline-secondary",
                                      onclick=_onclick(channel, {"action": "why_not", "key": key, "fid": fid})))
        elif d.get("decisionRef"):
            out.append(ui.tags.button(fa("rotate-left"), " Undo", type="button",
                                      class_="btn btn-sm btn-outline-secondary",
                                      onclick=_onclick(channel, {"action": "withdraw",
                                                                 "decision": d["decisionRef"]})))
        eligible = (cand.get("eligibility") or {}).get("status", "eligible") == "eligible"
        if eligible and ((cand.get("definition") or {}).get("openEnds") or cand.get("completion")):
            out.append(ui.tags.button(fa("pen-ruler"), " Complete the curve" if cand.get("needsCompletion")
                                      else " Change the completion", type="button",
                                      class_="btn btn-sm " + ("btn-outline-primary" if cand.get("needsCompletion")
                                                              else "btn-outline-secondary"),
                                      onclick=_onclick(channel, {"action": "complete", "key": key, "fid": fid})))
        if eligible:
            attrs = ({"disabled": "disabled", "title": oc.EXTENSION_OFF} if not extension_on else
                     {"disabled": "disabled", "title": COMPLETE_FIRST} if cand.get("needsCompletion") else {})
            out.append(ui.tags.button(fa("circle-check"), " Select", type="button",
                                      class_="btn btn-sm btn-outline-primary",
                                      onclick=_onclick(channel, {"action": "select", "key": key, "fid": fid}),
                                      **attrs))
    if considered and row["status"] != C.SELECTED:
        out.append(ui.tags.button(fa("xmark"), " Remove", type="button",
                                  class_="btn btn-sm btn-link text-muted",
                                  onclick=_onclick(channel, {"action": "drop", "key": key})))
    return ui.div(*out, class_="fs-actions")


def has_curve(cand: Mapping) -> bool:
    tile = candidate_tile(cand)
    return any(len(s.get("points") or []) >= 2 for s in tile.get("strata") or [])


def kind_label(cand: Mapping) -> str:
    kind = (cand.get("identity") or {}).get("sourceKind")
    if cand.get("buildStatus") in ("not_run", "failed") and kind == "fitted":
        return "No curve"
    return C.SOURCE_KIND_LABELS.get(kind, kind or "")


def reason_ui(text: str, *, limit: int = 240):
    """The reason, folded after ``limit`` characters (a withheld metric's statement
    walks every source the build tried)."""
    text = str(text or "")
    if len(text) <= limit:
        return ui.div(text, class_="fs-reason")
    cut = text.rfind(" ", 0, limit)
    head = text[:cut if cut > 0 else limit]
    return ui.tags.details(ui.tags.summary(head + " ...", ui.tags.span(" More", class_="fs-more")),
                           ui.div(text), class_="fs-reason fs-reason-long")


def linked_row_id(candidate_key: Any) -> str:
    """The element id of a candidate's row, the target of the ``candidate=<key>`` deep link."""
    return dom_id("cand_" + str(candidate_key or ""))


def alternative_row_ui(row: Mapping, cand: Mapping, *, ns, compare: list, extension_on: bool,
                       linked: bool = False):
    """One candidate's row. ``linked``: the row a deep link opened, marked so a reader
    finds it."""
    d = row.get("decision") or {}
    ident = cand.get("identity") or {}
    return ui.tags.tr(
        ui.tags.td(ui.div(cand.get("label") or (ident.get("subject") or {}).get("id"), class_="fs-name"),
                   ui.div(kind_label(cand), class_="fs-kind")),
        ui.tags.td(status_pill(row["status"], review=bool(row.get("needsReview")))),
        ui.tags.td(reason_ui(d.get("reason") or ""),
                   ui.div(rule_words(d.get("rule")), class_="fs-rule")),
        ui.tags.td(_who(d), class_="fs-who"),
        ui.tags.td(_actions(row, cand, ns=ns, compare=compare, extension_on=extension_on)),
        id=ns(linked_row_id(row["candidateKey"])),
        class_="is-linked" if linked else None,
    )


def _chip(cand: Mapping):
    ident = cand.get("identity") or {}
    kind = ident.get("sourceKind")
    return ui.tags.span(ui.tags.span(cand.get("label") or (ident.get("subject") or {}).get("id"),
                                     class_="fs-chip-name"),
                        ui.tags.span(kind_label(cand), class_="fs-chip-kind"),
                        class_="fs-chip")


def select01_status(fn: Mapping, *, limit: int, approvals: Iterable[Mapping] = (),
                    origin_approvals: Iterable[Mapping] = ()) -> Optional[dict]:
    """What SELECT-01 says about one function: None under the portfolio maximum, else
    ``{n, limit, approved, by, at_build, note}``. An approval given here (the session's
    ``portfolio_approvals``) or recorded at the build (the origin's) counts; a blank name
    approves nothing."""
    n = len(fn.get("selected") or [])
    if n <= int(limit):
        return None
    fid = str(fn["functionId"])
    mine = next((a for a in approvals or []
                 if str(a.get("functionId")) == fid and str(a.get("approver") or "").strip()), None)
    if mine:
        return {"n": n, "limit": int(limit), "approved": True, "by": str(mine["approver"]),
                "at_build": False, "note": str(mine.get("note") or ""), "date": str(mine.get("date") or "")}
    built = next((a for a in origin_approvals or []
                  if str(a.get("functionId")) == fid and str(a.get("approvedBy") or "").strip()), None)
    if built:
        return {"n": n, "limit": int(limit), "approved": True, "by": str(built["approvedBy"]),
                "at_build": True, "note": str(built.get("note") or ""), "date": ""}
    return {"n": n, "limit": int(limit), "approved": False, "by": "", "at_build": False,
            "note": "", "date": ""}


def function_row_ui(fn: Mapping, cands: Mapping, *, ns, compare: list, extension_on: bool,
                    open_: bool = False, sqt_ready: bool = False, select01: Optional[Mapping] = None,
                    can_source: bool = False, gap_withdrawable: bool = False,
                    linked_key: Optional[str] = None):
    """One function: the curves that score it on the summary line; the alternatives,
    the gap, what waits on a decision, and the decisions a person can take on it
    (a source, a documented gap, a SELECT-01 approval) inside.

    ``select01``: :func:`select01_status` for the function (None under the maximum);
    ``can_source``: a source can be chosen for it (an ecoregion's pressure-screen build);
    ``gap_withdrawable``: its documented gap is the session's own, not one a curve
    decision carries; ``linked_key``: the candidate a deep link opened, marked on its row."""
    selected = [cands[r["candidateKey"]] for r in fn["selected"] if r["candidateKey"] in cands]
    alts = [r for r in fn["alternatives"] if r["candidateKey"] in cands]
    channel = ns("final_action")
    fid = str(fn["functionId"])
    flags = []
    if fn["unassessed"]:
        gap = fn.get("gap") or {}
        flags.append(ui.tags.span("Documented gap" if gap else "No curve", class_="fs-flag is-gap",
                                  title=str(gap.get("justification") or "")))
    if fn["unresolved"]:
        flags.append(ui.tags.span(f"{fn['unresolved']} to resolve", class_="fs-flag is-open"))
    if select01 and not select01["approved"]:
        flags.append(ui.tags.span(f"{select01['n']} metrics: approve as a complementary set",
                                  class_="fs-flag is-open",
                                  title=f"More than {select01['limit']} metrics score this function; "
                                        "rule SELECT-01 publishes it only with a recorded approval."))
    elif select01:
        flags.append(ui.tags.span(f"{select01['n']} metrics, approved by {select01['by']}",
                                  class_="fs-flag",
                                  title=("Approved at the build." if select01["at_build"]
                                         else "Approved here.") + (" " + select01["note"]
                                                                   if select01["note"] else "")))
    summary = ui.tags.summary(
        ui.div(ui.tags.span(fa("chevron-right"), class_="fs-chev"),
               ui.div(ui.tags.span(fn["functionName"], class_="fs-fn-name"),
                      ui.tags.span(fn["discipline"], class_="fs-fn-disc"), class_="fs-fn-title"),
               class_="fs-fn-head"),
        ui.div(*([_chip(c) for c in selected] or [ui.tags.span("Nothing selected", class_="fs-none")]),
               class_="fs-chips"),
        ui.div(ui.tags.span(f"{len(alts)} considered" if alts else "No alternatives", class_="fs-count"),
               *flags, class_="fs-fn-meta"),
        class_="fs-fn-summary")
    body = []
    if fn["unassessed"] and fn.get("gap"):
        gap = fn["gap"]
        body.append(ui.div(ui.tags.strong("Why it is unassessed: "),
                           str(gap.get("justification") or gap.get("reason") or ""),
                           (ui.tags.span(f" Recorded by {gap.get('recordedBy')}.", class_="fs-who")
                            if gap.get("recordedBy") else None),
                           class_="fs-gap"))
    if select01 and not select01["approved"]:
        body.append(ui.div(
            fa("circle-info"), f" {select01['n']} metrics score this function, more than the "
            f"default maximum of {select01['limit']}. Rule SELECT-01 publishes such a set only "
            "with a recorded human approval that they are complementary, under a name.",
            class_="fs-note"))
    for w in fn.get("waiting") or []:
        body.append(ui.div(fa("hourglass-half"), f" Waiting for a build: {w.get('metric')} "
                           f"({(w.get('source') or {}).get('title') or 'a refused source'}).",
                           class_="fs-waiting"))
    for s in fn.get("stale") or []:
        # a decision the version cannot apply can still be withdrawn here (REF-15's own undo),
        # which also frees a considered curve it selected for removal
        undo = (ui.tags.button(fa("rotate-left"), " Undo", type="button",
                               class_="btn btn-sm btn-link", onclick=sp.undo_onclick(s["decision"]["id"]))
                if s["decision"].get("id") else None)
        body.append(ui.div(fa("triangle-exclamation"), f" {s['decision'].get('metric')}: {s['why']}", undo,
                           class_="fs-waiting is-stale"))
    rows = [alternative_row_ui(r, cands[r["candidateKey"]], ns=ns, compare=compare,
                               extension_on=extension_on,
                               linked=bool(linked_key) and r["candidateKey"] == linked_key)
            for r in fn["selected"] + alts if r["candidateKey"] in cands]
    body.append(ui.tags.table(
        ui.tags.thead(ui.tags.tr(ui.tags.th("Curve"), ui.tags.th("Status"), ui.tags.th("Why"),
                                 ui.tags.th("Decided by"), ui.tags.th(""))),
        ui.tags.tbody(*rows), class_="table table-sm fs-table"))
    tools = [ui.tags.button(fa("magnifying-glass"), " Add a state SQT curve", type="button",
                            class_="btn btn-sm btn-outline-secondary",
                            onclick=_onclick(channel, {"action": "add_sqt", "fid": fid}),
                            **({} if sqt_ready else {"disabled": "disabled",
                                                      "title": "The state SQT registry is not available."}))]
    if can_source:
        # REF-15: a source for a metric of this function (the verified catalog, an earlier
        # version, another assessment, or a curve entered here), through the one dialog
        tools.append(ui.tags.button(fa("circle-plus"), " Choose a source", type="button",
                                    class_="btn btn-sm btn-outline-primary",
                                    title="Choose where a curve for this function comes from "
                                          "(recorded as your decision)",
                                    onclick=sd.open_onclick(function=fid, stop=True)))
    if fn["unassessed"] and not fn.get("gap"):
        tools.append(ui.tags.button(fa("pen"), " Document a gap", type="button",
                                    class_="btn btn-sm btn-outline-secondary",
                                    title="Record why this function is left unassessed (COV-01)",
                                    onclick=_onclick(channel, {"action": "gap", "fid": fid})))
    elif fn["unassessed"] and gap_withdrawable:
        tools.append(ui.tags.button(fa("rotate-left"), " Withdraw the gap", type="button",
                                    class_="btn btn-sm btn-link text-muted",
                                    onclick=_onclick(channel, {"action": "withdraw_gap", "fid": fid})))
    if select01 and not select01["approved"]:
        tools.append(ui.tags.button(fa("circle-check"), " Approve as a complementary set", type="button",
                                    class_="btn btn-sm btn-outline-primary",
                                    onclick=_onclick(channel, {"action": "approve", "fid": fid})))
    elif select01 and not select01["at_build"]:
        tools.append(ui.tags.button(fa("rotate-left"), " Withdraw the approval", type="button",
                                    class_="btn btn-sm btn-link text-muted",
                                    onclick=_onclick(channel, {"action": "unapprove", "fid": fid})))
    body.append(ui.div(*tools, class_="fs-fn-tools"))
    attrs = {"open": ""} if open_ else {}
    toggle = (f"Shiny.setInputValue('{ns('fs_open')}',"
              f"{{fid:'{fid}',open:this.open}},{{priority:'event'}})")
    return ui.tags.details(summary, ui.div(*body, class_="fs-fn-body"),
                           class_="fs-fn" + (" is-gap" if fn["unassessed"] else ""),
                           id=ns(dom_id(fid)), ontoggle=toggle, **attrs)


def compare_ui(cands: list[Mapping], *, ns):
    """Up to three candidates side by side: the curve, and what it is."""
    if not cands:
        return None
    cols = []
    for c in cands[:C.MAX_COMPARE]:
        tile = candidate_tile(c)
        facts = [("Source", kind_label(c)), ("Reference n", tile.get("reference_n"))]
        for s in tile.get("strata") or []:
            at = crossings(s.get("points") or [])
            if at:
                facts.append(((f"{s.get('label')}: " if s.get("label") else "") + "Crosses 0.39 / 0.69",
                              " / ".join(cs.fmt_num(x) if x is not None else "none" for x in at)))
        checks = (c.get("eligibility") or {}).get("checks") or []
        said = []
        for chk in checks:
            if chk.get("status") != "pass":
                said.append(str(chk.get("detail") or ""))
                facts.append((CHECK_WORDS.get(str(chk.get("id")), str(chk.get("id") or "").replace("-", " ").capitalize()),
                              f"{CHECK_STATUS_WORDS.get(chk.get('status'), chk.get('status'))}. {chk.get('detail') or ''}".strip()))
        passed = [CHECK_WORDS.get(str(chk.get("id")), str(chk.get("id"))) for chk in checks if chk.get("status") == "pass"]
        if passed:
            facts.append(("Checks passed", ", ".join(passed)))
        for lim in c.get("limitations") or []:
            if not any(lim in d or d in lim for d in said if d):
                facts.append(("Limitation", lim))
        cols.append(ui.div(
            ui.div(c.get("label") or "", class_="fs-cmp-title"),
            ui.HTML(cs.tile_svg(tile, w=TILE_W, h=TILE_H)),
            ui.tags.dl(*[x for k, v in facts if v not in (None, "")
                         for x in (ui.tags.dt(k), ui.tags.dd(str(v)))], class_="fs-cmp-facts"),
            ui.tags.button(fa("xmark"), " Stop comparing", type="button", class_="btn btn-sm btn-link",
                           onclick=_onclick(ns("final_action"), {"action": "compare",
                                                                 "key": c["candidateKey"]})),
            class_="fs-cmp-col"))
    return ui.div(ui.div(ui.tags.strong("Compare"),
                         ui.tags.span(f"{len(cols)} of {C.MAX_COMPARE}", class_="fs-count"),
                         class_="fs-cmp-head"),
                  ui.div(*cols, class_="fs-cmp-cols"), class_="fs-compare")


def head_ui(register: Mapping, *, extension_on: bool = False, export_id: Optional[str] = None):
    counts = C.register_counts(register)
    fns = register.get("functions") or []
    head = ui.div(
        ui.div(ui.tags.h3("Select final curves", class_="fs-title"),
               ui.div(f"{counts['functionsSelected']} of {len(fns)} functions have a selected curve"
                      + (f", {counts['functionsUnassessed']} unassessed" if counts["functionsUnassessed"] else "")
                      + (f". {counts['unresolved']} item{'' if counts['unresolved'] == 1 else 's'} to resolve."
                         if counts["unresolved"] else "."), class_="fs-sub"),
               class_="fs-head-text"),
        ui.download_button(export_id, "Export register", class_="btn btn-sm btn-outline-secondary")
        if export_id else None,
        class_="fs-head")
    note = None if extension_on else ui.div(
        fa("circle-info"), " ", oc.EXTENSION_OFF,
        " A state SQT curve can still be added and compared, and a reason recorded.",
        class_="fs-note")
    return ui.TagList(head, note)


def functions_ui(register: Mapping, *, ns, compare: Iterable[str] = (), extension_on: bool = False,
                 open_ids: Iterable[str] = (), sqt_ready: bool = False, limit: Optional[int] = None,
                 approvals: Iterable[Mapping] = (), origin_approvals: Iterable[Mapping] = (),
                 can_source: bool = False, session_gap_ids: Iterable[str] = (),
                 linked_key: Optional[str] = None):
    """The function rows. ``limit``: the portfolio maximum (None: SELECT-01 is not judged);
    ``approvals`` / ``origin_approvals``: the session's and the build's SELECT-01 approvals;
    ``can_source``: a source can be chosen (an ecoregion's pressure-screen build);
    ``session_gap_ids``: the functions whose documented gap is the session's own;
    ``linked_key``: the candidate a ``candidate=<key>`` deep link opened (its functions are
    opened as well, and its row is marked)."""
    cands = {c["candidateKey"]: c for c in register.get("candidates") or []}
    compare = [k for k in compare or [] if k in cands]
    opened = set(open_ids or ()) | set(candidate_functions(register, linked_key) if linked_key else ())
    own_gaps = {str(x) for x in session_gap_ids or ()}
    rows = []
    for f in register.get("functions") or []:
        s01 = (select01_status(f, limit=limit, approvals=approvals, origin_approvals=origin_approvals)
               if limit is not None else None)
        rows.append(function_row_ui(f, cands, ns=ns, compare=compare, extension_on=extension_on,
                                    open_=f["functionId"] in opened, sqt_ready=sqt_ready,
                                    select01=s01, can_source=can_source,
                                    gap_withdrawable=str(f["functionId"]) in own_gaps,
                                    linked_key=linked_key))
    return ui.div(*rows, class_="fs-functions")


# --------------------------------------------------------------------------- #
# the deep link: candidate=<key> opens the candidate's row
# --------------------------------------------------------------------------- #
def parse_candidate_link(search: Any) -> Optional[str]:
    """The candidate key a page URL names (``?candidate=<key>``, what
    ``run_region_batch.py open --candidate KEY`` prints), or None."""
    text = str(search or "").strip()
    if not text:
        return None
    if text.startswith("?"):
        text = text[1:]
    if text.startswith("#"):
        text = text[1:]
    values = parse_qs(text, keep_blank_values=False).get("candidate") or []
    key = str(values[0]).strip() if values else ""
    return key or None


def candidate_functions(register: Mapping, candidate_key: Any) -> list[str]:
    """The functions whose rows list a candidate, in the register's order."""
    key = str(candidate_key or "")
    if not key:
        return []
    out: list[str] = []
    for fn in (register or {}).get("functions") or []:
        rows = list(fn.get("selected") or []) + list(fn.get("alternatives") or [])
        if any(str(r.get("candidateKey")) == key for r in rows):
            out.append(str(fn["functionId"]))
    return out


# --------------------------------------------------------------------------- #
# Compare: this version (A, the session's origin) against another (B)
# --------------------------------------------------------------------------- #
VERDICT_CLASS = {cmpv.AGREE: "is-selected", cmpv.DIFFER: "is-excluded", cmpv.NOT_APPLICABLE: "is-eligible"}


def origin_version_dir(origin: Optional[Mapping]) -> Optional[Path]:
    """The folder of the version the session was opened from (``state.assessment_source``):
    a library version, a staged version, or a run folder; None for a project built from
    scratch."""
    from streamcurves import library as lib
    o = origin or {}
    kind = o.get("kind")
    try:
        if kind == "library" and o.get("library_id") and o.get("version"):
            return lib.version_dir(str(o["library_id"]), int(o["version"]))
        if kind == "staged" and o.get("staged_path"):
            return Path(str(o["staged_path"]))
        if kind == "run" and o.get("run_dir"):
            return Path(str(o["run_dir"]))
    except (TypeError, ValueError):
        return None
    return None


def origin_label(origin: Optional[Mapping]) -> str:
    o = origin or {}
    kind = o.get("kind")
    if kind == "library" and o.get("library_id"):
        return f"{o['library_id']} v{o.get('version')}"
    if kind == "staged":
        return f"staged {cmpv.version_label(o.get('staged_path') or '')}"
    if kind == "run":
        return f"run {Path(str(o.get('run_dir') or '')).name}"
    return "this session's origin"


def compare_choices(entries: Iterable[Any]) -> dict[str, str]:
    """The gallery's list as ``{"<id>@v<n>": "<name> v<n> (<status>)"}``, only the versions
    whose files are on this computer (a download-only entry cannot be compared)."""
    out: dict[str, str] = {}
    for e in entries or []:
        for v in getattr(e, "versions", ()) or ():
            if getattr(v, "download_only", False):
                continue
            out[f"{e.id}@v{v.version}"] = f"{e.name} v{v.version} ({v.status_label})"
    return out


def compare_target(choice: Any, path_text: Any) -> Optional[Path]:
    """The folder B names: a staged run folder path when one is typed, else the chosen
    library version."""
    from streamcurves import library as lib
    text = str(path_text or "").strip().strip('"')
    if text:
        return Path(text)
    key = str(choice or "").strip()
    if "@v" in key:
        aid, ver = key.rsplit("@v", 1)
        if aid and ver.isdigit():
            return lib.version_dir(aid, int(ver))
    return None


def compare_chooser_ui(choices: Mapping, *, ns, a_label: str, selected: Optional[str] = None,
                       path_value: str = "", can_compare: bool = True):
    """Version A (the session's origin) against B: a library version from the gallery's
    list, or a staged run folder's path."""
    opts = {"": "Choose a version", **dict(choices)}
    return ui.div(
        ui.div(ui.tags.strong("A: "), a_label if can_compare else "no version to compare",
               class_="mb-1"),
        (ui.div(fa("circle-info"), " This session was not opened from a library version or a "
                "staged run, so there is no version A. Open one from the Assessment library "
                "or from the Region builder.", class_="fs-note") if not can_compare else None),
        ui.div(ui.input_select(ns("fs_cmp_b"), "B: a library version", opts,
                               selected=selected if selected in opts else ""),
               ui.input_text(ns("fs_cmp_path"), "or a staged run folder", value=path_value,
                             placeholder="For example: notes/DEEP_Working/analysis/runs/l3-71"),
               ui.input_action_button(ns("fs_cmp_run"), "Compare", class_="btn btn-sm btn-primary",
                                      disabled=not can_compare),
               class_="fs-cmp-chooser"),
        class_="fs-cmp-setup")


def _pill(text: str, cls: str):
    return ui.tags.span(text, class_=f"fs-status {cls}")


def _kv_table(rows: Iterable[tuple], head: Iterable[str]):
    return ui.tags.table(
        ui.tags.thead(ui.tags.tr(*[ui.tags.th(h) for h in head])),
        ui.tags.tbody(*[ui.tags.tr(*[ui.tags.td(c) for c in r]) for r in rows]),
        class_="table table-sm fs-table")


def _anchor_words(rows: Iterable[Mapping]) -> str:
    parts = []
    for a in rows or []:
        if a.get("deltaIqr") is not None:
            parts.append(f"band {a['band']:g}: {a['a']:.4g} to {a['b']:.4g} ({a['deltaIqr']:+.2f} IQR)")
        elif a.get("a") is not None and a.get("b") is not None:
            parts.append(f"band {a['band']:g}: {a['a']:.4g} to {a['b']:.4g}")
        else:
            parts.append(f"band {a['band']:g}: not reached in "
                         + ("A" if a.get("a") is None else "B"))
    return "; ".join(parts)


def compare_report_ui(rep: Mapping, *, ns, a_label: str, b_label: str, export_id: Optional[str] = None):
    """The comparison as a reader sees it: curves (largest delta), the register diff, the
    ledger diff, the reviewer decisions diff, the D3 owner-decision list and the digests."""
    c = rep.get("curves") or {}
    d = rep.get("decisions") or {}
    reg = rep.get("registers") or {}
    led = rep.get("ledgers") or {}
    od = rep.get("ownerDecisions") or {}
    dg = rep.get("digests") or {}
    man = rep.get("manifest") or {}
    head = ui.div(
        ui.div(ui.tags.strong("A: "), a_label, ui.tags.span(" against ", class_="text-muted"),
               ui.tags.strong("B: "), b_label, class_="fs-cmp-versions"),
        (ui.download_button(export_id, "Export CSV", class_="btn btn-sm btn-outline-secondary")
         if export_id else None),
        class_="fs-head")
    facts = []
    for k in ("methodology", "inputsDigest"):
        v = man.get(k) or {}
        facts.append((k, "equal" if v.get("equal") else "differs"))
    sd = (man.get("standingDecisions") or {})
    facts.append(("standing decisions",
                  f"A {(sd.get('a') or {}).get('policyVersion')} ({(sd.get('a') or {}).get('appliedCount')} applied), "
                  f"B {(sd.get('b') or {}).get('policyVersion')} ({(sd.get('b') or {}).get('appliedCount')} applied)"))
    manifest_block = ui.div(ui.tags.strong("Manifest"), _kv_table(facts, ("what", "A and B")),
                            class_="fs-cmp-block")
    curve_rows = []
    for mid, notes in (c.get("differ") or {}).items():
        delta = (c.get("max_delta") or {}).get(mid)
        curve_rows.append((mid, "" if delta is None else f"{delta:.4g}", "; ".join(notes)))
    for mid in c.get("only_a") or []:
        curve_rows.append((mid, "", "only in A"))
    for mid in c.get("only_b") or []:
        curve_rows.append((mid, "", "only in B"))
    curves_block = ui.div(
        ui.tags.strong("Curves"),
        ui.div(f"{len(c.get('identical') or [])} identical, {len(c.get('differ') or {})} differ, "
               f"{len(c.get('only_a') or [])} only in A, {len(c.get('only_b') or [])} only in B",
               class_="fs-sub"),
        _kv_table(curve_rows, ("metric", "largest delta", "what differs")) if curve_rows else None,
        class_="fs-cmp-block")
    if reg.get("note"):
        reg_body = ui.div(reg["note"], class_="fs-note")
    else:
        rows = []
        for ch in reg.get("changes") or []:
            if ch.get("change") == "changed":
                x, y = ch.get("before") or {}, ch.get("after") or {}
                rows.append((f"{x.get('function') or x.get('functionId')}: {x.get('candidate') or x.get('candidateKey')}",
                             "changed", "; ".join(f"{f}: {x.get(f)!s} to {y.get(f)!s}" for f in ch.get("fields") or [])))
            else:
                rows.append((f"{ch.get('function') or ch.get('functionId')}: {ch.get('candidate') or ch.get('candidateKey')}",
                             ch.get("change"), str(ch.get("status") or "")))
        cn = reg.get("counts") or {}
        reg_body = ui.TagList(
            ui.div(f"{cn.get('added', 0)} added, {cn.get('removed', 0)} removed, {cn.get('changed', 0)} changed",
                   class_="fs-sub"),
            _kv_table(rows, ("candidate", "change", "detail")) if rows else None)
    register_block = ui.div(ui.tags.strong("Candidate register"), reg_body, class_="fs-cmp-block")
    if led.get("note"):
        led_body = ui.div(led["note"], class_="fs-note")
    else:
        rows = []
        for ch in led.get("changes") or []:
            x, y = ch.get("before") or {}, ch.get("after") or {}
            rows.append((f"{ch.get('metric')} / {ch.get('function') or ch.get('functionId')}", ch.get("change"),
                         "; ".join(f"{f}: {x.get(f)!s} to {y.get(f)!s}" for f in ch.get("fields") or [])
                         if ch.get("change") == "changed" else str((y or x).get("disposition") or "")))
        cn = led.get("counts") or {}
        led_body = ui.TagList(
            ui.div(f"{led.get('same', 0)} unchanged, {cn.get('added', 0)} added, {cn.get('removed', 0)} removed, "
                   f"{cn.get('changed', 0)} changed", class_="fs-sub"),
            _kv_table(rows, ("metric / function", "change", "detail")) if rows else None)
    ledger_block = ui.div(ui.tags.strong("Rebuild ledger"), led_body, class_="fs-cmp-block")
    dec_rows = []
    for label, diffs in (d.get("differ") or {}).items():
        dec_rows.append((label, "; ".join(f"{f}: {va!s} to {vb!s}" for f, (va, vb) in diffs.items())))
    for side in ("only_a", "only_b"):
        for label in d.get(side) or []:
            dec_rows.append((label, f"only in {side[-1].upper()}"))
    decisions_block = ui.div(
        ui.tags.strong("Reviewer decisions"),
        ui.div(f"{d.get('same', 0)} same, {len(d.get('differ') or {})} differ, "
               f"{len(d.get('only_a') or [])} only in A, {len(d.get('only_b') or [])} only in B", class_="fs-sub"),
        _kv_table(dec_rows, ("record", "difference")) if dec_rows else None,
        class_="fs-cmp-block")
    items = od.get("items") or []
    cn = od.get("counts") or {}
    owner_rows = []
    for it in items:
        rec = it.get("recorded") or {}
        who = ", ".join(x for x in (str(rec.get("by") or ""), str(rec.get("when") or "")) if x)
        owner_rows.append((f"{it.get('rule')}: {it.get('subject')}",
                           f"{rec.get('action') or ''}" + (f" ({who})" if who else ""),
                           _pill(cmpv.VERDICT_WORDS.get(it.get("verdict"), str(it.get("verdict"))),
                                 VERDICT_CLASS.get(it.get("verdict"), "")),
                           ui.TagList(str(it.get("detail") or ""),
                                      (ui.div(_anchor_words(it.get("anchors")), class_="fs-kind")
                                       if it.get("anchors") else None))))
    owner_block = ui.div(
        ui.tags.strong("Owner decisions in A, as B decided them"),
        ui.div((f"{cn.get(cmpv.AGREE, 0)} agree, {cn.get(cmpv.DIFFER, 0)} differ, "
                f"{cn.get(cmpv.NOT_APPLICABLE, 0)} not applicable") if items else
               "A records no owner decision (REF-15, CURVE-07, COV-01, SELECT-01).", class_="fs-sub"),
        _kv_table(owner_rows, ("decision", "recorded", "B", "detail")) if owner_rows else None,
        class_="fs-cmp-block")
    digest_rows = []
    for side in ("a", "b"):
        one = dg.get(side) or {}
        for key in ("inputsDigest", "contentDigest"):
            rec = one.get(key) or {}
            digest_rows.append((f"{side.upper()} {key}",
                                _pill("replays", "is-selected") if rec.get("equal")
                                else _pill(rec.get("problem") or "does not replay", "is-excluded"),
                                str(rec.get("recorded") or "")))
    digest_block = ui.div(ui.tags.strong("Digests"),
                          _kv_table(digest_rows, ("digest", "re-derived", "recorded")) if digest_rows else None,
                          class_="fs-cmp-block")
    return ui.div(head, manifest_block, curves_block, register_block, ledger_block, decisions_block,
                  owner_block, digest_block, class_="fs-compare-versions")


def compare_csv(rep: Mapping) -> str:
    return cmpv.report_csv(rep)


def final_selection_ui(register: Mapping, *, ns, compare: Iterable[str] = (), extension_on: bool = False,
                       open_ids: Iterable[str] = (), sqt_ready: bool = False, export_id: Optional[str] = None,
                       **row_context):
    """The whole section at once (the page renders its parts separately);
    ``row_context`` is :func:`functions_ui`'s extra context."""
    cands = {c["candidateKey"]: c for c in register.get("candidates") or []}
    compare = [k for k in compare or [] if k in cands]
    return ui.div(head_ui(register, extension_on=extension_on, export_id=export_id),
                  compare_ui([cands[k] for k in compare], ns=ns),
                  functions_ui(register, ns=ns, compare=compare, extension_on=extension_on,
                               open_ids=open_ids, sqt_ready=sqt_ready, **row_context), class_="fs-page")


# --------------------------------------------------------------------------- #
# the build items left for the owner, and the dialogs the decisions use
# --------------------------------------------------------------------------- #
#: queue items answered elsewhere on this page: a held curve's row (CURVE-07) and a
#: function row's approval of its set (SELECT-01), so each decision has one home
ROW_ANSWERED_RULES = ("CURVE-07", "SELECT-01")


def build_items(doc: Optional[Mapping], answered: Iterable[Mapping] = ()) -> list[dict]:
    """The open items of the build's review queue that the list answers, each with the
    answer already recorded for the region (``answer``: the saved decision or None).
    CURVE-07 items are left to the curve's own row, SELECT-01 items to the function
    row's approval."""
    given = {(d.get("rule_id"), str(d.get("subject"))): d for d in answered or [] if isinstance(d, Mapping)}
    out = []
    for item in rb.open_queue_items(doc if isinstance(doc, Mapping) else None):
        if item.get("rule_id") in ROW_ANSWERED_RULES:
            continue
        out.append({**item, "answer": given.get((item.get("rule_id"), str(item.get("subject"))))})
    return out


def build_items_ui(items: list[Mapping], *, ns, writable: bool):
    """"Build items left for you": one card per open item with its decision and rationale,
    and one Save. ``writable``: the region's run folder is at hand (a checkout), so the
    answers reach ``owner_decisions.json`` for the next build."""
    if not items:
        return None
    cards = []
    for i, item in enumerate(items):
        answer = item.get("answer") or {}
        choices = {"": "(unanswered)", **rb.action_choices(item.get("rule_id"))}
        cards.append(ui.div(
            ui.div(ui.tags.code(str(item.get("item_id") or "")),
                   (ui.tags.span(rule_chip(str(item["rule_id"])), class_="ms-2") if item.get("rule_id") else None),
                   (ui.tags.span("BLOCKING", class_="badge bg-danger ms-2") if item.get("blocking") else None),
                   (ui.tags.span(f"Answered: {answer.get('action')}", class_="fs-status is-selected ms-2")
                    if answer else None),
                   class_="mb-1"),
            ui.p(linkify_rule_ids(str(item.get("question") or "")), class_="mb-1"),
            ui.tags.details(ui.tags.summary("Evidence", class_="text-muted small"),
                            ui.tags.pre(json.dumps(item.get("evidence") or {}, indent=1, default=str),
                                        class_="rb-log")),
            ui.input_select(ns(f"fs_act_{i}"), "Decision", choices,
                            selected=str(answer.get("action") or "") if answer else ""),
            ui.input_text_area(ns(f"fs_why_{i}"), "Rationale (required)", rows=2, width="100%",
                               value=str(answer.get("rationale") or "") if answer else ""),
            class_="rb-item border rounded p-2 mb-2"))
    note = ("Saved into the region's own record, which the next build reads." if writable else
            "This copy has no run folder for the region, so answers cannot be saved here; "
            "answer them from a STAF checkout.")
    return ui.div(
        ui.tags.strong(f"Build items left for you ({len(items)})"),
        ui.div(note, class_="fs-note"),
        *cards,
        (ui.input_action_button(ns("fs_save_answers"), "Save answers", class_="btn btn-primary btn-sm")
         if writable else None),
        class_="fs-build-items")


def review_modal(label: str, decision: str, *, ns):
    """Accept or remove a curve held for review (CURVE-07), with the rationale the record keeps."""
    accepting = decision == "accept"
    verb = "Accept" if accepting else "Remove"
    return ui.modal(
        ui.tags.p(f"{verb} {label}" + ("." if accepting else " from the published scope.")),
        ui.tags.p("The answer is recorded on this version's curve review and, for the region, "
                  "in its answers file, so the next build applies it too.", class_="fs-note"),
        ui.input_text_area(ns("fs_review_note"), "Rationale (required)", rows=3, width="100%"),
        title=f"{verb} curve", easy_close=True,
        footer=ui.TagList(ui.modal_button("Cancel"),
                          ui.input_action_button(ns("fs_review_confirm"), verb,
                                                 class_="btn btn-success" if accepting else "btn btn-danger")))


def gap_modal(function_name: str, *, ns, by: str, reasons: Iterable[str]):
    """Document why a function is left unassessed (COV-01)."""
    return ui.modal(
        ui.tags.p(f"Record why {function_name} carries no curve in this assessment. A reader can "
                  "then tell a deliberate scope decision from an oversight; the record travels "
                  "with the published version and with the region.", class_="fs-note"),
        ui.input_select(ns("fs_gap_reason"), "Reason", {r: r.replace("-", " ") for r in reasons}),
        ui.input_text_area(ns("fs_gap_why"), "Justification (required, at least 20 characters)",
                           rows=3, width="100%",
                           placeholder="Why this assessment carries no metric for it."),
        ui.input_text(ns("fs_gap_by"), "Your initials", value=by),
        title="Document a gap", easy_close=True,
        footer=ui.TagList(ui.modal_button("Cancel"),
                          ui.input_action_button(ns("fs_gap_confirm"), "Record", class_="btn btn-primary")))


def approval_modal(function_name: str, n: int, limit: int, *, ns, by: str):
    """Approve a function's metrics as a complementary set (SELECT-01)."""
    return ui.modal(
        ui.tags.p(f"{function_name} is scored by {n} metrics, more than the default maximum of "
                  f"{limit}. Rule SELECT-01 publishes such a set only with a recorded human "
                  "approval that the metrics are complementary. Your approval is recorded under "
                  "your initials and written into the version's metadata at publish.",
                  class_="fs-note"),
        ui.input_text(ns("fs_approve_by"), "Your initials", value=by),
        ui.input_text_area(ns("fs_approve_note"), "Why they are complementary", rows=3, width="100%"),
        title="Approve as a complementary set", easy_close=True,
        footer=ui.TagList(ui.modal_button("Cancel"),
                          ui.input_action_button(ns("fs_approve_confirm"), "Approve", class_="btn btn-primary")))


def register_export(state) -> Optional[dict]:
    """The register of the open session as a published version's provenance carries it:
    one record per candidate and function, and the counts. None for a legacy session, or
    when the register cannot be read (a publish never fails on it)."""
    try:
        reg = session_register(state)
        if reg is None:
            return None
        return {"schema": 1, "rows": C.export_rows(reg), "counts": C.register_counts(reg)}
    except Exception:  # noqa: BLE001 - the record is additive; the publish goes on without it
        import logging
        logging.getLogger("streamcurves").exception("the candidate register could not be exported")
        return None


def portfolio_limit() -> int:
    """The portfolio maximum SELECT-01 judges against (methodology config)."""
    from streamcurves import methodology
    return int(methodology.threshold("metric_portfolio.default_maximum_metrics_per_function"))


def session_register(state) -> Optional[dict]:
    """The open session's register (``candidates.deep_register``), or None for a
    legacy session (no reference build) or when it cannot be read."""
    try:
        from views import curve_gallery as cg
        with reactive.isolate():
            if state.reference_build() is None:
                return None
            region = state.region_of_applicability() or {}
            return C.deep_register(tiles=cg.gallery_rows(state, include_reference=True),
                                   build=state.reference_build(),
                                   decisions=state.owner_curve_decisions() or [],
                                   metric_config=state.metric_config() or {},
                                   register=state.candidate_register(),
                                   coverage_exceptions=state.function_coverage_exceptions() or [],
                                   region={"code": region.get("code")})
    except Exception:  # noqa: BLE001 - the publish page only asks; a refusal is not a crash
        import logging
        logging.getLogger("streamcurves").exception("the candidate register could not be read")
        return None


def unresolved_count(state, register: Optional[Mapping] = None) -> Optional[int]:
    """How many items the register leaves to resolve (``candidates.function_rows``
    counts them per function), or None for a legacy session: what the publish
    page's status default reads."""
    reg = register if register is not None else session_register(state)
    if reg is None:
        return None
    return int(C.register_counts(reg)["unresolved"])


def unapproved_functions(state, register: Optional[Mapping] = None, *,
                         limit: Optional[int] = None) -> list[dict]:
    """The functions the register scores with more metrics than the portfolio maximum
    that neither the session's approvals nor the build's cover:
    ``[{functionId, functionName, nMetrics}]`` (SELECT-01)."""
    reg = register if register is not None else session_register(state)
    if reg is None:
        return []
    lim = portfolio_limit() if limit is None else int(limit)
    with reactive.isolate():
        origin = state.assessment_source() or {}
    approvals = st.portfolio_approvals(state)
    out = []
    for fn in reg.get("functions") or []:
        s01 = select01_status(fn, limit=lim, approvals=approvals,
                              origin_approvals=origin.get("portfolio_approvals") or [])
        if s01 and not s01["approved"]:
            out.append({"functionId": str(fn["functionId"]), "functionName": fn.get("functionName"),
                        "nMetrics": s01["n"]})
    return out


def export_csv(register: Mapping) -> str:
    rows = C.export_rows(register)
    buf = io.StringIO()
    cols = ["functionId", "function", "candidate", "subject", "sourceKind", "status", "rule",
            "decidedBy", "who", "when", "reason", "basisDigest", "needsReview", "candidateKey"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# the state SQT picker and the REF-15 selection of a considered SQT curve
# --------------------------------------------------------------------------- #
VERIFICATION_WORDS = {"verified": "Verified", "partially-verified": "Partly verified",
                      "unverified": "Not verified", "defective": "Defective"}
CHECK_CLASS = {"pass": "is-selected", "warn": "is-pending", "fail": "is-excluded", "unknown": "is-eligible"}
CHECK_WORDS = {"eligibility": "Registry", "construct": "Metric", "protocol": "Protocol", "units": "Units",
               "direction": "Direction", "score-scale": "Index bands", "geography": "Where",
               "stream-type": "Stream type", "source-limits": "Source limits",
               "extrapolation": "Past the ends", "past-ends": "Past the ends", "form": "Form",
               "strata": "Strata"}
CHECK_STATUS_WORDS = {"fail": "Does not apply", "warn": "Note", "unknown": "Not established"}


@functools.lru_cache(maxsize=64)
def region_states(code: str) -> tuple:
    """Two-letter codes of the states an ecoregion's NRSA stations lie in."""
    try:
        from streamcurves import nrsa_dataset as nds
        s = nds.load_dataset(nds.MULTI_CYCLE_DATASET_ID).sites
        col = "pstl_code" if "pstl_code" in s.columns else "state"
        vals = s.loc[s["us_l3code"].astype(str) == str(code), col].dropna().astype(str)
        return tuple(sorted({v.strip().upper() for v in vals if len(v.strip()) == 2}))
    except Exception:  # noqa: BLE001 - no archive: the geography check reads as unknown
        return ()


def function_metrics(register: Mapping, function_id: str) -> list[str]:
    """The metric keys of the curves this session has for a function, selected or not, other
    than considered SQT curves: what an SQT curve for it is checked against."""
    cands = {c["candidateKey"]: c for c in register.get("candidates") or []}
    fn = next((x for x in register.get("functions") or [] if x["functionId"] == function_id), None) or {}
    out: list[str] = []
    for row in list(fn.get("selected") or []) + list(fn.get("alternatives") or []):
        ident = (cands.get(row["candidateKey"]) or {}).get("identity") or {}
        mk = (ident.get("subject") or {}).get("id")
        if ident.get("sourceKind") != "sqt" and mk and str(mk) not in out:
            out.append(str(mk))
    return out


def curve_targets(state, metrics: Iterable[str]) -> list[dict]:
    """``candidates.sqt_target`` for each metric: the session's configuration of it and the
    range of its values in the session's data."""
    import numpy as np
    import pandas as pd
    with reactive.isolate():
        mc = state.metric_config() or {}
        data = state.data()
    out = []
    for mk in metrics or []:
        cfg = mc.get(mk) or {}
        col = cfg.get("column_name") or mk
        rng = None
        if isinstance(data, pd.DataFrame) and col in data.columns:
            v = pd.to_numeric(data[col], errors="coerce").to_numpy(dtype=float)
            v = v[np.isfinite(v)]
            if v.size:
                rng = (float(v.min()), float(v.max()))
        out.append(C.sqt_target(mk, cfg, rng))
    return out


def recheck(cand: Mapping, function_id: str, *, states: Iterable = (), targets: Iterable[Mapping] = (),
            region: Optional[Mapping] = None) -> dict:
    """A considered SQT candidate checked again against ``targets`` (the curves it would take
    the place of, else the function's own): the same candidate, rebuilt from its frozen record
    with the checks that context gives and the author's completion applied."""
    rec = cand.get("record") or {}
    again = C.sqt_candidate(rec, function_id=function_id, region=region,
                            context=C.sqt_context(rec, function_id=function_id, states=states,
                                                  targets=targets))
    return C.with_completion(again, cand.get("completion"))


def _edition_words(r: Mapping) -> str:
    named = ((r.get("verification") or {}).get("against") or {}).get("edition")
    if named:
        return f"{r.get('state')} SQT {named}"
    if r.get("edition"):
        return f"{r.get('edition')} (edition inferred)"
    return f"{r.get('state')} SQT, edition not named"


def edition_choices(records: Iterable[Mapping]) -> dict:
    """The edition filter's options, in the words each row shows (``_edition_words``)."""
    return {w: w for w in sorted({_edition_words(r) for r in records or []})}


def picker_modal(function_id: str, function_name: str, *, ns, states: Iterable[str]):
    from streamcurves import sqt_registry
    states = list(states or [])
    facets = sqt_registry.facets()
    here = [s for s in states if s in facets["states"]]
    return ui.modal(
        ui.p(f"Published state SQT curves for {function_name}. Adding one puts it beside this "
             "function's curves for comparison; it selects nothing.", class_="fs-note"),
        ui.div(ui.input_text(ns("fs_q"), "Metric", placeholder="For example: canopy"),
               ui.input_select(ns("fs_state"), "State", {"": "Any state", **{s: s for s in facets["states"]}},
                               selected=here[0] if len(here) == 1 else ""),
               ui.input_select(ns("fs_edition"), "Edition", {"": "Any edition",
                                                             **edition_choices(sqt_registry.records())}),
               ui.input_select(ns("fs_ver"), "Verification", {"": "Any", **VERIFICATION_WORDS}),
               ui.input_checkbox(ns("fs_allfn"), "Every function", False),
               ui.input_checkbox(ns("fs_elig"), "Eligible only", True),
               class_="fs-picker-filters"),
        ui.output_ui(ns("fs_sqt_results")),
        title="Add a state SQT curve", size="xl", easy_close=True, footer=ui.modal_button("Close"))


def picker_results_ui(records: list, *, ns, function_id: str, context: Optional[Mapping] = None,
                      states: Iterable = (), targets: Iterable[Mapping] = (), have: Iterable[str] = (),
                      limit: int = 60):
    """The matching records, the ones that apply first. Each is checked against ``context``
    when given, else against this function's curves (``targets``, ``candidates.sqt_context``),
    with the checks adding it runs (``candidates.sqt_checks``), so a row never reads Applies
    for a curve that adding would exclude."""
    from streamcurves import sqt_registry as reg
    if not records:
        return ui.div(fa("circle-info"), " No SQT curve matches these filters.", class_="fs-note")
    rows = []
    scored = []
    targets = list(targets or [])
    for r in records:
        ctx = context if context is not None else C.sqt_context(r, function_id=function_id, states=states,
                                                                targets=targets)
        checks, _adoption = C.sqt_checks(r, ctx)
        scored.append((("fail", "warn", "unknown", "pass").index(reg.overall(checks)) * -1, r, checks))
    scored.sort(key=lambda x: (x[0], x[1].get("state"), x[1].get("originalMetricName") or ""))
    have = set(have or ())
    for _rank, r, checks in scored[:limit]:
        worst = reg.overall(checks)
        first = next((c for c in checks if c["status"] == worst and worst != "pass"), None)
        ver = (r.get("verification") or {}).get("status")
        added = r.get("key") in have
        rows.append(ui.tags.tr(
            ui.tags.td(ui.div(r.get("originalMetricName"), class_="fs-name"),
                       ui.div(r.get("stratumName") if r.get("stratumName") != "Default" else "", class_="fs-kind")),
            ui.tags.td(ui.div(_edition_words(r)),
                       ui.div((r.get("function") or {}).get("name") or "", class_="fs-kind")),
            ui.tags.td(ui.tags.span(VERIFICATION_WORDS.get(ver, ver or ""),
                                    class_="fs-status " + {"verified": "is-selected", "partially-verified": "is-pending",
                                                           "defective": "is-excluded"}.get(ver, "is-eligible"))),
            ui.tags.td(ui.tags.span({"pass": "Applies", "warn": "Applies, with notes", "fail": "Does not apply",
                                     "unknown": "Not established"}[worst], class_=f"fs-status {CHECK_CLASS[worst]}"),
                       ui.div(first["detail"] if first else "", class_="fs-rule")),
            ui.tags.td(ui.tags.button(fa("check") if added else fa("plus"), " Added" if added else " Add",
                                      type="button", class_="btn btn-sm " + ("btn-outline-secondary" if added
                                                                             else "btn-outline-primary"),
                                      onclick=_onclick(ns("final_action"), {"action": "add_record", "key": r.get("key"),
                                                                            "fid": function_id}),
                                      **({"disabled": "disabled"} if added else {})))))
    more = (ui.div(f"Showing {limit} of {len(records)}. Narrow the filters to see the rest.", class_="fs-note")
            if len(records) > limit else None)
    return ui.TagList(ui.tags.table(
        ui.tags.thead(ui.tags.tr(ui.tags.th("SQT metric"), ui.tags.th("Source"), ui.tags.th("Checked"),
                                 ui.tags.th("Here"), ui.tags.th(""))),
        ui.tags.tbody(*rows), class_="table table-sm fs-table"), more)


def replacement_checks_ui(results: list[tuple[Optional[Mapping], dict]]):
    """What checking a considered SQT curve again says: ``[(target, rechecked candidate)]``,
    one per curve it takes the place of, or ``[(None, rechecked)]`` against the function's own
    curves when it replaces none."""
    if not results:
        return None
    items = []
    for target, again in results:
        checks = (again.get("eligibility") or {}).get("checks") or []
        if target is None:
            bad = [c for c in checks if c.get("status") == "fail"]
            notes = [c for c in checks if c.get("status") in ("warn", "unknown")]
            if bad:
                items.append(ui.tags.li(ui.tags.strong("It does not apply here. "),
                                        " ".join(str(c.get("detail")) for c in bad), class_="is-fail"))
            else:
                items.append(ui.tags.li(ui.tags.strong("Checked against this function's curves. "),
                                        " ".join(str(c.get("detail")) for c in notes)
                                        or "Nothing here refuses it."))
            continue
        name = target.get("name") or target.get("metric")
        if not C.same_metric(again.get("record") or {}, target.get("metric")):
            items.append(ui.tags.li(f"{name} measures another metric. The SQT curve scores its own, "
                                    "measured as the SQT specifies, so units and values are not compared."))
            continue
        bad = [c for c in checks if c.get("status") == "fail"]
        notes = [c for c in checks if c.get("status") in ("warn", "unknown")
                 and c.get("id") in ("units", "direction", "extrapolation", "past-ends")]
        if bad:
            items.append(ui.tags.li(ui.tags.strong(f"{name}: does not apply. "),
                                    " ".join(str(c.get("detail")) for c in bad), class_="is-fail"))
        else:
            items.append(ui.tags.li(ui.tags.strong(f"{name}: the same metric. "),
                                    " ".join(str(c.get("detail")) for c in notes)
                                    or "Units, direction and the values scored here agree."))
    head = ("Checked again for this function" if all(t is None for t, _ in results)
            else "Checked against the curves it takes the place of")
    return ui.div(ui.tags.strong(head), ui.tags.ul(*items), class_="fs-recheck")


def select_modal(cand: Mapping, function_name: str, fitted: list[tuple[str, str]], *, ns):
    """Select a considered SQT curve for a function (REF-15): why, and which curves built
    here it takes the place of there."""
    d = cand.get("definition") or {}
    return ui.modal(
        ui.p(f"Select {cand.get('label')} for {function_name}. It scores the function from this "
             "version on, as a published criterion labelled State SQT, and every later build of the "
             "region applies the decision."),
        ui.div(f"Its index is kept as the SQT publishes it; DEEP bands it at 0.39 and 0.69. "
               f"Verification: {VERIFICATION_WORDS.get(cand.get('verification'), cand.get('verification') or 'unknown')}."
               + (f" Units: {d.get('units')}." if d.get("units") else " Units are not stated."), class_="fs-note"),
        ui.input_checkbox_group(ns("fs_replace"), "It takes the place of (they stay built, recorded as "
                                "supported, not selected)", {mk: name for mk, name in fitted},
                                selected=[mk for mk, _ in fitted]) if fitted else
        ui.div("No curve built here scores this function, so nothing is replaced.", class_="fs-note"),
        ui.output_ui(ns("fs_select_checks")),
        ui.input_text_area(ns("fs_select_reason"), "Why", rows=3, width="100%"),
        title="Select a state SQT curve", size="l", easy_close=True,
        footer=ui.TagList(ui.modal_button("Cancel"),
                          ui.input_action_button(ns("fs_select_confirm"), "Select", class_="btn btn-primary")))


def completion_modal(cand: Mapping, function_name: str, *, ns, by: str = ""):
    """Complete an SQT curve the source leaves open (the owner's decision 1, 2026-09-24): the
    published points, each open end, up to ``COMPLETION_ROWS`` points past each, the reason
    and the initials (``by``, the open project's), with a live preview of the completed curve."""
    d = cand.get("definition") or {}
    published = d.get("publishedPoints") or d.get("points") or []
    ends = d.get("openEnds") or []
    prior: dict = {}
    for q in (cand.get("completion") or {}).get("points") or []:
        prior.setdefault(str(q.get("side")), []).append(q)
    toward = {"increasing": {"low": 0, "high": 1}, "decreasing": {"low": 1, "high": 0}}.get(
        str(d.get("direction")), {})
    blocks = []
    for e in ends:
        side = str(e.get("side"))
        aim = f" Its last point reaches index {toward[side]}." if side in toward else ""
        mine = prior.get(side) or []
        fields = []
        for i in range(COMPLETION_ROWS):
            q = mine[i] if i < len(mine) else None
            fields.append(ui.div(
                ui.input_numeric(ns(f"fs_cx_{side}_{i}"), "Value", value=q.get("x") if q else None),
                ui.input_numeric(ns(f"fs_cy_{side}_{i}"), "Index", value=q.get("y") if q else None,
                                 min=0, max=1, step=0.01),
                class_="fs-completion-row"))
        blocks.append(ui.div(
            ui.tags.strong(f"Past the {side} end"),
            ui.div(f"The source stops at {cs.fmt_num(e.get('x'))}, which scores {cs.fmt_num(e.get('y'))}.{aim}",
                   class_="fs-note"),
            *fields, class_="fs-completion-end"))
    points = ui.tags.table(
        ui.tags.thead(ui.tags.tr(ui.tags.th("Value"), ui.tags.th("Index"))),
        ui.tags.tbody(*[ui.tags.tr(ui.tags.td(cs.fmt_num(q.get("x"))), ui.tags.td(cs.fmt_num(q.get("y"))))
                        for q in published]),
        class_="table table-sm fs-table fs-completion-points")
    return ui.modal(
        ui.p(f"{cand.get('label')} for {function_name}. The source does not say how the curve scores "
             "past its open end, so it is used only once points take it to the index limit, keeping "
             "its direction. The published curve names the points the SQT publishes and the ones "
             "added here.", class_="mb-2"),
        ui.div(ui.div(ui.tags.strong("Points the SQT publishes"), points), *blocks, class_="fs-completion"),
        ui.output_ui(ns("fs_completion_preview")),
        ui.input_text_area(ns("fs_completion_reason"), "Why these points", rows=3, width="100%",
                           value=str((cand.get("completion") or {}).get("reason") or "")),
        ui.input_text(ns("fs_completion_by"), "Your initials", value=by),
        title="Complete the curve", size="l", easy_close=True,
        footer=ui.TagList(ui.modal_button("Cancel"),
                          ui.input_action_button(ns("fs_completion_save"), "Save the completion",
                                                 class_="btn btn-primary")))


# --------------------------------------------------------------------------- #
# server: the register's own actions (REF-15's are source_panel's)
# --------------------------------------------------------------------------- #
def disposition_form(cand: Mapping, fid: str, function_name: str, *, ns):
    return ui.modal(
        ui.p(f"Record why {cand.get('label')} is not selected for {function_name}. "
             "This is the register's record; it changes nothing the version scores."),
        ui.input_text_area(ns("fs_reason"), "Reason", rows=3, width="100%",
                           placeholder="For example: the protocol differs from what DEEP users measure."),
        title="Not selected, and why",
        footer=ui.TagList(ui.modal_button("Cancel"),
                          ui.input_action_button(ns("fs_reason_confirm"), "Record", class_="btn btn-primary")),
        easy_close=True)


def final_selection_server(input, output, session, state, *, tiles):
    """Register the section's output and handlers inside the Reference Curves page.
    ``tiles``: a reactive calc of the gallery's tiles with the reference tiles included."""
    ns = session.ns
    compare = reactive.value([])
    opened = reactive.value(set())
    pending = reactive.value(None)
    picker = reactive.value(None)

    def _register() -> dict:
        build = state.reference_build()
        region = state.region_of_applicability() or {}
        return C.deep_register(tiles=tiles(), build=build,
                               decisions=state.owner_curve_decisions() or [],
                               metric_config=state.metric_config() or {},
                               register=state.candidate_register(),
                               coverage_exceptions=state.function_coverage_exceptions() or [],
                               region={"code": region.get("code")})

    register = reactive.calc(_register)

    def _sqt_ready() -> bool:
        try:
            from streamcurves import sqt_registry
            return sqt_registry.available()
        except Exception:  # noqa: BLE001 - no registry yet reads as not available
            return False

    def _states() -> list:
        with reactive.isolate():
            code = (state.region_of_applicability() or {}).get("code")
        return list(region_states(str(code or "")))

    def _run_dir():
        """The region's run folder (where its answers, gaps and curve decisions
        live), or None in an installed copy or for a non-ecoregion session."""
        with reactive.isolate():
            return rb.region_run_dir(state.region_of_applicability())

    def _doc() -> Optional[dict]:
        """The build's provenance document (records and review queue), when the
        session came from a build."""
        with reactive.isolate():
            doc = state.source_provenance()
        return doc if isinstance(doc, dict) and doc.get("records") is not None else None

    def _can_source() -> bool:
        """A source can be chosen for a function (REF-15): an ecoregion session
        with a pressure-screen build."""
        from streamcurves import pressure_evidence as pe
        with reactive.isolate():
            build = state.reference_build()
            region = state.region_of_applicability()
        return bool(build and build.get("method") == pe.METHOD and rb.is_ecoregion(region))

    def _session_gap_ids() -> set:
        """The functions whose documented gap is the session's own (not one a curve
        decision carries, which goes with the decision's undo)."""
        with reactive.isolate():
            gaps = state.function_coverage_exceptions() or []
        return {str(g.get("functionId")) for g in gaps
                if isinstance(g, Mapping) and not str(g.get("decision") or "").startswith("cd-")}

    def _origin_approvals() -> list:
        with reactive.isolate():
            origin = state.assessment_source() or {}
        return list(origin.get("portfolio_approvals") or [])

    @render.ui
    def final_selection():
        # the shell only: the head, the compare panel, the build items and the list
        # render on their own, so comparing a curve never redraws (and folds) the
        # list around it
        if state.reference_build() is None:
            return ui.div(fa("circle-info"), " The candidate register reads a pressure-screen build. "
                          "This version was built before it, so its curves are listed in the Gallery "
                          "and Table.", class_="fs-note")
        return ui.div(ui.output_ui(ns("fs_head")), ui.output_ui(ns("fs_compare")),
                      ui.output_ui(ns("fs_build_items")), ui.output_ui(ns("fs_list")),
                      ui.output_ui(ns("fs_versions")), class_="fs-page")

    @render.ui
    def fs_head():
        return head_ui(register(), extension_on=oc.alternatives_enabled(), export_id=ns("fs_export"))

    # ── the deep link: candidate=<key> opens the candidate's row ────────────
    linked = reactive.value(None)        # the key a link named, until its row is opened
    highlight = reactive.value(None)     # the key whose row is marked

    @reactive.effect
    def _read_deep_link():
        # the page URL, as the browser sent it (run_region_batch.py open prints it)
        try:
            search = session.clientdata.url_search()
        except Exception:  # noqa: BLE001 - not sent yet: the read re-runs when it is
            return
        key = parse_candidate_link(search)
        if key:
            linked.set(key)

    @reactive.effect
    async def _open_linked_row():
        key = linked()
        if not key or state.reference_build() is None:
            return
        fids = candidate_functions(register(), key)
        if not fids:
            return                      # the open session does not hold it: wait for one that does
        with reactive.isolate():
            opened.set(set(opened()) | set(fids))
            nonce = state.nav_request_nonce() or 0
            snonce = state.workspace_section_nonce() or 0
        highlight.set(key)
        linked.set(None)
        # Reference curves, Select final curves, then the row itself
        state.curves_section.set(SECTION)
        state.nav_request.set("curves")
        state.nav_request_nonce.set(nonce + 1)
        state.workspace_section_request.set(SECTION)
        state.workspace_section_nonce.set(snonce + 1)
        await session.send_custom_message("scrollToElement", {"id": ns(linked_row_id(key))})

    # ── Compare: A (the session's origin) against B ─────────────────────────
    cmp_result = reactive.value(None)    # {"report", "a_label", "b_label"}
    _cmp_inputs: dict = {"choice": "", "path": ""}

    def _gallery_choices() -> dict:
        try:
            from streamcurves import gallery
            return compare_choices(gallery.entries_from_library())
        except Exception:  # noqa: BLE001 - no library here: the path box still works
            return {}

    @render.ui
    def fs_versions():
        got = cmp_result()
        with reactive.isolate():
            origin = state.assessment_source() or {}
        a_dir = origin_version_dir(origin)
        body = [compare_chooser_ui(_gallery_choices(), ns=ns, a_label=origin_label(origin),
                                   selected=_cmp_inputs.get("choice"), path_value=_cmp_inputs.get("path") or "",
                                   can_compare=a_dir is not None)]
        if got:
            body.append(compare_report_ui(got["report"], ns=ns, a_label=got["a_label"],
                                          b_label=got["b_label"], export_id=ns("fs_cmp_csv")))
        return ui.tags.details(
            ui.tags.summary(ui.tags.strong("Compare with another version"),
                            ui.tags.span(" this version against a library version or a staged run: "
                                         "curves, register, ledger, decisions, digests", class_="fs-count")),
            ui.div(*body, class_="fs-fn-body"),
            class_="fs-fn fs-versions", **({"open": ""} if got else {}))

    @reactive.effect
    @reactive.event(input.fs_cmp_run)
    @guard("compare the versions")
    async def _compare_versions():
        with reactive.isolate():
            origin = state.assessment_source() or {}
            choice = str(input.fs_cmp_b() or "") if "fs_cmp_b" in input else ""
            path_text = str(input.fs_cmp_path() or "") if "fs_cmp_path" in input else ""
        a_dir = origin_version_dir(origin)
        if a_dir is None:
            ui.notification_show("This session was not opened from a library version or a staged run, "
                                 "so there is no version A to compare.", type="warning", duration=8)
            return
        b_dir = compare_target(choice, path_text)
        if b_dir is None:
            ui.notification_show("Choose a version, or enter a staged run folder.", type="warning", duration=6)
            return
        _cmp_inputs.update(choice=choice, path=path_text)
        try:
            with st.busy(state):
                rep = await asyncio.to_thread(
                    cmpv.full_report, a_dir, b_dir, with_registers=True, with_ledger=True,
                    with_digests=True, with_owner_decisions=True)
        except FileNotFoundError as exc:
            ui.notification_show(str(exc), type="warning", duration=10)
            return
        except Exception as exc:  # noqa: BLE001 - the report names the problem, never a dead page
            ui.notification_show(f"The versions could not be compared: {exc}", type="error", duration=12)
            return
        cmp_result.set({"report": rep, "a_label": origin_label(origin),
                        "b_label": cmpv.version_label(b_dir)})

    @render.download(filename=lambda: "version-comparison.csv")
    def fs_cmp_csv():
        with reactive.isolate():
            got = cmp_result()
        yield compare_csv(got["report"]) if got else "section,subject,field,a,b,note\n"

    @render.ui
    def fs_compare():
        cands = {c["candidateKey"]: c for c in register()["candidates"]}
        return compare_ui([cands[k] for k in compare() if k in cands], ns=ns)

    # repaints the build items after an answer is saved (the file is not reactive)
    answers_nonce = reactive.value(0)

    @render.ui
    def fs_build_items():
        state.source_provenance()
        answers_nonce()
        run_dir = _run_dir()
        answered = rb.read_answers(run_dir) if run_dir is not None else []
        return build_items_ui(build_items(_doc(), answered), ns=ns, writable=run_dir is not None)

    @render.ui
    def fs_list():
        with reactive.isolate():
            open_ids = set(opened())
        state.portfolio_approvals()
        state.function_coverage_exceptions()
        state.assessment_source()
        return functions_ui(register(), ns=ns, compare=compare(), extension_on=oc.alternatives_enabled(),
                            open_ids=open_ids, sqt_ready=_sqt_ready(), limit=portfolio_limit(),
                            approvals=st.portfolio_approvals(state), origin_approvals=_origin_approvals(),
                            can_source=_can_source(), session_gap_ids=_session_gap_ids(),
                            linked_key=highlight())

    @reactive.effect
    @reactive.event(input.fs_open)
    def _track_open():
        p = input.fs_open() or {}
        fid = str(p.get("fid") or "")
        if fid:
            cur = set(opened())
            (cur.add if p.get("open") else cur.discard)(fid)
            opened.set(cur)

    @render.download(filename=lambda: "candidate-register.csv")
    def fs_export():
        with reactive.isolate():
            yield export_csv(register())

    @reactive.effect
    @reactive.event(input.final_action)
    @guard("act on the candidate")
    def _act():
        p = input.final_action() or {}
        action, key, fid = p.get("action"), str(p.get("key") or ""), str(p.get("fid") or "")
        with reactive.isolate():
            reg = register()
        cands = {c["candidateKey"]: c for c in reg["candidates"]}
        if fid:
            opened.set(set(opened()) | {fid})
        if action == "compare":
            cur = list(compare())
            if key in cur:
                cur.remove(key)
            elif len(cur) >= C.MAX_COMPARE:
                ui.notification_show(f"Compare at most {C.MAX_COMPARE} at a time. Stop comparing one first.",
                                     type="warning", duration=5)
                return
            else:
                cur.append(key)
            compare.set(cur)
            return
        name = next((f["functionName"] for f in reg["functions"] if f["functionId"] == fid), fid)
        if action == "review" and key in cands:
            # a curve held for review (CURVE-07): accept or remove, with a rationale
            metric = str(((cands[key].get("identity") or {}).get("subject") or {}).get("id") or "")
            decision = "remove" if str(p.get("decision")) == "remove" else "accept"
            if not metric:
                return
            pending.set({"key": key, "fid": fid, "review": decision, "metric": metric})
            ui.modal_show(review_modal(cands[key].get("label") or metric, decision, ns=ns))
            return
        if action == "gap" and fid:
            pending.set({"fid": fid, "gap": True})
            ui.modal_show(gap_modal(name, ns=ns, by=sp.maintainer(state), reasons=rb.coverage_reasons()))
            return
        if action == "withdraw_gap" and fid:
            with reactive.isolate():
                gaps = list(state.function_coverage_exceptions() or [])
            kept = [g for g in gaps if str(g.get("functionId")) != fid
                    or str(g.get("decision") or "").startswith("cd-")]
            state.function_coverage_exceptions.set(kept)
            run_dir = _run_dir()
            if run_dir is not None:
                rb.remove_gap(run_dir, fid)
            ui.notification_show(f"Withdrawn: {name} is no longer documented as a gap.",
                                 type="message", duration=5)
            return
        if action == "approve" and fid:
            fn = next((f for f in reg["functions"] if f["functionId"] == fid), None)
            s01 = (select01_status(fn, limit=portfolio_limit(), approvals=st.portfolio_approvals(state),
                                   origin_approvals=_origin_approvals()) if fn else None)
            if not s01:
                ui.notification_show(f"{name} is within the portfolio maximum; no approval is needed.",
                                     type="message", duration=5)
                return
            pending.set({"fid": fid, "approve": True})
            ui.modal_show(approval_modal(name, s01["n"], s01["limit"], ns=ns, by=sp.maintainer(state)))
            return
        if action == "unapprove" and fid:
            st.withdraw_portfolio_approval(state, fid)
            ui.notification_show(f"Withdrawn: the approval of {name}'s set.", type="message", duration=5)
            return
        if action == "why_not" and key in cands:
            pending.set({"key": key, "fid": fid})
            name = next((f["functionName"] for f in reg["functions"] if f["functionId"] == fid), fid)
            ui.modal_show(disposition_form(cands[key], fid, name, ns=ns))
            return
        if action == "withdraw":
            state.candidate_register.set(C.withdraw_disposition(state.candidate_register(),
                                                                str(p.get("decision") or "")))
            ui.notification_show("Undone.", type="message", duration=4)
            return
        if action == "drop" and key:
            try:
                new = C.remove_considered(state.candidate_register(), key,
                                          decisions=state.owner_curve_decisions() or [])
            except ValueError as exc:
                ui.notification_show(str(exc), type="warning", duration=6)
                return
            state.candidate_register.set(new)
            compare.set([k for k in compare() if k != key])
            return
        if action == "add_sqt":
            picker.set(fid)
            name = next((f["functionName"] for f in reg["functions"] if f["functionId"] == fid), fid)
            code = (state.region_of_applicability() or {}).get("code")
            ui.modal_show(picker_modal(fid, name, ns=ns, states=region_states(str(code or ""))))
            return
        if action == "add_record":
            from streamcurves import sqt_registry
            record = sqt_registry.record(key)
            if record is None:
                ui.notification_show("That SQT curve is no longer in the registry.", type="warning", duration=5)
                return
            code = (state.region_of_applicability() or {}).get("code")
            targets = curve_targets(state, function_metrics(reg, fid))
            cand = C.sqt_candidate(record, function_id=fid, region={"code": code},
                                   context=C.sqt_context(record, function_id=fid, states=_states(),
                                                         targets=targets))
            try:
                new = C.add_considered(state.candidate_register(), cand, by=sp.maintainer(state))
            except ValueError as exc:
                ui.notification_show(str(exc), type="warning", duration=6)
                return
            state.candidate_register.set(new)
            ui.notification_show(f"Added for comparison: {cand['label']}.", type="message", duration=5)
            return
        if action == "complete" and key in cands:
            pending.set({"key": key, "fid": fid, "complete": True})
            name = next((f["functionName"] for f in reg["functions"] if f["functionId"] == fid), fid)
            ui.modal_show(completion_modal(cands[key], name, ns=ns, by=sp.maintainer(state)))
            return
        if action == "select" and key in cands:
            if not oc.alternatives_enabled():
                ui.notification_show(oc.EXTENSION_OFF, type="warning", duration=6)
                return
            if cands[key].get("needsCompletion"):
                ui.notification_show(COMPLETE_FIRST, type="warning", duration=8)
                return
            fn = next((f for f in reg["functions"] if f["functionId"] == fid), None)
            fitted = [((cands[r["candidateKey"]]["identity"].get("subject") or {}).get("id"),
                       cands[r["candidateKey"]].get("label"))
                      for r in (fn or {}).get("selected") or []
                      if cands[r["candidateKey"]]["identity"].get("sourceKind") == "fitted"]
            pending.set({"key": key, "fid": fid, "select": True})
            ui.modal_show(select_modal(cands[key], (fn or {}).get("functionName") or fid, fitted, ns=ns))

    @reactive.effect
    @reactive.event(input.fs_reason_confirm)
    @guard("record the reason")
    def _confirm_reason():
        p = pending()
        if not p:
            return
        with reactive.isolate():
            reg = register()
        cand = next((c for c in reg["candidates"] if c["candidateKey"] == p["key"]), {})
        try:
            new = C.record_disposition(state.candidate_register(), p["key"], p["fid"], by=sp.maintainer(state),
                                       reason=input.fs_reason() or "", basis_digest=cand.get("basisDigest"))
        except ValueError as exc:
            ui.notification_show(str(exc), type="warning", duration=6)
            return
        state.candidate_register.set(new)
        pending.set(None)
        ui.modal_remove()
        ui.notification_show("Recorded. It stays with this project and its published record.",
                             type="message", duration=5)

    # ── the decisions this section is the one home of ───────────────────────
    def _record_answer(item: Mapping, action: str, rationale: str) -> Optional[str]:
        """Write one reviewer answer into the region's ``owner_decisions.json``
        (:func:`region_build.save_answer`), checked against the build's record first.
        Returns the problem, or None when saved (or when there is no run folder to
        save into, which is not an error: an installed copy keeps the session's copy)."""
        run_dir = _run_dir()
        doc = _doc() or {"records": []}
        try:
            decision = rb.build_decision(doc, item, action, rationale, reviewer=sp.maintainer(state))
        except ValueError as exc:
            return str(exc)
        problems = rb.decision_problems(doc, decision)
        # a record the build never wrote cannot be checked; the answer still stands
        problems = [x for x in problems if not x.startswith("No record on this run matches")]
        if problems:
            return "; ".join(problems)
        if run_dir is not None:
            rb.save_answer(run_dir, decision)
        return None

    @reactive.effect
    @reactive.event(input.fs_review_confirm)
    @guard("record the review decision")
    def _confirm_review():
        p = pending()
        if not p or not p.get("review"):
            return
        note = str(input.fs_review_note() or "").strip()
        if not note:
            ui.notification_show("Add a rationale before continuing.", type="warning", duration=5)
            return
        metric = str(p.get("metric") or "")
        accepting = p["review"] == "accept"
        decision = rs.DECISION_FINALIZED if accepting else rs.DECISION_REMOVED
        # the session's curve review: what the version publishes
        ca.set_review_decision(state, metric, decision, note=note, actor=sp.maintainer(state))
        # and the region's answers file: what the next build applies (the queue item
        # when the build raised one, else the same answer in its shape)
        item = next((i for i in rb.open_queue_items(_doc())
                     if i.get("rule_id") == "CURVE-07" and str(i.get("subject")) == metric), None)
        item = item or {"rule_id": "CURVE-07", "subject": metric, "evidence": {}}
        problem = _record_answer(item, "accept" if accepting else "reject", note)
        pending.set(None)
        ui.modal_remove()
        answers_nonce.set((answers_nonce() or 0) + 1)
        done = "Accepted" if accepting else "Removed"
        if problem:
            ui.notification_show(f"{done} {metric} for this version. The region's answers file was "
                                 f"not written: {problem}", type="warning", duration=10)
            return
        ui.notification_show(f"{done} {metric}. Recorded on this version and for the region's "
                             "next build.", type="message", duration=5)

    @reactive.effect
    @reactive.event(input.fs_gap_confirm)
    @guard("record the gap")
    def _confirm_gap():
        from streamcurves import prefs
        p = pending()
        if not p or not p.get("gap"):
            return
        fid = str(p["fid"])
        exc = rb.build_coverage_exception(fid, input.fs_gap_reason() or "", input.fs_gap_why() or "",
                                          recorded_by=prefs.given_or_na(input.fs_gap_by()))
        problems = rb.coverage_problems([exc])
        if problems:
            ui.notification_show("Not recorded. " + " ".join(problems), type="warning", duration=10)
            return
        from datetime import datetime, timezone
        exc["recordedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with reactive.isolate():
            gaps = list(state.function_coverage_exceptions() or [])
        state.function_coverage_exceptions.set(
            [g for g in gaps if str(g.get("functionId")) != fid] + [exc])
        run_dir = _run_dir()
        if run_dir is not None:
            rb.save_gap(run_dir, exc)
        pending.set(None)
        ui.modal_remove()
        ui.notification_show(f"Recorded {fid} as a documented gap"
                             + (", here and for the region's next build." if run_dir is not None
                                else " for this version."), type="message", duration=5)

    @reactive.effect
    @reactive.event(input.fs_approve_confirm)
    @guard("record the approval")
    def _confirm_approval():
        p = pending()
        if not p or not p.get("approve"):
            return
        try:
            st.add_portfolio_approval(state, p["fid"], approver=str(input.fs_approve_by() or ""),
                                      note=str(input.fs_approve_note() or ""))
        except ValueError as exc:
            ui.notification_show(str(exc), type="warning", duration=6)
            return
        pending.set(None)
        ui.modal_remove()
        ui.notification_show("Approved. The publish writes it into the version's metadata (SELECT-01).",
                             type="message", duration=5)

    @reactive.effect
    @reactive.event(input.fs_save_answers)
    @guard("save the answers")
    def _save_answers():
        run_dir = _run_dir()
        if run_dir is None:
            ui.notification_show("This copy has no run folder for the region, so the answers "
                                 "cannot be saved here.", type="warning", duration=6)
            return
        items = build_items(_doc(), rb.read_answers(run_dir))
        saved, problems = 0, []
        for i, item in enumerate(items):
            action = input[f"fs_act_{i}"]() if f"fs_act_{i}" in input else None
            why = input[f"fs_why_{i}"]() if f"fs_why_{i}" in input else ""
            if not action:
                continue
            problem = _record_answer(item, str(action), str(why or ""))
            if problem:
                problems.append(f"{item.get('item_id')}: {problem}")
            else:
                saved += 1
        answers_nonce.set((answers_nonce() or 0) + 1)
        if problems:
            ui.notification_show("Not all saved. " + " | ".join(problems), type="warning", duration=14)
        if saved:
            ui.notification_show(f"Saved {count_text(saved, 'answer')} for the region. Build it again "
                                 "to fold them in.", type="message", duration=8)
        elif not problems:
            ui.notification_show("Answer at least one item first.", type="warning", duration=4)

    # suspend_when_hidden=False: dialog outputs bind while the modal is still hidden
    # (Bootstrap fade) and a suspended output never resumes (DEEP documents the same trap)
    @output(suspend_when_hidden=False)
    @render.ui
    def fs_sqt_results():
        fid = picker()
        if not fid:
            return None
        from streamcurves import sqt_registry
        with reactive.isolate():
            targets = curve_targets(state, function_metrics(register(), fid))
        # read live, so a curve just added reads Added
        have = {((c.get("identity") or {}).get("sourceRef") or {}).get("registryKey")
                for c in C.load_register(state.candidate_register())["considered"]
                if fid in (c.get("functions") or [])}
        q = " ".join(str(input.fs_q() or "").lower().split())
        recs = sqt_registry.records(state=input.fs_state() or None,
                                    function=None if input.fs_allfn() else fid,
                                    eligible=True if input.fs_elig() else None)
        if input.fs_edition():
            # filtered by the words the rows show, the ones the filter offers
            recs = [r for r in recs if _edition_words(r) == input.fs_edition()]
        if input.fs_ver():
            recs = [r for r in recs if (r.get("verification") or {}).get("status") == input.fs_ver()]
        if q:
            recs = [r for r in recs if q in " ".join(str(x or "") for x in (
                r.get("originalMetricName"), r.get("key"), r.get("stratumName"))).lower()]
        return picker_results_ui(recs, ns=ns, function_id=fid, states=_states(), targets=targets, have=have)

    def _rechecks(p: Mapping, replaced: Iterable[str]) -> list:
        """The candidate checked again as adding checked it: against each curve it takes the
        place of, or, when it replaces none, against the function's own curves."""
        with reactive.isolate():
            reg = register()
            code = (state.region_of_applicability() or {}).get("code")
        cand = next((c for c in reg["candidates"] if c["candidateKey"] == p.get("key")), None)
        if cand is None or not cand.get("record"):
            return []
        replaced = list(replaced or [])
        if not replaced:
            targets = curve_targets(state, function_metrics(reg, p["fid"]))
            return [(None, recheck(cand, p["fid"], states=_states(), targets=targets, region={"code": code}))]
        return [(tg, recheck(cand, p["fid"], states=_states(), targets=[tg], region={"code": code}))
                for tg in curve_targets(state, replaced)]

    # suspend_when_hidden=False: dialog outputs bind while the modal is still hidden
    # (Bootstrap fade) and a suspended output never resumes (DEEP documents the same trap)
    @output(suspend_when_hidden=False)
    @render.ui
    def fs_select_checks():
        p = pending()
        if not p or not p.get("select"):
            return None
        return replacement_checks_ui(_rechecks(p, input.fs_replace() or []))

    @reactive.effect
    @reactive.event(input.fs_select_confirm)
    @guard("record the selection")
    def _confirm_select():
        from streamcurves import owner_sources
        from streamcurves import region_build as rb
        p = pending()
        if not p or not p.get("select"):
            return
        with reactive.isolate():
            reg = register()
            build = state.reference_build()
            built = state.completed_metrics() or {}
            current = list(state.owner_curve_decisions() or [])
            region = state.region_of_applicability() or {}
        cand = next((c for c in reg["candidates"] if c["candidateKey"] == p["key"]), None)
        if cand is None:
            return
        rechecked = _rechecks(p, input.fs_replace() or [])
        refused = [(tg, again) for tg, again in rechecked
                   if (again.get("eligibility") or {}).get("status") != "eligible"]
        if refused:
            tg, again = refused[0]
            where = f"in place of {tg.get('name')}" if tg else "here"
            ui.notification_show(f"It does not apply {where}: "
                                 + " ".join((again.get("eligibility") or {}).get("reasons") or []),
                                 type="warning", duration=10)
            return
        if cand.get("needsCompletion") or any(again.get("needsCompletion") for _tg, again in rechecked):
            ui.notification_show(COMPLETE_FIRST, type="warning", duration=8)
            return
        try:
            from views import curve_gallery as cg
            metric = (cand["identity"].get("subject") or {}).get("id")
            source = owner_sources.sqt_source(cand)
            decision = oc.new_decision(
                metric, oc.SOURCE,
                rationale=input.fs_select_reason() or "", recorded_by=sp.maintainer(state),
                functions=[p["fid"]], source=source,
                replaces=[{"metric": mk, "functionId": p["fid"]} for mk in (input.fs_replace() or [])])
            # the digest the register computes for the curve this decision puts in the function
            # (its tile), so the decision reads Look again only when that curve changes
            basis = cg.metric_basis(state, metric, oc.merge(current, decision))
            if basis:
                decision["basisDigest"] = basis
            oc.validate(decision, build=build, built=built, decisions=current)
            run_dir = rb.region_run_dir(region)
            if run_dir is not None:
                rb.standing_decisions(run_dir, region.get("code"))
                oc.save(run_dir, decision)
        except ValueError as exc:
            ui.notification_show(str(exc), type="warning", duration=8)
            return
        state.owner_curve_decisions.set(oc.merge(current, decision))
        pending.set(None)
        ui.modal_remove()
        ui.notification_show("Selected. It applies here now and to every later build of this region.",
                             type="message", duration=6)

    # completing an SQT curve the source leaves open
    def _completing() -> Optional[dict]:
        p = pending()
        if not p or not p.get("complete"):
            return None
        with reactive.isolate():
            reg = register()
        return next((c for c in reg["candidates"] if c["candidateKey"] == p["key"]), None)

    def _typed_points(cand: Mapping) -> list[dict]:
        """The points typed into the dialog, past each open end (blank rows left out)."""
        out = []
        for e in (cand.get("definition") or {}).get("openEnds") or []:
            side = str(e.get("side"))
            for i in range(COMPLETION_ROWS):
                ids = (f"fs_cx_{side}_{i}", f"fs_cy_{side}_{i}")
                if not all(x in input for x in ids):
                    continue
                x, y = input[ids[0]](), input[ids[1]]()
                if x is None and y is None:
                    continue
                out.append({"x": x, "y": y, "side": side})
        return out

    # suspend_when_hidden=False: dialog outputs bind while the modal is still hidden
    # (Bootstrap fade) and a suspended output never resumes (DEEP documents the same trap)
    @output(suspend_when_hidden=False)
    @render.ui
    def fs_completion_preview():
        cand = _completing()
        if cand is None:
            return None
        rec = cand.get("record") or {}
        adoption = C.sqt_adoption(rec)
        typed = _typed_points(cand)
        problems = C.check_completion(adoption["points"], adoption["openEnds"], rec.get("direction"), typed)
        pts = sorted([(float(x), float(y)) for x, y in adoption["points"]]
                     + [(float(q["x"]), float(q["y"])) for q in typed
                        if q.get("x") is not None and q.get("y") is not None])
        tile = {"metric": (cand.get("identity") or {}).get("subject", {}).get("id"),
                "display_name": cand.get("label"), "units": rec.get("units"),
                "strata": [{"label": None, "points": pts}], "reference_range": (None, None),
                "domain": None, "badge": ""}
        said = (ui.tags.ul(*[ui.tags.li(x) for x in problems], class_="fs-completion-problems") if problems
                else ui.div(fa("circle-check"), " These points complete the curve.", class_="fs-completion-ok"))
        return ui.div(ui.HTML(cs.tile_svg(tile, w=TILE_W + 120, h=TILE_H + 40)), said,
                      class_="fs-completion-preview")

    @reactive.effect
    @reactive.event(input.fs_completion_save)
    @guard("save the completion")
    def _save_completion():
        from streamcurves import prefs
        cand = _completing()
        if cand is None:
            return
        p = pending()
        with reactive.isolate():
            decisions = list(state.owner_curve_decisions() or [])
        try:
            new = C.complete_curve(state.candidate_register(), p["key"], _typed_points(cand),
                                   by=prefs.given_or_na(input.fs_completion_by()),
                                   reason=input.fs_completion_reason() or "", decisions=decisions)
        except ValueError as exc:
            ui.notification_show(str(exc), type="warning", duration=10)
            return
        state.candidate_register.set(new)
        pending.set(None)
        ui.modal_remove()
        ui.notification_show("Completed. The curve names the points you added, and it can now be selected.",
                             type="message", duration=6)


__all__ = ["SECTION", "candidate_tile", "final_selection_ui", "function_row_ui", "compare_ui",
           "head_ui", "functions_ui", "has_curve", "kind_label", "reason_ui", "dom_id",
           "export_csv", "final_selection_server", "disposition_form", "register_export",
           "function_metrics", "curve_targets", "recheck", "replacement_checks_ui", "picker_results_ui",
           "picker_modal", "select_modal", "region_states", "completion_modal", "rule_words",
           "edition_choices", "COMPLETE_FIRST", "RULE_WORDS",
           "select01_status", "build_items", "build_items_ui", "review_modal", "gap_modal",
           "approval_modal", "portfolio_limit", "session_register", "unresolved_count",
           "unapproved_functions", "ROW_ANSWERED_RULES", "linked_row_id", "parse_candidate_link",
           "candidate_functions", "origin_version_dir", "origin_label", "compare_choices",
           "compare_target", "compare_chooser_ui", "compare_report_ui", "compare_csv", "VERDICT_CLASS"]
