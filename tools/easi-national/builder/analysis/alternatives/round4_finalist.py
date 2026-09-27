"""The finalist step's multiplicity report: BH q-values across the eight primaries.

    python -m builder.analysis.alternatives.round4_finalist --summary <study>/summary.json ...
        [--out <report.json>]

Reads the studies' ``summary.json`` (one per family, runner 1.2.0), takes each family's one
primary comparison (P1 on the family's primary target in the frozen design; P5 for E8), and
reports the Benjamini-Hochberg q-values across them with the addendum's q <= 0.10, beside
each study's own decision by the margins. Reported as supporting evidence only: adoption
rests on the margins, and no composition is made here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import round4 as r4


def primary_p(candidate: dict) -> tuple:
    """``(p, description)`` of a candidate's primary comparison from its summary block."""
    decision = candidate.get("decision") or {}
    if candidate.get("decision_rule") == "presentation":
        block = candidate.get("P5") or {}
        return block.get("p_two_sided"), "P5 completeness optimism (HUC8 bootstrap, two-sided)"
    primary = decision.get("primary_target") or "T1"
    row = ((candidate.get("P1") or {}).get("designs") or {}).get("frozen", {}).get(primary) or {}
    return row.get("p_two_sided"), f"P1 paired AUC delta on {primary}, frozen design (HUC8 bootstrap, two-sided)"


def benjamini_hochberg(pvalues: dict) -> dict:
    """``{family: q}`` for the families with a p-value: q_(i) = min_{j >= i} p_(j) * m / j."""
    items = [(family, p) for family, p in pvalues.items() if p is not None]
    m = len(items)
    ordered = sorted(items, key=lambda kv: kv[1])
    q = {}
    running = 1.0
    for rank in range(m, 0, -1):
        family, p = ordered[rank - 1]
        running = min(running, p * m / rank)
        q[family] = min(1.0, running)
    return q


def report(summaries: list[dict]) -> dict:
    rows = []
    pvalues = {}
    for doc in summaries:
        for candidate in doc.get("candidates") or []:
            p, description = primary_p(candidate)
            family = candidate["id"]
            pvalues[family] = p
            rows.append({"family": family, "family_name": candidate.get("family"), "study": doc.get("study_id"),
                         "decision_rule": candidate.get("decision_rule"), "primary": description, "p": p,
                         "adopted_by_margins": (candidate.get("decision") or {}).get("adopted"),
                         "reasons": (candidate.get("decision") or {}).get("reasons")})
    q = benjamini_hochberg(pvalues)
    for row in rows:
        row["q"] = q.get(row["family"])
        row["q_supports"] = None if row["q"] is None else row["q"] <= r4.MARGINS["bh_q"]
    missing = [f for f in r4.FAMILIES if f not in pvalues]
    return {"families_expected": list(r4.FAMILIES), "families_read": sorted(pvalues), "families_missing": missing,
            "q_threshold": r4.MARGINS["bh_q"], "rows": sorted(rows, key=lambda r: r["family"]),
            "note": ("Benjamini-Hochberg q-values across the eight primaries are reported as supporting evidence; "
                     "adoption rests on each study's margins. No composition is made here."),
            "complete": not missing}


def retrospective_report(summaries: list[dict]) -> dict:
    """The finalist's one read of the retrospective cohort (2023-24), kept apart from the
    family decisions (which read the development cohort only): per family the P1 rows of the
    retrospective cohort on T1 and T2 in both designs, beside the decision the study made."""
    rows = []
    for doc in summaries:
        for candidate in doc.get("candidates") or []:
            cohorts = (candidate.get("P1") or {}).get("cohorts") or {}
            block = cohorts.get(r4.RETROSPECTIVE_COHORT) or {}
            rows.append({"family": candidate["id"], "study": doc.get("study_id"),
                         "adopted_by_margins": (candidate.get("decision") or {}).get("adopted"),
                         "deciding_cohort": (candidate.get("decision") or {}).get("deciding_cohort"),
                         "retrospective": {design: {t: {k: v for k, v in (block.get(design, {}).get(t) or {}).items()
                                                        if k in ("reference", "alternative", "delta", "delta_median", "ci_low",
                                                                 "ci_high", "n", "boot_valid", "supported", "present")}
                                                    for t in ("T1", "T2")}
                                           for design in ("frozen", "watershed-held-out")}})
    return {"cohort": r4.RETROSPECTIVE_COHORT, "rows": sorted(rows, key=lambda r: r["family"]),
            "note": ("The retrospective cohort is read once, here, after the family decisions were taken on the "
                     "development cohort; nothing here changes a decision or a margin.")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--summary", action="append", required=True, type=Path, help="a study's summary.json (repeat)")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--retrospective", type=Path, metavar="OUT",
                    help="also write the separate retrospective-cohort report to OUT (refused when OUT exists: "
                         "the retrospective cohort is read once)")
    a = ap.parse_args(argv)
    summaries = [json.loads(Path(p).read_text(encoding="utf-8")) for p in a.summary]
    out = report(summaries)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(out, indent=1, sort_keys=True))
    if a.retrospective is not None:
        if a.retrospective.exists():
            raise SystemExit(f"{a.retrospective} exists: the retrospective cohort is read once")
        retro = retrospective_report(summaries)
        a.retrospective.parent.mkdir(parents=True, exist_ok=True)
        a.retrospective.write_text(json.dumps(retro, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps({"retrospective": str(a.retrospective), "families": [r["family"] for r in retro["rows"]]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
