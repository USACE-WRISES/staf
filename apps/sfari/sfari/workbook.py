"""SFARI's workbook and summary: the calculator once per scenario, then the Summary and
ReferenceCurves tabs (the shared ``libs/staf_workbook``, vendored in ``_vendor``).

The calculator template stays the certified draft; each scenario's tab is a copy of its score
sheet with that scenario's entries, identical apart from sheet names. SFARI rates metrics on a
Likert scale and has no numeric curves, so its ReferenceCurves tab holds the Likert criteria, the
suggestion breakpoints and the Likert scores.
"""
from __future__ import annotations

import datetime as _dt

from . import calculator, config, likert, scoring
from ._vendor.staf_workbook.assemble import AssemblySpec, ScenarioInput, assemble
from ._vendor.staf_workbook.model import compare as cmp_model
from ._vendor.staf_workbook.model.compare import Measure, ScenarioScores
from ._vendor.staf_workbook.model.refcurves import RefCurves, TableBlock
from ._vendor.staf_workbook.model.summary import SummaryInfo, regions_for_l3
from ._vendor.staf_workbook.sheets import SummaryCells

SPEC = AssemblySpec("SFARI", calculator.SHEET_NAME, blocks={"Instructions": "L4:P27"}, force_recalc=True)
_SUB_CELLS = {"physical": "C8", "chemical": "C56", "biological": "C72"}


def functions() -> list:
    """``[(id, name, category), ...]`` in the worksheet's order."""
    return [(f["id"], f["name"], f["category"]) for f in sorted(config.functions(), key=lambda f: f["order"])]


def scenario_scores(function_scores: dict) -> ScenarioScores:
    """A scenario's scores as the comparison and the Summary tab read them (empty before any
    function is scored)."""
    raw = dict((fid, (v or {}).get("score")) for fid, v in (function_scores or {}).items())
    scored = dict((fid, s) for fid, s in raw.items() if s is not None)
    if not scored:
        return ScenarioScores()
    sc = scoring.score_assessment(raw)
    return ScenarioScores(Measure(sc["ecosystemConditionIndexRaw"]),
                          dict((k, Measure(v)) for k, v in sc["subIndicesRaw"].items()),
                          dict((fid, Measure(float(s))) for fid, s in scored.items()))


def comparison(names: list, function_score_sets: list):
    return cmp_model.build(names, [scenario_scores(fs) for fs in function_score_sets], functions(),
                           index_digits=2, function_digits=0)


def summary_cells() -> SummaryCells:
    rows = dict((fid, row) for fid, row, _label in calculator.FUNCTION_ROWS)
    sheet = calculator.SHEET_NAME
    return SummaryCells(eci=(sheet, "A8"), sub=dict((k, (sheet, c)) for k, c in _SUB_CELLS.items()),
                        functions=[(fid, name, cat, (sheet, f"H{rows[fid]}")) for fid, name, cat in functions()],
                        index_digits=2, function_digits=0)


def summary_info(delin: dict) -> SummaryInfo:
    d = delin or {}
    dl = d.get("delineation") or {}
    site = ((d.get("siteEngine") or {}).get("site") or {})
    l3 = (site.get("ecoregionL3") or {}).get("code")
    if l3 is None and dl.get("snapped_lat") is not None:
        from ._vendor.site_engine import context
        l3 = (context.ecoregion_l3_at(dl.get("snapped_lat"), dl.get("snapped_lon")) or {}).get("code")
    return SummaryInfo("SFARI", dl.get("gnis_name"), dl.get("reach_length_ft"), dl.get("drainage_area_sqkm"),
                       dl.get("stream_order"), regions_for_l3(l3))


def reference_curves() -> RefCurves:
    order = list(config.LIKERT_ORDER)
    scores = TableBlock("Likert scores", ["Likert", "Score"],
                        [[name, config.LIKERT_NUMERIC[name]] for name in order] + [[config.LIKERT_NA, "Not counted"]],
                        widths=[22, 12])
    rows = []
    fn_names = dict((fid, name) for fid, name, _cat in functions())
    for fid, _name, _cat in functions():
        for m in config.metrics_by_function().get(fid, []):
            crit = dict((c.get("likert"), c.get("criteria", "")) for c in (m.get("likertCriteria") or [])
                        if isinstance(c, dict))
            rows.append([fn_names[fid], m["name"]] + [crit.get(name, "") for name in order])
    criteria = TableBlock("Scoring criteria", ["Function", "Metric"] + order, rows,
                          widths=[22, 26, 24, 24, 24, 24, 24])
    by_id = config.metrics_by_id()
    brk = []
    for mid, spec in likert.BREAKS.items():
        cells = []
        for bound, label in spec["breaks"]:
            if bound is None:
                cells.append("otherwise")
            else:
                cells.append(("below " if spec["dir"] == likert.LOW else "at least ") + f"{bound:g}")
        brk.append([(by_id.get(mid) or {}).get("name", mid), "Lower is better" if spec["dir"] == likert.LOW
                    else "Higher is better"] + cells)
    breaks = TableBlock("Suggested ratings from desktop values", ["Metric", "Direction", "Strongly Agree", "Agree",
                                                                   "Disagree", "Strongly Disagree"], brk,
                        note="Suggestions only: the assessor's Likert rating is what scores.")
    return RefCurves("SFARI rates metrics on a Likert scale; these are its criteria.", [scores, criteria, breaks])


def build(delin: dict, scenarios: list, *, today: _dt.date | None = None) -> bytes:
    """The workbook for ``scenarios`` = [(Scenario, {"metric_scores", "function_scores"}), ...],
    Existing Conditions first."""
    inputs, scores = [], []
    for s, st in scenarios:
        values = calculator.input_values(delin, st.get("metric_scores") or {}, st.get("function_scores") or {},
                                         today=today)
        inputs.append(ScenarioInput(s.id, s.name, s.description,
                                    fill=lambda xml, v=values: calculator.fill_sheet(xml, v)))
        scores.append(scenario_scores(st.get("function_scores") or {}))
    return assemble(calculator.blank_bytes(), SPEC, inputs, summary=summary_info(delin), cells=summary_cells(),
                    scores=scores, curves=reference_curves(), generated_on=today)
