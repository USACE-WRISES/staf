"""Rules scorecard: replay the standing-decision policy across published versions.

For every library version that carries provenance, apply the current policy to
its recorded review queue (offline, no recomputation; see decisions.replay) and
report how much of the recorded judgment the rules reproduce:

  match / alias      the policy reaches the same action and decision class
  open               the policy leaves the item for the owner (stricter, not wrong)
  mismatch           the policy would decide differently (investigate)
  owner-only         recorded decisions with no queue item (never policy territory)
  approvals?         portfolio approvals the policy cannot derive
  origins            recorded rationale origins: policy / ai+owner / owner

Two passes per version: the default policy, and one with every opt-in entry
enabled, so the marginal coverage of the opt-ins is visible.

Superseded versions are replayed too (labeled), but only the LATEST version of
each assessment carries the reproducibility claim: older versions were built
and recorded under earlier decision vocabularies, so their divergence is
history, not a rules failure. Only latest-version mismatches itemize and set
the exit code.

Usage (from apps/stream-curves):
  python scripts/rules_scorecard.py [--library-root PATH] [--policy PATH] [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamcurves import decisions as dec  # noqa: E402
from streamcurves import library as lib  # noqa: E402


def _origin_mix(doc: dict) -> dict[str, int]:
    mix = {"policy": 0, "ai+owner": 0, "owner": 0, "other": 0}
    for rec in doc.get("records") or []:
        if not rec.get("reviewer_action"):
            continue
        origin = str(rec.get("reviewer_rationale_origin") or "")
        if origin.startswith("standing_policy"):
            mix["policy"] += 1
        elif origin == "ai_drafted_owner_approved":
            mix["ai+owner"] += 1
        elif origin == "owner_written":
            mix["owner"] += 1
        else:
            mix["other"] += 1
    return mix


def _counts_cell(counts: dict) -> str:
    m = counts.get("match", 0) + counts.get("alias_match", 0)
    return f"{m}m/{counts.get('stricter_open', 0)}o/{counts.get('mismatch', 0)}x"


def scorecard(library_root: Path, policy: dict) -> list[dict]:
    optional = sorted(str(e.get("id")) for e in policy.get("entries") or []
                      if e.get("id") and not e.get("enabled", False))
    rows: list[dict] = []
    assess_root = library_root / "assessments"
    for adir in sorted(p for p in assess_root.iterdir() if p.is_dir()):
        vdirs = sorted((v for v in adir.iterdir()
                        if v.is_dir() and v.name.startswith("v")),
                       key=lambda v: int(v.name[1:]))
        with_prov = [v for v in vdirs if (v / "provenance.json").exists()]
        for vdir in with_prov:
            doc = json.loads((vdir / "provenance.json").read_text(encoding="utf-8"))
            default = dec.replay(vdir, policy)
            full = dec.replay(vdir, policy, enabled=optional)
            rows.append({
                "assessment": adir.name,
                "version": vdir.name,
                "latest": vdir is with_prov[-1],
                "items": len(default.rows),
                "default": default.counts(),
                "all_optional": full.counts(),
                "mismatches": [r["item_id"] for r in full.mismatches()],
                "owner_only": [f"{o['rule_id']}:{o['subject']}"
                               for o in full.owner_only_records],
                "approvals_not_derived": full.portfolio_approvals_not_derived,
                "origins": _origin_mix(doc),
            })
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--library-root", default=None)
    ap.add_argument("--policy", default=None)
    ap.add_argument("--json", default=None, help="write the full rows here as JSON")
    a = ap.parse_args(argv)

    root = Path(a.library_root) if a.library_root else lib.library_root()
    policy = dec.load_policy(a.policy)
    rows = scorecard(root, policy)

    print(f"library: {root}")
    print(f"policy:  {dec.policy_version(policy)} "
          f"({(policy.get('meta') or {}).get('sha256', '')[:19]})")
    print()
    head = (f"{'assessment':34s} {'ver':4s} {'items':>5s} {'default':>12s} "
            f"{'all-opt':>12s} {'owner-only':>10s} {'appr?':>5s}  origins p/a/o")
    print(head)
    print("-" * len(head))
    for r in rows:
        o = r["origins"]
        tag = "" if r["latest"] else " (superseded)"
        print(f"{r['assessment'] + tag:34s} {r['version']:4s} {r['items']:>5d} "
              f"{_counts_cell(r['default']):>12s} {_counts_cell(r['all_optional']):>12s} "
              f"{len(r['owner_only']):>10d} {len(r['approvals_not_derived']):>5d}  "
              f"{o['policy']}/{o['ai+owner']}/{o['owner']}")
        if r["latest"]:
            for mid in r["mismatches"]:
                print(f"    MISMATCH: {mid}")
        elif r["mismatches"]:
            print(f"    ({len(r['mismatches'])} mismatch(es) against a superseded "
                  f"decision vocabulary; not itemized)")
    print()
    print("cells are match/open/mismatch; owner-only = recorded decisions outside "
          "the queue; appr? = portfolio approvals the policy cannot derive.")

    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {a.json}")
    return 1 if any(r["mismatches"] for r in rows if r["latest"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
