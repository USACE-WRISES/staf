"""Load DEEP detailed-assessment definitions (predefined registry + uploads).

A DEEP assessment selects, per STAF function, one or more metrics; each metric
carries an inlined reference curve (a list of ``{x, y}`` points) already resolved
to the assessment's source (e.g. a state SQT). The predefined registry in
``data/deep-assessments.json`` and an uploaded bundle share the same shape, so
both load through :class:`LoadedAssessment`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config


@dataclass
class LoadedAssessment:
    assessment_id: str
    assessment_name: str
    source_citation: str = ""
    state_code: str = ""
    state_name: str = ""
    applicability: str = ""
    metrics_by_function: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "LoadedAssessment":
        return cls(
            assessment_id=d.get("assessmentId", ""),
            assessment_name=d.get("assessmentName", d.get("assessmentId", "")),
            source_citation=d.get("sourceCitation", ""),
            state_code=d.get("stateCode", ""),
            state_name=d.get("stateName", ""),
            applicability=d.get("applicability", ""),
            metrics_by_function=d.get("metricsByFunction", []),
            raw=d,
        )

    @property
    def function_ids(self) -> list[str]:
        return [fn["functionId"] for fn in self.metrics_by_function]

    def metrics_for_function(self, function_id: str) -> list[dict]:
        for fn in self.metrics_by_function:
            if fn["functionId"] == function_id:
                return fn.get("metrics", [])
        return []

    def all_metrics(self) -> list[dict]:
        """Every (function, metric) pair — a metric assigned to N functions appears N times."""
        out: list[dict] = []
        for fn in self.metrics_by_function:
            out.extend(fn.get("metrics", []))
        return out


# --------------------------------------------------------------------------- #
# Registry (predefined) + upload
# --------------------------------------------------------------------------- #
def list_predefined() -> list[dict]:
    """Lightweight catalog for a picker: id, name, state, counts."""
    out = []
    for a in config.assessments():
        mbf = a.get("metricsByFunction", [])
        out.append(
            {
                "assessmentId": a["assessmentId"],
                "assessmentName": a.get("assessmentName", a["assessmentId"]),
                "stateCode": a.get("stateCode", ""),
                "sourceCitation": a.get("sourceCitation", ""),
                "functionCount": len([fn for fn in mbf if fn.get("metrics")]),
                "metricCount": sum(len(fn.get("metrics", [])) for fn in mbf),
            }
        )
    return out


def load_predefined(assessment_id: str) -> LoadedAssessment:
    registry = config.assessments_by_id()
    if assessment_id not in registry:
        raise KeyError(f"unknown assessment {assessment_id!r}")
    return LoadedAssessment.from_dict(registry[assessment_id])


def from_bundle(bundle: dict) -> LoadedAssessment:
    """Validate and load a user-uploaded assessment bundle (curves inlined)."""
    problems = validate_bundle(bundle)
    if problems:
        raise ValueError("invalid assessment bundle: " + "; ".join(problems))
    return LoadedAssessment.from_dict(bundle)


def validate_bundle(bundle: dict) -> list[str]:
    """Structural checks for an assessment (predefined or uploaded). Empty == OK."""
    problems: list[str] = []
    if not bundle.get("assessmentId"):
        problems.append("missing assessmentId")
    mbf = bundle.get("metricsByFunction")
    if not isinstance(mbf, list) or not mbf:
        problems.append("missing or empty metricsByFunction")
        return problems

    known_fids = set(config.functions_by_id())
    for fn in mbf:
        fid = fn.get("functionId")
        if fid not in known_fids:
            problems.append(f"unknown functionId {fid!r}")
        for m in fn.get("metrics", []):
            mid = m.get("metricId")
            if not mid:
                problems.append(f"metric missing metricId in function {fid!r}")
                continue
            point_sets = []
            if (m.get("curve") or {}).get("points"):
                point_sets.append(m["curve"]["points"])
            for layer in (m.get("curveLayers") or []):
                if layer.get("points"):
                    point_sets.append(layer["points"])
            if not point_sets:
                problems.append(f"metric {mid!r} has no curve points")
                continue
            bad = False
            for pts in point_sets:
                for p in pts:
                    y = p.get("y")
                    if y is None or not (0.0 <= float(y) <= 1.0):
                        problems.append(f"metric {mid!r} curve index out of [0,1]: {y!r}")
                        bad = True
                        break
                if bad:
                    break
    return problems
