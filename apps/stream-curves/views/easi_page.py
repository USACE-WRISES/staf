"""EASI method projects: the page behind the five EASI stages.

An EASI project (``streamcurves.easi_method``) is the EASI screening method as files: a
catalog of 20 function methods, 34 reference curves and the tables beside them. This page
shows it in the StreamCurves shell, one stage at a time (the strip and the Project panel
switch ``state.easi_stage``; ``streamcurves.easi_method.stages`` names the stages):

* Method: the identity EASI reports (method version, package and evaluator digests),
  where this version came from, and the 20 functions with what each one computes;
* Development data: the reference screen the curves record, and any development data
  packages the project carries;
* Curves and criteria: the reference curves by family and stratum and every fixed band.
  In a draft revision, curve knots, band edges and regional nutrient edges are edited here,
  each change with a reason, one undo step each;
* Final selection: the method selected for each function; an analytical change flags its
  function until a person confirms the selection with a reason;
* Review and publish: what changed from the origin, the consequences on the project's
  preview cases (scored in worker processes, never in this process), the method package
  download, and (a maintainer's checkout) publishing the next library version.

Only a draft revision is ever edited: an import or a library version is the method as it
was. Every edit is a pure function over the project (``easi_method.edit``); the page swaps
the whole project and keeps the one it replaced for Undo. Nothing here writes EASI.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os

from shiny import module, reactive, render, ui

from streamcurves import curve_svg
from streamcurves import library as lib
from streamcurves import prefs
from streamcurves import run_state as rs
from streamcurves import workspace as ws
from streamcurves.easi_method import edit
from streamcurves.easi_method import evaluate
from streamcurves.easi_method import io as eio
from streamcurves.easi_method import register as reg
from streamcurves.easi_method import stages as es
from streamcurves.easi_method.model import parsed
from views import state as st
from views.state import AppState
from views.theme import fa
from views.uihelpers import guard

logger = logging.getLogger("streamcurves")

BREAKS = (0.39, 0.69)
TASK_KEY = "easi_preview"
UNDO_DEPTH = 50

OPERATOR_LABELS = {**es.OPERATOR_LABELS, "minimum": "Lowest of its inputs",
                   "minimum_of_products": "Lowest of paired products"}
RATING_CLASS = {"Good": "is-good", "Fair": "is-fair", "Poor": "is-poor"}
DECIDED_LABELS = {"imported": "Imported", "automated": "Automated", "person": "Person"}


# --------------------------------------------------------------------------- #
# small pure helpers
# --------------------------------------------------------------------------- #
def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def short(digest) -> str:
    d = str(digest or "")
    return d.split(":", 1)[1][:12] if ":" in d else d[:12]


def person() -> str:
    """Who is recorded as making a decision or an edit."""
    return (str(prefs.get(prefs.PREPARED_BY) or "").strip()
            or os.environ.get("STAF_LIBRARY_MAINTAINER", "").strip()
            or os.environ.get("USERNAME", "").strip() or os.environ.get("USER", "").strip())


def _evt(evt_id: str, **fields) -> str:
    extra = "".join(f"{k}: {int(v)}, " for k, v in fields.items())
    return (f"Shiny.setInputValue('{evt_id}', {{{extra}n: Date.now() + Math.random()}}, "
            "{priority: 'event'})")


def _btn(evt_id: str, label, cls: str = "btn btn-primary btn-sm", **attrs):
    return ui.tags.button(label, type="button", class_=cls, onclick=_evt(evt_id), **attrs)


def _fmt(v) -> str:
    """An authored number exactly as it scores (up to ten significant digits)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "" if v is None else str(v)
    return str(int(f)) if f.is_integer() and abs(f) < 1e12 else f"{f:.10g}"


def _short_num(v) -> str:
    """A reading (a crossing, a quartile): four significant digits."""
    try:
        return f"{float(v):.4g}"
    except (TypeError, ValueError):
        return "" if v is None else str(v)


def metric_meta(project) -> dict:
    return {m["metricId"]: m for m in project.metrics().get("metrics", [])}


def humanize(key: str) -> str:
    return (str(key or "").replace("-", " ").replace("_", " ").capitalize()
            .replace("Streamcat", "StreamCat"))


#: Slope classes in their natural order (the file keeps them in fit order).
SLOPE_ORDER = {"lt_0.5": 0, "0.5_to_2": 1, "ge_2": 2}

#: The reference screen's watershed variables, in words.
SCREEN_VARIABLES = {
    "agriculture_ws": "Agricultural cover in the watershed (%)",
    "dor": "Degree of regulation (%)",
    "mines_ws": "Mines in the watershed",
    "pctimp2019ws": "Impervious cover in the watershed, 2019 (%)",
    "rddensws": "Road density in the watershed (km/km\u00b2)",
    "sc__nabd_densws": "Dam density in the watershed (NABD)",
    "sc__npdesdensws": "NPDES permit density in the watershed",
    "fcode_class": "Flowline type",
    "wadeable": "Wadeable",
}


def curve_list(project) -> list[tuple[str, str]]:
    """Every reference curve as (set, stratum), families in file order, strata with the
    national fallback last."""
    out = []
    for name, s in (project.curves().get("sets") or {}).items():
        keys = [k for k in (s.get("curves") or {}) if k != "national"]
        if s.get("stratifier") == "slope_class":
            keys.sort(key=lambda k: SLOPE_ORDER.get(k, 9))
        out += [(name, k) for k in keys] + ([(name, "national")] if "national" in (s.get("curves") or {}) else [])
    return out


def curve_users(project) -> dict[str, list[str]]:
    """Curve set -> the functions that read it."""
    metas = metric_meta(project)
    out: dict[str, list[str]] = {}
    for m in project.catalog().get("methods", []):
        fname = (metas.get(m["metricId"]) or {}).get("functionName") or m["metricId"]
        for name in reg.curve_sets_used(m):
            if fname not in out.setdefault(name, []):
                out[name].append(fname)
    return out


def band_rules(project) -> list[dict]:
    """Every fixed-band rule in catalog order: a method's (or fallback route's) own bands,
    then its inputs' bands."""
    metas = metric_meta(project)
    out = []
    for m in project.catalog().get("methods", []):
        meta = metas.get(m["metricId"]) or {}
        for route in [m] + list(m.get("variants") or []):
            is_variant = route is not m
            units = next((i.get("units") for i in route.get("inputs") or [] if i.get("units")), "")
            common = {"methodKey": route["methodKey"], "function": meta.get("functionName") or m["metricId"],
                      "functionId": meta.get("functionId"),
                      "route": humanize(route["methodKey"]) if is_variant else None}
            if isinstance(route.get("bands"), list) and len(route["bands"]) >= 2:
                out.append({**common, "input": None, "label": route.get("title") or humanize(route["methodKey"]),
                            "units": units, "bands": route["bands"]})
            for i in route.get("inputs") or []:
                if isinstance(i.get("bands"), list) and len(i["bands"]) >= 2:
                    out.append({**common, "input": i["key"], "label": i.get("label") or i["key"],
                                "units": i.get("units") or "", "bands": i["bands"]})
    return out


def regional_rules(project) -> list[dict]:
    """Every regional edge pair: (method, input, region) with its Good and Poor edges."""
    metas = metric_meta(project)
    out = []
    for m in project.catalog().get("methods", []):
        meta = metas.get(m["metricId"]) or {}
        for i in m.get("inputs") or []:
            for region, pair in sorted((i.get("regionalBands") or {}).items()):
                out.append({"methodKey": m["methodKey"], "input": i["key"],
                            "inputLabel": i.get("label") or i["key"], "units": i.get("units") or "",
                            "function": meta.get("functionName") or m["metricId"],
                            "region": region, "good": pair[0], "poor": pair[1]})
    return out


def ordered_bands(bands: list[dict]) -> list[dict]:
    return sorted(bands, key=lambda b: float("-inf") if b.get("min") is None else b["min"])


def curve_tile(project, set_name: str, stratum: str, *, with_origin: bool = True) -> dict:
    s = (project.curves().get("sets") or {}).get(set_name) or {}
    c = (s.get("curves") or {}).get(stratum) or {}
    strata = [{"label": "This version", "points": [tuple(p) for p in c.get("points") or []]}]
    changed = False
    if with_origin and project.base.get("reference-curves.json"):
        o = parsed(project.base["reference-curves.json"])
        oc = (((o.get("sets") or {}).get(set_name) or {}).get("curves") or {}).get(stratum) or {}
        if oc.get("points") != c.get("points"):
            changed = True
            strata.append({"label": "Origin", "points": [tuple(p) for p in oc.get("points") or []]})
    tile = {"metric": f"{set_name}/{stratum}", "display_name": es.stratum_name(stratum),
            "strata": strata, "changed": changed, "curve": c, "set": s}
    if c.get("q25") is not None and c.get("q75") is not None:
        tile["reference_range"] = (float(c["q25"]), float(c["q75"]))
    return tile


def history_since_origin(project) -> list[dict]:
    """The edits and decisions recorded after the project's origin (its fork or import)."""
    hist = list(project.history or [])
    cut = 0
    for i, h in enumerate(hist):
        if h.get("action") in ("fork", "import"):
            cut = i + 1
    return [h for h in hist[cut:] if h.get("kind") in ("analytical", "display", "decision")]


def names_of(project) -> dict:
    """Plain names for history sentences: method key -> "<function>" (a fallback route adds
    its name), input key -> its label, function id -> its name."""
    metas = metric_meta(project)
    out = {}
    for m in project.catalog().get("methods", []):
        meta = metas.get(m["metricId"]) or {}
        fname = meta.get("functionName") or m["metricId"]
        out[m["methodKey"]] = fname
        if meta.get("functionId"):
            out[meta["functionId"]] = fname
        for v in m.get("variants") or []:
            out[v["methodKey"]] = f"{fname} ({humanize(v['methodKey'])} route)"
        for i in m.get("inputs") or []:
            out[f"{m['methodKey']}/{i['key']}"] = i.get("label") or i["key"]
    return out


def describe(h: dict, names: dict | None = None) -> str:
    """One plain sentence for a history record."""
    names = names or {}
    t = h.get("target") or {}
    a = h.get("action")
    mk = t.get("methodKey")
    fname = names.get(mk, mk)
    if a == "set_band_edge":
        b, af = h.get("before") or {}, h.get("after") or {}
        what = names.get(f"{mk}/{t['input']}", t["input"]) if t.get("input") else "band"
        return (f"{fname}: {what.lower() if t.get('input') else what} edge {t.get('edge', 0) + 1} "
                f"from {_fmt(b.get('value'))} to {_fmt(af.get('value'))}"
                + ("" if b.get("owner") == af.get("owner")
                   else f", a value at the edge now counts toward the {af.get('owner')} band"))
    if a == "set_regional_edges":
        b, af = h.get("before") or [None, None], h.get("after") or [None, None]
        what = names.get(f"{mk}/{t.get('input')}", str(t.get("input") or "").upper())
        return (f"{fname}: {what} edges in {es.stratum_name(t.get('region'))} from "
                f"{_fmt(b[0])} and {_fmt(b[1])} to {_fmt(af[0])} and {_fmt(af[1])}")
    if a == "set_curve_points":
        return (f"{es.family_name(t.get('set'))} curve for {es.stratum_name(t.get('stratum'))}: "
                "new knots")
    if a == "set_text":
        return f"{fname}: wording of its {t.get('field')}"
    if a == "confirm_selection":
        return f"{names.get(t.get('functionId'), t.get('functionId'))}: selection confirmed"
    if a == "fork":
        return "the start of this revision"
    if a == "import":
        return "the import"
    return str(a or "change")


def easi_view(state: AppState) -> dict | None:
    """The EASI strip's snapshot: statuses, the current stage and the count to badge. Read
    by the strip and the Project panel (views/stagebar.py)."""
    project = state.easi_project()
    if project is None:
        return None
    snap = es.snapshot(project, preview=state.easi_preview(), published=is_published(project))
    statuses = es.stage_status(snap)
    if (state.tasks_running() or {}).get(TASK_KEY):
        statuses["publish"] = {"status": rs.STAGE_RUNNING,
                               "detail": "Scoring the preview cases."}
    return {"snap": snap, "statuses": statuses, "stage": state.easi_stage(),
            "version_line": snap["version_line"]}


def is_published(project) -> bool:
    """True when the library holds this project's version number with exactly these files
    (an unedited revision has its origin's files, but it is not that version)."""
    ver, digest = int(project.meta.get("version") or 0), project.package_digest
    origin = project.origin()
    if origin.get("kind") == "library" and int(origin.get("version") or 0) == ver \
            and origin.get("packageDigest") == digest:
        return True
    try:
        man = lib.read_manifest(eio.ASSESSMENT_ID) if lib.exists() else None
    except Exception:  # noqa: BLE001
        man = None
    return any(int(v.get("version") or 0) == ver and v.get("contentDigest") == digest
               for v in (man or {}).get("versions") or [])


def next_version(project) -> int:
    """The version a revision of ``project`` becomes: after the project's own version and
    after the latest the library holds."""
    try:
        man = lib.read_manifest(eio.ASSESSMENT_ID) if lib.exists() else None
    except Exception:  # noqa: BLE001
        man = None
    return max(int(project.meta.get("version") or 1) + 1, int((man or {}).get("latestVersion") or 0) + 1)


def publish_block_reason() -> str | None:
    """Why this copy cannot publish, in the words the DEEP publish page uses."""
    if not ws.can_publish():
        return "maintainer"
    if not lib.writable():
        return f"The assessment library at {lib.library_root()} is not writable here."
    return lib.publish_gate_reason(person()) if lib.is_canonical_root() else None


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #
def easi_page_ui(id: str):
    ns = module.resolve_id(id)
    return ui.div(ui.output_ui(ns("head")), ui.output_ui(ns("body")), class_="easi-page")


@module.server
def easi_page_server(input, output, session, state: AppState):
    ns = session.ns
    _tasks: set = set()
    _modal_err = reactive.value(None)
    _target: dict = {"kind": None, "i": None, "e": None}
    _last_stage: dict = {"stage": None}
    _drafts: dict = {}                    # typed publish fields, kept across re-renders
    _pub_tick = reactive.value(0)

    def _launch(coro):
        task = asyncio.create_task(coro)
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return task

    def _get():
        with reactive.isolate():
            return state.easi_project()

    def _apply(new, message: str) -> None:
        """Swap in an edited project; the one it replaces becomes the undo step."""
        with reactive.isolate():
            cur = state.easi_project()
            undo = list(state.easi_undo() or [])
        undo.append(cur)
        state.easi_undo.set(undo[-UNDO_DEPTH:])
        state.easi_project.set(new)
        ui.notification_show(message, type="message", duration=4)

    def _running() -> bool:
        return bool((state.tasks_running() or {}).get(TASK_KEY))

    # ── the head: name, version, identity, actions ─────────────────────────────
    @render.ui
    def head():
        p = state.easi_project()
        if p is None:
            return ui.div("Open an EASI method project to see it here.", class_="text-muted p-4")
        ident = p.identity()
        undo = state.easi_undo() or []
        last = (undo and p.history and p.history[-1]) or None
        actions = []
        if undo:
            actions.append(_btn(ns("undo"), ui.TagList(fa("rotate-left"), " Undo"),
                                "btn btn-outline-secondary btn-sm",
                                title=("Undo: " + describe(last, names_of(p))) if last
                                else "Undo the last change"))
        actions.append(ui.download_button(ns("dl_package"),
                                          ui.TagList(fa("file-zipper"), " Method package"),
                                          class_="btn btn-outline-secondary btn-sm"))
        chips = [ui.span(ui.span("Method", class_="easi-id-k"), ui.span(ident["methodVersion"],
                                                                      class_="easi-id-v"),
                         class_="easi-id", title="The method version EASI reports with every score"),
                 ui.span(ui.span("Package", class_="easi-id-k"),
                         ui.span(short(ident["packageDigest"]), class_="easi-id-v"),
                         class_="easi-id", title=ident["packageDigest"]),
                 ui.span(ui.span("Evaluator", class_="easi-id-k"),
                         ui.span(short(ident["evaluatorDigest"]), class_="easi-id-v"),
                         class_="easi-id",
                         title="The EASI code these files are scored with: " + ident["evaluatorDigest"])]
        banner = None
        if not p.is_revision():
            banner = ui.div(
                ui.div(ui.tags.strong(es.version_line(p) + ". "),
                       "This version stays exactly as it is. Start a revision to change curves "
                       "or criteria; your changes become the next version.",
                       class_="easi-banner-text"),
                _btn(ns("fork"), ui.TagList(fa("code-branch"), f" Start v{next_version(p)}"),
                     "btn btn-primary btn-sm"),
                class_="easi-banner")
        return ui.div(
            ui.div(ui.div(ui.h2(p.meta.get("label") or eio.ASSESSMENT_NAME, class_="easi-title"),
                          ui.div(es.version_line(p), class_="easi-sub"), class_="easi-head-text"),
                   ui.div(*actions, class_="easi-head-actions"), class_="easi-head-row"),
            ui.div(*chips, class_="easi-ids"),
            banner,
            class_="easi-head")

    @render.download(filename=lambda: _package_name())
    def dl_package():
        p = _get()
        blob, _ident = eio.export_zip(p)
        yield blob

    def _package_name() -> str:
        p = _get()
        if p is None:
            return "easi-method.zip"
        return (f"{p.meta.get('methodId') or eio.METHOD_ID}-v{int(p.meta.get('version') or 1)}-"
                f"{short(p.package_digest)[:8]}.easi-method.zip")

    # ── the body: one stage at a time ────────────────────────────────────────
    @render.ui
    def body():
        p = state.easi_project()
        if p is None:
            return None
        stage = state.easi_stage() or "method"
        jump = None
        if _last_stage.get("stage") != stage:
            _last_stage["stage"] = stage
            jump = ui.tags.script("(function(){var c=document.querySelector('.sc-content');"
                                  "if(c)c.scrollTop=0;})();")
        render_stage = {"method": _method_stage, "development": _development_stage,
                        "curves": _curves_stage, "selection": _selection_stage,
                        "publish": _publish_stage}.get(stage, _method_stage)
        return ui.div(ui.h3(es.STAGE_SHORT.get(stage, "Method"), class_="easi-stage-title"),
                      render_stage(p), jump, class_="easi-stage")

    # ── 1. Method ────────────────────────────────────────────────────────────
    def _facts(rows):
        return ui.tags.dl(*[ui.TagList(ui.tags.dt(k), ui.tags.dd(v)) for k, v in rows if v],
                          class_="easi-facts")

    def _origin_sentence(p) -> str:
        o = p.origin()
        imp = (p.meta.get("lineage") or {}).get("importedFrom") or {}
        if o.get("kind") == "revision":
            return (f"A revision of v{o.get('version')} (method {o.get('methodVersion')}), "
                    f"started {str(p.meta.get('created') or '')[:10]}.")
        if o.get("kind") == "library":
            return f"Version {o.get('version')} of the STAF assessment library."
        if imp:
            return (f"Imported unchanged from EASI ({imp.get('source')}) on "
                    f"{str(imp.get('importedAt') or '')[:10]}"
                    + (f" by {imp.get('importedBy')}" if imp.get("importedBy") else "") + ".")
        return ""

    def _method_stage(p):
        cat = p.catalog()
        curves = p.curves()
        metas = metric_meta(p)
        n_curves = sum(len(s.get("curves") or {}) for s in (curves.get("sets") or {}).values())
        geo = p.meta.get("geography") or {}
        calc = p.calculator if p.meta.get("calculatorFor") == p.package_digest else None
        facts = _facts([
            ("Origin", _origin_sentence(p)),
            ("Geography", f"{geo.get('name') or 'Contiguous United States'}; reference curves by "
                          "NARS-9 region or slope class, each with a national fallback"),
            ("Functions", f"{len(cat.get('methods') or [])}"),
            ("Reference curves", f"{n_curves} in {len(curves.get('sets') or {})} families"),
            ("Calculator workbook", calc[0] if calc else
             "None for this version (EASI shows the worksheet without the Excel download)"),
        ])
        rows = []
        for i, m in enumerate(cat.get("methods") or []):
            meta = metas.get(m["metricId"]) or {}
            inputs = [x.get("label") or x.get("key") for x in m.get("inputs") or [] if not x.get("contextOnly")]
            how = OPERATOR_LABELS.get(m.get("operator"), humanize(m.get("operator")))
            if m.get("curve") or any(x.get("curve") for x in m.get("inputs") or []):
                how = ("Reference curve" if m.get("operator") == "threshold"
                       else how + ", reference curve")
            routes = len(m.get("variants") or [])
            rows.append(ui.tags.tr(
                ui.tags.td(str(i + 1), class_="easi-num"),
                ui.tags.td(ui.div(meta.get("functionName") or m["metricId"], class_="easi-strong"),
                           ui.div(meta.get("discipline") or "", class_="easi-muted")),
                ui.tags.td(ui.div(m.get("title") or m["methodKey"]),
                           ui.div(", ".join(inputs), class_="easi-muted")),
                ui.tags.td(how + (f"; {routes} fallback route" + ("s" if routes > 1 else "")
                                  if routes else "")),
                ui.tags.td(str(m.get("confidence") or ""), class_="easi-center",
                           title="Confidence the method records (H, M or L)"),
            ))
        table = ui.tags.table(
            ui.tags.thead(ui.tags.tr(ui.tags.th("#"), ui.tags.th("Function"), ui.tags.th("Method"),
                                     ui.tags.th("How it scores"), ui.tags.th("Conf."))),
            ui.tags.tbody(*rows), class_="table table-sm easi-table")
        return ui.TagList(
            ui.div(facts, class_="easi-card"),
            ui.div("Functions", class_="sc-sec"),
            table,
            ui.p("Band edges, regional nutrient edges and curve knots change in a revision. How "
                 "each function combines its inputs, where the data come from and the rollup "
                 "weights belong to EASI's evaluator (its digest is above) and change only in "
                 "EASI itself.", class_="easi-note"))

    # ── 2. Development data ─────────────────────────────────────────────────────
    def _development_stage(p):
        prov = p.curves().get("provenance") or {}
        screen = prov.get("screen") or {}
        strict, relaxed = screen.get("strict") or {}, screen.get("relaxed") or {}
        frame = screen.get("frame") or {}

        def rule(r):
            if not r:
                return "not applied"
            op, val = r
            if isinstance(val, bool):
                return "yes" if (val == (op == "==")) else "no"
            sym = {"<=": "\u2264", ">=": "\u2265", "==": "=", "!=": "\u2260"}.get(op, op)
            return f"{sym} {_fmt(val)}"

        names = sorted(set(strict) | set(relaxed))
        crit = ui.tags.table(
            ui.tags.thead(ui.tags.tr(ui.tags.th("Watershed variable"), ui.tags.th("Strict screen"),
                                     ui.tags.th("Relaxed screen"))),
            ui.tags.tbody(*[ui.tags.tr(
                ui.tags.td(ui.div(SCREEN_VARIABLES.get(n, n)), ui.div(ui.tags.code(n), class_="easi-muted")),
                ui.tags.td(rule(strict.get(n))), ui.tags.td(rule(relaxed.get(n)))) for n in names]),
            class_="table table-sm easi-table easi-table-narrow")
        floors = prov.get("panelFloors") or {}
        frame_words = []
        if (frame.get("wadeable") or [None, None])[1] is True:
            frame_words.append("wadeable streams")
        if (frame.get("fcode_class") or [None, None])[:2] == ["!=", "canal"]:
            frame_words.append("canals excluded")
        frame_words += [f"{k} {rule(v)}" for k, v in frame.items() if k not in ("wadeable", "fcode_class")]
        facts = _facts([
            ("Reference screen", screen.get("id")),
            ("Frame", ", ".join(frame_words)),
            ("Panel floors", "; ".join(f"{k} panel {v} sites" for k, v in floors.items())),
            ("Dataset vintage", prov.get("datasetVintage")),
            ("Curve engine", short(prov.get("curveEngineSha256"))),
            ("Fit registry", short((prov.get("registry") or {}).get("sha256"))),
        ])
        packages = p.evidence or []
        if packages:
            ev = ui.tags.ul(*[ui.tags.li(f"{e.get('name') or e.get('id')}: {e.get('role') or ''}")
                              for e in packages], class_="easi-list")
        else:
            ev = ui.div(fa("box-open"), ui.span(" No development data packages are attached to "
                                               "this project."), class_="easi-empty")
        return ui.TagList(
            ui.p("The reference curves record how their reference sites were chosen and fit. "
                 "Least-disturbed sites pass every criterion of the strict screen; strata "
                 "with too few fall back to the relaxed screen.", class_="easi-note mt-0"),
            ui.div(facts, class_="easi-card"),
            ui.div("Screen criteria", class_="sc-sec"), crit,
            ui.div("Development data packages", class_="sc-sec"), ev)

    # ── 3. Curves and criteria ──────────────────────────────────────────────────
    def _changes_strip(p):
        hist = [h for h in history_since_origin(p) if h.get("kind") in ("analytical", "display")]
        if not hist or p.is_unchanged_from_origin():
            return None
        o = p.origin()
        names = names_of(p)
        items = [ui.tags.li(describe(h, names), ui.span(f" ({h.get('reason')})", class_="easi-muted")
                            if h.get("reason") else None) for h in hist[-8:]]
        more = len(hist) - 8
        return ui.div(ui.div(f"Changed from v{o.get('version')}", class_="easi-changes-h"),
                      ui.tags.ul(*items, class_="easi-list"),
                      ui.div(f"and {more} earlier" if more > 0 else "", class_="easi-muted"),
                      class_="easi-changes")

    def _curves_stage(p):
        editable = p.is_revision()
        users = curve_users(p)
        tiles_by_set: dict[str, list] = {}
        for i, (name, stratum) in enumerate(curve_list(p)):
            t = curve_tile(p, name, stratum)
            c = t["curve"]
            tags = []
            if t["changed"]:
                tags.append(ui.span("Changed", class_="sc-tag is-changed"))
            if c.get("panelTier") and c.get("panelTier") != "complete":
                tags.append(ui.span(str(c["panelTier"]).capitalize(), class_="sc-tag"))
            tiles_by_set.setdefault(name, []).append(ui.tags.button(
                ui.div(ui.span(t["display_name"], class_="easi-tile-name"),
                       ui.span(stratum if stratum in es.STRATUM_NAMES and stratum.isupper() else "",
                               class_="easi-tile-code"),
                       class_="easi-tile-head"),
                ui.HTML(curve_svg.tile_svg(t, w=220, h=130, band_breaks=BREAKS)),
                ui.div(ui.span(f"n {c.get('n', c.get('nMembers', ''))}"),
                       ui.span(f"0.39 at {_short_num(c.get('x39'))}, 0.69 at {_short_num(c.get('x69'))}"),
                       class_="easi-tile-foot"),
                ui.div(*tags, class_="easi-tile-tags") if tags else None,
                type="button", class_="easi-tile" + (" is-changed" if t["changed"] else ""),
                title=("Edit" if editable else "View") + f" {es.family_name(name)}, {t['display_name']}",
                onclick=_evt(ns("curve_open"), i=i)))
        families = []
        sets = p.curves().get("sets") or {}
        for name, tiles in tiles_by_set.items():
            s = sets.get(name) or {}
            direction = "higher is better" if s.get("higherIsBetter") else "lower is better"
            families.append(ui.div(
                ui.div(ui.span(es.family_name(name), class_="easi-family-name"),
                       ui.span(f"by {es.STRATIFIER_NAMES.get(s.get('stratifier'), s.get('stratifier'))}"
                               f", {direction}, {len(tiles)} curves", class_="easi-muted"),
                       ui.span("Used by " + ", ".join(users.get(name) or ["no function"]),
                               class_="easi-family-users"),
                       class_="easi-family-head"),
                ui.div(*tiles, class_="easi-tiles"), class_="easi-family"))
        # fixed criteria
        rows = []
        for i, r in enumerate(band_rules(p)):
            bands = ordered_bands(r["bands"])
            segs = []
            for e, b in enumerate(bands):
                segs.append(ui.span(ui.span(b.get("rating"), class_="easi-band-r"),
                                    ui.span(b.get("label") or "", class_="easi-band-l"),
                                    class_="easi-band " + RATING_CLASS.get(b.get("rating"), "")))
                if e < len(bands) - 1 and b.get("max") is not None \
                        and b.get("max") == bands[e + 1].get("min"):
                    owner = "lower" if b.get("maxInclusive") else "upper"
                    owner_rating = b.get("rating") if owner == "lower" else bands[e + 1].get("rating")
                    tip = f"{_fmt(b.get('max'))} counts as {owner_rating}"
                    if editable:
                        segs.append(ui.tags.button(_fmt(b.get("max")), type="button",
                                                   class_="easi-edge is-editable", title="Edit: " + tip,
                                                   onclick=_evt(ns("edge_open"), i=i, e=e)))
                    else:
                        segs.append(ui.span(_fmt(b.get("max")), class_="easi-edge", title=tip))
            rows.append(ui.tags.tr(
                ui.tags.td(ui.div(r["function"], class_="easi-strong"),
                           ui.div(("Fallback route: " + r["route"]) if r["route"] else "",
                                  class_="easi-muted")),
                ui.tags.td(ui.div(r["label"]), ui.div(r["units"], class_="easi-muted")),
                ui.tags.td(ui.div(*segs, class_="easi-bands"))))
        criteria = ui.tags.table(
            ui.tags.thead(ui.tags.tr(ui.tags.th("Function"), ui.tags.th("Rule"), ui.tags.th("Bands"))),
            ui.tags.tbody(*rows), class_="table table-sm easi-table")
        regional = regional_rules(p)
        reg_rows = {}
        for i, r in enumerate(regional):
            cell = ui.span(f"{_fmt(r['good'])} / {_fmt(r['poor'])}", class_="easi-pair")
            if editable:
                cell = ui.tags.button(f"{_fmt(r['good'])} / {_fmt(r['poor'])}", type="button",
                                      class_="easi-pair is-editable",
                                      title=f"Edit the {r['inputLabel']} edges in {r['region']}",
                                      onclick=_evt(ns("regional_open"), i=i))
            reg_rows.setdefault(r["region"], {})[r["input"]] = cell
        inputs = sorted({r["input"] for r in regional})
        labels = {r["input"]: f"{r['inputLabel']} ({r['units']})" for r in regional}
        reg_table = ui.tags.table(
            ui.tags.thead(ui.tags.tr(ui.tags.th("NARS-9 region"),
                                     *[ui.tags.th(labels[k]) for k in inputs])),
            ui.tags.tbody(*[ui.tags.tr(ui.tags.td(f"{es.stratum_name(reg_)} ({reg_})"),
                                       *[ui.tags.td(cells.get(k)) for k in inputs])
                            for reg_, cells in sorted(reg_rows.items())]),
            class_="table table-sm easi-table easi-table-narrow")
        preview_btn = None
        if editable and not p.is_unchanged_from_origin():
            preview_btn = _preview_button(p)
        return ui.TagList(
            _changes_strip(p),
            ui.div(ui.span("Reference curves", class_="easi-sec-title"), preview_btn,
                   class_="easi-sec-row"),
            ui.p("Shaded column: the reference sites' middle half. Dashed lines: the condition "
                 "breaks at 0.39 and 0.69, where a value becomes Fair and Good."
                 + (" A changed curve draws its origin dashed." if editable else ""),
                 class_="easi-note mt-0"),
            *families,
            ui.div("Fixed criteria", class_="sc-sec"),
            ui.p("Each edge is where one rating ends and the next begins; hover an edge to see which "
                 "side it counts toward." + (" Click an edge to move it." if editable else ""),
                 class_="easi-note mt-0"),
            criteria,
            ui.div("Regional nutrient edges", class_="sc-sec"),
            ui.p("Good at or below the first value, Poor at or above the second.",
                 class_="easi-note mt-0"),
            reg_table)

    # ── 4. Final selection ────────────────────────────────────────────────────
    def _selection_stage(p):
        rows = reg.status_rows(p)
        body = []
        for i, r in enumerate(rows):
            status = (ui.span("Needs your confirmation", class_="sc-tag is-attention")
                      if r["needsReview"] else ui.span("Selected", class_="sc-tag is-selected"))
            action = None
            if r["needsReview"]:
                action = ui.tags.button("Confirm…", type="button", class_="btn btn-outline-primary btn-sm",
                                        onclick=_evt(ns("confirm_open"), i=i))
            body.append(ui.tags.tr(
                ui.tags.td(ui.div(r["functionName"], class_="easi-strong")),
                ui.tags.td(r["method"]),
                ui.tags.td(r.get("who") if r["decidedBy"] == "person" and r.get("who")
                           else DECIDED_LABELS.get(r["decidedBy"], r["decidedBy"] or "")),
                ui.tags.td(status), ui.tags.td(action, class_="easi-right")))
        n_pending = sum(1 for r in rows if r["needsReview"])
        lead = (f"{n_pending} function" + (" changed" if n_pending == 1 else "s changed")
                + " after its method was selected. Review the consequences, then confirm each "
                  "one with a reason." if n_pending else
                "Every function has its selected method. A change to a function's curve or "
                "criteria asks for your confirmation here.")
        return ui.TagList(
            ui.p(lead, class_="easi-note mt-0"),
            ui.tags.table(ui.tags.thead(ui.tags.tr(ui.tags.th("Function"), ui.tags.th("Selected method"),
                                                   ui.tags.th("Decided by"), ui.tags.th("Status"),
                                                   ui.tags.th(""))),
                          ui.tags.tbody(*body), class_="table table-sm easi-table"),
            ui.p("Alternatives considered for a function are listed with it once they are "
                 "recorded in the project.", class_="easi-note"))

    # ── 5. Review and publish ─────────────────────────────────────────────────
    def _preview_button(p):
        return ui.output_ui(ns("preview_btn"), inline=True)

    @render.ui
    def preview_btn():
        p = state.easi_project()
        if p is None:
            return None
        pv = state.easi_preview()
        current = bool(pv and pv.get("packageDigest") == p.package_digest)
        if _running():
            return ui.span(fa("spinner"), " Scoring the preview cases…", class_="easi-running")
        return _btn(ns("preview"), ui.TagList(fa("flask"), " Preview again" if current
                                              else " Preview consequences"),
                    "btn btn-outline-primary btn-sm" if current else "btn btn-primary btn-sm")

    def _preview_block(p):
        pv = state.easi_preview()
        if not pv or pv.get("packageDigest") != p.package_digest:
            msg = ("Score the project's preview cases with this version and with v"
                   f"{p.origin().get('version')} to see which ratings move."
                   if p.cases and p.cases.get("cases") else
                   "This project has no preview cases, so its consequences cannot be previewed.")
            return ui.div(ui.p(msg, class_="mb-2"), _preview_button(p) if p.cases else None,
                          class_="easi-card")
        metas = {m["metricId"]: m for m in p.metrics().get("metrics", [])}
        rows = []
        for mid, info in sorted(pv.get("byMetric", {}).items(), key=lambda kv: -kv[1]["cases"]):
            trans = ", ".join(f"{k.replace(' -> ', ' to ')}: {v}"
                              for k, v in sorted(info["transitions"].items(), key=lambda kv: -kv[1]))
            rows.append(ui.tags.tr(ui.tags.td((metas.get(mid) or {}).get("functionName") or mid),
                                   ui.tags.td(str(info["cases"]), class_="easi-right"),
                                   ui.tags.td(trans)))
        eci = pv.get("eciShift") or {}
        summary = (f"{pv['casesChanged']} of {pv['cases']} preview cases change a rating."
                   if pv.get("casesChanged") else
                   f"No rating changes on the {pv['cases']} preview cases.")
        eci_line = (f"Condition index shift: {eci['min']:+.3f} to {eci['max']:+.3f} "
                    f"(mean size {eci['meanAbs']:.3f})." if eci.get("n") else "")
        ident = pv.get("identity") or {}
        stamp = (f"Scored {ident.get('draft', {}).get('methodVersion')} against "
                 f"{ident.get('base', {}).get('methodVersion')} in worker processes, "
                 f"{pv.get('seconds')} s.")
        table = (ui.tags.table(ui.tags.thead(ui.tags.tr(ui.tags.th("Function"),
                                                        ui.tags.th("Cases", class_="easi-right"),
                                                        ui.tags.th("Rating changes"))),
                               ui.tags.tbody(*rows), class_="table table-sm easi-table")
                 if rows else None)
        return ui.div(ui.div(ui.tags.strong(summary), " ", eci_line), table,
                      ui.div(stamp, class_="easi-muted"),
                      ui.div("The preview cases are EASI's calculator test cases: every band edge, "
                             "curve crossing and fallback route, built around one reach. They show "
                             "which rules move and in which direction, not how many real reaches "
                             "would.", class_="easi-muted mt-1"),
                      ui.div(_preview_button(p), class_="mt-2"),
                      class_="easi-card")

    def _publish_stage(p):
        o = p.origin()
        hist = history_since_origin(p)
        changed = not p.is_unchanged_from_origin()
        if p.is_revision():
            names = names_of(p)
            if hist and changed:
                what = ui.tags.ul(*[ui.tags.li(describe(h, names),
                                               ui.span(f" ({h.get('reason')})", class_="easi-muted")
                                               if h.get("reason") else None) for h in hist],
                                  class_="easi-list")
            else:
                what = ui.p("Nothing has changed from v" + str(o.get("version")) + " yet"
                            + (" (the edits made so far cancel out)." if hist else "."),
                            class_="mb-0")
            d = edit.diff(_origin_project(p), p) if changed else None
            files = (ui.div("Files changed: " + ", ".join(d["filesChanged"]), class_="easi-muted mt-1")
                     if d and d["filesChanged"] else None)
            changes = ui.div(what, files, class_="easi-card")
        else:
            changes = ui.div(ui.p(es.version_line(p) + ". Nothing changes in this version; start a "
                                  "revision to change it.", class_="mb-0"), class_="easi-card")
        pending = reg.needs_review(p)
        return ui.TagList(
            ui.div(f"Changes from v{o.get('version')}" if p.is_revision() else "Changes",
                   class_="sc-sec mt-0"),
            changes,
            ui.div("Consequences", class_="sc-sec"),
            _preview_block(p) if p.is_revision() and changed else
            ui.div(ui.p("A version that changes nothing moves no rating.", class_="mb-0"),
                   class_="easi-card"),
            ui.div("Method package", class_="sc-sec"),
            ui.div(ui.p("The package is the file EASI loads: these method files and the identity "
                        "they score with. Downloading it changes nothing in EASI.", class_="mb-2"),
                   ui.download_button(ns("dl_package2"), ui.TagList(fa("file-zipper"),
                                                                    " Download method package"),
                                      class_="btn btn-outline-secondary btn-sm"),
                   class_="easi-card"),
            ui.div("Publish", class_="sc-sec"),
            _publish_form(p, pending))

    @render.download(filename=lambda: _package_name())
    def dl_package2():
        p = _get()
        blob, _ident = eio.export_zip(p)
        yield blob

    def _publish_form(p, pending):
        _pub_tick()
        published = is_published(p)
        if published:
            return ui.div(ui.p(fa("circle-check"), " This version is in the assessment library.",
                               class_="mb-0"), class_="easi-card")
        block = publish_block_reason()
        if block == "maintainer":
            return ui.div(ui.p("Versions are published to the STAF assessment library by its "
                               "maintainer, from a STAF checkout started with "
                               "STAF_LIBRARY_PUBLISH=1. Send them this project file.",
                               class_="mb-0"), class_="easi-card")
        if block:
            return ui.div(ui.p(block, class_="mb-0"), class_="easi-card")
        man = lib.read_manifest(eio.ASSESSMENT_ID) or {}
        next_v = int(man.get("latestVersion") or 0) + 1
        ver = int(p.meta.get("version") or 1)
        problems = []
        if ver != next_v:
            problems.append(f"This project is v{ver}, but the library's next EASI version is v{next_v}.")
        if p.is_revision() and p.is_unchanged_from_origin():
            problems.append("Change the method before publishing a revision.")
        if pending:
            problems.append("Confirm the changed selections first: "
                            + ", ".join(r["functionName"] for r in pending) + ".")
        pv = state.easi_preview()
        if p.is_revision() and not p.is_unchanged_from_origin() and not (
                pv and pv.get("packageDigest") == p.package_digest):
            problems.append("Preview the consequences of this version first.")
        form = ui.div(
            ui.input_radio_buttons(ns("pub_status"), "Publish as",
                                   {"draft": "Draft", "preliminary": "Preliminary"},
                                   selected=_drafts.get("pub_status") or "draft", inline=True),
            ui.input_text_area(ns("pub_notes"), "Revision notes", value=_drafts.get("pub_notes") or "",
                               placeholder="What changed and why", rows=3, width="100%"),
            ui.div(f"Recorded as published by {person() or 'nobody (set STAF_LIBRARY_MAINTAINER)'} "
                   f"into {lib.library_root()}.", class_="easi-muted mb-2"),
            _btn(ns("publish"), ui.TagList(fa("cloud-arrow-up"), f" Publish v{ver}"),
                 "btn btn-primary btn-sm", **({"disabled": "disabled"} if problems else {})),
            class_="easi-card")
        if problems:
            return ui.TagList(ui.div(*[ui.div(fa("circle-exclamation"), " ", x) for x in problems],
                                     class_="easi-problems"), form)
        return form

    def _origin_project(p):
        o = p.copy()
        o.files = p.origin_files()
        o.base = {}
        return o

    @reactive.effect
    @guard("keep the publish notes")
    def _keep_drafts():
        _drafts["pub_notes"] = input.pub_notes()
        _drafts["pub_status"] = input.pub_status()

    # ── actions ─────────────────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.undo)
    @guard("undo")
    def _undo():
        with reactive.isolate():
            undo = list(state.easi_undo() or [])
        if not undo:
            return
        prev = undo.pop()
        state.easi_undo.set(undo)
        state.easi_project.set(prev)
        ui.notification_show("Undone.", type="message", duration=3)

    @reactive.effect
    @reactive.event(input.fork)
    @guard("start a revision")
    def _fork():
        p = _get()
        if p is None or p.is_revision():
            return
        new = eio.fork(p, by=person() or "author", version=next_version(p))
        _apply(new, f"Started v{new.meta['version']}, a revision of v{p.meta.get('version')}. "
                    f"v{p.meta.get('version')} stays as it is.")
        state.easi_stage.set("curves")

    def _modal(title: str, *body, apply_id: str, apply_label: str = "Apply", size: str = "l"):
        _modal_err.set(None)
        ui.modal_show(ui.modal(
            *body, ui.output_ui(ns("modal_err")),
            title=title, size=size, easy_close=True,
            footer=ui.TagList(ui.modal_button("Cancel", class_="btn btn-outline-secondary"),
                              _btn(ns(apply_id), apply_label, "btn btn-primary"))))

    @render.ui
    def modal_err():
        msg = str(_modal_err() or "").strip()
        if not msg:
            return None
        msg = msg[0].upper() + msg[1:] + ("" if msg.endswith(".") else ".")
        return ui.div(fa("circle-exclamation"), " ", msg, class_="easi-modal-err")

    def _reason_input(value: str = "", *, label: str = "Reason (recorded with the change)",
                      placeholder: str = "Why this change"):
        return ui.input_text(ns("reason"), label, value=value, width="100%",
                             placeholder=placeholder)

    # a curve: view or edit its knots
    @reactive.effect
    @reactive.event(input.curve_open)
    @guard("open the curve")
    def _curve_open():
        p = _get()
        i = int((input.curve_open() or {}).get("i", -1))
        items = curve_list(p)
        if not 0 <= i < len(items):
            return
        name, stratum = items[i]
        _target.update(kind="curve", i=i)
        t = curve_tile(p, name, stratum)
        c = t["curve"]
        s = t["set"]
        quantity = str(s.get("quantity") or "")
        svg = ui.HTML(curve_svg.tile_svg(t, w=560, h=300, band_breaks=BREAKS, point_labels=True,
                                         x_label=es.QUANTITY_NAMES.get(quantity, quantity)))
        facts = _facts([
            ("Reference sites", f"{c.get('n')} of {c.get('nMembers')} panel members"),
            ("Screen", f"{c.get('screen')}, panel {c.get('panelTier')}"),
            ("Reference quartiles", f"{_short_num(c.get('q25'))}, {_short_num(c.get('q50'))}, "
                                    f"{_short_num(c.get('q75'))}"),
            ("Crossings", f"0.39 at {_fmt(c.get('x39'))}; 0.69 at {_fmt(c.get('x69'))}"),
            ("Direction", "higher is better" if s.get("higherIsBetter") else "lower is better"),
        ])
        body = [ui.div(svg, class_="easi-curve-big"), facts]
        if p.is_revision():
            knots = "\n".join(f"{_fmt(x)}, {_fmt(y)}" for x, y in c.get("points") or [])
            body += [ui.input_text_area(ns("knots"), "Knots: value, index (one pair per line, "
                                                     "increasing value, index 0 to 1)",
                                        value=knots, rows=6, width="100%"),
                     _reason_input()]
            _modal(f"{es.family_name(name)}: {t['display_name']}", *body, apply_id="curve_apply",
                   apply_label="Apply the new knots")
        else:
            ui.modal_show(ui.modal(*body, title=f"{es.family_name(name)}: {t['display_name']}",
                                   size="l", easy_close=True,
                                   footer=ui.modal_button("Close", class_="btn btn-outline-secondary")))

    @reactive.effect
    @reactive.event(input.curve_apply)
    @guard("apply the curve")
    def _curve_apply():
        p = _get()
        if _target.get("kind") != "curve":
            return
        name, stratum = curve_list(p)[int(_target["i"])]
        try:
            pts = []
            for line in str(input.knots() or "").replace(";", "\n").splitlines():
                line = line.strip()
                if not line:
                    continue
                a, b = [x.strip() for x in line.replace("\t", ",").split(",")[:2]]
                pts.append((float(a), float(b)))
            reason = str(input.reason() or "").strip()
            if not reason:
                raise edit.EditError("say why (a short reason is recorded with the change)")
            new = edit.set_curve_points(p, name, stratum, pts, by=person() or "author",
                                        reason=reason)
        except (ValueError, edit.EditError) as exc:
            _modal_err.set(str(exc) if isinstance(exc, edit.EditError)
                           else "Each line needs two numbers: a value and its index.")
            return
        ui.modal_remove()
        _apply(new, f"Changed the {es.family_name(name).lower()} curve for "
                    f"{es.stratum_name(stratum)}.")

    # a band edge
    @reactive.effect
    @reactive.event(input.edge_open)
    @guard("open the band edge")
    def _edge_open():
        p = _get()
        ev = input.edge_open() or {}
        i, e = int(ev.get("i", -1)), int(ev.get("e", -1))
        rules = band_rules(p)
        if not p.is_revision() or not 0 <= i < len(rules):
            return
        r = rules[i]
        bands = ordered_bands(r["bands"])
        if not 0 <= e < len(bands) - 1:
            return
        _target.update(kind="edge", i=i, e=e)
        lo, hi = bands[e], bands[e + 1]
        owner = "lower" if lo.get("maxInclusive") else "upper"
        _modal(f"{r['function']}: {r['label']}",
               ui.p(f"The edge between {lo.get('rating')} and {hi.get('rating')} is at "
                    f"{_fmt(lo.get('max'))} {r['units']}; a value exactly at the edge counts as "
                    f"{lo.get('rating') if owner == 'lower' else hi.get('rating')}.", class_="mb-3"),
               ui.input_numeric(ns("edge_value"), f"New edge ({r['units']})".replace(" ()", ""),
                                value=lo.get("max")),
               ui.input_radio_buttons(ns("edge_owner"), "A value exactly at the edge counts as",
                                      {"lower": lo.get("rating"), "upper": hi.get("rating")},
                                      selected=owner, inline=True),
               _reason_input(), apply_id="edge_apply", apply_label="Move the edge", size="m")

    @reactive.effect
    @reactive.event(input.edge_apply)
    @guard("move the band edge")
    def _edge_apply():
        p = _get()
        if _target.get("kind") != "edge":
            return
        r = band_rules(p)[int(_target["i"])]
        try:
            value = input.edge_value()
            if value is None:
                raise edit.EditError("enter the new edge value")
            reason = str(input.reason() or "").strip()
            if not reason:
                raise edit.EditError("say why (a short reason is recorded with the change)")
            new = edit.set_band_edge(p, r["methodKey"], r["input"], int(_target["e"]), float(value),
                                     owner=input.edge_owner(), by=person() or "author", reason=reason)
        except edit.EditError as exc:
            _modal_err.set(str(exc))
            return
        ui.modal_remove()
        _apply(new, f"Moved a band edge of {r['function']}.")

    # a regional edge pair
    @reactive.effect
    @reactive.event(input.regional_open)
    @guard("open the regional edges")
    def _regional_open():
        p = _get()
        i = int((input.regional_open() or {}).get("i", -1))
        rules = regional_rules(p)
        if not p.is_revision() or not 0 <= i < len(rules):
            return
        r = rules[i]
        _target.update(kind="regional", i=i)
        _modal(f"{r['inputLabel']} in {es.stratum_name(r['region'])} ({r['region']})",
               ui.p(f"Good at or below {_fmt(r['good'])} {r['units']}; Poor at or above "
                    f"{_fmt(r['poor'])} {r['units']}.", class_="mb-3"),
               ui.div(ui.input_numeric(ns("reg_good"), "Good edge", value=r["good"]),
                      ui.input_numeric(ns("reg_poor"), "Poor edge", value=r["poor"]),
                      class_="easi-pair-inputs"),
               _reason_input(), apply_id="regional_apply", apply_label="Apply the edges", size="m")

    @reactive.effect
    @reactive.event(input.regional_apply)
    @guard("apply the regional edges")
    def _regional_apply():
        p = _get()
        if _target.get("kind") != "regional":
            return
        r = regional_rules(p)[int(_target["i"])]
        try:
            good, poor = input.reg_good(), input.reg_poor()
            if good is None or poor is None:
                raise edit.EditError("enter both edges")
            reason = str(input.reason() or "").strip()
            if not reason:
                raise edit.EditError("say why (a short reason is recorded with the change)")
            new = edit.set_regional_edges(p, r["methodKey"], r["input"], r["region"], float(good),
                                          float(poor), by=person() or "author", reason=reason)
        except edit.EditError as exc:
            _modal_err.set(str(exc))
            return
        ui.modal_remove()
        _apply(new, f"Changed the {r['inputLabel']} edges in {r['region']}.")

    # confirming a changed selection
    @reactive.effect
    @reactive.event(input.confirm_open)
    @guard("open the confirmation")
    def _confirm_open():
        p = _get()
        rows = reg.status_rows(p)
        i = int((input.confirm_open() or {}).get("i", -1))
        if not 0 <= i < len(rows) or not rows[i]["needsReview"]:
            return
        r = rows[i]
        _target.update(kind="confirm", i=i)
        names = names_of(p)
        changes = [describe(h, names) for h in history_since_origin(p)
                   if (h.get("target") or {}).get("methodKey") == r["methodKey"]
                   or h.get("action") == "set_curve_points"]
        _modal(f"Confirm {r['functionName']}",
               ui.p(f"{r['functionName']} keeps {r['method']} as it now stands.", class_="mb-2"),
               ui.tags.ul(*[ui.tags.li(c) for c in changes], class_="easi-list") if changes else None,
               ui.input_text(ns("who"), "Your name", value=person(), width="100%"),
               _reason_input(label="Reason (recorded with the decision)",
                             placeholder="Why the selection stands"),
               apply_id="confirm_apply", apply_label="Confirm the selection",
               size="m")

    @reactive.effect
    @reactive.event(input.confirm_apply)
    @guard("confirm the selection")
    def _confirm_apply():
        p = _get()
        if _target.get("kind") != "confirm":
            return
        r = reg.status_rows(p)[int(_target["i"])]
        try:
            new = reg.confirm_selection(p, r["functionId"], by=str(input.who() or ""),
                                        reason=str(input.reason() or ""), at=_now())
        except ValueError as exc:
            _modal_err.set(str(exc))
            return
        ui.modal_remove()
        _apply(new, f"Confirmed {r['functionName']}.")

    # the consequences preview (worker processes; never this process's EASI)
    @reactive.effect
    @reactive.event(input.preview)
    @guard("preview the consequences")
    def _preview():
        p = _get()
        if p is None or _running():
            return
        _launch(_run_preview(p))

    def _set_running(on: bool) -> None:
        with reactive.isolate():
            tasks = dict(state.tasks_running() or {})
        if on:
            tasks[TASK_KEY] = True
        else:
            tasks.pop(TASK_KEY, None)
        state.tasks_running.set(tasks)

    async def _run_preview(p):
        with st.busy(state):
            _set_running(True)
            await st.task_flush()
            try:
                out = await asyncio.to_thread(evaluate.preview, p)
                with reactive.isolate():
                    state.easi_preview.set(out)
                ui.notification_show(
                    f"{out['casesChanged']} of {out['cases']} preview cases change a rating.",
                    type="message", duration=5)
            except Exception as exc:  # noqa: BLE001 - the task must say what happened
                logger.exception("EASI preview failed")
                ui.notification_show(f"The preview could not run: {exc}", type="error",
                                     duration=10)
            finally:
                _set_running(False)
            await st.task_flush()

    # publishing (a maintainer's checkout)
    @reactive.effect
    @reactive.event(input.publish)
    @guard("publish")
    def _publish():
        p = _get()
        if p is None or publish_block_reason():
            return
        notes = str(input.pub_notes() or "").strip()
        if not notes:
            ui.notification_show("Write the revision notes first.", type="warning", duration=5)
            return
        pv = state.easi_preview() if p.is_revision() else None
        consequences = None
        if pv and pv.get("packageDigest") == p.package_digest:
            consequences = {k: pv.get(k) for k in ("cases", "casesChanged", "byMetric", "eciShift",
                                                   "identity", "casesDigest", "at")}
        try:
            version = eio.publish(p, author=person(), revision_notes=notes,
                                  status=str(input.pub_status() or "draft"),
                                  consequences=consequences)
        except (ValueError, RuntimeError) as exc:
            ui.notification_show(f"Not published: {exc}", type="error", duration=10)
            return
        _drafts.clear()
        _pub_tick.set(_pub_tick() + 1)
        state.easi_project.set(p.copy())        # the strip and the page re-read the library
        ui.notification_show(f"Published EASI method v{version} to {lib.library_root()}.",
                             type="message", duration=8)


__all__ = ["easi_page_ui", "easi_page_server", "easi_view", "curve_list", "band_rules",
           "regional_rules", "curve_tile", "describe", "names_of", "history_since_origin",
           "is_published"]
