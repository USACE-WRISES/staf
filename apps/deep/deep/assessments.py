"""Load DEEP detailed-assessment definitions (predefined registry + uploads).

A DEEP assessment selects, per STAF function, one or more metrics; each metric
carries an inlined reference curve (a list of ``{x, y}`` points) already resolved
to the assessment's source (e.g. a state SQT). The predefined registry in
``data/deep-assessments.json`` and an uploaded bundle share the same shape, so
both load through :class:`LoadedAssessment`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config, geo, session


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
    """Lightweight catalog for a picker: id, name, state, counts, and (for library
    assessments) the region + embedded version/last-updated."""
    out = []
    for a in config.assessments():
        mbf = a.get("metricsByFunction", [])
        lib = a.get("library") or {}
        region = a.get("region") or lib.get("region") or {}
        out.append(
            {
                "assessmentId": a["assessmentId"],
                "assessmentName": a.get("assessmentName", a["assessmentId"]),
                "stateCode": a.get("stateCode", ""),
                "sourceCitation": a.get("sourceCitation", ""),
                "functionCount": len([fn for fn in mbf if fn.get("metrics")]),
                "metricCount": sum(len(fn.get("metrics", [])) for fn in mbf),
                "regionName": region.get("name", ""),
                "version": lib.get("version"),
                "updatedAt": lib.get("updatedAt", ""),
            }
        )
    return out


def _region_geometry(region: dict | None) -> dict | None:
    """Normalize a stored region polygon into a GeoJSON geometry, or None.

    A published library bundle stores the outline as a geometry dict
    ({type, coordinates}); a user-drawn region may store bare rings (a list). Both
    normalize to a geometry so the map layer renders them the same way.
    """
    poly = (region or {}).get("polygon")
    if not poly:
        return None
    if isinstance(poly, dict) and poly.get("type") and poly.get("coordinates"):
        return poly
    if isinstance(poly, list) and poly:
        first = poly[0]
        # a bare ring of [x, y] points -> single-ring Polygon; a list of rings -> Polygon
        if first and isinstance(first[0], (int, float)):
            return {"type": "Polygon", "coordinates": [poly]}
        return {"type": "Polygon", "coordinates": poly}
    return None


def library_region_features() -> dict:
    """A GeoJSON FeatureCollection of the available assessments that carry a region
    outline, for DEEP's "available assessments" map overlay. Each feature's properties
    carry ``assessmentId`` (for click-to-load), ``assessmentName`` and ``regionName``.
    ``features`` is empty when nothing has a polygon (e.g. only the state-SQT registry).
    """
    feats: list[dict] = []
    for a in config.assessments():
        region = a.get("region") or (a.get("library") or {}).get("region") or {}
        geom = _region_geometry(region)
        if not geom:
            continue
        lib = a.get("library") or {}
        feats.append(
            {
                "type": "Feature",
                "properties": {
                    "assessmentId": a.get("assessmentId"),
                    "assessmentName": a.get("assessmentName", a.get("assessmentId")),
                    "regionName": region.get("name", ""),
                    "version": lib.get("version"),
                    "updatedAt": lib.get("updatedAt", ""),
                },
                "geometry": geom,
            }
        )
    return {"type": "FeatureCollection", "features": feats}


# --------------------------------------------------------------------------- #
# Applicability (which assessments cover a clicked point)
# --------------------------------------------------------------------------- #
def _point_in_ring(x: float, y: float, ring: list) -> bool:
    """Ray-casting point-in-ring test; ``ring`` is a sequence of ``[lon, lat]`` pairs."""
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _point_in_polygon(x: float, y: float, rings: list) -> bool:
    """Point in a GeoJSON polygon: inside the outer ring and outside every hole."""
    if not rings or not _point_in_ring(x, y, rings[0]):
        return False
    return not any(_point_in_ring(x, y, h) for h in rings[1:])


def _point_in_geometry(lon: float, lat: float, geom: dict | None) -> bool:
    if not geom:
        return False
    t, coords = geom.get("type"), geom.get("coordinates") or []
    if t == "Polygon":
        return _point_in_polygon(lon, lat, coords)
    if t == "MultiPolygon":
        return any(_point_in_polygon(lon, lat, poly) for poly in coords)
    return False


def applicable_assessments(lat: float, lon: float) -> list[str]:
    """assessmentIds applicable at ``(lat, lon)``: the region polygon contains the point, or
    the assessment has no region polygon (no area of applicability -> applies everywhere).

    Legacy matcher retained for the current selector/tests. The redesign uses the stricter
    :func:`covering_assessments`; this stays for backward compatibility."""
    out: list[str] = []
    for a in config.assessments():
        region = a.get("region") or (a.get("library") or {}).get("region") or {}
        geom = _region_geometry(region)
        if geom is None or _point_in_geometry(lon, lat, geom):
            out.append(a.get("assessmentId"))
    return out


def covering_assessments(lat: float, lon: float, *, require_polygon: bool = True) -> list[str]:
    """assessmentIds whose published region polygon **covers** ``(lat, lon)``, ordered
    **certified first, then preliminary** (stable within each tier).

    Stricter than :func:`applicable_assessments`: with ``require_polygon=True`` (the
    redesign default) a polygonless assessment does NOT apply everywhere — it is excluded,
    so DEEP only offers assessments with a real area of applicability. Pass
    ``require_polygon=False`` to also include polygonless assessments as a clearly-labeled
    national fallback (the interim behavior until the state SQTs receive polygons upstream —
    Part E). Lifecycle status is read from the bundle (``session.lifecycle_status``),
    defaulting to preliminary.
    """
    certified: list[str] = []
    preliminary: list[str] = []
    for a in config.assessments():
        region = a.get("region") or (a.get("library") or {}).get("region") or {}
        geom = _region_geometry(region)
        if geom is None:
            if require_polygon:
                continue
            covered = True  # national fallback (no defined area of applicability)
        else:
            covered = _point_in_geometry(lon, lat, geom)
        if not covered:
            continue
        bucket = certified if session.lifecycle_status(a) == "certified" else preliminary
        bucket.append(a.get("assessmentId"))
    return certified + preliminary


def covering_refs(lat: float, lon: float, *, require_polygon: bool = True) -> list[dict]:
    """Per covering assessment id, its eligible version refs, certified-first.

    Coverage is decided from the *default* version's region polygon. Returns one dict per
    covering id: ``{assessmentId, assessmentName, regionName, defaultRef, refs,
    lifecycleByRef, versionByRef, hasCertified}`` where ``refs`` is ordered certified-desc
    then preliminary-desc with the default ref first. Ids with a certified default sort
    ahead. ``require_polygon`` mirrors :func:`covering_assessments`.
    """
    records = config._registry_records()
    by_id: dict[str, list[dict]] = {}
    for r in records:
        aid = r.get("assessmentId")
        if aid:
            by_id.setdefault(aid, []).append(r)

    out: list[dict] = []
    for aid, recs in by_id.items():
        default_ref = config.default_ref_for(aid)
        default_rec = config.load_ref(default_ref) if default_ref else None
        default_rec = default_rec or recs[0]
        region = default_rec.get("region") or (default_rec.get("library") or {}).get("region") or {}
        geom = _region_geometry(region)
        if geom is None:
            if require_polygon:
                continue
            covered = True
        else:
            covered = _point_in_geometry(lon, lat, geom)
        if not covered:
            continue

        certified = sorted((r for r in recs if r.get("lifecycle") == "certified"),
                           key=lambda r: -int(r.get("version") or 0))
        preliminary = sorted((r for r in recs if r.get("lifecycle") != "certified"),
                             key=lambda r: -int(r.get("version") or 0))
        ordered = certified + preliminary
        refs = [r["assessmentRef"] for r in ordered]
        if default_ref in refs:
            refs = [default_ref] + [r for r in refs if r != default_ref]
        out.append({
            "assessmentId": aid,
            "assessmentName": default_rec.get("assessmentName", aid),
            "regionName": region.get("name", ""),
            "defaultRef": default_ref,
            "refs": refs,
            "lifecycleByRef": {r["assessmentRef"]: r.get("lifecycle", "preliminary") for r in ordered},
            "versionByRef": {r["assessmentRef"]: int(r.get("version") or 1) for r in ordered},
            "hasCertified": bool(certified),
        })
    out.sort(key=lambda e: (0 if e["hasCertified"] else 1, e["assessmentId"]))
    return out


def load_ref(ref: str) -> LoadedAssessment:
    """Load a specific ``id@vN`` version record as a :class:`LoadedAssessment`."""
    rec = config.load_ref(ref)
    if rec is None:
        raise KeyError(f"unknown assessment ref {ref!r}")
    return LoadedAssessment.from_dict(rec)


def resolve_site_regions(lat, lon) -> dict:
    """Resolve a snapped site to its region labels via :mod:`deep.geo`.

    Returns ``{"level3": {"code","name"}|None, "state": {"code","abbr","name"}|None}``.
    Missing coordinates -> both None. Used by the session provenance stamp and the reports.
    """
    if lat is None or lon is None:
        return {"level3": None, "state": None}
    return {"level3": geo.level3_at(lat, lon), "state": geo.state_at(lat, lon)}


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
