"""Export EASI's calculator case set as a self-contained preview case set.

The calculator parity cases (``tests/data/calculator_cases.json``) are partial
records merged onto the stored-evidence fixture of ``tests/test_national_preloaded``.
StreamCurves cannot import EASI's tests, so this writes every case with its
complete record, the override ratings and the observed evidence it applies, in
the order the engine composes them. StreamCurves embeds the file in an EASI
project and scores it with any method version to preview a draft's consequences.

    python scripts/export_preview_cases.py <out.json>          (from apps/easi)

The cases cover band edges on both sides, every reference curve at its crossings
and knots, missing inputs, every fallback route, observed overrides, the override
scores of all 20 functions, the strata and the rollup extremes. The output is
deterministic.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import calculator_cases as cc  # noqa: E402

SCHEMA = "staf-easi-preview-cases"


def build() -> dict:
    fixture = cc.load_fixture()
    raw = cc.FIXTURE.read_bytes()
    cases = []
    for case in fixture["cases"]:
        cases.append({"id": case["id"], "group": case["group"],
                      "record": cc.make_record(case.get("record") or {}),
                      "ratings": case.get("ratings") or None,
                      "observed": case.get("observed") or None})
    groups: dict[str, int] = {}
    for c in cases:
        groups[c["group"]] = groups.get(c["group"], 0) + 1
    return {"schema": SCHEMA, "schemaVersion": 1,
            "source": {"fixture": "apps/easi/tests/data/calculator_cases.json",
                       "fixtureSha256": hashlib.sha256(raw).hexdigest(),
                       "fixtureMethodVersion": fixture.get("method_version")},
            "groups": dict(sorted(groups.items())), "cases": cases}


def main(argv: list[str]) -> int:
    out = Path(argv[1])
    doc = build()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}: {len(doc['cases'])} cases {doc['groups']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
