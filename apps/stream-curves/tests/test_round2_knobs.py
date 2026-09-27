"""Campaign Round 2, work package R2a: the candidate families as engine knobs.

For every knob: the default is today's behavior (a fixture result is identical
to the pinned one), the alternative changes the decision on a small synthetic
pool, and the knob joins the inputs digest payload only when it is set away
from its default (``inputs.reference.knobs``, absence semantics). The published
versions' replay is proven separately by ``tests/test_replay_digests.py``.

Knobs and their config keys:

  B1  reference_pool.ladder_rule                       first_pass | narrowest_adequate
  B2  reference_hierarchy.regional_screen.enabled      true | false
  B3  reference_transfer.yaml search_order             a global list (the shipped
      configuration since methodology 0.15) | absent (a family's own order)
  B4  data_rules.min_n_unstratified, exploratory_n_unstratified, min_n_stratum,
      very_small_stratum_n                             (already keys; config sha in the digest)
  C1  metric_portfolio.fill_to, second_metric_rule, second_metric_max_abs_spearman,
      second_metric_min_auc                            rank | independent_and_discriminating
  C2  curve12.gate, curve12.min_auc                    false | true
  C3a curve10.zero_inflated_share, zero_inflated_handling   null | two_part or withhold
  C3b curve10.tail_offsets_iqr                         [0.5, 1.5, 2.5] (iqr-seed-3, the
      default since methodology 0.15) | another triple, the iqr-seed-2 [0.3, 4/3, 7/3] included
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from streamcurves import acceptance, curves, methodology
from streamcurves import pressure_evidence as pe
from streamcurves import provenance as pv
from streamcurves import reference_pool as rp
from tests.test_reference_pool import CFG, SETTINGS, _frame, _stations, _values


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _config_with(monkeypatch, **overrides):
    """The shipped config with dotted-path overrides, installed as the loaded one."""
    cfg = copy.deepcopy(methodology.load_config())
    for path, value in overrides.items():
        node = cfg
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    monkeypatch.setattr(methodology, "load_config", lambda: cfg)
    return cfg


def _choose(metric, frame, target, **kw):
    values = kw.pop("values", None)
    values = _values(frame) if values is None else values
    return rp.choose_pool(metric, values, frame, target,
                          profile=rp.family_profile(metric, CFG), cfg=CFG,
                          settings=SETTINGS, **kw)


def _screened(prefix, n, *, l3, l2="8.3", l1="8", strict=True, ag=5.0, start=0, flat=False):
    """Stations carrying every variable of the pressure screen, so the regional
    screen (the relaxed tier with the agriculture limit) can admit them; a
    station with ``strict=False`` fails the strict screen on agriculture only."""
    s = _stations(prefix, n, l3=l3, l2=l2, l1=l1, strict=strict, relaxed=True, ag=ag,
                  start=start, flat=flat)
    return s.assign(rddensws=1.0, dor=0.0, npdesdensws=0.0, mines_ws=0.0)


# --------------------------------------------------------------------------- #
# the knob registry: every default is today's value, and the shipped config
# sets no knob away from it
# --------------------------------------------------------------------------- #
def test_the_shipped_config_sets_no_knob_away_from_its_default():
    assert methodology.round2_knobs() == {}
    for path, default in methodology.KNOB_DEFAULTS.items():
        raw = methodology.threshold(path, "__missing__")
        assert raw != "__missing__", f"{path} is not in the shipped config"
    assert methodology.ladder_rule() == "first_pass"
    assert methodology.regional_screen_enabled() is True
    assert methodology.portfolio_settings() == {"fill_to": 2, "second_metric_rule": "rank",
                                                "max_abs_spearman": 0.65, "min_auc": 0.55}
    assert methodology.curve12_gate() == {"gate": False, "min_auc": 0.55}
    geo = methodology.seed_geometry()
    assert geo["tail_offsets_iqr"] == curves.MONOTONE_TAIL_OFFSETS_IQR
    assert geo["custom_tail_offsets"] is False
    assert geo["zero_inflated_share"] is None and geo["zero_inflated_handling"] is None
    assert methodology.MONOTONE_TAIL_OFFSETS_IQR == curves.MONOTONE_TAIL_OFFSETS_IQR


def test_an_absent_key_reads_as_the_default(monkeypatch):
    """A campaign config root copied from an older config still runs as today."""
    cfg = copy.deepcopy(methodology.load_config())
    del cfg["reference_pool"]["ladder_rule"]
    del cfg["curve12"]
    del cfg["curve10"]
    del cfg["reference_hierarchy"]["regional_screen"]["enabled"]
    monkeypatch.setattr(methodology, "load_config", lambda: cfg)
    assert methodology.round2_knobs() == {}
    assert methodology.ladder_rule() == "first_pass"
    assert methodology.curve12_gate()["gate"] is False


@pytest.mark.parametrize("path,value,match", [
    ("reference_pool.ladder_rule", "widest", "ladder_rule"),
    ("metric_portfolio.second_metric_rule", "best", "second_metric_rule"),
    ("curve10.zero_inflated_handling", "drop", "zero_inflated_handling"),
    ("curve10.tail_offsets_iqr", [0.3, 0.2, 0.1], "tail_offsets_iqr"),
])
def test_a_misconfigured_knob_fails_loudly(monkeypatch, path, value, match):
    _config_with(monkeypatch, **{path: value})
    with pytest.raises(ValueError, match=match):
        methodology.round2_knobs()


def test_a_share_without_a_handling_is_refused(monkeypatch):
    _config_with(monkeypatch, **{"curve10.zero_inflated_share": 0.25})
    with pytest.raises(ValueError, match="zero_inflated_handling"):
        methodology.seed_geometry()


# --------------------------------------------------------------------------- #
# B1: the ladder rule
# --------------------------------------------------------------------------- #
def _b1_frame():
    # the pinned fixture of tests/test_reference_pool.py: an exploratory local
    # pool of 12 and an adequate Level II pool of 35
    return _frame(_stations("T", 12, l3="55", l2="8.2"),
                  _stations("M", 3, l3="56", l2="8.2", flat=True),
                  _stations("N", 20, l3="57", l2="8.2", flat=True))


def test_b1_first_pass_is_the_default_and_is_today():
    d, _ = _choose("m_form", _b1_frame(), "55")
    d_explicit, _ = _choose("m_form", _b1_frame(), "55", ladder_rule="first_pass")
    assert d.status == rp.STATUS_LOCAL and d.level == "l3" and d.n_usable == 12
    assert d.disposition == "exploratory"
    assert d.to_dict() == d_explicit.to_dict()
    assert [t["option"] for t in d.options_tried] == ["local"]


def test_b1_narrowest_adequate_prefers_the_adequate_wider_pool():
    d, ledger = _choose("m_form", _b1_frame(), "55", ladder_rule="narrowest_adequate")
    assert d.status == "borrowed_l2" and d.level == "l2" and d.n_usable == 35
    assert d.disposition == "adequate"
    tried = {t["option"]: t["why"] for t in d.options_tried}
    assert tried["local"].startswith("passes acceptance with 12 usable stations, exploratory")
    assert tried["regional_l2"] == "accepted"
    assert set(ledger[ledger["in_pool"]]["level"]) == {"l2"}


def test_b1_narrowest_adequate_falls_back_to_the_narrowest_exploratory_pool():
    frame = _frame(_stations("T", 12, l3="55", l2="8.2"),
                   _stations("M", 3, l3="56", l2="8.2", flat=True))
    d, _ = _choose("m_form", frame, "55", ladder_rule="narrowest_adequate")
    assert d.status == rp.STATUS_LOCAL and d.n_usable == 12 and d.disposition == "exploratory"
    tried = {t["option"]: t["why"] for t in d.options_tried}
    assert tried["local"].startswith("accepted: the narrowest exploratory pool that passes")
    # every wider option was walked; the Level II pool (15) is exploratory too, so
    # the narrowest of the exploratory pools that pass wins
    assert tried["regional_l2"].startswith("passes acceptance with 15 usable stations")
    assert "regional_l1" in tried


def test_b1_the_config_drives_choose_pool_and_the_digest(monkeypatch):
    _config_with(monkeypatch, **{"reference_pool.ladder_rule": "narrowest_adequate"})
    d, _ = _choose("m_form", _b1_frame(), "55")
    assert d.level == "l2"
    assert methodology.round2_knobs() == {"reference_pool.ladder_rule": "narrowest_adequate"}


# --------------------------------------------------------------------------- #
# B2: the regional screen switch
# --------------------------------------------------------------------------- #
def _b2_frame():
    # three strict stations of the target, fifteen that only the regional
    # screen admits (agriculture 20, above strict's 10 and under the floor of
    # 25), and twelve strict donors in the Level II parent
    return _frame(_screened("T", 3, l3="71"),
                  _screened("R", 15, l3="71", strict=False, ag=20.0, start=3),
                  _screened("D", 12, l3="65", flat=True))


def test_b2_the_regional_screen_admits_relaxed_stations_today():
    d, ledger = _choose("m_form", _b2_frame(), "71")
    assert d.status == rp.STATUS_LOCAL_RELAXED and d.screen == rp.SCREEN_REGIONAL
    assert d.n_usable == 18 and d.disposition == "exploratory"
    assert d.screen_detail["agriculture_limit"] == 25.0
    rows = ledger[(ledger["option"] == "regional_l3") & ledger["in_pool"]]
    assert sorted(set(rows["admitted_by"])) == ["regional", "strict"]
    assert (rows["admitted_by"] == "regional").sum() == 15


def test_b2_off_keeps_every_wider_pool_to_the_strict_screen(monkeypatch):
    _config_with(monkeypatch, **{"reference_hierarchy.regional_screen.enabled": False})
    d, ledger = _choose("m_form", _b2_frame(), "71")
    assert d.status == "borrowed_l2" and d.screen == rp.SCREEN_STRICT and d.n_usable == 15
    assert d.n_local == 3
    assert set(ledger["screen"]) == {"strict"}
    assert set(ledger.loc[ledger["in_pool"], "admitted_by"]) == {"strict"}
    # the relaxed-only stations failed the strict screen in every option tried
    relaxed = ledger[ledger["station_key"].str.startswith("R")]
    assert not relaxed["in_pool"].any() and (relaxed["admitted_by"] == "").all()
    tried = {t["option"]: t["why"] for t in d.options_tried}
    assert "the regional screen is off" in tried["regional_l3"]
    assert "least-disturbed stations from Level II" in d.transfer_note
    assert "regional least-disturbed screen" not in d.transfer_note
    assert methodology.round2_knobs() == {"reference_hierarchy.regional_screen.enabled": False}


# --------------------------------------------------------------------------- #
# B3: one search order for every family (adopted at methodology 0.15)
# --------------------------------------------------------------------------- #
def test_b3_a_global_search_order_replaces_every_family_order():
    per_family = rp.family_profile("m_form", CFG)
    assert per_family["search_order_source"] == "family"
    assert rp.search_order(per_family) == ["l3", "l2", "l1"]
    cfg = {**CFG, "search_order": ["l3", "nars9", "l2", "l1"]}
    prof = rp.family_profile("m_form", cfg)
    assert prof["search_order"] == ["l3", "nars9", "l2", "l1"]
    assert prof["search_order_source"] == "global"
    frame = _frame(_stations("T", 40, l3="55", l2="8.2", strict=False),
                   _stations("M", 4, l3="56", l2="8.2", flat=True))
    d, _ = rp.choose_pool("m_form", _values(frame), frame, "55", profile=prof, cfg=cfg,
                          settings=SETTINGS)
    assert [t["option"] for t in d.options_tried] == [
        "local", "regional_l3", "regional_nars9", "regional_l2", "regional_l1"]
    # the shipped transfer config sets the one order of methodology 0.15 (B3
    # adopted): every family reads it, and no family carries an order of its own
    shipped = rp.load_transfer_config()
    assert shipped["search_order"] == ["l3", "l2", "l1", "nars9"]
    assert all("search_order" not in p for p in shipped["families"].values())
    assert rp.family_profile("chem_PTL")["search_order_source"] == "global"
    assert rp.search_order(rp.family_profile("chem_PTL")) == ["l3", "l2", "l1", "nars9"]


# --------------------------------------------------------------------------- #
# B4: the DATA floors are config keys the engine reads
# --------------------------------------------------------------------------- #
def test_b4_the_floors_are_read_from_the_config(monkeypatch):
    _config_with(monkeypatch, **{"data_rules.min_n_unstratified": 25,
                                 "data_rules.exploratory_n_unstratified": 15,
                                 "data_rules.min_n_stratum": 20,
                                 "data_rules.very_small_stratum_n": 8})
    got = rp.floors()
    assert (got["adequate"], got["exploratory"], got["stratum_min_n"]) == (25, 15, 20)
    # choose_pool reads them (settings=None): 12 stations are now below the floor
    frame = _frame(_stations("T", 12, l3="55", l2="8.2"))
    d, _ = rp.choose_pool("m_form", _values(frame), frame, "55",
                          profile=rp.family_profile("m_form", CFG), cfg=CFG)
    assert d.status == rp.STATUS_INSUFFICIENT
    assert "below the floor of 15" in d.options_tried[0]["why"]


def test_b4_the_two_copies_of_the_floors_agree_in_the_shipped_config():
    """The station pools read data_rules; the rungs after them read
    acceptance.sample_adequacy (basis_ladder, acceptance.sample_ok). A B4 arm
    must move both, so the shipped copies are pinned equal here."""
    data = rp.floors()
    acc = acceptance.settings()
    assert (data["exploratory"], data["adequate"]) == (acc["exploratory"], acc["adequate"])


def test_b4_changing_a_floor_changes_the_digest_through_the_config_sha():
    """The digest payload carries the methodology block, which carries the
    config file's sha256: any edit to a floor moves every new digest."""
    fp = methodology.config_fingerprints()
    manifest = {"region": {"code": "58"}, "methodology": fp, "configs": [],
                "inputs": {"nrsa_values": None, "nrsa_sites": None}}
    before = methodology.inputs_digest(pv.digest_payload_from_manifest(manifest))
    other = {**manifest, "methodology": {**fp, "config_sha256": "sha256:" + "0" * 64}}
    after = methodology.inputs_digest(pv.digest_payload_from_manifest(other))
    assert pv.digest_payload_from_manifest(manifest)["methodology"]["config_sha256"] \
        == fp["config_sha256"]
    assert before != after


# --------------------------------------------------------------------------- #
# C1: the second-metric rule
# --------------------------------------------------------------------------- #
def _mapping(pairs):
    return pd.DataFrame([{"metric_key": m, "discipline": "Biology", "function_label": f,
                          "sort_order": i} for i, (m, f) in enumerate(pairs)])


def _c1_evidence():
    rng = np.random.default_rng(5)
    a = rng.normal(0, 1, 40)
    data = pd.DataFrame({"site_id": [f"s{i}" for i in range(40)],
                         "a": a, "b": a + rng.normal(0, 0.05, 40), "c": rng.normal(0, 1, 40)})
    return {"reference_support": {m: {"status": "local"} for m in "abc"},
            "carried": {}, "carry_rebuilt": {}, "fixed_metrics": {}, "data": data,
            "discrimination": {"a": {"aucRefVsPressure": 0.8},
                               "b": {"aucRefVsPressure": 0.8},
                               "c": {"aucRefVsPressure": 0.7}}}


def _c1_select(ev, scores=None):
    rows = {m: {} for m in "abc"}
    mapping = _mapping([("a", "Population support"), ("b", "Population support"),
                        ("c", "Population support")])
    scores = scores or {"a": {"total": 90}, "b": {"total": 80}, "c": {"total": 70}}
    meta: dict = {}
    got_rows, got_map = pe.select_portfolio(ev, rows, mapping, {}, scores, meta)
    return meta["portfolioSelection"]["population-support"], got_rows


def test_c1_rank_is_the_default_and_is_today():
    sel, rows = _c1_select(_c1_evidence())
    assert sel["selected"] == ["a", "b"] and [x["metric"] for x in sel["notSelected"]] == ["c"]
    assert sel["rule"] == "rank" and "secondMetric" not in sel
    assert set(rows) == {"a", "b"}


def test_c1_independent_and_discriminating_refuses_a_correlated_second(monkeypatch):
    _config_with(monkeypatch, **{"metric_portfolio.fill_to": 1,
                                 "metric_portfolio.second_metric_rule":
                                     "independent_and_discriminating"})
    sel, rows = _c1_select(_c1_evidence())
    assert sel["selected"] == ["a", "c"] and [x["metric"] for x in sel["notSelected"]] == ["b"]
    assert sel["rule"] == "independent_and_discriminating"
    second = sel["secondMetric"]
    assert second["maxAbsSpearman"] == 0.65 and second["minAuc"] == 0.55 and second["places"] == 2
    checks = {c["metric"]: c for c in second["checks"]}
    assert checks["b"]["independent"] is False and checks["b"]["joins"] is False
    assert checks["b"]["absSpearman"]["a"] > 0.9 and "Spearman" in checks["b"]["why"]
    assert checks["c"]["independent"] and checks["c"]["discriminates"] and checks["c"]["joins"]
    assert checks["c"]["auc"] == 0.7 and checks["c"]["why"] == ""
    assert methodology.round2_knobs() == {
        "metric_portfolio.fill_to": 1,
        "metric_portfolio.second_metric_rule": "independent_and_discriminating"}


def test_c1_a_second_that_does_not_discriminate_stays_out(monkeypatch):
    _config_with(monkeypatch, **{"metric_portfolio.fill_to": 1,
                                 "metric_portfolio.second_metric_rule":
                                     "independent_and_discriminating"})
    ev = _c1_evidence()
    ev["discrimination"]["c"] = {"aucRefVsPressure": 0.5}
    sel, rows = _c1_select(ev)
    assert sel["selected"] == ["a"] and set(rows) == {"a"}
    checks = {c["metric"]: c for c in sel["secondMetric"]["checks"]}
    assert checks["c"]["independent"] and not checks["c"]["discriminates"]
    assert "below 0.55" in checks["c"]["why"]
    # a candidate with no shared station values is not shown independent
    ev2 = _c1_evidence()
    ev2["data"] = ev2["data"].drop(columns=["c"])
    ev2["discrimination"]["b"] = {"aucRefVsPressure": 0.5}
    sel2, _ = _c1_select(ev2)
    checks2 = {c["metric"]: c for c in sel2["secondMetric"]["checks"]}
    assert checks2["c"]["independent"] is False and "no station values" in checks2["c"]["why"]


# --------------------------------------------------------------------------- #
# C2: the CURVE-12 gate
# --------------------------------------------------------------------------- #
DISC = {"inv": {"verdict": "inverted", "aucRefVsPressure": 0.40, "nRef": 12, "nPressure": 30},
        "none": {"verdict": "none", "aucRefVsPressure": 0.50, "nRef": 12, "nPressure": 30},
        "weak": {"verdict": "weak", "aucRefVsPressure": 0.60, "nRef": 12, "nPressure": 30},
        "good": {"verdict": "discriminates", "aucRefVsPressure": 0.80, "nRef": 12,
                 "nPressure": 30},
        "unk": {"verdict": "not_evaluable", "aucRefVsPressure": None, "nRef": 2, "nPressure": 0}}


def test_c2_the_gate_is_off_by_default_and_withholds_when_on():
    assert pe.discrimination_gate(DISC) == {}
    assert pe.discrimination_gate(DISC, {"gate": False, "min_auc": 0.55}) == {}
    gated = pe.discrimination_gate(DISC, {"gate": True, "min_auc": 0.55})
    assert sorted(gated) == ["inv", "none"]
    assert gated["inv"]["why"] == "inverted" and gated["none"]["why"] == "below_min_auc"
    assert pe.discrimination_gate_statement(gated["inv"]).startswith(
        "Discrimination gate. In the discrimination check this curve ranked pressured")
    text = pe.discrimination_gate_statement(gated["none"])
    assert "area under the curve 0.50, below the gate's floor of 0.55" in text
    assert "not scored" in text and chr(8212) not in text
    # a lower floor gates less; an unevaluable check never gates
    assert sorted(pe.discrimination_gate(DISC, {"gate": True, "min_auc": 0.45})) == ["inv"]


def test_c2_a_gated_metric_is_withheld_with_its_reason_and_kept_off_the_curve_counts(monkeypatch):
    _config_with(monkeypatch, **{"curve12.gate": True})
    assert methodology.round2_knobs() == {"curve12.gate": True}
    gated = pe.discrimination_gate(DISC)
    decision = {"status": "local", "level": "l3", "region_code": "58", "basis": "regional-reference",
                "levels_tried": [{"level": "l3", "n_usable": 12}], "options_tried": []}
    ev = {"insufficient_support": {
        "inv": {"decision": decision, "config": {"display_name": "Inverted metric", "units": "x"},
                "reason": pe.DISCRIMINATION_GATE,
                "statement": pe.discrimination_gate_statement(gated["inv"]),
                "detail": gated["inv"]}},
          "reference_support": {"inv": pe.withheld_support_record(decision, pe.DISCRIMINATION_GATE),
                                "ok": {"status": "local", "basis": "regional-reference"}},
          "reference_screen": {}, "reference_pool_summary": {}}
    w = pe.withheld_metrics(ev)
    assert len(w) == 1 and w[0]["reason"] == pe.DISCRIMINATION_GATE
    assert w[0]["statement"].startswith("Discrimination gate.") and w[0]["detail"]["auc"] == 0.4
    assert w[0]["metricName"] == "Inverted metric" and w[0]["rule"] == "CURVE-12"
    block = pe.reference_method_block(ev)
    assert block["nCurvesLocal"] == 1 and block["nWithheld"] == 1
    assert block["curvesByBasis"] == {"regional-reference": 1}
    assert pe.is_withheld_record(ev["reference_support"]["inv"])
    assert ev["reference_support"]["inv"]["status"] == pe.STATUS_WITHHELD
    assert ev["reference_support"]["inv"]["pool_status"] == "local"
    assert ev["reference_support"]["inv"]["rule"] == "CURVE-12"
    assert not pe.is_withheld_record(ev["reference_support"]["ok"])
    # a documented gap drafted for its function names the gate, not a missing pool
    result = {"coverage": {"missingFunctionIds": [w[0]["functionId"]]},
              "insufficient_support": ev["insufficient_support"]}
    if w[0]["functionId"]:
        draft = pe.coverage_exceptions_draft(result)
        assert draft and "withheld by the discrimination gate" in draft[0]["justification"]


# --------------------------------------------------------------------------- #
# C3a: the zero-inflated pool
# --------------------------------------------------------------------------- #
ZERO_POOL = [0.0, 0.0, 0.0, 0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0]
ENTRY = {"column_name": "m", "higher_is_better": True}


def _build(values, entry=ENTRY):
    frame = pd.DataFrame({"site_id": [f"s{i}" for i in range(len(values))], "m": values})
    res = curves.build_reference_curve(frame, "m", {"m": entry}, build_plots=False)
    pts = res["curve_row"]["curve_points"].iloc[0]
    points = [[float(x), float(y)] for x, y in zip(pts["metric_value"], pts["index_score"])]
    return str(res["curve_row"]["curve_status"].iloc[0]), points


def test_c3a_off_by_default_keeps_the_degenerate_fallback():
    assert curves.zero_inflation_of(ZERO_POOL, ENTRY) is None
    status, points = _build(ZERO_POOL)
    assert status == "degenerate_q25"
    assert points == [[0.0, 0.0], [0.0, 0.7], [7.5, 1.0]]


def test_c3a_the_rule_judges_the_pool_the_engine_sees():
    geo = {"zero_inflated_share": 0.25, "zero_inflated_handling": "two_part"}
    rec = curves.zero_inflation_of(ZERO_POOL, ENTRY, geo)
    assert rec["share"] == 0.4 and rec["n_zero"] == 4 and rec["n_positive"] == 6
    assert rec["two_part_applicable"] is True and rec["handling"] == "two_part"
    # a pool within the threshold, a signed scale, a falling metric and a
    # two-sided form are never zero-inflated under this rule
    assert curves.zero_inflation_of([0.0, 1.0, 2.0, 3.0, 4.0, 5.0], ENTRY, geo) is None
    assert curves.zero_inflation_of(ZERO_POOL, {**ENTRY, "signed_scale": True}, geo) is None
    assert curves.zero_inflation_of(ZERO_POOL, {**ENTRY, "higher_is_better": False}, geo) is None
    assert curves.zero_inflation_of(ZERO_POOL, {**ENTRY, "curve_form": "optimum"}, geo) is None
    # a positive part too thin for a seed: the record says the engine keeps today's fallback
    thin = curves.zero_inflation_of([0.0] * 6 + [3.0, 4.0, 5.0], ENTRY, geo)
    assert thin is not None and thin["two_part_applicable"] is False


def test_c3a_two_part_seeds_the_positive_part_and_scores_zero_at_zero(monkeypatch):
    _config_with(monkeypatch, **{"curve10.zero_inflated_share": 0.25,
                                 "curve10.zero_inflated_handling": "two_part"})
    assert methodology.round2_knobs() == {"curve10.zero_inflated_handling": "two_part",
                                          "curve10.zero_inflated_share": 0.25}
    status, points = _build(ZERO_POOL)
    stats = curves.reference_curve_summary_stats([2.0, 4.0, 6.0, 8.0, 10.0, 12.0])
    q25, q75, iqr = stats["q25"], stats["q75"], stats["iqr"]
    assert status == "complete"
    # the ladder's two lowest points fell below zero and folded onto the origin;
    # the plateau ends at the iqr-seed-3 near offset (0.5 IQR)
    assert points == [[0.0, 0.0], [q25, 0.7], [q75, 1.0], [q75 + iqr * 0.5, 1.0]]
    assert curves.interp_curve(points, 0.0) == 0.0
    # a positive part too thin for a seed keeps today's fallback
    status2, points2 = _build([0.0] * 6 + [3.0, 4.0, 5.0])
    assert status2 == "degenerate_q25" and points2 == [[0.0, 0.0], [0.0, 0.7], [3.0, 1.0]]
    # a pool that is not zero-inflated is untouched
    status3, points3 = _build([float(v) for v in range(1, 21)])
    assert status3 == "complete" and points3[0] == [0.0, 0.0] and points3[2] == [5.75, 0.7]


def test_c3a_withhold_takes_the_metric_out_with_its_statement(monkeypatch):
    _config_with(monkeypatch, **{"curve10.zero_inflated_share": 0.25,
                                 "curve10.zero_inflated_handling": "withhold"})
    data = pd.DataFrame({"site_id": [f"s{i}" for i in range(10)], "m": ZERO_POOL,
                         "k": [float(v) for v in range(1, 11)]})
    records = pe.zero_inflation_records(data, {"m": ENTRY, "k": {**ENTRY, "column_name": "k"}})
    assert list(records) == ["m"] and records["m"]["handling"] == "withhold"
    text = pe.zero_inflated_statement(records["m"])
    assert text.startswith("Zero-inflated reference pool. 4 of the 10 reference stations (40%)")
    assert "not scored" in text
    # the engine itself changes nothing under withhold: the evidence pass withholds
    status, _ = _build(ZERO_POOL)
    assert status == "degenerate_q25"
    # and off means no record at all
    assert pe.zero_inflation_records(data, {"m": ENTRY}, curves.DEFAULT_SEED_GEOMETRY) == {}


def test_c3a_the_two_part_caveat_rides_on_the_annotation():
    ev = {"reference_support": {"m": {"status": "local", "basis": "regional-reference"}},
          "zero_inflation": {"m": {"share": 0.4, "threshold": 0.25, "n_zero": 4, "n": 10,
                                   "handling": "two_part", "two_part_applicable": True}}}
    ann = pe.reference_annotations(ev, ["m"])["m"]
    assert ann["zeroInflation"]["share"] == 0.4
    assert ann["curveCaveats"] == [
        "4 of the 10 reference stations (40%) have a value of zero, above the 25% at which "
        "the pool counts as zero-inflated, so the curve was fitted to the positive values "
        "only and a value of zero scores 0 (the two-part rule)."]
    plain = pe.reference_annotations({"reference_support": ev["reference_support"]}, ["m"])["m"]
    assert "curveCaveats" not in plain and "zeroInflation" not in plain


# --------------------------------------------------------------------------- #
# C3b: the tail endpoints (adopted at methodology 0.15: iqr-seed-3)
# --------------------------------------------------------------------------- #
RISING = [float(v) for v in range(1, 21)]
SIGNED = [-8.0, -6.0, -4.0, -2.0, -1.0, 0.0, 1.0, 2.0, 4.0, 6.0]
IQR_SEED_2 = [0.3, "4/3", "7/3"]


def test_c3b_the_default_offsets_are_the_engines_and_reproduce_the_golden_seeds(monkeypatch):
    # the golden masters of tests/test_golden_masters.py (iqr-seed-3), on the shipped config
    assert curves.MONOTONE_TAIL_OFFSETS_IQR == (0.5, 1.5, 2.5)
    assert _build(RISING)[1] == [[0.0, 0.0], [2.4642857142857144, 0.3], [5.75, 0.7],
                                 [15.25, 1.0], [20.0, 1.0]]
    assert _build(RISING, {**ENTRY, "higher_is_better": False})[1] == [
        [1.0, 1.0], [5.75, 1.0], [15.25, 0.7], [29.5, 0.3], [39.0, 0.0]]
    assert _build(SIGNED, {**ENTRY, "signed_scale": True})[1] == [
        [-16.625, 0.0], [-11.375, 0.3], [-3.5, 0.7], [1.75, 1.0], [4.375, 1.0]]
    # the same values written as fractions, or as plain floats, are still the default
    for spelled in (["0.5", "3/2", "5/2"], [0.5, 1.5, 2.5]):
        _config_with(monkeypatch, **{"curve10.tail_offsets_iqr": spelled})
        assert methodology.seed_geometry()["custom_tail_offsets"] is False
        assert methodology.round2_knobs() == {}
        assert _build(RISING, {**ENTRY, "higher_is_better": False})[1][4] == [39.0, 0.0]
    assert methodology.parse_offset("4/3") == 4 / 3 and methodology.parse_offset(0.3) == 0.3


def test_c3b_the_iqr_seed_2_offsets_are_a_knob_that_reproduces_the_earlier_seeds(monkeypatch):
    """The endpoints every version published under 0.9 to 0.14 was seeded with
    stay reachable: set through the knob they join the inputs digest and give
    the iqr-seed-2 golden seeds back exactly."""
    _config_with(monkeypatch, **{"curve10.tail_offsets_iqr": IQR_SEED_2})
    geo = methodology.seed_geometry()
    assert geo["tail_offsets_iqr"] == curves.LEGACY_TAIL_OFFSETS_IQR_SEED_2
    assert geo["custom_tail_offsets"] is True
    assert methodology.round2_knobs() == {
        "curve10.tail_offsets_iqr": list(curves.LEGACY_TAIL_OFFSETS_IQR_SEED_2)}
    assert _build(RISING)[1] == [[0.0, 0.0], [2.4642857142857144, 0.3], [5.75, 0.7],
                                 [15.25, 1.0], [18.1, 1.0]]
    assert _build(RISING, {**ENTRY, "higher_is_better": False})[1] == [
        [2.9, 1.0], [5.75, 1.0], [15.25, 0.7], [27.916666666666664, 0.3],
        [37.41666666666667, 0.0]]
    assert _build(SIGNED, {**ENTRY, "signed_scale": True})[1] == [
        [-15.75, 0.0], [-10.5, 0.3], [-3.5, 0.7], [1.75, 1.0], [3.325, 1.0]]


def test_c3b_other_offsets_move_the_tails_and_join_the_digest(monkeypatch):
    _config_with(monkeypatch, **{"curve10.tail_offsets_iqr": [0.25, 1.0, 2.0]})
    assert methodology.round2_knobs() == {"curve10.tail_offsets_iqr": [0.25, 1.0, 2.0]}
    stats = curves.reference_curve_summary_stats(RISING)
    q25, q75, iqr = stats["q25"], stats["q75"], stats["iqr"]
    status, falling = _build(RISING, {**ENTRY, "higher_is_better": False})
    assert status == "complete"
    assert falling == [[max(0.0, q25 - iqr * 0.25), 1.0], [q25, 1.0], [q75, 0.7],
                       [q75 + iqr * 1.0, 0.3], [q75 + iqr * 2.0, 0.0]]
    status, rising = _build(RISING)
    assert rising[:4] == [[0.0, 0.0], [q25 * 3 / 7, 0.3], [q25, 0.7], [q75, 1.0]]
    assert rising[4] == [q75 + iqr * 0.25, 1.0]
    s = curves.reference_curve_summary_stats(SIGNED)
    status, signed = _build(SIGNED, {**ENTRY, "signed_scale": True})
    assert signed == [[s["q25"] - s["iqr"] * 2.0, 0.0], [s["q25"] - s["iqr"] * 1.0, 0.3],
                      [s["q25"], 0.7], [s["q75"], 1.0], [s["q75"] + s["iqr"] * 0.25, 1.0]]
    # the two-sided seed is not governed by this knob
    _, optimum = _build([2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0],
                        {"column_name": "m", "curve_form": "optimum"})
    assert optimum == [[0.0, 0.0], [0.0, 0.3], [2.675, 0.7], [4.25, 1.0],
                       [8.75, 1.0], [10.325, 0.7], [13.25, 0.3], [17.75, 0.0]]


# --------------------------------------------------------------------------- #
# the digest: a knob joins only when it is set away from its default
# --------------------------------------------------------------------------- #
def test_the_knobs_join_the_digest_payload_only_when_recorded():
    base = {"inputs": {"reference": {"method": "pressure-screen"}}}
    plain = pv.digest_payload_from_manifest(base)
    assert "knobs" not in plain["reference"]
    knobs = {"reference_pool.ladder_rule": "narrowest_adequate", "curve12.gate": True}
    set_ = {"inputs": {"reference": {"method": "pressure-screen", "knobs": knobs}}}
    payload = pv.digest_payload_from_manifest(set_)
    assert payload["reference"]["knobs"] == knobs
    assert list(payload["reference"]["knobs"]) == sorted(knobs)
    assert methodology.inputs_digest(payload) != methodology.inputs_digest(plain)
    # an empty block records nothing, as a default run's manifest would
    empty = {"inputs": {"reference": {"method": "pressure-screen", "knobs": {}}}}
    assert "knobs" not in pv.digest_payload_from_manifest(empty)["reference"]


def test_the_manifest_records_the_knobs_a_run_carried(monkeypatch):
    """build_run_manifest writes reference.knobs from the result only when the
    evidence pass recorded a non-default knob; the digest follows."""
    result = {"region": {"kind": "ecoregion", "code": "58", "name": "x"},
              "reference_method": "pressure-screen", "reference_screen": {"id": "s", "tier": "strict"},
              "scale_registry": {}, "nrsa_dataset": "multi-cycle-v1"}
    plain = pv.build_run_manifest(result, argv=[], started_at="a", finished_at="a")
    assert "knobs" not in plain["inputs"]["reference"]
    knobbed = pv.build_run_manifest(
        {**result, "methodology_knobs": {"curve12.gate": True, "curve12.min_auc": 0.6}},
        argv=[], started_at="a", finished_at="a")
    assert knobbed["inputs"]["reference"]["knobs"] == {"curve12.gate": True,
                                                       "curve12.min_auc": 0.6}
    assert knobbed["inputsDigest"] != plain["inputsDigest"]
    assert pv.digest_payload_from_manifest(knobbed)["reference"]["knobs"]["curve12.gate"] is True
    assert methodology.inputs_digest(pv.digest_payload_from_manifest(plain)) == plain["inputsDigest"]


def test_a_fraction_offset_multiplies_then_divides_like_the_iqr_seed_2_literals(monkeypatch):
    """The iqr-seed-2 engine wrote the falling seed's mid and far knots as ``iqr * 4 / 3``
    and ``iqr * 7 / 3`` (multiply, then divide); a pre-rounded 4/3 differs from that in
    the last place, which 88 of the EASI members refits showed on 2026-09-26. A fraction
    spelled in the knob keeps its numerator and denominator and the seed reproduces the
    literals bit for bit; a decimal offset is one multiplication, as before."""
    import json
    import pickle
    rng = np.random.default_rng(7)
    iqrs = [float(v) for v in rng.uniform(0.001, 500.0, size=2000)] + [
        0.1, 0.3, 1.0, 2.0, 3.0, 7.0, 9.5, 1e-6, 12345.678]
    mid, far = methodology.parse_offset("4/3"), methodology.parse_offset("7/3")
    assert isinstance(mid, curves.IqrOffset) and mid.ratio == (4, 3) and mid == 4 / 3
    assert pickle.loads(pickle.dumps(mid)).ratio == (4, 3)
    assert json.dumps(mid) == json.dumps(4 / 3)
    assert methodology.parse_offset(0.3) == 0.3 and not hasattr(methodology.parse_offset(0.3), "ratio")
    for iqr in iqrs:
        assert curves.offset_times(iqr, mid) == iqr * 4 / 3
        assert curves.offset_times(iqr, far) == iqr * 7 / 3
        # the iqr-seed-3 default is one multiplication whichever way it is spelled
        assert curves.offset_times(iqr, 1.5) == iqr * 1.5 == curves.offset_times(iqr, methodology.parse_offset("3/2"))
        assert curves.offset_times(iqr, 2.5) == iqr * 2.5 == curves.offset_times(iqr, methodology.parse_offset("5/2"))
        assert curves.offset_times(iqr, 0.5) == iqr * 0.5 == curves.offset_times(iqr, methodology.parse_offset("1/2"))
    # the distinction is not idle: the pre-rounded product differs somewhere in the sample
    assert any(iqr * (4 / 3) != iqr * 4 / 3 for iqr in iqrs)
    # through the knob, a falling seed's knots are the literals' arithmetic
    _config_with(monkeypatch, **{"curve10.tail_offsets_iqr": IQR_SEED_2})
    frame = pd.DataFrame({"site_id": [f"s{i}" for i in range(20)], "m": [float(v) for v in range(1, 21)]})
    res = curves.build_reference_curve(frame, "m", {"m": {"column_name": "m", "higher_is_better": False}},
                                       build_plots=False)
    pts = res["curve_row"]["curve_points"].iloc[0]
    xs = [float(x) for x in pts["metric_value"]]
    q25, q75, iqr = 5.75, 15.25, 9.5
    assert xs[3] == q75 + iqr * 4 / 3 and xs[4] == q75 + iqr * 7 / 3 and xs[0] == max(0.0, q25 - iqr * 0.3)
