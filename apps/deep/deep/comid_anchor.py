"""The StreamCat lookup engine's reach for a DEEP site.

DEEP snaps every click to the high-resolution NHD and the STAF site engine
computes the HR reach watershed there. The StreamCat lookup engine still
answers by NHDPlus V2 COMID: the EPA modeled integrity indices that exist only
per V2 reach (flow permanence, dewatered segments, barriers, nutrients) and a
labeled stand-in for a watershed value the engine could not compute. This
module finds that COMID with the vendored engine's shared classification
(``anchor.classify_click``, the rule EASI and DEEP use): a V2 line within the
snap tolerance of the click is the reach itself (``v2Direct``); otherwise the
NLDI raindrop from the HR snap point finds the nearest StreamCat reach downstream
(``hrSurrogate``) with the routed distance and the drainage-area ratio. The
ratio is reported on every value and never enforced: DEEP ignores the
payload's ``declined`` flag (2026-09-07).

``resolve`` is a worker-thread helper; the rest are pure label helpers over
the anchor payload. Never raises.
"""
from __future__ import annotations

from typing import Callable, Optional

from .datasources import flowlines

SNAP_TOL_FT = 150.0
PROBE_HALF_DEG = 0.012          # about 0.8 mi half-box for the V2 probe
ENGINE_NAME = "StreamCat lookup engine"
STREAMCAT_URL = "https://www.epa.gov/national-aquatic-resource-surveys/streamcat-dataset"


def _classify(lat: float, lon: float, *, v2_hit, hr_hit, snap_tol_ft: float,
              progress: Optional[Callable[[dict], None]] = None) -> dict:
    from deep._vendor.site_engine import anchor
    extra = {"progress": progress} if progress is not None else {}
    return anchor.classify_click(lat, lon, v2_hit=v2_hit, hr_hit=hr_hit,
                                 snap_tol_ft=snap_tol_ft, **extra)


def _feature_by_id(fc: Optional[dict], prop: str, value) -> Optional[dict]:
    if not fc or value is None:
        return None
    try:
        want = int(value)
    except (TypeError, ValueError):
        return None
    for feat in fc.get("features") or []:
        got = (feat.get("properties") or {}).get(prop)
        try:
            if got is not None and int(got) == want:
                return feat
        except (TypeError, ValueError):
            continue
    return None


def resolve(lat: float, lon: float, hr_hit, *, v2_fc: Optional[dict] = None,
            snap_tol_ft: float = SNAP_TOL_FT,
            progress: Optional[Callable[[dict], None]] = None) -> dict:
    """``{"anchor": payload}`` or ``{"error": code, "detail"}``.

    ``hr_hit`` is the HR snap ``(snap_lat, snap_lon, dist_ft, nhdplusid)`` the
    click landed on; ``v2_fc`` is the viewport's V2 layer when it is loaded,
    else the V2 lines around the point are fetched. A V2 line within
    ``snap_tol_ft`` of the click is the reach itself and needs no service
    call; otherwise the engine routes from the HR snap point.
    """
    try:
        fc = v2_fc if (v2_fc and v2_fc.get("features")) else None
        if fc is None:
            d = PROBE_HALF_DEG
            fc = flowlines.flowlines_in_bbox(lon - d, lat - d, lon + d, lat + d)
        v2_hit = (flowlines.nearest_point_on_lines(fc, lat, lon, id_prop="comid")
                  if fc else None)
        extra = {"progress": progress} if progress is not None else {}
        res = _classify(lat, lon, v2_hit=v2_hit,
                        hr_hit=tuple(hr_hit) if hr_hit else None,
                        snap_tol_ft=snap_tol_ft, **extra)
    except Exception as exc:  # noqa: BLE001 - resilience by design
        return {"error": "snap_service_error", "detail": str(exc)}
    if not isinstance(res, dict):
        return {"error": "snap_service_error", "detail": "unexpected response shape"}
    if res.get("error"):
        return res
    payload = res.get("anchor")
    if comid(payload) is None:
        return {"error": "snap_service_error", "detail": "unexpected response shape"}
    if payload and payload.get("anchorKind") == "v2Direct" and v2_hit:
        # the bbox features carry gnis_name; the engine's v2Direct payload does not
        feat = _feature_by_id(fc, "comid", v2_hit[3])
        name = ((feat or {}).get("properties") or {}).get("gnis_name")
        scored = payload.setdefault("scoredReach", {})
        if name and not scored.get("gnisName"):
            scored["gnisName"] = str(name).strip() or None
        _fill_covered_reach(scored)
    return res


def _v2_reach_attrs(comid_value) -> dict:
    """The NHDPlus V2 attributes of one reach from the fabric API (memoized
    there): the ``delineation.flowline_attrs`` keys plus ``reachcode``. ``{}``
    when the service has no answer. Never raises."""
    try:
        from .datasources import fabric
        feat = fabric.feature_by_comid(int(comid_value))
        if not feat:
            return {}
        out = fabric.attrs_from_feature(feat)
        rc = ((feat.get("properties") or {}).get("reachcode"))
        out["reachcode"] = str(rc) if rc else None
        return out
    except Exception:  # noqa: BLE001 - resilience by design
        return {}


_COVERED_FIELDS = (("drainageAreaSqkm", "drainage_area_sqkm"), ("slope", "slope"),
                   ("fcode", "fcode"), ("streamOrder", "stream_order"),
                   ("reachcode", "reachcode"), ("gnisName", "gnis_name"))


def _fill_covered_reach(scored: dict) -> None:
    """The engine's ``v2Direct`` payload leaves the reach's drainage area, slope,
    FCODE, stream order and reach code to the consumer (2026-09-07): fill
    them here, in the background task, so the no-watershed continuation on a
    covered stream keeps the site attributes the evidence pull needs."""
    if scored.get("comid") is None:
        return
    if all(scored.get(k) is not None for k, _ in _COVERED_FIELDS):
        return
    attrs = _v2_reach_attrs(scored["comid"])
    if not attrs:
        return
    for key, src in _COVERED_FIELDS:
        if scored.get(key) is None and attrs.get(src) is not None:
            scored[key] = attrs[src]


# --------------------------------------------------------------------------- #
# pure helpers over the anchor payload
# --------------------------------------------------------------------------- #
def comid(anchor: Optional[dict]) -> Optional[int]:
    """The COMID that keys the StreamCat values, whatever the drainage-area ratio."""
    if not isinstance(anchor, dict) or not isinstance(anchor.get("scoredReach"), dict):
        return None
    c = anchor["scoredReach"].get("comid")
    try:
        value = int(c)
        return value if not isinstance(c, bool) and value > 0 and float(c) == value else None
    except (TypeError, ValueError, OverflowError):
        return None


def is_routed(anchor: Optional[dict]) -> bool:
    return (anchor or {}).get("anchorKind") == "hrSurrogate"


def synthetic(comid_value) -> Optional[dict]:
    """A minimal covered-reach anchor for a session that carries a COMID but no
    classification (saved before 2026-09-07)."""
    try:
        value = comid({"scoredReach": {"comid": comid_value}})
        return {"anchorKind": "v2Direct", "anchorSchemaVersion": 1,
                "scoredReach": {"network": "nhdplus-v2", "comid": value},
                "notes": []} if value is not None else None
    except (TypeError, ValueError):
        return None


def fmt_ratio(value) -> Optional[str]:
    """``32`` past ten times, ``2.7`` below it, None when unknown."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return f"{v:.0f}" if v >= 10 else f"{v:.1f}"


def _dist(anchor: dict) -> Optional[str]:
    d = (anchor.get("routing") or {}).get("routedDistanceFt")
    try:
        return f"{float(d):,.0f} ft" if d is not None else None
    except (TypeError, ValueError):
        return None


def _ratio(anchor: dict) -> Optional[str]:
    return fmt_ratio((anchor.get("routing") or {}).get("daRatio"))


def reach_name(anchor: Optional[dict]) -> str:
    name = ((anchor or {}).get("scoredReach") or {}).get("gnisName")
    return str(name).strip() if name and str(name).strip() else "an unnamed reach"


def reach_text(anchor: Optional[dict]) -> str:
    """The Basin pane row: ``COMID 9327042 (this reach)``, or ``Mink Brook
    (COMID 9327042), 199 ft downstream``, or ``none found``."""
    c = comid(anchor)
    if c is None:
        return "none found"
    if not is_routed(anchor):
        return f"COMID {c} (this reach)"
    dist = _dist(anchor)
    return f"{reach_name(anchor)} (COMID {c})" + (f", {dist} downstream" if dist else "")


def describes(anchor: Optional[dict]) -> str:
    """The reach a StreamCat value describes on a stream outside NHDPlus V2
    (``EvidenceResult.anchor_label``): ``nearest StreamCat reach Mink Brook
    (COMID 9327042), 199 ft downstream, which drains 32 times this stream``.
    Empty on a covered reach: the value describes the clicked reach."""
    c = comid(anchor)
    if c is None or not is_routed(anchor):
        return ""
    parts = [f"nearest StreamCat reach {reach_name(anchor)} (COMID {c})"]
    dist = _dist(anchor)
    if dist:
        parts.append(f"{dist} downstream")
    ratio = _ratio(anchor)
    if ratio:
        parts.append(f"which drains {ratio} times this stream")
    return ", ".join(parts)


def source_label(anchor: Optional[dict]) -> str:
    """``StreamCat lookup engine, COMID 9327042 (this reach)`` or ``StreamCat
    lookup engine, nearest StreamCat reach Mink Brook (COMID 9327042)``."""
    c = comid(anchor)
    if c is None:
        return ENGINE_NAME
    if not is_routed(anchor):
        return f"{ENGINE_NAME}, COMID {c} (this reach)"
    return f"{ENGINE_NAME}, nearest StreamCat reach {reach_name(anchor)} (COMID {c})"


def basin_suffix(anchor: Optional[dict]) -> str:
    """The value-text suffix that says whose basin a StreamCat value describes."""
    c = comid(anchor)
    return f" (NHDPlus V2 basin, COMID {c})" if c is not None else " (NHDPlus V2 basin)"


def basin_note(anchor: Optional[dict]) -> str:
    """One tooltip sentence on the basin behind a StreamCat value."""
    c = comid(anchor)
    if c is None:
        return "Describes an NHDPlus V2 reach basin."
    if not is_routed(anchor):
        return (f"Describes the NHDPlus V2 basin of COMID {c}, the reach this point "
                "sits on, as EPA StreamCat summarized it.")
    dist = _dist(anchor)
    ratio = _ratio(anchor)
    txt = (f"Describes the NHDPlus V2 basin of {reach_name(anchor)} (COMID {c}), the "
           "nearest StreamCat reach downstream")
    if dist:
        txt += f", {dist} away"
    if ratio:
        txt += f", which drains {ratio} times this stream"
    return txt + "."


def snap_line(anchor: Optional[dict]) -> Optional[str]:
    """The Identify pane's third line once the reach is known."""
    c = comid(anchor)
    if c is None:
        return None
    if not is_routed(anchor):
        return f"StreamCat values come from this reach (COMID {c})."
    dist = _dist(anchor)
    ratio = _ratio(anchor)
    txt = f"StreamCat values come from {reach_name(anchor)} (COMID {c})"
    if dist:
        txt += f", {dist} downstream"
    if ratio:
        txt += f", which drains {ratio} times this stream"
    return txt + "."


def legend_reach(anchor: Optional[dict]) -> Optional[dict]:
    """``{"comid", "name"}`` for the map legend, None without a COMID."""
    c = comid(anchor)
    if c is None:
        return None
    name = ((anchor or {}).get("scoredReach") or {}).get("gnisName")
    return {"comid": c, "name": (str(name).strip() or None) if name else None}


def route_segment(anchor: Optional[dict]) -> Optional[dict]:
    """The dashed connector from the clicked HR stream to the covered reach
    (a one-line FeatureCollection), None on a covered reach."""
    if not is_routed(anchor):
        return None
    clicked = (anchor or {}).get("clickedStream") or {}
    scored = (anchor or {}).get("scoredReach") or {}
    if clicked.get("snapLat") is None or scored.get("snapLat") is None:
        return None
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "LineString", "coordinates": [
            [clicked["snapLon"], clicked["snapLat"]],
            [scored["snapLon"], scored["snapLat"]]]}}]}
