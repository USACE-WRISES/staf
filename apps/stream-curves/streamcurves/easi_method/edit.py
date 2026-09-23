"""Supported edits to an EASI method draft, and the semantic difference between versions.

Every edit is a pure function: it returns a new project with the changed method file,
one history record and, when the edit is analytical, the calculator dropped (a workbook
is only ever served for the files it was generated from). Edited JSON is written back
in the file's own style (two-space indent, sorted keys, ASCII, its own newline), so a
version diff shows only what changed.

Supported: fixed-band edges and which side owns them, regional TN/TP edges, curve
knots, and display text. Operators, inputs, routes, derivations, weights and anchors are
evaluator behavior (see the component inventory) and change only through reviewed
development of EASI itself.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import re
from typing import Any, Optional

from .model import EasiProject

DISPLAY_FIELDS = ("title", "limitations", "basisClass", "rationale", "label")


class EditError(ValueError):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def dump_like(original: bytes, obj: Any) -> bytes:
    """Serialize ``obj`` the way ``original`` was written."""
    nl = "\r\n" if b"\r\n" in original else "\n"
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True)
    if nl != "\n":
        text = text.replace("\n", nl)
    if original.endswith(b"\n"):
        text += nl
    return text.encode("utf-8")


def _load(project: EasiProject, name: str):
    return json.loads(project.files[name].decode("utf-8"))


def _origin(project: EasiProject, name: str):
    """The origin's parsed copy of a method file (the version this draft came from)."""
    return json.loads(project.origin_files()[name].decode("utf-8"))


def _same(a, b) -> bool:
    try:
        return a is not None and b is not None and float(a) == float(b)
    except (TypeError, ValueError):
        return False


def _with_file(project: EasiProject, name: str, obj, record: dict) -> EasiProject:
    if not project.is_revision():
        raise EditError("only a draft revision can be edited; start a revision of this version first")
    new = project.copy()
    blob = dump_like(project.files[name], obj)
    if blob == project.files[name]:
        raise EditError("the edit changes nothing")
    new.set_file(name, blob)
    record = {"at": _now(), **record, "file": name}
    new.history.append(record)
    if record.get("kind") == "analytical":
        new.calculator = None
        new.meta["calculatorFor"] = None
    restamp_identity(new)
    new.meta["updated"] = record["at"]
    return new


def restamp_identity(project: EasiProject) -> None:
    """Keep ``scoring-identity.json`` truthful once the method differs from its origin:
    it names the hashes of the files it identifies, and the changed method is named as a
    revision derived from the origin (never with the origin's own alternative id)."""
    raw = project.files["scoring-identity.json"]
    ident = json.loads(raw.decode("utf-8"))
    lineage = project.meta.get("lineage") or {}
    origin = (lineage.get("origin") or {})
    if origin.get("packageDigest") == project.package_digest:
        return
    if set(project.base) <= {"scoring-identity.json"}:
        # every other file is back to its origin bytes: so is the identity
        if "scoring-identity.json" in project.base:
            project.set_file("scoring-identity.json", project.base["scoring-identity.json"])
        return
    base = ((lineage.get("importedFrom") or {}).get("scoringIdentity")
            or origin.get("scoringIdentity") or {})
    ident["catalog_sha256"] = sha_hex(project.files["screening-methods.json"])
    ident["curves_sha256"] = sha_hex(project.files["reference-curves.json"])
    ident["nars_geography_sha256"] = sha_hex(project.files["nars-ecoregions-9.geojson.gz"])
    curves = json.loads(project.files["reference-curves.json"].decode("utf-8"))
    ident["curve_count"] = sum(len(s.get("curves") or {}) for s in (curves.get("sets") or {}).values())
    mid, ver = project.meta.get("methodId") or "easi-screening", int(project.meta.get("version") or 1)
    ident["alternative_id"] = f"{mid}-v{ver}"
    ident["alternative_name"] = (project.meta.get("label") if project.meta.get("labelSetByAuthor")
                                 else f"{base.get('alternative_name') or 'EASI screening method'}, "
                                      f"revision v{ver}")
    ident["derived_from"] = {k: base.get(k) for k in ("alternative_id", "catalog_sha256",
                                                        "curves_sha256") if base.get(k)}
    project.set_file("scoring-identity.json", dump_like(raw, ident))


def sha_hex(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def crossing(points: list, target: float):
    """The first x where the polyline reaches ``target`` (the displayed x39/x69)."""
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if y0 == y1:
            if y0 == target:
                return round(float(x0), 6)
            continue
        if (y0 - target) * (y1 - target) <= 0:
            return round(float(x0) + (target - y0) * (float(x1) - float(x0)) / (y1 - y0), 6)
    return None


def _method(cat: dict, method_key: str) -> dict:
    for m in cat.get("methods", []):
        if m.get("methodKey") == method_key:
            return m
        for v in m.get("variants") or []:
            if v.get("methodKey") == method_key:
                return v
    raise EditError(f"no method {method_key!r} in the catalog")


def _rule(method: dict, input_key: Optional[str]) -> dict:
    if input_key is None:
        return method
    for i in method.get("inputs") or []:
        if i.get("key") == input_key:
            return i
    raise EditError(f"method {method.get('methodKey')!r} has no input {input_key!r}")


# --------------------------------------------------------------------------- #
# band labels: EASI shows them (criteria tooltips, worksheet), so they move with the edge
# --------------------------------------------------------------------------- #
_NUM = re.compile(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])")


def _decimals(v: float) -> int:
    for d in range(0, 7):
        if abs(round(v, d) - v) < 1e-12:
            return d
    return 6


def number_text(v, like: str = "") -> str:
    """``v`` written with at least as many decimals as ``like`` and as many as it needs."""
    d = max(len(like.split(".")[1]) if "." in like else 0, _decimals(float(v)))
    return f"{float(v):.{d}f}"


def replace_number(text: str, old, new) -> Optional[str]:
    """``text`` with its first standalone number equal to ``old`` written as ``new`` in the
    same style, or None when ``text`` names no such number."""
    for m in _NUM.finditer(text or ""):
        if float(m.group()) == float(old):
            return text[:m.start()] + number_text(new, m.group()) + text[m.end():]
    return None


def label_suffix(label: str) -> str:
    """What follows a label's last number: its units (" km/km\u00b2", "%", " recorded")."""
    found = list(_NUM.finditer(label or ""))
    return label[found[-1].end():] if found else ""


def band_label(band: dict, suffix: str = "") -> str:
    """A band's label from its bounds, in the catalog's style: "<1", "1-<3", ">1.3-\u22641.5",
    "10-25%", "\u22653"."""
    lo, hi = band.get("min"), band.get("max")
    if lo is None:
        return ("\u2264" if band.get("maxInclusive") else "<") + number_text(hi) + suffix
    if hi is None:
        return ("\u2265" if band.get("minInclusive") else ">") + number_text(lo) + suffix
    left = ("" if band.get("minInclusive") else ">") + number_text(lo)
    if band.get("minInclusive") and band.get("maxInclusive"):
        right = number_text(hi)
    else:
        right = ("\u2264" if band.get("maxInclusive") else "<") + number_text(hi)
    return f"{left}-{right}{suffix}"


def _band_signature(bands: list[dict]) -> list[tuple]:
    return [(b.get("rating"), b.get("min"), b.get("max"), bool(b.get("minInclusive")),
             bool(b.get("maxInclusive")))
            for b in sorted(bands, key=lambda b: float("-inf") if b.get("min") is None else b["min"])]


def band_edges(bands: list[dict]) -> list[dict]:
    """The shared edges between consecutive bands, lowest first: value and owner."""
    ordered = sorted(bands, key=lambda b: (float("-inf") if b.get("min") is None else b["min"]))
    edges = []
    for lo, hi in zip(ordered, ordered[1:]):
        owner = "lower" if lo.get("maxInclusive") else "upper"
        edges.append({"value": lo.get("max"), "owner": owner, "below": lo.get("rating"),
                      "above": hi.get("rating")})
    return edges


def set_band_edge(project: EasiProject, method_key: str, input_key: Optional[str], edge: int,
                  value: float, *, owner: Optional[str] = None, by: str, reason: str) -> EasiProject:
    """Move one shared band edge (``edge`` counts from the lowest), keeping the bands a
    partition of the line: the lower band's max and the upper band's min move together."""
    cat = _load(project, "screening-methods.json")
    rule = _rule(_method(cat, method_key), input_key)
    bands = rule.get("bands")
    if not isinstance(bands, list) or len(bands) < 2:
        raise EditError("this rule has no fixed bands")
    ordered = sorted(bands, key=lambda b: (float("-inf") if b.get("min") is None else b["min"]))
    if not 0 <= edge < len(ordered) - 1:
        raise EditError(f"edge {edge} does not exist")
    lo, hi = ordered[edge], ordered[edge + 1]
    if lo.get("max") is None or hi.get("min") is None or not _same(lo.get("max"), hi.get("min")):
        raise EditError("these bands do not share an edge (a count scale); change them in EASI")
    before = {"value": lo.get("max"), "owner": "lower" if lo.get("maxInclusive") else "upper"}
    low_bound = lo.get("min")
    high_bound = hi.get("max")
    v = float(value)
    if (low_bound is not None and v <= low_bound) or (high_bound is not None and v >= high_bound):
        raise EditError("an edge must stay between its neighbours")
    try:
        o_rule = _rule(_method(_origin(project, "screening-methods.json"), method_key), input_key)
        o_value = sorted(o_rule["bands"], key=lambda b: (float("-inf") if b.get("min") is None
                                                         else b["min"]))[edge].get("max")
    except (EditError, KeyError, IndexError, TypeError):
        o_value = None
    if _same(o_value, v):
        v = o_value                      # the origin's own number, so a revert is exact
    elif v.is_integer() and isinstance(before["value"], int):
        v = int(v)
    own = owner or before["owner"]
    lo["max"], hi["min"] = v, v
    lo["maxInclusive"], hi["minInclusive"] = own == "lower", own == "upper"
    for band in (lo, hi):
        old_label = band.get("label")
        if not old_label:
            continue
        moved = replace_number(old_label, before["value"], v) if own == before["owner"] else None
        band["label"] = moved if moved is not None else band_label(band, label_suffix(old_label))
    method = _method(cat, method_key)
    plotted = (input_key is None or (not method.get("bands") and not method.get("curve") and next(
        (i.get("key") for i in method.get("inputs") or [] if not i.get("contextOnly")), None) == input_key))
    marks = method.get("breakpoints") or []
    if plotted and edge < len(marks) and isinstance(marks[edge], dict) and marks[edge].get("label"):
        moved = replace_number(marks[edge]["label"], before["value"], v)
        if moved is not None:
            marks[edge]["label"] = moved      # the plot's annotation at this edge
    try:
        o_method = _method(_origin(project, "screening-methods.json"), method_key)
        o_rule = _rule(o_method, input_key)
    except (EditError, KeyError, TypeError):
        o_method = o_rule = None
    if (o_rule is not None and isinstance(o_rule.get("bands"), list)
            and _band_signature(o_rule["bands"]) == _band_signature(rule["bands"])):
        # back to the origin's bands: its own labels and annotations, byte for byte
        rule["bands"] = copy.deepcopy(o_rule["bands"])
        if "breakpoints" in o_method:
            method["breakpoints"] = copy.deepcopy(o_method["breakpoints"])
    return _with_file(project, "screening-methods.json", cat, {
        "action": "set_band_edge", "kind": "analytical", "by": by, "reason": reason,
        "target": {"methodKey": method_key, "input": input_key, "edge": edge},
        "before": before, "after": {"value": v, "owner": own}})


def set_regional_edges(project: EasiProject, method_key: str, input_key: str, region: str,
                       good: float, poor: float, *, by: str, reason: str) -> EasiProject:
    cat = _load(project, "screening-methods.json")
    rule = _rule(_method(cat, method_key), input_key)
    rb = rule.get("regionalBands")
    if not isinstance(rb, dict) or region not in rb:
        raise EditError(f"no regional edges for {region!r}")
    if not float(good) < float(poor):
        raise EditError("the Good edge must be below the Poor edge")
    before = list(rb[region])
    try:
        o_pair = _rule(_method(_origin(project, "screening-methods.json"), method_key),
                       input_key)["regionalBands"][region]
    except (EditError, KeyError, TypeError):
        o_pair = None
    new_pair = [float(good), float(poor)]
    if o_pair and len(o_pair) == 2 and all(_same(a, b) for a, b in zip(o_pair, new_pair)):
        new_pair = list(o_pair)
    rb[region] = new_pair
    return _with_file(project, "screening-methods.json", cat, {
        "action": "set_regional_edges", "kind": "analytical", "by": by, "reason": reason,
        "target": {"methodKey": method_key, "input": input_key, "region": region},
        "before": before, "after": rb[region]})


def set_curve_points(project: EasiProject, set_name: str, stratum: str, points: list,
                     *, by: str, reason: str) -> EasiProject:
    pts = [[float(x), float(y)] for x, y in points]
    if len(pts) < 2:
        raise EditError("a curve needs at least two knots")
    if any(b[0] < a[0] for a, b in zip(pts, pts[1:])):
        raise EditError("knots must be in increasing x")
    if any(not 0.0 <= y <= 1.0 for _, y in pts):
        raise EditError("curve index values must be between 0 and 1")
    curves = _load(project, "reference-curves.json")
    s = (curves.get("sets") or {}).get(set_name)
    if not s or stratum not in (s.get("curves") or {}):
        raise EditError(f"no curve {set_name}/{stratum}")
    c = s["curves"][stratum]
    before = c.get("points")
    crossings = {key: crossing(pts, t) for key, t in (("x39", 0.39), ("x69", 0.69))}
    missing = [k for k, v in crossings.items() if v is None]
    if missing:
        raise EditError("the curve must cross both condition breaks (0.39 and 0.69); EASI shows "
                        "each curve's crossings with its bands")
    o_curve = (((_origin(project, "reference-curves.json").get("sets") or {}).get(set_name) or {})
               .get("curves") or {}).get(stratum)
    if o_curve is not None and [[float(x), float(y)] for x, y in o_curve.get("points") or []] == pts:
        s["curves"][stratum] = o_curve   # back to the origin's curve, byte for byte
    else:
        # The edit is recorded in the project's history and the version's provenance, never
        # inside the method file EASI loads.
        c["points"] = pts
        c.update(crossings)
    return _with_file(project, "reference-curves.json", curves, {
        "action": "set_curve_points", "kind": "analytical", "by": by, "reason": reason,
        "target": {"set": set_name, "stratum": stratum}, "before": before, "after": pts})


def set_text(project: EasiProject, method_key: str, field: str, value, *, by: str,
             reason: str) -> EasiProject:
    if field not in DISPLAY_FIELDS:
        raise EditError(f"{field!r} is not a display field")
    cat = _load(project, "screening-methods.json")
    m = _method(cat, method_key)
    before = m.get(field)
    m[field] = value
    return _with_file(project, "screening-methods.json", cat, {
        "action": "set_text", "kind": "display", "by": by, "reason": reason,
        "target": {"methodKey": method_key, "field": field}, "before": before, "after": value})


# --------------------------------------------------------------------------- #
# semantic difference
# --------------------------------------------------------------------------- #
def _walk(a, b, path, out, limit=400):
    if len(out) >= limit:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            _walk(a.get(k), b.get(k), f"{path}.{k}" if path else k, out, limit)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _walk(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append({"path": path, "before": a, "after": b})


def diff(a: EasiProject, b: EasiProject) -> dict:
    """What changed from ``a`` to ``b``, by function, curve and file, split into
    analytical changes (they move ratings and invalidate review) and display ones."""
    from .register import analytical_method
    out = {"identical": a.files == b.files, "filesChanged": sorted(n for n in a.files
                                                                  if a.files[n] != b.files.get(n)),
           "methods": [], "curves": [], "other": [],
           "identity": {"from": a.identity(), "to": b.identity()}}
    if out["identical"]:
        return out
    ca, cb = a.catalog(), b.catalog()
    ma = {m["methodKey"]: m for m in ca.get("methods", [])}
    mb = {m["methodKey"]: m for m in cb.get("methods", [])}
    for key in sorted(set(ma) | set(mb)):
        x, y = ma.get(key), mb.get(key)
        if x == y:
            continue
        analytical, display = [], []
        if x is None or y is None:
            analytical.append({"path": key, "before": bool(x), "after": bool(y)})
        else:
            _walk(analytical_method(x), analytical_method(y), "", analytical)
            dx = {k: v for k, v in x.items() if k not in analytical_method(x)}
            dy = {k: v for k, v in y.items() if k not in analytical_method(y)}
            _walk(dx, dy, "", display)
        out["methods"].append({"methodKey": key, "title": (y or x).get("title"),
                               "metricId": (y or x).get("metricId"),
                               "analytical": analytical, "display": display})
    cva, cvb = a.curves(), b.curves()
    for name in sorted(set(cva.get("sets", {})) | set(cvb.get("sets", {}))):
        sa = (cva.get("sets") or {}).get(name) or {}
        sb = (cvb.get("sets") or {}).get(name) or {}
        for stratum in sorted(set(sa.get("curves", {})) | set(sb.get("curves", {}))):
            pa = ((sa.get("curves") or {}).get(stratum) or {}).get("points")
            pb = ((sb.get("curves") or {}).get(stratum) or {}).get("points")
            if pa != pb:
                out["curves"].append({"set": name, "stratum": stratum, "before": pa, "after": pb})
    for name in out["filesChanged"]:
        if name not in ("screening-methods.json", "reference-curves.json"):
            out["other"].append(name)
    out["analytical"] = bool(out["curves"] or any(m["analytical"] for m in out["methods"])
                             or any(n in ("cwa-mapping.json", "easi-metrics.json",
                                          "ecoregion-crosswalk.json", "nars-ecoregions-9.geojson.gz")
                                    for n in out["other"]))
    return out
