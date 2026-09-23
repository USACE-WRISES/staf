"""Select final curves: the candidate register of a DEEP session, one row per STAF function.

The third section of the Reference Curves page, beside Gallery and Table. Each function
shows the curves that score it and, folded under it, every alternative considered with its
status and the reason: SELECT-04's supported but not selected curves, curves withheld for
want of reference support (REF-06), curves held for review (CURVE-07), the owner's REF-15
decisions, and curves added for comparison (a published state SQT curve). Up to three can be
compared side by side.

Nothing here decides a curve on its own. "Use in this function" and "Undo" open REF-15's own
form and undo (``views.source_panel``); a state SQT curve is selected through a REF-15
decision, the extension behind ``owner_decisions.alternatives_over_fitted``. The reason a
person gives for not selecting a considered curve is the register's own record
(``candidates.record_disposition``). Every string is user-visible, so none carries an em dash.
"""
from __future__ import annotations

import csv
import functools
import io
import json
import re
from typing import Any, Iterable, Mapping, Optional

from shiny import reactive, render, ui

from streamcurves import candidates as C
from streamcurves import curve_svg as cs
from streamcurves import owner_curves as oc
from views import source_panel as sp
from views.theme import fa
from views.uihelpers import guard

SECTION = "final"
STATUS_CLASS = {C.SELECTED: "is-selected", C.ELIGIBLE: "is-eligible", C.EXCLUDED: "is-excluded",
                C.NOT_EVALUATED: "is-pending", C.FAILED: "is-failed", C.SUPERSEDED: "is-superseded"}
RULE_WORDS = {"SELECT-04": "Portfolio rule (SELECT-04)", "REF-15": "Owner decision (REF-15)",
              "REF-06": "No defensible reference (REF-06)", "CURVE-07": "Held for review (CURVE-07)",
              "CURVE-11": "Fixed criterion (CURVE-11)", "REF-12": "Reference hierarchy",
              "review": "Curve review", "build": "Build", "person": "Recorded by a person",
              "applicability": "Applicability checks", "considered": "Added for comparison"}
TILE_W, TILE_H = 260, 160


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


def _short(digest: Any) -> str:
    text = str(digest or "")
    return text.split(":", 1)[-1][:10] if text else ""


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
        if (cand.get("eligibility") or {}).get("status", "eligible") == "eligible":
            attrs = {} if extension_on else {"disabled": "disabled",
                                             "title": oc.EXTENSION_OFF}
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


def alternative_row_ui(row: Mapping, cand: Mapping, *, ns, compare: list, extension_on: bool):
    d = row.get("decision") or {}
    ident = cand.get("identity") or {}
    return ui.tags.tr(
        ui.tags.td(ui.div(cand.get("label") or (ident.get("subject") or {}).get("id"), class_="fs-name"),
                   ui.div(kind_label(cand), class_="fs-kind")),
        ui.tags.td(status_pill(row["status"], review=bool(row.get("needsReview")))),
        ui.tags.td(reason_ui(d.get("reason") or ""),
                   ui.div(RULE_WORDS.get(d.get("rule"), d.get("rule") or ""), class_="fs-rule")),
        ui.tags.td(_who(d), class_="fs-who"),
        ui.tags.td(_actions(row, cand, ns=ns, compare=compare, extension_on=extension_on)),
    )


def _chip(cand: Mapping):
    ident = cand.get("identity") or {}
    kind = ident.get("sourceKind")
    return ui.tags.span(ui.tags.span(cand.get("label") or (ident.get("subject") or {}).get("id"),
                                     class_="fs-chip-name"),
                        ui.tags.span(kind_label(cand), class_="fs-chip-kind"),
                        class_="fs-chip")


def function_row_ui(fn: Mapping, cands: Mapping, *, ns, compare: list, extension_on: bool,
                    open_: bool = False, sqt_ready: bool = False):
    """One function: the curves that score it on the summary line; the alternatives,
    the gap and what waits on a decision inside."""
    selected = [cands[r["candidateKey"]] for r in fn["selected"] if r["candidateKey"] in cands]
    alts = [r for r in fn["alternatives"] if r["candidateKey"] in cands]
    flags = []
    if fn["unassessed"]:
        gap = fn.get("gap") or {}
        flags.append(ui.tags.span("Documented gap" if gap else "No curve", class_="fs-flag is-gap",
                                  title=str(gap.get("justification") or "")))
    if fn["unresolved"]:
        flags.append(ui.tags.span(f"{fn['unresolved']} to resolve", class_="fs-flag is-open"))
    summary = ui.tags.summary(
        ui.div(ui.tags.span(fn["functionName"], class_="fs-fn-name"),
               ui.tags.span(fn["discipline"], class_="fs-fn-disc"), class_="fs-fn-head"),
        ui.div(*([_chip(c) for c in selected] or [ui.tags.span("Nothing selected", class_="fs-none")]),
               class_="fs-chips"),
        ui.div(ui.tags.span(f"{len(alts)} considered" if alts else "No alternatives", class_="fs-count"),
               *flags, class_="fs-fn-meta"),
        class_="fs-fn-summary")
    body = []
    if fn["unassessed"] and fn.get("gap"):
        body.append(ui.div(ui.tags.strong("Why it is unassessed: "),
                           str(fn["gap"].get("justification") or fn["gap"].get("reason") or ""),
                           class_="fs-gap"))
    for w in fn.get("waiting") or []:
        body.append(ui.div(fa("hourglass-half"), f" Waiting for a build: {w.get('metric')} "
                           f"({(w.get('source') or {}).get('title') or 'a refused source'}).",
                           class_="fs-waiting"))
    for s in fn.get("stale") or []:
        body.append(ui.div(fa("triangle-exclamation"), f" {s['decision'].get('metric')}: {s['why']}",
                           class_="fs-waiting is-stale"))
    rows = [alternative_row_ui(r, cands[r["candidateKey"]], ns=ns, compare=compare,
                               extension_on=extension_on) for r in fn["selected"] + alts
            if r["candidateKey"] in cands]
    body.append(ui.tags.table(
        ui.tags.thead(ui.tags.tr(ui.tags.th("Curve"), ui.tags.th("Status"), ui.tags.th("Why"),
                                 ui.tags.th("Decided by"), ui.tags.th(""))),
        ui.tags.tbody(*rows), class_="table table-sm fs-table"))
    tools = [ui.tags.button(fa("magnifying-glass"), " Add a state SQT curve", type="button",
                            class_="btn btn-sm btn-outline-secondary",
                            onclick=_onclick(ns("final_action"), {"action": "add_sqt", "fid": fn["functionId"]}),
                            **({} if sqt_ready else {"disabled": "disabled",
                                                      "title": "The state SQT registry is not available."}))]
    body.append(ui.div(*tools, class_="fs-fn-tools"))
    attrs = {"open": ""} if open_ else {}
    toggle = (f"Shiny.setInputValue('{ns('fs_open')}',"
              f"{{fid:'{fn['functionId']}',open:this.open}},{{priority:'event'}})")
    return ui.tags.details(summary, ui.div(*body, class_="fs-fn-body"),
                           class_="fs-fn" + (" is-gap" if fn["unassessed"] else ""),
                           id=ns(dom_id(fn["functionId"])), ontoggle=toggle, **attrs)


def compare_ui(cands: list[Mapping], *, ns):
    """Up to three candidates side by side: the curve, and what it is."""
    if not cands:
        return None
    cols = []
    for c in cands[:C.MAX_COMPARE]:
        ident = c.get("identity") or {}
        tile = candidate_tile(c)
        facts = [("Source", kind_label(c)),
                 ("Metric", (ident.get("subject") or {}).get("id")),
                 ("Reference n", tile.get("reference_n"))]
        for s in tile.get("strata") or []:
            at = crossings(s.get("points") or [])
            if at:
                facts.append(((f"{s.get('label')}: " if s.get("label") else "") + "Crosses 0.39 / 0.69",
                              " / ".join(cs.fmt_num(x) if x is not None else "none" for x in at)))
        facts.append(("Content", _short(c.get("basisDigest"))))
        for chk in (c.get("eligibility") or {}).get("checks") or []:
            facts.append((str(chk.get("id") or chk.get("check") or "").replace("-", " ").capitalize(),
                          f"{chk.get('status')}: {chk.get('detail') or ''}".strip(": ")))
        for lim in c.get("limitations") or []:
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
                 open_ids: Iterable[str] = (), sqt_ready: bool = False):
    cands = {c["candidateKey"]: c for c in register.get("candidates") or []}
    compare = [k for k in compare or [] if k in cands]
    opened = set(open_ids or ())
    return ui.div(*[function_row_ui(f, cands, ns=ns, compare=compare, extension_on=extension_on,
                                    open_=f["functionId"] in opened, sqt_ready=sqt_ready)
                    for f in register.get("functions") or []], class_="fs-functions")


def final_selection_ui(register: Mapping, *, ns, compare: Iterable[str] = (), extension_on: bool = False,
                       open_ids: Iterable[str] = (), sqt_ready: bool = False, export_id: Optional[str] = None):
    """The whole section at once (the page renders its three parts separately)."""
    cands = {c["candidateKey"]: c for c in register.get("candidates") or []}
    compare = [k for k in compare or [] if k in cands]
    return ui.div(head_ui(register, extension_on=extension_on, export_id=export_id),
                  compare_ui([cands[k] for k in compare], ns=ns),
                  functions_ui(register, ns=ns, compare=compare, extension_on=extension_on,
                               open_ids=open_ids, sqt_ready=sqt_ready), class_="fs-page")


def register_export(state) -> Optional[dict]:
    """The register of the open session as a published version's provenance carries it:
    one record per candidate and function, and the counts. None for a legacy session, or
    when the register cannot be read (a publish never fails on it)."""
    try:
        from views import curve_gallery as cg
        with reactive.isolate():
            if state.reference_build() is None:
                return None
            region = state.region_of_applicability() or {}
            reg = C.deep_register(tiles=cg.gallery_rows(state, include_reference=True),
                                  build=state.reference_build(),
                                  decisions=state.owner_curve_decisions() or [],
                                  metric_config=state.metric_config() or {},
                                  register=state.candidate_register(),
                                  coverage_exceptions=state.function_coverage_exceptions() or [],
                                  region={"code": region.get("code")})
        return {"schema": 1, "rows": C.export_rows(reg), "counts": C.register_counts(reg)}
    except Exception:  # noqa: BLE001 - the record is additive; the publish goes on without it
        return None


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


def sqt_context(code: Any, function_id: str) -> dict:
    return {"function": function_id, "states": list(region_states(str(code or ""))), "scoreScale": "staf"}


def picker_modal(function_id: str, function_name: str, *, ns, states: Iterable[str]):
    states = list(states or [])
    return ui.modal(
        ui.p(f"Published state SQT curves for {function_name}. Adding one puts it beside this "
             "function's curves for comparison; it selects nothing.", class_="fs-note"),
        ui.div(ui.input_text(ns("fs_q"), "Metric", placeholder="For example: canopy"),
               ui.input_select(ns("fs_state"), "State", {"": "Any state", **{s: s for s in
                               ("AK", "CO", "MI", "MN", "NC", "SC", "WI", "WY")}},
                               selected=states[0] if len(states) == 1 else ""),
               ui.input_select(ns("fs_ver"), "Verification", {"": "Any", **VERIFICATION_WORDS}),
               ui.input_checkbox(ns("fs_allfn"), "Every function", False),
               ui.input_checkbox(ns("fs_elig"), "Eligible only", True),
               class_="fs-picker-filters"),
        ui.output_ui(ns("fs_sqt_results")),
        title="Add a state SQT curve", size="xl", easy_close=True, footer=ui.modal_button("Close"))


def picker_results_ui(records: list, *, ns, function_id: str, context: Mapping, have: Iterable[str] = (),
                      limit: int = 60):
    from streamcurves import sqt_registry as reg
    if not records:
        return ui.div(fa("circle-info"), " No SQT curve matches these filters.", class_="fs-note")
    rows = []
    scored = []
    for r in records:
        checks = reg.applicability(r, context)
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
            ui.tags.td(ui.div(str(r.get("edition") or f"{r.get('state')} SQT, edition not named")),
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
        ui.input_text_area(ns("fs_select_reason"), "Why", rows=3, width="100%"),
        title="Select a state SQT curve", size="l", easy_close=True,
        footer=ui.TagList(ui.modal_button("Cancel"),
                          ui.input_action_button(ns("fs_select_confirm"), "Select", class_="btn btn-primary")))


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
            return bool(sqt_registry.load().get("records"))
        except Exception:  # noqa: BLE001 - no registry yet reads as not available
            return False

    @render.ui
    def final_selection():
        # the shell only: the head, the compare panel and the list render on their own,
        # so comparing a curve never redraws (and folds) the list around it
        if state.reference_build() is None:
            return ui.div(fa("circle-info"), " The candidate register reads a pressure-screen build. "
                          "This version was built before it, so its curves are listed in the Gallery "
                          "and Table.", class_="fs-note")
        return ui.div(ui.output_ui(ns("fs_head")), ui.output_ui(ns("fs_compare")),
                      ui.output_ui(ns("fs_list")), class_="fs-page")

    @render.ui
    def fs_head():
        return head_ui(register(), extension_on=oc.alternatives_enabled(), export_id=ns("fs_export"))

    @render.ui
    def fs_compare():
        cands = {c["candidateKey"]: c for c in register()["candidates"]}
        return compare_ui([cands[k] for k in compare() if k in cands], ns=ns)

    @render.ui
    def fs_list():
        with reactive.isolate():
            open_ids = set(opened())
        return functions_ui(register(), ns=ns, compare=compare(), extension_on=oc.alternatives_enabled(),
                            open_ids=open_ids, sqt_ready=_sqt_ready())

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
            state.candidate_register.set(C.remove_considered(state.candidate_register(), key))
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
            cand = C.sqt_candidate(record, function_id=fid, context=sqt_context(code, fid),
                                   region={"code": code})
            try:
                new = C.add_considered(state.candidate_register(), cand, by=sp.maintainer())
            except ValueError as exc:
                ui.notification_show(str(exc), type="warning", duration=6)
                return
            state.candidate_register.set(new)
            ui.notification_show(f"Added for comparison: {cand['label']}.", type="message", duration=5)
            return
        if action == "select" and key in cands:
            if not oc.alternatives_enabled():
                ui.notification_show(oc.EXTENSION_OFF, type="warning", duration=6)
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
            new = C.record_disposition(state.candidate_register(), p["key"], p["fid"], by=sp.maintainer(),
                                       reason=input.fs_reason() or "", basis_digest=cand.get("basisDigest"))
        except ValueError as exc:
            ui.notification_show(str(exc), type="warning", duration=6)
            return
        state.candidate_register.set(new)
        pending.set(None)
        ui.modal_remove()
        ui.notification_show("Recorded. It stays with this project and its published record.",
                             type="message", duration=5)

    @render.ui
    def fs_sqt_results():
        fid = picker()
        if not fid:
            return None
        from streamcurves import sqt_registry
        with reactive.isolate():
            code = (state.region_of_applicability() or {}).get("code")
        # read live, so a curve just added reads Added
        have = {((c.get("identity") or {}).get("sourceRef") or {}).get("registryKey")
                for c in C.load_register(state.candidate_register())["considered"]
                if fid in (c.get("functions") or [])}
        q = " ".join(str(input.fs_q() or "").lower().split())
        recs = sqt_registry.records(state=input.fs_state() or None,
                                    function=None if input.fs_allfn() else fid,
                                    eligible=True if input.fs_elig() else None)
        if input.fs_ver():
            recs = [r for r in recs if (r.get("verification") or {}).get("status") == input.fs_ver()]
        if q:
            recs = [r for r in recs if q in " ".join(str(x or "") for x in (
                r.get("originalMetricName"), r.get("key"), r.get("stratumName"))).lower()]
        return picker_results_ui(recs, ns=ns, function_id=fid, context=sqt_context(code, fid), have=have)

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
        try:
            source = owner_sources.sqt_source(cand)
            decision = oc.new_decision(
                (cand["identity"].get("subject") or {}).get("id"), oc.SOURCE,
                rationale=input.fs_select_reason() or "", recorded_by=sp.maintainer(),
                functions=[p["fid"]], source=source,
                replaces=[{"metric": mk, "functionId": p["fid"]} for mk in (input.fs_replace() or [])])
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


__all__ = ["SECTION", "candidate_tile", "final_selection_ui", "function_row_ui", "compare_ui",
           "head_ui", "functions_ui", "has_curve", "kind_label", "reason_ui", "dom_id",
           "export_csv", "final_selection_server", "disposition_form", "register_export"]
