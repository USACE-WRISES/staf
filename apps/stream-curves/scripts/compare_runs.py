"""Compare two assessment version dirs: curves, decisions, queue, manifest, and on request
the candidate registers, the rebuild ledgers, the re-derived digests and what a re-derived
run decided where the owner had decided (campaign decision D3).

A version dir is any folder holding assessment.deep.json + provenance.json (a published
apps/library version, or a staged one under <out>/library/...); a run folder resolves to
the version it staged. The internals live in ``streamcurves.compare``; this is the command
line over them. The report states WHAT differs; classifying WHY (rule evolution, seed
shift, data drift, judgment) stays with the reader.

Usage (from apps/stream-curves):
  python scripts/compare_runs.py --a <version_dir> --b <version_dir>
      [--registers] [--ledger] [--digest] [--owner-decisions]
      [--json OUT] [--markdown OUT]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from streamcurves import compare as cmp  # noqa: E402

# the classic entry point, kept for callers that import it
compare = cmp.compare


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--a", required=True, help="version dir (or run folder) A, the recorded version")
    ap.add_argument("--b", required=True, help="version dir (or run folder) B, the other version")
    ap.add_argument("--registers", action="store_true",
                    help="diff the candidate registers (candidateRegister) through candidates.diff")
    ap.add_argument("--ledger", action="store_true",
                    help="diff the rebuild ledgers per metric and function")
    ap.add_argument("--digest", action="store_true",
                    help="re-derive each version's inputsDigest from its manifest and its "
                         "contentDigest from its bundle")
    ap.add_argument("--owner-decisions", action="store_true",
                    help="per recorded REF-15 / CURVE-07 / COV-01 / SELECT-01 decision of A, what "
                         "B decided (agree, differ, not applicable), anchors in IQR units")
    ap.add_argument("--json", default=None, help="write the report as JSON to this path")
    ap.add_argument("--markdown", default=None, help="write the report as markdown tables to this path")
    args = ap.parse_args(argv)

    rep = cmp.full_report(Path(args.a), Path(args.b), with_registers=args.registers,
                          with_ledger=args.ledger, with_digests=args.digest,
                          with_owner_decisions=args.owner_decisions)
    sys.stdout.write(cmp.report_text(rep))
    if args.json:
        import json
        Path(args.json).write_text(json.dumps(rep, indent=1, default=str) + "\n", encoding="utf-8")
        print(f"wrote {args.json}")
    if args.markdown:
        Path(args.markdown).write_text(cmp.report_markdown(rep), encoding="utf-8")
        print(f"wrote {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
