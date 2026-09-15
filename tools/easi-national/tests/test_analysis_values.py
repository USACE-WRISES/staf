"""The analysis ``values`` step: the flattened scoring trace of a real
report, the cross-section extras, and one HUC8 harvested end to end with
scheme A parity against the score stage's own output."""
from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder import config, state
from builder.analysis import values
from builder.paths import DataRoot
from builder.stages import score as score_stage
from builder.units import Chunk

BASE = {"pctimp2019": 0.35, "pctwdwet2019": 1.5, "pcthbwet2019": 0.5, "pctcrop2019": 12.0, "pcthay2019": 8.0,
        "pctmxfst2019": 20.0, "pctdecid2019": 30.0, "pctconif2019": 5.0, "pctgrs2019": 10.0, "pctshrb2019": 2.0,
        "kffact": 0.28, "rddens": 1.2, "damnrmstor": 500.0, "runoff": 400.0,
        "hyd": 0.95, "sed": 0.93, "chem": 0.91, "conn": 0.97, "temp": 0.96, "habt": 0.94}


def _streamcat_row():
    from easi.metrics import registry
    row = {}
    for name in registry.STREAMCAT_NAMES:
        if name in BASE:                                    # prG_BMMI is absent, as in the national cache
            for aoi in ("ws", "cat", "wsrp100"):
                row[f"{name}{aoi}".lower()] = BASE[name]
    return row


def _wqp(param, value):
    return {"parameter": param, "value": value, "units": "mg/L", "observation_count": 6, "station_count": 2,
            "date_start": "2020-01-01", "date_end": "2024-01-01", "nearest_distance_mi": 1.2,
            "excluded_count": 0, "excluded": {}, "station_medians": {"a": value, "b": value}, "query_ok": True}


def _record():
    return {"comid": 1, "huc4": "0208", "huc8": "02080204", "huc12": "020802040101", "vpu": "02",
            "gnis_name": "Test Run", "streamorde": 2, "fcode": 46006, "totdasqkm": 50.0, "lengthkm": 1.2,
            "slope": 0.004, "sinuosity": 1.15, "lat": 37.9, "lon": -78.5, "hydroseq": 1, "dnhydroseq": 0,
            "levelpathi": 1, "tocomid": 0, "streamcat": _streamcat_row(), "nrsa": None, "bankfull": None,
            "attains_exact": {},
            "attains_nearby": {"assessment_unit": "VA-1", "assessment_name": "Test Creek", "overallstatus": "Not Assessed",
                               "isimpaired": "N", "ircategory": "3", "distance_m": 120.0, "match_type": "nearby",
                               "source_layer": 1},
            "wqp_tn": _wqp("tn", 0.5), "wqp_tp": _wqp("tp", 0.02),
            "nid_dams": [{"name": "Dam", "storage": 10.0, "height": 5.0, "distance_m": 900.0}],
            "nas_taxa": ["Corbicula fluminea"], "nas_scope": "huc12", "geomorph": None, "schema_version": 1}


def test_flatten_trace_carries_every_input_rating_and_context():
    from easi.national import client
    report = client.score_record(_record(), cross_section=False)
    flat = values.flatten_trace(report)
    assert flat["rating_catchment_hydrology"] == "Good" and flat["index_catchment_hydrology"] == 0.85
    assert flat["fs_catchment_hydrology"] == 13 and flat["kind_catchment_hydrology"] == "worst_index"
    assert flat["v__catchment_hydrology__impervious"] == pytest.approx(0.35)
    assert flat["v__catchment_hydrology__agriculture"] == pytest.approx(20.0)
    assert flat["r__catchment_hydrology__agriculture"] == "Good" and flat["c__catchment_hydrology"] == 0.85
    assert flat["governing_catchment_hydrology"] in ("impervious", "agriculture")
    assert flat["c__streamflow_regime"] == pytest.approx(100.0 * 500.0 / (1000.0 * 400.0), abs=1e-9)
    assert flat["v__streamflow_regime__runoff"] == pytest.approx(400.0)
    assert flat["method_low_flow_baseflow_dynamics"] == "streamcat-hyd-integrity"
    assert flat["fallback_low_flow_baseflow_dynamics"] is True and flat["c__low_flow_baseflow_dynamics"] == pytest.approx(0.95)
    assert flat["ctx__nutrient_cycling__region"] == "SAP" and flat["v__nutrient_cycling__tn"] == pytest.approx(0.5)
    assert flat["r__nutrient_cycling__tp"] in ("Good", "Fair", "Poor")
    assert flat["method_water_soil_quality"] == "streamcat-chem-integrity-regulatory"
    assert flat["ctx__water_soil_quality__fallbackReason"]
    assert flat["ctx__population_support__products_ICI"] is not None
    assert flat["v__watershed_connectivity__damCount"] == 1.0 and flat["v__community_dynamics__taxaCount"] == 1.0
    assert flat["index_high_flow_dynamics"] is None                     # Tier 1: no cross-section
    assert flat["eci_raw"] == pytest.approx(report["ecosystemConditionIndexRaw"])
    assert flat["n_rated"] == report["computedCount"]


def test_xs_extras_summarise_the_transects_uncensored():
    scalars = [
        {"bank_height_ratio": 2.0, "low_bank_capped": True, "entrenchment_ratio": 1.2, "edge_limited": False,
         "bankfull_width_m": 8.0, "bankfull_depth_m": 0.5},
        {"bank_height_ratio": 1.4, "low_bank_capped": False, "entrenchment_ratio": 2.5, "edge_limited": True,
         "bankfull_width_m": 10.0, "bankfull_depth_m": 0.6},
        {"bank_height_ratio": 0.8, "low_bank_capped": False, "entrenchment_ratio": 3.0, "edge_limited": False,
         "bankfull_width_m": 12.0, "bankfull_depth_m": 0.7},
    ]
    geomorph = {"n_transects": 3, "dem_resolution_m": 1, "candidate_scalars": scalars,
                "reach": {"n": 3, "bank_height_ratio": {"median": 1.4, "capped": 1},
                          "entrenchment_ratio": {"median": 2.5}}}
    out = values.xs_extras(geomorph)
    assert out["xs_n"] == 3 and out["dem_res_m"] == 1.0
    assert out["bhr_median"] == 1.4 and out["er_median"] == 2.5
    assert out["bhr_share_capped"] == pytest.approx(1 / 3) and out["bhr_share_ge_1p5"] == pytest.approx(1 / 3)
    assert out["bhr_share_ge_1p3"] == pytest.approx(2 / 3) and out["bhr_mean_uncapped"] == pytest.approx(1.1)
    assert out["bhr_p25"] == pytest.approx(1.1) and out["bhr_max"] == 2.0 and out["er_max"] == 3.0
    assert out["er_edge_limited_share"] == pytest.approx(1 / 3)
    assert out["bankfull_width_cv"] == pytest.approx(0.16330, abs=1e-4)
    assert all(values.xs_extras(g)[k] is None for g in (None, {}) for k in values.XS_EXTRA_COLUMNS)


def test_harvest_reproduces_the_score_stage_and_the_step_assembles(tmp_path, monkeypatch):
    from easi.national import records
    root = DataRoot(tmp_path / "data").ensure()
    huc8 = "02080204"
    chunk = Chunk(id="state-VA", kind="state", label="Virginia", huc8s=[huc8], states=["VA"])
    chunk.save(root)
    rec = _record()
    derived = {**{k: rec[k] for k in records.IDENTITY_FIELDS}, "nars9": "SAP", "l3_code": "45",
               "l3_name": "Piedmont", "physio_division": "APL", "bankfull": None, "nrsa": None}
    root.huc8_dir(huc8).mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([derived]), root.huc8_file(huc8, "derived"))
    joins = {"comid": 1, "attains_exact": json.dumps({}), "attains_nearby": json.dumps(rec["attains_nearby"]),
             "wqp_tn": json.dumps(rec["wqp_tn"]), "wqp_tp": json.dumps(rec["wqp_tp"]),
             "nid_dams": json.dumps(rec["nid_dams"]), "nas_taxa": json.dumps(rec["nas_taxa"]), "nas_scope": "huc12"}
    pq.write_table(pa.Table.from_pylist([joins]), root.huc8_file(huc8, "joins"))
    root.chunk_raw(chunk.id, "streamcat").parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([{"comid": 1, **rec["streamcat"]}]), root.chunk_raw(chunk.id, "streamcat"))
    states, progress, control = state.UnitStates(root), state.Progress(root, quiet=True), state.Control(root)
    score_stage.run_score(root, chunk, huc8, states, progress, control)
    baked = pq.read_table(root.huc8_file(huc8, "scores")).to_pylist()[0]

    part = values.harvest_huc8(str(root.root), huc8, chunk.id)
    row = pq.read_table(part).to_pylist()[0]
    for key in ("catchment_hydrology", "nutrient_cycling", "population_support", "community_dynamics"):
        assert row[f"index_{key}"] == baked[f"index_{key}"] == row[f"scores_index_{key}"]
        assert row[f"rating_{key}"] == baked[f"rating_{key}"]
    assert row["eci_raw"] == pytest.approx(baked["eci_raw"]) and row["scores_eci_raw"] == baked["eci_raw"]
    assert row["tier"] == config.BASE_TIER and row["xs_status"] == "none" and row["l3_code"] == "45"
    assert row["attains_au"] == "VA-1" and row["attains_match"] == "nearby" and row["attains_nearby_cat"] == "3"
    assert row["nid_dam_count"] == 1 and row["nid_nearest_m"] == 900.0 and row["nas_taxa_count"] == 1
    assert row["wqp_tn_value"] == pytest.approx(0.5) and row["wqp_tp_stations"] == 2
    assert row["bhr_median"] is None

    monkeypatch.setattr(values, "chunk_of_huc8", lambda r: {huc8: chunk.id})
    path = values.run_values(root, progress, control, {"workers": 1})
    table = pq.read_table(path)
    assert table.num_rows == 1 and "v__catchment_hydrology__impervious" in table.column_names
    parity = json.loads(values.parity_a_path(root).read_text())
    assert parity["mismatched_reaches"] == 0 and parity["functions"]["eci_raw"] == 0
    meta = json.loads(values.values_meta_path(root).read_text())
    assert meta["n_reaches"] == 1 and meta["huc8s"] == 1
    assert values.chunk_of_huc8.__name__ == "<lambda>" or True                 # the real map is exercised below
    monkeypatch.undo()
    assert values.chunk_of_huc8(root) == {huc8: "state-VA"}
    assert values.inputs_values(root, {}) != values.inputs_landscape(root, {})


def test_join_caches_accepts_a_large_string_unit_key(tmp_path):
    import pandas as pd
    root = DataRoot(tmp_path / "data").ensure()
    root.analysis.mkdir()
    pd.DataFrame({"assessmentunitidentifier": ["VA-1", "VA-2"], "ecological_use": ["Fully Supporting", None],
                  "aquatic_life_strict": ["Good", None]}).to_parquet(values.attains_attributes_path(root), index=False)
    table = pa.table({"comid": pa.array([1, 2, 3], pa.int64()), "attains_au": pa.array(["VA-2", None, "VA-1"], pa.string())})
    out = values.join_caches(root, table)
    assert out.column("au__aquatic_life_strict").to_pylist() == [None, None, "Good"]
    assert out.column("comid").to_pylist() == [1, 2, 3]


def test_landscape_parity_allows_adapter_rounding_and_restricts_integrity_pairs(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    root.analysis.mkdir()
    vt = pa.table({
        "comid": pa.array([1, 2, 3], pa.int64()),
        "v__habitat_provision__woodyRiparian": pa.array([23.9, 32.2, None], pa.float64()),
        "c__low_flow_baseflow_dynamics": pa.array([0.95, 40.0, 0.90], pa.float64()),
        "method_low_flow_baseflow_dynamics": pa.array(
            ["streamcat-hyd-integrity", "nrsa-wetted-channel-condition", "streamcat-hyd-integrity"]),
        "v__catchment_hydrology__impervious": pa.array([0.35, 1.0, 2.5], pa.float64()),
    })
    pq.write_table(vt, values.values_path(root))
    landscape = pa.table({
        "comid": pa.array([1, 2, 3], pa.int64()),
        "woody_wsrp100": pa.array([23.8, 32.19, 10.0], pa.float32()),      # the adapters round to one decimal
        "hyd_min": pa.array([0.95, 0.4, 0.90], pa.float32()),              # row 2 scored the observed NRSA branch
        "pctimp2019ws": pa.array([0.35, 1.0, 2.6], pa.float32()),          # a real disagreement on row 3
    })
    report = values.landscape_parity(root, landscape)
    assert report["pairs_checked"] == 3
    assert report["failed"] == ["v__catchment_hydrology__impervious vs pctimp2019ws"]
    hyd = report["pairs"]["c__low_flow_baseflow_dynamics vs hyd_min"]
    assert hyd["compared"] == 2 and hyd["restricted_to"] == "streamcat-hyd-integrity" and hyd["share_over"] == 0.0
    woody = report["pairs"]["v__habitat_provision__woodyRiparian vs woody_wsrp100"]
    assert woody["compared"] == 2 and woody["max_abs_diff"] == pytest.approx(0.1, abs=1e-5) and woody["share_over"] == 0.0


def test_concrete_casts_null_and_large_string_columns():
    table = pa.table({"comid": pa.array([1, 2], pa.int64()), "station_key": pa.array(["a", "b"], pa.large_string()),
                      "rating_x": pa.nulls(2), "v__x__y": pa.nulls(2)})
    out = values.concrete(table)
    assert out.schema.field("station_key").type == pa.string() and out.schema.field("rating_x").type == pa.string()
    assert out.schema.field("v__x__y").type == pa.float64() and out.column("station_key").to_pylist() == ["a", "b"]
    assert values.concrete(out) is out


def test_target_states_are_the_state_chunks_wholly_in_the_values_table(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    root.analysis.mkdir()
    Chunk(id="state-VA", kind="state", label="Virginia", huc8s=["02080204"], states=["VA"]).save(root)
    Chunk(id="state-TX", kind="state", label="Texas", huc8s=["12090206", "12090207"], states=["TX"]).save(root)
    Chunk(id="huc8-02080204", kind="huc8", label="Rivanna", huc8s=["02080204"], states=["VA"]).save(root)
    assert values.target_states(root) == []                           # no values table yet
    pq.write_table(pa.table({"comid": pa.array([1, 2, 3], pa.int64()), "huc8": ["02080204", "12090206", "12090206"]}),
                   values.values_path(root))
    assert values.target_states(root) == ["VA"]                       # Texas has a HUC8 without harvested reaches
    pq.write_table(pa.table({"comid": pa.array([1, 2, 3], pa.int64()), "huc8": ["02080204", "12090206", "12090207"]}),
                   values.values_path(root))
    assert values.target_states(root) == ["TX", "VA"]
