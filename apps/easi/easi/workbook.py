"""EASI's workbook and summary: the calculator's Instructions, the Summary, then the calculator once
per scenario, each tab named after its scenario (the shared ``libs/staf_workbook``, vendored in
``_vendor``). It opens on Instructions. The layout follows SFARI's (owner, 2026-10-23): no
ReferenceCurves tab (the template's Reference sheet draws every curve and Metrics shows the Good,
Fair and Poor ranges each value was rated against), and the Summary's site block right under its
title with Reach name and NARS-9 region read live from Existing Conditions, then a gap.

Presentation code, outside the method digest. The template is the app's own
(``calculator.template_bytes``); each scenario's tab is a copy of its Scenario sheet with that
scenario's screening typed in (the values the engine rated, the override scores and the notes), and
the sheets that compute from it are copied too, hidden for the alternatives. The blank workbook is
the same with one empty sheet named "Scenario" (``build_blank``).
"""
from __future__ import annotations

import datetime as _dt
import io
import re
import zipfile

from . import calculator, config
from ._vendor.staf_workbook.assemble import AssemblySpec, ScenarioInput, assemble
from ._vendor.staf_workbook.model import compare as cmp_model
from ._vendor.staf_workbook.model.compare import Measure, ScenarioScores
from ._vendor.staf_workbook.model.summary import L1_NAMES, NARS9_NAMES, SummaryInfo, regions_for_l3
from ._vendor.staf_workbook.sheets import SummaryCells

#: the workbook opens on the template's Instructions, ahead of Summary (owner, 2026-10-19)
SPEC = AssemblySpec("EASI", calculator.SHEET_NAME, force_recalc=True, lead=("Instructions",))
SUB_NAMES = {"physical": "sub_index_physical", "chemical": "sub_index_chemical",
             "biological": "sub_index_biological"}
INDEX_DIGITS, FUNCTION_DIGITS = 2, 0
#: the Summary's rows read live from Existing Conditions: the scenario sheet's Reach ID and NARS-9
#: region entries (owner, 2026-10-23, as SFARI's Reach name and Reach length)
SITE_CELLS = {"Reach name": "site_name", "NARS-9 region": "ctx_region"}


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


def summary_cells(template: bytes, stored: dict | None = None) -> SummaryCells:
    """Where each scenario's results sit: the calculator's ECI and outcome cells, each function's
    score cell (``mNN_score``, NN its metric's catalog position), and the site rows the Summary reads
    live; ``stored`` (cell -> value, what Existing Conditions holds for this site) gives the values
    those rows store as their results (none for the blank)."""
    names = _named_cells(template)
    position = dict((mid, n) for n, mid in enumerate(config.metrics_by_id(), 1))
    metric_of = dict((m.get("functionId"), mid) for mid, m in config.metrics_by_id().items())
    fns = []
    for fid, label, cat in functions():
        n = position.get(metric_of.get(fid))
        key = f"m{n:02d}_score" if n else None
        if key in names:
            fns.append((fid, label, cat, names[key]))
    site = dict((label, (*names[entry], (stored or {}).get(names[entry][1], "")))
                for label, entry in SITE_CELLS.items())
    return SummaryCells(eci=names["eci"], sub=dict((k, names[v]) for k, v in SUB_NAMES.items()),
                        functions=fns, index_digits=INDEX_DIGITS, function_digits=FUNCTION_DIGITS,
                        site_cells=site, site_first=True)


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


def scenario_result(base: dict, report: dict, notes: dict) -> dict:
    """The result dict the calculator reads for one scenario: the screening with that scenario's
    scored report and the shared notes."""
    rows = [{**r, "userNote": (notes or {}).get(r["metricId"], "")} for r in report.get("metricRows") or []]
    return {**(base or {}), "report": {**report, "metricRows": rows}}


def build(base: dict, scenarios: list, notes: dict, *, today: _dt.date | None = None,
          template: bytes | None = None) -> bytes:
    """The workbook for ``scenarios`` = [(Scenario, scored report), ...], Existing Conditions first."""
    today = today or _dt.date.today()
    template = template if template is not None else calculator.template_bytes()
    inputs, scores, stored = [], [], None
    for s, report in scenarios:
        values, notes_ref = calculator.filled_values(scenario_result(base, report, notes), today=today)
        stored = values if stored is None else stored
        inputs.append(ScenarioInput(s.id, s.name, s.description,
                                    fill=lambda xml, v=values, r=notes_ref: calculator.fill_sheet(xml, v, r)))
        scores.append(scenario_scores(report))
    first = scenarios[0][1] if scenarios else (base or {}).get("report") or {}
    return assemble(template, SPEC, inputs, summary=summary_info({**(base or {}), "report": first}),
                    cells=summary_cells(template, stored or {}), scores=scores, generated_on=today)


def build_blank() -> bytes:
    """The blank workbook (``calculator.BLANK_PATH``): the template with one empty score sheet named
    "Scenario" and the Summary's site rows and date left for the assessor. Nothing in it depends on
    the day or a site, so ``scripts/build_blank_calculator.py`` writes it once."""
    template = calculator.template_bytes()
    return assemble(template, SPEC, [ScenarioInput("existing", calculator.SHEET_NAME)],
                    summary=SummaryInfo("EASI", blank=True), cells=summary_cells(template),
                    scores=[ScenarioScores()])
