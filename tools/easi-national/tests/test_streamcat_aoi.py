"""AOI routing, old-cache upgrades, null answers, and request-plan resume."""
from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder import config, state
from builder.paths import DataRoot
from builder.stages import streamcat as sc, streamcat_national as scn
from builder.units import Chunk

MODEL = "prg_bmmi0809"


def _post(payload):
    comids = [200, 201] if "region" in payload else [int(c) for c in payload["comid"].split(",")]
    return [{"COMID": c, **{
        (name + ("" if aoi == "other" else aoi)).upper(): None if aoi == "other" else float(c)
        for name in payload["name"].split(",") for aoi in payload["aoi"].split(",")}}
        for c in comids]


@pytest.mark.parametrize("selected", ["regional", "legacy"])
def test_default_requests_follow_the_runtime_criteria_set(tmp_path, monkeypatch, selected):
    monkeypatch.setenv("EASI_CRITERIA_SET", selected)
    root = DataRoot(tmp_path / "data").ensure()
    calls = []

    def post(payload):
        calls.append(payload)
        return _post(payload)

    path = scn.run_streamcat_national(root, state.Progress(root, quiet=True), state.Control(root),
                                     post=post, region_list=["Region02"], workers=1)
    asked = [n for p in calls for n in p["name"].split(",")]
    if selected == "regional":
        assert MODEL in asked and "prG_BMMI" not in asked
        assert [p["name"] for p in calls if p["aoi"] == "other"] == [MODEL]
        table = pq.read_table(path)
        assert table.column(MODEL).to_pylist() == [None, None]
        assert MODEL + "ws" not in table.column_names
    else:
        assert "prG_BMMI" in asked and MODEL not in asked
        assert all(p["aoi"] == "ws,cat,wsrp100" for p in calls)


def test_old_cache_fetches_missing_model_but_retains_known_null(tmp_path, monkeypatch):
    root = DataRoot(tmp_path / "data").ensure()
    pq.write_table(scn.table_of({c: {"elevws": 10.0, "elevcat": 11.0, "elevwsrp100": 12.0}
                                for c in [200, 201]}), scn.cache_path(root))
    pq.write_table(pa.table({"comid": pa.array([200, 201], pa.int64()),
                             "huc8": ["02080204"] * 2}), root.vaa)
    chunk = Chunk(id="huc8-test", kind="huc8", label="t", huc8s=["02080204"])
    calls = []

    def post(payload):
        calls.append(payload)
        return _post(payload)

    monkeypatch.setattr(sc, "_post", post)
    args = (root, chunk, state.UnitStates(root), state.Progress(root, quiet=True), state.Control(root))
    kwargs = {"names": ["elev", MODEL], "aoi_by_name": {MODEL: "other"}}
    sc.run_streamcat(*args, **kwargs)
    assert calls == [{"name": MODEL, "aoi": "other", "comid": "200,201"}]
    table = pq.read_table(root.chunk_raw(chunk.id, "streamcat"))
    assert table.column(MODEL).to_pylist() == [None, None]
    assert table.column("elevws").to_pylist() == [10.0, 10.0]
    # An upgraded cache's explicit nulls mean the request was completed.
    pq.write_table(table, scn.cache_path(root))
    calls.clear()
    sc.run_streamcat(*args, **kwargs, force=True)
    assert calls == []


def test_national_resume_discards_parts_when_metric_names_change(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STREAMCAT_NAME_GROUP", 1)
    root = DataRoot(tmp_path / "data").ensure()
    args = (root, state.Progress(root, quiet=True), state.Control(root))
    calls = []

    def interrupted(payload):
        calls.append(payload)
        if payload["name"] == "sand":
            raise state.PauseRequested()
        return _post(payload)

    with pytest.raises(state.PauseRequested):
        scn.run_streamcat_national(*args, names=["elev", "sand"], post=interrupted,
                                   region_list=["Region02"], workers=1)
    assert (scn._parts_dir(root) / "Region02-g0.parquet").exists()
    calls.clear()

    def resumed(payload):
        calls.append(payload)
        return _post(payload)

    path = scn.run_streamcat_national(*args, names=["bfi", "sand"], post=resumed,
                                     region_list=["Region02"], workers=1)
    assert [p["name"] for p in calls] == ["bfi", "sand"]
    assert "bfiws" in pq.read_schema(path).names and "elevws" not in pq.read_schema(path).names


def test_same_plan_resumes_only_the_missing_aoi_group(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    args = (root, state.Progress(root, quiet=True), state.Control(root))
    kwargs = {"names": ["elev", MODEL], "aoi_by_name": {MODEL: "other"},
              "region_list": ["Region02"], "workers": 1}

    def interrupted(payload):
        if payload["aoi"] == "other":
            raise state.PauseRequested()
        return _post(payload)

    with pytest.raises(state.PauseRequested):
        scn.run_streamcat_national(*args, **kwargs, post=interrupted)
    assert state.Ledger(root, "streamcat-national").keys() == ["Region02-ws+cat+wsrp100-g0"]
    calls = []

    def resumed(payload):
        calls.append(payload)
        return _post(payload)

    scn.run_streamcat_national(*args, **kwargs, post=resumed)
    assert calls == [{"name": MODEL, "aoi": "other", "region": "Region02"}]
