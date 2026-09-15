"""The NRSA validation frame: recoded targets, the desktop screens and the
small statistics, and the desktop rows for an in-extent station (stored
evidence) and an outside station (synthetic record from the caches)."""
from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder import state
from builder.analysis import nrsa, screens, stats, values
from builder.paths import DataRoot
from builder.stages import score as score_stage
from builder.units import Chunk
from test_analysis_values import _record


def _csv(path, header, rows, bom=False):
    import csv as _csv
    import io
    buffer = io.StringIO(newline="")
    writer = _csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    data = buffer.getvalue().encode("latin-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)
    return path


def test_recodes_follow_the_published_classes():
    assert nrsa.recode_class("OE_COND", "O/E>=0.9") == "Good" and nrsa.recode_class("OE_COND", "O/E<0.9") == "Fair"
    assert nrsa.recode_class("OE_COND", "OE<0.5") == "Poor" and nrsa.recode_class("OE_COND", "O/E<0.8") == "Poor"
    assert nrsa.recode_class("RIPDIST_COND", "Low") == "Good" and nrsa.recode_class("RIPDIST_COND", "High") == "Poor"
    assert nrsa.recode_class("BENT_MMI_COND", "Not Assessed") is None and nrsa.recode_class("NTL_COND", "fair") == "Fair"
    assert nrsa.recode_class("BENT_MMI_COND", "") is None


def test_targets_pool_the_cycles_and_carry_the_reference_class(tmp_path):
    raw = tmp_path / "raw"
    _csv(raw / "1314/nrsa1314_allcond_05312019_0.csv",
         ["SITE_ID", "VISIT_NO", "WGT_EXT_SP", "STRAH_CAT", "BENT_MMI_COND", "MMI_BENT", "OE_COND", "RIPDIST_COND", "LRBS_USE"],
         [["S1", "1", "12.5", "SM", "Good", "61.2", "O/E>=0.9", "Low", "-0.3"],
          ["S2", "1", "3.0", "MD", "Poor", "20.1", "OE<0.5", "High", "-1.7"]])
    _csv(raw / "1819/nrsa1819_data_for_populationestimates.csv",
         ["UID", "SITE_ID", "VISIT_NO", "AG_ECO9", "WGT_TP", "BENT_MMI_COND", "RIPVEG_COND"],
         [["u", "S1", "1", "SAP", "9.9", "Fair", "Not Assessed"]], bom=True)
    _csv(raw / "2324/nrsa2324_benthicmmi.csv", ["SITE_ID", "VISIT_NO", "MMI_BENT", "BENT_MMI_COND"],
         [["S3", "1", "44.0", "Fair"], ["S3", "2", "40.0", "Poor"]])
    _csv(raw / "1314/nrsa1314_siteinformation_wide_04292019.csv", ["SITE_ID", "VISIT_NO", "RT_NRSA"],
         [["S1", "1", "R"], ["S2", "1", "Im"], ["S2", "2", "?"]])
    rows = {(r["cycle"], r["site_id"], r["visit_no"]): r for r in nrsa.load_targets(raw)}
    s1 = rows[("1314", "S1", "1")]
    assert s1["t__bent_mmi"] == "Good" and s1["t__oe"] == "Good" and s1["t__ripdist"] == "Good"
    assert s1["x__mmi_bent"] == 61.2 and s1["x__lrbs_use"] == -0.3 and s1["weight"] == 12.5 and s1["rt_nrsa"] == "R"
    s1b = rows[("1819", "S1", "1")]
    assert s1b["t__bent_mmi"] == "Fair" and s1b["t__ripveg"] is None and s1b["weight"] == 9.9 and s1b["rt_nrsa"] == "R"
    assert rows[("1314", "S2", "1")]["rt_nrsa"] == "Im" and rows[("2324", "S3", "2")]["t__bent_mmi"] == "Poor"
    assert rows[("2324", "S3", "1")]["rt_nrsa"] is None


def test_screens_and_composite_pressure():
    columns = {"pctimp2019ws": np.array([0.5, 2.0, 0.1, np.nan]), "agriculture_ws": np.array([5.0, 5.0, 40.0, 1.0]),
               "rddensws": np.array([0.5, 0.5, 0.5, 0.5]), "dor": np.array([0.0, 0.0, 0.0, 0.0]),
               "fcode_class": np.array(["perennial", "perennial", "canal", "perennial"], dtype=object),
               "wadeable": np.array([True, True, True, False])}
    mask, skipped = screens.evaluate(columns, {**screens.STRICT, **screens.FRAME_RULES})
    assert mask.tolist() == [True, False, False, False]
    assert sorted(skipped) == ["mines_ws", "sc__nabd_densws", "sc__npdesdensws"]
    with pytest.raises(KeyError):
        screens.evaluate(columns, screens.STRICT, missing="fail")
    relaxed, _ = screens.evaluate(columns, screens.RELAXED)
    assert relaxed.tolist() == [True, True, False, False]                  # 2 % impervious passes the relaxed cap
    pressure, used = screens.composite_pressure(columns)
    assert used == ["pctimp2019ws", "agriculture_ws", "rddensws", "dor"]
    assert pressure[1] > pressure[0] and np.isfinite(pressure).all()


def test_small_statistics():
    assert stats.auc([1, 2, 3, 4], [False, False, True, True]) == 1.0
    assert stats.auc([4, 3, 2, 1], [False, False, True, True]) == 0.0
    assert stats.auc([1, 1, 1, 1], [False, False, True, True]) == 0.5
    assert stats.auc([1, 2], [True, True]) is None
    assert stats.cliffs_delta([1, 2, 3, 4], [False, False, True, True]) == 1.0
    assert stats.spearman([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert stats.spearman([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)
    assert stats.weighted_kappa(["Good", "Fair", "Poor", "Good"], ["Good", "Fair", "Poor", "Good"]) == pytest.approx(1.0)
    assert stats.weighted_kappa(["Good", "Good", "Poor", "Poor"], ["Poor", "Poor", "Good", "Good"]) < 0
    assert stats.weighted_kappa(["Good", None], ["Good", "Poor"]) is None
    boots = stats.cluster_bootstrap(np.array([1, 1, 2, 2, 3, 3]), lambda rows: float(len(rows)), n_boot=5)
    assert boots.shape == (5,) and (boots > 0).all()
    lo, hi = stats.interval([1, 2, 3, 4, 5])
    assert lo < hi


def test_desktop_rows_from_evidence_and_from_the_caches(tmp_path, monkeypatch):
    from easi.national import records
    root = DataRoot(tmp_path / "data").ensure()
    root.analysis.mkdir()
    huc8 = "02080204"
    chunk = Chunk(id="state-VA", kind="state", label="Virginia", huc8s=[huc8], states=["VA"])
    chunk.save(root)
    rec = _record()
    derived = {**{k: rec[k] for k in records.IDENTITY_FIELDS}, "nars9": "SAP", "l3_code": "45",
               "l3_name": "Piedmont", "physio_division": "APL", "bankfull": None, "nrsa": None}
    root.huc8_dir(huc8).mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([derived]), root.huc8_file(huc8, "derived"))
    joins_row = {"comid": 1, "attains_exact": json.dumps({}), "attains_nearby": json.dumps(rec["attains_nearby"]),
                 "wqp_tn": json.dumps(rec["wqp_tn"]), "wqp_tp": json.dumps(rec["wqp_tp"]),
                 "nid_dams": json.dumps(rec["nid_dams"]), "nas_taxa": json.dumps(rec["nas_taxa"]), "nas_scope": "huc12"}
    pq.write_table(pa.Table.from_pylist([joins_row]), root.huc8_file(huc8, "joins"))
    root.chunk_raw(chunk.id, "streamcat").parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([{"comid": 1, **rec["streamcat"]}]), root.chunk_raw(chunk.id, "streamcat"))
    states, progress, control = state.UnitStates(root), state.Progress(root, quiet=True), state.Control(root)
    score_stage.run_score(root, chunk, huc8, states, progress, control)
    monkeypatch.setattr(values, "chunk_of_huc8", lambda r: {huc8: chunk.id})
    values.run_values(root, progress, control, {"workers": 1})

    # the outside station: a VAA row, a strata row and a national StreamCat row for comid 2
    pq.write_table(pa.table({"comid": pa.array([2], pa.int64()), "reachcode": ["05010001000002"], "gnis_name": ["Out"],
                             "streamorde": [3], "fcode": [46006], "totdasqkm": [80.0], "lengthkm": [2.0], "slope": [0.002],
                             "hydroseq": [5], "dnhydroseq": [4], "levelpathi": [5], "tocomid": [4],
                             "huc8": ["05010001"], "huc4": ["0501"], "vpuid": ["05"]}), root.vaa)
    pq.write_table(pa.table({"comid": pa.array([2], pa.int64()), "huc12": ["050100010101"], "lat": [37.5], "lon": [-79.5],
                             "sinuosity_flowline": [1.3], "l3": ["45"], "nars9": ["SAP"], "physio": ["APL"]}),
                   nrsa.strata_path(root))
    pq.write_table(pa.Table.from_pylist([{"comid": 2, **rec["streamcat"]}]), root.national / "streamcat.parquet")
    archive = tmp_path / "nrsa"
    archive.mkdir()
    monkeypatch.setattr(nrsa, "NRSA_DIR", archive)
    pq.write_table(pa.Table.from_pylist([
        {"station_key": "IN-1", "lat": 37.9, "lon": -78.5, "comid": 1, "us_l3code": "45", "ag_eco9": "SAP", "state": "VA",
         "protocol": "WADEABLE", "comid_source": "epa_published"},
        {"station_key": "OUT-2", "lat": 37.5, "lon": -79.5, "comid": 2, "us_l3code": "45", "ag_eco9": "SAP", "state": "VA",
         "protocol": "WADEABLE", "comid_source": "nldi_snapped"},
        {"station_key": "NONE-3", "lat": 40.0, "lon": -90.0, "comid": 0, "us_l3code": "54", "ag_eco9": "TPL", "state": "IL",
         "protocol": "WADEABLE", "comid_source": "none"}]), archive / "stations.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"cycle": "1314", "site_id": "S1", "visit_no": "1", "station_key": "IN-1"},
        {"cycle": "1819", "site_id": "S1", "visit_no": "1", "station_key": "IN-1"},
        {"cycle": "1314", "site_id": "S2", "visit_no": "1", "station_key": "OUT-2"}]), archive / "site_visits.parquet")
    archive_values = pa.Table.from_pylist([
        {"station_key": "IN-1", "cycle": "1314", "site_id": "S1", "visit_no": "1", "phab_XCMGW": 0.8, "land_BFIWS": 44.0},
        {"station_key": "OUT-2", "cycle": "1314", "site_id": "S2", "visit_no": "1", "phab_XCMGW": 0.2, "land_BFIWS": 30.0},
        {"station_key": "OUT-2", "cycle": "1314", "site_id": "S2", "visit_no": "2", "phab_XCMGW": 0.3, "land_BFIWS": 30.0}])
    for key in ("station_key", "cycle"):                 # the pandas-written archive carries large_string keys
        idx = archive_values.schema.get_field_index(key)
        archive_values = archive_values.set_column(idx, key, archive_values.column(key).cast(pa.large_string()))
    pq.write_table(archive_values, archive / "values.parquet")
    raw = tmp_path / "raw"
    _csv(raw / "1314/nrsa1314_allcond_05312019_0.csv", ["SITE_ID", "VISIT_NO", "WGT_EXT_SP", "BENT_MMI_COND", "MMI_BENT"],
         [["S1", "1", "1.0", "Good", "70"], ["S2", "1", "2.0", "Poor", "20"]])
    _csv(raw / "1314/nrsa1314_siteinformation_wide_04292019.csv", ["SITE_ID", "VISIT_NO", "RT_NRSA"],
         [["S1", "1", "R"], ["S2", "1", "Im"]])
    monkeypatch.setattr(nrsa, "NRSA_RAW", raw)

    pq.write_table(pa.table({"comid": pa.array([1, 2], pa.int64()), "minedensws": [0.0, 0.5], "coalminedensws": [0.0, 0.0],
                             "pcthydricws": [10.0, 0.0], "pctconif2019catrp100": [30.0, 5.0],
                             "pctdecid2019catrp100": [20.0, 5.0], "pctmxfst2019catrp100": [0.0, 0.0],
                             "pctshrb2019catrp100": [0.0, 0.0], "pctwdwet2019catrp100": [0.0, 0.0]}),
                   values.candidates_path(root))

    nrsa.run(root, progress, control, {})
    assert nrsa.desktop_fresh(root) and json.loads(nrsa.desktop_meta_path(root).read_text())["stations"] == 2
    desktop = {r["station_key"]: r for r in pq.read_table(nrsa.desktop_path(root)).to_pylist()}
    assert desktop["IN-1"]["in_set"] is True and desktop["IN-1"]["desktop_source"] == "evidence"
    assert desktop["IN-1"]["rating_catchment_hydrology"] == "Good" and desktop["IN-1"]["pctimp2019ws"] == pytest.approx(0.35)
    assert desktop["IN-1"]["agriculture_ws"] == pytest.approx(20.0)
    # the quantities derived from the candidate cache exist only when the caches are joined first
    assert desktop["IN-1"]["mines_ws"] == 0.0 and desktop["OUT-2"]["mines_ws"] == pytest.approx(0.5)
    assert desktop["IN-1"]["woody_catrp100"] == pytest.approx(50.0) and desktop["IN-1"]["wetland_retention"] == pytest.approx(0.2)
    assert desktop["OUT-2"]["wetland_retention"] is None
    out = desktop["OUT-2"]
    assert out["in_set"] is False and out["desktop_source"] == "synthetic" and out["huc8"] == "05010001"
    assert out["rating_catchment_hydrology"] == "Good" and out["v__catchment_hydrology__impervious"] == pytest.approx(0.35)
    assert out["sinuosity"] == 1.3 and out["lat"] == 37.5 and out["nid_dam_count"] is None      # no dam inventory on disk
    assert out["rating_high_flow_dynamics"] is None
    assert desktop["NONE-3"]["desktop_source"] == "missing" if "NONE-3" in desktop else True
    frame = pq.read_table(nrsa.frame_path(root)).to_pylist()
    by = {(r["station_key"], r["cycle"]): r for r in frame}
    assert by[("IN-1", "1314")]["t__bent_mmi"] == "Good" and by[("IN-1", "1314")]["rt_nrsa"] == "R"
    assert by[("IN-1", "1314")]["a__phab_XCMGW"] == 0.8 and by[("IN-1", "1314")]["land_BFIWS"] == 44.0
    assert by[("IN-1", "1314")]["rating_catchment_hydrology"] == "Good"
    assert by[("OUT-2", "1314")]["a__phab_XCMGW"] == 0.2 and by[("OUT-2", "1314")]["t__bent_mmi"] == "Poor"
    assert ("OUT-2", "1314") in by and all(r["visit_no"] == "1" for r in frame)
    check = (nrsa.screen_check_path(root)).read_text(encoding="utf-8")
    assert "strict,US" in check and "relaxed,SAP" in check
