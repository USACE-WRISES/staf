"""Versioned, JSON-serializable contracts for the EASI batch engine.

These dataclasses are the shared vocabulary between the batch runner, the
qualification model, the exports, and any consumer (the EASI batch UI or the
vendored StreamCurves screening step). Every top-level type round-trips through
``to_dict``/``from_dict`` so a batch can be serialized to JSON and back.

Design notes:
- Coordinates normalize to 6 decimals for exact-result reuse (``SiteRequest.key``).
- Raw (unrounded) ECI/sub-indices are kept for qualification alongside 2-decimal
  display values (``SiteResult``).
- Failures are structured ``Issue`` records, not free text alone.
- ``Completeness`` counts defaulted evidence separately from genuinely unavailable
  evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

CONTRACTS_SCHEMA_VERSION = 1

# Per-site processing lifecycle.
PROCESSING_STATES = ("queued", "running", "succeeded", "partial", "failed", "cancelled")

# Automatic qualification decisions and final (post-review) decisions, kept separate.
AUTO_DECISIONS = ("qualified", "excluded", "not_evaluable")
FINAL_DECISIONS = ("retained", "excluded", "pending")


# --- request side ---------------------------------------------------------- #
@dataclass
class SiteRequest:
    """One point location to assess. ``site_id`` may be supplied or generated."""
    site_id: str
    lat: float
    lon: float
    comid: Optional[int] = None
    reach_length_ft: float = 1000.0
    snap_tolerance_ft: float = 150.0
    source_choices: dict[str, str] = field(default_factory=dict)
    overrides: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def key(self) -> tuple:
        """Normalized computation key: identical keys may share computation.

        Different ``site_id``s at the same normalized location still each receive a
        distinct ``SiteResult`` (the runner copies the shared computation per id).
        """
        return (round(float(self.lat), 6), round(float(self.lon), 6),
                self.comid, round(float(self.reach_length_ft), 1),
                tuple(sorted((self.source_choices or {}).items())))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SiteRequest":
        return cls(
            site_id=str(d["site_id"]), lat=float(d["lat"]), lon=float(d["lon"]),
            comid=d.get("comid"),
            reach_length_ft=float(d.get("reach_length_ft", 1000.0)),
            snap_tolerance_ft=float(d.get("snap_tolerance_ft", 150.0)),
            source_choices=dict(d.get("source_choices") or {}),
            overrides=dict(d.get("overrides") or {}),
            metadata=dict(d.get("metadata") or {}))


@dataclass
class BatchConfig:
    """Batch-wide defaults captured as a snapshot in the request and result."""
    metric_ids: Optional[list[str]] = None       # None = all registered metrics
    reach_length_ft: float = 1000.0
    snap_tolerance_ft: float = 150.0
    source_choices: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BatchConfig":
        return cls(
            metric_ids=list(d["metric_ids"]) if d.get("metric_ids") is not None else None,
            reach_length_ft=float(d.get("reach_length_ft", 1000.0)),
            snap_tolerance_ft=float(d.get("snap_tolerance_ft", 150.0)),
            source_choices=dict(d.get("source_choices") or {}))


@dataclass
class BatchRequest:
    sites: list[SiteRequest]
    config: BatchConfig = field(default_factory=BatchConfig)
    criteria: Optional[dict] = None              # serialized qualification rule
    schema_version: int = CONTRACTS_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version,
                "config": self.config.to_dict(),
                "criteria": self.criteria,
                "sites": [s.to_dict() for s in self.sites]}

    @classmethod
    def from_dict(cls, d: dict) -> "BatchRequest":
        return cls(
            sites=[SiteRequest.from_dict(s) for s in d.get("sites", [])],
            config=BatchConfig.from_dict(d.get("config") or {}),
            criteria=d.get("criteria"),
            schema_version=int(d.get("schema_version", CONTRACTS_SCHEMA_VERSION)))


# --- result side ----------------------------------------------------------- #
@dataclass
class Issue:
    """A structured, stable-coded problem (never free text alone)."""
    code: str                      # stable slug, e.g. "delineation_failed"
    severity: str = "warning"      # info | warning | error
    stage: str = ""                # validation|snap|delineation|prefetch|metrics|report|qualify
    source: str = ""               # external service or module, when relevant
    site_id: str = ""
    metric_id: str = ""
    retryable: bool = False
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Issue":
        return cls(**{k: d.get(k, getattr(cls, k, "")) for k in
                      ("code", "severity", "stage", "source", "site_id",
                       "metric_id", "retryable", "message")})


@dataclass
class Completeness:
    """Evidence tally. ``defaulted`` (a documented screening default was used) is
    counted separately from ``unavailable`` (genuinely no data)."""
    total: int = 0
    computed: int = 0
    defaulted: int = 0
    unavailable: int = 0
    overridden: int = 0
    excluded: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Completeness":
        return cls(**{k: int(d.get(k, 0)) for k in
                      ("total", "computed", "defaulted", "unavailable",
                       "overridden", "excluded")})


@dataclass
class MetricRecord:
    """One metric's result, extended with band/availability/missing-reason."""
    metric_id: str
    name: str = ""
    discipline: str = ""
    function_id: str = ""
    function_name: str = ""
    scale: Optional[str] = None
    confidence: str = "L"
    generated_rating: Optional[str] = None
    final_rating: Optional[str] = None
    index: Optional[float] = None
    function_score: Optional[int] = None
    band: str = ""
    value_text: str = ""
    source: str = ""
    source_mode: str = ""           # chosen source option, when multi-source
    status: str = ""                # ok|unavailable|excluded|pending|override
    availability: str = ""          # available|unavailable|excluded|pending
    missing_reason: str = ""
    overrideable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MetricRecord":
        known = {f for f in cls.__dataclass_fields__}          # noqa: E1101
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class DelineationSummary:
    comid: Optional[int] = None
    gnis_name: str = ""
    huc8: Optional[str] = None
    huc12: Optional[str] = None
    drainage_area_sqkm: Optional[float] = None
    watershed_area_sqkm: Optional[float] = None
    snapped_lat: Optional[float] = None
    snapped_lon: Optional[float] = None
    reach_length_ft: Optional[float] = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DelineationSummary":
        known = {f for f in cls.__dataclass_fields__}          # noqa: E1101
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


@dataclass
class Qualification:
    """Automatic and final decisions kept separate; reviewer overrides audited."""
    auto: str = "not_evaluable"          # AUTO_DECISIONS
    final: str = "pending"               # FINAL_DECISIONS
    criteria_id: str = ""                # preset name or "custom"
    reasons: list[str] = field(default_factory=list)
    partial_evidence: bool = False       # qualified while evidence incomplete
    reviewer: str = ""
    reviewer_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Qualification":
        known = {f for f in cls.__dataclass_fields__}          # noqa: E1101
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


@dataclass
class SiteResult:
    site_id: str
    state: str = "queued"
    input: dict = field(default_factory=dict)
    delineation: DelineationSummary = field(default_factory=DelineationSummary)
    metrics: list[MetricRecord] = field(default_factory=list)
    raw_eci: Optional[float] = None
    raw_sub_indices: dict[str, float] = field(default_factory=dict)
    eci: Optional[float] = None                     # 2-decimal display
    sub_indices: dict[str, float] = field(default_factory=dict)
    function_scores: dict[str, int] = field(default_factory=dict)
    completeness: Completeness = field(default_factory=Completeness)
    issues: list[Issue] = field(default_factory=list)
    qualification: Qualification = field(default_factory=Qualification)
    revision: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "site_id": self.site_id, "state": self.state, "input": self.input,
            "delineation": self.delineation.to_dict(),
            "metrics": [m.to_dict() for m in self.metrics],
            "raw_eci": self.raw_eci, "raw_sub_indices": self.raw_sub_indices,
            "eci": self.eci, "sub_indices": self.sub_indices,
            "function_scores": self.function_scores,
            "completeness": self.completeness.to_dict(),
            "issues": [i.to_dict() for i in self.issues],
            "qualification": self.qualification.to_dict(),
            "revision": self.revision,
            # ``_``-prefixed metadata (e.g. the heavy per-site artifact source) is
            # private and kept out of the compact serialization.
            "metadata": {k: v for k, v in self.metadata.items()
                         if not str(k).startswith("_")},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SiteResult":
        return cls(
            site_id=str(d["site_id"]), state=d.get("state", "queued"),
            input=dict(d.get("input") or {}),
            delineation=DelineationSummary.from_dict(d.get("delineation") or {}),
            metrics=[MetricRecord.from_dict(m) for m in d.get("metrics", [])],
            raw_eci=d.get("raw_eci"),
            raw_sub_indices=dict(d.get("raw_sub_indices") or {}),
            eci=d.get("eci"), sub_indices=dict(d.get("sub_indices") or {}),
            function_scores=dict(d.get("function_scores") or {}),
            completeness=Completeness.from_dict(d.get("completeness") or {}),
            issues=[Issue.from_dict(i) for i in d.get("issues", [])],
            qualification=Qualification.from_dict(d.get("qualification") or {}),
            revision=int(d.get("revision", 0)),
            metadata=dict(d.get("metadata") or {}))


@dataclass
class BatchResult:
    sites: list[SiteResult] = field(default_factory=list)
    config: BatchConfig = field(default_factory=BatchConfig)
    criteria: Optional[dict] = None
    diagnostics: dict = field(default_factory=dict)
    generated_ids: dict[str, str] = field(default_factory=dict)   # requested->assigned
    schema_version: int = CONTRACTS_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "config": self.config.to_dict(),
            "criteria": self.criteria,
            "diagnostics": self.diagnostics,
            "generated_ids": self.generated_ids,
            "sites": [s.to_dict() for s in self.sites],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BatchResult":
        return cls(
            sites=[SiteResult.from_dict(s) for s in d.get("sites", [])],
            config=BatchConfig.from_dict(d.get("config") or {}),
            criteria=d.get("criteria"),
            diagnostics=dict(d.get("diagnostics") or {}),
            generated_ids=dict(d.get("generated_ids") or {}),
            schema_version=int(d.get("schema_version", CONTRACTS_SCHEMA_VERSION)))
