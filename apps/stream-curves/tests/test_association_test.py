"""The association test (O3): DEEP-style scoring of evaluation stations, the AUC and
Spearman rows, the station lists hashed, and a paired comparison between two arms on
identical stations. Synthetic frame, values and bio indices; no build."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from streamcurves import round2

APP = Path(__file__).resolve().parents[1]
SCRIPTS = APP / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_association_test as at  # noqa: E402
import run_round2  # noqa: E402

PROTOCOL = """\
schema: evaluation-protocol/1
protocol_version: "1.0"
campaign: test-campaign
candidates:
  B1: {family: pool_rule, config: {reference_pool.ladder_rule: narrowest_adequate}, primary_outcome: O1, decision: accuracy_change}
"""


def _bundle(scale: float = 1.0):
    """A bundle scoring conductivity (lower is better) and pH (two layers by drainage
    area) for Water and soil quality, and impervious cover on fixed criteria."""
    return {
        "assessmentId": "test", "region": {"kind": "ecoregion", "code": "55", "name": "Test"},
        "metricsByFunction": [
            {"functionId": "water-soil-quality", "functionName": "Water & soil quality", "discipline": "Physicochemistry",
             "metrics": [
                 {"metricId": "spring-chem-cond", "metricName": "Conductivity", "criteriaBasis": "reference",
                  "basis": "regional-reference", "howToMeasure": "probe",
                  "curve": {"points": [{"x": 50.0 * scale, "y": 1.0}, {"x": 200.0 * scale, "y": 0.69},
                                       {"x": 400.0 * scale, "y": 0.39}, {"x": 800.0 * scale, "y": 0.0}]}},
                 {"metricId": "spring-chem-ph", "metricName": "pH", "criteriaBasis": "reference",
                  "basis": "regional-reference", "howToMeasure": "probe", "curve": {"points": []},
                  "curveLayers": [{"stratum": "", "points": [{"x": 6.0, "y": 0.0}, {"x": 7.0, "y": 1.0}, {"x": 9.0, "y": 0.0}]},
                                  {"stratum": "lt_10", "points": [{"x": 5.0, "y": 0.0}, {"x": 6.5, "y": 1.0}, {"x": 8.0, "y": 0.0}]}],
                  "stratifier": {"variable": "drainage_area_sqkm", "breaks": [10.0], "right": True,
                                 "classes": [{"key": "lt_10", "label": "under 10 km2"}, {"key": "ge_10", "label": "10 km2 and up"}]}}]},
            {"functionId": "catchment-hydrology", "functionName": "Catchment hydrology", "discipline": "Hydrology",
             "metrics": [{"metricId": "spring-pctimp2019ws", "metricName": "Impervious", "criteriaBasis": "fixed",
                          "basis": "published-benchmark",
                          "curve": {"points": [{"x": 0.0, "y": 1.0}, {"x": 10.0, "y": 0.69}, {"x": 25.005, "y": 0.39},
                                               {"x": 44.5, "y": 0.0}]}}]},
        ],
        "insufficientReferenceSupport": [],
    }


def _inputs():
    keys = [f"S{i}" for i in range(1, 9)]
    frame = pd.DataFrame({
        "station_key": keys, "l3": ["55"] * 8,
        "pass_strict": [False] * 7 + [True],
        "huc8": ["0101", "0101", "0102", "0102", "0103", "0103", None, "0101"],
        "huc12": [f"h{i}" for i in range(8)], "nars9": ["TPL"] * 8,
        "drainage_area_sqkm": [4.0, 40.0, 4.0, 40.0, 4.0, 40.0, 4.0, 4.0], "nhd_slope": [0.01] * 8,
        "pctimp2019ws": [1.0, 2.0, 30.0, 35.0, 5.0, 6.0, 50.0, 0.5]})
    values = pd.DataFrame({"site_id": keys,
                           "chem_COND": [60.0, 80.0, 700.0, 900.0, 250.0, 300.0, np.nan, 40.0],
                           "chem_PH": [7.0, 7.1, 6.1, 6.0, 7.5, 8.5, 7.0, 6.9]}).set_index("site_id", drop=False)
    values.index = values.index.astype(str)
    return {"frame": frame, "values": values, "value_policy": "newest-nonnull-v2"}


def _bio():
    return pd.DataFrame({"station_key": [f"S{i}" for i in range(1, 8)],
                         "benthic_mmi_class": ["Good", "Good", "Poor", "Poor", "Fair", "Fair", "Poor"],
                         "mmi_bent": [80.0, 75.0, 20.0, 15.0, 50.0, 45.0, 10.0],
                         "oe_class": ["Good", "Good", "Poor", "Poor", None, None, None],
                         "oe_score": [1.0, 0.95, 0.4, 0.3, np.nan, np.nan, np.nan],
                         "fish_mmi_class": ["Good", None, "Poor", None, "Good", "Poor", None],
                         "mmi_fish": [70.0, np.nan, 20.0, np.nan, 65.0, 25.0, np.nan]})


def _campaign(root: Path, bundle: dict, code: str = "55") -> Path:
    run = root / f"l3-{code}"
    v = run / "library" / "assessments" / "test-region" / "v1"
    v.mkdir(parents=True)
    (v.parent / "manifest.json").write_text(json.dumps({"latestVersion": 1}), encoding="utf-8")
    (v / "assessment.deep.json").write_text(json.dumps(bundle), encoding="utf-8")
    (root / "batch_summary.json").write_text(json.dumps({"regions": [{"l3": code, "exit": 0, "seconds": 12.5}]}),
                                             encoding="utf-8")
    return root


@pytest.fixture
def protocol(tmp_path) -> Path:
    p = tmp_path / "protocol.yaml"
    p.write_text(PROTOCOL, encoding="utf-8")
    return p


def test_evaluation_stations_withhold_the_strict_reference():
    inp = _inputs()
    st = at.evaluation_stations(inp["frame"], "55")
    assert list(st["station_key"]) == [f"S{i}" for i in range(1, 8)]
    assert at.evaluation_stations(inp["frame"], "71").empty


def test_score_stations_scores_like_deep():
    inp = _inputs()
    st = at.evaluation_stations(inp["frame"], "55")
    scored = at.score_stations(_bundle(), st, inp["values"])
    assert list(scored.columns[:5]) == ["station_key", "l3", "huc8", "huc12", "nars9"]
    s1 = scored.iloc[0]
    # S1: conductivity 60 -> index (1 - (60-50)/150*0.31); pH 7.0 on the lt_10 layer -> 2/3; mean x 15
    cond = 1.0 - (60.0 - 50.0) / 150.0 * 0.31
    ph = 1.0 - (7.0 - 6.5) / 1.5
    assert s1["f__water-soil-quality"] == pytest.approx((cond + ph) / 2 * 15)
    assert s1["f__catchment-hydrology"] == pytest.approx((1.0 - 1.0 / 10.0 * 0.31) * 15)
    assert pd.isna(s1["eci"])                                        # 18 functions unassessed
    assert s1["eci_over_scored"] > 0 and s1["n_functions_scored"] == 2
    s7 = scored.iloc[6]                                              # conductivity missing, pH scores
    assert s7["f__water-soil-quality"] == pytest.approx((1.0 - (7.0 - 6.5) / 1.5) * 15)
    assert scored["huc8"].isna().sum() == 1


def test_associate_uses_good_against_poor_and_hashes_folds():
    inp = _inputs()
    scored = at.score_stations(_bundle(), at.evaluation_stations(inp["frame"], "55"), inp["values"])
    scored = scored.merge(_bio(), on="station_key", how="left")
    rows = pd.DataFrame(at.associate(scored, scope="55", n_boot=30, seed=11))
    wsq = rows[(rows["subject"] == "f__water-soil-quality") & (rows["target"] == "benthic_mmi")].iloc[0]
    assert wsq["n_pos"] == 2 and wsq["n_neg"] == 3 and wsq["n_class"] == 5, "Fair stations are excluded"
    assert wsq["auc"] == 1.0 and wsq["auc_lo"] == 1.0
    assert wsq["n_rho"] == 7
    assert wsq["rho"] == pytest.approx(round2.spearman(scored["f__water-soil-quality"], scored["mmi_bent"]))
    assert 0.5 < wsq["rho"] < 1.0, "S7 scores on pH alone and breaks the monotone order"
    fish = rows[(rows["subject"] == "eci") & (rows["target"] == "fish_mmi")].iloc[0]
    assert fish["n_pos"] == 2 and fish["n_neg"] == 2
    assert set(rows["subject"]) == {"eci", "f__water-soil-quality", "f__catchment-hydrology"}
    assert all(f is not None for f in scored.apply(at.fold_of, axis=1))


def test_run_writes_the_three_outputs_with_the_stamp(tmp_path, protocol, monkeypatch):
    monkeypatch.setattr("streamcurves.code_identity.fingerprint", lambda app_root=None: "f" * 64)
    campaign = _campaign(tmp_path / "campaign", _bundle())
    bio = tmp_path / "bio.csv"
    _bio().to_csv(bio, index=False)
    got = at.run(campaign=campaign, bio_indices=bio, out_dir=tmp_path / "out", protocol=protocol,
                 n_boot=20, seed=11, inputs=_inputs(), config_root=tmp_path / "cfg")
    for name in (at.TABLE_FILE, at.STATIONS_FILE, at.SUMMARY_FILE):
        assert (tmp_path / "out" / name).is_file()
    summary = json.loads((tmp_path / "out" / at.SUMMARY_FILE).read_text(encoding="utf-8"))
    assert summary["stamp"]["protocol"]["sha256"] == round2.sha256_of(protocol)
    assert summary["stamp"]["campaign"] == "test-campaign" and summary["stamp"]["code"]["fingerprint"] == "f" * 64
    assert summary["stamp"]["configRoot"].endswith("cfg")
    assert summary["regions"]["55"]["n_stations"] == 7
    assert summary["regions"]["55"]["stations_hash"] == round2.station_list_hash([f"S{i}" for i in range(1, 8)])
    assert summary["pooled"]["stations_hash"] == summary["regions"]["55"]["stations_hash"]
    assert summary["pooled"]["n_folds"] == 4
    table = got["table"]
    assert set(table["scope"]) == {"55", "pooled"}
    pooled = table[(table["scope"] == "pooled") & (table["subject"] == "eci") & (table["target"] == "benthic_mmi")].iloc[0]
    assert pooled["auc_lo"] is not None and pooled["auc_lo"] <= pooled["auc"] <= pooled["auc_hi"]
    again = at.run(campaign=campaign, bio_indices=bio, out_dir=tmp_path / "out2", protocol=protocol,
                   n_boot=20, seed=11, inputs=_inputs(), config_root=tmp_path / "cfg")
    assert again["table"].equals(got["table"]), "deterministic"


def test_paired_comparison_between_two_arms_uses_identical_stations(tmp_path, protocol, monkeypatch):
    monkeypatch.setattr("streamcurves.code_identity.fingerprint", lambda app_root=None: "f" * 64)
    bio = tmp_path / "bio.csv"
    _bio().to_csv(bio, index=False)
    outs = {}
    for arm, scale in (("A2", 1.0), ("B1", 3.0)):
        campaign = _campaign(tmp_path / arm / "campaign", _bundle(scale))
        got = at.run(campaign=campaign, bio_indices=bio, out_dir=tmp_path / arm / "association", protocol=protocol,
                     n_boot=20, seed=11, inputs=_inputs())
        outs[arm] = got["stations"]
    same = run_round2.o3_comparison(outs["A2"], outs["A2"], n_boot=20, seed=11)
    assert same["n_stations"] == 7 and same["stations_hash"] == round2.station_list_hash([f"S{i}" for i in range(1, 8)])
    eci = next(r for r in same["rows"] if r["subject"] == "eci" and r["target"] == "benthic_mmi")
    assert eci["delta"] == 0.0 and eci["blocks"] is False
    diff = run_round2.o3_comparison(outs["B1"], outs["A2"], n_boot=20, seed=11)
    row = next(r for r in diff["rows"] if r["subject"] == "eci" and r["target"] == "benthic_mmi")
    assert row["n"] == 5 and row["lo"] is not None
    assert {r["target"] for r in diff["rows"]} == set(at.TARGETS)


def test_coverage_and_usability_read_the_staged_campaign(tmp_path):
    campaign = _campaign(tmp_path / "campaign", _bundle())
    cov, use = run_round2.coverage_and_usability(campaign)
    assert cov["regions"]["55"]["functionsSupported"] == 2
    assert cov["totals"]["curvesByBasis"] == {"published-benchmark": 1, "regional-reference": 2}
    assert use["regions"]["55"]["fieldMetrics"] == 2 and use["regions"]["55"]["distinctProcedures"] == 1
    assert use["totals"]["runtimeSeconds"] == 12.5 and use["totals"]["failures"] == 0
    assert use["perAssessment"]["fieldMetrics"] == 2.0
