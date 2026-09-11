"""StreamCat national cache: the region pull, its resume, and the chunk stage
reading rows from the cache instead of the API."""
from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder import config, state
from builder.paths import DataRoot
from builder.stages import streamcat as sc
from builder.stages import streamcat_national as scn
from builder.units import Chunk

NAMES = ["elev", "pctimp2019", "rddens", "bfi", "clay", "sand"]
REGION_COMIDS = {"Region02": [200, 201, 202], "Region03N": [300, 301]}


def fake_post(payload):
    names = payload["name"].split(",")
    aois = payload["aoi"].split(",")
    comids = REGION_COMIDS[payload["region"]] if "region" in payload else [int(c) for c in payload["comid"].split(",")]
    items = []
    for c in comids:
        item = {"COMID": c}
        for n in names:
            for a in aois:
                item[f"{n}{a}".upper()] = float(c) + len(n)
        items.append(item)
    return items


def test_national_pull_merges_regions_and_groups_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STREAMCAT_NAME_GROUP", 5)
    root = DataRoot(tmp_path / "data").ensure()
    progress, control = state.Progress(root, quiet=True), state.Control(root)
    calls = []

    def post(payload):
        calls.append(payload)
        return fake_post(payload)

    # a finished request (ledger entry + part on disk) is not asked again
    ledger = state.Ledger(root, "streamcat-national")
    parts = scn._parts_dir(root)
    pre = sc._normalize(fake_post({"name": ",".join(NAMES[:5]), "aoi": "ws,cat,wsrp100", "region": "Region02"}))
    scn.common.write_parquet(scn.table_of(pre), parts / "Region02-g0.parquet")
    ledger.add("Region02-g0")

    path = scn.run_streamcat_national(root, progress, control, names=NAMES, post=post,
                                      region_list=["Region02", "Region03N"], workers=2)
    assert [p["region"] for p in calls].count("Region02") == 1 and len(calls) == 3
    table = pq.read_table(path)
    assert table.column("comid").to_pylist() == [200, 201, 202, 300, 301]
    assert len(table.column_names) == 1 + len(NAMES) * 3
    assert table.column("elevws").to_pylist()[0] == pytest.approx(204.0)
    assert not parts.exists() and len(state.Ledger(root, "streamcat-national")) == 0


def test_chunk_stage_reads_the_cache_and_asks_the_api_only_for_missing_reaches(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STREAMCAT_NAME_GROUP", 5)
    monkeypatch.setattr(config, "STREAMCAT_COMID_CHUNK", 500)
    root = DataRoot(tmp_path / "data").ensure()
    progress, control = state.Progress(root, quiet=True), state.Control(root)
    scn.run_streamcat_national(root, progress, control, names=NAMES, post=fake_post,
                               region_list=["Region02"], workers=1)
    # the VAA slim table: three cached reaches and one the cache lacks
    pq.write_table(pa.table({"comid": pa.array([200, 201, 202, 999], pa.int64()),
                             "huc8": ["02080204"] * 4}), root.vaa)
    chunk = Chunk(id="huc8-test", kind="huc8", label="t", huc8s=["02080204"])
    chunk.save(root)
    calls = []

    def post(payload):
        calls.append(payload)
        return fake_post(payload)

    monkeypatch.setattr(sc, "_post", post)
    sc.run_streamcat(root, chunk, state.UnitStates(root), progress, control, names=NAMES)
    out = pq.read_table(root.chunk_raw("huc8-test", "streamcat"))
    assert out.column("comid").to_pylist() == [200, 201, 202, 999]
    assert len(out.column_names) == 1 + len(NAMES) * 3
    assert len(calls) == 2 and all(p.get("comid") == "999" for p in calls)   # one batch per group, no state pull
