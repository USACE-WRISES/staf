"""Round 2 helpers (streamcurves.round2) and the orchestrator's refusals.

The statistics on synthetic data (the paired delta and its interval, the paired AUC
delta, Benjamini-Hochberg), the adoption rule for every decision type, the subgroup
block, arm config generation for every candidate of the protocol (the knob lands in
the copied config, the baseline is untouched), the C4 map variant rules, the scorer
against DEEP's own interpolation and rollup, and the refusal on a protocol sha mismatch.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from streamcurves import round2
from streamcurves.paths import APP_CONFIG_DIR

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
SCRIPTS = APP / "scripts"
COMMITTED_PROTOCOL = APP_CONFIG_DIR / "methodology" / "evaluation_protocol_v1.yaml"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

#: The candidates block of Pre-registration V, verbatim, so the generator is tested
#: without the notes folder; the committed protocol is used instead when present.
PROTOCOL_TEXT = """\
schema: evaluation-protocol/1
protocol_version: "1.0"
campaign: national-campaign-2026-09
baseline:
  methodology_version: "0.14-provisional"
  value_policy_round2: newest-nonnull-v2
regions:
  development:
    published: ["58", "71", "55", "65", "27", "50", "13"]
    added: ["43", "17", "1", "59", "47", "75", "45"]
    testable_cells: ["58", "50", "13", "65", "43", "17", "1"]
candidates:
  B1: {family: pool_rule, config: {reference_pool.ladder_rule: narrowest_adequate}, primary_outcome: O1, decision: accuracy_change}
  B2: {family: regional_screen, config: {regional_screen.enabled: false}, primary_outcome: O2, decision: accuracy_change}
  B3: {family: search_order, config: {reference_transfer.search_order: [l3, l2, l1, nars9]}, primary_outcome: O1, decision: simplification}
  B4: {family: data_floors, config: {data_rules.min_n_unstratified: 25, data_rules.exploratory_n_unstratified: 15, data_rules.min_n_stratum: 20, data_rules.very_small_stratum_n: 8}, primary_outcome: O5, decision: simplification}
  C1: {family: portfolio_size, config: {portfolio.fill_to: 1, portfolio.second_metric_max_abs_spearman: 0.65, portfolio.second_metric_min_auc: 0.55}, primary_outcome: O3, decision: simplification}
  C2: {family: discrimination_gate, config: {curve12.gate: true, curve12.min_auc: 0.55}, primary_outcome: O3, decision: accuracy_change}
  C3a: {family: geometry_zero_inflated, config: {curve10.zero_inflated_share: 0.25, curve10.zero_inflated_handling: two_part_or_withhold}, primary_outcome: O5, decision: accuracy_change}
  C3b: {family: geometry_endpoints, config: {curve10.tail_offsets_iqr: [0.3, 1.3333, 2.3333]}, primary_outcome: O5, decision: simplification}
  C4: {family: assignment, config: {metric_map: evidence_table_revise_set}, primary_outcome: O3, decision: accuracy_change}
  C5: {family: sqt_sources, config: {owner_decisions.alternatives_over_fitted: true}, primary_outcome: O4, decision: coverage_only, constraint: an unresolved question blocks adoption (D4a)}
  C6: {family: carry_forward, config: {refit: all}, primary_outcome: O1, decision: reference_arm}
"""


@pytest.fixture
def protocol_path(tmp_path) -> Path:
    if COMMITTED_PROTOCOL.is_file():
        return COMMITTED_PROTOCOL
    p = tmp_path / "evaluation_protocol_v1.yaml"
    p.write_text(PROTOCOL_TEXT, encoding="utf-8")
    return p


@pytest.fixture
def source_config(tmp_path) -> Path:
    """A small config folder with the four real files the knobs land in."""
    src = tmp_path / "source_config"
    (src / "methodology").mkdir(parents=True)
    for rel in ("methodology/methodology_config.yaml", "reference_transfer.yaml", "metric_map.yaml",
                "metric_evidence.yaml"):
        shutil.copyfile(APP_CONFIG_DIR / rel, src / rel)
    return src


# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #
def test_auc_and_spearman_on_known_data():
    assert round2.auc([1, 2, 3, 4], [False, False, True, True]) == 1.0
    assert round2.auc([1, 2, 3, 4], [True, True, False, False]) == 0.0
    assert round2.auc([1, 1, 1, 1], [True, False, True, False]) == 0.5
    assert round2.auc([1, 2], [True, True]) is None
    assert round2.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert round2.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert round2.spearman([1, 2], [1, 2]) is None


def test_paired_delta_finds_a_shift_and_not_a_null():
    rng = np.random.default_rng(3)
    base = rng.normal(0.7, 0.05, size=120)
    clusters = [f"h{i % 12}" for i in range(120)]
    shifted = round2.paired_delta(base + 0.10, base, clusters, level=0.90, n_boot=100, seed=11)
    assert shifted["n"] == 120 and shifted["n_clusters"] == 12
    assert shifted["estimate"] == pytest.approx(0.10)
    assert shifted["excludes_zero"] is True and shifted["lo"] > 0
    noise = base + rng.normal(0.0, 0.01, size=120)
    null = round2.paired_delta(noise, base, clusters, level=0.90, n_boot=100, seed=11)
    assert null["excludes_zero"] is False
    assert null["lo"] < 0 < null["hi"]
    assert null["p_value"] is not None and null["p_value"] > 0.05


def test_paired_delta_is_deterministic_and_drops_missing_pairs():
    a = np.array([0.9, 0.8, np.nan, 0.7, 0.6])
    b = np.array([0.8, 0.8, 0.5, np.nan, 0.5])
    one = round2.paired_delta(a, b, ["x", "x", "y", "y", "z"], n_boot=50, seed=5)
    two = round2.paired_delta(a, b, ["x", "x", "y", "y", "z"], n_boot=50, seed=5)
    assert one == two
    assert one["n"] == 3
    with pytest.raises(ValueError):
        round2.paired_delta([1, 2], [1], ["a", "b"])


def test_paired_auc_delta_on_identical_stations():
    rng = np.random.default_rng(7)
    good = rng.normal(0.8, 0.1, size=40)
    poor = rng.normal(0.4, 0.1, size=40)
    strong = np.concatenate([good, poor])
    weak = strong + rng.normal(0.0, 0.5, size=80)
    pos = np.array([True] * 40 + [False] * 40)
    clusters = [f"huc{i % 10}" for i in range(80)]
    got = round2.paired_auc_delta(strong, weak, pos, clusters, n_boot=100, seed=11)
    assert got["auc_a"] > got["auc_b"] and got["delta"] > 0
    assert got["lo"] > round2.MARGINS["O3"]["block_lower_bound"] and got["blocks"] is False
    same = round2.paired_auc_delta(strong, strong, pos, clusters, n_boot=50, seed=11)
    assert same["delta"] == 0.0 and same["lo"] == 0.0 and same["hi"] == 0.0
    worse = round2.paired_auc_delta(weak, strong, pos, clusters, n_boot=100, seed=11)
    assert worse["blocks"] is True


def test_benjamini_hochberg():
    q = round2.bh_adjust({"a": 0.01, "b": 0.04, "c": 0.03, "d": None})
    assert q["a"] == pytest.approx(0.03)
    assert q["b"] == pytest.approx(0.04)
    assert q["c"] == pytest.approx(0.04)
    assert q["d"] is None


def test_seed_for_is_content_derived():
    assert round2.seed_for("B1", "o1", seed=11) == round2.seed_for("B1", "o1", seed=11)
    assert round2.seed_for("B1", "o1", seed=11) != round2.seed_for("B1", "o1", seed=12)


# --------------------------------------------------------------------------- #
# the adoption rule
# --------------------------------------------------------------------------- #
def _delta(estimate, lo, hi):
    return {"estimate": estimate, "lo": lo, "hi": hi, "level": 0.9, "excludes_zero": bool(lo > 0 or hi < 0),
            "statistic": "median", "n": 40, "n_clusters": 7}


def test_accuracy_change_needs_the_interval_and_the_margin():
    ok = round2.adoption("accuracy_change", primary=_delta(0.08, 0.02, 0.14), limits=[])
    assert ok["verdict"] == "adopt"
    small = round2.adoption("accuracy_change", primary=_delta(0.03, 0.01, 0.05), limits=[])
    assert small["verdict"] == "reject"
    wide = round2.adoption("accuracy_change", primary=_delta(0.08, -0.02, 0.18), limits=[])
    assert wide["verdict"] == "reject"
    none = round2.adoption("accuracy_change", primary=None, limits=[])
    assert none["verdict"] == "reject" and "no paired comparison" in " ".join(none["reasons"])


def test_simplification_is_noninferiority():
    assert round2.adoption("simplification", primary=_delta(-0.01, -0.04, 0.02), limits=[])["verdict"] == "adopt"
    assert round2.adoption("simplification", primary=_delta(-0.03, -0.06, 0.00), limits=[])["verdict"] == "reject"


def test_blocks_reject_whatever_the_primary_says():
    limits = [round2.o3_block([{"target": "benthic_mmi", "lo": -0.03}])]
    got = round2.adoption("accuracy_change", primary=_delta(0.2, 0.1, 0.3), limits=limits)
    assert got["verdict"] == "reject" and got["blocks"] == ["O3"]
    o2 = round2.o2_limit(0.06, 0.01)
    assert o2["blocks"] is True and "above" in o2["why"]
    assert round2.o2_limit(0.04, 0.01)["blocks"] is True      # worsened by 0.03
    assert round2.o2_limit(0.04, 0.03)["blocks"] is False
    assert round2.o5_limit(0.10, 0.07)["blocks"] is True
    assert round2.o5_limit(0.10, 0.09)["blocks"] is False
    assert round2.o5_limit(None, 0.09)["blocks"] is False


def test_reference_arm_and_coverage_only():
    ref = round2.adoption("reference_arm", primary=_delta(0.2, 0.1, 0.3), limits=[])
    assert ref["verdict"] == "reference-only"
    cov = round2.adoption("coverage_only", primary=None, limits=[], coverage_gain=3)
    assert cov["verdict"] == "reference-only" and any("D4a" in r for r in cov["reasons"])
    cov = round2.adoption("coverage_only", primary=None, limits=[], coverage_gain=3, constraint_resolved=True)
    assert cov["verdict"] == "adopt"
    cov = round2.adoption("coverage_only", primary=None, limits=[], coverage_gain=0, constraint_resolved=True)
    assert cov["verdict"] == "reject"
    with pytest.raises(ValueError):
        round2.adoption("unknown", primary=None, limits=[])


def test_subgroup_block():
    sg = round2.subgroup_block({"CPL": 0.02, "NAP": -0.12, "WMT": -0.05})
    assert sg["blocks"] is True and sg["losing"] == ["NAP"]
    assert round2.subgroup_block({"CPL": -0.09, "NAP": None})["blocks"] is False
    got = round2.adoption("simplification", primary=_delta(0.0, -0.02, 0.02), limits=[sg])
    assert got["verdict"] == "reject" and got["blocks"] == ["subgroups"]


# --------------------------------------------------------------------------- #
# arm config roots
# --------------------------------------------------------------------------- #
def test_route_knob():
    assert round2.route_knob("reference_pool.ladder_rule")["file"] == round2.METHODOLOGY_FILE
    assert round2.route_knob("regional_screen.enabled")["path"] == "reference_hierarchy.regional_screen.enabled"
    assert round2.route_knob("portfolio.fill_to")["path"] == "metric_portfolio.fill_to"
    assert round2.route_knob("reference_transfer.search_order") == {"kind": "transfer", "file": "reference_transfer.yaml",
                                                                    "path": "search_order"}
    assert round2.route_knob("metric_map")["kind"] == "metric_map"
    assert round2.route_knob("refit")["kind"] == "stage"
    with pytest.raises(round2.KnobError):
        round2.route_knob("nonsense")


def test_arms_for_every_candidate(tmp_path, protocol_path, source_config):
    proto = round2.load_protocol(protocol_path)
    before = round2.config_tree_fingerprint(source_config)
    root = tmp_path / "round2"
    base = round2.build_arm_root(protocol_path, round2.BASELINE_ARM, source_config=source_config, out_root=root)
    base_rec = round2.read_arm(base)
    assert base_rec["config"]["untouched"] is True and base_rec["knobs"] == {}
    assert base_rec["protocol"]["sha256"] == round2.sha256_of(protocol_path)
    for arm_id in round2.candidate_ids(proto):
        d = round2.build_arm_root(protocol_path, arm_id, source_config=source_config, out_root=root)
        rec = round2.read_arm(d)
        cand = round2.candidate(proto, arm_id)
        assert rec["knobs"] == cand["config"]
        assert rec["protocol"]["sha256"] == round2.sha256_of(protocol_path)
        assert rec["stageFlags"]["refit"] == "all"
        assert rec["stageFlags"]["maintainer"] == round2.REHEARSAL_MAINTAINER
        for r in rec["applied"]:
            if r["file"] is None:
                assert r["engineKey"] in round2.STAGE_KNOBS
                continue
            if r["engineKey"] == "metric_map":
                text = (d / "config" / round2.METRIC_MAP_FILE).read_text(encoding="utf-8")
                assert "candidate C4 variant" in text and r["rules"]
                continue
            doc = yaml.safe_load((d / "config" / r["file"]).read_text(encoding="utf-8"))
            assert round2.get_dotted(doc, r["engineKey"]) == r["value"], (arm_id, r)
        if arm_id == "C6":
            assert rec["config"]["untouched"] is True and rec["changes"]["stage_flags"] == ["refit"]
        else:
            assert rec["config"]["untouched"] is False
    assert round2.config_tree_fingerprint(source_config) == before, "the source config is never edited"
    c1 = yaml.safe_load((round2.arm_dir(root, "C1") / "config" / round2.METHODOLOGY_FILE).read_text(encoding="utf-8"))
    assert round2.get_dotted(c1, "metric_portfolio.second_metric_rule") == "independent_and_discriminating"
    assert round2.get_dotted(c1, "metric_portfolio.fill_to") == 1
    b2 = yaml.safe_load((round2.arm_dir(root, "B2") / "config" / round2.METHODOLOGY_FILE).read_text(encoding="utf-8"))
    assert round2.get_dotted(b2, "reference_hierarchy.regional_screen.enabled") is False
    b4 = yaml.safe_load((round2.arm_dir(root, "B4") / "config" / round2.METHODOLOGY_FILE).read_text(encoding="utf-8"))
    assert round2.get_dotted(b4, "acceptance.sample_adequacy.adequate") == 25, "B4 moves both floor copies"
    assert round2.get_dotted(b4, "data_rules.min_n_unstratified") == 25
    b3 = yaml.safe_load((round2.arm_dir(root, "B3") / "config" / round2.TRANSFER_FILE).read_text(encoding="utf-8"))
    assert b3["search_order"] == ["l3", "l2", "l1", "nars9"]
    assert b3["families"]["biology"]["search_order"] == ["l3", "l2", "nars9", "l1"], "per-family orders kept"
    text = (root / "arms" / "B3" / "config" / round2.TRANSFER_FILE).read_text(encoding="ascii")
    assert "GENERATED" in text.splitlines()[0]


def test_finalist_composes_and_refuses_a_conflict(tmp_path, protocol_path, source_config):
    root = tmp_path / "round2"
    for arm_id in ("B1", "B3", "C1"):
        round2.build_arm_root(protocol_path, arm_id, source_config=source_config, out_root=root)
    target = round2.compose_finalist(protocol_path, [round2.arm_dir(root, a) for a in ("B1", "B3", "C1")],
                                     source_config=source_config, out_root=root)
    rec = round2.read_arm(target)
    assert rec["members"] == ["B1", "B3", "C1"]
    doc = yaml.safe_load((target / "config" / round2.METHODOLOGY_FILE).read_text(encoding="utf-8"))
    assert round2.get_dotted(doc, "reference_pool.ladder_rule") == "narrowest_adequate"
    assert round2.get_dotted(doc, "metric_portfolio.fill_to") == 1
    # a second arm setting the same key differently is refused
    other = round2.build_arm_root(protocol_path, "B1x", source_config=source_config, out_root=root,
                                  knobs={"reference_pool.ladder_rule": "first_pass"})
    with pytest.raises(round2.KnobError):
        round2.compose_finalist(protocol_path, [round2.arm_dir(root, "B1"), other],
                                source_config=source_config, out_root=root)


def test_refusal_on_a_protocol_sha_mismatch(tmp_path, protocol_path, source_config):
    root = tmp_path / "round2"
    round2.build_arm_root(protocol_path, "B1", source_config=source_config, out_root=root)
    moved = tmp_path / "moved_protocol.yaml"
    moved.write_text(protocol_path.read_text(encoding="utf-8") + "\n# an addendum after the results were read\n",
                     encoding="utf-8")
    assert round2.sha256_of(moved) != round2.sha256_of(protocol_path)
    with pytest.raises(round2.ProtocolMismatch):
        round2.build_arm_root(moved, "B1", source_config=source_config, out_root=root)
    with pytest.raises(round2.ProtocolMismatch):
        round2.compose_finalist(moved, [round2.arm_dir(root, "B1")], source_config=source_config, out_root=root)
    import run_round2
    with pytest.raises(SystemExit) as exc:
        run_round2.main(["stage", "--arm", "B1", "--out", str(root), "--protocol", str(moved), "--regions", "55"])
    assert "refusing" in str(exc.value)
    with pytest.raises(SystemExit):
        run_round2.main(["arms", "--out", str(root), "--protocol", str(moved), "--arm", "B1",
                         "--source-config", str(source_config)])
    with pytest.raises(SystemExit):
        run_round2.main(["compare", "--arm", "B1", "--out", str(root), "--protocol", str(moved)])
    # and the committed protocol still passes
    assert run_round2.main(["arms", "--out", str(root), "--protocol", str(protocol_path), "--arm", "B1",
                            "--source-config", str(source_config)]) == 0


def test_stage_argv_carries_the_rehearsal_flags(tmp_path, protocol_path, source_config):
    import run_round2
    root = tmp_path / "round2"
    d = round2.build_arm_root(protocol_path, "B4", source_config=source_config, out_root=root)
    argv = run_round2.stage_argv(d, round2.read_arm(d), ["55", "71"], workers=3, n_boot=50)
    assert argv[3] == "stage-many" and "--isolated" in argv
    assert argv[argv.index("--refit") + 1] == "all"
    assert argv[argv.index("--maintainer") + 1] == round2.REHEARSAL_MAINTAINER
    assert argv[argv.index("--out-root") + 1] == str(d / "campaign")
    assert argv.count("--l3") == 2 and argv[argv.index("--n-boot") + 1] == "50"


# --------------------------------------------------------------------------- #
# candidate C4
# --------------------------------------------------------------------------- #
def _listed(doc, code):
    return [f["function"] for f in doc["functions"] for m in f["metrics"] if m["code"] == code]


def test_c4_variant_rules_on_the_committed_map():
    mm = yaml.safe_load((APP_CONFIG_DIR / "metric_map.yaml").read_text(encoding="utf-8"))
    ev = yaml.safe_load((APP_CONFIG_DIR / "metric_evidence.yaml").read_text(encoding="utf-8"))
    out, log = round2.apply_revise_dispositions(mm, ev)
    rules = {r["rule"] for r in log}
    assert {"bfi_covariate", "chlorophyll_one_function", "dam_pressure_once", "agriculture_crops_plus_hay",
            "doc_decided", "fish_proportion_set", "left_as_listed"} <= rules
    for f in out["functions"]:
        for m in f["metrics"]:
            if m["code"] == "bfi":
                assert m["role"] == "predictor" and m["default_selected"] is False
    assert _listed(out, "chem_CHLA") == [round2.CHLOROPHYLL_FUNCTION]
    assert _listed(out, "damdens") == [round2.DAM_PRESSURE_FUNCTION]
    assert _listed(out, "pctcrop2019") == [] and _listed(out, round2.AGRICULTURE_CODE) == ["Catchment hydrology"]
    assert _listed(out, "chem_DOC") == []
    pop = next(f for f in out["functions"] if f["function"] == round2.POPULATION_SUPPORT_FUNCTION)
    by = {m["code"]: m for m in pop["metrics"]}
    assert all(by[c]["default_selected"] and not by[c]["reserve"] for c in round2.FISH_PROPORTION_SET)
    assert by[round2.FISH_RICHNESS_ALTERNATIVE]["reserve"] is True and not by[round2.FISH_RICHNESS_ALTERNATIVE]["default_selected"]
    assert by["bent_TOTLNTAX"]["default_selected"] is True
    # retained metrics and the input are untouched
    assert _listed(out, "chem_COND") == _listed(mm, "chem_COND")
    assert _listed(mm, "chem_CHLA") == ["Light and thermal regime"]
    left = {r["code"] for r in log if r["rule"] == "left_as_listed"}
    assert {"bent_HPRIME", "bent_TOTLNTAX", "chem_NTL", "pctwet2019", "phab_LWDeqVolM100"} <= left
    text = round2.render_metric_map(out, log, evidence_sha="e" * 64, source_sha="s" * 64)
    assert text.startswith("# metric_map.yaml, candidate C4 variant")
    assert yaml.safe_load(text)["functions"] == out["functions"]
    assert chr(0x2014) not in text


def test_c4_omit_and_unchanged_dispositions():
    mm = yaml.safe_load((APP_CONFIG_DIR / "metric_map.yaml").read_text(encoding="utf-8"))
    ev = {"metrics": {"phab_SINU": {"disposition": "omit"}, "chem_COND": {"disposition": "retain"},
                      "bfi": {"disposition": "retain", "construct_class": "natural_setting_covariate"}}}
    out, log = round2.apply_revise_dispositions(mm, ev)
    assert _listed(out, "phab_SINU") == [] and [r["rule"] for r in log] == ["omit"]
    assert _listed(out, "bfi") == _listed(mm, "bfi")
    assert next(m for f in out["functions"] for m in f["metrics"] if m["code"] == "bfi")["default_selected"] is True


# --------------------------------------------------------------------------- #
# the scorer against DEEP
# --------------------------------------------------------------------------- #
def _bundle():
    return {
        "assessmentId": "test", "region": {"kind": "ecoregion", "code": "55", "name": "Test"},
        "metricsByFunction": [
            {"functionId": "water-soil-quality", "functionName": "Water & soil quality", "discipline": "Physicochemistry",
             "metrics": [
                 {"metricId": "spring-chem-cond", "metricName": "Conductivity", "criteriaBasis": "reference",
                  "basis": "regional-reference", "howToMeasure": "probe",
                  "curve": {"points": [{"x": 50.0, "y": 1.0}, {"x": 200.0, "y": 0.69}, {"x": 400.0, "y": 0.39},
                                       {"x": 800.0, "y": 0.0}]}},
                 {"metricId": "spring-chem-ph", "metricName": "pH", "criteriaBasis": "reference",
                  "basis": "regional-reference", "howToMeasure": "probe",
                  "curve": {"points": []},
                  "curveLayers": [{"stratum": "", "points": [{"x": 6.0, "y": 0.0}, {"x": 7.0, "y": 1.0}, {"x": 9.0, "y": 0.0}]},
                                  {"stratum": "lt_10", "points": [{"x": 5.0, "y": 0.0}, {"x": 6.5, "y": 1.0}, {"x": 8.0, "y": 0.0}]}],
                  "stratifier": {"variable": "drainage_area_sqkm", "breaks": [10.0], "right": True,
                                 "classes": [{"key": "lt_10", "label": "under 10 km2"}, {"key": "ge_10", "label": "10 km2 and up"}]}}]},
            {"functionId": "catchment-hydrology", "functionName": "Catchment hydrology", "discipline": "Hydrology",
             "metrics": [{"metricId": "spring-pctimp2019ws", "metricName": "Impervious", "criteriaBasis": "fixed",
                          "basis": "published-benchmark",
                          "curve": {"points": [{"x": 0.0, "y": 1.0}, {"x": 10.0, "y": 0.69}, {"x": 25.005, "y": 0.39},
                                               {"x": 44.5, "y": 0.0}]}}]},
            {"functionId": "habitat-provision", "functionName": "Habitat provision", "discipline": "Biology", "metrics": []},
        ],
        "insufficientReferenceSupport": [{"metricId": "spring-phab-xfc-nat", "reason": "insufficient-reference-support"}],
    }


def _deep():
    deep_root = REPO / "apps" / "deep"
    if not (deep_root / "deep" / "curves.py").is_file():
        pytest.skip("apps/deep is not beside this checkout")
    if str(deep_root) not in sys.path:
        sys.path.insert(0, str(deep_root))
    from deep import curves as deep_curves, models as deep_models, reference_support as deep_rs
    return deep_curves, deep_models, deep_rs


@pytest.mark.parametrize("cond,ph,da,imp", [(120.0, 7.4, 4.0, 3.0), (650.0, 6.2, 40.0, 30.0), (30.0, 9.5, 12.0, 0.0),
                                            (None, 7.0, 4.0, 12.0), (1000.0, None, None, 50.0)])
def test_scorer_matches_deep(cond, ph, da, imp):
    deep_curves, deep_models, deep_rs = _deep()
    bundle = _bundle()
    columns_of = round2.metric_columns(bundle, ["chem_COND", "chem_PH", "pctimp2019ws", "drainage_area_sqkm"])
    assert columns_of == {"spring-chem-cond": "chem_COND", "spring-chem-ph": "chem_PH", "spring-pctimp2019ws": "pctimp2019ws"}
    values = {"chem_COND": cond, "chem_PH": ph, "pctimp2019ws": imp}
    mine = round2.score_station(bundle, values, columns_of=columns_of, drainage_area_sqkm=da, nhd_slope=None)
    delineation = {"delineation": {"drainage_area_sqkm": da, "slope": None}}
    measured = []
    for fid, m in round2.bundle_metrics(bundle):
        col = columns_of[m["metricId"]]
        v = values.get(col)
        stratum_mine = round2.station_stratum(m, drainage_area_sqkm=da, nhd_slope=None)
        stratum_deep = deep_rs.auto_stratum(m, delineation)
        assert stratum_mine == stratum_deep, (m["metricId"], stratum_mine, stratum_deep)
        measured.append(deep_models.MeasuredValue(metric_id=m["metricId"], value=v, na=v is None, stratum=stratum_deep))
    result, functions = deep_curves.score_site(bundle, measured)
    for fid, fr in functions.items():
        assert mine["functions"][fid] == pytest.approx(fr.score) if fr.score is not None else mine["functions"][fid] is None
        for mid, idx in fr.metric_indices.items():
            got = mine["indices"][f"{fid}|{mid}"]
            assert (got is None and idx is None) or got == pytest.approx(idx)
    assert mine["eci"] is None and result["ecosystemConditionIndexRaw"] is None, "habitat provision is unassessed"
    assert mine["eci_over_scored"] == pytest.approx(result["ecosystemConditionIndexOverScoredRaw"])
    assert list(mine["eci_bounds"]) == pytest.approx([round(x, 12) for x in result["ecosystemConditionIndexBounds"]], abs=0.01)
    assert mine["unassessed"] == result["unassessedFunctions"]


def test_rollup_matches_deep_on_a_complete_assessment():
    deep_root = REPO / "apps" / "deep"
    if not (deep_root / "deep" / "scoring.py").is_file():
        pytest.skip("apps/deep is not beside this checkout")
    if str(deep_root) not in sys.path:
        sys.path.insert(0, str(deep_root))
    from deep import scoring as deep_scoring
    scores = {fid: float(5 + (i * 7) % 11) for i, fid in enumerate(round2.FUNCTION_IDS)}
    mine = round2.rollup(scores)
    theirs = deep_scoring.rollup(scores)
    assert mine["eci"] == pytest.approx(theirs.ecosystem_condition_index)
    for k in round2.OUTCOMES:
        assert mine["sub_indices"][k] == pytest.approx(theirs.sub_indices[k])
    partial = {k: v for k, v in scores.items() if k not in ("nutrient-cycling", "habitat-provision")}
    mine = round2.rollup(partial, unassessed=("nutrient-cycling", "habitat-provision"))
    theirs = deep_scoring.rollup(partial, unassessed=("nutrient-cycling", "habitat-provision"))
    assert mine["eci"] is None and theirs.ecosystem_condition_index is None
    assert list(mine["eci_bounds"]) == pytest.approx(list(theirs.eci_bounds))


def test_outcome_mapping_is_deeps():
    p = REPO / "apps" / "deep" / "data" / "deep-outcome-mapping.json"
    if not p.is_file():
        pytest.skip("apps/deep data is not beside this checkout")
    rows = json.loads(p.read_text(encoding="utf-8"))
    assert {r["id"]: {k: r[k] for k in round2.OUTCOMES} for r in rows} == round2.OUTCOME_MAPPING


def test_interp_curve_is_deeps():
    deep_curves, _m, _r = _deep()
    from streamcurves import curves
    pts = [{"x": 3.0, "y": 0.2}, {"x": 1.0, "y": 1.0}, {"x": 3.0, "y": 0.5}, {"x": 5.0, "y": 0.0}]
    for x in (-1.0, 1.0, 2.0, 3.0, 4.0, 5.0, 9.0):
        assert curves.interp_curve(pts, x) == deep_curves.interp_curve(pts, x)


def test_coverage_and_usability_of_a_published_bundle():
    p = REPO / "apps" / "library" / "assessments" / "interior-plateau" / "v6" / "assessment.deep.json"
    if not p.is_file():
        pytest.skip("the published Interior Plateau bundle is not beside this checkout")
    b = json.loads(p.read_text(encoding="utf-8"))
    cov = round2.coverage_of(b)
    assert cov["functionsSupported"] == 20 and cov["unassessed"] == []
    assert cov["withheld"] == len(b["insufficientReferenceSupport"])
    assert sum(cov["curvesByBasis"].values()) == cov["metricListings"]
    use = round2.usability_of(b)
    assert 0 < use["fieldMetrics"] <= use["distinctProcedures"] + use["fieldMetrics"]
    assert all(m.startswith(round2.NRSA_FIELD_PREFIXES) for m in use["fieldMetricIds"])


def test_station_list_hash_is_order_free():
    assert round2.station_list_hash(["b", "a"]) == round2.station_list_hash(["a", "b"])
    assert round2.station_list_hash(["a"]) != round2.station_list_hash(["a", "b"])


# --------------------------------------------------------------------------- #
# the hierarchy harness read as cells
# --------------------------------------------------------------------------- #
def _hierarchy(flips: dict, nars9: dict, exceed=0.5, net=0.01):
    rows = []
    for (metric, l3, basis), flip in flips.items():
        rows.append({"metric": metric, "l3": l3, "basis": basis, "flip": flip, "exceedance": exceed,
                     "net_optimism": net, "nars9": nars9[l3], "family": "water_chemistry", "role": "dev"})
    return pd.DataFrame(rows)


def test_o1_comparison_pairs_identical_cells():
    nars9 = {"58": "NAP", "50": "UMW", "13": "XER"}
    cells = {(m, l3, b) for m in ("chem_COND", "chem_PH") for l3 in nars9 for b in ("2r_l2", "3c_matched")}
    base = _hierarchy({c: 0.30 for c in cells}, nars9)
    arm = _hierarchy({c: (0.20 if c[1] != "13" else 0.45) for c in cells}, nars9)
    arm = pd.concat([arm, _hierarchy({("chem_TURB", "58", "2r_l2"): 0.1}, nars9)], ignore_index=True)
    got = round2.o1_comparison(arm, base, n_boot=50, seed=11)
    assert got["n_cells"] == len(cells) and got["n_cells_arm"] == len(cells) + 1
    assert got["delta"]["estimate"] == pytest.approx(0.10)
    assert got["by_nars9"]["XER"] == pytest.approx(-0.15) and got["by_nars9"]["NAP"] == pytest.approx(0.10)
    assert round2.subgroup_block(got["by_nars9"])["losing"] == ["XER"]
    assert got["a1_share_arm"] == 1.0
    assert round2.o2_summary(arm) == pytest.approx(0.01)


def test_verdict_markdown_reads_plainly():
    v = {"arm": "B1", "baseline": "A2", "candidate": {"family": "pool_rule", "change": "x", "hypothesis": "h",
                                                     "primary_outcome": "O1", "decision": "accuracy_change"},
         "adoption": {"verdict": "reject", "reasons": ["primary rule not met"]},
         "primary": {"outcome": "O1", "unit": "cell", "n": 10, "n_clusters": 3,
                     "delta": _delta(0.01, -0.02, 0.04)},
         "outcomes": {"O1": {"delta_value": 0.01, "blocks": False}}, "subgroups": {"nars9": {"groups": {"NAP": 0.01}, "why": "ok"}},
         "coverage": {}, "stamp": {"campaign": "c", "protocol": {"sha256": "abc"}, "code": {"fingerprint": "def"}}}
    text = round2.verdict_markdown(v)
    assert "**Verdict: reject**" in text and "| O1 |" in text and chr(0x2014) not in text


# --------------------------------------------------------------------------- #
# compare and finalist, end to end on synthetic evaluation outputs
# --------------------------------------------------------------------------- #
def _write_evaluation(arm: Path, *, agreement: float, eci_shift: float, flip: float) -> None:
    ev = arm / "evaluation"
    nars9 = {"58": "NAP", "50": "UMW", "13": "XER"}
    cells = [(m, l3, b) for m in ("chem_COND", "chem_PH") for l3 in nars9 for b in ("2r_l2", "3c_matched")]
    hier = _hierarchy({c: 1.0 - agreement for c in cells}, nars9, exceed=0.5, net=0.01)
    (ev / "hierarchy").mkdir(parents=True)
    hier.to_csv(ev / "hierarchy" / "hierarchy.csv", index=False)
    rng = np.random.default_rng(1)
    n = 40
    stations = pd.DataFrame({
        "station_key": [f"S{i}" for i in range(n)], "l3": ["58"] * n,
        "huc8": [f"010{i % 5}" for i in range(n)], "huc12": [f"h{i}" for i in range(n)], "nars9": ["NAP"] * n,
        "benthic_mmi_class": ["Good" if i % 2 == 0 else "Poor" for i in range(n)],
        "mmi_bent": [70.0 if i % 2 == 0 else 20.0 for i in range(n)],
        "oe_class": [None] * n, "oe_score": [np.nan] * n, "fish_mmi_class": [None] * n, "mmi_fish": [np.nan] * n})
    base = np.where(np.arange(n) % 2 == 0, 0.7, 0.4) + rng.normal(0, 0.05, size=n)
    stations["eci_over_scored"] = base + eci_shift
    stations["eci"] = stations["eci_over_scored"]
    stations["f__water-soil-quality"] = stations["eci_over_scored"] * 15
    (ev / "association").mkdir(parents=True)
    stations.to_csv(ev / "association" / "association_stations.csv", index=False)
    (ev / "stability").mkdir(parents=True)
    pd.DataFrame({"l3": ["58", "50", "13"], "metric": ["chem_COND"] * 3, "flip_median": [flip] * 3,
                  "acc04_max_shift_iqr": [0.1] * 3}).to_csv(ev / "stability" / "stability.csv", index=False)
    (ev / "coverage.json").write_text(json.dumps({"totals": {"functionsSupported": 40, "curves": 50, "withheld": 3,
                                                             "curvesByBasis": {"regional-reference": 45, "published-benchmark": 5}}}),
                                      encoding="utf-8")
    (ev / "usability.json").write_text(json.dumps({"perAssessment": {"fieldMetrics": 12.0, "distinctProcedures": 9.0},
                                                   "totals": {"runtimeSeconds": 300.0, "failures": 0}}), encoding="utf-8")


def test_compare_and_finalist_end_to_end(tmp_path, protocol_path, source_config, monkeypatch):
    monkeypatch.setattr("streamcurves.code_identity.fingerprint", lambda app_root=None: "f" * 64)
    import run_round2
    root = tmp_path / "round2"
    for arm_id in (round2.BASELINE_ARM, "B1", "C6"):
        round2.build_arm_root(protocol_path, arm_id, source_config=source_config, out_root=root)
    _write_evaluation(round2.arm_dir(root, round2.BASELINE_ARM), agreement=0.70, eci_shift=0.0, flip=0.05)
    _write_evaluation(round2.arm_dir(root, "B1"), agreement=0.80, eci_shift=0.0, flip=0.05)
    _write_evaluation(round2.arm_dir(root, "C6"), agreement=0.70, eci_shift=0.0, flip=0.05)
    assert run_round2.main(["compare", "--arm", "B1", "--out", str(root), "--protocol", str(protocol_path),
                            "--n-boot", "30"]) == 0
    v = json.loads((root / "verdicts" / "B1" / "verdict.json").read_text(encoding="utf-8"))
    assert v["adoption"]["verdict"] == "adopt" and v["decision"] == "accuracy_change"
    assert v["primary"]["delta"]["estimate"] == pytest.approx(0.10) and v["primary"]["delta"]["excludes_zero"] is True
    assert v["outcomes"]["O3"]["blocks"] is False and v["outcomes"]["O3"]["delta_value"] == 0.0
    assert v["outcomes"]["O5"]["blocks"] is False and v["outcomes"]["O2"]["blocks"] is False
    assert v["coverageGain"] == 0 and v["stamp"]["protocol"]["sha256"] == round2.sha256_of(protocol_path)
    assert v["subgroups"]["nars9"]["blocks"] is False and set(v["subgroups"]["nars9"]["groups"]) == {"NAP", "UMW", "XER"}
    md = (root / "verdicts" / "B1" / "verdict.md").read_text(encoding="utf-8")
    assert "**Verdict: adopt**" in md and chr(0x2014) not in md
    assert run_round2.main(["compare", "--arm", "C6", "--out", str(root), "--protocol", str(protocol_path),
                            "--n-boot", "30"]) == 0
    c6 = json.loads((root / "verdicts" / "C6" / "verdict.json").read_text(encoding="utf-8"))
    assert c6["adoption"]["verdict"] == "reference-only"
    with pytest.raises(SystemExit):
        run_round2.main(["finalist", "--arms", "C6", "--out", str(root), "--protocol", str(protocol_path),
                         "--source-config", str(source_config)])
    assert run_round2.main(["finalist", "--arms", "B1", "--out", str(root), "--protocol", str(protocol_path),
                            "--source-config", str(source_config)]) == 0
    fin = round2.read_arm(round2.arm_dir(root, round2.FINALIST_ARM))
    assert fin["members"] == ["B1"] and fin["knobs"] == {"reference_pool.ladder_rule": "narrowest_adequate"}
    sup = json.loads((round2.arm_dir(root, round2.FINALIST_ARM) / "supporting.json").read_text(encoding="utf-8"))
    assert sup["verdicts"] == {"B1": "adopt", "C6": "reference-only"}
    assert sup["benjamini_hochberg"]["q_values"]["B1"] == 0.0 and "B1" in sup["benjamini_hochberg"]["passing"]


def test_a_missing_outcome_makes_the_verdict_inconclusive():
    """An absent block is not a passed one: C3b's hierarchy harness crashed on 2026-09-26 and
    the compare step had adopted on the stability outcome alone."""
    passing = dict(estimate=0.0, lo=0.0, hi=0.0, n=229, n_clusters=14)
    got = round2.adoption("simplification", primary=passing, limits=[], missing=["O1", "O2"])
    assert got["verdict"] == round2.VERDICT_INCONCLUSIVE
    assert got["missing"] == ["O1", "O2"]
    assert "no data for O1, O2" in got["reasons"][0]
    # with the data present the same primary adopts, and a reference arm stays reference-only
    assert round2.adoption("simplification", primary=passing, limits=[])["verdict"] == round2.VERDICT_ADOPT
    assert round2.adoption("reference_arm", primary=passing, limits=[], missing=["O1"])["verdict"] == round2.VERDICT_REFERENCE
    # an accuracy change and a coverage-only candidate are inconclusive too
    assert round2.adoption("accuracy_change", primary=passing, limits=[], missing=["O3"])["verdict"] == round2.VERDICT_INCONCLUSIVE
    assert round2.adoption("coverage_only", primary=None, limits=[], constraint_resolved=True, coverage_gain=3,
                           missing=["O5"])["verdict"] == round2.VERDICT_INCONCLUSIVE
