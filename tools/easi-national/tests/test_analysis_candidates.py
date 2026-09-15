"""The analysis candidate data: the per-name area-of-interest StreamCat pull
into its own cache, the EROM flow ratios, and the aquatic-life-use rule."""
from __future__ import annotations

import pandas as pd
import pyarrow.parquet as pq
import pytest

from builder import config, state
from builder.analysis import candidates
from builder.paths import DataRoot
from builder.stages import local_gdb
from builder.stages import streamcat_national as scn

REGION_COMIDS = {"Region02": [200, 201], "Region03N": [300]}


def fake_post(payload):
    names = payload["name"].split(",")
    aois = payload["aoi"].split(",")
    items = []
    for comid in REGION_COMIDS[payload["region"]]:
        item = {"COMID": comid}
        for name in names:
            for aoi in aois:
                if aoi == "other":
                    item[name.upper()] = float(comid) / 1000.0          # no suffix under "other"
                else:
                    item[f"{name}{aoi}".upper()] = float(comid) + len(name)
        items.append(item)
    return items


def test_candidate_pull_groups_by_area_of_interest_into_its_own_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STREAMCAT_NAME_GROUP", 2)
    root = DataRoot(tmp_path / "data").ensure()
    root.analysis.mkdir()
    progress, control = state.Progress(root, quiet=True), state.Control(root)
    calls = []

    def post(payload):
        calls.append(payload)
        return fake_post(payload)

    aoi = {"bfi": "ws,cat", "elev": "ws,cat", "pcthydric": "ws,cat", "pctimp2019": "catrp100", "prg_bmmi0809": "other"}
    groups = scn._plan_groups(list(aoi), aoi)
    assert [(g, a, k) for g, a, k in groups] == [(["bfi", "elev"], "ws,cat", "ws+cat-g0"), (["pcthydric"], "ws,cat", "ws+cat-g1"),
                                                (["pctimp2019"], "catrp100", "catrp100-g0"), (["prg_bmmi0809"], "other", "other-g0")]
    assert scn._plan_groups(["a", "b", "c"], None) == [(["a", "b"], "ws,cat,wsrp100", "g0"), (["c"], "ws,cat,wsrp100", "g1")]
    path = scn.run_streamcat_national(root, progress, control, names=list(aoi), aoi_by_name=aoi, post=post,
                                      region_list=list(REGION_COMIDS), workers=2,
                                      cache=root.analysis / "cands.parquet", ledger_name="cands",
                                      parts=root.analysis / "cands_parts")
    assert path == root.analysis / "cands.parquet" and not (root.national / "streamcat.parquet").exists()
    assert len(calls) == 8 and sorted({c["aoi"] for c in calls}) == ["catrp100", "other", "ws,cat"]
    table = pq.read_table(path)
    assert table.column("comid").to_pylist() == [200, 201, 300]
    assert set(table.column_names) == {"comid", "bfiws", "bficat", "elevws", "elevcat", "pcthydricws", "pcthydriccat",
                                       "pctimp2019catrp100", "prg_bmmi0809"}
    assert table.column("prg_bmmi0809").to_pylist()[0] == pytest.approx(0.2)
    assert not (root.analysis / "cands_parts").exists() and len(state.Ledger(root, "cands")) == 0
    assert candidates.candidate_aoi()["prg_bmmi0809"] == config.STREAMCAT_OTHER_AOI == "other"
    assert candidates.candidate_aoi()["bfi"] == "ws,cat" and candidates.candidate_aoi()["pctgrs2019"] == "catrp100"


def _erom_frame():
    rows = []
    for comid, qe_ma, qa_ma, qc_ma, months in (
            (1, 100.0, 80.0, 100.0, [50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 100]),
            (2, 0.0, 0.0, 0.0, [0.0] * 12),
            (3, 10.0, 10.0, 8.0, [10.0] * 12)):
        row = {"COMID": comid, "TotDASqKM": 50.0, "QA_MA": qa_ma, "QE_MA": qe_ma, "QC_MA": qc_ma,
               "VA_MA": 1.0, "VE_MA": 1.1}
        for i, month in enumerate(candidates.EROM_MONTHS):
            row[f"QE_{month}"] = months[i]
            row[f"QA_{month}"] = months[i] * 0.8
            row[f"QC_{month}"] = months[i] if comid != 3 else months[i] * 0.5
        rows.append(row)
    return pd.DataFrame(rows)


def test_erom_metrics_guard_zero_flows_and_measure_alteration():
    out = candidates.erom_metrics(_erom_frame())
    assert out["comid"].tolist() == [1, 2, 3]
    one = out.iloc[0]
    assert one["q_min_ratio"] == pytest.approx(0.5) and one["q_max_ratio"] == pytest.approx(1.5)
    assert one["q_alteration"] == pytest.approx(1.25) and one["q_alteration_c"] == pytest.approx(1.0)
    assert one["q_seasonal_alteration"] == pytest.approx(0.0) and one["q_per_km2"] == pytest.approx(2.0)
    two = out.iloc[1]
    assert all(pd.isna(two[k]) for k in ("q_min_ratio", "q_alteration", "q_alteration_c", "q_seasonal_alteration", "q_cv_monthly"))
    three = out.iloc[2]
    assert three["q_alteration_c"] == pytest.approx(1.25) and three["q_seasonal_alteration"] == pytest.approx(1.0)
    assert three["q_cv_monthly"] == pytest.approx(0.0)
    assert "qe_01" not in out.columns and "qe_ma" in out.columns


def test_aquatic_life_rating_strict_and_cause_screen():
    rate = candidates.aquatic_life_rating
    assert rate({"ecological_use": "Fully Supporting"})[0] == "Good"
    assert rate({"ecological_use": "Not Supporting", "ircategory": "5"})[0] == "Poor"
    assert rate({"ecological_use": "Not Supporting", "ircategory": "4A"})[0] == "Fair"
    assert rate({"ecological_use": "Not Supporting", "ircategory": "5", "hastmdl": "Y"})[0] == "Fair"
    nh = {"ecological_use": "Insufficient Information", "ircategory": "5", "overallstatus": "Not Supporting",
          "mercury": "Cause", "fishconsumption_use": "Not Supporting"}
    # strict leaves New Hampshire's mercury-only units unscored; the cause screen rates them Good
    assert rate(nh)[0] is None and rate(nh, mode="cause_screen")[0] == "Good"
    mercury_only = {"ecological_use": "Not Assessed", "overallstatus": "Not Supporting", "mercury": "Cause"}
    assert rate(mercury_only)[0] is None and rate(mercury_only, mode="cause_screen")[0] == "Good"
    mixed = {"ecological_use": None, "overallstatus": "Not Supporting", "mercury": "Cause", "sediment": "Cause"}
    assert rate(mixed, mode="cause_screen")[0] is None
    overall_ok = {"ecological_use": float("nan"), "overallstatus": "Fully Supporting"}
    assert rate(overall_ok)[0] is None and rate(overall_ok, mode="cause_screen")[0] == "Good"
    assert candidates.cause_list(mixed) == ["mercury", "sediment"]
    with pytest.raises(ValueError):
        rate({}, mode="loose")


def test_erom_and_attains_steps_use_the_geodatabase_readers(tmp_path, monkeypatch):
    root = DataRoot(tmp_path / "data").ensure()
    root.analysis.mkdir()
    progress, control = state.Progress(root, quiet=True), state.Control(root)
    fake_gdb = tmp_path / "x.gdb"
    fake_gdb.mkdir()
    monkeypatch.setattr(local_gdb, "nhdplus_gdb", lambda r: fake_gdb)
    monkeypatch.setattr(local_gdb, "attains_gdb", lambda r: fake_gdb)
    import pyogrio
    attributes = pd.DataFrame([
        {"assessmentunitidentifier": "NH-2", "state": "NH", "ircategory": "5", "overallstatus": "Not Supporting",
         "ecological_use": "Insufficient Information", "mercury": "Cause", "GLOBALID": "g2"},
        {"assessmentunitidentifier": "NH-1", "state": "NH", "ircategory": "2", "overallstatus": "Fully Supporting",
         "ecological_use": "Fully Supporting", "GLOBALID": "g1"}])

    def read_dataframe(path, layer=None, columns=None, read_geometry=True, **kwargs):
        assert read_geometry is False and str(path) == str(fake_gdb)
        if layer == "NHDFlowline_Network":
            assert list(columns) == list(local_gdb.EROM_COLUMNS)
            return _erom_frame()
        assert layer == local_gdb.ATTAINS_ATTRIBUTES_LAYER
        return attributes

    monkeypatch.setattr(pyogrio, "read_dataframe", read_dataframe)
    erom = pq.read_table(candidates.run_erom(root, progress, control)).to_pandas()
    assert erom["comid"].tolist() == [1, 2, 3] and "q_alteration" in erom.columns
    att = pq.read_table(candidates.run_attains(root, progress, control))       # pyarrow keeps None as None
    assert att.column("assessmentunitidentifier").to_pylist() == ["NH-1", "NH-2"] and "globalid" not in att.column_names
    assert att.column("aquatic_life_strict").to_pylist() == ["Good", None]
    assert att.column("aquatic_life_cause_screen").to_pylist() == ["Good", "Good"]      # mercury-only listing forgiven
    assert att.column("causes").to_pylist() == ["[]", '["mercury"]']
    assert candidates.inputs_erom(root, {}) != candidates.inputs_attains(root, {})


def test_probe_keeps_only_the_name_and_scale_pairs_the_api_answers():
    def post(payload):
        name, aoi = payload["name"], payload["aoi"]
        if name == "wdrw_ld" and aoi == "cat":
            raise RuntimeError("400 no such metric at that scale")
        if name == "msst2014":
            return [{"COMID": 1}]                                  # answers, but with no value column
        column = name.upper() if aoi == "other" else f"{name}{aoi}".upper()
        return [{"COMID": 1, column: 0.5}]

    kept, dropped = candidates.probe_names({"bfi": "ws,cat", "wdrw_ld": "ws,cat", "msst2014": "other",
                                            "prg_bmmi0809": "other"}, post)
    assert kept == {"bfi": "ws,cat", "wdrw_ld": "ws", "prg_bmmi0809": "other"}
    assert dropped == ["wdrw_ld@cat", "msst2014@other"]
