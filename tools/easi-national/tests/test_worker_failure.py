"""A failed job goes back to the front of the queue with a failure count."""
from __future__ import annotations

from builder import state, worker
from builder.paths import DataRoot


def test_job_from_unit_and_hard_stop_requeue(tmp_path, monkeypatch):
    assert worker.job_from_unit("state-VA") == {"job": "chunk", "kind": "state", "value": "VA"}
    assert worker.job_from_unit("huc8-02080204") == {"job": "chunk", "kind": "huc8", "value": "02080204"}
    assert worker.job_from_unit("vpu-02") == {"job": "tiles", "vpu": "02", "force": True}
    assert worker.job_from_unit("staging") == {"job": "stage"}
    assert worker.job_from_unit("") is None
    root = DataRoot(tmp_path / "data").ensure()
    progress = state.Progress(root)
    progress.begin("state-VA", "wqp", total=120)
    monkeypatch.setattr(state, "pid_alive", lambda pid: False)   # nothing to kill in the test
    q = state.Queue(root)
    q.append([{"job": "tiles", "vpu": "02", "force": True}, {"job": "stage"}])
    state.Control(root).request("pause")
    result = worker.hard_stop(root)
    assert result["requeued"] == {"job": "chunk", "kind": "state", "value": "VA"}
    assert [j["job"] for j in q.read()] == ["chunk", "tiles", "stage"]
    assert state.Control(root).read() == {}
    assert state.worker_state(root)["phase"] == "paused" and state.worker_state(root)["safe"]


def test_failed_job_is_requeued_in_front(tmp_path, monkeypatch):
    root = DataRoot(tmp_path / "data").ensure()
    ran = []

    def fake_run_job(root_, job, states, progress, control):
        ran.append(job["job"])
        if job["job"] == "boom":
            raise RuntimeError("service down")

    monkeypatch.setattr(worker, "run_job", fake_run_job)
    q = state.Queue(root)
    q.append([{"job": "a"}, {"job": "boom"}, {"job": "b"}])
    assert worker.drain_queue(root) == 1
    assert ran == ["a", "boom"]
    items = q.read()
    assert [i["job"] for i in items] == ["boom", "b"]
    assert items[0]["failures"] == 1
    assert worker.drain_queue(root) == 1                  # still failing: count climbs
    assert q.read()[0]["failures"] == 2
