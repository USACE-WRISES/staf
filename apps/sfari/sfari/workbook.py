"""SFARI's workbook and summary: the calculator's Instructions, the Summary, then the calculator
once per scenario, each tab named after its scenario (the shared ``libs/staf_workbook``, vendored in
``_vendor``). It opens on Instructions. The layout is the owner's 2026-10-19 calculator: no
ReferenceCurves tab, the Summary's site block right under its title with Reach name and Reach length
read live from Existing Conditions (B2, G2), then a gap.

The calculator template stays the owner's issue; each scenario's tab is a copy of its score sheet with
that scenario's entries, identical apart from sheet names. The blank workbook is the same with one
empty score sheet named "Scenario", as the owner's file has it (``build_blank``).
"""
from __future__ import annotations

import datetime as _dt

from . import calculator, config, scoring
from ._vendor.staf_workbook.assemble import AssemblySpec, ScenarioInput, assemble
from ._vendor.staf_workbook.model import compare as cmp_model
from ._vendor.staf_workbook.model.compare import Measure, ScenarioScores
from ._vendor.staf_workbook.model.summary import SummaryInfo, regions_for_l3
from ._vendor.staf_workbook.sheets import SummaryCells

#: the workbook opens on the template's Instructions, ahead of Summary (owner, 2026-10-19)
SPEC = AssemblySpec("SFARI", calculator.SHEET_NAME, blocks={"Instructions": "L4:P27"}, force_recalc=True,
                    lead=("Instructions",))
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


#: the Summary's rows that read Existing Conditions live (the owner's 2026-10-19 Summary)
SITE_CELLS = {"Reach name": "B2", "Reach length": "G2"}


def summary_cells(delin: dict | None = None) -> SummaryCells:
    """Where the Summary reads the calculator; ``delin`` gives the values its live site rows store
    (what the calculator's B2 and G2 hold for this site; none for the blank)."""
    rows = dict((fid, row) for fid, row, _label in calculator.FUNCTION_ROWS)
    sheet = calculator.SHEET_NAME
    stored = calculator.input_values(delin, {}, {}) if delin is not None else {}
    return SummaryCells(eci=(sheet, "A8"), sub=dict((k, (sheet, c)) for k, c in _SUB_CELLS.items()),
                        functions=[(fid, name, cat, (sheet, f"H{rows[fid]}")) for fid, name, cat in functions()],
                        index_digits=2, function_digits=0,
                        site_cells=dict((label, (sheet, cell, stored.get(cell, ""))) for label, cell in SITE_CELLS.items()),
                        site_first=True)


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
    return assemble(calculator.template_bytes(), SPEC, inputs, summary=summary_info(delin), cells=summary_cells(delin),
                    scores=scores, generated_on=today)


def build_blank() -> bytes:
    """The blank workbook (``calculator.BLANK_PATH``): the owner's 2026-10-19 calculator, with one empty
    score sheet named "Scenario" and the Summary's site rows and date left for the assessor. Nothing in
    it depends on the day or a site, so ``scripts/build_blank_calculator.py`` writes it once."""
    return assemble(calculator.template_bytes(), SPEC, [ScenarioInput("existing", calculator.SHEET_NAME)],
                    summary=SummaryInfo("SFARI", blank=True), cells=summary_cells(), scores=[ScenarioScores()])
