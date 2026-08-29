"""Compare two assessment version dirs: curves, decisions, queue, manifest.

A version dir is any folder holding assessment.deep.json + provenance.json
(a published apps/library version, or a staged one under <out>/library/...).
The report states WHAT differs; classifying WHY (rule evolution, seed shift,
data drift, judgment) stays with the reader.

Usage (from apps/stream-curves):
  python scripts/compare_runs.py --a <version_dir> --b <version_dir> [--json OUT]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(vdir: Path) -> tuple[dict, dict]:
    bundle = json.loads((vdir / "assessment.deep.json").read_text(encoding="utf-8"))
    doc = json.loads((vdir / "provenance.json").read_text(encoding="utf-8"))
    return bundle, doc


def _metrics(bundle: dict) -> dict[str, dict]:
    """metricId -> entry. Metrics are cross-listed under every function they
    serve; the copies are expected identical, so the first wins (a differing
    copy is reported)."""
    out: dict[str, dict] = {}
    for fn in bundle.get("metricsByFunction") or []:
        for m in fn.get("metrics") or []:
            mid = str(m.get("metricId"))
            if mid in out:
                if out[mid] != m:
                    out[mid] = dict(out[mid], _cross_listed_copies_differ=True)
                continue
            out[mid] = m
    return out


def _num_delta(a, b):
    """Max absolute difference between two same-shaped numeric structures."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b))
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        deltas = [_num_delta(x, y) for x, y in zip(a, b)]
        if all(d is not None for d in deltas):
            return max(deltas) if deltas else 0.0
    if isinstance(a, dict) and isinstance(b, dict) and set(a) == set(b):
        deltas = [_num_delta(a[k], b[k]) for k in a]
        if all(d is not None for d in deltas):
            return max(deltas) if deltas else 0.0
    return None


def _curve_diff(ca, cb) -> list[str]:
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
        d = _num_delta(va, vb)
        notes.append(f"{k} (max delta {d:.6g})" if d is not None else k)
    return notes


_METRIC_FIELDS = ("confidenceLabel", "confidenceTotal", "referenceN",
                  "referenceRange", "sampleDisposition", "referenceTier")


def _records(doc: dict) -> dict[tuple[str, str], dict]:
    out = {}
    for rec in doc.get("records") or []:
        out[(str(rec.get("rule_id")), str(rec.get("subject")))] = rec
    return out


def _queue_ids(doc: dict) -> set[str]:
    return {str(i.get("item_id"))
            for i in (doc.get("reviewQueue") or {}).get("items") or []}


def compare(vdir_a: Path, vdir_b: Path) -> dict:
    bundle_a, doc_a = _load(vdir_a)
    bundle_b, doc_b = _load(vdir_b)
    ma, mb = _metrics(bundle_a), _metrics(bundle_b)

    curves = {"identical": [], "differ": {}, "only_a": sorted(set(ma) - set(mb)),
              "only_b": sorted(set(mb) - set(ma))}
    for mid in sorted(set(ma) & set(mb)):
        ea, eb = ma[mid], mb[mid]
        notes = _curve_diff(ea.get("curve"), eb.get("curve"))
        for f in _METRIC_FIELDS:
            if ea.get(f) != eb.get(f):
                notes.append(f"{f}: {ea.get(f)!r} -> {eb.get(f)!r}")
        if notes:
            curves["differ"][mid] = notes
        else:
            curves["identical"].append(mid)

    ra, rb = _records(doc_a), _records(doc_b)
    decisions = {"same": 0, "differ": {}, "only_a": [], "only_b": []}
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
        fields = ("reviewer_action", "reviewer_decision_class",
                  "reviewer_rationale_origin")
        diffs = {f: (ra[key].get(f), rb[key].get(f))
                 for f in fields if ra[key].get(f) != rb[key].get(f)}
        if diffs:
            decisions["differ"][label] = diffs
        elif ra[key].get("reviewer_action"):
            decisions["same"] += 1

    qa, qb = _queue_ids(doc_a), _queue_ids(doc_b)
    queue = {"only_a": sorted(qa - qb), "only_b": sorted(qb - qa),
             "common": len(qa & qb)}

    man_a = doc_a.get("manifest") or {}
    man_b = doc_b.get("manifest") or {}
    manifest = {}
    for k in ("methodology", "diagnostics", "inputsDigest"):
        va, vb = man_a.get(k), man_b.get(k)
        manifest[k] = {"a": va, "b": vb, "equal": va == vb}
    sd_a = (man_a.get("standingDecisions") or {})
    sd_b = (man_b.get("standingDecisions") or {})
    manifest["standingDecisions"] = {
        "a": {kk: sd_a.get(kk) for kk in ("policyVersion", "enabledIds", "appliedCount")},
        "b": {kk: sd_b.get(kk) for kk in ("policyVersion", "enabledIds", "appliedCount")},
    }

    return {"a": str(vdir_a), "b": str(vdir_b), "manifest": manifest,
            "curves": curves, "decisions": decisions, "queue": queue}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    rep = compare(Path(args.a), Path(args.b))
    c, d, q = rep["curves"], rep["decisions"], rep["queue"]
    print(f"A: {rep['a']}\nB: {rep['b']}\n")
    for k, v in rep["manifest"].items():
        if k == "standingDecisions":
            print(f"manifest.{k}: A={v['a']} B={v['b']}")
        elif v["equal"]:
            print(f"manifest.{k}: equal")
        else:
            print(f"manifest.{k}: DIFFERS\n    A={v['a']}\n    B={v['b']}")
    print(f"\ncurves: {len(c['identical'])} identical, {len(c['differ'])} differ, "
          f"{len(c['only_a'])} only in A, {len(c['only_b'])} only in B")
    for mid, notes in c["differ"].items():
        print(f"  {mid}:")
        for n in notes:
            print(f"    {n}")
    for side in ("only_a", "only_b"):
        if c[side]:
            print(f"  {side}: {', '.join(c[side])}")
    print(f"\ndecisions: {d['same']} same, {len(d['differ'])} differ, "
          f"{len(d['only_a'])} only in A, {len(d['only_b'])} only in B")
    for label, diffs in d["differ"].items():
        print(f"  {label}: " + "; ".join(f"{f} {va!r} -> {vb!r}"
                                         for f, (va, vb) in diffs.items()))
    for side in ("only_a", "only_b"):
        if d[side]:
            print(f"  {side}: {', '.join(d[side])}")
    print(f"\nreview queue: {q['common']} common items, "
          f"only in A: {q['only_a'] or 'none'}, only in B: {q['only_b'] or 'none'}")

    if args.json:
        Path(args.json).write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
