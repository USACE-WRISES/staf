"""The sources an owner can choose for a curve (REF-15, owner decision 2026-09-22).

The build chooses where every curve comes from (the reference-source hierarchy,
REF-04 to REF-14). For a metric the build did not fit itself, the owner may
choose instead:

- ``catalog``: a criterion of the verified catalog (REF-14), when it is fit for
  this ecoregion by the catalog's five conditions;
- ``earlier_version``: the curve an earlier version of this assessment scored;
- ``other_assessment``: the curve another STAF assessment scores for the same
  metric, taken without a comparability check;
- ``entered``: thresholds or breakpoints the owner enters, with a citation or on
  professional judgment;
- ``refused_source``: a source the build tried and refused.

A choice is resolved when it is made, so a decision states exactly the curve it
puts in the version: its points and class layers, its config and the
annotations the bundle carries, in the shape a carried curve rides in
(``carry_forward.session_rows``).

Reads the canonical library and the station table; writes nothing. Every string
is user-visible, so none carries an em dash.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

from . import curve_basis, curves, field_methods, fixed_criteria, metric_map
from . import published_benchmark as pb
from . import reference_pool as rp

CATALOG, EARLIER, OTHER, REFUSED, ENTERED = (
    "catalog", "earlier_version", "other_assessment", "refused_source", "entered")
#: a published state SQT curve, frozen from the SQT registry (the REF-15 extension of
#: 2026-09-23: accepted only while ``owner_curves.alternatives_enabled``)
SQT = "sqt"
SOURCE_KINDS = (CATALOG, EARLIER, OTHER, REFUSED, ENTERED, SQT)
#: how a chosen source reads in the workspace (``curve_sources.KINDS``)
DISPLAY_KIND = {CATALOG: "published_benchmark", EARLIER: "carried", OTHER: "borrowed",
                REFUSED: "owner_exception", ENTERED: "owner_entered", SQT: "sqt"}
#: the heading each source is listed under in the source dialog
GROUP_LABELS = {CATALOG: "Verified catalog", EARLIER: "Earlier version of this assessment",
                OTHER: "Another assessment", REFUSED: "Refused by the build",
                ENTERED: "Enter a curve", SQT: "Published state SQT"}
#: REF-15's outcome word for a source decision (rule_catalog.json)
OUTCOMES = {CATALOG: "catalog", EARLIER: "earlier_version", OTHER: "borrowed",
            REFUSED: "refused_accepted", SQT: "sqt"}
THRESHOLDS, BREAKPOINTS = "thresholds", "breakpoints"
#: the ``curve_source`` of a chosen curve's row
CURVE_SOURCE = "owner"
#: the versions another assessment offers: the ones DEEP accepts for new work
ELIGIBLE_STATUSES = ("preliminary", "certified")
#: at most this many earlier curves of this assessment are offered
MAX_EARLIER = 3
#: the index a count criterion anchors at (only read for counts; kept for the
#: fixed criteria's construction, which takes it)
RATING_INDEX = {"Good": 0.85, "Fair": 0.545, "Poor": 0.195}

ENTERED_LABEL = curve_basis.label_for(curve_basis.OWNER)
ENTERED_STATEMENT = {
    THRESHOLDS: ("Scored against thresholds this assessment's owner entered, rather than "
                 "against reference stations."),
    BREAKPOINTS: ("Scored against a curve this assessment's owner entered point by point, "
                  "rather than against reference stations."),
}
ENTERED_LIMIT = curve_basis.limit_for(curve_basis.OWNER)
JUDGMENT = "Professional judgment"
BORROWED_LIMIT = ("The curve was built for another assessment and was not tested for "
                  "comparability with this ecoregion.")
#: what a borrowed curve keeps of its bundle entry: what describes the curve
#: itself. The other assessment's reference sample, range, confidence and
#: caveats describe that assessment's streams, so they stay behind.
BORROWED_KEEP = ("stratifier", "metricRole", "methodContext", "criteriaBasis",
                 "criteriaSource", "publishedBenchmark")


def outcome_of(source: Optional[Mapping]) -> str:
    """REF-15's outcome word for a chosen source."""
    src = source or {}
    kind = str(src.get("kind") or "")
    if kind == ENTERED:
        return "entered_cited" if str(src.get("citation") or "").strip() else "entered_judgment"
    return OUTCOMES.get(kind, kind)


def label_for(source: Optional[Mapping]) -> str:
    """The badge a chosen curve carries in the workspace."""
    src = source or {}
    kind, ref = str(src.get("kind") or ""), src.get("ref") or {}
    if kind == ENTERED:
        return ENTERED_LABEL
    if kind == CATALOG:
        return curve_basis.label_for(curve_basis.PUBLISHED)
    if kind == EARLIER:
        return f"From v{ref['version']}" if ref.get("version") else "From an earlier version"
    if kind == OTHER:
        return f"From {ref.get('regionName') or 'another assessment'}"
    if kind == SQT:
        tool = " ".join(str(x) for x in (ref.get("tool") or f"{ref.get('state') or ''} SQT".strip(),
                                         ref.get("edition")) if x)
        return tool or "State SQT"
    return "Owner exception"


# --------------------------------------------------------------------------- #
# the metric's config and functions
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _directions() -> tuple:
    from . import regional_agent as ra
    return ra.load_directions(), ra.load_landscape_directions()


def agent_config(metric: str) -> dict:
    """The metric's config as a build makes it (``regional_agent``), or ``{}`` for
    a metric a pressure build never scores (a pressure or a predictor)."""
    from . import regional_agent as ra
    mk = str(metric)
    directions, landscape = _directions()
    cfg, _ = ra.build_metric_config([mk], directions, include_reserve=True)
    if mk in cfg:
        return dict(cfg[mk])
    cfg, _ = ra.build_landscape_metric_config([mk], landscape, expectation_only=True)
    return dict(cfg.get(mk) or {})


def config_for(metric: str, *, metric_config: Optional[Mapping] = None,
               build: Optional[Mapping] = None) -> dict:
    """The config a chosen curve carries: the session's own, the one a curve from
    another source rode in with, else the one a build would give the metric."""
    mk = str(metric)
    cfg = (metric_config or {}).get(mk)
    if cfg:
        return dict(cfg)
    b = build or {}
    for key in ("ownerMetrics", "ladderMetrics", "carriedMetrics"):
        saved = (b.get(key) or {}).get(mk) or {}
        got = saved.get("config") or (saved.get("curve") or {}).get("config")
        if got:
            return dict(got)
    return agent_config(mk)


def sourceable(metric: str, *, built=()) -> Optional[str]:
    """Why the owner cannot choose a source for the metric, or None when they can."""
    mk = str(metric)
    if mk in {str(k) for k in built or ()}:
        return "Built here. Edit its curve in its analysis instead."
    if fixed_criteria.is_fixed(mk):
        return ("A fixed criterion, scored the same way in every region. It can be removed "
                "or taken out of a function, not given another source.")
    return None


def function_candidates(function_id: str) -> list[str]:
    """The metrics the STAF crosswalk offers for a function, as session keys (a
    StreamCat code by its watershed column)."""
    from . import regional_agent as ra
    out: list[str] = []
    for r in metric_map.metric_map_entries().itertuples(index=False):
        if ra._canonical_function_id(r.function_name) != str(function_id):
            continue
        key = (str(r.code) if r.source == "nrsa" else
               f"{r.code}ws" if r.source == "streamcat" else None)
        if key and key not in out:
            out.append(key)
    return out


def crosswalk_functions(metric: str) -> list[str]:
    """The function ids the crosswalk places the metric in, primary first."""
    from . import regional_agent as ra
    out: list[str] = []
    for f in metric_map.metric_map_functions_for(str(metric)):
        fid = ra._canonical_function_id(f.get("function_name"))
        if fid and fid not in out:
            out.append(fid)
    return out


@lru_cache(maxsize=1)
def _functions_by_id() -> dict:
    from .staf_library import staf_function_meta
    meta = staf_function_meta()
    return {str(i): (str(d), str(n))
            for i, n, d in zip(meta["id"], meta["name"], meta["discipline"])}


def function_name(function_id: str) -> str:
    return _functions_by_id().get(str(function_id), ("", str(function_id)))[1]


def mapping_rows_for(metric: str, function_ids: Iterable[str]) -> list[dict]:
    """The mapping rows that place a chosen curve in the functions named."""
    by_id = _functions_by_id()
    return [{"metric_key": str(metric), "discipline": by_id[str(f)][0],
             "function_label": by_id[str(f)][1]}
            for f in function_ids or () if str(f) in by_id]


# --------------------------------------------------------------------------- #
# a curve the owner enters
# --------------------------------------------------------------------------- #
def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def fmt(x: Any) -> str:
    """A threshold as the owner would write it (25, 0.05, 1.2e-05)."""
    return f"{float(x):.6g}"


def _domain(config: Mapping) -> tuple[Optional[float], Optional[float]]:
    return _num(config.get("domain_min")), _num(config.get("domain_max"))


def two_sided(config: Mapping) -> bool:
    """The metric has an optimum rather than a better direction, so only a curve
    entered point by point can express it."""
    return (config.get("higher_is_better") is None
            or str(config.get("curve_form") or "") == curves.CURVE_FORM_OPTIMUM)


def threshold_bands(good: float, poor: float, higher_is_better: bool,
                    units: str = "") -> tuple[list[dict], list[dict]]:
    """``(bands, labels)``: the three classes two thresholds make, in the shape the
    fixed criteria's anchors read, and as the criterion's band labels. The
    threshold itself belongs to the better class ("Good at or above 25")."""
    u = f" {units}" if units else ""
    if higher_is_better:
        bands = [{"rating": "Good", "min": good, "minInclusive": True},
                 {"rating": "Fair", "min": poor, "minInclusive": True, "max": good,
                  "maxInclusive": False},
                 {"rating": "Poor", "max": poor, "maxInclusive": False}]
        labels = [{"rating": "Good", "label": f"\u2265{fmt(good)}{u}"},
                  {"rating": "Fair", "label": f"\u2265{fmt(poor)} to <{fmt(good)}{u}"},
                  {"rating": "Poor", "label": f"<{fmt(poor)}{u}"}]
    else:
        bands = [{"rating": "Good", "max": good, "maxInclusive": True},
                 {"rating": "Fair", "min": good, "minInclusive": False, "max": poor,
                  "maxInclusive": True},
                 {"rating": "Poor", "min": poor, "minInclusive": False}]
        labels = [{"rating": "Good", "label": f"\u2264{fmt(good)}{u}"},
                  {"rating": "Fair", "label": f">{fmt(good)} to \u2264{fmt(poor)}{u}"},
                  {"rating": "Poor", "label": f">{fmt(poor)}{u}"}]
    return bands, labels


def threshold_points(good: Any, poor: Any,
                     config: Mapping) -> tuple[list[dict], list[str], list[dict]]:
    """``(points, errors, band labels)`` of the curve two thresholds make, by the
    construction every fixed and published criterion uses (CURVE-11): the Good
    threshold scores 0.69, the Poor one 0.39, and the line through them runs to
    0 and 1 inside the metric's range."""
    g, p = _num(good), _num(poor)
    if g is None or p is None:
        return [], ["Enter both thresholds as numbers."], []
    if config.get("higher_is_better") is None:
        return [], ["This metric has an optimum, so enter its curve point by point."], []
    hib = bool(config.get("higher_is_better"))
    if g == p:
        return [], ["The two thresholds must differ."], []
    if hib and g < p:
        return [], ["Higher is better for this metric, so the Good threshold must be above "
                    "the Poor one."], []
    if not hib and g > p:
        return [], ["Lower is better for this metric, so the Good threshold must be below "
                    "the Poor one."], []
    lo, hi = _domain(config)
    if any((lo is not None and v < lo) or (hi is not None and v > hi) for v in (g, p)):
        span = (f"{fmt(lo) if lo is not None else 'no minimum'} to "
                f"{fmt(hi) if hi is not None else 'no maximum'}")
        return [], [f"Both thresholds must lie inside the metric's range ({span})."], []
    bands, labels = threshold_bands(g, p, hib, str(config.get("units") or ""))
    # a resolution far below the thresholds' spacing, so the threshold itself
    # scores in its better class and nothing else moves
    anchors = fixed_criteria._anchors(bands, abs(g - p) * 1e-6)
    floor = lo if lo is not None else _open_floor(anchors, hib)
    raw = fixed_criteria._points({**anchors, "domain": [floor, hi]}, RATING_INDEX)
    points = [{"x": float(x), "y": float(y)} for x, y in raw]
    got = curves.validate_reference_curve_points(points, hib, curves.curve_form_of(config),
                                                 domain=(lo, hi))
    if not got["valid"]:
        return [], ["These thresholds make no valid curve in this metric's range. Enter the "
                    "curve point by point instead."], []
    return points, [], labels


def _open_floor(anchors: Mapping, higher_is_better: bool) -> Optional[float]:
    """Where a two-threshold curve begins on a metric that declares no lower bound
    (a log-scaled measure can run below zero), or None to begin at zero as the
    fixed criteria do. Higher is better: the line runs on below zero to where it
    reaches 0, so no value below the thresholds is held at a score between the
    classes. Lower is better: only thresholds below zero move the start, to where
    the line reaches 1."""
    lo_idx, hi_idx = fixed_criteria._index_bands()
    x1, x2 = float(anchors["anchor_good_fair"]), float(anchors["anchor_fair_poor"])
    slope = (lo_idx - hi_idx) / (x2 - x1)
    if higher_is_better:
        x_zero = x2 - lo_idx / slope
        return x_zero if x_zero < 0 else None
    return x1 + (1.0 - hi_idx) / slope if x1 < 0 else None


def direction_warning(points, config: Mapping) -> Optional[str]:
    """A curve entered point by point that runs against the metric's direction:
    allowed, since the owner may know better, but said before it is saved."""
    if two_sided(config) or len(points or []) < 2:
        return None
    ys = [float(p["y"]) for p in sorted(points, key=lambda p: float(p["x"]))]
    if config.get("higher_is_better") and ys[-1] < ys[0]:
        return ("These points score the metric lower as it rises, but higher is better for "
                "this metric.")
    if not config.get("higher_is_better") and ys[-1] > ys[0]:
        return ("These points score the metric higher as it rises, but lower is better for "
                "this metric.")
    return None


_POINT_LINE = re.compile(r"^\s*([-+0-9.eE]+)\s*[,;\t ]\s*([-+0-9.eE]+)\s*$")


def parse_points(text: str) -> tuple[list[dict], list[str]]:
    """``value, score`` per line (a comma, semicolon, tab or space between) as
    points, and the lines that could not be read."""
    points, bad = [], []
    for i, line in enumerate(str(text or "").splitlines(), start=1):
        if not line.strip():
            continue
        m = _POINT_LINE.match(line)
        x, y = (_num(m.group(1)), _num(m.group(2))) if m else (None, None)
        if x is None or y is None:
            bad.append(f"Line {i} is not a value and a score: {line.strip()}")
            continue
        points.append({"x": x, "y": y})
    return points, bad


def breakpoint_points(text: str, config: Mapping) -> tuple[list[dict], list[str]]:
    """``(points, errors)`` of a curve entered point by point, checked by the same
    rules as a curve drawn in an analysis (``curves.validate_reference_curve_points``)."""
    points, errors = parse_points(text)
    if errors:
        return [], errors
    if len(points) < 2:
        return [], ["Enter at least two points, one per line: value, score."]
    got = curves.validate_reference_curve_points(
        points, config.get("higher_is_better"), curves.curve_form_of(config),
        domain=_domain(config))
    if not got["valid"]:
        return [], list(got["errors"])
    return [{"x": float(r.metric_value), "y": float(r.index_score)}
            for r in got["points"].itertuples(index=False)], []


def entered_annotations(metric: str, *, method: str, title: str, citation: str = "",
                        config: Optional[Mapping] = None,
                        bands: Optional[list] = None) -> dict:
    """What a bundle states beside an owner-entered curve. It rests on no reference
    stations, so it states no reference support, sample or confidence."""
    config = config or {}
    cite = " ".join(str(citation or "").split())
    source = {"title": " ".join(str(title or "").split()) or ENTERED_LABEL,
              "bands": list(bands or []),
              "citations": [{"key": "owner", "text": cite}] if cite else [],
              "provisional": False, "limitations": [ENTERED_LIMIT]}
    ann = {"basis": curve_basis.OWNER, "basisLabel": ENTERED_LABEL,
           "basisStatement": ENTERED_STATEMENT[method], "basisLimit": ENTERED_LIMIT,
           "curveCaveats": [ENTERED_LIMIT], "criteriaSource": source,
           "sourceCitation": cite or f"{JUDGMENT} of the assessment's owner"}
    if config.get("metric_role"):
        ann["metricRole"] = config["metric_role"]
    method_text = field_methods.method_context(metric)
    if method_text:
        ann["methodContext"] = method_text
    return ann


def entered_option(metric: str, *, method: str, config: Mapping, title: str,
                   citation: str = "", good: Any = None, poor: Any = None,
                   points_text: str = "") -> dict:
    """The owner's own curve as an option; ``errors`` names what is wrong with it."""
    if method == THRESHOLDS:
        points, errors, labels = threshold_points(good, poor, config)
    else:
        points, errors = breakpoint_points(points_text, config)
        labels = []
    name = " ".join(str(title or "").split())
    if not name:
        errors = list(errors) + ["Give the curve a title, such as the criterion it states."]
    cite = " ".join(str(citation or "").split())
    ref: dict = {"method": method}
    if method == THRESHOLDS and not errors:
        ref.update({"good": float(good), "poor": float(poor),
                    "higherIsBetter": bool(config.get("higher_is_better"))})
    opt = _option(ENTERED, f"{ENTERED}:{method}", title=name or ENTERED_LABEL,
                  detail=("Two thresholds" if method == THRESHOLDS else "Entered point by point")
                  + (", cited" if cite else f", {JUDGMENT.lower()}"),
                  points=points, ref=ref, citation=cite)
    opt["errors"] = list(errors)
    if not errors:
        opt["annotations"] = entered_annotations(metric, method=method, title=name,
                                                 citation=cite, config=config, bands=labels)
    return opt


# --------------------------------------------------------------------------- #
# the sources the pool offers
# --------------------------------------------------------------------------- #
def _option(kind: str, key: str, *, title: str, detail: str = "", points=None, layers=None,
            annotations=None, ref=None, citation: str = "", available: bool = True,
            why_not: str = "", n_reference=None, stratum: str = "") -> dict:
    return {"kind": kind, "key": key, "group": GROUP_LABELS[kind], "title": title,
            "detail": detail, "available": bool(available), "why_not": why_not,
            "points": list(points or []), "layers": list(layers or []),
            "annotations": dict(annotations or {}), "ref": dict(ref or {}),
            "citation": " ".join(str(citation or "").split()),
            "n_reference": n_reference, "stratum": stratum, "errors": []}


def _target_stations(region_code: str) -> pd.DataFrame:
    from . import reference_screen as rscreen
    table = rscreen.load_station_screen()
    return table[table["l3"].astype(str) == str(region_code)]


def catalog_annotations(metric: str, region: str, *, region_code: str,
                        region_name: Optional[str] = None) -> dict:
    """What a bundle states beside a catalog criterion: what a build states when
    REF-14 admits it (``pressure_evidence.bundle_inputs``), without the confidence
    only the build's own ranking computes."""
    d = pb.pool_decision(metric, region, region_code=str(region_code),
                         region_name=region_name, family=rp.family_of(metric))
    basis = curve_basis.PUBLISHED
    limit = curve_basis.limit_for(basis)
    ann = {"criteriaBasis": fixed_criteria.CRITERIA_BASIS, "basis": basis,
           "basisLabel": curve_basis.label_for(basis),
           "basisStatement": curve_basis.statement_for(basis), "basisLimit": limit,
           "referenceSupport": rp.reference_support_record(d), "curveCaveats": [limit],
           "criteriaSource": pb.criteria_source(metric, region),
           "sourceCitation": pb.citation_line(metric, region),
           "publishedBenchmark": {**pb.provenance(metric), "region": str(region)}}
    method_text = field_methods.method_context(metric)
    if method_text:
        ann["methodContext"] = method_text
    return ann


def catalog_option(metric: str, *, region_code: str,
                   region_name: Optional[str] = None) -> Optional[dict]:
    """The verified catalog's criterion for the metric at this ecoregion, or None
    when the catalog holds none. Offered when it passes REF-14's five conditions
    here, and otherwise listed with the conditions it fails."""
    mk = str(metric)
    if not pb.entries_for(mk):
        return None
    got = pb.fitness(mk, frame=_target_stations(region_code), target_l3=str(region_code))
    region = got.get("region")
    title = (pb.criteria_source(mk, region).get("title") if region
             else str(got.get("benchmark") or "Published criterion"))
    spec = pb.lookup(mk, target_l3=str(region_code), region=region) or {}
    ref = {"entry": spec.get("id"), "region": region, "edition": spec.get("edition")}
    key = f"{CATALOG}:{spec.get('id')}"
    if not got.get("admissible"):
        failed = [v["why"] for v in (got.get("conditions") or {}).values() if not v["pass"]]
        return _option(CATALOG, key, title=title, detail="Verified catalog", ref=ref,
                       available=False,
                       why_not=" ".join(failed) or "The fitness test refused this criterion.")
    return _option(CATALOG, key, title=title,
                   detail=f"Verified catalog, fit for NARS-9 region {region}",
                   points=pb.curve_points(mk, region) or [], ref=ref,
                   citation=pb.citation_line(mk, region),
                   annotations=catalog_annotations(mk, region, region_code=region_code,
                                                   region_name=region_name))


@lru_cache(maxsize=128)
def _bundle_entry(assessment_id: str, version: int, metric_id: str) -> Optional[dict]:
    """The first bundle entry of ``metric_id`` in a published version, with the
    bundle's content digest, or None."""
    from . import library as lib
    try:
        bundle = lib.load_version_bundle(assessment_id, int(version)) or {}
    except (OSError, ValueError, TypeError):
        return None
    for block in bundle.get("metricsByFunction") or []:
        for m in block.get("metrics") or []:
            if str(m.get("metricId")) == metric_id:
                return {"entry": dict(m), "contentDigest": bundle.get("contentDigest")}
    return None


def _versions(assessment_id: str) -> list[int]:
    from . import library as lib
    out = []
    for v in (lib.read_manifest(assessment_id) or {}).get("versions") or []:
        try:
            out.append(int(v.get("version")))
        except (TypeError, ValueError):
            continue
    return sorted(set(out), reverse=True)


def _pts(points) -> tuple:
    return tuple((round(float(p["x"]), 9), round(float(p["y"]), 9)) for p in points or [])


def _signature(entry: Mapping) -> tuple:
    return (_pts((entry.get("curve") or {}).get("points")),
            tuple((str(L.get("stratum") or ""), _pts(L.get("points")))
                  for L in entry.get("curveLayers") or []))


def _curve_parts(entry: Mapping) -> tuple[list, list, str]:
    curve = entry.get("curve") or {}
    points = [{"x": float(p["x"]), "y": float(p["y"])} for p in curve.get("points") or []]
    layers = [{"stratum": str(L.get("stratum") or ""),
               "points": [{"x": float(p["x"]), "y": float(p["y"])}
                          for p in L.get("points") or []]}
              for L in entry.get("curveLayers") or []]
    return points, layers, str(curve.get("stratification") or "")


def _basis_words(entry: Mapping) -> str:
    """A bundle entry's source in a few words: its basis and what it rests on."""
    basis = curve_basis.resolve(entry.get("basis"), criteria_basis=entry.get("criteriaBasis"))
    label = str(entry.get("basisLabel") or curve_basis.label_for(basis) or "Reference curve")
    sup = entry.get("referenceSupport") or {}
    if basis in (curve_basis.PUBLISHED, curve_basis.OWNER) \
            or entry.get("criteriaBasis") == fixed_criteria.CRITERIA_BASIS:
        return label
    if basis == curve_basis.MODELED:
        n = _num(sup.get("nUsable"))
        return label + (f", fitted on {int(n):,} stations nationally" if n else "")
    if basis == curve_basis.NATIONAL:
        n = _num(sup.get("nUsable"))
        return label + (f", {int(n):,} national donor stations" if n else "")
    n = _num(entry["referenceN"] if entry.get("referenceN") is not None else sup.get("nUsable"))
    return label + (f", {int(n):,} reference stations" if n else "")


def borrowed_annotations(entry: Mapping, *, assessment_id: str, name: str, version: int,
                         region: Mapping, content_digest: Optional[str]) -> dict:
    """What a bundle states beside a curve taken from another assessment: what the
    curve is, where it comes from, and that nobody tested it here."""
    basis = curve_basis.resolve(entry.get("basis"), criteria_basis=entry.get("criteriaBasis"))
    ann = {k: entry[k] for k in BORROWED_KEEP if entry.get(k) is not None}
    sup = entry.get("referenceSupport") or {}
    if str(sup.get("status") or "") == rp.STATUS_PUBLISHED:
        # a published criterion states no station of anywhere, so its record is
        # as true here, and DEEP reads a criterion by it
        ann["referenceSupport"] = dict(sup)
    where = str(region.get("name") or region.get("code") or "another region")
    own = curve_basis.limit_for(basis)
    # the source's own limit describes the source's streams, not this ecoregion's
    own = f"In {where}: {own[:1].lower()}{own[1:]}" if own else ""
    ann.update({
        "basis": basis,
        "basisLabel": f"{curve_basis.label_for(basis) or 'Reference curve'}, from {where}",
        "basisStatement": (f"Curve of another STAF assessment, {name} (version {version}), "
                           f"chosen by this assessment's owner. It rests on that assessment's "
                           f"source for {where}."),
        "basisLimit": BORROWED_LIMIT,
        "curveCaveats": [BORROWED_LIMIT] + ([own] if own else []),
        "sourceCitation": f"{name}, version {version} (STAF assessment library)",
        "borrowedFrom": {"assessmentId": assessment_id, "assessmentName": name,
                         "version": int(version), "regionCode": region.get("code"),
                         "regionName": region.get("name"), "contentDigest": content_digest,
                         "basis": basis, "basisLabel": entry.get("basisLabel"),
                         "referenceN": entry.get("referenceN")}})
    return ann


def earlier_annotations(entry: Mapping, *, assessment_id: str, version: int,
                        content_digest: Optional[str]) -> dict:
    """An earlier version's curve states what it stated when it was published, as a
    carried curve does (``carry_forward.ANNOTATION_KEYS``)."""
    from . import carry_forward as cf
    ann = {k: entry[k] for k in cf.ANNOTATION_KEYS if k in entry}
    ann["carriedForward"] = {"assessmentId": assessment_id, "fromVersion": int(version),
                             "contentDigest": content_digest}
    return ann


def library_options(metric: str, *, region_code: str, current=None) -> list[dict]:
    """The curves the canonical library holds for the metric: this assessment's
    earlier versions (newest first, each distinct curve once, at most
    :data:`MAX_EARLIER`), then every other ecoregion assessment's latest version
    DEEP accepts. ``current``: the points the metric scores now, not offered again."""
    from . import library as lib
    from .deep_export import deep_slug
    mid = "spring-" + deep_slug(str(metric))
    now = _pts(current)
    mine: list[dict] = []
    others: list[dict] = []
    try:
        listed = lib.list_assessments()
    except (OSError, ValueError):
        return []
    for a in listed:
        reg = a.get("region") or {}
        if reg.get("kind") != "ecoregion" or lib.entry_type(a) != "deep":
            continue
        aid = str(a.get("assessmentId") or "")
        name = str(a.get("assessmentName") or aid)
        is_mine = str(reg.get("code")) == str(region_code)
        seen: set = set()
        for v in _versions(aid):
            status = lib.version_status(aid, v)
            if not is_mine and status not in ELIGIBLE_STATUSES:
                continue
            got = _bundle_entry(aid, v, mid)
            entry = (got or {}).get("entry")
            sig = _signature(entry) if entry else None
            points, layers, stratum = _curve_parts(entry) if entry else ([], [], "")
            usable = (entry is not None and len(points) >= 2 and sig not in seen
                      and not (now and sig[0] == now))
            if not usable:
                if is_mine:
                    continue
                # another assessment offers its latest eligible version only: an
                # older one it has since replaced is not its curve any more
                break
            seen.add(sig)
            digest = got["contentDigest"]
            words = f"{_basis_words(entry)} ({lib.status_label(status)})"
            if is_mine:
                mine.append(_option(
                    EARLIER, f"{EARLIER}:{aid}:v{v}", title=f"Version {v} of this assessment",
                    detail=words, points=points, layers=layers, stratum=stratum,
                    ref={"assessmentId": aid, "version": v, "contentDigest": digest},
                    citation=str(entry.get("sourceCitation") or ""),
                    n_reference=entry.get("referenceN"),
                    annotations=earlier_annotations(entry, assessment_id=aid, version=v,
                                                    content_digest=digest)))
                if len(mine) >= MAX_EARLIER:
                    break
            else:
                others.append(_option(
                    OTHER, f"{OTHER}:{aid}:v{v}", title=f"{name}, version {v}",
                    detail=words, points=points, layers=layers, stratum=stratum,
                    ref={"assessmentId": aid, "version": v, "contentDigest": digest,
                         "regionCode": reg.get("code"), "regionName": reg.get("name")},
                    citation=f"{name}, version {v} (STAF assessment library)",
                    annotations=borrowed_annotations(entry, assessment_id=aid, name=name,
                                                     version=v, region=reg,
                                                     content_digest=digest)))
                break
    return mine + others


#: the rule a station pool option is chosen under
POOL_OPTION_RULES = {"local": "REF-04"}
REFUSAL_NOTE = ("The next build of this region computes this curve from the source, records "
                "every check it fails, and scores it as your exception. Until then the metric "
                "scores as the build left it.")


def _pool_title(option: str, code: Any) -> str:
    """A station pool option in words: the local reference, this ecoregion under
    the regional screen, or a wider pool with its region's code."""
    from . import acceptance
    wider = option not in ("local", "regional_l3") and code not in (None, "")
    return acceptance.option_label(option) + (f" ({code})" if wider else "")


def _refused(rule: str, option: str, *, title: str, n: Optional[int], why: str,
             forcible: bool, why_not: str = "") -> dict:
    from . import basis_ladder
    enough = n is None or int(n) >= basis_ladder.MIN_FORCED_N
    opt = _option(REFUSED, f"{REFUSED}:{rule}:{option}", title=title,
                  detail=(f"{int(n):,} stations. " if n is not None else "")
                  + f"Refused by the build under {rule}.",
                  ref={"rule": rule, "option": option, "refusal": why},
                  available=bool(forcible and enough),
                  why_not=(why_not or (why if forcible and enough else
                                       f"Too few stations to build a curve from ({n})."
                                       if forcible else why)))
    opt["refusal"] = why
    return opt


def refused_options(metric: str, *, build: Optional[Mapping] = None,
                    provenance: Optional[Mapping] = None) -> list[dict]:
    """The sources the build tried for the metric and refused, each with its
    reason: the station pools and the national options the provenance names, a
    modeled specification that waits for approval or lies outside its limits,
    and a catalog criterion that failed a fitness condition. The owner may accept
    one (REF-15); its curve is computed at the next build. A source that holds
    nothing to build from (no criterion, too few stations) is listed as not
    available."""
    from . import curve_sources as csrc
    from . import reference_pool as rp_
    mk = str(metric)
    records = [r for r in (provenance or {}).get("records") or []
               if str(r.get("subject")) == mk and r.get("subject_kind", "metric") == "metric"]
    by_rule = {str(r.get("rule_id")): r for r in records}
    out: list[dict] = []
    pools = ((by_rule.get("REF-06") or {}).get("computed") or {}).get("options_tried") or []
    for x in pools:
        option = str(x.get("option") or "")
        rule = POOL_OPTION_RULES.get(option, "REF-11")
        # a pool the build could not even form (no region at that level) counted
        # no station, so there is nothing to compute from
        counted = x.get("n_usable") is not None
        why = str(x.get("why") or "")
        out.append(_refused(rule, option, title=_pool_title(option, x.get("region_code")),
                            n=x.get("n_usable"), why=why, forcible=counted,
                            why_not="" if counted else (why or "The build formed no pool here.")))
    rungs = []
    for w in (build or {}).get("insufficientReferenceSupport") or []:
        if str(w.get("metricKey")) == mk:
            rungs = list(w.get("rungsTried") or [])
    for rule in ("REF-12", "REF-13", "REF-14"):
        rec = by_rule.get(rule)
        if rec is not None and rec.get("verdict") == "pass":
            continue
        computed = (rec or {}).get("computed") or {}
        why = str(computed.get("why") or next((r.get("why") for r in rungs
                                               if r.get("rung") == rule), "") or "")
        if rec is None and not any(r.get("rung") == rule for r in rungs):
            continue
        name = csrc.RUNG_NAMES.get(rule, rule)
        if rule == "REF-12":
            options = computed.get("options") or [{"option": o, "n": None}
                                                  for o in csrc.NATIONAL_OPTIONS]
            for o in options:
                option = str(o.get("option") or "")
                out.append(_refused(rule, option,
                                    title=f"{name}, {csrc.NATIONAL_OPTIONS.get(option, option).lower()}",
                                    n=o.get("n"), why=str(o.get("why") or why), forcible=True))
        elif rule == "REF-13":
            # a specification waiting for approval, or one outside its limits, can
            # be run; a metric the registry holds nothing for cannot
            out.append(_refused(rule, "modeled", title=name, n=None, why=why,
                                forcible=bool(why) and "no approved specification" not in why))
        else:
            condition = computed.get("condition") or next(
                (r.get("condition") for r in rungs if r.get("rung") == rule), None)
            no_entry = list(condition or []) == ["catalog", "no entry"]
            out.append(_refused(rule, "catalog", title=name, n=None, why=why,
                                forcible=not no_entry))
    return out


def forced_annotations(metric: str, got: Mapping) -> dict:
    """What a bundle states beside a refused source the owner accepted: what the
    curve rests on, exactly as the build computed it, and every check it failed."""
    from . import reference_pool as rp_
    d = dict(got.get("decision") or {})
    basis = curve_basis.resolve(d.get("basis"))
    failed = [f for f in got.get("failed") or [] if not f.get("pass")]
    checks = "; ".join(f"{f.get('check')}: {str(f.get('why') or '').rstrip('.')}"
                       for f in failed) or "no check recorded"
    caveat = (f"The build refused this source ({checks}). The owner accepted it with a "
              "recorded rationale.")
    limit = curve_basis.limit_for(basis)
    ann = {"basis": basis,
           "basisLabel": f"{curve_basis.label_for(basis) or 'Reference curve'}, accepted by the owner",
           "basisStatement": curve_basis.statement_for(basis), "basisLimit": caveat,
           "referenceSupport": rp_.reference_support_record(d),
           "curveCaveats": [caveat] + ([limit] if limit else []),
           "ownerException": {"rule": got.get("rule"), "option": got.get("option"),
                              "failed": [dict(f) for f in failed]}}
    if basis == curve_basis.PUBLISHED:
        region = (got.get("row") or {}).get("benchmark_region")
        ann["criteriaBasis"] = fixed_criteria.CRITERIA_BASIS
        if region:
            ann["criteriaSource"] = pb.criteria_source(metric, region)
            ann["sourceCitation"] = pb.citation_line(metric, region)
            ann["publishedBenchmark"] = {**pb.provenance(metric), "region": str(region)}
    else:
        # a pool, the national donors or a model: the curve rests on stations as
        # any fitted curve does and states its basis the same way, since DEEP
        # prints a curve's station support only beside a stated basis
        ann["criteriaBasis"] = "reference"
    method_text = field_methods.method_context(metric)
    if method_text:
        ann["methodContext"] = method_text
    return ann


def forced_curve(metric: str, got: Mapping) -> dict:
    """A refused source's computed curve in the shape a decision holds it."""
    row = got.get("row") or {}
    frame = curves.normalize_reference_curve_points(row.get("curve_points"))
    config = dict(got.get("config") or {})
    return {"displayName": config.get("display_name") or str(metric), "curveStatus": "complete",
            "nReference": row.get("n_reference"), "stratum": str(row.get("stratum") or ""),
            "points": [{"x": float(r.metric_value), "y": float(r.index_score)}
                       for r in frame.itertuples(index=False)],
            "layers": [], "config": config,
            "annotations": forced_annotations(metric, got)}


def pool_for(metric: str, *, region_code: str, region_name: Optional[str] = None,
             build: Optional[Mapping] = None, provenance: Optional[Mapping] = None,
             current=None) -> list[dict]:
    """Every source the owner can choose for the metric, in the order the dialog
    lists them, the ones not available here last with their reason."""
    opts: list[dict] = []
    cat = catalog_option(metric, region_code=region_code, region_name=region_name)
    if cat is not None and not (current and cat["available"]
                                and _pts(cat["points"]) == _pts(current)):
        opts.append(cat)
    opts.extend(library_options(metric, region_code=region_code, current=current))
    opts.extend(refused_options(metric, build=build, provenance=provenance))
    return [o for o in opts if o["available"]] + [o for o in opts if not o["available"]]


# --------------------------------------------------------------------------- #
# the decision's source
# --------------------------------------------------------------------------- #
SQT_LABEL = "State SQT"


def _sqt_edition(frozen: Mapping) -> Optional[str]:
    """The edition the original names; None when only the metric library's References
    column suggests one (the curve row itself names no edition)."""
    against = ((frozen.get("verification") or {}).get("against") or {})
    return str(against["edition"]) if against.get("edition") else None


def sqt_statement(frozen: Mapping) -> str:
    edition = _sqt_edition(frozen)
    tool = " ".join(str(x) for x in (frozen.get("tool"), edition) if x) or "a state SQT"
    return (f"Scored against a curve published in the {tool}, rather than against stations from "
            "this ecoregion. The SQT's own index is kept. DEEP calls an index at or below 0.39 Not "
            "Functioning and at or below 0.69 Functioning At Risk; the SQT calls one below 0.30 Not "
            "Functioning and below 0.70 At Risk, so an index from 0.30 to 0.39 reads worse in DEEP "
            "and one above 0.69 and below 0.70 reads better.")


def sqt_source(candidate: Mapping) -> dict:
    """The ``source`` of a REF-15 decision that selects a considered state SQT curve
    (``candidates.sqt_candidate``): the published rule written as DEEP points (the conversion
    ``candidates.sqt_adoption`` recorded), in the shape a carried curve rides in, stated as a
    published criterion with the SQT's own label, verification and limits. Refused for a
    candidate the registry, its checks or its frozen record exclude."""
    if (candidate.get("eligibility") or {}).get("status") != "eligible":
        raise ValueError("This SQT curve is excluded: "
                         + " ".join((candidate.get("eligibility") or {}).get("reasons") or []))
    frozen = dict(candidate.get("record") or {})
    from . import sqt_registry
    if not sqt_registry.frozen_intact(frozen):
        raise ValueError("The frozen SQT record no longer matches its fingerprint.")
    if not frozen.get("eligible"):
        raise ValueError("The SQT registry marks this curve ineligible.")
    ident = candidate.get("identity") or {}
    mk = str((ident.get("subject") or {}).get("id"))
    d = candidate.get("definition") or {}
    direction = d.get("direction")
    edition = _sqt_edition(frozen)
    tool = " ".join(str(x) for x in (frozen.get("tool"), f"({edition})" if edition else None) if x) \
        or "the state SQT"
    how = (str(frozen["protocol"]) if frozen.get("protocol") else
           f"Measure it as the {tool} specifies.")
    config = {"display_name": candidate.get("label") or mk, "units": d.get("units") or "",
              "column_name": mk, "notes": how}
    if direction in ("increasing", "decreasing"):
        config["higher_is_better"] = direction == "increasing"
    ver = frozen.get("verification") or {}
    checked = {"verified": "Checked against the original.",
               "partially-verified": "Checked in part against the original."}.get(
        ver.get("status"), "A STAF adaptation of the SQT, not checked against the original.")
    limit = f"{curve_basis.limit_for(curve_basis.PUBLISHED)} {checked}"
    caveats = [limit] + [str(x) for x in candidate.get("limitations") or [] if str(x) not in limit]
    annotations = {"basis": curve_basis.PUBLISHED, "basisLabel": SQT_LABEL,
                   "basisStatement": sqt_statement(frozen), "basisLimit": limit,
                   # every limit, never a cut list: the one about the curve's ends matters most
                   "curveCaveats": list(dict.fromkeys(caveats)),
                   "criteriaSource": {"title": frozen.get("tool"), "edition": edition,
                                      "citation": frozen.get("citation"), "state": frozen.get("state")},
                   "sourceCitation": frozen.get("citation"),
                   "sqt": {"registryKey": frozen.get("key"), "verification": ver.get("status"),
                           "stratum": frozen.get("stratumName"),
                           "conversion": list(d.get("conversion") or [])}}
    if frozen.get("protocol"):
        annotations["methodContext"] = str(frozen["protocol"])
    return {"kind": SQT, "title": str(candidate.get("label") or mk),
            "citation": str(frozen.get("citation") or "") or None,
            "ref": {"registryKey": frozen.get("key"), "state": frozen.get("state"),
                    "tool": frozen.get("tool"), "edition": edition,
                    "candidateKey": candidate.get("candidateKey"),
                    "basisDigest": candidate.get("basisDigest"),
                    "fingerprint": (frozen.get("frozen") or {}).get("contentFingerprint"),
                    "verification": ver.get("status")},
            "curve": {"displayName": config["display_name"], "curveStatus": "complete",
                      "nReference": None, "stratum": str(d.get("stratum") or ""),
                      "points": [{"x": float(p["x"]), "y": float(p["y"])} for p in d.get("points") or []],
                      "layers": [], "config": config, "annotations": annotations}}


def decision_source(metric: str, option: Mapping, *, config: Mapping) -> dict:
    """The ``source`` a SOURCE decision records for a chosen option: what it is,
    and the curve itself in the shape a carried curve rides in."""
    config = dict(config or {})
    kind = str(option.get("kind"))
    if kind == REFUSED:
        return {"kind": kind, "ref": dict(option.get("ref") or {}),
                "title": str(option.get("title") or ""), "citation": None}
    return {"kind": kind, "ref": dict(option.get("ref") or {}),
            "title": str(option.get("title") or ""),
            "citation": str(option.get("citation") or "") or None,
            "curve": {"displayName": config.get("display_name") or str(metric),
                      "curveStatus": "complete",
                      "nReference": option.get("n_reference") if kind == EARLIER else None,
                      "stratum": str(option.get("stratum") or ""),
                      "points": [{"x": float(p["x"]), "y": float(p["y"])}
                                 for p in option.get("points") or []],
                      "layers": [dict(L) for L in option.get("layers") or []],
                      "config": config,
                      "annotations": dict(option.get("annotations") or {})}}


__all__ = [
    "CATALOG", "EARLIER", "OTHER", "REFUSED", "ENTERED", "SOURCE_KINDS", "DISPLAY_KIND",
    "GROUP_LABELS", "OUTCOMES", "THRESHOLDS", "BREAKPOINTS", "CURVE_SOURCE", "SQT",
    "ELIGIBLE_STATUSES", "ENTERED_LABEL", "ENTERED_LIMIT", "BORROWED_LIMIT", "JUDGMENT",
    "outcome_of", "label_for", "agent_config", "config_for", "sourceable",
    "function_candidates", "crosswalk_functions", "function_name", "mapping_rows_for",
    "fmt", "two_sided", "threshold_bands", "threshold_points", "direction_warning",
    "parse_points",
    "breakpoint_points", "entered_annotations", "entered_option", "catalog_annotations",
    "catalog_option", "borrowed_annotations", "earlier_annotations", "library_options",
    "refused_options", "pool_for", "decision_source", "REFUSAL_NOTE", "forced_annotations",
    "sqt_source", "sqt_statement", "SQT_LABEL",
    "forced_curve",
]
