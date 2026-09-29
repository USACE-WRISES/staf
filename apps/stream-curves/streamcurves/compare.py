"""Compare two assessment versions: what differs, never why.

A version is a folder holding ``assessment.deep.json`` and ``provenance.json`` (a published
``apps/library`` version, a staged one under ``<run>/library/...``), or a run folder, which
resolves to the version it staged, else to its preview bundle and decision log. Every
reader here is pure; ``scripts/compare_runs.py`` is the command line over it, and the
Compare section of Select final curves (``views/final_selection.py``) renders the same report.

- :func:`compare`: the curves (with the largest numeric delta per changed curve field), the
  reviewer decisions on the records, the review queue and the manifest facts;
- :func:`registers`: the candidate registers (``candidateRegister``) through
  ``candidates.diff``;
- :func:`ledgers`: the per-metric rebuild ledgers, per metric and function, each disposition
  change;
- :func:`digest`: a version's ``inputsDigest`` re-derived from its stored manifest and its
  ``contentDigest`` from its bundle;
- :func:`differences_from_owner_decisions`: per recorded owner decision (REF-15, CURVE-07,
  COV-01, SELECT-01), what a re-derived run decided about the same metric or function
  (campaign decision D3): agree, differ, or not applicable, with the curve anchors in IQR
  units where both versions carry the curve.

The report states WHAT differs; classifying WHY (rule evolution, seed shift, data drift,
judgment) stays with the reader.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from . import candidates as C
from . import methodology
from . import provenance as pv
from . import run_state as rs
from .curve_svg import DEEP_INDEX_BANDS

BUNDLE_FILE = "assessment.deep.json"
PROVENANCE_FILE = "provenance.json"
SESSION_FILE = "session.streamcurves.json"
PREVIEW_BUNDLE = "preview_bundle.deep.json"
DECISION_LOG = "decision_provenance_log.json"
PACKET_FILE = "review_packet.json"

#: the rules a person decides under, in the order the D3 list reads them
OWNER_RULES = ("REF-15", "CURVE-07", "COV-01", "SELECT-01")
AGREE, DIFFER, NOT_APPLICABLE = "agree", "differ", "not_applicable"
VERDICT_WORDS = {AGREE: "Agrees", DIFFER: "Differs", NOT_APPLICABLE: "Not applicable"}

#: the metric fields beside the curve a version diff reports
METRIC_FIELDS = ("confidenceLabel", "confidenceTotal", "referenceN",
                 "referenceRange", "sampleDisposition", "referenceTier")
#: the ledger fields a disposition change is judged on
LEDGER_FIELDS = ("disposition", "rule", "basisDigest", "option")
#: the reviewer fields a decision change is judged on
DECISION_FIELDS = ("reviewer_action", "reviewer_decision_class", "reviewer_rationale_origin")
#: reviewer answers that keep a held curve (CURVE-07)
KEEP_ACTIONS = ("accept", "accept_with_conditions", "modify")


# --------------------------------------------------------------------------- #
# reading a version
# --------------------------------------------------------------------------- #
def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_version_dir(path) -> Path:
    """The folder that holds the version: ``path`` itself, or, for a run folder, the version
    it staged (``review_packet.json`` names it)."""
    p = Path(path)
    if (p / BUNDLE_FILE).is_file():
        return p
    packet = p / PACKET_FILE
    if packet.is_file():
        try:
            staged = (_read_json(packet) or {}).get("staged") or {}
        except (OSError, ValueError):
            staged = {}
        if staged.get("path") and (Path(staged["path"]) / BUNDLE_FILE).is_file():
            return Path(staged["path"])
    return p


def load_version(path) -> tuple[dict, dict]:
    """``(bundle, provenance document)`` of a version folder or a run folder. A run folder
    a gate refused to stage still opens: its preview bundle and its decision log, with the
    log's manifest inlined (the log names it by ``manifestRef``)."""
    vdir = resolve_version_dir(path)
    bundle_path = vdir / BUNDLE_FILE
    if bundle_path.is_file():
        bundle = _read_json(bundle_path)
        prov = vdir / PROVENANCE_FILE
        doc = _read_json(prov) if prov.is_file() else {}
        return bundle, (doc if isinstance(doc, dict) else {})
    preview, log = vdir / PREVIEW_BUNDLE, vdir / DECISION_LOG
    if preview.is_file() and log.is_file():
        bundle = _read_json(preview)
        doc = _read_json(log)
        doc = doc if isinstance(doc, dict) else {"records": doc}
        ref = doc.get("manifestRef")
        if isinstance(ref, str) and ref and "manifest" not in doc and (vdir / ref).is_file():
            doc["manifest"] = _read_json(vdir / ref)
        return bundle, doc
    raise FileNotFoundError(
        f"{path} holds no assessment version: expected {BUNDLE_FILE} and {PROVENANCE_FILE}, "
        f"or a run folder with a staged version or a {PREVIEW_BUNDLE}")


def version_label(path) -> str:
    """``<assessment>/vN`` for a library version folder, else the folder name."""
    p = resolve_version_dir(path)
    if p.name[:1] == "v" and p.name[1:].isdigit():
        return f"{p.parent.name}/{p.name}"
    return p.name


def _doc_of(x) -> dict:
    if isinstance(x, Mapping):
        return dict(x)
    return load_version(x)[1]


# --------------------------------------------------------------------------- #
# the bundle's metrics and their curves
# --------------------------------------------------------------------------- #
def metrics_by_id(bundle: Optional[Mapping]) -> dict[str, dict]:
    """metricId -> entry. Metrics are cross-listed under every function they serve; the
    copies are expected identical, so the first wins (a differing copy is reported)."""
    out: dict[str, dict] = {}
    for fn in (bundle or {}).get("metricsByFunction") or []:
        for m in fn.get("metrics") or []:
            mid = str(m.get("metricId"))
            if mid in out:
                if out[mid] != m:
                    out[mid] = dict(out[mid], _cross_listed_copies_differ=True)
                continue
            out[mid] = m
    return out


def functions_of(bundle: Optional[Mapping]) -> dict[str, list[str]]:
    """functionId -> the metric ids that score it."""
    out: dict[str, list[str]] = {}
    for fn in (bundle or {}).get("metricsByFunction") or []:
        fid = str(fn.get("functionId") or "")
        if fid:
            out[fid] = [str(m.get("metricId")) for m in fn.get("metrics") or []]
    return out


def metric_id_of(metric_key: Any) -> str:
    """The bundle's id of a build metric key (``phab_XCMGW`` -> ``spring-phab-xcmgw``)."""
    from .deep_export import deep_slug
    key = str(metric_key or "")
    return key if key.startswith("spring-") else "spring-" + deep_slug(key)


def num_delta(a, b):
    """Max absolute difference between two same-shaped numeric structures, else None."""
    if isinstance(a, bool) or isinstance(b, bool):
        return None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b))
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        deltas = [num_delta(x, y) for x, y in zip(a, b)]
        if all(d is not None for d in deltas):
            return max(deltas) if deltas else 0.0
    if isinstance(a, dict) and isinstance(b, dict) and set(a) == set(b):
        deltas = [num_delta(a[k], b[k]) for k in a]
        if all(d is not None for d in deltas):
            return max(deltas) if deltas else 0.0
    return None


def curve_diff(ca, cb) -> list[str]:
    """Human-readable list of differing curve subkeys, with numeric deltas."""
    if ca == cb:
        return []
    if not isinstance(ca, dict) or not isinstance(cb, dict):
        return ["curve replaced (non-dict or missing)"]
    notes = []
    for k in sorted(set(ca) | set(cb)):
        va, vb = ca.get(k), cb.get(k)
        if va == vb:
            continue
        d = num_delta(va, vb)
        notes.append(f"{k} (max delta {d:.6g})" if d is not None else k)
    return notes


def curve_max_delta(ca, cb) -> Optional[float]:
    """The largest numeric change between two curves' points, None when the points cannot
    be compared point for point."""
    pa = _points(ca if isinstance(ca, Mapping) and "points" in ca else {"points": ca})
    pb = _points(cb if isinstance(cb, Mapping) and "points" in cb else {"points": cb})
    if not pa or not pb or len(pa) != len(pb):
        return None
    return max(max(abs(x0 - x1), abs(y0 - y1)) for (x0, y0), (x1, y1) in zip(pa, pb))


def _points(curve_or_metric: Optional[Mapping]) -> list[tuple[float, float]]:
    src = curve_or_metric or {}
    pts = src.get("points")
    if pts is None:
        pts = (src.get("curve") or {}).get("points")
    out = []
    for p in pts or []:
        try:
            if isinstance(p, Mapping):
                out.append((float(p["x"]), float(p["y"])))
            else:
                out.append((float(p[0]), float(p[1])))
        except (KeyError, IndexError, TypeError, ValueError):
            return []
    return out


def records_by_key(doc: Optional[Mapping]) -> dict[tuple[str, str], dict]:
    out = {}
    for rec in (doc or {}).get("records") or []:
        out[(str(rec.get("rule_id")), str(rec.get("subject")))] = rec
    return out


def queue_ids(doc: Optional[Mapping]) -> set[str]:
    return {str(i.get("item_id"))
            for i in ((doc or {}).get("reviewQueue") or {}).get("items") or []}


# --------------------------------------------------------------------------- #
# curves, decisions, queue, manifest
# --------------------------------------------------------------------------- #
def compare(vdir_a, vdir_b) -> dict:
    """The classic report: curves, reviewer decisions, review queue, manifest facts."""
    bundle_a, doc_a = load_version(vdir_a)
    bundle_b, doc_b = load_version(vdir_b)
    return compare_loaded(bundle_a, doc_a, bundle_b, doc_b,
                          a=str(resolve_version_dir(vdir_a)), b=str(resolve_version_dir(vdir_b)))


def compare_loaded(bundle_a: Mapping, doc_a: Mapping, bundle_b: Mapping, doc_b: Mapping, *,
                   a: str = "A", b: str = "B") -> dict:
    ma, mb = metrics_by_id(bundle_a), metrics_by_id(bundle_b)
    curves: dict = {"identical": [], "differ": {}, "max_delta": {},
                    "only_a": sorted(set(ma) - set(mb)), "only_b": sorted(set(mb) - set(ma))}
    for mid in sorted(set(ma) & set(mb)):
        ea, eb = ma[mid], mb[mid]
        notes = curve_diff(ea.get("curve"), eb.get("curve"))
        for f in METRIC_FIELDS:
            if ea.get(f) != eb.get(f):
                notes.append(f"{f}: {ea.get(f)!r} -> {eb.get(f)!r}")
        if notes:
            curves["differ"][mid] = notes
            curves["max_delta"][mid] = curve_max_delta(ea.get("curve"), eb.get("curve"))
        else:
            curves["identical"].append(mid)

    ra, rb = records_by_key(doc_a), records_by_key(doc_b)
    decisions: dict = {"same": 0, "differ": {}, "only_a": [], "only_b": []}
    for key in sorted(set(ra) | set(rb)):
        label = f"{key[0]}:{key[1]}"
        if key not in rb:
            if ra[key].get("reviewer_action"):
                decisions["only_a"].append(label)
            continue
        if key not in ra:
            if rb[key].get("reviewer_action"):
                decisions["only_b"].append(label)
            continue
        diffs = {f: (ra[key].get(f), rb[key].get(f))
                 for f in DECISION_FIELDS if ra[key].get(f) != rb[key].get(f)}
        if diffs:
            decisions["differ"][label] = diffs
        elif ra[key].get("reviewer_action"):
            decisions["same"] += 1

    qa, qb = queue_ids(doc_a), queue_ids(doc_b)
    queue = {"only_a": sorted(qa - qb), "only_b": sorted(qb - qa), "common": len(qa & qb)}

    man_a = (doc_a or {}).get("manifest") or {}
    man_b = (doc_b or {}).get("manifest") or {}
    manifest: dict = {}
    for k in ("methodology", "diagnostics", "inputsDigest"):
        va, vb = man_a.get(k), man_b.get(k)
        manifest[k] = {"a": va, "b": vb, "equal": va == vb}
    sd_a = man_a.get("standingDecisions") or {}
    sd_b = man_b.get("standingDecisions") or {}
    manifest["standingDecisions"] = {
        "a": {kk: sd_a.get(kk) for kk in ("policyVersion", "enabledIds", "appliedCount")},
        "b": {kk: sd_b.get(kk) for kk in ("policyVersion", "enabledIds", "appliedCount")},
    }
    return {"a": a, "b": b, "manifest": manifest, "curves": curves,
            "decisions": decisions, "queue": queue}


# --------------------------------------------------------------------------- #
# the candidate registers
# --------------------------------------------------------------------------- #
def registers(a, b) -> dict:
    """``candidates.diff`` over the two versions' ``candidateRegister`` exports (``a`` and
    ``b`` are provenance documents or version paths). A version published before the
    register entered provenance records none, and the report says so instead of reading
    an absent register as an empty one."""
    da, db = _doc_of(a), _doc_of(b)
    ra, rb = da.get("candidateRegister"), db.get("candidateRegister")
    have_a, have_b = isinstance(ra, Mapping), isinstance(rb, Mapping)
    rows_a = [dict(r) for r in (ra or {}).get("rows") or [] if isinstance(r, Mapping)]
    rows_b = [dict(r) for r in (rb or {}).get("rows") or [] if isinstance(r, Mapping)]
    available = have_a and have_b
    changes = C.diff(rows_a, rows_b) if available else []
    counts = {kind: sum(1 for c in changes if c.get("change") == kind)
              for kind in ("added", "removed", "changed")}
    missing = [side for side, have in (("A", have_a), ("B", have_b)) if not have]
    note = (None if available else
            f"{' and '.join(missing)} record{'s' if len(missing) == 1 else ''} no candidate "
            "register (published before the register entered provenance).")
    return {"available": available,
            "a": {"rows": len(rows_a), "counts": dict((ra or {}).get("counts") or {})},
            "b": {"rows": len(rows_b), "counts": dict((rb or {}).get("counts") or {})},
            "changes": changes, "counts": counts, "note": note}


# --------------------------------------------------------------------------- #
# the rebuild ledgers
# --------------------------------------------------------------------------- #
def _ledger_of(x) -> Optional[dict]:
    """A ledger document: the mapping given, a provenance document's ``metricLedger``, or
    the ledger built from a version folder's own files (``provenance.build_ledger_from_version``)."""
    if isinstance(x, Mapping):
        if "rows" in x:
            return dict(x)
        got = x.get("metricLedger")
        return dict(got) if isinstance(got, Mapping) else None
    vdir = resolve_version_dir(x)
    try:
        doc = load_version(vdir)[1]
    except FileNotFoundError:
        doc = {}
    if isinstance(doc.get("metricLedger"), Mapping):
        return dict(doc["metricLedger"])
    if (vdir / SESSION_FILE).is_file():
        return pv.build_ledger_from_version(vdir)
    return None


def _ledger_view(r: Mapping) -> dict:
    return {"disposition": r.get("disposition"), "rule": r.get("rule"),
            "basisDigest": r.get("basisDigest"), "option": r.get("option"),
            "reason": r.get("reason"), "who": r.get("who"), "when": r.get("when")}


def ledgers(a, b) -> dict:
    """Per metric and function, the disposition changes between two rebuild ledgers (each
    a ledger document, a provenance document carrying ``metricLedger``, or a version path
    whose ledger is built from its files)."""
    la, lb = _ledger_of(a), _ledger_of(b)
    available = la is not None and lb is not None

    def key(r):
        return str(r.get("metric")), str(r.get("functionId") or "")

    ra = {key(r): r for r in (la or {}).get("rows") or [] if isinstance(r, Mapping)}
    rb = {key(r): r for r in (lb or {}).get("rows") or [] if isinstance(r, Mapping)}
    changes: list[dict] = []
    same = 0
    for k in sorted(set(ra) | set(rb)):
        x, y = ra.get(k), rb.get(k)
        if x is None:
            changes.append({"change": "added", "metric": k[0], "functionId": k[1],
                            "function": y.get("function"), "before": None,
                            "after": _ledger_view(y), "fields": ["disposition"]})
        elif y is None:
            changes.append({"change": "removed", "metric": k[0], "functionId": k[1],
                            "function": x.get("function"), "before": _ledger_view(x),
                            "after": None, "fields": ["disposition"]})
        else:
            fields = [f for f in LEDGER_FIELDS if x.get(f) != y.get(f)]
            if fields:
                changes.append({"change": "changed", "metric": k[0], "functionId": k[1],
                                "function": y.get("function") or x.get("function"),
                                "before": _ledger_view(x), "after": _ledger_view(y),
                                "fields": fields})
            else:
                same += 1
    missing = [side for side, have in (("A", la is not None), ("B", lb is not None)) if not have]
    counts = {kind: sum(1 for c in changes if c.get("change") == kind)
              for kind in ("added", "removed", "changed")}
    return {"available": available, "changes": changes, "same": same, "counts": counts,
            "a": {"rows": len(ra), "schema": (la or {}).get("schema")},
            "b": {"rows": len(rb), "schema": (lb or {}).get("schema")},
            "note": (None if available else
                     f"{' and '.join(missing)} carr{'ies' if len(missing) == 1 else 'y'} no "
                     "rebuild ledger and no session to build one from.")}


# --------------------------------------------------------------------------- #
# the digests
# --------------------------------------------------------------------------- #
def digest(vdir) -> dict:
    """A version's ``inputsDigest`` re-derived from its stored manifest
    (``provenance.digest_payload_from_manifest``, the legacy rules for a manifest without
    ``digestSchema``) and its ``contentDigest`` re-derived from its bundle."""
    from . import library as lib
    vdir = resolve_version_dir(vdir)
    bundle, doc = load_version(vdir)
    manifest = doc.get("manifest") or {}
    recorded = manifest.get("inputsDigest") or doc.get("inputsDigest")
    rederived = None
    problem = None
    if manifest:
        try:
            rederived = methodology.inputs_digest(pv.digest_payload_from_manifest(manifest))
        except Exception as exc:  # noqa: BLE001 - the report names the problem
            problem = str(exc)
    else:
        problem = "no run manifest is stored"
    content_recorded = bundle.get("contentDigest")
    content_rederived = lib.content_digest(bundle)
    return {"path": str(vdir),
            "inputsDigest": {"recorded": recorded, "rederived": rederived,
                             "equal": bool(recorded) and recorded == rederived,
                             "digestSchema": manifest.get("digestSchema"), "problem": problem},
            "contentDigest": {"recorded": content_recorded, "rederived": content_rederived,
                              "equal": bool(content_recorded) and content_recorded == content_rederived}}


# --------------------------------------------------------------------------- #
# D3: what a re-derived run decided where the owner had decided
# --------------------------------------------------------------------------- #
def anchors(points: Iterable, bands: Iterable[float] = DEEP_INDEX_BANDS) -> list[Optional[float]]:
    """Where a curve first reaches each index band edge, by linear interpolation (None when
    it never does)."""
    pts = _points({"points": list(points or [])})
    out: list[Optional[float]] = []
    for t in bands:
        hit = None
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if y0 != y1 and (y0 - t) * (y1 - t) <= 0:
                hit = x0 + (t - y0) * (x1 - x0) / (y1 - y0)
                break
        out.append(hit)
    return out


def _iqr(m: Optional[Mapping]) -> Optional[float]:
    rr = (m or {}).get("referenceRange")
    try:
        lo, hi = float(rr[0]), float(rr[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return (hi - lo) if hi > lo else None


def anchor_differences(metric_a: Optional[Mapping], metric_b: Optional[Mapping],
                       bands: Iterable[float] = DEEP_INDEX_BANDS) -> list[dict]:
    """Per index band, where each version's curve reaches it and the shift from A to B, in
    the metric's units and in IQR units (the reference interquartile range B records, else
    A's). Empty when either version carries no curve."""
    pa, pb = _points(metric_a), _points(metric_b)
    if len(pa) < 2 or len(pb) < 2:
        return []
    iqr = _iqr(metric_b) or _iqr(metric_a)
    out = []
    for band, xa, xb in zip(bands, anchors(pa, bands), anchors(pb, bands)):
        delta = (xb - xa) if (xa is not None and xb is not None) else None
        out.append({"band": band, "a": xa, "b": xb, "delta": delta, "iqr": iqr,
                    "deltaIqr": (delta / iqr) if (delta is not None and iqr) else None})
    return out


def _recorded_by(r: Mapping) -> dict:
    inputs = r.get("inputs") if isinstance(r.get("inputs"), Mapping) else {}
    return {"by": r.get("reviewer") or inputs.get("recordedBy") or "",
            "when": str(r.get("reviewed_at") or inputs.get("recordedAt") or "")[:10],
            "rationale": r.get("reviewer_rationale") or inputs.get("rationale") or ""}


def _item(rule: str, subject: str, *, recorded: dict, rederived: dict, verdict: str,
          detail: str, metric: Optional[str] = None, function_id: Optional[str] = None,
          anchor_rows: Optional[list] = None) -> dict:
    return {"rule": rule, "subject": subject, "metric": metric, "functionId": function_id,
            "recorded": recorded, "rederived": rederived, "verdict": verdict,
            "detail": detail, "anchors": list(anchor_rows or [])}


def _curve_words(mid: str, ma: Mapping, mb: Mapping) -> tuple[str, list[dict]]:
    """How B's curve for ``mid`` compares with A's, and the anchors."""
    ea, eb = ma.get(mid), mb.get(mid)
    if not ea or not eb:
        return "", []
    if _points(ea) == _points(eb):
        return "the same curve", []
    rows = anchor_differences(ea, eb)
    said = ", ".join(f"{r['band']:g} at {r['b']:.4g} ({r['deltaIqr']:+.2f} IQR)"
                     if r.get("deltaIqr") is not None else
                     f"{r['band']:g} at {r['b']:.4g}" if r.get("b") is not None else
                     f"{r['band']:g} not reached"
                     for r in rows)
    return f"its own curve (band edges: {said})" if said else "its own curve", rows


def _ref15(r: Mapping, ma: Mapping, mb: Mapping, fb: Mapping, have_bundle: bool) -> dict:
    c = r.get("computed") or {}
    inputs = r.get("inputs") if isinstance(r.get("inputs"), Mapping) else {}
    metric = str(c.get("metric") or inputs.get("metric") or "")
    action = str(c.get("action") or inputs.get("action") or "")
    functions = [str(f) for f in (c.get("functions") or inputs.get("functions") or [])]
    mid = metric_id_of(metric)
    recorded = {"action": action, "functions": functions, **_recorded_by(r)}
    src = c.get("source") or {}
    if src:
        recorded["source"] = src.get("title") or src.get("kind")
    placed = [fid for fid, mids in fb.items() if mid in mids]
    if not have_bundle:
        return _item("REF-15", str(r.get("subject")), metric=metric, recorded=recorded,
                     rederived={}, verdict=NOT_APPLICABLE,
                     detail="No bundle to read what the re-derived run scores.")
    rederived = {"scored": mid in mb, "functions": placed}
    if action == "remove":
        if mid in mb:
            return _item("REF-15", str(r.get("subject")), metric=metric, recorded=recorded,
                         rederived=rederived, verdict=DIFFER,
                         detail=f"Removed here; the re-derived run scores it in {', '.join(placed) or 'no function'}.")
        return _item("REF-15", str(r.get("subject")), metric=metric, recorded=recorded,
                     rederived=rederived, verdict=AGREE,
                     detail="Removed here; the re-derived run has no curve for it either.")
    if action == "unmap":
        hit = [f for f in functions if f in placed]
        if hit:
            return _item("REF-15", str(r.get("subject")), metric=metric, recorded=recorded,
                         rederived=rederived, verdict=DIFFER,
                         detail=f"Taken out of {', '.join(functions)} here; the re-derived run "
                                f"scores it in {', '.join(hit)}.")
        return _item("REF-15", str(r.get("subject")), metric=metric, recorded=recorded,
                     rederived=rederived, verdict=AGREE,
                     detail=f"Taken out of {', '.join(functions)} here; the re-derived run "
                            "does not score it there either.")
    if action == "include":
        missing = [f for f in functions if f not in placed]
        if missing:
            return _item("REF-15", str(r.get("subject")), metric=metric, recorded=recorded,
                         rederived=rederived, verdict=DIFFER,
                         detail=f"Used in {', '.join(functions)} here; the re-derived run "
                                f"leaves it out of {', '.join(missing)}.")
        return _item("REF-15", str(r.get("subject")), metric=metric, recorded=recorded,
                     rederived=rederived, verdict=AGREE,
                     detail=f"Used in {', '.join(functions)} here; the re-derived run uses it there too.")
    # a source the owner chose
    if mid not in mb:
        return _item("REF-15", str(r.get("subject")), metric=metric, recorded=recorded,
                     rederived=rederived, verdict=DIFFER,
                     detail="A source chosen here; the re-derived run has no curve for it.")
    words, rows = _curve_words(mid, ma, mb)
    if words == "the same curve":
        return _item("REF-15", str(r.get("subject")), metric=metric, recorded=recorded,
                     rederived=rederived, verdict=AGREE,
                     detail="A source chosen here; the re-derived run scores the same curve.")
    if not words:
        return _item("REF-15", str(r.get("subject")), metric=metric, recorded=recorded,
                     rederived=rederived, verdict=DIFFER,
                     detail="A source chosen here; the re-derived run scores it with a curve of its own.")
    return _item("REF-15", str(r.get("subject")), metric=metric, recorded=recorded,
                 rederived=rederived, verdict=DIFFER, anchor_rows=rows,
                 detail=f"A source chosen here; the re-derived run scores {words}.")


def _curve07_answered(r: Mapping) -> bool:
    decided = str((r.get("computed") or {}).get("reviewer_decision") or "")
    return bool(r.get("reviewer_action")) or decided in (rs.DECISION_FINALIZED, rs.DECISION_REMOVED)


def _curve07_outcome(r: Mapping) -> str:
    """``kept``, ``dropped`` or ``pending`` from a CURVE-07 record's answer or decision."""
    action = str(r.get("reviewer_action") or "")
    if action in KEEP_ACTIONS:
        return "kept"
    if action == "reject":
        return "dropped"
    decided = str((r.get("computed") or {}).get("reviewer_decision") or "")
    if decided in (rs.DECISION_AUTO, rs.DECISION_FINALIZED):
        return "kept"
    if decided == rs.DECISION_REMOVED:
        return "dropped"
    return "pending"


def _curve07(r: Mapping, red_records: list, ma: Mapping, mb: Mapping) -> dict:
    metric = str(r.get("subject") or "")
    mid = metric_id_of(metric)
    recorded = {"action": r.get("reviewer_action") or (r.get("computed") or {}).get("reviewer_decision"),
                "outcome": _curve07_outcome(r), **_recorded_by(r)}
    other = next((x for x in red_records if str(x.get("rule_id")) == "CURVE-07"
                  and str(x.get("subject")) == metric), None)
    if other is None:
        if mid in mb:
            # scored without a review record: carried, or from a source after the pools
            rows = anchor_differences(ma.get(mid), mb.get(mid))
            rows = rows if any(x.get("delta") for x in rows) else []
            kept = recorded["outcome"] == "kept"
            return _item("CURVE-07", metric, metric=metric, recorded=recorded,
                         rederived={"status": None, "decision": None, "outcome": "kept"},
                         verdict=AGREE if kept else DIFFER, anchor_rows=rows,
                         detail=("Kept here; the re-derived run scores the curve without holding it."
                                 if kept else
                                 "Dropped here; the re-derived run scores the curve without holding it."))
        return _item("CURVE-07", metric, metric=metric, recorded=recorded, rederived={},
                     verdict=NOT_APPLICABLE,
                     detail="The re-derived run built no curve for it, so there was nothing to hold.")
    outcome = _curve07_outcome(other)
    rederived = {"status": (other.get("computed") or {}).get("curve_status"),
                 "decision": (other.get("computed") or {}).get("reviewer_decision"),
                 "outcome": outcome}
    words = {"kept": "keeps the curve", "dropped": "drops the metric",
             "pending": "holds the curve for review again"}[outcome]
    mine = recorded["outcome"]
    if mine == outcome and outcome != "pending":
        detail = f"{'Kept' if mine == 'kept' else 'Dropped'} here; the re-derived run {words}"
        rows = anchor_differences(ma.get(mid), mb.get(mid)) if mine == "kept" else []
        # the anchors ride only when a band edge moved: the same curve says nothing new
        rows = rows if any(x.get("delta") for x in rows) else []
        if rows:
            detail += " with band edges that moved"
        return _item("CURVE-07", metric, metric=metric, recorded=recorded, rederived=rederived,
                     verdict=AGREE, detail=detail + ".", anchor_rows=rows)
    return _item("CURVE-07", metric, metric=metric, recorded=recorded, rederived=rederived,
                 verdict=DIFFER, anchor_rows=anchor_differences(ma.get(mid), mb.get(mid)),
                 detail=f"{'Kept' if mine == 'kept' else 'Dropped' if mine == 'dropped' else 'Pending'} "
                        f"here; the re-derived run {words}.")


def _exclusions(bundle: Optional[Mapping]) -> dict[str, dict]:
    cov = (bundle or {}).get("functionCoverage") or {}
    return {str(e.get("functionId")): e for e in cov.get("exclusions") or [] if isinstance(e, Mapping)}


def _cov01(fid: str, *, recorded: dict, red_records: list, fb: Mapping, have_bundle: bool,
           red_exclusions: Mapping) -> dict:
    scored = [m for m in fb.get(fid) or []]
    other = next((x for x in red_records if str(x.get("rule_id")) == "COV-01"
                  and str(x.get("subject")) == fid), None)
    if scored:
        return _item("COV-01", fid, function_id=fid, recorded=recorded,
                     rederived={"scored": True, "metrics": scored}, verdict=DIFFER,
                     detail=f"A documented gap here; the re-derived run scores it with {', '.join(scored)}.")
    if have_bundle or other is not None or fid in red_exclusions:
        why = (red_exclusions.get(fid) or {}).get("reason") if fid in red_exclusions else None
        return _item("COV-01", fid, function_id=fid, recorded=recorded,
                     rederived={"scored": False, "reason": why}, verdict=AGREE,
                     detail="A documented gap here; the re-derived run leaves it unassessed too.")
    return _item("COV-01", fid, function_id=fid, recorded=recorded, rederived={},
                 verdict=NOT_APPLICABLE, detail="No bundle to read what the re-derived run scores.")


def _select01(r: Mapping, red_records: list, fb: Mapping) -> dict:
    fid = str(r.get("subject") or "")
    c = r.get("computed") or {}
    mine = sorted(str(m) for m in (c.get("bundle_metrics") or []))
    recorded = {"action": r.get("reviewer_action"), "metrics": mine, **_recorded_by(r)}
    other = next((x for x in red_records if str(x.get("rule_id")) == "SELECT-01"
                  and str(x.get("subject")) == fid), None)
    theirs = sorted(str(m) for m in ((other or {}).get("computed") or {}).get("bundle_metrics") or fb.get(fid) or [])
    if other is None and not theirs:
        return _item("SELECT-01", fid, function_id=fid, recorded=recorded, rederived={},
                     verdict=NOT_APPLICABLE,
                     detail="The re-derived run records nothing for this function.")
    needs = bool((other or {}).get("review_required")) if other is not None else None
    rederived = {"metrics": theirs, "reviewRequired": needs}
    if needs is False:
        return _item("SELECT-01", fid, function_id=fid, recorded=recorded, rederived=rederived,
                     verdict=NOT_APPLICABLE,
                     detail=f"Approved here as a set of {len(mine)}; the re-derived run scores it "
                            f"with {len(theirs)}, within the maximum, so no approval is needed.")
    if theirs == mine:
        return _item("SELECT-01", fid, function_id=fid, recorded=recorded, rederived=rederived,
                     verdict=AGREE, detail="The same metric set; the approval carries.")
    return _item("SELECT-01", fid, function_id=fid, recorded=recorded, rederived=rederived,
                 verdict=DIFFER,
                 detail=f"Approved here for {', '.join(mine) or 'no metric'}; the re-derived run "
                        f"scores it with {', '.join(theirs) or 'no metric'}, so the approval does not carry.")


def differences_from_owner_decisions(recorded_doc: Optional[Mapping], rederived_doc: Optional[Mapping], *,
                                     recorded_bundle: Optional[Mapping] = None,
                                     rederived_bundle: Optional[Mapping] = None) -> list[dict]:
    """Per recorded REF-15 / CURVE-07 / COV-01 / SELECT-01 decision of ``recorded_doc``,
    what the re-derived run (``rederived_doc``, built from scratch without those decisions)
    decided about the same metric or function: ``verdict`` agree, differ or not_applicable,
    the words in ``detail``, and ``anchors`` (the band edges of both curves, shift in IQR
    units) where both versions carry the curve. The bundles supply what the documents do
    not: which functions each version scores with which metrics, and the curves."""
    rec, red = dict(recorded_doc or {}), dict(rederived_doc or {})
    ma, mb = metrics_by_id(recorded_bundle), metrics_by_id(rederived_bundle)
    fb = functions_of(rederived_bundle)
    have_bundle = bool(rederived_bundle)
    red_records = list(red.get("records") or [])
    red_exclusions = _exclusions(rederived_bundle)
    out: list[dict] = []
    seen_cov: set[str] = set()
    for r in rec.get("records") or []:
        if not isinstance(r, Mapping):
            continue
        rule = str(r.get("rule_id") or "")
        if rule == "REF-15":
            out.append(_ref15(r, ma, mb, fb, have_bundle))
        elif rule == "CURVE-07" and _curve07_answered(r):
            out.append(_curve07(r, red_records, ma, mb))
        elif rule == "COV-01" and (r.get("reviewer_action")
                                   or str(r.get("subject")) in _exclusions(recorded_bundle)):
            fid = str(r.get("subject") or "")
            seen_cov.add(fid)
            exc = _exclusions(recorded_bundle).get(fid) or {}
            recorded = {"action": r.get("reviewer_action") or "documented gap",
                        "reason": exc.get("reason"), **_recorded_by(r)}
            if exc.get("recordedBy") and not recorded["by"]:
                recorded["by"] = exc["recordedBy"]
            out.append(_cov01(fid, recorded=recorded, red_records=red_records, fb=fb,
                              have_bundle=have_bundle, red_exclusions=red_exclusions))
        elif rule == "SELECT-01" and r.get("reviewer_action"):
            out.append(_select01(r, red_records, fb))
    # a documented gap the bundle carries with no COV-01 record of its own
    for fid, exc in _exclusions(recorded_bundle).items():
        if fid in seen_cov:
            continue
        recorded = {"action": "documented gap", "reason": exc.get("reason"),
                    "by": exc.get("recordedBy") or "", "when": str(exc.get("recordedAt") or "")[:10],
                    "rationale": exc.get("justification") or ""}
        out.append(_cov01(fid, recorded=recorded, red_records=red_records, fb=fb,
                          have_bundle=have_bundle, red_exclusions=red_exclusions))
    order = {rule: i for i, rule in enumerate(OWNER_RULES)}
    out.sort(key=lambda x: (order.get(x["rule"], 9), str(x.get("subject"))))
    return out


def owner_decision_counts(items: Iterable[Mapping]) -> dict:
    out = {AGREE: 0, DIFFER: 0, NOT_APPLICABLE: 0}
    for it in items or []:
        out[str(it.get("verdict"))] = out.get(str(it.get("verdict")), 0) + 1
    return out


# --------------------------------------------------------------------------- #
# the whole report, its rows, its CSV and its markdown
# --------------------------------------------------------------------------- #
def full_report(vdir_a, vdir_b, *, with_registers: bool = False, with_ledger: bool = False,
                with_digests: bool = False, with_owner_decisions: bool = False) -> dict:
    """:func:`compare` plus the optional sections. Every section is computed from the two
    folders' own files; a section a version cannot supply says so in its ``note``."""
    bundle_a, doc_a = load_version(vdir_a)
    bundle_b, doc_b = load_version(vdir_b)
    rep = compare_loaded(bundle_a, doc_a, bundle_b, doc_b,
                         a=str(resolve_version_dir(vdir_a)), b=str(resolve_version_dir(vdir_b)))
    if with_registers:
        rep["registers"] = registers(doc_a, doc_b)
    if with_ledger:
        rep["ledgers"] = ledgers(vdir_a, vdir_b)
    if with_digests:
        rep["digests"] = {"a": digest(vdir_a), "b": digest(vdir_b)}
    if with_owner_decisions:
        items = differences_from_owner_decisions(doc_a, doc_b, recorded_bundle=bundle_a,
                                                 rederived_bundle=bundle_b)
        rep["ownerDecisions"] = {"items": items, "counts": owner_decision_counts(items)}
    return rep


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, sort_keys=True, default=str)
    return str(v)


def report_rows(rep: Mapping) -> list[dict]:
    """The report as flat rows (``section, subject, field, a, b, note``) for a table or a CSV."""
    rows: list[dict] = []

    def add(section, subject, field="", a="", b="", note=""):
        rows.append({"section": section, "subject": _cell(subject), "field": _cell(field),
                     "a": _cell(a), "b": _cell(b), "note": _cell(note)})

    for k, v in (rep.get("manifest") or {}).items():
        if k == "standingDecisions":
            add("manifest", k, "", v.get("a"), v.get("b"))
        else:
            add("manifest", k, "", v.get("a"), v.get("b"), "equal" if v.get("equal") else "differs")
    c = rep.get("curves") or {}
    for mid in c.get("identical") or []:
        add("curves", mid, "", "", "", "identical")
    for mid, notes in (c.get("differ") or {}).items():
        add("curves", mid, "max delta", (c.get("max_delta") or {}).get(mid), "", "; ".join(notes))
    for side in ("only_a", "only_b"):
        for mid in c.get(side) or []:
            add("curves", mid, "", "present" if side == "only_a" else "",
                "present" if side == "only_b" else "", f"only in {side[-1].upper()}")
    d = rep.get("decisions") or {}
    for label, diffs in (d.get("differ") or {}).items():
        for f, (va, vb) in diffs.items():
            add("decisions", label, f, va, vb, "differs")
    for side in ("only_a", "only_b"):
        for label in d.get(side) or []:
            add("decisions", label, "", "", "", f"only in {side[-1].upper()}")
    q = rep.get("queue") or {}
    for side in ("only_a", "only_b"):
        for item in q.get(side) or []:
            add("queue", item, "", "", "", f"only in {side[-1].upper()}")
    reg = rep.get("registers")
    if reg:
        if reg.get("note"):
            add("register", "", "", "", "", reg["note"])
        for ch in reg.get("changes") or []:
            if ch.get("change") == "changed":
                x, y = ch.get("before") or {}, ch.get("after") or {}
                for f in ch.get("fields") or []:
                    add("register", f"{x.get('functionId')}: {x.get('candidate') or x.get('candidateKey')}",
                        f, x.get(f), y.get(f), "changed")
            else:
                r = {k: v for k, v in ch.items() if k != "change"}
                add("register", f"{r.get('functionId')}: {r.get('candidate') or r.get('candidateKey')}",
                    "status", r.get("status") if ch["change"] == "removed" else "",
                    r.get("status") if ch["change"] == "added" else "", ch["change"])
    led = rep.get("ledgers")
    if led:
        if led.get("note"):
            add("ledger", "", "", "", "", led["note"])
        for ch in led.get("changes") or []:
            x, y = ch.get("before") or {}, ch.get("after") or {}
            for f in ch.get("fields") or []:
                add("ledger", f"{ch.get('metric')} / {ch.get('functionId')}", f, x.get(f), y.get(f), ch["change"])
    dg = rep.get("digests")
    if dg:
        for side in ("a", "b"):
            one = dg.get(side) or {}
            for key in ("inputsDigest", "contentDigest"):
                rec = one.get(key) or {}
                add("digest", f"{side.upper()} {key}", "recorded", rec.get("recorded"), "", "")
                add("digest", f"{side.upper()} {key}", "re-derived", rec.get("rederived"), "",
                    "replays" if rec.get("equal") else (rec.get("problem") or "does not replay"))
    od = rep.get("ownerDecisions")
    if od:
        for it in od.get("items") or []:
            add("owner decisions", f"{it.get('rule')}:{it.get('subject')}",
                (it.get("recorded") or {}).get("action") or "",
                json.dumps(it.get("recorded") or {}, sort_keys=True, default=str),
                json.dumps(it.get("rederived") or {}, sort_keys=True, default=str),
                f"{VERDICT_WORDS.get(it.get('verdict'), it.get('verdict'))}: {it.get('detail')}")
            for a in it.get("anchors") or []:
                add("owner decisions", f"{it.get('rule')}:{it.get('subject')}",
                    f"band {a.get('band'):g}", a.get("a"), a.get("b"),
                    f"{a['deltaIqr']:+.2f} IQR" if a.get("deltaIqr") is not None else "")
    return rows


CSV_COLUMNS = ("section", "subject", "field", "a", "b", "note")


def report_csv(rep: Mapping) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
    w.writeheader()
    for r in report_rows(rep):
        w.writerow(r)
    return buf.getvalue()


def report_text(rep: Mapping) -> str:
    """The command line's print, one section after another."""
    c, d, q = rep.get("curves") or {}, rep.get("decisions") or {}, rep.get("queue") or {}
    lines = [f"A: {rep.get('a')}", f"B: {rep.get('b')}", ""]
    for k, v in (rep.get("manifest") or {}).items():
        if k == "standingDecisions":
            lines.append(f"manifest.{k}: A={v['a']} B={v['b']}")
        elif v.get("equal"):
            lines.append(f"manifest.{k}: equal")
        else:
            lines.append(f"manifest.{k}: DIFFERS\n    A={v['a']}\n    B={v['b']}")
    lines.append("")
    lines.append(f"curves: {len(c.get('identical') or [])} identical, {len(c.get('differ') or {})} differ, "
                 f"{len(c.get('only_a') or [])} only in A, {len(c.get('only_b') or [])} only in B")
    for mid, notes in (c.get("differ") or {}).items():
        delta = (c.get("max_delta") or {}).get(mid)
        lines.append(f"  {mid}:" + (f" (max delta {delta:.6g})" if delta is not None else ""))
        lines.extend(f"    {n}" for n in notes)
    for side in ("only_a", "only_b"):
        if c.get(side):
            lines.append(f"  {side}: {', '.join(c[side])}")
    lines.append("")
    lines.append(f"decisions: {d.get('same', 0)} same, {len(d.get('differ') or {})} differ, "
                 f"{len(d.get('only_a') or [])} only in A, {len(d.get('only_b') or [])} only in B")
    for label, diffs in (d.get("differ") or {}).items():
        lines.append(f"  {label}: " + "; ".join(f"{f} {va!r} -> {vb!r}" for f, (va, vb) in diffs.items()))
    for side in ("only_a", "only_b"):
        if d.get(side):
            lines.append(f"  {side}: {', '.join(d[side])}")
    lines.append("")
    lines.append(f"review queue: {q.get('common', 0)} common items, "
                 f"only in A: {q.get('only_a') or 'none'}, only in B: {q.get('only_b') or 'none'}")
    reg = rep.get("registers")
    if reg is not None:
        lines.append("")
        if reg.get("note"):
            lines.append(f"registers: {reg['note']}")
        else:
            cn = reg.get("counts") or {}
            lines.append(f"registers: {reg['a']['rows']} rows in A, {reg['b']['rows']} in B; "
                         f"{cn.get('added', 0)} added, {cn.get('removed', 0)} removed, {cn.get('changed', 0)} changed")
            for ch in reg.get("changes") or []:
                if ch.get("change") == "changed":
                    x, y = ch["before"], ch["after"]
                    lines.append(f"  {x.get('functionId')}: {x.get('candidate') or x.get('candidateKey')}: "
                                 + "; ".join(f"{f} {x.get(f)!r} -> {y.get(f)!r}" for f in ch.get("fields") or []))
                else:
                    lines.append(f"  {ch.get('functionId')}: {ch.get('candidate') or ch.get('candidateKey')}: "
                                 f"{ch['change']} ({ch.get('status')})")
    led = rep.get("ledgers")
    if led is not None:
        lines.append("")
        if led.get("note"):
            lines.append(f"ledgers: {led['note']}")
        else:
            cn = led.get("counts") or {}
            lines.append(f"ledgers: {led.get('same', 0)} rows unchanged; {cn.get('added', 0)} added, "
                         f"{cn.get('removed', 0)} removed, {cn.get('changed', 0)} changed")
            for ch in led.get("changes") or []:
                x, y = ch.get("before") or {}, ch.get("after") or {}
                lines.append(f"  {ch.get('metric')} / {ch.get('functionId')}: {ch['change']}"
                             + ("; " + "; ".join(f"{f} {x.get(f)!r} -> {y.get(f)!r}" for f in ch.get("fields") or [])
                                if ch["change"] == "changed" else ""))
    dg = rep.get("digests")
    if dg is not None:
        lines.append("")
        for side in ("a", "b"):
            one = dg.get(side) or {}
            for key in ("inputsDigest", "contentDigest"):
                rec = one.get(key) or {}
                state = "replays" if rec.get("equal") else (rec.get("problem") or "DOES NOT REPLAY")
                lines.append(f"{side.upper()} {key}: {state}\n    recorded   {rec.get('recorded')}\n"
                             f"    re-derived {rec.get('rederived')}")
    od = rep.get("ownerDecisions")
    if od is not None:
        cn = od.get("counts") or {}
        lines.append("")
        lines.append(f"owner decisions: {cn.get(AGREE, 0)} agree, {cn.get(DIFFER, 0)} differ, "
                     f"{cn.get(NOT_APPLICABLE, 0)} not applicable")
        for it in od.get("items") or []:
            lines.append(f"  {it['rule']}:{it['subject']}: {VERDICT_WORDS.get(it['verdict'], it['verdict'])}. {it['detail']}")
            for a in it.get("anchors") or []:
                shift = f" ({a['deltaIqr']:+.2f} IQR)" if a.get("deltaIqr") is not None else ""
                lines.append(f"    band {a['band']:g}: A {_cell(a.get('a')) or 'not reached'}, "
                             f"B {_cell(a.get('b')) or 'not reached'}{shift}")
    return "\n".join(lines) + "\n"


def report_markdown(rep: Mapping) -> str:
    """The report as markdown tables, one per section."""
    lines = [f"# Version comparison", "", f"- A: `{rep.get('a')}`", f"- B: `{rep.get('b')}`", ""]
    by_section: dict[str, list[dict]] = {}
    for r in report_rows(rep):
        by_section.setdefault(r["section"], []).append(r)
    for section, rows in by_section.items():
        lines.append(f"## {section.capitalize()}")
        lines.append("")
        lines.append("| subject | field | A | B | note |")
        lines.append("|---|---|---|---|---|")
        for r in rows:
            cells = [str(r[k]).replace("|", "\\|").replace("\n", " ") for k in ("subject", "field", "a", "b", "note")]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


__all__ = ["BUNDLE_FILE", "PROVENANCE_FILE", "OWNER_RULES", "AGREE", "DIFFER", "NOT_APPLICABLE",
           "VERDICT_WORDS", "METRIC_FIELDS", "LEDGER_FIELDS", "resolve_version_dir", "load_version",
           "version_label", "metrics_by_id", "functions_of", "metric_id_of", "num_delta", "curve_diff",
           "curve_max_delta", "records_by_key", "queue_ids", "compare", "compare_loaded", "registers",
           "ledgers", "digest", "anchors", "anchor_differences", "differences_from_owner_decisions",
           "owner_decision_counts", "full_report", "report_rows", "report_csv", "report_text",
           "report_markdown", "CSV_COLUMNS"]
