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
import json
import logging
import os
from pathlib import Path

from shiny import module, reactive, render, ui

from streamcurves import candidates as cands_mod
from streamcurves import curve_svg
from streamcurves import evidence_store as evs
from streamcurves import library as lib
from streamcurves import prefs
from streamcurves import run_state as rs
from streamcurves import workspace as ws
from streamcurves.easi_method import edit
from streamcurves.easi_method import alternatives as alts
from streamcurves.easi_method import evaluate
from streamcurves.easi_method import evidence as ev
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
#: what an edit records when nobody set their name (never a stand-in identity)
UNNAMED = "Name not set"


# --------------------------------------------------------------------------- #
# small pure helpers
# --------------------------------------------------------------------------- #
def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def short(digest) -> str:
    d = str(digest or "")
    return d.split(":", 1)[1][:12] if ":" in d else d[:12]


def _plain_failure(exc: Exception) -> str:
    """A failed preview in words, with the next step (the detail stays in the log)."""
    text = str(exc)
    if "not consistent" in text or "unknown stratifier" in text or "direction" in text:
        first = text.split(": ", 1)[-1].split(";")[0]
        return ("The preview could not run: the draft's method files do not pass EASI's checks "
                f"({first}). Undo the last change, or correct it, and preview again.")
    if "capabilities" in text:
        return ("The preview could not run: this draft needs something this EASI cannot score. "
                "Undo the last change and preview again.")
    return f"The preview could not run: {text}. Try again; if it fails again, save the project and report it."


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


def _sentence(text) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def _unavailable_line(i: dict) -> str:
    """One item a package does not carry: what, why, and what to do."""
    head = str(i.get("item") or "").strip()
    head = head[:1].upper() + head[1:]
    return " ".join(x for x in (f"{head}:", _sentence(i.get("why")), _sentence(i.get("remedy"))) if x.strip(": "))


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
                   else f", a value at the edge now counts toward the {af.get('owner')} band")
                + ("; read its breakpoint note again, it explains the old edge"
                   if h.get("textToCheck") else ""))
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
    if a == "renumber":
        return f"renumbered from v{t.get('from')} to v{t.get('to')}"
    if a == "adopt_candidate":
        fns = [names.get(f, f) for f in t.get("functions") or []]
        return (", ".join(fns) or "a function") + ": another method selected"
    if a == "import_alternatives":
        return "the study's alternatives, imported for comparison"
    if a == "add_sqt_candidate":
        return f"{names.get(t.get('functionId'), t.get('functionId'))}: a state SQT curve considered"
    return str(a or "change")


def covers(coverage: dict) -> str:
    """One line for what a package covers."""
    c = coverage or {}
    parts = []
    for key, noun in (("memberRows", "member rows"), ("reaches", "reaches"), ("fits", "fits"),
                      ("operationalCurves", "operational curves")):
        if isinstance(c.get(key), int):
            parts.append(f"{c[key]:,} {noun}")
    if isinstance(c.get("studyReceipts"), list):
        parts.append(f"{len(c['studyReceipts'])} study receipts")
    if c.get("build"):
        parts.append(f"build {str(c['build'])[:8]}")
    return ", ".join(parts)


def check_lines(checks: dict) -> list[str]:
    """A package's recorded checks in words (an unknown check stays as its JSON)."""
    out = []
    for key, c in (checks or {}).items():
        if key == "eromMonthsReproduceStoredCv" and isinstance(c, dict):
            out.append(f"The 12 monthly EROM flows reproduce the stored flow CV of "
                       f"{c.get('identicalAtStoredPrecision', 0):,} of {c.get('comparable', 0):,} "
                       f"members exactly at its stored precision ({c.get('storedType')}); "
                       f"missing values agree: {'yes' if c.get('nullsAgree') else 'no'}.")
        elif key == "panelsRegenerateMembers" and isinstance(c, dict):
            out.append(f"Drawing the panels again from this package gives "
                       f"{'the same' if c.get('identical') else 'different'} "
                       f"{c.get('memberRows', 0):,} member rows ({c.get('seconds')} s).")
        else:
            out.append(f"{key}: {json.dumps(c, sort_keys=True)}")
    return out


def size_text(n) -> str:
    n = int(n or 0)
    return f"{n / 1e6:,.1f} MB" if n >= 100_000 else f"{n / 1e3:,.0f} KB"


def evidence_base() -> str:
    """Where packages are fetched from (a folder or an https base); empty when unset."""
    return os.environ.get("STREAMCURVES_EVIDENCE_BASE_URL", "").strip()


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


def _package_blob(project) -> bytes:
    """The method package a download serves: the library's own bytes for a published
    version it holds (the release asset), otherwise the project's export."""
    if is_published(project):
        try:
            ver = int(project.meta.get("version") or 0)
            if lib.exists() and lib.version_dir(eio.ASSESSMENT_ID, ver).is_dir():
                return lib.easi_package_bytes(eio.ASSESSMENT_ID, ver)
        except Exception:  # noqa: BLE001 - fall back to exporting the same files
            pass
    return eio.export_zip(project)[0]


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
                         class_="easi-id", title="The method version EASI reports with every score")]
        more = [ui.span(ui.span("Package", class_="easi-id-k"),
                         ui.span(short(ident["packageDigest"]), class_="easi-id-v"),
                         class_="easi-id", title=ident["packageDigest"]),
                 ui.span(ui.span("Evaluator", class_="easi-id-k"),
                         ui.span(short(ident["evaluatorDigest"]), class_="easi-id-v"),
                         class_="easi-id",
                         title="The EASI code these files are scored with: " + ident["evaluatorDigest"])]
        chips.append(ui.tags.details(ui.tags.summary("Identity", class_="easi-ids-more"),
                                     ui.div(*more, class_="easi-ids"), class_="easi-ids-details"))
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
        yield _package_blob(_get())

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
        facts = ui.TagList(_facts([
            ("Reference screen", screen.get("id")),
            ("Frame", ", ".join(frame_words)),
            ("Dataset vintage", prov.get("datasetVintage")),
        ]), ui.tags.details(ui.tags.summary("Technical record", class_="easi-muted"), _facts([
            ("Smallest panel per level", "; ".join(f"{k}: {v} sites" for k, v in floors.items())),
            ("Curve engine", short(prov.get("curveEngineSha256"))),
            ("Fit registry", short((prov.get("registry") or {}).get("sha256"))),
        ])))
        return ui.TagList(
            ui.p("The reference curves record how their reference sites were chosen and fit. "
                 "Least-disturbed sites pass every criterion of the strict screen; strata "
                 "with too few fall back to the relaxed screen.", class_="easi-note mt-0"),
            ui.div(facts, class_="easi-card"),
            ui.div("Screen criteria", class_="sc-sec"), crit,
            ui.div("Development data packages", class_="sc-sec"),
            ui.output_ui(ns("packages")),
            ui.output_ui(ns("refit_box")))

    # ── development data packages ───────────────────────────────────────────────
    _store_tick = reactive.value(0)
    _jobs: dict = {"busy": None}
    _refit = reactive.value(None)

    def _installed() -> list[dict]:
        _store_tick()
        try:
            return evs.installed()
        except Exception:  # noqa: BLE001 - an unreadable store lists nothing
            return []

    @render.ui
    def packages():
        p = state.easi_project()
        if p is None:
            return None
        inst = _installed()
        refs = list(p.evidence or [])
        rows = []
        for i, ref in enumerate(refs):
            st = ev.status(ref, inst)
            fetchable = bool(evidence_base())
            if st == "installed":
                here = ui.span(fa("circle-check"), " Verified", class_="easi-ok")
                action = ui.tags.button("View", type="button", class_="btn btn-outline-secondary btn-sm",
                                        onclick=_evt(ns("pkg_view"), i=i))
            else:
                words = {"damaged": "Damaged: download or import it again",
                         "other": "Another version is here", "missing": "Not on this computer"}[st]
                here = ui.div(ui.span(fa("triangle-exclamation"), " ", words, class_="easi-bad")
                              if st == "damaged" else ui.span(words, class_="easi-muted"),
                              None if fetchable else ui.div("Import its package file.", class_="easi-muted"))
                action = (ui.tags.button("Download", type="button", class_="btn btn-outline-primary btn-sm",
                                         onclick=_evt(ns("pkg_download"), i=i))
                          if fetchable else None)
            roles = ", ".join(ev.ROLE_LABELS.get(r, r) for r in ref.get("roles") or [])
            repro = ref.get("reproducibility") or ""
            rows.append(ui.tags.tr(
                ui.tags.td(ui.div(ref.get("title") or ref["packageId"], class_="easi-strong"),
                           ui.div(f"{ref['packageId']}, {ref.get('version')}", class_="easi-muted")),
                ui.tags.td(roles),
                ui.tags.td(ev.REPRODUCIBILITY_LABELS.get(repro, repro),
                           title=ev.REPRODUCIBILITY_HELP.get(repro, "")),
                ui.tags.td(covers(ref.get("coverage"))),
                ui.tags.td(size_text(ref.get("bytes")), class_="easi-right easi-nowrap",
                           title=(f"Unpacked. The download is {size_text((ref.get('archive') or {}).get('bytes'))}."
                                  if (ref.get("archive") or {}).get("bytes") else "Unpacked")),
                ui.tags.td(here, class_="easi-nowrap"),
                ui.tags.td(action, ui.tags.button("Remove", type="button", class_="btn btn-link btn-sm",
                                                  title="Stop naming this package in the project",
                                                  onclick=_evt(ns("pkg_detach"), i=i)),
                           class_="easi-right easi-nowrap")))
        spare = ev.spare_packages(inst, refs)
        if rows:
            table = ui.tags.table(
                ui.tags.thead(ui.tags.tr(ui.tags.th("Package"), ui.tags.th("Role"),
                                         ui.tags.th("Reproducible"), ui.tags.th("Covers"),
                                         ui.tags.th("Size", class_="easi-right"),
                                         ui.tags.th("Here"), ui.tags.th(""))),
                ui.tags.tbody(*rows), class_="table table-sm easi-table")
        else:
            table = ui.div(fa("box-open"), ui.span(" This project names no development data "
                                                   "packages yet."), class_="easi-empty")
        tools = [ui.tags.button(ui.TagList(fa("file-import"), " Import a package"), type="button",
                                class_="btn btn-outline-secondary btn-sm",
                                onclick=_evt(ns("pkg_import")))]
        if spare:
            tools.append(ui.tags.button(ui.TagList(fa("link"), f" Attach {len(spare)} from this computer"),
                                        type="button", class_="btn btn-outline-secondary btn-sm",
                                        onclick=_evt(ns("pkg_attach_all"))))
        busy = _jobs.get("busy")
        return ui.TagList(table, ui.div(*tools, ui.span(busy, class_="easi-running") if busy else None,
                                        class_="easi-tools"))

    def _ref_at(i):
        p = _get()
        refs = list((p.evidence if p else None) or [])
        return (p, refs[i]) if 0 <= i < len(refs) else (p, None)

    @reactive.effect
    @reactive.event(input.pkg_detach)
    @guard("remove the package reference")
    def _pkg_detach():
        p, ref = _ref_at(int((input.pkg_detach() or {}).get("i", -1)))
        if ref is None:
            return
        _apply(ev.detach(p, ref["packageId"], by=person() or UNNAMED),
               f"The project no longer names {ref.get('title') or ref['packageId']}.")

    @reactive.effect
    @reactive.event(input.pkg_attach_all)
    @guard("attach the packages")
    def _pkg_attach_all():
        p = _get()
        if p is None:
            return
        new = p
        for rec in ev.spare_packages(_installed(), p.evidence):
            new = ev.attach(new, ev.reference(rec["manifest"], package_digest=evs.package_digest(
                rec["manifest"])), by=person() or UNNAMED)
        if new is not p:
            _apply(new, "Attached the packages on this computer.")

    @reactive.effect
    @reactive.event(input.pkg_import)
    @guard("open the import")
    def _pkg_import():
        _target.update(kind="import")
        _modal("Import a development data package",
               ui.p("A package is a .evidence.zip file or its unpacked folder. It is checked file "
                    "by file before the project names it.", class_="mb-2"),
               ui.input_text(ns("pkg_path"), "Package file or folder", width="100%",
                             placeholder="For example: easi-dev-members-2026.09.15-baseline.evidence.zip"),
               apply_id="pkg_import_apply", apply_label="Import", size="m")

    @reactive.effect
    @reactive.event(input.pkg_import_apply)
    @guard("import the package")
    def _pkg_import_apply():
        raw = str(input.pkg_path() or "").strip().strip('"')
        if not raw:
            _modal_err.set("enter the path of a package file or folder")
            return
        path = Path(raw)
        if not path.exists():
            _modal_err.set("nothing is at that path")
            return
        ui.modal_remove()
        _launch(_run_install(path))

    _replacing: dict = {"ref": None, "old": None}

    async def _run_install(path: Path | None, *, ref: dict | None = None):
        label = (ref.get("title") or ref["packageId"]) if ref is not None else path.name
        _jobs["busy"] = f" Downloading {label}..." if ref is not None else f" Checking {label}..."
        _store_tick.set(_store_tick() + 1)
        await st.task_flush()
        try:
            with st.busy(state):
                archive = None
                if ref is not None:
                    target = await asyncio.to_thread(evs.fetch_reference, evidence_base(), ref)
                elif path.is_file():
                    target = await asyncio.to_thread(evs.install_zip, path)
                    archive = {"name": path.name, "sha256": await asyncio.to_thread(evs.sha_file, path),
                               "bytes": path.stat().st_size}
                else:
                    target = await asyncio.to_thread(evs.install_folder, path)
                doc = evs.read_manifest(target)
            title = doc.get("title") or doc["packageId"]
            if ref is not None:
                ui.notification_show(f"{title} is on this computer and verified.", type="message", duration=5)
                return
            with reactive.isolate():
                p = state.easi_project()
            if p is None:
                return
            got = {"packageId": doc["packageId"], "dataDigest": doc["dataDigest"],
                   "packageDigest": evs.package_digest(doc)}
            old = next((e for e in p.evidence if e.get("packageId") == doc["packageId"]), None)
            same_data = bool(old) and old.get("dataDigest") == doc["dataDigest"]
            new_ref = ev.reference(doc, archive=archive or ev.carried_archive(old, got["packageDigest"]),
                                   package_digest=got["packageDigest"])
            if old is not None and not evs.matches(got, old):
                # another version of a package the project names: the author decides
                _replacing.update(ref=new_ref, old=old)
                _modal(f"Replace {title}?",
                       ui.p(f"This project names {title} {old.get('version')}. The package just "
                            f"checked is {doc.get('version')}"
                            + (", with the same data and another description." if same_data
                               else ", with other data.")
                            + " Replace the reference?", class_="mb-2"),
                       _reason_input(placeholder="Why the project should name this version"),
                       apply_id="pkg_replace_apply", apply_label="Replace", size="m")
                return
            new = ev.attach(p, new_ref, by=person() or UNNAMED)
            if new is not p:
                with reactive.isolate():
                    _apply(new, f"The project names {title}.")
            else:
                ui.notification_show(f"{title} is verified on this computer.", type="message", duration=5)
        except evs.EvidenceCancelled:
            ui.notification_show("Download cancelled. It continues from where it stopped next time.",
                                 type="message", duration=6)
        except evs.EvidenceError as exc:
            ui.notification_show(f"The package was not installed: {exc}", type="error", duration=12)
        except Exception as exc:  # noqa: BLE001 - never a silent failure
            logger.exception("evidence package install failed")
            ui.notification_show(f"The package could not be installed ({exc}).", type="error", duration=12)
        finally:
            _jobs["busy"] = None
            _store_tick.set(_store_tick() + 1)
            await st.task_flush()

    @reactive.effect
    @reactive.event(input.pkg_replace_apply)
    @guard("replace the package reference")
    def _pkg_replace_apply():
        new_ref, old = _replacing.get("ref"), _replacing.get("old")
        why = str(input.reason() or "").strip()
        if new_ref is None:
            ui.modal_remove()
            return
        if len(why) < 10:
            _modal_err.set("give a reason of at least 10 characters")
            return
        p = _get()
        if p is None:
            return
        ui.modal_remove()
        _replacing.update(ref=None, old=None)
        _apply(ev.attach(p, new_ref, by=person() or UNNAMED, reason=why),
               f"The project names {new_ref.get('title') or new_ref['packageId']} {new_ref.get('version')}.")

    @reactive.effect
    @reactive.event(input.pkg_download)
    @guard("download the package")
    def _pkg_download():
        p, ref = _ref_at(int((input.pkg_download() or {}).get("i", -1)))
        if ref is None or _jobs.get("busy"):
            return
        _launch(_run_install(None, ref=ref))

    # the package viewer
    _viewing: dict = {"folder": None, "files": [], "doc": None}

    @reactive.effect
    @reactive.event(input.pkg_view)
    @guard("open the package")
    async def _pkg_view():
        p, ref = _ref_at(int((input.pkg_view() or {}).get("i", -1)))
        if ref is None:
            return
        try:
            folder = await asyncio.to_thread(evs.ready, ref)
        except evs.EvidenceError as exc:
            _store_tick.set(_store_tick() + 1)
            ui.notification_show(str(exc), type="error", duration=10)
            return
        doc = evs.read_manifest(folder)
        tables = [rel for rel, rec in doc["files"].items() if rel.endswith((".parquet", ".csv"))]
        _viewing.update(folder=folder, files=tables, doc=doc)
        files = ui.tags.table(
            ui.tags.thead(ui.tags.tr(ui.tags.th("File"), ui.tags.th("Rows", class_="easi-right"),
                                     ui.tags.th("Columns", class_="easi-right"),
                                     ui.tags.th("Size", class_="easi-right"))),
            ui.tags.tbody(*[ui.tags.tr(ui.tags.td(ui.tags.code(rel.split("/", 1)[-1])),
                                       ui.tags.td(f"{rec['rows']:,}" if isinstance(rec.get("rows"), int) else "",
                                                  class_="easi-right"),
                                       ui.tags.td(str(len(rec.get("columns") or [])) if rec.get("columns")
                                                  else "", class_="easi-right"),
                                       ui.tags.td(size_text(rec["bytes"]), class_="easi-right"))
                            for rel, rec in sorted(doc["files"].items())]),
            class_="table table-sm easi-table")
        repro = doc.get("reproducibility") or ""
        facts = _facts([
            ("Version", doc.get("version")),
            ("Role", ", ".join(ev.ROLE_LABELS.get(r, r) for r in doc.get("roles") or [])),
            ("Reproducible", f"{ev.REPRODUCIBILITY_LABELS.get(repro, repro)}: "
                             f"{ev.REPRODUCIBILITY_HELP.get(repro, '')}" if repro else None),
            ("Covers", covers(doc.get("coverage"))),
            ("Shared as", (doc.get("redistribution") or {}).get("status")),
        ])
        sources = [s for s in doc.get("sources") or [] if isinstance(s, dict)]
        technical = ui.tags.details(ui.tags.summary("Technical record", class_="easi-muted"), _facts([
            ("Data digest", doc["dataDigest"]),
            ("Package digest", evs.package_digest(doc)),
            ("Depends on", ", ".join(f"{d.get('packageId')} ({short(d.get('dataDigest'))})"
                                     for d in doc.get("dependsOn") or [])),
        ]))
        lists = []
        for key, title in (("limitations", "Limitations"), ("unavailable", "Not in this package")):
            items = doc.get(key) or []
            if items:
                lists.append(ui.div(title, class_="sc-sec"))
                lists.append(ui.tags.ul(*[ui.tags.li(i if isinstance(i, str) else _unavailable_line(i))
                                          for i in items], class_="easi-list"))
        checks = doc.get("checks") or {}
        preview = None
        if tables:
            preview = ui.TagList(
                ui.div("Data", class_="sc-sec"),
                ui.div(ui.input_select(ns("pkg_table"), None,
                                       {t: t.split("/", 1)[-1] for t in tables}, width="320px"),
                       ui.download_button(ns("pkg_csv"), ui.TagList(fa("file-csv"), " Export as CSV"),
                                          class_="btn btn-outline-secondary btn-sm"),
                       class_="easi-tools"),
                ui.output_ui(ns("pkg_preview")))
        ui.modal_show(ui.modal(
            ui.p(doc.get("description") or "", class_="mb-2"), facts, technical,
            ui.div("Sources", class_="sc-sec") if sources else None,
            ui.tags.ul(*[ui.tags.li(" ".join(str(x) for x in (s.get("citation") or s.get("id"),
                                                             f"({s['path']})" if s.get("path") else "") if x))
                         for s in sources], class_="easi-list") if sources else None,
            ui.div("Checks", class_="sc-sec") if checks else None,
            ui.tags.ul(*[ui.tags.li(x) for x in check_lines(checks)], class_="easi-list")
            if checks else None,
            *lists, ui.div("Files", class_="sc-sec"), files, preview,
            title=doc.get("title") or doc["packageId"], size="xl", easy_close=True,
            footer=ui.modal_button("Close", class_="btn btn-outline-secondary")))

    def _table_path():
        try:
            rel = input.pkg_table()
        except Exception:  # noqa: BLE001 - no viewer open
            return None
        folder = _viewing.get("folder")
        if not folder or rel not in _viewing.get("files", []):
            return None
        return Path(folder) / rel

    # suspend_when_hidden=False: dialog outputs bind while the modal is still hidden
    # (Bootstrap fade) and a suspended output never resumes (DEEP documents the same trap)
    @output(suspend_when_hidden=False)
    @render.ui
    def pkg_preview():
        path = _table_path()
        if path is None:
            return None
        import pyarrow.parquet as pq
        if path.suffix == ".parquet":
            pf = pq.ParquetFile(path)
            cols = pf.schema_arrow.names
            head = pf.read_row_group(0, columns=cols[:14]).slice(0, 15).to_pylist()
            n = pf.metadata.num_rows
        else:
            import csv
            with path.open(encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                cols = reader.fieldnames or []
                head = [dict(r) for _, r in zip(range(15), reader)]
            n = None
        shown = cols[:14]

        def cell(v):
            if isinstance(v, float):
                return "" if v != v else f"{v:.6g}"
            return "" if v is None else str(v)

        return ui.TagList(
            ui.div(f"First {len(head)} of {n:,} rows" if n is not None else f"First {len(head)} rows",
                   (f", {len(shown)} of {len(cols)} columns" if len(cols) > len(shown) else ""),
                   class_="easi-muted mb-1"),
            ui.div(ui.tags.table(ui.tags.thead(ui.tags.tr(*[ui.tags.th(c) for c in shown])),
                                 ui.tags.tbody(*[ui.tags.tr(*[ui.tags.td(cell(r.get(c))) for c in shown])
                                                 for r in head]),
                                 class_="table table-sm easi-table easi-preview"),
                   class_="easi-scroll"))

    @render.download(filename=lambda: (_table_path() or Path("table.csv")).stem + ".csv")
    def pkg_csv():
        path = _table_path()
        if path is None:
            return
        rel = str(path.relative_to(Path(_viewing["folder"]))).replace("\\", "/")
        # the manifest that was verified when the viewer opened, never one read again from disk
        rec = ((_viewing.get("doc") or {}).get("files") or {}).get(rel) or {}
        if evs.sha_file(path) != rec.get("sha256"):
            why = f"{rel} no longer matches its package. Download or import the package again."
            ui.notification_show(why, type="error", duration=10)
            # raised, not returned: a download that ends without a byte would be saved as an empty
            # file, while an error mid-stream leaves the transfer unfinished and the browser drops it
            raise evs.EvidenceError(why)
        import pyarrow.csv as pacsv
        import pyarrow.parquet as pq
        import io as _io
        if path.suffix == ".csv":
            yield path.read_bytes()
            return
        pf = pq.ParquetFile(path)
        for i in range(pf.num_row_groups):
            buf = _io.BytesIO()
            pacsv.write_csv(pf.read_row_group(i), buf,
                            write_options=pacsv.WriteOptions(include_header=(i == 0)))
            yield buf.getvalue()

    # refitting the operational curves from the packages
    @render.ui
    def refit_box():
        p = state.easi_project()
        if p is None:
            return None
        inst = _installed()
        need = {r["packageId"]: r for r in (p.evidence or []) if r["packageId"] in ("easi-dev-members",
                                                                                  "easi-dev-fits")}
        ready = len(need) == 2 and all(ev.status(r, inst) == "installed" for r in need.values())
        if not ready:
            return None
        res = _refit()
        body = [ui.p("Refit the 34 curves from the member values in these packages, with the "
                     "recipe that produced them, and compare the fits with this project's curves.",
                     class_="mb-2")]
        if res and res.get("packageDigest") == p.package_digest:
            cmp_ = res["curves"]
            rec = res.get("recipe") or {}
            if rec and not rec.get("same"):
                body.append(ui.div(fa("triangle-exclamation"), " The curve engine or fit settings here differ from "
                                   "the ones the package records (" + ", ".join(rec.get("differences") or []) +
                                   "), so the refit is not expected to match exactly.", class_="easi-note mb-2"))
            if cmp_["allIdentical"]:
                body.append(ui.div(fa("circle-check"), f" All {cmp_['curves']} curves are exactly their "
                                   f"fits ({res['seconds']} s).", class_="easi-ok mb-2"))
            else:
                body.append(ui.div(f"{cmp_['identical']} of {cmp_['curves']} curves are exactly their "
                                   f"fits ({res['seconds']} s).", class_="mb-1"))
                body.append(ui.tags.ul(*[ui.tags.li(
                    f"{d['curve']}: " + ", ".join(
                        f"{k} differs by {v:.6g}" if isinstance(v, (int, float)) else f"{k} differs"
                        for k, v in d["diffs"].items()))
                    for d in cmp_["differing"]] + [ui.tags.li(f"{m}: no usable fit") for m in cmp_["missing"]],
                    class_="easi-list mb-2"))
        body.append(ui.span(fa("spinner"), " Refitting...", class_="easi-running") if _jobs.get("refit")
                    else ui.tags.button(ui.TagList(fa("rotate"), " Refit the curves"), type="button",
                                        class_="btn btn-outline-primary btn-sm",
                                        onclick=_evt(ns("refit_run"))))
        return ui.TagList(ui.div("Refit", class_="sc-sec"), ui.div(*body, class_="easi-card"))

    @reactive.effect
    @reactive.event(input.refit_run)
    @guard("refit the curves")
    def _refit_run():
        p = _get()
        if p is None or _jobs.get("refit"):
            return
        _launch(_run_refit(p))

    async def _run_refit(p):
        from streamcurves.easi_method import refit as rf
        _jobs["refit"] = True
        _store_tick.set(_store_tick() + 1)
        await st.task_flush()
        try:
            with st.busy(state):
                members_dir = await asyncio.to_thread(
                    evs.ready, next(e for e in p.evidence if e["packageId"] == "easi-dev-members"))

                def work():
                    import time as _time
                    t0 = _time.perf_counter()
                    members, values, panels = rf.load_members(members_dir)
                    rows = rf.fit_registry(members, values, panels, quantities=(
                        "natural_wsrp100", "woody_wsrp100", "q_cv_monthly", "er_median"))
                    curves = rf.operational_curves(rows, members, values, panels)
                    return {"curves": rf.compare_curves(curves, p.curves()),
                            "recipe": rf.recipe_check(members_dir),
                            "seconds": round(_time.perf_counter() - t0, 1),
                            "packageDigest": p.package_digest}

                res = await asyncio.to_thread(work)
            with reactive.isolate():
                _refit.set(res)
        except Exception as exc:  # noqa: BLE001 - the task says what happened
            logger.exception("EASI refit failed")
            ui.notification_show(f"The refit could not run: {exc}", type="error", duration=10)
        finally:
            _jobs["refit"] = False
            _store_tick.set(_store_tick() + 1)
            await st.task_flush()

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

    # ── 4. Final selection: select final methods ─────────────────────────────
    _cmp = reactive.value([])            # candidate keys compared side by side (up to 3)
    _open_fns: set = set()               # functions whose row is open, kept across re-renders

    def _alt_source(c) -> str:
        ref = (c.get("identity") or {}).get("sourceRef") or {}
        if ref.get("alternative"):
            return ref["alternative"].replace("alternative-", "Alternative ")
        if ref.get("criteriaSet") == "legacy":
            return "Legacy criteria"
        if (c.get("identity") or {}).get("sourceKind") == "sqt":
            return f"{ref.get('state')} SQT"
        return "This method"

    def _alt_tiles(p, c):
        """One thumbnail per curve family the candidate reads, every stratum overlaid."""
        try:
            d = alts.definition_of(p, c)
        except Exception:  # noqa: BLE001 - an SQT candidate carries points, not a method
            d = {"curveSets": {}}
        out = []
        for name, s in (d.get("curveSets") or {}).items():
            strata = [{"label": k, "points": [(float(x), float(y)) for x, y in (v or {}).get("points") or []]}
                      for k, v in sorted((s.get("curves") or {}).items())]
            out.append(ui.div(ui.div(f"{es.family_name(name)} (by "
                                     f"{es.STRATIFIER_NAMES.get(s.get('stratifier'), s.get('stratifier'))}, "
                                     f"{len(strata)} curves)", class_="easi-muted"),
                              ui.HTML(curve_svg.tile_svg({"metric": name, "display_name": name, "strata": strata,
                                                   "reference_range": (None, None), "domain": None},
                                                  w=260, h=150))))
        pts = ((c.get("definition") or {}).get("points") or [])
        if not out and pts:
            out.append(ui.HTML(curve_svg.tile_svg({"metric": "sqt", "display_name": c.get("label"),
                                            "strata": [{"label": None, "points": [(q["x"], q["y"]) for q in pts]}],
                                            "reference_range": (None, None), "domain": None}, w=260, h=150)))
        if not out:
            m = (d.get("method") or {})
            out.append(ui.div(f"{m.get('title') or m.get('methodKey') or ''}: scored by "
                              f"{OPERATOR_LABELS.get(m.get('operator'), m.get('operator') or 'its criteria')}, "
                              "no reference curve.", class_="easi-muted"))
        return out

    def _selection_stage(p):
        rows = reg.status_rows(p)
        studies = (p.register or {}).get("studies") or []
        cands = {c["candidateKey"]: c for c in (p.register or {}).get("candidates", [])}
        with reactive.isolate():
            opened = set(_open_fns)
        cmp_keys = [k for k in _cmp() if k in cands]
        n_pending = sum(1 for r in rows if r["needsReview"])
        lead = (f"{n_pending} function" + (" changed" if n_pending == 1 else "s changed")
                + " after its method was selected. Review the consequences, then confirm each "
                  "one with a reason." if n_pending else
                "Every function has its selected method. Open a function to see the alternatives "
                "considered for it.")
        parts = [ui.p(lead, class_="easi-note mt-0")]
        for s in studies:
            parts.append(ui.div(
                fa("book-open"), " Alternatives from the controlled study of 2026-09-15 (receipts "
                "verified). Its rule recommended "
                f"{str(s.get('recommendation') or 'no alternative').replace('alternative-', 'Alternative ')}; "
                f"the owner adopted {str(s.get('adopted') or '').replace('alternative-', 'Alternative ')} "
                f"on {(s.get('adoption') or {}).get('date')}.", class_="fs-note",
                title=f"Study completion record sha256 {s.get('completionSha256')}"))
        if not studies and alts.STUDY_DIR.is_dir() and p.is_revision():
            parts.append(ui.div(
                _btn(ns("alt_import"), ui.TagList(fa("download"), " Import the 2026-09-15 study alternatives"),
                     "btn btn-outline-secondary btn-sm"), class_="easi-tools"))
        if cmp_keys:
            cols = []
            for k in cmp_keys:
                c = cands[k]
                cols.append(ui.div(ui.div(c.get("label") or "", class_="fs-cmp-title"),
                                   ui.div(_alt_source(c), class_="easi-muted"), *_alt_tiles(p, c),
                                   class_="fs-cmp-col"))
            parts.append(ui.div(ui.div(ui.tags.strong("Compare"),
                                       ui.tags.span(f"{len(cols)} of 3", class_="fs-count"),
                                       _btn(ns("alt_cmp_clear"), "Stop comparing", "btn btn-link btn-sm"),
                                       class_="fs-cmp-head"),
                                ui.div(*cols, class_="fs-cmp-cols"), class_="fs-compare"))
        items = []
        for i, r in enumerate(rows):
            selected = cands.get(r["selectedCandidate"]) if r["selectedCandidate"] else None
            others = [x for x in r["rows"] if x["candidateKey"] != r["selectedCandidate"]]
            flags = []
            if r["needsReview"]:
                flags.append(ui.span("Needs your confirmation", class_="sc-tag is-attention"))
            summary = ui.tags.summary(
                ui.div(ui.tags.span(r["functionName"], class_="fs-fn-name"),
                       ui.tags.span(r["method"], class_="fs-fn-disc"), class_="fs-fn-head"),
                ui.div(ui.tags.span(ui.tags.span(_alt_source(selected) if selected else "Nothing selected",
                                                 class_="fs-chip-name"),
                                    ui.tags.span(r.get("who") if r["decidedBy"] == "person" and r.get("who")
                                                 else DECIDED_LABELS.get(r["decidedBy"], r["decidedBy"] or ""),
                                                 class_="fs-chip-kind"), class_="fs-chip"),
                       class_="fs-chips"),
                ui.div(ui.tags.span(f"{len(others)} considered" if others else "No alternatives",
                                    class_="fs-count"), *flags, class_="fs-fn-meta"),
                class_="fs-fn-summary")
            trs = []
            for j, x in enumerate(r["rows"]):
                c, d = x["candidate"], x["decision"] or {}
                on = x["candidateKey"] in cmp_keys
                acts = [ui.tags.button(ui.TagList(fa("check" if on else "table-columns"),
                                                  " Comparing" if on else " Compare"), type="button",
                                       class_="btn btn-sm " + ("btn-primary" if on else "btn-outline-secondary"),
                                       onclick=_evt(ns("alt_cmp"), i=i, j=j),
                                       **{"aria-pressed": "true" if on else "false"})]
                if x["status"] == "eligible_not_selected" and p.is_revision() \
                        and (c.get("identity") or {}).get("sourceKind") == "imported_alternative":
                    acts.append(ui.tags.button(ui.TagList(fa("circle-check"), " Select"), type="button",
                                               class_="btn btn-sm btn-outline-primary",
                                               onclick=_evt(ns("alt_select"), i=i, j=j)))
                if x["status"] == "selected" and r["needsReview"]:
                    acts.append(ui.tags.button("Confirm…", type="button", class_="btn btn-sm btn-outline-primary",
                                               onclick=_evt(ns("confirm_open"), i=i)))
                trs.append(ui.tags.tr(
                    ui.tags.td(ui.div(_alt_source(c), class_="fs-name"),
                               ui.div(c.get("label") or "", class_="fs-kind")),
                    ui.tags.td(ui.tags.span(cands_mod.STATUS_LABELS.get(x["status"], x["status"]),
                                            class_="fs-status " + {"selected": "is-selected",
                                                                   "excluded": "is-excluded"}.get(x["status"], "is-eligible"))),
                    ui.tags.td(ui.div(d.get("reason") or "", class_="fs-reason"),
                               *[ui.div(x, class_="fs-rule") for x in (c.get("limitations") or [])[:3]]),
                    ui.tags.td(", ".join(v for v in ((d.get("who") if d.get("decidedBy") == "person" else
                                                      DECIDED_LABELS.get(d.get("decidedBy"), d.get("decidedBy") or "")),
                                                     str(d.get("when") or "")[:10]) if v), class_="fs-who"),
                    ui.tags.td(ui.div(*acts, class_="fs-actions"))))
            body = ui.tags.table(ui.tags.thead(ui.tags.tr(ui.tags.th("Method"), ui.tags.th("Status"),
                                                          ui.tags.th("Why"), ui.tags.th("Decided"),
                                                          ui.tags.th(""))),
                                 ui.tags.tbody(*trs), class_="table table-sm fs-table")
            toggle = (f"Shiny.setInputValue('{ns('alt_open')}',{{i:{i},open:this.open}},{{priority:'event'}})")
            attrs = {"open": ""} if r["functionId"] in opened else {}
            items.append(ui.tags.details(summary, ui.div(body, class_="fs-fn-body"),
                                         class_="fs-fn" + (" is-gap" if r["needsReview"] else ""),
                                         ontoggle=toggle, **attrs))
        parts.append(ui.div(*items, class_="fs-functions"))
        return ui.TagList(*parts)

    def _alt_at(p, payload):
        rows = reg.status_rows(p)
        i, j = int(payload.get("i", -1)), int(payload.get("j", -1))
        if not 0 <= i < len(rows) or not 0 <= j < len(rows[i]["rows"]):
            return None, None
        return rows[i], rows[i]["rows"][j]

    @reactive.effect
    @reactive.event(input.alt_open)
    @guard("track the open functions")
    def _alt_open():
        p = _get()
        rows = reg.status_rows(p) if p is not None else []
        v = input.alt_open() or {}
        i = int(v.get("i", -1))
        if 0 <= i < len(rows):
            (_open_fns.add if v.get("open") else _open_fns.discard)(rows[i]["functionId"])

    @reactive.effect
    @reactive.event(input.alt_cmp)
    @guard("compare the methods")
    def _alt_cmp():
        p = _get()
        r, x = _alt_at(p, input.alt_cmp() or {})
        if x is None:
            return
        _open_fns.add(r["functionId"])
        cur = list(_cmp())
        k = x["candidateKey"]
        if k in cur:
            cur.remove(k)
        elif len(cur) >= 3:
            ui.notification_show("Compare at most 3 at a time. Stop comparing one first.",
                                 type="warning", duration=5)
            return
        else:
            cur.append(k)
        _cmp.set(cur)

    @reactive.effect
    @reactive.event(input.alt_cmp_clear)
    @guard("stop comparing")
    def _alt_cmp_clear():
        _cmp.set([])

    @reactive.effect
    @reactive.event(input.alt_select)
    @guard("open the selection")
    def _alt_select_open():
        p = _get()
        r, x = _alt_at(p, input.alt_select() or {})
        if x is None:
            return
        moves = alts.affected(p, x["candidateKey"])
        names = {row["functionId"]: row["functionName"] for row in reg.status_rows(p)}
        _target.update(kind="alt_select", key=x["candidateKey"])
        _open_fns.add(r["functionId"])
        _modal(f"Select for {r['functionName']}",
               ui.p(f"{r['functionName']} takes {_alt_source(x['candidate'])}'s definition in this draft."
                    + (" These functions read the same curves and move with it: "
                       + ", ".join(names.get(m["functionId"], m["functionId"]) for m in moves[1:]) + "."
                       if len(moves) > 1 else ""), class_="mb-2"),
               ui.p("Its curves and criteria replace the draft's for these functions; the method it "
                    "replaces stays in the register as eligible, not selected. Selecting the method this "
                    "draft started from puts every byte of it back.", class_="easi-note"),
               ui.input_text(ns("who"), "Your name", value=person(), width="100%"),
               _reason_input(label="Reason (recorded with the decision)", placeholder="Why this method"),
               apply_id="alt_select_apply", apply_label="Select", size="m")

    @reactive.effect
    @reactive.event(input.alt_select_apply)
    @guard("select the method")
    def _alt_select_apply():
        p = _get()
        if _target.get("kind") != "alt_select":
            return
        try:
            new = alts.adopt(p, str(_target.get("key")), by=str(input.who() or ""),
                             reason=str(input.reason() or ""), at=_now())
        except (ValueError, alts.AlternativeError) as exc:
            _modal_err.set(str(exc))
            return
        ui.modal_remove()
        _apply(new, "Selected. Preview the consequences before you publish.")

    @reactive.effect
    @reactive.event(input.alt_import)
    @guard("import the study alternatives")
    def _alt_import():
        p = _get()
        try:
            new = alts.import_alternatives(p, alts.STUDY_DIR, imported_by=person(), at=_now())
        except (OSError, ValueError, alts.AlternativeError) as exc:
            ui.notification_show(str(exc), type="warning", duration=8)
            return
        _apply(new, "Imported the study's alternatives. Nothing the method scores changed.")

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
                        "they score with. Downloading it changes nothing in EASI. When the "
                        "maintainer activates a changed method in EASI, new assessments use it, and "
                        "the stored nationwide results stay those of the earlier method until they "
                        "are recomputed.", class_="mb-2"),
                   ui.download_button(ns("dl_package2"), ui.TagList(fa("file-zipper"),
                                                                    " Download method package"),
                                      class_="btn btn-outline-secondary btn-sm"),
                   class_="easi-card"),
            ui.div("Publish", class_="sc-sec"),
            _publish_form(p, pending))

    @render.download(filename=lambda: _package_name())
    def dl_package2():
        yield _package_blob(_get())

    def _publish_form(p, pending):
        _pub_tick()
        published = is_published(p)
        if published:
            return ui.div(ui.p(fa("circle-check"), " This version is in the assessment library.",
                               class_="mb-0"), class_="easi-card")
        block = publish_block_reason()
        if block == "maintainer":
            return ui.div(ui.p("Versions are published to the STAF assessment library by its "
                               "maintainer. Send them this project file.",
                               class_="mb-0"), class_="easi-card")
        if block:
            return ui.div(ui.p(block, class_="mb-0"), class_="easi-card")
        ver = int(p.meta.get("version") or 1)
        problems = _publish_problems(p, pending)
        next_v = _next_library_version()
        renumber = (_btn(ns("renumber"), f"Renumber as v{next_v}", "btn btn-outline-primary btn-sm")
                    if p.is_revision() and ver != next_v else None)
        form = ui.div(
            ui.input_radio_buttons(ns("pub_status"), "Publish as",
                                   {"draft": "Draft", "preliminary": "Preliminary"},
                                   selected=_drafts.get("pub_status") or "draft", inline=True),
            ui.input_text_area(ns("pub_notes"), "Revision notes", value=_drafts.get("pub_notes") or "",
                               placeholder="What changed and why", rows=3, width="100%"),
            ui.div(f"Recorded as published by {person()} into {lib.library_root()}." if person() else
                   f"Publishes into {lib.library_root()}; no name is set yet.", class_="easi-muted mb-2"),
            _btn(ns("publish"), ui.TagList(fa("cloud-arrow-up"), f" Publish v{ver}"),
                 "btn btn-primary btn-sm", **({"disabled": "disabled"} if problems else {})),
            class_="easi-card")
        if problems:
            return ui.TagList(ui.div(*[ui.div(fa("circle-exclamation"), " ", x) for x in problems],
                                     renumber, class_="easi-problems"), form)
        return form

    def _next_library_version() -> int:
        man = lib.read_manifest(eio.ASSESSMENT_ID) or {}
        return int(man.get("latestVersion") or 0) + 1

    def _publish_problems(p, pending=None) -> list[str]:
        """Why this project cannot be published yet: the form shows these and the publish
        handler refuses on the same list, so a click can never skip one."""
        if pending is None:
            pending = reg.needs_review(p)
        next_v = _next_library_version()
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
        if not person():
            problems.append("Say who publishes: set your name as Prepared by in the Project "
                            "panel, or STAF_LIBRARY_MAINTAINER.")
        return problems

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
        new = eio.fork(p, by=person() or UNNAMED, version=next_version(p))
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

    # suspend_when_hidden=False: dialog outputs bind while the modal is still hidden
    # (Bootstrap fade) and a suspended output never resumes (DEEP documents the same trap)
    @output(suspend_when_hidden=False)
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
            new = edit.set_curve_points(p, name, stratum, pts, by=person() or UNNAMED,
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
                                     owner=input.edge_owner(), by=person() or UNNAMED, reason=reason)
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
                                          float(poor), by=person() or UNNAMED, reason=reason)
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
        method = next((m for m in p.catalog().get("methods", []) if m.get("methodKey") == r["methodKey"]), {})
        reads = set(reg.curve_sets_used(method))
        changes = [describe(h, names) for h in history_since_origin(p)
                   if (h.get("target") or {}).get("methodKey") == r["methodKey"]
                   or (h.get("action") == "set_curve_points" and (h.get("target") or {}).get("set") in reads)
                   or (h.get("action") == "adopt_candidate"
                       and r["functionId"] in ((h.get("target") or {}).get("functions") or []))]
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
                ui.notification_show(_plain_failure(exc), type="error", duration=12)
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
        problems = _publish_problems(p)
        if problems:
            ui.notification_show("Not published: " + " ".join(problems), type="warning", duration=10)
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

    @reactive.effect
    @reactive.event(input.renumber)
    @guard("renumber the revision")
    def _renumber():
        p = _get()
        if p is None or not p.is_revision():
            return
        to = _next_library_version()
        was = int(p.meta.get("version") or 1)
        if to == was:
            return
        new = p.copy()
        new.meta["version"] = to
        edit.restamp_identity(new)
        new.history.append({"action": "renumber", "at": _now(), "by": person() or "",
                            "kind": "lifecycle", "reason": f"v{was} became v{to}: the library "
                            f"published v{to - 1} after this revision started",
                            "target": {"from": was, "to": to}})
        _apply(new, f"This revision is now v{to}.")


__all__ = ["easi_page_ui", "easi_page_server", "easi_view", "curve_list", "band_rules",
           "regional_rules", "curve_tile", "describe", "names_of", "history_since_origin",
           "is_published"]
