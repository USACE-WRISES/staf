"""Published curves are carried forward (methodology 0.14).

A rebuild of an ecoregion that already has a published version keeps every
curve that version scores: the same points, class layers, annotations, source
and function placement. Only missing or newly developed curves walk the
reference-source hierarchy, so a new methodology version fills gaps without
silently moving what is already in use.

One exception, decided from data rather than by hand: a curve is rebuilt when a
value in its published pool is one the data verification corrected or removed.

  - A pool curve (local or regional stations) carries its station values in the
    published session. Each is checked against every value the verified archive
    holds for that station in a compatible, protocol-valid survey
    (``nrsa_dataset.valid_cycle_values``). A value no such survey holds was
    wrong or incompatible, and one such value is enough to rebuild the curve.
  - A national or modeled curve carries no station values of its own. It is
    rebuilt when its metric is one whose archive values were corrected
    (``nrsa_dataset.corrected_metrics``).
  - A published criterion rests on no station and is never rebuilt for data.

Values the verification added for stations that had none are not defects: they
are information the preserved curve did not use, and the curve stays.

A curve the published version itself carried forward is a curve it scores, and
is carried again. It keeps the ``carriedForward`` naming the version that built
it (REF-05 records that its reference support was decided there), and it is
judged where it was built: a pool curve's station values are read from that
version's session. When that version cannot be read, the curve is judged as a
national or modeled curve is, by whether its metric's archive values were
corrected.

A curve the owner chose (REF-15, an entry carrying ``ownerDecision``) is never
carried: the region's curve decisions are its standing record. While the
decision stands the next build keeps the metric out of its own fit ("your choice
stands") and the decision puts the owner's curve back; once the owner has
withdrawn it, the metric walks the hierarchy like any other.

Reads the canonical library only. Pure otherwise: no network, no file writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from . import curve_basis
from . import nrsa_dataset as nd
from .deep_export import deep_slug

#: the bundle entry fields a carried curve keeps, which are exactly the fields
#: the exporter reads from a metric's annotations
ANNOTATION_KEYS = ("referenceN", "sampleDisposition", "metricRole", "curveCaveats",
                   "confidenceLabel", "confidenceTotal", "referenceRange", "criteriaBasis",
                   "criteriaSource", "referenceSupport", "localComparison", "stratifier",
                   "discrimination", "basis", "basisLabel", "basisStatement", "basisLimit",
                   "publishedBenchmark", "methodContext", "sourceCitation")
#: camelCase bundle record -> the decision dict a run's reference support holds
_SUPPORT_FIELDS = {"status": "status", "level": "level", "regionCode": "region_code",
                   "regionName": "region_name", "nPool": "n_pool",
                   "nComparable": "n_comparable", "nUsable": "n_usable", "nLocal": "n_local",
                   "nHuc12": "n_huc12", "disposition": "disposition", "family": "family",
                   "selfCoverage": "self_coverage", "supportedLevel": "supported_level",
                   "transferRisk": "transfer_risk", "transferNote": "transfer_note",
                   "basis": "basis"}
CURVE_SOURCE = "carried_forward"


def _library_root(root: Optional[Path] = None) -> Path:
    from . import library as lib
    return Path(root) if root is not None else lib.canonical_root()


def find_published(l3_code: str, *, root: Optional[Path] = None) -> Optional[tuple[str, int]]:
    """``(assessment id, latest version)`` of the ecoregion's published
    assessment in the canonical library, or None."""
    base = _library_root(root) / "assessments"
    if not base.is_dir():
        return None
    for mpath in sorted(base.glob("*/manifest.json")):
        try:
            man = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        reg = man.get("region") or {}
        ver = int(man.get("latestVersion") or 0)
        if reg.get("kind") == "ecoregion" and str(reg.get("code")) == str(l3_code) and ver > 0:
            return str(man.get("assessmentId") or mpath.parent.name), ver
    return None


def _points_frame(points: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"point_order": i + 1, "metric_value": float(p["x"]),
                          "index_score": float(p["y"])} for i, p in enumerate(points or [])])


def _decision_from_record(rec: dict, metric: str, version: int) -> dict:
    out = {"metric": metric}
    for k, v in _SUPPORT_FIELDS.items():
        if k in (rec or {}):
            out[v] = rec[k]
    out["covariates"] = [c for c in (rec or {}).get("covariates") or [] if c != "lith_group"]
    out["basis"] = curve_basis.resolve(out.get("basis"))
    out["carried_from"] = int(version)
    return out


def _pool_defects(metric: str, pool: pd.Series) -> dict:
    """How many of a published pool's station values the verified archive does
    not hold in any compatible, protocol-valid survey of that station."""
    pool = pd.to_numeric(pool, errors="coerce").dropna()
    if not len(pool):
        return {"checked": False, "n_pool": 0, "n_defective": 0}
    valid = nd.valid_cycle_values(list(pool.index.astype(str)), metric)
    if not len(valid):
        # a landscape metric, not an NRSA field measurement: nothing to check
        return {"checked": False, "n_pool": int(len(pool)), "n_defective": 0}
    by_station = valid.groupby(valid["station_key"].astype(str))["value"].apply(
        lambda s: s.to_numpy(dtype=float))
    bad = 0
    for station, value in pool.items():
        held = by_station.get(str(station))
        if held is None or not np.isclose(held, float(value), rtol=1e-6, atol=1e-9).any():
            bad += 1
    return {"checked": True, "n_pool": int(len(pool)), "n_defective": int(bad)}


def _session_pool(session_data: Any, metric: str) -> Optional[pd.Series]:
    """A metric's station values in a session's data (its pool), or None."""
    if not isinstance(session_data, pd.DataFrame) or metric not in session_data.columns:
        return None
    key_col = "site_id" if "site_id" in session_data.columns else "station_key"
    return pd.Series(pd.to_numeric(session_data[metric], errors="coerce").to_numpy(),
                     index=session_data[key_col].astype(str)).dropna()


def _origin_data(origin: dict, cache: dict, *, root: Optional[Path] = None) -> Any:
    """The session data of the version a carried curve was built in (its
    ``carriedForward``), read once per version; None when it cannot be read."""
    from . import library as lib
    from . import session_io as sio
    try:
        key = (str(origin.get("assessmentId") or ""), int(origin.get("fromVersion")))
    except (TypeError, ValueError):
        return None
    if key not in cache:
        vdir = _library_root(root) / "assessments" / key[0] / f"v{key[1]}"
        try:
            cache[key] = sio.decode_session_fields(
                sio.load_session_payload(vdir / lib.SESSION_FILE)).get("data")
        except (OSError, ValueError, KeyError, TypeError):
            cache[key] = None
    return cache[key]


def _recarried_why(metric: str, block: dict, basis: str, corrected, cache: dict, *,
                   root: Optional[Path] = None) -> Optional[dict]:
    """Why a curve the published version itself carried forward is rebuilt, judged
    where it was built, or None when it carries again."""
    origin = block.get("carriedForward") if isinstance(block.get("carriedForward"), dict) else {}
    was = origin.get("fromVersion")
    if basis == curve_basis.PUBLISHED:
        return None
    pool = (None if basis in (curve_basis.NATIONAL, curve_basis.MODELED)
            else _session_pool(_origin_data(origin, cache, root=root), metric))
    if pool is None:
        # a national or modeled curve, or a pool the version that built it no
        # longer shows here: judged by whether the metric's values were corrected
        reason = nd.is_corrected(metric, corrected)
        if not reason:
            return None
        return {"why": (f"The curve carried from version {was} rests on this metric's archive "
                        f"values, which the data verification corrected ({reason}).")}
    got = _pool_defects(metric, pool)
    if not got["n_defective"]:
        return None
    return {"why": (f"{got['n_defective']} of the {got['n_pool']} station values the curve was "
                    f"built on in version {was} are not values the verified archive holds for "
                    f"those stations in a compatible survey, so the curve is rebuilt from "
                    f"corrected data."), **got}


def prepare(l3_code: str, *, root: Optional[Path] = None) -> dict:
    """What a rebuild of ``l3_code`` carries forward from its latest published
    version, and what it rebuilds and why.

    Returns ``{}`` when the ecoregion has no published version. Otherwise
    ``assessmentId``, ``fromVersion``, ``contentDigest``, ``carried`` (metric ->
    the curve row, config, annotations, mapping rows and reference support to
    restore), ``rebuilt`` (metric -> why it goes through the hierarchy) and
    ``approvals`` (the version's SELECT-01 approvals, each with the signature of
    the function block it approved, for :func:`carried_approvals`).
    """
    from . import library as lib
    from . import session_io as sio
    found = find_published(l3_code, root=root)
    if not found:
        return {}
    aid, ver = found
    vdir = _library_root(root) / "assessments" / aid / f"v{ver}"
    bundle = json.loads((vdir / lib.BUNDLE_FILE).read_text(encoding="utf-8"))
    fields = sio.decode_session_fields(sio.load_session_payload(vdir / lib.SESSION_FILE))
    build = fields.get("reference_build") or {}
    out: dict[str, Any] = {"assessmentId": aid, "fromVersion": ver,
                           "contentDigest": bundle.get("contentDigest"),
                           "carried": {}, "rebuilt": {},
                           "approvals": _prior_approvals(vdir, bundle)}
    if build.get("method") != "pressure-screen":
        out["legacy"] = True
        return out
    config = dict(fields.get("metric_config") or {})
    ladder = dict(build.get("ladderMetrics") or {})
    fixed = set(build.get("fixedMetrics") or [])
    # the curves the version itself carried forward are curves it scores too
    recarried = {mk: c for mk, c in (build.get("carriedMetrics") or {}).items()
                 if mk not in config and mk not in ladder}
    data = fields.get("data")
    keys = set(config) | set(ladder) | fixed | set(recarried)
    by_id = {"spring-" + deep_slug(k): k for k in keys}
    origins: dict = {}

    blocks: dict[str, dict] = {}
    functions: dict[str, list[dict]] = {}
    for fn in bundle.get("metricsByFunction") or []:
        for m in fn.get("metrics") or []:
            mk = by_id.get(str(m.get("metricId")))
            if mk is None or mk in fixed or m.get("ownerDecision"):
                continue
            blocks.setdefault(mk, m)
            functions.setdefault(mk, []).append(
                {"functionId": fn.get("functionId"), "functionName": fn.get("functionName"),
                 "discipline": m.get("discipline") or fn.get("discipline")})

    corrected = nd.corrected_metrics()
    for mk, block in sorted(blocks.items()):
        basis = curve_basis.resolve(block.get("basis"), criteria_basis=block.get("criteriaBasis"))
        why = None
        if mk in ladder:
            if basis != curve_basis.PUBLISHED:
                reason = nd.is_corrected(mk, corrected)
                if reason:
                    why = (f"The published {curve_basis.label_for(basis).lower()} rests on this "
                           f"metric's archive values, which the data verification corrected "
                           f"({reason}).")
        elif mk in recarried:
            got = _recarried_why(mk, block, basis, corrected, origins, root=root)
            if got:
                out["rebuilt"][mk] = got
                continue
        elif isinstance(data, pd.DataFrame) and mk in data.columns:
            key_col = "site_id" if "site_id" in data.columns else "station_key"
            pool = pd.Series(pd.to_numeric(data[mk], errors="coerce").to_numpy(),
                             index=data[key_col].astype(str)).dropna()
            got = _pool_defects(mk, pool)
            if got["n_defective"]:
                why = (f"{got['n_defective']} of the {got['n_pool']} station values the "
                       f"published curve was built on are not values the verified archive "
                       f"holds for those stations in a compatible survey, so the curve is "
                       f"rebuilt from corrected data.")
                out["rebuilt"][mk] = {"why": why, **got}
                continue
        if why:
            out["rebuilt"][mk] = {"why": why}
            continue
        curve = block.get("curve") or {}
        row: dict[str, Any] = {
            "metric": mk, "display_name": block.get("metricName") or mk,
            "stratum": curve.get("stratification") or "",
            "curve_status": block.get("curveStatus") or "complete",
            "curve_source": CURVE_SOURCE, "n_reference": block.get("referenceN"),
            "curve_points": _points_frame(curve.get("points") or [])}
        layers = block.get("curveLayers") or []
        if layers:
            row["all_strata"] = [{"stratum": L.get("stratum") or "",
                                  "curve_points": _points_frame(L.get("points") or [])}
                                 for L in layers]
        annotations = {k: block[k] for k in ANNOTATION_KEYS if k in block}
        origin = block.get("carriedForward")
        if mk in recarried and isinstance(origin, dict) and origin.get("fromVersion"):
            # carried again: it still names the version that built it
            annotations["carriedForward"] = dict(origin)
            built_in = int(origin["fromVersion"])
        else:
            annotations["carriedForward"] = {"assessmentId": aid, "fromVersion": ver,
                                             "contentDigest": bundle.get("contentDigest")}
            built_in = ver
        cfg = dict(config.get(mk) or (ladder.get(mk) or {}).get("config")
                   or (recarried.get(mk) or {}).get("config") or {})
        out["carried"][mk] = {
            "row": row, "config": cfg, "annotations": annotations,
            "mapping": [{"metric_key": mk, "discipline": f["discipline"],
                         "function_label": f["functionName"]} for f in functions.get(mk, [])],
            "functions": [f["functionId"] for f in functions.get(mk, [])],
            "decision": _decision_from_record(block.get("referenceSupport") or
                                              {"basis": basis}, mk, built_in),
            "basis": basis, "ladder": mk in ladder,
        }
    return out


def _round_points(points) -> list[list[float]]:
    return [[round(float(p["x"]), 9), round(float(p["y"]), 9)] for p in points or []]


def block_signature(block: Optional[dict]) -> dict:
    """``{metricId: {points, layers}}`` of one function block of a bundle: the
    exact metric set, and the curve of each, that a SELECT-01 approval of that
    function approved."""
    out: dict[str, dict] = {}
    for m in (block or {}).get("metrics") or []:
        curve = m.get("curve") or {}
        out[str(m.get("metricId"))] = {
            "points": _round_points(curve.get("points")),
            "layers": [[str(L.get("stratum") or ""), _round_points(L.get("points"))]
                       for L in m.get("curveLayers") or []],
        }
    return out


def _prior_approvals(vdir: Path, bundle: dict) -> list[dict]:
    """The published version's SELECT-01 approvals, each with the signature of the
    block it approved. A pending marker never carries: only an approval a named
    owner confirmed."""
    from . import decisions as dec
    from . import library as lib
    try:
        meta = json.loads((vdir / lib.META_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    blocks = {str(b.get("functionId")): b for b in bundle.get("metricsByFunction") or []}
    out = []
    for ap in meta.get("portfolioApprovals") or []:
        fid = str(ap.get("functionId") or "")
        who = str(ap.get("approvedBy") or "").strip()
        if not fid or not who or dec.PENDING_SUFFIX in who or fid not in blocks:
            continue
        out.append({"functionId": fid, "approvedBy": who, "note": str(ap.get("note") or ""),
                    "metrics": block_signature(blocks[fid])})
    return out


def carried_approvals(prior_approvals, bundle: Optional[dict], carried, fixed=(), *,
                      have=(), from_version=None) -> list[dict]:
    """The prior version's SELECT-01 approvals that stand in this build (owner
    decision, 2026-09-21): an approval carries when the function's metrics here
    are exactly the set it approved, each with the same curve, and every one is
    carried forward unchanged or a fixed criterion. It keeps its recorded
    approver, and its note says it carried. Any change to the set needs a new
    approval, and one the owner gave for this build (``have``) wins."""
    if not prior_approvals or not bundle:
        return []
    ok_ids = {"spring-" + deep_slug(k) for k in list(carried or {}) + list(fixed or [])}
    blocks = {str(b.get("functionId")): b for b in bundle.get("metricsByFunction") or []}
    have = {str(f) for f in have or ()}
    out = []
    for ap in prior_approvals:
        fid = str(ap.get("functionId") or "")
        if not fid or fid in have or fid not in blocks:
            continue
        now = block_signature(blocks[fid])
        if not now or now != ap.get("metrics") or not set(now) <= ok_ids:
            continue
        note = (f"Carried from v{from_version} with its approved metric set unchanged."
                if from_version else "Carried forward with its approved metric set unchanged.")
        if ap.get("note"):
            note += " " + str(ap["note"])
        entry = {"functionId": fid, "approvedBy": ap["approvedBy"], "note": note}
        if from_version:
            entry["carriedFrom"] = int(from_version)
        out.append(entry)
    return out


def mapping_rows(carried: dict) -> pd.DataFrame:
    """The function assignments of the carried curves, exactly as published."""
    rows = [r for c in (carried or {}).values() for r in c.get("mapping") or []]
    return pd.DataFrame(rows, columns=["metric_key", "discipline", "function_label"])


def session_rows(carried: dict) -> dict:
    """What a session stores so an interactive republish restores the carried
    curves exactly (the points are published facts, never refitted)."""
    out = {}
    for mk, c in (carried or {}).items():
        row = c["row"]
        pts = row["curve_points"]
        out[mk] = {
            "displayName": row.get("display_name") or mk,
            "curveStatus": row.get("curve_status") or "complete",
            "nReference": row.get("n_reference"), "stratum": row.get("stratum") or "",
            "points": [{"x": float(r.metric_value), "y": float(r.index_score)}
                       for r in pts.itertuples(index=False)],
            "layers": [{"stratum": L["stratum"],
                        "points": [{"x": float(r.metric_value), "y": float(r.index_score)}
                                   for r in L["curve_points"].itertuples(index=False)]}
                       for L in row.get("all_strata") or []],
            "config": c.get("config") or {}, "annotations": c.get("annotations") or {},
            "mapping": c.get("mapping") or [], "decision": c.get("decision") or {},
        }
    return out


def restore_rows(saved: dict) -> dict:
    """The inverse of :func:`session_rows`: ``{metric: carried entry}``."""
    out = {}
    for mk, s in (saved or {}).items():
        row = {"metric": mk, "display_name": s.get("displayName") or mk,
               "stratum": s.get("stratum") or "", "curve_status": s.get("curveStatus") or "complete",
               "curve_source": CURVE_SOURCE, "n_reference": s.get("nReference"),
               "curve_points": _points_frame(s.get("points") or [])}
        if s.get("layers"):
            row["all_strata"] = [{"stratum": L.get("stratum") or "",
                                  "curve_points": _points_frame(L.get("points") or [])}
                                 for L in s["layers"]]
        out[mk] = {"row": row, "config": s.get("config") or {},
                   "annotations": s.get("annotations") or {},
                   "mapping": s.get("mapping") or [], "decision": s.get("decision") or {}}
    return out
