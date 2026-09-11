"""The inventory the Data and Cross-sections tabs show: national datasets,
chunk downloads, the archive by state, the bandwidth budget, disk use."""
from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq

from builder import config, inventory, state
from builder.paths import DataRoot
from builder.units import Chunk


def _root(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    pq.write_table(pa.table({"comid": pa.array([1, 2], pa.int64()), "huc8": ["02080204", "02080204"]}), root.vaa)
    pq.write_table(pa.table({"comid": pa.array([1, 2], pa.int64()), "huc4": ["0208", "0208"]}), root.index)
    states = state.UnitStates(root)
    states.set("national", "vaa", "done", inputs="x", note="82 s")
    states.set("national", "index", "done", inputs="x")
    states.set("national", "nas", "running", inputs="x")
    chunk = Chunk(id="state-VA", kind="state", label="Virginia", huc8s=["02080204", "02080205"], n_comids=1500,
                  states=["VA"], vpus=["02"])
    chunk.save(root)
    raw = root.chunk_dir("state-VA") / "raw"
    raw.mkdir(parents=True)
    pq.write_table(pa.table({"comid": pa.array([1], pa.int64())}), raw / "streamcat.parquet")
    pq.write_table(pa.table({"comid": pa.array([1], pa.int64())}), raw / "wqp_tn.parquet")
    states.set("state-VA", "streamcat", "done", inputs="x")
    states.set("state-VA", "wqp", "running", inputs="x")
    states.set("02080204", "derive", "done", inputs="x")
    states.set("02080204", "xs_sample", "done", inputs="x")
    states.set("02080205", "xs_sample", "pending", inputs="x", note="paused")
    huc8 = root.huc8_dir("02080204")
    huc8.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"comid": 1, "status": "ok", "dem_res_m": 1.0, "bytes_est": 4_000_000},
        {"comid": 2, "status": "ok", "dem_res_m": 10.0, "bytes_est": 200_000},
        {"comid": 3, "status": "no_reach", "dem_res_m": None, "bytes_est": 0}]), huc8 / "xs_sample.parquet")
    (huc8 / "xs_profiles.parquet").write_bytes(b"x" * 1234)
    return root


def test_national_datasets_report_status_size_and_rows(tmp_path):
    listed = inventory.national_datasets(_root(tmp_path))
    rows = {r["step"]: r for r in listed}
    assert rows["vaa"]["status"] == "done" and rows["vaa"]["rows"] == 2 and rows["vaa"]["size"] > 0
    assert rows["vaa"]["note"] == "82 s" and rows["index"]["status"] == "done"
    assert rows["nas"]["status"] == "running"
    assert rows["streamcat"]["status"] == "missing" and rows["dem1m_index"]["status"] == "missing"
    assert [r["step"] for r in listed][:2] == ["vaa", "index"]


def test_chunk_downloads_list_every_state_and_each_source(tmp_path):
    rows = {r["chunk"]: r for r in inventory.chunk_downloads(_root(tmp_path))}
    va = rows["state-VA"]
    assert va["label"] == "Virginia" and va["huc8s"] == 2 and va["reaches"] == 1500 and va["status"] == "partial"
    assert va["sources"]["streamcat"]["status"] == "done" and va["sources"]["streamcat"]["size"] > 0
    assert va["sources"]["wqp"]["status"] == "running" and va["sources"]["attains"]["status"] == "pending"
    assert va["huc8_stages"]["derive"] == {"done": 1, "total": 2, "status": "1/2"}
    assert va["raw_size"] > 0 and va["published_huc4s"] == 0 and va["huc4s"] == 1
    assert rows["state-TX"]["status"] == "not started" and rows["state-TX"]["huc8s"] is None
    assert len(rows) == len(inventory.CONUS_STATES)          # Virginia's chunk replaces its placeholder


def test_xs_inventory_counts_the_archive_by_resolution(tmp_path):
    rows = {r["chunk"]: r for r in inventory.xs_inventory(_root(tmp_path))}
    va = rows["state-VA"]
    assert va["sampled"] == 1 and va["derived"] == 0 and va["huc8s"] == 2 and va["status"] == "paused"
    assert va["reaches_sampled"] == 2 and va["n_1m"] == 1 and va["n_10m"] == 1 and va["n_3m"] == 0
    assert va["archive_bytes"] == 1234 and va["bytes_est"] == 3_020_000 and va["last_activity"]
    assert rows["state-WY"]["status"] == "not started"


def test_xs_inventory_counts_a_huc8_in_flight_from_its_heartbeat(tmp_path):
    import json
    root = _root(tmp_path)
    states = state.UnitStates(root)
    states.set("02080205", "xs_sample", "running", inputs="x")
    beat = root.huc8_dir("02080205")
    beat.mkdir(parents=True, exist_ok=True)
    (beat / "xs_sample.progress.json").write_text(json.dumps({"done": 300, "total": 900, "n_1m": 280, "n_3m": 0,
                                                             "n_10m": 20, "bytes_this_run": 700_000_000}),
                                                  encoding="utf-8")
    va = {r["chunk"]: r for r in inventory.xs_inventory(root)}["state-VA"]
    assert va["status"] == "sampling" and va["sampled"] == 1
    assert va["reaches_sampled"] == 302 and va["n_1m"] == 281 and va["n_10m"] == 21
    assert va["bytes_est"] == 281 * 3_000_000 + 21 * 20_000


def test_bandwidth_catalog_and_disk_status(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr(config, "XS_BYTE_BUDGET_GB", 0.00001)          # 10 KB
    counter = state.Bandwidth(root, budget_bytes=10_000, flush_every=1)
    counter.add(4_000)
    status = inventory.bandwidth_status(root)
    assert status["bytes"] == 4_000 and status["budget_bytes"] == 10_000 and status["fraction"] == 0.4
    assert status["month"] in status["months"] and status["reaches"] == 1
    catalogs = inventory.catalog_status(root)
    assert catalogs["1m"]["present"] is False and catalogs["19"]["present"] is False
    disk = inventory.disk_status(root)
    assert disk["free"] > 0 and disk["folders"]["huc8"] >= 1234 and "chunks" in disk["folders"]
