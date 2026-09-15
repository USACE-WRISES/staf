"""EROM conversion, offline record plumbing, invalidation and analysis reuse."""
from __future__ import annotations

import json
import os

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder import state
from builder.analysis import candidates, values
from builder.paths import DataRoot
from builder.stages import local_gdb, national, score
from builder.units import Chunk
from easi.datasources import fabric
from easi.metrics import hydraulics
from easi.national import client, records


def _raw(comid=1):
    return {"comid": comid, "qe_ma": 10.0,
            **{f"qe_{i:02d}": float(i) for i in range(1, 13)}}


@pytest.fixture
def root(tmp_path):
    return DataRoot(tmp_path / "data").ensure()


def test_converter_requests_only_fourteen_columns_and_preserves_floats(root, monkeypatch, tmp_path):
    import pyogrio
    gdb = tmp_path / "fixture.gdb"
    frame = pd.DataFrame([_raw(2), _raw(1)])
    frame.columns = [name.upper() for name in frame.columns]
    frame.loc[0, "QE_01"] = float("inf")
    frame.loc[1, "QE_12"] = 1.234567890123

    def read(path, *, layer, columns, read_geometry):
        assert path == str(gdb) and layer == "NHDFlowline_Network"
        assert columns == list(local_gdb.EROM_EVIDENCE_COLUMNS)
        assert len(columns) == 14 and not read_geometry
        return frame

    monkeypatch.setattr(pyogrio, "read_dataframe", read)
    path = local_gdb.convert_erom(root, state.Progress(root, quiet=True), gdb=gdb)
    table = pq.read_table(path)
    assert path == root.erom
    assert table.column_names == [key.lower() for key in local_gdb.EROM_EVIDENCE_COLUMNS]
    assert table["comid"].to_pylist() == [1, 2]
    assert table["qe_12"][0].as_py() == 1.234567890123
    assert table["qe_01"][1].as_py() is None
    assert table.schema.field("qe_ma").type == pa.float64()


def test_national_erom_step_and_digest(root, monkeypatch, tmp_path):
    gdb = tmp_path / "fixture.gdb"
    monkeypatch.setattr(local_gdb, "nhdplus_gdb", lambda r: gdb)
    monkeypatch.setattr(local_gdb, "attains_gdb", lambda r: None)
    calls = []
    monkeypatch.setattr(local_gdb, "convert_erom", lambda r, p: calls.append(r))
    steps = dict(national._gdb_steps(root, state.Progress(root, quiet=True)))
    assert "erom" in steps
    steps["erom"]()
    assert calls == [root]
    assert national._inputs_for("erom", root) == state.digest(
        "erom", gdb.name, local_gdb.EROM_EVIDENCE_COLUMNS, 1)


def test_erom_rows_reads_one_filtered_huc8_and_missing_cache_is_unknown(root, monkeypatch):
    assert score.erom_rows(root, [1]) == {}
    pq.write_table(pa.Table.from_pylist([_raw(1), _raw(2), _raw(3)]), root.erom)
    calls = []
    read = pq.read_table

    def tracked(path, *args, **kwargs):
        calls.append((path, kwargs))
        return read(path, *args, **kwargs)

    monkeypatch.setattr(pq, "read_table", tracked)
    assert set(score.erom_rows(root, [3, 1, 1])) == {1, 3}
    assert calls == [(root.erom, {"filters": [("comid", "in", [1, 3])]})]
    assert score.erom_rows(root, []) == {}
    assert len(calls) == 1


def test_record_for_preserves_complete_erom_and_schema1_strata_absence():
    record = score.record_for({"comid": 1}, {}, None, {}, _raw())
    assert record["erom"] == fabric.erom_from_properties(_raw())
    assert "l3_code" not in record and "nars9" not in record
    assert records.from_row(records.to_row(record))["erom"] == record["erom"]
    assert score.record_for({"comid": 1}, {}, None, {})["erom"] is None
    invalid = {**_raw(), "qe_09": None}
    assert score.record_for({"comid": 1}, {}, None, {}, invalid)["erom"] is None


def test_erom_cache_change_invalidates_score_and_analysis_parts(root):
    before = score.erom_stamp(root)
    part_before = values.part_key(root, "02080204")
    input_before = candidates.inputs_erom(root)
    pq.write_table(pa.Table.from_pylist([_raw()]), root.erom)
    assert score.erom_stamp(root) != before
    assert values.part_key(root, "02080204") != part_before
    assert candidates.inputs_erom(root) != input_before
    stamp = score.erom_stamp(root)
    os.utime(root.erom, ns=(root.erom.stat().st_atime_ns, root.erom.stat().st_mtime_ns + 1_000_000_000))
    assert score.erom_stamp(root) != stamp


def test_score_and_harvest_pass_identical_erom_records(root, monkeypatch):
    huc8 = "02080204"
    chunk = Chunk(id="huc8-test", kind="huc8", label="test", huc8s=[huc8])
    chunk.save(root)
    root.huc8_dir(huc8).mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([{
        "comid": 1, "huc4": "0208", "huc8": huc8, "lat": 38.0, "lon": -78.0,
        "l3_code": "45", "nars9": "SAP",
    }]), root.huc8_file(huc8, "derived"))
    pq.write_table(pa.Table.from_pylist([{"comid": 1}]), root.huc8_file(huc8, "joins"))
    root.chunk_raw(chunk.id, "streamcat").parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([{"comid": 1}]), root.chunk_raw(chunk.id, "streamcat"))
    pq.write_table(pa.Table.from_pylist([_raw(1), _raw(2)]), root.erom)
    seen = []

    def fake_score(record, **kwargs):
        seen.append(record)
        return {"metricRows": [], "subIndicesRaw": {}, "computedCount": 0}

    monkeypatch.setattr(client, "score_record", fake_score)
    states, progress, control = state.UnitStates(root), state.Progress(root, quiet=True), state.Control(root)
    score.run_score(root, chunk, huc8, states, progress, control)
    values.harvest_huc8(str(root.root), huc8, chunk.id)
    assert len(seen) == 2 and seen[0] == seen[1]
    assert seen[0]["erom"] == fabric.erom_from_properties(_raw())
    stored = records.from_row(pq.read_table(root.huc8_file(huc8, "evidence")).to_pylist()[0])
    assert stored["erom"] == seen[0]["erom"]


def test_analysis_uses_one_cv_function_and_preserves_stored_supplemental_evidence(root, monkeypatch):
    root.analysis.mkdir()
    raw = [_raw(1), {**_raw(2), "qe_07": None}]
    pq.write_table(pa.Table.from_pylist(raw), root.erom)
    pq.write_table(pa.Table.from_pylist([
        {"comid": 1, "q_cv_monthly": 99.0, "qa_ma": 5.0, "q_alteration": 2.0},
        {"comid": 2, "q_cv_monthly": 99.0, "qa_ma": 7.0, "q_alteration": 1.5},
    ]), candidates.erom_path(root))
    seen = []

    def scalar_cv(erom):
        seen.append(erom)
        return 0.123456 if erom is not None else None

    monkeypatch.setattr(hydraulics, "monthly_flow_cv", scalar_cv, raising=False)
    monkeypatch.setattr(local_gdb, "read_erom", lambda *a, **k: pytest.fail("Stored national cache must be used"))
    out = pq.read_table(candidates.run_erom(root, state.Progress(root, quiet=True), state.Control(root))).to_pylist()
    assert seen == [fabric.erom_from_properties(raw[0]), None]
    assert out[0]["q_cv_monthly"] == 0.123456 and out[1]["q_cv_monthly"] is None
    assert [row["qa_ma"] for row in out] == [5.0, 7.0]
    assert [row["q_alteration"] for row in out] == [2.0, 1.5]


def test_analysis_erom_requires_raw_national_cache(root):
    with pytest.raises(RuntimeError, match="run the national erom step first"):
        candidates.run_erom(root, state.Progress(root, quiet=True), state.Control(root))
