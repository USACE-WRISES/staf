"""The line under each metric's name (owner, 2026-10-04: every metric shows a description in EASI,
SFARI and DEEP). DEEP's says what the value is and which way is better; the way comes from the
scoring curve, so it can never contradict the score."""
from __future__ import annotations

import re

import pytest

import app
from deep import config, curves, describe
from deep.assessments import LoadedAssessment

_ENGINE_WORDS = ("STAF site engine", "StreamCat lookup engine", "HR reach watershed", "engine")


def _baked_metrics():
    out = []
    for rec in config._registry_records():
        la = LoadedAssessment.from_dict(config.load_ref(rec["assessmentRef"]))
        out.extend((rec["assessmentRef"], m) for m in la.all_metrics())
    return out


BAKED = _baked_metrics()


def test_every_baked_metric_gets_a_short_plain_line():
    missing, bad = set(), []
    for ref, m in BAKED:
        line = describe.describe(m)
        if not line:
            missing.add(m.get("metricName"))
            continue
        if (len(line) > 110 or "—" in line or "–" in line or not line.endswith(".")
                or any(w in line for w in _ENGINE_WORDS)):
            bad.append((ref, line))
    assert not missing, sorted(missing)
    assert not bad, bad[:5]


def test_the_direction_always_agrees_with_the_curve():
    for ref, m in BAKED:
        pts = sorted(curves.active_points(m, None) or [], key=lambda p: p["x"])
        if len(pts) < 2:
            continue
        line = describe.describe(m)
        ys = [p["y"] for p in pts]
        if "higher is better" in line:
            assert ys[-1] > ys[0], (ref, m.get("metricName"))
        elif "higher is worse" in line:
            assert ys[-1] < ys[0], (ref, m.get("metricName"))
        elif "both high and low" in line:
            assert max(ys) > ys[0] and max(ys) > ys[-1], (ref, m.get("metricName"))


def test_the_direction_reads_the_curve_shape():
    up = [{"x": 0, "y": 0}, {"x": 5, "y": 0.7}, {"x": 10, "y": 1}]
    down = [{"x": 0, "y": 1}, {"x": 10, "y": 0.69}, {"x": 25, "y": 0.39}, {"x": 44.5, "y": 0}]
    peak = [{"x": 0, "y": 0}, {"x": 5, "y": 1}, {"x": 9, "y": 1}, {"x": 14, "y": 0.3}]
    shoulder = [{"x": 0, "y": 0.98}, {"x": 5, "y": 1}, {"x": 10, "y": 1}]
    assert describe.direction(up) == "higher is better"
    assert describe.direction(down) == "higher is worse"
    assert describe.direction(peak) == "both high and low values score lower"
    assert describe.direction(shoulder) == ""                # a flat shoulder is not a direction
    assert describe.direction([]) == "" and describe.direction(up[:1]) == ""


def test_mnemonic_names_read_like_their_full_names():
    def line(name):
        return describe.what_of({"metricName": name})
    assert line("XEMBED") == line("Embeddedness") != ""
    assert line("RP100_cm") == line("Residual pool depth") != ""
    assert line("Tolerant individuals (%) ~ HBI") == line("TOLRPIND") != ""
    assert line("  Sand   and fines ") == line("Sand + fines")


def test_an_unknown_metric_falls_back_safely():
    pts = [{"x": 0, "y": 1}, {"x": 10, "y": 0}]
    plain = {"metricName": "Owner metric", "howToMeasure": "Percent bare ground; higher is worse.",
             "curve": {"points": pts}}
    assert describe.describe(plain) == "Percent bare ground; higher is worse."
    fixed = {"metricName": "Owner metric", "curve": {"points": pts},
             "howToMeasure": "Owner metric (%) is scored on the same fixed criteria EASI uses."}
    assert describe.describe(fixed) == ""                    # never a criteria sentence
    long = {"metricName": "Owner metric", "curve": {"points": pts}, "howToMeasure": "x" * 120}
    assert describe.describe(long) == ""
    assert describe.describe({"metricName": "Owner metric"}) == ""


def test_the_row_carries_the_line_and_the_score_card_the_function_statement():
    ref, m = next((r, mm) for r, mm in BAKED if mm.get("metricName") == "Impervious surface")
    row = str(app._metric_row(m, {}, midx=None))
    assert re.search(r'class="staf-metric-desc">Percent of the watershed in impervious surface; '
                     r"higher is worse\.</div>", row)
    card = str(app._scorecard("catchment-hydrology", 12.0))
    statement = config.functions_by_id()["catchment-hydrology"]["function_statement"]
    assert 'class="sfari-fn-statement"' in card and statement[:40] in card
    assert card.index("deep-fscore-head") < card.index("sfari-fn-statement") < card.index("deep-fscore-row")


@pytest.mark.parametrize("name", sorted(describe.ALIASES))
def test_every_alias_points_at_a_phrase(name):
    assert describe.ALIASES[name] in describe.WHAT
