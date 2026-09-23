"""Published state Stream Quantification Tool (SQT) curves as candidate curve sources.

``data/sqt/registry.json`` is built by ``scripts/build_sqt_registry.py`` from the raw bins
of the STAF metric library CSV, checked against the adapted ``*-sqt-adapted`` assessments
in the assessment library and against any original SQT workbooks on disk. Each record is
one state x metric x stratum curve: its source values, its points on the 0 to 1 index, how
the source behaves past its last point, its verification status and its issues.

This module reads the registry (cached, never written), searches it, says whether a record
applies to a target as a list of explicit checks, and freezes a record for adoption into a
project, so a later registry build never changes saved work.

SQT and STAF band an index differently. An SQT calls an index below 0.30 Not Functioning
and below 0.70 Functioning At Risk; STAF and DEEP call an index at or below 0.39 Not
Functioning and at or below 0.69 Functioning At Risk. An SQT index from 0.30 to 0.39 is
therefore At Risk in its own tool and Not Functioning in DEEP.

Pure: file reads and arithmetic. No shiny, no network.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .paths import DATA_DIR

REGISTRY_PATH = DATA_DIR / "sqt" / "registry.json"
SCHEMA_VERSION = 1

#: SQT bands: Not Functioning below 0.30, Functioning At Risk below 0.70, else Functioning
SQT_SCORE_SCALE = {"kind": "sqt-0-1",
                   "bands": {"notFunctioning": 0.30, "functioningAtRisk": 0.70}}
#: STAF/DEEP bands: Not Functioning at or below 0.39, At Risk at or below 0.69
STAF_SCORE_SCALE = {"kind": "staf-0-1",
                    "bands": {"notFunctioning": 0.39, "functioningAtRisk": 0.69}}

VERIFICATION_STATUSES = ("verified", "partially-verified", "unverified", "defective")
FORMS = ("piecewise", "two-sided", "categorical", "threshold-table")
EXTRAPOLATIONS = ("flat", "linear", "unknown")
DIRECTIONS = ("increasing", "decreasing", "two-sided", "unknown")
SEVERITIES = ("defect", "warning", "note")
CHECK_STATUSES = ("pass", "warn", "fail", "unknown")
CHECK_IDS = ("eligibility", "construct", "protocol", "units", "direction", "score-scale",
             "geography", "stream-type", "source-limits", "extrapolation")

#: index tolerance when a curve is evaluated against a value
INDEX_TOLERANCE = 0.011


# --------------------------------------------------------------------------- #
# fingerprints
# --------------------------------------------------------------------------- #
def canonical_json(obj: Any) -> str:
    """The one serialization every fingerprint hashes: sorted keys, no spaces, ASCII."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fingerprint(obj: Any) -> str:
    """``sha256:<hex>`` of :func:`canonical_json`."""
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("ascii")).hexdigest()


def points_fingerprint(points: Iterable[Mapping]) -> str:
    """Fingerprint of a point list as ``[[x, y], ...]`` in its stored order."""
    return fingerprint([[float(p["x"]), float(p["y"])] for p in points])


def content_fingerprint(record: Mapping) -> str:
    """Fingerprint of a record's content (everything except its ``frozen`` block)."""
    body = {k: v for k, v in dict(record).items() if k != "frozen"}
    return fingerprint(body)


# --------------------------------------------------------------------------- #
# loading (cached, read-only)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=4)
def _doc(path_text: str) -> dict:
    p = Path(path_text)
    if not p.exists():
        return {"schemaVersion": SCHEMA_VERSION, "records": [], "findings": [], "summary": {}}
    return json.loads(p.read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def _index(path_text: str) -> dict:
    return {r["key"]: r for r in _doc(path_text).get("records") or []}


def _path(path: Optional[Path]) -> str:
    return str(path or REGISTRY_PATH)


def clear_cache() -> None:
    _doc.cache_clear()
    _index.cache_clear()


def load(path: Optional[Path] = None) -> dict:
    """The whole registry document (a copy: the cache is never handed out)."""
    return copy.deepcopy(_doc(_path(path)))


def available(path: Optional[Path] = None) -> bool:
    """True when the registry file exists and holds records (cheap: no copy)."""
    return bool(_doc(_path(path)).get("records"))


def registry_fingerprint(path: Optional[Path] = None) -> Optional[str]:
    """``sha256:`` of the registry file's bytes, or None when it is absent."""
    p = Path(_path(path))
    return ("sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()) if p.exists() else None


def record(key: str, path: Optional[Path] = None) -> Optional[dict]:
    """One record by key (a copy), or None when the registry has no such key."""
    r = _index(_path(path)).get(str(key or ""))
    return copy.deepcopy(r) if r is not None else None


def _norm(text: Any) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _matches_metric(r: Mapping, metric: str) -> bool:
    m = _norm(metric)
    key_slug = str(r.get("key") or "").split(":")[2] if r.get("key") else ""
    return m in (_norm(r.get("stafMetricId")), _norm(r.get("originalMetricName")),
                 _norm(key_slug))


def _matches_function(r: Mapping, function: str) -> bool:
    f = _norm(function)
    fn = r.get("function") or {}
    return f in (_norm(fn.get("id")), _norm(fn.get("name")), _norm(fn.get("mappedFunction")))


def _matches_state(r: Mapping, state: str) -> bool:
    s = _norm(state)
    return s in (_norm(r.get("state")), _norm(r.get("stateName")))


def records(state: Optional[str] = None, function: Optional[str] = None,
            metric: Optional[str] = None, edition: Optional[str] = None,
            applicable_to: Optional[Mapping] = None, *, eligible: Optional[bool] = None,
            path: Optional[Path] = None) -> list[dict]:
    """Records matching every filter given, in registry (key) order, as copies.

    ``state``: two-letter code or state name. ``function``: STAF function id or name.
    ``metric``: STAF metric id, the SQT metric name or the key's metric slug.
    ``edition``: text contained in the record's edition (``"2020"`` finds ``"MN SQT 2020"``).
    ``applicable_to``: an :func:`applicability` context; keeps records with no failed
    check. ``eligible``: True or False keeps only eligible or only ineligible records.
    """
    out = []
    for r in _doc(_path(path)).get("records") or []:
        if state is not None and not _matches_state(r, state):
            continue
        if function is not None and not _matches_function(r, function):
            continue
        if metric is not None and not _matches_metric(r, metric):
            continue
        if edition is not None and _norm(edition) not in _norm(r.get("edition")):
            continue
        if eligible is not None and bool(r.get("eligible")) != bool(eligible):
            continue
        if applicable_to is not None and overall(applicability(r, applicable_to)) == "fail":
            continue
        out.append(copy.deepcopy(r))
    return out


# --------------------------------------------------------------------------- #
# curve arithmetic
# --------------------------------------------------------------------------- #
def _pts(points: Iterable[Mapping]) -> list[tuple[float, float]]:
    return [(float(p["x"]), float(p["y"])) for p in points
            if p.get("x") is not None and p.get("y") is not None]


def evaluate(points: Iterable[Mapping], x: float, extrapolation: str = "unknown"
             ) -> Optional[float]:
    """Index of a record's curve at ``x``: linear between points (stored ascending in x).

    Past the first or last point: ``flat`` holds the end value; ``linear`` extends the end
    segment until the index reaches 0 or 1 and holds it there; ``unknown`` returns None.
    """
    pts = _pts(points)
    if not pts or x is None or math.isnan(float(x)):
        return None
    x = float(x)
    if len(pts) == 1:
        return pts[0][1] if x == pts[0][0] else None
    x0, xn = pts[0][0], pts[-1][0]
    if x0 <= x <= xn:
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            if ax <= x <= bx:
                if bx == ax:
                    return by
                return ay + (x - ax) / (bx - ax) * (by - ay)
    if extrapolation == "flat":
        return pts[0][1] if x < x0 else pts[-1][1]
    if extrapolation == "linear":
        (ax, ay), (bx, by) = (pts[0], pts[1]) if x < x0 else (pts[-2], pts[-1])
        if bx == ax:
            return ay if x < x0 else by
        y = ay + (x - ax) / (bx - ax) * (by - ay)
        return min(1.0, max(0.0, y))
    return None


def band(index: Optional[float], scale: Mapping = SQT_SCORE_SCALE) -> Optional[str]:
    """``NF`` / ``FAR`` / ``F`` of an index on a score scale (SQT by default)."""
    if index is None:
        return None
    b = (scale or {}).get("bands") or {}
    nf, far = float(b.get("notFunctioning", 0.3)), float(b.get("functioningAtRisk", 0.7))
    if (scale or {}).get("kind") == "staf-0-1":
        return "NF" if index <= nf else "FAR" if index <= far else "F"
    return "NF" if index < nf else "FAR" if index < far else "F"


def open_ends(r: Mapping) -> list[str]:
    """``low`` and/or ``high``: the ends of a record's curve that stop inside (0, 1)."""
    pts = _pts(r.get("normalizedPoints") or [])
    if len(pts) < 2:
        return []
    ends = []
    if 0.0 < pts[0][1] < 1.0:
        ends.append("low")
    if 0.0 < pts[-1][1] < 1.0:
        ends.append("high")
    return ends


# --------------------------------------------------------------------------- #
# applicability
# --------------------------------------------------------------------------- #
def _check(cid: str, status: str, detail: str) -> dict:
    return {"id": cid, "status": status, "detail": detail}


def _fmt(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:g}"


def _eligibility(r: Mapping) -> dict:
    if r.get("eligible"):
        status = (r.get("verification") or {}).get("status")
        if status == "unverified":
            return _check("eligibility", "warn",
                          "STAF adaptation, not verified against the original SQT.")
        return _check("eligibility", "pass", f"Eligible; verification: {status}.")
    codes = sorted({i.get("code") for i in r.get("issues") or []
                    if i.get("severity") == "defect"})
    return _check("eligibility", "fail",
                  "Not eligible: " + (", ".join(codes) if codes else "defect recorded") + ".")


def _construct(r: Mapping, ctx: Mapping) -> dict:
    target = ctx.get("stafMetricId") or ctx.get("metric")
    fn = ctx.get("function")
    rid = r.get("stafMetricId")
    rfn = r.get("function") or {}
    if target:
        if _norm(target) in (_norm(rid), _norm(r.get("originalMetricName"))):
            return _check("construct", "pass", f"Same metric ({rid}).")
        if fn and _norm(fn) in (_norm(rfn.get("id")), _norm(rfn.get("name"))):
            return _check("construct", "warn",
                          f"Same STAF function ({rfn.get('name')}), different metric: "
                          f"the SQT scores {r.get('originalMetricName')}.")
        return _check("construct", "fail",
                      f"Different metric: the SQT scores {r.get('originalMetricName')} "
                      f"({rid}).")
    if fn:
        if _norm(fn) in (_norm(rfn.get("id")), _norm(rfn.get("name"))):
            return _check("construct", "warn",
                          f"Same STAF function ({rfn.get('name')}); the target metric "
                          "is not given.")
        return _check("construct", "fail",
                      f"Different STAF function: the SQT curve serves {rfn.get('name')}.")
    return _check("construct", "unknown", "No target metric or function given.")


def _protocol(r: Mapping, ctx: Mapping) -> dict:
    rp, tp = r.get("protocol"), ctx.get("protocol")
    if not rp:
        return _check("protocol", "unknown",
                      "The SQT source names no field protocol for this curve.")
    if not tp:
        return _check("protocol", "unknown", f"The SQT uses {rp}; the target's is not given.")
    if _norm(rp) == _norm(tp):
        return _check("protocol", "pass", f"Same protocol ({rp}).")
    return _check("protocol", "warn", f"The SQT uses {rp}; the target uses {tp}.")


def _units(r: Mapping, ctx: Mapping) -> dict:
    ru, tu = r.get("units"), ctx.get("units")
    if not ru:
        return _check("units", "unknown",
                      "Units are not stated in the source; confirm the target is measured "
                      "the same way before scoring on this curve.")
    if not tu:
        return _check("units", "unknown", f"The SQT measures in {ru}; the target's units "
                                          "are not given.")
    if _norm(ru) == _norm(tu):
        return _check("units", "pass", f"Same units ({ru}).")
    return _check("units", "fail", f"The SQT measures in {ru}; the target in {tu}.")


def _direction(r: Mapping, ctx: Mapping) -> dict:
    rd, td = r.get("direction") or "unknown", ctx.get("direction")
    if rd == "unknown":
        return _check("direction", "unknown", "The source curve has no usable direction.")
    if not td:
        return _check("direction", "unknown", f"The SQT curve is {rd}; the target's "
                                              "expected direction is not given.")
    if _norm(td) == rd:
        return _check("direction", "pass", f"Both {rd}.")
    if "two-sided" in (rd, _norm(td)):
        return _check("direction", "warn", f"The SQT curve is {rd}; the target is {td}.")
    return _check("direction", "fail", f"Opposite directions: the SQT curve is {rd}, "
                                       f"the target is {td}.")


def _score_scale(r: Mapping, ctx: Mapping) -> dict:
    kind = ((r.get("scoreScale") or {}).get("kind")) or SQT_SCORE_SCALE["kind"]
    target = _norm(ctx.get("scoreScale") or "staf")
    if kind == SQT_SCORE_SCALE["kind"] and target in ("sqt", "sqt-0-1"):
        return _check("score-scale", "pass", "Both use the SQT bands (0.30 and 0.70).")
    if kind == SQT_SCORE_SCALE["kind"]:
        return _check("score-scale", "warn",
                      "SQT bands differ from STAF: the SQT calls an index below 0.30 Not "
                      "Functioning and below 0.70 At Risk; STAF and DEEP use 0.39 and 0.69, "
                      "so an SQT index from 0.30 to 0.39 is At Risk in the SQT and Not "
                      "Functioning in DEEP.")
    return _check("score-scale", "unknown", f"Unrecognized score scale {kind}.")


def _states(ctx: Mapping) -> list[str]:
    states = ctx.get("states")
    if states is None and ctx.get("state"):
        states = [ctx.get("state")]
    return [str(s).strip().upper() for s in (states or []) if str(s).strip()]


def _geography(r: Mapping, ctx: Mapping) -> dict:
    geo = r.get("geography") or {}
    state = str(geo.get("state") or r.get("state") or "").upper()
    region = geo.get("region")
    states = _states(ctx)
    if not states:
        extra = f" It applies only to {region}." if region else ""
        return _check("geography", "unknown",
                      f"No target states given; the curve is from the {state} SQT.{extra}")
    if state not in states:
        return _check("geography", "fail",
                      f"The curve is from the {state} SQT; the target spans "
                      f"{', '.join(states)}.")
    if region:
        return _check("geography", "warn",
                      f"In {state}, but the curve applies only to {region}.")
    return _check("geography", "pass", f"The target includes {state}.")


def _stream_type(r: Mapping, ctx: Mapping) -> dict:
    st = r.get("streamTypes") or None
    target = str(ctx.get("streamType") or "").strip()
    if not st:
        return _check("stream-type", "pass", "Not stratified by stream type.")
    types = list(st.get("types") or [])
    excluded = list(st.get("except") or [])
    words = (", ".join(types) + " stream types") if types else \
        ("stream types other than " + ", ".join(excluded))
    if not target:
        return _check("stream-type", "unknown",
                      f"The curve is for {words}; the target's stream type is not given.")
    t = target.upper()
    if types:
        if any(t == x.upper() for x in types):
            return _check("stream-type", "pass", f"The curve covers stream type {target}.")
        if any(t[:1] == x.upper()[:1] for x in types):
            return _check("stream-type", "warn",
                          f"The curve is for {words}; {target} is a variant of the same type.")
        return _check("stream-type", "fail", f"The curve is for {words}, not {target}.")
    if any(t == x.upper() for x in excluded):
        return _check("stream-type", "fail", f"The curve is for {words}, not {target}.")
    return _check("stream-type", "pass", f"The curve covers stream type {target}.")


def _source_limits(r: Mapping, ctx: Mapping) -> dict:
    limits = [str(x) for x in r.get("sourceLimits") or [] if str(x).strip()]
    flow = _norm(ctx.get("flowType"))
    if not limits:
        matched = (r.get("verification") or {}).get("against")
        if matched:
            return _check("source-limits", "pass", "The original states no limits.")
        return _check("source-limits", "unknown", "No original on file to state limits.")
    text = " ".join(limits)
    if flow and "not applicable" in text.lower() and flow in text.lower():
        return _check("source-limits", "fail", f"The original says: {text}")
    return _check("source-limits", "warn", f"The original says: {text}")


def _extrapolation(r: Mapping, ctx: Mapping) -> dict:
    pts = _pts(r.get("normalizedPoints") or [])
    ext = r.get("extrapolation") or "unknown"
    thresholds = r.get("thresholds") or []
    if len(pts) < 2:
        return _check("extrapolation", "fail", "The source gives fewer than two points.")
    ends = open_ends(r)
    xr = ctx.get("xRange")
    lo = hi = None
    try:
        vals = [float(v) for v in (xr or [])]
    except (TypeError, ValueError):
        vals = []
    if len(vals) == 2 and not any(math.isnan(v) for v in vals):
        lo, hi = min(vals), max(vals)
    thr = ""
    if thresholds:
        thr = " Part of the range is scored by threshold bins: " + "; ".join(
            f"{t.get('op')}{_fmt(t.get('value'))} scores {_fmt(t.get('index'))}"
            for t in thresholds) + "."
    x0, xn = pts[0][0], pts[-1][0]
    if lo is None:
        if not ends:
            return _check("extrapolation", "warn" if thr else "pass",
                          "The curve runs from index 0 to 1; past its ends it holds 0 or 1."
                          + thr)
        return _check("extrapolation", "warn" if ext == "linear" else "unknown",
                      f"The curve stops inside the index range at its {' and '.join(ends)} "
                      f"end; the source's behaviour past it is {ext}." + thr)
    beyond = []
    if lo < x0:
        beyond.append(("low", x0))
    if hi > xn:
        beyond.append(("high", xn))
    if not beyond:
        return _check("extrapolation", "pass",
                      f"The target range {_fmt(lo)} to {_fmt(hi)} lies inside the curve's "
                      f"{_fmt(x0)} to {_fmt(xn)}." + thr)
    open_beyond = [(e, x) for e, x in beyond if e in ends]
    if not open_beyond:
        return _check("extrapolation", "warn" if thr else "pass",
                      "The target runs past the curve's ends, where it holds 0 or 1." + thr)
    where = " and ".join(f"past {_fmt(x)}" for _e, x in open_beyond)
    if ext == "linear":
        return _check("extrapolation", "warn",
                      f"The target runs {where}; the source extends its end segment "
                      "linearly there." + thr)
    return _check("extrapolation", "fail",
                  f"The target runs {where}, where the source does not say how it scores."
                  + thr)


def applicability(record_: Mapping, context: Optional[Mapping] = None) -> list[dict]:
    """Explicit checks of one record against a target, each ``{id, status, detail}``.

    ``status`` is ``pass``, ``warn``, ``fail`` or ``unknown``. Context keys, all optional:
    ``stafMetricId`` (or ``metric``), ``function`` (STAF id or name), ``protocol``,
    ``units``, ``direction`` (increasing, decreasing or two-sided), ``scoreScale`` (``staf``
    by default, or ``sqt``), ``states`` (two-letter codes of the target ecoregion's states,
    or ``state``), ``streamType`` (a Rosgen type such as ``C``), ``flowType`` (perennial,
    intermittent or ephemeral) and ``xRange`` (``[min, max]`` of the values to score).
    """
    r = record_ or {}
    ctx = dict(context or {})
    return [
        _eligibility(r),
        _construct(r, ctx),
        _protocol(r, ctx),
        _units(r, ctx),
        _direction(r, ctx),
        _score_scale(r, ctx),
        _geography(r, ctx),
        _stream_type(r, ctx),
        _source_limits(r, ctx),
        _extrapolation(r, ctx),
    ]


def overall(checks: Sequence[Mapping]) -> str:
    """The worst status of a check list: fail, then warn, then unknown, then pass."""
    statuses = {str(c.get("status")) for c in checks or []}
    for s in ("fail", "warn", "unknown"):
        if s in statuses:
            return s
    return "pass"


# --------------------------------------------------------------------------- #
# adoption
# --------------------------------------------------------------------------- #
def frozen_copy(record_: Mapping) -> dict:
    """A deep copy of a record for a project, stamped with its content fingerprint.

    The copy carries everything the curve needs (points, extrapolation, citation, issues),
    so it scores the same whatever later builds do to the registry. ``frozen`` holds
    ``contentFingerprint`` (over every other field) and the registry key it came from.
    """
    body = copy.deepcopy({k: v for k, v in dict(record_).items() if k != "frozen"})
    body["frozen"] = {
        "contentFingerprint": content_fingerprint(body),
        "registryKey": body.get("key"),
        "schemaVersion": SCHEMA_VERSION,
    }
    return body


def frozen_intact(frozen: Mapping) -> bool:
    """True when a frozen copy's content still hashes to its recorded fingerprint."""
    stamp = (frozen or {}).get("frozen") or {}
    return bool(stamp) and stamp.get("contentFingerprint") == content_fingerprint(frozen)
