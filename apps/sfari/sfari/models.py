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
    field_value_text: str = ""                   # concise self-identifying print value ("Impervious 12.3%")
    suggested_likert: Optional[str] = None      # from autoSuggest vs likertCriteria
    confidence: str = "M"                        # H/M/L — confidence in the DATA
    source: str = ""
    source_url: str = ""
    status: str = "ok"                           # ok | unavailable | pending
    note: str = ""
    # Provenance of the entry itself: which engine or service produced it.
    #   "engine"    the STAF site engine (HR reach watershed)
    #   "streamcat" the StreamCat lookup engine by NHDPlus V2 COMID: the EPA
    #               modeled indices that exist only per V2 reach, and the
    #               labeled stand-in for a watershed value the site engine has
    #               no value for (evidence.py, 2026-09-07)
    #   "pull"      other direct services (NWIS, WQP, NWI, NID, TIGER, NHD VAAs)
    # Cross-section attach entries keep their source-string convention.
    # Additive with safe defaults so saved sessions round-trip unchanged.
    origin: str = "pull"
    engine_version: Optional[str] = None
    # Set on every StreamCat entry: the reach the value describes on a stream
    # outside NHDPlus V2 (empty on a covered reach) and why StreamCat stood in
    # for the engine (empty on the COMID-only indices). ``upgrade_pending`` is
    # legacy (sessions saved before 2026-09-05): never set and never read now.
    anchor_label: str = ""
    fallback_reason: str = ""
    upgrade_pending: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceResult":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})
