"""DEEP's workbook and summary: the loaded version's calculator once per scenario, then the
Summary and ReferenceCurves tabs (the shared ``libs/staf_workbook``, vendored in ``_vendor``).

The calculator stays the one published for this version (``calculator.template_for`` hands it out
only when its digest matches the loaded bundle). Each scenario's tab is a copy of its DEEP Score
sheet with that scenario's measured values; the sheets that compute from it (Metrics, Results,
ChartData) are copied too, hidden for the alternatives.

The calculator's index is the mean of the three outcome ratios over the functions scored, which is
a running total and not always a condition claim: a function the assessment cannot score leaves the
index an interval. So the Summary shows the calculator's own numbers, which match its tabs, with the
application's condition claim beside them, and the page compares the claims.
"""
from __future__ import annotations

import datetime as _dt
import io
import re
import zipfile

from . import calculator, config, curves, measure, scoring
from ._vendor.staf_workbook.assemble import AssemblySpec, ScenarioInput, assemble
from ._vendor.staf_workbook.model import compare as cmp_model
from ._vendor.staf_workbook.model.compare import Measure, ScenarioScores
from ._vendor.staf_workbook.model.refcurves import CurveBlock, RefCurves
from ._vendor.staf_workbook.model.summary import SummaryInfo, regions_for_l3
from ._vendor.staf_workbook.sheets import SummaryCells

SPEC = AssemblySpec("DEEP", calculator.SHEET_NAME, force_recalc=True)
SUB_NAMES = {"physical": "sub_index_physical", "chemical": "sub_index_chemical",
             "biological": "sub_index_biological"}
INDEX_DIGITS, FUNCTION_DIGITS = 2, 1
CLAIM_HEADER = "Condition claim"
SUMMARY_NOTE = ("Index values are the calculator's arithmetic over the functions scored. The condition "
                "claim states what the assessment supports.")


def functions(assessment) -> list:
    """``[(id, name, category), ...]`` the assessment scores, in framework order; a function the
    framework does not know keeps its bundle place at the end."""
    blocks = dict((fn.get("functionId"), fn) for fn in (getattr(assessment, "metrics_by_function", None) or []))
    out = []
    for f in sorted(config.functions(), key=lambda f: f.get("order", 0)):
        fn = blocks.pop(f["id"], None)
        if fn is not None:
            out.append((f["id"], fn.get("functionName") or f.get("name") or f["id"], f.get("category", "")))
    out.extend((fid, fn.get("functionName") or fid, fn.get("discipline", "")) for fid, fn in blocks.items())
    return out


def score(assessment, measured: dict) -> dict:
    """The application's score of one scenario's measured values."""
    if assessment is None:
        return scoring.score_assessment({})
    return curves.score_site(assessment, measure.measured_from_state(measured or {}))[0]


def _claim(bounds, value) -> Measure:
    if value is not None:
        return Measure(float(value))
    if not bounds:
        return Measure()
    low, high = float(bounds[0]), float(bounds[1])
    return Measure(low) if abs(high - low) < 1e-12 else Measure(low=low, high=high)


def claim_scores(sc: dict) -> ScenarioScores:
    """A scenario as the page compares it: the index the assessment supports (a value, or the
    interval an unassessed function leaves open), the measured outcome sub-indices as the rail
    shows them, and the function scores."""
    fs = dict((fid, s) for fid, s in (sc.get("functionScores") or {}).items() if s is not None)
    if not fs:
        return ScenarioScores()
    subs = dict((k, Measure() if v is None else Measure(float(v)))
                for k, v in (sc.get("subIndicesRaw") or {}).items())
    return ScenarioScores(_claim(sc.get("ecosystemConditionIndexBounds"), sc.get("ecosystemConditionIndexRaw")),
                          subs, dict((fid, Measure(float(s))) for fid, s in fs.items()))


def workbook_scores(sc: dict) -> ScenarioScores:
    """A scenario as its calculator computes it (the Summary's stored results)."""
    fs = dict((fid, s) for fid, s in (sc.get("functionScores") or {}).items() if s is not None)
    if not fs:
        return ScenarioScores()
    return ScenarioScores(Measure(float(sc.get("ecosystemConditionIndexOverScoredRaw") or 0.0)),
                          dict((k, Measure(float(v))) for k, v in (sc.get("subIndicesOverScoredRaw") or {}).items()),
                          dict((fid, Measure(float(s))) for fid, s in fs.items()))


def comparison(assessment, names: list, results: list):
    """The scenarios side by side, from each one's :func:`score` result."""
    return cmp_model.build(names, [claim_scores(sc) for sc in results], functions(assessment),
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


def summary_cells(template: bytes, assessment, claims: list) -> SummaryCells:
    """Where each scenario's results sit (the calculator's own named cells) and the claim column."""
    names = _named_cells(template)
    fns = [(fid, label, cat, names[f"fs_{calculator.metric_key(fid)}"]) for fid, label, cat in functions(assessment)
           if f"fs_{calculator.metric_key(fid)}" in names]
    return SummaryCells(eci=names["eci"], sub=dict((k, names[v]) for k, v in SUB_NAMES.items()),
                        functions=fns, index_digits=INDEX_DIGITS, function_digits=FUNCTION_DIGITS,
                        extra_columns=[(CLAIM_HEADER, list(claims))], note=SUMMARY_NOTE)


def summary_info(delin: dict) -> SummaryInfo:
    d = delin or {}
    dl = d.get("delineation") or {}
    l3 = None
    if dl.get("snapped_lat") is not None and dl.get("snapped_lon") is not None:
        from . import geo
        try:
            l3 = (geo.level3_at(dl["snapped_lat"], dl["snapped_lon"]) or {}).get("code")
        except Exception:  # noqa: BLE001 - the summary reads "Not available" instead
            l3 = None
    return SummaryInfo("DEEP", dl.get("gnis_name"), dl.get("reach_length_ft"), dl.get("drainage_area_sqkm"),
                       dl.get("stream_order"), regions_for_l3(l3))


def _layers(m: dict) -> list:
    """``[(label, stratum, points), ...]``: each curve layer, or the single curve."""
    layers = m.get("curveLayers") or []
    if layers:
        return [(calculator.layer_label(layer.get("stratum"), m), str(layer.get("stratum", "")),
                 layer.get("points") or []) for layer in layers]
    curve = m.get("curve") or {}
    return [(curve.get("layerName") or "Reference curve", None, curve.get("points") or [])]


def _used_layer(m: dict, state: dict):
    layers = m.get("curveLayers") or []
    if not layers:
        return None
    chosen = ((state or {}).get(m.get("metricId")) or {}).get("stratum")
    for layer in layers:
        if chosen is not None and str(layer.get("stratum", "")) == str(chosen):
            return calculator.layer_label(layer.get("stratum"), m)
    active = m.get("activeStratum")
    for layer in layers:
        if active and str(layer.get("stratum", "")) == str(active):
            return calculator.layer_label(layer.get("stratum"), m)
    return calculator.layer_label(layers[0].get("stratum"), m)


def reference_curves(assessment, scenarios: list) -> RefCurves:
    """Every scored metric's curve (each layer of a stratified one as its own series), with the
    curve set each scenario used. ``scenarios`` = [(name, measured values), ...]."""
    blocks, seen = [], set()
    by_fn = dict((fn.get("functionId"), fn) for fn in (getattr(assessment, "metrics_by_function", None) or []))
    for fid, fname, _cat in functions(assessment):
        for m in (by_fn.get(fid) or {}).get("metrics", []):
            mid = m.get("metricId")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            series = [(label, [(float(p["x"]), float(p["y"])) for p in pts
                               if p.get("x") is not None and p.get("y") is not None])
                      for label, _stratum, pts in _layers(m)]
            series = [(label, pts) for label, pts in series if pts]
            if not series:
                continue
            used = {}
            for name, state in scenarios:
                layer = _used_layer(m, state)
                if layer is not None:
                    used.setdefault(layer, []).append(name)
            if used and len(series) > 1:
                parts = [f"{layer} ({', '.join(names)})" for layer, names in used.items()]
                subtitle = f"{fname}. Curve set used: " + "; ".join(parts)
            else:
                subtitle = fname
            blocks.append(CurveBlock(m.get("metricName") or mid, subtitle, m.get("xLabel") or "", "Index", series))
    intro = (f"Reference curves of {assessment.assessment_name}." if getattr(assessment, "assessment_name", None)
             else "Reference curves of this assessment.")
    return RefCurves(intro, blocks)


def build(template: bytes, assessment, delin: dict, scenarios: list, *, today: _dt.date | None = None) -> bytes:
    """The workbook for ``scenarios`` = [(Scenario, measured values), ...], Existing Conditions
    first, from the loaded version's calculator."""
    today = today or _dt.date.today()
    inputs, book_scores, claims, used = [], [], [], []
    for s, measured in scenarios:
        values, replaceable = calculator.filled_values(template, assessment, measured, delin, today=today)
        inputs.append(ScenarioInput(s.id, s.name, s.description,
                                    fill=lambda xml, v=values, r=replaceable: calculator.fill_sheet(xml, v, r)))
        sc = score(assessment, measured)
        book_scores.append(workbook_scores(sc))
        claims.append(scoring.index_claim(sc) if sc.get("functionScores") else "")
        used.append((s.name, measured))
    return assemble(template, SPEC, inputs, summary=summary_info(delin),
                    cells=summary_cells(template, assessment, claims), scores=book_scores,
                    curves=reference_curves(assessment, used), generated_on=today)
