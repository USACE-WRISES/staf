"""The curve source panel: where a curve this build did not fit comes from.

Opened from a gallery tile, a row of the Table's "Curves from other sources" or a
source chip in Function mapping, all through one input any page can set
(``OPEN_INPUT``). The body is built from the session's reference build and the
opened version's provenance by ``streamcurves.curve_sources``; opening it
changes nothing.
"""
from __future__ import annotations

import json
import os
import re
from typing import Mapping, Optional

from shiny import module, reactive, ui

from streamcurves import curve_sources as src
from streamcurves import curve_svg as cs
from streamcurves import metric_names
from streamcurves import owner_curves as oc
from streamcurves import pressure_evidence as pe
from streamcurves import region_build as rb
from streamcurves import run_state as rs
from streamcurves.deep_export import deep_read_staf_crosswalk, metrics_per_function_quick
from views import assessment_publish as ap
from views.state import AppState
from views.theme import fa
from views.uihelpers import guard

PANEL_ID = "source_panel"
#: the one input every page sets to open the panel for a metric
OPEN_INPUT = f"{PANEL_ID}-open"
#: the inputs any page sets to act on a curve (REF-15), or to undo a decision
ACT_INPUT = f"{PANEL_ID}-act"
UNDO_INPUT = f"{PANEL_ID}-undo"
PANEL_W, PANEL_H = 300, 184


def open_onclick(metric: str, *, stop: bool = False) -> str:
    """The onclick that opens the panel for ``metric`` from any page. ``stop``
    keeps the click from also reaching an element around it."""
    payload = json.dumps({"metric": str(metric)}).replace(chr(39), chr(92) + chr(39))
    return (("event.stopPropagation();" if stop else "")
            + f"Shiny.setInputValue('{OPEN_INPUT}',{payload},{{priority:'event'}})")


def open_onkeydown() -> str:
    return "if(event.key==='Enter'||event.key===' '){event.preventDefault();this.click();}"


def _set(input_id: str, payload: dict, *, stop: bool) -> str:
    text = json.dumps(payload).replace(chr(39), chr(92) + chr(39))
    return (("event.stopPropagation();" if stop else "")
            + f"Shiny.setInputValue('{input_id}',{text},{{priority:'event'}})")


def act_onclick(metric: str, action: str, functions=(), *, stop: bool = True) -> str:
    """The onclick that opens the decision form: remove the curve, take it out of
    ``functions`` (unmap), or use it there (include)."""
    return _set(ACT_INPUT, {"metric": str(metric), "action": str(action),
                            "functions": [str(f) for f in functions or ()]}, stop=stop)


def undo_onclick(decision_id: str, *, stop: bool = True) -> str:
    """The onclick that withdraws one of the owner's decisions."""
    return _set(UNDO_INPUT, {"decision": str(decision_id)}, stop=stop)


def maintainer() -> str:
    """Who records a decision: the same chain the publish and the builds use."""
    return (os.environ.get("STAF_LIBRARY_MAINTAINER")
            or os.environ.get("USERNAME") or os.environ.get("USER") or "").strip()


def function_names_by_id() -> dict:
    return {str(f.get("id")): str(f.get("name") or f.get("id")) for f in deep_read_staf_crosswalk()}


def kind_class(kind) -> str:
    return "src-" + re.sub(r"[^a-z0-9]+", "-", str(kind or "").lower()).strip("-")


def kind_badge(kind, label: Optional[str] = None):
    """The source badge: the kind's icon and its label (or ``label``)."""
    icon = src.kind_icon(kind)
    return ui.tags.span(fa(icon) if icon else None,
                        ui.tags.span(str(label or src.kind_label(kind))),
                        class_=f"source-badge {kind_class(kind)}")


def display_name(metric: str, entry: Mapping) -> str:
    name = (entry.get("config") or {}).get("display_name")
    if metric_names.is_placeholder_name(name, metric):
        name = metric_names.display_name_for(metric, name)
    return str(name or metric)


def function_names(entry: Mapping) -> list[str]:
    """The functions the curve is placed in, primary first."""
    out: list[str] = []
    for r in entry.get("mapping") or []:
        label = str(r.get("function_label") or "").strip()
        if label and label not in out:
            out.append(label)
    return out


def _section(title: str, *body):
    return ui.tags.section(ui.tags.h6(title, class_="source-panel-h"), *body,
                           class_="source-panel-section")


def breakpoint_value(x: float) -> str:
    """A breakpoint to four significant figures (0.5815, 1.069, 219.5), so the
    table agrees with the thresholds the facts quote."""
    return f"{x:.1f}" if abs(x) >= 1000 else f"{x:.4g}"


def _breakpoint_table(points, units: str):
    head = f"Value ({units})" if units else "Value"
    return ui.div(ui.tags.table(
        ui.tags.tbody(
            ui.tags.tr(ui.tags.th(head), *[ui.tags.td(breakpoint_value(x)) for x, _ in points]),
            ui.tags.tr(ui.tags.th("Score"), *[ui.tags.td(f"{y:.2f}") for _, y in points])),
        class_="table table-sm source-breakpoints"), class_="source-breakpoints-wrap")


def _trail(steps):
    items = []
    for s in steps:
        verdict = str(s.get("verdict") or "")
        items.append(ui.tags.li(
            ui.tags.span(str(s.get("step") or ""), class_="source-trail-step"),
            ui.tags.span(verdict, class_="source-trail-verdict is-"
                         + re.sub(r"[^a-z]+", "-", verdict.lower()).strip("-")),
            ui.tags.span(str(s.get("why") or ""), class_="source-trail-why")
            if s.get("why") else None))
    return ui.tags.ol(*items, class_="source-trail")


def panel_title(metric: str, entry: Mapping):
    return ui.div(
        ui.div(ui.tags.span(display_name(metric, entry), class_="source-panel-name"),
               ui.tags.code(metric, class_="source-panel-code"), class_="source-panel-id"),
        kind_badge(entry.get("kind"), entry.get("label")),
        class_="source-panel-head")


def _removed_banner(decision: Mapping):
    return ui.div(
        ui.tags.strong("Removed from this assessment. "),
        f"{decision.get('recordedBy')}, {str(decision.get('recordedAt') or '')[:10]}: "
        f"{decision.get('rationale')}",
        ui.tags.button(fa("rotate-left"), " Undo", type="button",
                       class_="btn btn-link btn-sm p-0 ms-2",
                       onclick=undo_onclick(decision.get("id"), stop=False)),
        class_="alert alert-secondary py-2 small source-panel-removed")


def _functions_block(metric: str, entry: Mapping, decisions) -> list:
    """The functions the curve scores, each with its remove control, then the ones
    the owner took it out of, each with its undo."""
    removed = oc.removed(decisions).get(str(metric))
    chips = []
    for r in entry.get("mapping") or []:
        label = str(r.get("function_label") or "").strip()
        fid = pe.canonical_function_id(label)
        if not label:
            continue
        chips.append(ui.tags.span(
            label,
            (ui.tags.button(fa("xmark"), type="button", class_="source-fn-x",
                            title=f"Remove from {label}",
                            onclick=act_onclick(metric, oc.UNMAP, [fid]))
             if fid and not removed else None),
            class_="source-fn-chip"))
    names = function_names_by_id()
    for d in oc.decisions_for(metric, decisions):
        if d.get("action") != oc.UNMAP:
            continue
        for fid in d.get("functions") or []:
            chips.append(ui.tags.span(
                names.get(str(fid), str(fid)),
                ui.tags.button(fa("rotate-left"), type="button", class_="source-fn-x",
                               title="Undo: score this function again",
                               onclick=undo_onclick(d.get("id"))),
                class_="source-fn-chip is-owner-removed",
                title=f"Removed by {d.get('recordedBy')}: {d.get('rationale')}"))
    return chips or [ui.tags.span("Not used in any function in this version.",
                                  class_="text-muted")]


def panel_body(metric: str, entry: Mapping, *, provenance: Optional[Mapping] = None,
               build: Optional[Mapping] = None, decisions=()):
    """Everything the panel shows about one curve, and the owner's decisions on it."""
    kind = entry.get("kind")
    units = src.units_of(entry)
    tile = cs.reference_tile(metric, entry)
    svg = cs.tile_svg(tile, w=PANEL_W, h=PANEL_H, x_label=units or None, point_labels=True)
    rows = list(src.source_facts(metric, entry, provenance=provenance, build=build))
    chosen = src.chosen_by(metric, entry)
    if chosen:
        rows.append(("Chosen by", chosen))
    if entry.get("owner"):
        rows.append(("Why", str(entry["owner"].get("rationale") or "")))
    facts = ui.tags.dl(*[t for label, value in rows
                         for t in (ui.tags.dt(label), ui.tags.dd(value))],
                       class_="source-panel-facts")
    removed = oc.removed(decisions).get(str(metric))
    parts = [
        _removed_banner(removed) if removed else None,
        ui.p(src.kind_sentence(kind), class_="source-panel-sentence"),
        ui.div(ui.div(ui.HTML(svg), class_="source-panel-curve"), facts,
               class_="source-panel-grid"),
    ]
    trail = src.build_trail(metric, entry, provenance=provenance, build=build)
    if trail:
        parts.append(_section("How the build got here", _trail(trail)))
    limits = src.limits(entry)
    if limits:
        parts.append(_section("Limits", ui.tags.ul(*[ui.tags.li(t) for t in limits],
                                                   class_="source-panel-limits")))
    points = src.breakpoints(entry)
    if points:
        parts.append(_section("Breakpoints", _breakpoint_table(points, units)))
    parts.append(_section("Functions", ui.div(*_functions_block(metric, entry, decisions),
                                              class_="source-panel-functions")))
    return ui.div(*[p for p in parts if p is not None], class_="source-panel")


def build_has_curve(metric: str, build: Optional[Mapping]) -> bool:
    """The build itself gave the metric a curve (carried, or from a source after
    the station pools), which a removal takes out and an undone choice gives back."""
    b = build or {}
    return str(metric) in (b.get("ladderMetrics") or {}) or str(metric) in (
        b.get("carriedMetrics") or {})


def panel_modal(metric: str, entry: Mapping, *, provenance: Optional[Mapping] = None,
                build: Optional[Mapping] = None, decisions=()):
    from views import source_dialog as sd
    removed = oc.removed(decisions).get(str(metric))
    owner = entry.get("owner")
    footer = [ui.modal_button("Close")]
    if not removed and entry.get("kind") != "fixed":
        footer.insert(0, ui.tags.button(
            fa("right-left"), " Change source", type="button",
            class_="btn btn-outline-primary",
            onclick=sd.open_onclick(metric=metric, stop=False)))
    if owner:
        footer.insert(0, ui.tags.button(
            fa("rotate-left"), " Undo this choice", type="button",
            class_="btn btn-outline-secondary",
            title=("The build's own curve for this metric returns" if build_has_curve(
                metric, build) else "The metric goes back to being withheld"),
            onclick=undo_onclick(owner.get("id"), stop=False)))
    if not removed and (not owner or build_has_curve(metric, build)):
        footer.insert(0, ui.tags.button(
            fa("trash-can"), " Remove from assessment", type="button",
            class_="btn btn-outline-danger me-auto", onclick=act_onclick(metric, oc.REMOVE)))
    elif owner:
        footer[0].attrs["class"] = footer[0].attrs["class"] + " me-auto"
    return ui.modal(
        panel_body(metric, entry, provenance=provenance, build=build, decisions=decisions),
        title=panel_title(metric, entry),
        footer=ui.TagList(*footer),
        size="l", easy_close=True)


def entry_for(state: AppState, metric: str) -> Optional[dict]:
    """The metric's ``reference_rows`` entry in the open session under the owner's
    decisions, or, for a curve the owner removed, as the build placed it."""
    with reactive.isolate():
        build = state.reference_build()
        decisions = state.owner_curve_decisions() or []
        mapping = state.discipline_function_mapping()
        built = state.completed_metrics() or {}
    effective = oc.effective_build(build, decisions, built=built)
    entry = pe.reference_rows(effective, mapping, built=built).get(str(metric))
    if entry is None and str(metric) in oc.removed(decisions):
        entry = pe.reference_rows(build, mapping, built=built).get(str(metric))
    return entry


def function_counts(state: AppState) -> dict:
    """How many curves score each function now, under the owner's decisions."""
    with reactive.isolate():
        completed = state.completed_metrics() or {}
        review = state.curve_review() or {}
        mapping = state.discipline_function_mapping()
        effective = ap.effective_reference_build(state)
    in_scope = {mk: cm for mk, cm in completed.items()
                if mk not in review or rs.is_in_scope(review[mk])}
    return metrics_per_function_quick(
        in_scope, mapping, extra=pe.reference_metric_counts(effective, mapping, built=completed),
        exclude_pairs=pe.not_selected_pairs(effective))


def decision_form(metric: str, name: str, action: str, functions: list, emptied: list,
                  *, ns, others: list, chosen: bool = False):
    """The one form behind every decision: what it does, why, and the gap it would
    leave. Back returns to the panel."""
    names = function_names_by_id()
    fn_text = ", ".join(names.get(f, f) for f in functions)
    if action == oc.REMOVE:
        title = f"Remove {name} from this assessment"
        what = (f"{name} stops scoring {fn_text or 'any function'}. Every later build of this "
                "region leaves it out too, and nothing takes its place unless you choose a "
                "curve." + (" This also withdraws your choice of its source."
                            if chosen else ""))
        verb, cls = "Remove", "btn btn-danger"
    elif action == oc.UNMAP:
        title = f"Remove {name} from {fn_text}"
        keep = ", ".join(names.get(f, f) for f in others)
        what = (f"{name} stops scoring {fn_text}"
                + (f" and keeps scoring {keep}." if keep else ".")
                + " Every later build of this region does the same.")
        verb, cls = "Remove", "btn btn-danger"
    else:
        title = f"Use {name} in {fn_text}"
        what = (f"The two-per-function rule left {name} out of {fn_text}. Using it records your "
                "decision, and every later build of this region uses it there too.")
        verb, cls = f"Use in {fn_text}", "btn btn-primary"
    body = [ui.p(what),
            ui.input_text_area(ns("dec_rationale"), "Why", rows=3, width="100%",
                               placeholder="What you know that the build did not"),
            ui.div(f"At least {oc.min_rationale()} characters. Recorded under your name.",
                   class_="text-muted small mb-2")]
    for i, fid in enumerate(emptied):
        body.append(ui.div(
            ui.tags.strong(f"{names.get(fid, fid)} would have no curve left."),
            ui.div("Say why it is left unassessed. The version records it as a documented "
                   "gap, and undoing this decision undoes the gap.", class_="small mb-1"),
            ui.input_text_area(ns(f"dec_gap_{i}"), None, rows=2, width="100%",
                               placeholder="Why this function carries no curve"),
            class_="source-decision-gap"))
    # a curve from another source goes back to its panel; a fitted one has none
    back = (ui.modal_button("Cancel") if action == oc.INCLUDE else
            ui.tags.button("Back", type="button", class_="btn btn-outline-secondary",
                           onclick=open_onclick(metric)))
    return ui.modal(
        *body, title=title,
        footer=ui.TagList(back, ui.input_action_button(ns("dec_confirm"), verb, class_=cls)),
        size="m", easy_close=True)


@module.server
def source_panel_server(input, output, session, state: AppState):
    @reactive.effect
    @reactive.event(input.open)
    @guard("open the curve source")
    def _open():
        metric = str((input.open() or {}).get("metric") or "")
        if not metric:
            return
        entry = entry_for(state, metric)
        if entry is None:
            ui.notification_show(f"{metric} has no curve from another source in this version.",
                                 type="warning", duration=5)
            return
        with reactive.isolate():
            provenance = state.source_provenance()
            build = state.reference_build()
            decisions = state.owner_curve_decisions() or []
        ui.modal_show(panel_modal(metric, entry, provenance=provenance, build=build,
                                  decisions=decisions))

    pending = reactive.value(None)

    def _region_dir():
        with reactive.isolate():
            return rb.region_run_dir(state.region_of_applicability())

    def _standing(run_dir) -> None:
        """Seed the region's record from its published version before the first
        save or undo here, so the published decisions reach the next build."""
        with reactive.isolate():
            code = (state.region_of_applicability() or {}).get("code")
        rb.standing_decisions(run_dir, code)

    @reactive.effect
    @reactive.event(input.act)
    @guard("start the curve decision")
    def _act():
        p = input.act() or {}
        metric, action = str(p.get("metric") or ""), str(p.get("action") or "")
        functions = [str(f) for f in p.get("functions") or []]
        if not metric or action not in (oc.REMOVE, oc.UNMAP, oc.INCLUDE):
            return
        if _region_dir() is None:
            ui.notification_show("Curve decisions are recorded for an ecoregion assessment.",
                                 type="warning", duration=6)
            return
        entry = entry_for(state, metric)
        with reactive.isolate():
            mc = state.metric_config() or {}
        name = (display_name(metric, entry) if entry else
                metric_names.display_name_for(metric, (mc.get(metric) or {}).get("display_name"))
                or metric)
        placed = [pe.canonical_function_id(r.get("function_label"))
                  for r in (entry or {}).get("mapping") or []]
        placed = [f for f in placed if f]
        affected = placed if action == oc.REMOVE else functions if action == oc.UNMAP else []
        counts = function_counts(state)
        emptied = [f for f in affected if int(counts.get(f, 0) or 0) <= 1]
        pending.set({"metric": metric, "action": action,
                     "functions": functions if action != oc.REMOVE else [],
                     "emptied": emptied})
        with reactive.isolate():
            chosen = metric in oc.sourced(state.owner_curve_decisions() or [])
        ui.modal_show(decision_form(metric, name, action, affected or functions, emptied,
                                    ns=session.ns,
                                    others=[f for f in placed if f not in functions],
                                    chosen=chosen))

    @reactive.effect
    @reactive.event(input.dec_confirm)
    @guard("record the curve decision")
    def _confirm():
        p = pending()
        run_dir = _region_dir()
        if not p or run_dir is None:
            return
        gaps = []
        for i, fid in enumerate(p["emptied"]):
            try:
                text = input[f"dec_gap_{i}"]()
            except Exception:  # noqa: BLE001 - the field is absent when nothing empties
                text = ""
            gaps.append({"functionId": fid, "reason": oc.GAP_REASON, "justification": text})
        with reactive.isolate():
            build = state.reference_build()
            built = state.completed_metrics() or {}
            current = list(state.owner_curve_decisions() or [])
        try:
            decision = oc.new_decision(p["metric"], p["action"],
                                       rationale=input.dec_rationale() or "",
                                       recorded_by=maintainer(), functions=p["functions"],
                                       coverage_exceptions=gaps)
            oc.validate(decision, build=build, built=built, decisions=current)
            _standing(run_dir)
            oc.save(run_dir, decision)
        except ValueError as exc:
            ui.notification_show(str(exc), type="warning", duration=8)
            return
        state.owner_curve_decisions.set(oc.merge(current, decision))
        pending.set(None)
        ui.modal_remove()
        ui.notification_show(
            f"Saved: {oc.ACTION_LABELS[decision['action']].lower()}, {decision['metric']}. "
            "It applies here now and to every later build of this region.",
            type="message", duration=6)

    @reactive.effect
    @reactive.event(input.undo)
    @guard("undo the curve decision")
    def _undo():
        did = str((input.undo() or {}).get("decision") or "")
        if not did:
            return
        with reactive.isolate():
            current = list(state.owner_curve_decisions() or [])
            gaps = list(state.function_coverage_exceptions() or [])
            held = (state.reference_build() or {}).get("ownerHeld") or {}
        undone = next((d for d in current if d.get("id") == did), {})
        run_dir = _region_dir()
        if run_dir is not None:
            _standing(run_dir)
        oc.undo(run_dir, did)
        remaining = [d for d in current if d.get("id") != did]
        state.owner_curve_decisions.set(remaining)
        # a gap recorded with the decision goes with it
        kept = oc.live_exceptions(gaps, remaining)
        if kept != gaps:
            state.function_coverage_exceptions.set(kept)
        ui.modal_remove()
        if str(undone.get("metric")) in held:
            # "your choice stands": this build kept the metric out of its fit
            ui.notification_show(
                "Undone. This build held the metric out of its fit for your decision, so it "
                "has no curve here now. Build the region again to fit it.",
                type="message", duration=9)
            return
        ui.notification_show("Undone. The curve reads as the build made it.",
                             type="message", duration=5)


__all__ = ["PANEL_ID", "OPEN_INPUT", "ACT_INPUT", "UNDO_INPUT", "open_onclick",
           "open_onkeydown", "act_onclick", "undo_onclick", "maintainer", "kind_class",
           "build_has_curve",
           "kind_badge", "display_name", "function_names", "panel_title", "panel_body",
           "panel_modal", "entry_for", "function_counts", "decision_form",
           "source_panel_server"]
