"""WQP acquisition: row normalization and the split-on-failure fetch."""
from __future__ import annotations

import csv
import io

import pytest

from builder.stages import wqp as wqp_stage


def _csv(rows):
    fields = ["ResultIdentifier", "MonitoringLocationIdentifier", "ActivityStartDate",
              "ResultMeasureValue", "ResultMeasure/MeasureUnitCode", "ResultSampleFractionText",
              "ResultStatusIdentifier", "ResultDetectionConditionText", "LatitudeMeasure",
              "LongitudeMeasure"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for r in rows:
        w.writerow({f: r.get(f, "") for f in fields})
    return buf.getvalue().encode("utf-8")


def test_normalize_rows_applies_the_app_exclusion_order():
    rows = [
        {"ResultIdentifier": "1", "MonitoringLocationIdentifier": "A", "ResultMeasureValue": "",
         "ResultSampleFractionText": "Total"},
        {"ResultIdentifier": "2", "MonitoringLocationIdentifier": "A", "ResultMeasureValue": "1.0",
         "ResultSampleFractionText": "Total", "ResultStatusIdentifier": "Rejected"},
        {"ResultIdentifier": "3", "MonitoringLocationIdentifier": "A", "ResultMeasureValue": "1.0",
         "ResultSampleFractionText": "Total", "ResultDetectionConditionText": "Not Detected"},
        {"ResultIdentifier": "4", "MonitoringLocationIdentifier": "A", "ResultMeasureValue": "1.0",
         "ResultSampleFractionText": "Dissolved"},
        {"ResultIdentifier": "5", "MonitoringLocationIdentifier": "A", "ResultMeasureValue": "1.0",
         "ResultSampleFractionText": "Total", "ResultMeasure/MeasureUnitCode": "furlongs"},
        {"ResultIdentifier": "6", "MonitoringLocationIdentifier": "A", "ResultMeasureValue": "abc",
         "ResultSampleFractionText": "Total", "ResultMeasure/MeasureUnitCode": "mg/l"},
        {"ResultIdentifier": "7", "MonitoringLocationIdentifier": "", "ResultMeasureValue": "250",
         "ResultSampleFractionText": "Total", "ResultMeasure/MeasureUnitCode": "ug/l",
         "ActivityStartDate": "2021-03-04", "LatitudeMeasure": "38.0", "LongitudeMeasure": "-78.5"},
    ]
    out = wqp_stage.normalize_rows("tn", _csv(rows).decode("utf-8"))
    reasons = [r["reason"] for r in out]
    assert reasons == ["blank", "rejected", "censored", "non_total_fraction",
                       "unsupported_unit", "nonnumeric", "ok"]
    assert out[-1]["value"] == pytest.approx(0.25) and out[-1]["date"] == "2021-03-04"
    assert out[-1]["station"] == "unknown-station"


def test_dense_cells_split_before_fetching_and_empty_cells_skip_it():
    fetched, counted = [], []

    def station_count(param, bbox, start):
        counted.append(bbox)
        width = bbox[2] - bbox[0]
        if width > 0.6:
            return 900                     # a metropolitan cell: split it
        if bbox[1] >= 37.5 and bbox[0] >= -78.5:
            return 0                       # the north-east quarter is empty
        return 40

    def fetch(param, bbox, start):
        fetched.append(bbox)
        return _csv([{"ResultIdentifier": f"{bbox[0]:.2f}-{bbox[1]:.2f}", "MonitoringLocationIdentifier": "S",
                      "ResultMeasureValue": "1", "ResultSampleFractionText": "Total",
                      "ResultMeasure/MeasureUnitCode": "mg/l"}])

    rows = wqp_stage.fetch_cell_rows("tn", [-79.0, 37.0, -78.0, 38.0], "01-01-2016", fetch,
                                     station_count=station_count, sleep=lambda s: None)
    assert len(counted) == 5                # the whole cell, then its four quarters
    assert len(fetched) == 3 and len(rows) == 3   # three quarters fetched, the empty one skipped


def test_a_failing_cell_is_retried_then_split_into_quarters():
    calls, naps = [], []

    def fetch(param, bbox, start):
        calls.append(bbox)
        width = bbox[2] - bbox[0]
        if width > 0.3:
            raise TimeoutError("too big")
        return _csv([{"ResultIdentifier": f"{bbox[0]:.2f}", "MonitoringLocationIdentifier": "S",
                      "ResultMeasureValue": "1", "ResultSampleFractionText": "Total",
                      "ResultMeasure/MeasureUnitCode": "mg/l"}])

    rows = wqp_stage.fetch_cell_rows("tn", [-79.0, 37.0, -78.0, 38.0], "01-01-2016", fetch,
                                     sleep=naps.append, station_count=None)
    # the whole cell and the 4 halves each fail 3 times (2 pauses each), then the
    # 16 quarter cells (0.25 wide) answer on their first try
    assert len(calls) == 3 * (1 + 4) + 16 and len(rows) == 16
    assert naps == list(wqp_stage.RETRY_DELAYS_S) * 5

    flaky = {"n": 0}

    def flaky_fetch(param, bbox, start):
        flaky["n"] += 1
        if flaky["n"] == 1:
            raise ConnectionError("Remote end closed connection")
        return _csv([{"ResultIdentifier": "1", "MonitoringLocationIdentifier": "S",
                      "ResultMeasureValue": "1", "ResultSampleFractionText": "Total",
                      "ResultMeasure/MeasureUnitCode": "mg/l"}])

    rows = wqp_stage.fetch_cell_rows("tn", [-79.0, 37.0, -78.0, 38.0], "01-01-2016", flaky_fetch,
                                     sleep=lambda s: None, station_count=None)
    assert len(rows) == 1 and flaky["n"] == 2          # a transient reset is retried, not split

    def always_fails(param, bbox, start):
        raise TimeoutError("down")

    with pytest.raises(RuntimeError, match="keeps failing"):
        wqp_stage.fetch_cell_rows("tn", [-79.0, 37.0, -78.0, 38.0], "01-01-2016", always_fails,
                                  sleep=lambda s: None, station_count=None)


def test_a_slow_cell_is_split_at_once_and_pause_interrupts_a_request(monkeypatch, tmp_path):
    import time
    from builder import config, state
    from builder.paths import DataRoot
    monkeypatch.setattr(wqp_stage, "CONTROL_POLL_S", 0.02)
    monkeypatch.setattr(config, "WQP_TIMEOUT_S", 0.06)
    calls = []

    def slow_when_wide(param, bbox, start):
        calls.append(bbox)
        if bbox[2] - bbox[0] > 0.3:
            time.sleep(0.5)                       # never answers within the timeout
        return _csv([{"ResultIdentifier": f"{bbox[0]:.2f}", "MonitoringLocationIdentifier": "S",
                      "ResultMeasureValue": "1", "ResultSampleFractionText": "Total",
                      "ResultMeasure/MeasureUnitCode": "mg/l"}])

    rows = wqp_stage.fetch_cell_rows("tn", [-79.0, 37.0, -78.0, 38.0], "01-01-2016", slow_when_wide,
                                     sleep=lambda s: None, station_count=None)
    assert len(rows) == 16 and len(calls) == 1 + 4 + 16     # timeouts split, never retry whole

    root = DataRoot(tmp_path / "data").ensure()
    control = state.Control(root)
    control.request("pause")

    def never(param, bbox, start):
        time.sleep(1.0)
        return b""

    started = time.monotonic()
    with pytest.raises(state.PauseRequested):
        wqp_stage.fetch_cell_rows("tn", [-79.0, 37.0, -78.0, 38.0], "01-01-2016", never,
                                  sleep=lambda s: None, control=control, station_count=None)
    assert time.monotonic() - started < 0.5                   # within a poll, not a request


def test_run_wqp_fetches_cells_concurrently_and_resumes_from_the_ledger(tmp_path, monkeypatch):
    import threading
    import time
    from builder import config, state
    from builder.paths import DataRoot
    from builder.units import Chunk
    monkeypatch.setattr(config, "WQP_CONCURRENCY", 3)
    monkeypatch.setattr(wqp_stage, "CELL_DEG", 0.5)
    root = DataRoot(tmp_path / "data").ensure()
    chunk = Chunk(id="huc8-test", kind="huc8", label="test", huc8s=["02080204"],
                  bbox=[-79.0, 37.0, -78.0, 38.0])
    chunk.save(root)
    in_flight, peak = {"n": 0, "peak": 0}, threading.Lock()

    def fetch(param, bbox, start):
        with peak:
            in_flight["n"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["n"])
        time.sleep(0.05)
        with peak:
            in_flight["n"] -= 1
        return _csv([{"ResultIdentifier": f"{param}-{bbox[0]:.2f}-{bbox[1]:.2f}",
                      "MonitoringLocationIdentifier": "S", "ResultMeasureValue": "1",
                      "ResultSampleFractionText": "Total", "ResultMeasure/MeasureUnitCode": "mg/l"}])

    monkeypatch.setattr(wqp_stage, "fetch_station_count", lambda *a, **k: 10)
    states, progress, control = state.UnitStates(root), state.Progress(root), state.Control(root)
    # pre-mark two cells done in the ledger: they must not be fetched again
    ledger = state.Ledger(root, "wqp-huc8-test")
    parts = wqp_stage.common.parts_dir(root, "huc8-test", "wqp")
    for key in ("tn-c000", "tp-c000"):
        wqp_stage.common.write_part(parts, key, {"rows": []})
        ledger.add(key)
    wqp_stage.run_wqp(root, chunk, states, progress, control, fetch=fetch)
    assert in_flight["peak"] >= 2                     # cells really ran side by side
    assert states.is_done("huc8-test", "wqp")
    import pyarrow.parquet as pq
    tn = pq.read_table(root.chunk_raw("huc8-test", "wqp_tn")).to_pylist()
    assert len(tn) == 3 and not any(r["result_id"].endswith("-79.00-37.00") for r in tn)


def test_start_date_is_ten_years_back():
    from datetime import date
    assert wqp_stage.start_date(date(2026, 9, 10)) == "09-10-2016"
    assert wqp_stage.start_date(date(2028, 2, 29)) == "02-28-2018"
