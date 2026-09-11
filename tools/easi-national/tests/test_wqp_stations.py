"""WQP by station batches: parameter derivation, ledger coverage, halving a cut
batch, date windows for one overloaded station, batch-size adaptation, and the
stage end to end with a resume."""
from __future__ import annotations

import csv
import io

import pytest

from builder.stages import wqp as wqp_stage
from builder.stages import wqp_stations as st

RESULT_FIELDS = ["ResultIdentifier", "MonitoringLocationIdentifier", "CharacteristicName",
                 "ActivityStartDate", "ResultMeasureValue", "ResultMeasure/MeasureUnitCode",
                 "ResultSampleFractionText", "ResultStatusIdentifier", "ResultDetectionConditionText",
                 "LatitudeMeasure", "LongitudeMeasure"]
STATION_FIELDS = ["MonitoringLocationIdentifier", "MonitoringLocationName", "OrganizationIdentifier",
                  "LatitudeMeasure", "LongitudeMeasure"]


def _csv(fields, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({f: row.get(f, "") for f in fields})
    return buf.getvalue().encode("utf-8")


def _result(station, ident, name="Phosphorus", value="1"):
    return {"ResultIdentifier": ident, "MonitoringLocationIdentifier": station, "CharacteristicName": name,
            "ActivityStartDate": "2020-01-01", "ResultMeasureValue": value,
            "ResultMeasure/MeasureUnitCode": "mg/l", "ResultSampleFractionText": "Total",
            "LatitudeMeasure": "37.5", "LongitudeMeasure": "-78.5"}


def test_normalize_rows_derives_the_parameter_from_the_characteristic_name():
    text = _csv(RESULT_FIELDS, [_result("S", "1", "Total Nitrogen, mixed forms"), _result("S", "2", "Phosphorus"),
                                _result("S", "3", "Temperature, water"), _result("S", "4", "Nitrate")]).decode()
    assert [r["param"] for r in wqp_stage.normalize_rows(None, text)] == ["tn", "tp"]
    assert [r["param"] for r in wqp_stage.normalize_rows("tn", text)] == ["tn"] * 4   # explicit: unchanged


def test_uncovered_ranges_come_from_the_ledger_keys():
    assert st.uncovered(10, []) == [[0, 10]]
    assert st.uncovered(10, [st.batch_key(0, 3), st.batch_key(5, 7), "junk"]) == [[3, 5], [7, 10]]
    assert st.uncovered(10, [st.batch_key(0, 10)]) == []


def test_a_cut_batch_is_halved_and_a_cut_station_is_fetched_in_date_windows():
    calls = []

    def fetch(ids, start, end=None):
        calls.append((tuple(ids), start, end))
        whole_range = start == "09-10-2016" and end is None
        if len(ids) > 2 or ("BIG" in ids and (len(ids) > 1 or whole_range)):
            raise ConnectionError("('Connection aborted.', RemoteDisconnected(...))")
        return _csv(RESULT_FIELDS, [_result(s, f"{s}|{start}|{end}") for s in ids])

    result = st.batch_rows(["A", "B", "C", "BIG", "E"], "09-10-2016", fetch, sleep=lambda s: None)
    assert result.split and result.seconds is None
    assert sorted({r["station"] for r in result.rows}) == ["A", "B", "BIG", "C", "E"]
    big = [c for c in calls if c[0] == ("BIG",)]
    assert big[0][2] is None and len(big) == 1 + st.DATE_WINDOWS
    assert big[-1][2] is None                                    # the last window keeps the open end
    assert len(result.rows) == 4 + st.DATE_WINDOWS


def test_transient_errors_are_retried_with_pauses_then_raise():
    naps = []

    def broken(ids, start, end=None):
        raise RuntimeError("500 Server Error")

    with pytest.raises(RuntimeError, match="keep failing"):
        st.batch_rows(["A", "B"], "09-10-2016", broken, sleep=naps.append)
    assert naps == list(st.RETRY_DELAYS_S)                      # retried, never split


def test_a_throttled_batch_waits_with_escalating_pauses_and_parks_every_connection(monkeypatch):
    import time
    monkeypatch.setitem(st._cooldown, "until", 0.0)
    naps, calls = [], {"n": 0}

    def throttled_twice(ids, start, end=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("429 Client Error: Too Many Requests for url: https://example")
        return _csv(RESULT_FIELDS, [_result("A", "1")])

    result = st.batch_rows(["A"], "09-10-2016", throttled_twice, sleep=naps.append)
    assert len(result.rows) == 1 and result.requests == 3 and not result.split
    assert naps[0] == st.THROTTLE_DELAYS_S[0] and st.THROTTLE_DELAYS_S[1] in naps   # 30 s, then 60 s
    assert st._cooldown["until"] > time.monotonic()               # other connections wait too

    def always_throttled(ids, start, end=None):
        raise RuntimeError("429 Client Error: Too Many Requests")

    with pytest.raises(RuntimeError, match="still throttled"):
        st.batch_rows(["A", "B"], "09-10-2016", always_throttled, sleep=lambda s: None)


def test_batch_size_follows_the_answer_time():
    quick = st.BatchResult(rows=[], seconds=5.0, split=False, requests=1)
    slow = st.BatchResult(rows=[], seconds=150.0, split=False, requests=1)
    cut = st.BatchResult(rows=[], seconds=None, split=True, requests=3)
    assert st.adapt(25, quick) == 38 and st.adapt(st.BATCH_MAX, quick) == st.BATCH_MAX
    assert st.adapt(25, slow) == 12 and st.adapt(25, cut) == 12 and st.adapt(5, cut) == st.BATCH_MIN
    assert st.adapt(25, st.BatchResult(rows=[], seconds=60.0, split=False, requests=1)) == 25


def test_batches_are_capped_by_url_length_and_rows_take_missing_coordinates_from_the_station_list(monkeypatch):
    stations = [{"station": f"ORG-{i:04d}", "station_name": f"site {i}", "org": "ORG", "lat": 37.0 + i, "lon": -78.0}
                for i in range(50)]
    assert st.fit(stations, 0, 10) == 10 and st.fit(stations, 45, 10) == 50
    monkeypatch.setattr(st, "MAX_IDS_CHARS", 40)             # room for two ids of 8 chars plus the prefix
    assert st.fit(stations, 0, 10) == 2 and st.fit(stations, 49, 10) == 50
    rows = [{"station": "ORG-0003", "station_name": "", "lat": None, "lon": None},
            {"station": "ORG-0004", "station_name": "kept", "lat": 1.0, "lon": 2.0},
            {"station": "elsewhere", "station_name": "", "lat": None, "lon": None}]
    st.fill_from_stations(rows, stations)
    assert (rows[0]["lat"], rows[0]["lon"], rows[0]["station_name"]) == (40.0, -78.0, "site 3")
    assert (rows[1]["lat"], rows[1]["station_name"]) == (1.0, "kept")
    assert rows[2]["lat"] is None


def test_date_windows_cover_the_span_without_overlap():
    windows = st.date_windows("09-10-2016", "09-09-2026")
    assert windows[0][0] == "09-10-2016" and windows[-1][1] == "09-09-2026"
    assert len(windows) == st.DATE_WINDOWS
    assert st.date_windows("09-10-2016")[-1][1] is None


def test_the_pool_requeues_cut_batches_as_halves_with_their_own_ledger_keys(tmp_path, monkeypatch):
    from builder import config, state
    from builder.paths import DataRoot
    from builder.units import Chunk
    monkeypatch.setattr(config, "WQP_CONCURRENCY", 2)
    monkeypatch.setattr(st, "CELL_DEG", 2.0)
    monkeypatch.setattr(st, "BATCH_START", 8)
    monkeypatch.setitem(st._cooldown, "until", 0.0)
    root = DataRoot(tmp_path / "data").ensure()
    chunk = Chunk(id="huc8-test", kind="huc8", label="test", huc8s=["02080204"],
                  bbox=[-79.0, 37.0, -78.0, 38.0])
    chunk.save(root)
    keys_seen = []

    def stations(bbox, start):
        return _csv(STATION_FIELDS, [{"MonitoringLocationIdentifier": f"S{k:02d}", "MonitoringLocationName": "n",
                                      "OrganizationIdentifier": "o", "LatitudeMeasure": "37.5",
                                      "LongitudeMeasure": "-78.5"} for k in range(8)])

    def results(ids, start, end=None):
        keys_seen.append(tuple(ids))
        if len(ids) > 2:
            raise ConnectionError("('Connection aborted.', RemoteDisconnected(...))")
        return _csv(RESULT_FIELDS, [_result(s, f"{s}-tp", "Phosphorus") for s in ids])

    states, progress, control = state.UnitStates(root), state.Progress(root, quiet=True), state.Control(root)
    st.run_wqp(root, chunk, states, progress, control, fetch=results, fetch_stations_fn=stations)
    import pyarrow.parquet as pq
    tp = pq.read_table(root.chunk_raw("huc8-test", "wqp_tp")).to_pylist()
    assert sorted(r["station"] for r in tp) == [f"S{k:02d}" for k in range(8)]
    assert max(len(k) for k in keys_seen) == 8 and [len(k) for k in keys_seen].count(2) == 4   # 8 -> 4+4 -> 2+2+2+2
    assert states.is_done("huc8-test", "wqp")


def test_run_wqp_lists_stations_per_cell_then_fetches_batches_and_resumes(tmp_path, monkeypatch):
    from builder import config, state
    from builder.paths import DataRoot
    from builder.units import Chunk
    monkeypatch.setattr(config, "WQP_CONCURRENCY", 2)
    monkeypatch.setattr(st, "CELL_DEG", 0.5)
    monkeypatch.setattr(st, "BATCH_START", 5)
    root = DataRoot(tmp_path / "data").ensure()
    chunk = Chunk(id="huc8-test", kind="huc8", label="test", huc8s=["02080204"],
                  bbox=[-79.0, 37.0, -78.0, 38.0])
    chunk.save(root)
    asked = []

    def stations(bbox, start):
        tag = f"{bbox[0]:.1f}_{bbox[1]:.1f}"
        return _csv(STATION_FIELDS, [{"MonitoringLocationIdentifier": f"S{tag}_{k}", "MonitoringLocationName": "n",
                                      "OrganizationIdentifier": "o", "LatitudeMeasure": str(bbox[1] + 0.1),
                                      "LongitudeMeasure": str(bbox[0] + 0.1)} for k in range(3)])

    def results(ids, start, end=None):
        asked.append(list(ids))
        rows = []
        for s in ids:
            rows.append(_result(s, f"{s}-tn", "Nitrogen"))
            rows.append(_result(s, f"{s}-tp", "Total Phosphorus, mixed forms"))
        return _csv(RESULT_FIELDS, rows)

    states, progress, control = state.UnitStates(root), state.Progress(root), state.Control(root)
    st.run_wqp(root, chunk, states, progress, control, fetch=results, fetch_stations_fn=stations)
    import pyarrow.parquet as pq
    listed = pq.read_table(root.chunk_raw("huc8-test", "wqp_stations")).to_pylist()
    assert len(listed) == 12 and listed == sorted(listed, key=lambda s: s["station"])
    tn = pq.read_table(root.chunk_raw("huc8-test", "wqp_tn")).to_pylist()
    tp = pq.read_table(root.chunk_raw("huc8-test", "wqp_tp")).to_pylist()
    assert len(tn) == 12 and len(tp) == 12 and all(r["param"] == "tn" for r in tn)
    assert sum(len(b) for b in asked) == 12 and max(len(b) for b in asked) <= 8
    assert states.is_done("huc8-test", "wqp")

    # resume: a run with the first batch already in the ledger never asks for those stations
    asked.clear()
    ledger = state.Ledger(root, "wqp-results-huc8-test")
    parts = st.common.parts_dir(root, "huc8-test", "wqp_results")
    st.common.write_part(parts, st.batch_key(0, 5), {"rows": []})
    ledger.add(st.batch_key(0, 5))
    st.run_wqp(root, chunk, states, progress, control, fetch=results, fetch_stations_fn=stations, force=True)
    first_five = {s["station"] for s in listed[:5]}
    assert not any(set(b) & first_five for b in asked) and sum(len(b) for b in asked) == 7
