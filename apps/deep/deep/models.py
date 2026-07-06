"""Data models for a DEEP assessment run: measured values + per-function results.

These are the analogues of SFARI's ``MetricScore`` / ``FunctionScore``, but the
input is a *measured value* (later scored by a curve) rather than a Likert pick.
Kept as plain dataclasses with ``to_dict`` / ``from_dict`` for session save/load.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MeasuredValue:
    """One metric's measured value at a site.

    ``value is None`` or ``na`` means the metric was not measured / Not Applicable
    and is excluded from its function's mean. ``origin`` distinguishes a
    desktop-computed value from a field entry; ``source`` is a provenance label.
    """
    metric_id: str
    value: Optional[float] = None
    origin: str = "field"          # "desktop" | "field"
    source: str = ""               # e.g. "EPA StreamCat"
    note: str = ""
    na: bool = False
    stratum: Optional[str] = None   # chosen curve-layer stratum (multi-stratum metrics)

    @property
    def is_scored(self) -> bool:
        return (not self.na) and self.value is not None

    def to_dict(self) -> dict:
        return {
            "metricId": self.metric_id,
            "value": self.value,
            "origin": self.origin,
            "source": self.source,
            "note": self.note,
            "na": self.na,
            "stratum": self.stratum,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MeasuredValue":
        return cls(
            metric_id=d["metricId"],
            value=d.get("value"),
            origin=d.get("origin", "field"),
            source=d.get("source", ""),
            note=d.get("note", ""),
            na=bool(d.get("na", False)),
            stratum=d.get("stratum"),
        )


@dataclass
class FunctionResult:
    """Scoring outcome for one function: its metric indices and the 0-15 score."""
    function_id: str
    metric_indices: dict[str, Optional[float]] = field(default_factory=dict)
    score: Optional[float] = None   # 0-15; None when NA (no metric scored)
    na: bool = True

    def to_dict(self) -> dict:
        return {
            "functionId": self.function_id,
            "metricIndices": self.metric_indices,
            "score": self.score,
            "na": self.na,
        }
