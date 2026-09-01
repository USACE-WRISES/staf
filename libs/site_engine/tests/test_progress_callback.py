"""The progress callback reports stages and never touches the record."""
from __future__ import annotations

import json

from site_engine import engine
from test_engine_determinism import _wire


def test_progress_events_and_record_unchanged(monkeypatch):
    _wire(monkeypatch)
    events: list[dict] = []
    a = engine.compute_site(40.3112, -83.0561, progress=events.append)
    b = engine.compute_site(40.3112, -83.0561)
    assert a["status"] == "ok"
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    stages = [e["stage"] for e in events]
    assert stages[0] == "site" and stages[-1] == "done"
    assert "walk" in stages and "metrics" in stages
    walk = [e for e in events if e["stage"] == "walk"]
    assert all(walk[i]["reaches"] <= walk[i + 1]["reaches"]
               for i in range(len(walk) - 1))
    families = [e["family"] for e in events
                if e["stage"] == "metrics" and e.get("family")]
    assert families == sorted(families) and families


def test_raising_callback_does_not_break_the_run(monkeypatch):
    _wire(monkeypatch)

    def bad(event):
        raise RuntimeError("ui gone")
    rec = engine.compute_site(40.3112, -83.0561, progress=bad)
    assert rec["status"] == "ok"
