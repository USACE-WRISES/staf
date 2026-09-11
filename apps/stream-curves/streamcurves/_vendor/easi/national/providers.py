"""Stored answers for the point-keyed services.

Four adapters call a federal service by location (ATTAINS, WQP, NID) or by HUC
(NAS) through module attributes: ``attains.impairment_at_point`` /
``impairment_near_point``, ``wqp.sample_summary``, ``nid_barriers.barriers_near``
and ``nas.established_taxa``. A precomputed record carries those answers, so
while it is being scored the wrapped functions return the stored answer for
*that point* (or HUC) and fall through to the live service for any other point.

Keying the registry by the normalized point rather than by thread is what keeps
this safe inside the app: a live assessment of a different reach running in
another session never sees a stored answer, and the nutrients adapter's worker
threads (which do not inherit context variables) still find it.

A stored ``None`` means the builder has no answer; the wrapper then returns the
value the adapter treats as "service unavailable" instead of calling the live
service, so the precomputed path never touches the network.
"""
from __future__ import annotations

import copy
import threading
from contextlib import contextmanager
from typing import Any, Optional

from ..datasources import attains, nas, nid_barriers, wqp

_LOCK = threading.Lock()
_POINTS: dict[tuple[float, float], list] = {}     # key -> [refcount, answers]
_HUCS: dict[str, list] = {}                         # huc -> [refcount, answers]
_ORIGINALS: dict[str, Any] = {}


def point_key(lat: float, lon: float) -> tuple[float, float]:
    """The normalized point (six decimals, ``SiteRequest.key()``'s rule)."""
    return (round(float(lat), 6), round(float(lon), 6))


def answers_for(record: dict) -> dict:
    """The stored point-service answers of a record."""
    return {key: record.get(key) for key in
            ("attains_exact", "attains_nearby", "wqp_tn", "wqp_tp",
             "nid_dams", "nas_taxa", "nas_scope")}


def _acquire(table: dict, key, answers: dict) -> None:
    entry = table.get(key)
    if entry is None:
        table[key] = [1, answers]
    else:
        entry[0] += 1
        entry[1] = answers


def _release(table: dict, key) -> None:
    entry = table.get(key)
    if entry is None:
        return
    entry[0] -= 1
    if entry[0] <= 0:
        del table[key]


def _huc_keys(record: dict) -> list[str]:
    huc12, huc8 = record.get("huc12"), record.get("huc8")
    scope = record.get("nas_scope")
    if scope == "huc8" and huc8:
        return [str(huc8)]
    if huc12:
        return [str(huc12)]
    return [str(huc8)] if huc8 else []


@contextmanager
def preloaded(record: dict):
    """Register the record's answers for its point and HUC for the duration."""
    install()
    key = point_key(record["lat"], record["lon"])
    answers = answers_for(record)
    hucs = _huc_keys(record)
    with _LOCK:
        _acquire(_POINTS, key, answers)
        for huc in hucs:
            _acquire(_HUCS, huc, answers)
    try:
        yield answers
    finally:
        with _LOCK:
            _release(_POINTS, key)
            for huc in hucs:
                _release(_HUCS, huc)


def active_points() -> int:
    """How many points are registered (a test and diagnostics hook)."""
    with _LOCK:
        return len(_POINTS)


def _point_answers(lat, lon) -> Optional[dict]:
    try:
        key = point_key(lat, lon)
    except (TypeError, ValueError):
        return None
    with _LOCK:
        entry = _POINTS.get(key)
        return entry[1] if entry else None


def _huc_answers(*hucs) -> Optional[dict]:
    with _LOCK:
        for huc in hucs:
            if huc:
                entry = _HUCS.get(str(huc))
                if entry:
                    return entry[1]
    return None


def _impairment_at_point(lat, lon, *args, **kwargs):
    answers = _point_answers(lat, lon)
    if answers is None:
        return _ORIGINALS["impairment_at_point"](lat, lon, *args, **kwargs)
    stored = answers.get("attains_exact")
    return copy.deepcopy(stored) if isinstance(stored, dict) else {}


def _impairment_near_point(lat, lon, *args, **kwargs):
    answers = _point_answers(lat, lon)
    if answers is None:
        return _ORIGINALS["impairment_near_point"](lat, lon, *args, **kwargs)
    stored = answers.get("attains_nearby")
    return copy.deepcopy(stored) if isinstance(stored, dict) else {}


def _sample_summary(param, lat, lon, *args, **kwargs):
    answers = _point_answers(lat, lon)
    if answers is None:
        return _ORIGINALS["sample_summary"](param, lat, lon, *args, **kwargs)
    stored = answers.get(f"wqp_{param}")
    return copy.deepcopy(stored) if isinstance(stored, dict) else None


def _barriers_near(lat, lon, *args, **kwargs):
    answers = _point_answers(lat, lon)
    if answers is None:
        return _ORIGINALS["barriers_near"](lat, lon, *args, **kwargs)
    stored = answers.get("nid_dams")
    return copy.deepcopy(list(stored)) if isinstance(stored, (list, tuple)) else None


def _established_taxa(huc12=None, huc8=None, *args, **kwargs):
    answers = _huc_answers(huc12, huc8)
    if answers is None:
        return _ORIGINALS["established_taxa"](huc12, huc8, *args, **kwargs)
    stored = answers.get("nas_taxa")
    return list(stored) if isinstance(stored, (list, tuple)) else None


_WRAPPERS = (
    (attains, "impairment_at_point", _impairment_at_point),
    (attains, "impairment_near_point", _impairment_near_point),
    (wqp, "sample_summary", _sample_summary),
    (nid_barriers, "barriers_near", _barriers_near),
    (nas, "established_taxa", _established_taxa),
)


def install() -> None:
    """Wrap the four services once (idempotent)."""
    with _LOCK:
        for module, name, wrapper in _WRAPPERS:
            current = getattr(module, name)
            if current is wrapper:
                continue
            _ORIGINALS.setdefault(name, current)
            setattr(module, name, wrapper)


def uninstall() -> None:
    """Restore the live functions (tests)."""
    with _LOCK:
        for module, name, wrapper in _WRAPPERS:
            if getattr(module, name) is wrapper and name in _ORIGINALS:
                setattr(module, name, _ORIGINALS.pop(name))
        _POINTS.clear()
        _HUCS.clear()
