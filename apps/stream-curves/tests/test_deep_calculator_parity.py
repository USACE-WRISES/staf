"""Same values, same scores: the generated Excel calculator against DEEP.

The workbook is a deliverable of the method, so its formulas are evaluated (with
the ``formulas`` package, no Excel needed) and compared with DEEP's own scoring
code on real published bundles: every knot of every curve, both clamps, values
between knots, sparse worksheets, and each curve set of a stratified metric.
Agreement is required to 1e-9 on the metric index, the function score, the three
outcome sub-indices and the Ecosystem Condition Index.

DEEP is the sibling app. Its ``deep`` package is imported from ``apps/deep`` by
path (its scoring modules are pure), and the test skips where it is absent.

``DEEP_EXCEL_PARITY=1`` repeats one case in the installed Excel through
``py -3.12`` and pywin32, the target runtime.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from streamcurves import deep_calculator as dc

formulas = pytest.importorskip("formulas")
openpyxl = pytest.importorskip("openpyxl")

_APPS = Path(__file__).resolve().parents[2]
_DEEP_ROOT = _APPS / "deep"
_LIBRARY = _APPS / "library" / "assessments"
pytestmark = pytest.mark.skipif(not (_DEEP_ROOT / "deep" / "curves.py").is_file(),
                                reason="the DEEP app is not in this checkout")
TOL = 1e-9


def _deep():
    if str(_DEEP_ROOT) not in sys.path:
        sys.path.insert(0, str(_DEEP_ROOT))
    from deep import curves, measure
    from deep.assessments import LoadedAssessment
    return curves, measure, LoadedAssessment


def _latest(assessment: str) -> dict:
    folder = _LIBRARY / assessment
    versions = sorted((p for p in folder.glob("v*") if p.is_dir()),
                      key=lambda p: int(p.name[1:]))
    if not versions:
        pytest.skip(f"no published version of {assessment}")
    return json.loads((versions[-1] / "assessment.deep.json").read_text(encoding="utf-8"))


def _synthetic() -> dict:
    """The methodology 0.12 shapes no published bundle carries yet: a fixed
    curve, a stratified metric that serves two functions, a vertical step."""
    asc = [{"x": 0, "y": 0}, {"x": 10, "y": 1}]
    step = [{"x": 0, "y": 1.0}, {"x": 5, "y": 0.7}, {"x": 5, "y": 0.3}, {"x": 12, "y": 0.0}]
    embed = {"metricId": "spring-phab-xembed", "metricName": "Embeddedness",
             "xLabel": "Embeddedness (%)", "curve": {"points": asc}, "activeStratum": "",
             "curveLayers": [{"stratum": "", "points": asc},
                             {"stratum": "ge_2", "points": [{"x": 0, "y": 0}, {"x": 5, "y": 1}]},
                             {"stratum": "0.5_to_2", "points": [{"x": 2, "y": 0.1},
                                                                  {"x": 8, "y": 0.9}]}],
             "stratifier": {"variable": "nhd_slope", "breaks": [0.005, 0.02], "right": False,
                            "classes": [{"key": "lt_0.5", "label": "Low gradient"},
                                        {"key": "0.5_to_2", "label": "Moderate gradient (0.5 to "
                                                                       "2 percent), \"mid\" & co"},
                                        {"key": "ge_2", "label": "Steep (2 percent and above)"}]}}
    fixed = {"metricId": "spring-pctimp2019ws", "metricName": "Impervious surface",
             "xLabel": "Impervious surface (%)", "criteriaBasis": "fixed",
             "curve": {"points": [{"x": 0, "y": 1}, {"x": 10, "y": 0.69},
                                  {"x": 25.005, "y": 0.39}, {"x": 44.5115, "y": 0}]}}
    stepped = {"metricId": "spring-step", "metricName": "Stepped", "curve": {"points": step}}
    return {"assessmentId": "parity-synthetic", "assessmentName": "Parity synthetic",
            "library": {"version": 1, "updatedAt": "2026-09-19T00:00:00Z"},
            "scoringContract": {"indirectWeight": 0.10, "functionScoreMax": 15},
            "metricsByFunction": [
                {"functionId": "catchment-hydrology", "functionName": "Catchment hydrology",
                 "discipline": "Hydrology", "metrics": [fixed]},
                {"functionId": "hyporheic-connectivity", "functionName": "Hyporheic connectivity",
                 "discipline": "Hydraulics", "metrics": [embed, stepped]},
                {"functionId": "bed-composition-bedform-dynamics",
                 "functionName": "Bed composition and bedform dynamics",
                 "discipline": "Geomorphology", "metrics": [embed]},
                {"functionId": "community-dynamics", "functionName": "Community dynamics",
                 "discipline": "Biology", "metrics": [stepped]}]}


def _synthetic_016() -> dict:
    """The methodology 0.16 shapes: a flagged transfer (REF-16), an EPA benchmark
    (REF-14) and the two last-resort curves (REF-17) exactly as the build writes
    them, the adopted EASI methods' points read from config/fixed_criteria.yaml."""
    from streamcurves import fixed_criteria as fc
    from streamcurves import published_benchmark as pb

    def pts(key):
        return [{"x": float(x), "y": float(y)} for x, y in fc.last_resort_entry(key)["points"]]
    wet = {"metricId": "spring-pctwet2019ws", "metricName": "Wetland cover of the watershed",
           "xLabel": "Wetland cover of the watershed (%)", "criteriaBasis": "fixed",
           "basis": "easi-screening-method", "curve": {"points": pts("pctwet2019ws")}}
    bhr = {"metricId": "spring-bank-height-ratio", "metricName": "Bank height ratio",
           "xLabel": "Bank height ratio (ratio)", "criteriaBasis": "fixed",
           "basis": "easi-screening-method", "curve": {"points": pts("bank_height_ratio")}}
    flagged = {"metricId": "spring-phab-xfc-nat", "metricName": "Instream fish cover",
               "transferRisk": "unvalidated", "confidenceCap": 39,
               "curve": {"points": [{"x": 0, "y": 0}, {"x": 20, "y": 0.39},
                                    {"x": 45, "y": 0.69}, {"x": 80, "y": 1}]}}
    mmi = {"metricId": "spring-bent-mmi-bent", "metricName": "Benthic MMI",
           "criteriaBasis": "fixed", "basis": "published-benchmark",
           "curve": {"points": pb.curve_points("bent_MMI_BENT", "CPL")}}
    return {"assessmentId": "parity-synthetic-016", "assessmentName": "Parity synthetic 0.16",
            "library": {"version": 1, "updatedAt": "2026-09-28T00:00:00Z"},
            "scoringContract": {"indirectWeight": 0.10, "functionScoreMax": 15},
            "metricsByFunction": [
                {"functionId": "surface-water-storage", "functionName": "Surface water storage",
                 "discipline": "Hydrology", "metrics": [wet]},
                {"functionId": "channel-floodplain-dynamics",
                 "functionName": "Channel and floodplain dynamics",
                 "discipline": "Geomorphology", "metrics": [bhr]},
                {"functionId": "habitat-provision", "functionName": "Habitat provision",
                 "discipline": "Biology", "metrics": [flagged]},
                {"functionId": "community-dynamics", "functionName": "Community dynamics",
                 "discipline": "Biology", "metrics": [mmi]}]}


BUNDLES = {
    "northeastern-highlands": lambda: _latest("northeastern-highlands"),
    "eastern-corn-belt-plains": lambda: _latest("eastern-corn-belt-plains"),
    "interior-plateau": lambda: _latest("interior-plateau"),
    "nc-sqt-adapted": lambda: _latest("nc-sqt-adapted"),
    "synthetic-0.12": _synthetic,
    "synthetic-0.16": _synthetic_016,
}


class Book:
    """One generated workbook, loaded once into a ``formulas`` model."""

    def __init__(self, bundle: dict, folder: Path):
        self.bundle = bundle
        self.path = folder / "calculator.xlsx"
        self.path.write_bytes(dc.build_calculator(bundle))
        wb = openpyxl.load_workbook(self.path)
        self.names = {k: v.attr_text for k, v in wb.defined_names.items()}
        self.entries = [n for n in self.names if n.startswith(("in_", "st_", "site_"))]
        self.model = formulas.ExcelModel().loads(str(self.path)).finish()

    def key(self, name: str) -> str:
        sheet, addr = self.names[name].split("!")
        return f"'[{self.path.name}]{sheet.strip(chr(39)).upper()}'!{addr.replace('$', '')}"

    def evaluate(self, entries: dict, outputs: list[str]) -> dict:
        inputs = {self.key(n): "" for n in self.entries}
        for n, v in entries.items():
            inputs[self.key(n)] = v
        sol = self.model.calculate(inputs=inputs, outputs=[self.key(o) for o in outputs])
        out = {}
        for o in outputs:
            v = sol[self.key(o)].value
            while hasattr(v, "__len__") and not isinstance(v, str):
                v = v[0]
            out[o] = None if (isinstance(v, str) and v == "") else float(v)
        return out


@pytest.fixture(scope="module", params=list(BUNDLES))
def book(request, tmp_path_factory) -> Book:
    return Book(BUNDLES[request.param](), tmp_path_factory.mktemp(request.param.replace(".", "_")))


def _metrics(la) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in la.all_metrics():
        out.setdefault(m["metricId"], m)
    return out


def _compare(book: Book, state: dict) -> None:
    """Score ``state`` in DEEP and in the workbook and require agreement."""
    curves, measure, LoadedAssessment = _deep()
    la = LoadedAssessment.from_dict(book.bundle)
    metrics = _metrics(la)
    entries = {}
    for mid, rc in state.items():
        if rc.get("value") is not None and not rc.get("na"):
            entries["in_" + dc.metric_key(mid)] = rc["value"]
        if rc.get("stratum") is not None and len(curves.curve_strata(metrics[mid])) > 1:
            entries["st_" + dc.metric_key(mid)] = dc.layer_label(rc["stratum"], metrics[mid])
    sc, fres = curves.score_site(la, measure.measured_from_state(state))
    outputs = (["eci"] + [f"sub_index_{o}" for o in dc.OUTCOMES]
               + ["fs_" + dc.function_key(fid) for fid in fres]
               + ["idx_" + dc.metric_key(mid) for mid in metrics])
    got = book.evaluate(entries, outputs)
    # The workbook's cells are ratios over the rows that carry a score, which is the
    # running total. DEEP publishes that as ...OverScored and withholds a claim where
    # the assessment leaves functions unassessed, so parity is asserted against the
    # arithmetic the cells implement (apps/deep/tests/test_scoring.py owns the rule).
    assert got["eci"] == pytest.approx(sc["ecosystemConditionIndexOverScoredRaw"], abs=TOL)
    for o in dc.OUTCOMES:
        assert got[f"sub_index_{o}"] == pytest.approx(sc["subIndicesOverScoredRaw"][o], abs=TOL), o
    for fid, fr in fres.items():
        name = "fs_" + dc.function_key(fid)
        if fr.score is None:
            assert got[name] is None, fid
        else:
            assert got[name] == pytest.approx(fr.score, abs=TOL), fid
    index_of = {}
    for fr in fres.values():
        index_of.update(fr.metric_indices)
    for mid in metrics:
        want = index_of.get(mid)
        have = got["idx_" + dc.metric_key(mid)]
        if want is None:
            assert have is None, mid
        else:
            assert have == pytest.approx(want, abs=TOL), mid


def test_a_blank_workbook_scores_nothing(book):
    _compare(book, {})


def test_every_knot_and_both_clamps(book):
    """Each metric in turn at every knot of its active curve, below the first
    knot, above the last, and halfway along every segment."""
    curves, _measure, LoadedAssessment = _deep()
    la = LoadedAssessment.from_dict(book.bundle)
    metrics = _metrics(la)
    probes: dict[str, list[float]] = {}
    for mid, m in metrics.items():
        xs = sorted(float(p["x"]) for p in curves.active_points(m))
        span = (xs[-1] - xs[0]) or 1.0
        mids = [(a + b) / 2.0 for a, b in zip(xs, xs[1:])]
        probes[mid] = [xs[0] - 0.25 * span] + xs + mids + [xs[-1] + 0.25 * span]
    depth = max(len(v) for v in probes.values())
    for i in range(depth):
        _compare(book, {mid: {"value": vals[i % len(vals)]} for mid, vals in probes.items()})


def test_seeded_random_worksheets_with_gaps(book):
    curves, _measure, LoadedAssessment = _deep()
    la = LoadedAssessment.from_dict(book.bundle)
    metrics = _metrics(la)
    rng = random.Random(20260919)
    for _case in range(4):
        state = {}
        for mid, m in metrics.items():
            if rng.random() < 0.35:                 # a gap: the metric drops out
                continue
            xs = sorted(float(p["x"]) for p in curves.active_points(m))
            span = (xs[-1] - xs[0]) or 1.0
            rec = {"value": rng.uniform(xs[0] - 0.1 * span, xs[-1] + 0.1 * span)}
            layers = curves.curve_strata(m)
            if len(layers) > 1 and rng.random() < 0.7:
                rec["stratum"] = rng.choice(layers)
            state[mid] = rec
        _compare(book, state)


def test_each_curve_set_selects_its_own_curve(book):
    curves, _measure, LoadedAssessment = _deep()
    la = LoadedAssessment.from_dict(book.bundle)
    stratified = {mid: m for mid, m in _metrics(la).items() if len(curves.curve_strata(m)) > 1}
    if not stratified:
        pytest.skip("this bundle has no stratified metric")
    rng = random.Random(7)
    picked = dict(list(stratified.items())[:6])
    depth = max(len(curves.curve_strata(m)) for m in picked.values())
    for i in range(min(depth, 5)):
        state = {}
        for mid, m in picked.items():
            layers = curves.curve_strata(m)
            layer = layers[i % len(layers)]
            xs = sorted(float(p["x"]) for p in curves.active_points(m, layer))
            state[mid] = {"value": rng.uniform(xs[0], xs[-1]), "stratum": layer}
        _compare(book, state)


def test_not_applicable_is_a_blank(book):
    curves, _measure, LoadedAssessment = _deep()
    la = LoadedAssessment.from_dict(book.bundle)
    state = {}
    for i, (mid, m) in enumerate(_metrics(la).items()):
        xs = sorted(float(p["x"]) for p in curves.active_points(m))
        state[mid] = {"value": xs[len(xs) // 2], "na": i % 2 == 0}
    _compare(book, state)


def test_the_last_resort_boundaries_and_a_missing_measurement(tmp_path):
    """REF-17 (owner decision D19): at EASI's own boundaries (wetland 1 and 5
    percent, bank height ratio 1.3 and 1.5) DEEP and the workbook agree and DEEP
    reads EASI's class; a site with no bank height ratio leaves channel and
    floodplain dynamics unassessed in both, while the other functions score."""
    from streamcurves import fixed_criteria as fc
    curves, measure, LoadedAssessment = _deep()
    b = Book(_synthetic_016(), tmp_path)
    classes = {"Good": "Functioning", "Fair": "Functioning-at-Risk", "Poor": "Non-Functioning"}
    for wet, bhr in ((0.99, 1.3), (1.0, 1.31), (5.0, 1.5), (5.01, 1.51), (0.0, 1.0)):
        state = {"spring-pctwet2019ws": {"value": wet}, "spring-bank-height-ratio": {"value": bhr},
                 "spring-phab-xfc-nat": {"value": 30.0}, "spring-bent-mmi-bent": {"value": 50.0}}
        _compare(b, state)
        la = LoadedAssessment.from_dict(b.bundle)
        _sc, fres = curves.score_site(la, measure.measured_from_state(state))
        for key, mid, x in (("pctwet2019ws", "spring-pctwet2019ws", wet),
                            ("bank_height_ratio", "spring-bank-height-ratio", bhr)):
            idx = [fr.metric_indices[mid] for fr in fres.values() if mid in fr.metric_indices][0]
            assert fc.deep_class(idx) == classes[fc.class_of(fc.last_resort_entry(key), x)], (key, x)
    state = {"spring-pctwet2019ws": {"value": 3.0}, "spring-phab-xfc-nat": {"value": 30.0},
             "spring-bent-mmi-bent": {"value": 50.0}}
    _compare(b, state)
    la = LoadedAssessment.from_dict(b.bundle)
    _sc, fres = curves.score_site(la, measure.measured_from_state(state))
    assert fres["channel-floodplain-dynamics"].score is None
    assert fres["surface-water-storage"].score is not None


# --------------------------------------------------------------------------- #
# the installed Excel (opt in)
# --------------------------------------------------------------------------- #
def _excel_available() -> bool:
    if os.environ.get("DEEP_EXCEL_PARITY") != "1" or sys.platform != "win32":
        return False
    if shutil.which("py") is None:
        return False
    return subprocess.run(["py", "-3.12", "-c", "import win32com.client"],
                          capture_output=True).returncode == 0


@pytest.mark.skipif(not _excel_available(), reason="set DEEP_EXCEL_PARITY=1 on a machine with Excel")
def test_the_installed_excel_agrees(tmp_path):
    curves, measure, LoadedAssessment = _deep()
    bundle = _synthetic()
    path = tmp_path / "calculator.xlsx"
    path.write_bytes(dc.build_calculator(bundle))
    la = LoadedAssessment.from_dict(bundle)
    state = {"spring-phab-xembed": {"value": 3.0, "stratum": "ge_2"},
             "spring-pctimp2019ws": {"value": 12.0}, "spring-step": {"value": 5.0}}
    sc, _ = curves.score_site(la, measure.measured_from_state(state))
    helper = tmp_path / "excel_eval.py"
    helper.write_text(
        "import json, sys\n"
        "import win32com.client as win32\n"
        "path, entries = sys.argv[1], json.loads(sys.argv[2])\n"
        "excel = win32.DispatchEx('Excel.Application'); excel.Visible = False\n"
        "excel.DisplayAlerts = False\n"
        "try:\n"
        "    wb = excel.Workbooks.Open(path, UpdateLinks=0, ReadOnly=True)\n"
        "    wb.Worksheets('DEEP Score').Unprotect('deep')\n"
        "    for name, value in entries.items():\n"
        "        wb.Names(name).RefersToRange.Value = value\n"
        "    excel.CalculateFull()\n"
        "    print(json.dumps({'eci': wb.Names('eci').RefersToRange.Value}))\n"
        "    wb.Close(False)\n"
        "finally:\n"
        "    excel.Quit()\n", encoding="utf-8")
    entries = {"in_spring_phab_xembed": 3.0,
               "st_spring_phab_xembed": dc.layer_label("ge_2", _synthetic()["metricsByFunction"][1]
                                                       ["metrics"][0]),
               "in_spring_pctimp2019ws": 12.0, "in_spring_step": 5.0}
    done = subprocess.run(["py", "-3.12", str(helper), str(path), json.dumps(entries)],
                          capture_output=True, text=True, timeout=600, check=True)
    got = json.loads(done.stdout.strip().splitlines()[-1])
    assert got["eci"] == pytest.approx(sc["ecosystemConditionIndexRaw"], abs=TOL)
