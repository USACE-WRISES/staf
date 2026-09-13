"""The staging statistics: per state and for everything published, the
index quantiles and band counts under the app's own edges, the function
rating shares and score histograms, coverage from the national index, and
the asset's place in the manifest."""
from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder import state
from builder.paths import DataRoot
from builder.stages import coverage, stats
from test_states_national import polygons_file, write_comid_states

HUC8_A, HUC8_B = "02080204", "02080205"


def _scores(root, huc8, comids, eci, fs, tiers, provisional=None, n_rated=None, tier=None):
    n = len(comids)
    table = pa.table({
        "comid": pa.array(comids, pa.int64()),
        "huc4": pa.array([huc8[:4]] * n, pa.string()), "huc8": pa.array([huc8] * n, pa.string()),
        "eci_raw": pa.array(eci, pa.float64()), "phys_raw": pa.array(eci, pa.float64()),
        "chem_raw": pa.array(eci, pa.float64()), "bio_raw": pa.array([None] * n, pa.float64()),
        "provisional": pa.array(provisional or [False] * n, pa.bool_()),
        "n_rated": pa.array(n_rated or [20] * n, pa.int64()),
        "tier": pa.array(tier or [2] * n, pa.int64()),
        "fs_catchment_hydrology": pa.array(fs, pa.int64()),
        "tier_catchment_hydrology": pa.array(tiers, pa.string()),
        "fs_channel_evolution": pa.array([13] * n, pa.int64()),
    })
    path = root.huc8_file(huc8, "scores")
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    pq.write_table(pa.table({"comid": pa.array(comids, pa.int64()), "huc4": pa.array([huc8[:4]] * n)}),
                   root.huc8_file(huc8, "evidence"))


def _root(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    write_comid_states(root, [(1, "AA"), (2, "AA"), (3, "AA"), (4, "BB"), (5, "BB"), (6, "AA"), (7, "BB")])
    pq.write_table(pa.table({"comid": pa.array([1, 2, 3, 4, 5, 6, 7, 8], pa.int64()),
                             "huc4": pa.array(["0208"] * 8, pa.string())}), root.index)
    _scores(root, HUC8_A, [1, 2, 3, 4], eci=[0.39, 0.3901, 0.69, 0.70], fs=[13, 8, None, 3],
            tiers=["observed", "screening-proxy", "unavailable", "observed"],
            provisional=[False, False, False, True], n_rated=[20, 20, 19, 20])
    _scores(root, HUC8_B, [5, 9], eci=[0.85, 0.2], fs=[13, 13], tiers=["connected-nearby", "observed"],
            tier=[2, 1])
    return root


def test_groups_count_bands_quantiles_shares_and_coverage(tmp_path):
    root = _root(tmp_path)
    polygons = polygons_file(tmp_path / "states.geojson.gz")
    out = stats.build_stats(root, [HUC8_B, HUC8_A], vintage="2026.09", method_version="abc",
                            polygons_path=polygons)
    assert out["schema_version"] == 1 and out["vintage"] == "2026.09" and out["huc8s"] == 2
    assert out["reaches"] == 6 and out["reaches_total"] == 8 and out["unassigned"] == 1   # comid 9 has no state
    assert [f["id"] for f in out["measures"]["indices"]] == ["eci", "physical", "chemical", "biological"]
    assert len(out["measures"]["functions"]) == 20 and out["measures"]["index_edges"] == [0.39, 0.69]
    assert out["measures"]["score_edges"] == [5, 10]
    assert set(out["groups"]) == {"US", "AA", "BB"}
    us = out["groups"]["US"]
    assert us["n"] == 6 and us["provisional"] == 1 and us["complete"] == 5 and us["tier2"] == 5
    eci = us["indices"]["eci"]
    assert eci["n"] == 6 and eci["min"] == 0.2 and eci["max"] == 0.85
    assert eci["bands"] == [2, 2, 2]                 # 0.39 is Non-Functioning, 0.69 is At-Risk, 0.70 Functioning
    assert sum(eci["hist"]) == 6 and len(eci["hist"]) == 20
    assert us["indices"]["biological"] is None      # never computed in the fixture
    ch = us["functions"]["catchment-hydrology"]
    assert ch["rated"] == 5 and ch["unrated"] == 1 and ch["bands"] == [1, 1, 3] and ch["mean"] == 10.0
    assert ch["hist"][13] == 3 and ch["hist"][8] == 1 and ch["hist"][3] == 1 and len(ch["hist"]) == 16
    assert ch["tiers"] == {"connected-nearby": 1, "observed": 3, "screening-proxy": 1}   # rated rows only
    assert us["functions"]["channel-evolution"]["bands"] == [0, 0, 6]
    absent = us["functions"]["nutrient-cycling"]     # not in the fixture's columns
    assert absent == {"rated": 0, "unrated": 6, "bands": [0, 0, 0], "mean": None, "hist": [0] * 16, "tiers": {}}
    aa = out["groups"]["AA"]
    assert aa["n"] == 3 and aa["complete"] == 2 and aa["indices"]["eci"]["p50"] == 0.3901
    assert aa["functions"]["catchment-hydrology"]["bands"] == [0, 1, 1] and aa["functions"]["catchment-hydrology"]["mean"] == 10.5
    bb = out["groups"]["BB"]
    assert bb["n"] == 2 and bb["provisional"] == 1 and bb["indices"]["eci"]["bands"] == [0, 0, 2]
    assert out["states"] == {"AA": {"name": "Alpha", "n_total": 4, "n_scored": 3, "coverage": 0.75},
                             "BB": {"name": "Beta", "n_total": 3, "n_scored": 2, "coverage": 0.6667}}
    assert json.loads(json.dumps(out)) == out


def test_staging_writes_the_asset_and_skips_it_without_the_state_table(tmp_path):
    root = _root(tmp_path)
    (root.national / "huc8_index.json").write_text(json.dumps(
        {HUC8_A: {"huc4": "0208", "n": 4, "vpu": "02"}, HUC8_B: {"huc4": "0208", "n": 3, "vpu": "02"}}),
        encoding="utf-8")
    root.huc4_vpu.write_text(json.dumps({"0208": "02"}), encoding="utf-8")
    progress = state.Progress(root, quiet=True)
    manifest = coverage.run_staging(root, state.UnitStates(root), progress)
    entry = manifest["assets"]["stats.json"]
    assert entry["asset"] == "stats.json" and entry["bytes"] > 100 and len(entry["sha256"]) == 64
    data = json.loads((root.staging / "stats.json").read_text(encoding="utf-8"))
    assert data["reaches"] == 6 and data["method_version"] == manifest["method_version"]
    assert data["vintage"] == manifest["vintage"] and set(data["groups"]) == {"US", "AA", "BB"}
    # without the national states step the publish set is unchanged from before
    stats.states_stage.comid_state_path(root).unlink()
    manifest = coverage.run_staging(root, state.UnitStates(root), progress)
    assert "stats.json" not in manifest["assets"] and not (root.staging / "stats.json").exists()
    with pytest.raises(stats.NoStateTable):
        stats.build_stats(root, [HUC8_A])
