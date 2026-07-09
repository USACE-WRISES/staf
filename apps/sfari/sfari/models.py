"""Shared data types.

Only ``EvidenceResult`` is a formal type — it is what each desktop adapter in
``evidence.py`` returns (then serialized to a plain dict via ``to_dict``). The
assessment's live state is held as plain dicts in ``app.py`` and serialized by
``session.py``; their shapes are:

    metric_scores[metricId]   = {"likert": str|None, "note": str, "photos": [{"id","uri"}]}
    function_scores[functionId] = {"score": int|None, "note": str}
    evidence[metricId]        = EvidenceResult.to_dict()
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass
class EvidenceResult:
    """Pulled desktop evidence for one metric (supports scoring; not itself a score)."""
    metric_id: str
    value: Any = None
    value_text: str = ""
    suggested_likert: Optional[str] = None      # from autoSuggest vs likertCriteria
    confidence: str = "M"                        # H/M/L — confidence in the DATA
    source: str = ""
    source_url: str = ""
    status: str = "ok"                           # ok | unavailable
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceResult":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})
