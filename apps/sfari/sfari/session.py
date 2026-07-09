"""Resumable-session serialization.

Serializes a whole SFARI assessment (delineation + per-metric Likert/notes +
per-function 0-15 scores + pulled evidence + cross-section) to a single JSON file
so a field visit can be paused and resumed. Photos are not captured in v1 (a
later addition), but all scores, notes, and evidence round-trip.
"""
from __future__ import annotations

import json

SCHEMA_VERSION = 1


def dump(delineation: dict, metric_scores: dict, function_scores: dict,
         evidence: dict, cross_section=None) -> str:
    """Serialize the assessment reactive state to a JSON string."""
    return json.dumps({
        "schemaVersion": SCHEMA_VERSION,
        "method": "SFARI",
        "delineation": delineation or {},
        "metric_scores": metric_scores or {},
        "function_scores": function_scores or {},
        "evidence": evidence or {},
        "cross_section": cross_section,
    }, indent=2, ensure_ascii=False)


def load(text: str) -> dict:
    """Parse a saved assessment. Returns the state dict (missing keys default empty)."""
    d = json.loads(text)
    function_scores = d.get("function_scores", {})
    for rec in function_scores.values():
        if isinstance(rec, dict):
            rec.pop("na", None)   # function-level N/A was removed; only metrics can be N/A
    return {
        "delineation": d.get("delineation", {}),
        "metric_scores": d.get("metric_scores", {}),
        "function_scores": function_scores,
        "evidence": d.get("evidence", {}),
        "cross_section": d.get("cross_section"),
    }
