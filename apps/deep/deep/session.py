"""Resumable-session serialization for DEEP.

Serializes a whole DEEP run — the delineation, *which* assessment definition was
used (inlined with its curves so the file resumes standalone, no registry
needed), and the per-metric measured values — to a single JSON file so a
field/desk session can be paused and resumed. Scores are recomputed on load
(not trusted from the file).
"""
from __future__ import annotations

import json

SCHEMA_VERSION = 1


def dump(delineation: dict, assessment: dict, measured_values: dict) -> str:
    """Serialize the run state to a JSON string.

    ``assessment`` is the loaded assessment dict (metricsByFunction with inlined
    curves) so a resumed session does not depend on the predefined registry.
    """
    return json.dumps({
        "schemaVersion": SCHEMA_VERSION,
        "method": "DEEP",
        "delineation": delineation or {},
        "assessment": assessment or {},
        "measured_values": measured_values or {},
    }, indent=2, ensure_ascii=False)


def load(text: str) -> dict:
    """Parse a saved run. Returns the state dict (missing keys default empty)."""
    d = json.loads(text)
    return {
        "delineation": d.get("delineation", {}),
        "assessment": d.get("assessment", {}),
        "measured_values": d.get("measured_values", {}),
    }
