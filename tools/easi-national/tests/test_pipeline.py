"""The run-stage wrapper and the queue-driven worker loop."""
from __future__ import annotations

import pytest

from builder import state, worker
from builder.paths import DataRoot
from builder.stages import common


@pytest.fixture
def root(tmp_path):
    return DataRoot(tmp_path / "data").ensure()


def test_run_stage_skips_done_work_and_records_pause_failure(root):
    states, progress = state.UnitStates(root), state.Progress(root)
    calls = []
    assert common.run_stage(states, "u", "s", "in1", lambda: calls.append(1), progress)
    assert calls == [1] and states.is_done("u", "s", "in1")
    assert not common.run_stage(states, "u", "s", "in1", lambda: calls.append(2), progress)
    assert calls == [1]                                   # same inputs: a no-op
    assert common.run_stage(states, "u", "s", "in2", lambda: calls.append(3), progress)
    assert calls == [1, 3]                                # changed inputs: reruns

    def pause():
        raise state.PauseRequested()
    with pytest.raises(state.PauseRequested):
        common.run_stage(states, "u", "p", "x", pause, progress)
    assert states.get("u", "p")["status"] == "pending" and states.get("u", "p")["note"] == "paused"

    def boom():
        raise RuntimeError("service down")
    with pytest.raises(RuntimeError):
        common.run_stage(states, "u", "f", "x", boom, progress)
    assert states.get("u", "f")["status"] == "failed" and "service down" in states.get("u", "f")["note"]


def test_drain_queue_runs_jobs_in_order_and_keeps_a_paused_job(root, monkeypatch):
    ran = []

    def fake_run_job(root_, job, states, progress, control):
        ran.append(job["job"])
        if job["job"] == "pauses":
            raise state.PauseRequested()

    monkeypatch.setattr(worker, "run_job", fake_run_job)
    q = state.Queue(root)
    q.append([{"job": "a"}, {"job": "pauses"}, {"job": "b"}])
    assert worker.drain_queue(root) == 2                  # paused
    assert ran == ["a", "pauses"]
    assert [j["job"] for j in q.read()] == ["pauses", "b"]   # the paused job stays in front
    assert worker.drain_queue(root) == 2                  # still pauses (fake keeps raising)
    q.write([{"job": "b"}])
    assert worker.drain_queue(root) == 0
    assert ran[-1] == "b" and q.read() == []


def test_bbox_helpers():
    grown = common.buffer_bbox([-79.0, 37.6, -78.0, 38.5], 10.0)
    assert grown[0] < -79.0 and grown[1] < 37.6 and grown[2] > -78.0 and grown[3] > 38.5
    cells = common.bbox_grid([-79.0, 37.6, -77.5, 38.5], 1.0)
    assert len(cells) == 2 and cells[0][0] == -79.0 and cells[-1][2] == pytest.approx(-77.5)
    assert common.esri_bounds({"rings": [[[1, 2], [3, 4], [1, 4], [1, 2]]]}) == (1, 2, 3, 4)
    assert common.esri_bounds({"x": 5, "y": 6}) == (5, 6, 5, 6)
    assert common.esri_bounds({}) is None


def test_sampling_schedules_the_largest_huc8s_first(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from builder import pipeline
    from builder.paths import DataRoot
    root = DataRoot(tmp_path / "data").ensure()
    for huc8, n in (("02080201", 3), ("02080202", 30), ("02080203", 12)):
        root.huc8_dir(huc8).mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({"comid": pa.array(range(n), pa.int64())}), root.huc8_file(huc8, "derived"))
    assert pipeline.largest_first(root, ["02080201", "02080202", "02080209", "02080203"]) == [
        "02080202", "02080203", "02080201", "02080209"]
