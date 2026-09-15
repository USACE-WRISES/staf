"""The panel's worker phases and the safe-to-shut-down verdict."""
from __future__ import annotations

import json
import os

from builder import state
from builder.paths import DataRoot


def _heartbeat(root, **fields):
    payload = {"pid": os.getpid(), "unit": "state-VA", "stage": "wqp", "message": "", **fields}
    (root.state / "progress.json").write_text(json.dumps(payload), encoding="utf-8")


def test_phases_follow_the_pid_and_the_control_file(tmp_path, monkeypatch):
    root = DataRoot(tmp_path / "data").ensure()
    assert state.worker_state(root)["phase"] == "idle" and state.worker_state(root)["safe"]

    _heartbeat(root)                                     # our own pid: alive
    running = state.worker_state(root)
    assert running["phase"] == "running" and not running["safe"]
    state.Control(root).request("pause")
    pausing = state.worker_state(root)
    assert pausing["phase"] == "pausing" and not pausing["safe"]
    assert "15 minutes" in pausing["text"]               # the WQP stage warns about the slow request

    monkeypatch.setattr(state, "pid_alive", lambda pid: False)   # the worker exited
    _heartbeat(root, message="paused")
    paused = state.worker_state(root)
    assert paused["phase"] == "paused" and paused["safe"]
    _heartbeat(root, message="failed: {'job': 'chunk'}")
    assert state.worker_state(root)["phase"] == "failed"
    _heartbeat(root, message="queue empty")
    assert state.worker_state(root)["phase"] == "idle"


def test_pid_alive_knows_this_process_and_a_dead_one():
    assert state.pid_alive(os.getpid())
    assert not state.pid_alive(2 ** 22 + 12345)
    assert not state.pid_alive(None)


def test_state_run_jobs_are_tagged_so_shared_jobs_survive_the_queue(tmp_path, monkeypatch):
    from builder import worker
    from builder.units import Chunk
    root = DataRoot(tmp_path / "data").ensure()

    def fake_chunk(root_, kind, value):
        vpus = {"MT": ["10U"], "WY": ["10L", "10U"]}[value.upper()]
        return Chunk(id=f"state-{value.upper()}", kind="state", label=value, huc8s=[], vpus=vpus)

    monkeypatch.setattr(worker, "_chunk_for", fake_chunk)
    # the trap the tag exists for: identical untagged items are dropped
    state.Queue(root).append([{"job": "stage"}, {"job": "publish"}, {"job": "stage"}])
    assert [i["job"] for i in state.Queue(root).read()] == ["stage", "publish"]
    state.Queue(root).clear()

    worker.queue_state_run(root, "mt")
    worker.queue_state_run(root, "WY")
    items = state.Queue(root).read()
    assert [i["job"] for i in items] == ["chunk", "tiles", "stage", "publish",
                                         "chunk", "tiles", "tiles", "stage", "publish"]
    assert [i["for"] for i in items] == ["MT"] * 4 + ["WY"] * 5
    assert sum(1 for i in items if i["job"] == "tiles" and i["vpu"] == "10U") == 2
    assert items[0] == {"job": "chunk", "kind": "state", "value": "MT", "for": "MT"}
    assert worker.job_label(items[0]) == "Chunk state MT"
    assert worker.job_label(items[1]) == "Tiles 10U (for MT)"
    assert worker.job_label(items[3]) == "Publish (for MT)"
    # queueing a state twice adds nothing
    worker.queue_state_run(root, "MT")
    assert len(state.Queue(root).read()) == 9
