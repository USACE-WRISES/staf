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
