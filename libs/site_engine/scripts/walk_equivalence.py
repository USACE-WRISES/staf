"""Walk equivalence: the node walk (0.2.1) against the dnhydroseq scan (0.2.0).

Runs both upstream walks from the same anchor reaches and compares the tree
id sets, hop counts, and wall time. The scan costs about 36 seconds per hop,
so the panel is the small acceptance trees plus Sugar Run; pass ``--max-hops``
to bound the scan.

    python scripts/walk_equivalence.py [--max-hops 15] [--out out/walk_equivalence.json]

Live (hydro.nationalmap.gov). Prints one line per site and a verdict; exit 1
when any completed pair differs.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from site_engine import hr  # noqa: E402

# (label, nhdplusid) from the 2026-09-01 runtime profile: every completed
# multi-reach tree under 45 hops, plus a one-reach control.
PANEL = [
    ("Ohio (Waldo) trib", 24000800011818),
    ("Great Lakes (MI) 3 reaches", 60001800017690),
    ("Mid-Atlantic (PA) 4 reaches", 10000600001253),
    ("South Atlantic (GA) 8 reaches", 15001600099044),
    ("Mountain West (CO) 8 reaches", 41000600139191),
    ("Ozarks (AR/OK) 29 reaches", 21000200078589),
    ("Pacific Northwest (WA) 269 reaches", 55000800088012),
]


def walk_scan(anchor: dict, max_hops: int) -> tuple[set[int], int, str | None]:
    tree = {int(anchor["nhdplusid"])}
    frontier = [int(anchor["hydroseq"])]
    hops = 0
    while frontier:
        if hops >= max_hops:
            return tree, hops, "hop budget"
        parents = hr.parents_by_dnhydroseq(frontier, with_geometry=False)
        if parents is None:
            return tree, hops, "query failed"
        frontier = []
        for rec in parents:
            rid = rec.get("nhdplusid")
            if rid is None or rid in tree:
                continue
            tree.add(int(rid))
            if rec.get("hydroseq"):
                frontier.append(int(rec["hydroseq"]))
        hops += 1
    return tree, hops, None


def walk_node(anchor: dict, max_hops: int) -> tuple[set[int], int, str | None]:
    tree = {int(anchor["nhdplusid"])}
    frontier = [anchor]
    hops = 0
    while frontier:
        if hops >= max_hops:
            return tree, hops, "hop budget"
        parents = hr.parents_by_node(frontier)
        if parents is None:
            return tree, hops, "query failed"
        frontier = []
        for rec in parents:
            rid = rec.get("nhdplusid")
            if rid is None or rid in tree:
                continue
            tree.add(int(rid))
            if rec.get("hydroseq") and rec.get("geometry"):
                frontier.append(rec)
        hops += 1
    return tree, hops, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-hops", type=int, default=200)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(Path(__file__).parent / "out" / "walk_equivalence.json"))
    args = ap.parse_args()
    rows = []
    bad = 0
    for label, nid in (PANEL[: args.limit] if args.limit else PANEL):
        anchor = hr.flowline_by_id(nid)
        if not anchor:
            print(f"{label}: anchor fetch failed")
            rows.append({"label": label, "nhdplusid": nid, "status": "anchor failed"})
            bad += 1
            continue
        t = time.time()
        node_tree, node_hops, node_stop = walk_node(anchor, args.max_hops)
        t_node = time.time() - t
        t = time.time()
        scan_tree, scan_hops, scan_stop = walk_scan(anchor, args.max_hops)
        t_scan = time.time() - t
        equal = node_tree == scan_tree and node_hops == scan_hops
        if not equal:
            bad += 1
        rows.append({"label": label, "nhdplusid": nid,
                     "node": {"reaches": len(node_tree), "hops": node_hops,
                              "secs": round(t_node, 1), "stop": node_stop},
                     "scan": {"reaches": len(scan_tree), "hops": scan_hops,
                              "secs": round(t_scan, 1), "stop": scan_stop},
                     "only_node": sorted(node_tree - scan_tree)[:20],
                     "only_scan": sorted(scan_tree - node_tree)[:20],
                     "equal": equal})
        print(f"{label}: node {len(node_tree)} reaches/{node_hops} hops in {t_node:.0f}s; "
              f"scan {len(scan_tree)}/{scan_hops} in {t_scan:.0f}s; "
              f"{'EQUAL' if equal else 'DIFFER'}", flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rows": rows, "n_differ": bad}, indent=2), encoding="utf-8")
    print("verdict:", "all completed pairs equal" if bad == 0 else f"{bad} pair(s) differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
