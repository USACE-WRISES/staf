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
    #   "engine"    the STAF site engine (exact watershed)
    #   "streamcat" the StreamCat lookup engine (COMID-keyed StreamCat values)
    #   "pull"      other direct services (NWIS, WQP, NWI, NID, TIGER, NHD VAAs)
    # Cross-section attach entries keep their source-string convention.
    # Additive with safe defaults so saved sessions round-trip unchanged.
    origin: str = "pull"
    engine_version: Optional[str] = None
    # The reach a COMID-keyed value describes on a site outside NHDPlus V2
    # ("nearest covered reach, COMID x, N ft downstream, DA ratio r"); empty
    # on covered sites and for values that are not COMID-keyed.
    anchor_label: str = ""
    # Set when a StreamCat value stands in because the site engine failed or
    # refused: the plain reason, shown beside the value.
    fallback_reason: str = ""
    # True while the site engine is still running on a covered site and this
    # StreamCat value will be replaced by the exact-watershed value.
    upgrade_pending: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceResult":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})
