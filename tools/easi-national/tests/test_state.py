"""Checkpoints, ledgers, control, queue, rates: the pause/resume machinery."""
from __future__ import annotations

import json

import pytest

from builder import state
from builder.paths import DataRoot


@pytest.fixture
def root(tmp_path):
    return DataRoot(tmp_path / "data").ensure()


def test_unit_states_round_trip_and_staleness(root):
    st = state.UnitStates(root)
    assert not st.is_done("chunk-a", "streamcat")
    st.set("chunk-a", "streamcat", "done", inputs="abc", note="12 s")
    assert st.is_done("chunk-a", "streamcat")
    assert st.is_done("chunk-a", "streamcat", "abc")
    assert not st.is_done("chunk-a", "streamcat", "other")     # inputs changed -> not done
    again = state.UnitStates(root)
    assert again.get("chunk-a", "streamcat")["status"] == "done"
    assert again.mark_stale("streamcat") == 1
    assert again.get("chunk-a", "streamcat")["status"] == "stale"
    with pytest.raises(ValueError):
        again.set("chunk-a", "streamcat", "bogus")


def test_ledger_survives_a_restart_and_skips_done_keys(root):
    ledger = state.Ledger(root, "nas-chunk-a")
    ledger.add("010203040506", n=3)
    ledger.add("010203040506", n=3)
    ledger.add("010203040507")
    assert len(ledger) == 2 and "010203040506" in ledger
    reopened = state.Ledger(root, "nas-chunk-a")
    assert reopened.pending(["010203040506", "010203040508"]) == ["010203040508"]
    reopened.clear()
    assert len(state.Ledger(root, "nas-chunk-a")) == 0


def test_control_raises_between_chunks(root):
    control = state.Control(root)
    control.check()
    control.request("pause")
    with pytest.raises(state.PauseRequested):
        control.check()
    control.request("cancel")
    with pytest.raises(state.CancelRequested):
        control.check()
    control.clear()
    control.check()


def test_queue_dedupes_and_pops_in_order(root):
    q = state.Queue(root)
    q.append([{"unit": "a", "stage": "streamcat"}, {"unit": "a", "stage": "geometry"}])
    q.append([{"unit": "a", "stage": "streamcat"}, {"unit": "b", "stage": "streamcat"}])
    assert [(i["unit"], i["stage"]) for i in q.read()] == [
        ("a", "streamcat"), ("a", "geometry"), ("b", "streamcat")]
    assert q.pop_front()["stage"] == "streamcat"
    assert len(q.read()) == 2
    q.clear()
    assert q.pop_front() is None


def test_progress_heartbeat_and_rates(root):
    progress = state.Progress(root)
    progress.begin("chunk-a", "nas", total=10, message="starting")
    progress.tick(done=5)
    progress.write(force=True)
    data = state.read_progress(root)
    assert data["unit"] == "chunk-a" and data["done"] == 5 and data["total"] == 10
    progress.say("hello")
    assert "hello" in json.dumps(state.read_progress(root)["log"])
    rates = state.Rates(root)
    rates.record("nas", items=100, seconds=25.0)
    rates.record("nas", items=100, seconds=35.0)
    assert rates.per_item("nas", default=1.0) == pytest.approx(0.3)
    assert rates.per_item("wqp", default=7.0) == 7.0


def test_digest_is_stable_for_equal_inputs():
    assert state.digest("streamcat", ["a", "b"], {"x": 1}) == state.digest("streamcat", ["a", "b"], {"x": 1})
    assert state.digest("streamcat", ["a"]) != state.digest("streamcat", ["b"])


def test_bandwidth_flush_outlives_a_concurrent_reader(tmp_path):
    """On Windows a reader holding the file open refuses the rename; the
    flush retries until the reader lets go instead of failing the stage."""
    import threading
    import time
    from builder.paths import DataRoot
    root = DataRoot(tmp_path / "data").ensure()
    counter = state.Bandwidth(root, budget_bytes=1e12, flush_every=1)
    counter.add(10)                                   # the file exists
    hold = threading.Event()

    def reader():
        with open(root.bandwidth, "r", encoding="utf-8") as fh:
            hold.set()
            time.sleep(0.4)
            fh.read()
    t = threading.Thread(target=reader)
    t.start()
    hold.wait()
    counter.add(20)                                   # flushes while the reader holds the file
    t.join()
    assert counter.month_bytes() == 30
