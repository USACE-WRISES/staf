"""Bounded readers preserve the original row-selection and report behavior."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from easi.national import client, records
from test_national_client import _record


def _dataset(root, count=1300):
    root.mkdir(parents=True, exist_ok=True)
    # Unsorted files are the published format. Include a duplicate in a later
    # batch to exercise the original filter/dict last-physical-row semantics.
    comids = list(range(1000, 1000 + count))[::2] + list(range(1000, 1000 + count))[1::2]
    rows = [{"comid": comid, "huc4": "0102", "geomorph": json.dumps(
        {"position": pos, "nested": [{"value": pos}]})} for pos, comid in enumerate(comids)]
    rows.append({"comid": comids[0], "huc4": "0102", "geomorph": '{"last":true}'})
    pq.write_table(pa.Table.from_pylist(rows), root / client.evidence_asset("0102"))
    pq.write_table(pa.table({"comid": comids, "huc4": ["0102"] * count}), root / client.INDEX)
    pq.write_table(pa.Table.from_pylist(rows), root / client.scores_asset("01"))
    (root / client.MANIFEST).write_text(json.dumps({"version": 1, "units": {"0102": {"vpu": "01"}}}))
    return client.Dataset(str(root)), rows


def _old_rows(rows, wanted):
    return {int(row["comid"]): records.from_row(row) for row in rows if row["comid"] in wanted}


def test_first_middle_last_unsorted_missing_and_duplicate_rows_match_previous_reader(tmp_path):
    ds, rows = _dataset(tmp_path)
    wanted = [rows[0]["comid"], rows[700]["comid"], rows[-2]["comid"], 999999]
    before = pa.total_allocated_bytes()
    assert ds.records(wanted) == _old_rows(rows, wanted)
    assert ds.scores(wanted) == _old_rows(rows, wanted)
    assert ds.records([999999]) == {}
    assert ds.records([]) == {}
    assert ds.records(wanted)[wanted[0]]["geomorph"] == {"last": True}
    assert pa.total_allocated_bytes() == before
    assert not any(isinstance(value, (pa.Table, pa.RecordBatch)) for value in vars(ds).values())
    assert ds._index[0].dtype == np.dtype("int64")
    assert ds._index[1].dtype == np.dtype("S4")
    assert sum(column.nbytes for column in ds._index) == 12 * 1300
    assert ds.huc4_of(wanted) == {comid: "0102" for comid in wanted[:-1]}


def test_index_keeps_leading_zeroes_and_first_duplicate(tmp_path):
    pq.write_table(pa.table({"comid": [9, 3, 9, 1], "huc4": ["0009", "0102", "9999", "0001"]}),
                   tmp_path / client.INDEX)
    ds = client.Dataset(str(tmp_path))
    assert ds.huc4_of([1, 3, 9, -1]) == {1: "0001", 3: "0102", 9: "0009"}


@pytest.mark.parametrize("huc4", ["12345", "1", "abcd", None, "１２３４"])
def test_compact_index_rejects_invalid_codes_instead_of_truncating(tmp_path, huc4):
    pq.write_table(pa.table({"comid": [1], "huc4": [huc4]}), tmp_path / client.INDEX)
    ds = client.Dataset(str(tmp_path))
    with pytest.raises(client.DatasetError, match="invalid HUC4"):
        ds.huc4_of([1])
    assert ds._index is None


def test_decodes_only_matches_and_stops_after_last_needed_batch(tmp_path, monkeypatch):
    ds, rows = _dataset(tmp_path)
    real_file = pq.ParquetFile
    reads = []
    decoded = []
    real_decode = records.from_row

    class TracedFile:
        def __init__(self, path, **kwargs):
            assert kwargs == {"buffer_size": 256 * 1024, "pre_buffer": False}
            self.reader = real_file(path, **kwargs)
            self.metadata = self.reader.metadata
            self.trace = {"path": path.name, "closed": False, "batches": 0}
            reads.append(self.trace)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.reader.close()
            self.trace["closed"] = True

        def iter_batches(self, **kwargs):
            assert kwargs["batch_size"] == 512 and kwargs["use_threads"] is False
            self.trace["columns"] = kwargs.get("columns")
            iterator = self.reader.iter_batches(**kwargs)
            try:
                for batch in iterator:
                    self.trace["batches"] += 1
                    yield batch
            finally:
                iterator.close()

    monkeypatch.setattr(pq, "ParquetFile", TracedFile)
    monkeypatch.setattr(pq, "read_table", lambda *a, **k: pytest.fail("whole table read"))

    def decode(row):
        decoded.append(row["comid"])
        return real_decode(row)

    monkeypatch.setattr(records, "from_row", decode)
    wanted = [rows[1]["comid"], rows[2]["comid"]]
    assert ds.records(wanted) == _old_rows(rows, wanted)
    # Ignore the independent expected-result conversions in the assertion.
    assert decoded[:2] == wanted and len(decoded) == 4
    evidence = [read for read in reads if read["path"].startswith("evidence")]
    assert evidence[0]["columns"] == ["comid"] and evidence[0]["batches"] == 3
    assert evidence[1]["columns"] is None and evidence[1]["batches"] == 1
    assert all(read["closed"] for read in reads)
    before = len(reads)
    ds.records(wanted)
    assert len(reads) == before


def test_nested_values_are_isolated_between_callers_and_cache(tmp_path):
    ds, rows = _dataset(tmp_path)
    comid = rows[1]["comid"]
    original = ds.records([comid])
    original[comid]["geomorph"]["nested"][0]["value"] = "mutated"
    assert ds.records([comid])[comid]["geomorph"]["nested"][0]["value"] == 1
    other = ds.scores([comid])
    other[comid]["geomorph"]["nested"].clear()
    assert ds.scores([comid])[comid]["geomorph"]["nested"] == [{"value": 1}]


def test_record_cache_evicts_by_count_and_reuses_recent_results(tmp_path, monkeypatch):
    ds, rows = _dataset(tmp_path)
    wanted = [row["comid"] for row in rows[1:259]]
    assert len(ds.records(wanted)) == 258
    assert len(ds._records) == 256
    assert ds._record_bytes <= 16 * 1024 * 1024
    real_decode = records.from_row
    decoded = []

    def decode(row):
        decoded.append(row["comid"])
        return real_decode(row)

    monkeypatch.setattr(records, "from_row", decode)
    ds.records([wanted[-1]])
    assert not decoded
    ds.records([wanted[0]])
    assert decoded == [wanted[0]]


def test_cache_byte_caps_and_oversize_records(tmp_path, monkeypatch):
    ds, rows = _dataset(tmp_path, count=20)
    first = rows[1]["comid"]
    ds.records([first])
    one_record = ds._record_bytes
    one_position = ds._position_bytes
    ds.clear()
    monkeypatch.setattr(client, "_RECORD_CACHE_BYTES", one_record + 1)
    monkeypatch.setattr(client, "_POSITION_CACHE_BYTES", one_position - 1)
    assert len(ds.records([rows[1]["comid"], rows[2]["comid"]])) == 2
    assert len(ds._records) == 1 and ds._record_bytes <= one_record + 1
    assert not ds._positions and ds._position_bytes == 0
    ds.clear()
    monkeypatch.setattr(client, "_RECORD_CACHE_BYTES", 1)
    assert first in ds.records([first])
    assert not ds._records and ds._record_bytes == 0


def test_position_cache_count_is_bounded(tmp_path):
    ds, rows = _dataset(tmp_path, count=20)
    for i in range(18):
        name = f"evidence_{i:04}.parquet"
        pq.write_table(pa.Table.from_pylist(rows), tmp_path / name)
        ds._rows_for(name, [rows[1]["comid"]])
    assert len(ds._positions) == 16
    assert "evidence_0000.parquet" not in ds._positions
    assert "evidence_0017.parquet" in ds._positions
    assert ds._position_bytes <= 8 * 1024 * 1024


def test_simultaneous_callers_decode_once(tmp_path, monkeypatch):
    ds, rows = _dataset(tmp_path)
    entered, release = threading.Event(), threading.Event()
    real_decode = records.from_row
    calls = []
    comid = rows[1]["comid"]

    def decode(row):
        calls.append(row["comid"])
        entered.set()
        assert release.wait(10)
        return real_decode(row)

    monkeypatch.setattr(records, "from_row", decode)
    with ThreadPoolExecutor(max_workers=4) as pool:
        first = pool.submit(ds.records, [comid])
        assert entered.wait(10)
        others = [pool.submit(ds.records, [comid]) for _ in range(3)]
        release.set()
        result = first.result(10)
        assert all(task.result(10) == result for task in others)
    assert calls == [comid]


@pytest.mark.parametrize("invalidate", ["refresh", "clear"])
def test_refresh_during_decode_discards_old_records(tmp_path, monkeypatch, invalidate):
    ds, rows = _dataset(tmp_path)
    entered, release = threading.Event(), threading.Event()
    real_decode = records.from_row

    def decode(row):
        entered.set()
        assert release.wait(10)
        return real_decode(row)

    monkeypatch.setattr(records, "from_row", decode)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(ds.records, [rows[1]["comid"]])
        assert entered.wait(10)
        if invalidate == "refresh":
            (tmp_path / client.MANIFEST).write_text('{"version":2}')
            ds.manifest(refresh=True)
        else:
            ds.clear()
        release.set()
        with pytest.raises(client.DatasetError, match="dataset changed"):
            task.result(10)
    assert not ds._positions and not ds._records and ds._index is None
    assert ds._position_bytes == ds._record_bytes == 0


@pytest.mark.parametrize("error", [ValueError, KeyboardInterrupt])
def test_readers_release_resources_after_decode_failure(tmp_path, monkeypatch, error):
    ds, rows = _dataset(tmp_path)
    before = pa.total_allocated_bytes()
    real_decode = records.from_row

    def fail(row):
        raise error("injected decode failure")

    monkeypatch.setattr(records, "from_row", fail)
    with pytest.raises(error, match="injected"):
        ds.records([rows[1]["comid"]])
    assert pa.total_allocated_bytes() == before
    assert not ds._records
    assert client._PARQUET_READERS.acquire(blocking=False)
    assert client._PARQUET_READERS.acquire(blocking=False)
    assert not client._PARQUET_READERS.acquire(blocking=False)
    client._PARQUET_READERS.release()
    client._PARQUET_READERS.release()
    monkeypatch.setattr(records, "from_row", real_decode)
    assert rows[1]["comid"] in ds.records([rows[1]["comid"]])


def test_index_refresh_after_resolving_path_cannot_cache_old_index(tmp_path, monkeypatch):
    ds, _ = _dataset(tmp_path)
    real_path = ds.asset_path

    def path_then_refresh(name):
        path = real_path(name)
        (tmp_path / client.MANIFEST).write_text('{"version":2}')
        ds.manifest(refresh=True)
        return path

    monkeypatch.setattr(ds, "asset_path", path_then_refresh)
    with pytest.raises(client.DatasetError, match="dataset changed"):
        ds.huc4_of([1000])
    assert ds._index is None


@pytest.mark.parametrize("method", ["records", "scores"])
def test_public_lookup_cannot_combine_partitions_across_refresh(tmp_path, monkeypatch, method):
    ds, rows = _dataset(tmp_path)
    real_rows = ds._rows_for

    def rows_then_refresh(name, comids):
        result = real_rows(name, comids)
        (tmp_path / client.MANIFEST).write_text('{"version":2}')
        ds.manifest(refresh=True)
        return result

    monkeypatch.setattr(ds, "_rows_for", rows_then_refresh)
    with pytest.raises(client.DatasetError, match="dataset changed"):
        getattr(ds, method)([rows[1]["comid"]])


def test_parquet_reader_limit_is_shared_across_datasets(tmp_path, monkeypatch):
    datasets = [_dataset(tmp_path / str(i), count=20)[0] for i in range(3)]
    real_file = pq.ParquetFile
    lock = threading.Lock()
    both_entered, release = threading.Event(), threading.Event()
    active = peak = 0

    class TracedFile:
        def __init__(self, path, **kwargs):
            self.reader = real_file(path, **kwargs)
            self.metadata = self.reader.metadata

        def __enter__(self):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    both_entered.set()
            assert release.wait(10)
            return self

        def __exit__(self, *args):
            nonlocal active
            self.reader.close()
            with lock:
                active -= 1

        def iter_batches(self, **kwargs):
            return self.reader.iter_batches(**kwargs)

    monkeypatch.setattr(pq, "ParquetFile", TracedFile)
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks = [pool.submit(ds.records, [1002]) for ds in datasets]
        assert both_entered.wait(10)
        release.set()
        assert all(1002 in task.result(10) for task in tasks)
    assert peak == 2 and active == 0


def test_scored_report_matches_original_decoding(tmp_path):
    row = records.to_row(_record())
    pq.write_table(pa.Table.from_pylist([row]), tmp_path / client.evidence_asset("0102"))
    pq.write_table(pa.table({"comid": [row["comid"]], "huc4": ["0102"]}), tmp_path / client.INDEX)
    ds = client.Dataset(str(tmp_path))
    actual = ds.records([row["comid"]])[row["comid"]]
    expected = records.from_row(pq.read_table(tmp_path / client.evidence_asset("0102")).to_pylist()[0])
    assert actual == expected
    assert client.score_record(actual, cross_section=False) == client.score_record(expected, cross_section=False)
