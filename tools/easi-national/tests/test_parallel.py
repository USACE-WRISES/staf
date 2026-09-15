"""Concurrency safety: the shared checkpoint file under many writers, and the
chunk driver's HUC8 grouping."""
from __future__ import annotations

import threading

import pytest

from builder import pipeline, state
from builder.paths import DataRoot
from builder.units import Chunk


def test_unit_states_survive_concurrent_writers(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()

    def writer(n):
        st = state.UnitStates(root)             # its own in-memory copy, like a pool child
        for i in range(15):
            st.set(f"huc8-{n}", "derive", "done", inputs=f"in{i}", note=str(i))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = state.UnitStates(root)
    for n in range(6):
        assert final.get(f"huc8-{n}", "derive")["status"] == "done"
        assert final.get(f"huc8-{n}", "derive")["inputs"] == "in14"
    assert not (root.state / "units.lock").exists()


def test_run_chunk_groups_huc8_stages_and_honours_the_sequential_path(tmp_path, monkeypatch):
    root = DataRoot(tmp_path / "data").ensure()
    chunk = Chunk(id="huc4-0208", kind="huc4", label="t", huc8s=["02080203", "02080204"])
    chunk.save(root)
    calls = []

    def fake(name):
        def run(root_, chunk_, huc8, states, progress, control, *, force=False):
            calls.append((name, huc8))
        return run

    monkeypatch.setattr(pipeline, "_huc8_stage_fn", lambda name: fake(name))
    monkeypatch.setattr(pipeline, "_chunk_stage_fn",
                        lambda name: (lambda *a, **k: calls.append((name, None))))
    states, progress, control = state.UnitStates(root), state.Progress(root), state.Control(root)
    pipeline.run_chunk(root, chunk, states, progress, control,
                       stages=["attains", "derive", "nas", "joins", "score"], workers=1)
    assert calls == [("attains", None),
                     ("derive", "02080203"), ("derive", "02080204"),
                     ("nas", None),
                     ("joins", "02080203"), ("score", "02080203"),
                     ("joins", "02080204"), ("score", "02080204")]


@pytest.mark.parametrize("selected,expected_stages", [
    (["score"], ["score"]),
    (["streamcat", "score"], ["streamcat", "score"]),
    (["joins"], ["joins"]),
    (["score", "joins"], ["joins", "score"]),
])
def test_run_chunk_executes_selected_group_members_once(tmp_path, monkeypatch, selected, expected_stages):
    root = DataRoot(tmp_path / "data").ensure()
    chunk = Chunk(id="state-VA", kind="state", label="Virginia",
                  huc8s=["02080203", "02080204"])
    calls = []

    def fake(name):
        def run(root_, chunk_, huc8, states, progress, control, *, force=False):
            calls.append((name, huc8))
        return run

    monkeypatch.setattr(pipeline, "_huc8_stage_fn", fake)
    monkeypatch.setattr(pipeline, "_chunk_stage_fn",
                        lambda name: (lambda *a, **k: calls.append((name, None))))
    pipeline.run_chunk(root, chunk, state.UnitStates(root), state.Progress(root), state.Control(root),
                       stages=selected, workers=1)
    expected = [("streamcat", None)] if "streamcat" in expected_stages else []
    expected += [(name, huc8) for huc8 in chunk.huc8s
                 for name in expected_stages if name != "streamcat"]
    assert calls == expected
