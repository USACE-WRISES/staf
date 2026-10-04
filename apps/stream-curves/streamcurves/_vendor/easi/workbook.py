"""EASI's workbook and summary: the calculator once per scenario, then the Summary and
ReferenceCurves tabs (the shared ``libs/staf_workbook``, vendored in ``_vendor``).

Presentation code, outside the method digest. The calculator stays the active method's own
workbook (``calculator.blank_bytes``); each scenario's tab is a copy of its EASI Score sheet with
that scenario's screening typed in (the values the engine rated, the override scores and the
notes), and the sheets that compute from it are copied too, hidden for the alternatives.
ReferenceCurves shows the curves this site was scored against (one per curve metric, for its
stratum) and the rating bands of the rule-based metrics.
"""
from __future__ import annotations

import datetime as _dt
import io
import re
import zipfile

from . import calculator, config, screening_methods
from ._vendor.staf_workbook.assemble import AssemblySpec, ScenarioInput, assemble
from ._vendor.staf_workbook.model import compare as cmp_model
from ._vendor.staf_workbook.model.compare import Measure, ScenarioScores
from ._vendor.staf_workbook.model.refcurves import CurveBlock, RefCurves, TableBlock
from ._vendor.staf_workbook.model.summary import L1_NAMES, NARS9_NAMES, SummaryInfo, regions_for_l3
from ._vendor.staf_workbook.sheets import SummaryCells

SPEC = AssemblySpec("EASI", calculator.SHEET_NAME, force_recalc=True)
SUB_NAMES = {"physical": "sub_index_physical", "chemical": "sub_index_chemical",
             "biological": "sub_index_biological"}
INDEX_DIGITS, FUNCTION_DIGITS = 2, 0
#: The quantity each curve set scores, as a chart axis reads it
CURVE_X = {"flow-variability": "Monthly flow variability (ratio)", "entrenchment": "Entrenchment ratio",
           "corridor-woody": "Woody riparian cover (%)", "corridor-natural": "Natural riparian cover (%)"}
SLOPE_TEXT = {"lt_0.5": "slope under 0.5%", "0.5_to_2": "slope 0.5 to 2%", "ge_2": "slope 2% or more"}


def functions() -> list:
    """``[(function id, name, category), ...]`` in the worksheet's order."""
    return [(f["id"], f.get("name") or f["id"], f.get("category", "")) for f in config.functions()]


def scenario_scores(sc) -> ScenarioScores:
    """A scenario's scores as the comparison and the Summary tab read them."""
    if not sc:
        return ScenarioScores()
    fs = dict((fid, s) for fid, s in (sc.get("functionScores") or {}).items() if s is not None)
    if not fs:
        return ScenarioScores()
    eci = sc.get("ecosystemConditionIndexRaw")
    if eci is None:
        eci = sc.get("ecosystemConditionIndex")
    subs = sc.get("subIndicesRaw") or sc.get("subIndices") or {}
    return ScenarioScores(Measure() if eci is None else Measure(float(eci)),
                          dict((k, Measure() if v is None else Measure(float(v))) for k, v in subs.items()),
                          dict((fid, Measure(float(s))) for fid, s in fs.items()))


def comparison(names: list, reports: list):
    """The scenarios side by side, from each one's scored report."""
    return cmp_model.build(names, [scenario_scores(r) for r in reports], functions(),
                           index_digits=INDEX_DIGITS, function_digits=FUNCTION_DIGITS)


def _named_cells(template: bytes) -> dict:
    """Defined name -> (sheet, cell) for the workbook-level names of the calculator."""
    with zipfile.ZipFile(io.BytesIO(template)) as archive:
        book = archive.read("xl/workbook.xml").decode("utf-8")
    out = {}
    for attrs, target in re.findall(r"<definedName\b([^>]*)>([^<]*)</definedName>", book):
        if "localSheetId=" in attrs:
            continue
        name = re.search(r'\bname="([^"]+)"', attrs)
        got = re.match(r"^'?(.+?)'?!\$?([A-Z]+)\$?(\d+)$", calculator._xml_unescape(target))
        if name and got:
            out[name.group(1)] = (got.group(1).replace("''", "'"), got.group(2) + got.group(3))
    return out


def summary_cells(template: bytes) -> SummaryCells:
    """Where each scenario's results sit: the calculator's ECI and outcome cells, and each
    function's score cell (``mNN_score``, NN its metric's catalog position)."""
    names = _named_cells(template)
    position = dict((mid, n) for n, mid in enumerate(config.metrics_by_id(), 1))
    metric_of = dict((m.get("functionId"), mid) for mid, m in config.metrics_by_id().items())
    fns = []
    for fid, label, cat in functions():
        n = position.get(metric_of.get(fid))
        key = f"m{n:02d}_score" if n else None
        if key in names:
            fns.append((fid, label, cat, names[key]))
    return SummaryCells(eci=names["eci"], sub=dict((k, names[v]) for k, v in SUB_NAMES.items()),
                        functions=fns, index_digits=INDEX_DIGITS, function_digits=FUNCTION_DIGITS)


def _regions(strata: dict) -> dict:
    """EASI's own answers (its Level I to III and NARS-9 polygons), named from the shared table."""
    strata = strata or {}
    out = regions_for_l3(strata.get("l3"))
    l2 = strata.get("l2")
    if l2 and str(l2) != str(out.get("l2") or ""):
        out["l2"], out["l2_name"] = str(l2), (str(strata.get("l2_name") or "").title() or None)
    if strata.get("l1"):
        out["l1"] = str(strata["l1"])
        out["l1_name"] = L1_NAMES.get(out["l1"])
    if strata.get("nars9"):
        out["nars9"] = str(strata["nars9"])
        out["nars9_name"] = NARS9_NAMES.get(out["nars9"])
    return out


def summary_info(result: dict) -> SummaryInfo:
    result = result or {}
    d = result.get("delineation") or {}
    report = result.get("report") or {}
    site = calculator.site_identity(result)
    order = d.get("stream_order")
    if order is None:       # an engine-delineated site keeps it in the Basin characteristics only
        order = next((row[1] for row in (report.get("basin") or {}).get("rows") or []
                      if isinstance(row, (list, tuple)) and len(row) == 2 and row[0] == "Stream order"), None)
    return SummaryInfo("EASI", site.get("name") or "(unnamed stream)", d.get("reach_length_ft"),
                       d.get("drainage_area_sqkm"), order, _regions(report.get("strata") or {}))


def _stratum_text(definition: dict, stratum: str) -> str:
    if stratum == "national":
        return "national curve"
    kind = definition.get("stratifier")
    if kind == "slope_class":
        return SLOPE_TEXT.get(stratum, f"slope class {stratum}")
    if kind == "nars9":
        name = NARS9_NAMES.get(stratum)
        return f"NARS-9 region {stratum}" + (f" ({name})" if name else "")
    if kind == "l2":
        return f"Level II ecoregion {stratum}"
    return str(stratum)


def reference_curves(report: dict) -> RefCurves:
    """The curve each curve metric was scored against for this site, then the rating bands of the
    metrics rated by rule. Scenarios change ratings, never the curves, so one set serves all."""
    sets = screening_methods.curve_sets()
    blocks, bands = [], []
    for mkey, row in calculator._catalog_rows(report or {}):
        trace = row.get("scoring") or {}
        function = row.get("functionName") or row.get("name") or mkey
        drawn = False
        for name, used in (trace.get("curves") or {}).items():
            set_id, stratum = (used or {}).get("set"), str((used or {}).get("stratum"))
            definition = sets.get(set_id) or {}
            curve = (definition.get("curves") or {}).get(stratum) or {}
            points = [(float(p[0]), float(p[1])) for p in curve.get("points") or []
                      if isinstance(p, (list, tuple)) and len(p) == 2]
            if not points:
                continue
            n = (used or {}).get("n") or curve.get("n")
            subtitle = f"{function}. {_stratum_text(definition, stratum).capitalize()}"
            if n:
                subtitle += f", {int(n):,} reference reaches"
            label = next((f"{i.get('label')} ({i.get('units')})" if i.get("units") else str(i.get("label"))
                          for i in trace.get("inputs") or [] if name != "method" and i.get("key") == name), None)
            blocks.append(CurveBlock(row.get("name") or function, subtitle, label or CURVE_X.get(set_id, set_id),
                                     "Index", [(_stratum_text(definition, stratum).capitalize(), points)]))
            drawn = True
        if drawn:
            continue
        for crit in ((row.get("methodCriteria") or {}).get("automated") or []):
            b = crit.get("bands") or {}
            if b:
                unit = crit.get("units")
                bands.append([function, str(crit.get("label") or crit.get("input") or "") + (f" ({unit})" if unit else ""),
                              b.get("Good", ""), b.get("Fair", ""), b.get("Poor", "")])
    if bands:
        blocks.append(TableBlock("Rating bands", ["Function", "Measure", "Good", "Fair", "Poor"], bands,
                                 widths=[30, 34, 18, 18, 18]))
    return RefCurves("The reference curves this site was scored against, and the rating bands of the other "
                     "metrics.", blocks)


def scenario_result(base: dict, report: dict, notes: dict) -> dict:
    """The result dict the calculator reads for one scenario: the screening with that scenario's
    scored report and the shared notes."""
    rows = [{**r, "userNote": (notes or {}).get(r["metricId"], "")} for r in report.get("metricRows") or []]
    return {**(base or {}), "report": {**report, "metricRows": rows}}


def build(base: dict, scenarios: list, notes: dict, *, today: _dt.date | None = None,
          template: bytes | None = None) -> bytes:
    """The workbook for ``scenarios`` = [(Scenario, scored report), ...], Existing Conditions first."""
    today = today or _dt.date.today()
    template = template if template is not None else calculator.blank_bytes()
    inputs, scores = [], []
    for s, report in scenarios:
        values, notes_ref = calculator.filled_values(scenario_result(base, report, notes), today=today)
        inputs.append(ScenarioInput(s.id, s.name, s.description,
                                    fill=lambda xml, v=values, r=notes_ref: calculator.fill_sheet(xml, v, r)))
        scores.append(scenario_scores(report))
    first = scenarios[0][1] if scenarios else (base or {}).get("report") or {}
    return assemble(template, SPEC, inputs, summary=summary_info({**(base or {}), "report": first}),
                    cells=summary_cells(template), scores=scores, curves=reference_curves(first),
                    generated_on=today)
