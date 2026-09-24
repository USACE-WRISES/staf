"""Choose where a curve comes from (REF-15): the source dialog.

Opened for a function (Function mapping's "Add a source", the Table card) or for
a metric (the source panel's "Change source"), through one input any page sets
(``OPEN_INPUT``). The owner picks the metric, then its source: the verified
catalog, an earlier version of this assessment, another assessment's curve, or a
curve entered here, by two thresholds or point by point. The choice is recorded
as the owner's decision with its rationale (``owner_curves``); it applies at
once, and every later build of the region applies it too.

What the sources are, and how each becomes a curve, is ``owner_sources``'s. This
module is the dialog around it.
"""
from __future__ import annotations

import json
from typing import Mapping, Optional

from shiny import module, reactive, render, ui

from streamcurves import curve_sources as src
from streamcurves import curve_svg as cs
from streamcurves import metric_names
from streamcurves import owner_curves as oc
from streamcurves import owner_sources as osrc
from streamcurves import pressure_evidence as pe
from streamcurves import region_build as rb
from views import assessment_publish as ap
from views import source_panel as sp
from views.state import AppState
from views.theme import fa
from views.uihelpers import guard

DIALOG_ID = "source_dialog"
#: the one input every page sets to open the dialog
OPEN_INPUT = f"{DIALOG_ID}-open"
ENTERED_KEY = "entered"
PREVIEW_W, PREVIEW_H = 300, 184


def open_onclick(*, metric: Optional[str] = None, function: Optional[str] = None,
                 stop: bool = True) -> str:
    """The onclick that opens the dialog: for a function (pick a metric), for a
    metric (change its source), or for neither (pick a function first)."""
    payload = json.dumps({"metric": str(metric or ""), "function": str(function or "")})
    payload = payload.replace(chr(39), chr(92) + chr(39))
    return (("event.stopPropagation();" if stop else "")
            + f"Shiny.setInputValue('{OPEN_INPUT}',{payload},{{priority:'event'}})")


# --------------------------------------------------------------------------- #
# what the session holds
# --------------------------------------------------------------------------- #
def session_view(state: AppState) -> dict:
    """The session's curves as the dialog reads them, under the owner's decisions."""
    with reactive.isolate():
        build = state.reference_build()
        decisions = list(state.owner_curve_decisions() or [])
        mapping = state.discipline_function_mapping()
        built = state.completed_metrics() or {}
        mc = state.metric_config() or {}
        region = state.region_of_applicability() or {}
        effective = ap.effective_reference_build(state)
        provenance = state.source_provenance()
    return {"build": build, "decisions": decisions, "mapping": mapping, "built": set(built),
            "metric_config": mc, "region": region, "effective": effective,
            "provenance": provenance,
            "entries": pe.reference_rows(effective, mapping, built=built),
            "not_selected": pe.not_selected_pairs(effective),
            "withheld": {str(w.get("metricKey")) for w in
                         (effective or {}).get("insufficientReferenceSupport") or []}}


def metric_name(metric: str, view: Mapping) -> str:
    entry = view["entries"].get(metric)
    if entry:
        return sp.display_name(metric, entry)
    cfg = view["metric_config"].get(metric) or {}
    name = metric_names.display_name_for(metric, cfg.get("display_name"))
    return str(name or metric)


def metric_state(metric: str, function_id: Optional[str], view: Mapping) -> tuple[str, bool]:
    """``(state words, sourceable)`` of a metric for the dialog's metric list."""
    mk = str(metric)
    if mk in view["built"]:
        left_out = function_id and (mk, str(function_id)) in view["not_selected"]
        return ("built here, not selected here" if left_out else "built here"), False
    if osrc.sourceable(mk) is not None:
        return "fixed criterion", False
    entry = view["entries"].get(mk)
    if entry:
        here = bool(function_id) and str(function_id) in (entry.get("functions") or [])
        label = str(entry.get("label") or src.kind_label(entry.get("kind")))
        # lower-case the label's first word only, so a place keeps its name
        label = label[:1].lower() + label[1:]
        if entry.get("owner") and entry.get("kind") not in ("owner_entered", "owner_exception"):
            label = f"{label}, chosen by the owner"
        return (f"{label}, scores this function" if here else label), True
    if mk in view["withheld"]:
        return "withheld, no reference support", True
    return "not in this version", True


def function_metrics(function_id: str, view: Mapping) -> list[tuple[str, str]]:
    """``[(metric, label)]`` the owner can choose a source for in a function: the
    crosswalk's candidates and the curves from another source placed there, each
    with its state, scorable ones only."""
    keys = list(osrc.function_candidates(function_id))
    for mk, entry in view["entries"].items():
        if str(function_id) in (entry.get("functions") or []) and mk not in keys:
            keys.append(mk)
    out = []
    for mk in keys:
        state_words, ok = metric_state(mk, function_id, view)
        if not ok:
            continue
        if mk not in view["entries"] and mk not in view["withheld"] and not osrc.config_for(
                mk, metric_config=view["metric_config"], build=view["build"]):
            continue                        # a pressure or a predictor: never scored
        out.append((mk, f"{metric_name(mk, view)} ({state_words})"))
    return out


def not_sourceable_note(function_id: str, view: Mapping) -> Optional[str]:
    """The function's candidates the dialog cannot re-source, named once."""
    names = []
    for mk in osrc.function_candidates(function_id):
        state_words, ok = metric_state(mk, function_id, view)
        if not ok and (mk in view["built"] or osrc.sourceable(mk) is not None):
            names.append(f"{metric_name(mk, view)} ({state_words})")
    if not names:
        return None
    return ("Not listed: " + "; ".join(names) + ". A curve built here is edited in its "
            "analysis, and a fixed criterion is the same in every region.")


def scoring_functions(metric: str, function_id: Optional[str], view: Mapping) -> list[str]:
    """Where the chosen curve will score: where the metric's curve scores now, and
    the function the dialog was opened for."""
    entry = view["entries"].get(str(metric))
    fids = list((entry or {}).get("functions") or [])
    if function_id and str(function_id) not in fids:
        fids.append(str(function_id))
    return fids or osrc.crosswalk_functions(metric)


def current_points(metric: str, view: Mapping) -> list[dict]:
    entry = view["entries"].get(str(metric))
    return [{"x": x, "y": y} for x, y in src.breakpoints(entry)] if entry else []


def pool(metric: str, view: Mapping) -> list[dict]:
    region = view["region"] or {}
    # the refusals are the build's own, so they are read from the build's record
    return osrc.pool_for(metric, region_code=str(region.get("code") or ""),
                         region_name=region.get("name"), build=view["build"],
                         provenance=view.get("provenance"),
                         current=current_points(metric, view))


# --------------------------------------------------------------------------- #
# the pieces of the dialog
# --------------------------------------------------------------------------- #
def preview_svg(metric: str, points: list, config: Mapping, *, layers=()) -> str:
    """The option's curve, drawn like the source panel's."""
    row = {"metric": metric, "display_name": config.get("display_name") or metric,
           "curve_status": "complete",
           "curve_points": [{"point_order": i + 1, "metric_value": p["x"], "index_score": p["y"]}
                            for i, p in enumerate(points or [])]}
    if layers:
        row["all_strata"] = [{"stratum": L.get("stratum") or "", "curve_points": [
            {"point_order": i + 1, "metric_value": p["x"], "index_score": p["y"]}
            for i, p in enumerate(L.get("points") or [])]} for L in layers]
    tile = cs.reference_tile(metric, {"row": row, "config": config, "annotations": {},
                                      "kind": None, "label": None, "in_bundle": True})
    units = src.units_of({"config": config})
    return cs.tile_svg(tile, w=PREVIEW_W, h=PREVIEW_H, x_label=units or None, point_labels=True)


def option_label(opt: Mapping):
    return ui.div(ui.div(str(opt.get("title") or ""), class_="source-option-title"),
                  ui.div(str(opt.get("detail") or ""), class_="source-option-detail"),
                  class_="source-option")


def sources_list(options: list[dict], *, ns, selected: Optional[str] = None):
    """The radio list of every source the owner can choose, grouped, then the ones
    not available here with their reason."""
    available = [o for o in options if o["available"]]
    unavailable = [o for o in options if not o["available"]]
    choices: dict = {}
    for o in available:
        if o["kind"] != osrc.REFUSED:
            choices[o["key"]] = option_label(o)
    choices[ENTERED_KEY] = option_label({"title": "Enter a curve",
                                         "detail": "Two thresholds, or breakpoints point by point, "
                                                   "cited or on professional judgment"})
    # a source the build refused, last: the owner's exception, computed at the
    # next build with every check it fails recorded
    for o in available:
        if o["kind"] == osrc.REFUSED:
            choices[o["key"]] = option_label(o)
    first = selected if selected in choices else next(iter(choices))
    parts = [ui.input_radio_buttons(ns("choice"), None, choices=choices, selected=first,
                                    width="100%")]
    if unavailable:
        parts.append(ui.div(
            ui.div("Not available here", class_="source-dialog-h"),
            ui.tags.ul(*[ui.tags.li(ui.tags.strong(str(o["title"])), " ",
                                    ui.tags.span(str(o.get("detail") or ""), class_="text-muted"),
                                    ui.div(str(o.get("why_not") or ""),
                                           class_="source-option-why"))
                         for o in unavailable], class_="source-unavailable"),
            class_="source-dialog-unavailable"))
    return ui.div(*parts, class_="source-dialog-sources")


def refused_detail(opt: Mapping):
    """A source the build refused: what the build found, and what accepting it does."""
    facts = [("Source", opt.get("title")), ("What it is", opt.get("detail")),
             ("The build found", opt.get("refusal"))]
    return ui.div(
        ui.tags.dl(*[t for label, value in facts if value
                     for t in (ui.tags.dt(label), ui.tags.dd(str(value)))],
                   class_="source-panel-facts"),
        ui.div(fa("clock-rotate-left"), " ", osrc.REFUSAL_NOTE,
               class_="source-dialog-preview-note mt-2"))


def option_detail(metric: str, opt: Mapping, config: Mapping):
    """The chosen option's curve and what it states."""
    if opt.get("kind") == osrc.REFUSED:
        return refused_detail(opt)
    facts = [("Source", opt.get("title")), ("What it is", opt.get("detail")),
             ("Citation", opt.get("citation"))]
    ann = opt.get("annotations") or {}
    bands = (ann.get("criteriaSource") or {}).get("bands") or []
    if bands:
        facts.append(("Thresholds", "; ".join(f"{b.get('rating')} {b.get('label')}"
                                              for b in bands if b.get("label"))))
    limit = ann.get("basisLimit")
    if limit:
        facts.append(("Limit", limit))
    return ui.div(
        ui.div(ui.HTML(preview_svg(metric, opt.get("points") or [], config,
                                   layers=opt.get("layers") or [])),
               class_="source-panel-curve"),
        ui.tags.dl(*[t for label, value in facts if value
                     for t in (ui.tags.dt(label), ui.tags.dd(str(value)))],
                   class_="source-panel-facts"),
        class_="source-panel-grid")


def entered_form(config: Mapping, *, ns):
    """The owner's own curve: two thresholds or breakpoints, a title and a citation."""
    optimum = osrc.two_sided(config)
    units = src.units_of({"config": config})
    hib = config.get("higher_is_better")
    unit_txt = f" ({units})" if units else ""
    if optimum:
        methods = {osrc.BREAKPOINTS: "Point by point"}
        direction = "This metric has an optimum, so its curve is entered point by point."
    else:
        methods = {osrc.THRESHOLDS: "Two thresholds", osrc.BREAKPOINTS: "Point by point"}
        direction = ("Higher values are better for this metric." if hib
                     else "Lower values are better for this metric.")
    good_label = ("Good at or above" if hib else "Good at or below") + unit_txt
    poor_label = ("Poor below" if hib else "Poor above") + unit_txt
    return ui.div(
        ui.input_radio_buttons(ns("ent_method"), None, choices=methods, inline=True,
                               selected=next(iter(methods))),
        ui.div(direction, class_="text-muted small mb-2"),
        # the condition names the input without its namespace: a conditional
        # panel built in a module prefixes it (data-ns-prefix)
        ui.panel_conditional(
            f"input.ent_method === '{osrc.THRESHOLDS}'",
            ui.layout_columns(
                ui.input_numeric(ns("ent_good"), good_label, value=None),
                ui.input_numeric(ns("ent_poor"), poor_label, value=None),
                col_widths=[6, 6]),
            ui.div("The Good threshold scores 0.69 and the Poor one 0.39, the way every "
                   "published criterion is drawn.", class_="text-muted small mb-2")),
        ui.panel_conditional(
            f"input.ent_method === '{osrc.BREAKPOINTS}'",
            ui.input_text_area(ns("ent_points"), f"Breakpoints: value{unit_txt}, score",
                               rows=5, width="100%",
                               placeholder="One per line, for example\n0, 0\n10, 0.4\n25, 0.7\n40, 1"),
            ui.div("Scores run from 0 to 1 and must cross 0.3 and 0.7.",
                   class_="text-muted small mb-2")),
        ui.layout_columns(
            ui.input_text(ns("ent_title"), "Title", placeholder="What the curve states",
                          width="100%"),
            ui.input_text(ns("ent_citation"), "Citation (optional)",
                          placeholder="Leave blank for professional judgment", width="100%"),
            col_widths=[6, 6]),
        ui.output_ui(ns("ent_preview")),
        class_="source-dialog-entered")


def dialog_modal(*, ns, title: str, head, has_function_pick: bool, functions: dict,
                 function_id: Optional[str]):
    """The dialog's frame: what it is about, the source list, the detail and why."""
    pick = []
    if has_function_pick:
        pick.append(ui.input_select(ns("function"), "Function", functions,
                                    selected=function_id, width="100%"))
    return ui.modal(
        ui.div(
            head,
            *pick,
            ui.output_ui(ns("metric_pick")),
            ui.div(ui.div(ui.div("Source", class_="source-dialog-h"),
                          ui.output_ui(ns("sources")), class_="source-dialog-left"),
                   ui.div(ui.output_ui(ns("detail")), class_="source-dialog-right"),
                   class_="source-dialog-grid"),
            ui.output_ui(ns("scores")),
            ui.input_text_area(ns("rationale"), "Why", rows=2, width="100%",
                               placeholder="What makes this source right for this ecoregion"),
            ui.div(f"At least {oc.min_rationale()} characters. Recorded under your name, and "
                   "applied by every later build of this region.",
                   class_="text-muted small"),
            class_="source-dialog"),
        title=title,
        footer=ui.TagList(ui.modal_button("Cancel"),
                          ui.input_action_button(ns("confirm"), "Use this source",
                                                 class_="btn btn-primary")),
        size="l", easy_close=False)


@module.server
def source_dialog_server(input, output, session, state: AppState):
    ns = session.ns
    ctx = reactive.value(None)
    cache: dict = {}

    def _choices(fid: str) -> dict:
        """The metrics the function offers, cached for the dialog's life."""
        key = ("function", fid)
        if key not in cache:
            cache[key] = dict(function_metrics(fid, ctx()["view"]))
        return cache[key]

    def _metric() -> Optional[str]:
        c = ctx()
        if not c:
            return None
        if c.get("metric"):
            return c["metric"]
        try:
            picked = str(input.metric() or "") or None
        except Exception:  # noqa: BLE001 - the select is not rendered yet
            return None
        # a select no longer on screen keeps its last value: only a metric the
        # chosen function offers now counts, so nothing is saved into the wrong one
        fid = _function()
        return picked if picked and fid and picked in _choices(fid) else None

    def _function() -> Optional[str]:
        c = ctx()
        if not c:
            return None
        if c.get("function") or not c.get("pick_function"):
            return c.get("function") or None
        try:
            return str(input.function() or "") or None
        except Exception:  # noqa: BLE001
            return None

    def _pool(metric: str) -> list[dict]:
        if metric not in cache:
            cache[metric] = pool(metric, ctx()["view"])
        return cache[metric]

    def _config(metric: str) -> dict:
        view = ctx()["view"]
        return osrc.config_for(metric, metric_config=view["metric_config"], build=view["build"])

    @reactive.effect
    @reactive.event(input.open)
    @guard("open the source dialog")
    def _open():
        p = input.open() or {}
        metric, function_id = str(p.get("metric") or ""), str(p.get("function") or "")
        with reactive.isolate():
            region = state.region_of_applicability()
        if not rb.is_ecoregion(region):
            ui.notification_show("A curve's source is chosen for an ecoregion assessment.",
                                 type="warning", duration=6)
            return
        view = session_view(state)
        if not view["build"] or view["build"].get("method") != pe.METHOD:
            ui.notification_show("Sources are chosen in a pressure-screen assessment.",
                                 type="warning", duration=6)
            return
        names = sp.function_names_by_id()
        if metric:
            why = osrc.sourceable(metric, built=view["built"])
            if why:
                ui.notification_show(why, type="warning", duration=6)
                return
            name = metric_name(metric, view)
            title = f"Choose the source of {name}"
            head = ui.div(ui.tags.span(name, class_="source-panel-name"),
                          ui.tags.code(metric, class_="source-panel-code ms-2"),
                          ui.div(metric_state(metric, None, view)[0].capitalize(),
                                 class_="text-muted small"),
                          class_="source-dialog-head")
        elif function_id:
            title = f"Add a source to {names.get(function_id, function_id)}"
            head = None
        else:
            title, head = "Add a source", None
        cache.clear()
        # a function with no curve first, where a source is most often wanted
        counts = _counts(view)
        order = sorted(names, key=lambda f: bool(counts.get(f)))
        functions = {f: names[f] + ("" if counts.get(f) else " (no curve)") for f in order}
        ctx.set({"metric": metric or None, "function": function_id or None,
                 "pick_function": not metric and not function_id, "view": view})
        ui.modal_show(dialog_modal(ns=ns, title=title, head=head,
                                   has_function_pick=not metric and not function_id,
                                   functions=functions,
                                   function_id=function_id or (order[0] if order else None)))

    def _counts(view) -> dict:
        """How many curves score each function now, under the owner's decisions."""
        return sp.function_counts(state)

    @render.ui
    def metric_pick():
        c = ctx()
        if not c or c.get("metric"):
            return None
        fid = _function()
        if not fid:
            return None
        choices = _choices(fid)
        note = not_sourceable_note(fid, c["view"])
        if not choices:
            return ui.div("No metric of this function can take a source here. "
                          + (note or "Its candidates in the crosswalk are not scored as "
                                     "curves in STAF."),
                          class_="alert alert-secondary py-2 small")
        return ui.div(
            ui.input_select(ns("metric"), "Metric", choices, width="100%"),
            ui.div(note, class_="text-muted small mb-2") if note else None)

    @render.ui
    def sources():
        metric = _metric()
        if not metric:
            return None
        return sources_list(_pool(metric), ns=ns)

    @render.ui
    def detail():
        metric = _metric()
        if not metric:
            return None
        try:
            choice = str(input.choice() or "")
        except Exception:  # noqa: BLE001 - the list is not rendered yet
            return None
        config = _config(metric)
        if choice == ENTERED_KEY:
            return entered_form(config, ns=ns)
        opt = next((o for o in _pool(metric) if o["key"] == choice), None)
        return option_detail(metric, opt, config) if opt else None

    def _entered(metric: str) -> dict:
        return osrc.entered_option(
            metric, method=str(input.ent_method() or osrc.BREAKPOINTS), config=_config(metric),
            title=input.ent_title() or "", citation=input.ent_citation() or "",
            good=input.ent_good(), poor=input.ent_poor(), points_text=input.ent_points() or "")

    @render.ui
    def ent_preview():
        metric = _metric()
        if not metric:
            return None
        try:
            opt = _entered(metric)
        except Exception:  # noqa: BLE001 - the form is not rendered yet
            return None
        errors = [e for e in opt["errors"] if not e.startswith("Give the curve a title")]
        if errors or not opt["points"]:
            return ui.div(*[ui.div(e) for e in errors] or ["The curve appears here."],
                          class_="source-dialog-preview-note")
        warning = osrc.direction_warning(opt["points"], _config(metric))
        return ui.div(ui.div(ui.HTML(preview_svg(metric, opt["points"], _config(metric))),
                             class_="source-panel-curve source-dialog-preview"),
                      ui.div(fa("triangle-exclamation"), " ", warning,
                             class_="source-dialog-preview-note text-warning-emphasis")
                      if warning else None)

    @render.ui
    def scores():
        metric = _metric()
        if not metric:
            return None
        names = sp.function_names_by_id()
        fids = scoring_functions(metric, _function() if not ctx()["metric"] else None,
                                 ctx()["view"])
        return ui.div(fa("layer-group"), " Scores ",
                      ", ".join(names.get(f, f) for f in fids) or "no function",
                      class_="source-dialog-scores")

    @reactive.effect
    @reactive.event(input.confirm)
    @guard("record the source")
    def _confirm():
        c = ctx()
        metric = _metric()
        if not c or not metric:
            ui.notification_show("Choose a metric.", type="warning", duration=5)
            return
        with reactive.isolate():
            region = state.region_of_applicability()
            choice = str(input.choice() or "")
        run_dir = rb.region_run_dir(region)
        config = _config(metric)
        if choice == ENTERED_KEY:
            with reactive.isolate():
                opt = _entered(metric)
            if opt["errors"]:
                ui.notification_show(" ".join(opt["errors"]), type="warning", duration=8)
                return
        else:
            opt = next((o for o in _pool(metric) if o["key"] == choice and o["available"]), None)
            if opt is None:
                ui.notification_show("Choose a source.", type="warning", duration=5)
                return
        view = c["view"]
        functions = scoring_functions(metric, _function() if not c["metric"] else None, view)
        with reactive.isolate():
            current = list(state.owner_curve_decisions() or [])
            rationale = input.rationale() or ""
        try:
            decision = oc.new_decision(metric, oc.SOURCE, rationale=rationale,
                                       recorded_by=sp.maintainer(), functions=functions,
                                       source=osrc.decision_source(metric, opt, config=config))
            from views import curve_gallery as _cg
            basis = _cg.metric_basis(state, metric, oc.merge(current, decision))
            if basis:
                decision["basisDigest"] = basis
            oc.validate(decision, build=view["build"], built=view["built"], decisions=current)
            if run_dir is not None:
                # the region's record starts from its published decisions (seeded once);
                # an installed copy has no region record and keeps the choice in the
                # project's session only
                rb.standing_decisions(run_dir, (region or {}).get("code"))
                oc.save(run_dir, decision)
        except ValueError as exc:
            ui.notification_show(str(exc), type="warning", duration=8)
            return
        state.owner_curve_decisions.set(oc.merge(current, decision))
        ctx.set(None)
        ui.modal_remove()
        if opt["kind"] == osrc.REFUSED:
            ui.notification_show(
                f"Saved: {opt['title']} for {metric_name(metric, view)}. The next build of this "
                "region computes it; build the region again in the Region builder.",
                type="message", duration=9)
            return
        ui.notification_show(
            f"Saved: {metric_name(metric, view)} now scores against {opt['title']}. It applies "
            + ("here now and to every later build of this region." if run_dir is not None
               else "in this project; the maintainer records it for the region when they "
                    "publish your revision."), type="message", duration=7)


__all__ = ["DIALOG_ID", "OPEN_INPUT", "ENTERED_KEY", "open_onclick", "session_view",
           "metric_name", "metric_state", "function_metrics", "not_sourceable_note",
           "scoring_functions", "current_points", "pool", "preview_svg", "option_label",
           "sources_list", "refused_detail", "option_detail", "entered_form", "dialog_modal",
           "source_dialog_server"]
